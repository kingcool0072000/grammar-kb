"""作业卷管理：题库答案列 + 历次逐题对错（爱问云同步）。

数据模型（grammar.db）：
- homework_question 加 answer 列（TEXT，可空）——真答案文本预留位；
  当前爱问云测验全部为对/错标记（questionType=[3]），无答案原文，
  因此「已经考过的题目答案」落地为逐题历次对错（homework_q_result）。
- homework_q_result(lecture_number, qnum, exam_date, correct, source)：
  每场已批改测验的逐题对错（aicloud result==10 → correct=1）。

同步规则：只取 status==60（已出分）的测验；按测验名解析讲次；
(lecture, qnum, date, source) 幂等 upsert。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Optional

from . import dbreg
from .aicloud_sync import AiCloudClient, _lecture_of
from .paths import data_path

# ---- 错题题库（data/wrongbank.json：测验平台错题导出，含作业卷答案+解析） ----

_WRONG_BANK_CACHE: Optional[dict] = None


def load_wrong_bank() -> dict:
    """错题题库：讲次 → 题号 → {q 题面, exp 解析}，进程内缓存。

    由 scripts/build_wrongbank.py 从测验平台导出生成；此前内嵌前端
    wrongbank.js（随 bundle 人人可见），出代码后收到后端数据文件，
    只经教师专属端点下发。
    """
    global _WRONG_BANK_CACHE
    if _WRONG_BANK_CACHE is None:
        try:
            _WRONG_BANK_CACHE = json.loads(
                data_path("wrongbank.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _WRONG_BANK_CACHE = {}
    return _WRONG_BANK_CACHE


_SCHEMA = """
CREATE TABLE IF NOT EXISTS homework_q_result (
    lecture_number INTEGER NOT NULL,
    qnum           INTEGER NOT NULL,
    exam_date      TEXT NOT NULL,
    correct        INTEGER NOT NULL,          -- 1 对 / 0 错
    source         TEXT NOT NULL DEFAULT 'aicloud',
    PRIMARY KEY (lecture_number, qnum, exam_date, source)
);
"""


def _db_path() -> str:
    """grammar.db 路径：$GRAMMAR_KB_DB → 仓库根 data/grammar.db（dbreg 统一口径）。"""
    return dbreg.grammar_db_path()


class HomeworkAdmin:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _db_path()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            # answer 列迁移（存量库补列）
            cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(homework_question)")}
            if "answer" not in cols:
                conn.execute(
                    "ALTER TABLE homework_question ADD COLUMN answer TEXT")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- 总览：讲次 × 题数 × 答案覆盖（纯题库；作答记录在批改中心） ----
    def papers(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT lecture_number, COUNT(*) n,"
                " SUM(CASE WHEN answer IS NOT NULL AND answer != '' THEN 1 ELSE 0 END) ans"
                " FROM homework_question GROUP BY lecture_number").fetchall()
        out = [{"lecture": r["lecture_number"], "questions": r["n"],
                "answered": r["ans"] or 0} for r in rows]
        out.sort(key=lambda x: x["lecture"])
        return out

    # ---- 单讲次：题目 + 答案（无作答记录） ----
    def paper_detail(self, lecture: int) -> dict:
        with self._connect() as conn:
            qs = conn.execute(
                "SELECT qnum, section, stem, options_json, answer"
                " FROM homework_question WHERE lecture_number = ?"
                " ORDER BY qnum", (int(lecture),),
            ).fetchall()
        questions = [{
            "qnum": q["qnum"], "section": q["section"],
            "stem": q["stem"] or "",
            "options_json": q["options_json"] or "[]",
            "answer": q["answer"],
        } for q in qs]
        return {"lecture": int(lecture), "questions": questions}

    def put_answer(self, lecture: int, qnum: int, answer: str) -> bool:
        """单题答案编辑（教师人工补录）。"""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE homework_question SET answer = ?"
                " WHERE lecture_number = ? AND qnum = ?",
                (str(answer or "")[:200], int(lecture), int(qnum)),
            )
            return cur.rowcount > 0

    # ---- 爱问云同步（只取已出分测验的逐题对错） ----
    def sync_aicloud(self, phone: str, password: str,
                     group_id: int = 3350581) -> dict:
        client = AiCloudClient(phone, password, group_id)
        client.login()
        quizzes = client.quiz_list()
        report = {"synced": 0, "skipped": 0, "rows": 0, "detail": []}
        with self._connect() as conn:
            for it in sorted(quizzes, key=lambda x: x.get("createTime") or 0):
                name = ((it.get("examInfo") or [{}])[0].get("name")) or "?"
                if it.get("status") != 60:
                    report["skipped"] += 1
                    continue
                lec = _lecture_of(name)
                if lec is None:
                    report["skipped"] += 1
                    continue
                obj = client._post_json(
                    "question/exam/nc/examInfo",
                    {"examResultId": it["_id"]}).get("obj") or {}
                end_ms = (obj.get("endTime")) or (
                    (obj.get("examResultInfo") or {}).get("endTime")) or 0
                if not end_ms:
                    report["skipped"] += 1
                    continue
                import datetime as _dt
                d = _dt.datetime.fromtimestamp(end_ms / 1000).strftime("%Y-%m-%d")
                qs = obj.get("questions") or []
                uas = {a["id"]: a for a in obj.get("userAnswers") or []}
                n = 0
                for idx, q in enumerate(qs, start=1):
                    ua = uas.get(q.get("id")) or {}
                    correct = 1 if str(ua.get("result")) == "10" else 0
                    conn.execute(
                        "INSERT OR REPLACE INTO homework_q_result"
                        " (lecture_number, qnum, exam_date, correct, source)"
                        " VALUES (?,?,?,?,'aicloud')", (lec, idx, d, correct))
                    n += 1
                report["synced"] += 1
                report["rows"] += n
                report["detail"].append(
                    {"lecture": lec, "date": d, "questions": n})
        return report

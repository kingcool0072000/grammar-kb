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
from pathlib import Path
from typing import Optional

from .aicloud_sync import AiCloudClient, _lecture_of

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
    import os
    return os.environ.get("GRAMMAR_KB_DB") or str(
        Path(__file__).resolve().parent.parent / "data" / "grammar.db")


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

    # ---- 总览：讲次 × 题数 × 同步覆盖 ----
    def papers(self) -> list[dict]:
        with self._connect() as conn:
            q = conn.execute(
                "SELECT lecture_number, COUNT(*) n FROM homework_question"
                " GROUP BY lecture_number").fetchall()
            r = conn.execute(
                "SELECT lecture_number, COUNT(DISTINCT exam_date) attempts,"
                " SUM(correct) ok, COUNT(*) total"
                " FROM homework_q_result GROUP BY lecture_number").fetchall()
        cov = {x["lecture_number"]: x for x in r}
        out = []
        for row in q:
            lec = row["lecture_number"]
            c = cov.get(lec)
            out.append({
                "lecture": lec, "questions": row["n"],
                "attempts": c["attempts"] if c else 0,
                "answered": c["total"] if c else 0,
                "ok": c["ok"] if c else 0,
            })
        out.sort(key=lambda x: x["lecture"])
        return out

    # ---- 单讲次：题目 + 历次对错 ----
    def paper_detail(self, lecture: int) -> dict:
        with self._connect() as conn:
            qs = conn.execute(
                "SELECT id, qnum, section, stem, options_json, answer"
                " FROM homework_question WHERE lecture_number = ?"
                " ORDER BY qnum", (int(lecture),),
            ).fetchall()
            rs = conn.execute(
                "SELECT qnum, exam_date, correct FROM homework_q_result"
                " WHERE lecture_number = ? ORDER BY exam_date, qnum",
                (int(lecture),),
            ).fetchall()
        history: dict[int, list] = {}
        for r in rs:
            history.setdefault(r["qnum"], []).append(
                {"date": r["exam_date"], "correct": r["correct"]})
        questions = []
        for q in qs:
            h = history.get(q["qnum"]) or []
            n_ok = sum(1 for x in h if x["correct"])
            status = ("steady" if h and n_ok == len(h)
                      else "never" if h and n_ok == 0
                      else "mixed" if h else "new")
            questions.append({
                "qnum": q["qnum"], "section": q["section"],
                "stem": (q["stem"] or "")[:80],
                "answer": q["answer"],
                "history": h, "status": status,
            })
        return {"lecture": int(lecture), "questions": questions}

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

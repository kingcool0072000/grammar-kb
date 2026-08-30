"""FCE 真题查询层（data/fce.db 只读，配合 server /fce-papers 端点）。

库由 :mod:`grammar_kb.fce_paper` 入库（4 Test × 87 题）；本模块只做读取拼装：
- ``list_papers()``：概览（4 套 Test 各 paper 的题数）
- ``get_paper(test_id)``：单套 Test 全部分区 + 题目（选项 JSON 已解为数组，
  答案随题目返回；stem 为空的 RUE P1 题，题干在分区的 passage 里）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

# paper 展示顺序与中文名
PAPER_ORDER = [
    "Reading and Use of English",
    "Writing",
    "Listening",
    "Speaking",
]
PAPER_CN = {
    "Reading and Use of English": "读写（Reading and Use of English）",
    "Writing": "写作（Writing）",
    "Listening": "听力（Listening）",
    "Speaking": "口语（Speaking）",
}


def _default_db_path() -> str:
    """fce.db 默认路径：环境变量 → iCloud Drive → 项目 data/。

    iCloud 放整个 fce.db（题库 + 练习/录音提交），学生端提交的录音随
    iCloud 同步到教师端设备（与 exam_store 的成绩库同一套模式；
    库为默认 journal 模式单文件，iCloud 整文件同步可靠）。
    首次启用 iCloud 时若云端无库而本地有，一次性整库拷贝上云。
    """
    import os
    import shutil

    env = os.environ.get("GRAMMAR_KB_FCE_DB")
    if env:
        return env
    icloud = os.path.expanduser(
        "~/Library/Mobile Documents/com~apple~CloudDocs/grammar-kb/fce.db"
    )
    icloud_dir = os.path.dirname(icloud)
    cloud_root = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs")
    if os.path.isdir(cloud_root):
        if not os.path.exists(icloud):
            os.makedirs(icloud_dir, exist_ok=True)
            local = Path(__file__).resolve().parent.parent / "data" / "fce.db"
            if local.exists():
                shutil.copy2(local, icloud)  # 一次性迁移：本地全量上云
        return icloud
    return str(Path(__file__).resolve().parent.parent / "data" / "fce.db")


class FcePaperStore:
    """fce.db 只读访问。db 不存在时各方法返回空结果（端点给 404/空列表）。"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _default_db_path()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def list_papers(self) -> list[dict]:
        """概览：每套 Test 的 paper/part → 题数。库不存在返回 []。"""
        if not Path(self.db_path).exists():
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT test_id, paper, part, COUNT(*) AS n"
                " FROM fce_question GROUP BY test_id, paper, part"
                " ORDER BY test_id, paper, part"
            ).fetchall()
        tests: dict[int, dict] = {}
        for r in rows:
            t = tests.setdefault(
                r["test_id"], {"test_id": r["test_id"], "title": f"Test {r['test_id']}", "papers": {}}
            )
            t["papers"].setdefault(r["paper"], []).append({"part": r["part"], "questions": r["n"]})
        return [tests[k] for k in sorted(tests)]

    def get_paper(self, test_id: int) -> Optional[dict]:
        """单套 Test：sections + questions（选项转数组、按 paper/part 排序）。"""
        if not Path(self.db_path).exists():
            return None
        with self._connect() as conn:
            test = conn.execute(
                "SELECT id, title FROM fce_test WHERE id = ?", (test_id,)
            ).fetchone()
            if test is None:
                return None
            sections = conn.execute(
                "SELECT id, paper, part, instruction, passage, page_start, page_end"
                " FROM fce_section WHERE test_id = ? ORDER BY ord",
                (test_id,),
            ).fetchall()
            questions = conn.execute(
                "SELECT paper, part, qnum, type, stem, stem2, keyword,"
                " options_json, answer FROM fce_question"
                " WHERE test_id = ? ORDER BY paper, part, qnum",
                (test_id,),
            ).fetchall()

        def paper_rank(p: str) -> tuple[int, str]:
            try:
                return (PAPER_ORDER.index(p), p)
            except ValueError:
                return (len(PAPER_ORDER), p)

        sec_by_key = {(s["paper"], s["part"]): dict(s) for s in sections}
        out_sections = []
        for s in sorted(sections, key=lambda s: (paper_rank(s["paper"]), s["part"])):
            d = dict(s)
            d["paper_cn"] = PAPER_CN.get(s["paper"], s["paper"])
            qs = [
                q
                for q in questions
                if q["paper"] == s["paper"] and q["part"] == s["part"]
            ]
            d["questions"] = [_question_out(q) for q in qs]
            out_sections.append(d)
        return {"test_id": test_id, "title": test["title"], "sections": out_sections}


def _question_out(q: sqlite3.Row) -> dict:
    try:
        options = json.loads(q["options_json"] or "{}")
    except (ValueError, TypeError):
        options = {}
    return {
        "qnum": q["qnum"],
        "type": q["type"],
        "stem": q["stem"] or "",
        "stem2": q["stem2"] or "",
        "keyword": q["keyword"] or "",
        # 选项数组：[["A", "text"], ...] 顺序稳定，前端无需关心 key 序
        "options": sorted(options.items()),
        "answer": q["answer"] or "",
    }

# --------------------------------------------------------------------------- #
# 练习记录 + 自动批改
# --------------------------------------------------------------------------- #

import re as _re
from datetime import datetime, timezone as _tz

SUBMISSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS fce_submission (
    id          INTEGER PRIMARY KEY,
    user        TEXT NOT NULL,
    test_id     INTEGER NOT NULL,
    paper       TEXT NOT NULL,
    part        INTEGER NOT NULL,
    answers_json TEXT DEFAULT '{}',
    detail_json TEXT DEFAULT '[]',
    auto_score  INTEGER DEFAULT 0,
    total       INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'auto',
    teacher_score  INTEGER,
    teacher_comment TEXT DEFAULT '',
    duration_sec INTEGER,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_fce_sub_user ON fce_submission(user, created_at);
"""


def _ensure_duration_column(conn: sqlite3.Connection) -> None:
    """老库补列（duration_sec 后加的字段，ALTER 幂等）。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(fce_submission)")}
    if "duration_sec" not in cols:
        conn.execute("ALTER TABLE fce_submission ADD COLUMN duration_sec INTEGER")
        conn.commit()

# 字母作答的题型（比对首字母）；作文题型不自动批改
_LETTER_TYPES = {"mcq3", "mcq4", "matchSentence", "matchPerson", "matchOpinion"}
_ESSAY_TYPES = {"essay", "essayOption"}


def _norm_word(s: str) -> str:
    """填空答案归一化：去空白/标点，小写。"""
    return _re.sub(r"[^a-z0-9]", "", (s or "").lower())


def grade_answer(qtype: str, expected: str, given: str) -> bool:
    """单题自动批改。

    - 字母题：比对首字母（忽略大小写/粘连）；
    - 填空/词形变换：expected 按 "/"、"OR" 切出可接受答案（"had / held" 表示
      两个都对），任一归一化相等即对；
    - 关键词改写（transform）：key 里的 "|" 标记句子空档位置而非备选——
      接受「任一侧片段 / 两侧拼接的全句 / 片段包含在作答里」。
    """
    if given is None or not str(given).strip():
        return False
    if not expected:
        return False
    if qtype in _LETTER_TYPES:
        return str(given).strip().upper()[:1] == expected.strip().upper()[:1]
    g = _norm_word(given)
    if qtype == "transform":
        # key 结构：备选答案以 "OR" 分隔；每个备选内部 "|" 标记空档位置、
        # "/" 标记可替换词（含 'd 缩写等）。接受「与某备选全句相等 / 命中
        # 空档片段 / 与某备选高相似（缩写差异兜底）」。
        import difflib

        for alt in _re.split(r"\bOR\b", expected):
            joined = _norm_word(alt)
            if not joined:
                continue
            if g == joined or difflib.SequenceMatcher(None, g, joined).ratio() >= 0.85:
                return True
            for frag in _re.split(r"\|", alt):
                f = _norm_word(frag)
                if f and (g == f or (len(f) > 5 and f in g)):
                    return True
        return False
    alts = {_norm_word(a) for a in _re.split(r"/|\bOR\b", expected)}
    alts.discard("")
    return g in alts


class FceSubmissionStore:
    """练习提交（fce.db 同库，独立表；需与 FcePaperStore 同一 db 路径）。"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _default_db_path()
        if Path(self.db_path).exists():
            conn = self._connect()
            conn.executescript(SUBMISSION_SCHEMA)
            conn.commit()
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(SUBMISSION_SCHEMA)
        _ensure_duration_column(conn)
        return conn

    def submit(
        self, user: str, test_id: int, paper: str, part: int, answers: dict,
        duration_sec: Optional[int] = None,
    ) -> dict:
        """提交一次大题练习：自动批改并落库，返回记录 + 逐题明细。"""
        with self._connect() as conn:
            qs = conn.execute(
                "SELECT qnum, type, answer FROM fce_question"
                " WHERE test_id=? AND paper=? AND part=? ORDER BY qnum",
                (test_id, paper, part),
            ).fetchall()
        if not qs:
            raise KeyError(f"题目不存在: Test {test_id} {paper} Part {part}")

        is_essay = all(q["type"] in _ESSAY_TYPES for q in qs)
        detail = []
        score = total = 0
        for q in qs:
            given = answers.get(str(q["qnum"]), answers.get(q["qnum"], ""))
            if is_essay:
                continue  # 作文不打分，整单待批改
            if not (q["answer"] or "").strip():
                continue  # 无标准答案的题不计数
            total += 1
            ok = grade_answer(q["type"], q["answer"], str(given))
            score += 1 if ok else 0
            detail.append(
                {"qnum": q["qnum"], "given": str(given), "expected": q["answer"], "correct": ok}
            )

        status = "pending" if is_essay else "auto"
        now = datetime.now(_tz.utc).isoformat(timespec="seconds")
        dur = int(duration_sec) if duration_sec and int(duration_sec) > 0 else None
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO fce_submission (user, test_id, paper, part, answers_json,"
                " detail_json, auto_score, total, status, duration_sec, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (user, test_id, paper, part, json.dumps(answers, ensure_ascii=False),
                 json.dumps(detail, ensure_ascii=False), score, total, status, dur, now),
            )
            row = conn.execute(
                "SELECT * FROM fce_submission WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return _sub_out(row)

    def list(self, user: Optional[str] = None, status: Optional[str] = None,
             limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM fce_submission"
        conds, args = [], []
        if user:
            conds.append("user = ?")
            args.append(user)
        if status:
            conds.append("status = ?")
            args.append(status)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._connect() as conn:
            return [_sub_out(r) for r in conn.execute(sql, args).fetchall()]

    def get(self, sub_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM fce_submission WHERE id = ?", (sub_id,)
            ).fetchone()
        return _sub_out(row) if row else None

    def grade(self, sub_id: int, teacher_score: Optional[int],
              teacher_comment: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM fce_submission WHERE id = ?", (sub_id,)
            ).fetchone()
            if row is None:
                return None
            sets, args = ["status = 'graded'", "teacher_comment = ?"], [teacher_comment or ""]
            if teacher_score is not None:
                sets.append("teacher_score = ?")
                args.append(teacher_score)
            args.append(sub_id)
            conn.execute(
                f"UPDATE fce_submission SET {', '.join(sets)} WHERE id = ?", args
            )
            row = conn.execute(
                "SELECT * FROM fce_submission WHERE id = ?", (sub_id,)
            ).fetchone()
        return _sub_out(row)

    def delete(self, sub_id: int) -> bool:
        """删除一条练习提交（教师清理数据）。"""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM fce_submission WHERE id = ?", (sub_id,))
            return cur.rowcount > 0


def _sub_out(r: sqlite3.Row) -> dict:
    def _j(s, default):
        try:
            return json.loads(s) if s else default
        except (ValueError, TypeError):
            return default

    return {
        "id": r["id"], "user": r["user"], "test_id": r["test_id"],
        "paper": r["paper"], "part": r["part"],
        "answers": _j(r["answers_json"], {}),
        "detail": _j(r["detail_json"], []),
        "auto_score": r["auto_score"], "total": r["total"],
        "status": r["status"],
        "teacher_score": r["teacher_score"], "teacher_comment": r["teacher_comment"],
        "duration_sec": r["duration_sec"],
        "created_at": r["created_at"],
    }

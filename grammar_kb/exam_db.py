"""作业成绩存储：独立的 exam.db（不与 grammar.db 混放）。

grammar.db 由 ingest 全量重建，放成绩会在重建时丢失；成绩是持续追加的
用户数据，独立建库（同 ecdict.db 一样由 .gitignore 的 data/*.db 覆盖）。

表结构极简：一条作答一行，错题题号存 JSON 数组。
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "exam.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS exam_record (
    id         TEXT PRIMARY KEY,
    lecture    INTEGER NOT NULL,
    date       TEXT NOT NULL,
    score      INTEGER NOT NULL,
    wrong      TEXT NOT NULL,      -- JSON 数组，如 [3,7,10]
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exam_lecture ON exam_record(lecture);
"""


class ExamStore:
    """作业成绩的增删查。单进程（uvicorn 单 worker）下 SQLite 足够。"""

    def __init__(self, db_path: str | Path = DEFAULT_DB):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        return self._conn

    def list(self) -> list[dict]:
        rows = self._get_conn().execute(
            "SELECT * FROM exam_record ORDER BY date DESC, created_at DESC"
        ).fetchall()
        return [self._to_dict(r) for r in rows]

    def add(self, lecture: int, date: str, score: int, wrong: list[int]) -> dict:
        rec = {
            "id": f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}",
            "lecture": int(lecture),
            "date": date,
            "score": int(score),
            "wrong": sorted({int(q) for q in wrong}),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._get_conn().execute(
            "INSERT INTO exam_record VALUES (?,?,?,?,?,?)",
            (rec["id"], rec["lecture"], rec["date"], rec["score"],
             json.dumps(rec["wrong"]), rec["created_at"]),
        )
        self._conn.commit()
        return rec

    def delete(self, record_id: str) -> bool:
        cur = self._get_conn().execute(
            "DELETE FROM exam_record WHERE id = ?", (record_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def _to_dict(r: sqlite3.Row) -> dict:
        return {
            "id": r["id"],
            "lecture": r["lecture"],
            "date": r["date"],
            "score": r["score"],
            "wrong": json.loads(r["wrong"]),
            "created_at": r["created_at"],
        }

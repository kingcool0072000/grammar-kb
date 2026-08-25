"""作业成绩持久化（SQLite，独立于主库 grammar.db）。

库文件路径解析顺序（:func:`default_exam_db_path`）：

1. 环境变量 ``GRAMMAR_KB_EXAM_DB``
2. iCloud Drive 可用时 ``~/Library/Mobile Documents/com~apple~CloudDocs/grammar-kb/exam.db``
   —— 数据量小，放云端由 iCloud 在多台设备间同步
3. ``<cwd>/data/exam.db``

注意：iCloud 对 SQLite 的 WAL 侧车文件同步不可靠，因此本库显式用
默认 journal 模式（DELETE），保证单文件自包含。
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS exam_records (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lecture    INTEGER NOT NULL,
    date       TEXT    NOT NULL,              -- YYYY-MM-DD
    score      INTEGER NOT NULL DEFAULT 0,
    wrong      TEXT    NOT NULL DEFAULT '[]', -- JSON 数组 [题号]
    updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


def default_exam_db_path() -> str:
    """成绩库默认路径：环境变量 → iCloud Drive → 项目 data/。"""
    env = os.environ.get("GRAMMAR_KB_EXAM_DB")
    if env:
        return env
    icloud = os.path.expanduser(
        "~/Library/Mobile Documents/com~apple~CloudDocs/grammar-kb/exam.db"
    )
    if os.path.isdir(os.path.dirname(icloud)) or os.path.isdir(
        os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs")
    ):
        return icloud
    return os.path.join(os.getcwd(), "data", "exam.db")


class ExamStore:
    """作业成绩记录的增删改查。每次操作独立开连接，兼容 iCloud 整文件替换。"""

    def __init__(self, path: Optional[str] = None):
        self.path = path or default_exam_db_path()
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with self._conn() as con:
            con.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "lecture": row["lecture"],
            "date": row["date"],
            "score": row["score"],
            "wrong": json.loads(row["wrong"]),
            "updatedAt": row["updated_at"],
        }

    def list(self) -> list[dict[str, Any]]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT * FROM exam_records ORDER BY date DESC, id DESC"
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def add(self, lecture: int, date: str, score: int = 0, wrong: list[int] | None = None) -> dict[str, Any]:
        with self._conn() as con:
            cur = con.execute(
                "INSERT INTO exam_records (lecture, date, score, wrong) VALUES (?, ?, ?, ?)",
                (lecture, date, score, json.dumps(sorted(wrong or []))),
            )
            row = con.execute(
                "SELECT * FROM exam_records WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return self._row_to_dict(row)

    def update(
        self,
        id: int,
        lecture: int,
        date: str,
        score: int = 0,
        wrong: list[int] | None = None,
    ) -> Optional[dict[str, Any]]:
        """整条更新；记录不存在返回 None。"""
        with self._conn() as con:
            cur = con.execute(
                "UPDATE exam_records SET lecture = ?, date = ?, score = ?, wrong = ?,"
                " updated_at = datetime('now') WHERE id = ?",
                (lecture, date, score, json.dumps(sorted(wrong or [])), id),
            )
            if cur.rowcount == 0:
                return None
            row = con.execute("SELECT * FROM exam_records WHERE id = ?", (id,)).fetchone()
        return self._row_to_dict(row)

    def delete(self, id: int) -> bool:
        with self._conn() as con:
            return con.execute("DELETE FROM exam_records WHERE id = ?", (id,)).rowcount > 0

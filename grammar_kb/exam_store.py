"""作业成绩持久化（SQLite，独立于主库 grammar.db）。

库文件路径解析顺序（:func:`default_exam_db_path`）：

1. 环境变量 ``GRAMMAR_KB_EXAM_DB``
2. ``<cwd>/data/exam.db``

真实数据只认生产服务器上的库；本机 data/exam.db 仅作开发测试。
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing, contextmanager
from typing import Any, Iterator, Optional

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
    """成绩库默认路径：环境变量 → 项目 data/。"""
    env = os.environ.get("GRAMMAR_KB_EXAM_DB")
    if env:
        return env
    return os.path.join(os.getcwd(), "data", "exam.db")


class ExamStore:
    """作业成绩记录的增删改查。每次操作独立开短连接。"""

    def __init__(self, path: Optional[str] = None):
        self.path = path or default_exam_db_path()
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with self._tx() as con:
            con.executescript(_SCHEMA)
            # v2：按学生隔离（批改中心筛选用）。存量行归 malin（历史单孩数据）
            cols = {r[1] for r in con.execute("PRAGMA table_info(exam_records)")}
            if "user" not in cols:
                con.execute(
                    "ALTER TABLE exam_records ADD COLUMN"
                    " user TEXT NOT NULL DEFAULT 'malin'")
            # v3：成绩类型（lecture=哈一课程测验 / hw=哈一作业卷）。
            # 存量行全部归课程测验（此前只有这一种来源）。
            if "kind" not in cols:
                con.execute(
                    "ALTER TABLE exam_records ADD COLUMN"
                    " kind TEXT NOT NULL DEFAULT 'lecture'")

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """事务 + 连接关闭双保证。

        sqlite3 连接的 ``with con`` 只管事务提交/回滚、不关连接；本库靠
        「每次操作短连接」保证句柄不悬滞，连接必须显式 close。
        """
        with closing(self._conn()) as con, con:
            yield con

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "lecture": row["lecture"],
            "date": row["date"],
            "score": row["score"],
            "wrong": json.loads(row["wrong"]),
            "user": row["user"],
            "kind": row["kind"],
            "updatedAt": row["updated_at"],
        }

    def list(self, user: Optional[str] = None) -> list[dict[str, Any]]:
        """成绩列表；user 过滤（None=全部，教师看板用）。"""
        with self._tx() as con:
            if user:
                rows = con.execute(
                    "SELECT * FROM exam_records WHERE user = ?"
                    " ORDER BY date DESC, id DESC", (user,)).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM exam_records ORDER BY date DESC, id DESC"
                ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def add(self, lecture: int, date: str, score: int = 0,
            wrong: list[int] | None = None, user: str = "malin",
            kind: str = "lecture") -> dict[str, Any]:
        kind = kind if kind in ("lecture", "hw") else "lecture"
        with self._tx() as con:
            cur = con.execute(
                "INSERT INTO exam_records (lecture, date, score, wrong, user, kind)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (lecture, date, score,
                 json.dumps(sorted(set(wrong or []))), (user or "malin")[:60], kind),
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
        kind: str = "lecture",
    ) -> Optional[dict[str, Any]]:
        """整条更新；记录不存在返回 None。"""
        kind = kind if kind in ("lecture", "hw") else "lecture"
        with self._tx() as con:
            cur = con.execute(
                "UPDATE exam_records SET lecture = ?, date = ?, score = ?, wrong = ?,"
                " kind = ?, updated_at = datetime('now') WHERE id = ?",
                (lecture, date, score, json.dumps(sorted(set(wrong or []))), kind, id),
            )
            if cur.rowcount == 0:
                return None
            row = con.execute("SELECT * FROM exam_records WHERE id = ?", (id,)).fetchone()
        return self._row_to_dict(row)

    def delete(self, id: int) -> bool:
        with self._tx() as con:
            return con.execute("DELETE FROM exam_records WHERE id = ?", (id,)).rowcount > 0

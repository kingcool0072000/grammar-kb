"""背单词练习成绩上报（fce.db 同库，随 iCloud 同步到教师端）。

学生端每完成一组练习（recite.js finish）自动上报：题数、首答错词数、
正确率、用时、错词列表与练习模式（打字输入/翻面自评）。教师端在
「批改中心」查看。数据量极小，不设删除（历史即成长记录）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone as _tz
from pathlib import Path
from typing import Optional

from .fce_query import _default_db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS recite_sessions (
    id          INTEGER PRIMARY KEY,
    user        TEXT NOT NULL,
    total       INTEGER NOT NULL DEFAULT 0,
    wrong       INTEGER NOT NULL DEFAULT 0,
    acc         INTEGER NOT NULL DEFAULT 0,
    duration_sec INTEGER DEFAULT 0,
    wrong_words TEXT DEFAULT '[]',
    mode        TEXT DEFAULT '',
    scope       TEXT DEFAULT '',
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_rs_user ON recite_sessions(user, created_at);
"""


class ReciteStore:
    """recite_sessions 读写。"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _default_db_path()
        if Path(self.db_path).exists():
            with self._connect() as conn:
                conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        return conn

    def submit(
        self, user: str, total: int, wrong: int, acc: int, duration_sec: int,
        wrong_words: Optional[list], mode: str = "", scope: str = "",
    ) -> dict:
        if total <= 0:
            raise ValueError("题数须为正")
        wrong_words = [str(w)[:60] for w in (wrong_words or [])][:50]
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO recite_sessions (user, total, wrong, acc, duration_sec,"
                " wrong_words, mode, scope, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (user, int(total), int(wrong), max(0, min(int(acc), 100)),
                 max(0, int(duration_sec or 0)), json.dumps(wrong_words, ensure_ascii=False),
                 mode or "", scope or "",
                 datetime.now(_tz.utc).isoformat(timespec="seconds")),
            )
            row = conn.execute(
                "SELECT * FROM recite_sessions WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        return _out(row)

    def list(self, user: Optional[str] = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM recite_sessions"
        args: tuple = ()
        if user:
            sql += " WHERE user = ?"
            args = (user,)
        sql += " ORDER BY id DESC LIMIT ?"
        args += (int(limit),)
        with self._connect() as conn:
            return [_out(r) for r in conn.execute(sql, args).fetchall()]


def _out(r: sqlite3.Row) -> dict:
    try:
        wrong_words = json.loads(r["wrong_words"] or "[]")
    except (ValueError, TypeError):
        wrong_words = []
    return {
        "id": r["id"], "user": r["user"], "total": r["total"], "wrong": r["wrong"],
        "acc": r["acc"], "duration_sec": r["duration_sec"],
        "wrong_words": wrong_words, "mode": r["mode"], "scope": r["scope"],
        "created_at": r["created_at"],
    }

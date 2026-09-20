"""专题学习：学生自学手册的「我学完了」进度记录（fce.db 同库，随 iCloud 同步）。

设计意图：
- 专题内容（手册 HTML/讲解）在前端静态打包，后端只管「谁在哪个专题学完了
  哪些主题」。一条 (user, topic_id) 一行，done_keys 存主题 key 的 JSON 数组，
  PUT 幂等覆盖；历史 key 不丢（completed_at 记首次时间，重置后可再学）。
- 专题对学生的可见性由表内 assignment 决定：assigned_user 为空＝全学生可见，
  指定用户＝仅该用户（本专题绑定 malin）。学生 GET 只能看到自己的专题；
  教师可看全部，用于跟进。
- 数据量小，不设删除。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone as _tz
from pathlib import Path
from typing import Optional

from .fce_query import _default_db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS topic_progress (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user           TEXT NOT NULL,
    topic_id       TEXT NOT NULL,
    assigned_user  TEXT NOT NULL DEFAULT '',
    done_keys      TEXT NOT NULL DEFAULT '[]',
    completed_at   TEXT DEFAULT '',
    updated_at     TEXT DEFAULT '',
    UNIQUE(user, topic_id)
);
"""


class TopicStore:
    """topic_progress 读写。"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _default_db_path()
        if Path(self.db_path).exists():
            # 打开一次触发建表（SCHEMA 在 _connect 内执行）
            self._connect().close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        return conn

    # ---- 读取 ----
    def get(self, user: str, topic_id: str) -> dict:
        """单个专题进度（学生本人或教师；无记录返回空进度）。"""
        user = (user or "").strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM topic_progress WHERE user = ? AND topic_id = ?",
                (user, topic_id),
            ).fetchone()
        return _out(row) if row else {
            "user": user, "topic_id": topic_id, "done_keys": [],
            "completed_at": "", "updated_at": "",
        }

    def list_for(self, viewer: str, role: str) -> list[dict]:
        """专题进度列表：教师看全部，学生只看 assigned 给自己（或全员）的行。"""
        viewer = (viewer or "").strip()
        with self._connect() as conn:
            if role == "teacher":
                rows = conn.execute(
                    "SELECT * FROM topic_progress ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM topic_progress WHERE user = ?"
                    " AND (assigned_user = '' OR assigned_user = ?)"
                    " ORDER BY updated_at DESC",
                    (viewer, viewer),
                ).fetchall()
        return [_out(r) for r in rows]

    # ---- 写入 ----
    def put(
        self, user: str, topic_id: str, done_keys: list[str], *,
        assigned_user: str = "",
    ) -> dict:
        """幂等覆盖某用户某专题的已完成主题集合。

        done_keys 去重排序后存 JSON；全部主题完成时记 completed_at
        （已有值不覆盖，重置后再学完会更新为新一轮时间）。
        assigned_user 仅在首次落行时写入（内容归属由前端静态清单声明，
        后端不重复维护）。
        """
        user = (user or "").strip()[:60]
        topic_id = (topic_id or "").strip()[:64]
        if not user or not topic_id:
            raise ValueError("user 与 topic_id 不能为空")
        keys = sorted({str(k).strip()[:40] for k in (done_keys or []) if str(k).strip()})
        now = datetime.now(_tz.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            old = conn.execute(
                "SELECT assigned_user, completed_at FROM topic_progress"
                " WHERE user = ? AND topic_id = ?",
                (user, topic_id),
            ).fetchone()
            assigned = (old["assigned_user"] if old else "") or (assigned_user or "").strip()[:60]
            completed_at = (old["completed_at"] if old else "") or ""
            if not completed_at and keys:
                completed_at = now  # 首次有完成项即记；全清空则保留历史
            conn.execute(
                "INSERT OR REPLACE INTO topic_progress"
                " (user, topic_id, assigned_user, done_keys, completed_at, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (user, topic_id, assigned,
                 json.dumps(keys, ensure_ascii=False), completed_at, now),
            )
            row = conn.execute(
                "SELECT * FROM topic_progress WHERE user = ? AND topic_id = ?",
                (user, topic_id),
            ).fetchone()
        return _out(row)


def _out(row: sqlite3.Row) -> dict:
    try:
        keys = json.loads(row["done_keys"] or "[]")
    except (ValueError, TypeError):
        keys = []
    return {
        "user": row["user"],
        "topic_id": row["topic_id"],
        "assigned_user": row["assigned_user"] or "",
        "done_keys": keys if isinstance(keys, list) else [],
        "completed_at": row["completed_at"] or "",
        "updated_at": row["updated_at"] or "",
    }

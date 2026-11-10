"""阅读更多（readingmore）：杂志阅读历史·只读查询。

杂志双页阅读器已于 2026-11-10 退役（静态页与心跳/统计端点已删除，
完整旧版见归档 tag archive/readingmore）。本模块仅保留教师学习日志
的只读查询端点 GET /readingmore/api/sessions：历史数据在
data/readingmore.db（rm_sessions / rm_lookups），不再新增写入。

鉴权由 server/app.py 既有 auth 中间件统一完成：教师登录后携带
Bearer token 访问；数据按 user 隔离（统计属个人学习数据）。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from . import dbreg

_router = APIRouter(prefix="/readingmore/api")


def _db_path() -> Path:
    """惰性解析库路径：环境变量可能在模块导入后才设（测试隔离场景）。"""
    return Path(dbreg.readingmore_db_path())


def _conn() -> sqlite3.Connection:
    db = _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


@contextmanager
def _tx():
    """连接即用即关（sqlite3 的 with 只管事务、不关连接）。"""
    c = _conn()
    try:
        with c:
            yield c
    finally:
        c.close()


def _init_db() -> None:
    """幂等建表：历史库已存在时无操作；新环境保证查询不因缺表而崩。"""
    with _tx() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS rm_sessions (
            id           TEXT PRIMARY KEY,          -- 客户端生成，跨心跳幂等
            user         TEXT NOT NULL,
            device       TEXT NOT NULL DEFAULT '',
            book         TEXT NOT NULL DEFAULT '',
            start        INTEGER NOT NULL,
            end          INTEGER NOT NULL,
            total_ms     INTEGER NOT NULL DEFAULT 0,   -- 页面可见时长
            focus_ms     INTEGER NOT NULL DEFAULT 0,   -- 窗口焦点时长
            engaged_ms   INTEGER NOT NULL DEFAULT 0,   -- 专注学习（焦点+2min 内有交互）
            turn_count   INTEGER NOT NULL DEFAULT 0,
            pages_json   TEXT NOT NULL DEFAULT '{}',   -- {页码: 累计毫秒}
            clicks_json  TEXT NOT NULL DEFAULT '{}',   -- {按钮标签: 次数}
            ways_json    TEXT NOT NULL DEFAULT '{}',   -- {翻页/缩放方式: 次数}
            vocab_ops    TEXT NOT NULL DEFAULT '{}',   -- {add/del/export/clear: n}
            youdao_count INTEGER NOT NULL DEFAULT 0,
            path_pts     INTEGER NOT NULL DEFAULT 0,   -- 轨迹点数（轨迹本体不上传）
            updated_at   INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_rm_sessions_user ON rm_sessions(user, start);
        CREATE TABLE IF NOT EXISTS rm_lookups (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            sid     TEXT NOT NULL,
            user    TEXT NOT NULL,
            word    TEXT NOT NULL,
            hit     INTEGER NOT NULL,                -- 1=本地词典命中
            ts      INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_rm_lookups_user ON rm_lookups(user, ts);
        CREATE INDEX IF NOT EXISTS ix_rm_lookups_sid ON rm_lookups(sid);
        """)


def _clamp_int(v, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return max(lo, min(hi, n))


@_router.get("/sessions")
def list_sessions(request: Request, user: str = "", limit: int = 100) -> dict:
    """教师端学习日志：杂志阅读会话列表（按 user 可选过滤，只读历史）。

    教师专属（学生由 server/app.py 鉴权中间件先拦 403，这里兜底再校验）；
    started_at 输出 UTC ISO 串（带 Z），与 focus 会话前端时间处理约定一致。
    """
    if getattr(request.state, "role", "") != "teacher":
        raise HTTPException(status_code=403, detail="仅教师可查看")
    lim = _clamp_int(limit, 1, 200)
    with _tx() as c:
        rows = c.execute(
            """SELECT s.id, s.user, s.device, s.book, s.start,
                      s.total_ms, s.engaged_ms, s.turn_count, s.vocab_ops,
                      (SELECT COUNT(*) FROM rm_lookups l WHERE l.sid = s.id) lookups
               FROM rm_sessions s
               WHERE (? = '' OR s.user = ?)
               ORDER BY s.start DESC LIMIT ?""",
            (user, user, lim)).fetchall()
        items = []
        for r in rows:
            try:
                vocab_add = int(json.loads(r["vocab_ops"] or "{}").get("add", 0))
            except (ValueError, TypeError):
                vocab_add = 0
            items.append({
                "id": r["id"], "user": r["user"], "book": r["book"],
                "device": r["device"],
                "started_at": datetime.fromtimestamp(
                    r["start"] / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "total_sec": round(r["total_ms"] / 1000),
                "engaged_sec": round(r["engaged_ms"] / 1000),
                "turns": r["turn_count"], "lookups": r["lookups"],
                "vocab_add": vocab_add,
            })
    return {"code": 0, "message": "ok", "data": items}


router = _router

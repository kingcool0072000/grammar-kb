"""阅读更多（readingmore）：杂志双页阅读器的会话统计服务。

独立数据文件 data/readingmore.db（与 fce/grammar/library 等并列），
不与任何现有表耦合；前端静态文件在 web/dist/readingmore/，由根
StaticFiles 天然服务，路由前缀 /readingmore/api/*。

鉴权由 server.py 既有 auth 中间件统一完成：学生/教师登录后携带
Bearer token 访问；数据按 user 隔离（统计属个人学习数据）。
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

_router = APIRouter(prefix="/readingmore/api")


def _db_path() -> Path:
    """惰性解析库路径：环境变量可能在模块导入后才设（测试隔离场景）。"""
    data_dir = Path(os.environ.get("GRAMMAR_KB_DATA_DIR")
                    or Path(__file__).resolve().parent.parent / "data")
    return data_dir / "readingmore.db"


def _conn() -> sqlite3.Connection:
    db = _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _init_db() -> None:
    with _conn() as c:
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


class SessionHeartbeat(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    device: str = Field(default="", max_length=64)
    book: str = Field(default="", max_length=120)
    start: int
    end: int
    total_ms: int = 0
    focus_ms: int = 0
    engaged_ms: int = 0
    turn_count: int = 0
    pages: dict[str, int] = Field(default_factory=dict)
    clicks: dict[str, int] = Field(default_factory=dict)
    ways: dict[str, int] = Field(default_factory=dict)
    vocab: dict[str, int] = Field(default_factory=dict)
    youdao: int = 0
    path_pts: int = 0
    # 查词明细：增量上报（客户端只传自上次心跳后新增的）
    new_lookups: list[dict[str, Any]] = Field(default_factory=list)


def _clamp_int(v: Any, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return max(lo, min(hi, n))


def _user_of(request: Request) -> str:
    user = getattr(request.state, "user", "")
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return user


@_router.post("/session")
def post_session(rec: SessionHeartbeat, request: Request) -> dict:
    """60s 心跳 upsert：同一会话重复上报覆盖更新，幂等。"""
    user = _user_of(request)
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    lookups = rec.new_lookups[:200]
    with _conn() as c:
        c.execute(
            """INSERT INTO rm_sessions
               (id,user,device,book,start,end,total_ms,focus_ms,engaged_ms,
                turn_count,pages_json,clicks_json,ways_json,vocab_ops,
                youdao_count,path_pts,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 device=excluded.device, book=excluded.book,
                 end=excluded.end,
                 total_ms=excluded.total_ms, focus_ms=excluded.focus_ms,
                 engaged_ms=excluded.engaged_ms, turn_count=excluded.turn_count,
                 pages_json=excluded.pages_json, clicks_json=excluded.clicks_json,
                 ways_json=excluded.ways_json, vocab_ops=excluded.vocab_ops,
                 youdao_count=excluded.youdao_count, path_pts=excluded.path_pts,
                 updated_at=excluded.updated_at""",
            (rec.id, user, rec.device[:64], rec.book[:120],
             _clamp_int(rec.start, 0, 99999999999999), _clamp_int(rec.end, 0, 99999999999999),
             _clamp_int(rec.total_ms, 0, 24 * 3600 * 1000),
             _clamp_int(rec.focus_ms, 0, 24 * 3600 * 1000),
             _clamp_int(rec.engaged_ms, 0, 24 * 3600 * 1000),
             _clamp_int(rec.turn_count, 0, 100000),
             json.dumps(rec.pages, ensure_ascii=False)[:20000],
             json.dumps(rec.clicks, ensure_ascii=False)[:8000],
             json.dumps(rec.ways, ensure_ascii=False)[:4000],
             json.dumps(rec.vocab, ensure_ascii=False)[:1000],
             _clamp_int(rec.youdao, 0, 100000),
             _clamp_int(rec.path_pts, 0, 100000),
             now),
        )
        for lk in lookups:
            c.execute(
                "INSERT INTO rm_lookups(sid,user,word,hit,ts) VALUES (?,?,?,?,?)",
                (rec.id, user, str(lk.get("w", ""))[:40][:40],
                 1 if lk.get("hit") else 0, _clamp_int(lk.get("t"), 0, 99999999999999)),
            )
        # 防膨胀：单会话查词明细超 5000 行时丢最旧的（正常远达不到）
        n = c.execute("SELECT COUNT(*) FROM rm_lookups WHERE sid=?", (rec.id,)).fetchone()[0]
        if n > 5000:
            c.execute(
                """DELETE FROM rm_lookups WHERE id IN (
                     SELECT id FROM rm_lookups WHERE sid=? ORDER BY id LIMIT -1 OFFSET 5000)""",
                (rec.id,),
            )
    return {"code": 0, "message": "ok", "data": {"received": len(lookups)}}


@_router.get("/sessions")
def list_sessions(request: Request, user: str = "", limit: int = 100) -> dict:
    """教师端学习日志：杂志阅读会话列表（按 user 可选过滤）。

    教师专属（学生由 server.py 鉴权中间件先拦 403，这里兜底再校验）；
    started_at 输出 UTC ISO 串（带 Z），与 focus 会话前端时间处理约定一致。
    """
    if getattr(request.state, "role", "") != "teacher":
        raise HTTPException(status_code=403, detail="仅教师可查看")
    lim = _clamp_int(limit, 1, 200)
    with _conn() as c:
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


def _agg(c: sqlite3.Connection, sql: str, args: tuple = ()) -> Any:
    return c.execute(sql, args).fetchone()[0]


@_router.get("/stats")
def get_stats(request: Request) -> dict:
    """聚合统计（当前登录用户全部会话）。"""
    user = _user_of(request)
    with _conn() as c:
        row = c.execute(
            """SELECT COUNT(*) n, COALESCE(SUM(total_ms),0) total_ms,
                      COALESCE(SUM(focus_ms),0) focus_ms,
                      COALESCE(SUM(engaged_ms),0) engaged_ms,
                      COALESCE(SUM(turn_count),0) turns,
                      COALESCE(SUM(youdao_count),0) youdao
               FROM rm_sessions WHERE user=?""", (user,)).fetchone()
        days: list[dict] = []
        for r in c.execute(
            """SELECT date(start/1000,'unixepoch','localtime') d,
                      SUM(engaged_ms) ms, COUNT(*) n
               FROM rm_sessions WHERE user=?
               GROUP BY d ORDER BY d DESC LIMIT 14""", (user,)):
            days.append({"date": r["d"], "engaged_ms": r["ms"], "sessions": r["n"]})
        pages: dict = {}
        for r in c.execute(
                "SELECT pages_json FROM rm_sessions WHERE user=?", (user,)):
            try:
                for k, v in json.loads(r["pages_json"] or "{}").items():
                    pages[k] = pages.get(k, 0) + int(v)
            except (ValueError, TypeError):
                continue
        top_pages = sorted(pages.items(), key=lambda kv: -kv[1])[:10]
        words = [{"word": r["word"], "n": r["n"], "miss": r["miss"]}
                 for r in c.execute(
                     """SELECT word, COUNT(*) n, SUM(1-hit) miss
                        FROM rm_lookups WHERE user=?
                        GROUP BY word ORDER BY n DESC LIMIT 20""", (user,))]
        lookup_total = _agg(c, "SELECT COUNT(*) FROM rm_lookups WHERE user=?", (user,))
        vocab_add = 0
        for r in c.execute(
                "SELECT vocab_ops FROM rm_sessions WHERE user=?", (user,)):
            try:
                vocab_add += int(json.loads(r["vocab_ops"] or "{}").get("add", 0))
            except (ValueError, TypeError):
                continue
    total = row["total_ms"]
    return {"code": 0, "message": "ok", "data": {
        "sessions": row["n"], "total_ms": total,
        "focus_ms": row["focus_ms"], "engaged_ms": row["engaged_ms"],
        "focus_pct": round(row["engaged_ms"] / total * 100) if total else 0,
        "turns": row["turns"], "youdao": row["youdao"],
        "lookup_total": lookup_total, "vocab_add": vocab_add,
        "days": days, "top_pages": top_pages, "top_words": words,
    }}


router = _router

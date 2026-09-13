"""专注力采集：泛读馆阅读器的学生会话行为数据（fce.db 同库，随 iCloud 同步）。

设计意图：
- 学生端阅读器（focus.js）静默上报一组会话累计值：活跃/离开时长、
  滚动与翻章、查词/播放/预习等互动计数、阅读进度起止、鼠标乱晃指数
  与逐分钟进度折线。每 60 秒心跳重传累计值，按 session_id
  INSERT OR REPLACE 幂等覆盖；created_at 保留首次上报时间。
- 综合评分（compute_score，0-100）在服务端按固定透明公式计算，
  明细 score_detail 随行落库返回，教师端可直接解释「为什么是这个分」。
- 与 fce.db 同库：iCloud 把成绩库同步到教师设备（本库保持默认
  journal 模式、单文件自包含，惯例见 exam_store.py 开头注释）。
- 数据量小，不设删除（历史即成长记录）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone as _tz
from pathlib import Path
from typing import Optional

from .fce_query import _default_db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS focus_sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT NOT NULL UNIQUE,
    user             TEXT NOT NULL,
    book_id          INTEGER,
    book_title       TEXT,
    started_at       TEXT,
    ended_at         TEXT,
    total_sec        INTEGER,
    active_sec       INTEGER,
    away_count       INTEGER,
    away_sec         INTEGER,
    longest_away_sec INTEGER,
    scroll_count     INTEGER,
    chapter_navs     INTEGER,
    lookups          INTEGER,
    plays            INTEGER,
    prep_opens       INTEGER,
    prep_starts      INTEGER,
    percent_start    REAL,
    percent_end      REAL,
    fast_scroll_flags INTEGER,
    mouse_metrics    TEXT,
    polyline         TEXT,
    score            INTEGER,
    score_detail     TEXT,
    created_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_fs_user ON focus_sessions(user, created_at);
"""


def _clamp_int(v, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = 0
    return max(lo, min(hi, n))


def _clamp_float(v, lo: float, hi: float) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        n = 0.0
    return max(lo, min(hi, n))


def _jitter_of(metrics) -> float:
    """从 mouse_metrics（dict 或 JSON 串）取乱晃指数 jitter，夹取 0-1。"""
    if isinstance(metrics, str):
        try:
            metrics = json.loads(metrics)
        except (ValueError, TypeError):
            metrics = None
    if not isinstance(metrics, dict):
        return 0.0
    try:
        return max(0.0, min(1.0, float(metrics.get("jitter") or 0)))
    except (TypeError, ValueError):
        return 0.0


def compute_score(d: dict) -> tuple[int, dict]:
    """综合评分（纯函数，不碰库）：正向四项合计权重 100，惩罚两项扣减。

    - effective_ratio：active_sec/total_sec，权重 45（total=0 时全 0 分）
    - engagement：每 5 分钟一次有分互动（查词×2）记满，权重 25
    - progress：每小时推进 90 个百分点记满，权重 15
    - 互动保底：engagement≥0.5 即记满，权重 15
    - away_penalty：每 10 分钟一次离开扣 0.5，封顶 12
    - jitter_penalty：乱晃指数 >0.6 起扣，1.0 扣满 5
    """
    total = max(0, int(d.get("total_sec") or 0))
    active = max(0, int(d.get("active_sec") or 0))
    lookups = max(0, int(d.get("lookups") or 0))
    plays = max(0, int(d.get("plays") or 0))
    prep_opens = max(0, int(d.get("prep_opens") or 0))
    away_count = max(0, int(d.get("away_count") or 0))
    p_start = _clamp_float(d.get("percent_start") or 0, 0, 100)
    p_end = _clamp_float(d.get("percent_end") or 0, 0, 100)

    zeros = {"effective_ratio": 0.0, "engagement": 0.0, "progress": 0.0,
             "away_penalty": 0.0, "jitter_penalty": 0.0}
    if total <= 0:
        return 0, zeros

    er = min(1.0, active / total)
    engagement = min(1.0, (lookups * 2 + plays + prep_opens) / max(1.0, total / 300))
    progress = min(1.0, max(0.0, p_end - p_start) / max(1.0, total / 600 * 15))
    away_penalty = min(12.0, away_count * (total / 600) * 0.5)
    jitter = _jitter_of(d.get("mouse_metrics"))
    jitter_penalty = min(5.0, (jitter - 0.6) * 12.5) if jitter > 0.6 else 0.0

    score = max(0, min(100, round(
        45 * er + 25 * engagement + 15 * progress + 15 * min(1.0, engagement * 2)
        - away_penalty - jitter_penalty)))
    detail = {
        "effective_ratio": round(er, 3),
        "engagement": round(engagement, 3),
        "progress": round(progress, 3),
        "away_penalty": round(away_penalty, 3),
        "jitter_penalty": round(jitter_penalty, 3),
    }
    return score, detail


class FocusStore:
    """focus_sessions 读写。"""

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
        self, user: str, session_id: str, *,
        book_id: Optional[int] = None, book_title: str = "",
        started_at: str = "", ended_at: str = "",
        total_sec: int = 0, active_sec: int = 0,
        away_count: int = 0, away_sec: int = 0, longest_away_sec: int = 0,
        scroll_count: int = 0, chapter_navs: int = 0,
        lookups: int = 0, plays: int = 0,
        prep_opens: int = 0, prep_starts: int = 0,
        percent_start: float = 0.0, percent_end: float = 0.0,
        fast_scroll_flags: int = 0, mouse_metrics: Optional[dict] = None,
        polyline: Optional[str] = None,
    ) -> dict:
        """按 session_id 幂等写入（心跳重传覆盖累计值），返回完整行。"""
        user = (user or "").strip()[:60]
        sid = (session_id or "").strip()[:64]
        if not user or not sid:
            raise ValueError("user 与 session_id 不能为空")
        row = {
            "session_id": sid,
            "user": user,
            "book_id": int(book_id) if book_id is not None else None,
            "book_title": (book_title or "")[:200],
            "started_at": (started_at or "")[:32],
            "ended_at": (ended_at or "")[:32],
            "total_sec": _clamp_int(total_sec, 0, 14400),
            "active_sec": _clamp_int(active_sec, 0, 14400),
            "away_count": _clamp_int(away_count, 0, 9999),
            "away_sec": _clamp_int(away_sec, 0, 14400),
            "longest_away_sec": _clamp_int(longest_away_sec, 0, 14400),
            "scroll_count": _clamp_int(scroll_count, 0, 9999),
            "chapter_navs": _clamp_int(chapter_navs, 0, 9999),
            "lookups": _clamp_int(lookups, 0, 9999),
            "plays": _clamp_int(plays, 0, 9999),
            "prep_opens": _clamp_int(prep_opens, 0, 9999),
            "prep_starts": _clamp_int(prep_starts, 0, 9999),
            "percent_start": _clamp_float(percent_start, 0, 100),
            "percent_end": _clamp_float(percent_end, 0, 100),
            "fast_scroll_flags": _clamp_int(fast_scroll_flags, 0, 9999),
        }
        score, detail = compute_score({**row, "mouse_metrics": mouse_metrics})
        metrics_json = ""
        if mouse_metrics:
            try:
                metrics_json = json.dumps(mouse_metrics, ensure_ascii=False)[:20000]
            except (TypeError, ValueError):
                metrics_json = ""
        with self._connect() as conn:
            old = conn.execute(
                "SELECT created_at FROM focus_sessions WHERE session_id = ?", (sid,)
            ).fetchone()
            created_at = ((old["created_at"] if old else None)
                          or datetime.now(_tz.utc).isoformat(timespec="seconds"))
            conn.execute(
                "INSERT OR REPLACE INTO focus_sessions (session_id, user, book_id,"
                " book_title, started_at, ended_at, total_sec, active_sec, away_count,"
                " away_sec, longest_away_sec, scroll_count, chapter_navs, lookups,"
                " plays, prep_opens, prep_starts, percent_start, percent_end,"
                " fast_scroll_flags, mouse_metrics, polyline, score, score_detail,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, row["user"], row["book_id"], row["book_title"],
                 row["started_at"], row["ended_at"], row["total_sec"],
                 row["active_sec"], row["away_count"], row["away_sec"],
                 row["longest_away_sec"], row["scroll_count"], row["chapter_navs"],
                 row["lookups"], row["plays"], row["prep_opens"], row["prep_starts"],
                 row["percent_start"], row["percent_end"], row["fast_scroll_flags"],
                 metrics_json, (polyline or "")[:200000], score,
                 json.dumps(detail, ensure_ascii=False), created_at),
            )
            saved = conn.execute(
                "SELECT * FROM focus_sessions WHERE session_id = ?", (sid,)
            ).fetchone()
        return _out(saved, detail=True)

    def list(self, user: Optional[str] = None, limit: int = 100) -> list[dict]:
        """会话列表（最近优先；不含 polyline/mouse_metrics 明细，控制体积）。"""
        sql = "SELECT * FROM focus_sessions"
        args: tuple = ()
        if user:
            sql += " WHERE user = ?"
            args = (user,)
        sql += " ORDER BY id DESC LIMIT ?"
        args += (int(limit),)
        with self._connect() as conn:
            return [_out(r) for r in conn.execute(sql, args).fetchall()]

    def get(self, id: int) -> Optional[dict]:
        """单条详情（含已解析的 mouse_metrics/polyline），不存在返回 None。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM focus_sessions WHERE id = ?", (int(id),)
            ).fetchone()
        return _out(row, detail=True) if row else None


def _maybe_json(s: Optional[str]):
    if not s:
        return None
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return s  # 非合法 JSON（如被截断的折线）原样返回


def _out(r: sqlite3.Row, detail: bool = False) -> dict:
    try:
        score_detail = json.loads(r["score_detail"] or "{}")
    except (ValueError, TypeError):
        score_detail = {}
    out = {
        "id": r["id"], "session_id": r["session_id"], "user": r["user"],
        "book_id": r["book_id"], "book_title": r["book_title"],
        "started_at": r["started_at"], "ended_at": r["ended_at"],
        "total_sec": r["total_sec"], "active_sec": r["active_sec"],
        "away_count": r["away_count"], "away_sec": r["away_sec"],
        "longest_away_sec": r["longest_away_sec"],
        "scroll_count": r["scroll_count"], "chapter_navs": r["chapter_navs"],
        "lookups": r["lookups"], "plays": r["plays"],
        "prep_opens": r["prep_opens"], "prep_starts": r["prep_starts"],
        "percent_start": r["percent_start"], "percent_end": r["percent_end"],
        "fast_scroll_flags": r["fast_scroll_flags"],
        "score": r["score"], "score_detail": score_detail,
        "created_at": r["created_at"],
        "mouse_metrics": None, "polyline": None,  # 列表默认不带明细
    }
    if detail:
        try:
            out["mouse_metrics"] = json.loads(r["mouse_metrics"]) if r["mouse_metrics"] else None
        except (ValueError, TypeError):
            out["mouse_metrics"] = None
        out["polyline"] = _maybe_json(r["polyline"])
    return out

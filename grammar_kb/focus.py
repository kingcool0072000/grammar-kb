"""专注力采集：泛读馆阅读器的学生会话行为数据（fce.db 同库）。

设计意图：
- 学生端阅读器（focus.js）静默上报一组会话累计值：活跃/离开时长、
  滚动与翻章、查词/播放/预习等互动计数、阅读进度起止、鼠标乱晃指数
  与逐分钟进度折线。每 60 秒心跳重传累计值，按 session_id
  INSERT OR REPLACE 幂等覆盖；created_at 保留首次上报时间。
- 综合评分（compute_score，0-100）在服务端按固定透明公式计算，
  明细 score_detail 随行落库返回，教师端可直接解释「为什么是这个分」。
- 与 fce.db 同库（本库保持默认 journal 模式、单文件自包含）。
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
    created_at       TEXT,
    -- v2：内容监控 + 学习范围（存量库经 _migrate_v2 ALTER 补列）
    lookup_words     TEXT,
    speak_words      TEXT,
    selected_words   TEXT,
    activity_json    TEXT,
    module           TEXT DEFAULT 'library',
    range_start      REAL,
    range_end        REAL,
    range_label      TEXT DEFAULT ''
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


_WORD_TYPES = ("select", "lookup", "speak", "chapter", "prep", "record")


def _clean_words(data, cap: int = 50) -> list[dict]:
    """词表清洗：接受 [[w,n]] / [{w,n}] / {w:n}，统一 [{"w","n"}] 按 n 降序截前 cap。"""
    items: list[tuple[str, int]] = []
    if isinstance(data, dict):
        for w, n in data.items():
            items.append((str(w), n))
    elif isinstance(data, list):
        for it in data:
            if isinstance(it, dict):
                items.append((str(it.get("w") or it.get("word") or ""),
                              it.get("n", it.get("count", 1))))
            elif isinstance(it, (list, tuple)) and len(it) >= 1:
                n = it[1] if len(it) >= 2 else 1
                items.append((str(it[0]), n))
    out = []
    for w, n in items:
        w = w.strip()[:60]
        if not w:
            continue
        try:
            n = max(1, min(99, int(n)))
        except (TypeError, ValueError):
            n = 1
        out.append({"w": w, "n": n})
    out.sort(key=lambda x: -x["n"])
    return out[:cap]


def _clean_activity(data, cap: int = 500) -> list[dict]:
    """activity 时间线清洗：[{t,type,w?}]，t 秒夹取、type 白名单、截前 cap。"""
    if not isinstance(data, list):
        return []
    out = []
    for it in data:
        if not isinstance(it, dict):
            continue
        t = _clamp_int(it.get("t"), 0, 86400)
        typ = str(it.get("type") or "")
        if typ not in _WORD_TYPES:
            continue
        rec = {"t": t, "type": typ}
        w = str(it.get("w") or "").strip()[:60]
        if w:
            rec["w"] = w
        out.append(rec)
    return out[:cap]


def _uniq_words(data) -> int:
    """词表去重词数（评分用；容错 dict/list 两形态）。"""
    if isinstance(data, dict):
        return len(data)
    if isinstance(data, list):
        ws = set()
        for it in data:
            if isinstance(it, dict):
                w = it.get("w") or it.get("word")
            elif isinstance(it, (list, tuple)) and it:
                w = it[0]
            else:
                w = None
            if w:
                ws.add(str(w))
        return len(ws)
    return 0


def _idle_gaps_of(metrics) -> list:
    if isinstance(metrics, str):
        try:
            metrics = json.loads(metrics)
        except (ValueError, TypeError):
            metrics = None
    if not isinstance(metrics, dict):
        return []
    gaps = metrics.get("idle_gaps")
    return gaps if isinstance(gaps, list) else []


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
             "away_penalty": 0.0, "jitter_penalty": 0.0, "idle_penalty": 0.0}
    if total <= 0:
        return 0, zeros

    # v2：内容互动密度——去重词数（同词重复查只计 1 次防刷分）；
    # 无词表数据（旧客户端）回退用计数字段估算去重数
    uniq_look = _uniq_words(d.get("lookup_words"))
    uniq_speak = _uniq_words(d.get("speak_words"))
    if uniq_look == 0 and lookups > 0:
        uniq_look = min(lookups, 20)
    if uniq_speak == 0 and plays > 0:
        uniq_speak = min(plays, 20)

    er = min(1.0, active / total)
    # 每 5 分钟 1.5 个不同词互动记满（查词双计）
    engagement = min(1.0, (uniq_look * 2 + uniq_speak + (1 if prep_opens else 0))
                     / max(1.0, total / 300 * 1.5))
    progress = min(1.0, max(0.0, p_end - p_start) / max(1.0, total / 600 * 15))
    away_penalty = min(12.0, away_count * (total / 600) * 0.5)
    jitter = _jitter_of(d.get("mouse_metrics"))
    jitter_penalty = min(5.0, (jitter - 0.6) * 12.5) if jitter > 0.6 else 0.0
    # v2：idle 惩罚——>5 分钟静止空档每段扣 4，封顶 8（拆分机制兜底残留）
    big_gaps = sum(1 for g in _idle_gaps_of(d.get("mouse_metrics"))
                   if isinstance(g, (list, tuple)) and len(g) >= 2
                   and (g[1] - g[0]) > 300)
    idle_penalty = min(8.0, big_gaps * 4.0)

    score = max(0, min(100, round(
        45 * er + 25 * engagement + 15 * progress + 15 * min(1.0, engagement * 2)
        - away_penalty - jitter_penalty - idle_penalty)))
    detail = {
        "effective_ratio": round(er, 3),
        "engagement": round(engagement, 3),
        "progress": round(progress, 3),
        "away_penalty": round(away_penalty, 3),
        "jitter_penalty": round(jitter_penalty, 3),
        "idle_penalty": round(idle_penalty, 3),
    }
    return score, detail


class FocusStore:
    """focus_sessions 读写。"""

    _V2_COLUMNS = ("lookup_words", "speak_words", "selected_words", "activity_json",
                   "module", "range_start", "range_end", "range_label")

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _default_db_path()
        if Path(self.db_path).exists():
            with self._connect() as conn:
                conn.executescript(SCHEMA)
                self._migrate_v2(conn)

    @staticmethod
    def _migrate_v2(conn: sqlite3.Connection) -> None:
        """存量库幂等补 v2 列（CREATE TABLE IF NOT EXISTS 对旧表不生效）。"""
        cols = {r[1] for r in conn.execute("PRAGMA table_info(focus_sessions)")}
        defaults = {"module": "'library'", "range_label": "''"}
        for col in FocusStore._V2_COLUMNS:
            if col not in cols:
                conn.execute(
                    f"ALTER TABLE focus_sessions ADD COLUMN {col} "
                    f"TEXT DEFAULT {defaults.get(col, 'NULL')}"
                    if col in defaults
                    else f"ALTER TABLE focus_sessions ADD COLUMN {col} TEXT"
                )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        self._migrate_v2(conn)
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
        module: str = "library", range_start: Optional[float] = None,
        range_end: Optional[float] = None, range_label: str = "",
        lookup_words=None, speak_words=None, selected_words=None,
        activity=None,
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
        module = module if module in ("library", "reading") else "library"
        lw = _clean_words(lookup_words)
        sw = _clean_words(speak_words)
        selw = _clean_words(selected_words)
        act = _clean_activity(activity)
        score, detail = compute_score({
            **row, "mouse_metrics": mouse_metrics,
            "lookup_words": lw, "speak_words": sw,
        })
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
            def _wj(items) -> str:
                return json.dumps(items, ensure_ascii=False)[:10000] if items else ""

            conn.execute(
                "INSERT OR REPLACE INTO focus_sessions (session_id, user, book_id,"
                " book_title, started_at, ended_at, total_sec, active_sec, away_count,"
                " away_sec, longest_away_sec, scroll_count, chapter_navs, lookups,"
                " plays, prep_opens, prep_starts, percent_start, percent_end,"
                " fast_scroll_flags, mouse_metrics, polyline, score, score_detail,"
                " created_at, lookup_words, speak_words, selected_words,"
                " activity_json, module, range_start, range_end, range_label)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, row["user"], row["book_id"], row["book_title"],
                 row["started_at"], row["ended_at"], row["total_sec"],
                 row["active_sec"], row["away_count"], row["away_sec"],
                 row["longest_away_sec"], row["scroll_count"], row["chapter_navs"],
                 row["lookups"], row["plays"], row["prep_opens"], row["prep_starts"],
                 row["percent_start"], row["percent_end"], row["fast_scroll_flags"],
                 metrics_json, (polyline or "")[:200000], score,
                 json.dumps(detail, ensure_ascii=False), created_at,
                 _wj(lw), _wj(sw), _wj(selw),
                 json.dumps(act, ensure_ascii=False)[:30000] if act else "",
                 module,
                 None if range_start is None else _clamp_float(range_start, 0, 100),
                 None if range_end is None else _clamp_float(range_end, 0, 100),
                 (range_label or "")[:120]),
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
        "module": r["module"] or "library",
        "range_label": r["range_label"] or "",
        "range_start": r["range_start"],
        "range_end": r["range_end"],
    }
    if detail:
        try:
            out["mouse_metrics"] = json.loads(r["mouse_metrics"]) if r["mouse_metrics"] else None
        except (ValueError, TypeError):
            out["mouse_metrics"] = None
        out["polyline"] = _maybe_json(r["polyline"])
        out["lookup_words"] = _maybe_json(r["lookup_words"]) or []
        out["speak_words"] = _maybe_json(r["speak_words"]) or []
        out["selected_words"] = _maybe_json(r["selected_words"]) or []
        out["activity"] = _maybe_json(r["activity_json"]) or []
    return out

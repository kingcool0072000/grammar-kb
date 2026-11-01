"""学情月报聚合（教师端 ``GET /analytics/summary`` 的数据层）。

把 2026-10 月人工挖数的 SQL 固化成可复用纯函数：入参一个学生 + 一个月，
四个库路径（缺省走 dbreg 环境变量口径），内部即用即关各开一条只读连接，
出参直接是响应 JSON 的 ``data``（契约字段名不得偏离，见 routes_analytics）。

时间口径（与人工分析一致，勿改）：
- 库内时间戳一律 UTC 且格式混杂（``2026-10-02T01:21:46Z`` 与
  ``2026-09-30 16:00:00`` 两种），过滤前必须 ``datetime()`` 归一；
- 北京 10 月 = UTC ``>= '2026-09-30 16:00'`` 且 ``< '2026-10-31 16:00'``；
- ``exam_records.date`` 是无时区日期串，按日期直接比；
- 「今天」用 :func:`grammar_kb.clock.today_cn`（北京钟）。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Iterator, Optional

from . import dbreg
from .clock import cn_date, today_cn

__all__ = ["build_summary", "parse_month", "prev_month", "month_utc_bounds"]

_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")

# FCE 全卷 part 数：Reading&Use of English 7 + Listening 4 + Writing 2 + Speaking 1。
# 人工月报固定用 14 作分母（Writing/Speaking 无客观题，也计入完成度）；
# plan_goals 口径需按周计划逐周对账，复杂度高，按约定取常量。
_FCE_PARTS_TOTAL = 14

_WRONG_WORDS_TOP = 15


# ---------------------------------------------------------------- 月份口径

def parse_month(month: Optional[str]) -> str:
    """校验月份参数 → ``YYYY-MM``。空/None = 当前北京月（clock 钟）。"""
    if month is None or not month.strip():
        return today_cn().strftime("%Y-%m")
    m = month.strip()
    if not _MONTH_RE.match(m):
        raise ValueError(f"month 须为 YYYY-MM（如 2026-10），收到 {month!r}")
    return m


def prev_month(month: str) -> str:
    """上一个日历月。"""
    first = date(int(month[:4]), int(month[5:7]), 1)
    last_of_prev = first - timedelta(days=1)
    return f"{last_of_prev.year:04d}-{last_of_prev.month:02d}"


def _next_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return f"{y:04d}-{m:02d}"


def month_utc_bounds(month: str) -> tuple[str, str]:
    """北京月份对应的 UTC 半开窗口 [start, end)，``YYYY-MM-DD HH:MM:SS``。

    北京 10 月 → ``('2026-09-30 16:00:00', '2026-10-31 16:00:00')``。
    库内 UTC 时间戳 ``datetime(col)`` 归一后与此窗口直接比较。
    """
    start_cn = datetime(int(month[:4]), int(month[5:7]), 1)
    end_cn = datetime.fromisoformat(_next_month(month) + "-01T00:00:00")
    lo = (start_cn - timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    hi = (end_cn - timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    return lo, hi


def month_date_bounds(month: str) -> tuple[str, str]:
    """纯日期列（exam_records.date）的比较窗口 [月初, 次月月初)。"""
    return month + "-01", _next_month(month) + "-01"


def month_last_day(month: str) -> date:
    return date.fromisoformat(_next_month(month) + "-01") - timedelta(days=1)


# ---------------------------------------------------------------- 连接与容错

@contextmanager
def _readonly(path: str) -> Iterator[sqlite3.Connection]:
    """只读短连接即用即关；库文件缺失时退化为空内存库（聚合结果为零值）。"""
    if path and os.path.exists(path):
        with dbreg.connect(path, readonly=True) as conn:
            yield conn
    else:
        with dbreg.connect(":memory:") as conn:
            yield conn


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list:
    """聚合查询一律容错：缺表/坏行返回空集，保证无数据月份不出 500。"""
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


def _one(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> Optional[sqlite3.Row]:
    try:
        return conn.execute(sql, args).fetchone()
    except sqlite3.Error:
        return None


def _r1(v) -> float:
    """保留 1 位小数（None/空 → 0.0，无数据月份字段零值）。"""
    return round(float(v), 1) if v is not None else 0.0


def _json_list(raw) -> list:
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


# ---------------------------------------------------------------- 卡片：作业卷均分

def _exam_rows(conn, user: str, lo: str, hi: str) -> list:
    return _rows(
        conn,
        "SELECT lecture, date, score, kind, wrong FROM exam_records"
        " WHERE user = ? AND date >= ? AND date < ? ORDER BY date, id",
        (user, lo, hi),
    )


def _lecture_titles(conn, numbers) -> dict[int, str]:
    ns = sorted({int(n) for n in numbers if n is not None})
    if not ns:
        return {}
    q = ",".join("?" * len(ns))
    return {
        r["number"]: r["title"]
        for r in _rows(conn, f"SELECT number, title FROM lecture WHERE number IN ({q})",
                       tuple(ns))
    }


def score_card(exam, grammar, user: str, month: str, pmonth: str) -> tuple[dict, list]:
    """作业卷均分卡片 + 逐卷明细（title 由 grammar.db lecture 反查）。"""
    lo, hi = month_date_bounds(month)
    plo, phi = month_date_bounds(pmonth)
    cur = _exam_rows(exam, user, lo, hi)
    prev = _exam_rows(exam, user, plo, phi)
    titles = _lecture_titles(grammar, [r["lecture"] for r in cur + prev])
    card = {
        "label": "作业卷均分",
        "value": _r1(sum(r["score"] for r in cur) / len(cur)) if cur else 0.0,
        "count": len(cur),
        "prev_value": _r1(sum(r["score"] for r in prev) / len(prev)) if prev else 0.0,
        "prev_count": len(prev),
    }
    scores = [
        {
            "date": r["date"],
            "lecture": r["lecture"],
            "title": titles.get(r["lecture"], ""),
            "kind": r["kind"] or "lecture",
            "score": r["score"],
            "wrong_count": len(_json_list(r["wrong"])),  # 解析失败容错 0
        }
        for r in cur
    ]
    return card, scores


# ---------------------------------------------------------------- 卡片：背词

def _recite_window(fc, user: str, lo: str, hi: str) -> dict:
    r = _one(
        fc,
        "SELECT COUNT(*) AS n, COALESCE(SUM(total), 0) AS t, COALESCE(SUM(wrong), 0) AS w,"
        f" COUNT(DISTINCT {cn_date('created_at')}) AS days"
        " FROM recite_sessions WHERE user = ?"
        " AND datetime(created_at) >= ? AND datetime(created_at) < ?",
        (user, lo, hi),
    )
    r = r or {"n": 0, "t": 0, "w": 0, "days": 0}
    n, t, w = r["n"] or 0, r["t"] or 0, r["w"] or 0
    return {
        "sessions": n,
        "days": r["days"] or 0,
        # 正确率 = (总词 - 错词) / 总词，保留 1 位
        "accuracy": _r1((t - w) / t * 100) if t > 0 else 0.0,
    }


def _recite_days(fc, user: str, lo: str, hi: str) -> list[str]:
    return [
        r[0]
        for r in _rows(
            fc,
            f"SELECT DISTINCT {cn_date('created_at')} AS d FROM recite_sessions"
            " WHERE user = ? AND datetime(created_at) >= ? AND datetime(created_at) < ?"
            " ORDER BY d",
            (user, lo, hi),
        )
        if r[0]
    ]


def month_gaps(days: list[str], month: str, today: date) -> list[dict]:
    """月内相邻学习日断档；末段断档到「月末与今天中较早者」为止。"""
    gaps: list[dict] = []
    for a, b in zip(days, days[1:]):
        da, db = date.fromisoformat(a), date.fromisoformat(b)
        span = (db - da).days
        if span > 1:
            gaps.append({
                "from": (da + timedelta(days=1)).isoformat(),
                "to": (db - timedelta(days=1)).isoformat(),
                "days": span - 1,
            })
    end = min(today, month_last_day(month))
    if days:
        tail = (end - date.fromisoformat(days[-1])).days
        if tail >= 1:
            gaps.append({
                "from": (date.fromisoformat(days[-1]) + timedelta(days=1)).isoformat(),
                "to": end.isoformat(),
                "days": tail,
            })
    else:  # 整月零学习日：按整月一段断档提示
        start = date.fromisoformat(month + "-01")
        if end >= start:
            gaps.append({
                "from": start.isoformat(), "to": end.isoformat(),
                "days": (end - start).days + 1,
            })
    return gaps


def mastered_card(fc, user: str, lo: str, hi: str) -> dict:
    """累计掌握词 + 本月新增（mastered_at 归一后落月）。"""
    total = _one(
        fc, "SELECT COUNT(*) AS n FROM recite_word_progress"
            " WHERE user = ? AND mastered_at IS NOT NULL", (user,))
    delta = _one(
        fc,
        "SELECT COUNT(*) AS n FROM recite_word_progress"
        " WHERE user = ? AND mastered_at IS NOT NULL"
        " AND datetime(mastered_at) >= ? AND datetime(mastered_at) < ?",
        (user, lo, hi),
    )
    return {
        "label": "累计掌握词",
        "value": (total or {"n": 0})["n"],
        "delta_month": (delta or {"n": 0})["n"],
    }


def backlog_card(fc, user: str, today: date) -> dict:
    """复习积压：recite_review 按 user 全量（跨月），到期即积压。

    - value = next_review <= 今天（北京）的词数；NULL = 已攻克，不算；
    - in_cycle = 该 user 周期内总行数（含已攻克，人工月报口径）；
    - stages = 按 stage 分桶计数（0=首错未复习，1..6=艾宾浩斯阶段）。
    """
    today_iso = today.isoformat()
    total = _one(fc, "SELECT COUNT(*) AS n FROM recite_review WHERE user = ?", (user,))
    due = _one(
        fc, "SELECT COUNT(*) AS n FROM recite_review WHERE user = ?"
            " AND next_review IS NOT NULL AND next_review != ''"
            " AND next_review <= ?", (user, today_iso))
    stages = _rows(
        fc, "SELECT stage, COUNT(*) AS n FROM recite_review WHERE user = ?"
            " GROUP BY stage ORDER BY stage", (user,))
    return {
        "label": "复习积压",
        "value": (due or {"n": 0})["n"],
        "in_cycle": (total or {"n": 0})["n"],
        "stages": [{"stage": r["stage"], "count": r["n"]} for r in stages],
    }


# ---------------------------------------------------------------- 卡片：泛读

def extensive_card(fc, library, user: str, lo: str, hi: str) -> dict:
    """泛读：focus_sessions module='library' 落月 + library.db 进度。"""
    agg = _one(
        fc,
        "SELECT COUNT(*) AS n, COALESCE(SUM(total_sec), 0) AS tsec,"
        " COALESCE(SUM(CASE WHEN COALESCE(total_sec, 0) < 60"
        " OR COALESCE(active_sec, 0) < 10 THEN 1 ELSE 0 END), 0) AS inv"
        " FROM focus_sessions WHERE user = ? AND module = 'library'"
        " AND datetime(created_at) >= ? AND datetime(created_at) < ?",
        (user, lo, hi),
    ) or {"n": 0, "tsec": 0, "inv": 0}
    sessions = agg["n"] or 0
    card = {
        "label": "泛读",
        "minutes": round((agg["tsec"] or 0) / 60),
        "sessions": sessions,
        # 无效会话 = 时长 <60s 或有效阅读 <10s（刷页/误触），占比 1 位内取整
        "invalid_pct": round((agg["inv"] or 0) / sessions * 100) if sessions else 0,
        "book": None,
        "progress_pct": None,
        "month_words": None,
    }
    book = _one(
        fc,
        "SELECT book_id, COUNT(*) AS n FROM focus_sessions"
        " WHERE user = ? AND module = 'library' AND book_id IS NOT NULL"
        " AND datetime(created_at) >= ? AND datetime(created_at) < ?"
        " GROUP BY book_id ORDER BY n DESC, book_id LIMIT 1",
        (user, lo, hi),
    )
    if book is None:
        return card
    # 进度行：优先本月主书，无则退回该生最近更新的一本（人工月报口径）
    prow = _one(
        library,
        "SELECT b.title AS title, p.percent AS percent FROM reading_progress p"
        " JOIN books b ON b.id = p.book_id WHERE p.user = ? AND p.book_id = ?",
        (user, book["book_id"]),
    ) or _one(
        library,
        "SELECT b.title AS title, p.percent AS percent FROM reading_progress p"
        " JOIN books b ON b.id = p.book_id WHERE p.user = ?"
        " ORDER BY p.updated_at DESC LIMIT 1",
        (user,),
    )
    if prow is None:
        return card
    card["book"] = prow["title"]
    card["progress_pct"] = _r1(prow["percent"]) if prow["percent"] is not None else None
    # 本月词数 = 进度差 × 全书章节词数合计（取整百；无进度差则 null）。
    # 进度差 = 当前进度 - 本月首个会话起点（会话起点即当时的书中位置）
    span = _one(
        fc,
        "SELECT MIN(percent_start) AS s, MAX(percent_end) AS e FROM focus_sessions"
        " WHERE user = ? AND module = 'library' AND book_id = ?"
        " AND datetime(created_at) >= ? AND datetime(created_at) < ?",
        (user, book["book_id"], lo, hi),
    )
    words_total = _one(library,
                       "SELECT COALESCE(SUM(word_count), 0) AS w FROM chapters"
                       " WHERE book_id = ?", (book["book_id"],))
    start = span["s"] if span else None
    end = prow["percent"] if prow["percent"] is not None else (span["e"] if span else None)
    if span and start is not None and end is not None and (words_total or {"w": 0})["w"]:
        delta = end - start
        if delta > 0:
            card["month_words"] = int(round(delta / 100 * words_total["w"] / 100.0) * 100)
    return card


# ---------------------------------------------------------------- 卡片：精读/视频/FCE/专题

def _scored_card(fc, table: str, user: str, lo: str, hi: str,
                 plo: str, phi: str, label: str) -> dict:
    """录音类卡片（精读/视频复述）：落月条数 + 教师评分均值 + 上月条数。

    只取聚合列，严禁 SELECT *——两表都带 audio_b64 MB 级大字段。
    """
    cur = _one(
        fc,
        f"SELECT COUNT(*) AS n, AVG(teacher_score) AS s FROM {table}"
        " WHERE user = ? AND datetime(created_at) >= ? AND datetime(created_at) < ?",
        (user, lo, hi),
    ) or {"n": 0, "s": None}
    prev = _one(
        fc,
        f"SELECT COUNT(*) AS n FROM {table}"
        " WHERE user = ? AND datetime(created_at) >= ? AND datetime(created_at) < ?",
        (user, plo, phi),
    ) or {"n": 0}
    return {
        "label": label,
        "recordings": cur["n"] or 0,
        "avg_score": _r1(cur["s"]),
        "prev_recordings": prev["n"] or 0,
    }


def fce_card(fc, user: str, lo: str, hi: str) -> dict:
    """FCE 客观题：correct/total 由各 part 的 auto_score/total 汇总。

    parts_done = 当月 distinct (paper, part) 提交数（一卷一部分算一格）；
    parts_total = 14 常量（全卷口径，见模块头注释）。
    """
    r = _one(
        fc,
        "SELECT COUNT(*) AS n, COALESCE(SUM(auto_score), 0) AS c,"
        " COALESCE(SUM(total), 0) AS t,"
        " COUNT(DISTINCT paper || '/' || part) AS p"
        " FROM fce_submission WHERE user = ?"
        " AND datetime(created_at) >= ? AND datetime(created_at) < ?",
        (user, lo, hi),
    ) or {"n": 0, "c": 0, "t": 0, "p": 0}
    return {
        "label": "FCE 客观题",
        "submissions": r["n"] or 0,
        "correct": r["c"] or 0,
        "total": r["t"] or 0,
        "parts_done": r["p"] or 0,
        "parts_total": _FCE_PARTS_TOTAL,
    }


def _node_count(content_json: str) -> int:
    """专题全部节点数（含未设 key 的节点）。口径同 topics.py 的 _count_nodes。"""
    try:
        nodes = json.loads(content_json or "{}").get("nodes") or []
    except (ValueError, TypeError, AttributeError):
        return 0

    def cnt(ns) -> int:
        return sum(1 + cnt(n.get("children") or [])
                   for n in ns if isinstance(n, dict))

    return cnt(nodes)


def _node_keys(content_json: str) -> set:
    """专题全部节点 key。口径同 topics.node_keys_from_content。"""
    try:
        data = json.loads(content_json or "{}")
    except (ValueError, TypeError, AttributeError):
        return set()
    keys: set = set()

    def walk(nodes) -> None:
        for n in nodes or []:
            if isinstance(n, dict):
                k = str(n.get("key") or "").strip()
                if k:
                    keys.add(k)
                walk(n.get("children"))

    walk(data.get("nodes"))
    return keys


def topics_items(fc, user: str, lo: str, hi: str) -> list:
    """落月完成的专题列表：done_keys 盖住全部节点才算完成
    （口径同 TopicStore._fully_done，内容缺失/坏 JSON = 未完成）。"""
    items: list[dict] = []
    for r in _rows(
        fc,
        "SELECT topic_id, done_keys FROM topic_progress"
        " WHERE user = ? AND COALESCE(completed_at, '') != ''"
        " AND datetime(completed_at) >= ? AND datetime(completed_at) < ?"
        " ORDER BY datetime(completed_at)",
        (user, lo, hi),
    ):
        c = _one(fc, "SELECT title, content FROM topic_content WHERE topic_id = ?",
                 (r["topic_id"],))
        if c is None:
            continue
        try:
            done = {str(k).strip() for k in _json_list(r["done_keys"])}
        except (ValueError, TypeError):
            continue
        need = _node_keys(c["content"])
        if not need or not done >= need:
            continue
        items.append({
            "topic_id": r["topic_id"],
            "title": c["title"],
            "nodes_total": _node_count(c["content"]),
            "nodes_done": len(done),
        })
    return items


def wrong_words_top(fc, user: str, lo: str, hi: str, limit: int = _WRONG_WORDS_TOP) -> list:
    """落月 recite_sessions.wrong_words JSON 聚合 Top N（解析失败容错跳过）。"""
    counter: Counter = Counter()
    for r in _rows(
        fc,
        "SELECT wrong_words FROM recite_sessions WHERE user = ?"
        " AND datetime(created_at) >= ? AND datetime(created_at) < ?",
        (user, lo, hi),
    ):
        words = [str(w).strip() for w in _json_list(r["wrong_words"])]
        counter.update(w for w in words if w)
    return [{"word": w, "count": c}
            for w, c in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


# ---------------------------------------------------------------- flags

def build_flags(cards: dict, daily: dict) -> list[dict]:
    """人工月报的预警规则，message 一律带具体数字。"""
    flags: list[dict] = []

    backlog = cards["backlog"]
    if backlog["value"] >= 150:
        pct = round(backlog["value"] / backlog["in_cycle"] * 100) \
            if backlog["in_cycle"] else 100
        flags.append({
            "level": "red", "key": "backlog",
            "message": f"艾宾浩斯积压 {backlog['value']} 词（{pct}%），先清逾期再放新词",
        })

    big = [g for g in daily["gaps"] if g["days"] >= 3]
    if big:
        g = max(big, key=lambda x: x["days"])
        flags.append({
            "level": "red", "key": "gaps",
            "message": f"最长断档 {g['days']} 天（{g['from']} 至 {g['to']}），需恢复背词节奏",
        })

    fce = cards["fce"]
    if fce["total"] > 0 and fce["correct"] / fce["total"] < 0.5:
        pct = round(fce["correct"] / fce["total"] * 100, 1)
        flags.append({
            "level": "red", "key": "fce",
            "message": f"FCE 客观题正确率 {pct}%（{fce['correct']}/{fce['total']}），低于 50%",
        })

    intensive = cards["intensive"]
    if intensive["prev_recordings"] > 0 \
            and intensive["recordings"] < intensive["prev_recordings"] * 0.5:
        flags.append({
            "level": "warn", "key": "intensive",
            "message": f"精读录音 {intensive['recordings']} 条，上月 "
                       f"{intensive['prev_recordings']} 条，下滑明显",
        })

    score = cards["score"]
    if score["count"] > 0 and score["value"] < 75:
        flags.append({
            "level": "warn", "key": "score",
            "message": f"作业卷均分 {score['value']}（{score['count']} 卷），低于 75 分",
        })

    return flags


# ---------------------------------------------------------------- 顶层聚合

def build_summary(user: str, month: Optional[str] = None,
                  paths: Optional[dict] = None) -> dict:
    """学情月报聚合入口。

    :param paths: 可选 ``{"fce": ..., "exam": ..., "grammar": ..., "library": ...}``
        覆盖库路径（测试用）；缺省走 dbreg 环境变量 → data/ 口径。
    """
    user = (user or "").strip()
    m = parse_month(month)
    pm = prev_month(m)
    lo, hi = month_utc_bounds(m)
    plo, phi = month_utc_bounds(pm)
    p = paths or {}
    with _readonly(p.get("fce") or dbreg.fce_db_path()) as fc, \
            _readonly(p.get("exam") or dbreg.exam_db_path()) as ex, \
            _readonly(p.get("grammar") or dbreg.grammar_db_path()) as gr, \
            _readonly(p.get("library") or dbreg.library_db_path()) as lb:
        score, scores = score_card(ex, gr, user, m, pm)
        cur_recite = _recite_window(fc, user, lo, hi)
        prev_recite = _recite_window(fc, user, plo, phi)
        days = _recite_days(fc, user, lo, hi)
        today = today_cn()
        cards = {
            "score": score,
            "recite_days": {
                "label": "背词学习日",
                "value": cur_recite["days"],
                "prev_value": prev_recite["days"],
            },
            "recite_accuracy": {
                "label": "背词正确率%",
                "value": cur_recite["accuracy"],
            },
            "mastered": mastered_card(fc, user, lo, hi),
            "backlog": backlog_card(fc, user, today),
            "extensive": extensive_card(fc, lb, user, lo, hi),
            "intensive": _scored_card(fc, "reading_recordings", user, lo, hi,
                                      plo, phi, "精读录音"),
            "video": _scored_card(fc, "video_retell", user, lo, hi,
                                  plo, phi, "视频复述"),
            "fce": fce_card(fc, user, lo, hi),
            "topics": {
                "label": "专题完成",
                "items": topics_items(fc, user, lo, hi),
            },
        }
        daily = {"recite_days": days, "gaps": month_gaps(days, m, today)}
        wrong_words = wrong_words_top(fc, user, lo, hi)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "user": user,
        "month": m,
        "generated_at": generated_at,
        "cards": cards,
        "scores": scores,
        "wrong_words": wrong_words,
        "daily": daily,
        "flags": build_flags(cards, daily),
    }

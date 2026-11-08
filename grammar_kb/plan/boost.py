"""提升档事件表 boost_log：每周 2 天「每天一件难事」的过关流水（fce.db）。

三种日粒度事件共用一张轻表（v3.0.0 设计 §2.3，避免三张碎片表）：
- memory：专题节点口诀卡翻面自评过关，
  item_ref = ``memory:{topic_id}:{node_key}``；
- retell：句型跟读复述提交（过关=教师批改 ≥7 分记 score），
  item_ref = ``retell:{pattern_id}``；
- 预留：周末输出三选一等日粒度选择。

写入口只有 :func:`record`（INSERT OR REPLACE，同 (user, date, item_ref)
重交即刷新）。读路径给三处消费：
- plan/schedule.py：欠账滚动判定（:func:`passed_map`）；
- plan/rules.py + plan/ctx.py：lecture_done 第三分支
  （:func:`lectures_advanced` / :func:`advanced_rows`，规则
  ``boost_gate_lecture``）；
- plan/loop.py：今日闭环 boost 格（:func:`day_done`）。

retell 的讲次归属查 ``grammar_kb.patterns``（并行车道实现，提供
get(item_id)）；模块未部署时该分支按未过关处理，不抛异常。
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import date as _date
from datetime import datetime, timedelta, timezone as _tz

from .. import dbreg
from ..clock import monday_of

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS boost_log (
  user TEXT NOT NULL, date TEXT NOT NULL, item_ref TEXT NOT NULL,
  kind TEXT NOT NULL, passed INTEGER NOT NULL DEFAULT 1,
  score REAL, ts TEXT,
  PRIMARY KEY (user, date, item_ref)
);
"""

# 默认提升日：周二/周四（设计已拍板）。只在 boost_items 非空、教师没配
# boost_days 时生效——零配置且无任务时不派生任何行。
DEFAULT_BOOST_DAYS = [2, 4]


def _connect():
    return dbreg.connect(dbreg.fce_db_path())


def _utc_now() -> str:
    return datetime.now(_tz.utc).isoformat(timespec="seconds")


# ---- item_ref 契约 ----

def item_ref_of(item: dict) -> str:
    """boost_items 条目 → item_ref；非法条目返回空串（派生时跳过）。"""
    kind = str((item or {}).get("type") or "")
    if kind == "memory":
        topic_id = str(item.get("topic_id") or "").strip()
        node_key = str(item.get("node_key") or "").strip()
        if topic_id and node_key:
            return f"memory:{topic_id}:{node_key}"
    elif kind == "retell":
        try:
            return f"retell:{int(item.get('pattern_id'))}"
        except (TypeError, ValueError):
            return ""
    return ""


def boost_days_of(tasks: dict, items: list | None = None) -> list[int]:
    """周计划 tasks 的提升日（周几数字，1=周一，升序去重）。

    教师配了 boost_days 用配置值；没配且 items 非空 → 默认 [2, 4]；
    都没有 → 空列表（当日不派生提升行）。
    """
    days: list[int] = []
    for x in (tasks or {}).get("boost_days") or []:
        try:
            n = int(x)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 7 and n not in days:
            days.append(n)
    items = (tasks or {}).get("boost_items") if items is None else items
    if not days and items:
        return list(DEFAULT_BOOST_DAYS)
    return sorted(days)


# ---- 写入 ----

def record(user: str, date: str, item_ref: str, kind: str,
           passed: bool = True, score: float | None = None) -> None:
    """记一条提升档事件（同键重交即刷新，ts 换新）。"""
    with _connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR REPLACE INTO boost_log"
            " (user, date, item_ref, kind, passed, score, ts)"
            " VALUES (?,?,?,?,?,?,?)",
            ((user or "").strip()[:60], (date or "").strip()[:10],
             (item_ref or "").strip()[:200], (kind or "").strip()[:16],
             1 if passed else 0, score, _utc_now()),
        )


# ---- 当日判定（loop / schedule 消费） ----

def item_done(user: str, date: str, item_ref: str) -> bool:
    """该生该日该提升项是否过关（passed=1 的记录存在）。"""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT passed FROM boost_log"
                " WHERE user = ? AND date = ? AND item_ref = ?",
                ((user or "").strip()[:60], (date or "").strip()[:10],
                 (item_ref or "").strip()[:200]),
            ).fetchone()
            return bool(row and row["passed"])
    except sqlite3.Error:
        return False


def day_done(user: str, date: str, item_refs: list[str]) -> bool:
    """当日提升档是否全过关：item_refs 非空且每条都 passed=1。"""
    refs = [r for r in (item_refs or []) if r]
    if not refs:
        return False
    return all(item_done(user, date, r) for r in refs)


def today_items(user: str, date: str) -> list[dict]:
    """date（北京日）当日的提升档任务清单（该日所属周 plan_weeks.tasks）。

    供 retell 提交等入口校验「今天确实排了这件事」。教师没配 boost_items
    时不派生任务——即使该日落在默认提升日（周二/周四）也返回空列表。
    """
    day = (date or "").strip()[:10]
    try:
        ws = monday_of(_date.fromisoformat(day)).isoformat()
    except ValueError:
        return []
    try:
        with _connect() as conn:
            conn.executescript(SCHEMA)
            row = conn.execute(
                "SELECT tasks FROM plan_weeks WHERE user = ? AND week_start = ?",
                ((user or "").strip()[:60], ws),
            ).fetchone()
    except (sqlite3.Error, ValueError):
        return []
    try:
        tasks = json.loads(row["tasks"] or "{}") if row else {}
    except (ValueError, TypeError):
        tasks = {}
    if not isinstance(tasks, dict):
        tasks = {}
    items = [x for x in (tasks.get("boost_items") or [])
             if isinstance(x, dict) and item_ref_of(x)]
    if not items:
        return []
    if day not in _boost_day_dates(ws, boost_days_of(tasks, items)):
        return []
    return items


def _boost_day_dates(week_start: str, days: list[int]) -> set[str]:
    """周内提升日的日期集合（days 为周几数字，1=周一）。"""
    try:
        ws = _date.fromisoformat(week_start)
    except ValueError:
        return set()
    return {(ws + timedelta(days=n - 1)).isoformat() for n in days
            if 1 <= n <= 7}


# ---- 周窗口 / 讲次归属（rules / ctx / schedule 消费） ----

_MEMORY_LECTURE_RE = re.compile(r"^lecture-(\d+)")


def _memory_ref_lecture(item_ref: str) -> int | None:
    """memory:{topic_id}:{node_key} → 讲次号（topic_id 的 lecture-{n} 前缀，
    与 plan/signals._topic_done_lectures 同一解析口径）。"""
    parts = (item_ref or "").split(":")
    if len(parts) != 3 or parts[0] != "memory":
        return None
    m = _MEMORY_LECTURE_RE.match(parts[1] or "")
    return int(m.group(1)) if m else None


def _retell_ref_lecture(item_ref: str) -> int | None:
    """retell:{pattern_id} → 讲次号（查 pattern_item.lecture）。

    grammar_kb.patterns 由并行车道实现（get(item_id) → dict|None，含
    lecture 列）；模块未部署/查询失败时返回 None 并 debug 兜底，不抛异常。
    """
    parts = (item_ref or "").split(":", 1)
    if len(parts) != 2 or parts[0] != "retell":
        return None
    try:
        pid = int(parts[1])
    except ValueError:
        return None
    try:
        from .. import patterns
        p = patterns.get(pid)
    except ImportError:
        logger.debug("patterns 模块未部署，retell 提升档 %s 不计讲次归属",
                     item_ref)
        return None
    except (sqlite3.Error, ValueError, TypeError, AttributeError) as e:
        logger.debug("retell %s 讲次归属查询失败：%s", item_ref, e)
        return None
    if not isinstance(p, dict):
        return None
    try:
        return int(p.get("lecture"))
    except (TypeError, ValueError):
        return None


def ref_lecture(item_ref: str, kind: str = "") -> int | None:
    """item_ref 关联的讲次号（memory 解析 topic_id 前缀；retell 查题库）。"""
    if kind == "memory" or (not kind and item_ref.startswith("memory:")):
        return _memory_ref_lecture(item_ref)
    if kind == "retell" or (not kind and item_ref.startswith("retell:")):
        return _retell_ref_lecture(item_ref)
    return _memory_ref_lecture(item_ref) or _retell_ref_lecture(item_ref)


def advanced_rows(user: str, lo: str | None, hi: str | None) -> list:
    """[lo, hi) 内「过关」且能归属到讲次的提升档记录 [(讲次, 北京日)]。

    过关口径与 :func:`pass_state_map` 一致：memory 打卡即过；retell
    须教师批改 ≥ RETELL_PASS_SCORE（提交未批/低分不算前进）。
    lo/hi 为 None 表示该侧不限。缺表容错空列表。
    """
    sql = ("SELECT item_ref, kind, date FROM boost_log"
           " WHERE user = ? AND passed = 1"
           " AND (kind != 'retell'"
           f"      OR (score IS NOT NULL AND score >= {RETELL_PASS_SCORE}))")
    args: list = [(user or "").strip()[:60]]
    if lo:
        sql += " AND date >= ?"
        args.append(lo[:10])
    if hi:
        sql += " AND date < ?"
        args.append(hi[:10])
    try:
        with _connect() as conn:
            conn.executescript(SCHEMA)
            rows = conn.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []
    out: list = []
    for r in rows:
        lec = ref_lecture(r["item_ref"], r["kind"] or "")
        if lec is not None:
            out.append((lec, r["date"]))
    return out


def lectures_advanced(user: str, lo: str | None, hi: str | None) -> set[int]:
    """[lo, hi) 内有提升档过关记录的讲次集合（lecture_done 第三分支）。"""
    return {lec for lec, _d in advanced_rows(user, lo, hi)}


def week_advanced(user: str, week_start: str, lecture: int) -> bool:
    """该周（week_start 起七天）内是否存在 ref 关联该讲次且 passed=1。"""
    try:
        ws = _date.fromisoformat(week_start).isoformat()
    except ValueError:
        return False
    hi = (_date.fromisoformat(ws) + timedelta(days=7)).isoformat()
    return int(lecture) in lectures_advanced(user, ws, hi)


def passed_map(user: str, lo: str, hi: str) -> dict[str, str]:
    """[lo, hi) 内 passed=1 的 item_ref → 最早过关日（欠账滚动判定用）。"""
    try:
        with _connect() as conn:
            conn.executescript(SCHEMA)
            rows = conn.execute(
                "SELECT item_ref, MIN(date) d FROM boost_log"
                " WHERE user = ? AND passed = 1 AND date >= ? AND date < ?"
                " GROUP BY item_ref",
                ((user or "").strip()[:60], lo[:10], hi[:10]),
            ).fetchall()
            return {r["item_ref"]: r["d"] for r in rows}
    except sqlite3.Error:
        return {}


# retell 过关线：教师批改 ≥7 分（与 videoStudy 批改口径一致）
RETELL_PASS_SCORE = 7


def pass_state_map(user: str, lo: str, hi: str) -> dict[str, dict]:
    """[lo, hi) 内每个 item_ref 的过关状态（完成 ≠ 过关两态口径）：

    - memory（及一切非 retell 事件）：任一条 passed=1 → ``passed``；
    - retell：最高分 ≥ RETELL_PASS_SCORE → ``passed``；
      有提交但还没批（score 全空）→ ``pending``（不欠账、不重派）；
      最高分 < 线 → ``graded_low``（老师建议再练，回流下一提升日）。

    返回 ``{item_ref: {"state": ..., "day": 窗内首次提交日}}``；
    窗内无记录的 ref 不在返回里。
    """
    try:
        with _connect() as conn:
            conn.executescript(SCHEMA)
            rows = conn.execute(
                "SELECT item_ref, kind, MAX(passed) mp, MAX(score) ms,"
                " MIN(date) d FROM boost_log"
                " WHERE user = ? AND date >= ? AND date < ?"
                " GROUP BY item_ref",
                ((user or "").strip()[:60], lo[:10], hi[:10]),
            ).fetchall()
        out: dict[str, dict] = {}
        for r in rows:
            if str(r["kind"]) == "retell":
                ms = r["ms"]
                if ms is not None and float(ms) >= RETELL_PASS_SCORE:
                    state = "passed"
                elif ms is None:
                    state = "pending"
                else:
                    state = "graded_low"
            else:
                state = "passed" if (r["mp"] or 0) == 1 else "pending"
            out[r["item_ref"]] = {"state": state, "day": r["d"]}
        return out
    except sqlite3.Error:
        return {}


def pass_state(user: str, day: str, item_ref: str) -> str:
    """单日单 ref 的过关状态（见 :func:`pass_state_map` 三态）；无记录 → todo。"""
    m = pass_state_map(user, day, _next_day(day))
    return (m.get(item_ref) or {}).get("state", "todo")


def _next_day(day: str) -> str:
    return (_date.fromisoformat(day) + timedelta(days=1)).isoformat()

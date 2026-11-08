"""今日闭环聚合（v3.0.0 设计 §2.1）：纯派生，不落库。

四格口径：
- 背词：``recite.review_summary`` 的 due_today+overdue（今天）；历史日按
  recite_review 的 next_review 直算（同一口径的回看版）；
- 微练习：``micro.day_status`` 的 done（micro_daily 整组做完）；
- 泛读：当日有效泛读词数 vs week_daily_tasks 泛读行当日摊派（books.py
  「今日 +N 词」同一公式）；拿不到目标（没配书/全部已达成）时
  target_words=None，ok=当日有有效泛读会话（或已全部达成）；
- 提升档：当日课程表派生出的 boost 行（week_daily_tasks 同一来源），
  非提升日=None，完成与否走 plan/boost.day_done。

streak（连续全绿天数）从查询日往前回溯，recite clear + micro done +
reading ok 三项（boost 不计入），全部用历史信号重算、无落库；历史日的
泛读额度无法精确回放（目标词数随当前读位漂移），回看口径=当日有有效
泛读会话。回溯上限 60 天。
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from .. import dbreg
from ..clock import cn_dt, monday_of, today_cn
from . import boost as plan_boost


def _default_store():
    from .store import PlanStore
    return PlanStore()


def _default_recite():
    from ..recite import ReciteStore
    return ReciteStore(dbreg.fce_db_path())


def _default_micro():
    from ..micro import MicroStore
    return MicroStore(dbreg.fce_db_path())


# 回溯上限（天）：连击最多往回查 60 天
STREAK_MAX_DAYS = 60


def loop_status(user: str, day: str, *, store=None, recite=None,
                micro=None) -> dict:
    """某生某北京日的闭环状态（字段契约见模块 docstring / 设计 §2.1）。

    ``store/recite/micro``：调用方已有的三个 store（my-week 端点传入，
    避免按默认路径重开库）；缺省自建默认实例。结果每次现算，不做跨请求缓存。
    """
    user = (user or "").strip()[:60]
    d = date.fromisoformat(str(day)[:10])
    day_iso = d.isoformat()
    ws = monday_of(d).isoformat()
    nxt = (d + timedelta(days=1)).isoformat()
    store = store or _default_store()
    recite = recite or _default_recite()
    micro = micro or _default_micro()

    # ---- 背词：今天用 review_summary 口径，历史日同口径直算 ----
    if day_iso == today_cn().isoformat():
        rs = recite.review_summary(user)
        due = int(rs.get("due_today") or 0)
        overdue = int(rs.get("overdue") or 0)
    else:
        due, overdue = _recite_counts(recite, user, day_iso)
    recite_clear = due + overdue == 0

    # ---- 微练习 ----
    st = micro.day_status(user, day_iso)
    micro_done = bool(st.get("done"))

    # ---- 泛读：当日词数 vs 当日摊派额度 ----
    words, has_reading = _day_readings(store, user, day_iso, nxt)
    target, quota_applies, week_tasks = _reading_target(
        store, user, ws, day_iso)
    if not quota_applies:
        reading_ok = has_reading  # 拿不到目标：有有效泛读会话即算
    elif target is None:
        reading_ok = True         # 各书目标都已达成：无额度可摊
    else:
        reading_ok = words >= target

    # ---- 提升档：当日派生清单里的 boost 行（与课程表同一来源）----
    derived = store.week_daily_tasks(ws, for_date=day_iso, user=user)
    boost_out: dict | None = None
    boost_rows = [r for r in derived if r.get("type") == "boost"]
    if boost_rows:
        refs = [str((r.get("boost") or {}).get("item_ref") or "")
                for r in boost_rows]
        refs = [x for x in refs if x]
        label = str(boost_rows[0].get("text") or "⭐ 提升任务")
        if not refs:
            # 「本周未配置」说明行：无可做任务，不拦闭环
            boost_out = {"scheduled": True, "done": True, "label": label,
                         "configured": False}
        else:
            # 两态口径（完成 ≠ 过关）：done=已提交/已打卡（当日闭环判定）；
            # state=过关进度——memory 打卡即 passed，retell 看教师分
            # （≥7 passed / 未批 pending / <7 graded_low 建议再练）
            states = [plan_boost.pass_state(user, day_iso, r) for r in refs]
            if states and all(s == "passed" for s in states):
                state = "passed"
            elif "graded_low" in states:
                state = "graded_low"
            elif "pending" in states:
                state = "pending"
            else:
                state = "todo"
            boost_out = {
                "scheduled": True,
                "done": plan_boost.day_done(user, day_iso, refs),
                "state": state,
                "passed": state == "passed",
                "label": label,
                "configured": bool((boost_rows[0].get("boost") or {})
                                   .get("configured", True)),
            }

    # ---- 连击：从今天往前回溯连续全绿天数（boost 不计入）----
    streak = _streak(store, recite, micro, user, d,
                     today_green=(recite_clear and micro_done and reading_ok))

    return {
        "recite": {"clear": recite_clear, "due": due, "overdue": overdue},
        "micro": {"done": micro_done, "answered": int(st.get("n") or 0),
                  "total": int(st.get("total") or 0) or 3},
        "reading": {"ok": reading_ok, "today_words": words,
                    "target_words": target},
        "boost": boost_out,
        "streak": streak,
        "next_queue": {
            "recite_words": _recite_due_count(recite, user, nxt),
            "micro_questions": _micro_wrong_open(micro, user, day_iso),
        },
    }


# ---- 背词（recite_review 直算；与 review_summary 同一「在周期」口径） ----

def _recite_counts(recite, user: str, day_iso: str) -> tuple[int, int]:
    """历史日的 (due, overdue)：conquered_at 为空且 next_review 与日比。"""
    try:
        with recite._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(next_review = ?), 0) due,"
                " COALESCE(SUM(next_review < ?), 0) overdue"
                " FROM recite_review"
                " WHERE user = ? AND conquered_at IS NULL"
                " AND next_review IS NOT NULL AND next_review <= ?",
                (day_iso, day_iso, user, day_iso),
            ).fetchone()
            return int(row["due"] or 0), int(row["overdue"] or 0)
    except sqlite3.Error:
        return 0, 0


def _recite_due_count(recite, user: str, day_iso: str) -> int:
    """next_review=该日（明天）的未攻克词数（明日回炉）。"""
    try:
        with recite._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) n FROM recite_review"
                " WHERE user = ? AND conquered_at IS NULL AND next_review = ?",
                (user, day_iso),
            ).fetchone()
            return int(row["n"] or 0)
    except sqlite3.Error:
        return 0


# ---- 微练习 ----

def _micro_wrong_open(micro, user: str, day: str) -> int:
    """明日回炉题数（与 micro._pick_daily 优先级①同一集合，见
    micro.wrong_open_qids：category=wrong ∪ 近 3 天答错未翻正）。"""
    try:
        return len(micro.wrong_open_qids(user, day))
    except Exception:
        return 0


def _micro_done_days(micro, user: str, lo: str, hi: str) -> set[str]:
    """[lo, hi] 内 micro_daily 整组做完（done=1）的北京日。"""
    try:
        with micro._connect() as conn:
            rows = conn.execute(
                "SELECT date FROM micro_daily"
                " WHERE user = ? AND done = 1 AND date >= ? AND date <= ?",
                (user, lo, hi),
            ).fetchall()
            return {r[0] for r in rows}
    except sqlite3.Error:
        return set()


# ---- 泛读 ----

_FOCUS_COLS = (
    "module, book_title, book_id, active_sec, lookups, plays,"
    " scroll_count, chapter_navs, percent_start, percent_end,"
    " percent_track, created_at"
)


def _day_readings(store, user: str, day_iso: str,
                  nxt: str) -> tuple[int, bool]:
    """当日有效泛读 (词数合计, 是否有有效会话)——_reading_day_detail 同口径
    （章区间+轨迹几何，剔僵尸会话）。"""
    try:
        with store._connect() as conn:
            rows = conn.execute(
                f"SELECT {_FOCUS_COLS}, {cn_dt('created_at')} AS d"
                " FROM focus_sessions"
                " WHERE user = ? AND module = 'library'"
                f" AND {cn_dt('created_at')} >= ?"
                f" AND {cn_dt('created_at')} < ?",
                (user, f"{day_iso} 00:00:00", f"{nxt} 00:00:00"),
            ).fetchall()
    except sqlite3.Error:
        return 0, False
    detail = store._reading_day_detail(rows)
    return sum(int(x.get("words") or 0) for x in detail), bool(detail)


def _reading_target(store, user: str, ws: str,
                    day_iso: str) -> tuple[int | None, bool, dict]:
    """泛读当日摊派额度（books.py「今日 +N 词」同一公式）。

    返回 (target_words, quota_applies, week_tasks)：
    - 配了书且至少一本还有剩余 → (各书当日额度之和, True, tasks)
    - 配了书且全部已达成 → (None, True, tasks)
    - 没配书 / 都匹配不到 → (None, False, tasks)
    """
    from .util import add_days
    days_left = max(1, (date.fromisoformat(add_days(ws, 6))
                        - date.fromisoformat(day_iso)).days + 1)
    week = store._get_week(ws, user)
    t = week.get("tasks") or {}
    pending = 0
    any_book = False
    all_reached = True
    for book_key, goal_val in (t.get("reading") or {}).items():
        row = store._match_book(book_key, user)
        if not row:
            continue
        any_book = True
        if isinstance(goal_val, list):
            goal_words = store._chapters_words_sum({str(row["id"]): goal_val})
            done_w = store._read_words_in_chapters(user, row["id"], goal_val)
            if goal_words > 0 and done_w >= goal_words:
                continue  # 该书已读完
            all_reached = False
            pending += max(1, round(max(0, goal_words - done_w) / days_left))
        else:
            goal_pct = float(goal_val or 100)
            stt = store._book_reading_state(row["id"], goal_pct, user)
            if not stt:
                continue
            if stt["percent"] >= (stt.get("goal_percent") or goal_pct) - 0.05:
                continue  # 目标已达成（与 completion goal_reached 同口径）
            all_reached = False
            pending += max(1, round((stt.get("remaining_words") or 0)
                                    / days_left))
    if not any_book:
        return None, False, t
    if all_reached:
        return None, True, t
    return pending, True, t


def _reading_session_days(store, user: str, lo: str, hi: str) -> set[str]:
    """[lo, hi) 各北京日是否有有效（非僵尸）泛读会话（连击回看口径：
    历史日的当日额度无法精确回放，有真读即算该格绿）。"""
    try:
        with store._connect() as conn:
            rows = conn.execute(
                f"SELECT {_FOCUS_COLS}, {cn_dt('created_at')} AS d"
                " FROM focus_sessions"
                " WHERE user = ? AND module = 'library'"
                f" AND {cn_dt('created_at')} >= ?"
                f" AND {cn_dt('created_at')} < ?",
                (user, f"{lo} 00:00:00", f"{hi} 00:00:00"),
            ).fetchall()
    except sqlite3.Error:
        return set()
    out: set[str] = set()
    for r in rows:
        if store._zombie_reading(r) or not store._reading_session_valid(r):
            continue
        day = str(r["d"] or "")[:10]
        if day:
            out.add(day)
    return out


# ---- 连击 ----

def _streak(store, recite, micro, user: str, day: date, *,
            today_green: bool) -> int:
    """从 day 往前连续全绿天数（recite clear + micro done + reading ok）。

    查询日当格用上面算好的三项真值（today_green）；更早的日子回看口径：
    - 背词：当前在周期 backlog（conquered_at 为空、next_review ≤ 当日为空
      ——已攻克词退出 backlog，历史「当天清没清」按现状回看，无落库近似；
    - 微练习：micro_daily done=1；
    - 泛读：当日有有效（非僵尸）泛读会话。
    """
    day_iso = day.isoformat()
    horizon = (day - timedelta(days=STREAK_MAX_DAYS - 1)).isoformat()
    backlog = _recite_backlog(recite, user, day_iso)
    micro_days = _micro_done_days(micro, user, horizon, day_iso)
    reading_days = _reading_session_days(store, user, horizon, day_iso)
    streak = 0
    cur = day
    while (day - cur).days < STREAK_MAX_DAYS:
        di = cur.isoformat()
        if di == day_iso:
            green = today_green
        else:
            clear = not any(x <= di for x in backlog)
            green = (clear and di in micro_days and di in reading_days)
        if not green:
            break
        streak += 1
        cur -= timedelta(days=1)
    return streak


def _recite_backlog(recite, user: str, hi_iso: str) -> list[str]:
    """当前在周期（conquered_at 为空）且 next_review ≤ hi 的日程列表。"""
    try:
        with recite._connect() as conn:
            rows = conn.execute(
                "SELECT next_review FROM recite_review"
                " WHERE user = ? AND conquered_at IS NULL"
                " AND next_review IS NOT NULL AND next_review <= ?",
                (user, hi_iso),
            ).fetchall()
            return [r[0] for r in rows]
    except sqlite3.Error:
        return []

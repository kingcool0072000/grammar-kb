"""周作业计分：非微练习按当周认完成，微练习按北京日打卡。

一周是周一 00:00 到下周一 00:00（北京时间，周日 24:00 截止）。
这一周里任何一天做完都算这一周；过了周日夜里再做，记在做的那一周，
不改上一周的进度。不改已经写下的流水。
词汇「本周新掌握」只数 mastered_at 落在本周周一至周日的词。
微练习开着时，周一到周日各打一次卡，过了当天不能补。
"""
from __future__ import annotations

import contextlib
import sqlite3
from datetime import date, timedelta

from ..clock import cn_dt, monday_of, today_cn
from .util import add_days

_CHECKIN_DAYS = 7  # 周一至周日
_WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
_WEEK_DETAIL = (
    "归在这一周，周一到周日哪一天完成都算。"
    "过了周日夜里，这一周的进度就定了，之后再做记在做的那一周。"
)


def week_credit_for(store, user: str, day: str, ctx=None) -> "WeekCredit":
    """同一周复用一份计分（week_view 逐日调用时挂在 ctx 上）。"""
    ws = monday_of(date.fromisoformat(str(day)[:10])).isoformat()
    cached = getattr(ctx, "week_credit", None) if ctx is not None else None
    if cached is not None and cached.ws == ws and cached.user == user:
        return cached
    credit = WeekCredit(store, user, ws)
    if ctx is not None:
        ctx.week_credit = credit
    return credit


class WeekCredit:
    """某一周的非微练习完成事实。查询懒加载，缺表当未完成。"""

    def __init__(self, store, user: str, week_start: str):
        self.store = store
        self.user = user
        self.ws = str(week_start)[:10]
        self.we = add_days(self.ws, 7)  # 词汇 / 错词：本周右开
        self._tasks = None
        self._vocab = None
        self._lectures = None
        self._hw = None
        self._fce = None
        self._videos = None
        self._nce2_shadow = None
        self._nce2_listen = None
        self._papers = None
        self._wrong = None
        self._boost = None

    def tasks(self) -> dict:
        if self._tasks is None:
            week = self.store._get_week(self.ws, user=self.user)
            t = week.get("tasks") if isinstance(week, dict) else None
            self._tasks = t if isinstance(t, dict) else {}
        return self._tasks

    def vocab_numbers(self) -> tuple[int, int, bool]:
        """(本周新掌握, 本周目标, 是否达标)。目标 0 且周初已满 → 达标。"""
        if self._vocab is not None:
            return self._vocab
        t = self.tasks()
        try:
            vg = int(t.get("vocab_goal") or 0)
        except (TypeError, ValueError):
            vg = 0
        if vg <= 0:
            self._vocab = (0, 0, False)
            return self._vocab
        lv = t.get("vocab_lv")
        words = None
        if lv is not None and lv != "":
            try:
                words = self.store._vocab_level_words(int(lv))
            except (TypeError, ValueError):
                words = None
        try:
            base = self.store._vocab_mastered_before(self.ws, self.user, words)
            gained = self.store._vocab_actuals(self.ws, self.we, self.user, words)
        except (TypeError, ValueError, sqlite3.Error):
            base, gained = 0, 0
        # 与周进度同一差额：分母 = 目标 − 周初已会；分子只数本周内新掌握。
        if base >= vg:
            self._vocab = (gained, vg, True)
            return self._vocab
        target = vg - base
        self._vocab = (gained, target, gained >= target)
        return self._vocab

    def lectures(self, kind: str = "lecture") -> set[int]:
        slot = self._lectures if kind == "lecture" else self._hw
        if slot is None:
            try:
                slot = self.store.lecture_done(self.ws, self.we, self.user, kind)
            except (TypeError, ValueError, sqlite3.Error):
                slot = set()
            if kind == "lecture":
                self._lectures = slot
            else:
                self._hw = slot
        return slot

    def fce(self) -> set[str]:
        if self._fce is None:
            try:
                self._fce = set(self.store._fce_actuals(self.ws, self.we, self.user))
            except (TypeError, ValueError, sqlite3.Error):
                self._fce = set()
        return self._fce

    def videos(self) -> set[str]:
        if self._videos is None:
            try:
                self._videos = self.store._retold_video_ids(self.ws, self.we, self.user)
            except (TypeError, ValueError, sqlite3.Error):
                self._videos = set()
        return self._videos

    def _nce2(self, kind: str) -> set[int]:
        try:
            return self.store._nce2_kind_ids(self.ws, self.we, self.user, kind)
        except (TypeError, ValueError, sqlite3.Error):
            return set()

    def nce2_shadow(self) -> set[int]:
        if self._nce2_shadow is None:
            self._nce2_shadow = self._nce2("shadow")
        return self._nce2_shadow

    def nce2_listen(self) -> set[int]:
        if self._nce2_listen is None:
            self._nce2_listen = self._nce2("listen")
        return self._nce2_listen

    def papers(self) -> set[str]:
        """本周 [周一, 下周一) 登记、且试卷仍在存档里的 paper_id。"""
        if self._papers is not None:
            return self._papers
        self._papers = archived_paper_ids(self.store, self.user, self.ws, self.we)
        return self._papers

    def wrong_words_in_week(self) -> bool:
        """本周 [周一, 下周一) 有过到期复习或攻克。过周之后的不算。"""
        if self._wrong is None:
            self._wrong = wrong_activity(self.store, self.user, self.ws, self.we)
        return self._wrong

    def boost_states(self) -> dict:
        if self._boost is None:
            from . import boost
            self._boost = boost.pass_state_map(self.user, self.ws, self.we)
        return self._boost

    def covers(self, task: dict) -> dict | None:
        """该条建议行若已按周完成，返回要并进完成结果的字段；否则 None。"""
        ty = task.get("type")
        if ty == "vocab" and task.get("quota"):
            gained, target, met = self.vocab_numbers()
            if met:
                return {"week_gained": gained, "week_target": target,
                        "gained": gained}
            return None
        if ty == "wrong_words":
            return {} if self.wrong_words_in_week() else None
        if ty == "lecture":
            n = _key_int(task, "lecture:")
            return {} if n is not None and n in self.lectures("lecture") else None
        if ty == "hw_paper":
            n = _key_int(task, "hw:")
            return {} if n is not None and n in self.lectures("hw") else None
        if ty == "fce":
            item = _fce_item(task)
            return {} if item and item in self.fce() else None
        if ty == "video":
            vid = str(task.get("video_id") or task.get("vid") or "")
            if not vid and str(task.get("key") or "").startswith("video:"):
                vid = str(task.get("key")).split(":", 1)[1]
            return {} if vid and vid in self.videos() else None
        if ty == "nce2":
            lid = task.get("lesson_id")
            if lid is None:
                lid = _key_int(task, "nce2:")
            if lid is None:
                return None
            mode = task.get("nce2_mode") or "shadow"
            if mode == "shadow":
                ok = int(lid) in self.nce2_shadow()
            else:
                ok = int(lid) in self.nce2_shadow() or int(lid) in self.nce2_listen()
            return {} if ok else None
        if ty == "paper":
            pid = ""
            key = str(task.get("key") or "")
            if key.startswith("paper:"):
                pid = key.split(":", 1)[1]
            return {} if pid and pid in self.papers() else None
        if ty == "boost":
            ref = str((task.get("boost") or {}).get("item_ref") or "")
            if not ref:
                return None
            state = (self.boost_states().get(ref) or {}).get("state")
            return {} if state in ("passed", "pending", "graded_low") else None
        return None


def _key_int(task: dict, prefix: str) -> int | None:
    key = str(task.get("key") or "")
    if not key.startswith(prefix):
        return None
    try:
        return int(key.split(":", 1)[1])
    except (TypeError, ValueError):
        return None


def _fce_item(task: dict) -> str:
    text = str(task.get("text") or "")
    if text.startswith("FCE "):
        return text[4:].strip()
    return ""


def _boost_done_state(state: str | None) -> bool:
    return state in ("passed", "pending", "graded_low")


def build_week_pool(store, user: str, week_start: str) -> list[dict]:
    """本周作业清单（每项一次）。完成不看排在周几。"""
    credit = WeekCredit(store, user, week_start)
    t = credit.tasks()
    pool: list[dict] = []

    def add(row: dict) -> None:
        row.setdefault("detail", _WEEK_DETAIL)
        pool.append(row)

    for lec in t.get("lectures") or []:
        try:
            n = int(lec)
        except (TypeError, ValueError):
            continue
        done = n in credit.lectures("lecture")
        add({"type": "lecture", "key": f"lecture:{n}",
             "text": f"第{n}讲 学+测", "done": done,
             "why": "week_credit" if done else "no_signal"})
    for lec in t.get("hw_papers") or []:
        try:
            n = int(lec)
        except (TypeError, ValueError):
            continue
        done = n in credit.lectures("hw")
        add({"type": "hw_paper", "key": f"hw:{n}",
             "text": f"作业卷（第{n}讲卷）", "done": done,
             "why": "week_credit" if done else "no_signal"})

    try:
        vg = int(t.get("vocab_goal") or 0)
    except (TypeError, ValueError):
        vg = 0
    if vg > 0:
        gained, target, met = credit.vocab_numbers()
        lv = t.get("vocab_lv")
        head = f"L{int(lv)} 本周新掌握" if lv is not None and lv != "" else "本周新掌握"
        if target <= 0 and met:
            text = f"{head}：周初已经达成"
        else:
            text = f"{head} {gained}/{target if target else vg}"
        add({"type": "vocab", "key": "vocab-week", "text": text, "done": met,
             "why": "week_credit" if met else "below_quota",
             "detail": "本周周一到周日新掌握的词都算。下一周才掌握的词记在下一周，不挪回来。"})

    for item in t.get("fce") or []:
        label = str(item)
        done = label in credit.fce()
        add({"type": "fce", "key": f"fce:{label}", "text": f"FCE {label}",
             "done": done, "why": "week_credit" if done else "no_signal"})

    for book_key, goal in (t.get("reading") or {}).items():
        row = store._reading_task(book_key, goal, user, 7)
        text = row.get("text") or f"泛读 {book_key}"
        done = ("已读完" in text) or ("目标已达成" in text)
        detail = row.get("detail") or "读完这一周的章节即可，不限哪一天"
        book = store._match_book(book_key, user)
        if book is not None and reading_moved_after(store, user, book["id"], credit.we):
            done = reading_met_before(store, user, book["id"], goal, credit.we)
            if not done:
                text = (text.replace("已读完 🎉", "这一周还没读完")
                        .replace("目标已达成 🎉", "这一周还没达成"))
                detail = "这一周结束前读到的进度。之后再读的记在后面的周。"
        add({"type": "reading", "key": f"reading:{book_key}", "text": text,
             "detail": detail,
             "done": done, "why": "goal_reached" if done else "not_read"})

    for vid in store._plan_videos(t):
        title = store._video_title_any(vid) or vid
        done = vid in credit.videos()
        add({"type": "video", "key": f"video:{vid}", "video_id": vid,
             "text": f"看视频+复述《{title}》", "done": done,
             "why": "week_credit" if done else "no_signal"})

    mode_of = {}
    for unit in store._nce2_units(t):
        for lid in unit["lessons"]:
            mode_of.setdefault(lid, unit["task_mode"])
    for lid in store._plan_lessons(t):
        mode = mode_of.get(lid, "shadow")
        if mode == "shadow":
            done = lid in credit.nce2_shadow()
        else:
            done = lid in credit.nce2_shadow() or lid in credit.nce2_listen()
        title = store._nce2_lesson_title_map().get(int(lid)) or f"第{lid}课"
        head = "精听" if mode == "listen" else "听力跟读"
        add({"type": "nce2", "key": f"nce2:{lid}", "lesson_id": int(lid),
             "text": f"{head}《{title}》", "done": done,
             "why": "week_credit" if done else "no_signal"})

    from .schedule import paper_short_label
    for pid in t.get("vocab_papers") or []:
        pid = str(pid)
        done = pid in credit.papers()
        add({"type": "paper", "key": f"paper:{pid}",
             "text": f"单词试卷 · {paper_short_label(pid)}", "done": done,
             "why": "week_credit" if done else "no_signal"})

    from . import boost
    for item in t.get("boost_items") or []:
        if not isinstance(item, dict):
            continue
        ref = boost.item_ref_of(item)
        if not ref:
            continue
        state = (credit.boost_states().get(ref) or {}).get("state")
        done = _boost_done_state(state)
        text, detail = store._boost_label(item)
        add({"type": "boost", "key": f"boost:{ref}", "text": text,
             "detail": (detail or "") + " · 本周哪天做都算",
             "done": done, "why": "week_credit" if done else "no_signal",
             "boost": {"item_ref": ref, "kind": str(item.get("type") or ""),
                       "configured": True}})

    wrong_n = 0
    try:
        wrong_n = int(store._wrong_words_count(user) or 0)
    except (TypeError, ValueError, sqlite3.Error):
        wrong_n = 0
    if wrong_n:
        done = credit.wrong_words_in_week()
        n = min(30, wrong_n)
        add({"type": "wrong_words", "key": "wrong_words",
             "text": f"错词再练 {n} 词",
             "detail": "本周任意一天复习了到期词或攻克了词，都算完成。过了这周再练，记在下一周。",
             "done": done, "why": "week_credit" if done else "no_signal"})
    return pool


def micro_checkins(store, user: str, week_start: str, enabled: bool) -> list[dict]:
    """微练习打卡：计划开启时周一到周日各一次。过了当天没打的算漏打。"""
    if not enabled:
        return []
    today = today_cn().isoformat()
    ws = date.fromisoformat(str(week_start)[:10])
    out: list[dict] = []
    for i in range(_CHECKIN_DAYS):
        d = (ws + timedelta(days=i)).isoformat()
        st = store._micro_daily_state(user, d)
        out.append(_checkin_row(d, i, st, today, required=True))
    return out


def _checkin_row(day: str, index: int, st: dict, today: str, *,
                 required: bool) -> dict:
    done = bool(st.get("done"))
    if not required:
        status = "done" if done else "record"
    elif done:
        status = "done"
    elif day < today:
        status = "missed"
    elif day == today:
        status = "open"
    else:
        status = "upcoming"
    return {
        "date": day,
        "weekday": index + 1,
        "label": _WEEKDAY_CN[index],
        "required": required,
        "status": status,
        "done": done,
        "answered": int(st.get("n") or 0),
        "total": int(st.get("total") or 0) or 3,
    }


def checkin_progress(rows: list[dict]) -> tuple[int, int, int]:
    """(已打卡, 漏打, 应打卡天数)。应打卡是周一到周日。"""
    required = [r for r in rows if r.get("required")]
    done = sum(1 for r in required if r.get("status") == "done")
    missed = sum(1 for r in required if r.get("status") == "missed")
    return done, missed, len(required)


def archived_paper_ids(store, user: str, lo: str, hi: str) -> set[str]:
    """[lo, hi) 内登记、且试卷仍在存档里的 paper_id。"""
    out: set[str] = set()
    try:
        with store._connect() as conn:
            tabs = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "vocab_exams" not in tabs:
                return out
            rows = conn.execute(
                "SELECT paper_id FROM vocab_exams"
                " WHERE user = ? AND exam_date >= ? AND exam_date < ?"
                " AND paper_id IS NOT NULL AND paper_id != ''",
                (user, str(lo)[:10], str(hi)[:10]),
            ).fetchall()
            pids = [str(r["paper_id"]) for r in rows if r["paper_id"]]
            archived: set[str] = set()
            if pids and "vocab_paper_history" in tabs:
                marks = ",".join("?" * len(pids))
                archived = {
                    r["paper_id"] for r in conn.execute(
                        "SELECT paper_id FROM vocab_paper_history"
                        f" WHERE paper_id IN ({marks})", pids)
                }
            out = {pid for pid in pids if pid in archived}
    except sqlite3.Error:
        return set()
    return out


def wrong_activity(store, user: str, lo: str, hi: str) -> bool:
    """[lo, hi) 内有过到期复习或攻克。缺列当没有。"""
    try:
        with store._connect() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(recite_review)")}
            parts = []
            args: list = [user]
            if "due_touch" in cols:
                parts.append("(due_touch >= ? AND due_touch < ?)")
                args.extend((str(lo)[:10], str(hi)[:10]))
            if "conquered_at" in cols:
                parts.append("(conquered_at >= ? AND conquered_at < ?)")
                args.extend((str(lo)[:10], str(hi)[:10]))
            if not parts:
                return False
            row = conn.execute(
                "SELECT 1 FROM recite_review WHERE user = ? AND ("
                + " OR ".join(parts) + ") LIMIT 1",
                args,
            ).fetchone()
            return row is not None
    except sqlite3.Error:
        return False


def reading_moved_after(store, user: str, book_id: int, hi: str) -> bool:
    """阅读进度行的更新时间不早于 hi：当前水位含这一周之后的阅读。

    没有时间戳时不当成「之后又读过」，沿用当前进度（打开中的这一周就是这样）。
    """
    conn = store._lib_connect()
    if conn is None:
        return False
    try:
        with contextlib.closing(conn):
            row = conn.execute(
                f"SELECT {cn_dt('updated_at')} AS d FROM reading_progress"
                " WHERE book_id = ? AND user = ?",
                (int(book_id), user),
            ).fetchone()
    except sqlite3.Error:
        return False
    if not row or not row["d"]:
        return False
    return str(row["d"]) >= f"{str(hi)[:10]} 00:00:00"


def focus_percent_before(store, user: str, book_id: int, hi: str) -> float:
    """hi 之前能证明的最高阅读进度（全书百分比）。

    整段会话都落在 hi 之前，用结束进度。会话跨过 hi 的，只用开头进度，
    避免把下一周接着读的部分算回这一周。
    """
    bound = f"{str(hi)[:10]} 00:00:00"
    try:
        with store._connect() as conn:
            rows = conn.execute(
                "SELECT percent_start, percent_end,"
                f" {cn_dt('created_at')} AS c, {cn_dt('ended_at')} AS e"
                " FROM focus_sessions"
                " WHERE user = ? AND module = 'library' AND book_id = ?",
                (user, int(book_id)),
            ).fetchall()
    except sqlite3.Error:
        return 0.0
    best = 0.0
    for r in rows:
        created = str(r["c"] or "")
        ended = str(r["e"] or "")
        if not created or created >= bound:
            continue
        if ended and ended >= bound:
            val = float(r["percent_start"] or 0)
        else:
            val = float(r["percent_end"] or r["percent_start"] or 0)
        if val > best:
            best = val
    return best


def reading_met_before(store, user: str, book_id: int, goal, hi: str) -> bool:
    """这一周结束前，这本书的周目标是否已经达到。"""
    if isinstance(goal, list):
        try:
            goal_words = store._chapters_words_sum({str(int(book_id)): goal})
            done_w = store._read_words_in_chapters(
                user, int(book_id), goal, before=hi)
        except (TypeError, ValueError, sqlite3.Error):
            return False
        return goal_words > 0 and done_w >= goal_words
    try:
        target = float(goal or 100)
    except (TypeError, ValueError):
        target = 100.0
    return focus_percent_before(store, user, int(book_id), hi) + 0.05 >= target

"""教师计划表：周维度学习计划 + 实时完成度（fce.db 同库）。

设计意图：
- 教师按周排计划（本周讲到第几讲 / 新掌握多少词 / 做哪些 FCE 部分 /
  阅读目标），tasks 存结构化 JSON；notes 存「上周要改善的部分」自由文本。
- 完成进度不在表里存快照，而是每次 GET /plan/weeks 按周窗口实时聚合：
  课程=exam_records 周内新增讲次与分数；词汇=recite_word_progress 周内
  新掌握词数（mastered_at 落在窗口内）；FCE=fce_submission 周内覆盖的
  Test/Part；阅读=reading_progress 周内增量（updated_at 在窗口内取现值，
  跨周增量按上周末快照差值近似——库只存最新值，历史快照不存）。
- PUT 幂等覆盖一周的 tasks/notes；只有教师可写。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone as _tz
from pathlib import Path
from typing import Optional

from .fce_query import _default_db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_weeks (
    week_start TEXT PRIMARY KEY,   -- 周一日期 YYYY-MM-DD
    tasks      TEXT NOT NULL DEFAULT '{}',  -- 结构化任务 JSON
    notes      TEXT DEFAULT '',    -- 改善项/备注（自由文本）
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS plan_day_checks (
    user       TEXT NOT NULL,
    date       TEXT NOT NULL,        -- YYYY-MM-DD
    task_key   TEXT NOT NULL,        -- micro:26 / wrong_words / lecture:29 / fce / reading:书名
    checked_at TEXT,
    PRIMARY KEY (user, date, task_key)
);
"""

# tasks JSON 结构（全部字段可缺省）：
# {
#   "lectures": [29, 30],        # 本周计划学/测的讲次
#   "vocab_goal": 320,           # 本周目标掌握词数（绝对值，非增量）
#   "fce": [["Test1","P1"], ...] # 简化为 ["Test1 P1", "Test2 RUE"] 文本即可
#   "reading": {"波西杰克逊1": 100} # 书名 → 周末目标进度 %
# }


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_bounds(week_start: str) -> tuple[str, str]:
    """[周一 00:00, 下周一 00:00) 的 ISO 串（UTC 简化口径）。"""
    ws = date.fromisoformat(week_start)
    return (ws.isoformat(), (ws + timedelta(days=7)).isoformat())


class PlanStore:
    """plan_weeks 读写 + 周完成度实时聚合。"""

    def __init__(self, db_path: Optional[str] = None,
                 exam_db_path: Optional[str] = None,
                 grammar_db_path: Optional[str] = None,
                 library_db_path: Optional[str] = None):
        self.db_path = db_path or _default_db_path()
        self.exam_db_path = exam_db_path
        self.grammar_db_path = grammar_db_path
        # 与 LibraryStore 同口径：环境变量优先（测试注入/生产改库都用它）
        import os as _os
        self.library_db_path = (
            library_db_path
            or _os.environ.get("GRAMMAR_KB_LIBRARY_DB")
            or None
        )
        if Path(self.db_path).exists():
            with self._connect() as conn:
                conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        return conn

    # ---- plan_weeks 读写 ----

    def list_weeks(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM plan_weeks ORDER BY week_start"
            ).fetchall()
        return [_week_out(r) for r in rows]

    def put_week(self, week_start: str, tasks: dict, notes: str) -> dict:
        date.fromisoformat(week_start)  # 非法日期抛 ValueError → 422
        if week_start != monday_of(date.fromisoformat(week_start)).isoformat():
            raise ValueError("week_start 须为周一日期")
        now = datetime.now(_tz.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO plan_weeks (week_start, tasks, notes, updated_at)"
                " VALUES (?,?,?,?)"
                " ON CONFLICT(week_start) DO UPDATE SET"
                " tasks=excluded.tasks, notes=excluded.notes, updated_at=excluded.updated_at",
                (week_start, json.dumps(tasks or {}, ensure_ascii=False),
                 (notes or "")[:4000], now),
            )
            row = conn.execute(
                "SELECT * FROM plan_weeks WHERE week_start = ?", (week_start,)
            ).fetchone()
        return _week_out(row)

    # ---- 周完成度实时聚合 ----

    def week_actuals(self, week_start: str, user: str = "malin") -> dict:
        """某周的实际完成情况（课程/词汇/FCE/阅读），供 GET 拼装与上周回顾。"""
        lo, hi = week_bounds(week_start)
        out: dict = {
            "lectures": self._exam_actuals(lo, hi),
            "vocab_mastered": self._vocab_actuals(lo, hi, user),
            "fce_parts": self._fce_actuals(lo, hi, user),
            "reading": self._reading_actuals(lo, hi, user),
        }
        return out

    def _exam_actuals(self, lo: str, hi: str) -> list[dict]:
        """周内新增的哈一成绩（exam_records 单学生表）。库不存在返回空。"""
        path = self._exam_db()
        if not path or not Path(path).exists():
            return []
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT lecture, date, score FROM exam_records"
                    " WHERE date >= ? AND date < ? ORDER BY date, lecture",
                    (lo, hi),
                ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def _exam_db(self) -> Optional[str]:
        if self.exam_db_path:
            return self.exam_db_path
        try:
            from .exam_store import default_exam_db_path
            return default_exam_db_path()
        except Exception:
            return None

    def _vocab_actuals(self, lo: str, hi: str, user: str) -> int:
        """周内新掌握词数（mastered_at 落在窗口内）。"""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) n FROM recite_word_progress"
                    " WHERE user = ? AND mastered_at >= ? AND mastered_at < ?",
                    (user, lo, hi),
                ).fetchone()
                return row["n"]
        except sqlite3.Error:
            return 0

    def _fce_actuals(self, lo: str, hi: str, user: str) -> list[str]:
        """周内提交覆盖的 Test/Part（"Test 1 · RUE P1" 形式）。"""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT test_id, paper, part FROM fce_submission"
                    " WHERE user = ? AND created_at >= ? AND created_at < ?",
                    (user, lo, hi),
                ).fetchall()
            return sorted({
                f"Test {r['test_id']} · {_paper_short(r['paper'])} P{r['part']}"
                for r in rows
            })
        except sqlite3.Error:
            return []

    def _reading_actuals(self, lo: str, hi: str, user: str) -> list[dict]:
        """周内有更新过的书及当前进度（增量口径见模块 docstring）。

        输出补全书口径要素：总词数/总章数（books.chapter_count）与当前
        全书百分比落点——按各章 word_count 累计算出「读到第几章、本章内
        位置」，前端拼「本次阅读 第X章xx%~第Y章xx%、共N词、时长」。
        """
        path = self.library_db_path or str(
            Path(__file__).resolve().parent.parent / "data" / "library.db"
        )
        if not Path(path).exists():
            return []
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT b.id AS book_id, b.title, b.chapter_count,"
                    " p.percent, p.reading_seconds, p.updated_at"
                    " FROM reading_progress p JOIN books b ON b.id = p.book_id"
                    " WHERE p.user = ? AND p.updated_at >= ? AND p.updated_at < ?",
                    (user, lo, hi),
                ).fetchall()
                if not rows:
                    return []
                ph = ",".join("?" * len(rows))
                ch_rows = conn.execute(
                    f"SELECT book_id, idx, title, word_count FROM chapters"
                    f" WHERE book_id IN ({ph}) ORDER BY book_id, idx",
                    [r["book_id"] for r in rows],
                ).fetchall()
        except sqlite3.Error:
            return []
        chapters: dict[int, list[dict]] = {}
        for c in ch_rows:
            chapters.setdefault(c["book_id"], []).append(
                {"idx": c["idx"], "title": c["title"], "word_count": c["word_count"] or 0}
            )
        out = []
        for r in rows:
            d = dict(r)
            chs = chapters.get(r["book_id"], [])
            total_words = sum(c["word_count"] for c in chs)
            d["total_words"] = total_words
            d["chapters_count"] = len(chs) or (r["chapter_count"] or 0)
            d["current_chapter"] = _chapter_at_words(chs, r["percent"] or 0.0, total_words)
            out.append(d)
        return out

    def last_week_review(self, week_start: str, user: str = "malin") -> dict:
        """上周回顾：自动摘要 + 上周的 notes（编辑当前周时作参照）。"""
        prev = (date.fromisoformat(week_start) - timedelta(days=7)).isoformat()
        lo, hi = week_bounds(prev)
        lectures = self._exam_actuals(lo, hi)
        avg = round(sum(x["score"] for x in lectures) / len(lectures), 1) if lectures else None
        low = [x for x in lectures if x["score"] < 70]
        return {
            "week_start": prev,
            "summary": {
                "lecture_count": len(lectures),
                "avg_score": avg,
                "low_scores": low,
                "vocab_mastered": self._vocab_actuals(lo, hi, user),
                "fce_parts": self._fce_actuals(lo, hi, user),
            },
            "notes": self.get_week_notes(prev),
        }

    def get_week_notes(self, week_start: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT notes FROM plan_weeks WHERE week_start = ?", (week_start,)
            ).fetchone()
        return row["notes"] if row else ""

    # ---- 外循环：诊断聚类 + 周验收 ----
    # 双循环设计：外循环（教师，周尺度）= 诊断感知 → 周目标 → 任务展开 → 验收；
    # 内循环（学生，日尺度）执行任务并自动回收信号（exam_records / recite /
    # fce_submission / reading_progress 都已是自动回流，此处只做聚合）。

    def diagnosis(self, weeks_back: int = 8) -> list[dict]:
        """错题信号按讲次聚类（外循环第①步：诊断感知）。

        口径与教师手工《错题清单_20-28讲》一致：以讲次为单位聚合近期
        （默认 8 周）所有作答记录——错题数、出现次数、最近日期、最近得分。
        讲次标题/分类从 grammar.db lecture 表补全（读不到就留空，聚类仍成立）。
        排序：错题总数降序（主）+ 最近出现时间降序（次），无错题的讲次不出现。
        """
        try:
            with sqlite3.connect(f"file:{self._exam_db()}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT lecture, date, score, wrong FROM exam_records"
                    " WHERE date >= ? ORDER BY date",
                    ((date.today() - timedelta(weeks=weeks_back)).isoformat(),),
                ).fetchall()
        except sqlite3.Error:
            return []
        agg: dict[int, dict] = {}
        for r in rows:
            try:
                wrong = json.loads(r["wrong"] or "[]")
            except (ValueError, TypeError):
                wrong = []
            a = agg.setdefault(r["lecture"], {
                "lecture": r["lecture"], "total_wrong": 0, "attempts": 0,
                "last_date": r["date"], "last_score": r["score"],
            })
            a["total_wrong"] += len(wrong)
            a["attempts"] += 1
            if r["date"] >= a["last_date"]:
                a["last_date"] = r["date"]
                a["last_score"] = r["score"]
        items = [a for a in agg.values() if a["total_wrong"] > 0]
        titles = self._lecture_titles()
        for a in items:
            t = titles.get(a["lecture"])
            a["title"] = t["title"] if t else ""
            a["category"] = t["category"] if t else ""
        # 主键降序 + 日期降序：先按错题数排，同数取更近的
        items.sort(key=lambda x: (x["total_wrong"], x["last_date"]), reverse=True)
        return items

    def _lecture_titles(self) -> dict[int, dict]:
        """lecture_number → {title, category}（grammar.db 只读；失败返回空）。"""
        path = self.grammar_db_path or str(
            Path(__file__).resolve().parent.parent / "data" / "grammar.db"
        )
        if not Path(path).exists():
            return {}
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT number, title, category FROM lecture"
                ).fetchall()
            return {r["number"]: {"title": r["title"], "category": r["category"]}
                    for r in rows}
        except sqlite3.Error:
            return {}

    def week_review(self, week_start: str, user: str = "malin") -> dict:
        """周验收（外循环第④步）：对照上周 focus_kps 判收敛/滚动/升级。

        判据（讲次级，数据可得性优先）：
        - converged：周内该讲次有 ≥1 次作答且最近一次错题数为 0（或未再错）
        - escalate：该讲次出现在 focus_kps 已连续 ≥2 周且仍未收敛 → 建议转专题
        - rolling：其余（默认路径，下周继续）
        """
        prev = (date.fromisoformat(week_start) - timedelta(days=7)).isoformat()
        prev_week = self._get_week(prev)
        focus = (prev_week.get("tasks") or {}).get("focus_kps") or []
        lo, hi = week_bounds(prev)
        try:
            with sqlite3.connect(f"file:{self._exam_db()}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT lecture, date, wrong FROM exam_records"
                    " WHERE date >= ? AND date < ? ORDER BY date",
                    (lo, hi),
                ).fetchall()
        except sqlite3.Error:
            rows = []
        # 讲次 → 周内最近一次错题数
        last_wrong: dict[int, int] = {}
        for r in rows:
            try:
                last_wrong[r["lecture"]] = len(json.loads(r["wrong"] or "[]"))
            except (ValueError, TypeError):
                last_wrong[r["lecture"]] = 0
        # 连续在焦周数：往前逐周数 focus_kps 里含该讲次的连续周数
        streak = {}
        for lec in focus:
            n, ws = 0, prev
            while True:
                w = self._get_week(ws)
                if lec in ((w.get("tasks") or {}).get("focus_kps") or []):
                    n += 1
                    ws = (date.fromisoformat(ws) - timedelta(days=7)).isoformat()
                else:
                    break
            streak[lec] = n
        items = []
        for lec in focus:
            tried = lec in last_wrong
            converged = tried and last_wrong[lec] == 0
            escalate = streak[lec] >= 2 and not converged
            items.append({
                "lecture": lec,
                "status": "converged" if converged else
                          ("escalate" if escalate else "rolling"),
                "retested": tried,
                "last_wrong": last_wrong.get(lec),
                "focus_streak": streak[lec],
            })
        titles = self._lecture_titles()
        for it in items:
            t = titles.get(it["lecture"])
            it["title"] = t["title"] if t else ""
        return {"week_start": prev, "items": items}

    def _get_week(self, week_start: str) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM plan_weeks WHERE week_start = ?", (week_start,)
            ).fetchone()
        return _week_out(row) if row else {"tasks": {}, "notes": ""}

    def week_daily_tasks(self, week_start: str, for_date: Optional[str] = None) -> list[dict]:
        """周目标 → 日任务模板展开预览（外循环第③步）。

        纯派生不落库（内循环真正执行时才记录）：按周任务推导每天的学生
        任务建议——攻坚讲次微练习 5 题/天、错词再练、FCE/阅读按剩余天数摊派。
        教师在编辑器里看一眼即可，学生端落地是下一步。
        for_date：周清单回看历史日时的「当天」口径（默认今天）。
        """
        w = self._get_week(week_start)
        t = w.get("tasks") or {}
        focus = t.get("focus_kps") or []
        lectures = t.get("lectures") or []
        ref = for_date or date.today().isoformat()
        if week_start <= ref <= add_days(week_start, 6):
            days_left = max(
                1, (date.fromisoformat(add_days(week_start, 6))
                    - date.fromisoformat(ref)).days + 1
            )
        else:
            days_left = 7
        tasks: list[dict] = []
        for lec in focus:
            tasks.append({
                "type": "micro_drill", "lecture": lec,
                "text": f"第{lec}讲 微练习 5 题", "daily": True,
            })
        try:
            with sqlite3.connect(f"file:{self._exam_db()}?mode=ro", uri=True) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) n FROM recite_word_progress"
                    " WHERE user = 'malin' AND status = 'wrong'"
                ).fetchone()
                wrong_words = row["n"]
        except sqlite3.Error:
            wrong_words = 0
        if wrong_words:
            per_day = min(20, max(5, (wrong_words + days_left - 1) // days_left))
            tasks.append({
                "type": "wrong_words", "text": f"错词再练 {per_day} 词/天",
                "detail": f"云端错题本共 {wrong_words} 词，{days_left} 天摊完",
            })
        if lectures:
            per = max(1, -(-len(lectures) // days_left))
            tasks.append({
                "type": "lecture", "text": f"讲次学/测 每天约 {per} 讲",
                "detail": f"本周计划 {len(lectures)} 讲，剩余 {days_left} 天",
            })
        if t.get("fce"):
            tasks.append({
                "type": "fce", "text": f"FCE {t['fce'][0]} 起步",
                "detail": f"共 {len(t['fce'])} 项，建议隔天一项",
            })
        for book, pct in (t.get("reading") or {}).items():
            tasks.append({
                "type": "reading", "text": f"泛读《{book[:12]}》目标 {pct}%",
                "detail": "每天 ≥15 分钟",
            })
        return tasks

    # ---- 内循环：学生今日任务 / 周清单（自动检测 + 手动打卡） ----

    def today_tasks(self, user: str = "malin", day: Optional[str] = None) -> dict:
        """学生某日的任务清单（内循环核心）。

        任务由本周 plan_weeks.tasks 派生（与教师驾驶舱「每日任务展开」
        同一规则），完成态双路：自动检测（当天对应活动表有记录）或
        手动打卡（plan_day_checks）。today 键与 week_daily_tasks 一致。
        """
        d = day or date.today().isoformat()
        ws = monday_of(date.fromisoformat(d)).isoformat()
        derived = self.week_daily_tasks(ws, for_date=d)
        checks = self._day_checks(user, d)
        auto = self._auto_done(user, d)
        out = []
        for t in derived:
            key = _task_key(t)
            t2 = dict(t)
            t2["key"] = key
            t2["auto_done"] = auto.get(key, False)
            t2["manual_done"] = key in checks
            t2["done"] = t2["auto_done"] or t2["manual_done"]
            out.append(t2)
        done_n = sum(1 for x in out if x["done"])
        return {"date": d, "week_start": ws, "tasks": out,
                "done": done_n, "total": len(out)}

    def week_todo(self, user: str = "malin") -> dict:
        """学生周清单：本周目标 + 周一至周日逐日任务完成矩阵。"""
        today_iso = date.today().isoformat()
        ws = monday_of(date.today()).isoformat()
        w = self._get_week(ws)
        t = w.get("tasks") or {}
        days = []
        for i in range(7):
            d = (date.fromisoformat(ws) + timedelta(days=i)).isoformat()
            if d > today_iso:
                days.append({"date": d, "future": True, "done": 0, "total": 0})
                continue
            td = self.today_tasks(user=user, day=d)
            days.append({
                "date": d, "future": False,
                "done": td["done"], "total": td["total"],
            })
        return {
            "week_start": ws,
            "goals": {
                "lectures": t.get("lectures") or [],
                "vocab_goal": t.get("vocab_goal"),
                "fce": t.get("fce") or [],
                "reading": t.get("reading") or {},
                "focus_kps": t.get("focus_kps") or [],
            },
            "days": days,
        }

    def put_day_check(self, user: str, day: str, task_key: str, on: bool) -> None:
        date.fromisoformat(day)  # 非法日期抛 ValueError → 422
        now = datetime.now(_tz.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            if on:
                conn.execute(
                    "INSERT OR REPLACE INTO plan_day_checks"
                    " (user, date, task_key, checked_at) VALUES (?,?,?,?)",
                    (user, day, task_key[:80], now),
                )
            else:
                conn.execute(
                    "DELETE FROM plan_day_checks"
                    " WHERE user = ? AND date = ? AND task_key = ?",
                    (user, day, task_key),
                )

    def _day_checks(self, user: str, day: str) -> set[str]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT task_key FROM plan_day_checks"
                    " WHERE user = ? AND date = ?", (user, day),
                ).fetchall()
            return {r["task_key"] for r in rows}
        except sqlite3.Error:
            return set()

    def _auto_done(self, user: str, day: str) -> dict[str, bool]:
        """当天活动信号 → task_key 自动完成。各表缺表/缺库均容错返回空。"""
        out: dict[str, bool] = {}
        nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
        exam_path = self._exam_db()
        if exam_path and Path(exam_path).exists():
            try:
                with sqlite3.connect(f"file:{exam_path}?mode=ro", uri=True) as conn:
                    rows = conn.execute(
                        "SELECT lecture FROM exam_records"
                        " WHERE date = ?", (day,),
                    ).fetchall()
                for r in rows:
                    out[f"micro:{r[0]}"] = True
                    out[f"lecture:{r[0]}"] = True
            except sqlite3.Error:
                pass
        try:
            with self._connect() as conn:
                tabs = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                if "recite_word_progress" in tabs:
                    row = conn.execute(
                        "SELECT COUNT(*) n FROM recite_word_progress"
                        " WHERE user = ? AND (mastered_at >= ? AND mastered_at < ?)",
                        (user, day, nxt),
                    ).fetchone()
                    if row and row["n"]:
                        out["wrong_words"] = True
                if "recite_sessions" in tabs:
                    row = conn.execute(
                        "SELECT COUNT(*) n FROM recite_sessions"
                        " WHERE user = ? AND (created_at >= ? AND created_at < ?)",
                        (user, day, nxt),
                    ).fetchone()
                    if row and row["n"]:
                        out.setdefault("wrong_words", True)
                if "fce_submission" in tabs:
                    row = conn.execute(
                        "SELECT COUNT(*) n FROM fce_submission"
                        " WHERE user = ? AND (created_at >= ? AND created_at < ?)",
                        (user, day, nxt),
                    ).fetchone()
                    if row and row["n"]:
                        out["fce"] = True
                if "focus_sessions" in tabs:
                    rows = conn.execute(
                        "SELECT book_title FROM focus_sessions"
                        " WHERE user = ? AND (created_at >= ? AND created_at < ?)",
                        (user, day, nxt),
                    ).fetchall()
                    for r in rows:
                        if r["book_title"]:
                            out[f"reading:{r['book_title'][:12]}"] = True
                        out.setdefault("reading", True)
        except sqlite3.Error:
            pass
        return out


def _paper_short(paper: str) -> str:
    if paper and paper.startswith("Reading"):
        return "RUE"
    return (paper or "")[:4]


def add_days(iso_str: str, n: int) -> str:
    return (date.fromisoformat(iso_str) + timedelta(days=n)).isoformat()


def _task_key(t: dict) -> str:
    """派生任务 → 稳定键（打卡表 task_key 口径）。"""
    ty = t.get("type")
    if ty == "micro_drill":
        return f"micro:{t['lecture']}"
    if ty == "lecture":
        # week_daily_tasks 的 lecture 任务是摊派汇总（可能每天讲数不同），
        # 键固定为 lecture，自动检测按当天任意讲次命中
        return "lecture"
    return str(ty)


def _chapter_at_words(chs: list[dict], percent: float, total_words: int) -> Optional[dict]:
    """全书百分比 → 落点章（按各章词数累计线性映射）。

    返回 {idx, title, chapter_percent}：chapter_percent 为「到本章末读了多少」
    的全书口径（词数累计/总词数），供前端拼「第X章 xx%~第Y章 xx%」区间。
    percent 是视线带驱动的全书精确值，与词数口径有 ±1 章误差，够展示用。
    """
    if not chs or total_words <= 0:
        return None
    words_seen = 0
    for c in chs:
        end_pct = round((words_seen + c["word_count"]) / total_words * 100, 1)
        if percent <= end_pct:
            return {
                "idx": c["idx"], "title": c["title"],
                "start_percent": round(words_seen / total_words * 100, 1),
                "end_percent": end_pct,
            }
        words_seen += c["word_count"]
    last = chs[-1]
    return {
        "idx": last["idx"], "title": last["title"],
        "start_percent": round((total_words - last["word_count"]) / total_words * 100, 1),
        "end_percent": 100.0,
    }


def _week_out(r: sqlite3.Row) -> dict:
    try:
        tasks = json.loads(r["tasks"] or "{}")
    except (ValueError, TypeError):
        tasks = {}
    return {
        "week_start": r["week_start"], "tasks": tasks,
        "notes": r["notes"] or "", "updated_at": r["updated_at"],
    }

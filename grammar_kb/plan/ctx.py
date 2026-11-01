"""周级请求上下文：一次视图请求内的信号预取与 memo（调用链局部传递）。

背景：week_view / overview / month_view 对同一周逐日调 ``today_tasks`` /
``week_daily_tasks``，每天各自重算同一批「周窗口信号」（lecture_done、
_fce_actuals、_done_article_ids、_retold_video_ids、_vocab_actuals…），
周内前缀窗口反复扫同一批表——同一周 7 天就是 O(天²) 次行扫描与连接。

WeekCtx 把每个窗口的原始行每周只捞一次，逐日前缀窗口在 Python 里切片。
切片的比较口径与原 SQL 逐条一致：

- exam_records.date 本来就是 ``YYYY-MM-DD`` 文本，原 SQL 也是裸字符串
  比较（``date >= date(?)`` 只归一化参数），Python 同序比较等价；
- cn_dt/cn_date 表达式结果是 ``YYYY-MM-DD[ HH:MM:SS]`` naive 文本，
  与传入边界串同构，Python 同序比较等价；
- 词汇按级计数改为「该生 mastered 行一次拉回 + 集合成员判断」，
  与 2000 占位符 ``word IN (…)`` 的 TEXT 二进制比较完全同口径。

另附标题/书目/规则开关等「请求内不变」数据的 memo。全部状态挂在
ctx 实例上、随调用链局部传递，不写 PlanStore 单例（FastAPI 线程池
并发请求互不干扰）。
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .. import dbreg
from ..clock import cn_date, cn_dt
from .util import add_days


def _sql_date(value: str) -> str:
    """date(?) 参数归一：``YYYY-MM-DD[ HH:MM:SS]`` → ``YYYY-MM-DD``。"""
    return str(value)[:10]


class WeekCtx:
    """(user, week_start) 一周的信号缓存。窗口内子查询走切片，越界回退直查。"""

    def __init__(self, store, user: str, week_start: str):
        self.store = store
        self.user = user
        self.week_start = week_start
        self.week_hi = add_days(week_start, 7)
        # ---- 懒预取槽 ----
        self._exam_rows: list | None = None      # (lecture, date_raw, kind)
        self._exam_has_user: bool | None = None
        self._topic_rows: list | None = None     # (lecture, cn_day)
        self._flag_cache: dict[str, int] = {}
        self._fce_rows: list | None = None       # (label, cn_dt)
        self._article_rows: list | None = None   # (article_id, created_at)
        self._video_rows: list | None = None     # (vid, cn_dt)
        self._mastered_rows: list | None = None  # (word, cn_day)
        self._level_words: dict = {}
        self._week_row: dict | None = None
        self._history: dict | None = None
        self._article_titles: dict = {}
        self._video_titles: dict = {}
        self._paper_levels: dict | None = None
        self._wrong_words: int | None = None
        self._match_book: dict = {}
        self._book_state: dict = {}
        self._chapter_nos: dict = {}
        self._ch_words: dict = {}
        self._read_words: dict = {}
        self._lib_sessions: dict = {}

    # ---- 规则开关（读路径，见 rules._flag） ----

    def flag(self, rule_id: str) -> int:
        if rule_id not in self._flag_cache:
            self._flag_cache[rule_id] = self.store._flag(self.user, rule_id)
        return self._flag_cache[rule_id]

    # ---- 讲次完成内核（成绩 ∪ 自学专题），口径同 rules.lecture_done ----

    def _exam_done_rows(self) -> list:
        """周窗口内成绩行 (lecture, date 原值, kind)。镜像 _exam_done_lectures。"""
        if self._exam_rows is None:
            self._exam_rows = []
            path = self.store._exam_db()
            if path and Path(path).exists():
                scope = bool(self.flag("exam_user_scope"))
                try:
                    with dbreg.connect(path, readonly=True) as conn:
                        cols = {r[1] for r in conn.execute(
                            "PRAGMA table_info(exam_records)")}
                        self._exam_has_user = "user" in cols
                        kind_col = ("COALESCE(kind, 'lecture')" if "kind" in cols
                                    else "'lecture'")
                        sql = (f"SELECT lecture, date, {kind_col} AS kind"
                               " FROM exam_records"
                               " WHERE date >= date(?) AND date < date(?)")
                        args: list = [self.week_start, self.week_hi]
                        if self._exam_has_user and scope:
                            sql += " AND user = ?"
                            args.append(self.user)
                        self._exam_rows = [
                            (r[0], r[1], r[2])
                            for r in conn.execute(sql, args).fetchall()
                        ]
                except sqlite3.Error:
                    self._exam_rows = []
        return self._exam_rows

    def _topic_done_rows(self) -> list:
        """周窗口内学完的自学专题 (讲次, 北京日)。镜像 _topic_done_lectures。"""
        if self._topic_rows is None:
            self._topic_rows = []
            try:
                with self.store._connect() as conn:
                    tabs = {r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")}
                    if "topic_progress" not in tabs or "topic_content" not in tabs:
                        return self._topic_rows
                    day = ("date(julianday(COALESCE(NULLIF(completed_at,''),"
                           " updated_at)) + 8.0/24)")
                    rows = conn.execute(
                        "SELECT topic_id, done_keys, " + day + " AS d"
                        " FROM topic_progress"
                        " WHERE user = ? AND completed_at != ''"
                        f" AND {day} >= date(?) AND {day} < date(?)",
                        (self.user, self.week_start, self.week_hi),
                    ).fetchall()
                    out: list = []
                    for r in rows:
                        m = re.match(r"lecture-(\d+)", r["topic_id"] or "")
                        if not m:
                            continue
                        try:
                            done_set = set(json.loads(r["done_keys"] or "[]"))
                        except (ValueError, TypeError):
                            continue
                        need = self.store._topic_node_keys(conn, r["topic_id"])
                        if need and done_set >= need:
                            out.append((int(m.group(1)), r["d"]))
                    self._topic_rows = out
            except sqlite3.Error:
                self._topic_rows = []
        return self._topic_rows

    def lecture_done(self, lo: str, hi: str, kind: str) -> set:
        """[lo, hi) 讲次完成集合（ctx 窗口内切片，越界回退逐窗查询）。"""
        if not (self.week_start <= _sql_date(lo) and _sql_date(hi) <= self.week_hi):
            return self.store.lecture_done(lo, hi, self.user, kind)
        lo_d, hi_d = _sql_date(lo), _sql_date(hi)
        out = {lec for lec, d, k in self._exam_done_rows()
               if k == kind and lo_d <= d < hi_d}
        if kind == "lecture" and self.flag("topic_counts_lecture"):
            out |= {lec for lec, d in self._topic_done_rows()
                    if lo_d <= d < hi_d}
        return out

    def exam_lectures_on(self, day: str) -> set:
        """当天有任意成绩登记的讲次（_auto_done 的 micro 口径：date 精确相等）。"""
        return {lec for lec, d, _k in self._exam_done_rows() if d == day}

    # ---- FCE 提交覆盖（_fce_actuals 同口径：cn_dt 窗口 + 标签集合） ----

    def _fce_all_rows(self) -> list:
        if self._fce_rows is None:
            self._fce_rows = []
            try:
                with self.store._connect() as conn:
                    from .util import _paper_short
                    rows = conn.execute(
                        "SELECT test_id, paper, part,"
                        f" {cn_dt('created_at')} AS d FROM fce_submission"
                        f" WHERE user = ? AND {cn_dt('created_at')} >= ?"
                        f" AND {cn_dt('created_at')} < ?",
                        (self.user, f"{self.week_start} 00:00:00",
                         f"{self.week_hi} 00:00:00"),
                    ).fetchall()
                    self._fce_rows = [
                        (f"Test {r['test_id']} · {_paper_short(r['paper'])}"
                         f" P{r['part']}", r["d"])
                        for r in rows
                    ]
            except sqlite3.Error:
                self._fce_rows = []
        return self._fce_rows

    def fce_actuals(self, lo: str, hi: str) -> list:
        """[lo, hi) 提交覆盖的 Test/Part 标签（sorted）。"""
        if not (self.week_start <= _sql_date(lo) and _sql_date(hi) <= self.week_hi):
            return self.store._fce_actuals(lo, hi, self.user)
        return sorted({label for label, d in self._fce_all_rows()
                       if lo <= d < hi})

    # ---- 精读朗读 / 视频复述（窗口内 DISTINCT id） ----

    def _article_all_rows(self) -> list:
        if self._article_rows is None:
            self._article_rows = []
            try:
                with self.store._connect() as conn:
                    self._article_rows = [
                        (r["article_id"], r["created_at"]) for r in conn.execute(
                            "SELECT DISTINCT article_id, created_at"
                            " FROM reading_recordings"
                            " WHERE user = ? AND created_at >= ?"
                            " AND created_at < ?",
                            (self.user, f"{self.week_start} 00:00:00",
                             f"{self.week_hi} 00:00:00"),
                        ).fetchall()
                    ]
            except sqlite3.Error:
                self._article_rows = []
        return self._article_rows

    def done_article_ids(self, lo: str, hi: str) -> set:
        """[lo, hi) 已提交朗读录音的文章 id（created_at 裸串窗口，同原 SQL）。"""
        if not (self.week_start <= _sql_date(lo) and _sql_date(hi) <= self.week_hi):
            return self.store._done_article_ids(lo, hi, self.user)
        # 原实现不过滤 NULL article_id（集合里带 None 也无害），保持同构
        return {aid for aid, created in self._article_all_rows()
                if lo <= created < hi}

    def _video_all_rows(self) -> list:
        if self._video_rows is None:
            self._video_rows = []
            try:
                with self.store._connect() as conn:
                    names = {r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")}
                    if "video_retell" not in names:
                        return self._video_rows
                    self._video_rows = [
                        (r["vid"], r["d"]) for r in conn.execute(
                            "SELECT DISTINCT vid,"
                            f" {cn_dt('created_at')} AS d FROM video_retell"
                            f" WHERE user = ? AND {cn_dt('created_at')} >= ?"
                            f" AND {cn_dt('created_at')} < ?",
                            (self.user, f"{self.week_start} 00:00:00",
                             f"{self.week_hi} 00:00:00"),
                        ).fetchall()
                    ]
            except sqlite3.Error:
                self._video_rows = []
        return self._video_rows

    def retold_video_ids(self, lo: str, hi: str) -> set:
        """[lo, hi) 提交过复述的视频 vid（_cn_bound 窗口，同原 SQL）。"""
        from .schedule import _cn_bound
        if not (self.week_start <= _sql_date(lo) and _sql_date(hi) <= self.week_hi):
            return self.store._retold_video_ids(lo, hi, self.user)
        blo, bhi = _cn_bound(lo), _cn_bound(hi)
        return {vid for vid, d in self._video_all_rows()
                if vid and blo <= d < bhi}

    # ---- 词汇（mastered 行一次拉回，集合成员计数同 word IN 口径） ----

    def mastered_day_rows(self) -> list:
        """该生全部已掌握词 (word, 北京日)。

        行数 = 掌握词数（百级）。一次拉回后 Python 集合计数，与按级
        ``word IN (2000 占位符)`` 的 TEXT 二进制比较完全同口径，免去
        每次调用解析两千参数。
        """
        if self._mastered_rows is None:
            self._mastered_rows = []
            try:
                with self.store._connect() as conn:
                    mp = cn_date("mastered_at")
                    self._mastered_rows = [
                        (r["word"], r["d"]) for r in conn.execute(
                            "SELECT word, " + mp + " AS d FROM recite_word_progress"
                            " WHERE user = ? AND mastered_at IS NOT NULL",
                            (self.user,),
                        ).fetchall()
                    ]
            except sqlite3.Error:
                self._mastered_rows = []
        return self._mastered_rows

    def vocab_actuals(self, lo: str, hi: str,
                      words=None) -> int:
        """[lo, hi) 新掌握词数（words 限定词集；口径同 _vocab_actuals）。"""
        lo_d, hi_d = _sql_date(lo), _sql_date(hi)
        return sum(1 for w, d in self.mastered_day_rows()
                   if lo_d <= d < hi_d and (words is None or w in words))

    def vocab_mastered_before(self, day: str, words=None) -> int:
        """day 00:00 前已掌握词数（口径同 _vocab_mastered_before）。"""
        d0 = _sql_date(day)
        return sum(1 for w, d in self.mastered_day_rows()
                   if d < d0 and (words is None or w in words))

    def level_words(self, level):
        """目标词集 memo（_vocab_level_words 同一实现，每周算一次）。"""
        key = None if level is None else int(level)
        if key not in self._level_words:
            self._level_words[key] = self.store._vocab_level_words(level)
        return self._level_words[key]

    # ---- 周计划 / 日快照 ----

    def week_tasks(self, week_start: str) -> dict:
        """_get_week memo（仅缓存本 ctx 的周；其他周直查）。"""
        if week_start != self.week_start:
            return self.store._get_week(week_start, user=self.user)
        if self._week_row is None:
            self._week_row = self.store._get_week(week_start, user=self.user)
        return self._week_row

    def history_day(self, day: str):
        """plan_history_days 快照，整周一次拉回（无记录返回 None 同原语义）。"""
        if self._history is None:
            self._history = {}
            try:
                with self.store._connect() as conn:
                    rows = conn.execute(
                        "SELECT date, tasks FROM plan_history_days"
                        " WHERE user = ? AND date >= ? AND date <= ?",
                        (self.user, self.week_start, add_days(self.week_start, 6)),
                    ).fetchall()
                for r in rows:
                    try:
                        self._history[r["date"]] = json.loads(r["tasks"] or "[]")
                    except (ValueError, TypeError):
                        self._history[r["date"]] = []
            except sqlite3.Error:
                self._history = {}
        return self._history.get(day)

    # ---- 标题 / 试卷级别 / 错词数（请求内不变，memo） ----

    def article_title(self, article_id) -> str | None:
        key = int(article_id)
        if key not in self._article_titles:
            self._article_titles[key] = self.store._article_title(key)
        return self._article_titles[key]

    def video_title(self, vid) -> str | None:
        key = str(vid)
        if key not in self._video_titles:
            self._video_titles[key] = self.store._video_title(key)
        return self._video_titles[key]

    def paper_level(self, paper_id: str):
        """vocab_paper_history 的 pid → level 整表一次拉回（原每卷一查）。"""
        if self._paper_levels is None:
            self._paper_levels = {}
            try:
                with self.store._connect() as conn:
                    for r in conn.execute(
                            "SELECT paper_id, level FROM vocab_paper_history"):
                        self._paper_levels[r["paper_id"]] = r["level"]
            except sqlite3.Error:
                self._paper_levels = {}
        key = str(paper_id)[:64]
        return self._paper_levels.get(key)

    def wrong_words_count(self) -> int:
        if self._wrong_words is None:
            self._wrong_words = self.store._wrong_words_count(self.user)
        return self._wrong_words

    # ---- 泛读书目（请求内不变，memo） ----

    def match_book(self, book_key: str):
        if book_key not in self._match_book:
            self._match_book[book_key] = self.store._match_book(
                book_key, self.user)
        return self._match_book[book_key]

    def book_reading_state(self, book_id: int, goal_val):
        key = (book_id, str(goal_val))
        if key not in self._book_state:
            self._book_state[key] = self.store._book_reading_state(
                book_id, goal_val, self.user)
        return self._book_state[key]

    def chapter_nos(self, book_id: int, idxs: list) -> list:
        key = (book_id, tuple(idxs))
        if key not in self._chapter_nos:
            self._chapter_nos[key] = self.store._chapter_nos(book_id, idxs)
        return self._chapter_nos[key]

    def chapters_words_sum(self, reading_chapters: dict) -> int:
        key = tuple(sorted((str(k), tuple(v or []))
                           for k, v in (reading_chapters or {}).items()))
        if key not in self._ch_words:
            self._ch_words[key] = self.store._chapters_words_sum(reading_chapters)
        return self._ch_words[key]

    def read_words_in_chapters(self, book_id: int, idxs: list) -> int:
        key = (book_id, tuple(idxs))
        if key not in self._read_words:
            self._read_words[key] = self.store._read_words_in_chapters(
                self.user, book_id, idxs)
        return self._read_words[key]

    def library_sessions(self, book_id):
        key = book_id
        if key not in self._lib_sessions:
            self._lib_sessions[key] = self.store._library_sessions_all(
                self.user, book_id)
        return self._lib_sessions[key]

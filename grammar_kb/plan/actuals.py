"""周窗口实录：考试、词汇、FCE、阅读。"""
from __future__ import annotations

import sqlite3
from bisect import bisect_right
from pathlib import Path
from typing import Optional

from .. import dbreg
from ..clock import cn_date, cn_dt, today_cn, week_bounds
from ..paths import data_path
from .util import _chapter_at_words, _paper_short, add_days
class ActualsMixin:

    # ---- 周完成度实时聚合 ----

    def week_actuals(self, week_start: str, user: str) -> dict:
        """某周的实际完成情况（课程/词汇/FCE/阅读），供 GET 拼装与上周回顾。"""
        lo, hi = week_bounds(week_start)
        out: dict = {
            "lectures": self._exam_actuals(lo, hi),
            "vocab_mastered": self._vocab_actuals(lo, hi, user),
            "fce_parts": self._fce_actuals(lo, hi, user),
            "reading": self._reading_actuals(lo, hi, user),
        }
        return out

    def weeks_actuals_bulk(self, week_starts: list[str], user: str) -> dict:
        """多周 actuals 一次聚合（/plan/weeks 批量路径）。

        返回 ``{week_start: {lectures, vocab_mastered, fce_parts, reading}}``，
        每周结构与 :meth:`week_actuals` 完全一致。原先每周 4 类各开连接
        各查一遍（还各带一次 PRAGMA 探测），几十周就是上百个连接；这里
        对 [最早周起点, min(明天零点, 最后周下界)] 每类各查一趟，
        Python 按周分桶。跨过今天零点的部分不查——事件不可能落在未来
        （未来周由调用方直接零值，不进本方法）。

        周与周之间的空档周（plan_weeks 没存的周）里的记录不归属任何
        输入周，丢弃——与逐周查询口径一致。
        """
        weeks = sorted(set(week_starts))
        if not weeks:
            return {}
        lo = f"{weeks[0]} 00:00:00"
        last_hi = f"{add_days(weeks[-1], 7)} 00:00:00"
        tomorrow = f"{add_days(today_cn().isoformat(), 1)} 00:00:00"
        hi = min(last_hi, tomorrow)

        def bucket(value: str) -> Optional[str]:
            """记录时间 → 归属周（不在输入周集/空档周 → None）。"""
            i = bisect_right(weeks, value) - 1
            if i < 0:
                return None
            ws = weeks[i]
            return ws if value < add_days(ws, 7) else None

        lectures: dict[str, list] = {ws: [] for ws in weeks}
        vocab: dict[str, int] = {ws: 0 for ws in weeks}
        fce: dict[str, list] = {ws: [] for ws in weeks}
        reading: dict[str, list] = {ws: [] for ws in weeks}

        # ---- 成绩（exam.db 一连接一 PRAGMA） ----
        path = self._exam_db()
        if path and Path(path).exists():
            try:
                with dbreg.connect(path, readonly=True) as conn:
                    conn.row_factory = sqlite3.Row
                    cols = {r[1] for r in conn.execute(
                        "PRAGMA table_info(exam_records)")}
                    kind_col = ("COALESCE(kind, 'lecture')" if "kind" in cols
                                else "'lecture'")
                    rows = conn.execute(
                        f"SELECT lecture, date, score, {kind_col} AS kind"
                        " FROM exam_records WHERE date >= date(?) AND date < date(?)"
                        " ORDER BY date, lecture", (lo, hi)).fetchall()
                for r in rows:
                    ws = bucket(r["date"])
                    if ws is not None:
                        lectures[ws].append(dict(r))
            except sqlite3.Error:
                pass

        # ---- 词汇（fce.db/主库，按北京日 GROUP BY） ----
        try:
            with self._connect() as conn:
                mp = self._MP_DATE
                rows = conn.execute(
                    f"SELECT {mp} AS d, COUNT(*) n FROM recite_word_progress"
                    f" WHERE user = ? AND {mp} >= date(?) AND {mp} < date(?)"
                    " GROUP BY d",
                    (user, lo, hi)).fetchall()
            for r in rows:
                ws = bucket(r["d"])
                if ws is not None:
                    vocab[ws] += r["n"]
        except sqlite3.Error:
            pass

        # ---- FCE（主库，按北京时刻分桶；每周内 sorted 去重同 _fce_actuals） ----
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT test_id, paper, part,"
                    f" {cn_dt('created_at')} AS d FROM fce_submission"
                    f" WHERE user = ? AND {cn_dt('created_at')} >= ?"
                    f" AND {cn_dt('created_at')} < ?",
                    (user, lo, hi)).fetchall()
            for r in rows:
                ws = bucket(r["d"])
                if ws is not None:
                    fce[ws].append(
                        f"Test {r['test_id']} · {_paper_short(r['paper'])}"
                        f" P{r['part']}")
            for ws in fce:
                fce[ws] = sorted(set(fce[ws]))
        except sqlite3.Error:
            for ws in fce:
                fce[ws] = []

        # ---- 阅读（library.db 一连接：进度 join + 章词数一次取齐） ----
        reading = self._reading_actuals_bulk(weeks, user, lo, hi, bucket)

        return {ws: {"lectures": lectures[ws], "vocab_mastered": vocab[ws],
                     "fce_parts": fce[ws], "reading": reading[ws]}
                for ws in weeks}

    def _reading_actuals_bulk(self, weeks: list[str], user: str,
                              lo: str, hi: str, bucket) -> dict[str, list]:
        """:meth:`_reading_actuals` 的多周版：同一窗口一趟取进度与章词数，
        按周分桶后逐行走同一套全书口径后处理。"""
        out: dict[str, list] = {ws: [] for ws in weeks}
        path = self.library_db_path or str(data_path("library.db"))
        if not Path(path).exists():
            return out
        try:
            with dbreg.connect(path, readonly=True) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT b.id AS book_id, b.title, b.chapter_count,"
                    " p.percent, p.reading_seconds, p.updated_at,"
                    f" {cn_dt('p.updated_at')} AS d"
                    " FROM reading_progress p JOIN books b ON b.id = p.book_id"
                    f" WHERE p.user = ? AND {cn_dt('p.updated_at')} >= ?"
                    f" AND {cn_dt('p.updated_at')} < ?",
                    (user, lo, hi),
                ).fetchall()
                if not rows:
                    return out
                ph = ",".join("?" * len(rows))
                ch_rows = conn.execute(
                    f"SELECT book_id, idx, title, word_count FROM chapters"
                    f" WHERE book_id IN ({ph}) ORDER BY book_id, idx",
                    [r["book_id"] for r in rows],
                ).fetchall()
        except sqlite3.Error:
            return out
        chapters: dict[int, list[dict]] = {}
        for c in ch_rows:
            chapters.setdefault(c["book_id"], []).append(
                {"idx": c["idx"], "title": c["title"],
                 "word_count": c["word_count"] or 0}
            )
        by_week: dict[str, list] = {}
        for r in rows:
            ws = bucket(r["d"])
            if ws is None:
                continue
            d = {k: r[k] for k in ("book_id", "title", "chapter_count",
                                   "percent", "reading_seconds", "updated_at")}
            chs = chapters.get(r["book_id"], [])
            total_words = sum(c["word_count"] for c in chs)
            d["total_words"] = total_words
            d["chapters_count"] = len(chs) or (r["chapter_count"] or 0)
            ch_at = _chapter_at_words(chs, r["percent"] or 0.0, total_words)
            d["current_chapter"] = ch_at["no"] if ch_at else None
            by_week.setdefault(ws, []).append(d)
        for ws, items in by_week.items():
            out[ws] = items
        return out


    def _exam_actuals(self, lo: str, hi: str, kind: Optional[str] = None) -> list[dict]:
        """周内新增的哈一成绩（exam_records 单学生表）。库不存在返回空。

        kind 过滤：lecture=课程测验 / hw=作业卷（老库无 kind 列视为
        lecture——与 _auto_done 同口径）；None=不过滤（周回顾/上周复盘用）。
        """
        path = self._exam_db()
        if not path or not Path(path).exists():
            return []
        try:
            with dbreg.connect(path, readonly=True) as conn:
                conn.row_factory = sqlite3.Row
                cols = {r[1] for r in conn.execute(
                    "PRAGMA table_info(exam_records)")}
                kind_col = ("COALESCE(kind, 'lecture')" if "kind" in cols
                            else "'lecture'")
                sql = (f"SELECT lecture, date, score, {kind_col} AS kind"
                       " FROM exam_records WHERE date >= date(?) AND date < date(?)")
                args: list = [lo, hi]
                if kind is not None:
                    sql += f" AND {kind_col} = ?"
                    args.append(kind)
                rows = conn.execute(sql + " ORDER BY date, lecture", args).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []


    def _exam_db(self) -> Optional[str]:
        if self.exam_db_path:
            return self.exam_db_path
        try:
            from ..exam_store import default_exam_db_path
            return default_exam_db_path()
        except Exception:
            return None


    def _vocab_level_words(self, level: Optional[int]) -> Optional[set[str]]:
        """目标词集（grammar.db vocab_word；level=None=不过滤）。

        周计划 vocab_goal 是这一级的累计目标词数（编辑器写成
        周初已会 + 本周新掌握）。词集只取 level = lv，其他级别不进
        分子分母。词库不可用时返回 None（降级为全词口径）。
        """
        if level is None:
            return None
        path = self.grammar_db_path or str(
            data_path("grammar.db"))
        if not Path(path).exists():
            return None
        try:
            with dbreg.connect(path, readonly=True) as conn:
                return {r[0] for r in conn.execute(
                    "SELECT word FROM vocab_word WHERE level = ?", (int(level),))}
        except sqlite3.Error:
            return None


    # mastered_at 是 UTC isoformat（2026-10-04T16:13:52+00:00）。周窗口和
    # 「今天」都是北京日历，走 clock.cn_date，避免 0–8 点被算进前一天。
    _MP_DATE = cn_date("mastered_at")

    def _mastered_day_rows(self, user: str) -> list[tuple[str, str]]:
        """该生全部已掌握词 → [(word, 北京日)]。

        行数 = 掌握词数（现网几百行）。按级计数改走这里：一次拉回后
        Python 集合判断与 ``word IN (2000 占位符)`` 的 TEXT 二进制比较
        完全同口径，免去每次解析两千个参数的 SQL（L2 词表 2170 词，
        编辑器/周进度每级各查一遍）。
        """
        try:
            with self._connect() as conn:
                return [
                    (r["word"], r["d"]) for r in conn.execute(
                        "SELECT word, " + self._MP_DATE + " AS d"
                        " FROM recite_word_progress"
                        " WHERE user = ? AND mastered_at IS NOT NULL",
                        (user,),
                    ).fetchall()
                ]
        except sqlite3.Error:
            return []

    @staticmethod
    def _count_in_window(rows: list[tuple[str, str]], lo: Optional[str],
                         hi: Optional[str], words: Optional[set[str]]) -> int:
        """[(word, day)] 里窗口 [lo, hi)（任一端 None=不限）∩ 词集的行数。"""
        lo_d = None if lo is None else str(lo)[:10]
        hi_d = None if hi is None else str(hi)[:10]
        return sum(1 for w, d in rows
                   if (lo_d is None or lo_d <= d) and (hi_d is None or d < hi_d)
                   and (words is None or w in words))

    def _vocab_actuals(self, lo: str, hi: str, user: str,
                       words: Optional[set[str]] = None) -> int:
        """周内新掌握词数（mastered_at 落在窗口内；words 限定词集）。"""
        try:
            if words is None:
                with self._connect() as conn:
                    row = conn.execute(
                        f"SELECT COUNT(*) n FROM recite_word_progress"
                        f" WHERE user = ? AND {self._MP_DATE} >= date(?)"
                        f" AND {self._MP_DATE} < date(?)",
                        (user, lo, hi),
                    ).fetchone()
                    return row["n"]
            return self._count_in_window(
                self._mastered_day_rows(user), lo, hi, words)
        except sqlite3.Error:
            return 0


    def _vocab_mastered_before(self, day: str, user: str,
                               words: Optional[set[str]] = None) -> int:
        """某日 00:00 前已掌握的词数（周词汇差额口径的基线）。"""
        try:
            if words is None:
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) n FROM recite_word_progress"
                        f" WHERE user = ? AND mastered_at IS NOT NULL"
                        f" AND {self._MP_DATE} < date(?)",
                        (user, day)).fetchone()
                    return row["n"] if row else 0
            return self._count_in_window(
                self._mastered_day_rows(user), None, day, words)
        except sqlite3.Error:
            return 0


    def _fce_actuals(self, lo: str, hi: str, user: str) -> list[str]:
        """周内提交覆盖的 Test/Part（"Test 1 · RUE P1" 形式）。"""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT test_id, paper, part FROM fce_submission"
                    f" WHERE user = ? AND {cn_dt('created_at')} >= ?"
                    f" AND {cn_dt('created_at')} < ?",
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
            data_path("library.db")
        )
        if not Path(path).exists():
            return []
        try:
            with dbreg.connect(path, readonly=True) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT b.id AS book_id, b.title, b.chapter_count,"
                    " p.percent, p.reading_seconds, p.updated_at"
                    " FROM reading_progress p JOIN books b ON b.id = p.book_id"
                    f" WHERE p.user = ? AND {cn_dt('p.updated_at')} >= ?"
                    f" AND {cn_dt('p.updated_at')} < ?",
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
            ch_at = _chapter_at_words(chs, r["percent"] or 0.0, total_words)
            d["current_chapter"] = ch_at["no"] if ch_at else None
            out.append(d)
        return out

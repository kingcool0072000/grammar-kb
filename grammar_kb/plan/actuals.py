"""周窗口实录：考试、词汇、FCE、阅读。"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from ..clock import cn_date, cn_dt, week_bounds
from ..paths import data_path
from .util import _chapter_at_words, _paper_short
class ActualsMixin:

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


    def _exam_actuals(self, lo: str, hi: str, kind: Optional[str] = None) -> list[dict]:
        """周内新增的哈一成绩（exam_records 单学生表）。库不存在返回空。

        kind 过滤：lecture=课程测验 / hw=作业卷（老库无 kind 列视为
        lecture——与 _auto_done 同口径）；None=不过滤（周回顾/上周复盘用）。
        """
        path = self._exam_db()
        if not path or not Path(path).exists():
            return []
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
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
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
                return {r[0] for r in conn.execute(
                    "SELECT word FROM vocab_word WHERE level = ?", (int(level),))}
        except sqlite3.Error:
            return None


    # mastered_at 是 UTC isoformat（2026-10-04T16:13:52+00:00）。周窗口和
    # 「今天」都是北京日历，走 clock.cn_date，避免 0–8 点被算进前一天。
    _MP_DATE = cn_date("mastered_at")

    def _vocab_actuals(self, lo: str, hi: str, user: str,
                       words: Optional[set[str]] = None) -> int:
        """周内新掌握词数（mastered_at 落在窗口内；words 限定词集）。"""
        try:
            with self._connect() as conn:
                if words is None:
                    row = conn.execute(
                        f"SELECT COUNT(*) n FROM recite_word_progress"
                        f" WHERE user = ? AND {self._MP_DATE} >= date(?)"
                        f" AND {self._MP_DATE} < date(?)",
                        (user, lo, hi),
                    ).fetchone()
                else:
                    ph = ",".join("?" * len(words)) or "NULL"
                    row = conn.execute(
                        f"SELECT COUNT(*) n FROM recite_word_progress"
                        f" WHERE user = ? AND {self._MP_DATE} >= date(?)"
                        f" AND {self._MP_DATE} < date(?)"
                        f" AND word IN ({ph})",
                        (user, lo, hi, *words),
                    ).fetchone()
                return row["n"]
        except sqlite3.Error:
            return 0


    def _vocab_mastered_before(self, day: str, user: str,
                               words: Optional[set[str]] = None) -> int:
        """某日 00:00 前已掌握的词数（周词汇差额口径的基线）。"""
        try:
            with self._connect() as conn:
                if words is None:
                    row = conn.execute(
                        "SELECT COUNT(*) n FROM recite_word_progress"
                        f" WHERE user = ? AND mastered_at IS NOT NULL"
                        f" AND {self._MP_DATE} < date(?)",
                        (user, day)).fetchone()
                else:
                    ph = ",".join("?" * len(words)) or "NULL"
                    row = conn.execute(
                        "SELECT COUNT(*) n FROM recite_word_progress"
                        f" WHERE user = ? AND mastered_at IS NOT NULL"
                        f" AND {self._MP_DATE} < date(?)"
                        f" AND word IN ({ph})",
                        (user, day, *words),
                    ).fetchone()
                return row["n"] if row else 0
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
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
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

"""总目标读写、完成状态与目标进度（编辑器选项源见 goals_editor）。"""
from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime, timezone as _tz
from typing import Optional

from .util import _paper_short


class GoalsMixin:
    """get/put_goals、goal_progress、完成判定与泛读目标词数。"""

    def get_goals(self, user: str) -> dict:
        """学生总目标（冲刺目标）。无记录返回默认骨架。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT goals FROM plan_goals WHERE user = ?", (user,)
            ).fetchone()
        if row:
            try:
                return json.loads(row["goals"] or "{}")
            except (ValueError, TypeError):
                pass
        return {}

    def put_goals(self, user: str, goals: dict) -> dict:
        now = datetime.now(_tz.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO plan_goals (user, goals, updated_at)"
                " VALUES (?,?,?)",
                (user, json.dumps(goals or {}, ensure_ascii=False), now),
            )
        return goals

    def goal_progress(self, user: str = "malin") -> dict:
        """总目标进度：六维度 done/goal 覆盖 + 总百分比。

        维度完成口径（与 _completion_status 一致）：讲次=考过；FCE Part=提交过；
        精读=有录音；泛读=所有时间实录词数总和/目标书目词数总和（会话级）；
        试卷=其级别考试 ≥80；词汇=已掌握词数/vocab_target。
        """
        g = self.get_goals(user)
        done = self._completion_status(user)
        dims = []

        def dim(icon, name, ok, total, extra=""):
            dims.append({"icon": icon, "name": name,
                         "done": ok, "total": total, "extra": extra})

        gl = g.get("lectures") or []
        if gl:
            d = len(set(gl) & set(done["lectures"]))
            dim("📚", "语法课程", d, len(gl))

        if g.get("vocab_target"):
            mastered = 0
            try:
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) n FROM recite_word_progress"
                        " WHERE user = ? AND mastered_at IS NOT NULL",
                        (user,)).fetchone()
                    mastered = row["n"] if row else 0
            except sqlite3.Error:
                pass
            dim("🔤", f"词汇 +{g['vocab_target']}",
                min(mastered, g["vocab_target"]), g["vocab_target"],
                extra=f"已掌握 {mastered}")
        elif g.get("vocab_levels"):
            passed = len((set(g["vocab_levels"]) & set(done["vocab_levels"])))
            dim("🔤", "词汇级别", passed, len(g["vocab_levels"]))

        gf = g.get("fce_parts") or []
        if gf:
            d = len(set(gf) & set(done["fce_parts"]))
            dim("🎧", "FCE Part", d, len(gf))

        ga_ = g.get("articles") or []
        if ga_:
            # 精读与朗读合并：完成判定=有录音（done["articles"] 本就以录音为准）
            d = len(set(ga_) & set(done["articles"]))
            dim("🎤", "精读朗读", d, len(ga_))

        gr = g.get("reading") or []
        gr_ch = g.get("reading_chapters") or {}
        if gr or gr_ch:
            # 泛读 = 统计周期内（总目标=所有时间）实录词数总和 vs 目标
            # 词数总和（新口径=选章词数和 reading_chapters；旧口径=整书）。
            # 会话级口径，与周目标/日卡片同一公式（有效性过滤+轨迹剔跳章
            # +必读章重叠），跨会话重叠段去重不重复计。不封顶（可超100%）。
            done_w = sum(e["words"] for e in self._reading_day_detail(
                self._library_sessions_all(user)))
            total_w = self._goal_reading_words(gr)
            total_w += self._chapters_words_sum(gr_ch)
            if total_w:
                dim("📖", "泛读", done_w, total_w,
                    extra=f"所有时间累计 {done_w} 词")

        gp = g.get("vocab_papers") or []
        if gp:
            done_papers = 0
            passed_levels = set(done["vocab_levels"] or [])
            for pid in gp:
                lv = self._paper_level(pid)
                if lv is not None and lv in passed_levels:
                    done_papers += 1
            dim("📝", "单词试卷", done_papers, len(gp),
                extra="按对应级别考试通过计")

        total_items = sum(d["total"] for d in dims)
        # 泛读按实际计数（可超 100%）；其余维度封顶
        done_items = sum(
            d["done"] if d["name"] == "泛读" else min(d["done"], d["total"])
            for d in dims)
        pct = round(done_items / total_items * 100) if total_items else None
        return {"user": user, "dims": dims, "percent": pct,
                "done_items": done_items, "total_items": total_items,
                "deadline": g.get("deadline")}

    def _completion_status(self, user: str) -> dict:
        """各类资产的「已完成」判定（供周编辑器过滤目标池）。

        讲次=有考试成绩；FCE Part=有提交覆盖；精读文章=有录音提交；
        词汇级别=考试≥80 通过；书=进度≥98%（读完）。查询容错缺表。
        """
        done: dict[str, list] = {"lectures": [], "fce_parts": [],
                                 "articles": [], "vocab_levels": [],
                                 "books": [], "hw_papers": []}
        # 讲次/作业卷走统一判定内核（成绩 ∪ 专题，按学生过滤）
        done["lectures"] = sorted(self.lecture_done(None, None, user, "lecture"))
        done["hw_papers"] = sorted(self.lecture_done(None, None, user, "hw"))
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                tabs = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if "fce_submission" in tabs:
                    for r in conn.execute(
                        "SELECT DISTINCT test_id, paper, part FROM fce_submission"
                        " WHERE user = ?", (user,)):
                        done["fce_parts"].append(
                            f"Test {r[0]} · {_paper_short(r[1])} P{r[2]}")
                if "reading_recordings" in tabs:
                    for r in conn.execute(
                        "SELECT DISTINCT article_id FROM reading_recordings"
                        " WHERE user = ? AND article_id IS NOT NULL", (user,)):
                        done["articles"].append(r[0])
                if "vocab_exams" in tabs:
                    for r in conn.execute(
                        "SELECT DISTINCT level FROM vocab_exams"
                        " WHERE user = ? AND score >= 80", (user,)):
                        done["vocab_levels"].append(r[0])
        except sqlite3.Error:
            pass
        conn2 = self._lib_connect()
        if conn2 is not None:
            try:
                with contextlib.closing(conn2):
                    for r in conn2.execute(
                        "SELECT book_id FROM reading_progress"
                        " WHERE user = ? AND percent >= 98", (user,)):
                        done["books"].append(r[0])
            except sqlite3.Error:
                pass
        return done

    def _library_sessions_all(self, user: str, book_id: Optional[int] = None) -> list:
        """该生全部泛读会话（library 模块，不限日期；总目标/章任务词数口径）。"""
        try:
            with self._connect() as conn:
                if book_id is None:
                    return conn.execute(
                        "SELECT module, book_title, book_id, active_sec,"
                        " percent_start, percent_end, lookups, plays,"
                        " scroll_count, chapter_navs, percent_track"
                        " FROM focus_sessions"
                        " WHERE user = ? AND module = 'library'"
                        " AND book_id IS NOT NULL", (user,)).fetchall()
                return conn.execute(
                    "SELECT module, book_title, book_id, active_sec,"
                    " percent_start, percent_end, lookups, plays,"
                    " scroll_count, chapter_navs, percent_track"
                    " FROM focus_sessions"
                    " WHERE user = ? AND module = 'library'"
                    " AND book_id = ?", (user, int(book_id))).fetchall()
        except sqlite3.Error:
            return []

    def _goal_reading_words(self, book_ids: list) -> int:
        """总目标书目的目标词数总和：必读章配置口径（未配置=全书词数）。"""
        bids = [int(b) for b in book_ids if str(b).isdigit()]
        lib = self._lib_connect()
        if lib is None or not bids:
            return 0
        try:
            with contextlib.closing(lib):
                meta = self._book_meta(lib, bids)
        except sqlite3.Error:
            return 0
        return sum(w for info in meta.values() for i, w in info["chs"]
                   if info["required"] is None or i in info["required"])

    def _chapters_words_sum(self, reading_chapters: dict) -> int:
        """reading_chapters={bookId:[章idx]} 的词数总和（新章选口径）。"""
        if not reading_chapters:
            return 0
        lib = self._lib_connect()
        if lib is None:
            return 0
        total = 0
        try:
            with contextlib.closing(lib):
                for bid, idxs in reading_chapters.items():
                    if not str(bid).isdigit() or not isinstance(idxs, list) or not idxs:
                        continue
                    ph = ",".join("?" * len(idxs))
                    row = lib.execute(
                        "SELECT COALESCE(SUM(word_count),0) FROM chapters"
                        f" WHERE book_id = ? AND idx IN ({ph})",
                        [int(bid)] + [int(x) for x in idxs if str(x).lstrip('-').isdigit()]
                    ).fetchone()
                    total += int(row[0] or 0)
        except (sqlite3.Error, TypeError, ValueError):
            return total
        return total

"""周计划编辑器的选项源：讲次、FCE、书目、文章、词汇与目标资产。"""
from __future__ import annotations

import contextlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Optional

from ..paths import data_path
from .util import _paper_short


class GoalsEditorMixin:
    """editor_data / goal_assets。完成态与总目标读写在 GoalsMixin。"""

    def editor_data(self, user: str = "malin",
                    week_start: Optional[str] = None) -> dict:
        """周计划编辑器的结构化选项源（全部来自真实备课内容）：
        lectures（48 讲）、fce_parts（Test×Paper×Part）、books（含必读章
        配置与词数、该生当前进度）、vocab（当前掌握词数）。
        """
        # 讲次（grammar.db）
        lectures = []
        gp = self.grammar_db_path or str(
            data_path("grammar.db"))
        if Path(gp).exists():
            try:
                with sqlite3.connect(f"file:{gp}?mode=ro", uri=True) as conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        "SELECT number, title, category FROM lecture ORDER BY number"
                    ).fetchall()
                lectures = [dict(r) for r in rows]
            except sqlite3.Error:
                pass
        # FCE Part（fce.db 同库）
        fce_parts = []
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT t.id AS test_id, t.title AS test_title,"
                    " s.paper, s.part, COUNT(q.id) AS questions"
                    " FROM fce_section s"
                    " JOIN fce_test t ON t.id = s.test_id"
                    " LEFT JOIN fce_question q ON q.section_id = s.id"
                    " GROUP BY s.id ORDER BY t.id, s.paper, s.part"
                ).fetchall()
                for r in rows:
                    fce_parts.append({
                        "test_id": r["test_id"], "test_title": r["test_title"],
                        "paper": r["paper"], "part": r["part"],
                        "questions": r["questions"] or 0,
                        "label": f"{r['test_title']} · {_paper_short(r['paper'])} P{r['part']}",
                    })
        except sqlite3.Error:
            pass
        # 泛读馆书目（library.db：词数 + 必读章配置 + 该生进度）
        # 每章明细供编辑器选章：有效章=有词数的内容章（word_count≥200
        # 或必读配置内），其他章（封面/目录）不进选项不计词——全书词数
        # =有效章词数之和（用户定案的模型）。
        books = []
        conn = self._lib_connect()
        if conn is not None:
            try:
                with contextlib.closing(conn):
                    brows = conn.execute(
                        "SELECT id, title, chapter_count FROM books ORDER BY id"
                    ).fetchall()
                    for b in brows:
                        chs = conn.execute(
                            "SELECT idx, title, word_count FROM chapters"
                            " WHERE book_id = ? ORDER BY idx", (b["id"],),
                        ).fetchall()
                        total_words = sum(c["word_count"] or 0 for c in chs)
                        cfg_row = conn.execute(
                            "SELECT chapters FROM book_reading_config WHERE book_id = ?",
                            (b["id"],),
                        ).fetchone()
                        cfg = None
                        if cfg_row:
                            try:
                                v = json.loads(cfg_row["chapters"] or "[]")
                                cfg = sorted({int(x) for x in v}) if v else None
                            except (ValueError, TypeError):
                                cfg = None
                        # 章号统一口径：标题字面数字（书印刷章号），无数字
                        # 回落结构号（首个 ≥200 词内容章起算）
                        title_num = {}
                        for c in chs:
                            m = re.match(r"\s*(\d+)", str(c["title"] or ""))
                            if m:
                                title_num[c["idx"]] = int(m.group(1))
                        first_content = 0
                        for c in chs:
                            if (c["word_count"] or 0) >= 200:
                                first_content = c["idx"]
                                break
                        # 有效章：必读配置优先；未配置=词数 ≥200 的内容章
                        valid = set(cfg) if cfg else {
                            c["idx"] for c in chs if (c["word_count"] or 0) >= 200}
                        chapter_opts = [
                            {"idx": c["idx"],
                             "no": title_num.get(c["idx"])
                             or max(1, c["idx"] - first_content + 1),
                             "title": (c["title"] or "")[:24],
                             "words": c["word_count"] or 0}
                            for c in chs if c["idx"] in valid]
                        goal_words = sum(o["words"] for o in chapter_opts)
                        prog = conn.execute(
                            "SELECT percent FROM reading_progress"
                            " WHERE book_id = ? AND user = ?", (b["id"], user),
                        ).fetchone()
                        books.append({
                            "id": b["id"], "title": b["title"],
                            "chapters": b["chapter_count"] or len(chs),
                            "valid_chapters": len(chapter_opts),
                            "total_words": total_words,
                            "configured_chapters": cfg,
                            "chapter_opts": chapter_opts,
                            "goal_words": goal_words,
                            "percent": round(prog["percent"] or 0.0, 1) if prog else 0.0,
                        })
            except sqlite3.Error:
                pass
        # 精读文章（只给派生文：学生端阅读练习只见派生文，base 原文是
        # 教师备课素材、对学生不可读——混进计划池会被选上，孩子无处可看
        # 也永远完不成）
        articles = []
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                arows = conn.execute(
                    "SELECT id, kind, base_key, title, words FROM reading_article"
                    " WHERE kind = 'derived' ORDER BY id"
                ).fetchall()
                articles = [{"id": r["id"], "kind": r["kind"],
                             "title": (r["title"] or "")[:40],
                             "words": r["words"] or 0} for r in arows]
        except sqlite3.Error:
            pass
        # 词汇现况
        mastered = 0
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) n FROM recite_word_progress"
                    " WHERE user = ? AND mastered_at IS NOT NULL", (user,),
                ).fetchone()
                mastered = row["n"]
        except sqlite3.Error:
            pass
        # 视频学习池（上架视频；计划向导「视频」类选项源）
        videos = []
        try:
            with self._connect() as conn:
                vrows = conn.execute(
                    "SELECT vid, title, category, duration_sec FROM video_item"
                    " WHERE enabled = 1 ORDER BY ord, vid"
                ).fetchall()
                videos = [
                    {"vid": r["vid"], "title": (r["title"] or "")[:60],
                     "category": r["category"] or "",
                     "duration_sec": r["duration_sec"] or 0}
                    for r in vrows
                ]
        except sqlite3.Error:
            pass
        # ---- 目标管理选项源（六类，全部关联备课资产） ----
        goal_assets = self.goal_assets(lectures, fce_parts, books, articles,
                                       user=user)
        # 周编辑器需要：该生总目标 + 各类资产完成状态（已完成不再出现在
        # 「从总目标选」池；额外补充池不受限，可复习旧内容）
        goals = self.get_goals(user)
        done = self._completion_status(user)
        return {"lectures": lectures, "fce_parts": fce_parts, "books": books,
                "articles": articles, "vocab_mastered": mastered,
                "videos": videos,
                "goal_assets": goal_assets, "goals": goals, "done": done,
                "vocab_week": self.vocab_week_stats(user, week_start)}

    def vocab_week_stats(self, user: str,
                         week_start: Optional[str] = None) -> dict:
        """编辑器提示用：每级周初已会、本周新掌握、最近在背的级别。

        与周进度同一窗口（该级 mastered_at）。老师填的「本周新掌握 N 个」
        保存成 vocab_goal = base + N，进度条分母就是 N。
        """
        from datetime import date as _date

        from ..clock import monday_of, today_cn
        from .util import add_days

        if week_start:
            week_start = monday_of(_date.fromisoformat(week_start)).isoformat()
        else:
            week_start = monday_of(today_cn()).isoformat()
        hi = add_days(week_start, 7)
        levels = []
        for lv in range(6):
            words = self._vocab_level_words(lv)
            if not words:
                levels.append({"level": lv, "words": 0, "base": 0, "gained": 0})
                continue
            levels.append({
                "level": lv,
                "words": len(words),
                "base": self._vocab_mastered_before(week_start, user, words),
                "gained": self._vocab_actuals(week_start, hi, user, words),
            })
        return {
            "week_start": week_start,
            "levels": levels,
            "base_all": self._vocab_mastered_before(week_start, user, None),
            "gained_all": self._vocab_actuals(week_start, hi, user, None),
            "practicing_level": self._practicing_vocab_level(user),
        }

    def _practicing_vocab_level(self, user: str) -> Optional[int]:
        """最近一次背词会话的 L{n}。没有就空，编辑器不猜。"""
        try:
            with self._connect() as conn:
                names = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if "recite_sessions" not in names:
                    return None
                rows = conn.execute(
                    "SELECT scope FROM recite_sessions WHERE user = ?"
                    " ORDER BY id DESC LIMIT 12",
                    (user,),
                ).fetchall()
        except sqlite3.Error:
            return None
        for r in rows:
            m = re.match(r"^L(\d)$", str(r["scope"] or "").strip())
            if not m:
                continue
            lv = int(m.group(1))
            if 0 <= lv <= 5:
                return lv
        return None

    def goal_assets(self, lectures: list, fce_parts: list, books: list,
                    articles: list, user: str = "malin") -> dict:
        """目标配置的选项源：
        4.1 哈一课程（带考卷存在标记）4.2 单词级别+词数 4.3 FCE Part
        4.4 精读文章 4.5 泛读书-章节（必读章配置+增量词数）4.6 单词考试试卷资产。
        """
        # 4.1 讲次 → 考卷（homework_question 里有题即有卷）；作业卷完成态
        # （kind=hw 成绩）供编辑器过滤
        hw: dict[int, int] = {}
        gp = self.grammar_db_path or str(
            data_path("grammar.db"))
        if Path(gp).exists():
            try:
                with sqlite3.connect(f"file:{gp}?mode=ro", uri=True) as conn:
                    for r in conn.execute(
                        "SELECT lecture_number, COUNT(*) FROM homework_question"
                        " GROUP BY lecture_number"):
                        hw[r[0]] = r[1]
            except sqlite3.Error:
                pass
        hw_done = set(self._completion_status(user).get("hw_papers") or [])
        lecture_opts = [{
            "number": l["number"], "title": l["title"],
            "category": l.get("category"),
            "has_paper": hw.get(l["number"], 0) > 0,
            "questions": hw.get(l["number"], 0),
            "hw_done": l["number"] in hw_done,
        } for l in lectures]

        # 4.2 单词级别 + 词数 + 该生每级已掌握数（词表在 grammar.db）；
        # 词数是级别内增量口径（L1 的目标与 L0 无关）
        vocab_levels = []
        vocab_counts: dict[int, int] = {}
        if Path(gp).exists():
            try:
                with sqlite3.connect(f"file:{gp}?mode=ro", uri=True) as conn:
                    for r in conn.execute(
                        "SELECT level, COUNT(*) FROM vocab_word WHERE level <= 5"
                        " GROUP BY level"):
                        vocab_counts[r[0]] = r[1]
            except sqlite3.Error:
                pass
        lv_mastered: dict[int, int] = {}
        try:
            with self._connect() as conn:
                for r in conn.execute(
                        "SELECT level, COUNT(*) n FROM recite_word_progress"
                        " WHERE user = ? AND mastered_at IS NOT NULL"
                        " AND level IS NOT NULL GROUP BY level", (user,)):
                    lv_mastered[r["level"]] = r["n"]
        except sqlite3.Error:
            pass
        for lv in range(6):
            vocab_levels.append({"level": lv,
                                 "words": vocab_counts.get(lv, 0),
                                 "mastered": lv_mastered.get(lv, 0)})

        # 4.6 单词试卷资产（vocab_paper_history，fce.db）
        vocab_papers = []
        try:
            with self._connect() as conn:
                tabs = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if "vocab_paper_history" in tabs:
                    for r in conn.execute(
                        "SELECT paper_id, level, created_at FROM vocab_paper_history"
                        " ORDER BY id DESC LIMIT 50"):
                        vocab_papers.append({
                            "paper_id": r["paper_id"], "level": r["level"],
                            "created_at": r["created_at"]})
        except sqlite3.Error:
            pass

        return {
            "lectures": lecture_opts,
            "vocab_levels": vocab_levels,
            "fce_parts": fce_parts,
            "articles": [{"id": a["id"], "title": a["title"],
                          "words": a["words"], "kind": a["kind"]}
                         for a in articles],
            "books": books,   # 含 chapters/total_words/goal_words（必读章口径）
            "vocab_papers": vocab_papers,
        }

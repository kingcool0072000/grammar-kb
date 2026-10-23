"""总目标、编辑器数据源、目标进度与完成状态。"""
from __future__ import annotations

import contextlib
import json
import re
import sqlite3
from datetime import datetime, timezone as _tz
from pathlib import Path
from typing import Optional

from ..paths import data_path
from .util import _paper_short
class GoalsMixin:

    # ---- 总目标 + 编辑器数据源（与备课内容完整挂钩） ----

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


    def editor_data(self, user: str = "malin") -> dict:
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
        # ---- 目标管理选项源（六类，全部关联备课资产） ----
        goal_assets = self.goal_assets(lectures, fce_parts, books, articles,
                                       user=user)
        # 周编辑器需要：该生总目标 + 各类资产完成状态（已完成不再出现在
        # 「从总目标选」池；额外补充池不受限，可复习旧内容）
        goals = self.get_goals(user)
        done = self._completion_status(user)
        return {"lectures": lectures, "fce_parts": fce_parts, "books": books,
                "articles": articles, "vocab_mastered": mastered,
                "goal_assets": goal_assets, "goals": goals, "done": done}


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

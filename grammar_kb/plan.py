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

import contextlib
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

    def week_daily_tasks(self, week_start: str, for_date: Optional[str] = None,
                         user: str = "malin") -> list[dict]:
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
        for book_key, pct in (t.get("reading") or {}).items():
            tasks.append(self._reading_task(book_key, pct, user, days_left))
        return tasks

    def _lib_connect(self) -> Optional[sqlite3.Connection]:
        """library.db 只读连接（无库返回 None）。"""
        path = self.library_db_path or str(
            Path(__file__).resolve().parent.parent / "data" / "library.db"
        )
        if not Path(path).exists():
            return None
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error:
            return None

    @staticmethod
    def _key_words(book_key: str) -> list[str]:
        """书名关键词（小写、去符号、去数字后缀），供模糊匹配。"""
        import re as _re
        return [w for w in _re.findall(r"[a-z]{3,}", (book_key or "").lower())]

    def _match_book(self, book_key: str, user: str) -> Optional[sqlite3.Row]:
        """计划里的书名 → 实际书行。先在该学生读过的书里找，再全库找。"""
        conn = self._lib_connect()
        if conn is None:
            return None
        words = self._key_words(book_key)
        if not words:
            return None
        try:
            with contextlib.closing(conn):
                # 学生读过的书优先（UNION ALL + 去重保序：读过的排前面）
                rows = conn.execute(
                    "SELECT b.id, b.title,"
                    " (b.id IN (SELECT book_id FROM reading_progress WHERE user = ?)) AS read_first"
                    " FROM books b ORDER BY read_first DESC",
                    (user,),
                ).fetchall()
                for row in rows:
                    title = (row["title"] or "").lower()
                    if all(w in title for w in words):
                        return row
        except sqlite3.Error:
            return None
        return None

    def _book_reading_state(self, book_id: int, goal_pct: float, user: str) -> Optional[dict]:
        """某书的精确阅读状态：当前章/目标章/词数。缺数据返回 None。

        目标口径：泛读馆配置了必读章（book_reading_config）时按配置算——
        目标词数=必读章词数和、目标章=必读章最后一章、达成=读到最后一必读章末；
        未配置则按全书百分比（goal_pct）线性映射。
        """
        conn = self._lib_connect()
        if conn is None:
            return None
        try:
            with contextlib.closing(conn):
                prog = conn.execute(
                    "SELECT percent, reading_seconds FROM reading_progress"
                    " WHERE book_id = ? AND user = ?", (book_id, user),
                ).fetchone()
                chs = conn.execute(
                    "SELECT idx, title, word_count FROM chapters"
                    " WHERE book_id = ? ORDER BY idx", (book_id,),
                ).fetchall()
                cfg_row = None
                has_cfg_table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table'"
                    " AND name='book_reading_config'").fetchone()
                if has_cfg_table:
                    cfg_row = conn.execute(
                        "SELECT chapters FROM book_reading_config WHERE book_id = ?",
                        (book_id,),
                    ).fetchone()
        except sqlite3.Error:
            return None
        if not chs:
            return None
        ch_list = [dict(c) for c in chs]
        total = sum(c["word_count"] or 0 for c in ch_list)
        if total <= 0:
            return None
        pct = (prog["percent"] or 0.0) if prog else 0.0

        # 必读章配置 → 目标口径
        cfg: Optional[list[int]] = None
        if cfg_row:
            try:
                v = json.loads(cfg_row["chapters"] or "[]")
                cfg = sorted({int(x) for x in v}) if v else None
            except (ValueError, TypeError):
                cfg = None
        by_idx = {c["idx"]: c for c in ch_list}
        if cfg and all(i in by_idx for i in cfg):
            goal_words = sum(by_idx[i]["word_count"] or 0 for i in cfg)
            goal_ch = max(cfg)
            # 必读章末尾的全书累计百分比（达成线）
            seen = 0
            goal_pct_eff = 100.0
            for c in ch_list:
                seen += c["word_count"] or 0
                if c["idx"] == goal_ch:
                    goal_pct_eff = round(seen / total * 100, 1)
                    break
            cfg_count = len(cfg)
        else:
            goal_pct_eff = float(goal_pct)
            goal_words = round(total * goal_pct_eff / 100)
            goal = _chapter_at_words(ch_list, goal_pct_eff, total)
            goal_ch = goal["idx"] if goal else None
            cfg_count = None

        cur = _chapter_at_words(ch_list, pct, total)
        words_read = round(total * pct / 100)
        return {
            "percent": round(pct, 1),
            "goal_percent": round(goal_pct_eff, 1),
            "configured_chapters": cfg_count,
            "current_chapter": cur["idx"] if cur else None,
            "goal_chapter": goal_ch,
            "words_read": words_read,
            "goal_words": goal_words,
            "remaining_words": max(0, goal_words - words_read),
            "total_words": total,
            "reading_seconds": (prog["reading_seconds"] or 0) if prog else 0,
        }

    def _reading_task(self, book_key: str, goal_pct: float, user: str,
                      days_left: int) -> dict:
        """泛读任务行：精确到章 + 词数（匹配不到书则回退百分比口径）。"""
        short = book_key[:14]
        row = self._match_book(book_key, user)
        if row:
            st = self._book_reading_state(row["id"], goal_pct, user)
            if st:
                cfg_txt = (f"必读 {st['configured_chapters']} 章"
                           if st.get("configured_chapters") else f"目标 {goal_pct}%")
                reached = st["percent"] >= (st.get("goal_percent") or float(goal_pct)) - 0.05
                if reached:
                    return {
                        "type": "reading", "book": book_key,
                        "key": f"reading:{book_key}",
                        "text": f"泛读《{short}》目标已达成 🎉",
                        "detail": f"已读 {st['percent']}% · {cfg_txt}"
                                  f"（{fmt_w(st['goal_words'])} 词）",
                    }
                per_day = max(1, round(st["remaining_words"] / max(1, days_left)))
                return {
                    "type": "reading", "book": book_key,
                    "key": f"reading:{book_key}",
                    "text": f"泛读《{short}》读到第{st['current_chapter']}章"
                            if st["current_chapter"] else f"泛读《{short}》开始读",
                    "detail": (f"目标第{st['goal_chapter']}章 · {cfg_txt}"
                               f" · 已读 {fmt_w(st['words_read'])}/{fmt_w(st['goal_words'])} 词"
                               f" · 今日 +{fmt_w(per_day)} 词"),
                }
        return {
            "type": "reading", "book": book_key, "key": f"reading:{book_key}",
            "text": f"泛读《{short}》目标 {goal_pct}%", "detail": "每天 ≥15 分钟",
        }

    # ---- 内循环：学生今日任务 / 周清单（自动检测 + 手动打卡） ----

    def today_tasks(self, user: str = "malin", day: Optional[str] = None) -> dict:
        """学生某日的任务清单（内循环核心）。

        任务由本周 plan_weeks.tasks 派生（与教师驾驶舱「每日任务展开」
        同一规则）。完成态只认自动检测——学生做了（做题/背词/阅读）即亮，
        不提供手动打卡（plan_day_checks 保留给教师端干预，不进 done）。
        """
        d = day or date.today().isoformat()
        ws = monday_of(date.fromisoformat(d)).isoformat()
        derived = self.week_daily_tasks(ws, for_date=d, user=user)
        auto, read_titles = self._auto_done(user, d)
        out = []
        for t in derived:
            key = _task_key(t)
            t2 = dict(t)
            t2["key"] = key
            if t.get("type") == "reading":
                # 完成判定：任务文案已是达成态（goal_percent 口径在后端算好），
                # 或当天有该书的阅读会话（按书名模糊匹配）
                reached = "目标已达成" in t.get("text", "")
                matched = any(
                    all(w in (title or "").lower() for w in self._key_words(t.get("book") or ""))
                    for title in read_titles
                ) if self._key_words(t.get("book") or "") else False
                t2["auto_done"] = reached or matched
            else:
                t2["auto_done"] = auto.get(key, False)
            t2["done"] = t2["auto_done"]
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

    def week_view(self, week_start: str, user: str = "malin") -> dict:
        """教师周视图：周拆解汇总 + 周一至周日逐日任务（含完成态）。

        周拆解（用户要的「自动拆解」）从 tasks 推导：
        - 阅读篇数（reading 目标书数）
        - 泛读总词数（各书目标% 对应词数 − 已读词数，按剩余摊到天）
        - 题型类数（微练习讲数 + FCE 项数 + 讲次测验，去重类目）
        历史周逐日按当天口径回看（today_tasks(day=…)）。
        """
        w = self._get_week(week_start)
        t = w.get("tasks") or {}
        today_iso = date.today().isoformat()
        in_week = week_start <= today_iso <= add_days(week_start, 6)
        days_left = max(1, (date.fromisoformat(add_days(week_start, 6))
                            - date.fromisoformat(today_iso)).days + 1) if in_week else 7

        # ---- 周拆解 ----
        reading_items = []
        for book_key, pct in (t.get("reading") or {}).items():
            row = self._match_book(book_key, user)
            st = self._book_reading_state(row["id"], pct, user) if row else None
            reading_items.append({
                "book": book_key, "goal_pct": pct,
                "percent": st["percent"] if st else None,
                "goal_percent": st["goal_percent"] if st else None,
                "goal_words": st["goal_words"] if st else None,
                "configured_chapters": st["configured_chapters"] if st else None,
                "remaining_words": st["remaining_words"] if st else None,
                "goal_chapter": st["goal_chapter"] if st else None,
                "current_chapter": st["current_chapter"] if st else None,
            })
        rem_total = sum(x["remaining_words"] or 0 for x in reading_items)
        drill_types = set()
        if t.get("focus_kps"):
            drill_types.add("微练习")
        if t.get("lectures"):
            drill_types.add("讲次测验")
        if t.get("fce"):
            drill_types.add("FCE 真题")
        breakdown = {
            "reading_books": len(reading_items),
            "reading_items": reading_items,
            "reading_words_total": rem_total,
            "reading_words_per_day": round(rem_total / days_left) if rem_total else 0,
            "drill_types": sorted(drill_types),
            "drill_count": len(t.get("focus_kps") or []) * 5 * days_left
                           if t.get("focus_kps") else 0,
            "lecture_count": len(t.get("lectures") or []),
            "fce_count": len(t.get("fce") or []),
        }

        # ---- 逐日矩阵 ----
        days = []
        for i in range(7):
            d = (date.fromisoformat(week_start) + timedelta(days=i)).isoformat()
            if d > today_iso:
                days.append({"date": d, "future": True, "done": 0, "total": 0,
                             "tasks": []})
                continue
            td = self.today_tasks(user=user, day=d)
            days.append({
                "date": d, "future": False,
                "done": td["done"], "total": td["total"],
                "tasks": [{"key": x["key"], "text": x["text"],
                           "detail": x.get("detail"), "type": x["type"],
                           "done": x["done"]} for x in td["tasks"]],
            })
        return {
            "week_start": week_start, "tasks": t, "notes": w.get("notes") or "",
            "breakdown": breakdown, "days": days, "current_week": in_week,
        }

    def month_view(self, month: str, user: str = "malin") -> dict:
        """月历：某月每天 {date, in_month, future, done, total}，周一为每周之首。"""
        y, m = month.split("-")[:2]
        first = date(int(y), int(m), 1)
        nxt_month = (date(int(y) + 1, 1, 1) if int(m) == 12
                     else date(int(y), int(m) + 1, 1))
        today_iso = date.today().isoformat()
        grid_start = monday_of(first)
        days = []
        d = grid_start
        while d < nxt_month:
            iso_ = d.isoformat()
            cell = {"date": iso_, "in_month": d.month == first.month,
                    "future": iso_ > today_iso}
            if iso_ > today_iso:
                td = self.week_daily_tasks(
                    monday_of(d).isoformat(), for_date=iso_, user=user)
                cell.update({"done": 0, "total": len(td)})
            else:
                td = self.today_tasks(user=user, day=iso_)
                cell.update({"done": td["done"], "total": td["total"]})
            days.append(cell)
            d += timedelta(days=1)
        return {"month": first.isoformat()[:7], "days": days}

    def overview(self, user: str = "malin") -> dict:
        """全览：最早已排计划周（无则回看 2 周）到冲刺线 2027-01-31 的逐周汇总。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT week_start FROM plan_weeks ORDER BY week_start"
            ).fetchall()
        seeded = [r["week_start"] for r in rows if r["week_start"] <= "2027-01-25"]
        today_iso = date.today().isoformat()
        cur = monday_of(date.today()).isoformat()
        earliest = (seeded[0] if seeded
                    else (date.fromisoformat(cur) - timedelta(days=14)).isoformat())
        weeks = []
        ws = earliest
        while ws <= "2027-01-25":
            w = self._get_week(ws)
            t = w.get("tasks") or {}
            has_plan = bool(t.get("focus_kps") or t.get("lectures")
                            or t.get("vocab_goal") or t.get("fce") or t.get("reading"))
            done = total = 0
            if ws <= cur:  # 已开始的周才聚合完成度（未来周无 actuals）
                for i in range(7):
                    d = (date.fromisoformat(ws) + timedelta(days=i)).isoformat()
                    if d > today_iso:
                        continue
                    td = self.today_tasks(user=user, day=d)
                    done += td["done"]
                    total += td["total"]
            weeks.append({
                "week_start": ws, "has_plan": has_plan,
                "is_current": ws == cur, "past": ws < cur,
                "focus_kps": t.get("focus_kps") or [],
                "lectures": t.get("lectures") or [],
                "vocab_goal": t.get("vocab_goal"),
                "reading": t.get("reading") or {},
                "notes": w.get("notes") or "",
                "done": done, "total": total,
            })
            ws = (date.fromisoformat(ws) + timedelta(days=7)).isoformat()
        return {"weeks": weeks, "current_week": cur}

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

    def _auto_done(self, user: str, day: str) -> tuple[dict[str, bool], list[str]]:
        """当天活动信号 → (task_key 完成表, 当日阅读书名列表)。

        各表缺表/缺库均容错返回空。阅读书名列表供 today_tasks 按书模糊
        匹配（focus_sessions.book_title）。
        """
        out: dict[str, bool] = {}
        read_titles: list[str] = []
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
                if rows:
                    out["lecture"] = True
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
                        "SELECT DISTINCT book_title FROM focus_sessions"
                        " WHERE user = ? AND (created_at >= ? AND created_at < ?)"
                        " AND book_title IS NOT NULL",
                        (user, day, nxt),
                    ).fetchall()
                    read_titles = [r["book_title"] for r in rows]
                    for r in rows:
                        out[f"reading:{(r['book_title'] or '')[:14]}"] = True
                        out.setdefault("reading", True)
        except sqlite3.Error:
            pass
        return out, read_titles


def _paper_short(paper: str) -> str:
    if paper and paper.startswith("Reading"):
        return "RUE"
    return (paper or "")[:4]


def add_days(iso_str: str, n: int) -> str:
    return (date.fromisoformat(iso_str) + timedelta(days=n)).isoformat()


def _task_key(t: dict) -> str:
    """派生任务 → 稳定键（打卡表 task_key 口径）。"""
    if t.get("key"):
        return str(t["key"])
    ty = t.get("type")
    if ty == "micro_drill":
        return f"micro:{t['lecture']}"
    return "lecture" if ty == "lecture" else str(ty)


def fmt_w(n: int) -> str:
    """词数千分位简写：1234 → 1.2k。"""
    v = int(n or 0)
    return f"{v/1000:.1f}k" if v >= 1000 else str(v)


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

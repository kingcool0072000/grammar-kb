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
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone as _tz
from pathlib import Path
from typing import Optional

from .fce_query import _default_db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_weeks (
    user       TEXT NOT NULL DEFAULT 'malin',  -- 周计划归属学生
    week_start TEXT NOT NULL,      -- 周一日期 YYYY-MM-DD
    tasks      TEXT NOT NULL DEFAULT '{}',  -- 结构化任务 JSON
    notes      TEXT DEFAULT '',    -- 改善项/备注（自由文本）
    updated_at TEXT,
    PRIMARY KEY (user, week_start)
);
CREATE TABLE IF NOT EXISTS plan_day_checks (
    user       TEXT NOT NULL,
    date       TEXT NOT NULL,        -- YYYY-MM-DD
    task_key   TEXT NOT NULL,        -- micro:26 / wrong_words / lecture:29 / fce / reading:书名
    checked_at TEXT,
    PRIMARY KEY (user, date, task_key)
);
CREATE TABLE IF NOT EXISTS plan_goals (
    user       TEXT PRIMARY KEY,
    goals      TEXT NOT NULL DEFAULT '{}',  -- 总目标 JSON（见 _goals_default）
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS plan_history_days (
    user       TEXT NOT NULL,
    date       TEXT NOT NULL,
    tasks      TEXT NOT NULL DEFAULT '[]',
    created_at TEXT,
    PRIMARY KEY (user, date)
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
                self._migrate_plan_weeks(conn)

    @staticmethod
    def _migrate_plan_weeks(conn):
        """旧 plan_weeks（week_start 单主键）→ 新表（user+week_start 复合主键）。

        存量周计划全部归 malin（历史上只有 malin 在用）。幂等：新表跳过。
        SQLite 改主键必须重建表。
        """
        cols = [r[1] for r in conn.execute("PRAGMA table_info(plan_weeks)")]
        if not cols or "user" in cols:
            return
        conn.executescript("""
            ALTER TABLE plan_weeks RENAME TO plan_weeks_old;
            CREATE TABLE plan_weeks (
                user       TEXT NOT NULL DEFAULT 'malin',
                week_start TEXT NOT NULL,
                tasks      TEXT NOT NULL DEFAULT '{}',
                notes      TEXT DEFAULT '',
                updated_at TEXT,
                PRIMARY KEY (user, week_start)
            );
            INSERT INTO plan_weeks (user, week_start, tasks, notes, updated_at)
                SELECT 'malin', week_start, tasks, notes, updated_at
                FROM plan_weeks_old;
            DROP TABLE plan_weeks_old;
        """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        return conn

    # ---- plan_weeks 读写 ----

    def list_weeks(self, user: str = "malin") -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM plan_weeks WHERE user = ? ORDER BY week_start",
                (user,),
            ).fetchall()
        return [_week_out(r) for r in rows]

    def put_week(self, week_start: str, tasks: dict, notes: str,
                 user: str = "malin") -> dict:
        date.fromisoformat(week_start)  # 非法日期抛 ValueError → 422
        if week_start != monday_of(date.fromisoformat(week_start)).isoformat():
            raise ValueError("week_start 须为周一日期")
        now = datetime.now(_tz.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO plan_weeks (user, week_start, tasks, notes, updated_at)"
                " VALUES (?,?,?,?,?)"
                " ON CONFLICT(user, week_start) DO UPDATE SET"
                " tasks=excluded.tasks, notes=excluded.notes, updated_at=excluded.updated_at",
                (user, week_start, json.dumps(tasks or {}, ensure_ascii=False),
                 (notes or "")[:4000], now),
            )
            row = conn.execute(
                "SELECT * FROM plan_weeks WHERE user = ? AND week_start = ?",
                (user, week_start),
            ).fetchone()
        return _week_out(row)

    def put_history_day(self, user: str, day: str, tasks: list[dict]) -> None:
        """历史回填的逐日计划快照（任务即真实完成，完成度 100%）。"""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO plan_history_days"
                " (user, date, tasks, created_at) VALUES (?,?,?,?)",
                (user, day, json.dumps(tasks or [], ensure_ascii=False),
                 datetime.now(_tz.utc).isoformat(timespec="seconds")),
            )

    def history_day(self, user: str, day: str) -> Optional[list[dict]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT tasks FROM plan_history_days WHERE user=? AND date=?",
                (user, day)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["tasks"] or "[]")
        except (ValueError, TypeError):
            return []

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

    def _vocab_mastered_before(self, day: str, user: str) -> int:
        """某日 00:00 前已掌握的词数（周词汇差额口径的基线）。"""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) n FROM recite_word_progress"
                    " WHERE user = ? AND mastered_at IS NOT NULL AND mastered_at < ?",
                    (user, day)).fetchone()
                return row["n"] if row else 0
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
            "notes": self.get_week_notes(prev, user=user),
        }

    def get_week_notes(self, week_start: str, user: str = "malin") -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT notes FROM plan_weeks WHERE user = ? AND week_start = ?",
                (user, week_start),
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
                w = self._get_week(ws, user=user)
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

    def _get_week(self, week_start: str, user: str = "malin") -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM plan_weeks WHERE user = ? AND week_start = ?",
                (user, week_start),
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
        w = self._get_week(week_start, user=user)
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
        if t.get("speak"):
            tasks.append({
                "type": "speak", "text": "朗读 1 篇（阅读训练）",
                "detail": f"每周 {t['speak']} 篇，录音自动进批改中心",
            })
        for pid in (t.get("vocab_papers") or []):
            plv = self._paper_level(pid)
            tasks.append({
                "type": "paper", "key": f"paper:{pid}",
                "text": f"📝 单词试卷练习（{pid}）",
                "detail": (f"L{plv} 级试卷" if plv is not None else "试卷资产")
                          + " · 线下完成后到词汇考试登记",
            })
        for aid in (t.get("articles") or []):
            title = self._article_title(aid)
            if title:
                tasks.append({
                    "type": "article", "article_id": aid,
                    "key": f"article:{aid}",
                    "text": f"精读《{title[:16]}》",
                    "detail": "阅读训练里读全文 + 提交朗读录音",
                })
        for book_key, pct in (t.get("reading") or {}).items():
            tasks.append(self._reading_task(book_key, pct, user, days_left))
        return tasks

    def _paper_level(self, paper_id: str) -> Optional[int]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT level FROM vocab_paper_history WHERE paper_id = ?",
                    (str(paper_id)[:64],)).fetchone()
                return row["level"] if row else None
        except sqlite3.Error:
            return None

    def _article_title(self, article_id: int) -> Optional[str]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT title FROM reading_article WHERE id = ?", (int(article_id),),
                ).fetchone()
                return row["title"] if row else None
        except sqlite3.Error:
            return None

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
        if (book_key or "").strip().isdigit():
            try:
                with contextlib.closing(conn):
                    row = conn.execute(
                        "SELECT id, title FROM books WHERE id = ?",
                        (int(book_key),),
                    ).fetchone()
                return row
            except sqlite3.Error:
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

    def _reading_task(self, book_key: str, goal_val, user: str,
                      days_left: int) -> dict:
        """泛读任务行。goal_val 两种格式：
        - list（新）：选章 idx 列表 → 目标=读完这些章（词数=章和）
        - number（旧）：全书目标 % → 按必读配置/全书口径
        匹配不到书则回退百分比文案。"""
        row = self._match_book(book_key, user)
        short = (row["title"][:14] if row else str(book_key)[:14])
        if isinstance(goal_val, list) and row:
            # 新章选格式：目标词数=选章词数和；已读=该生全书有效阅读
            # 区段 ∩ 选章的词数（水位区段跨会话去重，且只数选章内）
            goal_words = self._chapters_words_sum({str(row["id"]): goal_val})
            done_w = self._read_words_in_chapters(user, row["id"], goal_val)
            nos = self._chapter_nos(row["id"], goal_val)
            rng = (f"第{nos[0]}–{nos[-1]}章" if len(nos) > 1
                   else f"第{nos[0]}章") if nos else f"{len(goal_val)} 章"
            if done_w >= goal_words and goal_words > 0:
                return {
                    "type": "reading", "book": book_key,
                    "key": f"reading:{book_key}",
                    "text": f"泛读《{short}》{rng}已读完 🎉",
                    "detail": f"累计 {fmt_w(done_w)}/{fmt_w(goal_words)} 词",
                }
            per_day = max(1, round(max(0, goal_words - done_w) / max(1, days_left)))
            return {
                "type": "reading", "book": book_key,
                "key": f"reading:{book_key}",
                "text": f"泛读《{short}》{rng}",
                "detail": (f"已读 {fmt_w(done_w)}/{fmt_w(goal_words)} 词"
                           f" · 今日 +{fmt_w(per_day)} 词"),
            }
        goal_pct = float(goal_val or 100)
        if row:
            st = self._book_reading_state(row["id"], goal_pct, user)
            if st:
                cfg_txt = (f"必读 {st['configured_chapters']} 章"
                           if st.get("configured_chapters") else f"目标 {goal_pct}%")
                reached = st["percent"] >= (st.get("goal_percent") or goal_pct) - 0.05
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

    def _read_words_in_chapters(self, user: str, book_id: int, idxs: list) -> int:
        """该生在某书选章范围内的已读词数（跨会话区段去重 ∩ 选章）。"""
        lib = self._lib_connect()
        if lib is None or not idxs:
            return 0
        try:
            with contextlib.closing(lib):
                meta = self._book_meta(lib, {book_id})
        except sqlite3.Error:
            return 0
        info = meta.get(book_id)
        if not info:
            return 0
        total = sum(w for _, w in info["chs"])
        if total <= 0:
            return 0
        segs: list = []
        for r in self._library_sessions_all(user, book_id):
            if self._reading_session_valid(r):
                segs.extend(self._row_segs(r, total))
        ch_set = set(idxs)
        return self._segs_words(segs, info["chs"], ch_set)[0]

    def _chapter_nos(self, book_id: int, idxs: list) -> list[int]:
        """章 idx 列表 → 排序后的展示章号（标题字面数字，兜底结构号）。"""
        lib = self._lib_connect()
        if lib is None or not idxs:
            return []
        try:
            with contextlib.closing(lib):
                ph = ",".join("?" * len(idxs))
                rows = lib.execute(
                    "SELECT idx, title, word_count FROM chapters"
                    f" WHERE book_id = ? AND idx IN ({ph}) ORDER BY idx",
                    [book_id] + [int(x) for x in idxs]).fetchall()
        except (sqlite3.Error, ValueError, TypeError):
            return []
        if not rows:
            return []
        first_content = 0
        for r in rows:
            if (r["word_count"] or 0) >= 200:
                first_content = r["idx"]
                break
        out = []
        for r in rows:
            m = re.match(r"\s*(\d+)", str(r["title"] or ""))
            out.append(int(m.group(1)) if m
                       else max(1, r["idx"] - first_content + 1))
        return sorted(out)

    # ---- 内循环：学生今日任务 / 周清单（自动检测 + 手动打卡） ----

    def today_tasks(self, user: str = "malin", day: Optional[str] = None) -> dict:
        """学生某日的任务清单（内循环核心）。

        任务由本周 plan_weeks.tasks 派生（与教师驾驶舱「每日任务展开」
        同一规则）。完成态只认自动检测——学生做了（做题/背词/阅读）即亮，
        不提供手动打卡（plan_day_checks 保留给教师端干预，不进 done）。
        """
        d = day or date.today().isoformat()
        ws = monday_of(date.fromisoformat(d)).isoformat()
        historical = self.history_day(user, d)
        derived = historical if historical is not None else self.week_daily_tasks(ws, for_date=d, user=user)
        planned_articles = {t.get("article_id") for t in (derived or [])
                            if t.get("article_id") is not None}
        if historical is not None:
            # 回填快照里的任务即完成，不再依赖当前活动表；
            # 量化实录（泛读/背单词/计划外朗读）仍从活动表按日算
            out = []
            for t in derived:
                t2 = dict(t); t2["key"] = _task_key(t2)
                t2["auto_done"] = True; t2["done"] = True
                out.append(t2)
            _, _, acts_out = self._signals_and_acts(user, d, planned_articles)
            return {"date": d, "week_start": ws, "tasks": out,
                    "done": len(out), "total": len(out), "acts": acts_out}
        auto, read_titles, acts_out = self._signals_and_acts(user, d, planned_articles)
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
            # 朗读任务完成时带上实际读的文章名（通用文案「朗读 1 篇」看不出读了什么）
            if t.get("type") == "speak" and t2["done"]:
                names = []
                for aid in acts_out.get("all_speak_articles", []):
                    ti = self._article_title(aid)
                    if ti:
                        names.append(ti[:16])
                if names:
                    t2["detail"] = "读了：" + "、".join(dict.fromkeys(names))
            out.append(t2)
        done_n = sum(1 for x in out if x["done"])
        # ---- 当日实录（教师周历单日卡补充显示，构建逻辑见 _signals_and_acts）----
        return {"date": d, "week_start": ws, "tasks": out,
                "done": done_n, "total": len(out), "acts": acts_out}

    def _signals_and_acts(self, user: str, day: str,
                          planned_articles: set) -> tuple[dict, list, dict]:
        """当天活动信号 + 量化实录（历史日快照路径同样调用）。

        计划内任务已按信号打勾；实录补两类信息：
        1) 计划外的朗读提交（计划没排该篇，但学生做了——原逻辑直接不可见）
        2) 量化实录：泛读分钟+词数 / 背单词个数+分钟（无论是否在计划内）
        """
        auto, read_titles = self._auto_done(user, day)
        acts = getattr(self, "_day_acts", None) or {}
        extra_speaks = []
        for aid in acts.get("speak_articles", []):
            if aid not in planned_articles:
                title = self._article_title(aid)
                extra_speaks.append({
                    "type": "speak", "done": True,
                    "text": f"朗读《{title[:16]}》" if title else f"朗读提交（文章{aid}）",
                    "detail": "计划外提交，录音已进批改中心",
                })
        acts_out = {
            "extra_speaks": extra_speaks,
            "all_speak_articles": acts.get("speak_articles", []),
            "readings": acts.get("readings", []),
            "vocab_n": acts.get("vocab_n", 0),
            "vocab_min": acts.get("vocab_min", 0),
        }
        return auto, read_titles, acts_out

    def week_todo(self, user: str = "malin") -> dict:
        """学生周清单：本周目标 + 周一至周日逐日任务完成矩阵。"""
        today_iso = date.today().isoformat()
        ws = monday_of(date.today()).isoformat()
        w = self._get_week(ws, user=user)
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
        w = self._get_week(week_start, user=user)
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
        current_monday = monday_of(date.today()).isoformat()
        is_historical = week_start < current_monday
        for i in range(7):
            d = (date.fromisoformat(week_start) + timedelta(days=i)).isoformat()
            if d > today_iso:
                days.append({"date": d, "future": True, "done": 0, "total": 0,
                             "tasks": []})
                continue
            if is_historical:
                # 历史日优先显示逐日快照（真实活动=计划完成100%）；
                # 量化实录（泛读/背单词/计划外朗读）从活动表按日补上，
                # 无快照日也会带出实录（有活动就有量）。
                hist = self.history_day(user, d) or []
                planned_arts = {x.get("article_id") for x in hist
                                if x.get("article_id") is not None}
                _, _, acts = self._signals_and_acts(user, d, planned_arts)
                days.append({
                    "date": d, "future": False,
                    "done": len(hist), "total": len(hist),
                    "tasks": [{"key": x.get("key") or _task_key(x),
                               "text": x.get("text", ""),
                               "detail": x.get("detail"),
                               "type": x.get("type", "history"),
                               "done": True} for x in hist],
                    "acts": acts,
                })
                continue
            td = self.today_tasks(user=user, day=d)
            days.append({
                "date": d, "future": False,
                "done": td["done"], "total": td["total"],
                "tasks": [{"key": x["key"], "text": x["text"],
                           "detail": x.get("detail"), "type": x["type"],
                           "done": x["done"]} for x in td["tasks"]],
                "acts": td.get("acts") or {},
            })
        return {
            "week_start": week_start, "tasks": t, "notes": w.get("notes") or "",
            "breakdown": breakdown, "days": days, "current_week": in_week,
            "goal_progress": self._week_goal_progress(t, days, user, week_start),
        }

    def _week_goal_progress(self, t: dict, days: list, user: str,
                            week_start: str) -> dict:
        """周目标的各维度目标+进度（与总目标 goal_progress 的 dims 同构，
        供前端同一张「目标+进度」卡切换展示）。

        维度口径（按周任务配置）：
        - 讲次：计划讲次数 vs 本周考过的讲次数
        - 词汇：vocab_goal（级别百分比折算的累计词数）vs 周内新掌握词数
        - FCE：计划 Part 数 vs 周内提交覆盖数
        - 泛读：按各书周目标%（词数口径）vs 周内已读词数（实录汇总）
        - 精读：计划文章数 vs 已提交录音数
        - 试卷：计划卷数 vs 登记考试数
        """
        dims = []
        wk_done = sum(d.get("done") or 0 for d in days)
        wk_total = sum(d.get("total") or 0 for d in days)

        def dim(icon, name, ok, total, extra=""):
            if total:
                dims.append({"icon": icon, "name": name,
                             "done": round(ok, 1), "total": total, "extra": extra})

        lecs = t.get("lectures") or []
        if lecs:
            try:
                with sqlite3.connect(
                        f"file:{self._exam_db()}?mode=ro", uri=True) as conn:
                    ph = ",".join("?" * len(lecs))
                    n = conn.execute(
                        f"SELECT COUNT(DISTINCT lecture) n FROM exam_records"
                        f" WHERE lecture IN ({ph})"
                        f" AND date >= ? AND date <= ?",
                        [*lecs, week_start, add_days(week_start, 6)]).fetchone()
                    done_lec = n[0] if n else 0
            except sqlite3.Error:
                done_lec = 0
            dim("📚", "语法课程", done_lec, len(lecs))

        # 词汇差额口径：分母 = 目标词数 − 周初已掌握词数（学生从本周起点
        # 还需新学的量）；分子 = 周内新掌握。基线取「周一 00:00 前已掌握」。
        vg = t.get("vocab_goal")
        if vg:
            gained = self._vocab_actuals(week_start, add_days(week_start, 7), user)
            base = self._vocab_mastered_before(week_start, user)
            denom = max(1, vg - base)
            dim("🔤", "词汇", min(gained, denom), denom)

        fce = t.get("fce") or []
        if fce:
            try:
                with self._connect() as conn:
                    rows = conn.execute(
                        "SELECT test_id, paper, part FROM fce_submission"
                        " WHERE user = ? AND created_at >= ? AND created_at < ?",
                        (user, week_start, add_days(week_start, 7))).fetchall()
                    got = {f"Test {r['test_id']} · {_paper_short(r['paper'])} P{r['part']}"
                           for r in rows}
                done_fce = sum(1 for x in fce if x in got)
            except sqlite3.Error:
                done_fce = 0
            dim("🎧", "FCE真题", done_fce, len(fce))

        reading_map = t.get("reading") or {}
        if reading_map:
            # 泛读周目标=「目标章节完成百分比」（用户口径）：
            # 目标=选章序列（按书内顺序）；当前读位（云端进度词位）在
            # 序列内的均摊位置 / 序列总长。例：目标第5章，读到第5章5%
            # → 5%；目标第5-8章，读到第5章50% → (0.5章)/4章=12.5%。
            # 旧 pct 格式换算为等效章序列（全书前 N% 覆盖的章）。
            lib = self._lib_connect()
            done_pct = 0.0
            goal_pct_sum = 0.0   # 各书完成度 × 该书章数权重（多书均摊）
            goal_ch_count = 0
            try:
                if lib is not None:
                    with contextlib.closing(lib):
                        for k, v in reading_map.items():
                            if not str(k).isdigit():
                                continue
                            bid = int(k)
                            chs = lib.execute(
                                "SELECT idx, word_count FROM chapters"
                                " WHERE book_id = ? ORDER BY idx", (bid,)
                            ).fetchall()
                            total = sum(c["word_count"] or 0 for c in chs)
                            if not chs or total <= 0:
                                continue
                            if isinstance(v, list) and v:
                                sel = [c for c in chs if c["idx"] in
                                       {int(x) for x in v}]
                            else:
                                # 旧 pct：全书前 N% 覆盖的章序列
                                cut = (float(v) if not isinstance(v, list)
                                       else 100.0) / 100 * total
                                cum = 0
                                sel = []
                                for c in chs:
                                    cum += c["word_count"] or 0
                                    if cum <= cut:
                                        sel.append(c)
                                    elif (cum - (c["word_count"] or 0)) < cut:
                                        sel.append(c)  # 边界章算入
                            if not sel:
                                continue
                            sel_w = sum(c["word_count"] or 0 for c in sel)
                            prog = lib.execute(
                                "SELECT percent FROM reading_progress"
                                " WHERE book_id = ? AND user = ?",
                                (bid, user)).fetchone()
                            pos = ((prog[0] if prog else 0) or 0) / 100 * total
                            # 当前读位在选章序列内的均摊章进度：
                            # 读完的整章各计 1，当前章按章内比例计
                            prog_ch = 0.0
                            cum = 0
                            for c in chs:
                                c1 = cum + (c["word_count"] or 0)
                                if c in sel:
                                    if pos >= c1:
                                        prog_ch += 1.0
                                    elif pos > cum:
                                        prog_ch += (pos - cum) / max(1, c["word_count"] or 1)
                                cum = c1
                            done_pct += prog_ch
                            goal_pct_sum += len(sel)
                            goal_ch_count += len(sel)
            except (sqlite3.Error, TypeError, ValueError):
                pass
            if goal_ch_count:
                done100 = done_pct / goal_ch_count * 100
                dim("📖", "泛读", round(min(done100, 999), 1), 100)

        arts = t.get("articles") or []
        # 周内全部朗读提交（含计划外）：精读朗读 = 提交过录音才算完成。
        # 完成行的 article_id 可能缺字段（快照/占位行只有 key="article:N"），
        # 从 key 解析兜底。
        week_recorded = []
        for d in days:
            for x in (d.get("tasks") or []):
                if x.get("type") == "article" and x.get("done"):
                    aid = x.get("article_id")
                    if aid is None:
                        k = str(x.get("key") or "")
                        if k.startswith("article:") and k[8:].isdigit():
                            aid = int(k[8:])
                    if aid is not None:
                        week_recorded.append(aid)
            for x in (d.get("acts") or {}).get("extra_speaks", []):
                week_recorded.append(-1)  # 计划外朗读（无任务行）
        if arts:
            done_a = sum(1 for a in arts if a in week_recorded)
            extra_n = sum(1 for x in week_recorded if x == -1)
            dim("🎤", "精读朗读", done_a, len(arts),
                extra=f"计划外另朗 {extra_n} 篇" if extra_n else "")
        elif week_recorded:
            dim("🎤", "精读朗读", len(week_recorded), len(week_recorded),
                extra="均为计划外朗读")

        papers = t.get("vocab_papers") or []
        if papers:
            done_p = sum(
                1 for d in days
                for x in (d.get("tasks") or [])
                if x.get("type") == "paper" and x.get("done"))
            dim("📝", "试卷", done_p, len(papers))

        total_items = sum(d["total"] for d in dims)
        # 泛读维度按实际计数（可超 100%）；其余维度封顶（完成即满）
        done_items = sum(
            d["done"] if d["name"] == "泛读" else min(d["done"], d["total"])
            for d in dims)
        pct = round(done_items / total_items * 100) if total_items else None
        return {"dims": dims, "percent": pct,
                "done_items": round(done_items), "total_items": total_items,
                "week_start": week_start}

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
            Path(__file__).resolve().parent.parent / "data" / "grammar.db")
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
        # 精读文章（阅读训练：base 原文段落 + derived 派生文；课文级绑定）
        articles = []
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                arows = conn.execute(
                    "SELECT id, kind, base_key, title, words FROM reading_article"
                    " ORDER BY kind, id"
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
                                 "books": []}
        exam_path = self._exam_db()
        if exam_path and Path(exam_path).exists():
            try:
                with sqlite3.connect(f"file:{exam_path}?mode=ro", uri=True) as conn:
                    done["lectures"] = [r[0] for r in conn.execute(
                        "SELECT DISTINCT lecture FROM exam_records")]
            except sqlite3.Error:
                pass
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
        # 4.1 讲次 → 考卷（homework_question 里有题即有卷）
        hw: dict[int, int] = {}
        gp = self.grammar_db_path or str(
            Path(__file__).resolve().parent.parent / "data" / "grammar.db")
        if Path(gp).exists():
            try:
                with sqlite3.connect(f"file:{gp}?mode=ro", uri=True) as conn:
                    for r in conn.execute(
                        "SELECT lecture_number, COUNT(*) FROM homework_question"
                        " GROUP BY lecture_number"):
                        hw[r[0]] = r[1]
            except sqlite3.Error:
                pass
        lecture_opts = [{
            "number": l["number"], "title": l["title"],
            "category": l.get("category"),
            "has_paper": hw.get(l["number"], 0) > 0,
            "questions": hw.get(l["number"], 0),
        } for l in lectures]

        # 4.2 单词级别 + 词数（词表在 grammar.db）；cum_words=到该级止累计词数
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
        cum = 0
        for lv in range(6):
            cum += vocab_counts.get(lv, 0)
            vocab_levels.append({"level": lv,
                                 "words": vocab_counts.get(lv, 0),
                                 "cum_words": cum})

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
                "SELECT week_start FROM plan_weeks WHERE user = ?"
                " ORDER BY week_start", (user,),
            ).fetchall()
        seeded = [r["week_start"] for r in rows if r["week_start"] <= "2027-01-25"]
        today_iso = date.today().isoformat()
        cur = monday_of(date.today()).isoformat()
        earliest = (seeded[0] if seeded
                    else (date.fromisoformat(cur) - timedelta(days=14)).isoformat())
        weeks = []
        ws = earliest
        while ws <= "2027-01-25":
            w = self._get_week(ws, user=user)
            t = w.get("tasks") or {}
            has_plan = bool(t.get("focus_kps") or t.get("lectures")
                            or t.get("vocab_goal") or t.get("fce") or t.get("reading"))
            done = total = 0
            if ws <= cur:  # 已开始的周才聚合完成度（未来周无 actuals）
                historical_week = ws < cur
                for i in range(7):
                    d = (date.fromisoformat(ws) + timedelta(days=i)).isoformat()
                    if d > today_iso:
                        continue
                    if historical_week:
                        hist = self.history_day(user, d) or []
                        done += len(hist)
                        total += len(hist)
                    else:
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

    @staticmethod
    def _reading_session_valid(r) -> bool:
        """泛读会话有效性：剔除非真实阅读的信号（快速翻章/误触/闪进闪出）。

        判定（任一满足即有效）：
        - 停留 ≥60 秒（真读了）
        - 有交互：查词/播放/滚动/章导且停留 ≥10 秒
        （章切换后停留 <5 秒的翻章浏览——时长极短且无任何交互——无效。）
        """
        dur = r["active_sec"] or 0
        if dur >= 60:
            return True
        inter = ((r["lookups"] or 0) + (r["plays"] or 0)
                 + (r["scroll_count"] or 0) + (r["chapter_navs"] or 0))
        return dur >= 10 and inter > 0

    def _reading_day_detail(self, rows: list) -> list[dict]:
        """泛读会话 → 逐书精确实录（章区间 + 词数 + 分钟）。

        词数口径（按用户公式）：每本书取当日 span——
          结束位置所在章的结束位置 − 起始位置所在章的起始位置，
          逐章累计词数（边界章按重叠比例），剔除非必读章
          （book_reading_config.chapters 为空=全书口径）。
        percent 为 0-100 全书口径（epubjs locations/像素百分比），
          词位置 = percent/100 × 全书总词数。
        返回 [{book, from, to, min, words}]（from/to 为计入的章 idx；
          缺章节数据的书 from/to=None、words=0，只报分钟）。
        """
        conn = self._lib_connect()
        lib: dict[int, dict] = {}
        if conn is not None:
            try:
                with contextlib.closing(conn):
                    ids = {r["book_id"] for r in rows if r["book_id"] is not None}
                    lib = self._book_meta(conn, ids)
            except sqlite3.Error:
                pass

        by_book: dict[int, list] = {}
        for r in rows:
            if r["book_id"] is not None and self._reading_session_valid(r):
                by_book.setdefault(r["book_id"], []).append(r)
        # 无效会话也计入分钟口径的「在场时长」提示？不——无效即无效，
        # 分钟与词数都只认有效会话（否则出现 0 分钟 N 词的矛盾展示）。

        out = []
        for bid, srows in by_book.items():
            entry = {
                "book": (lib.get(bid, {}).get("title")
                         or (srows[0]["book_title"] or "") or f"#{bid}"),
                "from": None, "to": None,
                "min": round(sum(r["active_sec"] or 0 for r in srows) / 60),
                "words": 0,
            }
            info = lib.get(bid)
            if not info:
                out.append(entry)
                continue
            total = sum(w for _, w in info["chs"])
            if total <= 0:
                out.append(entry)
                continue

            # ---- 段级词数（v3 位置轨迹优先；无轨迹的存量会话退回 span 口径+限速封顶）----
            # 有效段集合：[(s_pos, e_pos)] 词位置区间（已剔跳章与短停留段）
            segs: list[tuple[float, float]] = []
            for r in srows:
                segs.extend(self._row_segs(r, total))
            # 去重合并（段可能跨会话重叠），再逐章计词
            words, counted = self._segs_words(segs, info["chs"], info["required"])
            entry["words"] = words
            if counted:
                entry["from"], entry["to"] = min(counted), max(counted)
            out.append(entry)
        return out

    @staticmethod
    def _book_meta(lib, ids) -> dict[int, dict]:
        """书目元数据批量取：章词数 + 必读章配置 + 章标题 + 书名。
        缺章节数据的书不出现（调用方按缺元数据兜底）。"""
        meta: dict[int, dict] = {}
        for bid in ids:
            chs = lib.execute(
                "SELECT idx, word_count, title FROM chapters"
                " WHERE book_id = ? ORDER BY idx", (bid,)).fetchall()
            if not chs:
                continue
            cfg = lib.execute(
                "SELECT chapters FROM book_reading_config WHERE book_id = ?",
                (bid,)).fetchone()
            try:
                req = set(json.loads(cfg[0])) if cfg and cfg[0] else None
            except (ValueError, TypeError):
                req = None
            meta[bid] = {"chs": [(c[0], c[1] or 0) for c in chs],
                         "required": req,
                         "titles": {c[0]: (c[2] or "") for c in chs}}
        # 书名（书已删时缺行，调用方用会话里的 book_title 兜底）
        for bid in list(meta):
            r = lib.execute("SELECT title FROM books WHERE id = ?", (bid,)).fetchone()
            if r:
                meta[bid]["title"] = r[0]
        return meta

    def _row_segs(self, r, total: int) -> list[tuple[float, float]]:
        """单会话 → 有效词位置区段：位置轨迹优先（剔跳章+限速封顶），
        无轨迹的存量会话退回 span 口径 + 会话级封顶（400 词/分 × active 分钟）。"""
        track = self._session_track(r)
        if track:
            return self._valid_track_segments(track, total, r["active_sec"] or 0)
        s = r["percent_start"] or 0
        e = r["percent_end"] or 0
        if e <= s:
            return []
        cap = max(0.0, (r["active_sec"] or 0)) / 60 * self.READ_WPM_CAP
        span_words = (e - s) / 100 * total
        ratio = min(1.0, cap / span_words) if span_words > 0 else 1.0
        return [(s / 100 * total, s / 100 * total + span_words * ratio)]

    @staticmethod
    def _segs_words(segs: list, chs: list, required) -> tuple[int, list[int]]:
        """词位置区段 →（排序去重合并后）与必读章的重叠词数 + 计入章 idx。"""
        merged: list[list[float]] = []
        for s_pos, e_pos in sorted(segs):
            if e_pos <= s_pos:
                continue
            if merged and s_pos <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e_pos)
            else:
                merged.append([s_pos, e_pos])
        words = 0
        counted: list[int] = []
        for s_pos, e_pos in merged:
            cum = 0
            for idx, wc in chs:
                c0, c1 = cum, cum + wc
                cum = c1
                if required is not None and idx not in required:
                    continue  # 非必读章剔除
                ov = min(e_pos, c1) - max(s_pos, c0)
                if ov > 0:
                    words += int(round(ov))
                    counted.append(idx)
        return words, counted

    def focus_words(self, ids: list) -> dict[int, dict]:
        """学习日志逐会话泛读词数 + 连续性/异常标记（教师端行内展示）。

        词数口径（防重复记录）：每本书维护「已读水位」=该生此前**带位置
        轨迹**的有效会话之有效区段最远终点（_valid_track_segments 口径，
        已剔跳章；无轨迹存量会话不推进水位——其限速折算终点只是记分
        口径不是真实读到的证据，会把大片区域误标「已读过」）。本次会话
        有效区段中，水位以下计入 overlap（重合，展示不计入学习词数），
        水位以上才是本次新学词数 words。回读整段在水位下 → words=0
        只显示重合；跳读空档不在会话区段内自然不计。
        - link：与同书上一会话（按开始时间）的衔接关系——
          next=顺延（|开头−上会话结尾|≤2%）、first=该书首个会话、
          back=回读（开头落后）、skip=跳读（开头超前，中间有断档）。
          开头取「恢复瞬态净化」后的稳定点（开局 3s 内 >4%/步的跳变
          是阅读器自愈落位，不是阅读）。
        - flags 异常标记：异常快=整段位移超 400 词/分物理读速（≥100 词才判，
          避免短会话小位移误报；含恢复瞬态的会话不判）；进度反向=结束位置
          低于开头 >1%；无有效阅读=闪进闪出/纯翻章会话（词数为 0）。
        位置口径（概念严格三分）：全书 % 仅是词位换算的内部中间量（s_pct/
        e_pct，前端仅兜底显示）；对外统计一律「当前阅读章节的章内 %」——
        chapters=[{ch 书内章号, s_in/e_in 章内起止%, words/overlap 该章
        新学/重合词数}]，新章开启=第N章 0%、续读=上次退出的章内位置。
        章号纯结构判定（首个 ≥200 词内容章起算第 1 章）；必读配置只定义
        计词范围（非必读章不列行不计词），不参与编号。
        返回 {会话id: {words, overlap, s_pct, e_pct, chapters[], link,
        link_note, flags[]}}。
        """
        out: dict[int, dict] = {}
        if not ids:
            return out
        rows: list = []
        hist: dict[tuple, list] = {}
        try:
            with self._connect() as conn:
                ph = ",".join("?" * len(ids))
                rows = conn.execute(
                    "SELECT id, user, started_at, book_id, active_sec,"
                    " percent_start, percent_end, lookups, plays,"
                    " scroll_count, chapter_navs, percent_track, created_at"
                    f" FROM focus_sessions WHERE id IN ({ph})"
                    " AND module = 'library'",
                    list(ids)).fetchall()
                # 每本书全历史时间线（水位/衔接基线要跨会话累计，批内 5-6 条
                # 不够）；只取批内涉及的书，通常 1-2 本。
                for r in rows:
                    if r["book_id"] is None:
                        continue
                    key = (r["user"], r["book_id"])
                    if key not in hist:
                        hist[key] = conn.execute(
                            "SELECT id, started_at, percent_start,"
                            " percent_end, active_sec, lookups, plays,"
                            " scroll_count, chapter_navs, percent_track,"
                            " created_at"
                            " FROM focus_sessions"
                            " WHERE user = ? AND module = 'library'"
                            " AND book_id = ?"
                            " ORDER BY started_at, id", key).fetchall()
        except sqlite3.Error:
            return out
        rows = [r for r in rows if r["book_id"] is not None]
        meta: dict[int, dict] = {}
        lib = self._lib_connect()
        if lib is not None:
            try:
                with contextlib.closing(lib):
                    meta = self._book_meta(lib, {r["book_id"] for r in rows})
            except sqlite3.Error:
                meta = {}
        # 时间线预演：逐会话两个基线（进入该会话前的状态）——
        #   prev_end=上一会话结束位置（衔接展示）；watermark=有效区段最远
        #   终点（词位置）。水位只由**带位置轨迹且口径未失真**的会话推进：
        #   无轨迹存量会话的限速折算终点只是记分口径、不是真实读到的证据；
        #   10-02~10-03 的「百分比口径失真」会话（前端分母 bug 期，详见
        #   _track_distorted）的 % 是章内值被当全书值，词位整体虚高。
        base: dict[int, dict] = {}
        for (user, bid), hs in hist.items():
            info = meta.get(bid)
            total = sum(w for _, w in info["chs"]) if info else 0
            prev_end = None
            watermark = 0.0
            for h in hs:
                base[h["id"]] = {"prev_end": prev_end, "watermark": watermark}
                if info and total > 0 and self._reading_session_valid(h):
                    tr = self._session_track(h)
                    if tr and not self._track_distorted(tr, h):
                        segs = self._valid_track_segments(
                            tr, total, h["active_sec"] or 0)
                        eff_end = max((e for _, e in segs), default=0.0)
                        watermark = max(watermark, eff_end)
                if h["percent_end"] is not None:
                    prev_end = h["percent_end"]
        for r in rows:
            info = meta.get(r["book_id"])
            total = sum(w for _, w in info["chs"]) if info else 0
            # 章号统一口径：优先取章节标题的字面数字（书自己印刷的
            # 「5 I Play Pinochle…」→ 第5章，与孩子看到的目录一致）；
            # 标题无数字（封面/目录/标题改造过的书）回落结构号——首个
            # 词数 ≥200 的内容章起算第 1 章。必读配置只管计词范围，
            # 绝不参与章号编号（改配置不漂移编号）。
            title_num: dict[int, int] = {}
            if info:
                for idx, t in (info.get("titles") or {}).items():
                    m = re.match(r"\s*(\d+)", str(t or ""))
                    if m:
                        title_num[idx] = int(m.group(1))
            first_content = 0
            if info:
                for idx, wc in info["chs"]:
                    if wc >= 200:
                        first_content = idx
                        break

            def disp_ch(idx: int) -> int:
                return title_num.get(idx) or max(1, idx - first_content + 1)
            valid = self._reading_session_valid(r)
            track = self._session_track(r)
            # 恢复瞬态净化：开局 3 秒内出现的大幅正向跳变（>4%/步）是
            # 阅读器「渲染开头→自愈滚到上次结尾」的落位动作（曾表现为
            # 「每次都从 5% 起读/回读 N%」假异常）。以瞬态后的稳定点为
            # 真实起点；无瞬态则用 percent_start。
            s0 = r["percent_start"] or 0
            e0 = r["percent_end"] or 0
            transient = False
            if track:
                prev = track[0]["p"]
                for pt in track[1:]:
                    if pt["t"] > 3:
                        break
                    if pt["p"] - prev > 4:
                        transient = True
                        s0 = max(s0, pt["p"])
                    prev = pt["p"]
            b = base.get(r["id"]) or {"prev_end": None, "watermark": 0.0}
            if valid and info and total > 0:
                segs = self._row_segs(r, total)
                wm = b["watermark"]
                lo: list = []
                hi: list = []
                for s_pos, e_pos in segs:
                    if e_pos <= wm:
                        lo.append((s_pos, e_pos))
                    elif s_pos >= wm:
                        hi.append((s_pos, e_pos))
                    else:
                        lo.append((s_pos, wm))
                        hi.append((wm, e_pos))
                overlap = self._segs_words(lo, info["chs"], info["required"])[0]
                words = self._segs_words(hi, info["chs"], info["required"])[0]
            else:
                words = overlap = 0
            flags: list[str] = []
            if track and self._track_distorted(track, r):
                flags.append("百分比口径失真（旧版分母bug，词数为封顶估算）")
            if not valid:
                # 纯翻章/闪进闪出：词数 0 已说明问题，不再叠加速度标记
                # （这类会话 active_sec 极小，raw 速度会是荒谬大数）。
                flags.append("无有效阅读")
            elif info and total > 0 and not transient:
                # 有效会话的「异常快」按原始位移速度判（词数口径已按
                # 400 词/分限速折算，被折算过的会话正是要暴露的对象）。
                # 含恢复瞬态的会话不判——瞬态位移是程序落位不是阅读。
                span_w = max(0.0, (e0 - s0) / 100 * total)
                mins = (r["active_sec"] or 0) / 60
                if (mins > 0 and span_w >= 100
                        and span_w / mins > self.READ_WPM_CAP):
                    flags.append(f"异常快 {round(span_w / mins)} 词/分")
            if e0 < s0 - 1:
                flags.append(f"进度反向 {round(s0 - e0)}%")
            link, note = "first", ""
            pe = b["prev_end"]
            if pe is not None:
                gap = s0 - pe
                if gap > 2:
                    link, note = "skip", f"跳读 {round(gap)}%"
                elif gap < -2:
                    link, note = "back", f"回读 {round(-gap)}%"
                else:
                    link, note = "next", ""

            # ---- 分章统计（统计口径=章内百分比）----
            # 全书 % 只是内部词位换算的中间量，对外一律按「当前阅读章节
            # 的章内 %」呈现：新章开启=第N章 0%，续读=上次退出的章内位置。
            # 逐有效章一行：章内起止% + 该章新学/重合词数（非必读章不列
            # 行——计词范围由配置定义，与章号编号相互独立）。前置页
            # （封面/目录）位置钳到第 1 章 0% 起。零位移（打开即退）也
            # 列位置行——教师能看到"停在第N章 X%"，词数自然为 0。
            ch_rows: list[dict] = []
            if info and total > 0 and e0 >= s0:
                first_pos = sum(w for i, w in info["chs"] if i < first_content)
                s_pos = max(s0 / 100 * total, first_pos)
                e_pos = max(e0 / 100 * total, s_pos)
                cum = 0
                for idx, wc in info["chs"]:
                    c1 = cum + wc
                    is_req = info["required"] is None or idx in info["required"]
                    if is_req and wc > 0 and c1 > s_pos and cum < e_pos:
                        a, b = max(cum, s_pos), min(c1, e_pos)
                        ch_rows.append({
                            "ch": disp_ch(idx),
                            "s_in": round((a - cum) / wc * 100),
                            "e_in": round((b - cum) / wc * 100),
                            "words": self._span_words(hi, cum, c1),
                            "overlap": self._span_words(lo, cum, c1),
                        })
                    cum = c1
            out[r["id"]] = {"words": words, "overlap": overlap,
                            "s_pct": round(s0), "e_pct": round(e0),
                            "chapters": ch_rows,
                            "link": link, "link_note": note, "flags": flags}
        return out

    @staticmethod
    def _span_words(segs: list, a: float, b: float) -> int:
        """词位区段集合与区间 [a,b) 的重叠词数（纯几何求和，不过滤章）。"""
        return int(round(sum(max(0.0, min(e, b) - max(s, a))
                             for s, e in segs)))

    # 阅读限速（词/分钟）：会话级封顶用（跳章剔除见 _valid_track_segments）
    READ_WPM_CAP = 400.0
    # 百分比口径失真期：前端精确百分比曾用滚动流高度当全书分母
    # （scrolled 大书懒渲染滚动流≈单章高，章内 % 被记成全书 %）。
    # 窗口收窄到「词位锚插值」实际部署时刻（2026-10-03T08:33Z，
    # add197b）——此后会话（含整数粒度的 944/945）数据已可信，
    # 不再标失真；窗口内带轨迹会话标失真：词数照算（限速封顶仍有
    # 意义）但不推进去重水位、不判衔接/异常。
    DISTORTED_UNTIL = "2026-10-03T08:33"

    @staticmethod
    def _track_distorted(track: list, r) -> bool:
        """会话是否落在百分比口径失真期（created_at 早于词位锚部署）。"""
        if not track:
            return False
        created = (r["created_at"] if "created_at" in r.keys()
                   else r["started_at"]) or ""
        return bool(created) and created < "2026-10-03T08:33"

    @staticmethod
    def _session_track(r) -> list[dict]:
        """会话行的 percent_track JSON → [{t, p}]（缺/坏返回 []）。
        兼容历史毫秒时间戳（会话上限 4h；末点 t>90000 必是 ms，整体换算秒）。"""
        raw = None
        try:
            raw = r["percent_track"]
        except (IndexError, KeyError):
            raw = None
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return []
        out = []
        if isinstance(data, list):
            for x in data:
                try:
                    out.append({"t": float(x["t"]), "p": float(x["p"])})
                except (KeyError, TypeError, ValueError):
                    continue
        if out and out[-1]["t"] > 90000:
            out = [{"t": pt["t"] / 1000, "p": pt["p"]} for pt in out]
        return out

    def _valid_track_segments(self, track: list[dict], total_words: int,
                              active_sec: int = 0) -> list[tuple[float, float]]:
        """位置轨迹 → 有效阅读跨度（词位置区间，每会话一段）。双层判定：

        ①跳章剔除（位置级）：单步位移 >4%，或 <30 秒内 >2% —— 视为章导航/
          快滚位移，不计并触发 30s 冷却；回退不计。epubjs 位置为 1% 整数粒度
          （约 930 词/格），真实阅读的瞬时速度差异无法在粒度内分辨，故不做
          逐点限速（会误杀正常快读）。
        ②会话级封顶：有效跨度词数 ≤ 400 词/分 × active_sec——物理读速上限，
          连续快滚（每步都不触发跳章判定的匀速刷）在这里被整体压回。
        返回 [(起点词位, 终点词位)]；无有效增量返回 []。
        """
        if len(track) < 2:
            return []
        lo_p = hi_p = None
        cool_until = -1.0
        last_change_t = track[0]["t"]
        prev_p = track[0]["p"]
        for pt in track[1:]:
            if pt["t"] < last_change_t:  # 时钟回拨：重置锚点
                last_change_t = pt["t"]
                prev_p = pt["p"]
                continue
            if pt["p"] == prev_p:
                continue
            dpct = pt["p"] - prev_p
            if dpct > 0:
                dt = pt["t"] - last_change_t
                is_jump = dpct > 4 or (dt < 30 and dpct > 2)
                if not is_jump and pt["t"] >= cool_until:
                    lo_p = prev_p if lo_p is None else min(lo_p, prev_p)
                    hi_p = pt["p"] if hi_p is None else max(hi_p, pt["p"])
                elif is_jump:
                    cool_until = pt["t"] + 30
            last_change_t = pt["t"]
            prev_p = pt["p"]
        if lo_p is None or hi_p is None or hi_p <= lo_p:
            return []
        span = (hi_p - lo_p) / 100 * total_words
        cap = max(0, active_sec or 0) / 60 * self.READ_WPM_CAP
        if cap > 0:
            span = min(span, cap)
        s_pos = lo_p / 100 * total_words
        return [(s_pos, s_pos + span)]

    def _auto_done(self, user: str, day: str) -> tuple[dict[str, bool], list[str]]:
        """当天活动信号 → (task_key 完成表, 当日阅读书名列表)。

        各表缺表/缺库均容错返回空。阅读书名列表供 today_tasks 按书模糊
        匹配（focus_sessions.book_title）。
        """
        out: dict[str, bool] = {}
        read_titles: list[str] = []
        self._day_acts = {"speak_articles": [], "readings": [],
                          "vocab_n": 0, "vocab_min": 0}
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
                        "SELECT COUNT(*) n, COALESCE(SUM(total),0) w,"
                        " COALESCE(SUM(duration_sec),0) dur"
                        " FROM recite_sessions"
                        " WHERE user = ? AND (created_at >= ? AND created_at < ?)",
                        (user, day, nxt),
                    ).fetchone()
                    if row and row["n"]:
                        out.setdefault("wrong_words", True)
                        self._day_acts["vocab_n"] = row["w"]
                        self._day_acts["vocab_min"] = round(row["dur"] / 60)
                if "fce_submission" in tabs:
                    row = conn.execute(
                        "SELECT COUNT(*) n FROM fce_submission"
                        " WHERE user = ? AND (created_at >= ? AND created_at < ?)",
                        (user, day, nxt),
                    ).fetchone()
                    if row and row["n"]:
                        out["fce"] = True
                if "reading_recordings" in tabs:
                    rows = conn.execute(
                        "SELECT article_id FROM reading_recordings"
                        " WHERE user = ? AND (created_at >= ? AND created_at < ?)",
                        (user, day, nxt),
                    ).fetchall()
                    if rows:
                        out["speak"] = True
                        for r in rows:
                            if r["article_id"]:
                                out[f"article:{r['article_id']}"] = True
                                self._day_acts["speak_articles"].append(r["article_id"])
                if "focus_sessions" in tabs:
                    rows = conn.execute(
                        "SELECT module, book_title, book_id, active_sec,"
                        " percent_start, percent_end, lookups, plays,"
                        " scroll_count, chapter_navs, percent_track"
                        " FROM focus_sessions"
                        " WHERE user = ? AND (created_at >= ? AND created_at < ?)"
                        " AND book_title IS NOT NULL",
                        (user, day, nxt),
                    ).fetchall()
                    read_titles = [r["book_title"] for r in rows]
                    for r in rows:
                        out[f"reading:{(r['book_title'] or '')[:14]}"] = True
                        out.setdefault("reading", True)
                    # 泛读（library 模块）实录：逐书章区间+词数+分钟（见 _reading_day_detail）
                    lib_rows = [r for r in rows if r["module"] == "library"]
                    if lib_rows:
                        self._day_acts["readings"] = self._reading_day_detail(lib_rows)
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

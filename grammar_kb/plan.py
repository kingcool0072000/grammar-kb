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
        self.library_db_path = library_db_path
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
        """周内有更新过的书及当前进度（增量口径见模块 docstring）。"""
        path = self.library_db_path or str(
            Path(__file__).resolve().parent.parent / "data" / "library.db"
        )
        if not Path(path).exists():
            return []
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT b.title, p.percent, p.reading_seconds, p.updated_at"
                    " FROM reading_progress p JOIN books b ON b.id = p.book_id"
                    " WHERE p.user = ? AND p.updated_at >= ? AND p.updated_at < ?",
                    (user, lo, hi),
                ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

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


def _paper_short(paper: str) -> str:
    if paper and paper.startswith("Reading"):
        return "RUE"
    return (paper or "")[:4]


def _week_out(r: sqlite3.Row) -> dict:
    try:
        tasks = json.loads(r["tasks"] or "{}")
    except (ValueError, TypeError):
        tasks = {}
    return {
        "week_start": r["week_start"], "tasks": tasks,
        "notes": r["notes"] or "", "updated_at": r["updated_at"],
    }

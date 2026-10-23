"""PlanStore：周计划读写。行为方法按主题拆在各 Mixin 中。

泛读词数：FocusMixin（逐日/逐会话）+ FocusTrackMixin（轨迹几何）
+ FocusMetaMixin（有效性与书目）。总目标：GoalsMixin（进度与完成态）
+ GoalsEditorMixin（编辑器选项源）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone as _tz
from pathlib import Path
from typing import Optional

from ..clock import monday_of
from ..fce_query import _default_db_path
from .schema import SCHEMA
from .util import _week_out
from .actuals import ActualsMixin
from .completion import CompletionMixin
from .books import BooksMixin
from .focus import FocusMixin
from .focus_meta import FocusMetaMixin
from .focus_track import FocusTrackMixin
from .goals import GoalsMixin
from .goals_editor import GoalsEditorMixin
from .review import ReviewMixin
from .rules import RulesMixin
from .schedule import ScheduleMixin
from .signals import SignalsMixin
from .views import ViewsMixin


class PlanStore(
    RulesMixin,
    ActualsMixin,
    ReviewMixin,
    ScheduleMixin,
    CompletionMixin,
    BooksMixin,
    ViewsMixin,
    GoalsMixin,
    GoalsEditorMixin,
    FocusMixin,
    FocusTrackMixin,
    FocusMetaMixin,
    SignalsMixin,
):
    """plan_weeks 读写 + 周完成度实时聚合。"""


    def __init__(self, db_path: Optional[str] = None,
                 exam_db_path: Optional[str] = None,
                 grammar_db_path: Optional[str] = None,
                 library_db_path: Optional[str] = None):
        self.db_path = db_path or _default_db_path()
        self.exam_db_path = exam_db_path
        # 主库默认路径与 ingest.default_db_path 同口径（GRAMMAR_KB_DB →
        # cwd/data/grammar.db）——此前 None 时 _vocab_level_words 各自兜底
        # 到「代码仓库 data/」，与 server 实际打开的库可能不是同一个
        if grammar_db_path is None:
            import os as _os
            env = _os.environ.get("GRAMMAR_KB_DB")
            grammar_db_path = env or str(Path.cwd() / "data" / "grammar.db")
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


    def _get_week(self, week_start: str, user: str = "malin") -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM plan_weeks WHERE user = ? AND week_start = ?",
                (user, week_start),
            ).fetchone()
        return _week_out(row) if row else {"tasks": {}, "notes": ""}

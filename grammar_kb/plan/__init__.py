"""教师计划表：周维度学习计划 + 实时完成度（fce.db 同库）。

公开导入与拆分前的 ``grammar_kb.plan`` 模块一致::

    from grammar_kb.plan import PlanStore, week_bounds, cn_dt, monday_of

实现按主题拆在本包内（store / rules / actuals / schedule / completion /
books / goals + goals_editor / focus + focus_track + focus_meta / …）。
「今天」与周一起点走 :mod:`grammar_kb.clock`（固定北京时间），不看进程时区。
某日哪些计划键算完成，只问 ``PlanStore.day_completion``。
"""
from __future__ import annotations

from ..clock import cn_dt, monday_of, today_cn, week_bounds
from .schema import RULES, SCHEMA
from .store import PlanStore
from .util import add_days, fmt_w

__all__ = [
    "PlanStore",
    "RULES",
    "SCHEMA",
    "add_days",
    "cn_dt",
    "fmt_w",
    "monday_of",
    "today_cn",
    "week_bounds",
]

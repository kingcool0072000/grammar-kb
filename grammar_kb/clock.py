"""北京时间。计划表「今天」、周一起点、SQL 日切都走这里。

库内时间戳仍是 UTC。按日/周切分用 :func:`cn_dt`（julianday +8 小时），
Python 侧的「今天」用 :func:`today_cn`。两者都固定 UTC+8，不看进程时区：
CI 和服务器经常是 UTC，和 +8 的 SQL 窗口会在跨日时对不齐。

测试可以用 :func:`freeze_clock` 把「现在」钉死。
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Iterator, Optional

CN_TZ = timezone(timedelta(hours=8))

_override: Optional[datetime] = None


def now_cn() -> datetime:
    """当前北京时间（aware）。"""
    if _override is not None:
        return _override
    return datetime.now(CN_TZ)


def today_cn() -> date:
    """北京日历上的今天。"""
    return now_cn().date()


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def current_monday() -> date:
    """本周周一（北京日历）。"""
    return monday_of(today_cn())


def cn_dt(col: str) -> str:
    """UTC 时间戳列 → 北京时区 datetime 表达式（julianday +8h）。

    created_at/updated_at 均按 UTC 落库，按日/周切分比较时必须先转时区
    （与 _MP_DATE 同口径）：否则北京时间 08:00 前的活动会被归到前一天。
    比较边界是本地零点的 naive 串（如 '2026-10-08 00:00:00'），
    datetime() 转换后同为 naive，字典序比较成立。
    """
    return f"datetime(julianday({col}) + 8.0/24)"


def week_bounds(week_start: str) -> tuple[str, str]:
    """[周一 00:00, 下周一 00:00) 的日期串（配合 cn_dt 做窗口比较）。

    返回值带时分秒。和纯日期（exam_records.date、date() 的结果）比较时
    必须先 date() 归一：否则 '2026-10-19' < '2026-10-19 00:00:00'，
    周一当天的记录会被排除在本周窗口之外。
    """
    ws = date.fromisoformat(week_start)
    return (
        f"{ws.isoformat()} 00:00:00",
        f"{(ws + timedelta(days=7)).isoformat()} 00:00:00",
    )


@contextmanager
def freeze_clock(when: datetime) -> Iterator[datetime]:
    """把 now_cn()/today_cn() 钉在某一时刻。无时区的 when 视为北京时间。"""
    global _override
    frozen = when.replace(tzinfo=CN_TZ) if when.tzinfo is None else when.astimezone(CN_TZ)
    prev = _override
    _override = frozen
    try:
        yield frozen
    finally:
        _override = prev

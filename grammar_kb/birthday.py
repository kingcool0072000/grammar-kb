"""学生生日祝福。不改数据库。

日期一律走 :func:`grammar_kb.clock.today_cn`（固定 UTC+8，即 Asia/Shanghai）。
服务器进程常是 UTC，``date.today()`` 在北京时间 0–8 点会落到前一天。

配置（后者覆盖前者）：

1. 代码默认：malin = ``11-10``
2. 可选 JSON 文件 ``data/birthdays.json``（或 ``GRAMMAR_KB_BIRTHDAYS_FILE``）
   形如 ``{"malin": "11-10", "mxy": "03-21"}``。非法项跳过，文件损坏则忽略。
3. 环境变量 ``GRAMMAR_KB_BIRTHDAYS=malin=11-10,mxy=03-21``。
   值写成 ``-`` / ``off`` / ``none`` 或留空，可取消某人的默认生日。

只对学生角色返回祝福。教师账号即使写进配置也不出现。
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

from .clock import today_cn
from .paths import data_path

DEFAULT_BIRTHDAYS = {"malin": "11-10"}

_OFF = {"", "-", "off", "none"}
_PAIR = re.compile(r"^([^=]+)=(.*)$")


def parse_mmdd(raw: str) -> str | None:
    """``MM-DD``。非法返回 None。2 月 29 日允许（按闰年校验）。"""
    text = (raw or "").strip()
    matched = re.fullmatch(r"(\d{2})-(\d{2})", text)
    if not matched:
        return None
    month, day = int(matched.group(1)), int(matched.group(2))
    if not 1 <= month <= 12:
        return None
    year = 2000 if (month, day) == (2, 29) else 2001
    try:
        date(year, month, day)
    except ValueError:
        return None
    return f"{month:02d}-{day:02d}"


def _apply_pair(out: dict[str, str], user: str, raw: str) -> None:
    name = (user or "").strip()
    if not name:
        return
    value = (raw or "").strip()
    if value.lower() in _OFF:
        out.pop(name, None)
        return
    parsed = parse_mmdd(value)
    if parsed:
        out[name] = parsed


def _merge_map(out: dict[str, str], raw: object) -> None:
    if not isinstance(raw, dict):
        return
    for key, val in raw.items():
        if isinstance(key, str) and isinstance(val, str):
            _apply_pair(out, key, val)


def birthdays_file() -> Path:
    override = os.environ.get("GRAMMAR_KB_BIRTHDAYS_FILE", "").strip()
    if override:
        return Path(override)
    return data_path("birthdays.json")


def _merge_file(out: dict[str, str], path: Path) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return
    _merge_map(out, raw)


def _merge_env(out: dict[str, str], raw: str) -> None:
    for part in (raw or "").split(","):
        piece = part.strip()
        if not piece:
            continue
        matched = _PAIR.fullmatch(piece)
        if not matched:
            continue
        _apply_pair(out, matched.group(1), matched.group(2))


def birthdays() -> dict[str, str]:
    """当前生效的 用户名 → MM-DD。每次现读，改环境变量或文件不用重启进程里的缓存。"""
    found = dict(DEFAULT_BIRTHDAYS)
    _merge_file(found, birthdays_file())
    _merge_env(found, os.environ.get("GRAMMAR_KB_BIRTHDAYS", ""))
    return found


def _mmdd(day: date) -> str:
    return f"{day.month:02d}-{day.day:02d}"


def is_birthday(user: str, today: date | None = None) -> bool:
    """``today`` 缺省为北京时间今天。传入的 date 也按日历月日比较，不再做时区换算。"""
    mmdd = birthdays().get((user or "").strip())
    if not mmdd:
        return False
    day = today_cn() if today is None else today
    return _mmdd(day) == mmdd


def greeting_for(user: str, role: str) -> dict:
    """登录学生在生日当天的祝福。非学生、非生日：``birthday`` 为 false，文案为空。"""
    today = today_cn()
    show = role == "student" and is_birthday(user, today)
    name = (user or "").strip()
    return {
        "user": name,
        "role": role or "",
        "date": today.isoformat(),
        "birthday": show,
        "title": f"{name}，生日快乐！🎂" if show else "",
        "message": "新的一岁，今天也好好学。爸爸为你骄傲。" if show else "",
    }

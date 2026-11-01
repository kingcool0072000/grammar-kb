"""教师端学情月报：``GET /analytics/summary?user=malin&month=2026-10``。

聚合逻辑在 :mod:`grammar_kb.analytics`（纯函数，四库只读连接即用即关）；
本模块只管端点、教师闸门和进程内 TTL 缓存（300s，key=(user, month)，
``{key: (ts, data)}`` + threading.Lock，不引新依赖）。
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import fastapi  # noqa: F401  FastAPI 解析注解里的 fastapi.Request
from fastapi import HTTPException

from .. import analytics
from .http import _ok, _require_teacher

_TTL_SECONDS = 300.0
_MAX_ENTRIES = 256  # 键空间 = 学生×月份，量小；上限防教师端批量拉取撑大

_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
_LOCK = threading.Lock()


def cache_clear() -> None:
    """清空进程内缓存（测试、或改完数据想立即看新数时用）。"""
    with _LOCK:
        _CACHE.clear()


def _cache_get(key: tuple[str, str]) -> Optional[dict]:
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None and time.monotonic() - hit[0] < _TTL_SECONDS:
            return hit[1]
        if hit is not None:
            _CACHE.pop(key, None)  # 过期即删，避免长期霸占上限名额
    return None


def _cache_put(key: tuple[str, str], data: dict) -> None:
    with _LOCK:
        if len(_CACHE) >= _MAX_ENTRIES:
            now = time.monotonic()
            for k in [k for k, (ts, _) in _CACHE.items() if now - ts >= _TTL_SECONDS]:
                _CACHE.pop(k, None)
            if len(_CACHE) >= _MAX_ENTRIES:
                _CACHE.pop(next(iter(_CACHE)))  # 仍满则丢最旧插入序的一格
        _CACHE[key] = (time.monotonic(), data)


def register_analytics(app, ctx):
    @app.get("/analytics/summary")
    def analytics_summary(request: "fastapi.Request",
                          user: str = "malin", month: Optional[str] = None):
        """某学生某月的学情聚合（教师）。

        - user：学生名，缺省 malin（与人工月报同口径）；
        - month：YYYY-MM，缺省当前北京月；非法格式 422。
        """
        _require_teacher(request)
        user = str(user)[:32].strip()
        try:
            m = analytics.parse_month(month)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        key = (user, m)
        cached = _cache_get(key)
        if cached is not None:
            return _ok(cached)
        data = analytics.build_summary(user, m)
        _cache_put(key, data)
        return _ok(data)

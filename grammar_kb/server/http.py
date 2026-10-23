"""路由共用的响应包装与教师闸门。

这些函数原先嵌在 ``create_app`` 里，各路由模块按原名调用。
"""
from __future__ import annotations

from typing import Any


def _ok(data: Any = None, message: str = "ok") -> dict:
    """统一成功响应包装。"""
    return {"code": 0, "message": message, "data": data}


def _html_response(body: str):
    """text/html 响应（考卷打印页等）。"""
    from fastapi.responses import HTMLResponse

    return HTMLResponse(body)


def _request_user(request) -> str:
    """从 auth 中间件注入的 state 取用户名。"""
    return getattr(request.state, "user", "")


def _require_teacher(request) -> None:
    if getattr(request.state, "role", "") != "teacher":
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="该功能仅教师账号可用")


def _require_teacher_lib(request) -> None:
    """泛读馆教师端点兜底（中间件是粗闸门，端点是细闸门，两层都要有）。"""
    _require_teacher(request)


def _to_int(v, default=None):
    """宽松 int 转换（body 里可能是 int/str/None）。"""
    if v is None or isinstance(v, bool):
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default

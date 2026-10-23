"""create_app 构造的依赖集合。路由函数只靠闭包看见的名字从这里取出。"""
from __future__ import annotations


class AppCtx:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def fill_focus_book_titles(self, items: list) -> None:
        """泛读会话存量行没存书名（前端曾传空）：按 book_id 批量反查补全。"""
        ids = [x.get("book_id") for x in items
               if x.get("book_id") is not None and not (x.get("book_title") or "").strip()]
        if not ids:
            return
        try:
            titles = self.library.titles_map(ids)
        except Exception:
            return
        for x in items:
            t = titles.get(x.get("book_id"))
            if t and not (x.get("book_title") or "").strip():
                x["book_title"] = t

    def require_book_access(self, request, book_id: int) -> None:
        """书架解锁门同样挡直链：/file、章节、预习、进度。

        未配置 bookUnlock 时全部放行（与 list_books 一致）。
        """
        from fastapi import HTTPException

        from .http import _request_user

        role = getattr(request.state, "role", "")
        if self.library.student_can_access_book(_request_user(request), role, book_id):
            return
        raise HTTPException(status_code=403, detail="这本书还没解锁")

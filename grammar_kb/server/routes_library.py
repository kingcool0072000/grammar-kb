"""泛读馆：书架、epub、章节预习、查词与进度。"""
from __future__ import annotations

import fastapi  # noqa: F401  FastAPI 解析注解里的 fastapi.Request
from fastapi import HTTPException

from ..library import LibraryError
from .http import _ok, _request_user, _require_teacher_lib, _to_int

def register_library(app, ctx):
    library = ctx.library
    _require_book_access = ctx.require_book_access

    # ---- 泛读馆（整本书英文泛读：书架 / epub 导入 / 章节预习 / 查词 / 进度） ----
    # 章节预习改离线导入（scripts/import_prep.py），线上不再在线生成（LLM 退役）。
    from fastapi.responses import FileResponse

    @app.get("/library/prep/{book_id}/{chapter_index}")
    def lib_prep_get(book_id: int, chapter_index: int, request: "fastapi.Request"):
        """读某章预习（师生可读）。未生成返回 data=null（区别于参考实现直接 404）。"""
        _require_book_access(request, book_id)
        payload = library.get_prep(book_id, chapter_index)
        return _ok(payload)

    @app.get("/library/books")
    def lib_books_list(request: "fastapi.Request"):
        """书架 + 当前用户阅读统计（学生受 bookUnlock 配置过滤）。"""
        return _ok(library.list_books(_request_user(request),
                                      role=getattr(request.state, "role", "student")))

    @app.post("/library/books")
    async def lib_books_upload(request: "fastapi.Request", file: "fastapi.UploadFile"):
        """上传 epub（教师）：仅 .epub ≤100MB；内存解析成功后事务落库+文件落盘。

        epub 解析是纯 CPU 密集（解压+逐章清洗），≤100MB 时可达数秒——
        下放到线程池跑，避免阻塞事件循环卡死全部并发请求。
        """
        _require_teacher_lib(request)
        content = await file.read()
        import anyio
        try:
            book = await anyio.to_thread.run_sync(
                lambda: library.add_book(
                    _request_user(request), file.filename or "",
                    file.content_type or "", content,
                )
            )
        except LibraryError as e:
            raise HTTPException(status_code=e.status, detail=str(e))
        return _ok(book)

    @app.get("/library/books/{book_id}/file")
    def lib_book_file(book_id: int, request: "fastapi.Request"):
        _require_book_access(request, book_id)
        got = library.get_file(book_id)
        if got is None:
            raise HTTPException(status_code=404, detail="书籍不存在或文件丢失")
        return FileResponse(got[0], media_type=got[1])

    @app.get("/library/books/{book_id}/cover")
    def lib_book_cover(book_id: int, request: "fastapi.Request"):
        _require_book_access(request, book_id)
        got = library.get_cover(book_id)
        if got is None:
            raise HTTPException(status_code=404, detail="无封面")
        return FileResponse(got[0], media_type=got[1])

    @app.get("/library/books/{book_id}/chapters")
    def lib_book_chapters(book_id: int, request: "fastapi.Request"):
        _require_book_access(request, book_id)
        chapters = library.list_chapters(book_id)
        if chapters is None:
            raise HTTPException(status_code=404, detail="书籍不存在")
        # bookTitle 供阅读器补专注力上报的书名（此前泛读记录标题是 #id）
        return _ok({"chapters": chapters, "bookTitle": library.get_title(book_id) or ""})

    @app.delete("/library/books/{book_id}")
    def lib_book_delete(book_id: int, request: "fastapi.Request"):
        """删除书（教师）：事务删 5 表关联 + unlink epub/封面。"""
        _require_teacher_lib(request)
        deleted = library.delete_book(book_id)
        if deleted is None:
            raise HTTPException(status_code=404, detail="书籍不存在")
        return _ok({"id": deleted})

    @app.post("/library/books/{book_id}/open")
    def lib_book_open(book_id: int, request: "fastapi.Request"):
        """记录"打开书"行为（更新 last_opened_at）。"""
        _require_book_access(request, book_id)
        library.touch_open(book_id)
        return _ok({"ok": True})

    @app.get("/library/books/{book_id}/progress")
    def lib_progress_get(book_id: int, request: "fastapi.Request"):
        """当前用户在某书的阅读进度（未开始返回 data=null）。"""
        _require_book_access(request, book_id)
        return _ok(library.get_progress(book_id, _request_user(request)))

    @app.put("/library/books/{book_id}/progress")
    def lib_progress_put(book_id: int, rec: dict, request: "fastapi.Request"):
        """上报阅读进度：percent 夹 0-100、delta 夹 0-3600，UPSERT。"""
        # 先看门再写：否则学生可以对锁定书写一条 percent>0，书架例外会把它放出来。
        _require_book_access(request, book_id)
        body = rec if isinstance(rec, dict) else {}
        cfi = body.get("cfi") or ""
        chapter_index = _to_int(body.get("chapterIndex"), 0)
        try:
            percent = float(body.get("percent") or 0)
        except (TypeError, ValueError):
            percent = 0.0
        delta = _to_int(body.get("readingSecondsDelta"), 0)
        out = library.put_progress(book_id, _request_user(request), str(cfi),
                                   chapter_index, percent, delta)
        if out is None:
            raise HTTPException(status_code=404, detail="书籍不存在")
        return _ok(out)

    @app.get("/library/settings")
    def lib_settings(request: "fastapi.Request"):
        """设置视图（师生同构，只有 student 段；AI 段已随 LLM 退役移除）。"""
        is_teacher = getattr(request.state, "role", "") == "teacher"
        return _ok(library.settings_view(is_teacher))

    # ---- 书籍解锁（教师手动配置；学生书架可见性门控） ----

    @app.get("/library/book-unlock")
    def lib_book_unlock_get(request: "fastapi.Request"):
        """书籍解锁配置：{学生: [book_id...]}；空=全部可见。教师专属。"""
        _require_teacher_lib(request)
        return _ok(library.get_book_unlock())

    @app.put("/library/book-unlock")
    def lib_book_unlock_put(rec: dict, request: "fastapi.Request"):
        """保存书籍解锁配置（整体覆盖）。教师专属。"""
        _require_teacher_lib(request)
        body = rec.get("unlock") if isinstance(rec, dict) else None
        if not isinstance(body, dict):
            raise HTTPException(status_code=422, detail="unlock 须为对象 {学生: [书id]}")
        return _ok(library.put_book_unlock(body))

    # ---- 必读章配置（教师）：计划表阅读目标按配置算词数/目标章 ----

    @app.get("/library/books/{book_id}/reading-config")
    def lib_reading_config(book_id: int, request: "fastapi.Request"):
        """必读章 idx 列表（未配置返回 null = 全书口径）。师生可读。"""
        _require_book_access(request, book_id)
        if library.get_title(book_id) is None:
            raise HTTPException(status_code=404, detail="书籍不存在")
        return _ok({"book_id": book_id, "chapters": library.get_reading_config(book_id)})

    @app.put("/library/books/{book_id}/reading-config")
    def lib_reading_config_put(book_id: int, rec: dict,
                               request: "fastapi.Request"):
        """保存必读章配置（教师；chapters 空数组=清除回全书口径）。"""
        _require_teacher_lib(request)
        if library.get_title(book_id) is None:
            raise HTTPException(status_code=404, detail="书籍不存在")
        chs = rec.get("chapters") if isinstance(rec, dict) else None
        if not isinstance(chs, list) or not all(
                isinstance(x, int) or (isinstance(x, str) and x.isdigit()) for x in chs):
            raise HTTPException(status_code=422, detail="chapters 须为整数数组")
        library.put_reading_config(book_id, [int(x) for x in chs])
        return _ok({"book_id": book_id,
                    "chapters": library.get_reading_config(book_id)})

    @app.put("/library/settings")
    def lib_settings_put(rec: dict, request: "fastapi.Request"):
        """保存设置（教师，仅 student 段浅合并）；返回设置视图。

        旧前端 payload 里的 ai/dict 键在此静默忽略（LLM 退役，不再有对应配置）。
        """
        _require_teacher_lib(request)
        body = rec if isinstance(rec, dict) else {}
        student_patch = body.get("student") if isinstance(body.get("student"), dict) else None
        try:
            return _ok(library.update_settings(student_patch))
        except LibraryError as e:
            raise HTTPException(status_code=e.status, detail=str(e))

    @app.get("/library/dict/{word}")
    def lib_dict(word: str):
        """查词（师生）：ECDICT 命中返回词条，未命中返回 not_found（AI 兜底已退役）。"""
        w = (word or "").strip()
        if not w or len(w) > 40:
            raise HTTPException(status_code=400, detail="无效单词")
        return _ok({"entry": library.dict_lookup(w)})

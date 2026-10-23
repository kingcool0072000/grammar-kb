"""泛读馆：书架、epub、章节预习、查词与进度。"""
from __future__ import annotations

from typing import Optional

import fastapi  # noqa: F401  FastAPI 解析注解里的 fastapi.Request
from fastapi import HTTPException

from ..library import LibraryError
from ..prep_queue import generate_prep_for_chapter
from .http import _ok, _request_user, _require_teacher_lib, _to_int

def register_library(app, ctx):
    library = ctx.library
    prep_queue = ctx.prep_queue
    _require_book_access = ctx.require_book_access

    # ---- 泛读馆（整本书英文泛读：书架 / epub 导入 / AI 章节预习 / 查词 / 进度） ----
    # 注意：/library/prep/batch 系列具名路由必须注册在参数路由
    # /library/prep/{book_id}/{chapter_index} 之前，否则 "batch" 会被当 bookId 吞掉。
    from fastapi.responses import FileResponse

    @app.post("/library/prep/batch")
    def lib_prep_batch(rec: dict, request: "fastapi.Request"):
        """批量生成预习（教师）：跳过已生成与 skip_prep 章；有活干建 job 异步跑。"""
        _require_teacher_lib(request)
        book_id = _to_int(rec.get("bookId"))
        indexes = rec.get("chapterIndexes")
        if not book_id or not isinstance(indexes, list) or not indexes:
            raise HTTPException(status_code=400, detail="参数不完整")
        todo = library.filter_batch(book_id, [int(i) for i in indexes])
        if todo is None:
            raise HTTPException(status_code=404, detail="书籍不存在")
        if not todo:
            return _ok({"jobId": None, "skipped": True})
        job_id = prep_queue.create_job(book_id, _request_user(request), todo)
        return _ok({"jobId": job_id, "skipped": False})

    @app.get("/library/prep/batch/{job_id}")
    def lib_prep_job(job_id: int, request: "fastapi.Request"):
        """批量任务进度轮询（教师）。"""
        _require_teacher_lib(request)
        view = library.get_job_view(job_id)
        if view is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return _ok(view)

    @app.post("/library/prep/single")
    def lib_prep_single(rec: dict, request: "fastapi.Request"):
        """单章立即生成/重试（教师，同步等待）。缓存命中直接返回。"""
        _require_teacher_lib(request)
        book_id = _to_int(rec.get("bookId"))
        chapter_index = _to_int(rec.get("chapterIndex"))
        if not book_id or chapter_index is None:
            raise HTTPException(status_code=400, detail="参数不完整")
        try:
            generate_prep_for_chapter(library, book_id, chapter_index)
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e))
        return _ok({"ok": True})

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
        """上传 epub（教师）：仅 .epub ≤100MB；内存解析成功后事务落库+文件落盘。"""
        _require_teacher_lib(request)
        content = await file.read()
        try:
            book = library.add_book(
                _request_user(request), file.filename or "",
                file.content_type or "", content,
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

    @app.put("/library/books/{book_id}/chapters/{idx}/skip")
    def lib_chapter_skip(book_id: int, idx: int, rec: dict, request: "fastapi.Request"):
        """标记/取消「无需生成预习」（教师）。学生经 PUT 前缀白名单进到这，必须拦下。"""
        _require_teacher_lib(request)
        skip = bool(rec.get("skip"))
        out = library.set_skip(book_id, idx, skip)
        if out is None:
            raise HTTPException(status_code=404, detail="章节不存在")
        return _ok(out)

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
        """设置视图：教师完整版；学生只有 student + hasKey（绝不泄漏 apiKey/model/prompt）。"""
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
        """保存设置（教师，可部分传 ai/dict/student 浅合并）；返回教师视图。"""
        _require_teacher_lib(request)
        body = rec if isinstance(rec, dict) else {}
        ai_patch = body.get("ai") if isinstance(body.get("ai"), dict) else None
        dict_patch = body.get("dict") if isinstance(body.get("dict"), dict) else None
        student_patch = body.get("student") if isinstance(body.get("student"), dict) else None
        try:
            return _ok(library.update_settings(ai_patch, dict_patch, student_patch))
        except LibraryError as e:
            raise HTTPException(status_code=e.status, detail=str(e))

    @app.get("/library/models")
    def lib_models(request: "fastapi.Request", key: Optional[str] = None):
        """智谱模型列表（教师；未传 key 时用已保存的）；失败回退 FALLBACK_MODELS。"""
        from .. import glm as _glm

        _require_teacher_lib(request)
        k = (key or "").strip() or library.get_settings()["ai"].get("apiKey") or ""
        if not k:
            raise HTTPException(status_code=400, detail="请先填写 API Key")
        try:
            models = _glm.list_models(k)
            if not models:
                raise RuntimeError("模型列表为空")
            return _ok({"models": models})
        except Exception as e:  # noqa: BLE001 - 接口异常时回退内置候选
            return _ok({"models": list(_glm.FALLBACK_MODELS), "fallback": True,
                        "message": str(e)})

    @app.post("/library/ai/test")
    def lib_ai_test(rec: dict, request: "fastapi.Request"):
        """连通性测试（教师）。"""
        from .. import glm as _glm

        _require_teacher_lib(request)
        body = rec if isinstance(rec, dict) else {}
        k = (body.get("apiKey") or "").strip() or library.get_settings()["ai"].get("apiKey") or ""
        if not k:
            raise HTTPException(status_code=400, detail="请先填写 API Key")
        result = _glm.test_api_key(k)
        if not result.get("ok"):
            raise HTTPException(status_code=502, detail=result.get("message") or "连接失败")
        return _ok({"ok": True, "message": result.get("message", "")})

    @app.get("/library/dict/{word}")
    def lib_dict(word: str, context: Optional[str] = None):
        """查词（师生）：缓存 → ECDICT → AI 兜底 → not_found。"""
        w = (word or "").strip()
        if not w or len(w) > 40:
            raise HTTPException(status_code=400, detail="无效单词")
        entry = library.dict_lookup(w, context=(context or "")[:300] or None)
        return _ok({"entry": entry})

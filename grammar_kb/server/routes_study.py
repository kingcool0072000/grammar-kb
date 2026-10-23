"""背单词、专注力心跳、自学专题进度与内容管理。"""
from __future__ import annotations

from typing import Optional

import fastapi  # noqa: F401  FastAPI 解析注解里的 fastapi.Request
from fastapi import HTTPException

from .http import _ok, _request_user, _require_teacher
from .schemas import ReciteSessionIn, FocusSessionIn, TopicProgressIn, TopicImportIn, TopicFlagIn, TopicAssignIn

def register_study(app, ctx):
    kbq = ctx.kbq
    recite = ctx.recite
    focus = ctx.focus
    topics = ctx.topics
    plan = ctx.plan
    _fill_focus_book_titles = ctx.fill_focus_book_titles

    # ---- 背单词成绩上报（学生自动上报，教师批改中心查看） ----

    @app.post("/recite/sessions")
    def recite_submit(rec: ReciteSessionIn, request: "fastapi.Request"):
        """学生完成一组背单词练习后上报成绩（前端静默尽力上报）。

        details（逐词对错明细）随组落 recite_word_progress，云端进度唯一源。
        """
        user = _request_user(request)
        try:
            return _ok(recite.submit(
                user, rec.total, rec.wrong, rec.acc, rec.duration_sec,
                rec.wrong_words, rec.mode, rec.scope, details=rec.details,
            ))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.get("/recite/progress")
    def recite_progress(
        request: "fastapi.Request", user: Optional[str] = None,
        include_words: bool = False,
    ):
        """背单词云端逐词进度与分级汇总。学生只看自己的；教师可带 user。"""
        u = user if (request.state.role == "teacher" and user) else request.state.user
        return _ok(recite.progress(u, include_words=include_words))

    @app.post("/recite/progress/sync")
    def recite_progress_sync(payload: dict, request: "fastapi.Request"):
        """本地历史进度一次性上云合并（{word: {right, wrong}}，max 语义幂等）。

        返回合并后的云端全量逐词进度；前端用它覆盖 localStorage 后清除旧 key。
        """
        user = _request_user(request)
        data = payload.get("progress") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise HTTPException(status_code=422, detail="payload.progress 须为 {word: {right, wrong}}")
        if len(data) > 20000:
            raise HTTPException(status_code=422, detail="词数超限")
        return _ok(recite.merge_progress(user, data))

    @app.delete("/recite/progress")
    def recite_progress_clear(request: "fastapi.Request", user: Optional[str] = None):
        """清空学生云端逐词进度（教师专属，测试/重置用）。"""
        _require_teacher(request)
        if not user:
            raise HTTPException(status_code=422, detail="须指定 user")
        return _ok({"user": user, "deleted": recite.clear_progress(user)})

    @app.get("/recite/wrongbook")
    def recite_wrongbook(request: "fastapi.Request", user: Optional[str] = None, limit: int = 500):
        """背单词错词本（艾宾浩斯口径）：在周期词 + 已攻克词本 + 统计，
        词带 vocab_word 中文释义（大小写不敏感匹配）。"""
        u = user if (request.state.role == "teacher" and user) else request.state.user
        data = recite.wrongbook(u, limit=limit)
        items = data.get("in_cycle", []) + data.get("conquered", [])
        # 批量补中文释义（vocab_word 同库于 grammar.db；大小写不敏感匹配）
        import json as _json
        import sqlite3 as _sqlite3

        want = {str(x["word"]).lower() for x in items if x.get("word")}
        gloss_map: dict = {}
        ordered = sorted(want)
        for i in range(0, len(ordered), 400):  # SQLite 变量上限 999，分块
            chunk = ordered[i:i + 400]
            ph = ",".join("?" * len(chunk))
            try:
                with kbq.db.conn as conn:
                    rows = conn.execute(
                        f"SELECT word, gloss, meanings, pos, example FROM vocab_word"
                        f" WHERE lower(word) IN ({ph})",
                        chunk,
                    ).fetchall()
            except (_sqlite3.Error, AttributeError):
                break
            for r in rows:
                try:
                    meanings = _json.loads(r["meanings"] or "[]")
                except (ValueError, TypeError):
                    meanings = []
                try:
                    pos = _json.loads(r["pos"] or "[]")
                except (ValueError, TypeError):
                    pos = []
                try:
                    example = _json.loads(r["example"] or "{}")
                except (ValueError, TypeError):
                    example = {}
                gloss_map[str(r["word"]).lower()] = {
                    "gloss": r["gloss"] or "", "meanings": meanings,
                    "pos": pos if isinstance(pos, list) else [],
                    "example": example if isinstance(example, dict) else {},
                }
        for x in items:
            g = gloss_map.get(str(x["word"]).lower())
            if g:
                x["gloss"] = g["gloss"]
                x["meanings"] = g["meanings"]
                x["pos"] = g["pos"]
                x["example"] = g["example"]
        return _ok(data)

    @app.get("/recite/sessions")
    def recite_list(
        request: "fastapi.Request", user: Optional[str] = None, limit: int = 100,
    ):
        """背单词练习记录。学生只看自己的；教师看全部。"""
        if request.state.role != "teacher":
            user = request.state.user
        return _ok(recite.list(user=user, limit=limit))

    # ---- 专注力采集（泛读馆阅读器学生静默上报，教师查看） ----

    @app.post("/focus/sessions")
    def focus_submit(rec: FocusSessionIn, request: "fastapi.Request"):
        """专注力心跳上报：前端每 60 秒重传累计值，按 session_id 幂等覆盖。"""
        user = _request_user(request)
        try:
            return _ok(focus.submit(user, **rec.model_dump()))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.get("/focus/sessions")
    def focus_list(
        request: "fastapi.Request", user: Optional[str] = None, limit: int = 100,
        offset: int = 0,
    ):
        """专注力会话列表（不含轨迹/鼠标明细）。学生只看自己的；教师看全部。
        limit+offset 翻页（id 倒序）；book_title 为空的行按 book_id 反查书名补全。"""
        if request.state.role != "teacher":
            user = request.state.user
        items = focus.list(user=user, limit=limit, offset=offset)
        _fill_focus_book_titles(items)
        return _ok(items)

    @app.get("/focus/sessions/{row_id}")
    def focus_detail(row_id: int, request: "fastapi.Request"):
        """专注力会话详情（含鼠标指标与进度折线）。教师专属。"""
        _require_teacher(request)
        data = focus.get(row_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"会话 id={row_id} 不存在")
        _fill_focus_book_titles([data])
        return _ok(data)

    @app.get("/focus/words")
    def focus_words(request: "fastapi.Request", ids: str = ""):
        """学习日志逐会话泛读词数（与计划表日卡片同一公式）。教师专属。"""
        _require_teacher(request)
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()][:50]
        return _ok(plan.focus_words(id_list))

    # ---- 专题学习（学生自学手册「我学完了」进度；进度行绑定学生本人） ----

    @app.get("/topics/progress")
    def topics_progress_list(request: "fastapi.Request"):
        """专题进度列表。学生只看自己的；教师看全部。"""
        return _ok(topics.list_for(
            _request_user(request), getattr(request.state, "role", ""),
        ))

    @app.get("/topics/{topic_id}/progress")
    def topics_progress_get(topic_id: str, request: "fastapi.Request"):
        """单个专题进度（学生本人；教师可带 ?user= 查任意学生）。"""
        user = _request_user(request)
        if request.state.role == "teacher":
            user = request.query_params.get("user") or user
        return _ok(topics.get(user, topic_id))

    @app.put("/topics/{topic_id}/progress")
    def topics_progress_put(
        topic_id: str, rec: "TopicProgressIn", request: "fastapi.Request",
    ):
        """覆盖写入当前学生的专题进度（谁登录记谁的行，教师代记也记在教师名下）。"""
        try:
            return _ok(topics.put(
                _request_user(request), topic_id, rec.done_keys,
                assigned_user=rec.assigned_user,
            ))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    # ---- v2 结构化专题内容：教师制作（JSON 导入），学生按 assigned 开放 ----

    @app.get("/topics-admin/list")
    def topics_admin_list(request: "fastapi.Request"):
        """专题内容管理列表（教师）。"""
        _require_teacher(request)
        return _ok(topics.list_topics())

    @app.post("/topics-admin/import")
    def topics_admin_import(rec: "TopicImportIn", request: "fastapi.Request"):
        """JSON 导入/更新专题（同 topic_id 覆盖）。教师。"""
        _require_teacher(request)
        try:
            return _ok(topics.upsert_topic(rec.meta or {}, rec.content or {}))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.get("/topics-admin/{topic_id}")
    def topics_admin_get(topic_id: str, request: "fastapi.Request"):
        """专题内容详情（教师；不展开题库引用，用于回填编辑）。"""
        _require_teacher(request)
        got = topics.get_topic(topic_id, expand=False)
        if got is None:
            raise HTTPException(status_code=404, detail=f"专题 {topic_id} 不存在")
        return _ok(got)

    @app.put("/topics-admin/{topic_id}/published")
    def topics_admin_publish(
        topic_id: str, rec: "TopicFlagIn", request: "fastapi.Request",
    ):
        """上架/下架。教师。"""
        _require_teacher(request)
        topics.set_published(topic_id, bool(rec.on))
        return _ok({"topic_id": topic_id, "published": bool(rec.on)})

    @app.put("/topics-admin/{topic_id}/assigned")
    def topics_admin_assign(
        topic_id: str, rec: "TopicAssignIn", request: "fastapi.Request",
    ):
        """配置开放学生（空=全员；逗号分隔=指定学生）。教师。"""
        _require_teacher(request)
        topics.set_assigned(topic_id, rec.assigned_to or "")
        return _ok({"topic_id": topic_id, "assigned_to": rec.assigned_to or ""})

    @app.delete("/topics-admin/{topic_id}")
    def topics_admin_delete(topic_id: str, request: "fastapi.Request"):
        """删除专题内容（进度行保留）。教师。"""
        _require_teacher(request)
        topics.delete_topic(topic_id)
        return _ok({"deleted": topic_id})

    @app.get("/topics-v2")
    def topics_v2_list(request: "fastapi.Request"):
        """学生可见的结构化专题列表（published+assigned）。"""
        return _ok(topics.list_topics(
            _request_user(request),
            getattr(request.state, "role", ""),
        ))

    @app.get("/topics-v2/{topic_id}")
    def topics_v2_get(topic_id: str, request: "fastapi.Request"):
        """单个结构化专题（题目已展开：题库引用带题干/AI 答案）。学生按开放权限。"""
        user = _request_user(request)
        role = getattr(request.state, "role", "")
        got = topics.get_topic(topic_id)
        if got is None:
            raise HTTPException(status_code=404, detail=f"专题 {topic_id} 不存在")
        if role != "teacher":
            assigned = [x for x in (got["assigned_to"] or "").split(",") if x]
            if not got["published"] or (assigned and user not in assigned):
                raise HTTPException(status_code=403, detail="该专题未对你开放")
        return _ok(got)

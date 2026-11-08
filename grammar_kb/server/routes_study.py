"""背单词、专注力心跳、自学专题进度与内容管理、句型清单。"""
from __future__ import annotations

import logging
from typing import Optional

import fastapi  # noqa: F401  FastAPI 解析注解里的 fastapi.Request
from fastapi import HTTPException

from .http import _ok, _request_user, _require_teacher
from .schemas import ReciteSessionIn, FocusSessionIn, TopicProgressIn, TopicImportIn, TopicFlagIn, TopicAssignIn, PatternsImportIn
from .. import patterns
from ..clock import today_cn
from ..recite import DuplicateSessionError
from ..vocabulary import fill_word_gloss, vocab_match_words

logger = logging.getLogger(__name__)

# 提升档打卡（plan/boost.py，并行车道）：模块未就位时置 None 兜底，
# 不影响本文件其余端点（集成时保证就位）。
try:
    from ..plan import boost
except ImportError:  # noqa: F401
    boost = None


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
        except DuplicateSessionError as e:
            # 连点/重试的兜底拦截：窗口内重复会话直接拒，不落库不推进节点
            raise HTTPException(status_code=429, detail=str(e))
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

    @app.get("/recite/review-summary")
    def recite_review_summary(request: "fastapi.Request", user: Optional[str] = None):
        """复习闭环摘要：到期 / 逾期 / 在周期 / 近 7 日与累计攻克 / 最近错词活动。

        学生只看自己的；教师可带 user。口径与 /recite/wrongbook 相同。
        逾期未清时附带今日新词 new_used / new_cap。
        """
        u = user if (request.state.role == "teacher" and user) else request.state.user
        return _ok(recite.review_summary(u))

    @app.get("/recite/wrongbook")
    def recite_wrongbook(request: "fastapi.Request", user: Optional[str] = None, limit: int = 500):
        """背单词错词本（艾宾浩斯口径）：在周期词 + 已攻克词本 + 统计，
        词带 vocab_word 中文释义（大小写不敏感匹配）。"""
        u = user if (request.state.role == "teacher" and user) else request.state.user
        data = recite.wrongbook(u, limit=limit)
        items = data.get("in_cycle", []) + data.get("conquered", [])
        fill_word_gloss(kbq.db.conn, items)
        return _ok(data)

    @app.get("/recite/conquered")
    def recite_conquered(
        request: "fastapi.Request", user: Optional[str] = None,
        page: int = 1, limit: int = 40, q: str = "",
    ):
        """已攻克词本分页。一页词条 + total/has_more，不返回全集。"""
        u = user if (request.state.role == "teacher" and user) else request.state.user
        also = vocab_match_words(kbq.db.conn, q) if (q or "").strip() else []
        data = recite.conquered_page(u, page=page, limit=limit, q=q, also_words=also)
        fill_word_gloss(kbq.db.conn, data.get("items") or [])
        return _ok(data)

    @app.get("/recite/gloss")
    def recite_gloss(words: str = ""):
        """开练前按需补释义：最多 40 个词，不拉错题本全集。"""
        names: list[str] = []
        for part in (words or "").split(","):
            w = part.strip().lower()[:60]
            if w and w not in names:
                names.append(w)
            if len(names) >= 40:
                break
        items = [{"word": w} for w in names]
        fill_word_gloss(kbq.db.conn, items)
        return _ok([x for x in items if x.get("gloss") or x.get("meanings")])

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

    @app.put("/topics/{topic_id}/nodes/{node_key}/memory-pass")
    def topic_memory_pass(topic_id: str, node_key: str, request: "fastapi.Request"):
        """专题节点口诀卡翻面自评过关（v3 提升档 memory 打卡）。

        学生打开节点只见提示面（front），点「翻开」看口诀（back）后自评
        「记住了」即打卡，一次自评通过（与背词翻面自评同口径）。可见性
        门禁与 /topics-v2/{topic_id} 同款。
        """
        user = _request_user(request)
        role = getattr(request.state, "role", "")
        got = topics.get_topic(topic_id)
        if got is None:
            raise HTTPException(status_code=404, detail=f"专题 {topic_id} 不存在")
        if role != "teacher":
            assigned = [x for x in (got["assigned_to"] or "").split(",") if x]
            if not got["published"] or (assigned and user not in assigned):
                raise HTTPException(status_code=403, detail="该专题未对你开放")
        node = topics.get_node(topic_id, node_key)
        if node is None:
            raise HTTPException(status_code=404, detail=f"节点 {node_key} 不存在")
        if not node.get("memory"):
            raise HTTPException(status_code=422, detail="该节点没有口诀卡")
        if boost is not None:
            try:
                boost.record(user, today_cn().isoformat(),
                             f"memory:{topic_id}:{node_key}", "memory",
                             passed=True)
            except Exception:  # noqa: BLE001  打卡失败不拦自评成功
                logger.warning("boost.record 失败（memory:%s:%s user=%s）",
                               topic_id, node_key, user, exc_info=True)
        else:
            logger.warning("plan.boost 未就位，memory 打卡未落库（%s:%s）",
                           topic_id, node_key)
        return _ok({"ok": True})

    # ---- 句型清单（v3 提升档「句型跟读复述」素材；提交走 video-study 复述链路） ----

    @app.get("/patterns")
    def patterns_list(request: "fastapi.Request", lecture: Optional[int] = None):
        """句型清单（可 ?lecture=30 按讲次过滤）。学生可读。"""
        return _ok(patterns.list_all(lecture))

    @app.post("/patterns-admin/import")
    def patterns_import(rec: "PatternsImportIn", request: "fastapi.Request"):
        """批量导入句型（data/patterns/*.json 走此口；同讲次同句跳过）。教师。"""
        _require_teacher(request)
        try:
            n = patterns.import_items([x.model_dump() for x in rec.items])
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return _ok({"imported": n})

"""微练习：学生每日 3 题（拉题/判分提交）+ 教师题库管理与统计。

学生端点只操作自己的数据（user 取自 auth 中间件注入的 request.state）；
micro-admin 端点教师专属（_require_teacher 细闸，中间件白名单也不放行学生）。
"""
from __future__ import annotations

import fastapi  # noqa: F401  FastAPI 解析注解里的 fastapi.Request
from fastapi import HTTPException

from .http import _ok, _request_user, _require_teacher
from .schemas import MicroQuestionIn, MicroQuestionUpdateIn, MicroSubmitIn
from ..clock import today_cn


def register_micro(app, ctx):
    micro = ctx.micro

    def _day_or_today(day: str) -> str:
        return (day or "").strip() or today_cn().isoformat()

    # ---- 学生端 ----

    @app.get("/micro/today")
    def micro_today(request: "fastapi.Request", day: str = ""):
        """当日 3 题（幂等：同一天重复调用返回同一组）。?day= 查历史。"""
        try:
            return _ok(micro.today(_request_user(request),
                                   _day_or_today(day)))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.post("/micro/submit")
    def micro_submit(rec: MicroSubmitIn, request: "fastapi.Request"):
        """提交作答，服务端判分。已答过的题重复提交不重复落 log。"""
        try:
            return _ok(micro.submit(
                _request_user(request), _day_or_today(rec.day),
                [a.model_dump() for a in rec.answers],
            ))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.get("/micro/history")
    def micro_history(request: "fastapi.Request", limit: int = 14):
        """我的做题记录（按天倒序，含题目/我的作答/对错/解析）。学生只看自己。"""
        return _ok(micro.history(_request_user(request), limit))

    # ---- 教师端：题库管理 ----

    @app.get("/micro-admin/questions")
    def micro_admin_list(request: "fastapi.Request", status: str = "",
                         category: str = "", limit: int = 500):
        """题库列表（status/category 过滤）。"""
        _require_teacher(request)
        return _ok(micro.list_questions(
            status=status or None, category=category or None, limit=limit))

    @app.post("/micro-admin/questions")
    def micro_admin_add(rec: MicroQuestionIn, request: "fastapi.Request"):
        """手动加题（默认 draft，上架走 PUT）。"""
        _require_teacher(request)
        try:
            qid = micro.add_question(**rec.model_dump())
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return _ok({"id": qid})

    @app.put("/micro-admin/questions/{qid}")
    def micro_admin_update(qid: int, rec: MicroQuestionUpdateIn,
                           request: "fastapi.Request"):
        """编辑题面/答案/状态（仅传修改字段）。"""
        _require_teacher(request)
        fields = {k: v for k, v in rec.model_dump().items() if v is not None}
        try:
            micro.update_question(qid, fields)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return _ok({"id": qid, "updated": sorted(fields)})

    @app.delete("/micro-admin/questions/{qid}")
    def micro_admin_delete(qid: int, request: "fastapi.Request"):
        """删题（作答流水保留）。"""
        _require_teacher(request)
        micro.delete_question(qid)
        return _ok({"deleted": qid})

    @app.get("/micro-admin/stats")
    def micro_admin_stats(request: "fastapi.Request"):
        """题库与作答统计（正确率由前端算）。"""
        _require_teacher(request)
        return _ok(micro.stats())

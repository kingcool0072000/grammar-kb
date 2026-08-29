"""HTTP 服务（可选依赖：``pip install fastapi uvicorn``，或 ``uv sync --extra server``）。

把 :class:`grammar_kb.query.Query` 的能力以 REST API 暴露，与 CLI / MCP 共用同一查询层。

- 统一响应格式：成功 ``{code:0, message:"ok", data:<...>}``；错误 ``{code:<http>, message, data:null}``
- 默认开启 CORS（可用环境变量 ``GRAMMAR_KB_CORS_ORIGINS`` 收紧，逗号分隔；默认 ``*``）

启动：
    grammar-kb-server                     # 默认 127.0.0.1:8000
    grammar-kb-server --port 8080 --host 0.0.0.0
    grammar-kb serve --port 8080          # 经由 CLI
    GRAMMAR_KB_DB=/path/grammar.db grammar-kb-server

启动后访问 http://127.0.0.1:8000/docs 查看交互式 API 文档。
"""
from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any, Optional

from . import __version__
from .ingest import open_db
from .query import Query

# 闭包内端点用 fastapi.Request 注解（本文件启用延迟注解，字符串注解只能在
# 模块命名空间求值——闭包局部名 Request 不可见会报 422）
import fastapi


def _ok(data: Any = None, message: str = "ok") -> dict:
    """统一成功响应包装。"""
    return {"code": 0, "message": message, "data": data}


try:
    from pydantic import BaseModel, Field

    class ExamRecordIn(BaseModel):
        """作业成绩请求体（模块级定义：本文件启用延迟注解，闭包内模型无法被 FastAPI 解析）。"""

        lecture: int = Field(ge=1, le=99, description="讲次")
        date: str = Field(description="作答日期 YYYY-MM-DD")
        score: int = Field(default=0, ge=0, le=100)
        wrong: list[int] = Field(default_factory=list, description="错题题号")

    class LoginIn(BaseModel):
        """登录请求体。"""

        user: str = Field(min_length=1, max_length=32)
        password: str = Field(min_length=1, max_length=64)

    class FceSubmissionIn(BaseModel):
        """FCE 大题练习提交（模块级定义：延迟注解下闭包内模型无法被解析）。"""

        test_id: int = Field(ge=1, le=4)
        paper: str
        part: int = Field(ge=0, le=7)
        answers: dict = Field(default_factory=dict)
        duration_sec: Optional[int] = Field(default=None, ge=0, le=24 * 3600)

    class FceGradeIn(BaseModel):
        """教师批改作文。"""

        teacher_score: Optional[int] = Field(default=None, ge=0, le=100)
        teacher_comment: str = ""

except ImportError:  # 未装 fastapi/pydantic 时仍可 import 本模块
    ExamRecordIn = None
    FceSubmissionIn = None
    FceGradeIn = None


def create_app(db_path: Optional[str] = None, exam_db_path: Optional[str] = None):
    """构造 FastAPI 应用。``db_path`` 为 None 时走默认库（GRAMMAR_KB_DB 或 data/grammar.db）；
    ``exam_db_path`` 为成绩库路径（默认 iCloud Drive 或 data/exam.db）。"""
    from fastapi import FastAPI, HTTPException, Query as FQuery, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    from .exam_store import ExamStore
    from .auth import UserStore, make_token, read_token
    from .fce_query import FcePaperStore, FceSubmissionStore

    kbq = Query(open_db(db_path))
    exams = ExamStore(exam_db_path)
    users = UserStore()
    fce_papers = FcePaperStore()
    fce_submissions = FceSubmissionStore()
    app = FastAPI(
        title="grammar-kb 题库 API",
        version=__version__,
        description="PDF 讲义/教材知识点库的只读查询服务",
    )

    # ---- CORS ----
    raw = os.environ.get("GRAMMAR_KB_CORS_ORIGINS", "*").strip()
    allow_origins = ["*"] if raw == "*" else [o.strip() for o in raw.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- 统一错误响应 ----
    @app.exception_handler(HTTPException)
    async def _http_exc(_, exc):  # noqa: ANN001
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "message": str(exc.detail), "data": None},
        )

    @app.exception_handler(Exception)
    async def _unhandled(_, exc):  # noqa: ANN001
        return JSONResponse(
            status_code=500,
            content={"code": 500, "message": f"内部错误: {exc}", "data": None},
        )

    # ---- 认证 ----
    # 白名单路径免登录：根信息、API 文档、登录本身
    _AUTH_OPEN = frozenset({"/", "/docs", "/redoc", "/openapi.json", "/auth/login"})
    # 学生角色可访问的 (method, path 前缀)：背单词所需数据 + 提交成绩 + FCE 真题练习
    _STUDENT_ALLOW = (
        ("GET", "/stats"),
        ("GET", "/vocabulary"),
        ("GET", "/dict/"),
        ("POST", "/exams"),
        ("GET", "/fce-papers"),
        ("POST", "/fce-submissions"),
        ("GET", "/fce-submissions"),
    )

    @app.middleware("http")
    async def _auth(request, call_next):  # noqa: ANN001
        path = request.url.path.rstrip("/") or "/"
        if path in _AUTH_OPEN:
            return await call_next(request)
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else ""
        payload = read_token(token) if token else None
        if payload is None:
            return JSONResponse(
                status_code=401,
                content={"code": 401, "message": "未登录或登录已过期", "data": None},
            )
        request.state.user = payload["user"]
        request.state.role = payload["role"]
        if payload["role"] != "teacher" and not any(
            request.method == m and path.startswith(p) for m, p in _STUDENT_ALLOW
        ):
            return JSONResponse(
                status_code=403,
                content={"code": 403, "message": "该功能仅教师账号可用", "data": None},
            )
        return await call_next(request)

    def _request_user(request) -> str:
        """从 auth 中间件注入的 state 取用户名。"""
        return getattr(request.state, "user", "")

    @app.post("/auth/login")
    def auth_login(rec: LoginIn):
        role = users.verify(rec.user, rec.password)
        if role is None:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        return _ok({"user": rec.user, "role": role, "token": make_token(rec.user, role)})

    # ---- 端点 ----
    @app.get("/")
    def root():
        return _ok(
            {
                "service": "grammar-kb",
                "version": __version__,
                "endpoints": [
                    "POST /auth/login",
                    "GET /stats",
                    "GET /lectures",
                    "GET /lectures/{number}?format=markdown|html",
                    "GET /kp/{id}?format=markdown|html",
                    "GET /search?q=...&category=...&limit=...",
                    "GET /markers?category=时态&tense=...",
                    "GET /relation?type=主将从现",
                    "GET /exam-signals",
                    "GET /exam-signal?signal=时态",
                    "GET /vocabulary?limit=300&min_freq=2",
                    "GET /fce-papers · GET /fce-papers/{test_id}",
                    "GET /homework · GET /homework/{lecture}",
                    "GET/POST /exams · PUT/DELETE /exams/{id}",
                    "GET /docs (Swagger UI)",
                ],
            }
        )

    @app.get("/stats")
    def stats():
        return _ok(kbq.stats())

    @app.get("/lectures")
    def lectures():
        return _ok([asdict(l) for l in kbq.list_lectures()])

    @app.get("/lectures/{number}")
    def lecture(number: int, format: str = "markdown"):
        content = kbq.lecture_html(number) if format == "html" else kbq.lecture_markdown(number)
        if content is None:
            raise HTTPException(status_code=404, detail=f"第 {number} 讲不存在")
        lec = kbq.get_lecture(number)
        return _ok(
            {
                "number": number,
                "title": lec.title if lec else "",
                "category": lec.category if lec else "",
                "format": format,
                "content": content,
            }
        )

    @app.get("/kp/{kp_id}")
    def kp(kp_id: int, format: str = "markdown"):
        content = kbq.kp_html(kp_id) if format == "html" else kbq.kp_markdown(kp_id)
        if content is None:
            raise HTTPException(status_code=404, detail=f"知识点 id={kp_id} 不存在")
        return _ok({"id": kp_id, "format": format, "content": content})

    @app.get("/search")
    def search(
        q: str = FQuery(..., description="关键词"),
        category: Optional[str] = None,
        limit: int = 20,
    ):
        items = kbq.search_kps(q, category=category, limit=limit)
        return _ok(
            {"query": q, "category": category, "count": len(items), "items": [asdict(k) for k in items]}
        )

    @app.get("/markers")
    def markers(category: str = "时态", tense: Optional[str] = None):
        rows = kbq.markers_by_tense(tense) if tense else kbq.markers_by_category(category)
        return _ok({"category": category, "tense": tense, "count": len(rows), "items": rows})

    @app.get("/relation")
    def relation(type: str = "主将从现"):
        items = kbq.kps_by_relation(type)
        return _ok({"type": type, "count": len(items), "items": [asdict(k) for k in items]})

    @app.get("/exam-signals")
    def exam_signals():
        """列出数据中实际出现的所有考点信号维度。"""
        return _ok(kbq.list_exam_signals())

    @app.get("/exam-signal")
    def exam_signal(signal: str = "时态"):
        """反查：给定考点信号，返回会考该信号的知识点（反之亦然）。"""
        items = kbq.kps_by_exam_signal(signal)
        return _ok({"signal": signal, "count": len(items), "items": [asdict(k) for k in items]})

    @app.get("/taxonomy")
    def taxonomy():
        """知识点主题体系树（大类 → 主题 → 知识点），聚合零散知识点。"""
        return _ok(kbq.taxonomy())

    @app.get("/homework")
    def homework_list(lectures: Optional[str] = None):
        """已导入作业卷的讲次列表；?lectures=2,3 批量取多讲题目（题干+选项）。"""
        if not lectures:
            return _ok(kbq.homework_list())
        nums = []
        for part in lectures.split(","):
            part = part.strip()
            if part.isdigit():
                nums.append(int(part))
        return _ok({n: (kbq.homework(n) or {}).get("items", []) for n in nums})

    @app.get("/homework/{lecture}")
    def homework(lecture: int):
        """某讲作业卷全部题目（题干 + 选项，题号与测验平台一致）。"""
        data = kbq.homework(lecture)
        if data is None:
            raise HTTPException(status_code=404, detail=f"第 {lecture} 讲的作业卷未导入")
        return _ok(data)

    @app.get("/dict/{word}")
    def dict_lookup(word: str):
        """查任意单词（ECDICT 全量词典，不限于讲义语料）。"""
        entry = kbq.dict_lookup(word)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"词典未收录：{word}")
        return _ok(entry)

    @app.get("/vocabulary")
    def vocabulary(limit: int = 300, min_freq: int = 2):
        """基于讲义语料的单词表（释义/词性/词形变化/来源）。"""
        return _ok(kbq.vocabulary(limit=limit, min_freq=min_freq))

    # ---- FCE 真题（只读；独立 data/fce.db，由 fce_paper 模块入库） ----

    @app.get("/fce-papers")
    def fce_papers_list():
        """FCE 青少版模拟卷概览：4 套 Test 各 paper/part 的题数。"""
        return _ok(fce_papers.list_papers())

    @app.get("/fce-papers/{test_id}")
    def fce_paper_detail(test_id: int, request: "fastapi.Request"):
        """单套 FCE Test 全部内容。学生版剥离答案/关键词（练习用），教师版完整。"""
        data = fce_papers.get_paper(test_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"FCE Test {test_id} 不存在")
        if getattr(request.state, "role", "teacher") != "teacher":
            for sec in data["sections"]:
                for q in sec["questions"]:
                    q["answer"] = ""
        return _ok(data)

    @app.post("/fce-submissions")
    def fce_submit(rec: FceSubmissionIn, request: "fastapi.Request"):
        """提交一次大题练习：客观题自动批改；作文转待教师批改。"""
        user = _request_user(request)
        try:
            data = fce_submissions.submit(
                user, rec.test_id, rec.paper, rec.part, rec.answers,
                duration_sec=rec.duration_sec,
            )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e).strip("'"))
        return _ok(data)

    @app.get("/fce-submissions")
    def fce_submissions_list(
        request: "fastapi.Request",
        user: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ):
        """练习历史。学生只能看自己的；教师可看全部（?status=pending 拉待批改作文）。"""
        if request.state.role != "teacher":
            user = request.state.user
        return _ok(
            fce_submissions.list(
                user=(user or None), status=(status or None), limit=limit
            )
        )

    @app.get("/fce-submissions/{sub_id}")
    def fce_submission_detail(sub_id: int):
        data = fce_submissions.get(sub_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"提交记录 id={sub_id} 不存在")
        return _ok(data)

    @app.put("/fce-submissions/{sub_id}")
    def fce_grade(sub_id: int, rec: FceGradeIn):
        """教师批改作文（打分 + 评语）。"""
        data = fce_submissions.grade(sub_id, rec.teacher_score, rec.teacher_comment)
        if data is None:
            raise HTTPException(status_code=404, detail=f"提交记录 id={sub_id} 不存在")
        return _ok(data)

    # ---- 作业成绩（可写；独立 exam.db，默认放 iCloud Drive 跨设备同步） ----

    @app.get("/exams")
    def exams_list():
        return _ok(exams.list())

    @app.post("/exams")
    def exams_add(rec: ExamRecordIn):
        return _ok(exams.add(**rec.model_dump()))

    @app.put("/exams/{exam_id}")
    def exams_update(exam_id: int, rec: ExamRecordIn):
        updated = exams.update(exam_id, **rec.model_dump())
        if updated is None:
            raise HTTPException(status_code=404, detail=f"成绩记录 id={exam_id} 不存在")
        return _ok(updated)

    @app.delete("/exams/{exam_id}")
    def exams_delete(exam_id: int):
        if not exams.delete(exam_id):
            raise HTTPException(status_code=404, detail=f"成绩记录 id={exam_id} 不存在")
        return _ok({"id": exam_id})

    return app


# 模块级单例（供 `uvicorn grammar_kb.server:app` 使用，走默认库）
try:
    app = create_app()
except Exception:  # 未安装 fastapi 时仍可 import 本模块
    app = None


def run_app(
    host: str = "127.0.0.1",
    port: int = 8000,
    db_path: Optional[str] = None,
    exam_db_path: Optional[str] = None,
) -> None:
    import uvicorn

    uvicorn.run(create_app(db_path, exam_db_path), host=host, port=port)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="grammar-kb-server", description="grammar-kb HTTP 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=None, help="数据库路径（默认 data/grammar.db 或 $GRAMMAR_KB_DB）")
    parser.add_argument(
        "--exam-db",
        default=None,
        help="作业成绩库路径（默认 $GRAMMAR_KB_EXAM_DB，iCloud Drive 可用时用 iCloud，否则 data/exam.db）",
    )
    args = parser.parse_args()
    run_app(host=args.host, port=args.port, db_path=args.db, exam_db_path=args.exam_db)


if __name__ == "__main__":
    main()

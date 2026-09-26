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
import re as _re
from dataclasses import asdict
from typing import Any, Optional
from pathlib import Path as _P

from . import __version__
from .ingest import open_db
from .query import Query

# 闭包内端点用 fastapi.Request 注解（本文件启用延迟注解，字符串注解只能在
# 模块命名空间求值——闭包局部名 Request 不可见会报 422）
import fastapi


def _ok(data: Any = None, message: str = "ok") -> dict:
    """统一成功响应包装。"""
    return {"code": 0, "message": message, "data": data}


def _html_response(body: str):
    """text/html 响应（考卷打印页等）。"""
    from fastapi.responses import HTMLResponse

    return HTMLResponse(body)


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

    class ReadingDerivedIn(BaseModel):
        """教师新增派生阅读文。"""

        base_key: str = Field(min_length=1, max_length=32)
        title: str = Field(default="", max_length=120)
        text: str = Field(min_length=20)
        source: str = Field(default="", max_length=200)

    class ReadingUpdateIn(BaseModel):
        """教师编辑派生阅读文（全部可选，仅传修改字段）。"""

        title: Optional[str] = Field(default=None, max_length=120)
        text: Optional[str] = None
        source: Optional[str] = Field(default=None, max_length=200)
        base_key: Optional[str] = Field(default=None, max_length=32)

    class ReadingRecordIn(BaseModel):
        """学生提交阅读录音（base64 webm，≤5 分钟，附选中的朗读文本）。"""

        article_id: int = Field(ge=1)
        audio_b64: str = Field(min_length=8)
        mime: str = Field(default="audio/webm", max_length=40)
        duration_sec: int = Field(default=0, ge=0, le=300)
        selected_text: str = Field(default="", max_length=4000)

    class ReadingGradeIn(BaseModel):
        """教师给录音打分（10 分制）。"""

        score: int = Field(ge=0, le=10)
        comment: str = Field(default="", max_length=2000)

    class ReciteSessionIn(BaseModel):
        """学生背单词一组练习的成绩上报。"""

        total: int = Field(ge=1, le=500)
        wrong: int = Field(default=0, ge=0, le=500)
        acc: int = Field(default=0, ge=0, le=100)
        duration_sec: int = Field(default=0, ge=0, le=3600)
        wrong_words: list = Field(default_factory=list)
        mode: str = Field(default="", max_length=20)
        scope: str = Field(default="", max_length=20)
        details: Optional[list] = None  # 逐词对错明细 [{word, correct}]，落云端逐词进度

    class FocusSessionIn(BaseModel):
        """泛读馆阅读器专注力心跳上报（累计值，按 session_id 幂等覆盖）。"""
        module: str = Field(default="library", max_length=16)
        range_start: Optional[float] = None
        range_end: Optional[float] = None
        range_label: str = Field(default="", max_length=120)
        lookup_words: Optional[list] = None
        speak_words: Optional[list] = None
        selected_words: Optional[list] = None
        activity: Optional[list] = None

        session_id: str = Field(min_length=1, max_length=64)
        book_id: Optional[int] = None
        book_title: str = Field(default="", max_length=200)
        started_at: str = Field(default="", max_length=32)
        ended_at: str = Field(default="", max_length=32)
        total_sec: int = Field(default=0, ge=0, le=14400)
        active_sec: int = Field(default=0, ge=0, le=14400)
        away_sec: int = Field(default=0, ge=0, le=14400)
        longest_away_sec: int = Field(default=0, ge=0, le=14400)
        away_count: int = Field(default=0, ge=0, le=9999)
        scroll_count: int = Field(default=0, ge=0, le=9999)
        chapter_navs: int = Field(default=0, ge=0, le=9999)
        lookups: int = Field(default=0, ge=0, le=9999)
        plays: int = Field(default=0, ge=0, le=9999)
        prep_opens: int = Field(default=0, ge=0, le=9999)
        prep_starts: int = Field(default=0, ge=0, le=9999)
        fast_scroll_flags: int = Field(default=0, ge=0, le=9999)
        percent_start: float = Field(default=0.0, ge=0, le=100)
        percent_end: float = Field(default=0.0, ge=0, le=100)
        mouse_metrics: Optional[dict] = None
        polyline: Optional[str] = Field(default=None, max_length=200000)

    class TopicProgressIn(BaseModel):
        """专题学习「我学完了」进度覆盖（done_keys＝已学完主题 key 集合）。"""

        done_keys: list[str] = Field(default_factory=list)
        assigned_user: str = Field(default="", max_length=60)

except ImportError:  # 未装 fastapi/pydantic 时仍可 import 本模块
    ExamRecordIn = None
    FceSubmissionIn = None
    FceDerivedIn = None
    ReadingDerivedIn = None
    ReadingUpdateIn = None
    ReadingRecordIn = None
    ReadingGradeIn = None
    ReciteSessionIn = None
    FocusSessionIn = None
    TopicProgressIn = None


def create_app(db_path: Optional[str] = None, exam_db_path: Optional[str] = None,
              fce_db_path: Optional[str] = None):
    """构造 FastAPI 应用。``db_path`` 为 None 时走默认库（GRAMMAR_KB_DB 或 data/grammar.db）；
    ``exam_db_path`` 为成绩库路径（默认 data/exam.db）。"""
    from fastapi import FastAPI, HTTPException, Query as FQuery
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    from .exam_store import ExamStore, default_exam_db_path
    from .auth import UserStore, make_token, read_token
    from .fce_query import FcePaperStore, FceSubmissionStore
    from .library import LibraryError, LibraryStore
    from .prep_queue import PrepQueue, generate_prep_for_chapter
    from .reading import ReadingStore
    from .recite import ReciteStore
    from .focus import FocusStore
    from .topics import TopicStore
    from .plan import PlanStore
    from .vocab_exam import VocabExamStore, build_paper, render_paper_html
    from .analytics_ai import AnalyticsAI

    # exam 库路径在此先落定：ExamStore 内部会自解析默认路径，但
    # AnalyticsAI 拿到 None 会 str(None)="None" → sqlite3.connect("None")
    # 在 cwd 生成名为 None 的脏文件。fce 系 store 均内部解析默认（无此风险）。
    if exam_db_path is None:
        exam_db_path = default_exam_db_path()
    kbq = Query(open_db(db_path))
    exams = ExamStore(exam_db_path)
    users = UserStore()
    fce_papers = FcePaperStore(fce_db_path)
    fce_submissions = FceSubmissionStore(fce_db_path)
    reading = ReadingStore(fce_db_path)
    analytics_ai = AnalyticsAI(exam_db_path)
    recite = ReciteStore(fce_db_path)
    focus = FocusStore(fce_db_path)
    topics = TopicStore(fce_db_path)
    # 计划表（教师周计划）：fce.db 同库存 tasks/notes，完成度实时聚合多库
    plan = PlanStore(fce_db_path, exam_db_path=exam_db_path)
    # 词汇级别考试：成绩登记（fce.db）；L0 恒开，L{n} 考试 ≥80 解锁 L{n+1}
    vocab_exam = VocabExamStore(fce_db_path)
    # 泛读馆（整本书英文泛读）：路径解析沿 fce_query 模式
    # （GRAMMAR_KB_LIBRARY_DB/GRAMMAR_KB_LIBRARY_DIR → data/）
    library = LibraryStore()
    prep_queue = PrepQueue(library)

    # 派生文范读 WAV 目录（{article_id}.wav）：TTS 导出后部署放置，不入
    # git。默认随代码仓 data/audio/reading/，生产可用 GRAMMAR_KB_AUDIO_DIR 改指
    audio_dir = _P(os.environ.get("GRAMMAR_KB_AUDIO_DIR")
                   or _P(__file__).resolve().parent.parent / "data" / "audio" / "reading")

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
    _AUTH_OPEN = frozenset({"/", "/api-info", "/docs", "/redoc", "/openapi.json", "/auth/login"})
    # 静态前端资源（web/dist 挂载时）：HTML/JS/CSS/字体免登录
    _STATIC_PREFIXES = ("/assets/", "/favicon", "/vite.svg")
    # 学生角色可访问的 (method, path 前缀)：背单词所需数据 + 提交成绩 + FCE 真题练习
    _STUDENT_ALLOW = (
        ("GET", "/stats"),
        ("GET", "/vocabulary"),
        ("GET", "/vocab-levels"),  # 分层词库（背单词 L0-L7；解锁由词汇考试驱动）
        ("GET", "/vocab-exams"),  # 学生看自己的考试状态（已过级别/解锁到哪级）
        ("GET", "/dict/"),
        ("GET", "/exams"),
        ("POST", "/exams"),
        ("GET", "/homework"),
        ("GET", "/fce-papers"),
        ("GET", "/fce-papers/"),  # 含 /{id}/audio（听力音频，师生可听）
        ("POST", "/fce-submissions"),
        ("GET", "/fce-submissions"),
        ("GET", "/reading/articles"),
        ("GET", "/reading/audio"),
        ("GET", "/reading/recordings"),
        ("POST", "/reading/recordings"),
        ("POST", "/recite/sessions"),
        ("GET", "/recite/sessions"),
        ("GET", "/recite/wrongbook"),  # 错词本（学生只看自己的）
        ("GET", "/recite/progress"),  # 云端逐词进度（出题与看板数据源）
        ("POST", "/recite/progress/sync"),  # 本地历史一次性上云合并
        ("POST", "/focus/sessions"),
        ("GET", "/focus/sessions"),  # /{id} 详情学生仍被端点内 _require_teacher 拦 403
        # 专题学习（学生自学手册进度；/topics/{id}/progress 学生只能写自己的行）
        ("GET", "/topics/"),
        ("PUT", "/topics/"),
        # 泛读馆（结尾斜杠不可省：前缀匹配语义）——上传 POST /library/books 本体不带斜杠，仍仅教师
        ("GET", "/library/books"),
        ("GET", "/library/books/"),
        ("GET", "/library/dict/"),
        ("GET", "/library/prep/"),
        ("GET", "/library/settings"),
        ("POST", "/library/books/"),  # 放行 /{id}/open（skip 等教师端点内有 _require_teacher 兜底）
        ("PUT", "/library/books/"),   # 放行 /{id}/progress（skip 端点内有 _require_teacher 兜底）
    )

    @app.middleware("http")
    async def _auth(request, call_next):  # noqa: ANN001
        # CORS 预检放行：本中间件在 CORSMiddleware 外层（后添加者更外），
        # 而浏览器预检 OPTIONS 按规范不带 Authorization——不先放行会被 401
        # 挡死，CORS 等于失效。预检由 CORSMiddleware 直接短路，不会触达端点。
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path.rstrip("/") or "/"
        if path in _AUTH_OPEN or path.startswith(_STATIC_PREFIXES):
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
    @app.get("/api-info", include_in_schema=False)
    def root():
        """服务信息（原 / 路径，静态前端部署时 / 让给 web/dist）。"""
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
        """查任意单词（ECDICT 全量词典，不限于讲义语料）。

        所有格归一：children's / childrens' / dogs' → 原词查询。
        """
        import re as _re

        w = word.strip()
        w = _re.sub(r"(?:['’]s|s['’])$", lambda m: "s" if m.group(0).startswith("s") else "", w)
        entry = kbq.dict_lookup(w)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"词典未收录：{word}")
        return _ok(entry)

    @app.get("/vocabulary")
    def vocabulary(limit: int = 300, min_freq: int = 2):
        """基于讲义语料的单词表（释义/词性/词形变化/来源）。"""
        return _ok(kbq.vocabulary(limit=limit, min_freq=min_freq))

    @app.get("/vocab-levels")
    def vocab_levels(request: "fastapi.Request", max_level: int = 7):
        """分层词库（vocab_word 表）：单词 + L0-L7 级别 + 释义/例句。

        L0=哈一课本高频词（与 /vocabulary min_freq=2 同源，带语料增强）；
        L1-L7 为外部词表增量（孩子侧只显示 L 编号，级别名不下发）。
        max_level 截断层数（如 5 = L0-L5）。
        """
        req_level = max(0, min(7, int(max_level)))
        # 学生端解锁规则（2026-09-26 起，取代教师手动 vocabUnlock）：
        # L0 恒开；L{n} 级考卷 ≥80 分（教师登记）自动解锁 L{n+1}。
        # counts 始终按 req_level 给全量（锁定层也要显示词数），items 才截断。
        max_level = req_level
        if request.state.role != "teacher":
            max_level = min(req_level, vocab_exam.unlocked_level(_request_user(request)))
        import json as _json

        def _j(s, dft):
            try:
                return _json.loads(s or dft)
            except (ValueError, TypeError):
                return _json.loads(dft)

        with kbq.db.conn as conn:
            counts_all = dict(
                (lv, n)
                for lv, n in conn.execute(
                    "SELECT level, COUNT(*) FROM vocab_word WHERE level <= ?"
                    " GROUP BY level",
                    (req_level,),
                ).fetchall()
            )
            rows = conn.execute(
                "SELECT word, level, pos, gloss, meanings, example, extra"
                " FROM vocab_word WHERE level <= ?",
                (max_level,),
            ).fetchall()
        items = [{
            "word": r["word"],
            "level": r["level"],
            "pos": _j(r["pos"], "[]"),
            "gloss": r["gloss"] or "",
            "meanings": _j(r["meanings"], "[]"),
            "example": _j(r["example"], "{}"),
            "extra": _j(r["extra"], "{}"),
        } for r in rows]
        # L0 按语料词频降序（跟「背单词」原有顺序一致），L1+ 按字母序
        items.sort(key=lambda x: (
            x["level"],
            -(int(x["extra"].get("freq") or 0)) if x["level"] == 0 else x["word"],
            x["word"],
        ))
        return _ok({"counts": counts_all, "unlocked": max_level, "items": items})

    # ---- 词汇级别考试（线下考卷 + 成绩登记；考试驱动解锁） ----

    @app.get("/vocab-exams")
    def vocab_exams_list(request: "fastapi.Request", user: Optional[str] = None):
        """词汇考试记录（师生可见：学生看自己的，教师看全部/指定 user）。"""
        u = user if (request.state.role == "teacher" and user) else request.state.user
        return _ok({
            "exams": vocab_exam.list(u),
            "passed_levels": sorted(vocab_exam.passed_levels(u)),
            "unlocked_level": vocab_exam.unlocked_level(u),
            "pass_score": 80,
        })

    @app.post("/vocab-exams")
    def vocab_exam_add(payload: dict, request: "fastapi.Request"):
        """登记一次词汇考试成绩（教师专属）。≥80 分自动解锁下一级。"""
        _require_teacher(request)
        try:
            user = str(payload.get("user", "")).strip()
            level = int(payload.get("level"))
            score = int(payload.get("score"))
            exam_date = str(payload.get("exam_date", "")).strip()
            note = str(payload.get("note", "") or "")
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="user/level/score/exam_date 须齐全")
        if not user:
            raise HTTPException(status_code=422, detail="user 须指定学生账号")
        try:
            rec = vocab_exam.record(user, level, score, exam_date, note)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return _ok({
            "exam": rec,
            "unlocked_level": vocab_exam.unlocked_level(user),
            "passed": score >= 80,
        })

    @app.delete("/vocab-exams/{exam_id}")
    def vocab_exam_del(exam_id: int, request: "fastapi.Request"):
        """删一条考试记录（教师专属，误登记用）。"""
        _require_teacher(request)
        with vocab_exam._connect() as conn:
            cur = conn.execute("DELETE FROM vocab_exams WHERE id = ?", (exam_id,))
        if not cur.rowcount:
            raise HTTPException(status_code=404, detail=f"记录 id={exam_id} 不存在")
        return _ok({"id": exam_id})

    @app.get("/vocab-exams/paper", response_class=None)
    def vocab_exam_paper(request: "fastapi.Request", level: int = 0, seed: Optional[int] = None):
        """生成 L{level} 级考卷 HTML（教师打印用；学生 403）。"""
        _require_teacher(request)
        try:
            paper = build_paper(max(0, min(5, int(level))), seed=seed)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        user = request.query_params.get("student", "")
        return _html_response(render_paper_html(paper, student=user))

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
    def fce_submission_detail(sub_id: int, request: "fastapi.Request"):
        """提交详情（含逐题对错明细）。学生只能看自己的，防越权读他人提交。"""
        data = fce_submissions.get(sub_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"提交记录 id={sub_id} 不存在")
        if request.state.role != "teacher" and data["user"] != request.state.user:
            raise HTTPException(status_code=403, detail="只能查看自己的练习提交")
        return _ok(data)

    @app.put("/fce-submissions/{sub_id}")
    def fce_grade(sub_id: int, rec: FceGradeIn):
        """教师批改作文（打分 + 评语）。"""
        data = fce_submissions.grade(sub_id, rec.teacher_score, rec.teacher_comment)
        if data is None:
            raise HTTPException(status_code=404, detail=f"提交记录 id={sub_id} 不存在")
        return _ok(data)

    @app.delete("/fce-submissions/{sub_id}")
    def fce_delete(sub_id: int, request: "fastapi.Request"):
        """教师删除一条 FCE 练习提交。"""
        if getattr(request.state, "role", "teacher") != "teacher":
            raise HTTPException(status_code=403, detail="该功能仅教师账号可用")
        if not fce_submissions.delete(sub_id):
            raise HTTPException(status_code=404, detail=f"提交记录 id={sub_id} 不存在")
        return _ok({"id": sub_id})

    # ---- 阅读训练（base 原文段 + derived 派生文 + 录音提交/批改） ----

    @app.get("/reading/articles")
    def reading_articles(request: "fastapi.Request", kind: Optional[str] = None):
        """文章列表。默认只返回派生文（阅读练习用）；教师传 kind=base 看原文段。"""
        is_teacher = getattr(request.state, "role", "teacher") == "teacher"
        if kind == "base":
            if not is_teacher:
                raise HTTPException(status_code=403, detail="原文段落列表仅教师可查")
            return _ok(reading.list_articles(kind="base"))
        if kind == "all" and is_teacher:
            return _ok(reading.list_articles(kind=None))
        return _ok(reading.list_articles(kind="derived"))

    @app.get("/reading/articles/{article_id}")
    def reading_article_detail(article_id: int, request: "fastapi.Request"):
        """文章正文（has_audio 标记是否备有范读 WAV）。学生访问 base 原文返回 403。"""
        data = reading.get_article(article_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"文章 id={article_id} 不存在")
        if data["kind"] == "base" and getattr(request.state, "role", "teacher") != "teacher":
            raise HTTPException(status_code=403, detail="原文段落仅教师可读，学生请练习派生文章")
        data["has_audio"] = (audio_dir / f"{article_id}.wav").is_file()
        return _ok(data)

    @app.get("/reading/audio/{article_id}")
    def reading_article_audio_file(article_id: int):
        """派生文范读 WAV 文件流。"""
        path = audio_dir / f"{article_id}.wav"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="该文章暂无范读音频")
        return FileResponse(path, media_type="audio/wav")

    # ---- FCE 听力音频（文件到位即生效；占位目录 data/audio/fce/） ----
    # 命名约定：data/audio/fce/{test_id}/{paper}_{part}.mp3（paper 简写，
    # 如 listening_p1.mp3）；404=音频未就绪，前端显示占位提示。
    fce_audio_dir = _P(os.environ.get("GRAMMAR_KB_FCE_AUDIO_DIR")
                       or _P(__file__).resolve().parent.parent / "data" / "audio" / "fce")

    @app.get("/fce-papers/{test_id}/audio/{key}")
    def fce_audio_file(test_id: int, key: str):
        """FCE 听力音频文件流（key 如 listening_p1；师生均可听）。"""
        safe = _re.sub(r"[^a-z0-9_]", "", key.lower())
        if not safe:
            raise HTTPException(status_code=422, detail="非法音频 key")
        path = fce_audio_dir / str(int(test_id)) / f"{safe}.mp3"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="该部分听力音频暂未就绪")
        return FileResponse(path, media_type="audio/mpeg")

    @app.get("/fce-papers/{test_id}/audio")
    def fce_audio_status(test_id: int):
        """某 Test 已就绪的听力音频 key 列表（前端按 key 显隐播放器）。"""
        d = fce_audio_dir / str(int(test_id))
        if not d.is_dir():
            return _ok({"available": []})
        keys = sorted(p.stem for p in d.glob("*.mp3"))
        return _ok({"available": keys})

    @app.post("/reading/articles")
    def reading_add_derived(rec: ReadingDerivedIn, request: "fastapi.Request"):
        """教师新增派生阅读文（挂到某个 base 段）。"""
        _require_teacher(request)
        try:
            return _ok(reading.add_derived(rec.base_key, rec.title, rec.text, rec.source))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.put("/reading/articles/{article_id}")
    def reading_update_derived(
        article_id: int, rec: ReadingUpdateIn, request: "fastapi.Request"
    ):
        """教师编辑派生阅读文。"""
        _require_teacher(request)
        data = reading.update_derived(
            article_id, rec.title, rec.text, rec.source, rec.base_key
        )
        if data is None:
            raise HTTPException(status_code=404, detail=f"派生文章 id={article_id} 不存在")
        return _ok(data)

    @app.delete("/reading/articles/{article_id}")
    def reading_delete_derived(article_id: int, request: "fastapi.Request"):
        """教师删除派生阅读文（base 原文段不可删）。"""
        _require_teacher(request)
        if not reading.delete_derived(article_id):
            raise HTTPException(status_code=404, detail=f"派生文章 id={article_id} 不存在")
        return _ok({"id": article_id})

    @app.post("/reading/recordings")
    def reading_submit_recording(rec: ReadingRecordIn, request: "fastapi.Request"):
        """学生提交阅读录音（一篇文章录一段）。"""
        user = _request_user(request)
        try:
            return _ok(reading.submit_recording(
                user, rec.article_id, rec.audio_b64, rec.mime, rec.duration_sec,
                selected_text=rec.selected_text,
            ))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e).strip("'"))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.get("/reading/recordings")
    def reading_recordings(
        request: "fastapi.Request", user: Optional[str] = None,
        status: Optional[str] = None, limit: int = 100,
    ):
        """录音列表。学生只看自己的；教师可看全部（?status=pending 拉待批改）。"""
        if request.state.role != "teacher":
            user = request.state.user
        return _ok(reading.list_recordings(user=user, status=status, limit=limit))

    @app.get("/reading/recordings/{rec_id}")
    def reading_recording_detail(rec_id: int, request: "fastapi.Request"):
        """录音详情（含 base64 音频，供播放）。学生只能听自己的。"""
        data = reading.get_recording(rec_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"录音 id={rec_id} 不存在")
        if request.state.role != "teacher" and data["user"] != request.state.user:
            raise HTTPException(status_code=403, detail="只能查看自己的录音")
        return _ok(data)

    @app.put("/reading/recordings/{rec_id}")
    def reading_grade_recording(rec_id: int, rec: ReadingGradeIn, request: "fastapi.Request"):
        """教师给录音打分（10 分制 + 评语）。"""
        _require_teacher(request)
        try:
            data = reading.grade_recording(rec_id, rec.score, rec.comment)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if data is None:
            raise HTTPException(status_code=404, detail=f"录音 id={rec_id} 不存在")
        return _ok(data)

    @app.delete("/reading/recordings/{rec_id}")
    def reading_delete_recording(rec_id: int, request: "fastapi.Request"):
        """教师删除一条录音提交。"""
        _require_teacher(request)
        if not reading.delete_recording(rec_id):
            raise HTTPException(status_code=404, detail=f"录音 id={rec_id} 不存在")
        return _ok({"id": rec_id})

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
        """背单词错词本：聚合学生会话错词（错次/最近错时间）。学生只看自己的。"""
        u = _request_user(request)
        if request.state.role != "teacher":
            u = _request_user(request)
        elif user:
            u = user
        return _ok(recite.wrongbook(u, limit=limit))

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
    ):
        """专注力会话列表（不含轨迹/鼠标明细）。学生只看自己的；教师看全部。"""
        if request.state.role != "teacher":
            user = request.state.user
        return _ok(focus.list(user=user, limit=limit))

    @app.get("/focus/sessions/{row_id}")
    def focus_detail(row_id: int, request: "fastapi.Request"):
        """专注力会话详情（含鼠标指标与进度折线）。教师专属。"""
        _require_teacher(request)
        data = focus.get(row_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"会话 id={row_id} 不存在")
        return _ok(data)

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

    # ---- 计划表（教师专属：周维度计划 + 实时完成度 + 上周回顾） ----

    @app.get("/plan/weeks")
    def plan_weeks(request: "fastapi.Request", user: str = "malin"):
        """全部周计划 + 每周实时完成度 + 当前周的上周回顾。教师专属。"""
        _require_teacher(request)
        weeks = plan.list_weeks()
        for w in weeks:
            w["actuals"] = plan.week_actuals(w["week_start"], user=user)
        from datetime import date as _date, timedelta as _td
        cur_mon = (_date.today() - _td(days=_date.today().weekday())).isoformat()
        return _ok({
            "weeks": weeks,
            "current_week": cur_mon,
            "last_week_review": plan.last_week_review(cur_mon, user=user),
        })

    @app.put("/plan/weeks/{week_start}")
    def plan_week_put(week_start: str, payload: dict, request: "fastapi.Request"):
        """保存一周的 tasks/notes（教师专属，幂等覆盖）。"""
        _require_teacher(request)
        tasks = payload.get("tasks") if isinstance(payload, dict) else None
        notes = payload.get("notes", "") if isinstance(payload, dict) else ""
        if tasks is None or not isinstance(tasks, dict):
            raise HTTPException(status_code=422, detail="payload.tasks 须为对象")
        try:
            return _ok(plan.put_week(week_start, tasks, str(notes or "")))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    # ---- 学情分析 · AI 周报（手动触发，分析上一自然周；教师专属） ----

    @app.get("/analytics/ai/reports")
    def analytics_ai_reports(request: "fastapi.Request", limit: int = 12):
        """已生成的 AI 周报列表（最近优先）。"""
        _require_teacher(request)
        return _ok({"reports": analytics_ai.list_reports(limit=limit)})

    @app.post("/analytics/ai/weekly")
    def analytics_ai_weekly(request: "fastapi.Request"):
        """手动触发生成上周学情分析：聚合四类作业 + 前四周基线 → GLM → 存档。

        模型与 Key 复用泛读馆设置（library_settings.ai_json）；按周覆盖存档。
        """
        from . import glm as _glm

        _require_teacher(request)
        settings = library.get_settings()
        try:
            report = analytics_ai.generate_weekly(
                settings,
                stores={
                    "exams": exams,
                    "fce_submissions": fce_submissions,
                    "reading": reading,
                    "recite": recite,
                },
            )
        except _glm.GlmError as e:
            raise HTTPException(status_code=getattr(e, "status", 502), detail=str(e))
        return _ok(report)

    def _require_teacher(request) -> None:
        if getattr(request.state, "role", "teacher") != "teacher":
            raise HTTPException(status_code=403, detail="该功能仅教师账号可用")

    def _to_int(v, default=None):
        """宽松 int 转换（body 里可能是 int/str/None）。"""
        if v is None or isinstance(v, bool):
            return default
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    # ---- 作业成绩（可写；独立 exam.db） ----

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

    # ---- 泛读馆（整本书英文泛读：书架 / epub 导入 / AI 章节预习 / 查词 / 进度） ----
    # 注意：/library/prep/batch 系列具名路由必须注册在参数路由
    # /library/prep/{book_id}/{chapter_index} 之前，否则 "batch" 会被当 bookId 吞掉。
    from fastapi.responses import FileResponse

    def _require_teacher_lib(request) -> None:
        """泛读馆教师端点兜底（中间件是粗闸门，端点是细闸门，两层都要有）。"""
        _require_teacher(request)

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
    def lib_prep_get(book_id: int, chapter_index: int):
        """读某章预习（师生可读）。未生成返回 data=null（区别于参考实现直接 404）。"""
        payload = library.get_prep(book_id, chapter_index)
        return _ok(payload)

    @app.get("/library/books")
    def lib_books_list(request: "fastapi.Request"):
        """书架 + 当前用户阅读统计。"""
        return _ok(library.list_books(_request_user(request)))

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
    def lib_book_file(book_id: int):
        got = library.get_file(book_id)
        if got is None:
            raise HTTPException(status_code=404, detail="书籍不存在或文件丢失")
        return FileResponse(got[0], media_type=got[1])

    @app.get("/library/books/{book_id}/cover")
    def lib_book_cover(book_id: int):
        got = library.get_cover(book_id)
        if got is None:
            raise HTTPException(status_code=404, detail="无封面")
        return FileResponse(got[0], media_type=got[1])

    @app.get("/library/books/{book_id}/chapters")
    def lib_book_chapters(book_id: int):
        chapters = library.list_chapters(book_id)
        if chapters is None:
            raise HTTPException(status_code=404, detail="书籍不存在")
        return _ok({"chapters": chapters})

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
    def lib_book_open(book_id: int):
        """记录"打开书"行为（更新 last_opened_at）。"""
        library.touch_open(book_id)
        return _ok({"ok": True})

    @app.get("/library/books/{book_id}/progress")
    def lib_progress_get(book_id: int, request: "fastapi.Request"):
        """当前用户在某书的阅读进度（未开始返回 data=null）。"""
        return _ok(library.get_progress(book_id, _request_user(request)))

    @app.put("/library/books/{book_id}/progress")
    def lib_progress_put(book_id: int, rec: dict, request: "fastapi.Request"):
        """上报阅读进度：percent 夹 0-100、delta 夹 0-3600，UPSERT。"""
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
        is_teacher = getattr(request.state, "role", "teacher") == "teacher"
        return _ok(library.settings_view(is_teacher))

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
        from . import glm as _glm

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
        from . import glm as _glm

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


    # ---- 静态前端（可选）：web/dist 构建产物由本进程直接服务 ----
    # 前端以 /api 调后端；开发期走 Vite 代理（剥前缀），部署期由下面
    # 的中间件承担同样的剥前缀工作，无需 nginx。GRAMMAR_KB_STATIC=0 关闭。
    web_dist = _P(__file__).resolve().parent.parent / "web" / "dist"
    serve_static = os.environ.get("GRAMMAR_KB_STATIC", "1") != "0" and web_dist.is_dir()
    if serve_static:

        @app.middleware("http")
        async def _strip_api_prefix(request, call_next):  # noqa: ANN001
            if request.url.path.startswith("/api/"):
                request.scope["path"] = request.url.path[4:]
            return await call_next(request)

        from fastapi.staticfiles import StaticFiles
        from fastapi.responses import FileResponse as _IndexFileResponse

        @app.get("/", include_in_schema=False)
        def _index():
            # 入口页禁缓存：index.html 引用按内容 hash 命名的 JS/CSS，资源可
            # 长缓存，但入口本身若被浏览器启发式缓存，发版后用户会一直加载
            # 旧 bundle（多次「线上已修好、用户仍报旧错」的根源）
            resp = _IndexFileResponse(web_dist / "index.html")
            resp.headers["Cache-Control"] = "no-cache"
            return resp

        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    else:

        @app.get("/", include_in_schema=False)
        def _root_fallback():
            return root()

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
        help="作业成绩库路径（默认 $GRAMMAR_KB_EXAM_DB，否则 data/exam.db）",
    )
    args = parser.parse_args()
    run_app(host=args.host, port=args.port, db_path=args.db, exam_db_path=args.exam_db)


if __name__ == "__main__":
    main()

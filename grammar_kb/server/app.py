"""FastAPI 应用组装。

路由按领域拆在 ``routes_*.py``。``create_app`` / ``app`` / ``run_app`` / ``main``
的导入路径与拆分前的 ``grammar_kb.server`` 模块一致。
"""
from __future__ import annotations

import os
from pathlib import Path as _P
from typing import Optional

from .. import __version__, dbreg
from ..ingest import open_db
from ..paths import ROOT
from ..query import Query
from .ctx import AppCtx
from .http import _ok
from .routes_atlas import register_atlas
from .routes_library import register_library
from .routes_plan import register_plan
from .routes_practice import register_practice
from .routes_query import register_query
from .routes_study import register_study
from .routes_video import register_video
from .routes_vocab import register_vocab
from .schemas import LoginIn

_GIT_SHA: Optional[str] = None


def _git_short() -> str:
    """当前检出的短 SHA。无 git（打包镜像、浅拷贝）时返回空串，结果进程内缓存。"""
    global _GIT_SHA
    if _GIT_SHA is not None:
        return _GIT_SHA
    import subprocess

    sha = ""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        sha = out.decode().strip()
    except (OSError, subprocess.SubprocessError):
        sha = ""
    _GIT_SHA = sha
    return sha


def create_app(db_path: Optional[str] = None, exam_db_path: Optional[str] = None,
              fce_db_path: Optional[str] = None):
    """构造 FastAPI 应用。``db_path`` 为 None 时走默认库（GRAMMAR_KB_DB 或 data/grammar.db）；
    ``exam_db_path`` 为成绩库路径（默认 data/exam.db）。"""
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    from ..exam_store import ExamStore, default_exam_db_path
    from ..auth import UserStore, make_token, read_token
    from ..fce_query import FcePaperStore, FceSubmissionStore
    from ..library import LibraryStore
    from ..prep_queue import PrepQueue
    from ..reading import ReadingStore
    from ..recite import ReciteStore
    from ..focus import FocusStore
    from ..topics import TopicStore
    from ..video_study import VideoStore
    from ..plan import PlanStore
    from ..vocab_exam import VocabExamStore
    from ..analytics_ai import AnalyticsAI

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
    # 视频学习（看视频 + 复述录音批改）：fce.db 同库
    video_study = VideoStore(fce_db_path)
    # 计划表（教师周计划）：fce.db 同库存 tasks/notes，完成度实时聚合多库
    plan = PlanStore(fce_db_path, exam_db_path=exam_db_path,
                   grammar_db_path=db_path)
    # 作业卷管理（grammar.db：题库 answer 列 + 历次逐题对错）
    from ..homework_admin import HomeworkAdmin
    from .. import readingmore as _readingmore
    homework_admin = HomeworkAdmin()
    # 词汇级别考试：成绩登记（fce.db）。单词级别可由教师直接设定；
    # 未设定时 L0 恒开，L{n} 考试 ≥80 解锁 L{n+1}
    vocab_exam = VocabExamStore(fce_db_path)
    # 泛读馆（整本书英文泛读）：路径解析沿 fce_query 模式
    # （GRAMMAR_KB_LIBRARY_DB/GRAMMAR_KB_LIBRARY_DIR → data/）
    library = LibraryStore()
    prep_queue = PrepQueue(library)

    # 派生文范读 WAV 目录（{article_id}.wav）：TTS 导出后部署放置，不入
    # git。默认随代码仓 data/audio/reading/，生产可用 GRAMMAR_KB_AUDIO_DIR 改指
    audio_dir = _P(dbreg.audio_reading_dir())

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

    # ---- 响应压缩：JS/CSS/JSON gzip（bundle 770K → ~250K，2C1G 小机与
    # 公网带宽下的首屏主瓶颈）。音频 wav/mp3 已压缩格式，最低阈值跳过
    from fastapi.middleware.gzip import GZipMiddleware
    app.add_middleware(GZipMiddleware, minimum_size=1024)

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
    _AUTH_OPEN = frozenset({
        "/", "/api-info", "/version", "/docs", "/redoc", "/openapi.json", "/auth/login",
    })
    # 静态前端资源（web/dist 挂载时）：HTML/JS/CSS/字体免登录
    _STATIC_PREFIXES = ("/assets/", "/favicon", "/vite.svg",
                        "/analytics-report.html",  # 学情静态报告（public 资源）
                        "/readingmore",  # 杂志阅读器静态页（API 另走 Bearer 鉴权）
                        # 词光插画/录音：<img>/<audio> 带不上 Authorization。
                        # 内容是 CC BY 公开素材，不是学生进度。
                        "/atlas-media",)
    # 学生角色可访问的 (method, path 前缀)：背单词所需数据 + 提交成绩 + FCE 真题练习
    _STUDENT_ALLOW = (
        ("GET", "/stats"),
        ("GET", "/vocabulary"),
        ("GET", "/vocab-levels"),  # 分层词库（背单词；学生可见范围=教师设定的单词级别）
        ("GET", "/atlas/"),  # 词光词条（插画/录音/例句）；媒体文件走 /atlas-media 免登录
        ("GET", "/atlas-info"),
        ("GET", "/vocab-exams"),  # 学生看自己的考试状态（已过级别/解锁到哪级）
        ("GET", "/dict/"),
        ("GET", "/exams"),
        ("POST", "/exams"),
        ("GET", "/homework"),
        ("GET", "/fce-papers"),
        ("GET", "/fce-papers/"),  # 含 /{id}/audio、/{id}/listening-vocab（听力音频与预习词表）
        ("POST", "/fce-submissions"),
        ("GET", "/fce-submissions"),
        ("GET", "/reading/articles"),
        ("GET", "/reading/audio"),
        ("GET", "/reading/recordings"),
        ("POST", "/reading/recordings"),
        ("POST", "/recite/sessions"),
        ("GET", "/recite/sessions"),
        ("GET", "/recite/wrongbook"),  # 错词本（学生只看自己的）
        ("GET", "/recite/conquered"),  # 已攻克词本分页
        ("GET", "/recite/gloss"),  # 开练前按需补释义（最多 40 词）
        ("GET", "/recite/review-summary"),  # 复习闭环摘要（到期/逾期/攻克/新词额度）
        ("GET", "/recite/progress"),  # 云端逐词进度（出题与看板数据源）
        ("POST", "/recite/progress/sync"),  # 本地历史一次性上云合并
        # 内循环（双循环教学）：学生今日任务/周清单/打卡（user 口径在端点内收窄）
        ("GET", "/plan/today"),
        ("GET", "/plan/week-todo"),
        ("GET", "/plan/my-week"),  # 学生课程表（只读自己本周计划）
        ("GET", "/plan/rules"),  # 完成判定规则（学生只读自己的）
        ("POST", "/plan/day-check"),
        ("POST", "/focus/sessions"),
        ("GET", "/focus/sessions"),  # /{id} 详情学生仍被端点内 _require_teacher 拦 403
        # 专题学习（学生自学手册进度；/topics/{id}/progress 学生只能写自己的行）
        ("GET", "/topics/"),
        ("GET", "/topics-v2"),
        ("PUT", "/topics/"),
        # 泛读馆（结尾斜杠不可省：前缀匹配语义）——上传 POST /library/books 本体不带斜杠，仍仅教师
        ("GET", "/library/books"),
        ("GET", "/library/books/"),
        ("GET", "/library/dict/"),
        ("GET", "/library/prep/"),
        ("GET", "/library/settings"),
        ("POST", "/library/books/"),  # 放行 /{id}/open（skip 等教师端点内有 _require_teacher 兜底）
        ("PUT", "/library/books/"),   # 放行 /{id}/progress（skip 端点内有 _require_teacher 兜底）
        # 录音音频二进制（<audio src> 直连；端点内校验只能听自己的）
        ("GET", "/reading/recordings/"),
        # 阅读更多（杂志阅读器统计）：心跳上报 + 自己的聚合数据
        ("POST", "/readingmore/api/session"),
        ("GET", "/readingmore/api/stats"),
        # 视频学习
        ("GET", "/video-study"),
        ("GET", "/video-study/items/"),
        ("POST", "/video-study/retells"),
        ("GET", "/video-study/retells"),
        ("GET", "/video-study/retells/"),
    )

    @app.middleware("http")
    async def _auth(request, call_next):  # noqa: ANN001
        # CORS 预检放行：本中间件在 CORSMiddleware 外层（后添加者更外），
        # 而浏览器预检 OPTIONS 按规范不带 Authorization——不先放行会被 401
        # 挡死，CORS 等于失效。预检由 CORSMiddleware 直接短路，不会触达端点。
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path.rstrip("/") or "/"
        if path in _AUTH_OPEN or (
            path.startswith(_STATIC_PREFIXES)
            # 阅读更多：静态页免登录，但 /readingmore/api/* 必须走 Bearer 鉴权
            and not path.startswith("/readingmore/api/")
        ):
            return await call_next(request)
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else ""
        # 二进制媒体端点（如 /reading/recordings/{id}/audio、
        # /fce-papers/{id}/audio/{key}）由 <audio src> 直连——HTML 媒体标签
        # 无法带 Authorization 头，token 走查询参数。仅对 /audio 结尾或
        # /audio/ 下属文件的只读媒体端点开放此通道。
        if not token and ("/audio" in path):
            tail = path.split("/audio", 1)[1]
            if tail == "" or tail.startswith("/"):
                token = request.query_params.get("token", "")
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

    @app.post("/auth/login")
    def auth_login(rec: LoginIn):
        role = users.verify(rec.user, rec.password)
        if role is None:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        return _ok({"user": rec.user, "role": role, "token": make_token(rec.user, role)})

    # 阅读更多（杂志阅读器）：API 走统一 Bearer 鉴权，用户身份由
    # _auth 中间件注入 request.state.user（模块内取，数据按用户隔离）
    _readingmore._init_db()
    app.include_router(_readingmore._router)

    # ---- 端点 ----
    @app.get("/version", include_in_schema=False)
    def app_version():
        """服务端版本与 git 短 SHA。免登录，供设置中心对照前端构建版本。"""
        return _ok({
            "service": "grammar-kb",
            "version": __version__,
            "git": _git_short(),
        })

    @app.get("/api-info", include_in_schema=False)
    def root():
        """服务信息（原 / 路径，静态前端部署时 / 让给 web/dist）。"""
        return _ok(
            {
                "service": "grammar-kb",
                "version": __version__,
                "git": _git_short(),
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

    ctx = AppCtx(
        kbq=kbq, exams=exams, users=users, fce_papers=fce_papers,
        fce_submissions=fce_submissions, reading=reading,
        analytics_ai=analytics_ai, recite=recite, focus=focus, topics=topics,
        videos=video_study,
        plan=plan, homework_admin=homework_admin, vocab_exam=vocab_exam,
        library=library, prep_queue=prep_queue, audio_dir=audio_dir,
    )
    # 注册顺序与拆分前 create_app 内的定义顺序一致（路径重叠时先注册者优先）
    register_query(app, ctx)
    register_atlas(app, ctx)
    register_vocab(app, ctx)
    register_practice(app, ctx)
    register_study(app, ctx)
    register_plan(app, ctx)
    register_video(app, ctx)
    register_library(app, ctx)

    # ---- 静态前端（可选）：web/dist 构建产物由本进程直接服务 ----
    # 前端以 /api 调后端；开发期走 Vite 代理（剥前缀），部署期由下面
    # 的中间件承担同样的剥前缀工作，无需 nginx。GRAMMAR_KB_STATIC=0 关闭。
    web_dist = ROOT / "web" / "dist"
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

        # /readingmore（无尾斜杠）不在此显式服务 HTML：页面内 assets/、dict/、
        # PDF 全是相对路径，必须由 StaticFiles 原生 307 → /readingmore/ 后才
        # 能正确解析（曾直接回 index.html 导致 /assets/pdf.min.js 404、
        # pdfjsLib is not defined）。/readingmore/ 的 no-cache 由 _CachedStatic
        # 的 .html 统一规则覆盖

        # 静态资源长缓存：vite 产物文件名带内容 hash（index-xxxx.js），
        # 内容变则名变——可安全 immutable 一年；index.html 已显式 no-cache
        from starlette.staticfiles import StaticFiles as _SM, NotModifiedResponse as _NMR  # noqa: F401
        _web_mount = StaticFiles(directory=web_dist, html=True)

        class _CachedStatic(_web_mount.__class__):
            def file_response(self, full_path, *args, **kwargs):  # noqa: ANN002, ANN003
                resp = super().file_response(full_path, *args, **kwargs)
                # HTML 无 hash 文件名（含 /readingmore/、analytics-report.html），
                # immutable 一年会让用户发版后一直加载旧页面——一律 no-cache；
                # 带 hash 的 JS/CSS/PDF 等其余资源仍长缓存。304 无响应体，
                # 缓存语义跟随首次 200，无需特判
                if str(full_path).endswith(".html"):
                    resp.headers["Cache-Control"] = "no-cache"
                else:
                    resp.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
                return resp

        app.mount("/", _CachedStatic(directory=web_dist, html=True), name="web")
    else:

        @app.get("/", include_in_schema=False)
        def _root_fallback():
            return root()

    return app


# 模块级单例（供 `uvicorn grammar_kb.server:app` 使用，走默认库）
try:
    app = create_app()
except Exception:  # 未安装 fastapi 或默认库不可用时仍可 import 本模块
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

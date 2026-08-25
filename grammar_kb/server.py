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

except ImportError:  # 未装 fastapi/pydantic 时仍可 import 本模块
    ExamRecordIn = None


def create_app(db_path: Optional[str] = None, exam_db_path: Optional[str] = None):
    """构造 FastAPI 应用。``db_path`` 为 None 时走默认库（GRAMMAR_KB_DB 或 data/grammar.db）；
    ``exam_db_path`` 为成绩库路径（默认 iCloud Drive 或 data/exam.db）。"""
    from fastapi import FastAPI, HTTPException, Query as FQuery
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    from .exam_store import ExamStore

    kbq = Query(open_db(db_path))
    exams = ExamStore(exam_db_path)
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

    # ---- 端点 ----
    @app.get("/")
    def root():
        return _ok(
            {
                "service": "grammar-kb",
                "version": __version__,
                "endpoints": [
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

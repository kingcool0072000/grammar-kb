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


def create_app(db_path: Optional[str] = None):
    """构造 FastAPI 应用。``db_path`` 为 None 时走默认库（GRAMMAR_KB_DB 或 data/grammar.db）。"""
    from fastapi import FastAPI, HTTPException, Query as FQuery
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    kbq = Query(open_db(db_path))
    from .exam_db import ExamStore

    exam_store = ExamStore()
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

    @app.get("/exams")
    def exams_list():
        """全部作业成绩记录。"""
        return _ok(exam_store.list())

    @app.post("/exams")
    async def exams_add(payload: dict):
        """新增一条作答记录 {lecture, date, score, wrong:[题号]}。"""
        try:
            rec = exam_store.add(
                lecture=int(payload["lecture"]),
                date=str(payload["date"]),
                score=int(payload["score"]),
                wrong=payload.get("wrong") or [],
            )
        except (KeyError, TypeError, ValueError) as e:
            raise HTTPException(status_code=422, detail=f"参数不完整：{e}")
        return _ok(rec)

    @app.delete("/exams/{record_id}")
    def exams_delete(record_id: str):
        """删除一条作答记录。"""
        if not exam_store.delete(record_id):
            raise HTTPException(status_code=404, detail="记录不存在")
        return _ok({"deleted": record_id})

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
) -> None:
    import uvicorn

    uvicorn.run(create_app(db_path), host=host, port=port)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="grammar-kb-server", description="grammar-kb HTTP 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=None, help="数据库路径（默认 data/grammar.db 或 $GRAMMAR_KB_DB）")
    args = parser.parse_args()
    run_app(host=args.host, port=args.port, db_path=args.db)


if __name__ == "__main__":
    main()

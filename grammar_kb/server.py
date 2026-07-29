"""HTTP 服务（可选依赖：``pip install fastapi uvicorn``，或 ``uv sync --extra server``）。

把 :class:`grammar_kb.query.Query` 的能力以 REST API 暴露，与 CLI / MCP 共用同一查询层。

启动：
    grammar-kb-server                     # 默认 127.0.0.1:8000
    grammar-kb-server --port 8080 --host 0.0.0.0
    grammar-kb serve --port 8080          # 经由 CLI
    GRAMMAR_KB_DB=/path/grammar.db grammar-kb-server

启动后访问 http://127.0.0.1:8000/docs 查看交互式 API 文档。
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from . import __version__
from .ingest import open_db
from .query import Query


def create_app(db_path: Optional[str] = None):
    """构造 FastAPI 应用。``db_path`` 为 None 时走默认库（GRAMMAR_KB_DB 或 data/grammar.db）。"""
    from fastapi import FastAPI, HTTPException, Query as FQuery

    kbq = Query(open_db(db_path))
    app = FastAPI(
        title="grammar-kb 题库 API",
        version=__version__,
        description="PDF 讲义/教材知识点库的只读查询服务",
    )

    @app.get("/")
    def root():
        return {
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
                "GET /docs (Swagger UI)",
            ],
        }

    @app.get("/stats")
    def stats():
        return kbq.stats()

    @app.get("/lectures")
    def lectures():
        return [asdict(l) for l in kbq.list_lectures()]

    @app.get("/lectures/{number}")
    def lecture(number: int, format: str = "markdown"):
        content = kbq.lecture_html(number) if format == "html" else kbq.lecture_markdown(number)
        if content is None:
            raise HTTPException(status_code=404, detail=f"第 {number} 讲不存在")
        lec = kbq.get_lecture(number)
        return {
            "number": number,
            "title": lec.title if lec else "",
            "category": lec.category if lec else "",
            "format": format,
            "content": content,
        }

    @app.get("/kp/{kp_id}")
    def kp(kp_id: int, format: str = "markdown"):
        content = kbq.kp_html(kp_id) if format == "html" else kbq.kp_markdown(kp_id)
        if content is None:
            raise HTTPException(status_code=404, detail=f"知识点 id={kp_id} 不存在")
        return {"id": kp_id, "format": format, "content": content}

    @app.get("/search")
    def search(
        q: str = FQuery(..., description="关键词"),
        category: Optional[str] = None,
        limit: int = 20,
    ):
        items = kbq.search_kps(q, category=category, limit=limit)
        return {
            "query": q,
            "category": category,
            "count": len(items),
            "items": [asdict(k) for k in items],
        }

    @app.get("/markers")
    def markers(category: str = "时态", tense: Optional[str] = None):
        rows = kbq.markers_by_tense(tense) if tense else kbq.markers_by_category(category)
        return {"category": category, "tense": tense, "count": len(rows), "items": rows}

    @app.get("/relation")
    def relation(type: str = "主将从现"):
        items = kbq.kps_by_relation(type)
        return {"type": type, "count": len(items), "items": [asdict(k) for k in items]}

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

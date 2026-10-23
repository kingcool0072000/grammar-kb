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

路由实现拆在本包的 ``routes_*.py``；``create_app`` / ``app`` / ``main`` 的导入路径不变。
"""
from __future__ import annotations

from .app import app, create_app, main, run_app
from .http import _html_response, _ok
from .schemas import (
    ExamRecordIn,
    FceGradeIn,
    FceSubmissionIn,
    FocusSessionIn,
    LoginIn,
    ReadingDerivedIn,
    ReadingGradeIn,
    ReadingRecordIn,
    ReadingUpdateIn,
    ReciteSessionIn,
    TopicAssignIn,
    TopicFlagIn,
    TopicImportIn,
    TopicProgressIn,
)

__all__ = [
    "app",
    "create_app",
    "main",
    "run_app",
    "_ok",
    "_html_response",
    "ExamRecordIn",
    "LoginIn",
    "FceSubmissionIn",
    "FceGradeIn",
    "ReadingDerivedIn",
    "ReadingUpdateIn",
    "ReadingRecordIn",
    "ReadingGradeIn",
    "ReciteSessionIn",
    "FocusSessionIn",
    "TopicProgressIn",
    "TopicImportIn",
    "TopicFlagIn",
    "TopicAssignIn",
]

"""PDF 讲义/教材 → 结构化知识点数据库。

把 PDF 清洗（去水印、还原表格）并拆解为可检索、可溯源的知识点，
存入本地 SQLite，提供 CLI 与 MCP 查询。

模块概览：
- pdf_parser   PDF → 清洗后的行/块 + 结构化表格（去水印）
- structure    清洗后文本 → 知识点切分（大纲树）
- classify     讲次/知识点分类（词法/句法/时态…）+ 标志词/关系抽取
- markdown     表格 → GFM markdown；知识点/整讲渲染
- db           SQLite schema + CRUD + FTS5（不截断）
- query        查询 API（按讲次/类别/标志词/全文）
- ingest/cli   导入与命令行
"""
from .models import (
    Lecture,
    KnowledgePoint,
    TableData,
    Marker,
    Relation,
    Block,
    Category,
)

__version__ = "0.2.0"
# 数据集版本（与代码版本解耦）：数据迭代时递增。可用环境变量 GRAMMAR_KB_DATA_VERSION 覆盖。
DATA_VERSION = "data-v1"
__all__ = [
    "Lecture",
    "KnowledgePoint",
    "TableData",
    "Marker",
    "Relation",
    "Block",
    "Category",
]

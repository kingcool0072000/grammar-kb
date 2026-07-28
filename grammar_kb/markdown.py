"""Markdown 渲染：表格 → GFM、知识点渲染、整讲还原。

"还原表格"指：原本是表格的内容（pdfplumber 检测出的 ruled table）
渲染回 GFM 管道表格；"给我第 N 课讲义 md"由 ``render_lecture`` 完成。
"""
from __future__ import annotations

from typing import Iterable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .models import KnowledgePoint, Lecture, Block, TableData


def _escape_pipe(cell: str) -> str:
    """转义单元格里的管道符与换行，避免破坏 GFM 表格结构。"""
    return cell.replace("|", "\\|").replace("\n", " ").strip()


def table_to_markdown(table: "TableData", with_caption: bool = True) -> str:
    """TableData → GFM markdown 表格（含可选 caption）。

    >>> from .models import TableData
    >>> t = TableData(headers=["时态名称","构成"], rows=[["一般现在时","do / does"]])
    >>> print(table_to_markdown(t))
    | 时态名称 | 构成 |
    | --- | --- |
    | 一般现在时 | do / does |
    """
    if not table.headers:
        return ""
    headers = [_escape_pipe(h) for h in table.headers]
    n = len(headers)
    sep = ["---"] * n
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(sep) + " |"]
    for row in table.rows:
        cells = [_escape_pipe(c) for c in row][:n]
        cells += [""] * (n - len(cells))
        lines.append("| " + " | ".join(cells) + " |")
    md = "\n".join(lines)
    if with_caption and table.caption:
        md = f"**{table.caption}**\n\n{md}"
    return md


def render_knowledge_point(kp: "KnowledgePoint", with_source: bool = True) -> str:
    """渲染单个知识点为 markdown（含溯源：第 N 讲 · 页码）。"""
    parts: list[str] = []
    parts.append(f"## {kp.title}")
    meta = [f"第{kp.lecture_number}讲", f"分类：{kp.category}"]
    if kp.section_path:
        meta.append(f"位置：{kp.section_path}")
    if with_source:
        meta.append(f"页码：P{kp.source_page}")
    parts.append(">" + " · ".join(meta))
    parts.append("")
    if kp.body_md:
        parts.append(kp.body_md.strip())
        parts.append("")
    if kp.examples_md:
        parts.append("**例句**")
        parts.append("")
        parts.append(kp.examples_md.strip())
        parts.append("")
    if kp.table_md:
        parts.append(kp.table_md.strip())
        parts.append("")
    if kp.markers:
        parts.append("**标志词**：" + "、".join(m.marker for m in kp.markers))
        parts.append("")
    if kp.relations:
        parts.append("**关系**：" + "；".join(r.type for r in kp.relations))
        parts.append("")
    if kp.tags:
        parts.append("**标签**：" + " ".join(f"#{t}" for t in kp.tags))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def render_block(block: "Block") -> str:
    """渲染一个内容块（整讲还原用）。"""
    if block.kind == "table" and block.table_data:
        return table_to_markdown(block.table_data)
    return block.text_md


def render_lecture(
    lecture: "Lecture",
    blocks: Iterable["Block"],
) -> str:
    """把一讲还原成完整 markdown（标题 + 各块，表格还原为 GFM）。

    对应需求"给我第 N 课讲义 md 格式"。
    """
    parts: list[str] = []
    parts.append(f"# 第{lecture.number}讲 {lecture.title}")
    parts.append("")
    meta = [f"分类：{lecture.category}"]
    if lecture.subcategory:
        meta.append(f"细分：{lecture.subcategory}")
    meta.append(f"页数：{lecture.page_count}")
    parts.append(">" + " · ".join(meta))
    parts.append("")
    parts.append("---")
    parts.append("")
    for block in blocks:
        text = render_block(block).strip()
        if text:
            parts.append(text)
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Markdown → HTML
# --------------------------------------------------------------------------- #

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC",
         "Microsoft YaHei", "Segoe UI", Roboto, sans-serif;
         max-width: 900px; margin: 2rem auto; padding: 0 1rem;
         line-height: 1.75; color: #24292f; }}
  h1 {{ border-bottom: 2px solid #ddd; padding-bottom: .3em; }}
  h2 {{ border-bottom: 1px solid #eee; padding-bottom: .2em; margin-top: 1.8em; }}
  h3 {{ margin-top: 1.4em; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; font-size: .95em; }}
  th, td {{ border: 1px solid #d0d7de; padding: 6px 13px; text-align: left;
           vertical-align: top; }}
  th {{ background: #f6f8fa; font-weight: 600; }}
  tr:nth-child(2n) td {{ background: #fafbfc; }}
  code {{ background: #eff1f3; padding: 1px 5px; border-radius: 4px; font-size: .9em; }}
  pre {{ background: #f6f8fa; padding: .8em; border-radius: 6px; overflow-x: auto; }}
  pre code {{ background: none; padding: 0; }}
  blockquote {{ color: #57606a; border-left: 3px solid #d0d7de;
               margin: .5em 0; padding: .2em 1em; background: #f6f8fa; }}
  a {{ color: #0969da; }}
</style>
</head>
<body>
{body}
</body>
</html>"""


def markdown_to_html(md: str, title: str = "文档") -> str:
    """把 Markdown 字符串转为带样式的完整 HTML 文档。

    支持 GFM 管道表格、标题、列表、代码块（依赖纯 Python 的 ``markdown`` 包）。
    """
    import markdown as _mk  # 延迟导入，仅在需要 HTML 时依赖

    body = _mk.markdown(
        md,
        extensions=["tables", "fenced_code", "sane_lists", "attr_list"],
    )
    return _HTML_TEMPLATE.format(title=title or "文档", body=body)

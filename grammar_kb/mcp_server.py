"""MCP 服务封装（可选依赖：``pip install mcp``）。

把 :class:`grammar_kb.query.Query` 的能力暴露为 MCP tools，供 Claude Desktop /
Claude Code 等 MCP 客户端直接查询语法知识点库。

启动：
    grammar-kb-mcp                              # 使用默认 DB（data/grammar.db）
    GRAMMAR_KB_DB=/path/grammar.db grammar-kb-mcp

设计说明：本模块刻意保持对 ``query.Query`` 的薄封装——每个 tool 都是一行调用
加格式化，因此后续增删 tool 与查询能力解耦。
"""
from __future__ import annotations

from typing import Optional

from .ingest import open_db
from .query import Query

try:
    from mcp.server.fastmcp import FastMCP  # type: ignore
except ImportError:  # 未安装 mcp 时仍可 import 本模块（便于单测/CLI）
    FastMCP = None  # type: ignore


# 单例查询句柄（懒加载，复用连接）
_query: Optional[Query] = None


def get_query() -> Query:
    global _query
    if _query is None:
        _query = Query(open_db())
    return _query


def _format_kp_list(kps) -> str:
    if not kps:
        return "（无命中）"
    lines = []
    for kp in kps:
        tags = " ".join(f"#{t}" for t in kp.tags)
        lines.append(f"- [#{kp.id}] 第{kp.lecture_number}讲 · {kp.category} · {kp.title}  {tags}")
    lines.append(f"\n共 {len(kps)} 条。")
    return "\n".join(lines)


if FastMCP is not None:
    mcp = FastMCP("grammar-kb")

    @mcp.tool()
    def search_knowledge_points(
        query: str, category: Optional[str] = None, limit: int = 20
    ) -> str:
        """按关键词检索语法知识点。

        参数：
            query: 关键词（中文或英文，如 "现在完成时"、"主将从现"、"since"）。
            category: 可选，限定大类：词法/句法/时态/语态/非谓语/综合复习。
            limit: 最多返回条数。
        返回：知识点列表（标题、所在讲次、分类、标签）。
        """
        kps = get_query().search_kps(query, category=category, limit=limit)
        return _format_kp_list(kps)

    @mcp.tool()
    def get_knowledge_point(kp_id: int) -> str:
        """按 id 获取单个知识点的完整 markdown（含解释、例句、表格、溯源）。"""
        md = get_query().kp_markdown(kp_id)
        return md or f"未找到知识点 id={kp_id}"

    @mcp.tool()
    def get_lecture_markdown(number: int) -> str:
        """获取某讲的完整 markdown 讲义（标题/正文/表格已还原为 GFM）。

        例如 number=25 返回"第二十五讲 动词时态3"的完整 md。
        """
        md = get_query().lecture_markdown(number)
        return md or f"未找到第 {number} 讲"

    @mcp.tool()
    def list_lectures() -> str:
        """列出已导入的全部讲次（讲号、标题、分类）。"""
        lecs = get_query().list_lectures()
        if not lecs:
            return "（库为空，请先 ingest）"
        return "\n".join(
            f"- 第{l.number}讲 {l.title}（{l.category}）" for l in lecs
        )

    @mcp.tool()
    def list_markers(category: str = "时态", tense: Optional[str] = None) -> str:
        """列出标志词/关键词，可溯源到讲次。

        默认返回所有时态关键词（category="时态"）。
        可用 tense 限定具体时态，如 tense="现在完成时"。
        """
        q = get_query()
        rows = q.markers_by_tense(tense) if tense else q.markers_by_category(category)
        if not rows:
            return "（无标志词）"
        out, cur = [], None
        for r in rows:
            t = r.get("tense") or "—"
            if t != cur:
                out.append(f"\n## {t}")
                cur = t
            lec = r.get("lecture_number")
            out.append(f"  - {r['marker']}" + (f"（第{lec}讲）" if lec else ""))
        out.append(f"\n共 {len(rows)} 个标志词。")
        return "\n".join(out)

    @mcp.tool()
    def find_by_relation(relation_type: str) -> str:
        """按关系类型查知识点，如 relation_type="主将从现"、"时态呼应"。"""
        kps = get_query().kps_by_relation(relation_type)
        if not kps:
            return "（无）"
        return _format_kp_list(kps)

    @mcp.tool()
    def stats() -> str:
        """返回知识库统计（讲次/知识点/标志词数量，按类别分布）。"""
        s = get_query().stats()
        lines = [
            f"讲次：{s['lectures']}",
            f"知识点：{s['knowledge_points']}",
            f"标志词：{s['markers']}",
            "按类别：",
        ]
        for cat, n in sorted(s["by_category"].items(), key=lambda x: -x[1]):
            lines.append(f"  {cat}: {n}")
        return "\n".join(lines)

    def main() -> None:
        mcp.run()

else:  # pragma: no cover - 仅当未安装 mcp 时
    def main() -> None:  # type: ignore
        raise SystemExit("未安装 mcp，请先 `pip install mcp`。")


if __name__ == "__main__":
    main()

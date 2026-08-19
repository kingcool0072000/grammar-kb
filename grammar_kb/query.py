"""查询 API：面向调用方（CLI 与后续 MCP 共用）的薄封装。

所有方法都基于 ``GrammarDB``，返回模型对象或 markdown 字符串。
刻意保持纯函数式风格，方便日后用 FastMCP 逐个包成 tool。
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from .db import GrammarDB, _row_to_kp
from .dict_db import DictDB
from .markdown import markdown_to_html, render_knowledge_point, render_lecture
from .models import Block, KnowledgePoint, Lecture


class Query:
    def __init__(self, db: GrammarDB, dict_db: "DictDB | None" = None):
        self.db = db
        self.dict = dict_db or DictDB()

    # ---- 主题体系（知识点聚合）-------------------------------------------- #

    def taxonomy(self) -> dict:
        """主题体系树：大类 → 主题（讲义 + 教材例句 + 知识点列表）。"""
        from .taxonomy import THEME_ORDER, brief_of, classify
        from .theme_notes import THEME_NOTES

        kps = self.db.conn.execute(
            "SELECT * FROM knowledge_point ORDER BY lecture_number, ord"
        ).fetchall()
        objects = [_row_to_kp(r, self.db.conn) for r in kps]

        tree: dict = {}
        for kp in objects:
            group, theme = classify(kp)
            tree.setdefault(group, {}).setdefault(theme, []).append(kp)

        out = []
        for group, themes in tree.items():
            order = THEME_ORDER.get(group, [])
            theme_list = sorted(themes.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)
            out_themes = []
            for t, theme_kps in theme_list:
                note = THEME_NOTES.get((group, t))
                out_themes.append({
                    "theme": t,
                    "count": len(theme_kps),
                    "note": note,
                    "examples": self._theme_examples(group, t, theme_kps),
                    "items": [
                        {
                            "id": kp.id,
                            "title": kp.title,
                            "lecture": kp.lecture_number,
                            # 一句话释义：正文首句；习题（原综合复习）取题干
                            "brief": (
                                "练习：" + kp.title[:44]
                                if kp.category == "综合复习"
                                else brief_of(kp)
                            ),
                        }
                        for kp in theme_kps
                    ],
                })
            out.append({
                "group": group,
                "themes": out_themes,
                "count": sum(len(v) for v in themes.values()),
            })
        return {"groups": [g for g in out if g["themes"]], "total": len(objects)}

    def _theme_examples(self, group: str, theme: str, theme_kps, limit: int = 3) -> list[dict]:
        """从该主题的教材语料提取中英对照例句（尽力使用教材原文）。

        过滤：英文须为完整句；中文须像翻译（排除「先行词/分析/句2」等语法讲解行）。
        优先选包含主题关键词的短句。
        """
        from .vocabulary import pair_sentences

        keywords = {
            "宾语从句": ("that", "if", "whether", "know", "told"),
            "定语从句": ("who", "which", "that", "whose", "whom"),
            "状语从句": ("if", "when", "because", "although", "while"),
            "感叹句": ("what", "how"),
            "反义疑问句": ("isn't", "doesn't", "didn't", "aren't"),
            "被动语态": ("been", "was ", "were ", "is ", "are ", "be "),
        }.get(theme, ())

        zh_noise = (
            "分析", "先行词", "句2", "句1", "代词", "结构", "语序", "表示", "用法",
            "主语", "谓语", "连接词", "表语", "从句", "引导词", "动词", "名词", "时态",
        )
        picked: list[dict] = []
        for kp in theme_kps:
            for en, zh in pair_sentences((kp.body_md or "") + "\n" + (kp.examples_md or "")):
                if len(picked) >= limit:
                    break
                en = en.strip()
                zh = zh.strip()
                if not (en[:1].isupper() and en[-1:] in ".!?"):
                    continue
                if not (4 <= len(zh) <= 45) or any(n in zh for n in zh_noise):
                    continue
                if any(p["en"] == en for p in picked):
                    continue
                picked.append({"en": en, "zh": zh})
            if len(picked) >= limit:
                break
        # 含主题关键词的例句优先
        if keywords:
            picked.sort(key=lambda p: 0 if any(k in p["en"].lower() for k in keywords) else 1)
        return picked[:limit]

    # ---- 词典（ECDICT）--------------------------------------------------- #

    def dict_lookup(self, word: str) -> Optional[dict]:
        """查任意单词的词典条目（音标/分行释义/词形/词性）。"""
        if not self.dict.available:
            return None
        return self.dict.lookup(word)

    # ---- 讲次 ------------------------------------------------------------ #

    def get_lecture(self, number: int) -> Optional[Lecture]:
        return self.db.get_lecture(number)

    def lecture_markdown(self, number: int) -> Optional[str]:
        """整讲还原为 markdown（含表格还原）。"""
        lec = self.db.get_lecture(number)
        if not lec:
            return None
        blocks = self.db.blocks_of_lecture(lec.id)  # type: ignore[arg-type]
        if not blocks:
            # 退路：从知识点拼
            blocks = self._blocks_from_kps(number)
        return render_lecture(lec, blocks)

    def lecture_html(self, number: int) -> Optional[str]:
        """整讲还原为 HTML（表格渲染为 <table>）。"""
        md = self.lecture_markdown(number)
        if md is None:
            return None
        lec = self.get_lecture(number)
        title = f"第{number}讲 {lec.title}" if lec else f"第{number}讲"
        return markdown_to_html(md, title=title)

    def _blocks_from_kps(self, number: int) -> list[Block]:
        """没有 lecture_block 时，从知识点反推块（兜底）。"""
        kps = self.db.kps_of_lecture(number)
        blocks: list[Block] = []
        seq = 0
        for kp in kps:
            blocks.append(
                Block(kind="subheading", text_md=f"### {kp.title}", page=kp.source_page, seq=seq)
            )
            seq += 1
            if kp.body_md:
                blocks.append(Block(kind="para", text_md=kp.body_md, page=kp.source_page, seq=seq))
                seq += 1
            if kp.examples_md:
                blocks.append(Block(kind="example", text_md=kp.examples_md, page=kp.source_page, seq=seq))
                seq += 1
            if kp.table_md:
                blocks.append(Block(kind="para", text_md=kp.table_md, page=kp.source_page, seq=seq))
                seq += 1
        return blocks

    def list_lectures(self) -> list[Lecture]:
        return self.db.list_lectures()

    # ---- 知识点 ---------------------------------------------------------- #

    def get_kp(self, kp_id: int) -> Optional[KnowledgePoint]:
        return self.db.get_kp(kp_id)

    def kp_markdown(self, kp_id: int) -> Optional[str]:
        kp = self.db.get_kp(kp_id)
        if not kp:
            return None
        return render_knowledge_point(kp)

    def kp_html(self, kp_id: int) -> Optional[str]:
        """单个知识点渲染为 HTML。"""
        kp = self.db.get_kp(kp_id)
        if not kp:
            return None
        return markdown_to_html(render_knowledge_point(kp), title=kp.title)

    def kps_of_lecture(self, number: int) -> list[KnowledgePoint]:
        return self.db.kps_of_lecture(number)

    def kps_of_category(self, category: str) -> list[KnowledgePoint]:
        return self.db.kps_of_category(category)

    def search_kps(
        self,
        query: str,
        category: Optional[str] = None,
        limit: int = 50,
    ) -> list[KnowledgePoint]:
        """全文检索知识点（FTS5 trigram；短查询/无命中时 LIKE 兜底）。"""
        q = (query or "").strip()
        if not q:
            return []
        rows = self._fts_search(q, category, limit)
        if not rows:
            rows = self._like_search(q, category, limit)
        return [_row_to_kp(r, self.db.conn) for r in rows]

    def _fts_search(self, q: str, category: Optional[str], limit: int):
        # trigram 支持短语查询；把双引号转义包裹成 phrase
        phrase = '"{}"'.format(q.replace('"', '""'))
        sql = (
            "SELECT k.* FROM knowledge_point k "
            "JOIN kp_fts f ON f.rowid = k.id "
            "WHERE kp_fts MATCH ?"
        )
        params: list = [phrase]
        if category:
            sql += " AND k.category = ?"
            params.append(category)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        try:
            return self.db.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []

    def _like_search(self, q: str, category: Optional[str], limit: int):
        like = f"%{q}%"
        sql = (
            "SELECT * FROM knowledge_point WHERE "
            "(title LIKE ? OR body_md LIKE ? OR examples_md LIKE ? OR table_md LIKE ?)"
        )
        params: list = [like] * 4
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY lecture_number, ord LIMIT ?"
        params.append(limit)
        return self.db.conn.execute(sql, params).fetchall()

    # ---- 标志词 ---------------------------------------------------------- #

    def markers_by_category(self, category: str = "时态") -> list[dict]:
        """某大类下的全部标志词（去重，带时态与讲次）。

        对应需求"给我所有时态关键词"。
        """
        rows = self.db.conn.execute(
            """SELECT DISTINCT marker, marker_type, tense, m.lecture_number
               FROM marker m JOIN knowledge_point k ON m.kp_id = k.id
               WHERE k.category = ?
               ORDER BY tense, marker""",
            (category,),
        ).fetchall()
        return [dict(r) for r in rows]

    def markers_by_tense(self, tense: str) -> list[dict]:
        rows = self.db.conn.execute(
            """SELECT DISTINCT marker, marker_type, lecture_number
               FROM marker WHERE tense = ? ORDER BY marker""",
            (tense,),
        ).fetchall()
        return [dict(r) for r in rows]

    def all_markers(self) -> list[dict]:
        rows = self.db.conn.execute(
            "SELECT DISTINCT marker, tense, marker_type FROM marker ORDER BY tense, marker"
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- 关系 ------------------------------------------------------------ #

    def kps_by_relation(self, rel_type: str) -> list[KnowledgePoint]:
        rows = self.db.conn.execute(
            """SELECT k.* FROM knowledge_point k
               JOIN relation r ON r.kp_id = k.id
               WHERE r.type = ? ORDER BY k.lecture_number""",
            (rel_type,),
        ).fetchall()
        return [_row_to_kp(r, self.db.conn) for r in rows]

    # ---- 考点信号 -------------------------------------------------------- #

    def list_exam_signals(self) -> list[str]:
        """数据里实际出现过的考点信号（去重排序）。"""
        rows = self.db.conn.execute(
            """SELECT DISTINCT e.value AS v FROM knowledge_point k,
               json_each(k.exam_signals_json) e
               WHERE e.value IS NOT NULL ORDER BY e.value"""
        ).fetchall()
        return [r["v"] for r in rows]

    def kps_by_exam_signal(self, signal: str) -> list[KnowledgePoint]:
        """反查：给定考点信号，返回会考该信号的所有知识点（反之亦然）。"""
        rows = self.db.conn.execute(
            """SELECT k.* FROM knowledge_point k, json_each(k.exam_signals_json) e
               WHERE e.value = ? ORDER BY k.lecture_number, k.ord""",
            (signal,),
        ).fetchall()
        return [_row_to_kp(r, self.db.conn) for r in rows]

    # ---- 统计 ------------------------------------------------------------ #

    def stats(self) -> dict:
        return self.db.stats()

    # ---- 单词表 ---------------------------------------------------------- #

    def vocabulary(self, limit: int = 300, min_freq: int = 2) -> list[dict]:
        """基于讲义语料的单词表（释义/词性/词形变化/来源）。"""
        from dataclasses import asdict

        from .vocabulary import build_vocabulary

        kps = self.db.conn.execute(
            "SELECT * FROM knowledge_point ORDER BY lecture_number, ord"
        ).fetchall()
        objects = [_row_to_kp(r, self.db.conn) for r in kps]
        # 词条词典信息（音标/释义/词形/词性）来自 ECDICT 全量词典（/dict 同源），
        # 语料层只贡献 freq/sources/examples/display 等增强
        entries = build_vocabulary(
            objects, limit=limit, min_freq=min_freq, dict_db=self.dict
        )
        return [asdict(e) for e in entries]

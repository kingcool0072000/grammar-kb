"""泛读会话有效性与书目元数据（章词数、必读章、展示章号）。"""
from __future__ import annotations

import json


class FocusMetaMixin:
    """会话是否算真实阅读，以及批量取书目章节。"""

    @staticmethod
    def _reading_session_valid(r) -> bool:
        """泛读会话有效性：剔除非真实阅读的信号（快速翻章/误触/闪进闪出）。

        判定（任一满足即有效）：
        - 停留 ≥60 秒（真读了）
        - 有交互：查词/播放/滚动/章导且停留 ≥10 秒
        （章切换后停留 <5 秒的翻章浏览——时长极短且无任何交互——无效。）
        """
        dur = r["active_sec"] or 0
        if dur >= 60:
            return True
        inter = ((r["lookups"] or 0) + (r["plays"] or 0)
                 + (r["scroll_count"] or 0) + (r["chapter_navs"] or 0))
        return dur >= 10 and inter > 0

    @staticmethod
    def _zombie_reading(r) -> bool:
        """泛读僵尸会话：泛读馆会话「时长够长被判有效」但零阅读证据——
        无位置轨迹、起止零位移（0%→0%，阅读器恢复失败落书首后挂着不动）。
        学习日志行/时长聚合/泛读分钟都剔（focus_words 的「无有效阅读」
        flag 同口径；非泛读模块恒 False）。"""
        if (r["module"] or "library") != "library" or r["book_id"] is None:
            return False
        # 注意：percent_track 存 JSON 字符串，空轨迹 '[]' 也是 truthy——
        # 必须解析后看长度
        try:
            if json.loads(r["percent_track"] or "[]"):
                return False
        except (ValueError, TypeError):
            pass
        inter = ((r["lookups"] or 0) + (r["plays"] or 0)
                 + (r["scroll_count"] or 0) + (r["chapter_navs"] or 0))
        if inter:
            return False
        return (r["percent_end"] or 0) <= (r["percent_start"] or 0)

    @staticmethod
    def _display_chapter_nos(chs: list, lo_idx: int, hi_idx: int) -> tuple[int, int]:
        """raw 章 idx → 展示章号（结构化重编号，与 focus_words 同口径）。

        epub 的 raw idx 含封面/目录/版权等前置页（Percy 第 6 章=idx10），
        对外章号一律「首个 ≥200 词内容章起算第 1 章」。"""
        first_content = 0
        for idx, wc in chs:
            if (wc or 0) >= 200:
                first_content = idx
                break
        return lo_idx - first_content + 1, hi_idx - first_content + 1

    @staticmethod
    def _book_meta(lib, ids) -> dict[int, dict]:
        """书目元数据批量取：章词数 + 必读章配置 + 章标题 + 书名。
        缺章节数据的书不出现（调用方按缺元数据兜底）。"""
        meta: dict[int, dict] = {}
        for bid in ids:
            chs = lib.execute(
                "SELECT idx, word_count, title FROM chapters"
                " WHERE book_id = ? ORDER BY idx", (bid,)).fetchall()
            if not chs:
                continue
            cfg = lib.execute(
                "SELECT chapters FROM book_reading_config WHERE book_id = ?",
                (bid,)).fetchone()
            try:
                req = set(json.loads(cfg[0])) if cfg and cfg[0] else None
            except (ValueError, TypeError):
                req = None
            meta[bid] = {"chs": [(c[0], c[1] or 0) for c in chs],
                         "required": req,
                         "titles": {c[0]: (c[2] or "") for c in chs}}
        # 书名（书已删时缺行，调用方用会话里的 book_title 兜底）
        for bid in list(meta):
            r = lib.execute("SELECT title FROM books WHERE id = ?", (bid,)).fetchone()
            if r:
                meta[bid]["title"] = r[0]
        return meta

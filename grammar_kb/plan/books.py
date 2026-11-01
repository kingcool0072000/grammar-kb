"""泛读书目匹配、章进度与阅读任务文案。"""
from __future__ import annotations

import contextlib
import json
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..paths import data_path
from .util import _chapter_at_words, _short, fmt_w

if TYPE_CHECKING:
    from .ctx import WeekCtx

class BooksMixin:

    def _lib_connect(self) -> Optional[sqlite3.Connection]:
        """library.db 只读连接（无库返回 None）。"""
        path = self.library_db_path or str(
            data_path("library.db")
        )
        if not Path(path).exists():
            return None
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error:
            return None


    @staticmethod
    def _key_words(book_key: str) -> list[str]:
        """书名关键词（小写、去符号、去数字后缀），供模糊匹配。"""
        import re as _re
        return [w for w in _re.findall(r"[a-z]{3,}", (book_key or "").lower())]


    def _match_book(self, book_key: str, user: str) -> Optional[sqlite3.Row]:
        """计划里的书名 → 实际书行。先在该学生读过的书里找，再全库找。"""
        conn = self._lib_connect()
        if conn is None:
            return None
        if (book_key or "").strip().isdigit():
            try:
                with contextlib.closing(conn):
                    row = conn.execute(
                        "SELECT id, title FROM books WHERE id = ?",
                        (int(book_key),),
                    ).fetchone()
                return row
            except sqlite3.Error:
                return None
        words = self._key_words(book_key)
        if not words:
            return None
        try:
            with contextlib.closing(conn):
                # 学生读过的书优先（UNION ALL + 去重保序：读过的排前面）
                rows = conn.execute(
                    "SELECT b.id, b.title,"
                    " (b.id IN (SELECT book_id FROM reading_progress WHERE user = ?)) AS read_first"
                    " FROM books b ORDER BY read_first DESC",
                    (user,),
                ).fetchall()
                for row in rows:
                    title = (row["title"] or "").lower()
                    if all(w in title for w in words):
                        return row
        except sqlite3.Error:
            return None
        return None


    def _book_reading_state(self, book_id: int, goal_pct: float, user: str) -> Optional[dict]:
        """某书的精确阅读状态：当前章/目标章/词数。缺数据返回 None。

        目标口径：泛读馆配置了必读章（book_reading_config）时按配置算——
        目标词数=必读章词数和、目标章=必读章最后一章、达成=读到最后一必读章末；
        未配置则按全书百分比（goal_pct）线性映射。
        """
        conn = self._lib_connect()
        if conn is None:
            return None
        try:
            with contextlib.closing(conn):
                prog = conn.execute(
                    "SELECT percent, reading_seconds FROM reading_progress"
                    " WHERE book_id = ? AND user = ?", (book_id, user),
                ).fetchone()
                chs = conn.execute(
                    "SELECT idx, title, word_count FROM chapters"
                    " WHERE book_id = ? ORDER BY idx", (book_id,),
                ).fetchall()
                cfg_row = None
                has_cfg_table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table'"
                    " AND name='book_reading_config'").fetchone()
                if has_cfg_table:
                    cfg_row = conn.execute(
                        "SELECT chapters FROM book_reading_config WHERE book_id = ?",
                        (book_id,),
                    ).fetchone()
        except sqlite3.Error:
            return None
        if not chs:
            return None
        ch_list = [dict(c) for c in chs]
        total = sum(c["word_count"] or 0 for c in ch_list)
        if total <= 0:
            return None
        pct = (prog["percent"] or 0.0) if prog else 0.0

        # 必读章配置 → 目标口径
        cfg: Optional[list[int]] = None
        if cfg_row:
            try:
                v = json.loads(cfg_row["chapters"] or "[]")
                cfg = sorted({int(x) for x in v}) if v else None
            except (ValueError, TypeError):
                cfg = None
        by_idx = {c["idx"]: c for c in ch_list}
        if cfg and all(i in by_idx for i in cfg):
            goal_words = sum(by_idx[i]["word_count"] or 0 for i in cfg)
            goal_ch = max(cfg)
            # 必读章末尾的全书累计百分比（达成线）
            seen = 0
            goal_pct_eff = 100.0
            for c in ch_list:
                seen += c["word_count"] or 0
                if c["idx"] == goal_ch:
                    goal_pct_eff = round(seen / total * 100, 1)
                    break
            cfg_count = len(cfg)
        else:
            goal_pct_eff = float(goal_pct)
            goal_words = round(total * goal_pct_eff / 100)
            goal = _chapter_at_words(ch_list, goal_pct_eff, total)
            goal_ch = goal["idx"] if goal else None
            cfg_count = None

        cur = _chapter_at_words(ch_list, pct, total)
        words_read = round(total * pct / 100)
        # 展示章号（结构化重编号）；goal_ch 是 raw idx（max(cfg)），换算
        first_idx = next((c["idx"] for c in ch_list
                          if (c["word_count"] or 0) >= 200), ch_list[0]["idx"])
        return {
            "percent": round(pct, 1),
            "goal_percent": round(goal_pct_eff, 1),
            "configured_chapters": cfg_count,
            "current_chapter": (cur["idx"] - first_idx + 1) if cur else None,
            "goal_chapter": (goal_ch - first_idx + 1)
                            if isinstance(goal_ch, int) else goal_ch,
            "words_read": words_read,
            "goal_words": goal_words,
            "remaining_words": max(0, goal_words - words_read),
            "total_words": total,
            "reading_seconds": (prog["reading_seconds"] or 0) if prog else 0,
        }


    def _reading_task(self, book_key: str, goal_val, user: str,
                      days_left: int, ctx: Optional["WeekCtx"] = None) -> dict:
        """泛读任务行。goal_val 两种格式：
        - list（新）：选章 idx 列表 → 目标=读完这些章（词数=章和）
        - number（旧）：全书目标 % → 按必读配置/全书口径
        匹配不到书则回退百分比文案。
        ``ctx``：书目/进度请求内 memo（周视图逐日复用，词数与进度不随日变，
        只有 days_left 进文案）。
        """
        row = (ctx.match_book(book_key) if ctx is not None
               else self._match_book(book_key, user))
        short = (_short(row["title"], 14) if row else _short(str(book_key), 14))
        if isinstance(goal_val, list) and row:
            # 新章选格式：目标词数=选章词数和；已读=该生全书有效阅读
            # 区段 ∩ 选章的词数（水位区段跨会话去重，且只数选章内）
            goal_words = (ctx.chapters_words_sum({str(row["id"]): goal_val})
                          if ctx is not None else
                          self._chapters_words_sum({str(row["id"]): goal_val}))
            done_w = (ctx.read_words_in_chapters(row["id"], goal_val)
                      if ctx is not None else
                      self._read_words_in_chapters(user, row["id"], goal_val))
            nos = (ctx.chapter_nos(row["id"], goal_val) if ctx is not None
                   else self._chapter_nos(row["id"], goal_val))
            rng = (f"第{nos[0]}–{nos[-1]}章" if len(nos) > 1
                   else f"第{nos[0]}章") if nos else f"{len(goal_val)} 章"
            if done_w >= goal_words and goal_words > 0:
                return {
                    "type": "reading", "book": book_key,
                    "key": f"reading:{book_key}",
                    "text": f"泛读《{short}》{rng}已读完 🎉",
                    "detail": f"累计 {fmt_w(done_w)}/{fmt_w(goal_words)} 词",
                }
            per_day = max(1, round(max(0, goal_words - done_w) / max(1, days_left)))
            return {
                "type": "reading", "book": book_key,
                "key": f"reading:{book_key}",
                "text": f"泛读《{short}》{rng}",
                "detail": (f"已读 {fmt_w(done_w)}/{fmt_w(goal_words)} 词"
                           f" · 今日 +{fmt_w(per_day)} 词"),
            }
        goal_pct = float(goal_val or 100)
        if row:
            st = (ctx.book_reading_state(row["id"], goal_pct)
                  if ctx is not None
                  else self._book_reading_state(row["id"], goal_pct, user))
            if st:
                cfg_txt = (f"必读 {st['configured_chapters']} 章"
                           if st.get("configured_chapters") else f"目标 {goal_pct}%")
                reached = st["percent"] >= (st.get("goal_percent") or goal_pct) - 0.05
                if reached:
                    return {
                        "type": "reading", "book": book_key,
                        "key": f"reading:{book_key}",
                        "text": f"泛读《{short}》目标已达成 🎉",
                        "detail": f"已读 {st['percent']}% · {cfg_txt}"
                                  f"（{fmt_w(st['goal_words'])} 词）",
                    }
                per_day = max(1, round(st["remaining_words"] / max(1, days_left)))
                return {
                    "type": "reading", "book": book_key,
                    "key": f"reading:{book_key}",
                    "text": f"泛读《{short}》读到第{st['current_chapter']}章"
                            if st["current_chapter"] else f"泛读《{short}》开始读",
                    "detail": (f"目标第{st['goal_chapter']}章 · {cfg_txt}"
                               f" · 已读 {fmt_w(st['words_read'])}/{fmt_w(st['goal_words'])} 词"
                               f" · 今日 +{fmt_w(per_day)} 词"),
                }
        return {
            "type": "reading", "book": book_key, "key": f"reading:{book_key}",
            "text": f"泛读《{short}》目标 {goal_pct}%", "detail": "每天 ≥15 分钟",
        }


    def _read_words_in_chapters(self, user: str, book_id: int, idxs: list) -> int:
        """该生在某书选章范围内的已读词数（跨会话区段去重 ∩ 选章）。"""
        lib = self._lib_connect()
        if lib is None or not idxs:
            return 0
        try:
            with contextlib.closing(lib):
                meta = self._book_meta(lib, {book_id})
        except sqlite3.Error:
            return 0
        info = meta.get(book_id)
        if not info:
            return 0
        total = sum(w for _, w in info["chs"])
        if total <= 0:
            return 0
        segs: list = []
        for r in self._library_sessions_all(user, book_id):
            if self._reading_session_valid(r):
                segs.extend(self._row_segs(r, total))
        ch_set = set(idxs)
        return self._segs_words(segs, info["chs"], ch_set)[0]


    def _chapter_nos(self, book_id: int, idxs: list) -> list[int]:
        """章 idx 列表 → 排序后的展示章号（标题字面数字，兜底结构号）。"""
        lib = self._lib_connect()
        if lib is None or not idxs:
            return []
        try:
            with contextlib.closing(lib):
                ph = ",".join("?" * len(idxs))
                rows = lib.execute(
                    "SELECT idx, title, word_count FROM chapters"
                    f" WHERE book_id = ? AND idx IN ({ph}) ORDER BY idx",
                    [book_id] + [int(x) for x in idxs]).fetchall()
        except (sqlite3.Error, ValueError, TypeError):
            return []
        if not rows:
            return []
        first_content = 0
        for r in rows:
            if (r["word_count"] or 0) >= 200:
                first_content = r["idx"]
                break
        out = []
        for r in rows:
            m = re.match(r"\s*(\d+)", str(r["title"] or ""))
            out.append(int(m.group(1)) if m
                       else max(1, r["idx"] - first_content + 1))
        return sorted(out)

"""计划表纯函数：截断、日期偏移、任务键、词数格式、周行反序列化。"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Optional


def _short(s: str, n: int) -> str:
    """截断加省略号（书名展示：超长以 … 结尾，不切单词中途裸断）。"""
    s = (s or "").strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"

def _paper_short(paper: str) -> str:
    if paper and paper.startswith("Reading"):
        return "RUE"
    return (paper or "")[:4]

def add_days(iso_str: str, n: int) -> str:
    return (date.fromisoformat(iso_str) + timedelta(days=n)).isoformat()

def _task_key(t: dict) -> str:
    """派生任务 → 稳定键（打卡表 task_key 口径）。"""
    if t.get("key"):
        return str(t["key"])
    ty = t.get("type")
    if ty == "micro_drill":
        return f"micro:{t['lecture']}"
    return "lecture" if ty == "lecture" else str(ty)

def fmt_w(n: int) -> str:
    """词数千分位简写：1234 → 1.2k。"""
    v = int(n or 0)
    return f"{v/1000:.1f}k" if v >= 1000 else str(v)

def _chapter_at_words(chs: list[dict], percent: float, total_words: int) -> Optional[dict]:
    """全书百分比 → 落点章（按各章词数累计线性映射）。

    返回 {idx, no, title, chapter_percent}：no 为对外展示章号（结构化
    重编号——首个 ≥200 词内容章起算第 1 章；epub raw idx 含封面/目录等
    前置页，直接展示会把「第 6 章」标成「第 10 章」）；idx 保留 raw 值
    供内部匹配。percent 是视线带驱动的全书精确值，与词数口径有 ±1 章
    误差，够展示用。
    """
    if not chs or total_words <= 0:
        return None
    first_content = next((c["idx"] for c in chs
                          if (c["word_count"] or 0) >= 200), chs[0]["idx"])
    words_seen = 0
    for c in chs:
        end_pct = round((words_seen + c["word_count"]) / total_words * 100, 1)
        if percent <= end_pct:
            return {
                "idx": c["idx"], "no": c["idx"] - first_content + 1,
                "title": c["title"],
                "start_percent": round(words_seen / total_words * 100, 1),
                "end_percent": end_pct,
            }
        words_seen += c["word_count"]
    last = chs[-1]
    return {
        "idx": last["idx"], "no": last["idx"] - first_content + 1,
        "title": last["title"],
        "start_percent": round((total_words - last["word_count"]) / total_words * 100, 1),
        "end_percent": 100.0,
    }

def _week_out(r: sqlite3.Row) -> dict:
    try:
        tasks = json.loads(r["tasks"] or "{}")
    except (ValueError, TypeError):
        tasks = {}
    return {
        "week_start": r["week_start"], "tasks": tasks,
        "notes": r["notes"] or "", "updated_at": r["updated_at"],
    }


"""把派生文章 JSON（researcher 抓取的 BNE Level 5 段落）入库为 derived 文章。

映射策略：按主题把抓到的段落挂到题材最接近的 FCE 原文段（base_key）：
- 青少年兼职与技能 → T1P7-x（Saturday jobs 四人）/ T3P1（Testing games）
- 动物自然科普 → T1P2（海豚）/ T2P2（马）/ T4P3（古文字考古）
- 科技发明 → T1P6（气球航天）/ T2P6（发电足球）
- 冒险运动 → T3P5（少年登山）/ T1P1（BMX）
- 校园活动与音乐会 → T2P7-x（音乐会评论）/ T4P7-x（School trips）

用法：python -m grammar_kb.reading_build 的兄弟脚本
    python scripts/ingest_derived.py data/derived_articles.json
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# 主题 → base_key 候选（按顺序轮转分配，保证每段 base 均有派生）
THEME_MAP = {
    "青少年兼职与技能": ["T1P7-A", "T1P7-B", "T1P7-C", "T1P7-D", "T3P1", "T2P1"],
    "动物自然科普": ["T1P2", "T2P2", "T1P3", "T4P3", "T3P3"],
    "科技发明": ["T1P6", "T2P6", "T4P6", "T2P3"],
    "冒险运动": ["T3P5", "T1P1", "T2P5", "T4P5"],
    "校园活动与音乐会": ["T2P7-A", "T2P7-B", "T2P7-C", "T2P7-D", "T4P7-A", "T4P7-B"],
}


def word_count(text: str) -> int:
    import re
    return len(re.findall(r"[A-Za-z0-9'-]+", text))


def main() -> None:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "data/derived_articles.json")
    items = json.loads(src.read_text(encoding="utf-8"))
    db = Path(__file__).resolve().parent.parent / "data" / "fce.db"
    conn = sqlite3.connect(db)
    # 幂等：按 url 去重（source 字段存 url）
    existing = {
        r[0] for r in conn.execute(
            "SELECT source FROM reading_article WHERE kind='derived'"
        )
    }
    cursor_per_theme = {}
    n = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for it in items:
        url = it.get("url", "")
        if url in existing:
            continue
        theme = it.get("theme", "")
        cands = THEME_MAP.get(theme)
        if not cands:
            continue
        i = cursor_per_theme.get(theme, 0)
        base_key = cands[i % len(cands)]
        cursor_per_theme[theme] = i + 1
        text = it["text"].strip()
        conn.execute(
            "INSERT INTO reading_article (kind, base_key, title, text, words, source, created_at)"
            " VALUES ('derived', ?, ?, ?, ?, ?, ?)",
            (base_key, it.get("title", ""), text, word_count(text),
             f"{it.get('source', '')} · {url}", now),
        )
        n += 1
    conn.commit()
    conn.close()
    print(f"imported {n} derived articles (skipped {len(items) - n} duplicates)")


if __name__ == "__main__":
    main()

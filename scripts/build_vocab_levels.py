# -*- coding: utf-8 -*-
"""构建分层词库表 vocab_word（grammar.db）：单词 + L0-L7 级别 + 释义/例句。

数据来源与分层规则（级别名不入库、不下发——孩子侧只看到 L 编号）：
- L0：哈一语料高频词（运行时 /vocabulary min_freq=2 同源，509 词），保留
  语料增强信息（freq/词形/例句对/来源讲次）；由本脚本实时调用 build_vocabulary
  生成，与「背单词」现有 L0 完全一致，孩子进度无缝衔接。
- L1-L7：初中/高中/四级/六级/考研/雅思/专四词表（KyleBing/english-vocabulary
  JSONL 预先下载到 data/wordlists/），先剔除 L0 已有词，再按「学习路径首次
  出现级别」归层（初中→高中→四级→六级→考研→雅思→专四），层间零重复。

用法：
    uv run python scripts/build_vocab_levels.py            # 全量重建
    uv run python scripts/build_vocab_levels.py --dry-run  # 只统计不入库

输入文件（data/wordlists/{初中,高中,四级,六级,考研,雅思,专四}.jsonl，行格式
{"word","us","uk","translations":[...],"phrases":[...],"sentences":[...]}）
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ORDER = ["初中", "高中", "四级", "六级", "考研", "雅思", "专四"]  # L1..L7

SCHEMA = """
CREATE TABLE IF NOT EXISTS vocab_word (
    word        TEXT PRIMARY KEY,
    level       INTEGER NOT NULL,            -- 0=哈一课本词, 1..7 按学习路径
    pos         TEXT NOT NULL DEFAULT '[]',  -- JSON ["v","n"]
    gloss       TEXT NOT NULL DEFAULT '',    -- 简明中文释义
    meanings    TEXT NOT NULL DEFAULT '[]',  -- JSON 分词性释义行
    example     TEXT NOT NULL DEFAULT '',    -- JSON {en, zh}
    extra       TEXT NOT NULL DEFAULT '{}'   -- L0 语料增强（freq/forms/sources/phonetic…）
);
CREATE INDEX IF NOT EXISTS idx_vocab_level ON vocab_word(level);
"""


def load_level_sets() -> list[tuple[str, set[str]]]:
    out = []
    for lv in ORDER:
        p = ROOT / "data" / "wordlists" / f"{lv}.jsonl"
        ws = set()
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            w = (d.get("word") or "").strip().lower()
            if w and w.isascii() and " " not in w and len(w) >= 2:
                ws.add(w)
        out.append((lv, ws))
    return out


def load_level_entries() -> dict[str, dict]:
    """级别词表的完整词条（释义/例句），供 L1+ 入库。"""
    out: dict[str, dict] = {}
    for lv in ORDER:
        p = ROOT / "data" / "wordlists" / f"{lv}.jsonl"
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            w = (d.get("word") or "").strip().lower()
            # 单字母词（a/i）与语料功能词无背诵价值且释义质量差，跳过
            if not w or not w.isascii() or " " in w or len(w) < 2:
                continue
            if w in out:
                continue
            trs = d.get("translations") or []
            pos = []
            meanings = []
            for t in trs:
                ty = (t.get("type") or "").strip().rstrip(".").lower()
                ty_n = {"vt": "v", "vi": "v", "aux": "v", "art": "det"}.get(ty, ty)
                if ty_n and ty_n not in pos and len(pos) < 4:
                    pos.append(ty_n)
                tr = (t.get("translation") or "").strip()
                if tr:
                    meanings.append((f"{ty}. " if ty and "." not in tr[:6] else "") + tr)
            ex = (d.get("sentences") or [{}])[0]
            out[w] = {
                "pos": pos,
                "gloss": "；".join(m[:40] for m in meanings[:3])[:120],
                "meanings": meanings[:6],
                "example": {
                    "en": (ex.get("sentence") or "").strip()[:200],
                    "zh": (ex.get("translation") or "").strip()[:160],
                },
            }
    return out


def build() -> None:
    # ---- L0：哈一语料高频词（与 /vocabulary min_freq=2 完全同源）----
    from grammar_kb.db import GrammarDB
    from grammar_kb.query import Query

    db_path = ROOT / "data" / "grammar.db"
    q = Query(GrammarDB(str(db_path)))
    hayi = q.vocabulary(limit=2000, min_freq=2)
    print(f"L0 哈一语料高频词: {len(hayi)}")
    l0_words = {e["word"].lower() for e in hayi}

    # ---- L1-L7：最低出现级别归层，剔除 L0 ----
    level_sets = load_level_sets()
    entries = load_level_entries()
    assign: dict[str, int] = {}
    for lv_idx, (_, ws) in enumerate(level_sets):
        for w in ws:
            if w in l0_words or w in assign:
                continue
            assign[w] = lv_idx + 1
    from collections import Counter

    dist = Counter(assign.values())
    for i, lv in enumerate(ORDER, start=1):
        print(f"L{i}（{lv}增量）: {dist.get(i, 0)}")
    print(f"合计: {len(l0_words) + len(assign)}")

    if ARGS.dry_run:
        return

    # ---- 入库（全量重建）----
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    con.execute("DELETE FROM vocab_word")
    rows = []
    for e in hayi:
        w = e["word"].lower()
        extra = {
            k: e.get(k)
            for k in ("freq", "forms", "sources", "phonetic", "special_spellings", "display")
            if e.get(k)
        }
        ex = (e.get("examples") or [{}])[0]
        rows.append((
            w, 0,
            json.dumps(e.get("pos") or [], ensure_ascii=False),
            e.get("gloss") or "",
            json.dumps(e.get("meanings") or [], ensure_ascii=False),
            json.dumps({"en": ex.get("en", ""), "zh": ex.get("zh", "")}, ensure_ascii=False),
            json.dumps(extra, ensure_ascii=False, default=str),
        ))
    for w, lv in assign.items():
        e = entries.get(w, {})
        rows.append((
            w, lv,
            json.dumps(e.get("pos") or [], ensure_ascii=False),
            e.get("gloss") or "",
            json.dumps(e.get("meanings") or [], ensure_ascii=False),
            json.dumps(e.get("example") or {}, ensure_ascii=False),
            "{}",
        ))
    con.executemany(
        "INSERT OR REPLACE INTO vocab_word (word, level, pos, gloss, meanings, example, extra)"
        " VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM vocab_word").fetchone()[0]
    per = con.execute(
        "SELECT level, COUNT(*) FROM vocab_word GROUP BY level ORDER BY level"
    ).fetchall()
    con.close()
    print(f"入库完成: {n} 词 -> {db_path}")
    print("分层:", {lv: c for lv, c in per})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计不入库")
    ARGS = ap.parse_args()
    build()

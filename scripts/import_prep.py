#!/usr/bin/env python3
"""章节预习离线导入：本地产出的预习 JSON 批量写入 library.db。

2026-11 生产 LLM 退役：线上不再在线生成章节预习，预习材料改由本机离线
产出（scripts/glm_client.py 或人工整理）后用本脚本导入。幂等：同
(book_id, chapter_index) 重复导入为整条覆盖（UPSERT），可放心重跑。

JSON 格式（数组，每项一章）：
    [{"book_id": 1, "chapter_index": 3,
      "payload": {"words": [{"word": "...", ...}], "idioms": []},
      "model": "offline", "difficulty": "L2"}]

用法：
    python3 scripts/import_prep.py prep.json --db data/library.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# 与 grammar_kb.library 的 chapter_preps DDL 同款（自包含，导入脚本不拖起
# DictDB 等重依赖；表已存在时无操作）
CHAPTER_PREPS_DDL = """
CREATE TABLE IF NOT EXISTS chapter_preps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  chapter_index INTEGER NOT NULL,
  payload TEXT NOT NULL,
  model TEXT DEFAULT '',
  difficulty TEXT DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(book_id, chapter_index)
);
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="章节预习离线导入（幂等 UPSERT chapter_preps）")
    ap.add_argument("json", help="预习 JSON 路径（数组，每项一章）")
    ap.add_argument("--db", required=True, help="library.db 路径")
    args = ap.parse_args(argv)
    if not Path(args.db).is_file():
        print(f"库不存在：{args.db}", file=sys.stderr)
        return 2
    try:
        rows = json.loads(Path(args.json).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"读取 JSON 失败：{e}", file=sys.stderr)
        return 2
    if not isinstance(rows, list):
        print("JSON 须为数组（每项一章）", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON")  # book_id 不存在立刻报错，不写孤儿行
    conn.executescript(CHAPTER_PREPS_DDL)
    ok = bad = 0
    for i, item in enumerate(rows):
        try:
            book_id = int(item["book_id"])
            chapter_index = int(item["chapter_index"])
            payload = item["payload"]
            if not isinstance(payload, dict):
                raise ValueError("payload 须为对象")
            model = str(item.get("model") or "offline")
            difficulty = str(item.get("difficulty") or "")
            existed = conn.execute(
                "SELECT 1 FROM chapter_preps WHERE book_id = ? AND chapter_index = ?",
                (book_id, chapter_index)).fetchone()
            conn.execute(
                "INSERT INTO chapter_preps"
                " (book_id, chapter_index, payload, model, difficulty, created_at)"
                " VALUES (?, ?, ?, ?, ?, datetime('now'))"
                " ON CONFLICT(book_id, chapter_index) DO UPDATE SET"
                " payload = excluded.payload, model = excluded.model,"
                " difficulty = excluded.difficulty, created_at = excluded.created_at",
                (book_id, chapter_index, json.dumps(payload, ensure_ascii=False),
                 model, difficulty))
            conn.commit()
            ok += 1
            print(f"[{i}] book {book_id} 第 {chapter_index} 章："
                  f"{'覆盖' if existed else '导入'}"
                  f"（words={len(payload.get('words') or [])}，"
                  f"idioms={len(payload.get('idioms') or [])}，model={model}）")
        except (KeyError, TypeError, ValueError, sqlite3.Error) as e:
            bad += 1
            print(f"[{i}] 失败：{e}", file=sys.stderr)
    conn.close()
    print(f"完成：成功 {ok}，失败 {bad}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

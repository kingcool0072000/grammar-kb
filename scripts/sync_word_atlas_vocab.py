#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按词光词头重写 data/wordlists，并写出 data/vocab_alias.json。

不打开 fce.db，不改背词进度。进度合并在服务启动时按别名表做。
重复执行是安全的：已经删掉的词不会回来，已有别名会保留。

    uv run python scripts/sync_word_atlas_vocab.py --atlas /path/to/word-atlas.json
    uv run python scripts/sync_word_atlas_vocab.py --atlas ... --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from grammar_kb.atlas_vocab import (  # noqa: E402
    ORDER,
    apply_typos,
    canon_key,
    load_alias_file,
    plan_vocab,
    read_wordlist_dir,
    records_from_files,
    render_files,
    write_alias_file,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="用词光词头整理和爸学词表")
    parser.add_argument("--atlas", required=True, help="word-atlas.json 路径")
    parser.add_argument("--wordlists", default=str(ROOT / "data" / "wordlists"))
    parser.add_argument("--alias", default=str(ROOT / "data" / "vocab_alias.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    entries = json.loads(Path(args.atlas).read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise SystemExit("word-atlas.json 应为数组")
    directory = Path(args.wordlists)
    files = read_wordlist_dir(directory)
    deleted, typo_aliases = apply_typos(files)
    records = records_from_files(files)
    existing = load_alias_file(Path(args.alias))
    plan = plan_vocab(records, entries, existing_aliases=existing)
    aliases = dict(plan["aliases"])
    aliases.update(typo_aliases)
    # 错词别名不要被计划里的同名键盖住：上面 update 会盖。错词优先。
    aliases.update(typo_aliases)

    remove = set(plan["remove"])
    for wrong in typo_aliases:
        remove.add(canon_key(wrong))
    for name in ORDER:
        files[name] = [
            row for row in files[name]
            if row.get("word") and canon_key(row["word"]) not in remove
        ]
    rendered = render_files(files, plan["additions"])

    by_kind = Counter(item["kind"] for item in aliases.values())
    by_level = Counter(item["level"] for item in plan["additions"].values())
    exam_true = sum(1 for item in plan["additions"].values() if item["exam"])
    summary = {
        "deleted_lines": deleted,
        "alias": dict(by_kind),
        "alias_n": len(aliases),
        "additions": dict(by_level),
        "additions_n": len(plan["additions"]),
        "additions_exam": exam_true,
        "removed_headwords": len(plan["remove"]),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if args.dry_run:
        return
    for name in ORDER:
        path = directory / f"{name}.jsonl"
        text = "\n".join(rendered[name])
        if text:
            text += "\n"
        path.write_text(text, encoding="utf-8")
    write_alias_file(Path(args.alias), aliases)
    print(f"已写词表 {directory} 和别名 {args.alias}")


if __name__ == "__main__":
    main()

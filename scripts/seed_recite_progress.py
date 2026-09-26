#!/usr/bin/env python3
"""把 malin 浏览器遗留的本地背词进度快照灌进云端 recite_word_progress。

数据来源：2026-09-26 从 malin 浏览器导出的 localStorage
``gkb-recite-progress-v1``（{word: {right, wrong}}，245 词 362 词次）。
新版前端已改为打开页面自动 /recite/progress/sync 合并上云；本脚本用于：
1. 生产部署后立即灌入基线（不依赖孩子下次打开页面）；
2. 万一浏览器数据被清，从这份固化快照恢复。

语义与 ReciteStore.merge_progress 一致（逐词 right/wrong 取 max，幂等，
可重复执行）。join 不上 vocab_word 的 key（脏 key / 库外词）打印清单不入库。

用法：
    uv run python scripts/seed_recite_progress.py --user malin [--dry-run]
    GRAMMAR_KB_FCE_DB=/path/fce.db uv run python scripts/seed_recite_progress.py ...
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grammar_kb.recite import ReciteStore  # noqa: E402

# malin 的 localStorage 快照（gkb-recite-progress-v1，2026-09-26 导出）
SNAPSHOT = {
    "able": (2, 0), "above": (1, 0), "abroad": (3, 0), "absent": (1, 0),
    "accept": (1, 0), "after": (1, 0), "again": (1, 0), "ago": (2, 0),
    "airport": (2, 0), "alice": (1, 0), "all": (1, 0), "allow": (1, 0),
    "amazed": (3, 0), "among": (2, 0), "another": (1, 0), "answer": (2, 0),
    "anyone": (1, 0), "arrive": (3, 0), "ask": (3, 0), "awake": (1, 0),
    "aware": (1, 0), "away": (2, 0), "baby": (2, 0), "bad": (2, 0),
    "beijing": (1, 0), "begin": (1, 0), "besides": (1, 0), "best": (2, 0),
    "birthday": (2, 0), "book": (1, 0), "break": (1, 0), "brother": (1, 0),
    "build": (1, 0), "buy": (1, 0), "call": (1, 0), "canada": (2, 0),
    "carefully": (1, 0), "case": (1, 0), "catch": (1, 0), "cheer": (2, 0),
    "chess": (2, 0), "china": (2, 0), "church": (2, 0), "class": (1, 0),
    "close": (4, 0), "clothes": (1, 0), "cold": (2, 0), "come": (3, 0),
    "complete": (1, 0), "computer": (1, 0), "concert": (1, 0), "day": (1, 0),
    "dinner": (1, 0), "each": (1, 0), "early": (1, 0), "easy": (1, 0),
    "eight": (1, 0), "either": (1, 0), "else": (1, 0), "enjoy": (2, 0),
    "enough": (1, 0), "exchange": (1, 0), "experience": (1, 0), "famous": (1, 0),
    "farm": (1, 0), "father": (1, 0), "few": (1, 0), "fifth": (1, 0),
    "fine": (2, 0), "finish": (1, 0), "first": (2, 0), "five": (1, 0),
    "flower": (1, 0), "fly": (1, 0), "football": (2, 0), "forest": (3, 0),
    "forget": (1, 0), "fourth": (1, 0), "france": (3, 0), "friday": (1, 0),
    "friend": (1, 0), "friendly": (1, 0), "front": (1, 0), "fun": (1, 0),
    "further": (1, 0), "gate": (1, 0), "german": (2, 0), "girl": (1, 0),
    "give": (1, 0), "graduation": (18, 3), "half": (2, 0), "hand": (1, 0),
    "happen": (1, 0), "hard": (2, 0), "harder": (2, 0), "heart": (1, 0),
    "help": (1, 0), "high": (1, 0), "history": (1, 0), "hope": (1, 0),
    "house": (3, 0), "idea": (1, 0), "ill": (2, 0), "improve": (1, 0),
    "interesting": (1, 0), "jane": (1, 0), "kate": (1, 0), "keep": (2, 0),
    "last": (1, 0), "late": (2, 0), "leave": (1, 0), "let": (2, 0),
    "life": (1, 0), "light": (1, 0), "like": (1, 0), "listen": (1, 0),
    "little": (1, 0), "look": (2, 0), "lose": (1, 0), "loudly": (1, 0),
    "make": (2, 0), "many": (1, 0), "mary": (1, 0), "match": (1, 0),
    "matter": (1, 0), "meet": (2, 0), "milk": (1, 0), "million": (1, 0),
    "miss": (2, 0), "moment": (3, 1), "month": (1, 0), "more": (1, 0),
    "morethoroughly": (1, 0), "near": (1, 0), "necessary": (2, 0), "never": (1, 0),
    "newspaper": (3, 0), "next": (1, 0), "nice": (1, 0), "ninth": (1, 0),
    "nobody": (1, 0), "none": (2, 0), "nor": (1, 0), "off": (1, 0),
    "offer": (1, 0), "old": (1, 0), "one": (1, 0), "order": (1, 0),
    "other": (1, 0), "over": (2, 0), "pair": (1, 0), "patiently": (6, 1),
    "picnic": (1, 0), "place": (1, 0), "plane": (1, 0), "plant": (1, 0),
    "play": (1, 0), "please": (2, 0), "prepare": (3, 0), "price": (1, 0),
    "problem": (1, 0), "put": (2, 0), "question": (1, 0), "quickly": (1, 0),
    "rather": (1, 0), "read": (1, 0), "really": (1, 0), "receive": (6, 1),
    "recently": (1, 0), "reply": (1, 0), "rest": (2, 0), "return": (1, 0),
    "rise": (1, 0), "schoolbag": (1, 0), "sea": (1, 0), "seat": (1, 0),
    "see": (1, 0), "seeing": (1, 0), "sell": (1, 0), "separate": (2, 0),
    "set": (1, 0), "short": (1, 0), "show": (1, 0), "singer": (1, 0),
    "sister": (1, 0), "six": (1, 0), "small": (2, 0), "smiths": (2, 0),
    "snow": (1, 0), "solution": (2, 0), "solve": (1, 0), "some": (2, 0),
    "soon": (1, 0), "sorry": (1, 0), "speak": (1, 0), "spill": (1, 0),
    "stand": (1, 0), "stop": (1, 0), "study": (2, 0), "success": (1, 0),
    "suddenly": (1, 0), "sun": (2, 0), "surprised": (1, 0), "sweet": (1, 0),
    "take": (2, 0), "talk": (1, 0), "teacher": (1, 0), "team": (2, 0),
    "tell": (1, 0), "ten": (1, 0), "thing": (1, 0), "third": (1, 0),
    "three": (3, 0), "through": (1, 0), "thus": (6, 1), "till": (2, 0),
    "tim": (1, 0), "time": (1, 0), "together": (1, 0), "tomorrow": (3, 0),
    "trousers": (1, 0), "try": (1, 0), "turn": (1, 0), "two": (1, 0),
    "until": (3, 0), "up": (2, 0), "use": (3, 0), "wake": (1, 0),
    "walk": (1, 0), "watch": (1, 0), "way": (1, 0), "wear": (1, 0),
    "weather": (1, 0), "week": (1, 0), "whether": (1, 0), "while": (1, 0),
    "whole": (2, 0), "window": (1, 0), "wish": (1, 0), "work": (2, 0),
    "world": (2, 0), "wrong": (1, 0), "year": (1, 0), "yesterday": (1, 0),
    "young": (1, 0),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", default="malin", help="学生账号（默认 malin）")
    ap.add_argument("--fce-db", default=None, help="fce.db 路径（默认走 ReciteStore 解析）")
    ap.add_argument("--dry-run", action="store_true", help="只打印匹配统计，不写库")
    args = ap.parse_args()

    store = ReciteStore(args.fce_db)
    levels = store._vocab_levels(list(SNAPSHOT))
    matched = {w: lv for w, lv in levels.items()}
    unmatched = sorted(w for w in SNAPSHOT if w not in matched)

    total_right = sum(r for r, _ in SNAPSHOT.values())
    total_wrong = sum(w for _, w in SNAPSHOT.values())
    print(f"快照：{len(SNAPSHOT)} 词 · {total_right + total_wrong} 词次（对 {total_right} / 错 {total_wrong}）")
    by_lv: dict[int, int] = {}
    for w in matched:
        by_lv[matched[w]] = by_lv.get(matched[w], 0) + 1
    for lv in sorted(by_lv):
        print(f"  L{lv}: {by_lv[lv]} 词")
    if unmatched:
        print(f"未匹配 vocab_word 的 key（不入库，人工核对）：{', '.join(unmatched)}")

    if args.dry_run:
        print("dry-run：未写库")
        return 0

    payload = {w: {"right": r, "wrong": wr} for w, (r, wr) in SNAPSHOT.items()}
    out = store.merge_progress(args.user, payload)
    print(
        f"已合并 → user={out['user']} 云端共 {out['total_seen']} 词 / 掌握 {out['mastered']} 词"
    )
    for lv, d in sorted(out["levels"].items()):
        total = f"/{d['total']}" if d.get("total") else ""
        print(f"  L{lv}: 掌握 {d['mastered']}{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

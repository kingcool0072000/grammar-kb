#!/usr/bin/env python3
"""NCE2 本地资产导入：双语 LRC → data/nce2/book.json + mp3 归一化拷贝。

源目录（默认 /Users/maxiangyu/Code/NCE/resources/NCE2-mp3-48k）里每课两个文件：
  01－A Private Conversation.lrc   （全角连接符分隔课号与标题）
  01－A Private Conversation.mp3   （48kbps mono，已压缩）

产出：
  data/nce2/book.json              结构化 manifest（进 git，随代码部署）
  data/audio/nce2/01.mp3 … 96.mp3  归一化文件名（data/audio 已 gitignore，走带外 scp）

幂等可重跑。用法：
  python3 scripts/import_nce2.py [源目录]
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SRC = Path("/Users/maxiangyu/Code/NCE/resources/NCE2-mp3-48k")

# 文件名：`01－标题`（全角连接符 U+FF0D，历史源文件如此）
FNAME_RE = re.compile(r"^(\d{1,3})\s*[\uFF0D\u2014-]\s*(.+?)\.lrc$", re.IGNORECASE)
# LRC 行首元标签：[al:…][ar:…][ti:…][by:…][offset:…]
META_RE = re.compile(r"^\[(?:al|ar|ti|by|offset):.*\]\s*$")
# 时间标签 [mm:ss] / [mm:ss.xx]；一行可能带多个（按 LRC 标准逐个展开）
TIME_RE = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
# 英文词计数：连续字母（含 's 所有格/缩写），数字词也算
WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+")


def parse_lrc(path: Path) -> list[dict]:
    """LRC → [{t, en, zh}]，双语按第一个 `|` 分裂；无中文行 zh 为空串。"""
    out: list[dict] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or META_RE.match(line):
            continue
        stamps = TIME_RE.findall(line)
        if not stamps:
            continue  # 无时间轴的行（空行/杂注）不入逐句表
        text = TIME_RE.sub("", line, count=len(stamps)).strip()
        en, _, zh = text.partition("|")
        en, zh = en.strip(), zh.strip()
        if not en:
            continue
        for mm, ss, frac in stamps:
            t = int(mm) * 60 + int(ss) + int((frac or "0").ljust(3, "0")[:3]) / 1000
            out.append({"t": round(t, 2), "en": en, "zh": zh})
    out.sort(key=lambda s: s["t"])
    return out


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    if not src.is_dir():
        print(f"源目录不存在：{src}", file=sys.stderr)
        return 1
    out_book = REPO / "data" / "nce2"
    out_audio = REPO / "data" / "audio" / "nce2"
    out_book.mkdir(parents=True, exist_ok=True)
    out_audio.mkdir(parents=True, exist_ok=True)

    lessons: list[dict] = []
    for lrc in sorted(src.glob("*.lrc")):
        m = FNAME_RE.match(lrc.name)
        if not m:
            print(f"跳过（文件名不识别）：{lrc.name}", file=sys.stderr)
            continue
        lid, title = int(m.group(1)), m.group(2).strip()
        mp3 = lrc.with_suffix(".mp3")
        if not mp3.exists():
            print(f"跳过（缺 mp3）：{lrc.name}", file=sys.stderr)
            continue
        sentences = parse_lrc(lrc)
        if not sentences:
            print(f"跳过（LRC 无有效句）：{lrc.name}", file=sys.stderr)
            continue
        n_words = sum(len(WORD_RE.findall(s["en"])) for s in sentences)
        shutil.copy2(mp3, out_audio / f"{lid:02d}.mp3")
        lessons.append({
            "id": lid, "title": title, "filename": f"{lid:02d}",
            "n_sentences": len(sentences), "n_words": n_words,
            "sentences": sentences,
        })

    if len(lessons) != 96:
        print(f"警告：解析出 {len(lessons)} 课（预期 96）", file=sys.stderr)
    lessons.sort(key=lambda x: x["id"])
    ids = [x["id"] for x in lessons]
    if ids != list(range(1, len(lessons) + 1)):
        print(f"警告：课号不连续：{ids}", file=sys.stderr)

    book = {"book": "NCE2", "lessons": lessons}
    target = out_book / "book.json"
    target.write_text(json.dumps(book, ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8")
    total_w = sum(x["n_words"] for x in lessons)
    print(f"✅ {len(lessons)} 课 → {target}")
    print(f"   共 {sum(x['n_sentences'] for x in lessons)} 句 / {total_w} 词")
    for x in (lessons[:1] + lessons[-1:]):
        print(f"   第 {x['id']:>2} 课《{x['title']}》"
              f" {x['n_sentences']} 句 {x['n_words']} 词")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

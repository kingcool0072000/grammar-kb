"""困难词本地粗筛：基于 google-10000 高频词表（按频率排序）。

直译自 FCEReadingLib server/src/services/wordfreq.ts。
三档阈值（老师可在设置中调整）：
 - L1 初级(KET/PET)：排名 > 4000 的词为候选困难词
 - L2 中级(FCE, 默认)：排名 > 8000
 - L3 进阶(CAE)：不在词表内（≈15000+）才算困难
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# 词表：行号 = 排名（1 起）
_WORDFREQ_PATH = Path(__file__).resolve().parent.parent / "data" / "wordfreq-10k.txt"

DIFFICULTY_LEVELS = {
    "L1": 4000,
    "L2": 8000,
    "L3": 9895,  # 词表全长：只有表外词才算困难
}

_RANK: dict[str, int] = {}


def _load_ranks() -> None:
    if _RANK:
        return
    try:
        text = _WORDFREQ_PATH.read_text(encoding="utf-8")
    except OSError:
        return  # 词表缺失时所有词都视为表外（粗筛退化）
    for i, line in enumerate(text.split("\n")):
        word = line.strip().lower()
        if word:
            _RANK[word] = i + 1


_load_ranks()


def _lemma(word: str) -> list[str]:
    """弱化词形：仅做轻量后缀归并（-s/-es/-ed/-d/-ing/-ly），避免引入完整词干库。

    仅当归并形式在词表内时才纳入（对应 TS 的 ``RANK.has(v)`` 检查）。
    """
    w = word.lower()
    out = [w]

    def try_push(v: Optional[str]) -> None:
        if v and len(v) >= 3 and v in _RANK:
            out.append(v)

    if w.endswith("ies") and len(w) > 4:
        try_push(w[:-3] + "y")
    if w.endswith("es") and len(w) > 3:
        try_push(w[:-2])
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        try_push(w[:-1])
    if w.endswith("ied") and len(w) > 4:
        try_push(w[:-3] + "y")
    if w.endswith("ed") and len(w) > 4:
        try_push(w[:-2])
        try_push(w[:-1])  # liked -> like
    if w.endswith("ing") and len(w) > 5:
        try_push(w[:-3])
        try_push(w[:-3] + "e")  # making -> make
    if w.endswith("ly") and len(w) > 4:
        try_push(w[:-2])
    return out


# 疑似专有名词：原文中首字母大写且不在句首
_PROPER_NOUN_RE = re.compile(r"^[A-Z]")

# 常见缩写（didn't/it's/I'm 等）不值得进预习
_CONTRACTION_RE = re.compile(r"^[a-z]+'(t|s|re|ve|ll|d|m)$", re.I)

# 按句切分（保留句末标点）
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")


def extract_hard_words(
    text: str, difficulty: str = "L2", limit: int = 25
) -> list[dict]:
    """从章节文本粗筛候选困难词。

    :param text: 章节纯文本
    :param difficulty: 难度档位（L1/L2/L3）
    :param limit: 候选上限（交给 AI 精筛与压缩到 maxWords）
    :return: ``[{word, count}, ...]`` 按频次降序、同频按字母序
    """
    threshold = DIFFICULTY_LEVELS.get(difficulty, DIFFICULTY_LEVELS["L2"])
    counts: dict[str, int] = {}
    sentences = _SENTENCE_SPLIT_RE.split(text or "")

    for sentence in sentences:
        words = _WORD_RE.findall(sentence)
        if not words:
            continue
        m = re.search(r"[A-Za-z]", sentence)
        sentence_start_idx = m.start() if m else -1
        for wi, raw in enumerate(words):
            lower = raw.lower().replace("’", "'")
            # 缩写（didn't/it's 等）跳过
            if _CONTRACTION_RE.match(lower):
                continue
            # 句首大写不算专有名词线索；非句首大写 => 疑似人名地名，跳过
            char_idx = sentence.find(raw, 0)
            is_sentence_start = wi == 0 or char_idx <= sentence_start_idx
            if _PROPER_NOUN_RE.match(raw) and not is_sentence_start:
                continue

            forms = _lemma(lower)
            best = min(_RANK.get(f, float("inf")) for f in forms)
            if best <= threshold:
                continue  # 常用词，跳过
            counts[lower] = counts.get(lower, 0) + 1

    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"word": w, "count": c} for w, c in items[:limit]]

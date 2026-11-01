"""词汇库映射与词形工具（vocab_word 查询 + 屈折还原）。

从 reading.py 抽出：fce_query（听力预习词表）与 reading（精读标注/
难度报告）共用同一套「词→级别/原形/词性」映射，此前靠函数内延迟
import 互相取对方私有符号，形成 reading↔fce_query 循环依赖。统一
落到这里（只依赖 dbreg），双方顶层 import，环消失。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from . import dbreg

_FUNCTION_WORDS = frozenset("""
a an the and or but if then than that this these those there here of in on at to
from by for with about into over under after before between during without within
as is am are was were be been being do does did doing done have has had having
i you he she it we they me him her us them my your his its our their mine yours
hers ours theirs myself yourself himself herself itself ourselves themselves
who whom whose which what when where why how not no nor so too very just also
only ever never always often sometimes usually will would shall should can could
may might must ought because whether while once whereas unless since although
though much many more most some any all each every both few several other
another such same own else someone somebody something anything anybody anyone
everything everyone everybody nothing nobody nowhere somewhere anywhere
everywhere above below through across against along among around behind beyond
near outside toward towards upon per via still already yet again almost nearly
quite rather together away back down up out off
there's it's don't doesn't didn't won't can't couldn't
wouldn't shouldn't isn't aren't wasn't weren't hasn't haven't hadn't i'm you're
he's she's we're they're that's what's let's
""".split())

_vocab_map_cache: dict = {}


def _vocab_level_map() -> dict[str, int]:
    """vocab_word 全量 {word: level}（进程内缓存；库缺失返回空表）。"""
    if _vocab_map_cache:
        return _vocab_map_cache
    try:
        path = dbreg.grammar_db_path()
        if not Path(path).exists():
            return _vocab_map_cache
        with dbreg.connect(path, readonly=True) as conn:
            _vocab_map_cache.update(
                dict(conn.execute("SELECT word, level FROM vocab_word").fetchall()))
    except Exception:  # noqa: BLE001
        pass  # 词库缺失时词汇维度降级（按库外词处理）
    return _vocab_map_cache


_IRREG_FORMS = {
    "said": "say", "says": "say", "known": "know", "knew": "know", "shown": "show",
    "showed": "show", "told": "tell", "made": "make", "went": "go", "gone": "go",
    "came": "come", "taken": "take", "took": "take", "found": "find", "began": "begin",
    "begun": "begin", "thought": "think", "felt": "feel", "kept": "keep",
    "left": "leave", "meant": "mean", "met": "meet", "paid": "pay", "built": "build",
    "sat": "sit", "stood": "stand", "lost": "lose", "sent": "send", "spent": "spend",
    "brought": "bring", "bought": "buy", "taught": "teach", "caught": "catch",
    "ran": "run", "grew": "grow", "grown": "grow", "drew": "draw", "drawn": "draw",
    "fell": "fall", "fallen": "fall", "held": "hold", "heard": "hear", "sold": "sell",
    "wore": "wear", "worn": "wear", "won": "win", "wrote": "write", "written": "write",
    "rode": "ride", "ridden": "ride", "chose": "choose", "chosen": "choose",
    "broke": "break", "broken": "break", "drove": "drive", "driven": "drive",
    "ate": "eat", "eaten": "eat", "gave": "give", "given": "give", "saw": "see",
    "seen": "see", "flew": "fly", "flown": "fly", "rose": "rise", "risen": "rise",
    "spoke": "speak", "spoken": "speak", "woke": "wake", "woken": "wake",
    "sang": "sing", "sung": "sing", "swam": "swim", "swum": "swim",
    "men": "man", "women": "woman", "children": "child", "feet": "foot",
    "teeth": "tooth", "mice": "mouse", "died": "die", "dying": "die",
    "lay": "lie", "lain": "lie", "laid": "lay", "lying": "lie", "tried": "try",
    "studied": "study", "carried": "carry", "worried": "worry",
}


def _lemma_candidates(w: str):
    """屈折变形 → 原形候选（vocab_word 只存原形；按命中次序尝试）。"""
    yield w
    if w.endswith("ies"):
        yield w[:-3] + "y"
    if w.endswith("ves"):
        yield w[:-3] + "f"
        yield w[:-3] + "fe"
    if w.endswith("es"):
        yield w[:-2]
        yield w[:-1]
    if w.endswith("s"):
        yield w[:-1]
    if w.endswith("ied"):
        yield w[:-3] + "y"
    if w.endswith("ed"):
        stem = w[:-2]
        yield stem + "e"        # liked → like
        yield stem              # walked → walk
        if len(stem) > 1 and stem[-1] == stem[-2]:
            yield stem[:-1]     # stopped → stop
    if w.endswith("ing"):
        stem = w[:-3]
        yield stem + "e"        # making → make
        yield stem              # reading → read
        if len(stem) > 1 and stem[-1] == stem[-2]:
            yield stem[:-1]     # running → run
    if w.endswith("est"):
        yield w[:-3]            # toughest → tough
        yield w[:-2]
        if len(w) > 4 and w[-4] == w[-5]:
            yield w[:-4]        # biggest → big
    if w.endswith("er"):
        yield w[:-2]
        yield w[:-1]
        if len(w) > 4 and w[-3] == w[-4]:
            yield w[:-3]        # bigger → big


def _lookup_level(vm: dict, w: str) -> tuple[Optional[int], str]:
    """查词层级；命中非原形时返回还原后的原形（供词形统计）。"""
    if w in vm:
        return vm[w], w
    base = _IRREG_FORMS.get(w)
    if base is not None and base in vm:
        return vm[base], base
    for c in _lemma_candidates(w):
        if c in vm:
            return vm[c], c
    return None, w


def _proper_nouns(text: str) -> set[str]:
    """句首（非句首）仍大写的词 → 专名（人名/地名等，不计难度）。"""
    out: set[str] = set()
    for m in re.finditer(r"\b[A-Z][a-z']+\b", text):
        prev = text[:m.start()].rstrip()
        if not prev or prev[-1] in ".!?\"':—-\n":
            continue  # 句首/段首/引语开头的大写不是专名
        out.add(m.group(0).lower())
    return out


_pos_map_cache: dict = {}


def _vocab_pos_map() -> dict[str, str]:
    """vocab_word 的 pos JSON 串 → 压缩成 'vna' 式字母串（进程内缓存）。"""
    if _pos_map_cache:
        return _pos_map_cache
    try:
        import json as _json

        path = dbreg.grammar_db_path()
        if not Path(path).exists():
            return _pos_map_cache
        with dbreg.connect(path, readonly=True) as conn:
            for word, pos in conn.execute("SELECT word, pos FROM vocab_word"):
                try:
                    letters = "".join(sorted({p.strip()[0] for p in _json.loads(pos or "[]")
                                               if p and p.strip()}))
                except Exception:  # noqa: BLE001
                    letters = ""
                _pos_map_cache[word] = letters
    except Exception:  # noqa: BLE001
        pass
    return _pos_map_cache

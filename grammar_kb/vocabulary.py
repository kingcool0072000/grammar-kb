"""单词表构建（基于讲义语料，纯函数，便于单测）。

数据来源：知识点的 ``examples_md`` / ``body_md`` 里的中英对照例句。
- 词频：英文 token 统计（去停用词/标点/数字）
- 词性：优先按该词出现的知识点细分（名词/动词/形容词…），否则后缀兜底
- 释义：英文句紧跟的中文句配对（取出现该词的例句翻译，去重取前几条）
- 词形变化：名词复数用 ``inflect``（含常见不规则）；动词用内置不规则表 + 规则；
  形容词用规则比较级/最高级
- 来源：溯源到知识点/讲次

说明：词形变化对未命中不规则表的动词按规则推断（可能不准），故在 entry 里标注
``forms_note``；释义来自例句配对，可能包含整句翻译而非词典式精炼释义。
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .models import KnowledgePoint

WORD_RE = re.compile(r"[A-Za-z]+(?:'[a-z])?")

# 常见虚词/代词/助动词等，不计入单词表
STOP_WORDS = frozenset(
    """
    a an the and or but if of to in on at for with by from as into onto upon about
    is am are was were be been being do does did doing have has had having
    will would shall should can could may might must need dare
    i you he she it we they me him her us them my your his its our their mine yours hers
    this that these those there here not no yes very too so just also only
    than then when where why how what who whom which whose
    don't didn't doesn't isn't aren't wasn't weren't won't wouldn't can't couldn't
    i'm you're he's she's it's we're they're that's what's
    s t d ll re ve m
    """.split()
)

# 细分（来自 lecture.subcategory / kp.tags）→ 词性
SUBCAT_POS = {
    "名词": "n",
    "动词": "v",
    "情态动词": "v",
    "形容词": "adj",
    "副词": "adv",
    "代词": "pron",
    "介词": "prep",
    "连词": "conj",
    "冠词": "art",
    "数词": "num",
}

# 初中常见不规则动词 base → (过去式, 过去分词)；"-" 表示无过去分词
IRREGULAR_VERBS: dict[str, tuple[str, str]] = {
    "go": ("went", "gone"), "do": ("did", "done"), "have": ("had", "had"),
    "make": ("made", "made"), "see": ("saw", "seen"), "come": ("came", "come"),
    "take": ("took", "taken"), "give": ("gave", "given"), "get": ("got", "got"),
    "buy": ("bought", "bought"), "bring": ("brought", "brought"),
    "think": ("thought", "thought"), "teach": ("taught", "taught"),
    "catch": ("caught", "caught"), "become": ("became", "become"),
    "run": ("ran", "run"), "swim": ("swam", "swum"), "begin": ("began", "begun"),
    "drink": ("drank", "drunk"), "sing": ("sang", "sung"),
    "write": ("wrote", "written"), "ride": ("rode", "ridden"),
    "drive": ("drove", "driven"), "rise": ("rose", "risen"),
    "break": ("broke", "broken"), "speak": ("spoke", "spoken"),
    "eat": ("ate", "eaten"), "fall": ("fell", "fallen"), "know": ("knew", "known"),
    "throw": ("threw", "thrown"), "fly": ("flew", "flown"), "draw": ("drew", "drawn"),
    "show": ("showed", "shown"), "wear": ("wore", "worn"), "beat": ("beat", "beaten"),
    "hit": ("hit", "hit"), "hurt": ("hurt", "hurt"), "let": ("let", "let"),
    "put": ("put", "put"), "cut": ("cut", "cut"), "cost": ("cost", "cost"),
    "read": ("read", "read"), "set": ("set", "set"), "shut": ("shut", "shut"),
    "lend": ("lent", "lent"), "send": ("sent", "sent"), "spend": ("spent", "spent"),
    "build": ("built", "built"), "feel": ("felt", "felt"), "keep": ("kept", "kept"),
    "sleep": ("slept", "slept"), "leave": ("left", "left"), "meet": ("met", "met"),
    "feed": ("fed", "fed"), "hold": ("held", "held"), "find": ("found", "found"),
    "tell": ("told", "told"), "sell": ("sold", "sold"), "stand": ("stood", "stood"),
    "sit": ("sat", "sat"), "win": ("won", "won"), "lose": ("lost", "lost"),
    "say": ("said", "said"), "pay": ("paid", "paid"), "lay": ("laid", "laid"),
    "mean": ("meant", "meant"), "learn": ("learnt", "learnt"),
    "smell": ("smelt", "smelt"), "spell": ("spelt", "spelt"),
}

# 不规则形容词/副词 base → (比较级, 最高级)
IRREGULAR_ADJ: dict[str, tuple[str, str]] = {
    "good": ("better", "best"), "well": ("better", "best"),
    "bad": ("worse", "worst"), "ill": ("worse", "worst"),
    "many": ("more", "most"), "much": ("more", "most"),
    "little": ("less", "least"), "far": ("farther", "farthest"),
}

# 后缀 → 词性（pos 推不出时兜底）
_SUFFIX_POS = [
    ("ly", "adv"),
    ("tion", "n"), ("sion", "n"), ("ment", "n"), ("ness", "n"),
    ("ity", "n"), ("ance", "n"), ("ence", "n"), ("ist", "n"),
    ("ful", "adj"), ("ous", "adj"), ("ive", "adj"), ("able", "adj"),
    ("ible", "adj"), ("al", "adj"), ("ic", "adj"), ("ish", "adj"),
    ("ize", "v"), ("ise", "v"), ("ify", "v"),
]

VOWELS = set("aeiou")


@dataclass
class WordEntry:
    word: str
    freq: int
    pos: list[str] = field(default_factory=list)
    meanings: list[str] = field(default_factory=list)
    forms: dict = field(default_factory=dict)
    forms_note: str = ""  # 词形变化的可靠性说明
    sources: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 句子配对
# --------------------------------------------------------------------------- #


def _cn_count(s: str) -> int:
    return sum(1 for c in s if "一" <= c <= "鿿")


def _en_count(s: str) -> int:
    return sum(1 for c in s if c.isascii() and c.isalpha())


def _line_kind(line: str) -> Optional[str]:
    cn, en = _cn_count(line), _en_count(line)
    if cn > 0 and cn >= en:
        return "zh"
    if en > 0:
        return "en"
    return None


def pair_sentences(text: str) -> list[tuple[str, str]]:
    """把多行文本里"英文句 + 紧跟中文句"配对成 (en, zh)。

    >>> pair_sentences("He goes to school.\\n他去上学。\\nx")
    [('He goes to school.', '他去上学。')]
    """
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    out = []
    i = 0
    while i < len(lines):
        if _line_kind(lines[i]) == "en" and len(lines[i]) < 120:
            if i + 1 < len(lines) and _line_kind(lines[i + 1]) == "zh" and len(lines[i + 1]) < 60:
                out.append((lines[i], lines[i + 1]))
                i += 2
            else:
                i += 1
        else:
            i += 1
    return out


# --------------------------------------------------------------------------- #
# 词形变化
# --------------------------------------------------------------------------- #


def _cvc_doubles(word: str) -> bool:
    """辅音-元音-辅音 且末尾不是 w/x/y → 双写末字母。"""
    return (
        len(word) >= 3
        and word[-1] not in VOWELS
        and word[-1] not in "wxy"
        and word[-2] in VOWELS
        and word[-3] not in VOWELS
    )


def regular_past(word: str) -> str:
    if word.endswith("e"):
        return word + "d"
    if word.endswith("y") and len(word) > 1 and word[-2] not in VOWELS:
        return word[:-1] + "ied"
    if _cvc_doubles(word):
        return word + word[-1] + "ed"
    return word + "ed"


def regular_ing(word: str) -> str:
    if word.endswith("ie"):
        return word[:-2] + "ying"
    if word.endswith("e"):
        return word[:-1] + "ing"
    if _cvc_doubles(word):
        return word + word[-1] + "ing"
    return word + "ing"


def regular_third(word: str) -> str:
    if word.endswith(("s", "x", "ch", "sh", "o")):
        return word + "es"
    if word.endswith("y") and len(word) > 1 and word[-2] not in VOWELS:
        return word[:-1] + "ies"
    return word + "s"


def regular_er(word: str) -> str:
    if word.endswith("e"):
        return word[:-1] + "er"
    if word.endswith("y") and len(word) > 1 and word[-2] not in VOWELS:
        return word[:-1] + "ier"
    if _cvc_doubles(word):
        return word + word[-1] + "er"
    return word + "er"


def regular_est(word: str) -> str:
    if word.endswith("e"):
        return word[:-1] + "est"
    if word.endswith("y") and len(word) > 1 and word[-2] not in VOWELS:
        return word[:-1] + "iest"
    if _cvc_doubles(word):
        return word + word[-1] + "est"
    return word + "est"


def word_forms(word: str, pos: Iterable[str], eng=None) -> tuple[dict, str]:
    """按词性返回词形变化与可靠性说明。"""
    pos = set(pos)
    forms: dict = {}
    note = ""
    if "n" in pos or not pos:
        try:
            if eng is None:
                import inflect

                eng = inflect.engine()
            pl = eng.plural_noun(word)
            if pl and pl != word:
                forms["plural"] = pl
        except Exception:
            pass
    if "v" in pos or not pos:
        if word in IRREGULAR_VERBS:
            past, pp = IRREGULAR_VERBS[word]
            forms["past"] = past
            if pp != "-":
                forms["past_participle"] = pp
            forms["present_participle"] = regular_ing(word)
            forms["third_singular"] = regular_third(word)
            note = "不规则动词（来自内置表）"
        else:
            forms["past"] = regular_past(word)
            forms["past_participle"] = regular_past(word)
            forms["present_participle"] = regular_ing(word)
            forms["third_singular"] = regular_third(word)
            note = "规则推断（可能不适用于不规则动词）"
    if "adj" in pos or not pos:
        if word in IRREGULAR_ADJ:
            forms["comparative"], forms["superlative"] = IRREGULAR_ADJ[word]
        else:
            forms["comparative"] = regular_er(word)
            forms["superlative"] = regular_est(word)
    # pos 为空时只保留名词/动词变化，避免每个词都给一整套
    if not pos:
        note = (note + "；词性未确定，仅给出常见变化").strip("；")
    return forms, note


def guess_pos(word: str) -> list[str]:
    for suf, p in _SUFFIX_POS:
        if word.endswith(suf):
            return [p]
    return []


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #


_MEANING_NOISE = re.compile(r"题目|解析|选项|答案|考点|规律|总结|综上所述|考法|做题")
_MEANING_BAD_START = re.compile(r"^[\d一二三四五六七八九十]+[)）、.]")


def _meaning_ok(zh: str) -> bool:
    """释义行是否干净（排除解析/选项/编号开头/表格残片等噪声）。"""
    if not zh or len(zh) > 40:
        return False
    if _MEANING_NOISE.search(zh):
        return False
    if _MEANING_BAD_START.match(zh):
        return False
    # 表格/解析残片：省略号、成对引号、单字母行
    if "……" in zh or zh.count('"') >= 2 or len(zh.strip()) <= 1:
        return False
    return True


def build_vocabulary(
    kps: list[KnowledgePoint],
    limit: int = 300,
    min_freq: int = 2,
) -> list[WordEntry]:
    """从知识点语料构建单词表。"""
    try:
        import inflect

        eng = inflect.engine()
    except Exception:
        eng = None

    stats: dict = defaultdict(
        lambda: {"freq": 0, "sources": {}, "meanings": [], "subcats": {}}
    )

    for kp in kps:
        subcat = next((t for t in kp.tags if t in SUBCAT_POS), None)
        src = {"kp_id": kp.id, "lecture": kp.lecture_number, "title": kp.title}
        for en_line, zh in pair_sentences(kp.examples_md + "\n" + kp.body_md):
            for w in WORD_RE.findall(en_line):
                wl = w.lower()
                if len(wl) < 2 or wl in STOP_WORDS or wl.isdigit():
                    continue
                s = stats[wl]
                s["freq"] += 1
                s["sources"][src["kp_id"]] = src
                if subcat:
                    s["subcats"][subcat] = s["subcats"].get(subcat, 0) + 1
                if _meaning_ok(zh) and zh not in s["meanings"] and len(s["meanings"]) < 5:
                    s["meanings"].append(zh)

    items = [(w, d) for w, d in stats.items() if d["freq"] >= min_freq]
    items.sort(key=lambda x: (-x[1]["freq"], x[0]))

    out: list[WordEntry] = []
    for w, d in items[:limit]:
        # 词性推断优先级：不规则动词/形容词表 > 后缀规则 > 所在讲细分（仅兜底）
        # （按"所在讲"推词性不可靠：连词讲的例句里 school 并非连词）
        pos: list[str] = []
        if w in IRREGULAR_VERBS:
            pos.append("v")
        if w in IRREGULAR_ADJ:
            pos.append("adj")
        if not pos:
            pos = guess_pos(w)
        if not pos:
            for sc, _ in sorted(d["subcats"].items(), key=lambda x: -x[1])[:1]:
                p = SUBCAT_POS.get(sc)
                if p:
                    pos.append(p)
                    break
        forms, note = word_forms(w, pos, eng)
        out.append(
            WordEntry(
                word=w,
                freq=d["freq"],
                pos=pos,
                meanings=d["meanings"][:3],
                forms=forms,
                forms_note=note,
                sources=list(d["sources"].values())[:5],
            )
        )
    return out

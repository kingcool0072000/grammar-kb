"""单词表构建（基于讲义语料，纯函数，便于单测）。

数据来源：知识点的 ``examples_md`` / ``body_md`` 里的中英对照例句。
- 词频：英文 token 统计（去停用词/标点/数字）；屈折变形先还原成原形再计数
  （went/going/goes 合并进 go），词条词形变化因此天然正确
- 词性：人工词表 > 内置不规则动词/形容词表 > 后缀规则 > 专名大写检测；
  不按"所在讲次细分"推断（曾把 mike/before/out 标成数词、happy 标成代词、
  now 标成冠词，属系统性错标）
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
    needn't hadn't haven't mustn't shan't let's sb sth
    s t d ll re ve m
    """.split()
)

# 细分（来自 lecture.subcategory / kp.tags）→ 词性。
# 注意：仅用于展示，不再用于给单词标词性——"数词"课例句里的 before 并不是数词，
# 按讲次推词性正是此前 mike/before/out→数词、happy→代词、now→冠词的错标根因。
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

# 缩写残片（撇号匹配遗漏留下的碎片），计入词表只会成为噪声，直接忽略
STOP_FRAGMENTS = frozenset(
    """don doesn aren isn wasn weren couldn shouldn wouldn mustn
    hasn haven didn shan""".split()
)

# --------------------------------------------------------------------------- #
# 不规则变化表与后缀规则（词形变化 + 屈折还原共用）
# --------------------------------------------------------------------------- #

VOWELS = set("aeiou")

# 辅音双写例外：重音在第一音节，不双写（visited/listened/happened/covered…）
NO_DOUBLE = frozenset("""
    visit listen open develop happen enter cover remember return appear
    cheer wait seat rain clean turn hear borrow follow hurry worry
""".split())

# 初中常见不规则动词 base → (过去式, 过去分词)
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
    "wake": ("woke", "woken"), "spill": ("spilt", "spilt"),
    "forget": ("forgot", "forgotten"),
    "hear": ("heard", "heard"),
}

# 不规则复数 复数 → 单数
IRREGULAR_PLURAL: dict[str, str] = {
    "feet": "foot", "teeth": "tooth", "men": "man", "women": "woman",
    "children": "child",
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

# --------------------------------------------------------------------------- #
# 词性人工词表
#
# 词性只按以下优先级推断：人工词表 > 不规则动词/形容词表 > 后缀规则 >
# 专名大写检测。绝不按"所在讲次细分"推断——那会把数词课例句里的 before
# 标成数词、代词课里的 happy 标成代词、冠词课里的 now 标成冠词。
# 封闭类（数词/代词/连词/介词）成员有限可穷举，最可靠；开放类挑语料高频词。
# --------------------------------------------------------------------------- #

# 基数词 / 序数词 / 频次词（封闭类）
NUM_WORDS = frozenset("""
    zero one two three four five six seven eight nine ten eleven twelve
    thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty
    thirty forty fifty sixty seventy eighty ninety
    hundred thousand million billion dozen
    first second third fourth fifth sixth seventh eighth ninth tenth
    once twice
""".split())

# 代词 / 不定限定词（封闭类；人称/指示代词已在停用词表）
PRON_WORDS = frozenset("""
    some any every each either neither none another several both all
    most other few such nobody everybody everyone everything
    somebody someone something anybody anyone anything nothing
    myself yourself yourselves himself herself itself ourselves themselves
    whatever
""".split())

# 从属连词（封闭类；and/or/if/but/so/than 等已在停用词表）
CONJ_WORDS = frozenset("""
    because although though unless whether while till since until nor thus
""".split())

# 介词与小品词（封闭类；in/on/at/for 等已在停用词表）
PREP_WORDS = frozenset("""
    under over above below near beside besides between among around behind
    inside outside without during against through across past off up down
    out away before after except
""".split())

# 常见副词（半封闭，挑中学语料高频）
ADV_WORDS = frozenset("""
    now always usually often sometimes never ever soon later fast hard
    quite almost already still yet ago today tomorrow yesterday tonight
    early late finally suddenly carefully quickly slowly again together
    home downstairs upstairs instead maybe certainly even rather enough
    abroad outdoors back else please earlier faster harder more most
""".split())

# 常见动词（开放类，挑讲义语料高频）
VERB_WORDS = frozenset("""
    come stay matter invite talk turn cancel clean enjoy study stop wake
    work accept ask attend break sell tell travel allow belong borrow
    call fall follow hear invent like miss offer plant sit solve spell
    hurry listen play rain read start take watch cook go help speak
    know think find feel keep let learn teach write walk run eat drink
    bring send spend meet stand win lose pay leave hold sleep sing
    dance laugh cry smile open close finish love want need hope wish
    try visit arrive reach move look use remember answer believe
    live raise happen improve develop understand forget wonder worry
    enter wait cover change complete prepare receive return seat taste
    sound appear cheer paint raise forget forget
""".split())

# 常见名词（开放类，挑讲义语料高频）
NOUN_WORDS = frozenset("""
    school mother father sister brother student teacher day boy girl
    house room water food apple homework friend door tree baby
    dictionary parent bottle chess coffee success speaking wood bike
    child earth football plan history gate match morning palace plate
    schoolbag sea son spring story supper team world park progress rest
    birthday price job idea advice problem building bird shoe computer
    teacher pair museum paper picture classroom church subject question
    answer way thing time life home word name book pen bag class grade
    lesson test exam exercise city country street river mountain lake
    zoo animal cat dog fish flower grass weather rain snow wind cloud
    sky sun moon star night afternoon evening week month year hour
    minute times luck people bus car dinner news front accident east
    lot project airport desk hand man order part party phone place plane
    police speech case boat cake concert crowd culture farm foot feet
    film floor forest gift glasses grandparent ground head health heart
    host milk mistake mobile movie newspaper purpose reason road roof
    row secret singer song summer town train trousers window wine table
    lie truth clothes cloth exchange painting paintings smoke
""".split())

# 常见形容词（开放类，挑讲义语料高频）
ADJ_WORDS = frozenset("""
    happy important interesting kind fine sure wrong clean convenient
    blue sweet new tall busy easy difficult free cold hot warm cool
    young old big small long short high low great nice lovely beautiful
    tired thirsty hungry afraid glad sorry lucky famous heavy
    last next red asleep awake aware interested honest necessary
    soft strong surprised surprising whole wide wooden dead absent
    better best further near light right fun amazed born
""".split())

# 专有名词（人名/地名；语料高频，大写检测覆盖不到句首场景，显式收录）
PROPER_WORDS = frozenset("""
    mike tom mary jill kate alice jack jane joe tim
    canada shandong
""".split())

def _build_lexicon() -> dict[str, tuple[str, ...]]:
    lex: dict[str, tuple[str, ...]] = {}
    for words, pos in (
        (VERB_WORDS, "v"), (NOUN_WORDS, "n"), (ADJ_WORDS, "adj"),
        (ADV_WORDS, "adv"), (PRON_WORDS, "pron"), (CONJ_WORDS, "conj"),
        (PREP_WORDS, "prep"), (NUM_WORDS, "num"), (PROPER_WORDS, "proper"),
    ):
        for w in words:
            merged = tuple(dict.fromkeys([*lex.get(w, ()), pos]))
            lex[w] = merged
    return lex

LEXICON = _build_lexicon()

# --------------------------------------------------------------------------- #
# 屈折还原（lemmatize）：把 went/going/goes 等还原成原形 go 再计数
# --------------------------------------------------------------------------- #

_INFLECT_MAP: dict[str, str] | None = None
_AMBIGUOUS_FORMS: frozenset[str] | None = None


def _get_inflect_maps() -> tuple[dict[str, str], frozenset[str]]:
    """构建 变形→原形 映射；有歧义的变形（多个原形）标记后不还原。"""
    global _INFLECT_MAP, _AMBIGUOUS_FORMS
    if _INFLECT_MAP is not None:
        return _INFLECT_MAP, _AMBIGUOUS_FORMS

    candidates: dict[str, set[str]] = defaultdict(set)

    def add(form: str, base: str) -> None:
        if form != base and form not in STOP_WORDS:
            candidates[form].add(base)

    # 不规则表（含三单：goes→go）
    for base, (past, pp) in IRREGULAR_VERBS.items():
        add(past, base)
        if pp != "-":
            add(pp, base)
        add(regular_ing(base), base)
        add(regular_third(base), base)
    for base, (er, est) in IRREGULAR_ADJ.items():
        add(er, base)
        add(est, base)

    # 词表内的规则动词/形容词，按规则生成变形
    for base in VERB_WORDS:
        if base in IRREGULAR_VERBS:
            continue
        add(regular_past(base), base)
        add(regular_ing(base), base)
        add(regular_third(base), base)
    for base in ADJ_WORDS:
        if base in IRREGULAR_ADJ:
            continue
        add(regular_er(base), base)
        add(regular_est(base), base)

    # 不规则复数（feet→foot 等）
    for plural, singular in IRREGULAR_PLURAL.items():
        add(plural, singular)

    # 规则名词复数（仅词表内名词，避免全语料爆炸/歧义）
    for base in NOUN_WORDS:
        if base.endswith(("s", "x", "ch", "sh")):
            add(base + "es", base)
        elif base.endswith("y") and len(base) > 1 and base[-2] not in VOWELS:
            add(base[:-1] + "ies", base)
        else:
            add(base + "s", base)

    ambiguous = {f for f, bases in candidates.items() if len(bases) > 1}
    mapping = {f: next(iter(bases)) for f, bases in candidates.items() if len(bases) == 1}

    _INFLECT_MAP = mapping
    _AMBIGUOUS_FORMS = frozenset(ambiguous)
    return mapping, ambiguous


def lemmatize(token: str) -> str:
    """返回 token 的原形；不在映射内则原样返回。"""
    mapping, _ = _get_inflect_maps()
    return mapping.get(token, token)


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
    if word in NO_DOUBLE:
        return False
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

    # capital 记录该词以"非句首大写"出现的次数与总次数，用于专名检测：
    # 语料里始终大写（如 Tom/Mike/Canada）→ 专有名词；仅句首大写 → 普通词
    stats: dict = defaultdict(
        lambda: {"freq": 0, "sources": {}, "meanings": [], "cap": 0}
    )
    mapping, ambiguous = _get_inflect_maps()

    for kp in kps:
        src = {"kp_id": kp.id, "lecture": kp.lecture_number, "title": kp.title}
        for en_line, zh in pair_sentences(kp.examples_md + "\n" + kp.body_md):
            for m in WORD_RE.finditer(en_line):
                w = m.group(0)
                wl = w.lower()
                if len(wl) < 2 or wl in STOP_WORDS or wl in STOP_FRAGMENTS:
                    continue
                if wl in ambiguous:  # 多个可能原形的变形（如 left），不还原
                    pass
                else:
                    wl = mapping.get(wl, wl)
                s = stats[wl]
                s["freq"] += 1
                s["sources"][src["kp_id"]] = src
                # 非句首位置仍大写 → 专名证据
                if m.start() > 0 and w[0].isupper():
                    s["cap"] += 1
                if _meaning_ok(zh) and zh not in s["meanings"] and len(s["meanings"]) < 5:
                    s["meanings"].append(zh)

    items = [(w, d) for w, d in stats.items() if d["freq"] >= min_freq]
    items.sort(key=lambda x: (-x[1]["freq"], x[0]))

    out: list[WordEntry] = []
    for w, d in items[:limit]:
        pos = infer_pos(w, d)
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


def infer_pos(word: str, d: dict) -> list[str]:
    """词性推断：人工词表 > 不规则动词/形容词表 > 后缀规则 > 专名检测。

    绝不按"所在讲次细分"推断（错标根因）。推不出就留空（前端显示为"其它"），
    错误的词性比没有词性更糟。
    """
    lex = LEXICON.get(word)
    if lex:
        # proper 与 n 并存时保留两者（前端把 proper 显示为专名标记）
        return list(lex)
    if word in IRREGULAR_VERBS:
        return ["v"]
    if word in IRREGULAR_ADJ:
        return ["adj"]
    suf = guess_pos(word)
    if suf:
        return suf
    # 专名检测：非句首大写占多数（≥2 次且过半）→ 专有名词。
    # 不能要求全部大写：Tom 偶尔落在句首时首字母大写不构成专名证据。
    if d.get("cap", 0) >= 2 and d["cap"] * 2 >= d["freq"]:
        return ["n", "proper"]  # proper 标记专有名词，前端可显示为人名/地名
    return []

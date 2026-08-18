"""单词表构建（基于讲义语料，纯函数，便于单测）。

数据来源：知识点 ``examples_md`` / ``body_md`` 的中英对照例句 + ECDICT 精简词典
（``data/ecdict-slim.json``，取自 https://github.com/skywind3000/ECDICT ，仅收录语料词）。

- 词频：英文 token 统计（去停用词/标点/数字）；屈折变形先还原成原形再计数
  （went/going/goes 合并进 go）
- 词性：ECDICT 词性 > 人工词表 > 不规则表 > 后缀规则（多音节/专名词保护）>
  专名大写检测。绝不按"所在讲次细分"推断（曾把 music/picnic/english 标成
  形容词、mike/before/out 标成数词，属系统性错标）
- 词形变化：优先 ECDICT exchange（p:过去式 d:过去分词 i:进行时 3:三单
  s:复数 r:比较级 t:最高级）；缺失才用内置规则；多音节形容词不给 er/est
  （interestinger 之类是错误形式，正确是 more interesting）
- 释义：``gloss`` 取 ECDICT 简明中文释义，``examples`` 为语料中英对照例句对；
  ``meanings``（例句整句中文）保留以兼容旧前端
- 音标/来源：phonetic 取 ECDICT；来源溯源到知识点/讲次
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .models import KnowledgePoint

# ECDICT 精简词典：word → {ph, t(中文释义), pos[], ex(词形变化编码)}
_ECDICT: dict | None = None
_ECDICT_PATHS = (
    __import__("pathlib").Path(__file__).resolve().parent.parent / "data" / "ecdict-slim.json",
    __import__("pathlib").Path(__file__).resolve().parent / "data" / "ecdict-slim.json",
)


def load_ecdict() -> dict:
    """惰性加载 ECDICT 精简词典（约 270KB，随库分发）；不可用时返回空表。"""
    global _ECDICT
    if _ECDICT is not None:
        return _ECDICT
    import json

    for path in _ECDICT_PATHS:
        try:
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    _ECDICT = json.load(f)
                return _ECDICT
        except Exception:
            continue
    _ECDICT = {}
    return _ECDICT


# ECDICT exchange 编码 → 我们的 forms 键
_EX_KEY = {"p": "past", "d": "past_participle", "i": "present_participle",
           "3": "third_singular", "s": "plural", "r": "comparative", "t": "superlative"}

# 多音节形容词/副词不用 er/est（正确形式是 more/most + adj）：
# 依据：以 -ing/-ed/-ful/-ous/-ive/-able/-ible/-ant/-ent/-less/-ish(专名除外) 结尾，
# 或 3 音节以上（粗略用 元音组数>2 判定）
_NO_ER_EST_SUFFIXES = ("ing", "ed", "ful", "ous", "ive", "able", "ible", "ant", "ent", "less", "some")


def _can_take_er_est(word: str) -> bool:
    """是否允许给该形容词生成规则比较级/最高级。"""
    if len(word) <= 2:
        return False
    for suf in _NO_ER_EST_SUFFIXES:
        if word.endswith(suf):
            return False
    # 音节粗估：相邻元音合并成组，>2 组视为多音节
    groups = 0
    prev_vowel = False
    for ch in word:
        if ch in VOWELS:
            if not prev_vowel:
                groups += 1
            prev_vowel = True
        else:
            prev_vowel = False
    if word.endswith("e") and word[-2:-1] and word[-2] not in VOWELS:
        groups += 0  # 词尾哑 e 不计音节
    return groups <= 2

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

# 后缀 → 词性（兜底，已大幅收紧）。
# 删除了 ic/ish/al 等误判重灾区（music/picnic 是名词、english 是专名），
# 且要求最小长度，避免 "go+ly" 之类碎片误命中。
_SUFFIX_POS = [
    ("ly", "adv"),
    ("tion", "n"), ("sion", "n"), ("ment", "n"), ("ness", "n"),
    ("ity", "n"), ("ance", "n"), ("ence", "n"), ("ist", "n"),
    ("ful", "adj"), ("ous", "adj"), ("ive", "adj"), ("able", "adj"),
    ("ible", "adj"),
    ("ize", "v"), ("ise", "v"), ("ify", "v"),
]

# 后缀规则排出的例外（仍按名词/不动处理）
_SUFFIX_EXCLUDE = frozenset(
    "music picnic magic traffic plastic electric english spanish".split()
)

# 不可数名词（无复数）与语言/国名形容词（无比较级最高级）
UNCOUNTABLE_NOUNS = frozenset(
    "music water coffee milk tea rice bread money news advice information "
    "furniture luggage homework housework traffic weather fun luck time "
    "english chinese japanese french german spanish".split()
)
NO_COMPARATIVE = frozenset(
    "english chinese japanese french german spanish american british "
    "alive asleep awake alone afraid wooden daily".split()
)


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

# 地名/国名词典释义行（精确匹配，去掉空格后比对）
# 专名兜底释义（词典缺失或首义为器具/动物等噪声时）
PROPER_GLOSS_OVERRIDE = {
    "beijing": "北京（中国首都）",
    "china": "中国",
    "alice": "爱丽丝（女子名）",
    "jack": "杰克（男子名）",
    "mike": "迈克（男子名）",
    "tom": "汤姆（男子名）",
}

PROPER_GLOSS = frozenset(
    ["北京(中华人民共和国首都)", "加拿大", "中国", "美国", "英国", "澳大利亚",
     "法国", "日本", "山东(位于中国东部沿海、黄河下游)", "电视", " Television的简称",
     "伦敦", "巴黎", "纽约"]
)

LEXICON = _build_lexicon()
# 完全不做屈折变化的词：功能词（限定词/代词/连词/副词小品词）、数词、专名。
# 这些词即使词典给了 a./n. 义也不该生成 thousander/nower/alls 之类的形式
NO_INFLECT = (
    NUM_WORDS
    | PRON_WORDS
    | CONJ_WORDS
    | PREP_WORDS
    | frozenset(
        "now here there all some any no none every each both either neither "
        "such more most much many little few less least good well better best "
        "not very too also only just even still yet again first last next "
        "up down out off back home away china beijing shandong canada "
        "tom mary jack jane joe jill kate alice mike tim tv "
        "enough abroad quite rather almost still already soon honest "
        "earlier faster harder nearest further farther lovely friendly lively "
        "sunday monday tuesday wednesday thursday friday saturday "
        "january february march april may june july august september "
        "october november december".split()
    )
)

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
    display: str = ""    # 展示形式（专名大写：beijing → Beijing；空 = 同 word）
    phonetic: str = ""   # ECDICT 音标
    gloss: str = ""      # ECDICT 简明中文释义（兼容字段，按行拼接）
    gloss_lines: list = field(default_factory=list)  # [{pos:'名词', text:'英语'}] 按词性分行
    examples: list = field(default_factory=list)  # [{en, zh}] 语料中英对照例句


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


def word_forms(word: str, pos: Iterable[str], eng=None, dict_entry=None) -> tuple[dict, str]:
    """按词性返回词形变化与可靠性说明。词典 exchange 优先，内置规则兜底。"""
    pos = set(pos)
    if word in NO_INFLECT:
        return {}, ""
    if dict_entry and dict_entry.get("forms"):
        return dict(dict_entry["forms"]), "词典实测词形（ECDICT）"
    forms: dict = {}
    note = ""

    # --- ECDICT exchange 优先（词典实测形式，最可靠）---
    ec = load_ecdict().get(word)
    if ec and ec.get("ex"):
        for part in ec["ex"].split("/"):
            code, _, val = part.partition(":")
            key = _EX_KEY.get(code)
            if key == "plural" and word in UNCOUNTABLE_NOUNS:
                continue
            if key and val and val != word and key not in forms:
                forms[key] = val
        if forms:
            return forms, "词典实测词形（ECDICT）"

    if "n" in pos or not pos:
        if word not in UNCOUNTABLE_NOUNS:
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
        elif word not in NO_COMPARATIVE and _can_take_er_est(word):
            forms["comparative"] = regular_er(word)
            forms["superlative"] = regular_est(word)
        # 注：形容词不做复数（lovelies/friendlies 不是形容词词形）——
        # 名词分支只在 pos 含 n 时给复数，此处不加
        # 多音节形容词不给 er/est：正确形式是 more/most + adj，不属于词形变化表
    # pos 为空时只保留名词/动词变化，避免每个词都给一整套
    if not pos:
        note = (note + "；词性未确定，仅给出常见变化").strip("；")
    return forms, note


def guess_pos(word: str) -> list[str]:
    if word in _SUFFIX_EXCLUDE:
        return []
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


# ECDICT 释义行的词性缩写 → 中文标签
_GLOSS_POS_CN = {
    "n": "名词", "v": "动词", "vi": "不及物动词", "vt": "及物动词", "aux": "助动词",
    "a": "形容词", "ad": "副词", "adv": "副词", "p": "介词", "prep": "介词",
    "conj": "连词", "pron": "代词", "num": "数词", "int": "感叹词",
}


def _gloss_split(raw) -> list[str]:
    """词典 t 字段兼容两种格式：list（分行）或 str（含字面 \n 或 ' / ' 分隔）。"""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [
        ln.strip()
        for ln in raw.replace("\\n", "\n").split("\n")
        if ln.strip()
    ]


def _gloss_lines(raw) -> list[dict]:
    """释义行 "n. 英语" → {pos:"名词", text:"英语"}；无词性前缀的行 text 原样。"""
    out = []
    for ln in _gloss_split(raw)[:3]:
        m = re.match(r"^(vi|vt|aux|int|adv|ad|prep|conj|pron|num|n|v|a|p|m|u|c)\.\s*(.+)$", ln)
        if m:
            tag = m.group(1)
            pos_cn = _GLOSS_POS_CN.get(tag, tag)
            text = m.group(2).strip()
            if text:
                out.append({"pos": pos_cn, "text": text})
        else:
            out.append({"pos": "", "text": ln})
    return out


def _gloss_join(raw) -> str:
    return " / ".join(x["text"] for x in _gloss_lines(raw))


def build_vocabulary(
    kps: list[KnowledgePoint],
    limit: int = 300,
    min_freq: int = 2,
    dict_db=None,
) -> list[WordEntry]:
    """从知识点语料构建单词表。

    词条词典信息（音标/释义/词形/词性）优先来自 ``dict_db``（ECDICT 全量），
    未提供或未命中时退回内置 slim 词典与规则推断——保证单测无词典环境可跑。
    """
    try:
        import inflect

        eng = inflect.engine()
    except Exception:
        eng = None

    if dict_db is not None and getattr(dict_db, "available", False):
        dlookup = dict_db.lookup
    else:
        dlookup = None

    # capital 记录该词以"非句首大写"出现的次数与总次数，用于专名检测：
    # 语料里始终大写（如 Tom/Mike/Canada）→ 专有名词；仅句首大写 → 普通词
    stats: dict = defaultdict(
        lambda: {"freq": 0, "sources": {}, "meanings": [], "cap": 0,
                 "examples": [], "forms_seen": set(), "spellings": {}}
    )
    # 语料词 → 该词条允许的变形集合（例句高亮时把 went/going/goes 都认作 go）
    lemma_forms: dict = defaultdict(set)
    mapping, ambiguous = _get_inflect_maps()

    for kp in kps:
        src = {"kp_id": kp.id, "lecture": kp.lecture_number, "title": kp.title}
        for en_line, zh in pair_sentences(kp.examples_md + "\n" + kp.body_md):
            # 第一遍：token 归并（变形→原形），记录命中本句的词条
            hit_lemmas: set = set()
            for m in WORD_RE.finditer(en_line):
                w = m.group(0)
                wl = w.lower()
                if len(wl) < 2 or wl in STOP_WORDS or wl in STOP_FRAGMENTS:
                    continue
                token = wl
                if wl not in ambiguous:  # 歧义变形（如 left）不还原
                    token = mapping.get(wl, wl)
                s = stats[token]
                s["freq"] += 1
                s["sources"][src["kp_id"]] = src
                s["spellings"][w] = s["spellings"].get(w, 0) + 1
                if token != wl:
                    s["forms_seen"].add(wl)
                hit_lemmas.add(token)
                # 非句首位置仍大写 → 专名证据
                if m.start() > 0 and w[0].isupper():
                    s["cap"] += 1
            # 第二遍：为命中词条收集中英例句对（去重，最多 3 条）与释义句
            if _meaning_ok(zh):
                for lemma in hit_lemmas:
                    s = stats[lemma]
                    if len(s["examples"]) < 3 and not any(
                        p["en"] == en_line for p in s["examples"]
                    ):
                        s["examples"].append({"en": en_line, "zh": zh})
                    if zh not in s["meanings"] and len(s["meanings"]) < 5:
                        s["meanings"].append(zh)

    items = [(w, d) for w, d in stats.items() if d["freq"] >= min_freq]
    items.sort(key=lambda x: (-x[1]["freq"], x[0]))

    out: list[WordEntry] = []
    ecdict = load_ecdict()
    for w, d in items[:limit]:
        # 词典条目：全量 dict_db 优先，slim json 兜底（{ph,t,pos,ex}）
        ec = dlookup(w) if dlookup else None
        if ec is None:
            raw = ecdict.get(w) or {}
            if raw:
                from .dict_db import EX_KEY as _EK

                forms_d = {}
                for part in (raw.get("ex") or "").split("/"):
                    c, _, v = part.partition(":")
                    k = _EK.get(c)
                    if k and v and v != w and k not in forms_d:
                        forms_d[k] = v
                ec = {
                    "phonetic": raw.get("ph", ""),
                    "gloss_lines": _gloss_lines(raw.get("t", "")),
                    "pos": raw.get("pos", []),
                    "forms": forms_d,
                }
        pos = infer_pos(w, d, ec)
        forms, note = word_forms(w, pos, eng, dict_entry=ec)
        ec = ec or {}
        # 展示形式：取语料中最常见的拼写（专名语料里多大写 → Beijing/Tom）
        display = max(d["spellings"], key=d["spellings"].get) if d["spellings"] else w
        # 词典释义行（gloss_lines）；专名只留人名/地名义行（jack=插座 是噪声）
        gloss_lines = ec.get("gloss_lines") or []
        if "proper" in pos:
            def _bare(ln: dict) -> str:
                return ln.get("text", "").replace(" ", "")

            named = [
                ln for ln in gloss_lines
                if ("（" in ln.get("text", "") and "名" in ln.get("text", ""))
                or "位于" in ln.get("text", "")
                or _bare(ln) in PROPER_GLOSS
            ]
            if named:
                gloss_lines = named[:1]
            elif w in PROPER_GLOSS_OVERRIDE:
                gloss_lines = [{"pos": "", "text": PROPER_GLOSS_OVERRIDE[w]}]
            else:
                gloss_lines = []
        out.append(
            WordEntry(
                word=w,
                display=display if display != w else "",
                freq=d["freq"],
                pos=pos,
                meanings=d["meanings"][:3],
                forms=forms,
                forms_note=note,
                sources=list(d["sources"].values())[:5],
                phonetic=ec.get("phonetic", "") or ec.get("ph", ""),
                gloss=" / ".join(x["text"] for x in gloss_lines),
                gloss_lines=gloss_lines,
                examples=d["examples"][:3],
            )
        )
    return out


# 语料里常见大写但并非专名的词（专有短语成分 the Great Wall / the Yangtze River、
# 星期/月份句首、机构通名 museum/palace）。大写证据对它们不构成 proper 判定
CAP_NOT_PROPER = frozenset(
    "river wall sunday friday saturday monday tuesday wednesday thursday "
    "museum palace amazon greens smiths english french german england france "
    "december january february march april june july august september october november".split()
)


def infer_pos(word: str, d: dict, dict_entry=None) -> list[str]:
    """词性推断：ECDICT > 人工词表 > 不规则表 > 后缀规则 > 专名大写检测。

    绝不按"所在讲次细分"推断（错标根因：数词课里的 before 被标成数词）。
    推不出就留空（前端显示为"其它"），错误的词性比没有词性更糟。
    """
    # 大写证据优先（白名单或语料实测非句首大写过半）：一律判专有名词，
    # 不再混入词典的名词/形容词义（mike 不再显示"话筒"、beijing 不再有复数）
    lex_all = LEXICON.get(word)
    if lex_all and "proper" in lex_all:
        return ["proper"]
    if (
        word not in CAP_NOT_PROPER
        and d.get("cap", 0) >= 2
        and d["cap"] * 2 >= d["freq"]
    ):
        return ["proper"]
    # 词典词性（dict_db 全量优先；slim json 兜底）
    if dict_entry and dict_entry.get("pos"):
        return list(dict_entry["pos"])
    ec = load_ecdict().get(word)
    if ec and ec.get("pos"):
        return list(ec["pos"])

    lex = LEXICON.get(word)
    if lex:
        return list(lex)
    if word in IRREGULAR_VERBS:
        return ["v"]
    if word in IRREGULAR_ADJ:
        return ["adj"]
    # 后缀规则已收紧（见 _SUFFIX_POS 注释），仍推不出再走大写检测
    suf = guess_pos(word)
    if suf:
        return suf
    return []

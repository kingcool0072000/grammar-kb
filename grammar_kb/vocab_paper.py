"""新版单词试卷生成（考试资产：与具体学生无关的 level 卷 + 按学生的混合卷）。

v2 重构口径（与前端 vocabLevel 出题配置、routes_vocab 同契）：

- **统一形态**：生成、存档、取回共用同一 paper dict，``sections`` 存什么
  渲染什么——修复旧版「落库已 shuffle 的词序、取回按位置还原分段」导致
  的分段错乱。
- **五题型**：词形变化(form) / 拼写挖空(spell) / 例句填空(ctx) /
  中译英(zh2en) / 英译中(en2zh)，按 ``ratios`` 配比出题（默认 6/8/12/12/12，
  词形段少一些，例句和中英互译多一些）。
- **分层出题**：简单词（vocab_word.level ≤ SIMPLE_MAX_LEVEL）优先词形变化
  题（extra.forms，ECDICT 实测优先）；复杂词优先例句填空题（example.en 里
  定位该词或其变形挖空）；各自不可行时按序退化，保证每词恰进一个题型段。
- **词形变化只考课堂里真正常见的屈折**：感叹词和话语标记（ok / okay）、过短的
  功能词、没有实质屈折的形容词副词，以及规则硬造、和原形一样的变形（ok→oked）
  不进词形段。名词当头、只是词典顺手标成动词的（crayon 的三单、crowd 的三单）
  也不考，改考复数；play、need 这类课堂动词仍考时态。候选不合格就换下一个词；
  实在不够就缩小该段，不凑垃圾题。动词、名词优先于仅有比较级的形容词。
- **中译英把长释义拆成几条短义**（分号隔开）；**英译中只给英文单词，不标词性**。
- **选词来源可配**（preset / source_mix）：本级或已学词池之外，还能按比例
  混入学生的「积压错词」——艾宾浩斯队列未攻克且已到期（recite_review）
  ∪ 错过且未掌握（recite_word_progress）。
- **seed 可复现**：同 seed 同卷（含题目顺序与挖空位置）。

历史兼容：vocab_paper_history 旧行只存 50 词词序（前 20 中译英、次 20
英译中、末 10 拼写），get_paper 对无 structure 的旧行按此还原并组装成
sections 形态返回。
"""
from __future__ import annotations

import html
import json
import random
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone as _tz
from pathlib import Path

from .atlas_vocab import exam_allowed
from .clock import today_cn
from .dbreg import connect as dbreg_connect
from .dbreg import fce_db_path as _default_fce_db
from .dbreg import grammar_db_path as _default_grammar_db
from .paper_html import render_template
from .vocabulary import (
    ADJ_WORDS,
    IRREGULAR_ADJ,
    IRREGULAR_PLURAL,
    IRREGULAR_VERBS,
    NO_COMPARATIVE,
    NO_INFLECT,
    NOUN_WORDS,
    STOP_WORDS,
    UNCOUNTABLE_NOUNS,
    VERB_WORDS,
    _can_take_er_est,
    regular_er,
    regular_est,
    regular_ing,
    regular_past,
    regular_third,
    word_forms,
)

# 简单词阈值：vocab_word.level ≤ 该值的词优先出词形变化题
SIMPLE_MAX_LEVEL = 1

# 词形变化键 → 中文标签（与 web/src/fmt.js FORM_CN 全称口径一致）
FORM_CN = {
    "past": "过去式", "past_participle": "过去分词", "present_participle": "现在分词",
    "third_singular": "第三人称单数", "plural": "复数",
    "comparative": "比较级", "superlative": "最高级",
}

# 词形题优先级：动词屈折 > 名词复数 > 形容词比较级（副词不进这一段）
_VERB_FORM_KEYS = ("past", "past_participle", "present_participle", "third_singular")
_NOUN_FORM_KEYS = ("plural",)
_ADJ_FORM_KEYS = ("comparative", "superlative")
_FORM_RANK = {k: 0 for k in _VERB_FORM_KEYS}
_FORM_RANK.update({k: 1 for k in _NOUN_FORM_KEYS})
_FORM_RANK.update({k: 2 for k in _ADJ_FORM_KEYS})

# 感叹词 / 话语标记。词典常把它们标成动词或名词（ok 的过去式 oked、
# hey 的复数 heys），词形题没有考察意义。
_DISCOURSE = frozenset("""
    ok okay okey okie hey hi hello hiya bye goodbye byebye
    oh ah aha ha haha wow yeah yep yup ya yes no nope nah
    um uh er erm hmm hm oops ouch ow alas amen hooray hurrah
    yo eh huh whoa gee gosh phew whew ahem psst shh shhh
    yay whoops alright
""".split())

# 词表里的词性缩写（含 wordlist 的 vt/int/convention）→ 考试用标签
_POS_ALIAS = {
    "vt": "v", "vi": "v", "verb": "v",
    "noun": "n", "a": "adj", "adjective": "adj",
    "ad": "adv", "adverb": "adv",
    "int": "interj", "interjection": "interj", "excl": "interj",
    "convention": "interj",
    "art": "det", "determiner": "det",
    "p": "prep", "preposition": "prep",
    "conjunction": "conj", "pronoun": "pron",
    "number": "num", "modal": "aux",
}

# 单个书面形式。带空格的 more beautiful 不算词形。数字只为测试造词（sc000ed）放行。
_FORM_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:['-][A-Za-z0-9]+)*$")

# 不规则复数里试卷值得考、内置表未全收的几对（表内 feet→foot 等会并进来）
_PLURAL_OF = {singular: plural for plural, singular in IRREGULAR_PLURAL.items()}
_PLURAL_OF.update({
    "mouse": "mice", "goose": "geese", "person": "people", "ox": "oxen",
})

# 题型段顺序与中文标题（sections 的固定排列顺序）
SECTION_ORDER = ("form", "spell", "ctx", "zh2en", "en2zh")
SECTION_TITLES = {"form": "词形变化", "spell": "拼写挖空", "ctx": "例句填空",
                  "zh2en": "中译英", "en2zh": "英译中"}

# 默认题型配比（合计 50 题）；custom ratios 只允许覆盖这些键。
# 词形段原先 12 题，又容易被生僻活用占满，默认改成最少的一档。
DEFAULT_RATIOS = {"form": 6, "spell": 8, "ctx": 12, "zh2en": 12, "en2zh": 12}

# 预设选词来源：standard=纯词池；reinforce=六成积压错词 + 四成词池；
# recycle=全量积压错词回炉。显式 source_mix 覆盖 preset。
PRESET_SOURCE_MIX: dict[str, dict | None] = {
    "standard": None,
    "reinforce": {"backlog_wrong": 0.6, "pool": 0.4},
    "recycle": {"backlog_wrong": 1.0},
}

# 与历史试卷重复 ≤10 词
MAX_HISTORY_OVERLAP = 10

# 例句质量护栏：英文字段混入中日韩字符视为语料脏数据（答案串等），不出例句题
_CJK_CHAR = re.compile(r"[一-鿿]")


def _pos_list(pos: str) -> list[str]:
    try:
        v = json.loads(pos or "[]")
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


# 词性中文（试卷展示用）：给考生「词性提示」，也用于选词多样性
POS_CN = {"n": "n. 名词", "v": "v. 动词", "adj": "adj. 形容词", "adv": "adv. 副词",
          "prep": "prep. 介词", "conj": "conj. 连词", "pron": "pron. 代词",
          "num": "num. 数词", "interj": "interj. 感叹词", "aux": "aux. 助动词"}


def _pos_cn(pos: str) -> str:
    """词性串 → 中文提示（如 '["v","n"]' → 'v. 动词 / n. 名词'）。"""
    tags = [t for t in _pos_list(pos) if t in POS_CN]
    return " / ".join(POS_CN[t] for t in tags)


_GLOSS_POS_PREFIX = None


def _strip_gloss_pos(gloss: str) -> str:
    """词库部分 gloss 自带词性前缀（如 'prep. 为，为了…'）——词性已有
    专门列展示，题目里剥掉开头前缀避免重复。"""
    global _GLOSS_POS_PREFIX
    if _GLOSS_POS_PREFIX is None:
        _GLOSS_POS_PREFIX = re.compile(
            r"^(?:n|v|adj|adv|prep|conj|pron|num|interj|aux)\.\s*/?\s*"
            r"(?:(?:n|v|adj|adv|prep|conj|pron|num|interj)\.\s*/?\s*)*")
    return _GLOSS_POS_PREFIX.sub("", gloss or "", count=1)


# 中译英：长释义拆成几条短义。分号、斜杠是义项；逗号是同一义里的近义词。
_SENSE_SPLIT = re.compile(r"[；;/\n|]+")
_SENSE_COMMA = re.compile(r"[,，、]+")
_SENSE_POS = re.compile(
    r"^(?:abbr|aux|adj|adv|prep|conj|pron|num|interj|int|art|vt|vi|ad|n|v|a|p|u|c|m)\.\s*",
    re.IGNORECASE,
)
_SENSE_BRACKET = re.compile(r"\[[^\[\]]*\]|（[^）]*）|\([^)]*\)")
_SENSE_SLANG = re.compile(r"\[(?:美俚|英俚|俚|口|医|化|计|法|经|建|语)")
_SENSE_MAX = 3
_SENSE_LEN = 12


def _short_senses(gloss: str, max_phrases: int = _SENSE_MAX) -> str:
    """长释义 → 最多三条短义，用中文分号隔开。

    「群众,一伙;一堆,许多,大众；v. 拥挤,挤满,挤进」变成「群众；一堆；拥挤」。
    每条义项只留开头的短说法，[美俚] 整段丢掉，避免一整墙中文。
    """
    groups: list[list[str]] = []
    for chunk in _SENSE_SPLIT.split(gloss or ""):
        chunk = chunk.strip()
        if not chunk or _SENSE_SLANG.search(chunk):
            continue
        chunk = _SENSE_BRACKET.sub("", chunk).strip()
        while True:
            nxt = _SENSE_POS.sub("", chunk, count=1)
            if nxt == chunk:
                break
            chunk = nxt.strip()
        bits: list[str] = []
        seen_bit: set[str] = set()
        for bit in _SENSE_COMMA.split(chunk):
            bit = bit.strip(" \t.。·")
            while True:
                nxt = _SENSE_POS.sub("", bit, count=1)
                if nxt == bit:
                    break
                bit = nxt.strip(" \t.。·")
            if not bit or len(bit) > _SENSE_LEN or bit in seen_bit:
                continue
            seen_bit.add(bit)
            bits.append(bit)
        if bits:
            groups.append(bits)
    phrases: list[str] = []
    seen: set[str] = set()
    depth = 0
    while len(phrases) < max_phrases:
        added = False
        for bits in groups:
            if depth >= len(bits) or bits[depth] in seen:
                continue
            seen.add(bits[depth])
            phrases.append(bits[depth])
            added = True
            if len(phrases) >= max_phrases:
                break
        if not added:
            break
        depth += 1
    if phrases:
        return "；".join(phrases)
    flat = _SENSE_BRACKET.sub("", gloss or "").strip()
    while True:
        nxt = _SENSE_POS.sub("", flat, count=1)
        if nxt == flat:
            break
        flat = nxt.strip()
    return flat.strip(" \t.。；;/")[:_SENSE_LEN]


_NAME_GLOSS_RE = None  # 延迟编译


def _is_name_word(pos: str, gloss: str) -> bool:
    """人名/地名/专名判定：pos=proper，或 gloss 明确标注（男子名/女子名/
    人名/…名）。这类词不适合做考题（答案发散、考察意义弱），全局规避。"""
    tags = set(_pos_list(pos))
    if "proper" in tags:
        return True
    gl = gloss or ""
    if "男子名" in gl or "女子名" in gl or "人名" in gl:
        return True
    global _NAME_GLOSS_RE
    if _NAME_GLOSS_RE is None:
        _NAME_GLOSS_RE = re.compile(r"[（(][^)）]*名[)）]")
    return bool(_NAME_GLOSS_RE.search(gl))


def _skip_for_en2zh(pos: str) -> bool:
    """英译中规避：凡 pos 含 prep（介词）或 proper（人名/地名）即排后。

    介词义主导的词（含 like/after/per/beneath 这类多性词）单独给英文
    让考生写中文，答案发散、考察意义弱——优先回避，不够时才兜底。
    """
    tags = set(_pos_list(pos))
    return bool(tags & {"prep", "proper"})


def _grammar_db_path() -> str | None:
    """grammar.db 路径（存在性校验；不可用返回 None）。

    本模块自持一份而非从 vocab_exam 导入私有实现；测试会 monkeypatch
    此名字指到临时库。
    """
    try:
        p = _default_grammar_db()
        return p if Path(p).exists() else None
    except Exception:
        return None


def _loads_dict(raw) -> dict:
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


def _rich_rows(rows) -> list[dict]:
    """vocab_word 原始行 → 富词条（pos 解析、forms/example 解析、专名规避）。

    列按存在性取（测试临时库可能没有 extra/example 列）；forms 值只留
    非空字符串。row 可能来自 sqlite3.Row，用 keys() 探测列。
    """
    out = []
    for r in rows:
        keys = set(r.keys())
        pos = (r["pos"] or "").strip()
        gloss = (r["gloss"] or "").strip()
        if _is_name_word(pos, gloss):
            continue
        extra = _loads_dict(r["extra"]) if "extra" in keys else {}
        forms_raw = extra.get("forms")
        forms = {}
        if isinstance(forms_raw, dict):
            forms = {k: v.strip() for k, v in forms_raw.items()
                     if k in FORM_CN and isinstance(v, str) and v.strip()}
        if not forms:
            # L1+ 词库未做语料增强（extra.forms 仅 L0 有）：按词性现算
            # （ECDICT exchange 实测优先，规则兜底）。与原形相同的变形
            # 不出题（原形不变类）。
            try:
                derived, _note = word_forms(r["word"], _pos_list(pos))
                forms = {k: v.strip() for k, v in derived.items()
                         if k in FORM_CN and isinstance(v, str) and v.strip()
                         and v.strip().lower() != r["word"].lower()}
            except Exception:
                forms = {}
        example = _loads_dict(r["example"]) if "example" in keys else {}
        out.append({"word": r["word"],
                    "level": r["level"] if "level" in keys else None,
                    "pos": pos, "pos_list": _pos_list(pos), "pos_cn": _pos_cn(pos),
                    "gloss": gloss, "gloss_clean": _strip_gloss_pos(gloss),
                    "forms": forms,
                    "example": {"en": str(example.get("en") or ""),
                                "zh": str(example.get("zh") or "")}})
    return out


def _load_words_rich(level: int) -> list[dict]:
    """本级词条：word/level/pos/gloss/forms/example（pos 解析为列表）。

    人名/地名/专名全局规避（pos=proper 或 gloss 标注 男子名/女子名/
    人名）：这类词进考题没有考察意义（tom→汤姆、beijing→北京），
    且中译英答案发散。exam_allowed 过滤词光标记 exam=false 的词。
    """
    path = _grammar_db_path()
    if not path:
        return []
    with dbreg_connect(path, readonly=True) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(vocab_word)")}
        sql = "SELECT word, level, pos, gloss"
        if "extra" in cols:
            sql += ", extra"
        if "example" in cols:
            sql += ", example"
        sql += " FROM vocab_word WHERE level = ?"
        rows = conn.execute(sql, (int(level),)).fetchall()
    rows = [r for r in rows if "extra" not in cols or exam_allowed(r["extra"])]
    return _rich_rows(rows)


def _load_words_by_names(names: list[str]) -> dict[str, dict]:
    """按词面批量取富词条（跨级别；混合卷/积压错词重建用），{word: rich}。"""
    if not names:
        return {}
    path = _grammar_db_path()
    if not path:
        return {}
    out: dict[str, dict] = {}
    chunk = 400
    with dbreg_connect(path, readonly=True) as conn:
        for i in range(0, len(names), chunk):
            part = names[i:i + chunk]
            rows = conn.execute(
                "SELECT word, level, pos, gloss, extra, example FROM vocab_word"
                f" WHERE word IN ({','.join('?' * len(part))})",
                part).fetchall()
            for w in _rich_rows(rows):
                out[w["word"]] = w
    return out


def _load_learned_words(user: str | None, fce_db_path: str | None = None) -> list[dict]:
    """某学生「已学」词（跨级别混合）。

    「已学」= recite_word_progress 里出现过的词，含尚未掌握
    （mastered_at 为空、只错过）的词。不是「已掌握」子集。回
    vocab_word 取释义/词性；词库里没有的词面跳过。
    """
    user = (user or "").strip()[:60]
    if not user:
        return []
    path = fce_db_path or _default_fce_db()
    if not Path(path).exists():
        return []
    try:
        with dbreg_connect(path, readonly=True) as conn:
            rows = conn.execute(
                "SELECT DISTINCT word FROM recite_word_progress WHERE user = ?",
                (user,)).fetchall()
    except sqlite3.OperationalError:
        return []
    learned = [r["word"] for r in rows if r["word"]]
    if not learned:
        return []
    rich = _load_words_by_names(learned)
    return [rich[w] for w in learned if w in rich]


def _load_backlog_wrong(user: str | None, fce_db_path: str | None = None) -> list[dict]:
    """学生「积压错词」= 艾宾浩斯到期未攻克 ∪ 错过且未掌握（并集去重）。

    - recite_review：conquered_at IS NULL 且 next_review <= 北京今天
      （到期还没复习完的词，与 recite 模块 due_now 同口径）
    - recite_word_progress：wrong > 0 且 mastered_at IS NULL（错过、尚未掌握）
    经 _load_words_by_names 富化，词库里没有的词面跳过。
    """
    user = (user or "").strip()[:60]
    if not user:
        return []
    path = fce_db_path or _default_fce_db()
    if not Path(path).exists():
        return []
    today = today_cn().isoformat()
    sql = (
        "SELECT word FROM recite_review"
        " WHERE user = ? AND conquered_at IS NULL"
        " AND next_review IS NOT NULL AND next_review <= ?"
        " UNION SELECT word FROM recite_word_progress"
        " WHERE user = ? AND wrong > 0 AND mastered_at IS NULL"
    )
    try:
        with dbreg_connect(path, readonly=True) as conn:
            rows = conn.execute(sql, (user, today, user)).fetchall()
    except sqlite3.OperationalError:
        return []
    names = [r["word"] for r in rows if r["word"]]
    if not names:
        return []
    rich = _load_words_by_names(names)
    out, seen = [], set()
    for w in names:  # 保持查询顺序（复习队列在前）
        if w in rich and w not in seen:
            seen.add(w)
            out.append(rich[w])
    return out


def _pick_diverse(words: list[dict], n: int, rng: random.Random) -> list[dict]:
    """多样选词：尽量覆盖不同首字母与不同词性（贪心轮转 + 兜底补齐）。"""
    by_initial: dict[str, list] = defaultdict(list)
    for w in words:
        by_initial[(w["word"] or "?")[0].lower()].append(w)
    initials = list(by_initial.keys())
    rng.shuffle(initials)
    for k in initials:
        rng.shuffle(by_initial[k])
    pos_seen: set[str] = set()
    picked: list[dict] = []
    used: set[str] = set()
    for round_no in range(1, 40):
        if len(picked) >= n:
            break
        progressed = False
        for k in initials:
            if len(picked) >= n:
                break
            for w in by_initial[k]:
                if w["word"] in used:
                    continue
                # 前 8 轮跳过已见词性，强制词性多样性；之后放开
                if round_no <= 8 and w["pos"] in pos_seen:
                    continue
                used.add(w["word"])
                pos_seen.add(w["pos"])
                picked.append(w)
                progressed = True
                break
        if not progressed:
            break
    for w in words:  # 兜底：忽略多样性补齐
        if len(picked) >= n:
            break
        if w["word"] not in used:
            used.add(w["word"])
            picked.append(w)
    rng.shuffle(picked)
    return picked[:n]


def _spell_blank(word: str, rng: random.Random) -> tuple[str, str]:
    """拼写挖空：保留首字母，挖 2-3 个非首字母。返回 (展示串, 答案字母)。"""
    letters = list(word)
    if len(letters) < 4:
        blanks = list(range(1, len(letters)))
    else:
        n_blank = min(3, len(letters) - 1)
        blanks = sorted(rng.sample(range(1, len(letters)), n_blank))
    answer = "".join(letters[i] for i in blanks)
    display = "".join("_" if i in blanks else ch for i, ch in enumerate(letters))
    return display, answer


_HISTORY_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS vocab_paper_history ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " level INTEGER NOT NULL,"
    " paper_id TEXT NOT NULL UNIQUE,"
    " words TEXT NOT NULL,"
    " created_at TEXT)")

# 旧表幂等补列（structure 存整卷 paper dict；config 冗余存配置便于直查）
_MIGRATE_COLUMNS = (
    ("structure", "TEXT"),
    ("config", "TEXT"),
    ("scope", "TEXT"),
    ("user", "TEXT DEFAULT ''"),
    ("seed", "INTEGER"),
)


def _normalize_ratios(ratios: dict | None) -> dict[str, int]:
    """题型配比：默认 6/8/12/12/12；custom 只允许覆盖已知题型键。"""
    base = {k: int(v) for k, v in DEFAULT_RATIOS.items()}
    if ratios:
        for k, v in ratios.items():
            if k not in base:
                raise ValueError(f"未知题型 {k}（可选：{'/'.join(SECTION_ORDER)}）")
            v = int(v)
            if v < 0:
                raise ValueError("ratios 各项须 ≥ 0")
            base[k] = v
    if sum(base.values()) < 1:
        raise ValueError("ratios 题量之和须 ≥ 1")
    return base


# ---- 词形变化：哪些词值得考 ----------------------------------------------- #

def _tag_order(w: dict) -> list[str]:
    """词性按词条里的先后保留（名词在前还是动词在前，选题时要看）。"""
    out: list[str] = []
    for raw in w.get("pos_list") or []:
        tag = str(raw or "").strip().lower().rstrip(".")
        if not tag:
            continue
        tag = _POS_ALIAS.get(tag, tag)
        if tag not in out:
            out.append(tag)
    return out


def _closed_class(word: str) -> bool:
    """功能词和话语标记不做词形题。

    不规则动词/形容词，以及人工词表里的实义词除外：do、have、good 仍可考，
    ok、very、the 不考。
    """
    if word in _DISCOURSE:
        return True
    if word in IRREGULAR_VERBS or word in IRREGULAR_ADJ:
        return False
    if word in VERB_WORDS or word in NOUN_WORDS or word in ADJ_WORDS:
        return False
    return word in STOP_WORDS or word in NO_INFLECT


def _has_vowel(word: str) -> bool:
    return any(ch in "aeiouy" for ch in word)


def _has_verb(word: str, tags: set[str]) -> bool:
    if "v" in tags or word in IRREGULAR_VERBS:
        return True
    # 词性空时 word_forms 会给每个词硬造一套动词变形；只信人工动词表
    return not tags and word in VERB_WORDS


def _has_noun(word: str, tags: set[str]) -> bool:
    if word in UNCOUNTABLE_NOUNS:
        return False
    if "n" in tags or word in _PLURAL_OF:
        return True
    return not tags and word in NOUN_WORDS


def _has_adj(word: str, tags: set[str]) -> bool:
    if "adj" in tags or word in IRREGULAR_ADJ:
        return True
    return not tags and word in ADJ_WORDS


# 名词临时活用成动词：以蜡笔作画、用颜料涂、把东西装进盒子。
_DENOMINAL_VERB = re.compile(
    r"(?:用|以|把|给|将).{0,8}(?:画|写|涂|装|包|盖|戴|糊|裹|系|绑|钉|刻|印|染|浇)"
)


def _classroom_verb(word: str) -> bool:
    return word in IRREGULAR_VERBS or word in VERB_WORDS


def _noun_before_verb(order: list[str]) -> bool:
    return "n" in order and "v" in order and order.index("n") < order.index("v")


def _skip_verb_forms(word: str, order: list[str], gloss: str) -> bool:
    """不考「把名词活用成动词」的屈折。

    crayon 的三单、crowd 的三单不是课堂题。两类丢掉动词屈折、只留复数：
    课堂名词表里、又不在课堂动词表的词（crowd、book）；名词义排在前面，
    动词义又是「用/以…画、装、涂」的临时活用（crayon）。
    play、need 在课堂动词表里，promise、refuse 名词在前但不是这种活用，
    仍然考时态。
    """
    if _classroom_verb(word) or "v" not in order:
        return False
    if word in NOUN_WORDS:
        return True
    return _noun_before_verb(order) and bool(_DENOMINAL_VERB.search(gloss or ""))


def _too_short_or_acronym(word: str) -> bool:
    """两字母规则变形（ok→oked）和 tv/dna 这类无元音缩写不考。"""
    if len(word) <= 2:
        return True
    return len(word) <= 4 and not _has_vowel(word)


def _verb_form_ok(word: str, key: str, form: str) -> bool:
    """动词变形要像真正的屈折，而不是 ok→oked 这种规则硬套。"""
    if word in IRREGULAR_VERBS:
        past, pp = IRREGULAR_VERBS[word]
        known = {past, regular_ing(word), regular_third(word)}
        if pp not in ("", "-"):
            known.add(pp)
        known = {x.lower() for x in known if x and x.lower() != word}
        if form in known:
            return True
        # 表内不规则动词不再接受规则 -ed（buyed / goed）
        if key in ("past", "past_participle") and form == regular_past(word):
            return False
        # dream→dreamt 这类词典异体：原形至少 3 个字母，且不是缩写
        return len(word) >= 3 and not _too_short_or_acronym(word)
    if _too_short_or_acronym(word):
        return False
    return True


def _plural_ok(word: str, form: str) -> bool:
    if word in UNCOUNTABLE_NOUNS:
        return False
    if _PLURAL_OF.get(word) == form:
        return True
    if _too_short_or_acronym(word):
        return False
    if form in {word + "s", word + "es"}:
        return True
    if (len(word) > 1 and word.endswith("y") and word[-2] not in "aeiou"
            and form == word[:-1] + "ies"):
        return True
    if word.endswith("fe") and form == word[:-2] + "ves":
        return True
    if word.endswith("f") and not word.endswith("ff") and form == word[:-1] + "ves":
        return True
    return form.endswith("s") and len(form) <= len(word) + 3


def _degree_form_ok(word: str, key: str, form: str) -> bool:
    """只保留真正写成一个词的比较级/最高级。副词 -ly、多音节 more 结构不考。"""
    if word in IRREGULAR_ADJ:
        er, est = IRREGULAR_ADJ[word]
        expect = er if key == "comparative" else est
        return form == expect.lower()
    if word in NO_COMPARATIVE or len(word) <= 2:
        return False
    if word.endswith("ly") and word != "early":
        return False
    if not _can_take_er_est(word):
        return False
    expect = regular_er(word) if key == "comparative" else regular_est(word)
    return form == expect


def _usable_forms(w: dict) -> list[tuple[str, str]]:
    """这个词能进词形段的 (键, 答案)。空列表 = 换下一个词。"""
    word = (w.get("word") or "").strip().lower()
    # 短语、带撇号的缩写不做词形题。数字只出现在测试造词（easy000）里，真词不会靠它过关：
    # 无元音的短缩写仍由 _too_short_or_acronym 挡住。
    if not re.fullmatch(r"[a-z][a-z0-9]*", word or ""):
        return []
    if _closed_class(word):
        return []
    order = _tag_order(w)
    tags = set(order)
    if not (_has_verb(word, tags) or _has_noun(word, tags) or _has_adj(word, tags)):
        return []
    verb_ok = _has_verb(word, tags) and not _skip_verb_forms(
        word, order, w.get("gloss") or "")
    forms = w.get("forms") or {}
    out: list[tuple[str, str]] = []
    for key in FORM_CN:
        raw = forms.get(key)
        if not isinstance(raw, str):
            continue
        shown = raw.strip()
        form = shown.lower()
        if not shown or form == word or not _FORM_TOKEN.fullmatch(shown):
            continue
        if key in _VERB_FORM_KEYS and verb_ok and _verb_form_ok(word, key, form):
            out.append((key, shown))
        elif key in _NOUN_FORM_KEYS and _has_noun(word, tags) and _plural_ok(word, form):
            out.append((key, shown))
        elif key in _ADJ_FORM_KEYS and _has_adj(word, tags) and _degree_form_ok(word, key, form):
            out.append((key, shown))
    return out


# ---- 题型 item 构建 -------------------------------------------------------- #

def _pick_form(w: dict, rng: random.Random) -> tuple[str | None, str | None]:
    """从合格变形里挑一个。动词优先于名词、名词优先于形容词。

    没有合格变形返回 (None, None)——调用方换下一个词，不硬凑。
    """
    usable = _usable_forms(w)
    if not usable:
        return None, None
    best = min(_FORM_RANK[k] for k, _ in usable)
    top = [kv for kv in usable if _FORM_RANK[kv[0]] == best]
    return rng.choice(top)


def _locate_ctx(word: str, forms: dict, en: str) -> tuple[str, str] | None:
    """在例句里定位该词或其任一变形（大小写不敏感、词边界匹配）。

    例句质量护栏：en 含中日韩字符（语料答案串混入，如「答案：didn't，…」）
    或已带挖空记号的，不出例句填空题。定位失败返回 None。
    """
    en = (en or "").strip()
    if not en or "____" in en or _CJK_CHAR.search(en):
        return None
    cands: list[str] = []
    seen: set[str] = set()
    for c in [word, *(forms or {}).values()]:
        c = (c or "").strip()
        key = c.lower()
        if c and key not in seen:
            seen.add(key)
            cands.append(c)
    for c in cands:
        m = re.search(rf"(?<!\w){re.escape(c)}(?!\w)", en, re.IGNORECASE)
        if m:
            return en[:m.start()] + "____" + en[m.end():], m.group(0)
    return None


# 词形题上的词性跟所考的屈折走：复数标名词，时态标动词。
# 避免 crayon 考复数时题干仍写着「动词」。
_FORM_POS_CN = {
    "past": "v. 动词", "past_participle": "v. 动词",
    "present_participle": "v. 动词", "third_singular": "v. 动词",
    "plural": "n. 名词",
    "comparative": "adj. 形容词", "superlative": "adj. 形容词",
}


def _form_item(w: dict, form_key: str, answer: str) -> dict:
    return {"word": w["word"],
            "pos_cn": _FORM_POS_CN.get(form_key) or w.get("pos_cn") or "",
            "gloss": w["gloss_clean"],
            "form_key": form_key, "form_label": FORM_CN[form_key], "answer": answer}


def _spell_item(w: dict, blanked: str, answer: str) -> dict:
    return {"word": w["word"], "pos_cn": w["pos_cn"], "gloss": w["gloss_clean"],
            "blanked": blanked, "answer": answer}


def _ctx_item(w: dict, located: tuple[str, str]) -> dict:
    sentence, answer = located
    ex = w.get("example") or {}
    return {"word": w["word"], "pos_cn": w["pos_cn"], "gloss": w["gloss_clean"],
            "sentence": sentence, "answer": answer, "zh": str(ex.get("zh") or "")}


def _zh2en_item(w: dict) -> dict:
    gloss = _short_senses(w.get("gloss") or w.get("gloss_clean") or "")
    return {"word": w["word"], "pos_cn": w["pos_cn"], "gloss": gloss}


def _en2zh_item(w: dict) -> dict:
    return {"word": w["word"], "pos_cn": w["pos_cn"], "gloss": w["gloss"]}


def _is_simple_word(w: dict) -> bool:
    lvl = w.get("level")
    return (isinstance(lvl, int) and not isinstance(lvl, bool)
            and lvl <= SIMPLE_MAX_LEVEL)


def _build_sections(words: list[dict], quota: dict[str, int],
                    rng: random.Random) -> list[dict]:
    """分层题型分配：每词恰进一个题型段，段内题数按 quota 凑。

    - 简单词（level ≤ SIMPLE_MAX_LEVEL）在同一类变形里优先 form，复杂词优先
      ctx。form 只收 _usable_forms 认可的词（课堂动词的时态、可数名词复数、
      能加 -er 的形容词）；ok、副词、硬造的过去式，以及 crayon/crowd 这类
      名词活用都不占名额。合格词不够就缩小词形段，余词落到后面的段。
      分层是「排序优先」而非把简单词独占：form/ctx 的可出词集合放开到全部合格词，
      同级内按 picked 的多样顺序稳定排序。否则纯简单词卷（如 L0）ctx 配额永远
      落空，例句资源全废。
    - 拼写段长词优先（≥2 字母可挖空，沿用旧「挑最长词」口径）。
    - 某段可出词不足时，富余词自然落到后面的段（zh2en/en2zh 兜底交替承接），
      保证选中词全部成题；各段实际题数以 items 为准。
    """
    form_ready: dict[str, tuple[str, str]] = {}
    for w in words:
        fk, fa = _pick_form(w, rng)
        if fk:
            form_ready[w["word"]] = (fk, fa)
    ctx_ready: dict[str, tuple[str, str]] = {}
    for w in words:
        ex = w.get("example") or {}
        hit = _locate_ctx(w["word"], w.get("forms") or {}, ex.get("en") or "")
        if hit:
            ctx_ready[w["word"]] = hit

    used: set[str] = set()
    buckets: dict[str, list[dict]] = {t: [] for t in SECTION_ORDER}

    def grab(n: int, pred=None, order_key=None) -> list[dict]:
        cands = [w for w in words
                 if w["word"] not in used and (pred is None or pred(w))]
        if order_key:
            cands.sort(key=order_key)  # 稳定排序：同级保持 picked 顺序
        got = cands[:max(0, int(n))]
        for w in got:
            used.add(w["word"])
        return got

    # 1) 词形变化：合格变形才入段。动词 > 名词 > 形容词；同一类里简单词排前。
    #    不合格的词不占名额，后面的词补上；池子干了这一段就变短。
    for w in grab(quota["form"], lambda w: w["word"] in form_ready,
                  order_key=lambda w: (
                      _FORM_RANK.get(form_ready[w["word"]][0], 9),
                      0 if _is_simple_word(w) else 1)):
        fk, fa = form_ready[w["word"]]
        buckets["form"].append(_form_item(w, fk, fa))
    # 2) 拼写挖空：剩余词里挑最长的（好挖空）
    for w in grab(quota["spell"], lambda w: len(w["word"]) >= 2,
                  order_key=lambda w: -len(w["word"])):
        blanked, ans = _spell_blank(w["word"], rng)
        buckets["spell"].append(_spell_item(w, blanked, ans))
    # 3) 例句填空：例句定位成功的词，复杂词排前
    for w in grab(quota["ctx"], lambda w: w["word"] in ctx_ready,
                  order_key=lambda w: 1 if _is_simple_word(w) else 0):
        buckets["ctx"].append(_ctx_item(w, ctx_ready[w["word"]]))
    # 4) 中译英：任意词
    for w in grab(quota["zh2en"]):
        buckets["zh2en"].append(_zh2en_item(w))
    # 5) 英译中：回避介词/专名优先，不够时兜底
    for w in grab(quota["en2zh"],
                  order_key=lambda w: 1 if _skip_for_en2zh(w["pos"]) else 0):
        buckets["en2zh"].append(_en2zh_item(w))
    # 兜底：quota 凑不满（form/ctx 可出词不足）时，富余词交替落中译英/英译中
    for i, w in enumerate(w for w in words if w["word"] not in used):
        used.add(w["word"])
        if i % 2 == 0:
            buckets["zh2en"].append(_zh2en_item(w))
        else:
            buckets["en2zh"].append(_en2zh_item(w))

    return [{"type": t, "title": SECTION_TITLES[t], "items": buckets[t]}
            for t in SECTION_ORDER if buckets[t]]


# ---- 选词 ------------------------------------------------------------------ #

def _select_words(total: int, backlog: list[dict], pool: list[dict],
                  hist: set[str], frac_backlog: float,
                  rng: random.Random) -> list[dict]:
    """按来源配比选词：积压错词优先填满再词池补；历史未用词优先。

    历史已用词只作兜底补齐（重叠计入 overlap，≤10 由调用方把关）。
    """
    picked: list[dict] = []
    used: set[str] = set()

    def take(ws: list[dict], n: int) -> None:
        if n <= 0:
            return
        got = _pick_diverse([w for w in ws if w["word"] not in used], n, rng)
        for w in got:
            used.add(w["word"])
        picked.extend(got)

    fresh_backlog = [w for w in backlog if w["word"] not in hist]
    fresh_pool = [w for w in pool if w["word"] not in hist]
    take(fresh_backlog, int(round(total * frac_backlog)) if frac_backlog else 0)
    if frac_backlog >= 1:
        # 全量回炉（frac=1）不碰词池；fresh 积压不足时用历史已用积压词兜底
        # （重叠计入 overlap，≤10 由调用方把关）
        take(backlog, total - len(picked))
    else:
        take(fresh_pool, total - len(picked))
        if len(picked) < total:  # 历史已用词兜底
            take(backlog, total - len(picked))
            take(pool, total - len(picked))
    return picked


# ---- 存档（fce.db vocab_paper_history） ------------------------------------ #

class VocabPaperStore:
    """vocab_paper_history 读写：试卷资产存档/取回/列表/删除。

    DDL 一次化：__init__ 建表 + 幂等补列（structure/config/scope/user/seed）。
    ``structure`` 存整卷 paper dict（含 sections），取回即所见——存什么渲染
    什么；``words`` 列保留词面列表，供历史去重与批改中心沿用。
    """

    def __init__(self, fce_db_path: str | None = None):
        self.db_path = fce_db_path or _default_fce_db()
        with dbreg_connect(self.db_path) as conn:
            conn.execute(_HISTORY_TABLE_SQL)
            cols = {r[1] for r in
                    conn.execute("PRAGMA table_info(vocab_paper_history)")}
            for name, ddl in _MIGRATE_COLUMNS:
                if name not in cols:
                    conn.execute(
                        f"ALTER TABLE vocab_paper_history ADD COLUMN {name} {ddl}")

    def save_paper(self, paper: dict) -> dict:
        """存档一份卷（生成→预览→保存 的第三步，或生成时直接落库）。

        paper_id 形如 L{n}-... 或 MIX-...（混合卷 level 记 -1）。
        词数须与 config.total 一致（旧口径无 config 时为 50）。
        幂等（INSERT OR REPLACE）。返回 {paper_id, level, count}。
        """
        paper = paper or {}
        pid = str(paper.get("paper_id") or "")[:64]
        ws = list(paper.get("words") or [])
        m = re.match(r"^(?:L(\d+)|MIX)-", pid)
        if not m or not ws:
            raise ValueError("试卷数据不完整（paper_id/words）")
        expected = int((paper.get("config") or {}).get("total") or 0) or 50
        if len(ws) != expected:
            raise ValueError(f"试卷须为 {expected} 词（现有 {len(ws)}）")
        raw_level = paper.get("level")
        if isinstance(raw_level, int) and not isinstance(raw_level, bool):
            level = int(raw_level)
        else:
            level = int(m.group(1)) if m.group(1) else -1
        created = (str(paper.get("created_at") or "")[:40]
                   or datetime.now(_tz.utc).isoformat(timespec="seconds"))
        seed = paper.get("seed")
        row = (
            level, pid, json.dumps(ws, ensure_ascii=False), created,
            json.dumps(paper, ensure_ascii=False),
            json.dumps(paper.get("config") or {}, ensure_ascii=False),
            str(paper.get("scope") or ("learned" if level < 0 else "level")),
            str(paper.get("user") or "")[:60],
            int(seed) if isinstance(seed, int) and not isinstance(seed, bool) else None,
        )
        with dbreg_connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO vocab_paper_history"
                " (level, paper_id, words, created_at, structure, config,"
                "  scope, user, seed) VALUES (?,?,?,?,?,?,?,?,?)", row)
        return {"paper_id": pid, "level": level, "count": len(ws)}

    def get_paper(self, paper_id: str) -> dict | None:
        """取一份历史卷（统一形态：structure 列存整卷，取回即所见）。

        旧版卷（只存词序）由一次性脚本
        scripts/oneoff_2026-11_legacy_backfill.py 回填 structure 后再上
        新代码；这里不再做位置还原 fallback。
        """
        pid = str(paper_id)[:64]
        with dbreg_connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT paper_id, level, words, created_at, structure, config,"
                " scope, user, seed FROM vocab_paper_history WHERE paper_id = ?",
                (pid,)).fetchone()
        if not row:
            return None
        try:
            ws = json.loads(row["words"] or "[]")
        except (ValueError, TypeError):
            ws = []
        if not isinstance(ws, list):
            ws = []

        paper: dict = {}
        if row["structure"]:
            loaded = _loads_dict(row["structure"])
            if loaded:
                paper = loaded
        # 列值是权威：paper_id/level/words/created_at 以列为准回填
        paper.update({"paper_id": row["paper_id"], "level": row["level"],
                      "words": ws, "created_at": row["created_at"]})
        paper.setdefault("scope", (row["scope"] if row["scope"] else
                                   ("learned" if int(row["level"] or 0) < 0 else "level")))
        paper.setdefault("user", row["user"] or "")
        paper.setdefault("seed", row["seed"])
        paper.setdefault("overlap_with_history", 0)
        return paper

    def delete_paper(self, paper_id: str) -> bool:
        """删除一份已生成的试卷资产，返回是否确有删除。"""
        with dbreg_connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM vocab_paper_history WHERE paper_id = ?",
                (str(paper_id)[:64],))
            return cur.rowcount > 0

    def list_history(self, level: int | None = None, limit: int = 50,
                     include_mix: bool = False) -> list[dict]:
        """已生成试卷。level=None 为全部（含混合卷）。

        include_mix：指定级别时再带上混合卷（level<0）。两桶各自取
        limit 条再按 id 合并，避免「先取全局最近 N 条再在客户端筛本级」
        时本级卷被别的级别挤出窗口。
        """
        limit = max(1, min(int(limit), 200))
        cols = "id, paper_id, level, words, created_at"
        with dbreg_connect(self.db_path) as conn:
            if level is None:
                rows = conn.execute(
                    f"SELECT {cols} FROM vocab_paper_history"
                    " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            elif include_mix:
                rows_lv = conn.execute(
                    f"SELECT {cols} FROM vocab_paper_history"
                    " WHERE level = ? ORDER BY id DESC LIMIT ?",
                    (int(level), limit)).fetchall()
                rows_mix = conn.execute(
                    f"SELECT {cols} FROM vocab_paper_history"
                    " WHERE level < 0 ORDER BY id DESC LIMIT ?",
                    (limit,)).fetchall()
                rows = sorted([*rows_lv, *rows_mix],
                              key=lambda r: r["id"], reverse=True)
            else:
                rows = conn.execute(
                    f"SELECT {cols} FROM vocab_paper_history"
                    " WHERE level = ? ORDER BY id DESC LIMIT ?",
                    (int(level), limit)).fetchall()
        out = []
        for r in rows:
            try:
                ws = json.loads(r["words"] or "[]")
            except (ValueError, TypeError):
                ws = []
            out.append({"paper_id": r["paper_id"], "level": r["level"],
                        "words": ws, "count": len(ws),
                        "created_at": r["created_at"]})
        return out

    def _hist_words(self, scope: str, level: int | None) -> set[str]:
        """历史去重词集：learned 只看先前混合卷（level<0）；level 只看本级。"""
        if scope == "learned":
            sql = "SELECT words FROM vocab_paper_history WHERE level < 0"
            params: tuple = ()
        else:
            sql = "SELECT words FROM vocab_paper_history WHERE level = ?"
            params = (int(level or 0),)
        out: set[str] = set()
        with dbreg_connect(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        for r in rows:
            try:
                out.update(json.loads(r["words"] or "[]"))
            except (ValueError, TypeError):
                pass
        return out


# ---- 生成 ------------------------------------------------------------------ #

def _scale_ratios(ratio_map: dict[str, int], total: int) -> dict[str, int]:
    """把题型配比按比例缩放到指定总题量（最大余数法，和恰为 total）。"""
    total = max(10, min(200, int(total)))
    src_total = sum(ratio_map.values())
    if src_total == total:
        return dict(ratio_map)
    scaled = {k: v * total // src_total for k, v in ratio_map.items()}
    rest = total - sum(scaled.values())
    if rest > 0:  # 余数给占比大的段（同占比按键名稳定排序）
        order = sorted(ratio_map,
                       key=lambda k: (-(ratio_map[k] * total % src_total), k))
        for k in order[:rest]:
            scaled[k] += 1
    return {k: v for k, v in scaled.items() if v > 0}


def generate_paper(level: int, save: bool = True, scope: str = "level",
                   user: str | None = None, fce_db_path: str | None = None,
                   seed: int | None = None, preset: str = "standard",
                   ratios: dict | None = None, total: int | None = None,
                   source_mix: dict | None = None) -> dict:
    """生成一份考试卷。save=True 经 VocabPaperStore 落库（含 sections）。

    scope="level"：本级词池（exam_allowed 过滤）。
    scope="learned"：user 的全部已学词（进度里出现过即可，含未掌握），
    跨级别混合。卷号 MIX- 前缀，level 存 -1。去重只对先前的混合卷。

    preset / source_mix：选词来源配比。standard=纯词池；reinforce=60% 积压
    错词 + 40% 词池；recycle=全量积压错词。显式 source_mix 覆盖 preset；
    含 backlog_wrong>0 时必须给 user。积压错词 = 艾宾浩斯到期未攻克 ∪
    错过未掌握（并集去重，仅留词库有的词面）。

    seed 非 None 时同 seed 同卷（题目顺序、挖空位置、变形键选择全一致）。

    total：指定总题量（10-200，越界取边界）。给定时按比例缩放各题型
    配比（最大余数法）；不给则总题量 = 配比之和（默认 50）。
    """
    rng = random.Random(seed) if seed is not None else random.Random()
    scope = scope or "level"
    if scope not in ("level", "learned"):
        raise ValueError("scope 须为 level 或 learned")
    preset = preset or "standard"
    if preset not in PRESET_SOURCE_MIX:
        raise ValueError(f"preset 须为 {'/'.join(PRESET_SOURCE_MIX)}")
    mix = dict(source_mix) if source_mix else PRESET_SOURCE_MIX[preset]
    frac_backlog = 0.0
    if mix:
        try:
            frac_backlog = float(mix.get("backlog_wrong") or 0)
        except (TypeError, ValueError) as e:
            raise ValueError("source_mix.backlog_wrong 须为 0-1 数值") from e
        if not 0 <= frac_backlog <= 1:
            raise ValueError("source_mix.backlog_wrong 须在 0-1")
    user_clean = (user or "").strip()[:60]
    if frac_backlog > 0 and not user_clean:
        raise ValueError("选词含积压错词（backlog_wrong>0）时须指定 user")

    ratio_map = _normalize_ratios(ratios)
    if total is not None:
        try:
            total = int(total)
        except (TypeError, ValueError) as e:
            raise ValueError("total 须为整数") from e
        ratio_map = _scale_ratios(ratio_map, total)
    total = sum(ratio_map.values())
    lvl_out, prefix = ((-1, "MIX") if scope == "learned"
                       else (int(level), f"L{int(level)}"))

    backlog: list[dict] = []
    if frac_backlog > 0:
        backlog = _load_backlog_wrong(user_clean, fce_db_path)
    if scope == "level":
        pool = _load_words_rich(int(level))
    else:
        pool = _load_learned_words(user_clean, fce_db_path)

    backlog_n = len({w["word"] for w in backlog})
    if frac_backlog >= 1:
        # 全量积压回炉：不用词池补，不足直接报现有量
        if backlog_n < total:
            raise ValueError(
                f"积压错词不足 {total}（现有 {backlog_n}），无法出全错词回炉卷")
    else:
        combined_n = len({w["word"] for w in backlog} | {w["word"] for w in pool})
        if combined_n < total:
            if frac_backlog > 0:
                raise ValueError(
                    f"可用词不足 {total}（词池 {len(pool)} + 积压错词 {backlog_n}）")
            if scope == "learned":
                who = user_clean or "该学生"
                raise ValueError(f"{who} 已学词不足 {total}（现有 {combined_n}）")
            raise ValueError(f"L{level} 词量不足 {total}（现有 {combined_n}）")

    store = VocabPaperStore(fce_db_path)
    hist = store._hist_words(scope, lvl_out)
    picked = _select_words(total, backlog, pool, hist, frac_backlog, rng)
    overlap = sum(1 for w in picked if w["word"] in hist)
    if overlap > MAX_HISTORY_OVERLAP:
        raise ValueError(
            f"与历史重复 {overlap} 词超限（≤{MAX_HISTORY_OVERLAP}）")

    sections = _build_sections(picked, ratio_map, rng)

    now = datetime.now(_tz.utc)
    paper_id = (f"{prefix}-" + now.strftime("%Y%m%d%H%M%S")
                + f"-{rng.randrange(1000, 9999)}")
    paper = {
        "paper_id": paper_id,
        "level": lvl_out,
        "scope": scope,
        "user": user_clean if scope == "learned" else "",
        "created_at": now.isoformat(timespec="seconds"),
        "seed": int(seed) if seed is not None else None,
        "overlap_with_history": overlap,
        "config": {"total": total, "ratios": {k: ratio_map[k] for k in SECTION_ORDER},
                   "simple_max_level": SIMPLE_MAX_LEVEL, "preset": preset,
                   "source_mix": dict(mix) if mix else None},
        "words": [w["word"] for w in picked],
        "sections": sections,
    }
    if save:
        store.save_paper(paper)
    return paper


# ---- 渲染 ------------------------------------------------------------------ #

_CN_SECTION_NUMS = ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十")

_TYPE_HINTS = {"form": "写出指定形式", "spell": "在空格处填写字母",
               "ctx": "根据句意填空", "zh2en": "写出英文", "en2zh": "写出中文意思"}


def _section_label(idx: int) -> str:
    return _CN_SECTION_NUMS[idx] if idx < len(_CN_SECTION_NUMS) else str(idx + 1)


def _paper_sections(paper: dict) -> list[dict]:
    """paper → sections 列表（sections 键优先；旧版 zh2en/en2zh/spell 键兼容）。"""
    secs = paper.get("sections")
    if isinstance(secs, list) and secs:
        return [s for s in secs if isinstance(s, dict)]
    out = []
    for t in ("zh2en", "en2zh", "spell"):
        items = paper.get(t) or []
        if items:
            out.append({"type": t, "title": SECTION_TITLES[t], "items": list(items)})
    return out


def _zh_senses_html(gloss: str, e) -> str:
    """中译英题干：短义各自包一层，中间用分号，不再倒一整段词典。"""
    text = _short_senses(gloss)
    parts = [p for p in text.split("；") if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return f'<span class="zh">{e(parts[0])}</span>'
    body = '<span class="sense-gap">；</span>'.join(
        f'<span class="sense">{e(p)}</span>' for p in parts)
    return f'<span class="zh">{body}</span>'


def _render_item(qtype: str, i: int, it: dict, e) -> str:
    """单个题行：题号、题干、书写横线（拼写题的横线就是挖空单词）。"""
    n = f'<span class="n">{i}.</span>'
    if qtype == "form":
        label = it.get("form_label") or FORM_CN.get(it.get("form_key") or "", "")
        pos = it.get("pos_cn") or ""
        pos_html = f'<span class="pos">{e(pos)}</span>' if pos else ""
        return (f'<div class="q">{n}<span class="prompt">'
                f'<span class="en">{e(it.get("word") or "")}</span>'
                f'{pos_html}<span class="ask">的{e(label)}</span></span>'
                f'<span class="write"></span></div>')
    if qtype == "spell":
        return (f'<div class="q">{n}<span class="prompt">'
                f'<span class="pos">{e(it.get("pos_cn") or "")}</span>'
                f'<span class="zh">{e(it.get("gloss") or "")}</span></span>'
                f'<span class="blanked">{e(it.get("blanked") or "")}</span></div>')
    if qtype == "ctx":
        # 空已在句中（____），不再另加一条横线，避免看起来像两道空
        return (f'<div class="q q-ctx">{n}<span class="prompt">'
                f'<span class="en">{e(it.get("sentence") or "")}</span>'
                f'<span class="zh">{e(it.get("zh") or it.get("gloss") or "")}</span></span></div>')
    if qtype == "zh2en":
        return (f'<div class="q">{n}<span class="prompt">'
                f'<span class="pos">{e(it.get("pos_cn") or "")}</span>'
                f'{_zh_senses_html(it.get("gloss") or "", e)}</span>'
                f'<span class="write"></span></div>')
    # 英译中只给英文单词。词性留在中译英、拼写和词形题上。
    return (f'<div class="q">{n}<span class="prompt">'
            f'<span class="en">{e(it.get("word") or "")}</span></span>'
            f'<span class="write"></span></div>')


def _answer_text(qtype: str, it: dict) -> str:
    if qtype == "zh2en":
        pos = it.get("pos_cn") or ""
        return f'{it.get("word") or ""}（{pos}）' if pos else str(it.get("word") or "")
    if qtype == "en2zh":
        return str(it.get("gloss") or "")[:14]
    return str(it.get("answer") or it.get("spell_answer") or "")


def render_new_paper_html(paper: dict, title: str = "") -> str:
    """可打印考卷：sections 分区 + 答案页（独立成页可撕）。

    存什么渲染什么：sections 里每段一个标题块；旧版形态（zh2en/en2zh/
    spell 键）经 _paper_sections 兼容转换。
    """
    e = html.escape
    paper = paper or {}
    lvl = paper.get("level")
    pid = str(paper.get("paper_id") or "")
    if title:
        t = title
    elif lvl is not None and int(lvl) < 0:
        who = (paper.get("user") or "").strip()
        t = f"哈一词汇考试 · 已学全部混合卷（{who}）" if who else "哈一词汇考试 · 已学全部混合卷"
    else:
        t = f"哈一词汇考试 · L{lvl} 级"

    sections = _paper_sections(paper)
    total = sum(len(s.get("items") or []) for s in sections)
    per = f"{round(100 / total, 1):g}" if total else "0"
    per_txt = f"共 {total} 题 · 每题 {per} 分 · " if total else ""
    meta_line = (f"卷号 {e(pid)} · {per_txt}姓名__________ · 得分______")

    q_blocks: list[str] = []
    a_blocks: list[str] = []
    for idx, sec in enumerate(sections):
        qtype = str(sec.get("type") or "")
        tt = str(sec.get("title") or SECTION_TITLES.get(qtype, qtype or "题目"))
        items = sec.get("items") or []
        head = f"{_section_label(idx)}、{tt}"
        hint = _TYPE_HINTS.get(qtype, "")
        sub = f"（{len(items)} 题 · {hint}）" if hint else f"（{len(items)} 题）"
        # 词形/拼写/例句题干较长，单列才写得下；中英互译题干短，两列省纸
        sec_cls = "sec compact" if qtype in {"zh2en", "en2zh"} else "sec"
        q_blocks.append(
            f'<section class="{sec_cls}"><h2>{e(head)}{e(sub)}</h2><div class="qs">'
            + "".join(_render_item(qtype, i, it, e)
                      for i, it in enumerate(items, 1))
            + "</div></section>")
        answers = "".join(
            f'<span class="ak">{i}.{e(_answer_text(qtype, it))}</span>'
            for i, it in enumerate(items, 1))
        a_blocks.append(
            f'<div class="ans-sec"><b>{e(head)}</b><p class="aks">{answers}</p></div>')

    return render_template(
        "new_paper.html",
        title_esc=e(t), pid_esc=e(pid), meta_line=meta_line,
        sections_html="".join(q_blocks), answers_html="".join(a_blocks),
    )


# ---- 模块级薄委托（兼容既有调用方/测试；内部转调 VocabPaperStore） ---------- #

def save_paper(paper: dict, fce_db_path: str | None = None) -> dict:
    return VocabPaperStore(fce_db_path).save_paper(paper)


def get_paper(paper_id: str, fce_db_path: str | None = None) -> dict | None:
    return VocabPaperStore(fce_db_path).get_paper(paper_id)


def delete_paper(paper_id: str, fce_db_path: str | None = None) -> bool:
    return VocabPaperStore(fce_db_path).delete_paper(paper_id)


def list_paper_history(level: int | None = None, limit: int = 50,
                       fce_db_path: str | None = None,
                       include_mix: bool = False) -> list[dict]:
    return VocabPaperStore(fce_db_path).list_history(
        level, limit=limit, include_mix=include_mix)

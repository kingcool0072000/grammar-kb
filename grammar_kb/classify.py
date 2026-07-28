"""分类、标志词抽取、关系检测——全部纯函数，便于单测。

设计要点：
1. 讲次分类基于**标题关键字**（而非写死讲号），这样讲义增删/改名仍可用。
2. 时态标志词词典内建初中语法常见的"时间状语/标志词"，逐时态归类，
   支撑"给我所有时态关键词"这类查询。
3. 关系检测识别"主将从现""时态呼应"等显式表述，落库为 relation。
"""
from __future__ import annotations

import re
from typing import Optional

from .models import Category, Marker, Relation


# --------------------------------------------------------------------------- #
# 讲次分类
# --------------------------------------------------------------------------- #

# 标题关键字 → (category, subcategory)。顺序敏感：先匹配更具体的关键字。
# 注意"动词时态"必须排在"动词"之前，否则会被"动词"吞掉。
_TITLE_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"动词时态|时态"), Category.TENSE.value, "动词时态"),
    (re.compile(r"被动语态|被动"), Category.VOICE.value, "被动语态"),
    (re.compile(r"不定式"), Category.NON_FINITE.value, "不定式"),
    (re.compile(r"动名词|分词"), Category.NON_FINITE.value, "动名词"),
    (re.compile(r"综合复习|阶段复习|复习"), Category.REVIEW.value, "综合复习"),
    (re.compile(r"名词"), Category.LEXICAL.value, "名词"),
    (re.compile(r"代词"), Category.LEXICAL.value, "代词"),
    (re.compile(r"冠词"), Category.LEXICAL.value, "冠词"),
    (re.compile(r"数词"), Category.LEXICAL.value, "数词"),
    (re.compile(r"介词"), Category.LEXICAL.value, "介词"),
    (re.compile(r"连词"), Category.LEXICAL.value, "连词"),
    (re.compile(r"形容词"), Category.LEXICAL.value, "形容词"),
    (re.compile(r"副词"), Category.LEXICAL.value, "副词"),
    (re.compile(r"情态动词"), Category.LEXICAL.value, "情态动词"),
    (re.compile(r"动词"), Category.LEXICAL.value, "动词"),
    (re.compile(r"主谓一致"), Category.SYNTAX.value, "主谓一致"),
    (re.compile(r"感叹句"), Category.SYNTAX.value, "感叹句"),
    (re.compile(r"反义疑问句|反意疑问句"), Category.SYNTAX.value, "反义疑问句"),
    (re.compile(r"特殊疑问句"), Category.SYNTAX.value, "特殊疑问句"),
    (re.compile(r"一般疑问句"), Category.SYNTAX.value, "一般疑问句"),
    (re.compile(r"倒装"), Category.SYNTAX.value, "倒装句"),
    (re.compile(r"宾语从句"), Category.SYNTAX.value, "宾语从句"),
    (re.compile(r"状语从句"), Category.SYNTAX.value, "状语从句"),
    (re.compile(r"定语从句"), Category.SYNTAX.value, "定语从句"),
    (re.compile(r"从句"), Category.SYNTAX.value, "从句"),
    (re.compile(r"句"), Category.SYNTAX.value, "句式"),
]


def classify_lecture_title(title: str) -> tuple[str, str]:
    """根据讲次标题返回 (category, subcategory)。

    >>> classify_lecture_title("第二十二讲 动词时态1")
    ('时态', '动词时态')
    >>> classify_lecture_title("第1讲 名词")
    ('词法', '名词')
    """
    for pat, cat, sub in _TITLE_RULES:
        if pat.search(title):
            return cat, sub
    return Category.OTHER.value, "其他"


# --------------------------------------------------------------------------- #
# 文件名解析
# --------------------------------------------------------------------------- #

_FILENAME_NUM = re.compile(r"(\d{1,3})")
# 去掉文件名里的"第X讲""讲义/讲义解析""_""编号"等，提取干净短标题
_TITLE_CLEAN = re.compile(
    r"(?:第[一二三四五六七八九十百零\d]+讲|讲义(?:卷)?\d*|讲义解析|_\s*讲义.*)"
)


def parse_filename(filename: str) -> tuple[Optional[int], str, str]:
    """从文件名解析 (讲号, 短标题, 用于分类的标题串)。

    >>> parse_filename("22.动词时态1_讲义解析.pdf")
    (22, '动词时态1', '动词时态1')
    >>> parse_filename("01.名词_讲义.pdf")
    (1, '名词', '名词')
    """
    import os

    base = os.path.basename(filename)
    base = re.sub(r"\.pdf$", "", base, flags=re.I)
    m = _FILENAME_NUM.search(base)
    number = int(m.group(1)) if m else None
    # 去掉前导编号
    title = re.sub(r"^\s*\d{1,3}\s*[.\-、]\s*", "", base)
    short = _TITLE_CLEAN.sub("", title).strip(" _-、.")
    short = re.sub(r"\s+", "", short)
    return number, short, short


def make_full_title(number: int, short_title: str) -> str:
    """拼"第N讲 短标题"。"""
    return f"第{number}讲 {short_title}"


# --------------------------------------------------------------------------- #
# 时态标志词词典
# --------------------------------------------------------------------------- #
# 每个时态对应的标志词/时间状语。匹配时用 \b 边界 + 大小写无关。
# 中文标志词直接子串匹配。

TENSE_MARKERS: dict[str, list[str]] = {
    "一般现在时": [
        "always", "usually", "often", "sometimes", "never", "seldom",
        "every day", "every week", "every morning", "every year",
        "on Sundays", "on Monday", "in the morning", "once a week",
        "twice a day", "three times",
    ],
    "一般过去时": [
        "yesterday", "yesterday morning", "last week", "last night",
        "last year", "last Monday", "... ago", "ago", "in 1990", "in 2008",
        "just now", "at that time", "the other day", "at the age of",
        "in the past",
    ],
    "一般将来时": [
        "tomorrow", "tomorrow morning", "next week", "next month",
        "next year", "next Monday", "soon", "later", "in the future",
        "the day after tomorrow", "in two days", "by and by",
        "from now on", "before long",
    ],
    "现在进行时": [
        "now", "right now", "at the moment", "at present", "these days",
        "look", "listen", "Look!", "Listen!", "nowadays", "currently",
    ],
    "过去进行时": [
        "at this time yesterday", "at 8 last night", "at that time",
        "at that moment", "then", "from 8 to 10 yesterday",
        "this time yesterday", "the whole morning",
    ],
    "现在完成时": [
        "already", "yet", "just", "ever", "never", "recently", "lately",
        "so far", "up to now", "till now", "until now", "since", "for",
        "in the past few years", "several times", "before", "once", "twice",
        "have been to", "have gone to", "have been in",
    ],
    "过去完成时": [
        "by the end of last term", "by the end of last year",
        "by then", "by the time", "by 9 o'clock", "before", "after",
        "until", "by last week", "had already", "had just",
    ],
    "过去将来时": [
        "would", "was going to", "were going to",
        "the next day", "the next week", "the following week",
    ],
}

# 时态 → 该时态常见的中文表述（用于把知识点正文里的时态名归一化）
TENSE_NAMES = list(TENSE_MARKERS.keys())


def _normalize_marker(m: str) -> str:
    """标志词归一：去空白、小写（仅英文小写，中文不变）。"""
    return re.sub(r"\s+", " ", m).strip().lower()


def extract_markers(
    text: str,
    tense: Optional[str] = None,
) -> list[Marker]:
    """从正文中抽取时态标志词。

    - 若给定 ``tense``，只在该时态词典里找；
    - 否则遍历所有时态，把命中的标志词连同时态一起返回（去重）。
    匹配对英文用大小写无关的词边界，对中文用子串。
    """
    if not text:
        return []
    seen: set[tuple[str, str]] = set()
    out: list[Marker] = []
    tenses = [tense] if tense else TENSE_NAMES
    low = text.lower()
    for tname in tenses:
        words = TENSE_MARKERS.get(tname, [])
        for w in words:
            norm = _normalize_marker(w)
            key = (tname, norm)
            if key in seen:
                continue
            if _contains_marker(low, norm):
                seen.add(key)
                out.append(
                    Marker(
                        marker=w,  # 保留原始大小写展示
                        marker_type="标志词",
                        tense=tname,
                    )
                )
    return out


def _contains_marker(low_text: str, norm: str) -> bool:
    """判断归一化后的标志词是否出现在归一化后的文本里。

    - 纯英文/含字母 → 用词边界，避免 "for" 命中 "before"。
    - 纯中文/符号 → 子串匹配。
    """
    if re.search(r"[a-z]", norm):
        # 转义并把 "..." 这种占位当作字面
        pat = r"\b" + re.escape(norm).replace(r"\.\.\.", r"\w+(?:\s+\w+)*") + r"\b"
        # 对含 ... 的，退化为更宽松匹配
        if "..." in norm:
            pat = re.escape(norm).replace(r"\.\.\.", r".+?")
        return re.search(pat, low_text) is not None
    return norm in low_text


# --------------------------------------------------------------------------- #
# 关系检测（主将从现 / 时态呼应 等）
# --------------------------------------------------------------------------- #

_RELATION_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"主将从现"), "主将从现", "主句用一般将来时，时间/条件状语从句用一般现在时表将来"),
    (re.compile(r"主过从过|主将从现|时态呼应|时态一致|前后时态"), "时态呼应", "从句时态与主句时态呼应"),
    (re.compile(r"主情从情|主谓一致"), "主谓一致", "主语与谓语在人称数上保持一致"),
    (re.compile(r"对比一般将来"), "对比", "与一般将来时对比"),
]


def detect_relations(text: str) -> list[Relation]:
    """检测正文里显式出现的关系表述。"""
    if not text:
        return []
    out: list[Relation] = []
    seen: set[str] = set()
    for pat, rtype, note in _RELATION_RULES:
        if pat.search(text) and rtype not in seen:
            seen.add(rtype)
            out.append(Relation(type=rtype, note=note))
    return out


# --------------------------------------------------------------------------- #
# 知识点标题 → 是否为时态类
# --------------------------------------------------------------------------- #

_TENSE_TITLE = re.compile(r"(一般现在时|一般过去时|一般将来时|现在进行时|过去进行时|现在完成时|过去完成时|过去将来时)")


def guess_tense_of_kp(title: str, body: str = "") -> Optional[str]:
    """若知识点与某具体时态相关，返回该时态名，否则 None。"""
    for name in TENSE_NAMES:
        if name in title or name in (body or "")[:200]:
            return name
    return None

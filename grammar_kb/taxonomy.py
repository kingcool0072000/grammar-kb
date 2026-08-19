"""知识点主题体系：把零散知识点聚合到 语法大类 → 主题 两级树。

不改动 knowledge_point 原始数据。匹配按信号强度三轮进行：

  第一轮  subcategory（讲次细分，最强信号，语言体系的官方分类）
  第二轮  title 强特征（从句/倒装/句型结构等结构性词汇）
  第三轮  body 搭配模式（make sb. do sth. 等固定搭配，跨词类的句型）

未命中回落「大类·其它」。

体系设计（覆盖初中语法，补齐此前缺失的维度）：

  词法    名词 / 代词 / 冠词 / 数词 / 介词 / 连词 / 形容词与副词 / 动词 /
          情态动词 / 特殊词法（it 用法、there be、近义辨析、所有格、构词）
  时态    时态标志词 / 时态呼应 / 八种时态细分 / 动词时态总览
  语态    被动语态
  非谓语  不定式 / 动名词
  句法    句子成分 / 五大句型结构 / 句子类型（陈述·疑问·感叹·祈使）/
          简单句与并列句 / 宾语从句 / 状语从句 / 定语从句 / 主谓一致 / 特殊句式（倒装）
  固定搭配 动词固定搭配（make sb. do sth.…）/ 介词固定搭配 / 固定句型
  综合复习 综合练习
"""
from __future__ import annotations

import re

from typing import Optional

from .models import KnowledgePoint

TENSE_NAMES = [
    "一般现在时", "一般过去时", "一般将来时", "现在进行时", "过去进行时",
    "现在完成时", "过去完成时", "过去将来时",
]

# 主题注册表：主题名 → 各轮规则（subcat 精确 / title 强特征 / body 搭配）
# key = (大类, 主题)；轮内按注册顺序取先命中
_RULES: list[tuple[str, str, dict]] = []


def _reg(cat: str, theme: str, **rule) -> None:
    _RULES.append((cat, theme, rule))


# ---- 第一轮：subcategory 精确映射 --------------------------------------- #
_reg("词法", "名词", subcat=["名词"])
_reg("词法", "代词", subcat=["代词"])
_reg("词法", "冠词", subcat=["冠词"])
_reg("词法", "数词", subcat=["数词"])
_reg("词法", "介词", subcat=["介词"])
_reg("词法", "连词", subcat=["连词"])
_reg("词法", "形容词与副词", subcat=["形容词", "副词"])
_reg("词法", "情态动词", subcat=["情态动词"])
_reg("词法", "动词", subcat=["动词"])          # 动词时态 subcat 归时态大类，不在此列
_reg("语态", "被动语态", subcat=["被动语态"])
_reg("非谓语", "不定式", subcat=["不定式"])
_reg("非谓语", "动名词", subcat=["动名词"])
_reg("句法", "感叹句", subcat=["感叹句"])
_reg("句法", "反义疑问句", subcat=["反义疑问句"])
_reg("句法", "特殊疑问句", subcat=["特殊疑问句"])
_reg("句法", "倒装句", subcat=["倒装句"])

# 具体句型的 title 规则（先于泛化的"句子类型"，避免"感叹句的用法"被泛化吞掉）
_reg("句法", "感叹句", title=["感叹句"])
_reg("句法", "反义疑问句", title=["反义疑问句", "附加问句"])
_reg("句法", "特殊疑问句", title=["特殊疑问句", "特殊疑问词"])
_reg("句法", "倒装句", title=["倒装"])
_reg("句法", "句子类型", title=[
    "陈述句", "疑问句", "祈使句", "否定句", "句型转换",
])
_reg("句法", "宾语从句", subcat=["宾语从句"])
_reg("句法", "状语从句", subcat=["状语从句"])
_reg("句法", "定语从句", subcat=["定语从句"])
_reg("句法", "主谓一致", subcat=["主谓一致"])
_reg("时态", "时态细分", subcat=["动词时态"])
# 动词时态 subcat 在时态大类下再按 title 细分到具体时态（见第二轮）

# ---- 第二轮：title 强特征 ------------------------------------------------ #
_reg("句法", "句子成分", title=[
    "句子成分", "五大句型", "主谓宾", "主系表", "主谓双宾", "主谓结构",
    "基本句型", "句子结构", "S + V", "SV(", "S+V",
])
_reg("句法", "简单句与并列句", title=["简单句", "并列句", "复合句"])

for _t in TENSE_NAMES:
    _reg("时态", _t, title=[_t])
_reg("时态", "时态呼应", title=[
    "主将从现", "主情从现", "主祈从现", "主过从必过", "主现从不限",
    "真理永不变", "时态呼应", "从句中的时态", "状语从句中的时态",
])
_reg("时态", "动词时态总览", title=["八种时态", "时态归纳", "时态总结", "时态对比", "时态的区别", "动词时态"])
_reg("时态", "时态标志词", title=["标志词", "时间状语", "信号词", "提示词"])
_reg("词法", "特殊词法", title=[
    "it的用法", "There be", "there be", "易混淆", "近义", "辨析",
    "用法归纳", "所有格", "构词", "缩写", "大小写",
])
_reg("固定搭配", "固定句型", title=[
    "固定搭配", "常用句型", "句型归纳", "句式", "It takes", "It is + ",
    "so...that", "such...that", "too...to", "enough to",
])

# ---- 第三轮：body 搭配模式（固定搭配跨类句型）---------------------------- #
_reg("固定搭配", "动词固定搭配", body=[
    "make sb. do", "make sb do", "let sb. do", "let sb do",
    "tell sb. to do", "tell sb to do", "ask sb. to do", "ask sb to do",
    "stop sb. from doing", "stop sb from doing",
    "see sb. doing", "see sb doing", "watch sb. doing", "hear sb. doing",
    "help sb. (to) do", "help sb do",
    "spend...on", "spend...doing", "spend …on",
    "had better", "would rather", "can’t help doing", "can't help doing",
    "used to do", "be used to doing", "look forward to doing",
    "be worth doing", "keep sb. doing", "find sb. doing",
])
_reg("固定搭配", "介词固定搭配", body=[
    "be good at", "be interested in", "be afraid of", "be full of",
    "belong to", "listen to", "arrive at", "arrive in", "depend on",
    "worry about", "think about", "be proud of", "take care of",
])

# 标志词密集（markers ≥ 4）的知识点归时态标志词（第四轮兜底，仅时态大类）


# --------------------------------------------------------------------------- #
# 综合复习分流：习题不是知识点，按考察主题归入对应语法主题
# --------------------------------------------------------------------------- #

# 主题词 → (大类, 主题)。综合复习的板块标题/解析考点词命中即归入
_REVIEW_TOPIC_WORDS: list[tuple[str, str, str]] = [
    # 具体句法（优先于泛化词）
    ("宾语从句", "句法", "宾语从句"), ("状语从句", "句法", "状语从句"),
    ("定语从句", "句法", "定语从句"), ("主谓一致", "句法", "主谓一致"),
    ("感叹句", "句法", "感叹句"), ("反义疑问句", "句法", "反义疑问句"),
    ("特殊疑问句", "句法", "特殊疑问句"), ("倒装", "句法", "倒装句"),
    ("被动语态", "语态", "被动语态"), ("情态动词", "词法", "情态动词"),
    ("不定式", "非谓语", "不定式"), ("动名词", "非谓语", "动名词"),
    ("数词", "词法", "数词"), ("介词", "词法", "介词"), ("连词", "词法", "连词"),
    ("冠词", "词法", "冠词"), ("代词", "词法", "代词"), ("名词", "词法", "名词"),
    ("形容词", "词法", "形容词与副词"), ("副词", "词法", "形容词与副词"),
    ("动词时态", "时态", "动词时态综合"), ("非谓语", "非谓语", "不定式"),
]

_REVIEW_BLOCK_RE = re.compile(
    r"(?:^[一二三四五六七八九十]+、|^[（(][一二三四五六七八九十\d]+[)）])([^\n]{2,10}?)(?:[0-9①-⑩]|易错|举一|提高|$)",
    re.M,
)
_REVIEW_EXAM_RE = re.compile(r"(?:考查|考察|考的是|本题考)了?([\u4e00-\u9fa5]{2,8})")


def _classify_review(kp: KnowledgePoint, hint: str = "") -> tuple[str, str]:
    """综合复习知识点 → 按内容考察的主题归类。

    优先级：题板块标题（一、介词）> 解析考点（本题考查XXX）>
    标志词密集（时态题）> 讲次括号提示（综合复习三（介词、连词））。
    """
    text = f"{kp.title or ''}\n{kp.body_md or ''}"
    # 1) 题板块标题聚类
    blocks = _REVIEW_BLOCK_RE.findall(kp.body_md or "")
    votes: list[str] = list(blocks)
    # 2) 解析考点词
    votes += _REVIEW_EXAM_RE.findall(kp.body_md or "")
    # 3) 讲次提示（括号内容）计一票
    m = re.search(r"[（(]([^（）()]+)[)）]", hint or "")
    if m:
        votes.append(m.group(1))
    # 计分：具体词优先（词长的优先匹配，避免"动词"吃掉"动词时态"）
    best: tuple[int, str, str] | None = None
    for word, cat, theme in _REVIEW_TOPIC_WORDS:
        score = sum(2 if v.startswith(word) or word in v else 0 for v in votes)
        if score and (best is None or score > best[0]):
            best = (score, cat, theme)
    if best:
        cat, theme = best[1], best[2]
        if theme == "动词时态综合":
            # 时态题细分到具体时态
            t = _tense_in_text(text)
            if t:
                return "时态", t
        return cat, theme
    # 4) 标志词密集 → 时态
    if len(kp.markers or []) >= 4:
        t = _tense_in_text(text)
        return "时态", t or "动词时态综合"

    # 5) 英文题干特征兜底（题干+选项无中文考点提示时）
    for pattern, cat, theme in _REVIEW_EN_HINTS:
        if re.search(pattern, text, re.I):
            return cat, theme
    return "综合复习", "未归类"


# 英文题干特征 → 主题（顺序即优先级；从上到下首个命中）
_REVIEW_EN_HINTS: list[tuple[str, str, str]] = [
    (r"\b(hers|mine|yours|ours|theirs)\b", "词法", "代词"),
    (r"\bby (my|his|her|our|their|him|them|us|the)\b", "语态", "被动语态"),
    (r"\b(was|were|is|are|be|been)\s+\w+(ed|en)\b", "语态", "被动语态"),
    (r"\b(after|before|when|while|as soon as|until)\b[^.?!]*\b(arrived|came|left|began|started|finished|returned|ends)\b", "句法", "状语从句"),
    (r"^(How|What|Where|When|Who|Whose|Which|Why)\b.*\?", "句法", "特殊疑问句"),
    (r"\bcan'?t be\b|\bmust be\b|\bshould be\b|\bmay be\b", "词法", "情态动词"),
    (r"\b(after|before)\b", "词法", "介词"),
    (r"\b(am|is|are|was|were)\s+\w+ing\b", "时态", "现在进行时"),
    (r"\bhave|has\b.*\b\w+(ed|en)\b", "时态", "现在完成时"),
    (r"\bwill\b|\bgoing to\b", "时态", "一般将来时"),
]


def _tense_in_text(text: str) -> str:
    for t in TENSE_NAMES:
        if t in text:
            return t
    return ""


def brief_of(kp: KnowledgePoint) -> str:
    """知识点一句话释义。

    PDF 切分常把正文首句并入标题（title 是长句、body 首行是残片），
    此时 title 本身就是最好的释义；否则取正文首个实质句。
    """
    title = (kp.title or "").strip()
    if (
        len(title) >= 16
        and not title.endswith(("：", ":", "？", "?", "。", "."))
        and ("，" in title or "（" in title or len(title) >= 26)
    ):
        return title[:56] + ("…" if len(title) > 56 else "")
    for ln in (kp.body_md or "").splitlines():
        s = ln.strip().lstrip("#>+-· ").strip()
        s = re.sub(r"^[（(]?[0-9①-⑩一二三四五六七八九十]+[)）、.．]\s*", "", s)
        s = re.sub(r"[*_`#]{1,2}", "", s).strip()
        # 跳过 PDF 断行残片（以标点开头或括号未闭合的行）与表格图示引用
        if s[:1] in "）)、，。；：,.":
            continue
        if len(s) >= 10 and re.search(r"[\u4e00-\u9fa5a-zA-Z]", s) and "如下图" not in s[:8]:
            return s[:56] + ("…" if len(s) > 56 else "")
    return (kp.title or "")[:40]


def classify(kp: KnowledgePoint) -> tuple[str, str]:
    """知识点 → (大类, 主题)。

    大类以讲次 category 为准；主题匹配只在同大类的规则内进行，
    唯固定搭配（第三轮）与标志词密集（第四轮）可跨大类。
    """
    cat = kp.category or "其它"
    # 综合复习不是知识点：按考察主题分流进对应语法主题
    if cat == "综合复习":
        return _classify_review(kp)
    subcat = kp.tags[1] if len(kp.tags) > 1 else ""
    title = kp.title or ""
    body = f"{kp.body_md or ''}\n{kp.examples_md or ''}"

    # 第一轮：subcategory（同大类）
    for rc, theme, rule in _RULES:
        if "subcat" in rule and rc == cat and any(s in subcat for s in rule["subcat"]):
            # 动词时态 subcat → 在时态大类内再细分具体时态
            if theme == "时态细分":
                return cat, _tense_by_title_or_other(title)
            return cat, theme

    # 第二轮：title 强特征（同大类）
    for rc, theme, rule in _RULES:
        if "title" in rule and rc == cat and any(s in title for s in rule["title"]):
            return cat, theme

    # 第三轮：body 搭配（跨大类 → 固定搭配）
    for rc, theme, rule in _RULES:
        if "body" in rule and any(s in body for s in rule["body"]):
            return "固定搭配", theme

    # 第四轮：标志词密集（时态大类）
    if cat == "时态" and len(kp.markers or []) >= 4:
        return cat, "时态标志词"

    return cat, "其它"


def _tense_by_title_or_other(title: str) -> str:
    for t in TENSE_NAMES:
        if t in title:
            return t
    return "动词时态综合"


# 大类 → 主题 的展示顺序（前端聚合用）
THEME_ORDER: dict[str, list[str]] = {
    "词法": ["名词", "代词", "冠词", "数词", "介词", "连词", "形容词与副词",
             "动词", "情态动词", "特殊词法", "其它"],
    "时态": ["时态标志词", "时态呼应", *TENSE_NAMES, "动词时态总览", "动词时态综合", "其它"],
    "语态": ["被动语态", "其它"],
    "非谓语": ["不定式", "动名词", "其它"],
    "句法": ["句子成分", "五大句型结构", "句子类型", "简单句与并列句",
             "宾语从句", "状语从句", "定语从句", "主谓一致", "感叹句",
             "反义疑问句", "特殊疑问句", "倒装句", "其它"],
    "固定搭配": ["动词固定搭配", "介词固定搭配", "固定句型", "其它"],
}


def classify_all(kps: list[KnowledgePoint]) -> dict[int, tuple[str, str]]:
    return {kp.id: classify(kp) for kp in kps}

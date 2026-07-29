"""结构化：清洗后的元素流 → 大纲树 + 知识点。

讲义版式（跨多本观察一致）：
- 标题：``第N讲 动词时态3``
- 节：``I.`` / ``II.`` 罗马 + 点（如 ``I.过去将来时``）
- 小节（=一个知识点）：``1.过去将来时的定义``
- 子项：``1)`` / ``2)`` 或 ``1）``
- 例句：``例：`` / ``例如``
- 课堂练习：``课堂练习1`` 后跟选择题，整段视为练习（不拆成知识点）

输出两类对象：
1. ``Block`` 流——忠实还原讲义（供 ``render_lecture`` 用，表格还原为 GFM）；
2. ``KnowledgePoint`` 列表——每个小节是一个可检索、可溯源的知识点。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .classify import (
    classify_lecture_title,
    detect_relations,
    extract_markers,
    guess_tense_of_kp,
    make_full_title,
    parse_filename,
)
from .models import Block, KnowledgePoint, Lecture, TableData
from .pdf_parser import PageElement, ParsedPdf


# --------------------------------------------------------------------------- #
# 行分类（纯函数）
# --------------------------------------------------------------------------- #

_RE_TITLE = re.compile(r"^第[一二三四五六七八九十百零\d]+讲")
_RE_SECTION = re.compile(r"^[IVX]+\.\s*\S")
_RE_SUBHEAD = re.compile(r"^(\d{1,2})\.\s*(\S.*)$")
_RE_SUBITEM = re.compile(r"^\d{1,2}[)）]\s*\S")
_RE_EXAMPLE = re.compile(r"^(例[如：:，,]|例如[:：]?|e\.g\.|for example)")
_RE_EXERCISE_HDR = re.compile(r"(课堂练习|随堂练习|巩固练习|课后练习|练习[一二三四五六七八九十\d]| Exercises|课堂检测)")
# 选择题选项行（A. / B. ...）——视为练习内容，不当标题
_RE_OPTION = re.compile(r"^\s*[A-DＡ-Ｄ][.、)）]\s*\S")
# 填空下划线多 → 多半是练习/例句，不当小节标题
_UNDERSCORE_RUN = re.compile(r"_{3,}")


def classify_line(line: str, in_exercise: bool = False) -> str:
    """返回行类型。

    取值：title / section / subhead / subitem / example / exercise_hdr /
          option / para。

    >>> classify_line("第二十五讲 动词时态3")
    'title'
    >>> classify_line("I.过去将来时")
    'section'
    >>> classify_line("1.过去将来时的定义")
    'subhead'
    >>> classify_line("1) Mary will go to Canada.")
    'subitem'
    >>> classify_line("例：He always goes to work by car.")
    'example'
    >>> classify_line("课堂练习1")
    'exercise_hdr'
    """
    s = line.strip()
    if not s:
        return "para"
    if _RE_TITLE.search(s):
        return "title"
    if _RE_EXERCISE_HDR.search(s):
        return "exercise_hdr"
    if _RE_SECTION.match(s):
        return "section"
    if _RE_OPTION.match(s):
        return "option"
    # 小节标题判定：数字开头 + 中文、不含长下划线/选项字母（长度不限，
    # 长定义行也按小节处理，由 _split_subhead 拆"标题：正文"）。
    m = _RE_SUBHEAD.match(s)
    if m and not in_exercise:
        body = m.group(2)
        if not _UNDERSCORE_RUN.search(s) and not _RE_OPTION.search(s):
            if re.search(r"[一-鿿]", body):
                return "subhead"
    if _RE_SUBITEM.match(s):
        return "subitem"
    if _RE_EXAMPLE.match(s):
        return "example"
    return "para"


def _clean_title_line(s: str) -> str:
    """合并跨行的标题，如 "动词时态3 \\n 第二十五讲" → "第二十五讲 动词时态3"。"""
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


@dataclass
class StructuredLecture:
    """一本 PDF 的结构化结果。"""

    lecture: Lecture
    blocks: list[Block]
    knowledge_points: list[KnowledgePoint]


def _split_subhead(line: str) -> tuple[str, str]:
    """把小节标题行拆成 (标题, 内联正文)。

    "1.过去将来时的定义：从过去看将来。" → ("过去将来时的定义", "从过去看将来。")
    无冒号则返回 (去掉"1."后的整行, "")。
    """
    s = re.sub(r"^\d{1,2}\.\s*", "", line.strip())
    for sep in ("：", ":"):
        if sep in s:
            head, tail = s.split(sep, 1)
            head = head.strip()
            tail = tail.strip()
            if head and tail:
                return head, tail
    return s, ""


def _flush_kp(
    kp: Optional[KnowledgePoint],
    kps: list[KnowledgePoint],
) -> None:
    """把当前知识点收尾并入列表（只要有标题即保留）。"""
    if kp is None:
        return
    if not kp.title:
        return
    kps.append(kp)


def structure(parsed: ParsedPdf, source_file: str = "") -> StructuredLecture:
    """把 ParsedPdf 解析为 讲次 + blocks + 知识点。"""
    number, short_title, _ = parse_filename(source_file or "")
    category, subcategory = classify_lecture_title(short_title)

    # 解析正文得到一个原始标题候选（若有"第N讲…"）
    title_line = ""
    for el in parsed.elements:
        if el.kind == "text" and _RE_TITLE.search(el.text):
            title_line = el.text
            break
    if title_line:
        full = _clean_title_line(title_line)
    else:
        full = make_full_title(number or 0, short_title)

    lecture = Lecture(
        number=number or 0,
        title=short_title,
        full_title=full,
        category=category,
        subcategory=subcategory,
        source_file=source_file,
        page_count=parsed.page_count,
    )

    blocks: list[Block] = []
    # 标题 H1 由 render_lecture 统一输出，这里不重复加入 block 流

    kps: list[KnowledgePoint] = []
    current_section: Optional[str] = None
    current_kp: Optional[KnowledgePoint] = None
    in_exercise = False
    seq = 1

    def section_path() -> str:
        parts = [p for p in [current_section] if p]
        return " > ".join(parts)

    def new_kp(title: str, page: int) -> KnowledgePoint:
        return KnowledgePoint(
            title=title,
            lecture_number=lecture.number,
            category=lecture.category,
            section_path=section_path(),
            source_page=page,
            ord=len(kps),
        )

    for el in parsed.elements:
        page = el.page
        if el.kind == "table":
            tdata: TableData = el.table  # type: ignore[assignment]
            # 在 block 流里记录表格（还原用）
            blocks.append(Block(kind="table", table_data=tdata, page=page, seq=seq))
            seq += 1
            # 附到当前知识点；若无则用节标题建一个
            from .markdown import table_to_markdown

            md = table_to_markdown(tdata)
            if current_kp is None or in_exercise:
                # 节级表格：归到当前节
                if current_section and not in_exercise:
                    current_kp = new_kp(current_section, page)
            if current_kp is not None and not in_exercise:
                if current_kp.table_md:
                    current_kp.table_md = current_kp.table_md + "\n\n" + md
                else:
                    current_kp.table_md = md
                    current_kp.table_data = tdata
                    current_kp.is_table = True
            continue

        line = el.text
        kind = classify_line(line, in_exercise=in_exercise)

        if kind == "title":
            # 标题行（已在 blocks[0]），跳过
            continue

        if kind == "exercise_hdr":
            in_exercise = True
            _flush_kp(current_kp, kps)
            current_kp = None
            blocks.append(Block(kind="exercise", text_md=line, page=page, seq=seq))
            seq += 1
            continue

        if kind == "section":
            in_exercise = False
            _flush_kp(current_kp, kps)
            current_kp = None
            current_section = line.strip()
            blocks.append(Block(kind="heading", text_md=f"## {line.strip()}", page=page, seq=seq))
            seq += 1
            continue

        if in_exercise:
            blocks.append(Block(kind="exercise", text_md=line, page=page, seq=seq))
            seq += 1
            continue

        if kind == "subhead":
            _flush_kp(current_kp, kps)
            title, inline_body = _split_subhead(line)
            current_kp = new_kp(title, page)
            if inline_body:
                current_kp.body_md = inline_body
            blocks.append(Block(kind="subheading", text_md=f"### {line.strip()}", page=page, seq=seq))
            seq += 1
            continue

        # 其余：归入当前知识点
        if kind == "example":
            if current_kp is None:
                current_kp = new_kp(current_section or "概述", page)
            if current_kp.examples_md:
                current_kp.examples_md += "\n" + line
            else:
                current_kp.examples_md = line
        else:  # subitem / option / para
            if current_kp is None:
                # 节开头还没有小节标题：把段落挂在节级 KP
                if current_section:
                    current_kp = new_kp(current_section, page)
                else:
                    current_kp = new_kp(short_title or "概述", page)
            if current_kp.body_md:
                current_kp.body_md += "\n" + line
            else:
                current_kp.body_md = line

        blocks.append(Block(kind=kind, text_md=line, page=page, seq=seq))
        seq += 1

    _flush_kp(current_kp, kps)

    # 后处理：分类、标志词、关系
    _enrich(kps, lecture)

    return StructuredLecture(lecture=lecture, blocks=blocks, knowledge_points=kps)


def _enrich(kps: list[KnowledgePoint], lecture: Lecture) -> None:
    """为每个知识点补充标志词、关系、时态、标签。"""
    for kp in kps:
        text = "\n".join([kp.body_md, kp.examples_md, kp.table_md])
        tense = guess_tense_of_kp(kp.title, kp.body_md)
        kp.markers = extract_markers(text, tense=tense)
        kp.relations = detect_relations(text)
        tags: list[str] = [lecture.category]
        if lecture.subcategory:
            tags.append(lecture.subcategory)
        if tense:
            tags.append(tense)
        kp.tags = tags


def structure_from_file(pdf_path: str) -> StructuredLecture:
    """端到端：PDF 路径 → 结构化讲次。

    ``source_file`` 只记录文件名（不含目录），避免本地绝对路径随数据集外泄。
    """
    import os

    from .pdf_parser import parse_pdf

    parsed = parse_pdf(pdf_path)
    return structure(parsed, source_file=os.path.basename(pdf_path))

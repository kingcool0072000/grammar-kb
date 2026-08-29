"""作业卷导入：哈一作业卷 PDF → 结构化题目（homework_question 表）。

作业卷版式（跨多份观察一致）：

- 页眉页脚噪声：``睿爸小屋`` / ``哈一（初中语法）`` / ``作业卷NN`` / ``作业卷NN k | n``
- 大题标题：``I. 中译英`` / ``II. Fill in the blanks ...`` / ``III. 选择最佳答案``（罗马数字）
- 题号行：``9. These are _________brothers. (he)``，或题号独立成行（``1.`` 后跟题干行）
- 选择题选项：``A. He`` / ``A．This``（点号半角/全角混用）独立成行
- 表格填空题（如代词人称表）：空格编号藏在表格单元格里，文本层表现为 ``_____1_____``

题号有两种风格，需要统一成平台的连续题号（1~35）：

- 连续式：各大题题号连续（I 大题 1-8，II 大题直接 9-20，III 21-35）
- 重启式：各大题从 1 重排（I 大题 1-10，II 大题 1-10 = 平台 11-20，III 直接标 21-35）

规则：大题首个题号为 1 且前面已有题 → 该大题题号 = 前面累计题数 + 卷内题号；
否则（首题号接着前面的连续编号，如 9 接 8）直接用卷内题号。

解析核心 :func:`parse_paper_text` 是纯函数（文本 → 题目列表），便于单测。
"""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field
from typing import Optional

# --------------------------------------------------------------------------- #
# 纯函数：文本 → 题目
# --------------------------------------------------------------------------- #

# 页眉页脚/噪声行
_RE_NOISE = re.compile(
    r"^(睿爸小屋|哈一（初中语法）|第[一二三四五六七八九十百零\d]+讲作业卷"
    r"|作业卷\d+(\s*\d+\s*\|\s*\d+)?|\d{1,3}\s*\|\s*\d{1,3}|\d{1,3})$"
)
# 大题标题：罗马数字 + 点号（必须有点，避免误吞 "I live near..." 这类题干）
_RE_SECTION = re.compile(r"^([IVX]{1,4})[.、．]\s*(.*)$")
# 题号行：题号 + 点号（半角/全角），题干可空（题号独立成行时）
_RE_QNUM = re.compile(r"^(\d{1,2})[.、．]\s*(.*)$")
# 选择题选项：字母 + 点号（必须有点，避免误吞 "A friend of ..." 题干）
_RE_OPT = re.compile(r"^([A-D])[.、．]\s*(.+)$")
# 表格空格编号：_____3_____
_RE_CELL = re.compile(r"_{2,}\s*(\d{1,2})\s*_{2,}")

_CELL_STEM = "（表格填空题，原题见作业卷表格）"


@dataclass
class HwQuestion:
    """一道作业题。qnum 为全卷连续题号（与测验平台一致）。"""

    qnum: int
    section: str = ""          # 所属大题标题原文（如 "III. 选择最佳答案"）
    stem: str = ""
    options: list[str] = field(default_factory=list)  # 选项正文（不含 A. 前缀）
    is_cell: bool = False      # 表格填空（题干在表格里，仅占位）


def parse_paper_text(text: str) -> list[HwQuestion]:
    """作业卷全文文本 → 按平台连续题号排序的题目列表。"""
    sections: list[dict] = []
    cur_sec: Optional[dict] = None
    cur_q: Optional[dict] = None

    def new_section(name: str) -> None:
        nonlocal cur_sec, cur_q
        cur_sec = {"name": name, "questions": []}
        sections.append(cur_sec)
        cur_q = None

    for raw in text.splitlines():
        ln = raw.strip()
        if not ln or _RE_NOISE.match(ln):
            continue

        m = _RE_SECTION.match(ln)
        if m and len(ln) < 80:
            new_section(ln)
            continue

        m = _RE_QNUM.match(ln)
        if m:
            local = int(m.group(1))
            if cur_sec is None:
                new_section("")
            # 同一大题内题号重复（PDF 重渲染残片）→ 丢弃
            if any(q["local"] == local for q in cur_sec["questions"]):
                continue
            cur_q = {
                "local": local,
                "stem": m.group(2).strip(),
                "options": [],
                "cell": False,
            }
            cur_sec["questions"].append(cur_q)
            continue

        # 表格空格编号：登记为占位题（不吸收后续行，避免表格杂文混入题干）
        cell_added = False
        for cm in _RE_CELL.finditer(ln):
            local = int(cm.group(1))
            if cur_sec and not any(q["local"] == local for q in cur_sec["questions"]):
                cur_sec["questions"].append(
                    {"local": local, "stem": _CELL_STEM, "options": [], "cell": True}
                )
                cell_added = True
        if cell_added:
            cur_q = None
            continue

        m = _RE_OPT.match(ln)
        if m and cur_q is not None:
            letter, body = m.group(1), m.group(2).strip()
            expected = "ABCD"[len(cur_q["options"])] if cur_q["options"] else "A"
            if letter == expected:
                cur_q["options"].append(body)
                continue
            # 不是预期的下一个选项字母（多半是题干里的枚举）→ 并入题干

        if cur_q is not None and not cur_q["cell"]:
            cur_q["stem"] = (cur_q["stem"] + " " + ln).strip()
        # 没有当前题（大题说明文字/表格残片）→ 忽略

    return _assign_qnums(sections)


def _assign_qnums(sections: list[dict]) -> list[HwQuestion]:
    """大题内题号 → 全卷连续题号（处理重启式/连续式两种编号风格）。"""
    out: list[HwQuestion] = []
    prev = 0  # 目前累计到的全局题号
    for sec in sections:
        qs = sorted(sec["questions"], key=lambda q: q["local"])
        if not qs:
            continue
        base = prev if (qs[0]["local"] == 1 and prev > 0) else 0
        for q in qs:
            out.append(
                HwQuestion(
                    qnum=base + q["local"],
                    section=sec["name"],
                    stem=q["stem"],
                    options=q["options"],
                    is_cell=q["cell"],
                )
            )
        prev = base + qs[-1]["local"]
    out.sort(key=lambda q: q.qnum)
    return out


# --------------------------------------------------------------------------- #
# 文件名解析 / PDF 读取 / 入库
# --------------------------------------------------------------------------- #


def parse_homework_filename(filename: str) -> tuple[Optional[int], str]:
    """``04.综合复习一_作业卷.pdf`` → (4, "综合复习一")；非作业卷返回 (None, "")。"""
    base = os.path.basename(filename)
    if "作业卷" not in base:
        return None, ""
    m = re.search(r"(\d{1,2})", base)
    if not m:
        return None, ""
    title = re.sub(r"\.pdf$", "", base, flags=re.I)
    title = re.sub(r"^\d{1,2}[.、.\-]?\s*", "", title)
    title = re.sub(r"[_\s]*作业卷[_\s]*$", "", title).strip()
    return int(m.group(1)), title


def parse_homework_pdf(pdf_path: str) -> tuple[int, str, list[HwQuestion]]:
    """作业卷 PDF → (讲次, 标题, 题目列表)。"""
    import fitz

    number, title = parse_homework_filename(pdf_path)
    if number is None:
        raise ValueError(f"文件名无法解析出讲次：{pdf_path}")
    doc = fitz.open(pdf_path)
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    return number, title, parse_paper_text(text)


def ingest_homework_pdf(db, pdf_path: str) -> tuple[int, str, int]:
    """解析并导入单份作业卷，返回 (讲次, 标题, 题目数)。"""
    number, title, questions = parse_homework_pdf(pdf_path)
    db.replace_homework(number, questions)
    return number, title, len(questions)


def ingest_homework_dir(db, directory: str) -> list[tuple[int, str, int, str]]:
    """导入目录下所有作业卷（文件名含「作业卷」的 PDF），按文件名排序。"""
    files = sorted(
        f for f in glob.glob(os.path.join(directory, "*.pdf"))
        if "作业卷" in os.path.basename(f) and not os.path.basename(f).startswith(".")
    )
    results = []
    for f in files:
        try:
            n, t, c = ingest_homework_pdf(db, f)
            results.append((n, t, c, ""))
        except Exception as e:  # noqa: BLE001
            results.append((0, os.path.basename(f), 0, f"{type(e).__name__}: {e}"))
    return results

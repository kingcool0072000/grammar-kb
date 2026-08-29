"""FCE 青少版（For Schools）模拟卷 PDF → fce.db（按 4 个 Test 维度存储）。

来源 PDF 为纯扫描图（无文字层），OCR 由 macOS Vision（Swift）完成：
渲染 200dpi PNG → ``ocr.swift`` 输出 ``y<TAB>x<TAB>text`` 三列行坐标文本。

卷面结构（4 套 Test，每套相同）：

- Reading and Use of English（7 个 Part，共 52 题）
  P1 四选一（选项 4 列网格页）/ P2 完形填空 / P3 词形变换（行尾大写提示词）
  / P4 关键词改写（两句 + 大写关键词）/ P5 阅读理解四选一
  / P6 句子还原（A-G 选项框）/ P7 多篇匹配（A-D 人物）
- Writing（P1 必答作文 + P2 四选一 2-5）
- Listening（P1 三选一 / P2 句子填空 / P3 A-H 匹配 / P4 三选一，共 30 题）
- Speaking（考官框架说明）
- 答案：每套 Test 的 Key 起始页（RUE 答案）+ 下一页（Listening 答案）

解析核心均为纯函数（输入行列表，输出结构化数据），便于抽查校验。
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------- #
# OCR：PDF → {page: [(y, x, text), ...]}
# --------------------------------------------------------------------------- #

SWIFT_OCR = r'''
import Foundation
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count >= 2 else { fatalError("usage: ocr.swift image.png") }
let url = URL(fileURLWithPath: args[1])
guard let img = NSImage(contentsOf: url),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    fatalError("cannot load image")
}
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
request.recognitionLanguages = ["en-US"]
request.automaticallyDetectsLanguage = false
let handler = VNImageRequestHandler(cgImage: cg, options: [:])
try handler.perform([request])
let lines = (request.results ?? []).compactMap { obs -> String? in
    guard let candidate = obs.topCandidates(1).first else { return nil }
    let bbox = obs.boundingBox
    let y = Int((1.0 - bbox.origin.y) * 10000)
    let x = Int(bbox.origin.x * 10000)
    return "\(y)\t\(x)\t\(candidate.string)"
}
print(lines.joined(separator: "\n"))
'''

# 水印/页脚行：Vision 偶尔把页脚广告水印识别出来，集中在页面最底部
_FOOTER_Y = 9600
# 重复出现的运行页眉（书眉）——保留在 OCR 数据里供结构探测/答案定位，
# 仅在正文组装（merge_rows）时过滤
_RUNNING_HEADERS = {
    "test 1", "test 2", "test 3", "test 4",
    "test 1 key", "test 2 key", "test 3 key", "test 4 key",
    "reading and use of english", "writing", "listening",
}


def is_running_header(text: str) -> bool:
    return text.strip().lower() in _RUNNING_HEADERS


# OCR 形近字归一化：西里尔/希腊形近字符 → 拉丁（如答案 "А" → "A"）
_LOOKALIKE = str.maketrans({
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K",
    "М": "M", "О": "O", "Р": "P", "Т": "T", "У": "Y", "Х": "X",
    "і": "i", "ѕ": "s", "ԁ": "d", "о": "o",
})


@dataclass
class OcrLine:
    page: int
    y: int
    x: int
    text: str


def parse_ocr_file(page: int, text: str) -> list[OcrLine]:
    """单个 OCR 输出文件 → 清洗后的 OcrLine 列表（去页脚水印/页码/书眉）。"""
    out: list[OcrLine] = []
    for raw in text.splitlines():
        parts = raw.split("\t", 2)
        if len(parts) != 3:
            continue
        try:
            y, x = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        t = parts[2].strip().translate(_LOOKALIKE)
        if not t or y >= _FOOTER_Y:
            continue
        if re.fullmatch(r"\d{1,3}", t) and y >= _FOOTER_Y:  # 页码（页脚区）
            continue
        if re.search(r"[KE＃#]{0,2}KE[TТЕ]|kpf|jq-", t):  # 水印残片
            continue
        out.append(OcrLine(page=page, y=y, x=x, text=t))
    return out


def render_and_ocr(pdf_path: str, out_dir: str) -> dict[int, list[OcrLine]]:
    """渲染 PDF 全部页面并 OCR（macOS Vision），返回 {页码: 行列表}。"""
    import fitz  # type: ignore

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    swift_path = out / "ocr.swift"
    swift_path.write_text(SWIFT_OCR, encoding="utf-8")
    pages: dict[int, list[OcrLine]] = {}
    with tempfile.TemporaryDirectory() as td:
        doc = fitz.open(pdf_path)
        for i in range(doc.page_count):
            png = Path(td) / f"p{i+1:03d}.png"
            txt = out / f"p{i+1:03d}.txt"
            if not txt.exists() or txt.stat().st_size == 0:
                doc[i].get_pixmap(dpi=200).save(png)
                subprocess.run(
                    ["swift", str(swift_path), str(png)],
                    stdout=open(txt, "w"),
                    stderr=subprocess.DEVNULL,
                    check=True,
                )
            pages[i + 1] = parse_ocr_file(i + 1, txt.read_text(encoding="utf-8"))
        doc.close()
    return pages


def load_ocr_dir(ocr_dir: str) -> dict[int, list[OcrLine]]:
    """加载已 OCR 好的目录（pNNN.txt），跳过脚本本身。"""
    pages: dict[int, list[OcrLine]] = {}
    for f in sorted(Path(ocr_dir).glob("p[0-9]*.txt")):
        m = re.fullmatch(r"p(\d+)\.txt", f.name)
        if not m:
            continue
        pages[int(m.group(1))] = parse_ocr_file(
            int(m.group(1)), f.read_text(encoding="utf-8")
        )
    return pages


# --------------------------------------------------------------------------- #
# 通用行工具
# --------------------------------------------------------------------------- #


def merge_rows(
    lines: list[OcrLine], tol: int = 45, keep_headers: bool = False
) -> list[tuple[int, list[OcrLine]]]:
    """按页聚成视觉行（页内按 y 聚类、行内按 x 排序），返回 [(y0, [OcrLine...]), ...]。

    必须先按页分组再按 y 聚类——跨页直接按 y 排序会让两页内容互相穿插。
    默认丢弃书眉行（Test N / paper 名）；答案页解析需用书眉切区块，
    传 keep_headers=True 保留。
    """
    order = sorted(
        (l for l in lines if keep_headers or not is_running_header(l.text)),
        key=lambda l: (l.page, l.y, l.x),
    )
    rows: list[tuple[int, list[OcrLine]]] = []
    for ln in order:
        if rows and ln.page == rows[-1][1][0].page and abs(ln.y - rows[-1][0]) <= tol:
            rows[-1][1].append(ln)
        else:
            rows.append((ln.y, [ln]))
    for _, rls in rows:
        rls.sort(key=lambda l: l.x)
    return rows


def row_text(row: tuple[int, list[OcrLine]]) -> str:
    return " ".join(l.text for l in row[1]).strip()


# --------------------------------------------------------------------------- #
# 结构探测：Test / Paper / Part / Key 页码
# --------------------------------------------------------------------------- #


@dataclass
class TestSpan:
    test_id: int
    rue_start: int          # READING AND USE OF ENGLISH 起始页
    papers: dict[str, int]  # paper -> 起始页
    key_start: int          # "Test N Key" 答案起始页


_PAPER_MARK = {
    "Reading and Use of English": "READING AND USE OF ENGLISH",
    "Writing": "WRITING (",
    "Listening": "LISTENING (",
    "Speaking": "SPEAKING (",
}


def detect_test_spans(pages: dict[int, list[OcrLine]]) -> list[TestSpan]:
    """扫描全部页面，定位 4 套 Test 的各 paper 起始页与答案页。"""
    spans: list[TestSpan] = []
    key_pages: dict[int, int] = {}
    for pno, lines in pages.items():
        low = [l.text.lower() for l in lines if l.y < 1500]
        joined = " ".join(low)
        for tid in range(1, 5):
            if f"test {tid}" in joined and "reading and use of english" in joined:
                if not any(s.test_id == tid for s in spans):
                    spans.append(TestSpan(tid, pno, {}, 0))
            if f"test {tid} key" in joined:
                key_pages.setdefault(tid, pno)

    for s in spans:
        # paper 起始页：从 rue_start 起向后 25 页内找各 paper 页眉（仅偶数区）
        end = s.rue_start + 26
        for pno in range(s.rue_start, min(end, max(pages) + 1)):
            head = " ".join(
                l.text for l in pages.get(pno, []) if l.y < 1300
            )
            for paper, mark in _PAPER_MARK.items():
                if mark in head and paper not in s.papers:
                    s.papers[paper] = pno
        s.key_start = key_pages.get(s.test_id, 0)
    spans.sort(key=lambda s: s.test_id)
    return spans


def part_pages(
    pages: dict[int, list[OcrLine]],
    start_page: int,
    end_page: int,
) -> dict[int, list[int]]:
    """某 paper 页区间内 {"Part N" 页眉} → {part: [页码...]}（含 part 起始页到下一 part 前）。"""
    part_start: dict[int, int] = {}
    for pno in range(start_page, end_page + 1):
        for l in pages.get(pno, []):
            if l.y < 2600:
                m = re.fullmatch(r"Part (\d)", l.text)
                if m and int(m.group(1)) not in part_start:
                    part_start[int(m.group(1))] = pno
    starts = sorted(part_start.items(), key=lambda kv: kv[1])
    out: dict[int, list[int]] = {}
    for i, (part, pno) in enumerate(starts):
        nxt = starts[i + 1][1] if i + 1 < len(starts) else end_page + 1
        out[part] = list(range(pno, nxt))
    return out


def pages_lines(
    pages: dict[int, list[OcrLine]], pnos: list[int]
) -> list[OcrLine]:
    out: list[OcrLine] = []
    for p in pnos:
        out.extend(pages.get(p, []))
    return out


# --------------------------------------------------------------------------- #
# 各题型解析（纯函数）
# --------------------------------------------------------------------------- #


def parse_options_grid(lines: list[OcrLine], letters: str = "ABCD") -> dict[int, dict[str, str]]:
    """四列选项网格页（RUE Part 1）：每行一道题，按 x 列心分列。

    列心由带字母前缀（含裸字母）的单元格推断；每个单元格贴最近列心，
    去掉题号/字母前缀后按列拼接（兼容字母与正文分成两个 span 的情况）。
    """
    rows = merge_rows(lines, tol=70)
    col_x: dict[str, list[int]] = {}
    for _, rls in rows:
        for ln in rls:
            m = re.match(rf"^\d{{1,2}}\s+([{letters}])\b", ln.text) or re.match(
                rf"^([{letters}])[.、]?\s*\S", ln.text
            )
            lm = re.fullmatch(rf"([{letters}])", ln.text)
            if m:
                col_x.setdefault(m.group(1), []).append(ln.x)
            elif lm:
                col_x.setdefault(lm.group(1), []).append(ln.x)
    centroids = {k: sum(v) / len(v) for k, v in col_x.items() if len(v) >= 3}
    if len(centroids) < len(letters):
        return {}

    out: dict[int, dict[str, str]] = {}
    for _, rls in rows:
        rls = sorted(rls, key=lambda l: l.x)
        m = re.match(r"^(\d{1,2})(?:\s|$)", rls[0].text)
        if not m or not (1 <= int(m.group(1)) <= 52):
            continue
        qnum = int(m.group(1))
        col_texts: dict[str, list[str]] = {}
        for ln in rls:
            col = min(centroids, key=lambda k: abs(centroids[k] - ln.x))
            txt = re.sub(r"^\d{1,2}\s+", "", ln.text)  # 去题号
            txt = re.sub(rf"^[{letters}][.、]?\s*", "", txt)  # 去字母前缀
            if txt:
                col_texts.setdefault(col, []).append(txt)
        out[qnum] = {k: " ".join(col_texts.get(k, [])) for k in letters}
    return out


_RE_QHEAD = re.compile(r"^(\d{1,2})\s+(.+)$")
_RE_BARENUM = re.compile(r"^\d{1,2}$")
_RE_OPT = re.compile(r"^([A-H])[.、]?\s+(.+)$")
_RE_LETTER_ONLY = re.compile(r"^([A-H])$")


def parse_line_items(
    lines: list[OcrLine],
    qrange: tuple[int, int],
    letters: str,
    qhead_x: float = 900,
) -> dict[int, dict]:
    """顺序题组（RUE P5 / Listening P1、P4）："N 题干" 行 + A-D 选项行 + 续行。

    - 题号可独立成行（裸数字），下一行接题干；
    - 选项字母可能丢失：续行 y 间距明显拉开时视为下一选项。
    """
    rows = merge_rows(lines)
    items: dict[int, dict] = {}
    cur: Optional[dict] = None
    last_y = 0
    last_was_option = False
    lo, hi = qrange
    for y, rls in rows:
        t = row_text((y, rls))
        x0 = rls[0].x
        m = _RE_QHEAD.match(t)
        if (m and x0 < qhead_x + 400) or (
            _RE_BARENUM.match(t) and x0 < qhead_x + 400
        ):
            n = int(m.group(1) if m else t)
            if lo <= n <= hi:
                cur = {"qnum": n, "stem": (m.group(2) if m else ""), "options": {}}
                items[n] = cur
                last_y = y
                last_was_option = False
                continue
        if cur is None:
            continue
        om = _RE_OPT.match(t)
        lm = _RE_LETTER_ONLY.match(t)
        if om and om.group(1) in letters:
            cur["options"][om.group(1)] = om.group(2)
            last_y = y
            last_was_option = True
            continue
        if lm and lm.group(1) in letters and lm.group(1) not in cur["options"]:
            cur["options"][lm.group(1)] = ""
            last_y = y
            last_was_option = True
            continue
        # 题号被 OCR 漏掉：上一题已完成（选项 ≥2）、大间距后的左边距行 → 推断下一题
        n_expected = cur["qnum"] + 1
        if (
            last_was_option
            and len(cur["options"]) >= 2
            and y - last_y > 250
            and x0 < qhead_x + 400
            and n_expected <= hi
            and n_expected not in items
            and re.match(r"^[A-Z]", t)
        ):
            cur = {"qnum": n_expected, "stem": t, "options": {}}
            items[n_expected] = cur
            last_y = y
            last_was_option = False
            continue
        # 选项字母丢失：已有 ≥2 个选项、行首缩进比选项基线深、间距接近选项行距
        nxt = next((k for k in letters if k not in cur["options"]), None)
        if (
            nxt
            and last_was_option
            and len(cur["options"]) >= 1
            and letters.index(nxt) == len(cur["options"])
            and y - last_y < 260
            and x0 >= 900
            and not _RE_OPT.match(t)
        ):
            cur["options"][nxt] = t
            last_y = y
            last_was_option = True
            continue
        # 续行：补到最近一个选项
        filled = [k for k in letters if k in cur["options"] and cur["options"][k]]
        nxt = next((k for k in letters if k not in cur["options"]), None)
        if (
            nxt
            and filled
            and y - last_y > 230
            and len(cur["options"].get(filled[-1], "")) > 0
            and letters.index(nxt) == len(filled)
        ):
            cur["options"][nxt] = t  # 丢失字母的下一个选项
        elif filled:
            k = filled[-1]
            cur["options"][k] = (cur["options"][k] + " " + t).strip()
        else:
            cur["stem"] = (cur["stem"] + " " + t).strip()
        last_y = y
        last_was_option = False
    return items


def parse_gap_passage(
    lines: list[OcrLine], qrange: tuple[int, int]
) -> tuple[str, dict[int, str]]:
    """完形/词形变换（RUE P2/P3）：整段文章 + 每个空号所在句行。

    返回 (passage, {qnum: 含空号的行})。
    """
    rows = merge_rows(lines)
    lo, hi = qrange
    passage: list[str] = []
    gaps: dict[int, str] = {}
    for _, rls in rows:
        t = row_text((_, rls))
        passage.append(t)
        for gm in re.finditer(r"\((\d{1,2})\)", t):
            n = int(gm.group(1))
            if lo <= n <= hi:
                gaps[n] = t
    return "\n".join(passage), gaps


def parse_cue_words(lines: list[OcrLine]) -> dict[int, str]:
    """RUE P3 行尾大写提示词：x>6500 的全大写短行 → 就近配对同页同行 y 的空号。"""
    cues: dict[int, str] = {}
    for l in lines:
        if l.x > 6500 and re.fullmatch(r"[A-Z][A-Z'/-]{2,}", l.text):
            # 就近找同一 y 范围内出现的空号
            for l2 in lines:
                if l2.page == l.page and abs(l2.y - l.y) < 60:
                    for gm in re.finditer(r"\((\d{1,2})\)", l2.text):
                        cues[int(gm.group(1))] = l.text
    return cues


def parse_transforms(lines: list[OcrLine], qrange: tuple[int, int]) -> dict[int, dict]:
    """RUE P4 关键词改写："N 第一句" + 大写关键词 + 第二句（含省略号）。"""
    rows = merge_rows(lines)
    lo, hi = qrange
    out: dict[int, dict] = {}
    cur: Optional[dict] = None
    for _, rls in rows:
        t = row_text((_, rls))
        m = _RE_QHEAD.match(t)
        if m and re.match(r"^\d{1,2}\b", t) and rls[0].x < 1300:
            n = int(m.group(1))
            if lo <= n <= hi:
                cur = {"qnum": n, "stem": m.group(2), "keyword": "", "stem2": ""}
                out[n] = cur
                continue
        if cur is None:
            continue
        if re.fullmatch(r"[A-Z'’]{2,}", t) and not cur["keyword"]:
            cur["keyword"] = t
        else:
            cur["stem2"] = (cur["stem2"] + " " + t).strip()
    return out


def parse_letter_box(
    lines: list[OcrLine], letters: str = "ABCDEFG"
) -> tuple[dict[str, str], list[OcrLine]]:
    """A-G 选项框（RUE P6）：字母行 + 缩进续行。返回 (选项, 非选项行)。"""
    rows = merge_rows(lines)
    opts: dict[str, str] = {}
    cur_letter: Optional[str] = None
    rest: list[OcrLine] = []
    for _, rls in rows:
        t = row_text((_, rls))
        m = re.match(rf"^([{letters}])\s+(.+)$", t)
        lm = re.fullmatch(rf"[{letters}]", t)
        if m and (cur_letter is None or m.group(1) != cur_letter):
            cur_letter = m.group(1)
            opts[cur_letter] = m.group(2)
        elif lm and cur_letter is None:
            cur_letter = lm.group(1)
            opts[cur_letter] = ""
        elif cur_letter is not None and rls[0].x > 1100:
            opts[cur_letter] = (opts[cur_letter] + " " + t).strip()
        else:
            rest.extend(rls)
    return opts, rest


def parse_matching_statements(
    lines: list[OcrLine], qrange: tuple[int, int]
) -> dict[int, str]:
    """RUE P7 / Listening P3：陈述句列表 + 右侧题号框，按 y 就近配对。"""
    lo, hi = qrange
    qnums = [(l.y, l.x, int(l.text)) for l in lines if re.fullmatch(r"\d{1,2}", l.text)
             and l.x > 7500 and lo <= int(l.text) <= hi]
    # 陈述句行：x<2500 的正文行（排除指令/标题）
    stmts = [
        (y, row_text((y, rls)))
        for y, rls in merge_rows([l for l in lines if l.x < 2500 and l.y > 2300])
    ]
    out: dict[int, str] = {}
    for qy, _qx, n in qnums:
        best = min(stmts, key=lambda s: abs(s[0] - qy), default=None)
        if best and abs(best[0] - qy) < 400:
            out[n] = best[1]
    return out


def parse_gap_fill_numbered(
    lines: list[OcrLine], qrange: tuple[int, int]
) -> dict[int, str]:
    """听力 P2 句子填空：题号是嵌在句中空格处的裸数字（x 不定，非左缘）。

    合并视觉行后，行内出现的范围内裸数字（x>2500，排除行首题干序号）
    即空号，所在行去掉该数字后作为题干。
    """
    lo, hi = qrange
    rows = merge_rows([l for l in lines if l.y > 2300])

    def is_marker(rls: list) -> Optional[int]:
        # 整行只有（右缘的）范围内裸数字 → 空号标记行
        if len(rls) == 1 and re.fullmatch(r"\d{1,2}", rls[0].text):
            n = int(rls[0].text)
            return n if lo <= n <= hi else None
        return None

    out: dict[int, str] = {}
    for y, rls in rows:
        mk = is_marker(rls)
        if mk is None:
            # 行内嵌数字：span 头（"15 is used..."）、span 尾（"...country.42"）、
            # 独立 token（"The name 15"）三种形态
            for l in rls:
                mm = (
                    re.fullmatch(r"(\d{1,2})", l.text)
                    or re.match(r"^(\d{1,2})\s+\S", l.text)
                    or re.search(r"(?:^|\s|\.)(\d{1,2})[. ]?$", l.text)
                )
                if not mm:
                    continue
                n = int(mm.group(1))
                if lo <= n <= hi and n not in out and l.x > 900:
                    t = row_text((y, rls))
                    out[n] = re.sub(rf"\s*\.?\s*{n}\s*", " ", t, count=1).strip()
        else:
            # 独立数字行：就近句子行（偏移可达 ~700px）；marker 常在句尾空格的
            # 右侧（基线略低于句子），同距时优先上方句子
            best = min(
                (
                    (sy, row_text((sy, srls)))
                    for (sy, srls) in rows
                    if not is_marker(srls) and row_text((sy, srls)) and mk not in out
                ),
                key=lambda s: (abs(s[0] - y) - (10 if s[0] < y else 0), s[0]),
                default=None,
            )
            if best and abs(best[0] - y) < 700:
                out[mk] = best[1]
    return out


def parse_people(lines: list[OcrLine]) -> tuple[dict[str, str], list[OcrLine]]:
    """RUE P7 人物页：独立字母行（A-D）+ 人名行 → {A: 姓名, ...}。"""
    rows = merge_rows(lines)
    people: dict[str, str] = {}
    rest: list[OcrLine] = []
    last_letter: Optional[str] = None
    for _, rls in rows:
        t = row_text((_, rls))
        if re.fullmatch(r"[A-D]", t) and rls[0].x < 800:
            last_letter = t
            people.setdefault(t, "")
            continue
        if last_letter and not people[last_letter] and len(t) < 30 and re.match(
            r"^[A-Z][a-z]+", t
        ):
            people[last_letter] = t
            continue
        rest.extend(rls)
    return people, rest


# --------------------------------------------------------------------------- #
# 答案 Key 解析
# --------------------------------------------------------------------------- #


def parse_key_page(lines: list[OcrLine]) -> dict[str, dict[int, str]]:
    """答案页 → {paper: {qnum: answer}}。

    字母题型（RUE P1/5/6/7、L P1/3/4）按阅读序 token 游走：
    字母 token 按序赋给期望题号，OCR 粘连噪声（"101 ome"、", 2У А"）被跳过。
    单词/短语题型（RUE P2/3/4、L P2）按 "N answer" 段解析，题号单调递增过滤。
    """
    rows = merge_rows(lines, keep_headers=True)
    # 切分：Reading and Use of English / Listening 两个 paper 区块
    sections: list[tuple[str, str]] = []
    cur_paper = ""
    for y, rls in rows:
        t = row_text((y, rls))
        if re.match(r"^Reading and Use of English", t):
            cur_paper = "Reading and Use of English"
        elif re.match(r"^Listening", t):
            cur_paper = "Listening"
        elif re.match(r"^Transcript", t):
            cur_paper = ""
        sections.append((cur_paper, t))

    # 单词/短语题型的 Part 题号范围（OCR y 抖动会让行乱序，不能用单调过滤）
    word_ranges = {
        "Reading and Use of English": {2: (9, 16), 3: (17, 24), 4: (25, 30)},
        "Listening": {2: (9, 18)},
    }
    out: dict[str, dict[int, str]] = {}
    # 字母题型的 Part 起始题号（token 游走的期望起点）
    part_starts = {
        "Reading and Use of English": {1: 1, 5: 31, 6: 37, 7: 43},
        "Listening": {1: 1, 3: 19, 4: 24},
    }
    for paper in ("Reading and Use of English", "Listening"):
        letter_parts = set(part_starts[paper])
        cur_part = 0
        last_q: Optional[int] = None
        expected = 1
        for pap, t in sections:
            if pap != paper:
                continue
            pm = re.fullmatch(r"Part (\d)", t)
            if pm:
                cur_part = int(pm.group(1))
                last_q = None
                expected = part_starts[paper].get(cur_part, 1)
                continue
            if re.match(r"^(Writing|Candidate responses)", t):
                continue
            if cur_part in letter_parts:
                # token 游走：字母（P6 可为 E-G、L P3 可为 A-H）按阅读序赋给期望题号；
                # 允许字母与题号粘连（OCR 常见 "7B"、"4A"）
                for tok in re.findall(r"\d{1,2}|(?<![A-Za-z])[A-H](?![A-Za-z])", t):
                    if tok.isdigit():
                        continue
                    out.setdefault(paper, {})[expected] = tok
                    expected += 1
            elif cur_part in word_ranges[paper]:
                lo, hi = word_ranges[paper][cur_part]
                pap_ans = out.setdefault(paper, {})
                # 该行所有可作答案起点的数字（范围内且未出现过；负向环视
                # 排除 "one /5 /" 这类粘连噪声），按出现位置切分段落
                starts = [
                    (m.start(), m.end(), int(m.group(1)))
                    for m in re.finditer(r"(?<![\d/])(\d{1,2})(?![\d/])", t)
                    if lo <= int(m.group(1)) <= hi
                    and int(m.group(1)) not in pap_ans
                ]
                if starts:
                    for i, (s, e, n) in enumerate(starts):
                        seg_end = starts[i + 1][0] if i + 1 < len(starts) else len(t)
                        pap_ans[n] = t[e:seg_end].strip()
                    last_q = starts[-1][2]
                elif last_q is not None:
                    if pap_ans[last_q] == "":
                        pap_ans[last_q] = t.strip()
                    else:
                        pap_ans[last_q] = (pap_ans[last_q] + " " + t).strip()
    return out


# --------------------------------------------------------------------------- #
# 整卷组装
# --------------------------------------------------------------------------- #


@dataclass
class Section:
    paper: str
    part: int
    title: str = ""
    instruction: str = ""
    passage: str = ""
    pages: tuple[int, int] = (0, 0)


@dataclass
class Question:
    paper: str
    part: int
    qnum: int
    type: str
    stem: str = ""
    stem2: str = ""
    keyword: str = ""
    options: dict[str, str] = field(default_factory=dict)
    answer: str = ""


_RUE_PARTS = {
    1: ("mcq4", (1, 8)),
    2: ("cloze", (9, 16)),
    3: ("wordFormation", (17, 24)),
    4: ("transform", (25, 30)),
    5: ("mcq4", (31, 36)),
    6: ("matchSentence", (37, 42)),
    7: ("matchPerson", (43, 52)),
}
_LIS_PARTS = {
    1: ("mcq3", (1, 8)),
    2: ("gapFill", (9, 18)),
    3: ("matchOpinion", (19, 23)),
    4: ("mcq3", (24, 30)),
}


def _split_instruction(rows: list[tuple[int, list[OcrLine]]]) -> tuple[str, str]:
    """把 part 页首分成指令区和正文区（以 "(0)" 或标题行为界）。"""
    instr: list[str] = []
    body: list[str] = []
    started = False
    for y, rls in rows:
        t = row_text((y, rls))
        if not started:
            if "(0)" in t or (
                2300 < y < 4200 and rls[0].x > 2000 and len(t) < 60
            ):
                started = True
            else:
                instr.append(t)
                continue
        body.append(t)
    return " ".join(instr), "\n".join(body)


def _iter_instruction(
    rows: list[tuple[int, list[OcrLine]]]
) -> list[tuple[int, str]]:
    """仅指令区行（题目说明文字，y < 2100），供 Writing 组装 instruction。"""
    return [
        (y, row_text((y, rls)))
        for y, rls in rows
        if y < 2100 and row_text((y, rls))
    ]


def build_test(
    pages: dict[int, list[OcrLine]], span: TestSpan
) -> tuple[list[Section], list[Question]]:
    sections: list[Section] = []
    questions: list[Question] = []

    p = span.papers
    rue_end = p.get("Writing", span.rue_start + 12) - 1
    wri_end = p.get("Listening", rue_end + 3) - 1
    lis_end = p.get("Speaking", wri_end + 7) - 1
    spk_end = lis_end + 2

    # ---- 答案 ----
    key_rue = parse_key_page(pages.get(span.key_start, []))
    key_lis = parse_key_page(pages.get(span.key_start + 1, []))
    answers = {
        "Reading and Use of English": key_rue.get("Reading and Use of English", {}),
        "Listening": key_lis.get("Listening", {}),
    }

    # ---- RUE ----
    rue_parts = part_pages(pages, span.rue_start, rue_end)
    for part, (qtype, (lo, hi)) in _RUE_PARTS.items():
        pnos = rue_parts.get(part, [])
        if not pnos:
            continue
        lines = pages_lines(pages, pnos)
        rows = merge_rows(lines)
        instr, body = _split_instruction(rows)
        sec = Section(
            paper="Reading and Use of English",
            part=part,
            instruction=instr,
            pages=(pnos[0], pnos[-1]),
        )
        qs: list[Question] = []
        if part == 1:
            # 首页文章 + 次页选项网格
            sec.passage = body if pnos[0] == pnos[-1] else _join_pages(pages, [pnos[0]])
            grid_lines = pages_lines(pages, pnos[1:]) if len(pnos) > 1 else lines
            opts = parse_options_grid(grid_lines, "ABCD")
            for n in range(lo, hi + 1):
                qs.append(
                    Question(
                        paper=sec.paper, part=part, qnum=n, type=qtype,
                        options=opts.get(n, {}),
                        answer=answers[sec.paper].get(n, ""),
                    )
                )
        elif part in (2, 3):
            passage, gaps = parse_gap_passage(lines, (lo, hi))
            cues = parse_cue_words(lines) if part == 3 else {}
            sec.passage = passage
            for n in range(lo, hi + 1):
                qs.append(
                    Question(
                        paper=sec.paper, part=part, qnum=n, type=qtype,
                        stem=gaps.get(n, ""),
                        keyword=cues.get(n, ""),
                        answer=answers[sec.paper].get(n, ""),
                    )
                )
        elif part == 4:
            trs = parse_transforms(lines, (lo, hi))
            for n in range(lo, hi + 1):
                tr = trs.get(n, {})
                qs.append(
                    Question(
                        paper=sec.paper, part=part, qnum=n, type=qtype,
                        stem=tr.get("stem", ""), stem2=tr.get("stem2", ""),
                        keyword=tr.get("keyword", ""),
                        answer=answers[sec.paper].get(n, ""),
                    )
                )
        elif part == 5:
            sec.passage = _join_pages(pages, pnos[:1])
            q_lines = pages_lines(pages, pnos[1:]) if len(pnos) > 1 else []
            items = parse_line_items(q_lines, (lo, hi), "ABCD") if q_lines else {}
            for n in range(lo, hi + 1):
                it = items.get(n, {})
                qs.append(
                    Question(
                        paper=sec.paper, part=part, qnum=n, type=qtype,
                        stem=it.get("stem", ""), options=it.get("options", {}),
                        answer=answers[sec.paper].get(n, ""),
                    )
                )
        elif part == 6:
            opts, rest = parse_letter_box(
                pages_lines(pages, pnos[1:]) if len(pnos) > 1 else lines
            )
            # P6 空号是裸数字（37、38…），非括号式，用裸数字配对
            gaps = parse_gap_fill_numbered(
                pages_lines(pages, pnos[:1]) + rest, (lo, hi)
            )
            passage, _ = parse_gap_passage(
                pages_lines(pages, pnos[:1]) + rest, (lo, hi)
            )
            sec.passage = passage
            for n in range(lo, hi + 1):
                qs.append(
                    Question(
                        paper=sec.paper, part=part, qnum=n, type=qtype,
                        stem=gaps.get(n, ""), options=opts,
                        answer=answers[sec.paper].get(n, ""),
                    )
                )
        elif part == 7:
            stmts = parse_matching_statements(pages_lines(pages, pnos[:1]), (lo, hi))
            people, rest = parse_people(pages_lines(pages, pnos[1:]))
            sec.passage = _rows_to_text(rest)
            for n in range(lo, hi + 1):
                qs.append(
                    Question(
                        paper=sec.paper, part=part, qnum=n, type=qtype,
                        stem=stmts.get(n, ""), options=people,
                        answer=answers[sec.paper].get(n, ""),
                    )
                )
        sections.append(sec)
        questions.extend(qs)

    # ---- Writing ----
    wri_parts = part_pages(pages, p["Writing"], wri_end)
    for part, pnos in sorted(wri_parts.items()):
        lines = pages_lines(pages, pnos)
        # Writing 没有题目指令/正文二分界（无 (0) 标记），整页文本作正文，
        # 题干从正文行 "^N ..." 起截取
        body = _rows_to_text(lines)
        instr = " ".join(
            t for _, t in _iter_instruction(merge_rows(lines)) if t
        )
        sec = Section(
            paper="Writing", part=part, instruction=instr, passage=body,
            pages=(pnos[0], pnos[-1]),
        )
        qs: list[Question] = []
        for n, qtype in ((1, "essay"), (2, "essayOption"), (3, "essayOption"),
                         (4, "essayOption"), (5, "essayOption")):
            if part == 1 and n != 1:
                continue
            if part == 2 and n == 1:
                continue
            qs.append(Question(paper="Writing", part=part, qnum=n, type=qtype,
                               stem=_extract_writing_stem(body, n)))
        sections.append(sec)
        questions.extend(qs)

    # ---- Listening ----
    lis_parts = part_pages(pages, p["Listening"], lis_end)
    for part, (qtype, (lo, hi)) in _LIS_PARTS.items():
        pnos = lis_parts.get(part, [])
        if not pnos:
            continue
        lines = pages_lines(pages, pnos)
        instr, body = _split_instruction(merge_rows(lines))
        sec = Section(
            paper="Listening", part=part, instruction=instr,
            passage=body if part not in (1, 4) else "",
            pages=(pnos[0], pnos[-1]),
        )
        qs: list[Question] = []
        if part in (1, 4):
            items = parse_line_items(lines, (lo, hi), "ABC")
            for n in range(lo, hi + 1):
                it = items.get(n, {})
                qs.append(
                    Question(
                        paper=sec.paper, part=part, qnum=n, type=qtype,
                        stem=it.get("stem", ""), options=it.get("options", {}),
                        answer=answers["Listening"].get(n, ""),
                    )
                )
        elif part == 2:
            gaps = parse_gap_fill_numbered(lines, (lo, hi))
            for n in range(lo, hi + 1):
                qs.append(
                    Question(
                        paper=sec.paper, part=part, qnum=n, type=qtype,
                        stem=gaps.get(n, ""),
                        answer=answers["Listening"].get(n, ""),
                    )
                )
        elif part == 3:
            # 选项 A-H 只取左栏（x<6300），右栏 Speaker 标签/题号不入选项文本
            left = [l for l in pages_lines(pages, pnos[:1]) if l.x < 6300]
            opts, _ = parse_letter_box(left, "ABCDEFGH")
            for n in range(lo, hi + 1):
                qs.append(
                    Question(
                        paper=sec.paper, part=part, qnum=n, type=qtype,
                        stem=f"Speaker {n - lo + 1}", options=opts,
                        answer=answers["Listening"].get(n, ""),
                    )
                )
        sections.append(sec)
        questions.extend(qs)

    # ---- Speaking ----
    spk_lines = pages_lines(pages, list(range(p["Speaking"], spk_end + 1)))
    rows_text = _rows_to_text(spk_lines)
    sections.append(
        Section(paper="Speaking", part=0, passage=rows_text,
                pages=(p["Speaking"], spk_end))
    )

    return sections, questions


def _join_pages(pages: dict[int, list[OcrLine]], pnos: list[int]) -> str:
    return _rows_to_text(pages_lines(pages, pnos))


def _rows_to_text(lines: list[OcrLine]) -> str:
    return "\n".join(row_text(r) for r in merge_rows(lines))


def _extract_writing_stem(body: str, n: int) -> str:
    """从 Writing 正文里抽 "N ..." 起的题干（到下一题号或段末）。"""
    m = re.search(rf"(?m)^{n}\s+(.*)$", body)
    if not m:
        return ""
    tail = body[m.start():]
    nxt = re.search(rf"(?m)^{n+1}\s", tail[1:])
    if nxt:
        tail = tail[: nxt.start() + 1]
    return tail.strip()


# --------------------------------------------------------------------------- #
# 入库
# --------------------------------------------------------------------------- #

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS fce_test (
    id          INTEGER PRIMARY KEY,
    title       TEXT UNIQUE NOT NULL,
    source_file TEXT,
    page_start  INTEGER,
    page_end    INTEGER,
    ingested_at TEXT
);

CREATE TABLE IF NOT EXISTS fce_section (
    id          INTEGER PRIMARY KEY,
    test_id     INTEGER NOT NULL REFERENCES fce_test(id) ON DELETE CASCADE,
    paper       TEXT NOT NULL,
    part        INTEGER NOT NULL,
    instruction TEXT DEFAULT '',
    passage     TEXT DEFAULT '',
    page_start  INTEGER DEFAULT 0,
    page_end    INTEGER DEFAULT 0,
    ord         INTEGER DEFAULT 0,
    UNIQUE(test_id, paper, part)
);

CREATE TABLE IF NOT EXISTS fce_question (
    id          INTEGER PRIMARY KEY,
    section_id  INTEGER NOT NULL REFERENCES fce_section(id) ON DELETE CASCADE,
    test_id     INTEGER NOT NULL,
    paper       TEXT NOT NULL,
    part        INTEGER NOT NULL,
    qnum        INTEGER NOT NULL,
    type        TEXT NOT NULL,
    stem        TEXT DEFAULT '',
    stem2       TEXT DEFAULT '',
    keyword     TEXT DEFAULT '',
    options_json TEXT DEFAULT '{}',
    answer      TEXT DEFAULT '',
    UNIQUE(test_id, paper, part, qnum)
);
CREATE INDEX IF NOT EXISTS idx_fceq_test ON fce_question(test_id, paper, part, qnum);
"""


def ingest_fce(
    db_path: str,
    pages: dict[int, list[OcrLine]],
    source_file: str = "",
) -> dict[int, dict]:
    """整卷入库，返回 {test_id: {sections: n, questions: n}}。"""
    spans = detect_test_spans(pages)
    if not spans:
        raise ValueError("未探测到任何 Test 起始页，请检查 OCR 数据")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    summary: dict[int, dict] = {}
    for span in spans:
        conn.execute("DELETE FROM fce_test WHERE id = ?", (span.test_id,))
        sections, questions = build_test(pages, span)
        conn.execute(
            "INSERT INTO fce_test (id, title, source_file, page_start, page_end, ingested_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (span.test_id, f"Test {span.test_id}", source_file,
             span.rue_start, span.key_start + 1, now),
        )
        sec_ids: dict[tuple[str, int], int] = {}
        for i, sec in enumerate(sections):
            cur = conn.execute(
                "INSERT INTO fce_section (test_id, paper, part, instruction, passage,"
                " page_start, page_end, ord) VALUES (?,?,?,?,?,?,?,?)",
                (span.test_id, sec.paper, sec.part, sec.instruction, sec.passage,
                 sec.pages[0], sec.pages[1], i),
            )
            sec_ids[(sec.paper, sec.part)] = cur.lastrowid
        for q in questions:
            conn.execute(
                "INSERT INTO fce_question (section_id, test_id, paper, part, qnum, type,"
                " stem, stem2, keyword, options_json, answer) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (sec_ids[(q.paper, q.part)], span.test_id, q.paper, q.part, q.qnum,
                 q.type, q.stem, q.stem2, q.keyword,
                 json.dumps(q.options, ensure_ascii=False), q.answer),
            )
        summary[span.test_id] = {"sections": len(sections), "questions": len(questions)}
    conn.commit()
    conn.close()
    return summary


# --------------------------------------------------------------------------- #
# 校验报告
# --------------------------------------------------------------------------- #

_EXPECT = {
    ("Reading and Use of English", 1): 8,
    ("Reading and Use of English", 2): 8,
    ("Reading and Use of English", 3): 8,
    ("Reading and Use of English", 4): 6,
    ("Reading and Use of English", 5): 6,
    ("Reading and Use of English", 6): 6,
    ("Reading and Use of English", 7): 10,
    ("Listening", 1): 8,
    ("Listening", 2): 10,
    ("Listening", 3): 5,
    ("Listening", 4): 7,
}


def validate(db_path: str) -> list[str]:
    """入库后校验：题数、答案覆盖率、选择题选项完整性。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    issues: list[str] = []
    for tid in range(1, 5):
        for (paper, part), want in _EXPECT.items():
            rows = conn.execute(
                "SELECT qnum, options_json, answer, type FROM fce_question"
                " WHERE test_id=? AND paper=? AND part=? ORDER BY qnum",
                (tid, paper, part),
            ).fetchall()
            if len(rows) != want:
                issues.append(
                    f"T{tid} {paper} P{part}: 题数 {len(rows)} != {want}"
                )
            for r in rows:
                if not r["answer"]:
                    issues.append(
                        f"T{tid} {paper} P{part} Q{r['qnum']}: 答案缺失"
                    )
                if r["type"].startswith("mcq"):
                    opts = json.loads(r["options_json"])
                    missing = [k for k, v in opts.items() if not v]
                    if len(opts) < 3 or missing:
                        issues.append(
                            f"T{tid} {paper} P{part} Q{r['qnum']}: 选项不全 {opts}"
                        )
    conn.close()
    return issues


if __name__ == "__main__":
    import sys
    import argparse

    ap = argparse.ArgumentParser(description="FCE 青少版 PDF → fce.db")
    ap.add_argument("--ocr-dir", help="已 OCR 的目录（pNNN.txt）；缺省则现场 OCR")
    ap.add_argument("--pdf", help="源 PDF 路径（现场 OCR 时必填）")
    ap.add_argument("--db", default="data/fce.db")
    args = ap.parse_args()

    if args.ocr_dir:
        pg = load_ocr_dir(args.ocr_dir)
    elif args.pdf:
        with tempfile.TemporaryDirectory() as td:
            pg = render_and_ocr(args.pdf, td)
    else:
        ap.error("需要 --ocr-dir 或 --pdf")

    s = ingest_fce(args.db, pg, source_file=args.pdf or args.ocr_dir)
    for tid, info in sorted(s.items()):
        print(f"Test {tid}: {info['sections']} sections, {info['questions']} questions")
    probs = validate(args.db)
    if probs:
        print(f"\n{len(probs)} 个校验问题：")
        for p in probs:
            print(" -", p)
    else:
        print("\n校验全部通过")

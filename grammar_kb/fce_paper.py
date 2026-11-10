"""FCE 青少版（For Schools）OCR 基元 + 答案页解析 + fce.db 表结构。

来源 PDF 为纯扫描图（无文字层），OCR 由 macOS Vision（Swift）完成：
渲染 200dpi PNG → ``ocr.swift`` 输出 ``y<TAB>x<TAB>text`` 三列行坐标文本。

2026-11 精简：整卷入库流水线（build_test / ingest_fce / validate）已退役——
fce.db 是一次性入库后的只读库，运行期只剩 reading_build（OCR 基元）与
fce_query（读取拼装）两条链路，解析纯函数仅保留仍被引用的部分。
完整旧版见 tag ``archive/pre-slim-20261110``。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------- #
# OCR：清洗
# --------------------------------------------------------------------------- #

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
# fce.db 表结构（入库流水线已退役，SCHEMA 供测试/工具按同一结构建库）
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

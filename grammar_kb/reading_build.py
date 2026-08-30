"""FCE 阅读原文重建管线：OCR 坐标 → 拆栏重组 → 填入正确答案 → 完整阅读文章。

背景：fce_paper.py 的 ``merge_rows`` 按视觉行合并后依 x 排序，多栏版式
（RUE P5/P6 双栏、P6 缩进导语、P7 四人网格）的文本会逐行交错。本模块
利用 OCR 缓存的 ``y x text`` 坐标按「栏 × 行」重组，再把各 Part 的空格
填上 fce.db 中的正确答案，得到可直接阅读的完整文章。

版式规则（4 套 Test 一致）：
- P1/P2/P3 单栏（P3 丢弃右缘大写提示词）
- P5 双栏；P6 双栏或三栏（T3 有缩进窄栏导语块，整体位于左栏上方 → 前置）
- P6 挖空行内 span 分裂：空号 37-42 贴「估计行尾在其左侧最近」的前文 span
- P7 次页 2×2 四人网格：A 左上 / C 右上 / B 左下 / D 右下（字母锚点可缺，
  用 B/D 锚点 y 或栏内最大行距分上下）

入库：每篇文章按 ~300 词切成分段，写 reading_article（kind=base），
base_key 形如 T1P5 / T1P7-A。

用法：
    python -m grammar_kb.reading_build --review /tmp/reading_review.md
    python -m grammar_kb.reading_build            # 重建 base 文章入库
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .fce_paper import OcrLine, load_ocr_dir, merge_rows

# --------------------------------------------------------------------------- #
# 参数
# --------------------------------------------------------------------------- #

_CHAR_W = 88          # 每字符宽度估值（栏宽 ~4300 / ~48 字符）
_COL_GAP = 600        # 列基聚类间距（栏基间隔 >4000，缩进导语 ~650，行内抖动 <350）
_MIN_COL_ROWS = 8     # 成列最少 span 数（标题/行中游移 span 不成列）
_LEAD_PAD = 150       # 上下象限/导语边界缓冲

# 示例 (0) 答案（OCR 例题区无法可靠还原，按原文语境人工判定）
EXAMPLE_ANSWERS: dict[tuple[int, int], str] = {
    (1, 1): "tricks", (1, 2): "is", (1, 3): "suggestion",
    (2, 1): "managed", (2, 2): "more", (2, 3): "obviously",
    (3, 1): "trying", (3, 2): "one", (3, 3): "society",
    (4, 1): "getting", (4, 2): "what", (4, 3): "villagers",
}

# T3P6 选项 D 在源 PDF 扫描件中物理缺失（选项页底部空白），41 空按上下文
# （俄国冰雪滑梯盛况 → 士兵带回故事）补推断句并标注
P6_D_FALLBACK = {
    3: "The slides were enormously popular with people of all ages, from ordinary villagers to members of the royal family",  # noqa: E501
}

# OCR 常见错字/丢空格修补（组装后全局替换）
OCR_FIXES: list[tuple[str, str]] = [
    ("T'll", "I'll"), ("l'd", "I'd"), ("l've", "I've"), ("1 was", "I was"),
    ("1 felt", "I felt"), ("1 knew", "I knew"), ("Ill ", "I'll "),
    ("Iknew", "I knew"), ("Iloved", "I loved"), ("Ithink", "I think"),
    ("Ihad", "I had"), ("ourtime", "our time"), ("in'hands'", "in 'hands'"),
    ("sately", "safely"), ("backgound", "background"),
    ("steeledged", "steel-edged"), ("Koscuiszko", "Kosciuszko"),
    ("..'", "'"), ("...", ""),
]

_SKIP_TEXT = re.compile(r"^(Part \d|Reading and Use of|Test \d|WRITING|LISTENING)")
_INSTR_HEAD = re.compile(
    r"^(For questions|Mark your|Write your|Example|You are going|You will|"
    r"Complete|Use only|There is|Turn over|Questions|Which )"
)


# --------------------------------------------------------------------------- #
# 行 / 列装配
# --------------------------------------------------------------------------- #


@dataclass
class Row:
    y: int
    spans: list[OcrLine]

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(s.text for s in self.spans)).strip()


def _rows(lines: list[OcrLine], tol: int = 45) -> list[Row]:
    return [Row(y, sorted(rls, key=lambda l: l.x)) for y, rls in merge_rows(lines, tol=tol)]


def _est_end(s: OcrLine) -> int:
    return s.x + len(s.text.strip()) * _CHAR_W


def _is_cue(s: OcrLine) -> bool:
    t = s.text.strip()
    return len(t) <= 12 and bool(re.fullmatch(r"[A-Z][A-Z'/-]+", t))


def _is_bare_num(s: OcrLine) -> bool:
    return bool(re.fullmatch(r"\d{1,2}", s.text.strip()))


def drop_margin_junk(spans: list[OcrLine]) -> list[OcrLine]:
    """页边距噪声：左缘裸数字（P5/P6 行号）、右缘短残片（'lin' 等，含字母才删，
    裸空号跨全宽保留——T3P6 的 40 就在右缘 x≈8900）。"""
    out = []
    for s in spans:
        t = s.text.strip()
        if s.x < 600 and _is_bare_num(s):
            continue
        if s.x > 8300 and len(t) < 20 and not _is_bare_num(s):
            continue
        out.append(s)
    return out


def detect_columns(rows: list[Row], min_n: int = _MIN_COL_ROWS) -> list[float]:
    """全部正文 span（非行首亦可）的 x 聚类出列基。

    排除：过短残片 / 纯大写（P3 提示词、书眉）/ 裸数字（空号、行号）。
    簇内 span 数 ≥8 才成列（标题/行中游移 span 不成列）。
    """
    xs: list[int] = []
    for r in rows:
        for s in r.spans:
            t = s.text.strip()
            if len(t) < 6 or t.isupper() or _is_bare_num(s) or _SKIP_TEXT.match(t):
                continue
            xs.append(s.x)
    if not xs:
        return []
    xs.sort()
    clusters: list[list[int]] = []
    for x in xs:
        if clusters and x - clusters[-1][-1] < _COL_GAP:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    bases = [sum(c) / len(c) for c in clusters if len(c) >= min_n]
    return sorted(bases)


def _column_of(x: float, bases: list[float]) -> int:
    if not bases:
        return 0
    for i in range(len(bases) - 1):
        if x < (bases[i] + bases[i + 1]) / 2:
            return i
    return len(bases) - 1


def _split_tail_num(t: str) -> tuple[str, Optional[str]]:
    """span 文本尾部粘连的空号（"charge. 40" → ("charge.", "40")）。"""
    m = re.search(r"\s(3[7-9]|4[0-2])$", t)
    if m:
        return t[: m.start()].strip(), m.group(1)
    return t, None


def _split_head_num(t: str) -> tuple[Optional[str], str]:
    """span 文本头部粘连的空号（"40 The boys…" → ("40", "The boys…")）。"""
    m = re.match(r"^(3[7-9]|4[0-2])\s+", t)
    if m:
        return m.group(1), t[m.end():].strip()
    return None, t


@dataclass
class ColBlock:
    base: float
    col: int
    entries: list[tuple[int, int, str]] = field(default_factory=list)  # (y, x0, text)
    is_lead: bool = False

    @property
    def y_min(self) -> int:
        return self.entries[0][0] if self.entries else 0

    @property
    def y_max(self) -> int:
        return self.entries[-1][0] if self.entries else 0


def column_blocks(
    rows: list[Row], bases: list[float], drop_cues: bool = False,
    margin_junk: bool = False,
) -> list[ColBlock]:
    """rows → 列块。行内 span 先贴列（x 中点归属），裸空号贴前文 span 的列。"""
    per_base: dict[float, ColBlock] = {}
    for r in rows:
        spans = list(r.spans)
        if margin_junk:
            spans = drop_margin_junk(spans)
        if drop_cues:
            spans = [s for s in spans if not _is_cue(s)]
        if not spans:
            continue
        prev: Optional[OcrLine] = None
        col_parts: dict[int, list[tuple[int, OcrLine]]] = {}
        for s in sorted(spans, key=lambda l: l.x):
            at_base = any(abs(s.x - b) < 600 for b in bases) if bases else False
            if _is_bare_num(s) and prev is not None and _est_end(prev) <= s.x + 300:
                col = _column_of(prev.x, bases)  # 空号贴前文
            elif not at_base and prev is not None and bases:
                # 行内跨中点的延续 span（不在任何列基附近）：贴同行前文所在列
                col = _column_of(prev.x, bases)
            else:
                col = _column_of(s.x, bases)
            col_parts.setdefault(col, []).append((s.x, s))
            if not _is_bare_num(s):
                prev = s
        for col, parts in col_parts.items():
            b = bases[col] if bases else 0.0
            blk = per_base.setdefault(b, ColBlock(b, col))
            t = re.sub(r"\s+", " ", " ".join(sp.text for _, sp in parts)).strip()
            # 尾/头粘连的空号拆出独立 token（fill_p6 按独立数字回填）
            body, tail_n = _split_tail_num(t)
            head_n, body = _split_head_num(body)
            t = ((head_n + " ") if head_n else "") + body + ((" " + tail_n) if tail_n else "")
            blk.entries.append((r.y, parts[0][0], t.strip()))
    blocks = list(per_base.values())
    for b in blocks:
        b.entries.sort(key=lambda e: e[0])
    # 导语块：整体位于某个更左列块的起始之上 → 前置（T3P6 窄栏导语、T2P6 缩进导语）
    for a in blocks:
        for b in blocks:
            if b.base < a.base and a.y_max < b.y_min - _LEAD_PAD:
                a.is_lead = True
                break
    leads = [b for b in blocks if b.is_lead]
    mains = [b for b in blocks if not b.is_lead]
    return sorted(leads, key=lambda b: b.y_min) + sorted(mains, key=lambda b: b.base)


def find_title(rows: list[Row]) -> Optional[tuple[int, str]]:
    """标题：首个长行（len≥45、y>2200）之前、最后一个「居中短行」候选。

    候选：y≥900（避开书眉）、x0>1500、len<60、含小写字母、非指令/Part 头。
    首个长行限 y>2200：指令区在 2200 之上，避免 "fits each gap (37-42)."
    这类指令续行抢跑（书眉恰好 45 字符也要跳过）。
    """
    cands: list[tuple[int, str]] = []
    first_long_y: Optional[int] = None
    for r in rows:
        t = r.text
        if first_long_y is None:
            if (
                len(t) >= 45
                and r.y > 2200
                and not _INSTR_HEAD.match(t)
                and not _SKIP_TEXT.match(t)
            ):
                first_long_y = r.y
            elif (
                r.y >= 900
                and r.spans[0].x > 1500
                and len(t) < 60
                and re.search(r"[a-z]", t)
                and not _INSTR_HEAD.match(t)
                and not _SKIP_TEXT.match(t)
                and not re.match(r"^\d", t)
            ):
                cands.append((r.y, t))
    # 只保留首个长行之前的候选，取最后一个（最靠近正文）
    usable = [c for c in cands if first_long_y is None or c[0] < first_long_y]
    return usable[-1] if usable else None


def reflow(entries: list[tuple[int, int, str]]) -> list[str]:
    """折行 → 段落：行距突变（>1.7×中位）或行首缩进突变（>500px）分界。"""
    if not entries:
        return []
    gaps = [b[0] - a[0] for a, b in zip(entries, entries[1:]) if b[0] > a[0]]
    med = sorted(gaps)[len(gaps) // 2] if gaps else 150
    paras: list[list[str]] = [[entries[0][2]]]
    for (y, x0, t), (py, px, _) in zip(entries[1:], entries[:-1]):
        if y - py > 1.7 * med + 40 or abs(x0 - px) > 500:
            paras.append([t])
        else:
            paras[-1].append(t)
    out = []
    for p in paras:
        t = " ".join(p)
        t = re.sub(r"(\w)- (\w)", r"\1\2", t)  # 行尾连字符
        out.append(t.strip())
    return [p for p in out if p]


def _clean(t: str) -> str:
    t = re.sub(r"\s+", " ", t)
    for a, b in OCR_FIXES:
        t = t.replace(a, b)
    t = re.sub(r"\s+([,.;:!?'])", r"\1", t)
    t = re.sub(r"([\"'])\s+", r"\1", t)
    t = re.sub(r"[.·•…_]{3,}", " ", t)
    return re.sub(r"\s{2,}", " ", t).strip()


# --------------------------------------------------------------------------- #
# 各 Part 组装
# --------------------------------------------------------------------------- #


def _page_rows(pages: dict[int, list[OcrLine]], pno: int) -> list[Row]:
    return _rows(pages.get(pno, []))


def _assemble_text(rows: list[Row], bases: list[float], drop_cues=False,
                   margin_junk=False) -> list[str]:
    """每栏独立 reflow（跨栏 y 回跳会把行距中位数拉乱），段序 = 栏序。"""
    blocks = column_blocks(rows, bases, drop_cues=drop_cues, margin_junk=margin_junk)
    out: list[str] = []
    for b in blocks:
        out.extend(_clean(p) for p in reflow(b.entries) if _clean(p))
    return out


def build_p123(pages: dict[int, list[OcrLine]], pno: int, part: int) -> tuple[str, list[str]]:
    rows = _page_rows(pages, pno)
    title = find_title(rows)
    if title is None:
        return "", []
    body = [r for r in rows if r.y > title[0] + 40 and r.text != title[1] and not _SKIP_TEXT.match(r.text)]
    return title[1], _assemble_text(body, [], drop_cues=(part == 3))


def build_p5(pages: dict[int, list[OcrLine]], pno: int) -> tuple[str, list[str]]:
    rows = _page_rows(pages, pno)
    bases = detect_columns(rows)
    title = find_title(rows)
    body = [r for r in rows if (title is None or r.y > title[0] + 40) and r.text != (title or ("", ""))[1] and not _SKIP_TEXT.match(r.text)]
    return (title[1] if title else ""), _assemble_text(body, bases, margin_junk=True)


def build_p6(
    pages: dict[int, list[OcrLine]], passage_pno: int, opts_pno: int
) -> tuple[str, list[str], dict[str, str]]:
    rows = _page_rows(pages, passage_pno)
    bases = detect_columns(rows)
    title = find_title(rows)
    body = [r for r in rows if (title is None or r.y > title[0] + 40) and r.text != (title or ("", ""))[1] and not _SKIP_TEXT.match(r.text)]
    paras = _assemble_text(body, bases, margin_junk=True)

    orows = _page_rows(pages, opts_pno)
    obases = detect_columns(orows, min_n=4)
    oblocks = column_blocks(orows, obases)
    opts: dict[str, str] = {}
    cur: Optional[str] = None
    for b in oblocks:
        for _, _, t in b.entries:
            m = re.match(r"^([A-G])[ .](.*)$", t)
            bare = re.fullmatch(r"[A-G]", t)
            if m:
                cur = m.group(1)
                opts[cur] = m.group(2).strip()
            elif bare:
                cur = bare.group(1)
                opts.setdefault(cur, "")
            elif cur:
                opts[cur] = (opts[cur] + " " + t).strip()
    return (title[1] if title else ""), paras, {k: _clean(v) for k, v in opts.items() if v}


def build_p7(
    pages: dict[int, list[OcrLine]], pno: int
) -> tuple[str, list[tuple[str, str, list[str]]]]:
    """四人网格页 → (页标题, [(letter, name, paragraphs)])。

    行 spans 先按列分流（名字行两侧同 y，不能整行归一列），
    再按上下象限切四人；字母锚点 A-D 仅用于定上下分界。
    """
    rows = _page_rows(pages, pno)
    all_bases = detect_columns(rows)
    # 四人页固定两栏：聚类超过 2 个时按 3500 中线并成左右两簇
    if len(all_bases) > 2:
        left = [b for b in all_bases if b < 3500] or [all_bases[0]]
        right = [b for b in all_bases if b >= 3500] or [all_bases[-1]]
        bases = [sum(left) / len(left), sum(right) / len(right)]
    else:
        bases = list(all_bases)
    if len(bases) < 2:
        bases = [bases[0] if bases else 400.0, 5200.0]
    title = find_title(rows)
    body = [r for r in rows if (title is None or r.y > title[0] + 40) and not _SKIP_TEXT.match(r.text)]
    # 字母锚点（A-D 裸 span）定下边界
    anchors = [
        (s.text.strip(), r.y)
        for r in body
        for s in r.spans
        if re.fullmatch(r"[A-D]", s.text.strip())
    ]
    bottom_y: Optional[int] = None
    bot = [y for lt, y in anchors if lt in "BD"]
    has_d_anchor = any(lt == "D" for lt, _ in anchors)
    if bot:
        bottom_y = min(bot)
    else:
        # 只剩 A/C 锚点（B/D 被 OCR 漏掉）：用 A 锚点 y + 行距估值（~3000px）
        top_only = [y for lt, y in anchors if lt in "AC"]
        if top_only:
            bottom_y = min(top_only) + 3000
        else:
            ys = sorted(r.y for r in body if _column_of(r.spans[0].x, bases) == 0)
            gaps = [(b - a, a) for a, b in zip(ys, ys[1:])]
            if gaps:
                g, at = max(gaps)
                if g > 1200:
                    bottom_y = at + g // 2
    # 行 spans 按列分流（去字母锚点）
    col_entries: dict[int, list[tuple[int, int, str]]] = {0: [], 1: []}
    for r in body:
        spans = [
            s for s in sorted(r.spans, key=lambda l: l.x)
            if not re.fullmatch(r"[A-D]", s.text.strip())
        ]
        if not spans:
            continue
        parts: dict[int, list[OcrLine]] = {}
        for s in spans:
            parts.setdefault(_column_of(s.x, bases), []).append(s)
        for col, ps in parts.items():
            t = re.sub(r"\s+", " ", " ".join(sp.text for sp in ps)).strip()
            if t:
                col_entries.setdefault(col, []).append((r.y, ps[0].x, t))
    people: list[tuple[str, str, list[str]]] = []
    quads = [("A", 0, True), ("C", 1, True), ("B", 0, False), ("D", 1, False)]
    for letter, col_idx, top in quads:
        ents_all = sorted(col_entries.get(col_idx, []))
        if not ents_all:
            continue
        ents = [
            e for e in ents_all
            if bottom_y is None or top == (e[0] < bottom_y)
        ]
        if not ents:
            continue
        # 下半象限：D 缺锚点时右栏边界右偏。两种形态都归 D：
        # 1) C 末行是靠近 bottom 的短行（下一人名字，T2P7）；
        # 2) C 内部行距断档（>2×中位）后跟短行名字 + 正文（T3P7：
        #    C 止于 4291，Arjun 名字在 4709，B 锚点却在 5145）
        if (not has_d_anchor) and top and letter == "C" and len(ents) >= 3 and bottom_y is not None:
            gaps = [b[0] - a[0] for a, b in zip(ents, ents[1:])]
            med = sorted(gaps)[len(gaps) // 2]
            cut = None
            for i, g in enumerate(gaps):
                nxt = ents[i + 1]
                if g > 2.3 * med and len(nxt[2]) < 40:
                    cut = i + 1
                    break
            if cut is None and ents[-1][0] >= bottom_y - 900 and len(ents[-1][2]) < 40:
                cut = len(ents) - 1
            if cut is not None:
                ents = ents[:cut]
        if (not has_d_anchor) and not top and letter == "D" and bottom_y is not None:
            # 回收被 C 边界误差吞掉的 D 行：右栏中断档点之后的所有行
            top_c = sorted(e for e in ents_all if e[0] < bottom_y)
            if len(top_c) >= 3:
                gaps = [b[0] - a[0] for a, b in zip(top_c, top_c[1:])]
                med = sorted(gaps)[len(gaps) // 2]
                for i, g in enumerate(gaps):
                    nxt = top_c[i + 1]
                    if g > 2.3 * med and len(nxt[2]) < 40:
                        ents = [e for e in ents_all if e[0] >= nxt[0]]
                        break
            ents.sort()
        # 名字：象限开头、不结束于句号的短行
        name_parts: list[str] = []
        rest: list[tuple[int, int, str]] = []
        name_zone = ents[0][0] + 900 if ents else 0
        for i, e in enumerate(ents):
            if (
                len(name_parts) == i
                and e[0] <= name_zone
                and len(e[2]) < 40
                and not e[2].endswith((".", "!", "?"))
            ):
                name_parts.append(e[2])
            else:
                rest.append(e)
        paras = [_clean(p) for p in reflow(rest) if _clean(p)]
        people.append((letter, " ".join(name_parts).strip(), paras))
    return (title[1] if title else ""), people


# --------------------------------------------------------------------------- #
# 填空
# --------------------------------------------------------------------------- #

_GAP_RE = re.compile(r"\((\d{1,2})\)\s*(?:[.·•…_]*\s*)*")


def first_alt(ans: str) -> str:
    """多选答案（"had / held"）取首个备选。"""
    return re.split(r"/|\bOR\b", ans)[0].strip()


def fill_numbered(paras: list[str], answers: dict[int, str]) -> tuple[list[str], int]:
    out, n = [], 0

    def sub(m):
        nonlocal n
        q = int(m.group(1))
        if q in answers:
            n += 1
            return f" **{answers[q]}** "
        return " "

    for p in paras:
        out.append(_post(re.sub(_GAP_RE, sub, p)))
    return out, n


def fill_p6(
    paras: list[str], opts: dict[str, str], answers: dict[int, str], test_id: int = 0
) -> tuple[list[str], int]:
    out, n = [], 0

    def sub(m):
        nonlocal n
        q = int(m.group(1))
        a = answers.get(q)
        if a:
            sent = opts.get(a.strip()[:1].upper(), "")
            if not sent and test_id in P6_D_FALLBACK and a.strip()[:1].upper() == "D":
                # 扫描件缺失的 D 选项（T3）：上下文推断句补位
                sent = P6_D_FALLBACK[test_id] + " [推断补位：源扫描件此句缺失]"
            if sent:
                n += 1
                return f" **{sent}** "
        return " "

    for p in paras:
        out.append(_post(re.sub(r"(?<![\d])(3[7-9]|4[0-2])(?![\d])", sub, p)))
    return out, n


def _post(t: str) -> str:
    t = re.sub(r"\s+([,.;:!?'])", r"\1", t)
    return re.sub(r"\s{2,}", " ", t).strip()


# --------------------------------------------------------------------------- #
# 组装 + 入库
# --------------------------------------------------------------------------- #

READING_SCHEMA = """
CREATE TABLE IF NOT EXISTS reading_article (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL DEFAULT 'base',   -- base=原文段落 / derived=派生文章
    base_key    TEXT NOT NULL,                  -- T1P5 / T1P7-A：派生文章挂到对应原文段
    title       TEXT DEFAULT '',
    text        TEXT NOT NULL,
    words       INTEGER DEFAULT 0,
    source      TEXT DEFAULT '',
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_ra_base ON reading_article(base_key, kind);
CREATE TABLE IF NOT EXISTS reading_recordings (
    id          INTEGER PRIMARY KEY,
    user        TEXT NOT NULL,
    article_id  INTEGER NOT NULL REFERENCES reading_article(id) ON DELETE CASCADE,
    audio_b64   TEXT NOT NULL,
    mime        TEXT DEFAULT 'audio/webm',
    duration_sec INTEGER DEFAULT 0,
    selected_text TEXT DEFAULT '',
    status      TEXT DEFAULT 'pending',
    teacher_score INTEGER,
    teacher_comment TEXT DEFAULT '',
    created_at  TEXT,
    graded_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_rr_user ON reading_recordings(user, created_at);
"""


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9''-]+", text))


def chunk_paragraphs(paras: list[str], hi: int = 300) -> list[str]:
    """相邻段落合并到 ≤hi 词；超长段按句切。"""
    chunks: list[str] = []
    for p in paras:
        if chunks and word_count(chunks[-1]) + word_count(p) <= hi:
            chunks[-1] = chunks[-1] + "\n\n" + p
        else:
            chunks.append(p)
    final: list[str] = []
    for c in chunks:
        if word_count(c) <= hi:
            final.append(c)
            continue
        sents = re.split(r"(?<=[.!?]) ", c)
        cur = ""
        for s in sents:
            if cur and word_count(cur + " " + s) > hi:
                final.append(cur)
                cur = s
            else:
                cur = (cur + " " + s).strip()
        if cur:
            final.append(cur)
    return final


def build_all(db_path: str, ocr_dir: str) -> list[dict]:
    pages = load_ocr_dir(ocr_dir)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    secs = conn.execute(
        "SELECT * FROM fce_section"
        " WHERE paper = 'Reading and Use of English' AND part IN (1,2,3,5,6,7)"
        " ORDER BY test_id, part"
    ).fetchall()
    out: list[dict] = []
    for sec in secs:
        tid, part = sec["test_id"], sec["part"]
        pnos = list(range(sec["page_start"], sec["page_end"] + 1))
        qs = conn.execute(
            "SELECT qnum, answer, options_json FROM fce_question"
            " WHERE test_id=? AND paper='Reading and Use of English' AND part=? ORDER BY qnum",
            (tid, part),
        ).fetchall()
        answers = {q["qnum"]: q["answer"] for q in qs}
        opts = {q["qnum"]: json.loads(q["options_json"] or "{}") for q in qs}
        rec: dict = {"test_id": tid, "part": part, "fills": 0, "expected": 0, "segments": []}
        if part in (1, 2, 3):
            title, paras = build_p123(pages, pnos[0], part)
            ansmap = dict(answers)
            ex = EXAMPLE_ANSWERS.get((tid, part), "")
            if ex:
                ansmap[0] = ex
            if part == 1:
                ansmap = {
                    q: (opts.get(q, {}).get(first_alt(a).upper(), first_alt(a)) if q else ex)
                    for q, a in ansmap.items()
                }
            else:
                ansmap = {q: first_alt(a) for q, a in ansmap.items()}
            filled, n = fill_numbered(paras, ansmap)
            rec.update(title=title, fills=n, expected=len(ansmap), segments=filled)
        elif part == 5:
            title, paras = build_p5(pages, pnos[0])
            rec.update(title=title, segments=paras)
        elif part == 6:
            title, paras, sentences = build_p6(pages, pnos[0], pnos[-1])
            filled, n = fill_p6(paras, sentences, answers, test_id=tid)
            rec.update(title=title, fills=n, expected=len(answers), segments=filled)
        elif part == 7:
            title, people = build_p7(pages, pnos[-1])
            rec["title"] = title
            rec["people"] = [
                {"letter": lt, "name": nm, "segments": ps} for lt, nm, ps in people
            ]
        out.append(rec)
    conn.close()
    return out


def ingest(db_path: str, recs: list[dict]) -> dict:
    conn = sqlite3.connect(db_path)
    conn.executescript(READING_SCHEMA)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute("DELETE FROM reading_article WHERE kind = 'base'")
    n = 0
    for r in recs:
        if r["part"] == 7:
            for p in r.get("people", []):
                if not p["segments"]:
                    continue
                key = f"T{r['test_id']}P7-{p['letter']}"
                title = f"{r['title']} · {p['name']}" if r["title"] else p["name"]
                for seg in chunk_paragraphs(p["segments"]):
                    conn.execute(
                        "INSERT INTO reading_article (kind, base_key, title, text, words, created_at)"
                        " VALUES ('base', ?, ?, ?, ?, ?)",
                        (key, title, seg, word_count(seg), now),
                    )
                    n += 1
        else:
            if not r["segments"]:
                continue
            key = f"T{r['test_id']}P{r['part']}"
            segs = chunk_paragraphs(r["segments"])
            for i, seg in enumerate(segs):
                title = r["title"] + (f"（{i + 1}）" if len(segs) > 1 else "")
                conn.execute(
                    "INSERT INTO reading_article (kind, base_key, title, text, words, created_at)"
                    " VALUES ('base', ?, ?, ?, ?, ?)",
                    (key, title, seg, word_count(seg), now),
                )
                n += 1
    conn.commit()
    conn.close()
    return {"articles": n}


def main() -> None:
    ap = argparse.ArgumentParser(description="FCE 阅读原文重建")
    ap.add_argument("--ocr-dir", default="data/ocr_cache")
    ap.add_argument("--db", default="data/fce.db")
    ap.add_argument("--review", help="只输出校对稿（markdown），不写库")
    args = ap.parse_args()

    recs = build_all(args.db, args.ocr_dir)
    if args.review:
        lines = ["# FCE 阅读原文重建校对稿\n"]
        for r in recs:
            head = f"Test {r['test_id']} · Part {r['part']}"
            if r.get("expected"):
                head += f" · 填空 {r['fills']}/{r['expected']}"
            lines.append(f"\n## {head} · {r.get('title', '')}\n")
            if r["part"] == 7:
                for p in r.get("people", []):
                    lines.append(f"### [{p['letter']}] {p['name']}\n")
                    lines.extend(chunk_paragraphs(p["segments"]))
            else:
                lines.extend(chunk_paragraphs(r["segments"]))
        Path(args.review).write_text("\n\n".join(lines), encoding="utf-8")
        print(f"校对稿已写入 {args.review}")
        return
    print(ingest(args.db, recs))


if __name__ == "__main__":
    main()

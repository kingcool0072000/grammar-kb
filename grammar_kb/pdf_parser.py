"""PDF 解析：去水印 + 重排行 + 还原表格。

水印有三层（已通过跨样本字体普查确认）：
1. 斜排水印 ``SimSun``（行方向非水平，dir≈(0.7,-0.7)）
2. 页眉/页脚 chrome ``MicrosoftYaHei`` / ``MicrosoftYaHei-Bold``
   （机构名 / 卷号 / 页码等版式元素）

正文内容字体：STKaiti / KaiTi / FangSong / TimesNewRomanPSMT 等。

可测性：核心逻辑（``is_watermark_span`` / ``filter_spans`` /
``group_spans_into_lines``）是纯函数，接收 Span 对象，单测无需真实 PDF。
fitz/pdfplumber 仅作为薄封装。
"""
from __future__ import annotations

import os
import re
import statistics
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional

from .models import TableData

# fitz/pdfplumber 延迟导入，避免未安装时 import 整个包失败（便于单测纯函数）。

# 已知的水印/页眉页脚字体（命中即丢弃）
WATERMARK_FONTS: frozenset[str] = frozenset(
    {
        "SimSun",
        "MicrosoftYaHei",
        "MicrosoftYaHei-Bold",
    }
)

# 水平方向阈值：dir_x 绝对值 >= 此值视为水平正文
_HORIZONTAL_X = 0.95


def _base_fontname(fontname: str) -> str:
    """去掉 PDF 子集前缀，如 ``YWSWBG+MicrosoftYaHei-Bold`` → ``MicrosoftYaHei-Bold``。"""
    fn = fontname or ""
    return fn.split("+", 1)[-1] if "+" in fn else fn


def _is_cjk(ch: str) -> bool:
    """字符是否为 CJK 汉字（用于决定拼接时是否插空格）。"""
    return bool(ch) and "一" <= ch <= "鿿"


def is_watermark_font(fontname: str) -> bool:
    """字体是否属于水印/页眉页脚（兼容子集前缀）。"""
    base = _base_fontname(fontname)
    return base in WATERMARK_FONTS or "MicrosoftYaHei" in base


@dataclass
class Span:
    """一个文字 span（可由 fitz span 构造，也可在测试里手搓）。"""

    text: str
    font: str
    size: float
    x0: float
    y0: float  # 顶部 top
    x1: float
    y1: float  # 底部 bottom
    dir_x: float = 1.0
    dir_y: float = 0.0

    @property
    def ymid(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def height(self) -> float:
        return max(1.0, self.y1 - self.y0)


@dataclass
class Line:
    """重排后的一行文本。"""

    text: str
    y0: float
    y1: float
    x0: float
    page: int = 1
    size: float = 0.0
    spans: list[Span] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 纯函数：水印判定 / 过滤 / 重排
# --------------------------------------------------------------------------- #


def is_watermark_span(span: Span) -> bool:
    """判断 span 是否为水印/页眉页脚，应丢弃。

    规则：
    1. 行方向非水平（斜排水印）；
    2. 字体为水印字体（SimSun / MicrosoftYaHei 系，兼容子集前缀）。
    """
    if abs(span.dir_x) < _HORIZONTAL_X:
        return True
    if is_watermark_font(span.font):
        return True
    return False


def filter_spans(spans: Iterable[Span]) -> list[Span]:
    """去掉水印/页眉页脚，仅保留正文 span。"""
    return [s for s in spans if not is_watermark_span(s) and s.text.strip()]


def _default_y_tol(spans: list[Span]) -> float:
    """按字号估计"同一行"的垂直容差。"""
    if not spans:
        return 4.0
    sizes = [s.height for s in spans]
    med = statistics.median(sizes)
    return max(2.5, med * 0.55)


def group_spans_into_lines(
    spans: Iterable[Span],
    y_tol: Optional[float] = None,
) -> list[Line]:
    """把 span 聚成行：垂直中心接近的算同一行，行内按 x0 升序拼接。

    返回按 y0（顶部）升序排列的 Line 列表。
    """
    spans = [s for s in spans if s.text.strip()]
    if not spans:
        return []
    if y_tol is None:
        y_tol = _default_y_tol(spans)

    # 按 y 中心排序后贪心聚类
    order = sorted(spans, key=lambda s: (s.ymid, s.x0))
    clusters: list[list[Span]] = []
    cur: list[Span] = []
    cur_y: Optional[float] = None
    for s in order:
        if cur_y is None or abs(s.ymid - cur_y) <= y_tol:
            cur.append(s)
            # 用当前行的平均 y 维持基准
            cur_y = statistics.mean([c.ymid for c in cur])
        else:
            clusters.append(cur)
            cur = [s]
            cur_y = s.ymid
    if cur:
        clusters.append(cur)

    lines: list[Line] = []
    for cl in clusters:
        cl.sort(key=lambda s: s.x0)
        # 相邻 span 之间若 x 缝隙较大，按需补空格（中英混排）；
        # 但 CJK↔CJK 之间不补空格（中文无词间空格）。
        parts: list[str] = []
        prev_x1: Optional[float] = None
        prev_text = ""
        for s in cl:
            if prev_x1 is not None and s.x0 - prev_x1 > s.height * 0.35:
                if not (_is_cjk(prev_text[-1]) and _is_cjk(s.text[0])):
                    parts.append(" ")
            parts.append(s.text)
            prev_x1 = s.x1
            prev_text = s.text
        text = "".join(parts).strip()
        if not text:
            continue
        ys0 = min(s.y0 for s in cl)
        ys1 = max(s.y1 for s in cl)
        xs0 = min(s.x0 for s in cl)
        size = statistics.median([s.size for s in cl])
        lines.append(
            Line(
                text=text,
                y0=ys0,
                y1=ys1,
                x0=xs0,
                size=size,
                spans=list(cl),
            )
        )
    lines.sort(key=lambda l: (l.y0, l.x0))
    return lines


# --------------------------------------------------------------------------- #
# fitz 封装：page → spans
# --------------------------------------------------------------------------- #


def _suppress_mupdf_errors() -> None:
    try:
        import fitz  # type: ignore

        fitz.TOOLS.mupdf_display_errors = False  # 屏蔽 ExtGState 噪声（部分版本生效）
    except Exception:
        pass


_suppress_mupdf_errors()


@contextmanager
def _silence_stdio() -> Iterator[None]:
    """解析期间把 stdout/stderr 重定向到 /dev/null，屏蔽 MuPDF 的 ExtGState 噪声。

    PyMuPDF 把这些告警写到 **stdout**（实测 fd 1），故需同时静默 1 和 2。
    Python 抛出的异常不受影响（异常经解释器传播，不走 fd 文本写入）。
    parse_pdf 本身不向 stdout 打印，故静默安全。
    """
    sys.stdout.flush()
    sys.stderr.flush()
    saved_out, saved_err = os.dup(1), os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(saved_out)
        os.close(saved_err)
        os.close(devnull)


def extract_spans(page) -> list[Span]:
    """从 fitz page 抽取所有水平方向的 span。"""
    import fitz  # type: ignore

    d = page.get_text("dict")
    out: list[Span] = []
    for block in d.get("blocks", []):
        if block.get("type", 0) != 0:  # 0 = text
            continue
        for line in block.get("lines", []):
            dx, dy = line.get("dir", (1.0, 0.0))
            for sp in line.get("spans", []):
                txt = sp.get("text", "")
                if not txt:
                    continue
                x0, y0, x1, y1 = sp.get("bbox", (0, 0, 0, 0))
                out.append(
                    Span(
                        text=txt,
                        font=sp.get("font", ""),
                        size=float(sp.get("size", 0.0)),
                        x0=x0,
                        y0=y0,
                        x1=x1,
                        y1=y1,
                        dir_x=dx,
                        dir_y=dy,
                    )
                )
    return out


# --------------------------------------------------------------------------- #
# pdfplumber 封装：page → 干净表格
# --------------------------------------------------------------------------- #


def _clean_plumber_page(page):
    """返回过滤掉水印字符的 pdfplumber page 副本。"""
    def keep(obj) -> bool:
        # pdfplumber 对象是 dict-like，必须用 .get()（属性访问拿不到 object_type）
        if not hasattr(obj, "get"):
            return True
        if obj.get("object_type") == "char":
            # 斜排水印：upright 为假（兼容 numpy.bool_）
            if not obj.get("upright", True):
                return False
            if is_watermark_font(obj.get("fontname", "")):
                return False
        return True

    return page.filter(keep)


def detect_tables(page) -> list[tuple[TableData, tuple[float, float, float, float]]]:
    """检测表格（已过滤水印字符），返回 ``[(TableData, bbox), ...]``。

    bbox 为 ``(x0, top, x1, bottom)``。数据与 bbox 来自同一次 find_tables，
    保证两者一一对应；单列"表格"（方框/标题盒误判）被剔除。
    """
    cpage = _clean_plumber_page(page)
    out: list[tuple[TableData, tuple[float, float, float, float]]] = []
    try:
        finder = cpage.find_tables()
    except Exception:
        return out
    for tf in finder:
        try:
            rows = tf.extract()
        except Exception:
            continue
        if not rows:
            continue
        rows = [r for r in rows if any((c or "").strip() for c in r)]
        if not rows:
            continue
        td = TableData.from_rows(rows)
        # 单列、或只有表头无数据行 → 多为方框/标题盒误判，丢弃
        if td.n_cols < 2 or not td.rows:
            continue
        bb = tf.bbox
        out.append((td, (bb[0], bb[1], bb[2], bb[3])))
    return out


def extract_tables(page) -> list[TableData]:
    """仅返回检测到的 TableData 列表（便于直接测试）。"""
    return [td for td, _ in detect_tables(page)]


def table_bboxes(page) -> list[tuple[float, float, float, float]]:
    """仅返回各表格 bbox（与 extract_tables 同序、同量）。"""
    return [bb for _, bb in detect_tables(page)]


# --------------------------------------------------------------------------- #
# 组合：page → 有序元素流（文本行 / 表格）
# --------------------------------------------------------------------------- #


@dataclass
class PageElement:
    """页面内一个元素：一段文本 或 一张表格，带页码与顶部 y。"""

    kind: str  # "text" | "table"
    text: str = ""
    table: Optional[TableData] = None
    page: int = 1
    top: float = 0.0
    bbox: Optional[str] = None


def iter_page_elements(
    page,
    page_number: int,
    fitz_page=None,
) -> list[PageElement]:
    """合并"干净文本行"与"表格"，按垂直位置输出有序元素流。

    - ``page``：pdfplumber page（用于表格检测）；
    - ``fitz_page``：fitz page（用于 span/行重排）。两者一般可由同一 PDF 派生。
    - 在表格 bbox 覆盖的 y 区间内，文本行被吞掉，避免表格内容重复出现。
    """
    # 1) 文本行（来自 fitz，已去水印）
    if fitz_page is not None:
        spans = filter_spans(extract_spans(fitz_page))
        lines = group_spans_into_lines(spans)
    else:
        lines = []

    # 2) 表格 + 其 bbox（同源检测，保证一一对应）
    detected = detect_tables(page)
    tbboxes = [bb for _, bb in detected]

    def in_any_table(y0: float, y1: float) -> bool:
        ymid = (y0 + y1) / 2.0
        for _bx0, bt, _bx1, bb in tbboxes:
            if bt <= ymid <= bb:
                return True
        return False

    elements: list[PageElement] = []
    # 用 (top, kind_order) 排序：表格与文本行混排
    pending: list[tuple[float, int, PageElement]] = []

    for ln in lines:
        if in_any_table(ln.y0, ln.y1):
            continue  # 表格已覆盖，跳过
        pending.append(
            (
                ln.y0,
                0,
                PageElement(
                    kind="text",
                    text=ln.text,
                    page=page_number,
                    top=ln.y0,
                ),
            )
        )

    for tdata, (bx0, bt, bx1, bb) in detected:
        if not tdata.headers:
            continue
        pending.append(
            (
                bt,
                1,
                PageElement(
                    kind="table",
                    table=tdata,
                    page=page_number,
                    top=bt,
                    bbox=f"[{bx0:.0f},{bt:.0f},{bx1:.0f},{bb:.0f}]",
                ),
            )
        )

    pending.sort(key=lambda x: (x[0], x[1]))
    return [e for _, _, e in pending]


# --------------------------------------------------------------------------- #
# 整本 PDF
# --------------------------------------------------------------------------- #


@dataclass
class ParsedPdf:
    """一本讲义 PDF 的解析结果。"""

    elements: list[PageElement]
    page_count: int

    def plain_text(self) -> str:
        """把所有元素拼成纯文本（表格转 markdown 块）。"""
        from .markdown import table_to_markdown

        parts: list[str] = []
        for el in self.elements:
            if el.kind == "text":
                parts.append(el.text)
            elif el.kind == "table" and el.table:
                parts.append(table_to_markdown(el.table))
        return "\n".join(parts)


def parse_pdf(path: str) -> ParsedPdf:
    """解析一本 PDF：fitz 读 span，pdfplumber 读表格，去水印并合并。"""
    import fitz  # type: ignore
    import pdfplumber  # type: ignore

    elements: list[PageElement] = []
    with _silence_stdio():
        doc = fitz.open(path)
        page_count = doc.page_count
        with pdfplumber.open(path) as pdf:
            n = min(page_count, len(pdf.pages))
            for i in range(n):
                fp = doc[i]
                pp = pdf.pages[i]
                elements.extend(iter_page_elements(pp, i + 1, fitz_page=fp))
        doc.close()
    return ParsedPdf(elements=elements, page_count=page_count)

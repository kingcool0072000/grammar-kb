"""pdf_parser 单测：水印过滤、行重排、表格抽取（纯函数）+ 真实 PDF 集成。"""
from grammar_kb.models import TableData
from grammar_kb.pdf_parser import Span
from grammar_kb.pdf_parser import (
    WATERMARK_FONTS,
    filter_spans,
    group_spans_into_lines,
    is_watermark_span,
)


# --------------------------------------------------------------------------- #
# 水印判定
# --------------------------------------------------------------------------- #


def _span(text="x", font="STKaiti", dx=1.0, dy=0.0):
    return Span(
        text=text,
        font=font,
        size=14.1,
        x0=0,
        y0=0,
        x1=10,
        y1=14,
        dir_x=dx,
        dir_y=dy,
    )


def test_watermark_rotated():
    # 斜排水印 dir≈(0.7,-0.7)
    s = _span("睿爸小屋", font="SimSun", dx=0.7, dy=-0.7)
    assert is_watermark_span(s)


def test_watermark_yahei_header():
    s = _span("睿爸小屋", font="MicrosoftYaHei-Bold")
    assert is_watermark_span(s)
    s2 = _span("1 | 8", font="MicrosoftYaHei")
    assert is_watermark_span(s2)


def test_content_kept():
    assert not is_watermark_span(_span("一般现在时", font="STKaiti"))
    assert not is_watermark_span(_span("do / does", font="TimesNewRomanPSMT"))
    assert not is_watermark_span(_span("动词", font="KaiTi"))
    assert not is_watermark_span(_span("的", font="FangSong"))


def test_watermark_font_set_contains_known():
    assert "SimSun" in WATERMARK_FONTS
    assert "MicrosoftYaHei" in WATERMARK_FONTS
    assert "MicrosoftYaHei-Bold" in WATERMARK_FONTS


def test_filter_spans_drops_watermark():
    spans = [
        _span("正文一", font="STKaiti"),
        _span("睿爸小屋", font="MicrosoftYaHei-Bold"),
        _span("水印", font="SimSun", dx=0.7, dy=-0.7),
        _span("正文二", font="TimesNewRomanPSMT"),
    ]
    kept = filter_spans(spans)
    assert [s.text for s in kept] == ["正文一", "正文二"]


# --------------------------------------------------------------------------- #
# 行重排
# --------------------------------------------------------------------------- #


def test_group_into_lines_orders_by_y_then_x():
    # 两行：行B在上方(y小)，行A在下方；行内打乱x顺序
    spans = [
        Span("世界", font="STKaiti", size=14, x0=50, y0=100, x1=80, y1=114),
        Span("你好", font="STKaiti", size=14, x0=10, y0=100, x1=40, y1=114),
        Span("第二行", font="STKaiti", size=14, x0=10, y0=130, x1=60, y1=144),
    ]
    lines = group_spans_into_lines(spans, y_tol=6)
    assert len(lines) == 2
    assert lines[0].text == "你好世界"  # 同行按 x 升序拼接
    assert lines[1].text == "第二行"


def test_group_into_lines_handles_empty():
    assert group_spans_into_lines([]) == []


def test_group_into_lines_inserts_space_for_gap():
    # 中英之间大缝隙应补空格
    spans = [
        Span("例", font="STKaiti", size=14, x0=10, y0=100, x1=24, y1=114),
        Span("He goes.", font="TimesNewRomanPSMT", size=14, x0=60, y0=100, x1=120, y1=114),
    ]
    lines = group_spans_into_lines(spans, y_tol=6)
    # 至少应把两段拼到同一行（同一 y）
    assert len(lines) == 1
    assert "例" in lines[0].text and "He goes." in lines[0].text


# --------------------------------------------------------------------------- #
# TableData.from_rows
# --------------------------------------------------------------------------- #


def test_tabledata_pads_ragged_rows():
    td = TableData.from_rows([["时态", "构成"], ["一般现在时"]])
    assert td.headers == ["时态", "构成"]
    assert td.rows == [["一般现在时", ""]]
    assert td.n_cols == 2


def test_tabledata_empty_header_filled():
    td = TableData.from_rows([["", ""], ["a", "b"]])
    assert td.headers == ["列1", "列2"]


# --------------------------------------------------------------------------- #
# 真实 PDF 集成（目录缺失则跳过）
# --------------------------------------------------------------------------- #


def test_real_pdf_watermark_removed(handbook_dir):
    import os

    from grammar_kb.pdf_parser import extract_spans, filter_spans

    path = os.path.join(handbook_dir, "22.动词时态1_讲义解析.pdf")
    if not os.path.isfile(path):
        import pytest

        pytest.skip("缺第22讲 PDF")
    import fitz

    doc = fitz.open(path)
    spans = extract_spans(doc[0])
    doc.close()
    kept = filter_spans(spans)
    full = "".join(s.text for s in kept)
    # 水印文字不应再出现
    assert "睿爸小屋" not in full
    assert "哈一（初中语法）" not in full
    # 正文保留
    assert "一般现在时" in full


def test_real_pdf_table_restored(handbook_dir):
    import os

    from grammar_kb.pdf_parser import extract_tables

    path = os.path.join(handbook_dir, "22.动词时态1_讲义解析.pdf")
    if not os.path.isfile(path):
        import pytest

        pytest.skip("缺第22讲 PDF")
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        tables = extract_tables(pdf.pages[0])
    assert len(tables) >= 1
    t = tables[0]
    assert "时态名称" in t.headers
    # 构造列里应包含 8 种时态的构成（无水印污染）
    joined = " ".join(t.headers) + " " + " ".join(c for r in t.rows for c in r)
    assert "do / does" in joined
    assert "have/has+done" in joined
    assert "had+done" in joined
    assert "睿爸" not in joined  # 水印不残留

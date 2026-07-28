"""structure 单测：行分类、知识点切分。"""
from grammar_kb.pdf_parser import PageElement, ParsedPdf
from grammar_kb.structure import classify_line, structure
from grammar_kb.models import TableData


# --------------------------------------------------------------------------- #
# 行分类
# --------------------------------------------------------------------------- #


def test_classify_line_types():
    assert classify_line("第二十五讲 动词时态3") == "title"
    assert classify_line("I.过去将来时") == "section"
    assert classify_line("1.过去将来时的定义") == "subhead"
    assert classify_line("1) Mary will go to Canada.") == "subitem"
    assert classify_line("例：He always goes to work by car.") == "example"
    assert classify_line("课堂练习1") == "exercise_hdr"


def test_classify_line_exercise_context():
    # 在练习块内，"1. ___ ..." 不应被判为小节标题
    assert classify_line("1. ______ is a must-see.", in_exercise=True) != "subhead"


def test_classify_line_blanks_not_subhead():
    # 含长下划线的英文题干不是小节标题
    assert classify_line("1. ____ is coming and I will get lucky money.") != "subhead"


def test_classify_line_option():
    assert classify_line("A. The Huangpu River") == "option"


# --------------------------------------------------------------------------- #
# 知识点切分（用合成元素流）
# --------------------------------------------------------------------------- #


def _txt(text, page=1, top=0.0):
    return PageElement(kind="text", text=text, page=page, top=top)


def _tbl(headers, rows, page=1, top=0.0):
    return PageElement(
        kind="table", table=TableData(headers=headers, rows=rows), page=page, top=top
    )


def test_structure_carves_knowledge_points():
    elements = [
        _txt("第二十二讲 动词时态1"),
        _txt("I.动词的八种时态", top=10),
        _tbl(["时态名称", "构成"], [["一般现在时", "do / does"]], top=20),
        _txt("II.一般现在时", top=30),
        _txt("1.一般现在时的定义：表示经常性的动作。", top=40),
        _txt("例：He always goes to work by car.", top=50),
        _txt("2.一般现在时的构成", top=60),
        _txt("主语+动词原形/三单。", top=70),
        _txt("课堂练习1", top=80),
        _txt("1. He ___ to school every day.", top=90),
        _txt("A. go  B. goes", top=95),
    ]
    parsed = ParsedPdf(elements=elements, page_count=1)
    sl = structure(parsed, source_file="22.动词时态1_讲义解析.pdf")

    # 讲次信息
    assert sl.lecture.number == 22
    assert sl.lecture.category == "时态"
    # 知识点：节级表格(1) + 定义(1) + 构成(1)，练习不算
    titles = [k.title for k in sl.knowledge_points]
    assert any("定义" in t for t in titles)
    assert any("构成" in t for t in titles)
    # 练习内容不应成为知识点
    assert not any("every day" in (k.body_md or "") and "go" in k.title for k in sl.knowledge_points)
    # 表格被还原到节级知识点
    table_kps = [k for k in sl.knowledge_points if k.table_md]
    assert any("时态名称" in k.table_md for k in table_kps)


def test_structure_extracts_markers_and_relations():
    elements = [
        _txt("I.现在完成时"),
        _txt("1.现在完成时的用法"),
        _txt("I have already done it. He has lived here since 2010."),
        _txt("在条件句中体现主将从现。"),
    ]
    sl = structure(ParsedPdf(elements=elements, page_count=1), source_file="23.动词时态2_讲义解析.pdf")
    kp = sl.knowledge_points[0]
    assert any(m.marker.lower() == "already" for m in kp.markers)
    assert any(r.type == "主将从现" for r in kp.relations)


def test_structure_section_path_recorded():
    elements = [
        _txt("I.过去将来时"),
        _txt("1.过去将来时的定义：从过去看将来。"),
    ]
    sl = structure(ParsedPdf(elements=elements, page_count=1), source_file="25.动词时态3_讲义解析.pdf")
    kp = sl.knowledge_points[0]
    assert "I.过去将来时" in kp.section_path
    assert "1.过去将来时的定义" in kp.title or "过去将来时的定义" in kp.title

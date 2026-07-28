"""markdown 渲染单测。"""
from grammar_kb.markdown import render_knowledge_point, render_lecture, table_to_markdown
from grammar_kb.models import Block, KnowledgePoint, Lecture, Marker, Relation, TableData


def test_table_to_markdown_basic():
    t = TableData(headers=["时态名称", "构成"], rows=[["一般现在时", "do / does"]])
    md = table_to_markdown(t)
    lines = md.splitlines()
    assert lines[0] == "| 时态名称 | 构成 |"
    assert lines[1] == "| --- | --- |"
    assert lines[2] == "| 一般现在时 | do / does |"


def test_table_to_markdown_caption():
    t = TableData(headers=["A", "B"], rows=[["1", "2"]], caption="对比表")
    md = table_to_markdown(t)
    assert md.startswith("**对比表**")


def test_table_to_markdown_escapes_pipe():
    t = TableData(headers=["x"], rows=[["a|b"]])
    md = table_to_markdown(t)
    assert "a\\|b" in md           # 管道符被转义
    assert len(md.splitlines()) == 3  # 表头/分隔/数据 三行成表


def test_table_to_markdown_empty_headers():
    assert table_to_markdown(TableData(headers=[], rows=[])) == ""


def test_render_knowledge_point_includes_source():
    kp = KnowledgePoint(
        title="过去将来时的定义",
        lecture_number=25,
        category="时态",
        section_path="I.过去将来时 > 1.定义",
        body_md="表示从过去某时间看将要发生的动作。",
        examples_md="Tom said Mary would go.",
        markers=[Marker(marker="would", tense="过去将来时")],
        relations=[Relation(type="时态呼应")],
        tags=["时态", "过去将来时"],
        source_page=1,
    )
    md = render_knowledge_point(kp)
    assert "## 过去将来时的定义" in md
    assert "第25讲" in md
    assert "P1" in md
    assert "**标志词**" in md and "would" in md
    assert "**关系**" in md and "时态呼应" in md
    assert "#过去将来时" in md


def test_render_lecture_with_table_block():
    lec = Lecture(number=25, title="动词时态3", full_title="第二十五讲 动词时态3",
                  category="时态", subcategory="动词时态", page_count=7)
    blocks = [
        Block(kind="heading", text_md="## I.过去将来时"),
        Block(kind="subheading", text_md="### 1.定义"),
        Block(kind="para", text_md="表示从过去某时间看将要发生。"),
        Block(kind="table", table_data=TableData(headers=["句型", "例"], rows=[["陈述句", "He would go."]])),
    ]
    md = render_lecture(lec, blocks)
    assert "# 第25讲 动词时态3" in md
    assert "## I.过去将来时" in md
    assert "| 陈述句 | He would go. |" in md  # 表格还原

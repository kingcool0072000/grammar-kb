"""query 单测：检索、标志词查询、整讲还原。"""
import os
import tempfile

import pytest

from grammar_kb.db import GrammarDB
from grammar_kb.ingest import open_db  # noqa: F401
from grammar_kb.models import Block, KnowledgePoint, Lecture, Marker
from grammar_kb.query import Query


@pytest.fixture()
def q():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "test.db")
        with GrammarDB(path) as g:
            lid = g.upsert_lecture(
                Lecture(
                    number=25,
                    title="动词时态3",
                    full_title="第二十五讲 动词时态3",
                    category="时态",
                    subcategory="动词时态",
                    page_count=7,
                )
            )
            g.insert_kp(
                KnowledgePoint(
                    title="过去将来时的定义",
                    lecture_number=25,
                    category="时态",
                    section_path="I.过去将来时 > 1.定义",
                    body_md="从过去某时间看将要发生。He would come. I have already done.",
                    markers=[
                        Marker(marker="would", tense="过去将来时"),
                        Marker(marker="already", tense="现在完成时"),
                    ],
                    source_page=1,
                ),
                lid,
            )
            g.insert_blocks(
                lid,
                [
                    Block(kind="heading", text_md="## I.过去将来时", page=1, seq=0),
                    Block(kind="para", text_md="正文段落。", page=1, seq=1),
                ],
            )
            yield Query(g)


def test_search_by_chinese(q):
    kps = q.search_kps("过去将来时")
    assert len(kps) == 1
    assert kps[0].title == "过去将来时的定义"


def test_search_by_english(q):
    kps = q.search_kps("already")
    assert len(kps) == 1


def test_search_category_filter(q):
    assert len(q.search_kps("定义", category="时态")) == 1
    assert len(q.search_kps("定义", category="词法")) == 0


def test_search_empty_query(q):
    assert q.search_kps("") == []


def test_markers_by_category(q):
    rows = q.markers_by_category("时态")
    markers = {r["marker"] for r in rows}
    assert "would" in markers
    assert "already" in markers


def test_markers_by_tense(q):
    rows = q.markers_by_tense("过去将来时")
    assert any(r["marker"] == "would" for r in rows)


def test_lecture_markdown_roundtrip(q):
    md = q.lecture_markdown(25)
    assert md is not None
    assert "# 第25讲 动词时态3" in md
    assert "I.过去将来时" in md
    assert "正文段落" in md


def test_kp_markdown(q):
    kps = q.search_kps("过去将来时")
    md = q.kp_markdown(kps[0].id)
    assert "过去将来时的定义" in md
    assert "第25讲" in md


def test_lecture_missing(q):
    assert q.lecture_markdown(999) is None


def test_lecture_html(q):
    html = q.lecture_html(25)
    assert html is not None
    assert html.startswith("<!DOCTYPE html>")
    assert "<title>第25讲 动词时态3</title>" in html
    assert "过去将来时" in html


def test_kp_html(q):
    kps = q.search_kps("过去将来时")
    html = q.kp_html(kps[0].id)
    assert html is not None
    assert "<html" in html
    assert kps[0].title in html


def test_lecture_html_missing(q):
    assert q.lecture_html(999) is None


def test_exam_signal_in_kp(q):
    kps = q.search_kps("过去将来时")
    assert kps and "时态" in kps[0].exam_signals


def test_kps_by_exam_signal(q):
    kps = q.kps_by_exam_signal("时态")
    assert any(kp.title == "过去将来时的定义" for kp in kps)


def test_list_exam_signals(q):
    sigs = q.list_exam_signals()
    assert "时态" in sigs

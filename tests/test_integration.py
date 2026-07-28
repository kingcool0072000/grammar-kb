"""端到端集成：真实 PDF → 导入 → 查询（缺讲义目录则整体跳过）。

直接覆盖用户的几个示例需求：
- 导入讲义、知识点可溯源到某一讲
- 表格被还原
- "所有时态关键词"可查
- "第25讲讲义 md" 可生成
"""
import os
import tempfile

import pytest

from grammar_kb.ingest import ingest_pdf, open_db
from grammar_kb.query import Query

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def ingested(handbook_dir):
    """导入第 22、25 讲到临时库，返回 Query。"""
    with tempfile.TemporaryDirectory() as d:
        os.environ["GRAMMAR_KB_DB"] = os.path.join(d, "it.db")
        db = open_db()
        for name in ["22.动词时态1_讲义解析.pdf", "25.动词时态3_讲义解析.pdf"]:
            p = os.path.join(handbook_dir, name)
            if os.path.isfile(p):
                ingest_pdf(db, p)
        yield Query(db)
        db.close()
        os.environ.pop("GRAMMAR_KB_DB", None)


def _have(ingested):
    return ingested.stats()["lectures"] > 0


def test_ingest_produces_knowledge_points(ingested):
    if not _have(ingested):
        pytest.skip("讲义未导入")
    s = ingested.stats()
    assert s["knowledge_points"] > 0
    assert s["by_category"].get("时态", 0) > 0


def test_table_restored_in_lecture22(ingested):
    if not _have(ingested):
        pytest.skip("讲义未导入")
    md = ingested.lecture_markdown(22)
    if md is None:
        pytest.skip("缺第22讲")
    # 八种时态构成表应被还原为 GFM 表格
    assert "| 时态名称 |" in md or "| 时态名称" in md
    assert "have/has+done" in md
    assert "睿爸" not in md  # 水印不残留


def test_tense_markers_query(ingested):
    """对应需求：给我所有时态关键词。"""
    if not _have(ingested):
        pytest.skip("讲义未导入")
    rows = ingested.markers_by_category("时态")
    assert len(rows) > 0
    # 至少出现一些经典标志词
    markers = {r["marker"].lower() for r in rows}
    classic = {"always", "now", "already", "since", "yesterday", "tomorrow"}
    assert markers & classic


def test_kp_traceable_to_lecture(ingested):
    """知识点可溯源到某一讲。"""
    if not _have(ingested):
        pytest.skip("讲义未导入")
    kps = ingested.kps_of_lecture(22)
    if not kps:
        pytest.skip("缺第22讲")
    for kp in kps:
        assert kp.lecture_number == 22
        assert kp.category == "时态"
        assert kp.source_page >= 1


def test_search_works(ingested):
    if not _have(ingested):
        pytest.skip("讲义未导入")
    # 中文子串检索
    res = ingested.search_kps("时态")
    assert isinstance(res, list)


def test_lecture25_markdown(ingested):
    """对应需求：给我第25课讲义 md。"""
    if not _have(ingested):
        pytest.skip("讲义未导入")
    md = ingested.lecture_markdown(25)
    if md is None:
        pytest.skip("缺第25讲")
    assert "# 第25讲" in md
    assert "过去将来时" in md

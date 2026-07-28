"""db 单测：CRUD、不截断、FTS 检索、级联清理。"""
import os
import tempfile

import pytest

from grammar_kb.db import GrammarDB
from grammar_kb.models import Block, KnowledgePoint, Lecture, Marker, Relation


@pytest.fixture()
def db():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "test.db")
        with GrammarDB(path) as g:
            yield g


def _make_lecture(number=25):
    return Lecture(
        number=number,
        title="动词时态3",
        full_title=f"第二十五讲 动词时态3",
        category="时态",
        subcategory="动词时态",
        source_file="/tmp/x.pdf",
        page_count=7,
    )


def test_lecture_upsert_and_get(db):
    lid = db.upsert_lecture(_make_lecture())
    assert lid > 0
    lec = db.get_lecture(25)
    assert lec is not None
    assert lec.title == "动词时态3"
    assert lec.category == "时态"
    # 再 upsert 同号应更新而非新增
    lid2 = db.upsert_lecture(_make_lecture())
    assert len(db.list_lectures()) == 1


def test_kp_insert_and_markers_relations(db):
    lid = db.upsert_lecture(_make_lecture())
    kp = KnowledgePoint(
        title="过去将来时的定义",
        lecture_number=25,
        category="时态",
        section_path="I.过去将来时 > 1.定义",
        body_md="从过去某时间看将要发生的动作。",
        examples_md="Tom said he would go.",
        markers=[Marker(marker="would", tense="过去将来时")],
        relations=[Relation(type="时态呼应")],
        tags=["时态", "过去将来时"],
        source_page=1,
    )
    kp_id = db.insert_kp(kp, lid)
    got = db.get_kp(kp_id)
    assert got.title == "过去将来时的定义"
    assert any(m.marker == "would" for m in got.markers)
    assert any(r.type == "时态呼应" for r in got.relations)
    assert "过去将来时" in got.tags


def test_no_truncation_long_body(db):
    """正文长度无上限：写入 200KB，读回完全相等。"""
    lid = db.upsert_lecture(_make_lecture())
    big = "知识点正文。" * 50000  # ~ 350000 字符
    assert len(big) > 200_000
    kp = KnowledgePoint(title="超长正文", lecture_number=25, category="时态", body_md=big)
    kp_id = db.insert_kp(kp, lid)
    got = db.get_kp(kp_id)
    assert got.body_md == big
    assert len(got.body_md) == len(big)


def test_fts_search_finds_kp(db):
    lid = db.upsert_lecture(_make_lecture())
    db.insert_kp(
        KnowledgePoint(
            title="现在完成时的用法",
            lecture_number=22,
            category="时态",
            body_md="I have already finished the work since this morning.",
        ),
        lid,
    )
    # 直接用底层 FTS（trigram 子串）
    rows = db.conn.execute(
        "SELECT k.id FROM knowledge_point k JOIN kp_fts f ON f.rowid=k.id "
        "WHERE kp_fts MATCH ?",
        ('"already"',),
    ).fetchall()
    assert len(rows) == 1


def test_fts_search_chinese_substring(db):
    lid = db.upsert_lecture(_make_lecture())
    db.insert_kp(
        KnowledgePoint(
            title="过去完成时的构成",
            lecture_number=26,
            category="时态",
            body_md="had 加过去分词。",
        ),
        lid,
    )
    rows = db.conn.execute(
        "SELECT k.id FROM knowledge_point k JOIN kp_fts f ON f.rowid=k.id "
        "WHERE kp_fts MATCH ?",
        ('"过去分词"',),
    ).fetchall()
    assert len(rows) == 1


def test_cascade_clear_lecture(db):
    lid = db.upsert_lecture(_make_lecture())
    db.insert_kp(
        KnowledgePoint(title="x", lecture_number=25, category="时态", body_md="y"),
        lid,
    )
    db.insert_blocks(lid, [Block(kind="para", text_md="hello", seq=0)])
    assert db.stats()["knowledge_points"] == 1
    db.clear_lecture(25)
    assert db.stats()["knowledge_points"] == 0
    # block 也应被级联清掉
    assert db.blocks_of_lecture(lid) == []


def test_blocks_roundtrip(db):
    lid = db.upsert_lecture(_make_lecture())
    from grammar_kb.models import TableData

    db.insert_blocks(
        lid,
        [
            Block(kind="heading", text_md="## I.节", page=1, seq=0),
            Block(kind="table", table_data=TableData(headers=["A", "B"], rows=[["1", "2"]]), page=1, seq=1),
        ],
    )
    blocks = db.blocks_of_lecture(lid)
    assert len(blocks) == 2
    assert blocks[1].kind == "table"
    assert blocks[1].table_data is not None
    assert blocks[1].table_data.headers == ["A", "B"]


def test_kps_of_category(db):
    lid = db.upsert_lecture(_make_lecture())
    db.insert_kp(
        KnowledgePoint(title="a", lecture_number=25, category="时态", body_md="x"), lid
    )
    assert len(db.kps_of_category("时态")) == 1
    assert len(db.kps_of_category("词法")) == 0


def test_reset_all_restarts_ids_from_one(db):
    """反复导入后 id 不应无限漂移；reset_all 后 id 从 1 开始、可复现。"""
    lid = db.upsert_lecture(_make_lecture())
    db.insert_kp(
        KnowledgePoint(title="第一次", lecture_number=25, category="时态", body_md="x"),
        lid,
    )
    first_max = db.conn.execute("SELECT MAX(id) FROM knowledge_point").fetchone()[0]

    # 模拟反复重建
    for _ in range(3):
        db.reset_all()
        lid = db.upsert_lecture(_make_lecture())
        db.insert_kp(
            KnowledgePoint(title="重建", lecture_number=25, category="时态", body_md="x"),
            lid,
        )

    # id 应回到小值（从 1 重新开始），而非累积到 first_max 之后越来越大
    ids = [r[0] for r in db.conn.execute("SELECT id FROM knowledge_point")]
    assert ids == [1]
    assert 1 <= first_max  # 之前至少有一条

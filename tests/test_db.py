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
        full_title="第二十五讲 动词时态3",
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
    db.upsert_lecture(_make_lecture())
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


def test_clear_lecture_purges_fts_index(db):
    """clear_lecture 必须真正清掉 FTS 索引（external-content 孤儿索引回归）。

    旧顺序（先 DELETE lecture 再 DELETE kp_fts）：级联已清空 knowledge_point，
    FTS 的子查询恒为空集 → 孤儿索引残留；同讲重新导入时 rowid 复用，
    旧关键词会误命中新知识点。注意 COUNT(*)/JOIN 走内容表查不出孤儿，
    必须直接 MATCH 查 FTS 索引本身。
    """
    lid = db.upsert_lecture(_make_lecture())
    db.insert_kp(
        KnowledgePoint(
            title="清理顺序探针",
            lecture_number=25,
            category="时态",
            body_md="独一无二的关键词zzq",
        ),
        lid,
    )
    # 搜索方式与现有 FTS 测试一致：先确认能搜到
    assert len(_fts_match(db, "独一无二的关键词zzq")) == 1

    db.clear_lecture(25)

    # 内容表行已删，JOIN 查不出来——直接查索引才能暴露孤儿
    assert _fts_index_rows(db, "独一无二的关键词zzq") == []

    # 真实危害：重新导入同讲（rowid 从头复用），旧关键词不得误命中新内容
    lid2 = db.upsert_lecture(_make_lecture())
    db.insert_kp(
        KnowledgePoint(title="重新导入", lecture_number=25, category="时态", body_md="全新正文"),
        lid2,
    )
    assert _fts_match(db, "独一无二的关键词zzq") == []
    assert len(_fts_match(db, "全新正文")) == 1


def _fts_match(db, term: str) -> list:
    """常规 FTS 搜索（JOIN 内容表，与 /search 端点同路径）。"""
    return db.conn.execute(
        "SELECT k.id FROM knowledge_point k JOIN kp_fts f ON f.rowid=k.id "
        "WHERE kp_fts MATCH ?",
        (f'"{term}"',),
    ).fetchall()


def _fts_index_rows(db, term: str) -> list:
    """绕过内容表直查 FTS 索引：暴露 external-content 孤儿索引。"""
    return db.conn.execute(
        "SELECT rowid FROM kp_fts WHERE kp_fts MATCH ?",
        (f'"{term}"',),
    ).fetchall()


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


def test_meta_roundtrip(db):
    db.set_meta("dataset_version", "data-v1")
    db.set_meta("generated_at", "2026-07-29")
    db.set_meta("dataset_version", "data-v2")  # 覆盖
    meta = db.get_meta_all()
    assert meta["dataset_version"] == "data-v2"
    assert meta["generated_at"] == "2026-07-29"
    # stats 应带上 dataset 字段
    assert db.stats()["dataset"]["dataset_version"] == "data-v2"


def test_exam_store_closes_connections(tmp_path):
    """ExamStore 每次操作后连接必须显式 close（iCloud 整文件替换的前提）。

    回归：``with conn`` 只管事务不管关闭，句柄悬滞与 iCloud 文件替换互相干扰。
    """
    import sqlite3
    from unittest.mock import patch

    from grammar_kb.exam_store import ExamStore

    made: list[sqlite3.Connection] = []
    orig = ExamStore._conn

    def spy(self):
        con = orig(self)
        made.append(con)
        return con

    store = ExamStore(str(tmp_path / "exam.db"))
    with patch.object(ExamStore, "_conn", spy):
        store.add(1, "2026-09-14", 90, [3])
        store.list()
        store.update(1, 1, "2026-09-15", 95)
        store.delete(1)

    assert len(made) == 4  # 四次操作各自开过连接
    for con in made:  # 已关闭的连接再执行语句必抛 ProgrammingError
        with pytest.raises(sqlite3.ProgrammingError):
            con.execute("SELECT 1")

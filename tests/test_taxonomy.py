"""知识点主题体系归类测试。"""
from grammar_kb.models import KnowledgePoint
from grammar_kb.taxonomy import classify


def _kp(title, cat="句法", subcat=None, body="", markers=None):
    return KnowledgePoint(
        title=title, lecture_number=1, category=cat,
        tags=[cat] + ([subcat] if subcat else []),
        body_md=body, markers=markers or [],
    )


def test_subcat_exact_match():
    assert classify(_kp("任意标题", cat="句法", subcat="定语从句")) == ("句法", "定语从句")
    assert classify(_kp("任意标题", cat="词法", subcat="冠词")) == ("词法", "冠词")


def test_tense_split_by_title():
    kp = _kp("现在完成时的用法", cat="时态", subcat="动词时态")
    assert classify(kp) == ("时态", "现在完成时")
    kp2 = _kp("时态归纳总结", cat="时态", subcat="动词时态")
    assert classify(kp2)[1] in ("动词时态总览", "动词时态综合")


def test_markers_dense_to_signal_words():
    kp = _kp("看时间状语选时态", cat="时态", subcat="动词时态",
             markers=[{"marker": "now"}] * 5)
    # 标志词密集但 title 命中细分时态时优先进细分——此用例 title 无时态名
    g, t = classify(kp)
    assert g == "时态"


def test_sentence_type_by_title():
    assert classify(_kp("感叹句的用法"))[1] == "感叹句"
    assert classify(_kp("陈述句变一般疑问句"))[1] == "句子类型"


def test_other_fallback():
    g, t = classify(_kp("完全无法归类的内容标题", cat="句法"))
    assert t == "其它"


def test_theme_notes_cover_all_populated_themes():
    """每个有知识点的主题都配有讲义（统一讲解形式：summary/points/formula/tips）。"""
    from grammar_kb.db import GrammarDB
    from grammar_kb.query import Query
    from grammar_kb.theme_notes import THEME_NOTES

    q = Query(GrammarDB("data/grammar.db"))
    t = q.taxonomy()
    missing = []
    for g in t["groups"]:
        for th in g["themes"]:
            note = th.get("note")
            if not note or not note.get("points"):
                missing.append(f"{g['group']}·{th['theme']}")
            else:
                # 讲义形式统一性：summary 一句话 + points 条目 + formula 或 tips
                assert note.get("summary"), f"{th['theme']} 缺 summary"
                assert note.get("formula") or note.get("tips"), f"{th['theme']} 缺 formula/tips"
    assert not missing, f"缺讲义的主题: {missing}"


def test_theme_examples_use_corpus():
    """主题例句来自教材语料（中英对、完整句、非语法标注行）。"""
    from grammar_kb.db import GrammarDB
    from grammar_kb.query import Query

    q = Query(GrammarDB("data/grammar.db"))
    t = q.taxonomy()
    total = sum(len(th["examples"]) for g in t["groups"] for th in g["themes"])
    assert total >= 40, f"教材例句过少: {total}"
    for g in t["groups"]:
        for th in g["themes"]:
            for ex in th["examples"]:
                assert ex["en"][:1].isupper() and ex["en"][-1] in ".!?"
                assert 4 <= len(ex["zh"]) <= 45


def test_review_routed_to_grammar_themes():
    """综合复习不是知识点：全部按考察主题分流，体系里不再有综合复习组。"""
    from grammar_kb.db import GrammarDB
    from grammar_kb.query import Query

    t = Query(GrammarDB("data/grammar.db")).taxonomy()
    groups = [g["group"] for g in t["groups"]]
    assert "综合复习" not in groups
    # 知识点总数不丢
    assert t["total"] == 359


def test_items_have_brief():
    """每个知识点条目带一句话释义。"""
    from grammar_kb.db import GrammarDB
    from grammar_kb.query import Query

    t = Query(GrammarDB("data/grammar.db")).taxonomy()
    for g in t["groups"]:
        for th in g["themes"]:
            for it in th["items"]:
                assert it.get("brief"), f"{th['theme']}/{it['title']} 缺 brief"


def test_exam_store_crud(tmp_path):
    """作业成绩库：增、查、删（独立 exam.db，不随 ingest 重建丢失）。"""
    from grammar_kb.exam_db import ExamStore

    s = ExamStore(tmp_path / "exam.db")
    r1 = s.add(lecture=22, date="2026-08-23", score=92, wrong=[10, 3, 7])
    assert r1["score"] == 92 and r1["wrong"] == [3, 7, 10]  # 排序去重
    s.add(lecture=22, date="2026-08-24", score=94, wrong=[7, 20])
    s.add(lecture=1, date="2026-08-25", score=100, wrong=[])

    all_ = s.list()
    assert len(all_) == 3
    # 日期倒序
    assert all_[0]["date"] == "2026-08-25"

    assert s.delete(r1["id"]) is True
    assert s.delete(r1["id"]) is False  # 再删不存在
    assert len(s.list()) == 2

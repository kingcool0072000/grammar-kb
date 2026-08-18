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

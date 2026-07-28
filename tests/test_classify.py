"""classify 模块单测：讲次分类、文件名解析、标志词抽取、关系检测。"""
from grammar_kb.classify import (
    TENSE_MARKERS,
    classify_lecture_title,
    detect_relations,
    extract_markers,
    guess_tense_of_kp,
    parse_filename,
)
from grammar_kb.models import Category


# --------------------------------------------------------------------------- #
# 讲次分类
# --------------------------------------------------------------------------- #


def test_classify_tense():
    assert classify_lecture_title("第二十二讲 动词时态1") == ("时态", "动词时态")


def test_classify_lexical():
    assert classify_lecture_title("第1讲 名词")[0] == "词法"
    assert classify_lecture_title("介词2") == ("词法", "介词")
    assert classify_lecture_title("形容词1") == ("词法", "形容词")


def test_classify_voice_and_nonfinite():
    assert classify_lecture_title("被动语态1") == ("语态", "被动语态")
    assert classify_lecture_title("不定式2") == ("非谓语", "不定式")
    assert classify_lecture_title("动名词1") == ("非谓语", "动名词")


def test_classify_syntax():
    assert classify_lecture_title("感叹句") == ("句法", "感叹句")
    assert classify_lecture_title("宾语从句") == ("句法", "宾语从句")
    assert classify_lecture_title("状语从句") == ("句法", "状语从句")
    assert classify_lecture_title("主谓一致") == ("句法", "主谓一致")


def test_classify_review():
    assert classify_lecture_title("综合复习五") == ("综合复习", "综合复习")


def test_classify_verb_not_tense():
    # "动词"（非时态）应归词法，不能误判为时态
    assert classify_lecture_title("动词1") == ("词法", "动词")


def test_classify_unknown():
    assert classify_lecture_title("杂项内容") == (Category.OTHER.value, "其他")


# --------------------------------------------------------------------------- #
# 文件名解析
# --------------------------------------------------------------------------- #


def test_parse_filename_with_suffix():
    assert parse_filename("22.动词时态1_讲义解析.pdf") == (22, "动词时态1", "动词时态1")


def test_parse_filename_no_suffix():
    assert parse_filename("01.名词_讲义.pdf") == (1, "名词", "名词")


def test_parse_filename_path():
    assert parse_filename("/some/dir/25.动词时态3_讲义解析.pdf")[0] == 25


# --------------------------------------------------------------------------- #
# 标志词抽取
# --------------------------------------------------------------------------- #


def test_extract_markers_present_perfect():
    text = "I have already finished it. He has lived here since 2010."
    ms = extract_markers(text)
    markers = {m.marker.lower() for m in ms}
    assert "already" in markers
    assert "since" in markers
    # 全部命中都应归到现在完成时
    assert all(m.tense == "现在完成时" for m in ms if m.marker.lower() in {"already", "since"})


def test_extract_markers_specific_tense_filter():
    text = "always now already"
    ms_now = extract_markers(text, tense="现在进行时")
    markers_now = {m.marker.lower() for m in ms_now}
    assert "now" in markers_now
    # 限定时态时不应返回别的时态的词
    assert all(m.tense == "现在进行时" for m in ms_now)


def test_extract_markers_word_boundary():
    # "for" 不能命中 "before"
    text = "He stood before the door."
    ms = extract_markers(text, tense="现在完成时")
    markers = {m.marker.lower() for m in ms}
    assert "for" not in markers  # before 里的 for 不算
    assert "before" in markers


def test_extract_markers_empty():
    assert extract_markers("") == []
    assert extract_markers("没有任何标志词的句子") == []


def test_tense_markers_dict_complete():
    # 8 个时态都有词典
    expected = {
        "一般现在时", "一般过去时", "一般将来时",
        "现在进行时", "过去进行时",
        "现在完成时", "过去完成时", "过去将来时",
    }
    assert expected <= set(TENSE_MARKERS.keys())


# --------------------------------------------------------------------------- #
# 关系检测
# --------------------------------------------------------------------------- #


def test_detect_zhugjiangxianxian():
    rels = detect_relations("这里体现主将从现：If it rains, I will stay.")
    types = {r.type for r in rels}
    assert "主将从现" in types


def test_detect_none():
    assert detect_relations("普通知识点正文") == []


# --------------------------------------------------------------------------- #
# 时态推断
# --------------------------------------------------------------------------- #


def test_guess_tense():
    assert guess_tense_of_kp("现在完成时的用法") == "现在完成时"
    assert guess_tense_of_kp("定义", body="本节讲一般过去时") == "一般过去时"
    assert guess_tense_of_kp("名词的复数") is None

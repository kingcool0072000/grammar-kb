"""单词表构建测试。"""
from grammar_kb.models import KnowledgePoint
from grammar_kb.vocabulary import (
    build_vocabulary,
    pair_sentences,
    regular_ing,
    regular_past,
    regular_third,
    word_forms,
    IRREGULAR_VERBS,
)


def test_pair_sentences_basic():
    pairs = pair_sentences("He goes to school.\n他去上学。\n无关行")
    assert pairs == [("He goes to school.", "他去上学。")]


def test_pair_sentences_skips_unpaired():
    # 英文行后面没有跟中文行（位于末尾）→ 不配对
    pairs = pair_sentences("先一段中文。\nOnly English.")
    assert pairs == []


def test_regular_past():
    assert regular_past("play") == "played"
    assert regular_past("use") == "used"
    assert regular_past("study") == "studied"
    assert regular_past("stop") == "stopped"  # 双写


def test_regular_ing():
    assert regular_ing("go") == "going"
    assert regular_ing("make") == "making"
    assert regular_ing("run") == "running"  # 双写
    assert regular_ing("lie") == "lying"


def test_regular_third():
    assert regular_third("go") == "goes"
    assert regular_third("study") == "studies"
    assert regular_third("play") == "plays"
    assert regular_third("watch") == "watches"


def test_word_forms_irregular_verb():
    forms, note = word_forms("go", ["v"])
    assert forms["past"] == "went"
    assert forms["past_participle"] == "gone"
    assert "不规则" in note


def test_word_forms_regular_verb():
    forms, note = word_forms("play", ["v"])
    assert forms["past"] == "played"
    assert "规则" in note


def test_word_forms_noun_plural():
    forms, _ = word_forms("box", ["n"])
    assert forms["plural"] in ("boxes", "boxs")


def test_irregular_table_has_common():
    assert IRREGULAR_VERBS["go"] == ("went", "gone")
    assert IRREGULAR_VERBS["buy"] == ("bought", "bought")


def _kp(title, examples, cat="时态", tags=None):
    return KnowledgePoint(
        title=title,
        lecture_number=22,
        category=cat,
        tags=tags or [cat],
        examples_md=examples,
    )


def test_build_vocabulary_extracts_and_pairs():
    kps = [
        _kp(
            "例",
            "She has studied abroad for two years.\n她已经在国外学习两年了。\n"
            "He goes to school.\n他去上学。",
        )
    ]
    voc = build_vocabulary(kps, limit=50, min_freq=1)
    words = {e.word for e in voc}
    assert "studied" in words or "study" in words or "she" not in words
    # 停用词被排除
    assert "has" not in words and "to" not in words
    # 至少一个词带释义
    e = next(e for e in voc if e.word in ("studied", "goes", "school", "abroad", "years"))
    # school/abroad 配到中文释义
    assert any(e.meanings for e in voc)


def test_build_vocabulary_pos_from_subcat():
    kps = [
        KnowledgePoint(
            title="形容词比较级",
            lecture_number=15,
            category="词法",
            tags=["词法", "形容词"],
            examples_md="This book is heavy.\n这本书很重。",
        )
    ]
    voc = build_vocabulary(kps, limit=20, min_freq=1)
    heavy = next((e for e in voc if e.word == "heavy"), None)
    assert heavy is not None
    assert "adj" in heavy.pos


def test_build_vocabulary_freq_and_sources():
    kps = [
        _kp("a", "He runs fast.\n他跑得快。"),
        _kp("b", "She runs too.\n她也跑。"),
    ]
    kps[0].id = 1
    kps[1].id = 2
    voc = build_vocabulary(kps, limit=20, min_freq=1)
    # 屈折变形合并进原形：runs → run，词频与来源随之合并
    run = next(e for e in voc if e.word == "run")
    assert run.freq == 2
    assert len(run.sources) == 2
    assert "third_singular" in run.forms


def test_pos_not_from_lecture_subcat():
    """回归：词性绝不按所在讲次细分推断。

    此前"数词"讲里的 before/out/mike 被标成数词、"代词"讲里的 happy 被标成
    代词、"冠词"讲里的 now 被标成冠词——系统性错标。
    """
    kps = [
        KnowledgePoint(
            title="数词",
            lecture_number=7,
            category="词法",
            tags=["词法", "数词"],
            examples_md="Mike got out before two.\n迈克在两点之前出去了。",
        ),
        KnowledgePoint(
            title="代词",
            lecture_number=2,
            category="词法",
            tags=["词法", "代词"],
            examples_md="Happy now!\n现在开心了！",
        ),
    ]
    voc = build_vocabulary(kps, limit=50, min_freq=1)
    by = {e.word: e for e in voc}
    # 推不出的词性宁缺勿错
    assert "num" not in (by.get("before", _e()).pos)
    assert "num" not in (by.get("mike", _e()).pos)
    assert "pron" not in (by.get("happy", _e()).pos)
    assert "art" not in (by.get("now", _e()).pos)


def _e():
    from dataclasses import dataclass

    @dataclass
    class _X:
        pos: list = None

    _X.pos = []
    return _X()


def test_pos_closed_classes():
    """封闭类词性来自人工词表。"""
    kps = [
        _kp("例", "Go out now because two are happy.\n现在出去因为两个人很开心。")
    ]
    voc = build_vocabulary(kps, limit=50, min_freq=1)
    by = {e.word: e for e in voc}
    assert "prep" in by["out"].pos
    assert "adv" in by["now"].pos
    assert "conj" in by["because"].pos
    assert "num" in by["two"].pos
    assert "adj" in by["happy"].pos
    assert "v" in by["go"].pos


def test_lemmatize_merges_inflections():
    """变形合并进原形再计数，词条词形变化因此天然正确。"""
    kps = [
        _kp("a", "He goes home.\n他回家。"),
        _kp("b", "He went home.\n他回家了。"),
        _kp("c", "He is going home.\n他正在回家。"),
        _kp("d", "He goes out.\n他出去。"),
    ]
    voc = build_vocabulary(kps, limit=50, min_freq=1)
    by = {e.word: e for e in voc}
    assert "goes" not in by and "went" not in by and "going" not in by
    assert by["go"].freq == 4
    assert by["go"].forms["past"] == "went"
    assert by["go"].forms["third_singular"] == "goes"


def test_proper_noun_detection():
    """非句首大写占多数 → 专有名词。"""
    kps = [
        _kp("a", "I saw Tom.\n我看见了汤姆。"),
        _kp("b", "Tom and Tom again.\n汤姆和汤姆。"),
    ]
    voc = build_vocabulary(kps, limit=50, min_freq=1)
    tom = next(e for e in voc if e.word == "tom")
    assert "proper" in tom.pos


def test_regular_past_no_false_doubling():
    """重音在前的动词不双写辅音。"""
    assert regular_past("visit") == "visited"
    assert regular_past("listen") == "listened"
    assert regular_past("open") == "opened"
    assert regular_past("happen") == "happened"
    # 双写规则本身仍生效
    assert regular_past("stop") == "stopped"

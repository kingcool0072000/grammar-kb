"""词形变化选题：ok→oked 这类不能变形的词不占词形段。

crayon / crowd 这类名词活用也不占动词屈折：考复数，不考三单。
"""
from __future__ import annotations

import json
import random

from grammar_kb.vocab_paper import (
    DEFAULT_RATIOS,
    _VERB_FORM_KEYS,
    _build_sections,
    _pick_form,
    _rich_rows,
    _short_senses,
    render_new_paper_html,
)

_QUOTA = {"form": 4, "spell": 0, "ctx": 0, "zh2en": 0, "en2zh": 0}


def _word(word, pos, forms, level=0, gloss="释义"):
    return {
        "word": word,
        "level": level,
        "pos": json.dumps(pos, ensure_ascii=False),
        "pos_list": list(pos),
        "pos_cn": "",
        "gloss": gloss,
        "gloss_clean": gloss,
        "forms": dict(forms),
        "example": {"en": "", "zh": ""},
    }


def _form_items(words, quota=None, seed=0):
    sections = _build_sections(words, dict(quota or _QUOTA), random.Random(seed))
    return next((s["items"] for s in sections if s["type"] == "form"), [])


def test_form_skips_ok_past_even_when_tagged_verb():
    """回归：ok / okay 被标成动词或写进 extra.forms 时，也不出「过去式 oked」。"""
    ok = _word("ok", ["v"], {
        "past": "oked", "past_participle": "oked",
        "present_participle": "oking", "third_singular": "oks", "plural": "oks",
    }, level=0)
    okay = _word("okay", ["v", "convention"], {"past": "okayed"}, level=0, gloss="好的")
    play = _word("play", ["v"], {"past": "played", "present_participle": "playing"}, level=5)
    items = _form_items([ok, okay, play], quota={"form": 3, "spell": 0, "ctx": 0,
                                                 "zh2en": 0, "en2zh": 0})
    assert [it["word"] for it in items] == ["play"]
    assert items[0]["answer"] in {"played", "playing"}
    assert _pick_form(ok, random.Random(0)) == (None, None)
    assert _pick_form(okay, random.Random(0)) == (None, None)


def test_rich_rows_rule_past_of_ok_is_not_examined():
    """词性空时 word_forms 会硬造 oked；落库的 extra.forms 同样不能进词形段。"""
    rows = [
        {"word": "ok", "level": 0, "pos": "[]", "gloss": "好；行",
         "extra": "{}", "example": "{}"},
        {"word": "okay", "level": 1, "pos": '["convention"]', "gloss": "好的",
         "extra": json.dumps({"forms": {"past": "okayed"}}), "example": "{}"},
        {"word": "play", "level": 1, "pos": '["v"]', "gloss": "玩",
         "extra": "{}", "example": "{}"},
    ]
    words = _rich_rows(rows)
    ok = next(w for w in words if w["word"] == "ok")
    assert ok["forms"].get("past", "").lower() in {"", "oked"}  # 规则层可以造，选题不能用
    items = _form_items(words, quota={"form": 3, "spell": 0, "ctx": 0, "zh2en": 0, "en2zh": 0})
    assert [it["word"] for it in items] == ["play"]
    assert all("ok" not in it["answer"].lower() for it in items)


def test_form_quota_filled_by_real_words_not_left_empty():
    """垃圾词占着简单词名额时，用后面的动词/名词补满；不够才缩短该段。"""
    junk = [
        _word("ok", ["v"], {"past": "oked"}, level=0),
        _word("oh", ["interj"], {"past": "ohed"}, level=0),
        _word("hi", ["interj", "n"], {"plural": "his", "past": "hied"}, level=0),
        _word("wow", ["interj"], {"past": "wowed"}, level=1),
    ]
    verbs = [
        _word(w, ["v"], {"past": w + "ed"}, level=6)
        for w in ("play", "open", "want", "look")
    ]
    items = _form_items(junk + verbs, quota={"form": 3, "spell": 0, "ctx": 0,
                                             "zh2en": 0, "en2zh": 0})
    assert [it["word"] for it in items] == ["play", "open", "want"]
    assert all(it["answer"].endswith("ed") and not it["answer"].startswith("ok")
               for it in items)


def test_form_section_shrinks_when_only_junk_remains():
    words = [
        _word("ok", ["v"], {"past": "oked"}),
        _word("okay", ["v"], {"past": "okayed"}),
    ]
    sections = _build_sections(words, _QUOTA, random.Random(0))
    assert all(s["type"] != "form" for s in sections)
    kept = [it["word"] for s in sections for it in s["items"]]
    assert kept == ["ok", "okay"]  # 仍可做中译英/英译中，只是不考词形


def test_short_irregular_verb_still_examined():
    go = _word("go", ["v"], {
        "past": "went", "past_participle": "gone",
        "present_participle": "going", "third_singular": "goes",
    }, level=0)
    items = _form_items([go, _word("ok", ["v"], {"past": "oked"})],
                        quota={"form": 2, "spell": 0, "ctx": 0, "zh2en": 0, "en2zh": 0})
    assert [it["word"] for it in items] == ["go"]
    assert items[0]["answer"] in {"went", "gone", "going", "goes"}


def test_identical_past_is_junk_but_real_participle_remains():
    put_same = _word("put", ["v"], {"past": "put", "past_participle": "put"})
    assert _pick_form(put_same, random.Random(1)) == (None, None)
    put = _word("put", ["v"], {
        "past": "put", "past_participle": "put", "present_participle": "putting",
    })
    key, ans = _pick_form(put, random.Random(1))
    assert ans == "putting"
    assert key == "present_participle"


def test_skips_uninflectable_adj_adv_keeps_gradable_adjective():
    big = _word("big", ["adj"], {"comparative": "bigger", "superlative": "biggest"})
    # -ly 副词即使被标成形容词，比较级 quicklier 也不考
    quickly = _word("quickly", ["adv", "adj"], {
        "comparative": "quicklier", "superlative": "quickliest",
    })
    beautiful = _word("beautiful", ["adj"], {
        "comparative": "beautifuler", "superlative": "beautifulest",
    })
    very = _word("very", ["adv"], {"comparative": "verier", "past": "veried"})
    items = _form_items(
        [quickly, beautiful, very, big],
        quota={"form": 4, "spell": 0, "ctx": 0, "zh2en": 0, "en2zh": 0},
    )
    assert [it["word"] for it in items] == ["big"]
    assert items[0]["answer"] in {"bigger", "biggest"}


def test_noun_plural_fills_when_no_verb_and_prefers_verb_over_noun():
    box = _word("box", ["n"], {"plural": "boxes"}, level=4)
    apple = _word("apple", ["n"], {"plural": "apples"}, level=0)
    play = _word("play", ["v", "n"], {"past": "played", "plural": "plays"}, level=5)
    # 有动词时动词优先，不把简单名词占满词形段
    items = _form_items([apple, box, play], quota={"form": 1, "spell": 0, "ctx": 0,
                                                   "zh2en": 0, "en2zh": 0})
    assert [it["word"] for it in items] == ["play"]
    assert items[0]["form_key"] == "past"
    # 没有动词时，名词复数可以考，简单词仍排前
    nouns = _form_items([box, apple], quota={"form": 2, "spell": 0, "ctx": 0,
                                             "zh2en": 0, "en2zh": 0})
    assert [it["word"] for it in nouns] == ["apple", "box"]
    assert [it["answer"] for it in nouns] == ["apples", "boxes"]


def _noun_verb(word, forms, gloss):
    return _word(word, ["n", "v"], forms, level=2, gloss=gloss)


def test_crayon_and_crowd_are_not_verbed():
    """名词当头的活用不考三单：crayon / crowd 只出复数。"""
    crayon = _noun_verb("crayon", {
        "plural": "crayons", "past": "crayoned", "past_participle": "crayoned",
        "present_participle": "crayoning", "third_singular": "crayons",
    }, "n. 蜡笔，有色粉笔；v. 以蜡笔作画，用颜色粉笔画")
    crowd = _noun_verb("crowd", {
        "plural": "crowds", "past": "crowded", "past_participle": "crowded",
        "present_participle": "crowding", "third_singular": "crowds",
    }, "n. 群众,一伙;一堆,许多,大众；v. 拥挤,挤满,挤进")
    for seed in range(6):
        assert _pick_form(crayon, random.Random(seed)) == ("plural", "crayons")
        assert _pick_form(crowd, random.Random(seed)) == ("plural", "crowds")
    items = _form_items(
        [crayon, crowd],
        quota={"form": 2, "spell": 0, "ctx": 0, "zh2en": 0, "en2zh": 0},
    )
    assert {it["word"]: it["form_key"] for it in items} == {
        "crayon": "plural", "crowd": "plural",
    }
    assert {it["answer"] for it in items} == {"crayons", "crowds"}
    assert all(it["form_key"] not in _VERB_FORM_KEYS for it in items)
    assert all(it["pos_cn"] == "n. 名词" for it in items)


def test_real_verbs_still_inflect_when_noun_is_also_listed():
    """课堂动词、以及名词在前但不是临时活用的词，仍考时态。"""
    play = _word("play", ["n", "v"], {
        "past": "played", "third_singular": "plays", "plural": "plays",
    }, gloss="n. 游戏；v. 玩，演奏")
    promise = _word("promise", ["n", "v"], {
        "past": "promised", "third_singular": "promises", "plural": "promises",
    }, gloss="n. 许诺，允诺；v. 允诺，许诺")
    join = _word("join", ["v", "n"], {
        "past": "joined", "third_singular": "joins", "plural": "joins",
    }, gloss="v. 参加；n. 连接")
    for w in (play, promise, join):
        keys = set()
        for seed in range(6):
            key, _ans = _pick_form(w, random.Random(seed))
            keys.add(key)
        assert keys <= set(_VERB_FORM_KEYS)
        assert keys


def test_default_form_quota_is_the_light_section():
    assert sum(DEFAULT_RATIOS.values()) == 50
    assert DEFAULT_RATIOS["form"] == 6
    assert DEFAULT_RATIOS["form"] < DEFAULT_RATIOS["zh2en"]
    assert DEFAULT_RATIOS["form"] < DEFAULT_RATIOS["en2zh"]
    assert DEFAULT_RATIOS["form"] < DEFAULT_RATIOS["ctx"]


def test_zh2en_gloss_splits_into_short_senses():
    crayon = _short_senses("n. 蜡笔，有色粉笔；v. 以蜡笔作画，用颜色粉笔画")
    assert crayon == "蜡笔；以蜡笔作画；有色粉笔"
    assert "v." not in crayon and "用颜色粉笔画" not in crayon
    crowd = _short_senses("n. 群众,一伙;一堆,许多,大众；v. 拥挤,挤满,挤进")
    assert crowd == "群众；一堆；拥挤"
    assert "挤满" not in crowd and "v." not in crowd
    apple = _short_senses(
        "n. 苹果，苹果树，苹果似的东西；[美俚]炸弹，手榴弹，（棒球的）球")
    assert apple == "苹果；苹果树；苹果似的东西"
    assert "炸弹" not in apple
    assert _short_senses("美丽的") == "美丽的"
    assert _short_senses(crayon) == crayon


def test_zh2en_html_uses_short_senses_and_en2zh_hides_pos():
    paper = {
        "level": 1, "paper_id": "P-SENSE-1", "user": "malin",
        "sections": [
            {"type": "zh2en", "title": "中译英", "items": [{
                "word": "crayon",
                "pos_cn": "n. 名词 / v. 动词",
                "gloss": "n. 蜡笔，有色粉笔；v. 以蜡笔作画，用颜色粉笔画",
            }]},
            {"type": "en2zh", "title": "英译中", "items": [{
                "word": "crowd",
                "pos_cn": "n. 名词 / v. 动词",
                "gloss": "n. 群众；v. 拥挤",
            }]},
        ],
    }
    html = render_new_paper_html(paper)
    zh, en = html.split("英译中", 1)
    assert "蜡笔" in zh and "以蜡笔作画" in zh and "有色粉笔" in zh
    assert 'class="sense"' in zh and "；" in zh
    assert "用颜色粉笔画" not in html
    assert "v. 以蜡笔作画" not in html
    question, _, _answers = en.partition('class="answers"')
    assert "crowd" in question
    assert "名词" not in question and "动词" not in question
    assert 'class="pos"' not in question

"""考卷模板渲染与录音转码分支（Phase 2/3 版式出代码后的回归锁）。

两套考卷 HTML 此前内嵌 Python f-string，2026-11 迁到
grammar_kb/templates/*.html（string.Template），迁移时做过字节级一致
验证——本文件把「版式关键要素」固化成断言，防止后续改模板丢区块。

词汇卷渲染以 sections 驱动（vocab_paper.render_new_paper_html）：
存什么渲染什么；旧版 dict（zh2en/en2zh/spell 顶层键）走兼容转换。
"""
from __future__ import annotations

import base64

from grammar_kb.audioutil import MAX_AUDIO_B64, transcode_for_safari
from grammar_kb.vocab_paper import render_new_paper_html


def _form_item(word, answer=None):
    return {"word": word, "pos_cn": "v. 动词", "gloss": f"{word}的释义",
            "form_key": "past", "form_label": "过去式", "answer": answer or f"{word}ed"}


def _spell_item(word, blanked=None, answer=None):
    return {"word": word, "pos_cn": "v. 动词", "gloss": f"{word}的释义",
            "blanked": blanked or (word[0] + "_" * (len(word) - 1)),
            "answer": answer or word[1:]}


def _ctx_item(word):
    # sentence 已是 generate_paper 定位词形后的挖空串（答案处替换为 ____）
    return {"word": word, "pos_cn": "v. 动词", "gloss": f"{word}的释义",
            "sentence": "We ____ it together.", "answer": word,
            "zh": f"{word}的例句"}


def _zh2en_item(word):
    return {"word": word, "pos_cn": "v. 动词", "gloss": f"{word}的释义"}


def _en2zh_item(word):
    return {"word": word, "pos_cn": "v. 动词", "gloss": f"{word}的释义"}


def _new_paper(level=1):
    """sections 形态（generate_paper 的产物）：五题型段，每段 2 题
    （共 10 题 → 每题 10 分，分值行可整除断言）。"""
    return {
        "level": level, "paper_id": "P-TEST-001", "user": "malin",
        "sections": [
            {"type": "form", "title": "词形变化",
             "items": [_form_item("go", "went"), _form_item("eat", "ate")]},
            {"type": "spell", "title": "拼写挖空",
             "items": [_spell_item("apple"), _spell_item("orbit")]},
            {"type": "ctx", "title": "例句填空",
             "items": [_ctx_item("read"), _ctx_item("write")]},
            {"type": "zh2en", "title": "中译英",
             "items": [_zh2en_item("gather"), _zh2en_item("spread")]},
            {"type": "en2zh", "title": "英译中",
             "items": [_en2zh_item("borrow"), _en2zh_item("lend")]},
        ],
    }


# ---- 哈一词汇卷（vocab_paper，五题型段 + 答案页）----

def test_new_paper_level_branch_renders_all_sections():
    html = render_new_paper_html(_new_paper(1), title="")
    assert html.startswith("<!DOCTYPE html>")
    assert "哈一词汇考试 · L1 级" in html
    assert "卷号 P-TEST-001" in html
    # 五段标题 + 题数提示，按 sections 顺序编号
    for head in ("一、词形变化（2 题 · 写出指定形式）",
                 "二、拼写挖空（2 题 · 在空格处填写字母）",
                 "三、例句填空（2 题 · 根据句意填空）",
                 "四、中译英（2 题 · 写出英文）",
                 "五、英译中（2 题 · 写出中文意思）"):
        assert head in html, f"版式缺要素：{head}"
    # 分值行：每题分 = 100 / 总题数
    assert "共 10 题 · 每题 10 分" in html
    # 题面要素：变形题给词面与要求、拼写题给挖空串、例句题给挖空句
    assert "的过去式" in html and "a____" in html and "We ____ it together." in html
    # 答案页：独立成页，每段标题 + 答案串
    assert "答案页" in html
    for head in ("一、词形变化", "二、拼写挖空", "三、例句填空",
                 "四、中译英", "五、英译中"):
        assert head in html, f"答案页缺段：{head}"
    assert "1.went" in html and "1.pple" in html and "1.read" in html
    assert "1.gather（v. 动词）" in html and "1.borrow的释义" in html


def test_new_paper_mixed_branch():
    html = render_new_paper_html(_new_paper(-1))
    assert "已学全部混合卷（malin）" in html


def test_new_paper_legacy_dict_still_renders():
    """旧版形态（zh2en/en2zh/spell 顶层键）兼容渲染：转成三段。"""
    legacy = {"level": 2, "paper_id": "P-OLD-001",
              "zh2en": [_zh2en_item(f"zw{i}") for i in range(20)],
              "en2zh": [_en2zh_item(f"ew{i}") for i in range(20)],
              "spell": [_spell_item(f"sw{i}") for i in range(10)]}
    html = render_new_paper_html(legacy)
    assert "卷号 P-OLD-001" in html
    for head in ("一、中译英（20 题", "二、英译中（20 题",
                 "三、拼写挖空（10 题"):
        assert head in html, f"版式缺要素：{head}"
    assert "共 50 题 · 每题 2 分" in html
    assert "答案页" in html


# ---- 录音转码（reading/video 共用 audioutil）----

def test_transcode_non_webm_passthrough():
    b64 = base64.b64encode(b"audio-bytes").decode()
    assert transcode_for_safari(b64, "audio/mp4") == (b64, "audio/mp4")
    assert transcode_for_safari(b64, "") == (b64, "")


def test_transcode_webm_without_ffmpeg_passthrough(monkeypatch):
    """无 ffmpeg 环境（教师 Windows 裸机等）原样存，Chrome 端仍可播。"""
    monkeypatch.setattr("shutil.which", lambda name: False)
    b64 = base64.b64encode(b"webm-bytes").decode()
    assert transcode_for_safari(b64, "audio/webm") == (b64, "audio/webm")


def test_max_audio_b64_guard():
    """体积上限常量被 reading/video 两处提交校验引用，锁住量纲（字节）。"""
    assert MAX_AUDIO_B64 == 12 * 1024 * 1024

"""考卷模板渲染与录音转码分支（Phase 2/3 版式出代码后的回归锁）。

两套考卷 HTML 此前内嵌 Python f-string，2026-11 迁到
grammar_kb/templates/*.html（string.Template），迁移时做过字节级一致
验证——本文件把「版式关键要素」固化成断言，防止后续改模板丢区块。
"""
from __future__ import annotations

import base64


from grammar_kb.audioutil import MAX_AUDIO_B64, transcode_for_safari
from grammar_kb.vocab_exam import render_paper_html
from grammar_kb.vocab_paper import render_new_paper_html


def _w(word, meaning="释义测试", gloss="释义测试", pos="n."):
    return {"word": word, "meaning": meaning, "gloss": gloss, "pos_cn": pos,
            "blanked": word[:2] + "__", "spell_answer": word}


def _exam_paper():
    return {"level": 1, "seed": 0, "sections": {
        "dictation": [_w(f"dw{i}") for i in range(15)],
        "zh2en": [_w(f"zw{i}") for i in range(15)],
        "en2zh": [_w(f"ew{i}") for i in range(15)],
        "spell": [_w(f"sw{i}") for i in range(5)],
    }}


def _new_paper(level=1):
    return {"level": level, "paper_id": "P-TEST-001", "user": "malin",
            "zh2en": [_w(f"zw{i}") for i in range(20)],
            "en2zh": [_w(f"ew{i}") for i in range(20)],
            "spell": [_w(f"sw{i}") for i in range(10)]}


# ---- L 级考卷（vocab_exam，四题型 + 答案页）----

def test_exam_paper_has_all_sections_and_answer_page():
    html = render_paper_html(_exam_paper(), student="王小明")
    assert html.startswith("<!DOCTYPE html>")
    for kw in ("一、听写", "二、中译英", "三、英译中", "四、拼写补全",
               "答案页（家长保管）", "得分：＿＿＿＿",
               "姓名：王小明", "L1 → L2", "80 分解锁 L2"):
        assert kw in html, f"版式缺要素：{kw}"


def test_exam_paper_blank_student_falls_back():
    html = render_paper_html(_exam_paper(), student="")
    assert "姓名：＿＿＿＿＿" in html


# ---- 哈一词汇卷（vocab_paper，三题型 + 答案页）----

def test_new_paper_level_branch():
    html = render_new_paper_html(_new_paper(1), title="")
    assert "哈一词汇考试 · L1 级" in html
    assert "卷号 P-TEST-001" in html
    for kw in ("一、中译英", "二、英译中", "三、拼写", "答案页"):
        assert kw in html, f"版式缺要素：{kw}"


def test_new_paper_mixed_branch():
    html = render_new_paper_html(_new_paper(-1))
    assert "已学全部混合卷（malin）" in html


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

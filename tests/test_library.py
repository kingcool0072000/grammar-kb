"""泛读馆后端测试：epub 解析 / 词频粗筛 / glm JSON 容错 / API 行为与学生白名单。

环境变量必须在 create_app 之前设置（LibraryStore 构造时读取）。
"""
import io
import json
import os
import re
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")  # multipart-form 上传需要 python-multipart

from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb import glm  # noqa: E402
from grammar_kb.epub_parse import parse_epub  # noqa: E402
from grammar_kb.server import create_app  # noqa: E402
from grammar_kb.wordfreq import DIFFICULTY_LEVELS, extract_hard_words  # noqa: E402


# ---- epub 合成夹具：封面页 + 目录页 + 编号章 x2 + 版权页（近空页） ----

_COVER_XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Cover</title></head>
<body><div><img src="cover.png" alt="cover"/></div></body></html>"""

_TOC_XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Contents</title></head>
<body><nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops">
<ol><li><a href="ch1.xhtml">1 The Beginning</a></li>
<li><a href="ch2.xhtml">2 The Sequel</a></li></ol></nav></body></html>"""


def _chapter_xhtml(num: int, title: str, sentences: int = 40) -> str:
    paras = "\n".join(
        f"<p>Sentence {num}.{i} the quick brown fox jumps over lazy dogs repeatedly.</p>"
        for i in range(sentences)
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title></head>
<body><h1>{num} {title}</h1>{paras}</body></html>"""


_COPYRIGHT_XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Copyright</title></head>
<body><p>Sample Press</p></body></html>"""


def build_epub_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        z.writestr("OEBPS/content.opf", """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>Test Book</dc:title>
<dc:creator>Alice</dc:creator><dc:creator>Bob</dc:creator>
<meta name="cover" content="cover-img"/>
</metadata>
<manifest>
<item id="cover-img" href="cover.png" media-type="image/png"/>
<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>
<item id="toc" href="toc.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
<item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
<item id="cr" href="copyright.xhtml" media-type="application/xhtml+xml"/>
</manifest>
<spine>
<itemref idref="cover"/><itemref idref="toc"/>
<itemref idref="c1"/><itemref idref="c2"/><itemref idref="cr"/>
</spine>
</package>""")
        z.writestr("OEBPS/cover.xhtml", _COVER_XHTML)
        z.writestr("OEBPS/toc.xhtml", _TOC_XHTML)
        z.writestr("OEBPS/ch1.xhtml", _chapter_xhtml(1, "The Beginning"))
        z.writestr("OEBPS/ch2.xhtml", _chapter_xhtml(2, "The Sequel"))
        z.writestr("OEBPS/copyright.xhtml", _COPYRIGHT_XHTML)
        z.writestr("OEBPS/cover.png", b"\x89PNG\r\n\x1a\nfakepng")
    return buf.getvalue()


EPUB_BYTES = build_epub_bytes()


# ---- 客户端夹具（环境变量在 create_app 前设） ----


def _login(client, user, password="123456"):
    r = client.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


@pytest.fixture()
def teacher(tmp_path):
    """教师客户端（独立 library.db / 文件目录 / users）。"""
    os.environ["GRAMMAR_KB_USERS"] = str(tmp_path / "users.json")
    os.environ["GRAMMAR_KB_AUTH_SECRET"] = str(tmp_path / "secret.key")
    os.environ["GRAMMAR_KB_LIBRARY_DB"] = str(tmp_path / "library.db")
    os.environ["GRAMMAR_KB_LIBRARY_DIR"] = str(tmp_path / "files")
    app = create_app()
    with TestClient(app) as c:
        c.headers.update(_login(c, "teacher"))
        yield c


@pytest.fixture()
def student(tmp_path):
    os.environ["GRAMMAR_KB_USERS"] = str(tmp_path / "users.json")
    os.environ["GRAMMAR_KB_AUTH_SECRET"] = str(tmp_path / "secret.key")
    os.environ["GRAMMAR_KB_LIBRARY_DB"] = str(tmp_path / "library.db")
    os.environ["GRAMMAR_KB_LIBRARY_DIR"] = str(tmp_path / "files")
    app = create_app()
    with TestClient(app) as c:
        c.headers.update(_login(c, "malin"))
        yield c


@pytest.fixture()
def book_id(teacher):
    r = teacher.post(
        "/library/books",
        files={"file": ("test.epub", EPUB_BYTES, "application/epub+zip")},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


# ---- epub 解析：辅助页归类 + 近空页合并 + 元数据 ----


class TestEpubParse:
    def test_metadata_and_cover(self):
        p = parse_epub(EPUB_BYTES)
        assert p.title == "Test Book"
        assert p.author == "Alice, Bob"  # 多 creator 逗号连
        assert p.cover is not None and p.cover.ext == "png"
        assert p.cover.buffer.startswith(b"\x89PNG")

    def test_auxiliary_and_merge(self):
        p = parse_epub(EPUB_BYTES)
        # 开头空页（封面）保留为首章；目录页并入第 1 个正文章（合并后文本以目录内容开头）
        # 版权页（<30 词）并入末章 → 最终 3 章
        assert len(p.chapters) == 3
        first, second, third = p.chapters
        assert first.auxiliary is True  # 封面页（合并后保留的空页）
        assert second.auxiliary is False  # 正文编号章
        assert third.auxiliary is False  # 正文编号章（吸收了尾部版权页）
        # 目录页文本被并入第 2 章（原 spine 第 1 项）：正文前先出现 TOC 的两个链接标签
        assert "1 The Beginning" in second.title
        assert second.text.startswith("1 The Beginning\n\n2 The Sequel\n\n")
        assert second.spine_index == 2  # 章入口 = 正文章自己的 spine 项
        # 尾部版权页并入末章
        assert "Sample Press" in third.text
        assert third.word_count > 0
        # idx 重排连续
        assert [c.idx for c in p.chapters] == [0, 1, 2]

    def test_title_priority_toc_over_heading(self):
        # TOC 标签 "1 The Beginning" 优先于 h1 内容 "1 The Beginning"（一致）；
        # 无 TOC 命中的章用 h1：改用无 nav 的最小书验证
        p = parse_epub(EPUB_BYTES)
        assert p.chapters[1].title == "1 The Beginning"

    def test_not_a_zip(self):
        from grammar_kb.epub_parse import EpubParseError

        with pytest.raises(EpubParseError):
            parse_epub(b"not a zip at all")


# ---- wordfreq：阈值与专有名词跳过 ----


class TestWordfreq:
    def test_difficulty_levels(self):
        assert DIFFICULTY_LEVELS == {"L1": 4000, "L2": 8000, "L3": 9895}

    def test_common_words_skipped_and_rare_kept(self):
        # banana 排名 9295：L1(>4000)/L2(>8000) 算难，L3(表外才算)不算
        text = "The banana ripened quietly in the dim pantry."
        l1 = extract_hard_words(text, "L1")
        l2 = extract_hard_words(text, "L2")
        l3 = extract_hard_words(text, "L3")
        words_l1 = [w["word"] for w in l1]
        words_l2 = [w["word"] for w in l2]
        assert "banana" in words_l1
        assert "banana" in words_l2
        assert "banana" not in [w["word"] for w in l3]
        # 高频词不进候选：the(排名 1)、dark(1630) 均为常用词
        assert "the" not in words_l1 and "dark" not in words_l1
        # L3（表外才算难）：pantry/ripened 表外 → 候选；quietly 可归并回 quiet(4164) 不算
        l3_words = [w["word"] for w in l3]
        assert "pantry" in l3_words and "ripened" in l3_words
        assert "quietly" not in l3_words
        # dim(9287) 在 L2 候选，但 quiet 归并后 quietly 只在 L1 算难
        assert "dim" in words_l2 and "quietly" in words_l1 and "quietly" not in words_l2

    def test_proper_noun_and_contraction_skipped(self):
        text = "Yesterday Xanadu shimmered. They didn't believe Xanadu legends."
        words = [w["word"] for w in extract_hard_words(text, "L2")]
        assert "xanadu" not in words  # 非句首大写 = 疑似专有名词
        assert "didn't" not in words  # 缩写跳过
        # yesterday 排名 2306 属常用词，也不在
        assert "yesterday" not in words

    def test_sentence_start_capital_not_skipped(self):
        # 句首大写不算专有名词线索：Xylophone（表外词）在句首应保留
        text = "Xylophones clattered everywhere."
        words = [w["word"] for w in extract_hard_words(text, "L2")]
        assert "xylophones" in words

    def test_limit_and_sort(self):
        text = " ".join(f"quizzical{i} xylophones rambunctious." for i in range(1, 8))
        out = extract_hard_words(text, "L2", limit=2)
        assert len(out) == 2
        # 同频按字母序
        assert [w["word"] for w in out] == sorted(w["word"] for w in out)
        # 频次降序：出现 7 次的排最前
        text2 = "xylophones clattered. a quizzical xylophones echo. xylophones again."
        words = [w["word"] for w in extract_hard_words(text2, "L2")]
        assert words[0] == "xylophones"


# ---- glm.extract_json：容错（不发网络请求） ----


class TestExtractJson:
    def test_plain(self):
        assert glm.extract_json('{"a": 1}') == {"a": 1}

    def test_think_block_stripped(self):
        text = "<think>reasoning about {braces}</think>\n{\"a\": {\"b\": 2}}"
        assert glm.extract_json(text) == {"a": {"b": 2}}

    def test_code_fence(self):
        text = "```json\n{\"a\": [1, 2]}\n```"
        assert glm.extract_json(text) == {"a": [1, 2]}

    def test_nested_braces_and_strings_with_braces(self):
        text = '前言 {"a": {"b": "he said {not json} \\" ok"}, "c": 1} 尾巴'
        assert glm.extract_json(text)["a"]["b"].startswith("he said")

    def test_prose_before_and_after(self):
        text = '好的，这是结果：{"word":"envy"} 请查收'
        assert glm.extract_json(text) == {"word": "envy"}

    def test_first_complete_object_wins(self):
        text = '{"a": 1} then {"b": 2}'
        assert glm.extract_json(text) == {"a": 1}

    def test_broken_raises(self):
        with pytest.raises(glm.GlmError):
            glm.extract_json("no json here at all")


# ---- API：上传 / 书架 / 章节 / 进度 ----


class TestBooksApi:
    def test_upload_and_list(self, teacher):
        r = teacher.post(
            "/library/books",
            files={"file": ("book.epub", EPUB_BYTES, "application/epub+zip")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["code"] == 0
        book = body["data"]
        assert book["title"] == "Test Book"
        assert book["author"] == "Alice, Bob"
        assert book["hasCover"] is True
        assert book["chapterCount"] == 3
        assert book["preppedCount"] == 0
        assert book["percent"] is None
        assert book["fileSize"] == len(EPUB_BYTES)

        lst = teacher.get("/library/books").json()["data"]
        assert len(lst["books"]) == 1
        assert lst["books"][0]["id"] == book["id"]
        assert lst["stats"] == {"reading": 0, "finished": 0, "totalSeconds": 0}

    def test_upload_rejects_non_epub(self, teacher):
        r = teacher.post(
            "/library/books", files={"file": ("book.pdf", b"%PDF-1.4", "application/pdf")}
        )
        assert r.status_code == 400
        assert "epub" in r.json()["message"]

    def test_upload_rejects_garbage_zip(self, teacher):
        r = teacher.post(
            "/library/books",
            files={"file": ("bad.epub", b"garbage-not-a-zip", "application/epub+zip")},
        )
        assert r.status_code == 422
        assert "解压" in r.json()["message"]

    def test_file_and_cover(self, teacher, book_id):
        f = teacher.get(f"/library/books/{book_id}/file")
        assert f.status_code == 200
        assert f.headers["content-type"] == "application/epub+zip"
        assert f.content == EPUB_BYTES
        c = teacher.get(f"/library/books/{book_id}/cover")
        assert c.status_code == 200
        assert c.headers["content-type"].startswith("image/png")
        assert teacher.get("/library/books/999/cover").status_code == 404

    def test_chapters_list(self, teacher, book_id):
        data = teacher.get(f"/library/books/{book_id}/chapters").json()["data"]
        cs = data["chapters"]
        assert [c["idx"] for c in cs] == sorted(c["idx"] for c in cs)
        assert len(cs) == 3
        by_idx = {c["idx"]: c for c in cs}
        assert by_idx[0]["skipPrep"] is True  # 封面辅助页
        assert by_idx[1]["skipPrep"] is False
        assert by_idx[1]["prepped"] is False
        assert by_idx[1]["spineIndex"] == 2

    def test_skip_toggle(self, teacher, book_id):
        r = teacher.put(f"/library/books/{book_id}/chapters/1/skip", json={"skip": True})
        assert r.json()["data"] == {"idx": 1, "skipPrep": True}
        cs = teacher.get(f"/library/books/{book_id}/chapters").json()["data"]["chapters"]
        assert next(c for c in cs if c["idx"] == 1)["skipPrep"] is True
        r = teacher.put(f"/library/books/{book_id}/chapters/1/skip", json={"skip": False})
        assert r.json()["data"] == {"idx": 1, "skipPrep": False}

    def test_open_and_progress_roundtrip(self, teacher, book_id):
        assert teacher.post(f"/library/books/{book_id}/open").json()["data"] == {"ok": True}
        listed = teacher.get("/library/books").json()["data"]["books"][0]
        assert listed["lastOpenedAt"] is not None

        r = teacher.put(
            f"/library/books/{book_id}/progress",
            json={"cfi": "epubcfi(/6/4)", "chapterIndex": 1, "percent": 42.5,
                  "readingSecondsDelta": 120},
        )
        assert r.json()["data"] == {"percent": 42.5, "readingSeconds": 120}
        prog = teacher.get(f"/library/books/{book_id}/progress").json()["data"]
        assert prog["cfi"] == "epubcfi(/6/4)"
        assert prog["chapterIndex"] == 1
        assert prog["percent"] == 42.5
        assert prog["readingSeconds"] == 120

        # 未开始的书 progress 为 null
        r = teacher.post(
            "/library/books",
            files={"file": ("b2.epub", EPUB_BYTES, "application/epub+zip")},
        )
        bid2 = r.json()["data"]["id"]
        assert teacher.get(f"/library/books/{bid2}/progress").json()["data"] is None

        # stats：reading 一本
        stats = teacher.get("/library/books").json()["data"]["stats"]
        assert stats["reading"] == 1 and stats["finished"] == 0 and stats["totalSeconds"] == 120

    def test_progress_clamped_and_accumulates(self, teacher, book_id):
        r = teacher.put(
            f"/library/books/{book_id}/progress",
            json={"cfi": "x", "chapterIndex": 0, "percent": 150, "readingSecondsDelta": 99999},
        ).json()["data"]
        assert r["percent"] == 100 and r["readingSeconds"] == 3600
        r2 = teacher.put(
            f"/library/books/{book_id}/progress",
            json={"cfi": "y", "chapterIndex": 2, "percent": -3, "readingSecondsDelta": 30},
        ).json()["data"]
        assert r2["percent"] == 0 and r2["readingSeconds"] == 3630
        # 最终 percent=0：既不在读（0<p<100）也非读完
        stats = teacher.get("/library/books").json()["data"]["stats"]
        assert stats["finished"] == 0 and stats["reading"] == 0 and stats["totalSeconds"] == 3630

    def test_delete_book(self, teacher, book_id, tmp_path):
        lib_dir = Path(os.environ["GRAMMAR_KB_LIBRARY_DIR"])
        files_before = sorted(p.name for p in (lib_dir / "books").iterdir())
        covers_before = sorted(p.name for p in (lib_dir / "covers").iterdir())
        assert len(files_before) == 1 and len(covers_before) == 1
        r = teacher.delete(f"/library/books/{book_id}")
        assert r.json()["data"] == {"id": book_id}
        assert teacher.get("/library/books").json()["data"]["books"] == []
        assert list((lib_dir / "books").iterdir()) == []
        assert list((lib_dir / "covers").iterdir()) == []
        assert teacher.delete(f"/library/books/{book_id}").status_code == 404


# ---- API：设置 ----


class TestSettings:
    def test_teacher_view(self, teacher):
        data = teacher.get("/library/settings").json()["data"]
        assert data["ai"]["model"] == "glm-4-flash"
        assert data["ai"]["difficulty"] == "L2"
        assert data["ai"]["maxWords"] == 12
        assert data["ai"]["apiKey"] == ""
        assert data["dict"] == {"aiFallback": True}
        assert data["student"] == {"preset": "paper", "fontFamily": "serif", "focus": True}
        assert data["hasKey"] is False
        assert "defaultPrompt" in data and "辅导老师" in data["defaultPrompt"]

    def test_clamp_and_merge(self, teacher):
        r = teacher.put(
            "/library/settings",
            json={"ai": {"apiKey": " k123 ", "maxWords": 99, "difficulty": "L3",
                         "prompt": "自定义"},
                  "dict": {"aiFallback": False}},
        ).json()["data"]
        assert r["ai"]["maxWords"] == 25  # 99 → 夹 25
        assert r["ai"]["apiKey"] == "k123"
        assert r["ai"]["difficulty"] == "L3"
        assert r["dict"]["aiFallback"] is False
        # 部分传：只改 student 不动 ai
        r2 = teacher.put(
            "/library/settings", json={"student": {"preset": "night", "fontFamily": "kai"}}
        ).json()["data"]
        assert r2["ai"]["maxWords"] == 25  # 浅合并保留
        assert r2["student"] == {"preset": "night", "fontFamily": "kai", "focus": True}
        # prompt 空串 = 恢复默认
        r3 = teacher.put("/library/settings", json={"ai": {"prompt": ""}}).json()["data"]
        assert r3["ai"]["prompt"] == ""

    def test_invalid_values_ignored(self, teacher):
        r = teacher.put(
            "/library/settings",
            json={"ai": {"difficulty": "L9", "maxWords": 1}, "student": {"preset": "dark"}},
        ).json()["data"]
        assert r["ai"]["difficulty"] == "L2"  # 非法档位忽略
        assert r["ai"]["maxWords"] == 3  # maxWords=1 → 夹到下限 3（同 TS 的 clamp）
        assert r["student"]["preset"] == "paper"

    def test_models_no_key_400(self, teacher):
        r = teacher.get("/library/models")
        assert r.status_code == 400

    def test_ai_test_no_key_400(self, teacher):
        r = teacher.post("/library/ai/test", json={})
        assert r.status_code == 400

    def test_ai_test_bad_key_502(self, teacher, monkeypatch):
        monkeypatch.setattr(glm, "list_models",
                            lambda key: (_ for _ in ()).throw(RuntimeError("boom")))
        r = teacher.post("/library/ai/test", json={"apiKey": "bad"})
        assert r.status_code == 502


# ---- API：prep（路由顺序 / batch 跳过逻辑 / single 无 key 502） ----


class TestPrep:
    def test_batch_route_not_swallowed_by_param_route(self, teacher):
        # GET /library/prep/batch/1 必须命中任务路由（404 任务不存在），而非参数路由
        r = teacher.get("/library/prep/batch/1")
        assert r.status_code == 404
        assert "任务不存在" in r.json()["message"]

    def test_get_prep_null_when_missing(self, teacher, book_id):
        r = teacher.get(f"/library/prep/{book_id}/1")
        assert r.status_code == 200
        assert r.json()["data"] is None

    def test_batch_all_skipped(self, teacher, book_id):
        # 章 0 是 skip_prep=1（封面辅助页）；先给章 1 造一份预习，再批量 → 全跳过
        from grammar_kb.library import LibraryStore

        store = LibraryStore()  # 沿环境变量指向测试库
        store.put_prep(book_id, 1, {"words": [], "idioms": []}, "m", "L2")
        r = teacher.post("/library/prep/batch", json={"bookId": book_id, "chapterIndexes": [0, 1]})
        assert r.json()["data"] == {"jobId": None, "skipped": True}

    def test_batch_creates_job_and_runs(self, teacher, book_id, monkeypatch):
        calls = []

        def fake_generate(api_key, model, chapter_title, text, candidates, max_words,
                          system_prompt=None):
            calls.append((chapter_title, tuple(candidates), max_words))
            return {"words": [{"word": candidates[0], "phonetic": "/x/", "pos": "n.",
                               "meaning": "测试", "example": "ex", "exampleZh": "例"}],
                    "idioms": []}

        monkeypatch.setattr(glm, "generate_prep", fake_generate)
        teacher.put("/library/settings", json={"ai": {"apiKey": "test-key"}})

        r = teacher.post("/library/prep/batch", json={"bookId": book_id, "chapterIndexes": [1, 2]})
        data = r.json()["data"]
        assert data["skipped"] is False and isinstance(data["jobId"], int)

        # 轮询直到任务完结（后台线程跑）
        import time

        for _ in range(100):
            job = teacher.get(f"/library/prep/batch/{data['jobId']}").json()["data"]
            if job["status"] in ("done", "partial", "interrupted"):
                break
            time.sleep(0.05)
        assert job["status"] == "done", job
        assert job["total"] == 2 and job["done"] == 2 and job["failedIndexes"] == []
        assert job["bookChapters"] == 3
        assert len(calls) == 2  # 两章各调一次 AI

        # 预习已可读
        payload = teacher.get(f"/library/prep/{book_id}/1").json()["data"]
        assert payload is not None and payload["words"]
        # preppedCount / prepped 标记联动
        cs = teacher.get(f"/library/books/{book_id}/chapters").json()["data"]["chapters"]
        assert sum(1 for c in cs if c["prepped"]) == 2

    def test_single_no_key_502(self, teacher, book_id):
        r = teacher.post("/library/prep/single", json={"bookId": book_id, "chapterIndex": 1})
        assert r.status_code == 502
        assert "API Key" in r.json()["message"]

    def test_single_generates_synchronously(self, teacher, book_id, monkeypatch):
        monkeypatch.setattr(
            glm, "generate_prep",
            lambda *a, **k: {"words": [], "idioms": [{"phrase": "give up", "literal": "放弃",
                                                      "actual": "认输", "usage": "口语"}]},
        )
        teacher.put("/library/settings", json={"ai": {"apiKey": "k"}})
        r = teacher.post("/library/prep/single", json={"bookId": book_id, "chapterIndex": 2})
        assert r.status_code == 200 and r.json()["data"] == {"ok": True}
        payload = teacher.get(f"/library/prep/{book_id}/2").json()["data"]
        assert payload["idioms"][0]["phrase"] == "give up"

    def test_single_empty_candidates_still_writes_payload(self, teacher, book_id, monkeypatch):
        # 无候选词的章也写空 payload，避免反复重试
        monkeypatch.setattr(
            glm, "generate_prep",
            lambda *a, **k: pytest.fail("无候选词不应调 AI"),
        )
        teacher.put("/library/settings", json={"ai": {"apiKey": "k"}})
        r = teacher.post("/library/prep/single", json={"bookId": book_id, "chapterIndex": 0})
        assert r.status_code == 200
        payload = teacher.get(f"/library/prep/{book_id}/0").json()["data"]
        assert payload == {"words": [], "idioms": []}


# ---- API：查词（ECDICT 真库；AI 兜底 mock） ----


class TestDict:
    def test_ecdict_hit(self, teacher):
        data = teacher.get("/library/dict/test").json()["data"]["entry"]
        assert data["source"] == "ecdict"
        assert data["word"] == "test"
        assert data["senses"]
        assert data["phonetic"]

    def test_normalization_variants(self, teacher):
        # ECDICT 精确命中优先：carried 本身有词条（形容词）
        data = teacher.get("/library/dict/carried").json()["data"]["entry"]
        assert data["source"] == "ecdict"
        assert data["word"] == "carried"

    def test_variant_fallback_with_mini_dict(self, tmp_path):
        # 迷你词典验证变体回退：perusing 不在典 → -ing 变体（peruse）重查命中
        import sqlite3

        from grammar_kb.dict_db import SCHEMA
        from grammar_kb.library import LibraryStore

        mini = tmp_path / "mini-ecdict.db"
        conn = sqlite3.connect(mini)
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO dict VALUES (?, ?, ?, '', ?, '', 0)",
            ("peruse", "pə'ru:s", json.dumps(["v. 细读"]), json.dumps(["v"])),
        )
        conn.commit()
        conn.close()
        store = LibraryStore(db_path=tmp_path / "lib.db", data_dir=tmp_path / "files",
                             ecdict_path=mini)
        entry = store.dict_lookup("perusing")
        assert entry["source"] == "ecdict"
        assert entry["word"] == "peruse"
        assert entry["senses"][0]["gloss"] == "细读"
        # 不在典也无 AI key → not_found
        nf = store.dict_lookup("zzzqqq")
        assert nf["source"] == "not_found"

    def test_possessive(self, teacher):
        # children's → 所有格归一 → children（词典收录其 pl. 词条）
        data = teacher.get("/library/dict/children's").json()["data"]["entry"]
        assert data["source"] == "ecdict"
        assert data["word"] == "children"

    def test_not_found(self, teacher):
        r = teacher.get("/library/dict/zzzzqqqqxxxx")
        assert r.status_code == 200
        assert r.json()["data"]["entry"]["source"] == "not_found"

    def test_ai_fallback_mocked(self, teacher, monkeypatch):
        teacher.put("/library/settings",
                    json={"ai": {"apiKey": "k"}, "dict": {"aiFallback": True}})

        def fake_explain(api_key, model, word, context=None):
            assert context == "the snorkbungler chattered"
            return {"word": word, "phonetic": "/f/", "pos": "n.", "meaning": "饶舌的人",
                    "example": "What a snorkbungler."}

        monkeypatch.setattr(glm, "explain_word", fake_explain)
        entry = teacher.get(
            "/library/dict/snorkbungler", params={"context": "the snorkbungler chattered"}
        ).json()["data"]["entry"]
        assert entry["source"] == "ai"
        assert entry["meaning"] == "饶舌的人"
        assert entry["example"]

        # 第二次查询命中 dict_cache（source=ai）
        entry2 = teacher.get("/library/dict/snorkbungler").json()["data"]["entry"]
        assert entry2["source"] == "ai"

    def test_ai_fallback_disabled_not_found(self, teacher, monkeypatch):
        teacher.put("/library/settings",
                    json={"ai": {"apiKey": "k"}, "dict": {"aiFallback": False}})
        monkeypatch.setattr(
            glm, "explain_word",
            lambda *a, **k: pytest.fail("aiFallback 关闭时不应调 AI"),
        )
        entry = teacher.get("/library/dict/qwertyzzz").json()["data"]["entry"]
        assert entry["source"] == "not_found"


# ---- 学生白名单与权限 ----


class TestStudentPermissions:
    def test_books_readonly(self, student, teacher, book_id):
        # 读：书架 / 章节 / 文件 / 封面 / 预习 / 进度均可
        assert student.get("/library/books").status_code == 200
        assert student.get(f"/library/books/{book_id}/chapters").status_code == 200
        assert student.get(f"/library/books/{book_id}/file").status_code == 200
        assert student.get(f"/library/books/{book_id}/cover").status_code == 200
        assert student.get(f"/library/prep/{book_id}/1").status_code == 200
        assert student.get(f"/library/books/{book_id}/progress").status_code == 200
        assert student.get("/library/dict/test").status_code == 200

    def test_upload_forbidden(self, student):
        r = student.post(
            "/library/books",
            files={"file": ("book.epub", EPUB_BYTES, "application/epub+zip")},
        )
        assert r.status_code == 403

    def test_delete_forbidden(self, student, book_id):
        assert student.delete(f"/library/books/{book_id}").status_code == 403

    def test_skip_forbidden(self, student, book_id):
        r = student.put(f"/library/books/{book_id}/chapters/1/skip", json={"skip": True})
        assert r.status_code == 403
        # 且未生效
        cs = student.get(f"/library/books/{book_id}/chapters").json()["data"]["chapters"]
        assert next(c for c in cs if c["idx"] == 1)["skipPrep"] is False

    def test_settings_write_forbidden(self, student):
        assert student.put("/library/settings", json={"ai": {"maxWords": 5}}).status_code == 403

    def test_teacher_endpoints_forbidden(self, student):
        assert student.post("/library/prep/batch", json={"bookId": 1, "chapterIndexes": [1]}).status_code == 403
        assert student.get("/library/prep/batch/1").status_code == 403
        assert student.post("/library/prep/single", json={"bookId": 1, "chapterIndex": 1}).status_code == 403
        assert student.get("/library/models").status_code == 403
        assert student.post("/library/ai/test", json={"apiKey": "x"}).status_code == 403

    def test_student_settings_no_leak(self, student, teacher):
        teacher.put("/library/settings", json={"ai": {"apiKey": "SECRET-KEY", "model": "glm-4-plus",
                                                      "prompt": "SECRET-PROMPT"}})
        raw = student.get("/library/settings").text
        assert "SECRET-KEY" not in raw
        assert "SECRET-PROMPT" not in raw
        assert "glm-4-plus" not in raw
        assert "defaultPrompt" not in raw
        data = json.loads(raw)["data"]
        assert set(data.keys()) == {"student", "hasKey"}
        assert data["hasKey"] is True

    def test_student_open_and_progress(self, student, book_id):
        assert student.post(f"/library/books/{book_id}/open").json()["data"] == {"ok": True}
        r = student.put(
            f"/library/books/{book_id}/progress",
            json={"cfi": "s", "chapterIndex": 1, "percent": 150, "readingSecondsDelta": 99999},
        )
        assert r.status_code == 200
        assert r.json()["data"] == {"percent": 100, "readingSeconds": 3600}

    def test_progress_is_per_user(self, student, teacher, book_id):
        teacher.put(f"/library/books/{book_id}/progress",
                    json={"cfi": "t", "chapterIndex": 1, "percent": 10,
                          "readingSecondsDelta": 60})
        student.put(f"/library/books/{book_id}/progress",
                    json={"cfi": "s", "chapterIndex": 2, "percent": 80,
                          "readingSecondsDelta": 30})
        tp = teacher.get(f"/library/books/{book_id}/progress").json()["data"]
        sp = student.get(f"/library/books/{book_id}/progress").json()["data"]
        assert tp["readingSeconds"] == 60 and sp["readingSeconds"] == 30
        assert tp["percent"] == 10 and sp["percent"] == 80
        # 各自的 stats 互不串
        tstats = teacher.get("/library/books").json()["data"]["stats"]
        sstats = student.get("/library/books").json()["data"]["stats"]
        assert tstats["reading"] == 1 and tstats["totalSeconds"] == 60
        assert sstats["reading"] == 1 and sstats["totalSeconds"] == 30

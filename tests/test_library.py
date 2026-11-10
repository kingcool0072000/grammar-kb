"""泛读馆后端测试：epub 解析 / 词频粗筛 / API 行为与学生白名单。

环境变量必须在 create_app 之前设置（LibraryStore 构造时读取）。
"""
import io
import json
import os
import sqlite3
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")  # multipart-form 上传需要 python-multipart

from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.epub_parse import parse_epub  # noqa: E402
from grammar_kb.library import WORD_CARD_DEFAULTS  # noqa: E402
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
        # 教师视角返回书目管理统计（不做教师个人阅读统计）
        assert lst["stats"]["manage"] is True
        assert lst["stats"]["books"] == 1

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

        # stats：教师视角=管理统计（书数增长）；阅读统计已不采教师
        stats = teacher.get("/library/books").json()["data"]["stats"]
        assert stats["manage"] is True and stats["books"] == 2

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
        # 最终 percent=0：教师视角管理统计不再带阅读态（进度数据本身仍在
        # progress 端点返回，clamp 语义由上方断言覆盖）
        stats = teacher.get("/library/books").json()["data"]["stats"]
        assert stats["manage"] is True

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
        assert data["student"] == {
            "preset": "paper", "fontFamily": "serif", "focus": True, "vocabUnlock": 0,
            "wordCards": WORD_CARD_DEFAULTS,
        }
        # AI 退役：设置视图不再有 ai/dict/hasKey/defaultPrompt 段
        assert set(data.keys()) == {"student"}

    def test_student_partial_merge(self, teacher):
        # 部分传：只改传入的 student 字段，其余浅合并保留默认值
        r = teacher.put(
            "/library/settings", json={"student": {"preset": "night", "fontFamily": "kai"}}
        ).json()["data"]
        assert r["student"] == {
            "preset": "night", "fontFamily": "kai", "focus": True,
            "vocabUnlock": 0, "wordCards": WORD_CARD_DEFAULTS,
        }

    def test_word_cards_defaults_and_patch(self, teacher, student):
        cards = teacher.get("/library/settings").json()["data"]["student"]["wordCards"]
        assert cards["library"] == {"def": True, "image": False, "example": False}
        assert cards["reading"]["image"] is False and cards["reading"]["example"] is False
        assert cards["recite"] == {"def": True, "image": True, "example": True}
        r = teacher.put("/library/settings", json={
            "student": {"wordCards": {"library": {"image": True, "example": "no"}}},
        })
        assert r.status_code == 200
        lib = r.json()["data"]["student"]["wordCards"]["library"]
        assert lib == {"def": True, "image": True, "example": False}
        assert r.json()["data"]["student"]["wordCards"]["recite"]["example"] is True
        seen = student.get("/library/settings").json()["data"]["student"]["wordCards"]
        assert seen["library"]["image"] is True
        assert seen["conquered"]["def"] is True
        bad = teacher.put("/library/settings", json={"student": {"wordCards": []}})
        assert bad.status_code == 422

    def test_invalid_values_ignored(self, teacher):
        r = teacher.put(
            "/library/settings", json={"student": {"preset": "dark"}}
        ).json()["data"]
        assert r["student"]["preset"] == "paper"  # 非法档位忽略
        bad = teacher.put("/library/settings", json={"student": {"focus": "yes"}})
        assert bad.status_code == 422  # focus 非布尔直接 422

    def test_no_ai_section_and_put_ignored(self, teacher, student):
        """AI 退役：设置视图（师生）无 ai/apiKey/model 字段；PUT 带 ai/dict 段被静默忽略。"""
        t = teacher.get("/library/settings").json()["data"]
        assert "ai" not in t and "apiKey" not in t and "model" not in t
        s = student.get("/library/settings").json()["data"]
        assert "ai" not in s and "apiKey" not in s and "model" not in s
        r = teacher.put(
            "/library/settings",
            json={"ai": {"apiKey": "k", "model": "glm-4-plus", "maxWords": 99},
                  "dict": {"aiFallback": True}},
        )
        assert r.status_code == 200
        assert "ai" not in r.json()["data"] and "dict" not in r.json()["data"]
        # 再 GET 与 PUT 返回完全一致：ai/dict 段没有产生任何副作用
        assert teacher.get("/library/settings").json() == r.json()


# ---- API：预习（AI 退役后只读：预习数据由线下脚本生成入库，端点仅读） ----


class TestPrepRead:
    def test_get_prep_roundtrip(self, teacher, book_id):
        # 未生成的章返回 null
        r = teacher.get(f"/library/prep/{book_id}/1")
        assert r.status_code == 200
        assert r.json()["data"] is None

        # 线下生成：直写 chapter_preps 一行（模拟 scripts/import_prep.py 的入库产物）
        payload = {
            "words": [{"word": "peruse", "phonetic": "/pə'ru:s/", "pos": "v.",
                       "meaning": "细读", "example": "peruse a book",
                       "exampleZh": "细读一本书"}],
            "idioms": [{"phrase": "give up", "literal": "放弃", "actual": "认输",
                        "usage": "口语"}],
        }
        with sqlite3.connect(os.environ["GRAMMAR_KB_LIBRARY_DB"]) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO chapter_preps (book_id, chapter_index, payload)"
                " VALUES (?, ?, ?)",
                (book_id, 1, json.dumps(payload, ensure_ascii=False)),
            )

        data = teacher.get(f"/library/prep/{book_id}/1").json()["data"]
        assert data == payload
        # prepped 标记与章节列表联动（书架/章节页可见「已预习」）
        cs = teacher.get(f"/library/books/{book_id}/chapters").json()["data"]["chapters"]
        assert next(c for c in cs if c["idx"] == 1)["prepped"] is True
        assert next(c for c in cs if c["idx"] == 2)["prepped"] is False


# ---- API：查词（纯 ECDICT：命中 ecdict / 未收录 not_found，AI 兜底已退役） ----

def _real_ecdict_available() -> bool:
    from grammar_kb.dict_db import default_db_path

    db = default_db_path()
    return db.is_file() and db.stat().st_size > 1_000_000


requires_real_ecdict = pytest.mark.skipif(
    not _real_ecdict_available(),
    reason="data/ecdict.db 不存在（45MB 本地构建产物，未入 git），跳过真库查词用例",
)


@pytest.fixture()
def teacher_mini_dict(tmp_path):
    """教师客户端 + 迷你 ECDICT（GRAMMAR_KB_ECDICT_DB 指向临时词典，不依赖 45MB 真库）。"""
    from grammar_kb.dict_db import SCHEMA

    mini = tmp_path / "mini-ecdict.db"
    conn = sqlite3.connect(mini)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO dict VALUES (?, ?, ?, '', ?, '', 0)",
        ("peruse", "pə'ru:s", json.dumps(["v. 细读"]), json.dumps(["v"])),
    )
    conn.commit()
    conn.close()
    os.environ["GRAMMAR_KB_USERS"] = str(tmp_path / "users.json")
    os.environ["GRAMMAR_KB_AUTH_SECRET"] = str(tmp_path / "secret.key")
    os.environ["GRAMMAR_KB_LIBRARY_DB"] = str(tmp_path / "library.db")
    os.environ["GRAMMAR_KB_LIBRARY_DIR"] = str(tmp_path / "files")
    os.environ["GRAMMAR_KB_ECDICT_DB"] = str(mini)
    try:
        app = create_app()
        with TestClient(app) as c:
            c.headers.update(_login(c, "teacher"))
            yield c
    finally:
        os.environ.pop("GRAMMAR_KB_ECDICT_DB", None)  # 防泄漏进其他用例


class TestDict:
    @requires_real_ecdict
    def test_ecdict_hit(self, teacher):
        data = teacher.get("/library/dict/test").json()["data"]["entry"]
        assert data["source"] == "ecdict"
        assert data["word"] == "test"
        assert data["senses"]
        assert data["phonetic"]

    @requires_real_ecdict
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
        # 不在典 → not_found（AI 兜底已退役）
        nf = store.dict_lookup("zzzqqq")
        assert nf["source"] == "not_found"

    @requires_real_ecdict
    def test_possessive(self, teacher):
        # children's → 所有格归一 → children（词典收录其 pl. 词条）
        data = teacher.get("/library/dict/children's").json()["data"]["entry"]
        assert data["source"] == "ecdict"
        assert data["word"] == "children"

    def test_not_found(self, teacher):
        r = teacher.get("/library/dict/zzzzqqqqxxxx")
        assert r.status_code == 200
        assert r.json()["data"]["entry"]["source"] == "not_found"

    def test_pure_ecdict_hit_and_miss(self, teacher_mini_dict):
        """AI 退役后查词纯 ECDICT：命中 source=ecdict，未收录 not_found，绝无 source='ai'。"""
        c = teacher_mini_dict
        # 即便把 ai/dict 配置塞回设置（被静默忽略），查词路径也不再有 AI 分支
        r = c.put("/library/settings",
                  json={"ai": {"apiKey": "k"}, "dict": {"aiFallback": True}})
        assert r.status_code == 200

        hit = c.get("/library/dict/peruse").json()["data"]["entry"]
        assert hit["source"] == "ecdict"
        assert hit["word"] == "peruse"
        assert hit["senses"][0]["gloss"] == "细读"
        # ECDICT 命中字段固定，无任何 AI 专属字段
        assert set(hit.keys()) <= {"word", "senses", "meaning", "phonetic", "pos", "source"}

        nf = c.get("/library/dict/zzzqqqxxx")
        entry = nf.json()["data"]["entry"]
        assert entry["source"] == "not_found"
        assert set(entry.keys()) <= {"word", "source"}
        assert '"ai"' not in nf.text  # 响应字段里不存在 AI 来源标记


# ---- 学生白名单与权限 ----


class TestStudentPermissions:
    def test_unlock_blocks_direct_links(self, student, teacher, book_id):
        """bookUnlock 不在列表里的书，直链文件/章节/进度都不能绕过书架。"""
        r = teacher.put("/library/book-unlock", json={"unlock": {"malin": [999]}})
        assert r.status_code == 200, r.text
        assert student.get(f"/library/books/{book_id}/file").status_code == 403
        assert student.get(f"/library/books/{book_id}/chapters").status_code == 403
        assert student.get(f"/library/prep/{book_id}/1").status_code == 403
        assert student.put(
            f"/library/books/{book_id}/progress",
            json={"cfi": "x", "chapterIndex": 1, "percent": 10, "readingSecondsDelta": 30},
        ).status_code == 403
        shelf = student.get("/library/books").json()["data"]["books"]
        assert all(b["id"] != book_id for b in shelf)
        # 教师不受这道门影响
        assert teacher.get(f"/library/books/{book_id}/file").status_code == 200
        # 清掉限制后学生可以开始读；再收紧时，已有进度的书仍然可读
        assert teacher.put("/library/book-unlock", json={"unlock": {}}).status_code == 200
        assert student.put(
            f"/library/books/{book_id}/progress",
            json={"cfi": "x", "chapterIndex": 1, "percent": 10, "readingSecondsDelta": 30},
        ).status_code == 200
        assert teacher.put("/library/book-unlock", json={"unlock": {"malin": [999]}}).status_code == 200
        assert student.get(f"/library/books/{book_id}/file").status_code == 200

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

    def test_settings_write_forbidden(self, student):
        assert student.put(
            "/library/settings", json={"student": {"preset": "night"}}
        ).status_code == 403

    def test_student_settings_no_leak(self, student, teacher):
        # AI 退役：即便 PUT 塞入 ai 段（被静默忽略），学生视图也不出现任何 AI 字段
        teacher.put("/library/settings", json={"ai": {"apiKey": "SECRET-KEY",
                                                      "model": "glm-4-plus",
                                                      "prompt": "SECRET-PROMPT"},
                                               "student": {"preset": "night"}})
        raw = student.get("/library/settings").text
        assert "SECRET-KEY" not in raw
        assert "SECRET-PROMPT" not in raw
        assert "glm-4-plus" not in raw
        assert "defaultPrompt" not in raw
        data = json.loads(raw)["data"]
        assert set(data.keys()) == {"student"}
        assert data["student"]["preset"] == "night"

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
        # 各自的 stats 互不串（教师视角=管理统计 manage；学生=阅读统计）
        tstats = teacher.get("/library/books").json()["data"]["stats"]
        sstats = student.get("/library/books").json()["data"]["stats"]
        assert tstats.get("manage") is True
        assert sstats["reading"] == 1 and sstats["totalSeconds"] == 30

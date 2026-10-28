"""词光叠加层：加表迁移、幂等导入、查词、媒体路径、HTTP。"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from grammar_kb.atlas import (
    MEDIA_DEFAULT,
    compact_for_words,
    ensure_schema,
    import_entries,
    lookup_keys,
    lookup_public,
    mirror_tree,
    resolve_media_file,
)
from grammar_kb.db import GrammarDB

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

LIBRARY = {
    "id": "library",
    "w": "library",
    "uk": "/ˈlaɪbrəri/",
    "us": "/ˈlaɪbreri/",
    "senses": [{"pos": "n.", "zh": "图书馆", "en": "A place with books."}],
    "ex": [
        {"en": "I borrowed a book.", "zh": "我借了一本书。"},
        {"en": "Libraries support learning.", "zh": "图书馆支持学习。"},
    ],
    "col": ["public library｜公共图书馆"],
    "fam": ["librarian (n.)"],
    "syn": ["archive"],
    "ant": [],
    "tip": "library 是图书馆，bookshop 是书店。",
    "note": "",
    "cefr": "A1",
    "topic": "education",
}
OCLOCK = {
    "id": "o-clock",
    "w": "o'clock",
    "uk": "/əˈklɒk/",
    "us": "/əˈklɑːk/",
    "senses": [{"pos": "adv.", "zh": "点钟", "en": "On the hour."}],
    "ex": [{"en": "at three o'clock", "zh": "三点整"}],
    "col": [],
    "fam": [],
    "syn": [],
    "ant": [],
    "tip": "",
    "note": "",
    "cefr": "A1",
    "topic": "daily",
}
POST = {
    "id": "post-office",
    "w": "post office",
    "uk": "/ˈpəʊst ɒfɪs/",
    "us": "/ˈpoʊst ɔːfɪs/",
    "senses": [{"pos": "n.", "zh": "邮局", "en": "A post office."}],
    "ex": [],
    "col": [],
    "fam": [],
    "syn": [],
    "ant": [],
    "tip": "",
    "note": "",
    "cefr": "A2",
    "topic": "city",
}
ZIP = {
    "id": "zipcode",
    "w": "ZIP code",
    "uk": "",
    "us": "/ˈzɪp kəʊd/",
    "senses": [{"pos": "n.", "zh": "邮政编码", "en": "A ZIP code."}],
    "ex": [],
    "col": [],
    "fam": [],
    "syn": [],
    "ant": [],
    "tip": "",
    "note": "",
    "cefr": "A2",
    "topic": "daily",
}


def _conn(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def test_lookup_keys_cover_phrases():
    assert "o'clock" in lookup_keys("O'Clock")
    assert "o-clock" in lookup_keys("o'clock")
    assert "post-office" in lookup_keys("post office")
    assert "zipcode" in lookup_keys("ZIP code")


def test_schema_upgrade_keeps_rows(tmp_path):
    path = tmp_path / "old.db"
    conn = _conn(path)
    conn.execute(
        "CREATE TABLE vocab_atlas (word TEXT PRIMARY KEY, atlas_id TEXT NOT NULL DEFAULT '')"
    )
    conn.execute("INSERT INTO vocab_atlas (word, atlas_id) VALUES ('apple', 'apple')")
    conn.commit()
    ensure_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(vocab_atlas)")}
    assert "tip" in cols and "media" in cols
    kept = conn.execute("SELECT atlas_id FROM vocab_atlas WHERE word='apple'").fetchone()
    assert kept["atlas_id"] == "apple"
    conn.close()


def test_grammar_db_open_is_additive(tmp_path):
    """打开库只加 vocab_atlas，不删已有讲次，也不碰另一份进度库。"""
    path = tmp_path / "grammar.db"
    db = GrammarDB(str(path))
    db.conn.execute(
        "INSERT INTO lecture (number, title) VALUES (3, '时态')"
    )
    db.conn.execute("CREATE TABLE keep_me (note TEXT)")
    db.conn.execute("INSERT INTO keep_me (note) VALUES ('son-progress')")
    db.conn.commit()
    db.close()

    fce = tmp_path / "fce.db"
    progress = sqlite3.connect(fce)
    progress.execute(
        "CREATE TABLE recite_word_progress (user TEXT, word TEXT, right INTEGER)"
    )
    progress.execute(
        "INSERT INTO recite_word_progress VALUES ('malin', 'apple', 4)"
    )
    progress.commit()
    progress.close()
    before = fce.read_bytes()

    again = GrammarDB(str(path))
    assert again.conn.execute("SELECT title FROM lecture WHERE number=3").fetchone()["title"] == "时态"
    assert again.conn.execute("SELECT note FROM keep_me").fetchone()["note"] == "son-progress"
    tables = {
        row[0]
        for row in again.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "vocab_atlas" in tables
    import_entries(again.conn, [LIBRARY])
    again.reset_all()
    still = again.conn.execute(
        "SELECT atlas_id FROM vocab_atlas WHERE word='library'"
    ).fetchone()
    assert still["atlas_id"] == "library"
    again.close()
    assert fce.read_bytes() == before


def test_import_is_idempotent_and_skips_unsafe_ids(tmp_path):
    conn = _conn(tmp_path / "atlas.db")
    flags = {"library": 31, "o-clock": 15, "post-office": 15, "zipcode": 15}
    entries = [LIBRARY, OCLOCK, POST, ZIP, {"w": "bad", "id": "../etc/passwd"}]
    first = import_entries(conn, entries, flags, have_index=True)
    assert first == {"upserted": 4, "skipped": 1}
    changed = dict(LIBRARY, tip="改过的提示")
    second = import_entries(conn, [changed, OCLOCK, POST, ZIP], flags, have_index=True)
    assert second["upserted"] == 4
    assert conn.execute("SELECT COUNT(*) FROM vocab_atlas").fetchone()[0] == 4
    tip = conn.execute("SELECT tip, media FROM vocab_atlas WHERE word='library'").fetchone()
    assert tip["tip"] == "改过的提示"
    assert tip["media"] == 31

    hit = lookup_public(conn, "Library")
    assert hit["id"] == "library"
    assert hit["topic_zh"] == "教育"
    assert hit["examples"][0]["zh"] == "我借了一本书。"
    assert hit["attribution"] == "Word Atlas contributors（词光）"
    assert lookup_public(conn, "o'clock")["id"] == "o-clock"
    assert lookup_public(conn, "POST OFFICE")["id"] == "post-office"
    assert lookup_public(conn, "zip code")["id"] == "zipcode"
    assert lookup_public(conn, "no-such-word") is None

    missing_index = import_entries(
        conn, [{"id": "only-json", "w": "only-json", "uk": "/x/"}], {}, have_index=True,
    )
    assert missing_index["upserted"] == 1
    media = conn.execute(
        "SELECT media FROM vocab_atlas WHERE word='only-json'"
    ).fetchone()["media"]
    assert media == 0
    no_index = import_entries(conn, [{"id": "plain", "w": "plain"}])
    assert no_index["upserted"] == 1
    assert conn.execute(
        "SELECT media FROM vocab_atlas WHERE word='plain'"
    ).fetchone()["media"] == MEDIA_DEFAULT
    conn.close()


def test_compact_for_words_omits_examples(tmp_path):
    conn = _conn(tmp_path / "compact.db")
    import_entries(conn, [LIBRARY])
    found = compact_for_words(conn, ["Library", "missing"])
    assert set(found) == {"library"}
    assert found["library"]["id"] == "library"
    assert "examples" not in found["library"]
    assert "senses" not in found["library"]
    conn.close()


def test_resolve_media_file_rejects_escape(tmp_path):
    root = tmp_path / "atlas"
    (root / "img").mkdir(parents=True)
    (root / "audio" / "uk").mkdir(parents=True)
    image = root / "img" / "library.webp"
    image.write_bytes(b"webp")
    audio = root / "audio" / "uk" / "library.mp3"
    audio.write_bytes(b"mp3")
    outside = tmp_path / "secret.mp3"
    outside.write_bytes(b"secret")
    (root / "img" / "evil.webp").symlink_to(outside)

    assert resolve_media_file(root, "img/library.webp") == image.resolve()
    assert resolve_media_file(root, "audio/uk/library.mp3") == audio.resolve()
    assert resolve_media_file(root, "img/evil.webp") is None
    assert resolve_media_file(root, "img/../secret.mp3") is None
    assert resolve_media_file(root, "../secret.mp3") is None
    assert resolve_media_file(root, "audio/uk/../../secret.mp3") is None
    assert resolve_media_file(root, "img/library.mp3") is None
    assert resolve_media_file(root, "sfx/click.mp3") is None
    assert resolve_media_file(root, "img/missing.webp") is None


def test_mirror_tree_is_idempotent(tmp_path):
    src = tmp_path / "src"
    (src / "img").mkdir(parents=True)
    (src / "audio" / "uk").mkdir(parents=True)
    (src / "img" / "library.webp").write_bytes(b"img")
    (src / "audio" / "uk" / "library.mp3").write_bytes(b"mp3")
    (src / "sfx").mkdir()
    (src / "sfx" / "click.mp3").write_bytes(b"nope")
    dest = tmp_path / "dest"
    first = mirror_tree(src, dest)
    assert first["linked"] + first["copied"] == 2
    assert not (dest / "sfx").exists()
    second = mirror_tree(src, dest)
    assert second["skipped"] == 2
    assert second["linked"] == 0 and second["copied"] == 0
    assert (dest / "img" / "library.webp").read_bytes() == b"img"


def test_import_script_cli(tmp_path):
    dataset = tmp_path / "word-atlas.json"
    index = tmp_path / "index.json"
    dataset.write_text(json.dumps([LIBRARY, {"w": "bad", "id": "../x"}]), encoding="utf-8")
    index.write_text(json.dumps({"words": [["library", "library", "", "", 0, 0, 15]]}), encoding="utf-8")
    db = tmp_path / "grammar.db"
    cmd = [
        PY, str(ROOT / "scripts" / "import_word_atlas.py"),
        "--json", str(dataset), "--index", str(index), "--db", str(db),
    ]
    subprocess.run(cmd, check=True, cwd=ROOT)
    subprocess.run(cmd, check=True, cwd=ROOT)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM vocab_atlas").fetchone()[0] == 1
    assert conn.execute("SELECT media FROM vocab_atlas").fetchone()[0] == 15
    meta = dict(conn.execute("SELECT key, value FROM meta"))
    assert meta["atlas_attribution"] == "Word Atlas contributors（词光）"
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "recite_word_progress" not in names
    conn.close()


@pytest.fixture()
def api(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from grammar_kb.server import create_app
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    monkeypatch.setenv("GRAMMAR_KB_EXAM_DB", str(tmp_path / "exam.db"))
    monkeypatch.setenv("GRAMMAR_KB_FCE_DB", str(tmp_path / "fce.db"))
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DB", str(tmp_path / "library.db"))
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DIR", str(tmp_path / "library"))
    media = tmp_path / "media"
    (media / "img").mkdir(parents=True)
    (media / "audio" / "uk").mkdir(parents=True)
    (media / "img" / "library.webp").write_bytes(b"webp-bytes")
    (media / "audio" / "uk" / "library.mp3").write_bytes(b"mp3-bytes")
    monkeypatch.setenv("GRAMMAR_KB_ATLAS_DIR", str(media))

    db = tmp_path / "grammar.db"
    conn = _conn(db)
    conn.execute(
        "CREATE TABLE vocab_word (word TEXT PRIMARY KEY, level INTEGER, pos TEXT,"
        " gloss TEXT, meanings TEXT, example TEXT, extra TEXT)"
    )
    conn.executemany(
        "INSERT INTO vocab_word VALUES (?,?,?,?,?,?,?)",
        [
            ("library", 0, "[]", "图书馆", "[]", "{}", "{}"),
            ("orbit", 3, "[]", "轨道", "[]", "{}", "{}"),
        ],
    )
    conn.commit()
    import_entries(conn, [LIBRARY], {"library": 15}, have_index=True)
    conn.close()

    app = create_app(str(db), str(tmp_path / "exam.db"), str(tmp_path / "fce.db"))
    with TestClient(app) as client:
        yield client


def _login(client, user, password="123456"):
    response = client.post("/auth/login", json={"user": user, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['token']}"}


def test_http_atlas_and_vocab_levels(api):
    assert api.get("/atlas/library").status_code == 401
    assert api.get("/atlas-media/img/library.webp").status_code == 200
    assert api.get("/atlas-media/img/library.webp").content == b"webp-bytes"
    assert api.get("/atlas-media/audio/uk/library.mp3").content == b"mp3-bytes"
    assert api.get("/atlas-media/img/missing.webp").status_code == 404
    assert api.get("/atlas-media/img/../secret.webp").status_code == 404
    assert api.get("/atlas-media/sfx/click.mp3").status_code == 404

    student = _login(api, "malin")
    entry = api.get("/atlas/library", headers=student)
    assert entry.status_code == 200
    body = entry.json()["data"]
    assert body["cefr"] == "A1"
    assert body["tip"].startswith("library")
    assert api.get("/atlas/nope", headers=student).status_code == 404
    info = api.get("/atlas-info", headers=student).json()["data"]
    assert info["count"] == 1
    assert info["media_ready"] is True
    assert info["license"] == "CC BY 4.0"

    levels = api.get("/vocab-levels?max_level=7", headers=student).json()["data"]
    words = {item["word"]: item for item in levels["items"]}
    assert words["library"]["gloss"] == "图书馆"
    assert words["library"]["level"] == 0
    assert words["library"]["atlas"]["id"] == "library"
    assert "examples" not in words["library"]["atlas"]
    assert "orbit" not in words

    teacher = _login(api, "teacher")
    teacher_items = api.get("/vocab-levels?max_level=7", headers=teacher).json()["data"]["items"]
    by_word = {item["word"]: item for item in teacher_items}
    assert by_word["orbit"]["gloss"] == "轨道"
    assert "atlas" not in by_word["orbit"]
    assert by_word["library"]["level"] == 0

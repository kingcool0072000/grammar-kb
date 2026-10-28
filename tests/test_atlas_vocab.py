"""词光词头优先：别名、考卷闸门、进度合并。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from grammar_kb.atlas_vocab import (
    canon_key,
    exam_allowed,
    plan_vocab,
)
from grammar_kb.recite import _fold_alias_progress, _fold_table


ROOT = Path(__file__).resolve().parent.parent


def _entry(word, cefr, fam=None, zh="义"):
    return {
        "id": word, "w": word, "cefr": cefr, "topic": "daily",
        "senses": [{"pos": "n.", "zh": zh}], "ex": [], "fam": fam or [],
    }


def test_family_spelling_and_exam_gate():
    records = {
        canon_key("happy"): {"word": "happy", "first": "初中", "lists": ["初中"]},
        canon_key("labour"): {"word": "labour", "first": "高中", "lists": ["高中"]},
        canon_key("he"): {"word": "he", "first": "初中", "lists": ["初中"]},
    }
    entries = [
        _entry("happiness", "A2", ["happy (adj.)"], "幸福"),
        _entry("happily", "A2", ["happy (adj.)"]),
        _entry("labor", "B2", [], "劳动"),
        _entry("him", "A1", ["he (pron.)", "his (det.)"], "他"),
        _entry("guitar", "A1", [], "吉他"),
        _entry("macroeconomics", "C1", [], "宏观经济学"),
    ]
    # him 已经在词表里，he 应并过去，而不是再追加一条。
    records[canon_key("him")] = {"word": "him", "first": "初中", "lists": ["初中"]}
    plan = plan_vocab(records, entries)
    assert plan["aliases"]["happy"] == {"to": "happiness", "kind": "family"}
    assert plan["aliases"]["he"] == {"to": "him", "kind": "family"}
    assert plan["aliases"]["labour"] == {"to": "labor", "kind": "spelling"}
    guitar = plan["additions"][canon_key("guitar")]
    assert guitar["level"] == "初中" and guitar["exam"] is False
    macro = plan["additions"][canon_key("macroeconomics")]
    assert macro["level"] == "雅思" and macro["exam"] is False
    labor = plan["additions"][canon_key("labor")]
    assert labor["level"] == "高中" and labor["exam"] is True
    assert canon_key("him") not in plan["additions"]
    assert canon_key("happy") in plan["remove"]


def test_exam_pool_skips_atlas_supplements(tmp_path, monkeypatch):
    db = tmp_path / "grammar.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE vocab_word (word TEXT PRIMARY KEY, level INTEGER, pos TEXT,"
        " gloss TEXT, extra TEXT)"
    )
    conn.executemany(
        "INSERT INTO vocab_word VALUES (?,?,?,?,?)",
        [
            ("labor", 2, "[]", "劳动", '{"exam": true, "origin": "word-atlas"}'),
            ("guitar", 1, "[]", "吉他", '{"exam": false, "origin": "word-atlas"}'),
            ("book", 1, "[]", "书", "{}"),
        ],
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        "grammar_kb.vocab_paper._grammar_db_path", lambda: str(db),
    )
    from grammar_kb.vocab_paper import _load_words_rich

    junior = {row["word"] for row in _load_words_rich(1)}
    senior = {row["word"] for row in _load_words_rich(2)}
    assert junior == {"book"}
    assert senior == {"labor"}
    assert exam_allowed("{}") is True
    assert exam_allowed('{"exam": false}') is False


def test_progress_fold_keeps_counts(tmp_path, monkeypatch):
    alias = tmp_path / "vocab_alias.json"
    alias.write_text(json.dumps({
        "aliases": {
            "happy": {"to": "happiness", "kind": "family"},
            "labour": {"to": "labor", "kind": "spelling"},
        },
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        "grammar_kb.atlas_vocab.data_path",
        lambda *parts: alias if parts == ("vocab_alias.json",) else tmp_path.joinpath(*parts),
    )
    db = tmp_path / "fce.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE recite_word_progress (
            user TEXT, word TEXT, level INTEGER, right INTEGER, wrong INTEGER,
            mastered_at TEXT, last_seen TEXT, first_seen TEXT,
            PRIMARY KEY (user, word)
        );
        CREATE TABLE recite_review (
            user TEXT, word TEXT, level INTEGER, wrong_count INTEGER, stage INTEGER,
            first_wrong_at TEXT, next_review TEXT, last_review_at TEXT, conquered_at TEXT,
            PRIMARY KEY (user, word)
        );
        CREATE TABLE recite_sessions (
            id INTEGER PRIMARY KEY, wrong_words TEXT
        );
    """)
    conn.execute(
        "INSERT INTO recite_word_progress VALUES (?,?,?,?,?,?,?,?)",
        ("malin", "happy", 1, 3, 1, "2026-01-01", "2026-02-01", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO recite_word_progress VALUES (?,?,?,?,?,?,?,?)",
        ("malin", "happiness", 1, 2, 0, None, "2026-03-01", "2026-02-15"),
    )
    conn.execute(
        "INSERT INTO recite_review VALUES (?,?,?,?,?,?,?,?,?)",
        ("malin", "happy", 1, 1, 2, "2026-01-02", "2026-01-09", "2026-01-02", None),
    )
    conn.execute(
        "INSERT INTO recite_sessions (wrong_words) VALUES (?)",
        ('["happy","book"]',),
    )
    conn.commit()
    _fold_alias_progress(conn)
    row = conn.execute(
        "SELECT right, wrong, mastered_at FROM recite_word_progress WHERE word='happiness'"
    ).fetchone()
    assert row["right"] == 5 and row["wrong"] == 1
    assert row["mastered_at"] == "2026-01-01"
    assert conn.execute(
        "SELECT COUNT(*) FROM recite_word_progress WHERE word='happy'"
    ).fetchone()[0] == 0
    review = conn.execute(
        "SELECT word, wrong_count FROM recite_review"
    ).fetchone()
    assert review["word"] == "happiness" and review["wrong_count"] == 1
    words = json.loads(conn.execute("SELECT wrong_words FROM recite_sessions").fetchone()[0])
    assert words == ["happiness", "book"]
    # 再跑一次不把计数再加一遍
    _fold_alias_progress(conn)
    again = conn.execute(
        "SELECT right FROM recite_word_progress WHERE word='happiness'"
    ).fetchone()["right"]
    assert again == 5
    conn.close()
    # 直接并表的路径也覆盖「只有别名、没有词头行」
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("DELETE FROM recite_meta")
    conn.execute(
        "INSERT INTO recite_word_progress VALUES (?,?,?,?,?,?,?,?)",
        ("malin", "labour", 2, 4, 0, "2026-04-01", "2026-04-01", "2026-04-01"),
    )
    conn.commit()
    _fold_table(conn, "recite_word_progress", {"labour": "labor"}, progress=True)
    moved = conn.execute(
        "SELECT right FROM recite_word_progress WHERE word='labor'"
    ).fetchone()
    assert moved["right"] == 4
    conn.close()


def test_committed_wordlists_drop_typos_and_keep_must():
    junior = (ROOT / "data" / "wordlists" / "初中.jsonl").read_text(encoding="utf-8")
    assert '"word":"Rusia"' not in junior and '"word":"rusia"' not in junior
    cet4 = (ROOT / "data" / "wordlists" / "四级.jsonl").read_text(encoding="utf-8").splitlines()
    for line in cet4:
        json.loads(line)
    assert any(json.loads(line).get("word") == "must" for line in (
        ROOT / "data" / "wordlists" / "初中.jsonl"
    ).read_text(encoding="utf-8").splitlines())
    aliases = json.loads(
        (ROOT / "data" / "vocab_alias.json").read_text(encoding="utf-8")
    )["aliases"]
    assert aliases["happy"]["to"] == "happiness"
    assert aliases["he"]["to"] == "him"
    assert aliases["labour"]["to"] == "labor"
    assert aliases["rusia"]["to"] == "russia"
    guitar = [
        json.loads(line) for line in junior.splitlines()
        if json.loads(line).get("word") == "guitar"
    ]
    assert guitar and guitar[0]["exam"] is False and guitar[0]["origin"] == "word-atlas"

"""词汇级别考试测试：解锁规则 / 成绩登记 / 考卷生成 / 权限。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.server import create_app  # noqa: E402
from grammar_kb.vocab_exam import build_paper, render_paper_html  # noqa: E402


@pytest.fixture()
def vex_env(tmp_path, monkeypatch):
    """隔离用户/成绩库；grammar.db 用仓库真实词表（考卷生成需 50 词）。"""
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    sqlite3.connect(tmp_path / "grammar.db").close()
    return tmp_path


def _client(env) -> TestClient:
    from pathlib import Path
    # vocab_exam.load_level_words 走 ingest 默认路径（仓库 data/grammar.db）
    real_grammar = Path(__file__).resolve().parent.parent / "data" / "grammar.db"
    return TestClient(create_app(str(real_grammar), str(env / "exam.db"),
                                 fce_db_path=str(env / "fce.db")))


def _login(c, user, password):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _require_level_words(monkeypatch):
    """考卷/词级统计依赖仓库 data/grammar.db 的 vocab_word。

    该库是本地 ingest 产物，不入库。CI 把 GRAMMAR_KB_DB 指到空的 tmp
    库（防止裸 create_app 在 data/ 下造空库），load_level_words 会跟
    环境变量走——有真库时改指过去，没有则跳过，与 taxonomy 用例同口径。
    """
    db = Path(__file__).resolve().parent.parent / "data" / "grammar.db"
    if not db.is_file():
        pytest.skip("data/grammar.db 不存在（本地构建产物，未入库），跳过依赖词表的用例")
    try:
        with sqlite3.connect(str(db)) as con:
            has = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vocab_word'"
            ).fetchone()
            n = (con.execute("SELECT COUNT(*) FROM vocab_word WHERE level = 0").fetchone()[0]
                 if has else 0)
    except sqlite3.Error:
        pytest.skip("data/grammar.db 无法读取 vocab_word，跳过依赖词表的用例")
    if n < 50:
        pytest.skip(f"data/grammar.db L0 词不足 50（现有 {n}），跳过依赖词表的用例")
    monkeypatch.setenv("GRAMMAR_KB_DB", str(db))


def test_exam_unlock_flow(vex_env, monkeypatch):
    _require_level_words(monkeypatch)
    """L0 恒开 → L0 考 82 分解锁 L1 → 查询/词汇级数联动。"""
    c = _client(vex_env)
    hs = _login(c, "malin", "123456")
    ht = _login(c, "teacher", "123456")
    # 初始：学生解锁 L0
    d = c.get("/vocab-exams", headers=hs).json()["data"]
    assert d["unlocked_level"] == 0 and d["passed_levels"] == []
    lv = c.get("/vocab-levels?max_level=5", headers=hs).json()["data"]
    assert lv["unlocked"] == 0
    # 登记 82 分（≥80 通过）
    r = c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 0, "score": 82, "exam_date": "2026-10-11"})
    assert r.status_code == 200 and r.json()["data"]["passed"] is True
    assert r.json()["data"]["unlocked_level"] == 1
    lv = c.get("/vocab-levels?max_level=5", headers=hs).json()["data"]
    assert lv["unlocked"] == 1 and "1" in lv["counts"]
    # 不及格不解锁
    c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 1, "score": 79, "exam_date": "2026-10-12"})
    d = c.get("/vocab-exams", headers=hs).json()["data"]
    assert d["unlocked_level"] == 1 and 1 not in d["passed_levels"]
    # 教师不受限
    lv = c.get("/vocab-levels?max_level=5", headers=ht).json()["data"]
    assert lv["unlocked"] == 5


def test_exam_validation_and_perm(vex_env):
    c = _client(vex_env)
    hs = _login(c, "malin", "123456")
    ht = _login(c, "teacher", "123456")
    # 学生不能登记/打卷
    assert c.post("/vocab-exams", headers=hs, json={
        "user": "malin", "level": 0, "score": 99,
        "exam_date": "2026-10-11"}).status_code == 403
    assert c.get("/vocab-exams/paper?level=0", headers=hs).status_code == 403
    # 参数校验
    assert c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 9, "score": 80,
        "exam_date": "2026-10-11"}).status_code == 422
    assert c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 0, "score": 120,
        "exam_date": "2026-10-11"}).status_code == 422
    assert c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 0, "score": 80,
        "exam_date": "2026/10/11"}).status_code == 422
    # 删除不存在 404
    assert c.delete("/vocab-exams/999", headers=ht).status_code == 404


def test_level_query_cannot_bypass_unlock(vex_env):
    """?level= 不能越过学生解锁上限，也不能把 unlocked 改写成所请求的级别。"""
    db = vex_env / "grammar.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE vocab_word (word TEXT, level INTEGER, pos TEXT, gloss TEXT,"
        " meanings TEXT, example TEXT, extra TEXT)"
    )
    con.executemany(
        "INSERT INTO vocab_word VALUES (?,?,?,?,?,?,?)",
        [
            ("apple", 0, "[]", "苹果", "[]", "{}", "{}"),
            ("orbit", 3, "[]", "轨道", "[]", "{}", "{}"),
        ],
    )
    con.commit()
    con.close()
    c = TestClient(create_app(str(db), str(vex_env / "exam.db"),
                              fce_db_path=str(vex_env / "fce.db")))
    hs = _login(c, "malin", "123456")
    locked = c.get("/vocab-levels?max_level=7&level=3", headers=hs)
    assert locked.status_code == 200, locked.text
    body = locked.json()["data"]
    assert body["unlocked"] == 0
    assert body["items"] == []
    assert body["counts"].get("3") == 1 or body["counts"].get(3) == 1
    own = c.get("/vocab-levels?max_level=7", headers=hs).json()["data"]
    assert [w["word"] for w in own["items"]] == ["apple"]
    ht = _login(c, "teacher", "123456")
    teacher = c.get("/vocab-levels?max_level=7&level=3", headers=ht).json()["data"]
    assert [w["word"] for w in teacher["items"]] == ["orbit"]
    assert teacher["unlocked"] == 7


def test_paper_generation(vex_env, monkeypatch):
    """考卷：50 词四部分 + HTML 结构 + seed 复现。"""
    _require_level_words(monkeypatch)
    c = _client(vex_env)
    ht = _login(c, "teacher", "123456")
    r = c.get("/vocab-exams/paper?level=0&seed=42", headers=ht)
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    body = r.text
    for marker in ("听写", "中译英", "英译中", "拼写补全", "答案页"):
        assert marker in body
    # seed 固定 → 两卷一致；无 seed → 随机
    p1 = build_paper(0, seed=42)
    p2 = build_paper(0, seed=42)
    assert [w["word"] for w in p1["words"]] == [w["word"] for w in p2["words"]]
    assert len(p1["words"]) == 50
    assert render_paper_html(p1).startswith("<!DOCTYPE html>")


def _fake_rich(names):
    """测试用富词条：不读 grammar.db。"""
    return {
        w: {"word": w, "pos": '["n"]', "pos_list": ["n"],
            "pos_cn": "n. 名词", "gloss": "义", "gloss_clean": "义"}
        for w in names
    }


def _insert_hist(db, level, paper_id, words):
    from grammar_kb.vocab_exam import VocabExamStore
    from grammar_kb.vocab_paper import _HISTORY_TABLE_SQL
    store = VocabExamStore(db)
    with store._connect() as conn:
        conn.execute(_HISTORY_TABLE_SQL)
        conn.execute(
            "INSERT OR REPLACE INTO vocab_paper_history"
            " (level, paper_id, words, created_at) VALUES (?,?,?,?)",
            (level, paper_id, json.dumps(words), "2026-01-01T00:00:00"),
        )


def test_mix_history_buckets_and_save(tmp_path):
    """本级 + 混合卷分桶取回；大量其它级别不能把本级或 MIX 挤出窗口。"""
    from grammar_kb.vocab_paper import list_paper_history, save_paper
    db = str(tmp_path / "fce.db")
    words = [f"w{i:02d}" for i in range(50)]
    save_paper({"paper_id": "L0-20260101010101-1000", "words": words,
                "created_at": "2026-01-01T00:00:00"}, fce_db_path=db)
    save_paper({"paper_id": "MIX-20260101010101-1001", "words": words,
                "created_at": "2026-01-01T00:00:01"}, fce_db_path=db)
    for i in range(30):
        save_paper({
            "paper_id": f"L2-20260101010101-{2000 + i}",
            "words": words, "created_at": "2026-01-02T00:00:00",
        }, fce_db_path=db)
    only = list_paper_history(0, limit=5, fce_db_path=db)
    assert [p["paper_id"] for p in only] == ["L0-20260101010101-1000"]
    both = list_paper_history(0, limit=5, fce_db_path=db, include_mix=True)
    ids = {p["paper_id"] for p in both}
    assert "L0-20260101010101-1000" in ids
    assert "MIX-20260101010101-1001" in ids
    assert all(p["level"] in (0, -1) for p in both)
    with pytest.raises(ValueError):
        save_paper({"paper_id": "MIX-short", "words": ["a"]}, fce_db_path=db)
    with pytest.raises(ValueError):
        save_paper({"paper_id": "NOPE-1", "words": words}, fce_db_path=db)


def test_learned_dedup_ignores_level_papers(tmp_path, monkeypatch):
    """learned 只跟先前 MIX（level<0）去重；本级卷用过的词不抽干混合卷。

    反过来，level 范围仍只跟本级历史去重，不看 MIX。
    """
    import grammar_kb.vocab_paper as vp
    words = [f"w{i:03d}" for i in range(80)]
    rich = list(_fake_rich(words).values())
    monkeypatch.setattr(vp, "_load_learned_words", lambda user, fce_db_path=None: rich)
    monkeypatch.setattr(vp, "_load_words_rich", lambda level: rich)
    db = str(tmp_path / "fce.db")
    _insert_hist(db, 0, "L0-20260101010101-3000", words)
    paper = vp.generate_paper(1, save=False, scope="learned", user="malin",
                              fce_db_path=db)
    assert paper["paper_id"].startswith("MIX-")
    assert paper["level"] == -1 and paper["scope"] == "learned"
    assert paper["user"] == "malin"
    assert paper["overlap_with_history"] == 0
    assert len(paper["words"]) == 50
    assert set(paper["words"]) <= set(words)
    # 先前 MIX 已覆盖全部词池 → 再出一卷应因重复超限失败
    _insert_hist(db, -1, "MIX-20260101010101-3001", words)
    with pytest.raises(ValueError, match="重复"):
        vp.generate_paper(1, save=False, scope="learned", user="malin",
                          fce_db_path=db)
    # level 范围不看 MIX：清掉本级历史后，只剩 MIX 覆盖也不挡本级
    from grammar_kb.vocab_exam import VocabExamStore
    with VocabExamStore(db)._connect() as conn:
        conn.execute("DELETE FROM vocab_paper_history WHERE level = 0")
    level_paper = vp.generate_paper(0, save=False, fce_db_path=db)
    assert level_paper["paper_id"].startswith("L0-")
    assert level_paper["overlap_with_history"] == 0
    # 本级历史仍按原口径去重
    _insert_hist(db, 0, "L0-20260101010101-3002", words)
    with pytest.raises(ValueError, match="重复"):
        vp.generate_paper(0, save=False, fce_db_path=db)


def test_paper_generate_learned_scope(vex_env, monkeypatch):
    """learned：进度里出现过的词（含未掌握）跨级出卷；登记须选 0-7 级。"""
    import grammar_kb.vocab_paper as vp
    monkeypatch.setattr(
        vp, "_load_words_by_names",
        lambda names: _fake_rich([n for n in names if n]))
    c = _client(vex_env)
    ht = _login(c, "teacher", "123456")
    db = str(vex_env / "fce.db")
    learned = [f"got{i:03d}" for i in range(80)]
    other = [f"other{i:03d}" for i in range(10)]
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS recite_word_progress ("
            " user TEXT NOT NULL, word TEXT NOT NULL, level INTEGER,"
            " right INTEGER NOT NULL DEFAULT 0, wrong INTEGER NOT NULL DEFAULT 0,"
            " mastered_at TEXT, last_seen TEXT, PRIMARY KEY (user, word))")
        # 已学 = 进度里出现过即可，这里全部未掌握（mastered_at 空）
        con.executemany(
            "INSERT OR REPLACE INTO recite_word_progress"
            " (user, word, level, right, wrong, mastered_at, last_seen)"
            " VALUES (?,?,?,?,?,?,datetime('now'))",
            [("malin", w, 0, 0, 1, None) for w in learned]
            + [("other", w, 1, 3, 0, "2026-01-01") for w in other])
    # 本级卷用过全部已学词，也不应挡住 MIX
    _insert_hist(db, 0, "L0-20260101010101-4000", learned)
    r = c.post("/vocab-papers/generate", headers=ht, json={
        "level": 1, "save": False, "scope": "learned", "user": "malin"})
    assert r.status_code == 200, r.text
    p = r.json()["data"]["paper"]
    assert p["paper_id"].startswith("MIX-") and p["level"] == -1
    assert p["scope"] == "learned" and p["user"] == "malin"
    assert len(p["words"]) == 50 and p["overlap_with_history"] == 0
    assert set(p["words"]) <= set(learned)
    assert set(p["words"]).isdisjoint(other)
    assert "已学全部混合卷" in r.json()["data"]["html"]
    r2 = c.post("/vocab-papers/save", headers=ht, json=p)
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["level"] == -1
    r3 = c.get(f"/vocab-papers/{p['paper_id']}/preview", headers=ht)
    assert r3.status_code == 200
    assert "已学全部混合卷" in r3.json()["data"]["html"]
    # 指定级别时默认不含 MIX；include_mix 把混合卷带上
    plain = c.get("/vocab-papers/history", headers=ht,
                  params={"level": 1, "limit": 20})
    assert plain.status_code == 200
    assert all(x["paper_id"] != p["paper_id"]
               for x in plain.json()["data"]["papers"])
    mixed = c.get("/vocab-papers/history", headers=ht,
                  params={"level": 0, "include_mix": True, "limit": 5})
    assert mixed.status_code == 200
    got = {x["paper_id"]: x["level"] for x in mixed.json()["data"]["papers"]}
    assert got.get(p["paper_id"]) == -1
    assert got.get("L0-20260101010101-4000") == 0
    # 混合卷不能按 level=-1 登记；选一个 0-7 级可以，并出现在批改列表
    assert c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": -1, "score": 88,
        "exam_date": "2026-10-11", "paper_id": p["paper_id"],
    }).status_code == 422
    ok = c.post("/vocab-exams", headers=ht, json={
        "user": "malin", "level": 2, "score": 88,
        "exam_date": "2026-10-11", "paper_id": p["paper_id"],
    })
    assert ok.status_code == 200, ok.text
    grading = c.get("/vocab-papers/grading", headers=ht).json()["data"]["papers"]
    row = next(x for x in grading if x["paper_id"] == p["paper_id"])
    assert row["level"] == -1
    assert any(e["user"] == "malin" and e["level"] == 2 and e["score"] == 88
               for e in row["exams"])
    assert c.post("/vocab-papers/generate", headers=ht, json={
        "level": 1, "scope": "learned"}).status_code == 422
    assert c.post("/vocab-papers/generate", headers=ht, json={
        "level": 1, "scope": "mixed"}).status_code == 422
    assert c.post("/vocab-papers/generate", headers=ht, json={
        "level": 1, "scope": "learned", "user": "ghost"}).status_code == 422
    hs = _login(c, "malin", "123456")
    assert c.post("/vocab-papers/generate", headers=hs, json={
        "level": 1, "scope": "learned", "user": "malin"}).status_code == 403

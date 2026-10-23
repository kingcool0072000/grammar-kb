"""词汇级别考试测试：解锁规则 / 成绩登记 / 考卷生成 / 权限。"""
from __future__ import annotations

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


def test_paper_generate_learned_scope(vex_env, monkeypatch):
    """learned 范围：按学生已学词（recite_word_progress）跨级混合出题。

    全链路：造进度 → 生成（MIX- 卷号/level=-1/词全部来自已学）→ 存档
    → 预览重建（跨级词面回填）→ 无 user/无进度 422。
    """
    _require_level_words(monkeypatch)
    c = _client(vex_env)
    ht = _login(c, "teacher", "123456")
    # 造 malin 的已学词：直接写 fce.db 的 recite_word_progress（L0/L1 各 30+）
    db = Path(str(vex_env)) / "fce.db"
    gdb = Path(__file__).resolve().parent.parent / "data" / "grammar.db"
    with sqlite3.connect(str(gdb)) as gcon:
        rows = gcon.execute(
            "SELECT word, level FROM vocab_word"
            " WHERE level IN (0, 1) AND gloss != '[]' LIMIT 130").fetchall()
    assert len(rows) >= 100, "词库 L0/L1 词量不足（fixture 前置条件）"
    with sqlite3.connect(str(db)) as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS recite_word_progress ("
            " user TEXT NOT NULL, word TEXT NOT NULL, level INTEGER,"
            " right INTEGER NOT NULL DEFAULT 0, wrong INTEGER NOT NULL DEFAULT 0,"
            " mastered_at TEXT, last_seen TEXT, PRIMARY KEY (user, word))")
        con.executemany(
            "INSERT OR REPLACE INTO recite_word_progress"
            " (user, word, level, right, wrong, last_seen) VALUES (?,?,?,?,?,datetime('now'))",
            [("malin", w, lv, 2, 0) for w, lv in rows])
    # 生成混合卷
    r = c.post("/vocab-papers/generate", headers=ht, json={
        "level": 1, "save": False, "scope": "learned", "user": "malin"})
    assert r.status_code == 200, r.text
    p = r.json()["data"]["paper"]
    assert p["paper_id"].startswith("MIX-") and p["level"] == -1
    assert p["scope"] == "learned" and p["user"] == "malin"
    assert len(p["words"]) == 50
    learned = {w for w, _ in rows}
    assert set(p["words"]) <= learned, "混合卷必须全部来自已学词"
    assert "已学全部混合卷" in r.json()["data"]["html"]
    # 存档 → 预览重建（跨级别回填词条）
    r2 = c.post("/vocab-papers/save", headers=ht, json=p)
    assert r2.status_code == 200
    r3 = c.get(f"/vocab-papers/{p['paper_id']}/preview", headers=ht)
    assert r3.status_code == 200
    pv = r3.json()["data"]
    assert pv["paper"]["words"] == 50 and "已学全部混合卷" in pv["html"]
    # 历史列表含混合卷
    r4 = c.get("/vocab-papers/history", headers=ht)
    assert any(x["paper_id"] == p["paper_id"] and x["level"] == -1
               for x in r4.json()["data"]["papers"])
    # 校验：learned 缺 user 422；scope 非法 422；无进度学生 422
    assert c.post("/vocab-papers/generate", headers=ht, json={
        "level": 1, "scope": "learned"}).status_code == 422
    assert c.post("/vocab-papers/generate", headers=ht, json={
        "level": 1, "scope": "mixed"}).status_code == 422
    assert c.post("/vocab-papers/generate", headers=ht, json={
        "level": 1, "scope": "learned", "user": "ghost"}).status_code == 422
    # 学生无权限生成
    hs = _login(c, "malin", "123456")
    assert c.post("/vocab-papers/generate", headers=hs, json={
        "level": 1, "scope": "learned", "user": "malin"}).status_code == 403

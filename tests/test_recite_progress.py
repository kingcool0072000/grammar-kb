"""背单词云端逐词进度端点测试：明细上报 / 进度查询 / 合并上云 / 权限。"""
from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.server import create_app  # noqa: E402


@pytest.fixture()
def recite_env(tmp_path, monkeypatch):
    """空 fce.db + 独立认证 / 成绩库（vocab_word 总量缺省时 levels.total 为 None）。"""
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    sqlite3.connect(tmp_path / "grammar.db").close()  # 合法空库
    return tmp_path


def _client(env) -> TestClient:
    return TestClient(create_app(str(env / "grammar.db"), str(env / "exam.db"),
                                 fce_db_path=str(env / "fce.db")))


def _login(c, user, password):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def test_details_upsert_and_progress(recite_env):
    c = _client(recite_env)
    h = _login(c, "malin", "123456")
    # 一组练习：1 新词对、1 新词错
    r = c.post("/recite/sessions", headers=h, json={
        "total": 2, "wrong": 1, "acc": 50, "duration_sec": 30,
        "wrong_words": ["bad"], "mode": "flip", "scope": "all",
        "details": [{"word": "good", "correct": True}, {"word": "bad", "correct": False}],
    })
    assert r.status_code == 200
    d = c.get("/recite/progress?include_words=true", headers=h).json()["data"]
    assert d["total_seen"] == 2 and d["mastered"] == 1
    words = {w["word"]: w for w in d["words"]}
    assert words["good"]["mastered"] is True
    assert words["bad"]["mastered"] is False
    # 补一次答对 → 翻正掌握
    c.post("/recite/sessions", headers=h, json={
        "total": 1, "wrong": 0, "acc": 100, "duration_sec": 5,
        "wrong_words": [], "mode": "flip", "scope": "wrongbook",
        "details": [{"word": "bad", "correct": True}],
    })
    d = c.get("/recite/progress", headers=h).json()["data"]
    assert d["mastered"] == 2
    # 学生看自己；无 user 权限提升
    assert d["user"] == "malin"


def test_progress_sync_idempotent(recite_env):
    c = _client(recite_env)
    h = _login(c, "malin", "123456")
    payload = {"progress": {"a": {"right": 3, "wrong": 0}, "b": {"right": 1, "wrong": 2}}}
    r1 = c.post("/recite/progress/sync", headers=h, json=payload)
    assert r1.status_code == 200
    # 重复上传同一快照：计数不翻倍（max 语义）
    c.post("/recite/progress/sync", headers=h, json=payload)
    d = c.get("/recite/progress?include_words=true", headers=h).json()["data"]
    words = {w["word"]: w for w in d["words"]}
    assert words["a"]["right"] == 3 and words["a"]["mastered"] is True
    assert words["b"]["right"] == 1 and words["b"]["wrong"] == 2
    # 与会话明细合并不冲突：云端 3 对 vs 本地 2 对 → max 取 3
    c.post("/recite/progress/sync", headers=h,
           json={"progress": {"a": {"right": 2, "wrong": 0}}})
    d = c.get("/recite/progress", headers=h).json()["data"]
    assert d["total_seen"] == 2


def test_progress_sync_bad_payload(recite_env):
    c = _client(recite_env)
    h = _login(c, "malin", "123456")
    assert c.post("/recite/progress/sync", headers=h, json={"progress": []}).status_code == 422
    assert c.post("/recite/progress/sync", headers=h, json={}).status_code == 422


def test_clear_progress_teacher_only(recite_env):
    c = _client(recite_env)
    hs = _login(c, "malin", "123456")
    ht = _login(c, "teacher", "123456")
    c.post("/recite/sessions", headers=hs, json={
        "total": 1, "wrong": 0, "acc": 100, "duration_sec": 1,
        "wrong_words": [], "mode": "flip", "scope": "all",
        "details": [{"word": "x", "correct": True}],
    })
    # 学生 403；教师不带 user 422
    assert c.delete("/recite/progress?user=malin", headers=hs).status_code == 403
    assert c.delete("/recite/progress", headers=ht).status_code == 422
    r = c.delete("/recite/progress?user=malin", headers=ht)
    assert r.status_code == 200 and r.json()["data"]["deleted"] == 1
    d = c.get("/recite/progress", headers=hs).json()["data"]
    assert d["total_seen"] == 0

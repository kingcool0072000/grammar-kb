"""计划表端点测试：周计划读写 / 实时完成度聚合 / 上周回顾 / 权限。"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.server import create_app  # noqa: E402

MON = (date.today() - timedelta(days=date.today().weekday())).isoformat()
PREV = (date.today() - timedelta(days=date.today().weekday() + 7)).isoformat()


@pytest.fixture()
def plan_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    sqlite3.connect(tmp_path / "grammar.db").close()
    return tmp_path


def _client(env) -> TestClient:
    return TestClient(create_app(str(env / "grammar.db"), str(env / "exam.db"),
                                 fce_db_path=str(env / "fce.db")))


def _login(c, user, password):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def test_plan_put_get_roundtrip(plan_env):
    c = _client(plan_env)
    ht = _login(c, "teacher", "123456")
    tasks = {"lectures": [29, 30], "vocab_goal": 380,
             "fce": ["Test1 RUE"], "reading": {"波西杰克逊1": 100}}
    r = c.put(f"/plan/weeks/{MON}", headers=ht, json={"tasks": tasks, "notes": "加油"})
    assert r.status_code == 200
    d = c.get("/plan/weeks?user=malin", headers=ht).json()["data"]
    assert d["current_week"] == MON
    w = next(x for x in d["weeks"] if x["week_start"] == MON)
    assert w["tasks"] == tasks and w["notes"] == "加油"
    assert "actuals" in w
    # 幂等覆盖
    c.put(f"/plan/weeks/{MON}", headers=ht, json={"tasks": {"lectures": [31]}, "notes": ""})
    d = c.get("/plan/weeks", headers=ht).json()["data"]
    w = next(x for x in d["weeks"] if x["week_start"] == MON)
    assert w["tasks"] == {"lectures": [31]}


def test_plan_validates_monday(plan_env):
    c = _client(plan_env)
    ht = _login(c, "teacher", "123456")
    today = date.today().isoformat()
    r = c.put(f"/plan/weeks/{today}", headers=ht, json={"tasks": {}, "notes": ""})
    if today == MON:
        pytest.skip("今天恰好是周一")
    assert r.status_code == 422
    assert c.put("/plan/weeks/not-a-date", headers=ht,
                 json={"tasks": {}, "notes": ""}).status_code == 422
    assert c.put(f"/plan/weeks/{MON}", headers=ht,
                 json={"notes": "x"}).status_code == 422  # 缺 tasks


def test_plan_teacher_only(plan_env):
    c = _client(plan_env)
    hs = _login(c, "malin", "123456")
    assert c.get("/plan/weeks", headers=hs).status_code == 403
    assert c.put(f"/plan/weeks/{MON}", headers=hs,
                 json={"tasks": {}}).status_code == 403


def test_plan_actuals_aggregate(plan_env):
    """完成度：周内成绩讲次 + 周内新掌握词（mastered_at 窗口过滤）。"""
    c = _client(plan_env)
    hs = _login(c, "malin", "123456")
    ht = _login(c, "teacher", "123456")
    # 本周一条成绩（今天必在本周窗口）
    c.post("/exams", headers=hs, json={"lecture": 29, "date": date.today().isoformat(),
                                       "score": 82, "wrong": []})
    # 本周新掌握一个词
    c.post("/recite/sessions", headers=hs, json={
        "total": 1, "wrong": 0, "acc": 100, "duration_sec": 5,
        "wrong_words": [], "mode": "flip", "scope": "all",
        "details": [{"word": "alpha", "correct": True}],
    })
    c.put(f"/plan/weeks/{MON}", headers=ht, json={"tasks": {"lectures": [29]}})
    d = c.get("/plan/weeks?user=malin", headers=ht).json()["data"]
    w = next(x for x in d["weeks"] if x["week_start"] == MON)
    assert w["actuals"]["lectures"] and w["actuals"]["lectures"][0]["lecture"] == 29
    assert w["actuals"]["vocab_mastered"] == 1
    # 上周回顾：无上周数据时摘要为空态，不报错
    rv = d["last_week_review"]
    assert rv["week_start"] == PREV
    assert rv["summary"]["vocab_mastered"] == 0

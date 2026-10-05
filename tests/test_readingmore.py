"""阅读更多（杂志阅读器）教师端会话列表端点测试。

数据隔离：GRAMMAR_KB_DATA_DIR 指到 tmp_path（readingmore.db 随之独立，
否则会误连本地 data/readingmore.db——生产快照，绝对不能写）。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.server import create_app  # noqa: E402


@pytest.fixture()
def rm_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    monkeypatch.setenv("GRAMMAR_KB_DATA_DIR", str(tmp_path))
    return tmp_path


def _client(env) -> TestClient:
    return TestClient(create_app(str(env / "grammar.db"), str(env / "exam.db"),
                                 fce_db_path=str(env / "fce.db")))


def _login(c, user, password="123456"):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _hb(sid, start_ms, **over):
    d = {
        "id": sid, "device": "dev-test", "book": "The Week Junior 2026-09",
        "start": start_ms, "end": start_ms + 600_000,
        "total_ms": 600_000, "focus_ms": 540_000, "engaged_ms": 480_000,
        "turn_count": 8, "pages": {"1": 100_000}, "clicks": {}, "ways": {},
        "vocab": {"add": 3, "del": 0}, "youdao": 0, "path_pts": 42,
        "new_lookups": [{"w": "satellite", "hit": 1, "t": start_ms + 1000},
                        {"w": "launch", "hit": 0, "t": start_ms + 2000}],
    }
    d.update(over)
    return d


def test_teacher_lists_and_filters(rm_env):
    c = _client(rm_env)
    t = _login(c, "teacher")
    s = _login(c, "malin")
    base = 1_800_000_000_000  # 任意固定毫秒时间戳
    assert c.post("/readingmore/api/session", headers=s,
                  json=_hb("rm-s1", base)).status_code == 200
    assert c.post("/readingmore/api/session", headers=t,
                  json=_hb("rm-t1", base + 3_600_000)).status_code == 200
    # 同会话重复心跳幂等：不新增行
    assert c.post("/readingmore/api/session", headers=s,
                  json=_hb("rm-s1", base, turn_count=9)).status_code == 200

    r = c.get("/readingmore/api/sessions", headers=t)
    assert r.status_code == 200, r.text
    items = r.json()["data"]
    assert len(items) == 2
    # start 倒序：后上报的 teacher 会话在前
    assert items[0]["id"] == "rm-t1" and items[0]["user"] == "teacher"
    row = next(x for x in items if x["id"] == "rm-s1")
    assert row["user"] == "malin"
    assert row["total_sec"] == 600 and row["engaged_sec"] == 480
    assert row["turns"] == 9  # 心跳覆盖后的值 and row["lookups"] == 2 and row["vocab_add"] == 3
    assert row["started_at"].endswith("Z") and "T" in row["started_at"]

    # user 过滤
    only = c.get("/readingmore/api/sessions",
                 headers=t, params={"user": "malin"}).json()["data"]
    assert len(only) == 1 and only[0]["user"] == "malin"


def test_student_forbidden_and_401(rm_env):
    c = _client(rm_env)
    s = _login(c, "malin")
    # 学生 token：鉴权中间件先拦 403（路径不在 _STUDENT_ALLOW）
    assert c.get("/readingmore/api/sessions", headers=s).status_code == 403
    # 未登录 401
    assert c.get("/readingmore/api/sessions").status_code == 401


def test_vocab_ops_bad_json_tolerated(rm_env):
    """存量行 vocab_ops 异常时不炸、按 0 处理。"""
    c = _client(rm_env)
    s = _login(c, "malin")
    c.post("/readingmore/api/session", headers=s, json=_hb("rm-bad", 1_800_000_000_000))
    import sqlite3
    db = rm_env / "readingmore.db"
    con = sqlite3.connect(db)
    con.execute("UPDATE rm_sessions SET vocab_ops='not-json' WHERE id='rm-bad'")
    con.commit()
    con.close()
    t = _login(c, "teacher")
    items = c.get("/readingmore/api/sessions", headers=t).json()["data"]
    assert items[0]["vocab_add"] == 0

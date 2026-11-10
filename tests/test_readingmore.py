"""阅读更多（杂志阅读器）只读历史端点测试。

阅读器已于 2026-11-10 退役（写入端点与静态页删除，归档 tag
archive/readingmore）：本文件只测教师学习日志的只读查询面
GET /readingmore/api/sessions，数据用 sqlite3 直接种库。

数据隔离：GRAMMAR_KB_DATA_DIR 指到 tmp_path（readingmore.db 随之独立，
否则会误连本地 data/readingmore.db——生产快照，绝对不能写）。
"""
from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.server import create_app  # noqa: E402

BASE_MS = 1_800_000_000_000  # 任意固定毫秒时间戳


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


def _seed(env, sid, user, *, start_ms=BASE_MS, turns=8, total=600_000,
          engaged=480_000, vocab_ops='{"add": 3, "del": 0}',
          book="The Week Junior 2026-09", lookups=2):
    """绕过已删的心跳端点，直接种只读历史数据。"""
    db = env / "readingmore.db"
    con = sqlite3.connect(db)
    con.execute(
        """INSERT INTO rm_sessions
           (id,user,device,book,start,end,total_ms,focus_ms,engaged_ms,
            turn_count,pages_json,clicks_json,ways_json,vocab_ops,
            youdao_count,path_pts,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (sid, user, "dev-test", book, start_ms, start_ms + 600_000,
         total, 540_000, engaged, turns, '{"1": 100000}', "{}", "{}",
         vocab_ops, 0, 42, start_ms))
    words = [("satellite", 1), ("launch", 0)][:lookups]
    for i, (w, hit) in enumerate(words):
        con.execute(
            "INSERT INTO rm_lookups(sid,user,word,hit,ts) VALUES (?,?,?,?,?)",
            (sid, user, w, hit, start_ms + 1000 * (i + 1)))
    con.commit()
    con.close()


def test_teacher_lists_and_filters(rm_env):
    c = _client(rm_env)
    _seed(rm_env, "rm-s1", "malin")
    _seed(rm_env, "rm-t1", "teacher", start_ms=BASE_MS + 3_600_000, turns=5)
    t = _login(c, "teacher")

    r = c.get("/readingmore/api/sessions", headers=t)
    assert r.status_code == 200, r.text
    items = r.json()["data"]
    assert len(items) == 2
    # start 倒序：后上报的 teacher 会话在前
    assert items[0]["id"] == "rm-t1" and items[0]["user"] == "teacher"
    row = next(x for x in items if x["id"] == "rm-s1")
    assert row["user"] == "malin"
    assert row["total_sec"] == 600 and row["engaged_sec"] == 480
    assert row["turns"] == 8
    assert row["lookups"] == 2 and row["vocab_add"] == 3
    assert row["started_at"].endswith("Z") and "T" in row["started_at"]

    # user 过滤
    only = c.get("/readingmore/api/sessions",
                 headers=t, params={"user": "malin"}).json()["data"]
    assert len(only) == 1 and only[0]["user"] == "malin"


def test_bad_vocab_ops_tolerated(rm_env):
    """存量行 vocab_ops 异常时不炸、按 0 处理。"""
    c = _client(rm_env)
    _seed(rm_env, "rm-bad", "malin", vocab_ops="not-json")
    t = _login(c, "teacher")
    items = c.get("/readingmore/api/sessions", headers=t).json()["data"]
    assert items[0]["vocab_add"] == 0


def test_student_forbidden_and_401(rm_env):
    c = _client(rm_env)
    s = _login(c, "malin")
    # 学生 token：鉴权中间件先拦 403（只读历史是教师专属，不在 _STUDENT_ALLOW）
    assert c.get("/readingmore/api/sessions", headers=s).status_code == 403
    # 未登录 401
    assert c.get("/readingmore/api/sessions").status_code == 401


def test_write_endpoints_retired(rm_env):
    """阅读器退役：心跳/统计端点不复存在，历史面只读。"""
    c = _client(rm_env)
    s = _login(c, "malin")
    t = _login(c, "teacher")
    # 学生：路径已撤出 _STUDENT_ALLOW → 中间件 403；教师 token 穿过
    # API 路由落到根 StaticFiles 挂载 → 非 GET 一律 405
    assert c.post("/readingmore/api/session", headers=s,
                  json={"id": "x"}).status_code == 403
    assert c.post("/readingmore/api/session", headers=t,
                  json={"id": "x"}).status_code == 405
    assert c.get("/readingmore/api/stats", headers=s).status_code == 403
    assert c.get("/readingmore/api/stats", headers=t).status_code == 404

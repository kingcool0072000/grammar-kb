"""登录认证与角色权限测试。

- UserStore：默认播种、校验、坏密码
- token：签发/读取、过期、篡改
- HTTP 权限矩阵：未登录 401；学生只放行 vocabulary/POST exams；教师全通
"""
import os
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.auth import UserStore, make_token, read_token  # noqa: E402
from grammar_kb.server import create_app  # noqa: E402


@pytest.fixture()
def users_file(tmp_path, monkeypatch):
    p = tmp_path / "users.json"
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(p))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    return p


@pytest.fixture()
def client(users_file):
    app = create_app()
    with TestClient(app) as c:
        yield c


def _login(c, user, password):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["data"]


# ---- UserStore / token ------------------------------------------------------ #

def test_user_store_seeds_defaults(users_file):
    s = UserStore()
    assert s.verify("malin", "123456") == "student"
    assert s.verify("teacher", "123456") == "teacher"
    assert s.verify("malin", "wrong") is None
    assert s.verify("nobody", "123456") is None
    # 播种后确实落盘
    assert users_file.exists()


def test_token_roundtrip(users_file):
    t = make_token("malin", "malin", ttl=60)
    assert read_token(t) is not None
    # 过期
    t2 = make_token("malin", "malin", ttl=-1)
    assert read_token(t2) is None
    # 篡改
    assert read_token(t[:-4] + "AAAA") is None
    assert read_token("not-a-token") is None


# ---- HTTP 权限矩阵 ---------------------------------------------------------- #

def test_login_wrong_password(client):
    r = client.post("/auth/login", json={"user": "malin", "password": "nope"})
    assert r.status_code == 401


def test_no_token_401(client):
    assert client.get("/stats").status_code == 401
    assert client.get("/exams").status_code == 401
    # 白名单放行
    assert client.get("/").status_code == 200


def test_student_scope(client):
    tok = _login(client, "malin", "123456")["token"]
    h = {"Authorization": f"Bearer {tok}"}
    # 学生可用：背单词数据 + 提交成绩
    assert client.get("/vocabulary", headers=h).status_code == 200
    r = client.post(
        "/exams", headers=h, json={"lecture": 1, "date": "2026-08-29", "score": 90}
    )
    assert r.status_code == 200
    # 学生禁用：管理成绩、课程讲次、知识点检索
    assert client.get("/exams", headers=h).status_code == 403
    assert client.delete("/exams/1", headers=h).status_code == 403
    assert client.put(
        "/exams/1", headers=h, json={"lecture": 1, "date": "2026-08-29", "score": 1}
    ).status_code == 403
    assert client.get("/lectures", headers=h).status_code == 403
    assert client.get("/search", headers=h, params={"q": "时态"}).status_code == 403
    # 学生提交的成绩自己不能删（教师才能删）——这里拿到刚建记录的 id
    exam_id = r.json()["data"]["id"]
    assert client.delete(f"/exams/{exam_id}", headers=h).status_code == 403


def test_teacher_full_access(client):
    tok = _login(client, "teacher", "123456")["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/stats", headers=h).status_code == 200
    r = client.post(
        "/exams", headers=h, json={"lecture": 2, "date": "2026-08-29", "score": 88}
    )
    assert r.status_code == 200
    exam_id = r.json()["data"]["id"]
    assert client.get("/exams", headers=h).status_code == 200
    assert client.delete(f"/exams/{exam_id}", headers=h).status_code == 200


def test_bad_token_401(client):
    h = {"Authorization": "Bearer garbage.token"}
    assert client.get("/stats", headers=h).status_code == 401

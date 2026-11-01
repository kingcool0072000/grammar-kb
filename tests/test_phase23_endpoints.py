"""Phase 2/3 新端点的权限与形状回归。

- GET /points：全量知识点（教师备课中心）。历史上前端用 9 个高频字
  探测式 /search 聚合只覆盖 95%+，此端点一次给全；学生不在白名单。
- GET /homework-admin/wrong-bank：错题题库（含作业卷答案+解析）。
  此前内嵌前端 JS 随 bundle 人人可见——教师专属门是本轮安全修复，
  必须有测试锁住。
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.db import GrammarDB  # noqa: E402
from grammar_kb.models import KnowledgePoint, Lecture  # noqa: E402
from grammar_kb.server import create_app  # noqa: E402


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """隔离五库 + 预置 1 个知识点。env 必须在 create_app 之前落定。"""
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    monkeypatch.setenv("GRAMMAR_KB_DB", str(tmp_path / "grammar.db"))
    monkeypatch.setenv("GRAMMAR_KB_FCE_DB", str(tmp_path / "fce.db"))
    monkeypatch.setenv("GRAMMAR_KB_EXAM_DB", str(tmp_path / "exam.db"))
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DB", str(tmp_path / "library.db"))
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DIR", str(tmp_path / "lib"))
    with GrammarDB(os.environ["GRAMMAR_KB_DB"]) as db:
        lid = db.upsert_lecture(Lecture(
            number=25, title="动词时态3", full_title="第二十五讲 动词时态3",
            category="时态", subcategory="动词时态", page_count=7,
        ))
        db.insert_kp(KnowledgePoint(
            title="过去将来时的定义", lecture_number=25, category="时态",
            section_path="I.过去将来时 > 1.定义",
            body_md="从过去某时间看将要发生。He would come.",
            markers=[], source_page=1,
        ), lid)
    app = create_app()
    with TestClient(app) as c:
        yield c


def _login(client, user, password="123456"):
    r = client.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["data"]["token"]}


def test_points_teacher_full_list(client):
    h = _login(client, "teacher")
    r = client.get("/points", headers=h)
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    assert len(items) >= 1
    assert items[0]["title"] == "过去将来时的定义"
    assert items[0]["lecture_number"] == 25


def test_points_student_forbidden(client):
    h = _login(client, "malin")
    assert client.get("/points", headers=h).status_code == 403


def test_points_requires_auth(client):
    assert client.get("/points").status_code == 401


def test_wrong_bank_teacher_shape(client):
    h = _login(client, "teacher")
    r = client.get("/homework-admin/wrong-bank", headers=h)
    assert r.status_code == 200
    bank = r.json()["data"]
    # 数据来自仓库 data/wrongbank.json（测验平台导出，28 讲 339 题）
    assert isinstance(bank, dict) and len(bank) >= 20
    lec28 = bank.get("28") or bank.get(28)
    assert lec28, "讲次 28（四步推理链覆盖讲）应在题库中"
    first = next(iter(lec28.values()))
    assert set(first) == {"q", "exp"}


def test_wrong_bank_student_forbidden(client):
    """安全修复回归锁：错题解析（作业卷答案）绝不可下发学生。"""
    h = _login(client, "malin")
    assert client.get("/homework-admin/wrong-bank", headers=h).status_code == 403


def test_wrong_bank_requires_auth(client):
    assert client.get("/homework-admin/wrong-bank").status_code == 401

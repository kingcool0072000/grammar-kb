"""阅读训练端点测试：文章列表权限 / 派生文 CRUD / 录音提交与 10 分制批改。"""
from __future__ import annotations

import base64
import shutil
import sqlite3

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.server import create_app  # noqa: E402

DERIVED_TEXT = (
    "Emma started her first Saturday job at a bakery near her home. "
    "She wakes up at seven in the morning and helps the baker prepare "
    "fresh bread for the customers. Although the work is tiring, she "
    "enjoys earning her own money and has learned how to serve customers politely."
)


@pytest.fixture()
def reading_env(tmp_path, monkeypatch):
    """独立 fce.db（只留 1 段 base）+ 独立认证/成绩库。"""
    src = shutil.copy(
        __file__.rsplit("/", 1)[0] + "/../data/fce.db", tmp_path / "fce.db"
    )
    conn = sqlite3.connect(tmp_path / "fce.db")
    conn.execute("DELETE FROM reading_article WHERE kind != 'base' OR base_key NOT IN ('T1P1')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    sqlite3.connect(tmp_path / "grammar.db").close()  # 合法空库
    return tmp_path


def _client(env) -> TestClient:
    return TestClient(create_app(str(env / "grammar.db"), str(env / "exam.db"),
                               fce_db_path=str(env / "fce.db")))


def _login(c, user, password="123456"):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def test_reading_full_flow(reading_env):
    c = _client(reading_env)
    t, s = _login(c, "teacher"), _login(c, "student")

    # 教师：base 原文段（kind=base）；学生：默认列表只有派生（空）
    arts_t = c.get("/reading/articles", headers=t, params={"kind": "base"}).json()["data"]
    assert arts_t and all(a["kind"] == "base" for a in arts_t)
    assert c.get("/reading/articles", headers=s).json()["data"] == []
    # 教师默认列表同样只有派生（空）
    assert c.get("/reading/articles", headers=t).json()["data"] == []
    # 学生请求 base 列表 → 403
    assert c.get("/reading/articles", headers=s, params={"kind": "base"}).status_code == 403

    # 学生读 base 正文 → 403
    base_id = arts_t[0]["id"]
    assert c.get(f"/reading/articles/{base_id}", headers=s).status_code == 403

    # 教师新增派生文
    r = c.post("/reading/articles", headers=t, json={
        "base_key": "T1P1", "title": "Emma 的面包房周六工",
        "text": DERIVED_TEXT, "source": "测试样例",
    })
    assert r.status_code == 200, r.text
    art = r.json()["data"]
    assert art["kind"] == "derived" and art["words"] >= 45

    # 学生能看到派生文并可读正文
    assert [a["id"] for a in c.get("/reading/articles", headers=s).json()["data"]] == [art["id"]]
    assert "bakery" in c.get(f"/reading/articles/{art['id']}", headers=s).json()["data"]["text"]

    # 学生提交录音
    audio = base64.b64encode(b"FAKE-WEBM-AUDIO").decode()
    r = c.post("/reading/recordings", headers=s, json={
        "article_id": art["id"], "audio_b64": audio,
        "mime": "audio/webm", "duration_sec": 95,
    })
    assert r.status_code == 200, r.text
    rec = r.json()["data"]
    assert rec["status"] == "pending"

    # 列表：学生只看自己的；教师按 pending 拉
    assert len(c.get("/reading/recordings", headers=s).json()["data"]) == 1
    assert len(c.get("/reading/recordings", headers=t, params={"status": "pending"}).json()["data"]) == 1

    # 教师打分 8/10
    r = c.put(f"/reading/recordings/{rec['id']}", headers=t, json={
        "score": 8, "comment": "流利度好，注意 pastime 重音。",
    })
    assert r.status_code == 200
    assert r.json()["data"]["teacher_score"] == 8
    assert r.json()["data"]["status"] == "graded"

    # 分数越界 422 / 空 base64 422 / 不存在文章 404
    assert c.put(f"/reading/recordings/{rec['id']}", headers=t, json={"score": 11}).status_code == 422
    assert c.post("/reading/recordings", headers=s, json={
        "article_id": art["id"], "audio_b64": "", "duration_sec": 10}).status_code == 422
    assert c.post("/reading/recordings", headers=s, json={
        "article_id": 99999, "audio_b64": audio, "duration_sec": 10}).status_code == 404

    # 学生不能增删改派生文
    assert c.post("/reading/articles", headers=s, json={
        "base_key": "T1P1", "text": "x" * 30}).status_code == 403
    assert c.put(f"/reading/articles/{art['id']}", headers=s, json={"title": "x"}).status_code == 403
    assert c.delete(f"/reading/articles/{art['id']}", headers=s).status_code == 403

    # 教师编辑 + 删除
    assert c.put(f"/reading/articles/{art['id']}", headers=t, json={"title": "改名"}).json()["data"]["title"] == "改名"
    assert c.delete(f"/reading/articles/{art['id']}", headers=t).status_code == 200
    assert c.get("/reading/articles", headers=s).json()["data"] == []


def test_dict_lookup_student_ok(reading_env):
    """学生查单词（阅读练习选中词查 ECDICT）。"""
    c = _client(reading_env)
    s = _login(c, "student")
    r = c.get("/dict/pastime", headers=s)
    assert r.status_code == 200
    assert "消遣" in r.json()["data"]["gloss"]

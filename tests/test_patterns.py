"""句型条目 pattern_item：导入幂等 / 校验 / 查询 / 端点权限。

素材表只管增查（提交链路在 video_study 复述里），教师导入走
/patterns-admin/import，学生只读 /patterns。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from grammar_kb import patterns

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.server import create_app  # noqa: E402

_PROJECT = Path(__file__).resolve().parent.parent
_SAMPLE = _PROJECT / "data" / "patterns" / "lecture-30-passive.json"

_ITEMS = [
    {"lecture": 30, "sentence": "The window was broken.",
     "translation": "窗户被打破了", "hints": ["What was broken?"]},
    {"lecture": 30, "sentence": "English is spoken here.",
     "translation": "这里说英语", "hints": []},
    {"lecture": 29, "sentence": "The book was written in 2010.",
     "translation": "这本书写于 2010 年", "hints": ["When was it written?"]},
]


# ---- 库函数：导入幂等 / 校验 / 查询 ----

def test_import_idempotent_and_get(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAMMAR_KB_FCE_DB", str(tmp_path / "fce.db"))
    n = patterns.import_items(_ITEMS)
    assert n == 3
    # 同 lecture+sentence 重导 → 全部跳过
    assert patterns.import_items(_ITEMS) == 0
    # 顺序变一下再导（含同句）也只跳过重复的
    assert patterns.import_items([_ITEMS[0], {"lecture": 31,
                                              "sentence": "New one."}]) == 1
    got = patterns.get(1)
    assert got["lecture"] == 30 and got["sentence"] == "The window was broken."
    assert got["translation"] == "窗户被打破了"
    assert got["hints"] == ["What was broken?"]
    assert patterns.get(9999) is None
    # 清单与按讲次过滤
    assert len(patterns.list_all()) == 4
    assert [p["sentence"] for p in patterns.list_all(30)] == [
        "The window was broken.", "English is spoken here."]
    assert patterns.list_all(99) == []


def test_import_validation_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAMMAR_KB_FCE_DB", str(tmp_path / "fce.db"))
    with pytest.raises(ValueError, match="lecture"):
        patterns.import_items([{"lecture": 0, "sentence": "x"}])
    with pytest.raises(ValueError, match="lecture"):
        patterns.import_items([{"sentence": "x"}])  # 缺 lecture
    with pytest.raises(ValueError, match="sentence"):
        patterns.import_items([{"lecture": 30, "sentence": "  "}])
    with pytest.raises(ValueError, match="hints"):
        patterns.import_items([{"lecture": 30, "sentence": "x", "hints": [1, 2]}])
    with pytest.raises(ValueError, match="须为对象"):
        patterns.import_items(["not-a-dict"])
    # 整批拒绝：合法混非法时一条都不入库
    with pytest.raises(ValueError):
        patterns.import_items([_ITEMS[0], {"lecture": 30}])
    assert patterns.list_all() == []


def test_sample_data_file_imports(tmp_path, monkeypatch):
    """data/patterns/lecture-30-passive.json 与导入格式一致，可直接进。"""
    monkeypatch.setenv("GRAMMAR_KB_FCE_DB", str(tmp_path / "fce.db"))
    items = json.loads(_SAMPLE.read_text(encoding="utf-8"))
    assert len(items) == 5
    assert all(x["lecture"] == 30 and x["sentence"] and x["hints"]
               for x in items)
    assert patterns.import_items(items) == 5


# ---- 端点：权限与导入 ----

def _client(tmp_path, monkeypatch) -> TestClient:
    for name in ("grammar.db", "exam.db", "fce.db"):
        sqlite3.connect(str(tmp_path / name)).close()
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    # plan/boost、patterns 直读该变量，必须与 create_app 的 fce 库同文件
    monkeypatch.setenv("GRAMMAR_KB_FCE_DB", str(tmp_path / "fce.db"))
    return TestClient(create_app(str(tmp_path / "grammar.db"),
                                 str(tmp_path / "exam.db"),
                                 fce_db_path=str(tmp_path / "fce.db")))


def _login(c, user, password="123456"):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def test_patterns_endpoints_and_permissions(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    t, s = _login(c, "teacher"), _login(c, "malin")
    # 学生不能导入（教师专属，wrong-bank 同款闸门）
    r = c.post("/patterns-admin/import", headers=s,
               json={"items": [{"lecture": 30, "sentence": "x"}]})
    assert r.status_code == 403
    # 教师导入 → 计数；重导幂等 → 0
    r = c.post("/patterns-admin/import", headers=t,
               json={"items": [{"lecture": 30, "sentence": "S1.",
                                "translation": "一", "hints": ["h?"]},
                               {"lecture": 29, "sentence": "S2."}]})
    assert r.status_code == 200 and r.json()["data"] == {"imported": 2}
    r = c.post("/patterns-admin/import", headers=t,
               json={"items": [{"lecture": 30, "sentence": "S1."}]})
    assert r.json()["data"] == {"imported": 0}
    # 非法条目 → 422
    assert c.post("/patterns-admin/import", headers=t,
                  json={"items": [{"lecture": 30}]}).status_code == 422
    # 学生可读；教师也可读；?lecture= 过滤生效
    r = c.get("/patterns", headers=s)
    assert r.status_code == 200 and len(r.json()["data"]) == 2
    r = c.get("/patterns?lecture=29", headers=s)
    assert [p["sentence"] for p in r.json()["data"]] == ["S2."]

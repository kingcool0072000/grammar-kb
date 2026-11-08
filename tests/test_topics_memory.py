"""专题节点口诀卡（memory 可选字段）：校验 / 展开 / 存量兼容 / 翻面自评打卡。

内容政策不变：作业卷题不进专题。memory 是纯增量字段——存量 topic JSON
没有该键照常导入与展开，展开结果不带 memory 键（前端按有无该键出翻面卡）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from grammar_kb.topics import TopicStore

try:  # 提升档打卡（并行车道 plan/boost.py）：未就位时跳过 boost 断言
    from grammar_kb.plan import boost  # noqa: F401
    _HAVE_BOOST = True
except ImportError:
    _HAVE_BOOST = False

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.server import create_app  # noqa: E402

_PROJECT = Path(__file__).resolve().parent.parent
_TOPIC_JSON = _PROJECT / "data" / "topics" / "lecture-29-passive-1.json"

_MEMORY_NODE = {
    "key": "k1",
    "title": "of / from 怎么区分？",
    "explain": "<p>讲解</p>",
    "memory": {
        "front": "hear of 和 hear from 有什么区别？",
        "back": "hear of 听说过（人/事）；hear from 收到来信/消息（from 后是人）。"
    },
}


def _store(tmp_path):
    return TopicStore(db_path=str(tmp_path / "fce.db"),
                      grammar_db_path=str(tmp_path / "grammar.db"))


def _content(nodes: list) -> dict:
    return {"nodes": nodes}


# ---- 校验 ----

def test_validate_memory_errors():
    v = TopicStore.validate_content
    base = {"key": "k", "title": "t"}
    # memory 不是对象
    errs = v(_content([{**base, "memory": "口诀"}]))
    assert any("memory 须为对象" in e for e in errs)
    # front / back 缺失或空
    errs = v(_content([{**base, "memory": {"back": "b"}}]))
    assert any("memory.front 不能为空" in e for e in errs)
    errs = v(_content([{**base, "memory": {"front": "f", "back": "  "}}]))
    assert any("memory.back 不能为空" in e for e in errs)
    # 超长：front ≤120、back ≤600
    errs = v(_content([{**base, "memory": {"front": "长" * 121, "back": "b"}}]))
    assert any("memory.front 超 120 字" in e for e in errs)
    errs = v(_content([{**base, "memory": {"front": "f", "back": "长" * 601}}]))
    assert any("memory.back 超 600 字" in e for e in errs)
    # 合法值：空错误列表
    assert v(_content([_MEMORY_NODE])) == []
    # 合法边界：front 恰 120、back 恰 600
    ok = {"key": "k", "title": "t",
          "memory": {"front": "问" * 120, "back": "答" * 600}}
    assert v(_content([ok])) == []


# ---- 导入与展开 ----

def test_memory_roundtrip_and_expand(tmp_path):
    store = _store(tmp_path)
    meta = {"topic_id": "t-mem", "title": "口诀卡专题"}
    got = store.upsert_topic(meta, _content([_MEMORY_NODE]))
    node = got["nodes"][0]
    assert node["memory"]["front"] == _MEMORY_NODE["memory"]["front"]
    assert node["memory"]["back"] == _MEMORY_NODE["memory"]["back"]
    # 单节点读取辅助：命中（含 memory）、查不到返回 None
    hit = store.get_node("t-mem", "k1")
    assert hit is not None and hit["memory"]["front"].startswith("hear of")
    assert store.get_node("t-mem", "nope") is None
    assert store.get_node("no-such-topic", "k1") is None


def test_legacy_topic_no_memory_key(tmp_path):
    """存量专题（无 memory 字段）：照常导入，展开结果不带 memory 键。"""
    data = json.loads(_TOPIC_JSON.read_text(encoding="utf-8"))
    store = _store(tmp_path)
    got = store.upsert_topic(data["meta"], data["content"])
    assert got["topic_id"] == "lecture-29-passive-1"
    for n in got["nodes"]:
        assert "memory" not in n
        assert store.get_node("lecture-29-passive-1", n["key"])["key"] == n["key"]
    # 存量 JSON 重复导入（覆盖路径）也不受 memory 校验影响
    assert store.upsert_topic(data["meta"], data["content"])["topic_id"]


# ---- 端点：翻面自评过关（memory 打卡） ----

def _client(tmp_path, monkeypatch) -> TestClient:
    """空白三库 + 账号/密钥隔离（不碰仓库 data/ 下的真库）。

    GRAMMAR_KB_FCE_DB 必须一并隔离：plan/boost 等模块直读该环境变量，
    否则打卡会写进真库。
    """
    for name in ("grammar.db", "exam.db", "fce.db"):
        sqlite3.connect(str(tmp_path / name)).close()
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    monkeypatch.setenv("GRAMMAR_KB_FCE_DB", str(tmp_path / "fce.db"))
    return TestClient(create_app(str(tmp_path / "grammar.db"),
                                 str(tmp_path / "exam.db"),
                                 fce_db_path=str(tmp_path / "fce.db")))


def _login(c, user, password="123456"):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def test_memory_pass_endpoint(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    t, s = _login(c, "teacher"), _login(c, "malin")
    r = c.post("/topics-admin/import", headers=t, json={
        "meta": {"topic_id": "lecture-30-passive-2", "title": "第30讲 · 被动语态②",
                 "assigned_to": "malin"},
        "content": _content([_MEMORY_NODE])})
    assert r.status_code == 200, r.text
    url = "/topics/lecture-30-passive-2/nodes/k1/memory-pass"
    # 学生自评过关：200 {"ok": True}
    r = c.put(url, headers=s)
    assert r.status_code == 200 and r.json()["data"] == {"ok": True}
    # 专题改派给别的学生后，malin 不可见：403
    c.put("/topics-admin/lecture-30-passive-2/assigned", headers=t,
          json={"assigned_to": "nobody"})
    assert c.put(url, headers=s).status_code == 403
    c.put("/topics-admin/lecture-30-passive-2/assigned", headers=t,
          json={"assigned_to": "malin"})
    # 下架后学生不可见：403；教师不受限
    c.put("/topics-admin/lecture-30-passive-2/published", headers=t,
          json={"on": False})
    assert c.put(url, headers=s).status_code == 403
    assert c.put(url, headers=t).status_code == 200
    c.put("/topics-admin/lecture-30-passive-2/published", headers=t,
          json={"on": True})
    # 没有口诀卡的节点：422；不存在的节点/专题：404
    c.post("/topics-admin/import", headers=t, json={
        "meta": {"topic_id": "t-plain", "title": "无口诀"},
        "content": _content([{"key": "p1", "title": "普通节点"}])})
    assert c.put("/topics/t-plain/nodes/p1/memory-pass", headers=s).status_code == 422
    assert c.put("/topics/lecture-30-passive-2/nodes/zz/memory-pass",
                 headers=s).status_code == 404
    assert c.put("/topics/no-such/nodes/k1/memory-pass",
                 headers=s).status_code == 404


@pytest.mark.skipif(not _HAVE_BOOST, reason="plan.boost 未就位，集成后补跑")
def test_memory_pass_writes_boost_log(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    t, s = _login(c, "teacher"), _login(c, "malin")
    c.post("/topics-admin/import", headers=t, json={
        "meta": {"topic_id": "t-mem2", "title": "口诀", "assigned_to": "malin"},
        "content": _content([_MEMORY_NODE])})
    assert c.put("/topics/t-mem2/nodes/k1/memory-pass", headers=s).status_code == 200
    with sqlite3.connect(tmp_path / "fce.db") as conn:
        row = conn.execute(
            "SELECT kind, passed FROM boost_log"
            " WHERE user='malin' AND item_ref='memory:t-mem2:k1'").fetchone()
    assert row == ("memory", 1)

"""HTTP 服务端点测试（需要 server extra：``uv sync --extra server``）。

响应统一为 {code, message, data}；并校验 CORS 头。
"""
import os
import tempfile

import pytest

# 需 server 依赖；缺失则整体跳过
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.db import GrammarDB  # noqa: E402
from grammar_kb.models import Block, KnowledgePoint, Lecture, Marker  # noqa: E402
from grammar_kb.server import create_app  # noqa: E402


@pytest.fixture()
def client():
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "test.db")
        with GrammarDB(db_path) as db:
            lid = db.upsert_lecture(
                Lecture(
                    number=25,
                    title="动词时态3",
                    full_title="第二十五讲 动词时态3",
                    category="时态",
                    subcategory="动词时态",
                    page_count=7,
                )
            )
            db.insert_kp(
                KnowledgePoint(
                    title="过去将来时的定义",
                    lecture_number=25,
                    category="时态",
                    section_path="I.过去将来时 > 1.定义",
                    body_md="从过去某时间看将要发生。He would come.",
                    markers=[Marker(marker="would", tense="过去将来时")],
                    source_page=1,
                ),
                lid,
            )
            db.insert_blocks(
                lid,
                [
                    Block(kind="heading", text_md="## I.过去将来时", page=1, seq=0),
                    Block(kind="para", text_md="正文段落。", page=1, seq=1),
                ],
            )
        app = create_app(db_path)
        with TestClient(app) as c:
            yield c


def test_envelope_success(client):
    r = client.get("/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["message"] == "ok"
    assert body["data"]["knowledge_points"] >= 1


def test_root(client):
    body = client.get("/").json()
    assert body["code"] == 0
    assert body["data"]["service"] == "grammar-kb"


def test_lectures_list(client):
    items = client.get("/lectures").json()["data"]
    assert any(l["number"] == 25 for l in items)


def test_lecture_markdown(client):
    body = client.get("/lectures/25").json()["data"]
    assert body["format"] == "markdown"
    assert "过去将来时" in body["content"]


def test_lecture_html(client):
    content = client.get("/lectures/25?format=html").json()["data"]["content"]
    assert content.startswith("<!DOCTYPE html>")
    assert "过去将来时" in content


def test_lecture_404(client):
    r = client.get("/lectures/999")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == 404
    assert "999" in body["message"]
    assert body["data"] is None


def test_kp(client):
    body = client.get("/kp/1").json()["data"]
    assert "过去将来时的定义" in body["content"]


def test_kp_html(client):
    content = client.get("/kp/1?format=html").json()["data"]["content"]
    assert "<html" in content


def test_kp_404(client):
    r = client.get("/kp/99999")
    assert r.status_code == 404
    assert r.json()["code"] == 404


def test_search(client):
    body = client.get("/search?q=过去将来时").json()["data"]
    assert body["count"] >= 1


def test_search_with_category(client):
    assert client.get("/search?q=过去将来时&category=时态").json()["data"]["count"] >= 1
    assert client.get("/search?q=过去将来时&category=词法").json()["data"]["count"] == 0


def test_markers(client):
    items = client.get("/markers?category=时态").json()["data"]["items"]
    assert any(it["marker"] == "would" for it in items)


def test_relation(client):
    assert "items" in client.get("/relation?type=主将从现").json()["data"]


def test_cors_header_present(client):
    """OPTIONS 预检或普通 GET 都应带 CORS 允许头。"""
    r = client.get("/stats", headers={"Origin": "http://example.com"})
    assert r.headers.get("access-control-allow-origin") in ("*", "http://example.com")


def test_cors_preflight(client):
    r = client.options(
        "/search",
        headers={
            "Origin": "http://example.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") in ("*", "http://example.com")

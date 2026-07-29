"""HTTP 服务端点测试（需要 server extra：``uv sync --extra server``）。"""
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
        # 用临时库构造 app
        app = create_app(db_path)
        with TestClient(app) as c:
            yield c


def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["service"] == "grammar-kb"


def test_stats(client):
    r = client.get("/stats")
    assert r.status_code == 200
    assert r.json()["knowledge_points"] >= 1


def test_lectures_list(client):
    r = client.get("/lectures")
    assert r.status_code == 200
    data = r.json()
    assert any(l["number"] == 25 for l in data)


def test_lecture_markdown(client):
    r = client.get("/lectures/25")
    assert r.status_code == 200
    body = r.json()
    assert body["format"] == "markdown"
    assert "过去将来时" in body["content"]


def test_lecture_html(client):
    r = client.get("/lectures/25?format=html")
    assert r.status_code == 200
    content = r.json()["content"]
    assert content.startswith("<!DOCTYPE html>")
    assert "过去将来时" in content


def test_lecture_404(client):
    assert client.get("/lectures/999").status_code == 404


def test_kp(client):
    r = client.get("/kp/1")
    assert r.status_code == 200
    assert "过去将来时的定义" in r.json()["content"]


def test_kp_html(client):
    r = client.get("/kp/1?format=html")
    assert r.status_code == 200
    assert "<html" in r.json()["content"]


def test_search(client):
    r = client.get("/search?q=过去将来时")
    assert r.status_code == 200
    assert r.json()["count"] >= 1


def test_search_with_category(client):
    assert client.get("/search?q=过去将来时&category=时态").json()["count"] >= 1
    assert client.get("/search?q=过去将来时&category=词法").json()["count"] == 0


def test_markers(client):
    r = client.get("/markers?category=时态")
    assert r.status_code == 200
    assert any(it["marker"] == "would" for it in r.json()["items"])


def test_relation(client):
    r = client.get("/relation?type=主将从现")
    assert r.status_code == 200
    assert "items" in r.json()

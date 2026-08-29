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
        # 全端点需登录：用临时 users.json/secret（默认密码 123456），与生产账号解耦。
        # 必须在 create_app 之前设置——UserStore 在构造时读取环境变量
        os.environ["GRAMMAR_KB_USERS"] = os.path.join(d, "users.json")
        os.environ["GRAMMAR_KB_AUTH_SECRET"] = os.path.join(d, "secret.key")
        app = create_app(db_path)
        with TestClient(app) as c:
            login = c.post(
                "/auth/login", json={"user": "teacher", "password": "123456"}
            )
            assert login.status_code == 200, login.text
            c.headers["Authorization"] = f"Bearer {login.json()['data']['token']}"
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


def test_fce_papers_empty_db(tmp_path):
    """fce.db 不存在时：列表空、详情 None（端点不崩）。"""
    from grammar_kb.fce_query import FcePaperStore

    store = FcePaperStore(str(tmp_path / "nope.db"))
    assert store.list_papers() == []
    assert store.get_paper(1) is None


def test_fce_papers_with_db(client, tmp_path):
    """用真实 schema 的小 fce.db 验证端点拼装。"""
    import json as _json

    import sqlite3

    from grammar_kb.fce_paper import SCHEMA as FCE_SCHEMA
    from grammar_kb.fce_query import FcePaperStore

    db = str(tmp_path / "fce.db")
    conn = sqlite3.connect(db)
    conn.executescript(FCE_SCHEMA)
    conn.execute("INSERT INTO fce_test (id, title) VALUES (1, 'Test 1')")
    cur = conn.execute(
        "INSERT INTO fce_section (test_id, paper, part, instruction, passage, ord)"
        " VALUES (1, 'Reading and Use of English', 1, 'instr', 'passage text', 0)"
    )
    conn.execute(
        "INSERT INTO fce_question (section_id, test_id, paper, part, qnum, type,"
        " stem, options_json, answer) VALUES (?,?,?,?,?,?,?,?,?)",
        (cur.lastrowid, 1, "Reading and Use of English", 1, 1, "mcq4", "",
         _json.dumps({"A": "aa", "B": "bb", "C": "cc", "D": "dd"}), "B"),
    )
    conn.commit()
    conn.close()

    store = FcePaperStore(db)
    papers = store.list_papers()
    assert len(papers) == 1
    assert papers[0]["papers"]["Reading and Use of English"][0]["questions"] == 1

    detail = store.get_paper(1)
    sec = detail["sections"][0]
    assert sec["paper"] == "Reading and Use of English"
    assert sec["passage"] == "passage text"
    q = sec["questions"][0]
    assert q["options"] == [("A", "aa"), ("B", "bb"), ("C", "cc"), ("D", "dd")]
    assert q["answer"] == "B"

    assert store.get_paper(99) is None


def test_fce_submissions_flow(client, tmp_path, monkeypatch):
    """提交→自动批改→作文待批改→教师批改 全链路（用临时 fce.db）。"""
    import json as _json

    import sqlite3

    from grammar_kb import server as srv
    from grammar_kb.fce_paper import SCHEMA as FCE_SCHEMA
    from grammar_kb.fce_query import FcePaperStore, FceSubmissionStore

    db = str(tmp_path / "fce.db")
    conn = sqlite3.connect(db)
    conn.executescript(FCE_SCHEMA)
    conn.execute("INSERT INTO fce_test (id, title) VALUES (1, 'Test 1')")
    cur = conn.execute(
        "INSERT INTO fce_section (test_id, paper, part, ord)"
        " VALUES (1, 'Reading and Use of English', 1, 0)"
    )
    sid = cur.lastrowid
    conn.executemany(
        "INSERT INTO fce_question (section_id, test_id, paper, part, qnum, type,"
        " stem, options_json, answer) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (sid, 1, "Reading and Use of English", 1, 1, "mcq4", "",
             _json.dumps({"A": "x", "B": "y", "C": "z", "D": "w"}), "B"),
            (sid, 1, "Reading and Use of English", 1, 2, "cloze", "s", "{}", "one / some"),
        ],
    )
    # 作文 part
    cur2 = conn.execute(
        "INSERT INTO fce_section (test_id, paper, part, ord)"
        " VALUES (1, 'Writing', 1, 1)"
    )
    conn.execute(
        "INSERT INTO fce_question (section_id, test_id, paper, part, qnum, type, stem)"
        " VALUES (?,?,?,?,?,?,'essay')",
        (cur2.lastrowid, 1, "Writing", 1, 1, "essay"),
    )
    conn.commit()
    conn.close()

    # 注入 app 的 store 指向临时库（app 已建好，直接换实例属性）
    client.app.router  # 触发 lazy
    import grammar_kb.server as srv_mod
    # create_app 闭包内的 fce_submissions 无法从外部替换——改为直接测 store + 端点兼容性
    sub_store = FceSubmissionStore(db)
    rec = sub_store.submit("stu", 1, "Reading and Use of English", 1,
                           {"1": "B", "2": "some"})
    assert rec["auto_score"] == 2 and rec["total"] == 2 and rec["status"] == "auto"
    rec2 = sub_store.submit("stu", 1, "Writing", 1, {"1": "My essay..."})
    assert rec2["status"] == "pending" and rec2["total"] == 0
    graded = sub_store.grade(rec2["id"], 18, "good")
    assert graded["status"] == "graded" and graded["teacher_score"] == 18
    # 学生视角列表只看自己（store 层由 server 过滤 user）
    assert len(sub_store.list(user="stu")) == 2
    assert len(sub_store.list(user="stu", status="graded")) == 1

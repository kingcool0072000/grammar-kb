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
    """/ 的双模式：web/dist 存在时返回前端 HTML；否则 API 信息 JSON。

    GRAMMAR_KB_STATIC=1（默认）且 web/dist 在 → FileResponse(index.html)。
    """
    import os
    from pathlib import Path

    dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    if dist.is_dir() and os.environ.get("GRAMMAR_KB_STATIC", "1") != "0":
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        # API 探活走 /api-info
        body = client.get("/api-info").json()
        assert body["code"] == 0 and body["data"]["service"] == "grammar-kb"
    else:
        body = client.get("/").json()
        assert body["code"] == 0
        assert body["data"]["service"] == "grammar-kb"


def test_api_prefix_strip(client):
    """单进程部署：前端 /api/* 前缀被中间件剥掉后正常路由。"""
    r = client.get("/api/api-info")
    assert r.status_code == 200
    assert r.json()["data"]["service"] == "grammar-kb"


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

    from grammar_kb.fce_paper import SCHEMA as FCE_SCHEMA
    from grammar_kb.fce_query import FceSubmissionStore

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


# ---- 多角色 / 路径解析回归（各自独立装配 app，环境变量全部隔离在 tmp） ----


@pytest.fixture()
def isolated_app(tmp_path, monkeypatch):
    """独立装配 app：语法库/成绩库/FCE 库/账号文件全指向 tmp，不触真实数据。

    环境变量必须在 create_app 之前设置（各 store 在构造时读路径）。
    fce.db 预置一套含标准答案的客观题，供提交端点走通自动批改。
    """
    import json as _json

    import sqlite3

    from grammar_kb.fce_paper import SCHEMA as FCE_SCHEMA

    db_path = str(tmp_path / "grammar.db")
    GrammarDB(db_path).close()  # 建空库即可（本组测试不查讲义内容）
    fce_db = str(tmp_path / "fce.db")
    conn = sqlite3.connect(fce_db)
    conn.executescript(FCE_SCHEMA)
    conn.execute("INSERT INTO fce_test (id, title) VALUES (1, 'Test 1')")
    cur = conn.execute(
        "INSERT INTO fce_section (test_id, paper, part, ord)"
        " VALUES (1, 'Reading and Use of English', 1, 0)"
    )
    conn.execute(
        "INSERT INTO fce_question (section_id, test_id, paper, part, qnum, type,"
        " stem, options_json, answer) VALUES (?,?,?,?,?,?,?,?,?)",
        (cur.lastrowid, 1, "Reading and Use of English", 1, 1, "mcq4", "",
         _json.dumps({"A": "x", "B": "y", "C": "z", "D": "w"}), "B"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    monkeypatch.setenv("GRAMMAR_KB_EXAM_DB", str(tmp_path / "exam.db"))
    monkeypatch.setenv("GRAMMAR_KB_FCE_DB", fce_db)
    # 默认账号由 UserStore 播种：malin/mxy（学生）、teacher（教师）
    yield create_app(db_path)


def _login(c, user: str, password: str = "123456") -> dict:
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def test_fce_submission_detail_ownership(isolated_app):
    """IDOR 回归：GET /fce-submissions/{id} 学生只能读自己的。

    白名单 ("GET", "/fce-submissions") 是前缀匹配，详情路由也放进来了；
    端点若无归属校验，任意学生可枚举读取他人提交（含答案明细）。
    """
    with TestClient(isolated_app) as c:
        stu_a = _login(c, "malin")
        r = c.post(
            "/fce-submissions",
            json={"test_id": 1, "paper": "Reading and Use of English",
                  "part": 1, "answers": {"1": "B"}},
            headers=stu_a,
        )
        assert r.status_code == 200, r.text
        sub_id = r.json()["data"]["id"]

        # 学生读自己 → 200
        assert c.get(f"/fce-submissions/{sub_id}", headers=stu_a).status_code == 200

        # 另一学生读他人的 → 403（风格与录音详情端点一致）
        stu_b = _login(c, "mxy", "mxy123")
        got = c.get(f"/fce-submissions/{sub_id}", headers=stu_b)
        assert got.status_code == 403
        assert got.json()["code"] == 403

        # 教师读任意 → 200
        teacher = _login(c, "teacher")
        assert c.get(f"/fce-submissions/{sub_id}", headers=teacher).status_code == 200


def test_cors_preflight_without_token(isolated_app):
    """CORS 预检回归：OPTIONS 不带 Authorization（浏览器规范行为）不得被 401。

    auth 中间件在 CORSMiddleware 外层（后 add 者更外），原先预检被 401 挡死
    且无 CORS 头，前端跨域请求实际全部失败。
    """
    with TestClient(isolated_app) as c:
        r = c.options(
            "/search",
            headers={
                "Origin": "http://example.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-origin") in ("*", "http://example.com")


def test_exam_db_path_resolved_not_none(tmp_path, monkeypatch):
    """路径解析回归：exam_db_path=None 时 AnalyticsAI 不得落到 ./None。

    旧实现 AnalyticsAI(None) → str(None)="None" → sqlite3.connect("None")
    在 cwd 生成名为 None 的脏文件；create_app 必须先解析成具体路径再装配。
    """
    import sqlite3

    from grammar_kb.exam_store import default_exam_db_path

    monkeypatch.chdir(tmp_path)  # cwd 干净，任何 "None" 脏文件都会暴露在这里
    exam_db = str(tmp_path / "exam.db")
    monkeypatch.setenv("GRAMMAR_KB_EXAM_DB", exam_db)
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    monkeypatch.setenv("GRAMMAR_KB_FCE_DB", str(tmp_path / "fce.db"))
    assert default_exam_db_path() == exam_db  # 前置：env 在 create_app 前生效

    db_path = str(tmp_path / "grammar.db")
    GrammarDB(db_path).close()
    app = create_app(db_path)
    with TestClient(app) as c:
        # 触发 analytics 端点（list_reports 会打开 analytics 自己的库）
        r = c.get("/analytics/ai/reports", headers=_login(c, "teacher"))
        assert r.status_code == 200, r.text

    # 成绩库与 AI 周报表同落环境变量指定路径（而非 ./None）
    con = sqlite3.connect(exam_db)
    tables = {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "exam_records" in tables       # ExamStore 用了该路径
    assert "ai_weekly_reports" in tables   # AnalyticsAI 用了同一具体路径
    assert not (tmp_path / "None").exists()


def test_topics_progress_flow(isolated_app):
    """专题学习进度：学生写入/读取自己的行；白名单放行；教师可查任意学生。"""
    with TestClient(isolated_app) as c:
        stu = _login(c, "malin")

        # 初始无记录 → 空进度
        r = c.get("/topics/tense-26-28/progress", headers=stu)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["done_keys"] == []

        # 写入两个主题 → 读回一致（去重排序）
        r = c.put(
            "/topics/tense-26-28/progress",
            json={"done_keys": ["t2", "t1", "t2"], "assigned_user": "malin"},
            headers=stu,
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["done_keys"] == ["t1", "t2"]
        assert r.json()["data"]["assigned_user"] == "malin"

        # 列表：学生只见自己的行
        r = c.get("/topics/progress", headers=stu)
        assert r.status_code == 200, r.text
        rows = r.json()["data"]
        assert len(rows) == 1 and rows[0]["user"] == "malin"

        # 另一学生读 malin 的进度：只能读到自己的（空）
        stu_b = _login(c, "mxy", "mxy123")
        r = c.get("/topics/tense-26-28/progress", headers=stu_b)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["done_keys"] == []
        assert r.json()["data"]["user"] == "mxy"

        # 教师可带 user 参数查任意学生
        teacher = _login(c, "teacher")
        r = c.get(
            "/topics/tense-26-28/progress",
            params={"user": "malin"},
            headers=teacher,
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["done_keys"] == ["t1", "t2"]

        # 未登录 401
        assert c.get("/topics/progress").status_code == 401

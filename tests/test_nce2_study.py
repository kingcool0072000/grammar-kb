"""NCE2 听力跟读端点测试：manifest 容错 / 打点幂等 / 跟读批改 / 学生宇宙。

学生宇宙（任务制）：只看被安排（或做过）的课文；课文音频与提交同门禁。
计划课次解析走真链路——教师排周计划（tasks.nce2_units）后由
plan._plan_lessons 解出课次（签名钉死 _plan_lessons(tasks) -> list[int]）。
"""
from __future__ import annotations

import base64
import json
import sqlite3

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.nce2_study import Nce2Store, load_manifest, lesson  # noqa: E402
from grammar_kb.server import create_app  # noqa: E402


@pytest.fixture()
def nce2_env(tmp_path, monkeypatch):
    """空白三库 + 指向 tmp 的 NCE2 manifest/音频目录。

    环境变量必须在 create_app 之前设（auth-roles 教训）：UserStore 在
    create_app 内实例化时读 GRAMMAR_KB_USERS；NCE2 路径虽是请求期现读，
    也一并提前，口径统一。
    """
    for name in ("grammar.db", "exam.db", "fce.db"):
        sqlite3.connect(str(tmp_path / name)).close()
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    monkeypatch.setenv("GRAMMAR_KB_NCE2_BOOK", str(tmp_path / "book.json"))
    monkeypatch.setenv("GRAMMAR_KB_NCE2_AUDIO_DIR", str(tmp_path / "audio"))
    (tmp_path / "audio").mkdir()
    # 第二个学生账号（他人不可见我的录音用例）：必须在 create_app 之前建——
    # app 内 UserStore 实例首次 _load 即缓存，事后写 users.json 不被看到
    from grammar_kb.auth import UserStore
    UserStore().add_user("wang", "123456")
    return tmp_path


def _write_manifest(env, lessons=None):
    """往 tmp 写 book.json（默认 3 课），供清单与音频端点用。"""
    if lessons is None:
        lessons = [
            {"id": i, "title": f"Lesson {i}",
             "sentences": [{"t": j * 3.5, "en": f"sentence {j}", "zh": f"句 {j}"}
                           for j in range(i)],
             "new_words": ["w1", "w2"][:i]}
            for i in (1, 2, 3)
        ]
    (env / "book.json").write_text(
        json.dumps({"book": "NCE2", "lessons": lessons}, ensure_ascii=False),
        encoding="utf-8")


def _client(env) -> TestClient:
    return TestClient(create_app(str(env / "grammar.db"), str(env / "exam.db"),
                                 fce_db_path=str(env / "fce.db")))


def _login(c, user, password="123456"):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _fake_audio() -> str:
    return base64.b64encode(b"\x1a\x45\xdf\xa3" + b"0" * 1024).decode()


def _assign(c, t, lids, task_mode=None):
    """教师给 malin 排本周 NCE2 课（真接口 + 计划车道钉死的 nce2_units 键）。"""
    from grammar_kb.clock import current_monday
    ws = current_monday().isoformat()
    unit = {"lessons": lids, "mode": "daily"}
    if task_mode:
        unit["task_mode"] = task_mode
    r = c.put(f"/plan/weeks/{ws}", headers=t, json={
        "tasks": {"nce2_units": [unit]},
        "notes": "", "user": "malin"})
    assert r.status_code == 200, r.text


def test_manifest_missing_degrades_gracefully(nce2_env):
    """manifest 缺失：load_manifest 空表、教师 /nce2/lessons 空清单不崩、提交 422。"""
    assert load_manifest() == {"book": "NCE2", "lessons": []}
    assert lesson(1) is None
    c = _client(nce2_env)
    t = _login(c, "teacher")
    r = c.get("/nce2/lessons", headers=t)
    assert r.status_code == 200 and r.json()["data"] == []
    r = c.post("/nce2/submissions", headers=t,
               json={"lesson_id": 1, "kind": "listen"})
    assert r.status_code == 422


def test_listen_idempotent_and_shadow_flow(nce2_env):
    """listen 同日幂等；shadow 提交→pending→批改→graded；listen 行拒批。"""
    _write_manifest(nce2_env)
    c = _client(nce2_env)
    t, s = _login(c, "teacher"), _login(c, "malin")
    _assign(c, t, [1])
    # 学生清单：只见课 1，初始未听；未显式配 task_mode 时 nce2_mode 缺省 shadow
    d = c.get("/nce2/lessons", headers=s).json()["data"]
    assert [x["id"] for x in d] == [1]
    assert d[0]["planned"] and d[0]["week"]
    assert d[0]["nce2_mode"] == "shadow"
    assert not d[0]["listen_today"] and d[0]["shadow"] is None
    # 显式 listen 组 → nce2_mode='listen'（学生端动作区按它出打卡按钮）
    _assign(c, t, [1], task_mode="listen")
    d = c.get("/nce2/lessons", headers=s).json()["data"]
    assert d[0]["nce2_mode"] == "listen"
    # 听力打点 → done；再打点 → 同一行（幂等）
    r = c.post("/nce2/submissions", headers=s, json={"lesson_id": 1, "kind": "listen"})
    assert r.status_code == 200
    first = r.json()["data"]
    assert first["status"] == "done"
    r2 = c.post("/nce2/submissions", headers=s, json={"lesson_id": 1, "kind": "listen"})
    assert r2.status_code == 200 and r2.json()["data"]["id"] == first["id"]
    d = c.get("/nce2/lessons", headers=s).json()["data"]
    assert d[0]["listen_today"] and d[0]["listen_done_ever"]
    # Store 直连第三次仍幂等：库里 listen 行只有一条
    store = Nce2Store(str(nce2_env / "fce.db"))
    again = store.submit("malin", 1, "listen")
    assert again["id"] == first["id"]
    conn = sqlite3.connect(str(nce2_env / "fce.db"))
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM nce2_submission WHERE kind='listen'").fetchone()[0]
    finally:
        conn.close()
    assert n == 1
    # 跟读提交 → pending；教师队列可见（带课标题，不带音频体）
    r = c.post("/nce2/submissions", headers=s, json={
        "lesson_id": 1, "kind": "shadow", "audio_b64": _fake_audio(),
        "mime": "audio/webm", "duration_sec": 42.5})
    assert r.status_code == 200
    sid = r.json()["data"]["id"]
    assert r.json()["data"]["status"] == "pending"
    q = c.get("/nce2/submissions", headers=t).json()["data"]
    assert len(q) == 1 and q[0]["id"] == sid
    assert q[0]["lesson_title"] == "Lesson 1" and q[0]["user"] == "malin"
    assert "audio_b64" not in q[0]
    # 批改：越界分数夹取 0-10 → graded
    r = c.put(f"/nce2/submissions/{sid}", headers=t,
              json={"teacher_score": 15, "teacher_comment": "发音清楚"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "graded" and data["teacher_score"] == 10
    assert data["graded_at"]
    assert c.get("/nce2/submissions", headers=t).json()["data"] == []
    q = c.get("/nce2/submissions?status=graded", headers=t).json()["data"]
    assert len(q) == 1 and q[0]["teacher_comment"] == "发音清楚"
    # 学生视角：最近一次 shadow 摘要出现在清单里；历史只有自己的行
    d = c.get("/nce2/lessons", headers=s).json()["data"]
    assert d[0]["shadow"]["id"] == sid and d[0]["shadow"]["teacher_score"] == 10
    mine = c.get("/nce2/submissions", headers=s).json()["data"]
    assert {x["kind"] for x in mine} == {"listen", "shadow"}
    # listen 打点不能批改（端点 422 / Store ValueError）
    assert c.put(f"/nce2/submissions/{first['id']}", headers=t,
                 json={"teacher_score": 5}).status_code == 422
    with pytest.raises(ValueError):
        store.grade(first["id"], 5, "")


def test_student_universe_and_permissions(nce2_env):
    """学生只见宇宙内课文；未安排不能提交；批改/删除端点学生 403。"""
    _write_manifest(nce2_env)
    c = _client(nce2_env)
    t, s = _login(c, "teacher"), _login(c, "malin")
    # 无安排无提交：空宇宙，提交也 403
    assert c.get("/nce2/lessons", headers=s).json()["data"] == []
    r = c.post("/nce2/submissions", headers=s,
               json={"lesson_id": 1, "kind": "listen"})
    assert r.status_code == 403
    # 排课后：只见课 1；课 2 不可见
    _assign(c, t, [1])
    d = c.get("/nce2/lessons", headers=s).json()["data"]
    assert [x["id"] for x in d] == [1]
    # 教师视角全量 3 课
    dall = c.get("/nce2/lessons", headers=t).json()["data"]
    assert [x["id"] for x in dall] == [1, 2, 3]
    for x in dall:
        assert set(x) == {"id", "title", "n_sentences", "n_words", "has_audio"}
    # 打点进入宇宙后：提交/音频不再被拦（有提交记录半边）
    r = c.post("/nce2/submissions", headers=s, json={"lesson_id": 1, "kind": "listen"})
    assert r.status_code == 200
    sid = r.json()["data"]["id"]
    # 学生不能批改、不能删（中间件 403）
    assert c.put(f"/nce2/submissions/{sid}", headers=s,
                 json={"teacher_score": 9}).status_code == 403
    assert c.delete(f"/nce2/submissions/{sid}", headers=s).status_code == 403
    # 教师删除成功；再删 404
    assert c.delete(f"/nce2/submissions/{sid}", headers=t).status_code == 200
    assert c.delete(f"/nce2/submissions/{sid}", headers=t).status_code == 404
    # 不存在的课次提交 422（清单里没有）
    r = c.post("/nce2/submissions", headers=t,
               json={"lesson_id": 99, "kind": "listen"})
    assert r.status_code == 422
    # kind=shadow 缺音频：schema 422
    r = c.post("/nce2/submissions", headers=t, json={"lesson_id": 1, "kind": "shadow"})
    assert r.status_code == 422
    # 非法 kind：schema 422
    r = c.post("/nce2/submissions", headers=t,
               json={"lesson_id": 1, "kind": "read"})
    assert r.status_code == 422


def test_lesson_audio_endpoint(nce2_env):
    """课文 mp3：宇宙内 200（token 查询参数直连）、无文件 404、宇宙外 403。

    文件名两口径都验：manifest filename 补零名（真实导入数据，'02.mp3'）
    与无 filename 时的契约默认 {id}.mp3（课 1）。
    """
    _write_manifest(nce2_env)
    fake_mp3 = b"ID3" + b"\x00" * 64
    (nce2_env / "audio" / "1.mp3").write_bytes(fake_mp3)
    padded = b"ID3" + b"\x01" * 64
    (nce2_env / "audio" / "02.mp3").write_bytes(padded)
    # 真实导入数据的 manifest 形状：filename 字段补零
    m = json.loads((nce2_env / "book.json").read_text(encoding="utf-8"))
    m["lessons"][1]["filename"] = "02"
    (nce2_env / "book.json").write_text(
        json.dumps(m, ensure_ascii=False), encoding="utf-8")
    c = _client(nce2_env)
    t, s = _login(c, "teacher"), _login(c, "malin")
    _assign(c, t, [1, 2])
    # 宇宙内 + 文件在：200，媒体类型 audio/mpeg（token 走查询参数）
    token = s["Authorization"][7:]
    r = c.get(f"/nce2/lessons/1/audio?token={token}")
    assert r.status_code == 200 and r.content == fake_mp3
    assert r.headers["content-type"].startswith("audio/mpeg")
    # filename 补零口径：课 2 走 manifest filename → 02.mp3
    r = c.get("/nce2/lessons/2/audio", headers=s)
    assert r.status_code == 200 and r.content == padded
    # 宇宙外（课 3 未安排也没做过）：403
    assert c.get("/nce2/lessons/3/audio", headers=s).status_code == 403
    # 清单里没有的课：404
    assert c.get("/nce2/lessons/999/audio", headers=s).status_code == 404
    # 教师绕过宇宙门禁，但仍受文件存在性约束
    assert c.get("/nce2/lessons/3/audio", headers=t).status_code == 404
    assert c.get("/nce2/lessons/1/audio", headers=t).status_code == 200
    # has_audio 随文件名口径走：课 1/2 True、课 3 False
    d = c.get("/nce2/lessons", headers=t).json()["data"]
    assert [(x["id"], x["has_audio"]) for x in d] == [(1, True), (2, True), (3, False)]
    # 「有提交记录」半边：先排课 3 → 打点 → 计划撤掉课 3 → 仍在宇宙内
    # （历史做过不因计划变更而消失），音频仍受文件存在性约束 404。
    # 未安排的课无法靠提交自举进宇宙（打点本身也要过门禁）。
    _assign(c, t, [3])
    r = c.post("/nce2/submissions", headers=s,
               json={"lesson_id": 3, "kind": "listen"})
    assert r.status_code == 200
    _assign(c, t, [1, 2])
    assert c.get("/nce2/lessons/3/audio", headers=s).status_code == 404  # 无文件仍 404


def test_submission_audio_playback(nce2_env):
    """跟读录音二进制：本人/教师可听，他人 403，mime 原样带出。"""
    _write_manifest(nce2_env)
    c = _client(nce2_env)
    t, s = _login(c, "teacher"), _login(c, "malin")
    _assign(c, t, [1])
    r = c.post("/nce2/submissions", headers=s, json={
        "lesson_id": 1, "kind": "shadow", "audio_b64": _fake_audio(),
        "mime": "audio/webm", "duration_sec": 30})
    sid = r.json()["data"]["id"]
    token = s["Authorization"][7:]
    r = c.get(f"/nce2/submissions/{sid}/audio?token={token}")
    assert r.status_code == 200 and len(r.content) == 1028
    assert r.headers["content-type"].startswith("audio/webm")
    assert c.get(f"/nce2/submissions/{sid}/audio", headers=t).status_code == 200
    # 其他学生 403（wang 已在 fixture 里建好）
    other = _login(c, "wang")
    assert c.get(f"/nce2/submissions/{sid}/audio", headers=other).status_code == 403


def test_lessons_with_sentences_flag(nce2_env):
    """?with_sentences=1 附 manifest 原样逐句时间轴；默认不带（列表页体积）。"""
    _write_manifest(nce2_env)
    c = _client(nce2_env)
    t, s = _login(c, "teacher"), _login(c, "malin")
    _assign(c, t, [1])
    # 默认：行内无 sentences 键
    d = c.get("/nce2/lessons", headers=t).json()["data"]
    assert len(d) == 3 and all("sentences" not in x for x in d)
    # 带参数：教师全量附逐句（manifest 原样，t/en/zh）
    d = c.get("/nce2/lessons?with_sentences=1", headers=t).json()["data"]
    s3 = next(x for x in d if x["id"] == 3)["sentences"]
    assert len(s3) == 3 and set(s3[0]) == {"t", "en", "zh"}
    # 学生：宇宙内同样带；宇宙外课不因参数而可见
    d = c.get("/nce2/lessons?with_sentences=1", headers=s).json()["data"]
    assert [x["id"] for x in d] == [1]
    assert len(d[0]["sentences"]) == 1 and d[0]["sentences"][0]["t"] == 0


def test_focus_module_nce2_persisted(nce2_env):
    """focus module='nce2' 提交 /focus/sessions：入库 module=='nce2' 不回落 library。"""
    c = _client(nce2_env)
    s = _login(c, "malin")
    r = c.post("/focus/sessions", headers=s, json={
        "session_id": "nce2-lesson-1-s1", "module": "nce2",
        "book_title": "NCE2 Lesson 1", "total_sec": 60, "active_sec": 48,
        "away_sec": 12, "lookups": 2, "plays": 3})
    assert r.status_code == 200, r.text
    conn = sqlite3.connect(str(nce2_env / "fce.db"))
    try:
        row = conn.execute(
            "SELECT module FROM focus_sessions WHERE session_id = ?",
            ("nce2-lesson-1-s1",)).fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] == "nce2"

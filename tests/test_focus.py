"""专注力采集端点测试：上报幂等 / 权限隔离 / 评分 / settings 开关。"""
from __future__ import annotations

import json
import sqlite3

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.server import create_app  # noqa: E402
from grammar_kb.focus import compute_score  # noqa: E402


@pytest.fixture()
def focus_env(tmp_path, monkeypatch):
    """空 fce.db（FocusStore 建表）+ 独立认证 / 成绩 / 泛读馆设置库。"""
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DB", str(tmp_path / "library.db"))
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DIR", str(tmp_path / "files"))
    sqlite3.connect(tmp_path / "grammar.db").close()  # 合法空库
    return tmp_path


def _client(env) -> TestClient:
    return TestClient(create_app(str(env / "grammar.db"), str(env / "exam.db"),
                               fce_db_path=str(env / "fce.db")))


def _login(c, user, password="123456"):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _payload(**over):
    d = {
        "session_id": "focus-abc-1", "book_id": 3, "book_title": "Charlotte's Web",
        "started_at": "2026-09-13T10:00:00", "ended_at": "2026-09-13T10:10:00",
        "total_sec": 600, "active_sec": 480, "away_count": 2, "away_sec": 120,
        "longest_away_sec": 80, "scroll_count": 150, "chapter_navs": 1,
        "lookups": 4, "plays": 2, "prep_opens": 1, "prep_starts": 1,
        "percent_start": 5.0, "percent_end": 40.0, "fast_scroll_flags": 0,
        "mouse_metrics": {"jitter": 0.2, "moves": 320},
        "polyline": json.dumps([[0, 5], [60, 12], [120, 18], [600, 40]]),
    }
    d.update(over)
    return d


def test_focus_submit_and_heartbeat(focus_env):
    """学生上报：返回 score/score_detail；同 session_id 重传覆盖且不新增行。"""
    c = _client(focus_env)
    s = _login(c, "malin")
    r = c.post("/focus/sessions", headers=s, json=_payload())
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert 0 <= d["score"] <= 100
    assert set(d["score_detail"]) == {
        "effective_ratio", "engagement", "progress", "away_penalty",
        "jitter_penalty", "idle_penalty"}
    assert d["mouse_metrics"]["jitter"] == 0.2  # 上报响应即完整行（含明细）
    assert d["session_id"] == "focus-abc-1" and d["user"] == "malin"

    # 60 秒心跳重传：total_sec 延长、ended_at 更新 → 同 session_id 覆盖
    r2 = c.post("/focus/sessions", headers=s, json=_payload(
        total_sec=1200, active_sec=1000, ended_at="2026-09-13T10:20:00"))
    assert r2.status_code == 200, r2.text
    mine = c.get("/focus/sessions", headers=s).json()["data"]
    assert len(mine) == 1  # 行数不变（幂等）
    assert mine[0]["ended_at"] == "2026-09-13T10:20:00"
    assert mine[0]["total_sec"] == 1200
    assert mine[0]["polyline"] is None and mine[0]["mouse_metrics"] is None  # 列表不带明细


def test_focus_visibility(focus_env):
    """列表权限：学生只见自己、教师见全部；详情教师 200 / 学生 403 / 404。"""
    c = _client(focus_env)
    t = _login(c, "teacher")
    s1 = _login(c, "malin")
    s2 = _login(c, "mxy", password="mxy123")
    assert c.post("/focus/sessions", headers=s1, json=_payload()).status_code == 200
    assert c.post("/focus/sessions", headers=s2, json=_payload(
        session_id="focus-xyz-9", user="mxy")).status_code == 200

    mine1 = c.get("/focus/sessions", headers=s1).json()["data"]
    assert len(mine1) == 1 and mine1[0]["user"] == "malin"
    mine2 = c.get("/focus/sessions", headers=s2).json()["data"]
    assert len(mine2) == 1 and mine2[0]["user"] == "mxy"
    all_ = c.get("/focus/sessions", headers=t).json()["data"]
    assert len(all_) == 2 and {r["user"] for r in all_} == {"malin", "mxy"}

    row_id = mine1[0]["id"]
    detail = c.get(f"/focus/sessions/{row_id}", headers=t)
    assert detail.status_code == 200, detail.text
    dd = detail.json()["data"]
    assert dd["polyline"][0] == [0, 5] and dd["mouse_metrics"]["moves"] == 320
    # 学生查详情：端点内 _require_teacher 拦截
    assert c.get(f"/focus/sessions/{row_id}", headers=s1).status_code == 403
    # 不存在
    assert c.get("/focus/sessions/99999", headers=t).status_code == 404


def test_focus_validation_and_whitelist(focus_env):
    """非法入参 422；白名单外方法（学生 PUT）403。"""
    c = _client(focus_env)
    s = _login(c, "malin")
    assert c.post("/focus/sessions", headers=s,
                  json=_payload(total_sec=99999)).status_code == 422
    assert c.post("/focus/sessions", headers=s,
                  json=_payload(session_id="")).status_code == 422
    assert c.post("/focus/sessions", headers=s,
                  json=_payload(lookups=-1)).status_code == 422
    assert c.put("/focus/sessions", headers=s, json=_payload()).status_code == 403


def test_focus_settings_switch(focus_env):
    """教师开关：默认开 → PUT focus=false → 两视图均 False；非布尔 422。"""
    c = _client(focus_env)
    t, s = _login(c, "teacher"), _login(c, "malin")
    # 默认开（学生视图 & 教师视图都带 focus）
    assert c.get("/library/settings", headers=s).json()["data"]["student"]["focus"] is True
    assert c.get("/library/settings", headers=t).json()["data"]["student"]["focus"] is True
    # 教师关掉
    r = c.put("/library/settings", headers=t, json={"student": {"focus": False}})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["student"]["focus"] is False
    assert c.get("/library/settings", headers=s).json()["data"]["student"]["focus"] is False
    # 再开回来 + 非布尔拒绝
    assert c.put("/library/settings", headers=t,
                 json={"student": {"focus": True}}).status_code == 200
    assert c.put("/library/settings", headers=t,
                 json={"student": {"focus": "on"}}).status_code == 422
    assert c.get("/library/settings", headers=t).json()["data"]["student"]["focus"] is True


def test_compute_score_known_inputs():
    """纯函数抽查：纯挂机低分，高互动专注高分；total=0 全零。"""
    idle_score, idle_detail = compute_score({
        "total_sec": 600, "active_sec": 0, "away_count": 3,
        "percent_start": 0, "percent_end": 0, "lookups": 0,
        "mouse_metrics": {"jitter": 0.9},
    })
    good_score, good_detail = compute_score({
        "total_sec": 600, "active_sec": 600, "away_count": 0,
        "percent_start": 5, "percent_end": 60, "lookups": 5,
        "mouse_metrics": {"jitter": 0.1},
    })
    assert 0 <= idle_score < good_score <= 100
    assert idle_score <= 5  # 挂机基本零分（惩罚扣减后触底）
    assert good_score >= 70
    assert idle_detail["effective_ratio"] == 0.0
    assert idle_detail["jitter_penalty"] == 3.75  # (0.9-0.6)*12.5
    assert good_detail["effective_ratio"] == 1.0
    zero_score, zero_detail = compute_score({"total_sec": 0, "active_sec": 0})
    assert zero_score == 0 and all(v == 0 for v in zero_detail.values())


# ---- v2：内容监控 + 学习范围 + 评分升级 ----

def test_v2_migration_idempotent(tmp_path):
    """旧 schema 库（无 v2 列）实例化后新列可用、旧数据不丢。"""
    import sqlite3
    from grammar_kb.focus import FocusStore
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    # 手工建 v1 列集 + 一行旧数据
    con.execute("""CREATE TABLE focus_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL UNIQUE,
        user TEXT NOT NULL, book_id INTEGER, book_title TEXT, started_at TEXT,
        ended_at TEXT, total_sec INTEGER, active_sec INTEGER, away_count INTEGER,
        away_sec INTEGER, longest_away_sec INTEGER, scroll_count INTEGER,
        chapter_navs INTEGER, lookups INTEGER, plays INTEGER, prep_opens INTEGER,
        prep_starts INTEGER, percent_start REAL, percent_end REAL,
        fast_scroll_flags INTEGER, mouse_metrics TEXT, polyline TEXT,
        score INTEGER, score_detail TEXT, created_at TEXT)""")
    con.execute("INSERT INTO focus_sessions (session_id, user, total_sec, score) VALUES ('old1','malin',100,50)")
    con.commit(); con.close()
    store = FocusStore(str(db))  # 触发迁移
    got = store.get(store.list()[0]["id"])
    assert got["module"] == "library" and got["range_label"] == ""  # 旧行有默认值
    # 幂等：二次实例化不炸、新写入带 v2 字段落库
    FocusStore(str(db))
    rec = store.submit("malin", "new1", total_sec=300, module="reading",
                       range_start=10, range_end=25, range_label="T1P7(180词)",
                       lookup_words=[["mysterious", 2], ["chill", 1]],
                       activity=[{"t": 5, "type": "lookup", "w": "mysterious"}])
    assert rec["module"] == "reading" and rec["range_label"] == "T1P7(180词)"
    d = store.get(rec["id"])
    assert d["lookup_words"][0] == {"w": "mysterious", "n": 2}
    assert d["activity"] == [{"t": 5, "type": "lookup", "w": "mysterious"}]


def test_word_clean_forms_equivalent():
    from grammar_kb.focus import _clean_words
    a = _clean_words([["cat", 2], ["dog", 1]])
    b = _clean_words([{"w": "cat", "n": 2}, {"w": "dog", "n": 1}])
    c = _clean_words({"cat": 2, "dog": 1})
    assert a == b == c == [{"w": "cat", "n": 2}, {"w": "dog", "n": 1}]


def test_score_v2_dedup_and_idle():
    from grammar_kb.focus import compute_score
    # 同词重复查 5 次 vs 5 个不同词各查 1 次：后者 engagement 明显更高
    base = {"total_sec": 600, "active_sec": 600}
    s1, d1 = compute_score({**base, "lookups": 5, "lookup_words": [["cat", 5]]})
    s2, d2 = compute_score({**base, "lookups": 5,
                            "lookup_words": [["a", 1], ["b", 1], ["c", 1], ["d", 1], ["e", 1]]})
    assert d2["engagement"] > d1["engagement"] and s2 > s1
    # idle_gaps 有 >300s 段：idle_penalty 每段 4
    _, d3 = compute_score({**base, "mouse_metrics": {"idle_gaps": [[10, 320], [500, 560]]}})
    assert d3["idle_penalty"] == 4.0  # 只有一段 >300s
    # 旧字段兜底（无词表）不崩
    _, d4 = compute_score({**base, "lookups": 3, "plays": 2})
    assert d4["engagement"] > 0


def test_list_has_module_range(focus_env):
    c = _client(focus_env)
    h = _login(c, "malin")
    r = c.post("/focus/sessions", headers=h, json={
        "session_id": "v2-list-1", "total_sec": 60, "module": "reading",
        "range_label": "T3P6", "range_start": 0, "range_end": 100,
        "lookup_words": [["word", 1]], "activity": [{"t": 1, "type": "lookup", "w": "word"}]})
    assert r.status_code == 200, r.text
    items = c.get("/focus/sessions", headers=h).json()["data"]
    mine = [x for x in items if x["session_id"] == "v2-list-1"][0]
    assert mine["module"] == "reading" and mine["range_label"] == "T3P6"
    tid = mine["id"]
    detail = _login(c, "teacher")  # 教师查详情
    d = c.get(f"/focus/sessions/{tid}", headers=detail).json()["data"]
    assert d["lookup_words"] == [{"w": "word", "n": 1}]
    assert d["activity"] == [{"t": 1, "type": "lookup", "w": "word"}]

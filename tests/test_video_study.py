"""视频学习端点测试：种子清单 / 权限 / 复述提交与批改 / 计划完成信号。

学生宇宙（任务制）：只看被安排（或做过）的视频；详情与提交同样门禁。
"""
from __future__ import annotations

import base64
import sqlite3

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.server import create_app  # noqa: E402


@pytest.fixture()
def video_env(tmp_path, monkeypatch):
    """空白三库（不依赖 data/fce.db 真库：种子在 VideoStore 首建时写入）。"""
    for name in ("grammar.db", "exam.db", "fce.db"):
        sqlite3.connect(str(tmp_path / name)).close()
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    return tmp_path


@pytest.fixture()
def assigned_env(video_env):
    """在 video_env 基础上给 malin 排本周 2 个视频（vid[0]、vid[1]）。"""
    c = _client(video_env)
    t = _login(c, "teacher")
    vids = c.get("/video-study", headers=t).json()["data"]
    from grammar_kb.clock import current_monday
    ws = current_monday().isoformat()
    r = c.put(f"/plan/weeks/{ws}", headers=t, json={
        "tasks": {"videos": [vids[0]["vid"], vids[1]["vid"]]},
        "notes": "", "user": "malin"})
    assert r.status_code == 200
    return video_env


def _client(env) -> TestClient:
    return TestClient(create_app(str(env / "grammar.db"), str(env / "exam.db"),
                                 fce_db_path=str(env / "fce.db")))


def _login(c, user, password="123456"):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _fake_audio() -> str:
    return base64.b64encode(b"\x1a\x45\xdf\xa3" + b"0" * 1024).decode()


def test_seed_videos_and_student_scope(video_env):
    c = _client(video_env)
    t, s = _login(c, "teacher"), _login(c, "malin")
    # 教师全量：10 个种子，形状完整
    vids = c.get("/video-study?include_disabled=1", headers=t).json()["data"]
    assert len(vids) == 10
    for v in vids:
        assert len(v["vid"]) == 11 and v["title"] and v["duration_sec"] > 0
        assert v["vocab"] and v["hints"]
    # 学生（无安排）：空宇宙
    d = c.get("/video-study", headers=s).json()["data"]
    assert d["videos"] == []
    assert all(not x for x in d["categories"].values())


def test_student_scope_with_assignment(assigned_env):
    c = _client(assigned_env)
    t, s = _login(c, "teacher"), _login(c, "malin")
    all_vids = c.get("/video-study", headers=t).json()["data"]
    d = c.get("/video-study", headers=s).json()["data"]
    assert len(d["videos"]) == 2
    assert set(d["categories"]["week"]) == {all_vids[0]["vid"], all_vids[1]["vid"]}
    assert d["categories"]["todo"] == [all_vids[0]["vid"], all_vids[1]["vid"]]
    # 未安排视频：详情 403、提交 403
    other = all_vids[2]["vid"]
    assert c.get(f"/video-study/items/{other}", headers=s).status_code == 403
    r = c.post("/video-study/retells", headers=s, json={
        "vid": other, "audio_b64": _fake_audio(),
        "mime": "audio/webm", "duration_sec": 10})
    assert r.status_code == 403
    # 安排过的：详情 200
    assert c.get(f"/video-study/items/{all_vids[0]['vid']}", headers=s).status_code == 200


def test_teacher_crud_and_student_scope(video_env):
    c = _client(video_env)
    t, s = _login(c, "teacher"), _login(c, "malin")
    # 学生不能增删改
    assert c.post("/video-study/items", headers=s,
                  json={"vid": "abc123DEF-_", "title": "X"}).status_code == 403
    # 教师新增 → 学生仍不可见（未安排）→ 安排后可见 → 下架后不可见
    r = c.post("/video-study/items", headers=t, json={
        "vid": "abc123DEF-_", "title": "Friction Demo", "category": "科学",
        "duration_sec": 120, "vocab": [{"word": "force", "gloss": "力"}],
        "hints": "What is friction?"})
    assert r.status_code == 200
    d = c.get("/video-study", headers=s).json()["data"]
    assert not any(v["vid"] == "abc123DEF-_" for v in d["videos"])
    from grammar_kb.clock import current_monday
    ws = current_monday().isoformat()
    c.put(f"/plan/weeks/{ws}", headers=t, json={
        "tasks": {"videos": ["abc123DEF-_"]}, "notes": "", "user": "malin"})
    d = c.get("/video-study", headers=s).json()["data"]
    assert any(v["vid"] == "abc123DEF-_" for v in d["videos"])
    r = c.put("/video-study/items/abc123DEF-_", headers=t, json={
        "vid": "abc123DEF-_", "title": "Friction Demo", "enabled": False})
    assert r.status_code == 200
    d = c.get("/video-study", headers=s).json()["data"]
    assert not any(v["vid"] == "abc123DEF-_" for v in d["videos"])
    # 下架视频详情对学生 403、对教师 200
    assert c.get("/video-study/items/abc123DEF-_", headers=s).status_code == 403
    assert c.get("/video-study/items/abc123DEF-_", headers=t).status_code == 200
    assert c.delete("/video-study/items/abc123DEF-_", headers=t).status_code == 200
    # 非法 vid 422
    r = c.post("/video-study/items", headers=t,
               json={"vid": "short", "title": "X"})
    assert r.status_code == 422


def test_retell_submit_grade_and_permissions(assigned_env):
    c = _client(assigned_env)
    t, s = _login(c, "teacher"), _login(c, "malin")
    vid = c.get("/video-study", headers=s).json()["data"]["videos"][0]["vid"]
    # 不存在（或未安排）的视频：门禁先行 403（不向学生泄露存在性）
    r = c.post("/video-study/retells", headers=s, json={
        "vid": "zzzzzzzzzzz", "audio_b64": _fake_audio(),
        "mime": "audio/webm", "duration_sec": 10})
    assert r.status_code == 403
    # 学生提交 → pending
    r = c.post("/video-study/retells", headers=s, json={
        "vid": vid, "audio_b64": _fake_audio(),
        "mime": "audio/webm", "duration_sec": 95})
    assert r.status_code == 200
    rid = r.json()["data"]["id"]
    assert r.json()["data"]["status"] == "pending"
    # 学生不能批改、不能删
    assert c.put(f"/video-study/retells/{rid}", headers=s,
                 json={"score": 9, "comment": ""}).status_code == 403
    assert c.delete(f"/video-study/retells/{rid}", headers=s).status_code == 403
    # 教师批改（分数越界 422 → 合法 9 分）
    assert c.put(f"/video-study/retells/{rid}", headers=t,
                 json={"score": 11, "comment": ""}).status_code == 422
    r = c.put(f"/video-study/retells/{rid}", headers=t,
              json={"score": 9, "comment": "讲得很完整"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "graded" and data["teacher_score"] == 9
    # 音频二进制端点：学生可听自己的
    token = s["Authorization"][7:]
    r = c.get(f"/video-study/retells/{rid}/audio?token={token}")
    assert r.status_code == 200 and len(r.content) == 1028
    # 学生列表只见自己的
    lst = c.get("/video-study/retells", headers=s).json()["data"]
    assert len(lst) == 1 and lst[0]["vid"] == vid


def test_plan_video_task_signal(assigned_env):
    """计划 videos → 今日清单出视频行；提交复述后自动点亮；进度分类正确。"""
    c = _client(assigned_env)
    t, s = _login(c, "teacher"), _login(c, "malin")
    vids = c.get("/video-study", headers=t).json()["data"]
    # 今日清单：工作日应有 1 个视频行（未完成）
    r = c.get("/plan/today", headers=s)
    assert r.status_code == 200
    vrows = [x for x in r.json()["data"]["tasks"] if x["type"] == "video"]
    assert len(vrows) >= 1
    assert not any(x["done"] for x in vrows)
    # 提交复述 → 该视频自动 ✓
    r = c.post("/video-study/retells", headers=s, json={
        "vid": vids[0]["vid"], "audio_b64": _fake_audio(),
        "mime": "audio/webm", "duration_sec": 60})
    assert r.status_code == 200
    r = c.get("/plan/today", headers=s)
    vrows = {x["key"]: x for x in r.json()["data"]["tasks"]
             if x["type"] == "video"}
    assert vrows[f"video:{vids[0]['vid']}"]["done"] is True
    # 周目标维度出现「视频复述」
    r = c.get("/plan/week-view", headers=t)
    dims = r.json()["data"]["goal_progress"]["dims"]
    vd = [d for d in dims if d["name"] == "视频复述"]
    assert vd and vd[0]["total"] == 2 and vd[0]["done"] == 1


def test_progress_endpoint(assigned_env):
    """教师进度端点：分类（本周/未完成/已完成/计划外）+ 学生不可访问。"""
    c = _client(assigned_env)
    t, s = _login(c, "teacher"), _login(c, "malin")
    vids = c.get("/video-study", headers=t).json()["data"]
    # 学生访问教师端点 403
    assert c.get("/video-study/progress", headers=s).status_code == 403
    # 初始：本周 2 全部未完成
    d = c.get("/video-study/progress?user=malin", headers=t).json()["data"]
    assert len(d["categories"]["week"]) == 2
    assert len(d["categories"]["todo"]) == 2
    assert d["categories"]["done"] == []
    # 提交并批改 → done 1 / todo 1，best_score 带出
    rid = c.post("/video-study/retells", headers=s, json={
        "vid": vids[0]["vid"], "audio_b64": _fake_audio(),
        "mime": "audio/webm", "duration_sec": 55}).json()["data"]["id"]
    c.put(f"/video-study/retells/{rid}", headers=t,
          json={"score": 8, "comment": "复述流利"})
    d = c.get("/video-study/progress?user=malin", headers=t).json()["data"]
    assert d["categories"]["done"] == [vids[0]["vid"]]
    assert d["categories"]["todo"] == [vids[1]["vid"]]
    item = next(v for v in d["videos"] if v["vid"] == vids[0]["vid"])
    assert item["best_score"] == 8 and item["retell_n"] == 1
    assert item["last_comment"] == "复述流利"
    # 学生列表同步：done 分组
    sd = c.get("/video-study", headers=s).json()["data"]
    assert sd["categories"]["done"] == [vids[0]["vid"]]


def test_plan_editor_video_pool_and_one_per_day(video_env):
    """编辑器选项源只给上架视频；工作日/周末每天至多排 1 个，并带 video_id。"""
    from datetime import date, timedelta

    from grammar_kb.clock import current_monday

    c = _client(video_env)
    t = _login(c, "teacher")
    vids = c.get("/video-study", headers=t).json()["data"]
    ed = c.get("/plan/editor-data", headers=t).json()["data"]
    pool = {v["vid"]: v for v in ed["videos"]}
    assert vids[0]["vid"] in pool
    assert pool[vids[0]["vid"]]["title"]
    assert pool[vids[0]["vid"]]["duration_sec"] > 0
    off = vids[0]
    assert c.put(f"/video-study/items/{off['vid']}", headers=t, json={
        "vid": off["vid"], "title": off["title"], "enabled": False,
    }).status_code == 200
    ed = c.get("/plan/editor-data", headers=t).json()["data"]
    assert all(v["vid"] != off["vid"] for v in ed["videos"])

    enabled = [v["vid"] for v in c.get("/video-study", headers=t).json()["data"]]
    # 下架的排在最前：当天应跳过它，改出下一个仍上架的
    four = [off["vid"], *enabled[:3]]
    ws = current_monday()
    r = c.put(f"/plan/weeks/{ws.isoformat()}", headers=t, json={
        "tasks": {"videos": four}, "notes": "", "user": "malin"})
    assert r.status_code == 200
    friday = (ws + timedelta(days=4)).isoformat()
    rows = [x for x in c.get(f"/plan/today?day={friday}&user=malin", headers=t)
            .json()["data"]["tasks"] if x["type"] == "video"]
    assert len(rows) == 1
    assert rows[0]["video_id"] == enabled[0]
    assert rows[0]["key"] == f"video:{enabled[0]}"
    sunday = (ws + timedelta(days=6)).isoformat()
    rows = [x for x in c.get(f"/plan/today?day={sunday}&user=malin", headers=t)
            .json()["data"]["tasks"] if x["type"] == "video"]
    assert len(rows) == 1 and rows[0]["video_id"] == enabled[0]

    wv = c.get(f"/plan/week-view?week_start={ws.isoformat()}", headers=t).json()["data"]
    assert wv["breakdown"]["video_count"] == 4
    assert "视频复述" in wv["breakdown"]["drill_types"]
    assert [x["vid"] for x in wv["video_titles"]] == four
    assert wv["video_titles"][0]["title"] == off["title"]
    assert all(x["title"] for x in wv["video_titles"])
    dims = [d for d in wv["goal_progress"]["dims"] if d["name"] == "视频复述"]
    assert dims and dims[0]["total"] == 4
    todo = c.get("/plan/week-todo?user=malin", headers=t).json()["data"]
    assert todo["goals"]["videos"] == four
    assert date.fromisoformat(friday).weekday() == 4

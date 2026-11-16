"""周范围新规则：微练习七天打卡、作业不再分工作日/周末、周统计周日夜里截止。"""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.clock import CN_TZ, freeze_clock  # noqa: E402
from grammar_kb.micro import MicroStore  # noqa: E402
from grammar_kb.plan.store import PlanStore  # noqa: E402
from grammar_kb.server import create_app  # noqa: E402

MON = "2026-11-09"
SAT = "2026-11-14"
SUN = "2026-11-15"
NEXT = "2026-11-16"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    monkeypatch.setenv("GRAMMAR_KB_FCE_DB", str(tmp_path / "fce.db"))
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DB", str(tmp_path / "library.db"))
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("RECITE_DEDUP_WINDOW_SEC", "0")
    sqlite3.connect(tmp_path / "grammar.db").close()
    return tmp_path


def _client(env) -> TestClient:
    return TestClient(create_app(
        str(env / "grammar.db"), str(env / "exam.db"),
        fce_db_path=str(env / "fce.db")))


def _login(c, user, password):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _when(day: str, hour: int = 12) -> datetime:
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hour, 0, tzinfo=CN_TZ)


def test_micro_checkin_includes_weekend_and_rejects_expired(env):
    """微练习：周六可打、周日还没到；过了的周五不能补。分母是 7。"""
    with freeze_clock(_when(SAT)):
        c = _client(env)
        ht = _login(c, "teacher", "123456")
        hs = _login(c, "malin", "123456")
        assert c.put(f"/plan/weeks/{MON}", headers=ht, json={
            "tasks": {"micro": 1}, "notes": "",
        }).status_code == 200
        micro = MicroStore(str(env / "fce.db"))
        ids = [micro.add_question(
            category="grammar", stem=f"q{i}", options=["A", "B"],
            answer="A", status="published") for i in range(3)]
        fri = "2026-11-13"
        today = micro.today("malin", SAT)
        assert today["slot"] is True and today["status"] == "open"
        assert today["can_check_in"] is True
        assert micro.submit("malin", SAT, [
            {"id": q, "choice": "A"} for q in ids])["done"] is True
        with pytest.raises(ValueError, match="不能补"):
            micro.submit("malin", fri, [{"id": ids[0], "choice": "A"}])
        expired = c.post("/micro/submit", headers=hs, json={
            "day": fri, "answers": [{"id": ids[0], "choice": "A"}],
        })
        assert expired.status_code == 422
        wv = c.get(f"/plan/week-view?week_start={MON}&user=malin",
                   headers=ht).json()["data"]
        by = {r["label"]: r for r in wv["micro_checkins"]}
        assert list(by) == ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        assert all(r["required"] for r in wv["micro_checkins"])
        assert by["周五"]["status"] == "missed"
        assert by["周六"]["status"] == "done"
        assert by["周日"]["status"] == "upcoming"
        dim = next(x for x in wv["goal_progress"]["dims"] if x["name"] == "微练习")
        assert dim["done"] == 1 and dim["total"] == 7


def test_homework_schedule_does_not_split_weekend(env):
    """FCE、单词卷、错词、周末模式的视频，周一也排，不再专属某一天。"""
    con = sqlite3.connect(env / "fce.db")
    con.execute("""CREATE TABLE IF NOT EXISTS video_item (
        vid TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT DEFAULT '',
        duration_sec INTEGER DEFAULT 0, vocab TEXT DEFAULT '[]',
        hints TEXT DEFAULT '', enabled INTEGER DEFAULT 1,
        ord INTEGER DEFAULT 0, created_at TEXT)""")
    con.execute("INSERT INTO video_item (vid, title) VALUES ('va', 'VideoA')")
    con.execute("INSERT INTO video_item (vid, title) VALUES ('vb', 'VideoB')")
    con.commit()
    con.close()
    ps = PlanStore(str(env / "fce.db"))
    ps.put_week(MON, {
        "lectures": [29],
        "fce": ["Test1 RUE"],
        "vocab_goal": 10,
        "vocab_papers": ["L0-paper"],
        "video_units": [{"vids": ["va", "vb"], "mode": "weekend"}],
        "boost_days": [6],
        "boost_items": [{"type": "memory", "topic_id": "lecture-30-passive-2",
                         "node_key": "k2"}],
    }, "", user="malin")
    mon = ps.week_daily_tasks(MON, for_date=MON, user="malin")
    kinds = {t["type"] for t in mon}
    assert {"lecture", "fce", "vocab", "paper", "video", "boost"} <= kinds
    assert [t["vid"] for t in mon if t["type"] == "video"] == ["va"]
    assert "工作日" not in " ".join(t.get("detail") or "" for t in mon)
    assert "周末" not in " ".join(t.get("detail") or "" for t in mon)
    sat = ps.week_daily_tasks(MON, for_date=SAT, user="malin")
    sat_kinds = {t["type"] for t in sat}
    assert "lecture" in sat_kinds and "fce" in sat_kinds and "vocab" in sat_kinds
    # boost_days 只写着周六，周一照样有提升行
    assert any(t["type"] == "boost" for t in mon)


def test_in_week_any_day_counts_and_next_week_does_not(env):
    """周内周日做完算这一周；下周一再做的讲次和词，不改上一周。"""
    with freeze_clock(_when(SUN)):
        c = _client(env)
        hs = _login(c, "malin", "123456")
        ht = _login(c, "teacher", "123456")
        assert c.put(f"/plan/weeks/{MON}", headers=ht, json={
            "tasks": {"lectures": [29, 30], "vocab_goal": 2},
        }).status_code == 200
        assert c.post("/exams", headers=hs, json={
            "lecture": 29, "date": SAT, "score": 80, "wrong": [], "kind": "lecture",
        }).status_code == 200
        con = sqlite3.connect(env / "fce.db")
        con.execute(
            "INSERT INTO recite_word_progress"
            " (user, word, level, right, wrong, mastered_at)"
            " VALUES ('malin', 'inweek', 0, 1, 0, ?)",
            (f"{SAT}T04:00:00+00:00",),
        )
        con.commit()
        con.close()
        wv = c.get(f"/plan/week-view?week_start={MON}&user=malin",
                   headers=ht).json()["data"]
        pool = {t["key"]: t for t in wv["week_pool"]}
        assert pool["lecture:29"]["done"] is True
        assert pool["lecture:30"]["done"] is False
        dim = next(x for x in wv["goal_progress"]["dims"] if x["name"] == "词汇")
        assert dim["done"] == 1
        # 周一的格子上也认周六做完的讲次
        mon = next(d for d in wv["days"] if d["date"] == MON)
        lec = next(t for t in mon["tasks"] if t["key"] == "lecture:29")
        assert lec["done"] is True and lec["why"] == "week_credit"

    with freeze_clock(_when(NEXT)):
        c = _client(env)
        hs = _login(c, "malin", "123456")
        ht = _login(c, "teacher", "123456")
        assert c.post("/exams", headers=hs, json={
            "lecture": 30, "date": NEXT, "score": 90, "wrong": [], "kind": "lecture",
        }).status_code == 200
        con = sqlite3.connect(env / "fce.db")
        con.execute(
            "INSERT INTO recite_word_progress"
            " (user, word, level, right, wrong, mastered_at)"
            " VALUES ('malin', 'nextweek', 0, 1, 0, ?)",
            (f"{NEXT}T04:00:00+00:00",),
        )
        con.commit()
        con.close()
        wv = c.get(f"/plan/week-view?week_start={MON}&user=malin",
                   headers=ht).json()["data"]
        pool = {t["key"]: t for t in wv["week_pool"]}
        assert pool["lecture:29"]["done"] is True  # 周内完成的还在
        assert pool["lecture:30"]["done"] is False
        dim = next(x for x in wv["goal_progress"]["dims"] if x["name"] == "词汇")
        assert dim["done"] == 1 and dim["total"] == 2
        # 下一周自己的计划没有这讲，做完也不把上一周点亮
        assert c.put(f"/plan/weeks/{NEXT}", headers=ht, json={
            "tasks": {"lectures": [30], "vocab_goal": 1},
        }).status_code == 200
        nxt = c.get(f"/plan/week-view?week_start={NEXT}&user=malin",
                    headers=ht).json()["data"]
        npool = {t["key"]: t for t in nxt["week_pool"]}
        assert npool["lecture:30"]["done"] is True
        ndim = next(x for x in nxt["goal_progress"]["dims"] if x["name"] == "词汇")
        assert ndim["done"] == 1

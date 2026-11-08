"""今日闭环聚合（plan/loop）测试：四格口径 / streak 回溯 / next_queue /
my-week 端点并入。

固定日历：2026-11-09（周一）所在周，freeze_clock 把「今天」钉在周二，
recite.review_summary / micro.submit 的当日口径与 loop_status(day=周二)
对齐。隔离库：GRAMMAR_KB_FCE_DB（plan.boost 走 dbreg 默认解析）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.clock import CN_TZ, freeze_clock  # noqa: E402
from grammar_kb.focus import FocusStore  # noqa: E402
from grammar_kb.micro import MicroStore  # noqa: E402
from grammar_kb.plan import boost as plan_boost  # noqa: E402
from grammar_kb.plan.loop import loop_status  # noqa: E402
from grammar_kb.plan.store import PlanStore  # noqa: E402
from grammar_kb.recite import ReciteStore  # noqa: E402
from grammar_kb.server import create_app  # noqa: E402

MON = "2026-11-09"
TUE = "2026-11-10"
WED = "2026-11-11"
ITEMS = [
    {"type": "memory", "topic_id": "lecture-30-passive-2", "node_key": "k2"},
    {"type": "retell", "pattern_id": 12},
]
MEM_REF = "memory:lecture-30-passive-2:k2"


def _utc(day_iso: str, hh: int = 12) -> str:
    """北京日 day_iso 的 hh 点（默认北京 12 点）对应的 UTC 串。"""
    dt = (datetime.fromisoformat(f"{day_iso} {hh:02d}:00:00")
          - timedelta(hours=8))
    return dt.strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    fce = str(tmp_path / "fce.db")
    monkeypatch.setenv("GRAMMAR_KB_FCE_DB", fce)
    monkeypatch.setenv("GRAMMAR_KB_DB", str(tmp_path / "grammar.db"))
    monkeypatch.setenv("GRAMMAR_KB_EXAM_DB", str(tmp_path / "exam.db"))
    lib = tmp_path / "library.db"
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DB", str(lib))
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DIR", str(tmp_path / "files"))
    con = sqlite3.connect(lib)
    con.executescript("""
      CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT);
      CREATE TABLE chapters (book_id INTEGER, idx INTEGER, title TEXT,
                             word_count INTEGER);
      CREATE TABLE book_reading_config (book_id INTEGER PRIMARY KEY,
                                        chapters TEXT);
      CREATE TABLE reading_progress (book_id INTEGER, user TEXT,
                                     percent REAL, reading_seconds INTEGER,
                                     updated_at TEXT);
    """)
    con.execute("INSERT INTO books VALUES (1, 'Percy Jackson')")
    con.executemany("INSERT INTO chapters VALUES (1, ?, ?, 1000)", [(1, "ch1"), (2, "ch2")])
    con.commit()
    con.close()
    return {"fce": fce, "exam": str(tmp_path / "exam.db"),
            "grammar": str(tmp_path / "grammar.db"),
            "users": str(tmp_path / "users.json"),
            "secret": str(tmp_path / "secret.key")}


def _stores(env):
    store = PlanStore(env["fce"], exam_db_path=env["exam"],
                      grammar_db_path=env["grammar"])
    recite = ReciteStore(env["fce"])
    micro = MicroStore(env["fce"], exam_db_path=env["exam"],
                       grammar_db_path=env["grammar"])
    FocusStore(env["fce"])
    return store, recite, micro


def _put_week(store, tasks):
    store.put_week(MON, tasks, "", "malin")


def _focus_row(env, user, day, pct=(0.0, 1.0), active=120):
    con = sqlite3.connect(env["fce"])
    con.execute(
        "INSERT INTO focus_sessions (session_id, user, book_id, book_title,"
        " started_at, total_sec, active_sec, scroll_count, lookups, plays,"
        " chapter_navs, percent_start, percent_end, created_at, module,"
        " percent_track) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"s-{user}-{day}-{pct[1]}", user, 1, "Percy Jackson",
         _utc(day), active, active, 3, 1, 0, 0, pct[0], pct[1],
         _utc(day), "library", "[]"))
    con.commit()
    con.close()


def _micro_daily_done(env, user, day):
    con = sqlite3.connect(env["fce"])
    con.execute(
        "INSERT OR REPLACE INTO micro_daily (user, date, qids_json,"
        " answered_json, done) VALUES (?,?,?,?,1)",
        (user, day, "[1,2,3]", '{"1":"A","2":"B","3":"C"}'))
    con.commit()
    con.close()


# ---- 四格基线（提升日、空信号） ----

def test_loop_baseline_boost_day(env):
    with freeze_clock(datetime(2026, 11, 10, 12, 0, tzinfo=CN_TZ)):
        store, recite, micro = _stores(env)
        _put_week(store, {"boost_days": [2, 4], "boost_items": ITEMS})
        out = loop_status("malin", TUE, store=store, recite=recite,
                          micro=micro)
    assert out["recite"] == {"clear": True, "due": 0, "overdue": 0}
    assert out["micro"] == {"done": False, "answered": 0, "total": 3}
    assert out["reading"] == {"ok": False, "today_words": 0,
                              "target_words": None}  # 没配泛读书 → 目标 None
    assert out["boost"]["scheduled"] is True
    assert out["boost"]["done"] is False
    assert out["boost"]["configured"] is True
    assert out["boost"]["label"].startswith("⭐ 提升")
    assert out["streak"] == 0
    assert out["next_queue"] == {"recite_words": 0, "micro_questions": 0}


def test_loop_non_boost_day(env):
    with freeze_clock(datetime(2026, 11, 11, 12, 0, tzinfo=CN_TZ)):
        store, recite, micro = _stores(env)
        _put_week(store, {"boost_days": [2, 4], "boost_items": ITEMS})
        out = loop_status("malin", WED, store=store, recite=recite,
                          micro=micro)
    assert out["boost"] is None  # 非提升日


def test_loop_boost_unconfigured_day(env):
    """boost_days 配了、items 空：说明行 → scheduled 且不拦闭环。"""
    with freeze_clock(datetime(2026, 11, 10, 12, 0, tzinfo=CN_TZ)):
        store, recite, micro = _stores(env)
        _put_week(store, {"boost_days": [2, 4]})
        out = loop_status("malin", TUE, store=store, recite=recite,
                          micro=micro)
    assert out["boost"]["scheduled"] is True
    assert out["boost"]["done"] is True
    assert out["boost"]["configured"] is False


def test_loop_boost_done(env):
    with freeze_clock(datetime(2026, 11, 10, 12, 0, tzinfo=CN_TZ)):
        store, recite, micro = _stores(env)
        _put_week(store, {"boost_days": [2, 4], "boost_items": ITEMS})
        plan_boost.record("malin", TUE, MEM_REF, "memory")
        plan_boost.record("malin", TUE, "retell:12", "retell")
        out = loop_status("malin", TUE, store=store, recite=recite,
                          micro=micro)
    assert out["boost"]["done"] is True


# ---- 背词格与明日回炉 ----

def test_loop_recite_due_and_next_queue(env):
    with freeze_clock(datetime(2026, 11, 10, 12, 0, tzinfo=CN_TZ)):
        store, recite, micro = _stores(env)
        _put_week(store, {"boost_days": [2, 4], "boost_items": ITEMS})
        con = sqlite3.connect(env["fce"])
        con.execute(
            "INSERT INTO recite_review (user, word, next_review)"
            " VALUES ('malin', 'due-word', ?)", (TUE,))       # 今天到期
        con.execute(
            "INSERT INTO recite_review (user, word, next_review)"
            " VALUES ('malin', 'tomorrow-word', ?)", (WED,))  # 明天到期
        con.commit()
        con.close()
        out = loop_status("malin", TUE, store=store, recite=recite,
                          micro=micro)
    assert out["recite"] == {"clear": False, "due": 1, "overdue": 0}
    assert out["next_queue"]["recite_words"] == 1


def test_loop_recite_overdue(env):
    with freeze_clock(datetime(2026, 11, 10, 12, 0, tzinfo=CN_TZ)):
        store, recite, micro = _stores(env)
        _put_week(store, {})
        con = sqlite3.connect(env["fce"])
        con.execute(
            "INSERT INTO recite_review (user, word, next_review)"
            " VALUES ('malin', 'old-word', '2026-11-05')")
        con.commit()
        con.close()
        out = loop_status("malin", TUE, store=store, recite=recite,
                          micro=micro)
    assert out["recite"]["overdue"] == 1
    assert out["recite"]["clear"] is False


# ---- 微练习格 ----

def test_loop_micro_done(env):
    with freeze_clock(datetime(2026, 11, 10, 12, 0, tzinfo=CN_TZ)):
        store, recite, micro = _stores(env)
        _put_week(store, {})
        ids = [micro.add_question(category="grammar", stem=f"q{i}",
                                  options=["A", "B"], answer="A",
                                  status="published") for i in range(3)]
        micro.today("malin", TUE)
        rep = micro.submit("malin", TUE,
                           [{"id": q, "choice": "A"} for q in ids])
        assert rep["done"] is True
        out = loop_status("malin", TUE, store=store, recite=recite,
                          micro=micro)
    assert out["micro"]["done"] is True
    assert out["micro"]["answered"] == 3
    assert out["micro"]["total"] == 3


def test_loop_next_queue_micro_wrong(env):
    """wrong 池未翻正题数：有错题未答对 → 1；最近答对 → 0。"""
    with freeze_clock(datetime(2026, 11, 10, 12, 0, tzinfo=CN_TZ)):
        store, recite, micro = _stores(env)
        _put_week(store, {})
        qid = micro.add_question(category="wrong", stem="w1",
                                 options=["A", "B"], answer="A",
                                 status="published")
        out = loop_status("malin", TUE, store=store, recite=recite,
                          micro=micro)
        assert out["next_queue"]["micro_questions"] == 1
        con = sqlite3.connect(env["fce"])
        con.execute(
            "INSERT INTO micro_log (user, qid, date, choice, is_correct)"
            " VALUES ('malin', ?, ?, 'A', 1)", (qid, TUE))
        con.commit()
        con.close()
        out = loop_status("malin", TUE, store=store, recite=recite,
                          micro=micro)
    assert out["next_queue"]["micro_questions"] == 0


# ---- 泛读格（目标词数 = books.py「今日 +N 词」同一公式） ----

def test_loop_reading_target(env):
    with freeze_clock(datetime(2026, 11, 10, 12, 0, tzinfo=CN_TZ)):
        store, recite, micro = _stores(env)
        _put_week(store, {"boost_days": [2, 4], "boost_items": ITEMS,
                          "reading": {"1": 100}})
        _focus_row(env, "malin", TUE, pct=(0.0, 1.0))  # 读 1% ≈ 20 词
        out = loop_status("malin", TUE, store=store, recite=recite,
                          micro=micro)
    assert out["reading"]["today_words"] == 20
    assert out["reading"]["target_words"] == 333  # round(2000/6)，周二剩 6 天
    assert out["reading"]["ok"] is False


def test_loop_reading_reached(env):
    with freeze_clock(datetime(2026, 11, 10, 12, 0, tzinfo=CN_TZ)):
        store, recite, micro = _stores(env)
        _put_week(store, {"reading": {"1": 100}})
        from pathlib import Path
        con = sqlite3.connect(Path(env["fce"]).parent / "library.db")
        con.execute("INSERT INTO reading_progress VALUES (1, 'malin', 99.5, 0, '')")
        con.commit()
        con.close()
        _focus_row(env, "malin", TUE, pct=(0.0, 1.0))
        out = loop_status("malin", TUE, store=store, recite=recite,
                          micro=micro)
    assert out["reading"]["target_words"] == 2   # 剩 10 词摊 6 天
    assert out["reading"]["ok"] is True


# ---- streak 回溯 ----

def test_loop_streak(env):
    with freeze_clock(datetime(2026, 11, 10, 12, 0, tzinfo=CN_TZ)):
        store, recite, micro = _stores(env)
        _put_week(store, {"reading": {"1": 100}})
        # 今天全绿：微练习做完 + 泛读达标（额度小：读位 99.5%）+ 无背词欠账
        from pathlib import Path
        con = sqlite3.connect(Path(env["fce"]).parent / "library.db")
        con.execute("INSERT INTO reading_progress VALUES (1, 'malin', 99.5, 0, '')")
        con.commit()
        con.close()
        ids = [micro.add_question(category="grammar", stem=f"s{i}",
                                  options=["A", "B"], answer="A",
                                  status="published") for i in range(3)]
        micro.today("malin", TUE)
        micro.submit("malin", TUE, [{"id": q, "choice": "A"} for q in ids])
        _focus_row(env, "malin", TUE, pct=(99.0, 99.5))
        # 昨天全绿：micro_daily done=1 + 有有效泛读会话 + 无背词欠账
        _micro_daily_done(env, "malin", MON)
        _focus_row(env, "malin", MON, pct=(97.0, 99.0))
        out = loop_status("malin", TUE, store=store, recite=recite,
                          micro=micro)
    assert out["streak"] == 2  # 周二 + 周一；周日没做断掉


def test_loop_streak_broken_by_today(env):
    """今天没闭环 → streak 0（从今天往前回溯，今天不绿即断）。"""
    with freeze_clock(datetime(2026, 11, 10, 12, 0, tzinfo=CN_TZ)):
        store, recite, micro = _stores(env)
        _put_week(store, {})
        _micro_daily_done(env, "malin", MON)
        out = loop_status("malin", TUE, store=store, recite=recite,
                          micro=micro)
    assert out["streak"] == 0


# ---- my-week 端点并入 ----

def _client(env) -> TestClient:
    return TestClient(create_app(env["fce"] + ".grammar", env["exam"],
                                 fce_db_path=env["fce"]))


def _login(c, user, password):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def test_my_week_includes_loop(env, monkeypatch):
    monkeypatch.setenv("GRAMMAR_KB_USERS", env["users"])
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", env["secret"])
    with freeze_clock(datetime(2026, 11, 10, 12, 0, tzinfo=CN_TZ)):
        store, _recite, _micro = _stores(env)
        _put_week(store, {"boost_days": [2, 4], "boost_items": ITEMS})
        c = _client(env)
        hs = _login(c, "malin", "123456")
        r = c.get("/plan/my-week", headers=hs)
        assert r.status_code == 200
        data = r.json()["data"]
    assert data["today"]["date"] == TUE
    loop = data["today"]["loop"]
    assert loop["recite"]["clear"] is True
    assert loop["boost"]["scheduled"] is True
    # 现有响应字段不受影响
    assert data["week_start"] == MON
    assert len(data["days"]) == 7
    assert "breakdown" in data and "completion" in data


# ---- 评审修复回归：明日回炉同源 + 提升档两态 ----

def test_loop_next_queue_includes_recent_wrong(env):
    """回炉计数与 _pick_daily 优先级① 同源：近 3 天答错的任意类目题都算
    （此前只数 category=wrong，漏掉 tense 等近错题）。"""
    with freeze_clock(datetime(2026, 11, 10, 12, 0, tzinfo=CN_TZ)):
        store, recite, micro = _stores(env)
        _put_week(store, {})
        qt = micro.add_question(category="tense", stem="t1",
                                options=["A", "B"], answer="A",
                                status="published")
        con = sqlite3.connect(env["fce"])
        con.execute(
            "INSERT INTO micro_log (user, qid, date, choice, is_correct)"
            " VALUES ('malin', ?, ?, 'B', 0)", (qt, "2026-11-09"))  # 昨天错
        con.commit()
        con.close()
        out = loop_status("malin", TUE, store=store, recite=recite,
                          micro=micro)
        assert out["next_queue"]["micro_questions"] == 1
        # 此后答对 → 翻正，回炉清零
        con = sqlite3.connect(env["fce"])
        con.execute(
            "INSERT INTO micro_log (user, qid, date, choice, is_correct)"
            " VALUES ('malin', ?, ?, 'A', 1)", (qt, TUE))
        con.commit()
        con.close()
        out = loop_status("malin", TUE, store=store, recite=recite,
                          micro=micro)
    assert out["next_queue"]["micro_questions"] == 0


def test_loop_boost_pending_and_low_states(env):
    """提升档两态：提交未批 done=True/state=pending（闭环不卡）；
    批低分 done=True/state=graded_low；过关 state=passed。"""
    with freeze_clock(datetime(2026, 11, 10, 12, 0, tzinfo=CN_TZ)):
        store, recite, micro = _stores(env)
        _put_week(store, {"boost_days": [2, 4],
                          "boost_items": [{"type": "memory",
                                           "topic_id": "lecture-30-passive-2",
                                           "node_key": "k2"}]})
        # 未做
        out = loop_status("malin", TUE, store=store, recite=recite,
                          micro=micro)
        assert out["boost"]["state"] == "todo"
        assert out["boost"]["done"] is False
        # 打卡 → passed（memory 打卡即过关）
        plan_boost.record("malin", TUE, MEM_REF, "memory")
        out = loop_status("malin", TUE, store=store, recite=recite,
                          micro=micro)
        assert out["boost"]["done"] is True
        assert out["boost"]["state"] == "passed"
        assert out["boost"]["passed"] is True
        # 换 retell 任务：提交未批 → pending，done=True（闭环不卡批改延迟）
        _put_week(store, {"boost_days": [2, 4],
                          "boost_items": [{"type": "retell",
                                           "pattern_id": 12}]})
        plan_boost.record("malin", TUE, "retell:12", "retell")
        out = loop_status("malin", TUE, store=store, recite=recite,
                          micro=micro)
        assert out["boost"]["done"] is True
        assert out["boost"]["state"] == "pending"
        # 批 5 分 → graded_low（过关不达成）
        plan_boost.record("malin", TUE, "retell:12", "retell", score=5)
        out = loop_status("malin", TUE, store=store, recite=recite,
                          micro=micro)
        assert out["boost"]["done"] is True
        assert out["boost"]["state"] == "graded_low"
        assert out["boost"]["passed"] is False

"""计划表批量信号的正确性守护（性能改造不改口径）：

1. week_view 的逐日矩阵与逐日 today_tasks 同口径（改前即成立，改后不得破坏）；
2. WeekCtx 周窗口切片与逐窗直查逐项等价（lecture_done / fce / 朗读 /
   复述 / 词汇计数），这是 O(天²) 优化的核心风险点；
3. /plan/weeks 未来周零值、响应结构不变。
"""
from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.clock import monday_of, today_cn  # noqa: E402
from grammar_kb.plan.ctx import WeekCtx  # noqa: E402
from grammar_kb.plan.store import PlanStore  # noqa: E402
from grammar_kb.server import create_app  # noqa: E402
from grammar_kb.plan.util import add_days, task_public  # noqa: E402

MON = monday_of(today_cn()).isoformat()
PREV = (monday_of(today_cn()) - timedelta(days=7)).isoformat()
NEXT = (monday_of(today_cn()) + timedelta(days=7)).isoformat()


@pytest.fixture()
def plan_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DB", str(tmp_path / "library.db"))
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("RECITE_DEDUP_WINDOW_SEC", "0")
    sqlite3.connect(tmp_path / "grammar.db").close()
    return tmp_path


def _client(env) -> TestClient:
    return TestClient(create_app(str(env / "grammar.db"), str(env / "exam.db"),
                                 fce_db_path=str(env / "fce.db")))


def _login(c, user, password):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _seed_week_activity(env, c, hs, ht):
    """当前周与上周都铺一点真实信号：成绩、背词、FCE、朗读、复述。"""
    # 成绩：上周 1 条 + 本周 2 讲（一讲今天，一讲本周早些天用日期直填）
    c.post("/exams", headers=hs, json={"lecture": 21, "date": PREV,
                                       "score": 70, "wrong": []})
    c.post("/exams", headers=hs, json={"lecture": 29, "date": MON,
                                       "score": 82, "wrong": []})
    c.post("/exams", headers=hs, json={"lecture": 30, "date": MON,
                                       "score": 55, "wrong": [3, 4]})
    c.post("/exams", headers=hs, json={"lecture": 29, "date": MON,
                                       "score": 90, "wrong": [], "kind": "hw"})
    # 周三的成绩：专踩「窗口右边界恰是有记录日」——hi 为开区间，
    # (MON, WED) 不得把这条算进去（切片口径与 SQL date < date(?) 一致）
    c.post("/exams", headers=hs, json={"lecture": 31,
                                       "date": add_days(MON, 2),
                                       "score": 77, "wrong": []})
    # 背词：本周新掌握 2 词
    for w in ("alpha", "delta"):
        c.post("/recite/sessions", headers=hs, json={
            "total": 1, "wrong": 0, "acc": 100, "duration_sec": 5,
            "wrong_words": [], "mode": "flip", "scope": "all",
            "details": [{"word": w, "correct": True}],
        })
    # FCE 提交 + 朗读录音 + 视频复述（直接落 fce.db，带现在时间戳）
    now = __import__("datetime").datetime.utcnow().isoformat(timespec="seconds")
    with sqlite3.connect(str(env / "fce.db")) as conn:
        conn.execute(
            "INSERT INTO fce_submission (user, test_id, paper, part, status,"
            " created_at) VALUES ('malin', 1, 'Reading', 1, 'pending', ?)",
            (now,))
        conn.execute(
            "INSERT INTO reading_recordings (user, article_id, audio_b64,"
            " mime, status, created_at) VALUES ('malin', 7, 'xx',"
            " 'audio/webm', 'pending', ?)", (now,))
        conn.execute(
            "INSERT INTO video_retell (user, vid, audio_b64, mime, status,"
            " created_at) VALUES ('malin', 'vidABC', 'xx', 'audio/webm',"
            " 'pending', ?)", (now,))


def test_week_view_days_match_today_tasks(plan_env):
    """week_view 逐日矩阵 ≡ 逐日 today_tasks（同一口径两种取法）。

    week_view 内部走共享 WeekCtx 的批量切片路径；/plan/today 每天自建
    单日 ctx 直查路径。两条路径必须给出同一份清单与实录。
    """
    c = _client(plan_env)
    hs = _login(c, "malin", "123456")
    ht = _login(c, "teacher", "123456")
    _seed_week_activity(plan_env, c, hs, ht)
    c.put(f"/plan/weeks/{MON}", headers=ht, json={"tasks": {
        "lectures": [29, 30], "focus_kps": [29], "vocab_goal": 1,
        "fce": ["Test 1 · RUE P1"], "articles": [7],
        "videos": ["vidABC"],
    }})
    wv = c.get("/plan/week-view", params={"week_start": MON, "user": "malin"},
               headers=ht).json()["data"]
    today_iso = today_cn().isoformat()
    assert len(wv["days"]) == 7
    for i, day in enumerate(wv["days"]):
        d = add_days(MON, i)
        td = c.get("/plan/today", params={"user": "malin", "day": d},
                   headers=ht).json()["data"]
        assert day["date"] == td["date"] == d
        assert day["done"] == td["done"], d
        assert day["total"] == td["total"], d
        assert [task_public(t) for t in td["tasks"]] == day["tasks"], d
        if d <= today_iso:
            # 已到的日子：完成率、口径与实录也一致
            assert day["percent"] == td["percent"], d
            assert day["basis"] == td["basis"], d
            assert day["acts"] == td["acts"], d
        else:
            # 未来日灰显：percent=null、done 恒 0
            assert day["future"] is True and day["done"] == 0
            assert day["percent"] is None


def test_week_ctx_slices_match_direct_queries(plan_env):
    """WeekCtx 周内切片 ≡ 逐窗直查（判定语义一对一）。"""
    c = _client(plan_env)
    hs = _login(c, "malin", "123456")
    ht = _login(c, "teacher", "123456")
    _seed_week_activity(plan_env, c, hs, ht)
    ps = PlanStore(str(plan_env / "fce.db"),
                   exam_db_path=str(plan_env / "exam.db"),
                   grammar_db_path=str(plan_env / "grammar.db"),
                   library_db_path=str(plan_env / "library.db"))
    ctx = WeekCtx(ps, "malin", MON)
    windows = [
        (MON, add_days(MON, 1)),                # 周一单日
        (f"{MON} 00:00:00", add_days(MON, 3)),  # 周窗口前缀（带时分）
        (MON, add_days(MON, 2)),                # hi 恰踩周三成绩日（开区间）
        (add_days(MON, 2), add_days(MON, 7)),   # 后半周（lo 恰踩周三）
        (MON, add_days(MON, 7)),                # 整周
        (PREV, MON),                            # 上周（越界走回退路径）
        (PREV, add_days(MON, 7)),               # 跨两周（越界回退）
    ]
    for lo, hi in windows:
        for kind in ("lecture", "hw"):
            assert ctx.lecture_done(lo, hi, kind) == \
                ps.lecture_done(lo, hi, "malin", kind), (lo, hi, kind)
        assert ctx.fce_actuals(lo, hi) == \
            ps._fce_actuals(lo, hi, "malin"), (lo, hi)
        assert ctx.done_article_ids(lo, hi) == \
            ps._done_article_ids(lo, hi, "malin"), (lo, hi)
        assert ctx.retold_video_ids(lo, hi) == \
            ps._retold_video_ids(lo, hi, "malin"), (lo, hi)
    # 词汇按集计数 ≡ SQL word IN 口径（含基线 before 查询）
    words = {"alpha", "delta", "notmastered"}
    lo, hi = f"{MON} 00:00:00", f"{add_days(MON, 7)} 00:00:00"
    assert ctx.vocab_actuals(lo, hi, words) == \
        ps._vocab_actuals(lo, hi, "malin", words)
    assert ctx.vocab_mastered_before(MON, words) == \
        ps._vocab_mastered_before(MON, "malin", words)
    with sqlite3.connect(str(plan_env / "fce.db")) as conn:
        ph = ",".join("?" * len(words))
        n = conn.execute(
            "SELECT COUNT(*) FROM recite_word_progress"
            " WHERE user = 'malin' AND mastered_at IS NOT NULL"
            " AND date(julianday(mastered_at) + 8.0/24) >= date(?)"
            " AND date(julianday(mastered_at) + 8.0/24) < date(?)"
            f" AND word IN ({ph})", (MON, add_days(MON, 7), *words),
        ).fetchone()[0]
    assert ctx.vocab_actuals(lo, hi, words) == n
    # micro 口径：当天有成绩的讲次（任意 kind，date 精确相等）
    assert ctx.exam_lectures_on(MON) == {29, 30}


def test_plan_weeks_future_week_zero(plan_env):
    """/plan/weeks：已开始的周批量聚合，未来周直接零值不查库。

    未来周即使排了计划，actuals 也是零值（与「事件不可能落在未来」
    一致）；响应里 actuals 的键结构与逐周聚合完全相同。
    """
    c = _client(plan_env)
    hs = _login(c, "malin", "123456")
    ht = _login(c, "teacher", "123456")
    c.post("/exams", headers=hs, json={"lecture": 29, "date": MON,
                                       "score": 82, "wrong": []})
    c.put(f"/plan/weeks/{MON}", headers=ht, json={"tasks": {"lectures": [29]}})
    c.put(f"/plan/weeks/{NEXT}", headers=ht,
          json={"tasks": {"lectures": [40], "vocab_goal": 50}})
    d = c.get("/plan/weeks", params={"user": "malin"}, headers=ht).json()["data"]
    zero = {"lectures": [], "vocab_mastered": 0, "fce_parts": [], "reading": []}
    cur = next(w for w in d["weeks"] if w["week_start"] == MON)
    fut = next(w for w in d["weeks"] if w["week_start"] == NEXT)
    assert set(cur["actuals"].keys()) == set(zero.keys())
    assert cur["actuals"]["lectures"] and \
        cur["actuals"]["lectures"][0]["lecture"] == 29
    assert fut["actuals"] == zero

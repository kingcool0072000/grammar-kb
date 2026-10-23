"""复习闭环：摘要计数、攻克池、新词额度、教师告警阈值。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone as _tz

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.clock import CN_TZ, freeze_clock, today_cn  # noqa: E402
from grammar_kb.recite import (  # noqa: E402
    ALERT_IN_CYCLE,
    ALERT_QUIET_DAYS,
    NEW_WORD_CAP,
    ReciteStore,
)
from grammar_kb.server import create_app  # noqa: E402


@pytest.fixture()
def recite_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    monkeypatch.delenv("RECITE_NEW_WORD_CAP", raising=False)
    sqlite3.connect(tmp_path / "grammar.db").close()
    return tmp_path


def _client(env) -> TestClient:
    return TestClient(create_app(
        str(env / "grammar.db"), str(env / "exam.db"),
        fce_db_path=str(env / "fce.db"),
    ))


def _login(c, user, password):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _submit(c, h, details, scope="all", mode="flip"):
    wrong = [d["word"] for d in details if not d["correct"]]
    return c.post("/recite/sessions", headers=h, json={
        "total": len(details), "wrong": len(wrong),
        "acc": round(100 * (len(details) - len(wrong)) / max(len(details), 1)),
        "duration_sec": 5, "wrong_words": wrong, "mode": mode,
        "scope": scope, "details": details,
    })


def _summary(c, h, user=None):
    path = "/recite/review-summary"
    if user:
        path += f"?user={user}"
    return c.get(path, headers=h).json()["data"]


def _set_next(env, word, day, user="malin"):
    con = sqlite3.connect(env / "fce.db")
    con.execute(
        "UPDATE recite_review SET next_review = ? WHERE user = ? AND word = ?",
        (day, user, word),
    )
    con.commit()
    con.close()


def _seed_cycle(env, n, next_review, *, conquered_at=None, with_session=True,
                user="malin", word_prefix="w"):
    """直接写入周期行（告警阈值用，避免打几十次上报）。"""
    store = ReciteStore(str(env / "fce.db"))
    now = datetime.now(_tz.utc).isoformat(timespec="seconds")
    with store._connect() as conn:
        if with_session:
            conn.execute(
                "INSERT INTO recite_sessions (user, total, wrong, acc, duration_sec,"
                " wrong_words, mode, scope, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (user, 1, 0, 100, 1, "[]", "flip", "all", now),
            )
        for i in range(n):
            conn.execute(
                "INSERT INTO recite_review (user, word, level, wrong_count, stage,"
                " first_wrong_at, next_review, last_review_at, conquered_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (user, f"{word_prefix}{i:03d}", 0, 1, 0,
                 next_review, next_review, next_review, conquered_at),
            )


def test_review_summary_counts_and_activity(recite_env):
    """到期 / 逾期 / 在周期分开计；攻克 7 日与累计；错词活动只收复习会话。"""
    c = _client(recite_env)
    h = _login(c, "malin", "123456")
    today = today_cn().isoformat()
    yesterday = (today_cn() - timedelta(days=1)).isoformat()
    tomorrow = (today_cn() + timedelta(days=1)).isoformat()

    _submit(c, h, [{"word": "due", "correct": False}])
    _submit(c, h, [{"word": "late", "correct": False}])
    _submit(c, h, [{"word": "soon", "correct": False}])
    _submit(c, h, [{"word": "revme", "correct": False}])
    _submit(c, h, [{"word": "easy", "correct": True}])  # 一遍掌握，不进周期
    _set_next(recite_env, "due", today)
    _set_next(recite_env, "late", yesterday)
    _set_next(recite_env, "soon", tomorrow)
    _set_next(recite_env, "revme", today)
    # 普通会话不应出现在错词活动里；错词本会话要出现
    plain = _submit(c, h, [{"word": "easy", "correct": True}], scope="all", mode="flip")
    assert plain.status_code == 200
    rev = _submit(c, h, [{"word": "revme", "correct": True}], scope="wrongbook", mode="flip")
    assert rev.status_code == 200

    s = _summary(c, h)
    wb = c.get("/recite/wrongbook", headers=h).json()["data"]
    assert s["today"] == today
    assert s["due_today"] == 1 and s["due_words"] == ["due"]
    assert s["overdue"] == 1 and s["overdue_words"] == ["late"]
    # 在周期 = 错词本 in_cycle（revme 答对只推进一节点，四人仍在周期）
    assert s["in_cycle"] == wb["stats"]["total"] == 4
    assert s["conquered_n"]["total"] == wb["stats"]["conquered_n"]
    assert s["conquered_n"]["d7"] >= 1  # easy 今天掌握
    assert "easy" not in s["conquer_pool"]
    sessions = [a for a in s["activity"] if a["kind"] == "session"]
    assert sessions and all(
        a["scope"] == "wrongbook" or a["mode"] == "conquer" for a in sessions
    )
    assert any(a["kind"] == "word" and a["word"] == "late" for a in s["activity"])
    # 学生带别人的 user 仍只看自己
    assert _summary(c, h, user="teacher")["in_cycle"] == s["in_cycle"]


def test_review_summary_teacher_can_read_student(recite_env):
    c = _client(recite_env)
    hs = _login(c, "malin", "123456")
    ht = _login(c, "teacher", "123456")
    _submit(c, hs, [{"word": "onlymalin", "correct": False}])
    mine = _summary(c, ht, user="malin")
    assert mine["user"] == "malin" and mine["in_cycle"] == 1
    own = _summary(c, ht)
    assert own["user"] == "teacher" and own["in_cycle"] == 0


def test_summary_splits_due_on_beijing_calendar(recite_env):
    """UTC 仍是 10-23 时，北京 10-24 01:30：当天到期与前一日逾期要分开。"""
    c = _client(recite_env)
    h = _login(c, "malin", "123456")
    when = datetime(2026, 10, 24, 1, 30, tzinfo=CN_TZ)
    with freeze_clock(when):
        _submit(c, h, [
            {"word": "todaynew", "correct": False},
            {"word": "mastered", "correct": True},
        ])
        _set_next(recite_env, "todaynew", "2026-10-25")  # 答错日本来就是明天，保持
        # 手工放两个词：一个今天到期，一个昨天（UTC 的「今天」）逾期
        store = ReciteStore(str(recite_env / "fce.db"))
        with store._connect() as conn:
            conn.execute(
                "INSERT INTO recite_review (user, word, level, wrong_count, stage,"
                " first_wrong_at, next_review, last_review_at)"
                " VALUES ('malin','due24',0,2,0,'2026-10-23','2026-10-24','2026-10-23')"
            )
            conn.execute(
                "INSERT INTO recite_review (user, word, level, wrong_count, stage,"
                " first_wrong_at, next_review, last_review_at)"
                " VALUES ('malin','over23',0,4,0,'2026-10-20','2026-10-23','2026-10-20')"
            )
            # 一遍掌握落在近 7 日边界内 / 外（北京日）
            conn.execute(
                "UPDATE recite_word_progress SET mastered_at = ? WHERE word = 'mastered'",
                ("2026-10-17T16:00:00+00:00",),  # 北京 10-18，距 10-24 为 6 天，算近 7 日
            )
            conn.execute(
                "INSERT INTO recite_word_progress (user, word, level, right, wrong,"
                " mastered_at, last_seen, first_seen) VALUES (?,?,?,?,?,?,?,?)",
                ("malin", "oldmaster", 0, 1, 0,
                 "2026-10-16T16:00:00+00:00",  # 北京 10-17，距 10-24 为 7 天，不算
                 "2026-10-16T16:00:00+00:00", None),
            )
        s = _summary(c, h)
    assert s["today"] == "2026-10-24"
    assert "due24" in s["due_words"] and "over23" not in s["due_words"]
    assert s["overdue_words"] == ["over23"] or s["overdue_words"][0] == "over23"
    assert "over23" not in s["due_words"]
    assert s["due_today"] == 1 and s["overdue"] == 1
    assert s["overdue_words"] == ["over23"]
    names = {x["word"] for x in c.get("/recite/wrongbook", headers=h).json()["data"]["conquered"]}
    assert "mastered" in names and "oldmaster" in names
    assert s["conquered_n"]["total"] == 2
    assert s["conquered_n"]["d7"] == 1  # 只有 mastered 落在近 7 日


def test_conquer_pool_is_cycle_only_and_ordered(recite_env):
    """池 = 到期 ∪ 逾期 ∪ 在周期。不含新鲜词、不含已攻克。到期在逾期之前。"""
    c = _client(recite_env)
    h = _login(c, "malin", "123456")
    today = today_cn().isoformat()
    _submit(c, h, [
        {"word": "fresh", "correct": True},
        {"word": "duehi", "correct": False},
        {"word": "duelo", "correct": False},
        {"word": "older", "correct": False},
        {"word": "newerover", "correct": False},
        {"word": "future", "correct": False},
        {"word": "done", "correct": False},
    ])
    _set_next(recite_env, "duehi", today)
    _set_next(recite_env, "duelo", today)
    _set_next(recite_env, "older", (today_cn() - timedelta(days=5)).isoformat())
    _set_next(recite_env, "newerover", (today_cn() - timedelta(days=1)).isoformat())
    _set_next(recite_env, "future", (today_cn() + timedelta(days=3)).isoformat())
    con = sqlite3.connect(recite_env / "fce.db")
    con.execute(
        "UPDATE recite_review SET wrong_count = 9 WHERE word = 'duehi'"
    )
    con.execute(
        "UPDATE recite_review SET wrong_count = 1 WHERE word = 'duelo'"
    )
    con.execute(
        "UPDATE recite_review SET stage = 6, next_review = NULL, conquered_at = ?"
        " WHERE word = 'done'",
        (today,),
    )
    con.commit()
    con.close()

    s = _summary(c, h)
    pool = s["conquer_pool"]
    assert pool[:2] == ["duehi", "duelo"]  # 同日到期，错次多的在前
    assert pool[2:4] == ["older", "newerover"]  # 逾期更早的在前
    assert pool[4:] == ["future"]
    assert "fresh" not in pool and "done" not in pool
    assert "nosuch" not in pool
    assert s["in_cycle"] == len(pool) == 5


def test_conquer_mode_advances_existing_stages(recite_env):
    """mode=conquer 即复习会话：答对推进节点；普通 mode 不推进。不另起一套连对。"""
    c = _client(recite_env)
    h = _login(c, "malin", "123456")
    today = today_cn().isoformat()
    _submit(c, h, [
        {"word": "cycle", "correct": False},
        {"word": "stay", "correct": False},
    ])
    _set_next(recite_env, "cycle", today)
    _set_next(recite_env, "stay", today)

    # 普通会话答对：节点不动
    r = _submit(c, h, [{"word": "stay", "correct": True}], scope="all", mode="flip")
    assert r.status_code == 200
    # 攻克模式即使 scope 不是 wrongbook，也推进同一套 stage
    r = _submit(c, h, [{"word": "cycle", "correct": True}], scope="1", mode="conquer")
    assert r.status_code == 200
    assert r.json()["data"]["review"]["advanced"] == 1

    con = sqlite3.connect(recite_env / "fce.db")
    rows = dict(con.execute(
        "SELECT word, stage FROM recite_review WHERE user = 'malin'"
    ).fetchall())
    con.close()
    assert rows["cycle"] == 1
    assert rows["stay"] == 0

    s = _summary(c, h)
    assert "cycle" in s["conquer_pool"] and "freshbrand" not in s["conquer_pool"]
    reviewed = [a for a in s["activity"] if a["kind"] == "word" and a["word"] == "cycle"]
    assert reviewed and reviewed[0]["event"] == "reviewed"


def test_new_cap_when_overdue_and_stricter_when_pile_remains(recite_env):
    """逾期 > 0 时默认限额 20；逾期数不少于额度则收到 0。周期内的词不算新词。"""
    c = _client(recite_env)
    h = _login(c, "malin", "123456")

    # 没有逾期：不限额
    assert _summary(c, h)["new_cap"] is None
    batch = [{"word": f"free{i}", "correct": True} for i in range(25)]
    r = _submit(c, h, batch)
    assert r.status_code == 200
    assert r.json()["data"]["new_cap"] is None
    assert r.json()["data"]["new_used"] == 25

    # 清掉这 25 个的首见标记，避免干扰后面的额度（它们是逾期出现之前学的）
    con = sqlite3.connect(recite_env / "fce.db")
    con.execute("UPDATE recite_word_progress SET first_seen = NULL")
    con.commit()
    con.close()

    _submit(c, h, [{"word": "late", "correct": False}])
    _set_next(recite_env, "late", (today_cn() - timedelta(days=2)).isoformat())
    # late 是今天错进周期的，首见不该占「逾期出现之后」的新词额度
    con = sqlite3.connect(recite_env / "fce.db")
    con.execute("UPDATE recite_word_progress SET first_seen = NULL WHERE word = 'late'")
    con.commit()
    con.close()
    s = _summary(c, h)
    assert s["overdue"] == 1 and s["new_cap"] == NEW_WORD_CAP == 20
    assert s["new_used"] == 0

    first = [{"word": f"n{i:02d}", "correct": True} for i in range(20)]
    r = _submit(c, h, first)
    assert r.status_code == 200
    assert r.json()["data"]["new_used"] == 20
    assert _summary(c, h)["new_used"] == 20

    blocked = _submit(c, h, [{"word": "onetoomany", "correct": True}])
    assert blocked.status_code == 422
    assert "上限" in blocked.json()["message"]
    # 被拒的词没有落进度
    prog = c.get("/recite/progress?include_words=true", headers=h).json()["data"]
    assert "onetoomany" not in {w["word"] for w in prog["words"]}

    # 攻克逾期词：已在周期里，不占新词额度，cap=20 用满也能复习
    r = _submit(
        c, h, [{"word": "late", "correct": True}],
        scope="wrongbook", mode="conquer",
    )
    assert r.status_code == 200
    assert r.json()["data"]["new_used"] == 20

    # 逾期堆 ≥ 额度：新词额度收到 0。已在周期的词仍可攻克。
    _seed_cycle(
        recite_env, 20, (today_cn() - timedelta(days=3)).isoformat(),
        with_session=False, word_prefix="pile",
    )
    s = _summary(c, h)
    assert s["overdue"] >= 20 and s["new_cap"] == 0
    assert _submit(c, h, [{"word": "brand", "correct": True}]).status_code == 422
    ok = _submit(
        c, h, [{"word": "pile000", "correct": True}],
        scope="all", mode="conquer",
    )
    assert ok.status_code == 200
    # 今天的 20 个首见还在；攻克词没有把额度再撑破
    assert _summary(c, h)["new_used"] == 20


def test_new_cap_configurable_and_ignores_old_first_seen(recite_env, monkeypatch):
    """RECITE_NEW_WORD_CAP 覆盖默认值。昨天首见的词今天不再占额度。"""
    monkeypatch.setenv("RECITE_NEW_WORD_CAP", "5")
    c = _client(recite_env)
    h = _login(c, "malin", "123456")
    _submit(c, h, [{"word": "late", "correct": False}])
    _set_next(recite_env, "late", (today_cn() - timedelta(days=1)).isoformat())
    con = sqlite3.connect(recite_env / "fce.db")
    con.execute("UPDATE recite_word_progress SET first_seen = NULL WHERE word = 'late'")
    con.commit()
    con.close()
    # 昨天就见过的词（先记一笔再把首见拨回前天）
    _submit(c, h, [{"word": "yesterday", "correct": True}])
    yesterday = (datetime.now(_tz.utc) - timedelta(days=2)).isoformat(timespec="seconds")
    con = sqlite3.connect(recite_env / "fce.db")
    con.execute(
        "UPDATE recite_word_progress SET first_seen = ? WHERE word = 'yesterday'",
        (yesterday,),
    )
    con.commit()
    con.close()

    s = _summary(c, h)
    assert s["new_cap"] == 5 and s["new_used"] == 0
    # 再练昨天的词，不占额度
    again = _submit(c, h, [{"word": "yesterday", "correct": True}])
    assert again.status_code == 200 and again.json()["data"]["new_used"] == 0

    five = [{"word": f"c{i}", "correct": True} for i in range(5)]
    assert _submit(c, h, five).status_code == 200
    assert _summary(c, h)["new_used"] == 5
    assert _submit(c, h, [{"word": "c5", "correct": True}]).status_code == 422

    # 逾期数达到配置额度：收到 0
    _seed_cycle(
        recite_env, 5, (today_cn() - timedelta(days=4)).isoformat(),
        with_session=False, word_prefix="x",
    )
    assert _summary(c, h)["new_cap"] == 0


def test_alert_thresholds(recite_env):
    """在周期 ≥50、近 14 日没有错词周期攻克、且有背词会话 → alert。边界取不到。"""
    c = _client(recite_env)
    h = _login(c, "malin", "123456")
    today = today_cn()
    overdue_day = (today - timedelta(days=2)).isoformat()

    _seed_cycle(recite_env, ALERT_IN_CYCLE - 1, overdue_day, with_session=True)
    assert _summary(c, h)["alert"] is False
    assert _summary(c, h)["in_cycle"] == ALERT_IN_CYCLE - 1

    # 补到 50，但没有会话（把刚才那条会话删掉，再补 1 个周期词）
    con = sqlite3.connect(recite_env / "fce.db")
    con.execute("DELETE FROM recite_sessions WHERE user = 'malin'")
    con.execute(
        "INSERT INTO recite_review (user, word, level, wrong_count, stage,"
        " first_wrong_at, next_review, last_review_at)"
        " VALUES ('malin','extra',0,1,0,?,?,?)",
        (overdue_day, overdue_day, overdue_day),
    )
    con.commit()
    con.close()
    s = _summary(c, h)
    assert s["in_cycle"] == ALERT_IN_CYCLE and s["alert"] is False

    # 有会话、50 在周期、14 日内无攻克 → 告警
    con = sqlite3.connect(recite_env / "fce.db")
    con.execute(
        "INSERT INTO recite_sessions (user, total, wrong, acc, duration_sec,"
        " wrong_words, mode, scope, created_at) VALUES"
        " ('malin',1,0,100,1,'[]','flip','all',?)",
        (datetime.now(_tz.utc).isoformat(timespec="seconds"),),
    )
    con.commit()
    con.close()
    assert _summary(c, h)["alert"] is True

    # 13 天前攻克过（含今天共 14 个日历日里）→ 不告警
    inside = (today - timedelta(days=ALERT_QUIET_DAYS - 1)).isoformat()
    con = sqlite3.connect(recite_env / "fce.db")
    con.execute(
        "INSERT INTO recite_review (user, word, level, wrong_count, stage,"
        " first_wrong_at, next_review, last_review_at, conquered_at)"
        " VALUES ('malin','won',0,1,6,?,NULL,?,?)",
        (inside, inside, inside),
    )
    con.commit()
    con.close()
    s = _summary(c, h)
    assert s["in_cycle"] == ALERT_IN_CYCLE  # 攻克词不占在周期
    assert s["alert"] is False

    # 刚好满 14 天（窗口外）→ 再次告警。一遍掌握不算周期攻克。
    outside = (today - timedelta(days=ALERT_QUIET_DAYS)).isoformat()
    now = datetime.now(_tz.utc).isoformat(timespec="seconds")
    con = sqlite3.connect(recite_env / "fce.db")
    con.execute(
        "UPDATE recite_review SET conquered_at = ? WHERE word = 'won'", (outside,),
    )
    con.execute(
        "INSERT INTO recite_word_progress (user, word, level, right, wrong,"
        " mastered_at, last_seen) VALUES ('malin','blast',0,3,0,?,?)",
        (now, now),
    )
    con.commit()
    con.close()
    s = _summary(c, h)
    assert s["alert"] is True
    assert s["conquered_n"]["d7"] >= 1  # blast 今天掌握，学生卡近 7 日仍看得见

"""背单词云端逐词进度端点测试：明细上报 / 进度查询 / 合并上云 / 权限。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone as _tz

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.server import create_app  # noqa: E402


@pytest.fixture()
def recite_env(tmp_path, monkeypatch):
    """空 fce.db + 独立认证 / 成绩库（vocab_word 总量缺省时 levels.total 为 None）。"""
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    sqlite3.connect(tmp_path / "grammar.db").close()  # 合法空库
    return tmp_path


def _client(env) -> TestClient:
    return TestClient(create_app(str(env / "grammar.db"), str(env / "exam.db"),
                                 fce_db_path=str(env / "fce.db")))


def _login(c, user, password):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def test_details_upsert_and_progress(recite_env):
    c = _client(recite_env)
    h = _login(c, "malin", "123456")
    # 一组练习：1 新词对、1 新词错
    r = c.post("/recite/sessions", headers=h, json={
        "total": 2, "wrong": 1, "acc": 50, "duration_sec": 30,
        "wrong_words": ["bad"], "mode": "flip", "scope": "all",
        "details": [{"word": "good", "correct": True}, {"word": "bad", "correct": False}],
    })
    assert r.status_code == 200
    d = c.get("/recite/progress?include_words=true", headers=h).json()["data"]
    assert d["total_seen"] == 2 and d["mastered"] == 1
    words = {w["word"]: w for w in d["words"]}
    assert words["good"]["mastered"] is True
    assert words["bad"]["mastered"] is False
    # 补一次答对 → 翻正掌握
    c.post("/recite/sessions", headers=h, json={
        "total": 1, "wrong": 0, "acc": 100, "duration_sec": 5,
        "wrong_words": [], "mode": "flip", "scope": "wrongbook",
        "details": [{"word": "bad", "correct": True}],
    })
    d = c.get("/recite/progress", headers=h).json()["data"]
    assert d["mastered"] == 2
    # 学生看自己；无 user 权限提升
    assert d["user"] == "malin"


def test_progress_sync_idempotent(recite_env):
    c = _client(recite_env)
    h = _login(c, "malin", "123456")
    payload = {"progress": {"a": {"right": 3, "wrong": 0}, "b": {"right": 1, "wrong": 2}}}
    r1 = c.post("/recite/progress/sync", headers=h, json=payload)
    assert r1.status_code == 200
    # 重复上传同一快照：计数不翻倍（max 语义）
    c.post("/recite/progress/sync", headers=h, json=payload)
    d = c.get("/recite/progress?include_words=true", headers=h).json()["data"]
    words = {w["word"]: w for w in d["words"]}
    assert words["a"]["right"] == 3 and words["a"]["mastered"] is True
    assert words["b"]["right"] == 1 and words["b"]["wrong"] == 2
    # 与会话明细合并不冲突：云端 3 对 vs 本地 2 对 → max 取 3
    c.post("/recite/progress/sync", headers=h,
           json={"progress": {"a": {"right": 2, "wrong": 0}}})
    d = c.get("/recite/progress", headers=h).json()["data"]
    assert d["total_seen"] == 2


def test_progress_sync_bad_payload(recite_env):
    c = _client(recite_env)
    h = _login(c, "malin", "123456")
    assert c.post("/recite/progress/sync", headers=h, json={"progress": []}).status_code == 422
    assert c.post("/recite/progress/sync", headers=h, json={}).status_code == 422


def test_clear_progress_teacher_only(recite_env):
    c = _client(recite_env)
    hs = _login(c, "malin", "123456")
    ht = _login(c, "teacher", "123456")
    c.post("/recite/sessions", headers=hs, json={
        "total": 1, "wrong": 0, "acc": 100, "duration_sec": 1,
        "wrong_words": [], "mode": "flip", "scope": "all",
        "details": [{"word": "x", "correct": True}],
    })
    # 学生 403；教师不带 user 422
    assert c.delete("/recite/progress?user=malin", headers=hs).status_code == 403
    assert c.delete("/recite/progress", headers=ht).status_code == 422
    r = c.delete("/recite/progress?user=malin", headers=ht)
    assert r.status_code == 200 and r.json()["data"]["deleted"] == 1
    d = c.get("/recite/progress", headers=hs).json()["data"]
    assert d["total_seen"] == 0


def test_wrongbook_with_gloss(recite_env):
    """错词本：在周期词带错次/日程 + vocab_word 中文释义（大小写不敏感匹配）。"""
    import json

    con = sqlite3.connect(recite_env / "grammar.db")
    con.execute(
        "CREATE TABLE IF NOT EXISTS vocab_word (word TEXT PRIMARY KEY, level INTEGER NOT NULL,"
        " pos TEXT DEFAULT '[]', gloss TEXT DEFAULT '', meanings TEXT DEFAULT '[]',"
        " example TEXT DEFAULT '', extra TEXT DEFAULT '{}')"
    )
    con.execute(
        "INSERT INTO vocab_word (word, level, gloss, meanings) VALUES (?,?,?,?)",
        ("bad", 0, "坏的", json.dumps(["adj. 坏的", "n. 坏事"])),
    )
    con.commit()
    con.close()

    c = _client(recite_env)
    h = _login(c, "malin", "123456")
    c.post("/recite/sessions", headers=h, json={
        "total": 1, "wrong": 1, "acc": 0, "duration_sec": 5,
        "wrong_words": ["Bad"], "mode": "flip", "scope": "all",
        "details": [{"word": "Bad", "correct": False}],
    })
    data = c.get("/recite/wrongbook", headers=h).json()["data"]
    assert len(data["in_cycle"]) == 1
    w = data["in_cycle"][0]
    assert w["wrong_count"] == 1 and w["stage"] == 0
    # 新鲜路径：next_review = 答错日+1（明天），今天未到期
    assert w["due"] is False
    assert w["next_review"] == (datetime.now(_tz.utc).date() + timedelta(days=1)).isoformat()
    # 上报词大小写不一致（Bad vs bad）也要能匹配到释义
    assert w["gloss"] == "坏的"
    assert w["meanings"] == ["adj. 坏的", "n. 坏事"]
    assert data["conquered"] == []
    assert data["stats"]["total"] == 1 and data["stats"]["due"] == 0


def _submit(c, h, details, scope="all"):
    wrong = [d["word"] for d in details if not d["correct"]]
    return c.post("/recite/sessions", headers=h, json={
        "total": len(details), "wrong": len(wrong),
        "acc": round(100 * (len(details) - len(wrong)) / len(details)),
        "duration_sec": 5, "wrong_words": wrong, "mode": "flip",
        "scope": scope, "details": details,
    })


def _wb(c, h):
    return c.get("/recite/wrongbook", headers=h).json()["data"]


def test_ebbinghaus_cycle(recite_env):
    """艾宾浩斯周期：错→进周期；复习答对逐节点推进；六节点全过→攻克
    （词入已攻克表，带 conquered_at 来源标注）；再答错→回炉 stage 清零。
    普通会话答对不推进。"""
    from grammar_kb.recite import EB_STAGES

    c = _client(recite_env)
    h = _login(c, "malin", "123456")
    con = sqlite3.connect(recite_env / "fce.db")

    _submit(c, h, [{"word": "bad", "correct": False}])
    d = _wb(c, h)
    assert d["stats"]["total"] == 1 and d["stats"]["due"] == 0  # D+1 明天到期
    w = d["in_cycle"][0]
    assert w["stage"] == 0

    # 普通会话答对：不推进节点
    _submit(c, h, [{"word": "bad", "correct": True}], scope="all")
    assert _wb(c, h)["in_cycle"][0]["stage"] == 0

    # 复习会话答对六次 → 六节点全过 → 攻克
    for i in range(len(EB_STAGES)):
        # 到期判断：next_review 可能在未来（按今天算 D+1 起），测试直接
        # 把 next_review 拉到今天模拟到期，再提交复习
        con.execute(
            "UPDATE recite_review SET next_review = ? WHERE user='malin' AND word='bad'",
            (datetime.now(_tz.utc).date().isoformat(),),
        )
        con.commit()
        r = _submit(c, h, [{"word": "bad", "correct": True}], scope="wrongbook")
        assert r.status_code == 200
    d = _wb(c, h)
    assert d["stats"]["total"] == 0 and d["stats"]["conquered_n"] == 1
    cq = d["conquered"][0]
    assert cq["word"] == "bad" and cq["wrong_count"] == 1
    assert cq["conquered_at"]  # 从错题周期攻克的来源标注

    # 攻克后再答错：回炉（stage=0、wrong_count 累加、conquered_at 清空、
    # 重占复习位）；「已攻克表」口径=背完掌握的全集，不因回炉立刻掉出
    _submit(c, h, [{"word": "bad", "correct": False}])
    d = _wb(c, h)
    assert d["stats"]["total"] == 1
    w = d["in_cycle"][0]
    assert w["stage"] == 0 and w["wrong_count"] == 2
    assert d["stats"]["conquered_n"] == 1  # right(6) >= wrong(2) 仍掌握
    assert not d["conquered"][0]["conquered_at"]  # 但来源标注已清（回炉中）
    con.close()


def test_conquered_is_full_mastered_list(recite_env):
    """已攻克题本 = 全部背完掌握的词（含一遍背会、从未进错题本的）。"""
    c = _client(recite_env)
    h = _login(c, "malin", "123456")
    _submit(c, h, [{"word": "easy", "correct": True},   # 一遍背会
                   {"word": "hard", "correct": False}])  # 只进错题周期
    d = _wb(c, h)
    words = {x["word"]: x for x in d["conquered"]}
    assert "easy" in words  # 没进过错题本的掌握词也在已攻克表
    assert words["easy"]["wrong_count"] == 0 and not words["easy"]["conquered_at"]
    assert "hard" not in words
    assert d["stats"]["total"] == 1 and d["stats"]["conquered_n"] == 1


def test_review_backfill_from_history(recite_env):
    """存量兼容：历史会话错过但无 recite_review 行的词，读 wrongbook 时回填。"""
    from grammar_kb.recite import ReciteStore

    # 直接绕过新调度写一条历史会话（模拟旧版本数据）
    store = ReciteStore(str(recite_env / "fce.db"))
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO recite_sessions (user, total, wrong, acc, duration_sec,"
            " wrong_words, mode, scope, created_at) VALUES ('malin', 1, 1, 0, 5,"
            " '[\"oldword\"]', 'flip', 'all', ?)",
            ((datetime.now(_tz.utc) - timedelta(days=3)).isoformat(timespec="seconds"),),
        )
    c = _client(recite_env)
    h = _login(c, "malin", "123456")
    d = _wb(c, h)
    assert d["stats"]["total"] == 1
    w = d["in_cycle"][0]
    assert w["word"] == "oldword" and w["wrong_count"] == 1
    assert w["due"] is True  # 3 天前错的，D+1 早已过期

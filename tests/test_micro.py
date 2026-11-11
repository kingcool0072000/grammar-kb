"""微练习单测：建表契约 / 池同步与错题覆盖 / 抽题优先级 / 判分 / 端点权限。

三库隔离：fce.db（micro_* 表）、grammar.db（lecture + homework_question
题源）、exam.db（已学范围与错题）都落在 tmp_path；wrongbank.json 用
monkeypatch 替换 homework_admin.load_wrong_bank（微练习惰性导入，按模块
属性调用，替换即生效）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

import pytest

from grammar_kb import homework_admin
from grammar_kb.clock import today_cn
from grammar_kb.micro import DAILY_N, MicroStore

pytest.importorskip("fastapi")

TODAY = today_cn().isoformat()
YESTERDAY = (today_cn() - timedelta(days=1)).isoformat()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """三库临时环境 + 假 wrongbank。"""
    fce, gram, exam = tmp_path / "fce.db", tmp_path / "grammar.db", tmp_path / "exam.db"
    con = sqlite3.connect(gram)
    con.executescript("""
      CREATE TABLE lecture (
        id INTEGER PRIMARY KEY, number INTEGER UNIQUE NOT NULL, title TEXT NOT NULL,
        category TEXT, subcategory TEXT, source_file TEXT, page_count INTEGER,
        ingested_at TEXT);
      CREATE TABLE homework_question (
        id INTEGER PRIMARY KEY, lecture_number INTEGER NOT NULL, qnum INTEGER NOT NULL,
        section TEXT, stem TEXT, options_json TEXT DEFAULT '[]',
        is_cell INTEGER DEFAULT 0, source_file TEXT, ord INTEGER DEFAULT 0);
    """)
    for num, title, cat in [(2, "代词1", "词法"), (3, "代词2", "词法"),
                            (22, "动词时态1", "时态")]:
        con.execute("INSERT INTO lecture (number, title, category) VALUES (?,?,?)",
                    (num, title, cat))
    con.commit()
    con.close()
    ex = sqlite3.connect(exam)
    ex.executescript("""
      CREATE TABLE exam_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT, lecture INTEGER NOT NULL,
        date TEXT NOT NULL, score INTEGER NOT NULL DEFAULT 0,
        wrong TEXT NOT NULL DEFAULT '[]', updated_at TEXT,
        user TEXT NOT NULL DEFAULT 'malin', kind TEXT NOT NULL DEFAULT 'lecture');
    """)
    ex.commit()
    ex.close()
    # 讲次 → 题号 → {q 答案, exp 解析}；(2,2) 答案非 A-D 单字母 → 不入池
    bank = {
        "2": {"1": {"q": "B", "exp": "形容词性物主代词用 his"},
              "2": {"q": "his", "exp": "非 A-D，跳过"}},
        "3": {"1": {"q": "c", "exp": "小写字母也应接受"}},
        "22": {"1": {"q": "A", "exp": "现在完成时标志 has + 过去分词"}},
    }
    monkeypatch.setattr(homework_admin, "load_wrong_bank", lambda: bank)
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DB", str(tmp_path / "library.db"))
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DIR", str(tmp_path / "files"))
    # GRAMMAR_KB_DATA_DIR 现统一作用于全部默认路径（dbreg 整目录隔离）：
    # 目录须存在，且 GRAMMAR_KB_DB 显式指向测试库，别蹭仓库真库
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("GRAMMAR_KB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GRAMMAR_KB_DB", str(tmp_path / "data" / "grammar.db"))
    # CI 会把 GRAMMAR_KB_FCE_DB 指到一个全程共用的库，优先级高于 DATA_DIR。
    # patterns / boost 都走这条路径。不覆盖的话，前面用例写下的
    # memory:t1:n1 会按 item_ref 排在本次 retell 前面，历史断言对不上。
    monkeypatch.setenv("GRAMMAR_KB_FCE_DB", str(fce))
    return {"fce": str(fce), "grammar": str(gram), "exam": str(exam)}


def _store(env) -> MicroStore:
    return MicroStore(env["fce"], exam_db_path=env["exam"],
                      grammar_db_path=env["grammar"])


def _seed_exam(env, lecture, wrong, user="malin"):
    con = sqlite3.connect(env["exam"])
    con.execute(
        "INSERT INTO exam_records (lecture, date, score, wrong, user)"
        " VALUES (?,?,?,?,?)", (lecture, "2025-12-01", 80, json.dumps(wrong), user))
    con.commit()
    con.close()


def _seed_hw(env, lecture, qnum, stem="", options=None, is_cell=0):
    con = sqlite3.connect(env["grammar"])
    con.execute(
        "INSERT INTO homework_question (lecture_number, qnum, stem, options_json, is_cell)"
        " VALUES (?,?,?,?,?)",
        (lecture, qnum, stem or f"题{lecture}-{qnum}",
         json.dumps(options if options is not None else ["甲", "乙", "丙", "丁"]),
         is_cell))
    con.commit()
    con.close()


def _log_rows(env, user="malin"):
    con = sqlite3.connect(env["fce"])
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM micro_log WHERE user = ?", (user,)).fetchall()
    con.close()
    return [dict(r) for r in rows]
    con = sqlite3.connect(env["fce"])
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM micro_log WHERE user = ?", (user,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


# ---- 建表契约 ----

def test_schema_contract(env):
    _store(env)
    con = sqlite3.connect(env["fce"])
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    idx = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    cols = {r[1] for r in con.execute("PRAGMA table_info(micro_question)")}
    con.close()
    assert {"micro_question", "micro_daily", "micro_log"} <= names
    assert {"idx_micro_log_user_date", "idx_micro_log_qid",
            "idx_micro_question_status_cat"} <= idx
    assert {"id", "category", "topic", "stem", "options_json", "answer", "explain",
            "source", "ref_lecture", "ref_qnum", "status", "created_at"} <= cols


# ---- 池同步：入池 + 时态归类 + 错题覆盖 + 无答案跳过 ----

def test_sync_pool_and_wrong_override(env):
    _seed_exam(env, 2, [1])       # (2,1) 有 A-D 答案 → 入池后覆盖为 wrong
    _seed_exam(env, 3, [1])       # (3,1) 小写 c 也接受 → 入池并覆盖
    _seed_exam(env, 22, [])       # 22 讲 → 已学范围 2..22；22 讲是时态
    _seed_hw(env, 2, 1)
    _seed_hw(env, 2, 2)           # wrongbank 答案非 A-D → 不入池
    _seed_hw(env, 2, 3)           # wrongbank 无该题 → 不入池
    _seed_hw(env, 2, 4, is_cell=1)  # 表格题 → 不入池
    _seed_hw(env, 2, 5, options=["只有一项"])  # 选项 <2 → 不入池
    _seed_hw(env, 3, 1)
    _seed_hw(env, 22, 1)
    store = _store(env)
    store.today("malin", TODAY)  # 触发池同步
    rows = store.list_questions(status="published", limit=100)
    assert len(rows) == 3
    by_ref = {(r["ref_lecture"], r["ref_qnum"]): r for r in rows}
    assert by_ref[(2, 1)]["category"] == "wrong"   # 错题覆盖优先
    assert by_ref[(2, 1)]["answer"] == "B"
    assert by_ref[(3, 1)]["category"] == "wrong"   # 小写 c 照样判为答案
    assert by_ref[(3, 1)]["answer"] == "C"
    assert by_ref[(22, 1)]["category"] == "tense"  # 讲次 category 含「时态」
    assert by_ref[(22, 1)]["topic"] == "动词时态1"
    assert by_ref[(22, 1)]["source"] == "seed"
    assert by_ref[(22, 1)]["explain"].startswith("现在完成时")
    # 幂等：再同步一遍不重复入池
    store._sync_pool("malin")
    assert len(store.list_questions(limit=100)) == 3


def test_empty_pool_without_exam(env):
    """无成绩记录 → 空池，today 不落行、返回 0 题。"""
    store = _store(env)
    _seed_hw(env, 2, 1)
    out = store.today("malin", TODAY)
    assert out["questions"] == [] and out["answered"] == {} and out["done"] is False
    assert store.day_status("malin", TODAY) == {"done": False, "n": 0, "total": 0}


# ---- today 幂等 ----

def test_today_idempotent(env):
    _seed_exam(env, 2, [1])
    _seed_hw(env, 2, 1)
    _seed_hw(env, 3, 1)
    _seed_exam(env, 3, [])
    _seed_exam(env, 22, [])
    _seed_hw(env, 22, 1)
    store = _store(env)
    a = store.today("malin", TODAY)
    b = store.today("malin", TODAY)
    assert [q["id"] for q in a["questions"]] == [q["id"] for q in b["questions"]]
    assert 0 < len(a["questions"]) <= DAILY_N
    for q in a["questions"]:
        assert "answer" not in q and "explain" not in q  # options 不含答案
    assert a["date"] == TODAY
    # 已有行直接返回：次日再调今日仍同题（不重抽）
    store._sync_pool("malin")
    c = store.today("malin", TODAY)
    assert [q["id"] for q in c["questions"]] == [q["id"] for q in a["questions"]]


# ---- 抽题优先级 ----

def _add_q(store, category, answer="A", topic="T", status="published", stem=None,
           ref_lecture=None, ref_qnum=None):
    return store.add_question(
        category=category, stem=stem or f"{category}-{topic}-{answer}",
        options=["甲", "乙", "丙", "丁"], answer=answer, topic=topic,
        status=status, ref_lecture=ref_lecture, ref_qnum=ref_qnum)


def test_wrong_unturned_first_and_memory_guarantee(env):
    store = _store(env)
    wrong_q = _add_q(store, "wrong", topic="旧错")
    tense_q = _add_q(store, "tense", topic="时态卡")
    _add_q(store, "grammar", topic="语法A")
    _add_q(store, "grammar", topic="语法B")
    day1 = store.today("malin", TODAY)
    ids = [q["id"] for q in day1["questions"]]
    assert ids[0] == wrong_q                       # 槽1：错题未翻正
    assert tense_q in ids                          # 槽2：记忆库保底（从未考过）
    # 提交：错题答错、其余答对 → 次日错题仍未翻正，继续置顶
    answers = [{"id": q, "choice": "B" if q == wrong_q else "A"}
               for q in ids]
    r1 = store.submit("malin", TODAY, answers)
    assert r1["done"] is True and r1["correct_n"] == DAILY_N - 1
    next_day = (today_cn() + timedelta(days=1)).isoformat()
    day2 = store.today("malin", next_day)
    assert day2["questions"][0]["id"] == wrong_q   # 最近一次非 correct → 最优先


def test_recent_wrong_recycle(env):
    """当日错题回流（v3.0.0 闭环版）：近 3 天答错且此后未答对过的题并入
    优先级①，次日置顶回炉；此后答对过（翻正）则不再回炉。"""
    store = _store(env)
    # 4 题同 topic 语法题，保证翻正后优先级③也轮不到它（近答对降权）
    g = [_add_q(store, "grammar", topic=f"G{i}", answer=a)
         for i, a in enumerate("ABCD")]
    recycle_q, fillers = g[0], g[1:]
    con = sqlite3.connect(env["fce"])
    con.execute("INSERT INTO micro_log (user, qid, date, choice, is_correct)"
                " VALUES ('malin', ?, ?, 'B', 0)", (recycle_q, YESTERDAY))
    con.commit()
    con.close()
    picked = store._pick_daily("malin", TODAY)
    assert picked[0] == recycle_q        # 昨天答错 → 今天槽1回炉
    # 翻正（答对）后：不再算欠账，槽1让位，且降权轮不到它
    con = sqlite3.connect(env["fce"])
    con.execute("INSERT INTO micro_log (user, qid, date, choice, is_correct)"
                " VALUES ('malin', ?, ?, 'A', 1)", (recycle_q, TODAY))
    con.commit()
    con.close()
    picked2 = store._pick_daily("malin", TODAY)
    assert recycle_q not in picked2
    # 老错题（4 天前答错、非 wrong 类）不在回炉并集里：不置顶（槽1由
    # 记忆库保底或轮转填充，但它不能出现在 picked[0]）
    old_q = _add_q(store, "grammar", topic="OLD")
    con = sqlite3.connect(env["fce"])
    con.execute("INSERT INTO micro_log (user, qid, date, choice, is_correct)"
                " VALUES ('malin', ?, ?, 'C', 0)",
                (old_q, (today_cn() - timedelta(days=4)).isoformat()))
    con.commit()
    con.close()
    picked3 = store._pick_daily("malin", TODAY)
    assert picked3[0] != old_q
    assert fillers  # 满足 lint：填充题组存在（翻正后被挤出的是这批之前的名额）


def test_topic_rotation_and_recent_correct_downweight(env):
    store = _store(env)
    # 同一 topic 4 题语法（池 > 3 才能看出谁被挤出）
    t1 = [_add_q(store, "grammar", topic="T1", answer=a) for a in "ABCD"]
    never_q = _add_q(store, "grammar", topic="T2")   # 从未被考 → 最先
    recent_q = t1[3]
    con = sqlite3.connect(env["fce"])
    con.execute("INSERT INTO micro_log (user, qid, date, choice, is_correct)"
                " VALUES ('malin', ?, ?, 'A', 1)", (recent_q, YESTERDAY))
    con.commit()
    con.close()
    out = store.today("malin", TODAY)
    picked = [q["id"] for q in out["questions"]]
    assert never_q in picked                        # topic NULL 最先
    assert recent_q not in picked                   # 近 3 天答对 → 降权被挤出


def test_focus_tilt(env):
    """本周 focus_kps 命中的讲次题在覆盖轮转里加权优先。"""
    store = _store(env)
    con = sqlite3.connect(env["fce"])
    con.execute("CREATE TABLE IF NOT EXISTS plan_weeks (user TEXT, week_start TEXT,"
                " tasks TEXT DEFAULT '{}', notes TEXT, updated_at TEXT,"
                " PRIMARY KEY (user, week_start))")
    con.execute("INSERT INTO plan_weeks (user, week_start, tasks) VALUES (?, ?, ?)",
                ("malin", TODAY, json.dumps({"focus_kps": [7]})))
    con.commit()
    con.close()
    focus_q = _add_q(store, "grammar", topic="F", stem="focus讲次题",
                     ref_lecture=7, ref_qnum=1)
    for i in range(4):
        _add_q(store, "grammar", topic=f"G{i}", stem=f"普通题{i}")
    out = store.today("malin", TODAY)
    picked = [q["id"] for q in out["questions"]]
    assert focus_q in picked  # 同为从未考过时，focus 题排最前


def test_draft_not_picked(env):
    store = _store(env)
    pub = _add_q(store, "grammar")
    _add_q(store, "grammar", stem="草稿题", status="draft")
    out = store.today("malin", TODAY)
    ids = [q["id"] for q in out["questions"]]
    assert pub in ids and len(ids) == 1
    con = sqlite3.connect(env["fce"])
    n = con.execute("SELECT COUNT(*) FROM micro_daily WHERE user='malin'").fetchone()[0]
    con.close()
    assert n == 1


# ---- 判分 / done / day_status ----

def test_grading_normalization_and_no_double_log(env):
    store = _store(env)
    q1 = _add_q(store, "grammar", answer="B", stem="第一题")
    q2 = _add_q(store, "grammar", answer="D", stem="第二题")
    out = store.today("malin", TODAY)
    assert sorted(q["id"] for q in out["questions"]) == sorted([q1, q2])
    r = store.submit("malin", TODAY, [
        {"id": q1, "choice": "  b. "},   # 归一化 → B，对
        {"id": q2, "choice": "c"},       # 错
    ])
    assert r["correct_n"] == 1 and r["done"] is True
    res = {x["id"]: x for x in r["results"]}
    assert res[q1]["correct"] is True and res[q1]["answer"] == "B"
    assert res[q2]["correct"] is False and res[q2]["answer"] == "D"
    assert res[q2]["explain"] == "" and res[q2]["category"] == "grammar"
    # 首次提交：每题一条 log
    assert len(_log_rows(env)) == 2
    # 重复提交：改答案只更新 answered_json，不重复落 log
    # （correct_n 口径 = 本次提交 results 里的答对数，仅含本次交的题）
    r2 = store.submit("malin", TODAY, [{"id": q2, "choice": "d"}])
    assert r2["correct_n"] == 1 and r2["results"][0]["correct"] is True
    assert len(_log_rows(env)) == 2
    st = store.day_status("malin", TODAY)
    assert st == {"done": True, "n": 2, "total": 2}


def test_day_status_tolerates_missing_table(env):
    """plan 层用裸 SQL 读 micro_daily：这里验证表被删后 day_status 不炸。"""
    store = _store(env)
    assert store.day_status("malin", TODAY) == {"done": False, "n": 0, "total": 0}
    con = sqlite3.connect(env["fce"])
    con.execute("DROP TABLE micro_daily")
    con.commit()
    con.close()
    assert store.day_status("malin", TODAY) == {"done": False, "n": 0, "total": 0}


# ---- 题库管理：upsert 幂等 / 更新白名单 ----

def test_upsert_idempotent(env):
    store = _store(env)
    row = {"category": "phrasal", "topic": "短语", "stem": "look after",
           "options": ["照顾", "寻找"], "answer": "a", "explain": "固定搭配",
           "source": "ai", "status": "published"}
    assert store.upsert_questions([row, dict(row),          # 批内同题去重
                                   {"category": "grammar", "stem": " ",   # 坏行跳过
                                    "options": [], "answer": "Z"}]) == (1, 2)
    # 再跑一遍全跳过
    assert store.upsert_questions([row]) == (0, 1)
    rows = store.list_questions()
    assert len(rows) == 1 and rows[0]["answer"] == "A" and rows[0]["source"] == "ai"
    # ref 为 NULL 时按 stem 全等判重：同 stem 再来仍跳过；不同 stem 才插入
    assert store.upsert_questions([dict(row, answer="b")]) == (0, 1)
    assert store.upsert_questions([dict(row, stem="look forward")]) == (1, 0)


def test_update_and_delete_question(env):
    store = _store(env)
    qid = _add_q(store, "grammar")
    store.update_question(qid, {"topic": "改标题", "answer": "d", "status": "published",
                                "options": ["一", "二"]})
    row = store.list_questions()[0]
    assert (row["topic"], row["answer"], row["status"], row["options"]) == \
        ("改标题", "D", "published", ["一", "二"])
    with pytest.raises(ValueError):
        store.update_question(qid, {"answer": "E"})
    with pytest.raises(ValueError):
        store.update_question(qid, {"created_at": "hack"})
    store.delete_question(qid)
    assert store.list_questions() == []
    # 作答流水保留（历史统计口径）
    assert store.stats()["log"] == {}


def test_stats(env):
    store = _store(env)
    pub = _add_q(store, "grammar")
    _add_q(store, "phrasal", status="draft")
    store.today("malin", TODAY)
    store.submit("malin", TODAY, [{"id": pub, "choice": "A"}])
    st = store.stats()
    assert st["by_category"]["grammar"]["published"] == 1
    assert st["by_category"]["phrasal"]["draft"] == 1
    assert st["log"]["grammar"]["asked"] == 1
    assert st["log"]["grammar"]["correct"] == 1


# ---- HTTP 端点与学生白名单 ----

@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient

    from grammar_kb.server import create_app
    return TestClient(create_app(env["grammar"], env["exam"],
                                 fce_db_path=env["fce"]))


def _login(c, user, password="123456"):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def test_micro_endpoints_and_role_gate(env, client):
    store = _store(env)
    qid = _add_q(store, "grammar", stem="端点题")
    hs = _login(client, "malin")    # 学生
    ht = _login(client, "teacher")  # 教师

    # 学生：/micro/today 200 且题目不含答案
    r = client.get("/micro/today", headers=hs)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["date"] == TODAY and len(data["questions"]) == 1
    assert data["questions"][0]["id"] == qid
    assert "answer" not in data["questions"][0]
    # 学生：历史日期、坏日期
    assert client.get("/micro/today", headers=hs,
                      params={"day": "2026-01-02"}).status_code == 200
    assert client.get("/micro/today", headers=hs,
                      params={"day": "bad-date"}).status_code == 422
    # 学生：提交判分（choice 归一化小写也算对）
    r = client.post("/micro/submit", headers=hs,
                    json={"answers": [{"id": qid, "choice": "a"}]})
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["done"] is True and body["correct_n"] == 1
    assert body["results"][0]["answer"] == "A"
    # 学生：micro-admin 一律 403
    for method, path, kw in [
        ("get", "/micro-admin/questions", {}),
        ("post", "/micro-admin/questions",
         {"json": {"stem": "x", "options": [], "answer": "A"}}),
        ("put", f"/micro-admin/questions/{qid}", {"json": {"status": "draft"}}),
        ("delete", f"/micro-admin/questions/{qid}", {}),
        ("get", "/micro-admin/stats", {}),
    ]:
        assert getattr(client, method)(path, headers=hs, **kw).status_code == 403

    # 教师：题库 CRUD + 统计
    assert client.get("/micro-admin/questions", headers=ht,
                      params={"category": "grammar"}).status_code == 200
    r = client.post("/micro-admin/questions", headers=ht,
                    json={"category": "idiom", "stem": "rain cats and dogs",
                          "options": ["倾盆大雨", "狗猫雨"], "answer": "A",
                          "topic": "习语", "explain": "固定习语"})
    assert r.status_code == 200
    new_id = r.json()["data"]["id"]
    assert client.put(f"/micro-admin/questions/{new_id}", headers=ht,
                      json={"status": "published"}).status_code == 200
    assert any(x["id"] == new_id and x["answer"] == "A"
               for x in client.get("/micro-admin/questions", headers=ht)
               .json()["data"])
    stats = client.get("/micro-admin/stats", headers=ht).json()["data"]
    assert stats["by_category"]["idiom"]["published"] == 1
    assert client.delete(f"/micro-admin/questions/{new_id}",
                         headers=ht).status_code == 200
    assert client.get("/micro-admin/questions", headers=ht).status_code == 200
    # 校验：坏 body 422
    assert client.post("/micro-admin/questions", headers=ht,
                       json={"stem": "", "answer": "A"}).status_code == 422
    assert client.post("/micro/submit", headers=hs,
                       json={"answers": []}).status_code == 422


def test_micro_history_endpoint(env, client):
    """/micro/history：按天倒序含答案/我的作答/对错；学生互相隔离。"""
    store = _store(env)
    q1 = _add_q(store, "grammar", stem="历史题1", answer="A")
    q2 = _add_q(store, "idiom", stem="历史题2", answer="B")
    hs = _login(client, "malin")
    hs2 = _login(client, "mxy", "mxy123")
    # malin 答题：q1 对、q2 错
    client.get("/micro/today", headers=hs)
    r = client.post("/micro/submit", headers=hs, json={
        "answers": [{"id": q1, "choice": "A"}, {"id": q2, "choice": "A"}]})
    assert r.status_code == 200
    h = client.get("/micro/history", headers=hs).json()["data"]
    assert len(h) == 1 and h[0]["date"] == TODAY and h[0]["done"]
    assert h[0]["correct_n"] == 1 and h[0]["total"] == 2
    by_id = {q["id"]: q for q in h[0]["questions"]}
    assert by_id[q1]["my_choice"] == "A" and by_id[q1]["my_correct"]
    assert by_id[q1]["answer"] == "A"  # 历史回看带答案与解析
    assert not by_id[q2]["my_correct"] and by_id[q2]["answer"] == "B"
    assert "explain" in by_id[q1]  # 解析字段在（值可空，seed 题没写）
    # mxy 没做题：空
    assert client.get("/micro/history", headers=hs2).json()["data"] == []


def test_boost_history_endpoint(env, client):
    """/boost/history：boost_log 流水 + retell 批改字段；user 参数收窄防冒充。"""
    from grammar_kb.plan import boost
    boost.record("malin", TODAY, "memory:t1:n1", "memory")
    hs = _login(client, "malin")
    h = client.get("/boost/history?user=malin", headers=hs).json()["data"]
    assert h["user"] == "malin" and len(h["items"]) == 1
    it = h["items"][0]
    assert it["kind"] == "memory" and it["passed"] and it["date"] == TODAY
    # 学生 A 传 user=B → 收窄回自己（看不到 B 的）
    h2 = client.get("/boost/history?user=mxy", headers=hs).json()["data"]
    assert h2["user"] == "malin" and len(h2["items"]) == 1


def test_micro_admin_progress(env, client):
    """/micro-admin/progress：全体汇总（每生）+ 单生逐天明细；学生 403。"""
    store = _store(env)
    q1 = _add_q(store, "grammar", stem="进度题1", answer="A")
    q2 = _add_q(store, "grammar", stem="进度题2", answer="B")
    ht = _login(client, "teacher")
    hs = _login(client, "malin")
    client.get("/micro/today", headers=hs)
    client.post("/micro/submit", headers=hs, json={
        "answers": [{"id": q1, "choice": "A"}, {"id": q2, "choice": "A"}]})
    # 学生 403（教师专属）
    assert client.get("/micro-admin/progress", headers=hs).status_code == 403
    # 全体汇总：malin 一行
    agg = client.get("/micro-admin/progress", headers=ht).json()["data"]["users"]
    row = next(u for u in agg if u["user"] == "malin")
    assert row["days_done"] == 1 and row["answered_n"] == 2
    assert row["correct_n"] == 1 and row["last_day"] == TODAY
    # 单生明细：一天、含逐题对错
    det = client.get("/micro-admin/progress?user=malin", headers=ht).json()["data"]
    assert det["user"] == "malin" and det["days_done"] == 1
    day = det["days"][0]
    assert day["date"] == TODAY and day["correct_n"] == 1 and day["total"] == 2
    by_id = {q["id"]: q for q in day["questions"]}
    assert by_id[q1]["my_correct"] and not by_id[q2]["my_correct"]


def test_patterns_update_and_delete(env, client):
    """句型编辑（白名单字段生效）/删除（list 无、boost 历史流水保留）。"""
    from grammar_kb import patterns
    from grammar_kb.plan import boost
    ht = _login(client, "teacher")
    patterns.import_items([{
        "lecture": 31, "sentence": "English is spoken here.",
        "translation": "", "hints": []}])
    pid = patterns.list_all(31)[0]["id"]
    boost.record("malin", TODAY, f"retell:{pid}", "retell", score=8)
    # 编辑：改 sentence + translation + hints
    r = client.put(f"/patterns-admin/{pid}", headers=ht, json={
        "sentence": "English is spoken everywhere.",
        "translation": "到处都说英语。", "hints": ["被动语态？"]})
    assert r.status_code == 200, r.text
    after = patterns.get(pid)
    assert after["sentence"] == "English is spoken everywhere."
    assert after["translation"] == "到处都说英语。" and after["hints"] == ["被动语态？"]
    # 空 sentence 422；不存在的 id 404；学生 403
    assert client.put(f"/patterns-admin/{pid}", headers=ht,
                      json={"sentence": "  "}).status_code == 422
    assert client.put("/patterns-admin/99999", headers=ht,
                      json={"sentence": "x"}).status_code == 404
    hs = _login(client, "malin")
    assert client.put(f"/patterns-admin/{pid}", headers=hs,
                      json={"sentence": "x"}).status_code == 403
    # 删除：素材没了，boost 历史流水仍在
    assert client.delete(f"/patterns-admin/{pid}", headers=ht).status_code == 200
    assert patterns.get(pid) is None
    assert not patterns.list_all(31)
    assert client.delete(f"/patterns-admin/{pid}", headers=ht).status_code == 404
    from grammar_kb.plan import boost as _b
    # 流水按日期倒序、item_ref 正序，不保证刚写入的 retell 排在第一条。
    rows = _b.history("malin", 10)
    assert any(r["item_ref"] == f"retell:{pid}" for r in rows)

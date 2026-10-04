"""计划表端点测试：周计划读写 / 实时完成度聚合 / 上周回顾 / 权限。"""
from __future__ import annotations

import io
import sqlite3
import zipfile
from datetime import date, timedelta

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.server import create_app  # noqa: E402

MON = (date.today() - timedelta(days=date.today().weekday())).isoformat()
PREV = (date.today() - timedelta(days=date.today().weekday() + 7)).isoformat()


@pytest.fixture()
def plan_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    # 阅读实况聚合会开 library.db（环境变量优先）——指向测试库防读生产数据
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DB", str(tmp_path / "library.db"))
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DIR", str(tmp_path / "files"))
    sqlite3.connect(tmp_path / "grammar.db").close()
    return tmp_path


def _client(env) -> TestClient:
    return TestClient(create_app(str(env / "grammar.db"), str(env / "exam.db"),
                                 fce_db_path=str(env / "fce.db")))


def _login(c, user, password):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def test_plan_put_get_roundtrip(plan_env):
    c = _client(plan_env)
    ht = _login(c, "teacher", "123456")
    tasks = {"lectures": [29, 30], "vocab_goal": 380,
             "fce": ["Test1 RUE"], "reading": {"波西杰克逊1": 100}}
    r = c.put(f"/plan/weeks/{MON}", headers=ht, json={"tasks": tasks, "notes": "加油"})
    assert r.status_code == 200
    d = c.get("/plan/weeks?user=malin", headers=ht).json()["data"]
    assert d["current_week"] == MON
    w = next(x for x in d["weeks"] if x["week_start"] == MON)
    assert w["tasks"] == tasks and w["notes"] == "加油"
    assert "actuals" in w
    # 幂等覆盖
    c.put(f"/plan/weeks/{MON}", headers=ht, json={"tasks": {"lectures": [31]}, "notes": ""})
    d = c.get("/plan/weeks", headers=ht).json()["data"]
    w = next(x for x in d["weeks"] if x["week_start"] == MON)
    assert w["tasks"] == {"lectures": [31]}


def test_plan_validates_monday(plan_env):
    c = _client(plan_env)
    ht = _login(c, "teacher", "123456")
    today = date.today().isoformat()
    r = c.put(f"/plan/weeks/{today}", headers=ht, json={"tasks": {}, "notes": ""})
    if today == MON:
        pytest.skip("今天恰好是周一")
    assert r.status_code == 422
    assert c.put("/plan/weeks/not-a-date", headers=ht,
                 json={"tasks": {}, "notes": ""}).status_code == 422
    assert c.put(f"/plan/weeks/{MON}", headers=ht,
                 json={"notes": "x"}).status_code == 422  # 缺 tasks


def test_plan_teacher_only(plan_env):
    c = _client(plan_env)
    hs = _login(c, "malin", "123456")
    assert c.get("/plan/weeks", headers=hs).status_code == 403
    assert c.put(f"/plan/weeks/{MON}", headers=hs,
                 json={"tasks": {}}).status_code == 403


def test_plan_actuals_aggregate(plan_env):
    """完成度：周内成绩讲次 + 周内新掌握词（mastered_at 窗口过滤）。"""
    c = _client(plan_env)
    hs = _login(c, "malin", "123456")
    ht = _login(c, "teacher", "123456")
    # 本周一条成绩（今天必在本周窗口）
    c.post("/exams", headers=hs, json={"lecture": 29, "date": date.today().isoformat(),
                                       "score": 82, "wrong": []})
    # 本周新掌握一个词
    c.post("/recite/sessions", headers=hs, json={
        "total": 1, "wrong": 0, "acc": 100, "duration_sec": 5,
        "wrong_words": [], "mode": "flip", "scope": "all",
        "details": [{"word": "alpha", "correct": True}],
    })
    c.put(f"/plan/weeks/{MON}", headers=ht, json={"tasks": {"lectures": [29]}})
    d = c.get("/plan/weeks?user=malin", headers=ht).json()["data"]
    w = next(x for x in d["weeks"] if x["week_start"] == MON)
    assert w["actuals"]["lectures"] and w["actuals"]["lectures"][0]["lecture"] == 29
    assert w["actuals"]["vocab_mastered"] == 1
    # 上周回顾：无上周数据时摘要为空态，不报错
    rv = d["last_week_review"]
    assert rv["week_start"] == PREV
    assert rv["summary"]["vocab_mastered"] == 0


def test_plan_reading_actuals_full_book(plan_env, monkeypatch):
    """阅读实况：全书口径要素（总词数/落点章区间），供「第X章xx%~xx%」文案。"""
    import zipfile, io

    # 独立 library.db：一本书两章（各 100 词），malin 读到 30%
    lib_path = plan_env / "library.db"
    from grammar_kb.library import LibraryStore
    lib = LibraryStore(str(lib_path), data_dir=str(plan_env / "files"))
    book_id = lib.add_book("teacher", "wonder.epub", "application/epub+zip",
                           _mini_epub_bytes("Wonder"))["id"]
    lib.put_progress(book_id, "malin", "epubcfi(/6/2)", 1, 30.0, 900)
    con = sqlite3.connect(lib_path)
    con.execute(
        "UPDATE reading_progress SET updated_at=? WHERE book_id=? AND user='malin'",
        (date.today().isoformat() + " 12:00:00", book_id))
    con.commit()
    con.close()

    c = _client(plan_env)
    ht = _login(c, "teacher", "123456")
    c.put(f"/plan/weeks/{MON}", headers=ht, json={"tasks": {"reading": {"Wonder": 100}}})
    d = c.get("/plan/weeks?user=malin", headers=ht).json()["data"]
    w = next(x for x in d["weeks"] if x["week_start"] == MON)
    rd = w["actuals"]["reading"]
    assert len(rd) == 1
    r = rd[0]
    assert r["title"] == "Wonder" and r["percent"] == 30.0
    assert r["reading_seconds"] == 900
    assert r["total_words"] > 0 and r["chapters_count"] == 2
    ch = r["current_chapter"]
    assert ch and 0 <= ch["start_percent"] <= 30 <= ch["end_percent"]


def _mini_epub_bytes(title: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0"'
                   ' xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="content.opf"'
                   ' media-type="application/oebps-package+xml"/></rootfiles></container>')
        items = "".join(f'<item id="c{i}" href="c{i}.xhtml"'
                        ' media-type="application/xhtml+xml"/>' for i in range(2))
        refs = "".join(f'<itemref idref="c{i}"/>' for i in range(2))
        z.writestr("content.opf",
                   '<?xml version="1.0" encoding="utf-8"?>'
                   '<package xmlns="http://www.idpf.org/2007/opf" version="3.0"'
                   ' unique-identifier="id"><metadata'
                   ' xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   f'<dc:identifier id="id">urn:uuid:{title}</dc:identifier>'
                   f'<dc:title>{title}</dc:title><dc:language>en</dc:language>'
                   '</metadata><manifest>' + items + '</manifest><spine>' + refs +
                   '</spine></package>')
        for i in range(2):
            z.writestr(f"c{i}.xhtml",
                       '<?xml version="1.0" encoding="utf-8"?>'
                       '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>'
                       f"Chapter {i + 1}</title></head><body><h1>Chapter {i + 1}</h1>"
                       "<p>" + "word " * 100 + "</p></body></html>")
    return buf.getvalue()


def _seed_watermark_bookcase(env):
    """水位去重测试脚手架：library.db 造 1 本书 10 章（每章 1000 词），
    fce.db 造带轨迹的泛读会话。返回 fce.db 路径。"""
    lib = sqlite3.connect(env / "library.db")
    lib.execute("CREATE TABLE IF NOT EXISTS books (id INTEGER PRIMARY KEY,"
                " title TEXT, author TEXT, file_path TEXT, created_at TEXT)")
    lib.execute("CREATE TABLE IF NOT EXISTS chapters (book_id INTEGER, idx INTEGER,"
                " title TEXT, word_count INTEGER, spine_index INTEGER)")
    lib.execute("CREATE TABLE IF NOT EXISTS book_reading_config (book_id INTEGER,"
                " user TEXT, chapters TEXT, vocabUnlock TEXT)")
    lib.execute("CREATE TABLE IF NOT EXISTS reading_progress (book_id INTEGER,"
                " user TEXT, cfi TEXT, chapter_index INTEGER, percent REAL,"
                " reading_seconds INTEGER, updated_at TEXT)")
    lib.execute("INSERT INTO books (id, title) VALUES (1, 'TestBook')")
    for i in range(10):
        lib.execute("INSERT INTO chapters VALUES (1, ?, ?, 1000, ?)",
                    (i, f"Ch{i}", i))
    lib.commit()
    lib.close()

    fce = sqlite3.connect(env / "fce.db")
    fce.execute("CREATE TABLE IF NOT EXISTS focus_sessions ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, user TEXT,"
                " book_id INTEGER, book_title TEXT, started_at TEXT, ended_at TEXT,"
                " total_sec INTEGER, active_sec INTEGER, away_count INTEGER,"
                " away_sec INTEGER, longest_away_sec INTEGER, scroll_count INTEGER,"
                " chapter_navs INTEGER, lookups INTEGER, plays INTEGER,"
                " prep_opens INTEGER, prep_starts INTEGER, percent_start REAL,"
                " percent_end REAL, fast_scroll_flags INTEGER, mouse_metrics TEXT,"
                " polyline TEXT, score REAL, score_detail TEXT, created_at TEXT,"
                " lookup_words TEXT, speak_words TEXT, selected_words TEXT,"
                " activity_json TEXT, module TEXT, range_start REAL, range_end REAL,"
                " range_label TEXT, percent_track TEXT)")
    return fce


def _add_session(fce, sid, started, track, active_sec):
    """track=[(t秒,pct)]，起止取轨迹首尾；started 用可排序的 ISO 字符串。"""
    fce.execute(
        "INSERT INTO focus_sessions (id, session_id, user, book_id, book_title,"
        " started_at, ended_at, total_sec, active_sec, away_count, away_sec,"
        " longest_away_sec, scroll_count, chapter_navs, lookups, plays,"
        " percent_start, percent_end, module, percent_track, created_at)"
        " VALUES (?, ?, 'malin', 1, 'TestBook', ?, ?, ?, ?, 0, 0, 0, 10, 0,"
        " 5, 2, ?, ?, 'library', ?, ?)",
        (sid, f"s{sid}", started, started, active_sec + 10, active_sec,
         track[0][1], track[-1][1],
         __import__("json").dumps([{"t": t, "p": p} for t, p in track]), started))


def test_focus_words_watermark_dedup(plan_env):
    """水位去重：第二次读重叠区 → words 只计水位以上新学，水位以下=overlap。"""
    from grammar_kb.plan import PlanStore
    _seed_watermark_bookcase(plan_env)
    fce = sqlite3.connect(plan_env / "fce.db")
    # 会话1：10s 内 5%→10%（慢速有效推进，5%≈500 词），水位推到 ~1000 词位
    _add_session(fce, 1, "2026-10-05T10:00:00Z",
                 [(2, 5), (6, 6), (10, 7), (200, 10)], 300)
    # 会话2：从 8% 顺延读到 15%——8-10% 重合（~200 词），10-15% 新学（~500 词）
    _add_session(fce, 2, "2026-10-06T10:00:00Z",
                 [(2, 8), (100, 9), (300, 12), (500, 15)], 500)
    fce.commit()
    fce.close()

    p = PlanStore(str(plan_env / "fce.db"),
                  exam_db_path=str(plan_env / "exam.db"))
    wm = p.focus_words([1, 2])
    assert wm[1]["words"] > 0 and wm[1]["overlap"] == 0  # 首读全量
    assert wm[2]["overlap"] > 0 and wm[2]["words"] > 0   # 部分重合部分新学
    assert wm[2]["link"] == "next"                        # 8% 起点≈上会话 10% 结尾
    # 重合+新学 ≈ 本次区段词数（10000 词/10%≈1000 词/1%，7% 段≈700 词）
    total2 = wm[2]["words"] + wm[2]["overlap"]
    assert 500 < total2 < 900


def test_my_week_student_only(plan_env):
    """学生课程表端点：学生只读自己本周；教师 403（教师走 /plan/week-view）。"""
    c = _client(plan_env)
    s = _login(c, "malin", "123456")
    r = c.get("/plan/my-week", headers=s)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["week_start"]  # 本周周一
    assert "days" in d and len(d["days"]) == 7
    assert "goal_progress" in d
    # 教师 403
    t = _login(c, "teacher", "123456")
    assert c.get("/plan/my-week", headers=t).status_code == 403


def test_hw_paper_dimension_split(plan_env):
    """课程/作业卷拆分：hw_papers 独立维度；kind=hw 成绩只点亮作业卷，
    kind=lecture 成绩只点亮语法课程；两维度互不串。"""
    c = _client(plan_env)
    hs = _login(c, "malin", "123456")
    ht = _login(c, "teacher", "123456")
    today = date.today().isoformat()
    # 一份作业卷成绩（第 26 讲）+ 一份课程测验成绩（第 25 讲）
    c.post("/exams", headers=hs, json={"lecture": 26, "date": today,
                                       "score": 88, "wrong": [], "kind": "hw"})
    c.post("/exams", headers=hs, json={"lecture": 25, "date": today,
                                       "score": 65, "wrong": [], "kind": "lecture"})
    tasks = {"lectures": [25, 26], "hw_papers": [26, 27]}
    c.put(f"/plan/weeks/{MON}", headers=ht, json={"tasks": tasks})
    d = c.get(f"/plan/week-view?week_start={MON}&user=malin",
              headers=ht).json()["data"]
    dims = {x["name"]: x for x in d["goal_progress"]["dims"]}
    assert dims["语法课程"]["done"] == 1 and dims["语法课程"]["total"] == 2
    assert dims["作业卷"]["done"] == 1 and dims["作业卷"]["total"] == 2
    # 完成态判定也按 kind 分流：26 讲只算卷考过、不算课程考过
    ed = c.get("/plan/editor-data?user=malin", headers=ht).json()["data"]
    assert 26 in ed["done"]["hw_papers"] and 26 not in ed["done"]["lectures"]
    assert 25 in ed["done"]["lectures"] and 25 not in ed["done"]["hw_papers"]
    # 学生任务派生：作业卷任务行出现（逐卷行，key=hw:{讲次}）
    td = c.get("/plan/today", headers=hs).json()["data"]
    hw_tasks = [t for t in td["tasks"] if t["type"] == "hw_paper"]
    assert len(hw_tasks) == 2
    # 作业卷任务行已点亮（kind=hw 成绩在今天）；未考的卷不点亮
    by_key = {t["key"]: t for t in hw_tasks}
    assert by_key["hw:26"]["done"] is True
    assert by_key["hw:27"]["done"] is False
    # my-week（week_view 同源）：tasks 透传 hw_papers + 周拆解含作业卷
    mw = c.get("/plan/my-week", headers=hs).json()["data"]
    assert mw["tasks"]["hw_papers"] == [26, 27]
    assert mw["breakdown"]["hw_paper_count"] == 2


def test_exam_kind_field_roundtrip(plan_env):
    """成绩 kind 字段：默认 lecture（兼容旧客户端）；hw 透传；非法值归一 lecture。"""
    c = _client(plan_env)
    hs = _login(c, "malin", "123456")
    r1 = c.post("/exams", headers=hs, json={"lecture": 1, "date": "2026-10-05",
                                            "score": 90, "wrong": []})
    assert r1.json()["data"]["kind"] == "lecture"
    r2 = c.post("/exams", headers=hs, json={"lecture": 2, "date": "2026-10-05",
                                            "score": 80, "wrong": [], "kind": "hw"})
    assert r2.json()["data"]["kind"] == "hw"
    r3 = c.post("/exams", headers=hs, json={"lecture": 3, "date": "2026-10-05",
                                            "score": 70, "wrong": [], "kind": "bogus"})
    assert r3.json()["data"]["kind"] == "lecture"

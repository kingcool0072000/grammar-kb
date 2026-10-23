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
    # 本周一条成绩。日期用周一：week_bounds 下界是「周一 00:00:00」，
    # 纯日期串必须仍算在窗口内（周一跑测试时今天恰好踩在这条边上）。
    c.post("/exams", headers=hs, json={"lecture": 29, "date": MON,
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
    # current_chapter 已改为展示章号（结构化重编号：首个 ≥200 词内容章=第1章）
    assert isinstance(r["current_chapter"], int) and r["current_chapter"] >= 1


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
    lib.execute("CREATE TABLE IF NOT EXISTS book_reading_config (book_id INTEGER,"
                " user TEXT, chapters TEXT, vocabUnlock TEXT)")
    lib.execute("CREATE TABLE IF NOT EXISTS reading_progress (book_id INTEGER,"
                " user TEXT, cfi TEXT, chapter_index INTEGER, percent REAL,"
                " reading_seconds INTEGER, updated_at TEXT)")
    lib.execute("CREATE TABLE IF NOT EXISTS chapters (id INTEGER PRIMARY KEY"
                " AUTOINCREMENT, book_id INTEGER, idx INTEGER, title TEXT,"
                " text_content TEXT, word_count INTEGER, spine_index INTEGER,"
                " skip_prep INTEGER)")
    lib.execute("INSERT INTO books (id, title, file_path)"
                " VALUES (1, 'TestBook', 'x.epub')")
    for i in range(10):
        lib.execute("INSERT INTO chapters (book_id, idx, title, word_count)"
                    " VALUES (1, ?, ?, 1000)", (i, f"Ch{i}"))
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


@pytest.mark.skipif(date.today().weekday() >= 5,
                    reason="周末模板无讲次/背词排程（口径=工作日）")
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


def test_vocab_goal_within_level_semantics(plan_env):
    """词汇目标=级别内增量口径：L1 的目标与 L0 掌握无关——分子分母只算
    L1 词；goal_assets.vocab_levels 带每级 mastered；周初超额显示满格。"""
    con = sqlite3.connect(plan_env / "grammar.db")
    con.execute(
        "CREATE TABLE IF NOT EXISTS vocab_word (word TEXT PRIMARY KEY, level INTEGER NOT NULL,"
        " pos TEXT DEFAULT '[]', gloss TEXT DEFAULT '', meanings TEXT DEFAULT '[]',"
        " example TEXT DEFAULT '', extra TEXT DEFAULT '{}')"
    )
    for w, lv in [("l0a", 0), ("l0b", 0), ("l1a", 1), ("l1b", 1), ("l1c", 1), ("l2a", 2)]:
        con.execute("INSERT INTO vocab_word (word, level) VALUES (?,?)", (w, lv))
    con.commit()
    con.close()

    c = _client(plan_env)
    hs = _login(c, "malin", "123456")
    ht = _login(c, "teacher", "123456")
    # 已掌握：L0 两词 + L1 一词。recite 上报的 level 补全走
    # ingest.default_db_path（本地 data/grammar.db）不经 create_app 传入
    # 路径——测试直接写 recite_word_progress.level。
    c.post("/recite/sessions", headers=hs, json={
        "total": 3, "wrong": 0, "acc": 100, "duration_sec": 5,
        "wrong_words": [], "mode": "flip", "scope": "all",
        "details": [{"word": w, "correct": True} for w in ("l0a", "l0b", "l1a")],
    })
    con2 = sqlite3.connect(plan_env / "fce.db")
    for w, lv in (("l0a", 0), ("l0b", 0), ("l1a", 1)):
        con2.execute("UPDATE recite_word_progress SET level = ? WHERE word = ?", (lv, w))
    con2.commit()
    con2.close()
    # l1a 上周就会了（拨 mastered_at 进基线）
    old = (date.today() - timedelta(days=7)).isoformat() + "T00:00:00+00:00"
    con2b = sqlite3.connect(plan_env / "fce.db")
    con2b.execute("UPDATE recite_word_progress SET mastered_at=? WHERE word='l1a'", (old,))
    con2b.commit()
    con2b.close()
    # goal_assets：每级 mastered 正确（L0=2 / L1=1）
    ed = c.get("/plan/editor-data?user=malin", headers=ht).json()["data"]
    vls = {v["level"]: v for v in ed["goal_assets"]["vocab_levels"]}
    assert vls[0]["mastered"] == 2 and vls[1]["mastered"] == 1
    assert vls[1]["words"] == 3  # 级内词数（不是累计 5）
    # 周计划 L1 60%（=2 词）：周初已会 1 → 分母 1；周内新会 l1b → 满格
    c.post("/recite/sessions", headers=hs, json={
        "total": 1, "wrong": 0, "acc": 100, "duration_sec": 5,
        "wrong_words": [], "mode": "flip", "scope": "L1",
        "details": [{"word": "l1b", "correct": True}],
    })
    con3 = sqlite3.connect(plan_env / "fce.db")
    con3.execute("UPDATE recite_word_progress SET level = 1 WHERE word = 'l1b'")
    con3.commit()
    con3.close()
    c.put(f"/plan/weeks/{MON}", headers=ht,
          json={"tasks": {"vocab_lv": 1, "vocab_pct": 60, "vocab_goal": 2}})
    d = c.get(f"/plan/week-view?week_start={MON}&user=malin",
              headers=ht).json()["data"]
    dim = next(x for x in d["goal_progress"]["dims"] if x["name"] == "词汇")
    # 分母=目标2−周初基线1=1；周内 L1 新会 l1b=1 → 1/1 满格
    assert dim["total"] == 1 and dim["done"] == 1


def test_vocab_goal_base_exceeded(plan_env):
    """周初已超额：L0 全会（509 口径在小词表=2/2），目标 1 词 → 满格+提示。"""
    con = sqlite3.connect(plan_env / "grammar.db")
    con.execute(
        "CREATE TABLE IF NOT EXISTS vocab_word (word TEXT PRIMARY KEY, level INTEGER NOT NULL,"
        " pos TEXT DEFAULT '[]', gloss TEXT DEFAULT '', meanings TEXT DEFAULT '[]',"
        " example TEXT DEFAULT '', extra TEXT DEFAULT '{}')"
    )
    for w in ("l0a", "l0b"):
        con.execute("INSERT INTO vocab_word (word, level) VALUES (?,0)", (w,))
    con.commit()
    con.close()
    c = _client(plan_env)
    hs = _login(c, "malin", "123456")
    ht = _login(c, "teacher", "123456")
    # 上周就会了 L0 两词（mastered_at 拨到上周）
    c.post("/recite/sessions", headers=hs, json={
        "total": 2, "wrong": 0, "acc": 100, "duration_sec": 5,
        "wrong_words": [], "mode": "flip", "scope": "all",
        "details": [{"word": "l0a", "correct": True}, {"word": "l0b", "correct": True}],
    })
    old = (date.today() - timedelta(days=14)).isoformat()
    con2 = sqlite3.connect(plan_env / "fce.db")
    con2.execute("UPDATE recite_word_progress SET level=0, mastered_at=?", (old,))
    con2.commit()
    con2.close()
    # 目标 L0 50%（=1 词）但周初已会 2 → 满格 + 「周初已达成」
    c.put(f"/plan/weeks/{MON}", headers=ht,
          json={"tasks": {"vocab_lv": 0, "vocab_pct": 50, "vocab_goal": 1}})
    d = c.get(f"/plan/week-view?week_start={MON}&user=malin",
              headers=ht).json()["data"]
    dim = next(x for x in d["goal_progress"]["dims"] if x["name"] == "词汇")
    assert dim["done"] == dim["total"] and "周初已达成" in (dim.get("extra") or "")


def test_daily_study_minutes(plan_env):
    """每日有效学习时长：study_min = 专注会话 active_sec 全量（泛读+精读+专题）
    + 背词 duration_sec；换日不串；week_view 与 my-week 同源透出。"""
    c = _client(plan_env)
    hs = _login(c, "malin", "123456")
    _seed_watermark_bookcase(plan_env)
    fce = sqlite3.connect(plan_env / "fce.db")
    # 周中某天：泛读 20 分钟 + 精读 10 分钟 = 30 分专注
    d1 = MON  # 周一
    for i, (mod, sec) in enumerate((("library", 1200), ("reading", 600))):
        # percent_track 带真实阅读位移（泛读会话无轨迹零位移会被判僵尸剔除）
        track = ('[{"t": 5, "p": 20.0}, {"t": 600, "p": 22.0}]'
                 if mod == "library" else None)
        fce.execute(
            "INSERT INTO focus_sessions (id, session_id, user, book_id, book_title,"
            " started_at, ended_at, total_sec, active_sec, module, created_at,"
            " percent_track)"
            " VALUES (?, ?, 'malin', 1, 'TestBook', ?, ?, ?, ?, ?, ?, ?)",
            (10 + i, f"d1s{i}", f"{d1}T10:00:00Z", f"{d1}T10:30:00Z",
             sec + 30, sec, mod, f"{d1}T10:30:00Z", track))
    fce.commit()
    fce.close()
    # 同日背词 15 分钟（900 秒，total 10 词）→ study_min = 30 + 15 = 45
    c.post("/recite/sessions", headers=hs, json={
        "total": 10, "wrong": 1, "acc": 90, "duration_sec": 900,
        "wrong_words": ["l0a"], "mode": "flip", "scope": "all",
        "details": [{"word": "l0a", "correct": False},
                    {"word": "l0b", "correct": True}],
    })
    con = sqlite3.connect(plan_env / "fce.db")
    con.execute("UPDATE recite_sessions SET created_at = ? || 'T12:00:00Z'", (d1,))
    con.commit()
    con.close()

    d = c.get(f"/plan/week-view?week_start={MON}&user=malin",
              headers=_login(c, "teacher", "123456")).json()["data"]
    monday = next(x for x in d["days"] if x["date"] == d1)
    assert monday["acts"]["study_min"] == 45
    assert monday["acts"]["focus_min"] == 30
    assert monday["acts"]["vocab_min"] == 15
    # 学生课程表同源
    m = c.get("/plan/my-week", headers=hs).json()["data"]
    m_mon = next(x for x in m["days"] if x["date"] == d1)
    assert m_mon["acts"]["study_min"] == 45


# ---- 周目标逐日均摊（week_daily_tasks 重构）----

def _put_week(c, ht, tasks):
    r = c.put(f"/plan/weeks/{MON}", headers=ht, json={"tasks": tasks})
    assert r.status_code == 200, r.text


@pytest.mark.skipif(date.today().weekday() >= 5,
                    reason="周末模板无讲次/背词排程（口径=工作日）")
def test_daily_spread_weekday_template(plan_env):
    """工作日均摊：讲次摊工作日、背词配额=剩余词÷剩余工作日。"""
    c = _client(plan_env)
    hs = _login(c, "malin", "123456")
    ht = _login(c, "teacher", "123456")
    _put_week(c, ht, {"lectures": [25, 26], "vocab_goal": 100})
    d = c.get("/plan/today", headers=hs).json()["data"]
    # 背词配额行存在且 quota 量化（周一 100/5=20；周五分母只剩 1 天）
    vocab = next((t for t in d["tasks"] if t["type"] == "vocab"), None)
    assert vocab is not None and vocab.get("quota")
    # 讲次按「剩余讲次 / 剩余工作日」向上取整摊到今天。
    # 周一 2/5 → 1 讲；周五 2/1 → 两讲都在今天。不能写死「恰好 1 讲」，
    # 也不能写成 `len==1 and … or "26" in text`（or 优先级会让多讲也误过）。
    wd_left = 5 - date.today().weekday()
    per = max(1, -(-2 // wd_left))  # 与 plan.week_daily_tasks 同一向上取整
    lecs = [t for t in d["tasks"] if t["type"] == "lecture"]
    assert [t["text"] for t in lecs] == [f"第{n}讲 学+测" for n in (25, 26)[:per]]


@pytest.mark.skipif(date.today().weekday() >= 5,
                    reason="周末模板无讲次/背词排程（口径=工作日）")
def test_daily_spread_future_days_carry_plan(plan_env):
    """未来日带出均摊计划：week_view 的 future days 不再是空 tasks。"""
    c = _client(plan_env)
    ht = _login(c, "teacher", "123456")
    _put_week(c, ht, {"lectures": [25], "vocab_goal": 200, "fce": ["Test1 RUE"]})
    d = c.get(f"/plan/week-view?week_start={MON}&user=malin",
              headers=ht).json()["data"]
    futures = [x for x in d["days"] if x["future"]]
    assert futures, "本周应存在未来日"
    for x in futures:
        assert x["total"] == len(x["tasks"])
        # 未来日任务全部 done=False（灰显计划，不是完成记录）
        assert all(not t["done"] for t in x["tasks"])
    # 周六未来日应排 FCE（模板：周六=FCE 大题+精读）
    sat_all = [x for x in d["days"]
               if x["future"] and date.fromisoformat(x["date"]).weekday() == 5]
    if sat_all:
        sat_tasks = sat_all[0]["tasks"]
        assert any(t["type"] == "fce" for t in sat_tasks)
    # 工作日未来日应有背词配额
    wd = [x for x in futures if date.fromisoformat(x["date"]).weekday() < 5]
    if wd:
        assert any(t["type"] == "vocab" for t in wd[0]["tasks"])


@pytest.mark.skipif(date.today().weekday() >= 5,
                    reason="周末模板无讲次/背词排程（口径=工作日）")
def test_vocab_quota_done_judgement(plan_env):
    """背词配额完成判定：当天背词词数 ≥ quota 即点亮。"""
    c = _client(plan_env)
    hs = _login(c, "malin", "123456")
    ht = _login(c, "teacher", "123456")
    _put_week(c, ht, {"vocab_goal": 20})
    d = c.get("/plan/today", headers=hs).json()["data"]
    vocab = next(t for t in d["tasks"] if t["type"] == "vocab")
    assert vocab["done"] is False
    # 背满周目标 20 词（≥任何工作日的均摊 quota——quota=ceil(剩余/剩余
    # 工作日)，周中任何一天跑都成立）→ 完成。created_at 归一到本地今天
    # 12:00Z——凌晨跑测试时 UTC 串还是昨天，会跨日聚合不到（同 study_min
    # 测试做法）
    c.post("/recite/sessions", headers=hs, json={
        "total": 20, "wrong": 0, "acc": 100, "duration_sec": 300,
        "wrong_words": [], "mode": "flip", "scope": "all", "details": [],
    })
    con = sqlite3.connect(plan_env / "fce.db")
    con.execute("UPDATE recite_sessions SET created_at = ? || 'T12:00:00Z'",
                (date.today().isoformat(),))
    con.commit()
    con.close()
    d = c.get("/plan/today", headers=hs).json()["data"]
    vocab = next(t for t in d["tasks"] if t["type"] == "vocab")
    assert vocab["done"] is True and "✓" in (vocab.get("detail") or "")


def test_teacher_exam_student_attribution(plan_env):
    """教师替孩子登记成绩：?student= 归属学生；学生登录永远记自己。"""
    c = _client(plan_env)
    ht = _login(c, "teacher", "123456")
    hs = _login(c, "malin", "123456")
    today = date.today().isoformat()
    # 教师登记（带 student）→ 归 malin
    r = c.post("/exams", headers=ht, json={
        "lecture": 29, "date": today, "score": 74,
        "wrong": [1, 2], "kind": "hw", "student": "malin"})
    assert r.status_code == 200
    rec = c.get("/exams", headers=ht,
                params={"user": "malin"}).json()["data"]
    assert any(x["lecture"] == 29 and x["user"] == "malin" for x in rec)
    # 教师不带 student → 记教师名下（旧行为保留）
    c.post("/exams", headers=ht, json={
        "lecture": 30, "date": today, "score": 80, "wrong": [], "kind": "hw"})
    rec = c.get("/exams", headers=ht, params={"user": "teacher"}).json()["data"]
    assert any(x["lecture"] == 30 and x["user"] == "teacher" for x in rec)
    # 学生登录提交（伪造 student 字段）→ 强制记自己
    c.post("/exams", headers=hs, json={
        "lecture": 31, "date": today, "score": 90, "wrong": [],
        "kind": "lecture", "student": "teacher"})
    rec = c.get("/exams", headers=ht, params={"user": "malin"}).json()["data"]
    assert any(x["lecture"] == 31 and x["user"] == "malin" for x in rec)


def test_topic_completion_lights_lecture(plan_env):
    """自学专题学完 = 讲次「学+测」完成：排程不再重排 + 日信号点亮。"""
    c = _client(plan_env)
    ht = _login(c, "teacher", "123456")
    _put_week(c, ht, {"lectures": [29]})
    # 造专题内容（2 节点）+ 学完的进度（updated_at 归一今天，UTC+8 口径）
    con = sqlite3.connect(plan_env / "fce.db")
    con.execute("""CREATE TABLE IF NOT EXISTS topic_content (
        topic_id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
        subtitle TEXT NOT NULL DEFAULT '', emoji TEXT NOT NULL DEFAULT '',
        assigned_to TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '{}',
        published INTEGER NOT NULL DEFAULT 1, kind TEXT NOT NULL DEFAULT 'topic',
        updated_at TEXT DEFAULT '')""")
    con.execute("""CREATE TABLE IF NOT EXISTS topic_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL,
        topic_id TEXT NOT NULL, assigned_user TEXT NOT NULL DEFAULT '',
        done_keys TEXT NOT NULL DEFAULT '[]', completed_at TEXT DEFAULT '',
        updated_at TEXT DEFAULT '', UNIQUE(user, topic_id))""")
    content = ('{"nodes": [{"key": "final", "title": "A"},'
               ' {"key": "steps", "title": "B"}]}')
    con.execute("INSERT OR REPLACE INTO topic_content"
                " (topic_id, title, content) VALUES ('lecture-29-passive-1',"
                " '第29讲·被动语态', ?)", (content,))
    con.execute("INSERT OR REPLACE INTO topic_progress"
                " (user, topic_id, done_keys, completed_at, updated_at)"
                " VALUES ('malin', 'lecture-29-passive-1', ?, ?, ?)",
                ('["final", "steps"]',
                 f"{date.today().isoformat()}T12:00:00+00:00",
                 f"{date.today().isoformat()}T12:00:00+00:00"))
    con.commit()
    con.close()
    # 当天日信号：专题学完点亮 lecture:29（_auto_done 直接断言，
    # 不依赖当日模板排不排讲次行——周日模板不出讲次是设计行为）
    from grammar_kb.plan import PlanStore
    ps = PlanStore(str(plan_env / "fce.db"))
    auto, _ = ps._auto_done("malin", date.today().isoformat())
    assert auto.get("lecture:29") is True
    # 排程口径：周窗口内 29 讲已算完成 → 工作日不再重排「第29讲 学+测」
    from grammar_kb.plan import week_bounds
    lo, _ = week_bounds(MON)
    upto = (date.today() + timedelta(days=1)).isoformat()
    assert 29 in ps._topic_done_lectures(lo, upto, "malin")
    # 完成日当天的 00:00:00 下界（week_bounds 形态）不能把当天记录比丢
    assert 29 in ps._topic_done_lectures(
        date.today().isoformat() + " 00:00:00", upto, "malin")
    # 学到一半（只完成 1/2 节点）不点亮
    con = sqlite3.connect(plan_env / "fce.db")
    con.execute("UPDATE topic_progress SET done_keys = '[\"final\"]'")
    con.commit()
    con.close()
    assert 29 not in ps._topic_done_lectures(lo, upto, "malin")
    assert "lecture:29" not in ps._auto_done(
        "malin", date.today().isoformat())[0]


def test_weekday_speak_completion_visible(plan_env):
    """周中提前提交朗读：提交日清单出「今天已提交朗读 ✓」行，
    且周六排程把它从剩余里剔除（不再重复排未完成）。"""
    c = _client(plan_env)
    ht = _login(c, "teacher", "123456")
    hs = _login(c, "malin", "123456")
    _put_week(c, ht, {"articles": [61]})
    con = sqlite3.connect(plan_env / "fce.db")
    con.execute("""CREATE TABLE IF NOT EXISTS reading_article (
        id INTEGER PRIMARY KEY, title TEXT, source TEXT DEFAULT '',
        text TEXT DEFAULT '', cefr TEXT DEFAULT '', created_at TEXT DEFAULT '')""")
    con.execute("INSERT OR REPLACE INTO reading_article (id, title, text)"
                " VALUES (61, 'Too Many Climbers on Mt. Fuji', 'x')")
    con.execute("""CREATE TABLE IF NOT EXISTS reading_recordings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, article_id INTEGER,
        file TEXT DEFAULT '', note TEXT DEFAULT '',
        created_at TEXT NOT NULL DEFAULT '')""")
    today = date.today()
    # 录音落在本周周六之前（如周三）——验证周六排程的剩余剔除。
    # 周三还没到（周一、周二）则退到周一。✓ 行只挂在提交日
    # （「今天已提交朗读」），不能拿日历今天去对：周二查 /plan/today
    # 会看到 0 篇精读，因为录音在周一。
    wed = date.fromisoformat(MON) + timedelta(days=2)
    rec_day = wed if wed <= today else date.fromisoformat(MON)
    con.execute("INSERT INTO reading_recordings (user, article_id, created_at)"
                " VALUES ('malin', 61, ?)",
                (f"{rec_day.isoformat()}T12:00:00+00:00",))
    con.commit()
    con.close()
    # 提交日是周一或周三，都是工作日模板
    d = c.get("/plan/today", headers=hs,
              params={"day": rec_day.isoformat()}).json()["data"]
    arts = [t for t in d["tasks"] if t.get("type") == "article"]
    assert any(t.get("done") for t in arts), \
        f"周中提交朗读的计划内文章应在提交日显示完成: {d['tasks']}"
    # 周六排程：已提交的 61 不再出现在周六清单（剩余剔除）
    sat = date.fromisoformat(MON) + timedelta(days=5)
    from grammar_kb.plan import PlanStore
    ps = PlanStore(str(plan_env / "fce.db"))
    sat_tasks = ps.week_daily_tasks(MON, for_date=sat.isoformat(), user="malin")
    sat_arts = [t for t in sat_tasks
                if t.get("type") == "article" and not t.get("done")]
    assert all(61 != t.get("article_id") for t in sat_arts), \
        f"已提交朗读的文章不应再排周六未完成: {sat_arts}"


def test_rules_registry_and_switch(plan_env):
    """完成判定规则：默认值生效 + 教师可开关 + 关闭后专题不再点亮讲次。"""
    c = _client(plan_env)
    ht = _login(c, "teacher", "123456")
    hs = _login(c, "malin", "123456")
    # 造专题内容 + 学完进度
    con = sqlite3.connect(plan_env / "fce.db")
    con.execute("""CREATE TABLE IF NOT EXISTS topic_content (
        topic_id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
        subtitle TEXT NOT NULL DEFAULT '', emoji TEXT NOT NULL DEFAULT '',
        assigned_to TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '{}',
        published INTEGER NOT NULL DEFAULT 1, kind TEXT NOT NULL DEFAULT 'topic',
        updated_at TEXT DEFAULT '')""")
    con.execute("""CREATE TABLE IF NOT EXISTS topic_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL,
        topic_id TEXT NOT NULL, assigned_user TEXT NOT NULL DEFAULT '',
        done_keys TEXT NOT NULL DEFAULT '[]', completed_at TEXT DEFAULT '',
        updated_at TEXT DEFAULT '', UNIQUE(user, topic_id))""")
    content = '{"nodes": [{"key": "a"}, {"key": "b"}]}'
    now = f"{date.today().isoformat()}T12:00:00+00:00"
    con.execute("INSERT OR REPLACE INTO topic_content (topic_id, content)"
                " VALUES ('lecture-29-passive-1', ?)", (content,))
    con.execute("INSERT OR REPLACE INTO topic_progress"
                " (user, topic_id, done_keys, completed_at, updated_at)"
                " VALUES ('malin', 'lecture-29-passive-1', ?, ?, ?)",
                ('["a", "b"]', now, now))
    con.commit()
    con.close()

    # 1. 规则清单：默认全开（教师与学生各可读）
    rules = c.get("/plan/rules", headers=ht,
                  params={"user": "malin"}).json()["data"]["rules"]
    by_id = {r["id"]: r for r in rules}
    assert set(by_id) == {"topic_counts_lecture", "speak_early_credit",
                          "zombie_filter", "exam_user_scope"}
    assert all(r["value"] == 1 for r in rules)  # 默认开
    assert len(c.get("/plan/rules", headers=hs).json()["data"]["rules"]) == 4

    # 2. 内核：默认（开）专题点亮 29 讲
    from grammar_kb.plan import PlanStore
    ps = PlanStore(str(plan_env / "fce.db"))
    assert 29 in ps.lecture_done(None, None, "malin", "lecture")

    # 3. 教师关掉规则 → 内核立即不再点亮（成绩也没有 29 → 空）
    r = c.put("/plan/rules/topic_counts_lecture", headers=ht,
              json={"value": 0, "user": "malin"})
    assert r.status_code == 200 and r.json()["data"]["value"] == 0
    assert 29 not in ps.lecture_done(None, None, "malin", "lecture")

    # 4. 未知规则 404；学生改规则 403
    assert c.put("/plan/rules/nope", headers=ht,
                 json={"value": 1}).status_code == 404
    assert c.put("/plan/rules/topic_counts_lecture", headers=hs,
                 json={"value": 1}).status_code == 403
    # 5. 恢复默认（开）
    c.put("/plan/rules/topic_counts_lecture", headers=ht,
          json={"value": 1, "user": "malin"})
    assert 29 in ps.lecture_done(None, None, "malin", "lecture")


def test_week_goal_progress_counts_topic(plan_env):
    """「语法课程 0/1」修复：周目标进度卡的讲次维度计入专题完成。"""
    c = _client(plan_env)
    ht = _login(c, "teacher", "123456")
    _put_week(c, ht, {"lectures": [29]})
    con = sqlite3.connect(plan_env / "fce.db")
    con.execute("""CREATE TABLE IF NOT EXISTS topic_content (
        topic_id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
        subtitle TEXT NOT NULL DEFAULT '', emoji TEXT NOT NULL DEFAULT '',
        assigned_to TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '{}',
        published INTEGER NOT NULL DEFAULT 1, kind TEXT NOT NULL DEFAULT 'topic',
        updated_at TEXT DEFAULT '')""")
    con.execute("""CREATE TABLE IF NOT EXISTS topic_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL,
        topic_id TEXT NOT NULL, assigned_user TEXT NOT NULL DEFAULT '',
        done_keys TEXT NOT NULL DEFAULT '[]', completed_at TEXT DEFAULT '',
        updated_at TEXT DEFAULT '', UNIQUE(user, topic_id))""")
    content = '{"nodes": [{"key": "a"}, {"key": "b"}]}'
    now = f"{date.today().isoformat()}T12:00:00+00:00"
    con.execute("INSERT OR REPLACE INTO topic_content (topic_id, content)"
                " VALUES ('lecture-29-passive-1', ?)", (content,))
    con.execute("INSERT OR REPLACE INTO topic_progress"
                " (user, topic_id, done_keys, completed_at, updated_at)"
                " VALUES ('malin', 'lecture-29-passive-1', ?, ?, ?)",
                ('["a", "b"]', now, now))
    # 干扰行：别的学生的完成专题不应点亮 malin 的讲次（user 过滤）
    con.execute("INSERT OR REPLACE INTO topic_progress"
                " (user, topic_id, done_keys, completed_at, updated_at)"
                " VALUES ('mxy', 'lecture-30-x', ?, ?, ?)",
                ('["a", "b"]', now, now))
    con.execute("INSERT OR REPLACE INTO topic_content (topic_id, content)"
                " VALUES ('lecture-30-x', ?)", (content,))
    con.commit()
    con.close()
    d = c.get(f"/plan/week-view?week_start={MON}&user=malin",
              headers=ht).json()["data"]
    dim = next(x for x in d["goal_progress"]["dims"]
               if x["name"] == "语法课程")
    assert dim["done"] == 1 and dim["total"] == 1, dim


def test_editor_data_excludes_base_articles(plan_env):
    """周计划编辑器文章池只给派生文：base 原文是教师备课素材、
    学生端阅读练习不可见（detail 403）——混进池会被选上，孩子无处
    可看也永远完不成（10-14 本周 2 篇精读排了 base id 2/3 的事故）。"""
    c = _client(plan_env)
    ht = _login(c, "teacher", "123456")
    con = sqlite3.connect(plan_env / "fce.db")
    con.execute("""CREATE TABLE IF NOT EXISTS reading_article (
        id INTEGER PRIMARY KEY, kind TEXT DEFAULT 'derived',
        base_key TEXT DEFAULT '', title TEXT, words INTEGER DEFAULT 0,
        source TEXT DEFAULT '', text TEXT DEFAULT '', cefr TEXT DEFAULT '',
        created_at TEXT DEFAULT '')""")
    # base id 1-3（学生端不可见） + derived id 52/53
    for i in (1, 2, 3):
        con.execute("INSERT OR REPLACE INTO reading_article (id, kind, title, text)"
                    " VALUES (?, 'base', ?, 'x')", (i, f"base-{i}"))
    for i in (52, 53):
        con.execute("INSERT OR REPLACE INTO reading_article (id, kind, title, text)"
                    " VALUES (?, 'derived', ?, 'x')", (i, f"derived-{i}"))
    con.commit()
    con.close()
    arts = c.get("/plan/editor-data?user=malin", headers=ht).json()["data"]["articles"]
    ids = {a["id"] for a in arts}
    assert ids == {52, 53}, f"编辑器文章池应只含派生文: {arts}"


def test_saturday_article_rows_carry_id_for_link(plan_env):
    """周六排程的精读行带 article_id：学生课程表/教师计划卡据此渲染
    「直达阅读练习」链接（#/reading?art=ID 自动进详情）。"""
    con = sqlite3.connect(plan_env / "fce.db")
    con.execute("""CREATE TABLE IF NOT EXISTS reading_article (
        id INTEGER PRIMARY KEY, kind TEXT DEFAULT 'derived',
        base_key TEXT DEFAULT '', title TEXT, words INTEGER DEFAULT 0,
        source TEXT DEFAULT '', text TEXT DEFAULT '', cefr TEXT DEFAULT '',
        created_at TEXT DEFAULT '')""")
    con.execute("INSERT OR REPLACE INTO reading_article (id, kind, title, text)"
                " VALUES (58, 'derived', 'The Secret Sense in Elephant Trunks', 'x')")
    con.commit()
    con.close()
    from grammar_kb.plan import PlanStore
    ps = PlanStore(str(plan_env / "fce.db"))
    ps.put_week(MON, {"articles": [58]}, "", user="malin")
    sat = date.fromisoformat(MON) + timedelta(days=5)
    rows = [t for t in ps.week_daily_tasks(MON, for_date=sat.isoformat(),
                                           user="malin")
            if t.get("type") == "article"]
    assert rows and all(r.get("article_id") for r in rows), rows
    # 标题在文案里被截到 14 字符（The Secret Sen…），断言截断前缀
    assert any("Secret Sen" in (r.get("text") or "") for r in rows), rows

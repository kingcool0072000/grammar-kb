"""NCE2 听力跟读接入计划/闭环的测试。

不依赖并行车道的 NCE2 Store：nce2_submission 表由本文件按契约 DDL 直建
直插（fce.db）；manifest 走 GRAMMAR_KB_NCE2_BOOK 指向的临时 book.json，
nce2_study 模块未合入时装只守契约的替身（合入后走真实 load_manifest）。
固定日历：2026-11-09（周一）所在周，全部 today_tasks 显式传 day，不依赖
真实时钟。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.plan.store import PlanStore  # noqa: E402
from grammar_kb.plan.util import task_public  # noqa: E402
from grammar_kb.server import create_app  # noqa: E402

MON = "2026-11-09"
TUE = "2026-11-10"
WED = "2026-11-11"
SAT = "2026-11-14"
SUN = "2026-11-15"

# 与 nce2_study 契约同构的 manifest 素材（load_manifest 原样读出）
BOOK = {
    "book": "NCE2",
    "lessons": [
        {"id": 1, "title": "A private conversation", "n_sentences": 8,
         "n_words": 120},
        {"id": 2, "title": "Breakfast or lunch", "n_sentences": 10,
         "n_words": 150},
        {"id": 3, "title": "Please send me a card", "n_sentences": 9,
         "n_words": 130},
        {"id": 5, "title": "No wrong numbers", "n_sentences": 8,
         "n_words": 110},
        {"id": 6, "title": "Percy Buttons", "n_sentences": 8,
         "n_words": 105},
        {"id": 7, "title": "Too late", "n_sentences": 9, "n_words": 125},
        {"id": 8, "title": "The best and the worst", "n_sentences": 8,
         "n_words": 115},
        {"id": 11, "title": "One good turn deserves another",
         "n_sentences": 8, "n_words": 105},
        {"id": 12, "title": "Goodbye and good luck", "n_sentences": 8,
         "n_words": 100},
        {"id": 13, "title": "The Greenwood Boys", "n_sentences": 9,
         "n_words": 130},
        {"id": 21, "title": "Do you call that a hat", "n_sentences": 8,
         "n_words": 110},
    ],
}

# nce2_submission 契约 DDL（并行车道建的表；测试环境自建同构表）
NCE2_DDL = """
CREATE TABLE IF NOT EXISTS nce2_submission (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user TEXT NOT NULL,
  lesson_id INTEGER NOT NULL,
  date TEXT NOT NULL,
  kind TEXT NOT NULL,
  audio_b64 TEXT,
  mime TEXT,
  duration_sec INTEGER,
  status TEXT,
  teacher_score REAL,
  teacher_comment TEXT,
  graded_at TEXT,
  created_at TEXT
);
"""


def _ensure_manifest_module(monkeypatch):
    """nce2_study 由并行车道建。未合入时注入只守契约的替身模块（读
    GRAMMAR_KB_NCE2_BOOK 原样 JSON），合入后自动走真实 load_manifest。"""
    try:
        import grammar_kb.nce2_study  # noqa: F401
        return
    except ImportError:
        pass
    mod = types.ModuleType("grammar_kb.nce2_study")

    def load_manifest():
        path = os.environ.get("GRAMMAR_KB_NCE2_BOOK")
        if not path or not Path(path).exists():
            return {"lessons": []}
        return json.loads(Path(path).read_text(encoding="utf-8"))

    mod.load_manifest = load_manifest
    monkeypatch.setitem(sys.modules, "grammar_kb.nce2_study", mod)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """空库三件套 + manifest 素材 + nce2_submission 表。"""
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))
    monkeypatch.setenv("GRAMMAR_KB_FCE_DB", str(tmp_path / "fce.db"))
    monkeypatch.setenv("GRAMMAR_KB_DB", str(tmp_path / "grammar.db"))
    monkeypatch.setenv("GRAMMAR_KB_EXAM_DB", str(tmp_path / "exam.db"))
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DB", str(tmp_path / "library.db"))
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DIR", str(tmp_path / "files"))
    book = tmp_path / "nce2_book.json"
    book.write_text(json.dumps(BOOK, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("GRAMMAR_KB_NCE2_BOOK", str(book))
    _ensure_manifest_module(monkeypatch)
    fce = tmp_path / "fce.db"
    con = sqlite3.connect(fce)
    con.executescript(NCE2_DDL)
    con.commit()
    con.close()
    return {"fce": str(fce), "exam": str(tmp_path / "exam.db"),
            "grammar": str(tmp_path / "grammar.db"),
            "book": str(book)}


def _store(env) -> PlanStore:
    return PlanStore(env["fce"], exam_db_path=env["exam"],
                     grammar_db_path=env["grammar"])


def _client(env) -> TestClient:
    return TestClient(create_app(env["grammar"], env["exam"],
                                 fce_db_path=env["fce"]))


def _login(c, user, password):
    r = c.post("/auth/login", json={"user": user, "password": password})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['data']['token']}"}


def _submit(env, user, lid, day, kind):
    """直接往 nce2_submission 插一行（date=北京当日 ISO，契约同款）。"""
    con = sqlite3.connect(env["fce"])
    con.execute(
        "INSERT INTO nce2_submission (user, lesson_id, date, kind, status,"
        " created_at) VALUES (?,?,?,?, 'submitted', ?)",
        (user, lid, day, kind, f"{day} 12:00:00"))
    con.commit()
    con.close()


def _nce2_rows(store: PlanStore, user: str, day: str) -> list[dict]:
    out = store.today_tasks(user, day=day)["tasks"]
    return [t for t in out if t.get("type") == "nce2"]


# ---- 单元：分组契约读法（nce2_units 唯一来源，无旧键兜底） ----

def test_nce2_units_contract(env):
    store = _store(env)
    units = store._nce2_units({"nce2_units": [
        {"lessons": ["2", 1, 2, "x"], "mode": "daily"},
        {"lessons": [3], "mode": "weekend", "task_mode": "listen"},
        {"lessons": [9], "mode": "bogus"},          # 坏 mode：整组跳过
        {"lessons": [], "mode": "daily"},           # 空组跳过
        "junk",
    ]})
    assert [(u["lessons"], u["mode"], u["task_mode"]) for u in units] == [
        ([2, 1], "daily", "shadow"),   # 去重保序转 int；task_mode 缺省 shadow
        ([3], "weekend", "listen"),
    ]
    # 无旧键兜底：旧平铺 nce2 键不读
    assert store._nce2_units({"nce2": [1, 2]}) == []
    assert store._plan_lessons({"nce2_units": [
        {"lessons": [2, 1], "mode": "daily"},
        {"lessons": [1, 3], "mode": "weekend"}]}) == [2, 1, 3]


# ---- daily 组：队首 1 课、完成信号、次日队列前进 ----

def test_daily_shadow_queue_signal_and_advance(env):
    store = _store(env)
    tasks = {"nce2_units": [{"lessons": [1, 2, 3], "mode": "daily",
                             "task_mode": "shadow"}]}
    c = _client(env)
    ht = _login(c, "teacher", "123456")
    r = c.put(f"/plan/weeks/{MON}", headers=ht,
              json={"tasks": tasks, "notes": ""})
    assert r.status_code == 200

    rows = _nce2_rows(store, "malin", TUE)
    assert len(rows) == 1  # 每天只队首 1 课，同日不堆
    row = rows[0]
    assert row["key"] == "nce2:1" and row["lesson_id"] == 1
    assert row["nce2_mode"] == "shadow"
    assert row["text"].startswith("听力跟读《")
    assert row["done"] is False and row["why"] == "no_signal"

    # 当天提交跟读 → 信号点亮（why 有值）；当天清单=新队首 + 今天完成的 ✓ 行
    _submit(env, "malin", 1, TUE, "shadow")
    rows = {x["lesson_id"]: x for x in _nce2_rows(store, "malin", TUE)}
    assert rows[1]["done"] is True and rows[1]["why"] == "signal"
    assert rows[1]["detail"] == "今天已提交 ✓"
    assert rows[2]["done"] is False  # 新队首同行出现

    # 次日队首前进：昨天做完的不再重现
    rows = _nce2_rows(store, "malin", WED)
    assert [x["lesson_id"] for x in rows] == [2]

    # 周视图：标题/计数（对齐 video 行的现状做法）
    v = store.week_view(MON, "malin")
    assert v["breakdown"]["nce2_count"] == 3
    assert "听力跟读" in v["breakdown"]["drill_types"]
    assert {"lesson_id": 1, "title": "A private conversation"} \
        in v["nce2_titles"]


# ---- listen 行：listen 提交满足；shadow 提交也满足 listen 行 ----

def test_listen_row_satisfied_by_listen(env):
    store = _store(env)
    store.put_week(MON, {"nce2_units": [{"lessons": [5, 6], "mode": "daily",
                                         "task_mode": "listen"}]}, "", "malin")
    rows = _nce2_rows(store, "malin", TUE)
    assert rows[0]["text"].startswith("精听《")  # listen 行文案
    _submit(env, "malin", 5, TUE, "listen")
    rows = {x["lesson_id"]: x for x in _nce2_rows(store, "malin", TUE)}
    assert rows[5]["done"] is True
    # 次日队首前进到第 6 课
    assert [x["lesson_id"] for x in _nce2_rows(store, "malin", WED)] == [6]


def test_listen_row_satisfied_by_shadow(env):
    """只有 listen 提交只满足 listen 行；反之跟读提交覆盖精听。"""
    store = _store(env)
    store.put_week(MON, {"nce2_units": [{"lessons": [7, 8], "mode": "daily",
                                         "task_mode": "listen"}]}, "", "malin")
    _submit(env, "malin", 7, TUE, "shadow")  # 只交了跟读
    rows = {x["lesson_id"]: x for x in _nce2_rows(store, "malin", TUE)}
    assert rows[7]["done"] is True
    assert [x["lesson_id"] for x in _nce2_rows(store, "malin", WED)] == [8]


def test_shadow_row_not_advanced_by_listen_only(env):
    """shadow 行只认跟读提交：只有 listen 提交 → 当天信号点亮（有提交即亮），
    但欠账没清，次日队首原样重现（不前进）。"""
    store = _store(env)
    store.put_week(MON, {"nce2_units": [{"lessons": [21], "mode": "daily",
                                         "task_mode": "shadow"}]}, "", "malin")
    _submit(env, "malin", 21, TUE, "listen")
    rows = {x["lesson_id"]: x for x in _nce2_rows(store, "malin", TUE)}
    assert rows[21]["done"] is True
    assert [x["lesson_id"] for x in _nce2_rows(store, "malin", WED)] == [21]


def test_monday_submission_counts_into_week_window(env):
    """周一的提交必须进周窗口 done 集合（回归：week_bounds 的 lo 带
    00:00:00，裸日期串 date 对它做 >= 会把周一整 exclusion——周二起
    周一完成的课被当假欠账重出）。"""
    store = _store(env)
    store.put_week(MON, {"nce2_units": [{"lessons": [1, 2], "mode": "daily",
                                         "task_mode": "shadow"}]}, "", "malin")
    _submit(env, "malin", 1, MON, "shadow")
    # 周一当天：✓ 行
    rows = {x["lesson_id"]: x for x in _nce2_rows(store, "malin", MON)}
    assert rows[1]["done"] is True
    # 周二：队首前进到第 2 课，第 1 课不再重现（假欠账）
    assert [x["lesson_id"] for x in _nce2_rows(store, "malin", TUE)] == [2]
    # ctx 切片与越界回退两条路径口径一致（周一命中）
    from grammar_kb.plan.ctx import WeekCtx
    ctx = WeekCtx(store, "malin", MON)
    assert ctx.nce2_shadow_done(MON + " 00:00:00", TUE) == {1}
    assert store._nce2_kind_ids(MON + " 00:00:00", TUE, "malin", "shadow") == {1}


# ---- weekend 组：周六集中 2 课、周日兜底清尾、工作日不排 ----

def test_weekend_group_sat_two_sun_sweep(env):
    store = _store(env)
    store.put_week(MON, {"nce2_units": [{"lessons": [11, 12, 13],
                                         "mode": "weekend",
                                         "task_mode": "shadow"}]}, "", "malin")
    assert _nce2_rows(store, "malin", TUE) == []  # 工作日不排 weekend 组
    assert [x["lesson_id"] for x in _nce2_rows(store, "malin", SAT)] == [11, 12]
    _submit(env, "malin", 11, SAT, "shadow")
    _submit(env, "malin", 12, SAT, "shadow")
    # 周六容量 2 课：交完的 ✓ 行保留，第 3 课顺延（欠账滚动）
    by_id = {x["lesson_id"]: x for x in _nce2_rows(store, "malin", SAT)}
    assert by_id[11]["done"] is True and by_id[12]["done"] is True
    assert by_id[13]["done"] is False
    # 周日只剩没做的第 13 课（清尾），做完的不再重现
    assert [x["lesson_id"] for x in _nce2_rows(store, "malin", SUN)] == [13]


# ---- 对外出口：task_public 透传 / 冻结快照白名单 ----

def test_task_public_passthrough():
    row = {"type": "nce2", "key": "nce2:3", "lesson_id": 3,
           "nce2_mode": "listen", "text": "精听《x》", "detail": "d",
           "done": True, "why": "signal"}
    out = task_public(row)
    assert out["lesson_id"] == 3 and out["nce2_mode"] == "listen"
    # 非 nce2 行不带这两个键
    vid = task_public({"type": "video", "key": "video:a", "vid": "a"})
    assert "lesson_id" not in vid and "nce2_mode" not in vid


def test_freeze_history_keeps_nce2_fields(env):
    store = _store(env)
    store.freeze_history_day("malin", TUE, [
        {"type": "nce2", "key": "nce2:1", "text": "听力跟读《x》",
         "detail": "d", "lesson_id": 1, "nce2_mode": "shadow", "done": True},
    ])
    rows = store.history_day("malin", TUE)
    assert rows[0]["lesson_id"] == 1 and rows[0]["nce2_mode"] == "shadow"
    assert rows[0]["done"] is True


# ---- 编辑器选项源：nce2 池 ----

def test_editor_data_nce2_pool(env):
    store = _store(env)
    pool = store.editor_data("malin")["nce2"]
    assert [x["id"] for x in pool] == [x["id"] for x in BOOK["lessons"]]
    assert pool[0]["title"] == "A private conversation"
    for x in pool:
        assert {"id", "title", "n_sentences", "n_words"} <= set(x)


def test_editor_data_nce2_pool_missing_file(env, monkeypatch):
    """manifest 缺文件 → 空池不崩（契约：缺文件返回空 lessons）。"""
    monkeypatch.setenv("GRAMMAR_KB_NCE2_BOOK",
                       str(Path(env["book"]).parent / "nope.json"))
    store = _store(env)
    assert store.editor_data("malin")["nce2"] == []
    # 计划行同样不出（标题查不到 → None）
    store.put_week(MON, {"nce2_units": [{"lessons": [1], "mode": "daily"}]},
                   "", "malin")
    assert _nce2_rows(store, "malin", TUE) == []

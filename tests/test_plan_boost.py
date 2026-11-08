"""提升档（boost_log）与派生行测试：事件表契约 / 周计划 boost 行 /
欠账滚动 / lecture_done 第三分支 / today_items 校验。

隔离：GRAMMAR_KB_FCE_DB 指向 tmp 库（plan.boost 走 dbreg 默认路径解析），
PlanStore 显式传同一文件。日期用固定周（2026-11-09 周一），不依赖真实今天。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from grammar_kb.clock import CN_TZ, freeze_clock
from grammar_kb.plan import boost
from grammar_kb.plan.ctx import WeekCtx
from grammar_kb.plan.store import PlanStore

MON = "2026-11-09"          # 周一
TUE = "2026-11-10"
THU = "2026-11-12"
SAT = "2026-11-14"
MEM_REF = "memory:lecture-30-passive-2:k2"
RET_REF = "retell:12"
ITEMS = [
    {"type": "memory", "topic_id": "lecture-30-passive-2", "node_key": "k2"},
    {"type": "retell", "pattern_id": 12},
]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAMMAR_KB_FCE_DB", str(tmp_path / "fce.db"))
    # 专题内容查 titles 走 fce.db 同库；其他默认库指到 tmp 防蹭仓库真库
    monkeypatch.setenv("GRAMMAR_KB_DB", str(tmp_path / "grammar.db"))
    monkeypatch.setenv("GRAMMAR_KB_EXAM_DB", str(tmp_path / "exam.db"))
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DB", str(tmp_path / "library.db"))
    return tmp_path / "fce.db"


def _store(env) -> PlanStore:
    return PlanStore(str(env), exam_db_path=str(env) + ".exam",
                     grammar_db_path=str(env) + ".grammar")


def _seed_topic(store: PlanStore, topic_id: str = "lecture-30-passive-2",
                node_key: str = "k2", title: str = "被动态语态一览") -> None:
    """topic_content 造一行（节点 k2 带标题），供派生行文案查标题。"""
    content = (
        '{"nodes": [{"key": "%s", "title": "%s", "explain": "", '
        '"wrong_qs": [], "general_qs": [], "children": []}]}' % (node_key, title)
    )
    with store._connect() as conn:
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS topic_content ("
            " topic_id TEXT PRIMARY KEY, title TEXT NOT NULL,"
            " subtitle TEXT NOT NULL DEFAULT '', emoji TEXT NOT NULL DEFAULT '',"
            " assigned_to TEXT NOT NULL DEFAULT '',"
            " content TEXT NOT NULL DEFAULT '{}',"
            " published INTEGER NOT NULL DEFAULT 1,"
            " created_at TEXT NOT NULL DEFAULT (datetime('now')), "
            " updated_at TEXT NOT NULL DEFAULT (datetime('now')));")
        conn.execute(
            "INSERT OR REPLACE INTO topic_content (topic_id, title, content)"
            " VALUES (?,?,?)", (topic_id, "被动语态", content))


# ---- boost_log 事件表契约 ----

def test_record_and_item_done(env):
    assert boost.item_done("malin", TUE, MEM_REF) is False
    boost.record("malin", TUE, MEM_REF, "memory", passed=True)
    assert boost.item_done("malin", TUE, MEM_REF) is True
    assert boost.item_done("malin", THU, MEM_REF) is False  # 按日隔离
    # 重交覆盖：passed 翻 false 后同键不再算过关
    boost.record("malin", TUE, MEM_REF, "memory", passed=False)
    assert boost.item_done("malin", TUE, MEM_REF) is False


def test_day_done(env):
    boost.record("malin", TUE, MEM_REF, "memory")
    assert boost.day_done("malin", TUE, [MEM_REF]) is True
    assert boost.day_done("malin", TUE, [MEM_REF, RET_REF]) is False
    assert boost.day_done("malin", TUE, []) is False  # 空列表 False
    boost.record("malin", TUE, RET_REF, "retell", passed=True, score=8.0)
    assert boost.day_done("malin", TUE, [MEM_REF, RET_REF]) is True


def test_ts_is_utc_iso(env):
    boost.record("malin", TUE, MEM_REF, "memory")
    con = sqlite3.connect(env)
    row = con.execute(
        "SELECT ts FROM boost_log WHERE user='malin'").fetchone()
    con.close()
    datetime.fromisoformat(row[0])  # 可解析 ISO；不抛即过


# ---- week_advanced / lectures_advanced（讲次归属） ----

def test_week_advanced_memory(env):
    boost.record("malin", TUE, MEM_REF, "memory")
    assert boost.week_advanced("malin", MON, 30) is True
    assert boost.week_advanced("malin", MON, 29) is False
    assert boost.week_advanced("malin", "2026-11-16", 30) is False  # 下周不算


def test_week_advanced_retell_via_patterns(env):
    """retell 讲次归属查 patterns.get（真实句型模块，fce.db 同库）。"""
    from grammar_kb import patterns
    assert patterns.import_items(
        [{"lecture": 22, "sentence": "There is no point (in) doing…"}]) == 1
    pid = patterns.list_all(22)[0]["id"]
    boost.record("malin", TUE, f"retell:{pid}", "retell", score=8)
    assert boost.week_advanced("malin", MON, 22) is True
    assert boost.week_advanced("malin", MON, 30) is False
    assert boost.week_advanced("malin", "2026-11-16", 22) is False  # 下周不算


def test_week_advanced_retell_without_pattern_row(env):
    """retell 记录查不到句型（id 不存在）→ 不归属任何讲次，False 不抛。"""
    boost.record("malin", TUE, "retell:99999", "retell")
    assert boost.week_advanced("malin", MON, 22) is False


def test_passed_map_first_day(env):
    boost.record("malin", TUE, MEM_REF, "memory")
    boost.record("malin", THU, MEM_REF, "memory")
    m = boost.passed_map("malin", MON, "2026-11-16")
    assert m[MEM_REF] == TUE  # 最早过关日（欠账滚动判定）


# ---- today_items（retell 提交校验用） ----

def test_today_items_configured(env):
    store = _store(env)
    store.put_week(MON, {"boost_days": [2, 4], "boost_items": ITEMS}, "", "malin")
    assert boost.today_items("malin", TUE) == ITEMS
    assert boost.today_items("malin", THU) == ITEMS
    assert boost.today_items("malin", MON) == []   # 非提升日
    assert boost.today_items("malin", SAT) == []


def test_today_items_default_days(env):
    """没配 boost_days 但 items 非空 → 默认 [2,4] 生效。"""
    store = _store(env)
    store.put_week(MON, {"boost_items": ITEMS}, "", "malin")
    assert boost.today_items("malin", TUE) == ITEMS
    assert boost.today_items("malin", MON) == []


def test_today_items_unconfigured_empty(env):
    """教师没配 items：默认提升日也返回空列表（无任务可校验）。"""
    store = _store(env)
    store.put_week(MON, {}, "", "malin")
    assert boost.today_items("malin", TUE) == []


# ---- week_daily_tasks 派生 boost 行 ----

def _boost_rows(store, day):
    rows = store.week_daily_tasks(MON, for_date=day, user="malin")
    return [r for r in rows if r.get("type") == "boost"]


def test_schedule_boost_rows(env):
    store = _store(env)
    _seed_topic(store)
    store.put_week(MON, {"boost_days": [2, 4], "boost_items": ITEMS},
                   "", "malin")
    tue = _boost_rows(store, TUE)
    assert [r["key"] for r in tue] == [f"boost:{MEM_REF}", f"boost:{RET_REF}"]
    assert tue[0]["boost"] == {"item_ref": MEM_REF, "kind": "memory",
                               "configured": True}
    assert "〈被动态语态一览〉" in tue[0]["text"]  # 查到了专题节点标题
    assert tue[1]["text"] == "⭐ 提升任务"          # patterns 未部署 → 兜底文案
    assert _boost_rows(store, MON) == []           # 周一不是提升日


def test_schedule_boost_default_days(env):
    """没配 boost_days、items 非空：默认周二/周四出，周一不出。"""
    store = _store(env)
    store.put_week(MON, {"boost_items": ITEMS}, "", "malin")
    assert len(_boost_rows(store, TUE)) == 2
    assert _boost_rows(store, MON) == []


def test_schedule_boost_days_without_items(env):
    """boost_days 配了、items 空：提升日出说明行（configured=False）。"""
    store = _store(env)
    store.put_week(MON, {"boost_days": [2, 4]}, "", "malin")
    tue = _boost_rows(store, TUE)
    assert len(tue) == 1
    assert tue[0]["text"] == "⭐ 提升日（本周未配置提升任务）"
    assert tue[0]["boost"]["configured"] is False
    assert _boost_rows(store, MON) == []


def test_schedule_boost_zero_config_silent(env):
    """boost_days 与 items 都没配：完全不派生（零配置无噪音）。"""
    store = _store(env)
    store.put_week(MON, {"lectures": [29]}, "", "malin")
    for d in (MON, TUE, THU, SAT):
        assert _boost_rows(store, d) == []


def test_schedule_boost_retell_label(env):
    """retell 行文案带句型原句前 24 字（真实 patterns 模块）。"""
    from grammar_kb import patterns
    patterns.import_items(
        [{"lecture": 22,
          "sentence": "There is no point (in) arguing about it any further"}])
    pid = patterns.list_all(22)[0]["id"]
    store = _store(env)
    store.put_week(MON, {"boost_days": [2], "boost_items": [
        {"type": "retell", "pattern_id": pid}]}, "", "malin")
    tue = _boost_rows(store, TUE)
    assert tue[0]["text"].startswith("⭐ 提升：句型复述〈There is no point")


def test_schedule_boost_rolling(env):
    """欠账滚动：周二过关的项周四不再出；都没做 → 周六兜底两条都在。"""
    store = _store(env)
    store.put_week(MON, {"boost_days": [2, 4], "boost_items": ITEMS},
                   "", "malin")
    boost.record("malin", TUE, MEM_REF, "memory")
    thu = {r["key"] for r in _boost_rows(store, THU)}
    assert thu == {f"boost:{RET_REF}"}          # memory 已过关，只剩 retell
    sat = {r["key"] for r in _boost_rows(store, SAT)}
    assert sat == {f"boost:{RET_REF}"}          # 周六兜底滚入未过关项
    # 过关当天行照出（✓ 由 completion 点亮）
    tue = {r["key"] for r in _boost_rows(store, TUE)}
    assert tue == {f"boost:{MEM_REF}", f"boost:{RET_REF}"}


# ---- 完成判定对接（day_completion / _why_done） ----

def test_completion_boost_keys(env):
    store = _store(env)
    store.put_week(MON, {"boost_days": [2, 4], "boost_items": ITEMS},
                   "", "malin")
    rows = store.week_daily_tasks(MON, for_date=TUE, user="malin")
    comp = store.day_completion("malin", TUE, rows)
    k = comp["keys"][f"boost:{MEM_REF}"]
    assert k == {"done": False, "why": "提升档未完成"}
    boost.record("malin", TUE, MEM_REF, "memory")
    comp = store.day_completion("malin", TUE, rows)
    assert comp["keys"][f"boost:{MEM_REF}"] == {"done": True,
                                                "why": "提升档已完成"}


def test_completion_boost_none_row_always_done(env):
    """说明行无可做任务：恒亮不占完成率。"""
    store = _store(env)
    store.put_week(MON, {"boost_days": [2, 4]}, "", "malin")
    rows = store.week_daily_tasks(MON, for_date=TUE, user="malin")
    comp = store.day_completion("malin", TUE, rows)
    assert comp["keys"]["boost:none"] == {"done": True, "why": "boost_none"}


# ---- lecture_done 第三分支（规则开关 + WeekCtx 缓存同步） ----

def test_lecture_done_boost_gate(env):
    store = _store(env)
    boost.record("malin", TUE, MEM_REF, "memory")
    nxt = "2026-11-16"
    # 默认关：boost 不进讲次完成
    assert 30 not in store.lecture_done(MON, nxt, "malin", "lecture")
    store.set_rule("malin", "boost_gate_lecture", 1)
    assert 30 in store.lecture_done(MON, nxt, "malin", "lecture")
    # hw 口径不受影响
    assert 30 not in store.lecture_done(MON, nxt, "malin", "hw")
    # WeekCtx 缓存版同一口径（周内前缀窗口切片）
    ctx = WeekCtx(store, "malin", MON)
    assert 30 in ctx.lecture_done(TUE, nxt, "lecture")
    assert 30 not in ctx.lecture_done(MON, TUE, "lecture")  # 记录在周二
    ctx2 = WeekCtx(store, "malin", "2026-11-16")            # 越界回退直查
    assert 30 in ctx2.lecture_done(MON, nxt, "lecture")     # 直查同结果
    assert 30 not in ctx2.lecture_done(nxt, "2026-11-23", "lecture")


def test_lecture_done_boost_gate_day_window(env):
    """_auto_done_full 走的单日窗口：当天过关联讲次即点亮 lecture:{n}。"""
    store = _store(env)
    store.set_rule("malin", "boost_gate_lecture", 1)
    boost.record("malin", TUE, MEM_REF, "memory")
    auto, _titles, _acts = store._auto_done_full("malin", TUE)
    assert auto.get("lecture:30") is True


# freeze_clock 冒烟：boost 模块不依赖「今天」，只确认导入面一致
def test_boost_record_frozen_clock(env):
    with freeze_clock(datetime(2026, 11, 10, 12, 0, tzinfo=CN_TZ)):
        boost.record("malin", TUE, RET_REF, "retell", passed=True, score=7.5)
    assert boost.item_done("malin", TUE, RET_REF) is True


# ---- 完成两态（pass_state：完成 ≠ 过关） ----

def test_pass_state_two_states(env):
    """memory 打卡即 passed；retell 未批 pending、低分 graded_low、
    重交高分翻 passed（重交刷新口径）。"""
    boost.record("malin", TUE, MEM_REF, "memory")
    assert boost.pass_state("malin", TUE, MEM_REF) == "passed"
    boost.record("malin", TUE, RET_REF, "retell")
    assert boost.pass_state("malin", TUE, RET_REF) == "pending"
    boost.record("malin", TUE, RET_REF, "retell", score=5)
    assert boost.pass_state("malin", TUE, RET_REF) == "graded_low"
    boost.record("malin", TUE, RET_REF, "retell", score=8)
    assert boost.pass_state("malin", TUE, RET_REF) == "passed"


def test_pass_state_map_window(env):
    """窗内聚合取 MAX(score)；无记录的 ref 不在返回里。"""
    boost.record("malin", TUE, RET_REF, "retell", score=5)
    boost.record("malin", THU, RET_REF, "retell", score=9)
    m = boost.pass_state_map("malin", MON, "2026-11-16")
    assert m[RET_REF] == {"state": "passed", "day": TUE}
    assert MEM_REF not in m
    assert boost.pass_state_map("malin", "2026-11-16", "2026-11-20") == {}


def test_advanced_rows_retell_needs_score(env):
    """「基础点前进」过关口径：retell 未批/低分不算，memory 打卡算。"""
    from grammar_kb import patterns
    patterns.import_items(
        [{"lecture": 22, "sentence": "There is no point (in) doing…"}])
    pid = patterns.list_all(22)[0]["id"]     # 隔离库自增，不必是 12
    rref = f"retell:{pid}"
    boost.record("malin", TUE, MEM_REF, "memory")
    boost.record("malin", TUE, rref, "retell")
    rows = boost.advanced_rows("malin", MON, "2026-11-16")
    assert (30, TUE) in rows
    assert all(r[0] != 22 for r in rows)   # retell 未批不归属
    boost.record("malin", TUE, rref, "retell", score=6)
    assert all(r[0] != 22 for r in boost.advanced_rows("malin", MON, "2026-11-16"))
    boost.record("malin", TUE, rref, "retell", score=7)
    assert (22, TUE) in boost.advanced_rows("malin", MON, "2026-11-16")


def test_schedule_boost_retell_low_score_rolls(env):
    """欠账新口径：retell 未批（pending）不重派；批 <7 分回流下一提升日。"""
    from grammar_kb import patterns
    patterns.import_items(
        [{"lecture": 30, "sentence": "The window was broken yesterday"}])
    pid = patterns.list_all(30)[0]["id"]
    rref = f"retell:{pid}"
    store = _store(env)
    store.put_week(MON, {"boost_days": [2, 4], "boost_items": [
        {"type": "retell", "pattern_id": pid}]}, "", "malin")
    # 周二提交未批：周四不重派（pending 不欠账）
    boost.record("malin", TUE, rref, "retell")
    assert _boost_rows(store, THU) == []
    # 批 5 分：graded_low → 周四回流同一任务
    boost.record("malin", TUE, rref, "retell", score=5)
    thu = _boost_rows(store, THU)
    assert [r["key"] for r in thu] == [f"boost:{rref}"]
    # 周四重交 9 分：过关，周六兜底不再出
    boost.record("malin", THU, rref, "retell", score=9)
    assert _boost_rows(store, SAT) == []

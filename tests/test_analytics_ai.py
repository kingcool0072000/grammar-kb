"""学情分析 AI 周报测试：周窗口 / 四源聚合 / 生成与存档 / 端点权限。"""
import json
import os
from datetime import date, datetime, timedelta

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb import glm  # noqa: E402
from grammar_kb.analytics_ai import AnalyticsAI, last_week_window  # noqa: E402
from grammar_kb.server import create_app  # noqa: E402

MON = date(2026, 9, 7)   # 周一
SUN = date(2026, 9, 13)  # 周日
LAST_MON = date(2026, 8, 31)


# ---- 周窗口 ----

def test_window_regular_day():
    # 周三触发 → 上一自然周
    assert last_week_window(date(2026, 9, 9)) == (LAST_MON, date(2026, 9, 6))


def test_window_sunday_returns_current_week():
    # 周日触发 → 本周刚收官，分析本周
    assert last_week_window(SUN) == (MON, SUN)


def test_window_monday_returns_previous_week():
    assert last_week_window(MON) == (LAST_MON, date(2026, 9, 6))


# ---- 聚合（fake stores） ----

class FakeStore:
    def __init__(self, rows, date_field):
        self.rows = rows
        self.date_field = date_field

    def list(self, limit=1000, **kw):
        return self.rows

    list_recordings = list


def _ts(day, hh=10):
    """九月某日某时的时间戳。"""
    return f"2026-09-{day:02d}T{hh:02d}:00:00"


def _fake_stores():
    exams = FakeStore([
        {"lecture": 27, "date": "2026-09-08", "score": 60, "wrong": [1, 5]},
        {"lecture": 28, "date": "2026-09-10", "score": 55, "wrong": [2]},
        # 上上周的，不应计入
        {"lecture": 26, "date": "2026-08-30", "score": 90, "wrong": []},
    ], "date")
    fce = FakeStore([
        {"test_id": 1, "part": 3, "paper": "Reading", "status": "auto",
         "auto_score": 7, "total": 8, "teacher_score": None, "created_at": _ts(9)},
        {"test_id": 1, "part": 4, "paper": "Listening", "status": "pending",
         "auto_score": None, "total": 10, "teacher_score": None, "created_at": _ts(10)},
        {"test_id": 2, "part": 1, "paper": "Reading", "status": "graded",
         "auto_score": None, "total": 8, "teacher_score": 6, "created_at": _ts(11)},
    ], "created_at")
    reading = FakeStore([
        {"article_id": 3, "article_title": "T1P7-A", "status": "graded",
         "teacher_score": 8, "duration_sec": 95, "created_at": _ts(11, 20)},
        {"article_id": 4, "article_title": "T2P1-B", "status": "pending",
         "teacher_score": None, "duration_sec": 60, "created_at": _ts(12)},
    ], "created_at")
    recite = FakeStore([
        {"user": "malin", "total": 30, "wrong": 6, "acc": 80, "duration_sec": 120,
         "mode": "spell", "scope": "all", "created_at": _ts(8, 19)},
        {"user": "malin", "total": 20, "wrong": 2, "acc": 90, "duration_sec": 90,
         "mode": "flip", "scope": "verb", "created_at": _ts(12, 19)},
    ], "created_at")
    return exams, fce, reading, recite


def test_collect_stats_aggregates_week_and_baseline(tmp_path):
    ai = AnalyticsAI(tmp_path / "a.db")
    ex, fce, rec, rts = _fake_stores()
    stats = ai.collect_stats(ex, fce, rec, rts, (MON, SUN))
    w = stats["week"]
    assert w["range"] == "2026-09-07 ~ 2026-09-13"
    assert w["exams"]["count"] == 2 and w["exams"]["avg"] == 57.5
    assert w["exams"]["items"][0]["wrong"] == [1, 5]
    assert w["fce"]["count"] == 3 and w["fce"]["auto_acc_pct"] == 88.0  # 7/8（单题先取整）
    assert w["reading_aloud"]["count"] == 2 and w["reading_aloud"]["avg_score_10"] == 8.0
    assert w["recite"]["count"] == 2 and w["recite"]["avg_acc_pct"] == 85.0
    assert w["recite"]["total_word_attempts"] == 50
    # 基线：紧邻前 4 周（含 08-30 那次哈一 90 分）
    keys = [b["week"] for b in stats["baseline_prev4"]]
    assert keys == ["2026-08-10", "2026-08-17", "2026-08-24", "2026-08-31"]
    # 08-30（周日）落在 08-24 那周
    assert stats["baseline_prev4"][2]["exams"] == 1
    assert stats["baseline_prev4"][2]["exam_avg"] == 90.0


def test_generate_weekly_no_key_raises(tmp_path):
    ai = AnalyticsAI(tmp_path / "a.db")
    with pytest.raises(glm.GlmError) as ei:
        ai.generate_weekly({"ai": {"apiKey": "", "model": "glm-4-flash"}})
    assert ei.value.status == 400


def test_generate_weekly_stores_report(tmp_path, monkeypatch):
    monkeypatch.setattr(
        glm, "chat_prose",
        lambda *a, **k: "## 总体态势\n上周稳中有升。",
    )
    ai = AnalyticsAI(tmp_path / "a.db")
    ex, fce, rec, rts = _fake_stores()
    rep = ai.generate_weekly(
        {"ai": {"apiKey": "sk-test", "model": "glm-5.3-flash"}},
        window=(MON, SUN),
        stores={"exams": ex, "fce_submissions": fce, "reading": rec, "recite": rts},
    )
    assert rep["weekKey"] == "2026-09-07"
    assert rep["content"].startswith("## 总体态势")
    assert rep["model"] == "glm-5.3-flash"
    assert ai.get_report("2026-09-07")["content"] == rep["content"]
    assert len(ai.list_reports()) == 1


# ---- 端点与权限 ----

def _mk_client(tmp_path, monkeypatch, role):
    os.environ["GRAMMAR_KB_USERS"] = str(tmp_path / "users.json")
    os.environ["GRAMMAR_KB_AUTH_SECRET"] = str(tmp_path / "secret.key")
    os.environ["GRAMMAR_KB_LIBRARY_DB"] = str(tmp_path / "library.db")
    os.environ["GRAMMAR_KB_LIBRARY_DIR"] = str(tmp_path / "files")
    os.environ["GRAMMAR_KB_EXAM_DB"] = str(tmp_path / "exam.db")  # 隔离报告表写入
    monkeypatch.setattr(glm, "chat_prose", lambda *a, **k: "## 总体态势\n测试报告。")
    app = create_app()
    with TestClient(app) as c:
        # 测试环境全新 users.json 播种默认密码
        r = c.post("/auth/login", json={"user": role, "password": "123456"})
        assert r.status_code == 200, r.text
        c.headers.update({"Authorization": "Bearer " + r.json()["data"]["token"]})
        yield c


@pytest.fixture()
def teacher_c(tmp_path, monkeypatch):
    yield from _mk_client(tmp_path, monkeypatch, "teacher")


@pytest.fixture()
def student_c(tmp_path, monkeypatch):
    yield from _mk_client(tmp_path, monkeypatch, "malin")


def test_endpoints_teacher_flow(teacher_c):
    # 未配 key → 400 提示去泛读馆设置
    r = teacher_c.post("/analytics/ai/weekly")
    assert r.status_code == 400
    assert "泛读馆设置" in r.json()["message"]
    # 配 key（走泛读馆设置接口）→ 生成成功
    r = teacher_c.put("/library/settings", json={"ai": {"apiKey": "sk-test", "model": "glm-5.3-flash"}})
    assert r.status_code == 200, r.text
    r = teacher_c.post("/analytics/ai/weekly")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["weekKey"] and data["content"]
    # 列表能读到
    r = teacher_c.get("/analytics/ai/reports")
    assert r.status_code == 200
    reports = r.json()["data"]["reports"]
    assert len(reports) >= 1 and reports[0]["weekKey"] == data["weekKey"]


def test_endpoints_student_forbidden(student_c):
    assert student_c.post("/analytics/ai/weekly").status_code == 403
    assert student_c.get("/analytics/ai/reports").status_code == 403

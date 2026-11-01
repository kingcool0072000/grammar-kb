"""学情月报端点 tests：GET /analytics/summary。

tmp 建四库（表结构由 create_app 里的各 Store 按生产 DDL 建），环境变量在
create_app 前设好（同 test_server / test_phase23_endpoints 先例）。时间统一
freeze 在北京时间 2026-11-01 10:00，落库时间戳故意混用
``...Z`` / ``...+00:00`` / ``YYYY-MM-DD HH:MM:SS`` 三种写法验证 datetime()
归一口径；北京 2026-10 月 = UTC [2026-09-30 16:00, 2026-10-31 16:00)。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime

import pytest

# 需 server 依赖；缺失则整体跳过
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb import analytics  # noqa: E402
from grammar_kb.clock import freeze_clock  # noqa: E402
from grammar_kb.server import create_app  # noqa: E402
from grammar_kb.server.routes_analytics import cache_clear  # noqa: E402

# 10 月作业卷：72+65+80+66 = 283 / 4 → 70.8；9 月：60+75+73 = 208 / 3 → 69.3
ExamRow = tuple[str, int, str, int, str, str]

_EXAM_ROWS: list[ExamRow] = [
    ("malin", 26, "2026-10-06", 72, json.dumps(list(range(10))), "hw"),
    ("malin", 27, "2026-10-11", 65, "[]", "hw"),
    ("malin", 26, "2026-10-20", 80, "[1,2,3]", "hw"),
    ("malin", 28, "2026-10-25", 66, "[9,8]", "hw"),
    ("malin", 25, "2026-09-10", 60, "[]", "hw"),
    ("malin", 26, "2026-09-20", 75, "[]", "hw"),
    ("malin", 24, "2026-09-25", 73, "[1]", "hw"),
    ("malin", 1, "2026-11-02", 100, "[]", "hw"),   # 10 月窗口外（次月）
    ("other", 26, "2026-10-06", 100, "[]", "hw"),  # 其他学生
]

# (user, total, wrong, wrong_words, created_at)——含前后边界各一条
_RECITE_SESSIONS = [
    ("malin", 10, 2, '["regard","adopt"]', "2026-10-02T01:21:46Z"),        # 北京 10-02
    ("malin", 10, 0, "[]", "2026-10-03T18:00:00+00:00"),                   # 北京 10-04
    ("malin", 10, 1, '["regard"]', "2026-09-30 16:00:00"),                 # 边界内：北京 10-01 00:00
    ("malin", 10, 0, '["egg"]', "2026-10-31 15:59:59"),                    # 边界内：北京 10-31 23:59
    ("malin", 99, 99, '["SHOULD_NOT_APPEAR"]', "2026-10-31 16:00:00"),     # 边界外：北京 11-01
    ("malin", 10, 3, '["oldword"]', "2026-09-15T10:00:00"),                # 上月
    ("other", 10, 0, '["x"]', "2026-10-02T08:00:00"),                      # 其他学生
]

# (user, word, stage, next_review, conquered_at)
_REVIEW_ROWS = ([("malin", f"w{i:03d}", 1, "2026-10-10", "") for i in range(150)]
                + [("malin", "w150", 2, "2026-11-05", ""),     # 未到期
                   ("malin", "w151", 0, "2026-10-15", ""),     # 逾期
                   ("malin", "w152", 3, None, "2026-10-20"),   # 已攻克（next_review=NULL，生产口径）
                   ("malin", "w153", 1, "2026-10-01", "")])    # 逾期

_PROGRESS_ROWS = [  # (word, mastered_at)；累计 3、10 月新增 2
    ("able", "2026-10-05T00:00:00Z"),
    ("above", "2026-10-20T00:00:00+00:00"),
    ("abroad", "2026-09-10T00:00:00"),
    ("act", None),
]

# (session_id, book_id, book_title, total_sec, active_sec, created_at, module, ps, pe)
_FOCUS_ROWS = [
    ("f1", 1, "Percy Jackson and the Lightning Thief", 600, 600, "2026-10-02T02:00:00Z", "library", 30.0, 35.0),
    ("f2", 1, "Percy Jackson and the Lightning Thief", 300, 300, "2026-10-09T02:00:00", "library", 35.0, 40.0),
    ("f3", 1, "Percy Jackson and the Lightning Thief", 120, 120, "2026-10-16T02:00:00", "library", 40.0, 45.0),
    ("f4", 1, "Percy Jackson and the Lightning Thief", 180, 180, "2026-10-23T02:00:00", "library", 45.0, 50.0),
    ("f5", 1, "Percy Jackson and the Lightning Thief", 30, 30, "2026-10-26T02:00:00", "library", 50.0, 50.0),   # 无效: <60s
    ("f6", 1, "Percy Jackson and the Lightning Thief", 620, 5, "2026-10-27T02:00:00", "library", 50.0, 50.0),   # 无效: <10s
    ("f7", 1, "Percy Jackson and the Lightning Thief", 600, 600, "2026-09-20T02:00:00", "library", 25.0, 30.0),  # 上月
    ("f8", None, "Article X", 240, 240, "2026-10-05T02:00:00", "reading", 0.0, 10.0),  # 非 library 模块
    ("f9", 1, "Percy Jackson and the Lightning Thief", 600, 600, "2026-10-02T02:00:00", "library", 0.0, 5.0),  # 其他学生
]

# (id, user, article_id, teacher_score, created_at)
_RECORDING_ROWS = [
    (1, "malin", 1, 6, "2026-10-08T02:00:00"),
    (2, "malin", 1, 4, "2026-10-15T02:00:00"),
] + [(i, "malin", 1, 5, f"2026-09-{d:02d}T02:00:00") for i, d in zip(range(3, 8), range(2, 7))]

_RETELL_ROWS = [(1, "malin", "v1", 5, "2026-10-09T02:00:00"),
                (2, "malin", "v2", 7, "2026-09-09T02:00:00")]

# (user, test_id, paper, part, auto, total, created_at)
_FCE_ROWS = [
    ("malin", 1, "Reading and Use of English", 1, 3, 8, "2026-10-05T02:00:00"),
    ("malin", 1, "Reading and Use of English", 2, 4, 8, "2026-10-06T02:00:00"),
    ("malin", 1, "Listening", 1, 2, 6, "2026-10-07T02:00:00"),
    ("malin", 1, "Reading and Use of English", 1, 8, 8, "2026-09-05T02:00:00"),  # 上月
]

_TOPIC_CONTENT = [
    ("tense-26-28", "时态专题", json.dumps(
        {"nodes": [{"key": "n1"}, {"key": "n2"}, {"key": "n3"}]})),
    ("vocab-scope", "词汇专题", json.dumps({"nodes": [{"key": "v1"}, {"key": "v2"}]})),
    ("sep-topic", "九月专题", json.dumps({"nodes": [{"key": "s1"}]})),
]

_TOPIC_PROGRESS = [  # (topic_id, done_keys, completed_at)
    ("tense-26-28", '["n1","n2","n3"]', "2026-10-15T10:00:00"),  # 完成，落月
    ("vocab-scope", '["v1"]', "2026-10-18T10:00:00"),            # 未盖全节点：不算完成
    ("no-such-content", '["z1"]', "2026-10-19T10:00:00"),        # 无内容：不算完成
    ("sep-topic", '["s1"]', "2026-09-19T15:24:28"),              # 上月完成
]


def _seed(paths: dict[str, str]) -> None:
    """各 Store 已在 create_app 里按生产 DDL 建表，这里只插最小数据集。"""

    def cx(name):
        conn = sqlite3.connect(paths[name])
        conn.row_factory = sqlite3.Row
        return conn

    exam = cx("exam")
    exam.executemany(
        "INSERT INTO exam_records (user, lecture, date, score, wrong, kind)"
        " VALUES (?, ?, ?, ?, ?, ?)", _EXAM_ROWS)
    exam.commit()
    exam.close()

    fce = cx("fce")
    fce.executemany(
        "INSERT INTO recite_sessions (user, total, wrong, wrong_words, created_at)"
        " VALUES (?, ?, ?, ?, ?)", _RECITE_SESSIONS)
    fce.executemany(
        "INSERT INTO recite_review (user, word, stage, next_review, conquered_at)"
        " VALUES (?, ?, ?, ?, ?)", _REVIEW_ROWS)
    fce.executemany(
        "INSERT INTO recite_word_progress (user, word, mastered_at) VALUES ('malin', ?, ?)",
        _PROGRESS_ROWS)
    fce.executemany(
        "INSERT INTO focus_sessions (session_id, user, book_id, book_title, total_sec,"
        " active_sec, created_at, module, percent_start, percent_end)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(sid, ("malin" if sid != "f9" else "other"), bid, bt, ts, as_, at, md, ps, pe)
         for (sid, bid, bt, ts, as_, at, md, ps, pe) in _FOCUS_ROWS])
    fce.executemany(
        "INSERT INTO reading_recordings (id, user, article_id, audio_b64,"
        " teacher_score, created_at) VALUES (?, ?, ?, 'x', ?, ?)", _RECORDING_ROWS)
    fce.executemany(
        "INSERT INTO video_retell (id, user, vid, audio_b64, teacher_score, created_at)"
        " VALUES (?, ?, ?, 'x', ?, ?)", _RETELL_ROWS)
    fce.executemany(
        "INSERT INTO fce_submission (user, test_id, paper, part, auto_score, total,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", _FCE_ROWS)
    fce.executemany(
        "INSERT INTO topic_content (topic_id, title, content) VALUES (?, ?, ?)",
        _TOPIC_CONTENT)
    fce.executemany(
        "INSERT INTO topic_progress (user, topic_id, done_keys, completed_at)"
        " VALUES ('malin', ?, ?, ?)", _TOPIC_PROGRESS)
    fce.commit()
    fce.close()

    lib = cx("library")
    lib.execute("INSERT INTO books (id, title, file_path) VALUES"
                " (1, 'Percy Jackson and the Lightning Thief', 'x.epub')")
    lib.execute("INSERT INTO reading_progress (book_id, user, percent, updated_at)"
                " VALUES (1, 'malin', 50.0, '2026-10-30T10:00:00')")
    lib.executemany("INSERT INTO chapters (book_id, idx, word_count) VALUES (1, ?, ?)",
                    [(0, 12000), (1, 12000)])  # 全书 24000 词
    lib.commit()
    lib.close()

    gram = cx("grammar")
    gram.executemany("INSERT INTO lecture (number, title) VALUES (?, ?)",
                     [(24, "被动语态1"), (25, "动词时态3"), (26, "动词时态4"),
                      (27, "动词时态5"), (28, "综合复习六")])
    gram.commit()
    gram.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    p = {
        "fce": str(tmp_path / "fce.db"),
        "exam": str(tmp_path / "exam.db"),
        "grammar": str(tmp_path / "grammar.db"),
        "library": str(tmp_path / "library.db"),
    }
    # 环境变量必须在 create_app 之前设（Store 构造时读环境定路径）
    monkeypatch.setenv("GRAMMAR_KB_FCE_DB", p["fce"])
    monkeypatch.setenv("GRAMMAR_KB_EXAM_DB", p["exam"])
    monkeypatch.setenv("GRAMMAR_KB_LIBRARY_DB", p["library"])
    monkeypatch.setenv("GRAMMAR_KB_DB", p["grammar"])
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(tmp_path / "secret.key"))

    app = create_app(p["grammar"])
    _seed(p)  # 表已由各 Store 建好，补数据
    cache_clear()

    with freeze_clock(datetime(2026, 11, 1, 10, 0)):  # 北京 2026-11-01 10:00
        with TestClient(app) as c:
            login = c.post("/auth/login", json={"user": "teacher", "password": "123456"})
            assert login.status_code == 200, login.text
            c.headers["Authorization"] = f"Bearer {login.json()['data']['token']}"
            yield c


def _summary(client, **params):
    r = client.get("/analytics/summary", params=params or {"user": "malin", "month": "2026-10"})
    assert r.status_code == 200, r.text
    return r.json()["data"]


# ---------------------------------------------------------------- 月份口径

def test_month_param_validation(client):
    assert client.get("/analytics/summary", params={"user": "malin", "month": "2026-13"}).status_code == 422
    assert client.get("/analytics/summary", params={"user": "malin", "month": "abc"}).status_code == 422
    assert client.get("/analytics/summary", params={"user": "malin", "month": "2026-1"}).status_code == 422


def test_month_defaults_to_current_beijing_month(client):
    data = _summary(client, user="malin")  # 冻结在今天 2026-11-01
    assert data["month"] == "2026-11"
    # 11 月窗口：10-31 16:00 UTC（北京 11-01 00:00）的会话归 11 月；11-02 的卷子归 11 月
    assert data["cards"]["recite_days"]["value"] == 1
    assert data["cards"]["score"]["count"] == 1
    assert data["cards"]["score"]["value"] == 100.0


def test_prev_month_is_previous_calendar_month():
    assert analytics.prev_month("2026-10") == "2026-09"
    assert analytics.prev_month("2026-01") == "2025-12"


def test_month_utc_bounds():
    assert analytics.month_utc_bounds("2026-10") == (
        "2026-09-30 16:00:00", "2026-10-31 16:00:00")


# ---------------------------------------------------------------- 卡片与月过滤

def test_score_card_and_scores_list(client):
    data = _summary(client)
    score = data["cards"]["score"]
    assert score["label"] == "作业卷均分"
    assert score["value"] == 70.8 and score["count"] == 4
    assert score["prev_value"] == 69.3 and score["prev_count"] == 3
    assert data["scores"][0] == {
        "date": "2026-10-06", "lecture": 26, "title": "动词时态4",
        "kind": "hw", "score": 72, "wrong_count": 10,
    }
    assert len(data["scores"]) == 4


def test_recite_cards_month_filter(client):
    data = _summary(client)
    # 学习日（北京）：10-01 / 10-02 / 10-04 / 10-31；边界外（北京 11-01）与上月、他人不计
    assert data["cards"]["recite_days"] == {"label": "背词学习日", "value": 4, "prev_value": 1}
    # (40-3)/40 = 92.5%；SHOULD_NOT_APPEAR 那条必须被边界挡掉
    assert data["cards"]["recite_accuracy"] == {"label": "背词正确率%", "value": 92.5}
    assert data["daily"]["recite_days"] == ["2026-10-01", "2026-10-02", "2026-10-04", "2026-10-31"]
    assert data["wrong_words"] == [
        {"word": "regard", "count": 2}, {"word": "adopt", "count": 1}, {"word": "egg", "count": 1}]


def test_gaps(client):
    gaps = _summary(client)["daily"]["gaps"]
    assert gaps == [
        {"from": "2026-10-03", "to": "2026-10-03", "days": 1},
        {"from": "2026-10-05", "to": "2026-10-30", "days": 26},
    ]  # 末段无断档：最后学习日 10-31 = min(今天 11-01, 月末 10-31)


def test_mastered_and_backlog(client):
    data = _summary(client)
    assert data["cards"]["mastered"] == {"label": "累计掌握词", "value": 3, "delta_month": 2}
    # 积压 = 150 + w151 + w153（next_review<=今天 2026-11-01）；in_cycle = 全量 154
    bl = data["cards"]["backlog"]
    assert bl["label"] == "复习积压"
    assert bl["value"] == 152 and bl["in_cycle"] == 154
    assert bl["stages"] == [{"stage": 0, "count": 1}, {"stage": 1, "count": 151},
                            {"stage": 2, "count": 1}, {"stage": 3, "count": 1}]


def test_extensive_card(client):
    card = _summary(client)["cards"]["extensive"]
    assert card["label"] == "泛读"
    assert card["sessions"] == 6
    assert card["minutes"] == 31          # (600+300+120+180+30+620)/60 = 30.8 → 31
    assert card["invalid_pct"] == 33      # 2/6（<60s 与 active<10s 各一条）
    assert card["book"] == "Percy Jackson and the Lightning Thief"
    assert card["progress_pct"] == 50.0
    # 进度差 50-30=20% × 全书 24000 词 = 4800（整百）
    assert card["month_words"] == 4800


def test_intensive_video_fce_topics(client):
    cards = _summary(client)["cards"]
    assert cards["intensive"] == {"label": "精读录音", "recordings": 2,
                                  "avg_score": 5.0, "prev_recordings": 5}
    assert cards["video"] == {"label": "视频复述", "recordings": 1,
                              "avg_score": 5.0, "prev_recordings": 1}
    assert cards["fce"] == {"label": "FCE 客观题", "submissions": 3, "correct": 9,
                            "total": 22, "parts_done": 3, "parts_total": 14}
    assert cards["topics"]["label"] == "专题完成"
    assert cards["topics"]["items"] == [
        {"topic_id": "tense-26-28", "title": "时态专题", "nodes_total": 3, "nodes_done": 3}]


# ---------------------------------------------------------------- flags

def test_flags_all_five_rules_fire(client):
    flags = _summary(client)["flags"]
    by_key = {f["key"]: f for f in flags}
    assert by_key["backlog"]["level"] == "red"
    assert "152" in by_key["backlog"]["message"]
    assert by_key["gaps"]["level"] == "red"
    assert "26" in by_key["gaps"]["message"]
    assert by_key["fce"]["level"] == "red"
    assert "9/22" in by_key["fce"]["message"]
    assert by_key["intensive"]["level"] == "warn"
    assert "2" in by_key["intensive"]["message"] and "5" in by_key["intensive"]["message"]
    assert by_key["score"]["level"] == "warn"
    assert "70.8" in by_key["score"]["message"]


# ---------------------------------------------------------------- 缓存

def test_cache_hits_within_ttl(client):
    first = _summary(client)
    assert first["cards"]["score"]["value"] == 70.8
    # 第二次调用前清库：命中缓存 → 数字不变（不再打库）
    con = sqlite3.connect(os.environ["GRAMMAR_KB_EXAM_DB"])
    con.execute("DELETE FROM exam_records")
    con.commit()
    con.close()
    assert _summary(client)["cards"]["score"]["value"] == 70.8
    # 手动清缓存后重算 → 全零（exam_records 已清空，当月上月都无卷）
    cache_clear()
    data = _summary(client)
    assert data["cards"]["score"] == {"label": "作业卷均分", "value": 0.0, "count": 0,
                                      "prev_value": 0.0, "prev_count": 0}


# ---------------------------------------------------------------- 健壮性

def test_empty_month_zero_values_no_500(client):
    data = _summary(client, user="malin", month="2026-05")
    assert data["month"] == "2026-05"
    assert data["cards"]["score"]["count"] == 0
    assert data["cards"]["recite_days"]["value"] == 0
    assert data["cards"]["recite_accuracy"]["value"] == 0.0
    assert data["cards"]["mastered"]["delta_month"] == 0
    assert data["cards"]["extensive"]["sessions"] == 0
    assert data["cards"]["extensive"]["book"] is None
    assert data["cards"]["intensive"]["recordings"] == 0
    assert data["cards"]["fce"] == {"label": "FCE 客观题", "submissions": 0, "correct": 0,
                                    "total": 0, "parts_done": 0, "parts_total": 14}
    assert data["cards"]["topics"]["items"] == []
    assert data["scores"] == [] and data["wrong_words"] == []
    # 整月零学习日 → 一条整月断档
    assert data["daily"]["gaps"] == [{"from": "2026-05-01", "to": "2026-05-31", "days": 31}]


def test_student_forbidden(client):
    login = client.post("/auth/login", json={"user": "malin", "password": "123456"})
    student = login.json()["data"]["token"]
    r = client.get("/analytics/summary", params={"user": "malin", "month": "2026-10"},
                   headers={"Authorization": f"Bearer {student}"})
    assert r.status_code == 403

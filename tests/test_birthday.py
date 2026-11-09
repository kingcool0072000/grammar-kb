"""学生生日：按北京时间，不看服务器 UTC，也不改数据库。"""
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from grammar_kb.birthday import (  # noqa: E402
    birthdays,
    greeting_for,
    is_birthday,
    parse_mmdd,
)
from grammar_kb.clock import freeze_clock  # noqa: E402
from grammar_kb.server import create_app  # noqa: E402

UTC = timezone.utc


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    """不读仓库 data/birthdays.json，也不吃外部环境变量。"""
    monkeypatch.setenv("GRAMMAR_KB_BIRTHDAYS_FILE", str(tmp_path / "birthdays.json"))
    monkeypatch.delenv("GRAMMAR_KB_BIRTHDAYS", raising=False)


def test_parse_mmdd():
    assert parse_mmdd("11-10") == "11-10"
    assert parse_mmdd(" 02-29 ") == "02-29"
    assert parse_mmdd("02-30") is None
    assert parse_mmdd("04-31") is None
    assert parse_mmdd("13-01") is None
    assert parse_mmdd("11-10-2026") is None
    assert parse_mmdd("2026-11-10") is None


def test_malin_defaults_to_nov_10():
    assert birthdays()["malin"] == "11-10"
    with freeze_clock(datetime(2026, 11, 10, 9, 0)):
        assert is_birthday("malin") is True
        assert is_birthday("mxy") is False
    with freeze_clock(datetime(2026, 11, 9, 23, 30)):
        assert is_birthday("malin") is False


def test_follows_shanghai_not_utc():
    """UTC 仍是 11-09 时北京已是 11-10；UTC 还在 11-10 时北京已是 11-11。"""
    # UTC 11-09 16:30 = 北京 11-10 00:30
    with freeze_clock(datetime(2026, 11, 9, 16, 30, tzinfo=UTC)):
        greet = greeting_for("malin", "student")
        assert greet["date"] == "2026-11-10"
        assert greet["birthday"] is True
        assert greet["title"] == "malin，生日快乐！🎂"
        assert "好好学" in greet["message"]

    # 北京 11-09 23:59（UTC 11-09 15:59）还不是生日
    with freeze_clock(datetime(2026, 11, 9, 15, 59, tzinfo=UTC)):
        greet = greeting_for("malin", "student")
        assert greet["date"] == "2026-11-09"
        assert greet["birthday"] is False
        assert greet["title"] == ""
        assert greet["message"] == ""

    # UTC 11-10 16:30 = 北京 11-11 00:30，不能再用 UTC 的「10 日」祝福
    with freeze_clock(datetime(2026, 11, 10, 16, 30, tzinfo=UTC)):
        greet = greeting_for("malin", "student")
        assert greet["date"] == "2026-11-11"
        assert greet["birthday"] is False
        assert greet["title"] == ""


def test_teacher_never_greeted_even_if_configured(monkeypatch):
    monkeypatch.setenv("GRAMMAR_KB_BIRTHDAYS", "teacher=11-10")
    with freeze_clock(datetime(2026, 11, 10, 8, 0)):
        assert greeting_for("teacher", "teacher")["birthday"] is False
        assert greeting_for("malin", "teacher")["birthday"] is False
        assert greeting_for("malin", "student")["birthday"] is True


def test_file_then_env_override(monkeypatch, tmp_path):
    path = tmp_path / "birthdays.json"
    path.write_text('{"mxy": "03-21", "malin": "01-02"}', encoding="utf-8")
    monkeypatch.setenv("GRAMMAR_KB_BIRTHDAYS_FILE", str(path))
    found = birthdays()
    assert found["mxy"] == "03-21"
    assert found["malin"] == "01-02"

    monkeypatch.setenv("GRAMMAR_KB_BIRTHDAYS", "malin=11-10,mxy=-")
    found = birthdays()
    assert found["malin"] == "11-10"
    assert "mxy" not in found

    monkeypatch.setenv("GRAMMAR_KB_BIRTHDAYS", "malin=13-40")
    assert birthdays()["malin"] == "01-02"  # 非法项跳过，文件里的值还在


def test_broken_file_keeps_default(monkeypatch, tmp_path):
    path = tmp_path / "birthdays.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("GRAMMAR_KB_BIRTHDAYS_FILE", str(path))
    assert birthdays() == {"malin": "11-10"}


@pytest.fixture()
def api(monkeypatch, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("GRAMMAR_KB_DATA_DIR", str(data))
    monkeypatch.setenv("GRAMMAR_KB_USERS", str(data / "users.json"))
    monkeypatch.setenv("GRAMMAR_KB_AUTH_SECRET", str(data / "secret.key"))
    app = create_app(str(data / "grammar.db"))
    with TestClient(app) as client:
        yield client


def _auth(client, user, password):
    login = client.post("/auth/login", json={"user": user, "password": password})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['data']['token']}"}


def test_endpoint_requires_login(api):
    assert api.get("/me/birthday").status_code == 401


def test_endpoint_uses_shanghai_date(api):
    headers = _auth(api, "malin", "123456")
    with freeze_clock(datetime(2026, 11, 9, 16, 30, tzinfo=UTC)):
        body = api.get("/me/birthday", headers=headers).json()
        assert body["code"] == 0
        data = body["data"]
        assert data["birthday"] is True
        assert data["date"] == "2026-11-10"
        assert data["role"] == "student"
        assert data["user"] == "malin"
    with freeze_clock(datetime(2026, 11, 10, 16, 30, tzinfo=UTC)):
        data = api.get("/me/birthday", headers=headers).json()["data"]
        assert data["date"] == "2026-11-11"
        assert data["birthday"] is False
        assert data["title"] == ""


def test_endpoint_other_student_and_teacher_silent(api):
    with freeze_clock(datetime(2026, 11, 10, 12, 0)):
        mxy = api.get("/me/birthday", headers=_auth(api, "mxy", "mxy123")).json()["data"]
        assert mxy["birthday"] is False
        assert mxy["user"] == "mxy"
        teacher = api.get(
            "/me/birthday", headers=_auth(api, "teacher", "123456")
        ).json()["data"]
        assert teacher["birthday"] is False
        assert teacher["role"] == "teacher"
        assert teacher["title"] == ""

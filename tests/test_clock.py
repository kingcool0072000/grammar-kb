"""北京时间时钟：不跟进程时区，测试可以钉死。"""
from datetime import datetime, timedelta, timezone

from grammar_kb.clock import CN_TZ, cn_date, cn_dt, current_monday, freeze_clock, now_cn, today_cn


def test_cn_dt_expression():
    assert cn_dt("created_at") == "datetime(julianday(created_at) + 8.0/24)"
    assert cn_date("mastered_at") == "date(julianday(mastered_at) + 8.0/24)"


def test_today_follows_beijing_not_utc():
    # 北京 10-24 01:30 仍是 UTC 10-23。
    when = datetime(2026, 10, 24, 1, 30, tzinfo=CN_TZ)
    with freeze_clock(when):
        assert today_cn().isoformat() == "2026-10-24"
        assert current_monday().isoformat() == "2026-10-19"
        assert now_cn() == when


def test_naive_when_is_beijing():
    with freeze_clock(datetime(2026, 1, 5, 9, 0)):
        assert today_cn().isoformat() == "2026-01-05"
        assert current_monday().isoformat() == "2026-01-05"  # 周一


def test_freeze_restores():
    before = now_cn()
    with freeze_clock(datetime(2020, 1, 1, 8, tzinfo=CN_TZ)):
        assert today_cn().isoformat() == "2020-01-01"
    after = now_cn()
    assert abs((after - before).total_seconds()) < 5
    assert after.tzinfo == CN_TZ
    assert after.utcoffset() == timedelta(hours=8)


def test_freeze_converts_other_zones():
    utc = timezone(timedelta(0))
    # UTC 10-23 16:30 = 北京 10-24 00:30
    with freeze_clock(datetime(2026, 10, 23, 16, 30, tzinfo=utc)) as frozen:
        assert frozen.hour == 0 and frozen.day == 24
        assert today_cn().isoformat() == "2026-10-24"

from datetime import date, datetime, timezone

from fvg_bot.clock import ET
from fvg_bot.data.store import (
    SessionDay,
    bars_path,
    cached_days,
    has_day,
    load_1m_bars,
    load_calendar,
    parse_rfc3339,
    write_calendar,
    write_day,
)

DAY = date(2026, 9, 28)
RAW = [
    {"t": "2026-09-28T13:30:00Z", "o": 571.1, "h": 571.5, "l": 570.9, "c": 571.4, "v": 1000, "n": 50, "vw": 571.2},
    {"t": "2026-09-28T13:31:00Z", "o": 571.4, "h": 571.6, "l": 571.0, "c": 571.2, "v": 800, "n": 40, "vw": 571.3},
]


def test_parse_rfc3339_handles_z_and_nanoseconds():
    assert parse_rfc3339("2026-09-28T13:30:00Z") == datetime(2026, 9, 28, 13, 30, tzinfo=timezone.utc)
    ts = parse_rfc3339("2026-09-28T13:30:00.123456789Z")
    assert ts.microsecond == 123456
    assert parse_rfc3339("2026-09-28T09:30:00-04:00") == datetime(2026, 9, 28, 9, 30, tzinfo=ET)


def test_day_roundtrip(tmp_path):
    assert not has_day(tmp_path, "spy", DAY, "sip")
    path = write_day(tmp_path, "spy", DAY, "sip", RAW)
    assert path == tmp_path / "stocks" / "1min" / "sip" / "SPY" / "2026-09-28.csv"
    assert has_day(tmp_path, "SPY", DAY, "sip")
    assert not list(path.parent.glob("*.tmp"))

    bars = load_1m_bars(tmp_path, "SPY", DAY)
    assert len(bars) == 2
    assert bars[0].ts == datetime(2026, 9, 28, 9, 30, tzinfo=ET)
    assert bars[0].ts.tzinfo is not None
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close) == (571.1, 571.5, 570.9, 571.4)


def test_feeds_are_stored_separately(tmp_path):
    write_day(tmp_path, "SPY", DAY, "iex", RAW)
    assert has_day(tmp_path, "SPY", DAY, "iex")
    assert not has_day(tmp_path, "SPY", DAY, "sip")
    assert bars_path(tmp_path, "SPY", DAY, "iex") != bars_path(tmp_path, "SPY", DAY, "sip")


def test_cached_days(tmp_path):
    assert cached_days(tmp_path, "SPY") == []
    write_day(tmp_path, "SPY", date(2026, 9, 29), "sip", RAW)
    write_day(tmp_path, "SPY", DAY, "sip", RAW)
    assert cached_days(tmp_path, "SPY") == [DAY, date(2026, 9, 29)]


def test_calendar_merge(tmp_path):
    def day(d, close_h):
        return SessionDay(d, datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET), datetime(d.year, d.month, d.day, close_h, 0, tzinfo=ET))

    assert load_calendar(tmp_path) == {}
    write_calendar(tmp_path, [day(date(2026, 11, 27), 13)])
    write_calendar(tmp_path, [day(date(2026, 11, 25), 16), day(date(2026, 11, 27), 13)])
    cal = load_calendar(tmp_path)
    assert sorted(cal) == [date(2026, 11, 25), date(2026, 11, 27)]
    assert cal[date(2026, 11, 27)].close == datetime(2026, 11, 27, 13, 0, tzinfo=ET)

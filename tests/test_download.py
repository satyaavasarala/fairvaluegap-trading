from datetime import date, datetime, timedelta, timezone

from fvg_bot.clock import ET
from fvg_bot.data.download import download_stock_bars, main
from fvg_bot.data.store import SessionDay, cached_days, load_1m_bars, load_calendar, write_day

# Mon 2026-11-23 .. Fri 2026-11-27, Thanksgiving (26th) closed, 27th closes at 13:00.
TRADING = [date(2026, 11, 23), date(2026, 11, 24), date(2026, 11, 25), date(2026, 11, 27)]


def session(d):
    close = 13 if d == date(2026, 11, 27) else 16
    return SessionDay(d, datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET), datetime(d.year, d.month, d.day, close, tzinfo=ET))


def bar_for(d, hh=9, mm=30):
    ts = datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)
    return {"t": ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 10, "n": 1, "vw": 1.2}


class FakeClient:
    def __init__(self, empty_days=()):
        self.bar_requests = []
        self.calendar_requests = []
        self.empty_days = set(empty_days)

    def calendar(self, start, end):
        self.calendar_requests.append((start, end))
        return [session(d) for d in TRADING if start <= d <= end]

    def stock_bars(self, symbol, start, end, feed):
        self.bar_requests.append((symbol, start, end, feed))
        rows = []
        d = start.date()
        while d <= end.date():
            if d in TRADING and d not in self.empty_days:
                # premarket bar at 04:00 and a late after-hours bar at 19:59, both same ET day
                rows += [bar_for(d, 4, 0), bar_for(d), bar_for(d, 19, 59)]
            d += timedelta(days=1)
        return rows


TODAY = date(2026, 12, 1)


def run(tmp_path, client, start=date(2026, 11, 23), end=date(2026, 11, 27), symbols=("SPY",)):
    return download_stock_bars(client, tmp_path, list(symbols), start, end, today=TODAY, log=lambda m: None)


def test_downloads_each_trading_day_once(tmp_path):
    c = FakeClient()
    assert run(tmp_path, c) == {"SPY": 4}
    assert cached_days(tmp_path, "SPY") == TRADING
    assert len(load_1m_bars(tmp_path, "SPY", date(2026, 11, 24))) == 3
    assert load_calendar(tmp_path)[date(2026, 11, 27)].close.hour == 13

    c2 = FakeClient()
    assert run(tmp_path, c2) == {"SPY": 0}
    assert c2.bar_requests == []


def test_only_missing_days_are_fetched(tmp_path):
    write_day(tmp_path, "SPY", date(2026, 11, 23), "sip", [bar_for(date(2026, 11, 23))])
    write_day(tmp_path, "SPY", date(2026, 11, 24), "sip", [bar_for(date(2026, 11, 24))])
    c = FakeClient()
    assert run(tmp_path, c) == {"SPY": 2}
    (_, start, end, _), = c.bar_requests
    assert start.date() == date(2026, 11, 25)
    assert end.date() == date(2026, 11, 27)
    assert len(load_1m_bars(tmp_path, "SPY", date(2026, 11, 23))) == 1


def test_empty_day_is_not_cached(tmp_path):
    c = FakeClient(empty_days={date(2026, 11, 25)})
    assert run(tmp_path, c) == {"SPY": 3}
    assert date(2026, 11, 25) not in cached_days(tmp_path, "SPY")


def test_end_clamped_before_today(tmp_path):
    c = FakeClient()
    download_stock_bars(c, tmp_path, ["SPY"], date(2026, 11, 23), date(2026, 11, 27), today=date(2026, 11, 25), log=lambda m: None)
    assert cached_days(tmp_path, "SPY") == [date(2026, 11, 23), date(2026, 11, 24)]


def test_calendar_requested_per_year(tmp_path):
    c = FakeClient()
    download_stock_bars(c, tmp_path, ["SPY"], date(2025, 12, 30), date(2026, 1, 2), today=TODAY, log=lambda m: None)
    assert c.calendar_requests == [
        (date(2025, 12, 30), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 1, 2)),
    ]


def test_multiple_symbols_and_feeds(tmp_path):
    c = FakeClient()
    download_stock_bars(c, tmp_path, ["SPY", "QQQ"], date(2026, 11, 23), date(2026, 11, 24), feed="iex", today=TODAY, log=lambda m: None)
    assert cached_days(tmp_path, "QQQ", "iex") == TRADING[:2]
    assert cached_days(tmp_path, "SPY", "sip") == []
    assert {r[3] for r in c.bar_requests} == {"iex"}


def test_main_requires_keys(monkeypatch, capsys):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    assert main(["--start", "2026-11-23"]) == 2
    assert "APCA_API_KEY_ID" in capsys.readouterr().err

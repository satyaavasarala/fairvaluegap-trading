import json
from datetime import date, datetime
from urllib.parse import parse_qs, urlparse

import pytest

from fvg_bot.clock import ET
from fvg_bot.data.alpaca_history import AlpacaHistory, AlpacaHTTPError


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        status, body = self.responses.pop(0)
        return status, json.dumps(body).encode() if not isinstance(body, bytes) else body


def client(http, sleeps=None):
    sleeps = sleeps if sleeps is not None else []
    return AlpacaHistory("KEY", "SECRET", http_get=http, sleep=sleeps.append, monotonic=lambda: 0.0)


def params(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


START = datetime(2026, 9, 28, 0, 0, tzinfo=ET)
END = datetime(2026, 9, 28, 23, 59, 59, tzinfo=ET)


def test_stock_bars_paginates_and_sends_expected_params():
    http = FakeHttp([
        (200, {"bars": {"SPY": [{"t": "a"}]}, "next_page_token": "tok"}),
        (200, {"bars": {"SPY": [{"t": "b"}]}, "next_page_token": None}),
    ])
    rows = client(http).stock_bars("SPY", START, END, feed="sip")
    assert rows == [{"t": "a"}, {"t": "b"}]

    url, headers = http.calls[0]
    assert urlparse(url).path == "/v2/stocks/bars"
    p = params(url)
    assert p["symbols"] == "SPY"
    assert p["timeframe"] == "1Min"
    assert p["feed"] == "sip"
    assert p["adjustment"] == "raw"
    assert p["limit"] == "10000"
    assert p["start"] == "2026-09-28T04:00:00Z"
    assert p["end"] == "2026-09-29T03:59:59Z"
    assert "page_token" not in p
    assert params(http.calls[1][0])["page_token"] == "tok"
    assert set(headers) == {"APCA-API-KEY-ID", "APCA-API-SECRET-KEY"}


def test_empty_bars_payload():
    http = FakeHttp([(200, {"bars": None, "next_page_token": None})])
    assert client(http).stock_bars("SPY", START, END, feed="sip") == []


def test_retries_on_429_then_succeeds():
    sleeps = []
    http = FakeHttp([(429, b"slow down"), (503, b"busy"), (200, {"bars": {}, "next_page_token": None})])
    client(http, sleeps).stock_bars("SPY", START, END, feed="sip")
    assert len(http.calls) == 3
    assert 1 in sleeps and 2 in sleeps


def test_non_retryable_error_raises_without_leaking_keys():
    http = FakeHttp([(403, b'{"message":"subscription does not permit querying recent SIP data"}')])
    with pytest.raises(AlpacaHTTPError) as e:
        client(http).stock_bars("SPY", START, END, feed="sip")
    assert e.value.status == 403
    assert "subscription" in str(e.value)
    assert "SECRET" not in str(e.value)


def test_gives_up_after_max_retries():
    http = FakeHttp([(429, b"x")] * 6)
    with pytest.raises(AlpacaHTTPError):
        client(http).stock_bars("SPY", START, END, feed="sip")
    assert len(http.calls) == 6


def test_throttle_spaces_requests():
    sleeps = []
    clock = iter([0.0, 0.1, 0.35])
    http = FakeHttp([(200, {"bars": {}, "next_page_token": None})] * 2)
    c = AlpacaHistory("K", "S", http_get=http, sleep=sleeps.append, monotonic=lambda: next(clock))
    c.stock_bars("SPY", START, END, feed="sip")
    c.stock_bars("SPY", START, END, feed="sip")
    assert sleeps == [pytest.approx(0.25)]


def test_calendar():
    http = FakeHttp([(200, {"market": {}, "calendar": [
        {"date": "2026-11-27", "core_start": "2026-11-27T09:30:00-05:00", "core_end": "2026-11-27T13:00:00-05:00"},
    ]})])
    days = client(http).calendar(date(2026, 11, 27), date(2026, 11, 27))
    assert urlparse(http.calls[0][0]).path == "/v3/calendar/NYSE"
    assert days[0].day == date(2026, 11, 27)
    assert days[0].close == datetime(2026, 11, 27, 13, 0, tzinfo=ET)

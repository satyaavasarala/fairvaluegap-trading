from datetime import date, datetime, timedelta

import pytest

from fvg_bot.backtest.levels import day_levels, level_study
from fvg_bot.backtest.rebalance import OPEN, WIN, WINDOWS
from fvg_bot.clock import ET
from fvg_bot.strategy.types import Bar, Direction

D1, D2 = date(2026, 9, 24), date(2026, 9, 25)


def at(d, h, m):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=ET)


def flat(d, h, m, price, n=1):
    return [Bar(at(d, h, m) + timedelta(minutes=i), price, price, price, price) for i in range(n)]


# Day 1 regular session: high 101, low 99 (PDH/PDL). After hours high 100.3, low 100.
DAY1 = (
    flat(D1, 9, 30, 100.0, 30)
    + [Bar(at(D1, 10, 0), 100.0, 101.0, 100.0, 100.0), Bar(at(D1, 10, 1), 100.0, 100.0, 99.0, 100.0)]
    + [Bar(at(D1, 16, 30), 100.0, 100.3, 100.0, 100.0)]
)
# Day 2: premarket 100.1-100.4, so ONH 100.4 and ONL 100.0 (prior post-market low).
# 09:45 sets sides at 100.5; 09:46 rallies into PDH 101 (fade short); 09:47 drops to 100.0,
# crossing ONH 100.4 and reaching ONL 100.0 from above (fade longs); then flat.
DAY2 = (
    [Bar(at(D2, 8, 0), 100.2, 100.4, 100.1, 100.2)]
    + flat(D2, 9, 30, 100.5, 16)
    + [Bar(at(D2, 9, 46), 100.5, 101.0, 100.5, 101.0), Bar(at(D2, 9, 47), 101.0, 101.0, 100.0, 100.0)]
    + flat(D2, 9, 48, 100.0, 30)
)
BARS = {D1: DAY1, D2: DAY2}


def run(stop=0.50, window=WINDOWS["full"]):
    return level_study("SPY", [D1, D2], BARS.__getitem__, window, stop, "full")


def test_day_levels():
    assert day_levels(DAY1[:32], DAY1[32:] + DAY2[:1]) == {"PDH": 101.0, "PDL": 99.0, "ONH": 100.4, "ONL": 100.0}
    assert day_levels([], []) == {}


def test_first_day_has_no_levels_and_touches_fade():
    r = run()
    assert r.sessions == 2
    assert [t.kind for t in r.touches] == ["PDH", "ONH", "ONL"]
    pdh, onh, onl = r.touches
    assert all(t.day == D2 for t in r.touches)

    assert pdh.direction is Direction.BEARISH
    assert pdh.entry_price == pytest.approx(101.0)
    assert pdh.stop_level == pytest.approx(101.5)
    assert pdh.brackets[1] == WIN and pdh.brackets[2] == WIN
    assert pdh.bracket_r[2] == pytest.approx(2.0)

    assert onh.direction is Direction.BULLISH
    assert onh.entry_price == pytest.approx(100.4)
    assert onh.stop_level == pytest.approx(99.9)
    assert onh.brackets[1] == OPEN
    assert onh.bracket_r[1] == pytest.approx(-0.8)  # marked at 100.0 at the end of data

    assert onl.direction is Direction.BULLISH
    assert onl.entry_price == pytest.approx(100.0)


def test_stop_size_scales_r():
    pdh = run(stop=1.00).touches[0]
    assert pdh.stop_level == pytest.approx(102.0)
    assert pdh.brackets[1] == WIN
    assert pdh.bracket_r[1] == pytest.approx(1.0)
    assert pdh.brackets[2] == OPEN


def test_touches_outside_window_are_ignored():
    r = level_study("SPY", [D1, D2], BARS.__getitem__, (WINDOWS["full"][0].replace(hour=10), WINDOWS["full"][1]), 0.5)
    assert r.touches == []

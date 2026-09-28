from datetime import datetime, time, timedelta

import pytest

from fvg_bot.backtest.rebalance import LOSS, OPEN, WIN, WINDOWS, Touch, bracket_rate, report_rows, study
from fvg_bot.clock import ET
from fvg_bot.strategy.types import Bar, Direction
from tests.test_fsm import ZONE_ROWS
from tests.test_replay import D, at, bucket

# Bullish 15m FVG [99.5, 100.6] confirmed at 10:15. Entering at 100.6 with a 0.03 buffer
# puts the stop at 99.47: stop distance 1.13.
ENTRY, STOP, DIST = 100.6, 99.47, 1.13


def day(after, flat_minutes=70):
    bars = []
    for i, row in enumerate(ZONE_ROWS):
        bars += bucket(at(9, 30) + timedelta(minutes=15 * i), *row)
    for i, (o, h, l, c) in enumerate(after):
        bars.append(Bar(at(10, 15) + timedelta(minutes=i), o, h, l, c))
    last = bars[-1]
    bars += [
        Bar(last.ts + timedelta(minutes=i), last.close, last.close, last.close, last.close)
        for i in range(1, flat_minutes)
    ]
    return bars


TOUCH_BAR = (101.0, 101.0, 100.5, 100.8)  # dips into the zone top from above


def run(after, window=WINDOWS["full"], **kw):
    bars = day(after)
    return study("SPY", [D], lambda d: bars, window, "full", **kw)


def zone_touches(r):
    """Touches of the fixture zone; the synthetic tail can form further 15m FVGs of its own."""
    return [t for t in r.touches if (t.zone_bottom, t.zone_top) == (99.5, 100.6)]


def test_first_touch_enters_with_far_edge_stop():
    r = run([TOUCH_BAR, (100.8, 101.8, 100.8, 101.75)])
    (t,) = zone_touches(r)
    assert t.direction is Direction.BULLISH
    assert t.entry_price == pytest.approx(ENTRY)
    assert t.stop_level == pytest.approx(STOP)
    assert t.stop_distance == pytest.approx(DIST)
    assert at(10, 15) < t.entry_at < at(10, 16)
    assert t.brackets == {1: WIN, 2: OPEN, 4: OPEN}
    assert t.horizons[15] == pytest.approx((101.75 - ENTRY) / DIST)
    assert t.horizons[60] == pytest.approx((101.75 - ENTRY) / DIST)


def test_stop_resolves_every_bracket():
    r = run([TOUCH_BAR, (100.8, 100.8, 99.4, 99.45)])
    (t,) = zone_touches(r)
    assert t.brackets == {1: LOSS, 2: LOSS, 4: LOSS}
    assert t.horizons[15] == pytest.approx((99.45 - ENTRY) / DIST)


def test_zone_is_entered_once():
    r = run([TOUCH_BAR, (100.8, 101.0, 100.4, 100.9), (100.9, 101.0, 100.3, 100.9)])
    assert len(zone_touches(r)) == 1


def test_gap_through_zone_is_not_an_entry():
    r = run([(99.0, 99.2, 98.9, 99.1)])
    assert zone_touches(r) == []
    assert r.gapped_through == 1


def test_touch_outside_window_is_ignored():
    r = run([TOUCH_BAR, (100.8, 101.8, 100.8, 101.75)], window=(time(10, 30), time(11, 30)))
    assert zone_touches(r) == []


def test_flatten_leaves_brackets_open():
    close = datetime(2026, 9, 28, 10, 40, tzinfo=ET)  # flatten at 10:25
    r = run([TOUCH_BAR], session_close=lambda d: close)
    (t,) = zone_touches(r)
    assert t.brackets == {1: OPEN, 2: OPEN, 4: OPEN}
    assert t.horizons[15] is None


def _touch(result_1r):
    t = Touch("SPY", D, Direction.BULLISH, 1.0, 2.0, at(10, 0), 2.0, 0.97)
    t.brackets = {1: result_1r, 2: LOSS, 4: LOSS}
    t.horizons = {15: 0.5, 60: None}
    return t


def test_bracket_rate_excludes_open():
    touches = [_touch(WIN), _touch(WIN), _touch(LOSS), _touch(OPEN)]
    p, ci, n, open_share = bracket_rate(touches, 1)
    assert n == 3
    assert p == pytest.approx(2 / 3)
    assert open_share == pytest.approx(0.25)
    assert ci > 0


def test_report_rows_formats():
    assert "n=   0" in report_rows("x", [])
    row = report_rows("SPY long ", [_touch(WIN), _touch(LOSS)])
    assert "1R  50%" in row and "+15m +0.50" in row

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


def test_bracket_r_records_exit_and_mark():
    (t,) = zone_touches(run([TOUCH_BAR, (100.8, 101.8, 100.8, 101.75)]))
    assert t.bracket_r[1] == pytest.approx((101.73 - ENTRY) / DIST)
    assert t.bracket_r[4] == pytest.approx((101.75 - ENTRY) / DIST)
    assert t.net_r(1, 0.02) == pytest.approx(t.bracket_r[1] - 0.02 / DIST)


def test_stop_floor_skips_tight_zones():
    r = run([TOUCH_BAR, (100.8, 101.8, 100.8, 101.75)], min_stop=2.0)
    assert zone_touches(r) == []
    assert r.skipped_tight >= 1


def test_session_bar():
    from fvg_bot.backtest.rebalance import session_bar

    b = session_bar([Bar(at(9, 30), 1.0, 2.0, 0.5, 1.5), Bar(at(9, 31), 1.5, 3.0, 1.0, 2.5)])
    assert (b.ts, b.open, b.high, b.low, b.close) == (at(9, 30), 1.0, 3.0, 0.5, 2.5)


def test_daily_zone():
    # Daily bars (h, l): (100, 99), (102, 100.5), (103, 101) form a bullish daily FVG [100, 101]
    # at day 3's close. Day 4 dips into it from 101.5.
    from datetime import date as _date

    days = [_date(2026, 9, 21), _date(2026, 9, 22), _date(2026, 9, 23), _date(2026, 9, 24)]
    ranges = [(100.0, 99.0), (102.0, 100.5), (103.0, 101.0)]

    def t(d, h, m):
        return datetime(d.year, d.month, d.day, h, m, tzinfo=ET)

    data = {}
    for d, (hi, lo) in zip(days, ranges):
        data[d] = [Bar(t(d, 9, 30), lo, hi, lo, hi)] + [Bar(t(d, 9, 31 + i), hi, hi, hi, hi) for i in range(5)]
    d4 = days[3]
    data[d4] = [Bar(t(d4, 9, 45), 101.5, 101.5, 101.5, 101.5), Bar(t(d4, 9, 46), 101.5, 101.5, 100.8, 100.9)] + [
        Bar(t(d4, 9, 47 + i), 100.9, 100.9, 100.9, 100.9) for i in range(5)
    ]
    r = study("SPY", days, data.__getitem__, WINDOWS["full"], "full", zone_tf="1d", min_stop=0.5)
    (touch,) = r.touches
    assert touch.kind == "fvg_1d"
    assert touch.day == d4
    assert touch.direction is Direction.BULLISH
    assert (touch.zone_bottom, touch.zone_top) == (100.0, 101.0)
    assert touch.entry_price == pytest.approx(101.0)
    assert touch.stop_level == pytest.approx(99.97)


def test_hourly_zone_kind_and_bad_tf():
    r = study("SPY", [D], lambda d: day([TOUCH_BAR]), WINDOWS["full"], "full", zone_tf="60m")
    assert all(t.kind == "fvg_60m" for t in r.touches)
    with pytest.raises(ValueError):
        study("SPY", [D], lambda d: [], WINDOWS["full"], zone_tf="4h")

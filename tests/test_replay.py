from datetime import date, datetime, timedelta

import pytest

from fvg_bot.backtest.replay import replay
from fvg_bot.clock import ET
from fvg_bot.config import Config
from fvg_bot.strategy.types import Bar, Direction
from tests.bars import SETUP_SCENARIO, make_bars
from tests.test_fsm import ZONE_ROWS

D = date(2026, 9, 28)


def at(h, m):
    return datetime(2026, 9, 28, h, m, tzinfo=ET)


def bucket(start, o, h, l, c):
    """15 one-minute bars whose 15m aggregate is (o, h, l, c)."""
    return [Bar(start, o, h, l, c)] + [
        Bar(start + timedelta(minutes=i), c, c, c, c) for i in range(1, 15)
    ]


def day_bars(after_arm):
    """09:30-10:15 builds the bullish 15m FVG [99.5, 100.6]; the canonical 1m scenario runs
    10:15-10:22 and arms at 10:23 (entry 101.3, stop 100.97); `after_arm` rows follow."""
    bars = []
    for i, row in enumerate(ZONE_ROWS):
        bars += bucket(at(9, 30) + timedelta(minutes=15 * i), *row)
    bars += make_bars(SETUP_SCENARIO + after_arm, start=at(10, 15))
    last = bars[-1]
    bars += [
        Bar(last.ts + timedelta(minutes=i), last.close, last.close, last.close, last.close)
        for i in range(1, 30)
    ]
    return bars


def run(after_arm, cfg=None):
    bars = day_bars(after_arm)
    return replay("SPY", [D], lambda d: bars, cfg or Config())


def test_arms_and_triggers_at_mid():
    r = run([(102.4, 102.5, 101.25, 101.4)])
    assert r.sessions == 1
    (s,) = r.setups
    assert s.direction is Direction.BULLISH
    assert s.armed_at == at(10, 23)
    assert s.entry_level == pytest.approx(101.3)
    assert s.stop_level == pytest.approx(100.97)
    assert s.triggered
    assert s.trigger_price == pytest.approx(101.3)
    assert at(10, 23) < s.trigger_at < at(10, 24)
    assert s.stop_distance == pytest.approx(0.33)
    assert r.reasons["touched 1m FVG mid"] == 1
    assert r.reasons["position closed"] == 1


def test_gap_through_mid_is_not_triggered():
    # Opens 0.2 below the mid; the gap guard allows 0.165.
    r = run([(101.1, 101.2, 101.05, 101.15)])
    (s,) = r.setups
    assert not s.triggered
    assert s.stop_distance == pytest.approx(0.33)
    assert r.reasons["gap guard: first touch too far through mid"] == 1


def test_no_retrace_is_not_triggered():
    r = run([(102.4, 102.6, 102.3, 102.5)])
    (s,) = r.setups
    assert not s.triggered


def test_quiet_day_has_no_setups():
    bars = [Bar(at(9, 30) + timedelta(minutes=i), 100.0, 100.0, 100.0, 100.0) for i in range(180)]
    r = replay("SPY", [D], lambda d: bars, Config())
    assert r.setups == []
    assert r.reasons["entry window closed"] == 1


def test_no_days():
    assert replay("SPY", [], lambda d: [], Config()).sessions == 0

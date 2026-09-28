from datetime import datetime, timedelta

import pytest

from fvg_bot.backtest.outcome import (
    EOD,
    STOP,
    TIME,
    TP,
    CostModel,
    TradeTracker,
    exit_rules,
)
from fvg_bot.clock import ET
from fvg_bot.config import Config
from fvg_bot.options.sizing import COST_FILTER, MIN_STOP
from fvg_bot.strategy.setup import find_setup
from fvg_bot.strategy.types import Direction
from tests.bars import SETUP_SCENARIO, make_bars

COSTS = CostModel(spread=0.02, delta=0.60, exit_pad=0.02)
T0 = datetime(2026, 9, 28, 10, 23, 40, tzinfo=ET)
FLATTEN = datetime(2026, 9, 28, 15, 45, tzinfo=ET)
# entry 101.3, stop 100.97: d = 0.33, R_prem = 0.33 * 0.6 + 0.04 = 0.238
# premium target move = 4 * 0.238 / 0.6 = 1.5867; underlying target move = 4 * 0.33 = 1.32


def tracker(direction=Direction.BULLISH, entry=101.3, stop=100.97):
    return TradeTracker(direction, T0, entry, stop, FLATTEN, exit_rules(), COSTS, 4.0)


def test_cost_model():
    assert COSTS.round_trip == pytest.approx(0.04)
    assert COSTS.r_prem(0.33) == pytest.approx(0.238)
    assert COSTS.net_r(-0.33, 0.33) == pytest.approx(-1.0)
    assert CostModel.from_config(Config(), 0.01, 0.6).exit_pad == Config().exit_slippage_pad


def test_exit_rule_names():
    names = [r.name for r in exit_rules()]
    assert names == ["prem_30", "prem_45", "prem_60", "prem_none", "und_30", "und_45", "und_60", "und_none"]


def test_underlying_target_hits_before_premium_target():
    t = tracker()
    assert not t.on_tick(T0 + timedelta(minutes=1), 102.62)
    assert t.outcomes["und_45"].reason == TP
    assert t.outcomes["und_45"].net_r == pytest.approx((1.32 * 0.6 - 0.04) / 0.238)
    assert t.outcomes["und_45"].gross_r == pytest.approx(4.0)
    assert "prem_45" not in t.outcomes
    assert t.on_tick(T0 + timedelta(minutes=2), 102.89)
    assert t.outcomes["prem_45"].reason == TP
    assert t.outcomes["prem_45"].net_r == pytest.approx((1.59 * 0.6 - 0.04) / 0.238)


def test_stop_through_level_resolves_everything():
    t = tracker()
    assert not t.on_tick(T0 + timedelta(seconds=5), 100.97)  # at the level is not through
    assert t.on_tick(T0 + timedelta(seconds=6), 100.96)
    assert {o.reason for o in t.outcomes.values()} == {STOP}
    assert t.outcomes["prem_45"].net_r == pytest.approx((-0.34 * 0.6 - 0.04) / 0.238)


def test_time_stops_resolve_independently():
    t = tracker()
    t.on_tick(T0 + timedelta(minutes=30), 101.5)
    assert t.outcomes["prem_30"].reason == TIME
    assert t.outcomes["prem_30"].move == pytest.approx(0.2)
    assert "prem_45" not in t.outcomes
    t.on_tick(T0 + timedelta(minutes=45), 101.4)
    assert t.outcomes["prem_45"].reason == TIME
    assert "prem_none" not in t.outcomes


def test_eod_flatten():
    t = tracker()
    t.on_tick(FLATTEN, 101.0)
    assert {o.reason for o in t.outcomes.values()} == {EOD}


def test_bearish_direction():
    t = tracker(Direction.BEARISH, entry=98.7, stop=99.03)
    t.on_tick(T0 + timedelta(minutes=1), 97.38)
    assert t.outcomes["und_45"].reason == TP
    assert t.outcomes["und_45"].move == pytest.approx(1.32)
    t.on_tick(T0 + timedelta(minutes=2), 99.04)
    assert t.outcomes["prem_45"].reason == STOP


def test_close_out():
    t = tracker()
    t.close_out(T0 + timedelta(hours=1), 101.1)
    assert t.done
    assert t.outcomes["und_none"].reason == EOD


def test_entry_check_uses_real_sizing():
    setup = find_setup(make_bars(SETUP_SCENARIO), Direction.BULLISH, 2, 5, as_of=7)
    check = COSTS.entry_check(Config())
    d = check(setup, setup.entry_level, T0)  # d 0.33: cost 0.04 > 0.15 * 0.238
    assert not d.ok and d.reason == COST_FILTER
    d = check(setup, setup.stop_level + 0.05, T0)
    assert not d.ok and d.reason == MIN_STOP
    ok = CostModel(0.0, 0.6, 0.0).entry_check(Config())(setup, setup.entry_level, T0)
    assert ok.ok and ok.plan.contracts >= 1

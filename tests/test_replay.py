from datetime import date, datetime, timedelta

import pytest

from fvg_bot.backtest.outcome import STOP, TP, CostModel, exit_rules
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
    assert r.reasons["touched LTF FVG mid"] == 1
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


COSTS = CostModel(spread=0.02, delta=0.60, exit_pad=0.02)
TRIGGER_BAR = (102.4, 102.5, 101.25, 101.4)


def run_tracked(after_trigger, cfg=None, check=None):
    bars = day_bars([TRIGGER_BAR] + after_trigger)
    kwargs = {"entry_check": check} if check else {}
    return replay("SPY", [D], lambda d: bars, cfg or Config(), exit_rules=exit_rules(), costs=COSTS, **kwargs)


def test_tracked_trade_hits_both_targets():
    # Underlying target 101.3 + 1.32 = 102.62; premium target 101.3 + 1.5867 = 102.8867.
    (s,) = run_tracked([(101.4, 103.0, 101.4, 102.95)]).setups
    assert s.outcomes["und_45"].reason == TP
    assert s.outcomes["und_45"].exit_price == pytest.approx(102.62)
    assert s.outcomes["prem_45"].reason == TP
    assert s.outcomes["prem_45"].exit_price == pytest.approx(102.89)
    assert len(s.outcomes) == 8


def test_tracked_trade_stopped():
    (s,) = run_tracked([(101.4, 101.4, 100.9, 100.95)]).setups
    assert {o.reason for o in s.outcomes.values()} == {STOP}
    assert s.outcomes["prem_45"].exit_price == pytest.approx(100.96)


def test_open_trade_closed_out_at_end_of_data():
    (s,) = run_tracked([]).setups
    assert s.outcomes["prem_none"].reason == "EOD"


def test_filtered_replay_rejects_on_cost():
    # d 0.33 -> R 0.238; cost 0.04 exceeds 15% of R, so the arm check fails.
    r = run_tracked([], check=COSTS.entry_check(Config()))
    assert r.setups == []
    assert r.reasons["arm check failed: round-trip cost exceeds cost filter"] == 1


def test_swing_stop_and_keep_armed_variants():
    cfg = Config(stop_mode="swing", abort_on_new_extreme=False)
    bars = day_bars([(102.4, 103.2, 102.3, 103.1), (103.1, 103.1, 101.25, 101.4)])
    r = replay("SPY", [D], lambda d: bars, cfg)
    (s,) = r.setups
    assert s.stop_level == pytest.approx(99.8 - 0.03)
    assert s.triggered
    assert s.trigger_price == pytest.approx(101.3)


def test_exit_rules_require_costs():
    with pytest.raises(ValueError):
        replay("SPY", [D], lambda d: [], Config(), exit_rules=exit_rules())


def minutes_bucket(start, n, o, h, l, c):
    """n one-minute bars whose aggregate is (o, h, l, c): the move happens in the first minute."""
    return [Bar(start, o, h, l, c)] + [
        Bar(start + timedelta(minutes=i), c, c, c, c) for i in range(1, n)
    ]


def test_five_minute_ltf():
    # Same rows as the 1m scenario, one per 5m bar from 10:15: arms at the 10:50 bar close
    # (10:55) and triggers inside the 10:55 bar.
    bars = []
    for i, row in enumerate(ZONE_ROWS):
        bars += minutes_bucket(at(9, 30) + timedelta(minutes=15 * i), 15, *row)
    for i, row in enumerate(SETUP_SCENARIO + [TRIGGER_BAR]):
        bars += minutes_bucket(at(10, 15) + timedelta(minutes=5 * i), 5, *row)
    bars += minutes_bucket(bars[-1].ts + timedelta(minutes=1), 20, *(bars[-1].close,) * 4)

    r = replay("SPY", [D], lambda d: bars, Config(ltf_minutes=5))
    (s,) = r.setups
    assert s.armed_at == at(10, 55)
    assert s.entry_level == pytest.approx(101.3)
    assert s.stop_level == pytest.approx(100.97)
    assert s.triggered
    assert at(10, 55) < s.trigger_at < at(10, 56)
    assert r.reasons["LTF ChoCh"] == 1


def test_ltf_must_divide_15():
    with pytest.raises(ValueError):
        Config(ltf_minutes=4)

import pytest

from fvg_bot.config import Config
from fvg_bot.options.sizing import (
    COST_FILTER,
    LOSS_CAP,
    MIN_STOP,
    RISK_BUDGET,
    exit_levels,
    round_trip_cost,
    size_trade,
)

CFG = Config()
# stop distance 0.40, delta 0.60, spread 0.02 + exit pad 0.02 -> R_prem 0.28
ENTRY, STOP, DELTA, BID, ASK = 100.40, 100.00, 0.60, 2.00, 2.02


def test_round_trip_cost_is_spread_plus_pad():
    assert round_trip_cost(2.00, 2.02, 0.02) == pytest.approx(0.04)


def test_basic_sizing():
    s, reason = size_trade(ENTRY, STOP, DELTA, BID, ASK, CFG)
    assert reason == ""
    assert s.stop_distance == pytest.approx(0.40)
    assert s.round_trip_cost == pytest.approx(0.04)
    assert s.r_prem == pytest.approx(0.28)
    assert s.r_dollars == pytest.approx(28.0)
    assert s.contracts == 1


def test_put_delta_sign_is_ignored():
    call, _ = size_trade(ENTRY, STOP, DELTA, BID, ASK, CFG)
    put, _ = size_trade(STOP, ENTRY, -DELTA, BID, ASK, CFG)
    assert put == call


def test_budget_exactly_one_r_is_one_contract():
    # 28 / (100 * 0.28) is 0.9999999999999999 in floating point.
    s, _ = size_trade(ENTRY, STOP, DELTA, BID, ASK, Config(risk_budget=28.0))
    assert s.contracts == 1


def test_budget_scales_contracts():
    s, _ = size_trade(ENTRY, STOP, DELTA, BID, ASK, Config(risk_budget=100.0, daily_loss_limit=200.0))
    assert s.contracts == 3


def test_disaster_loss_cap_reduces_contracts():
    # Worst loss per contract = 100 * (1.5 * 0.28 + 0.05) = 47 -> 100 / 47 -> 2.
    s, _ = size_trade(ENTRY, STOP, DELTA, BID, ASK, Config(risk_budget=100.0, daily_loss_limit=100.0))
    assert s.contracts == 2


def test_disaster_loss_cap_can_reject():
    s, reason = size_trade(ENTRY, STOP, DELTA, BID, ASK, Config(daily_loss_limit=40.0))
    assert s is None
    assert reason == LOSS_CAP


def test_min_stop_distance():
    s, reason = size_trade(100.05, 100.00, DELTA, BID, ASK, CFG)
    assert s is None
    assert reason == MIN_STOP


def test_cost_filter():
    # Cost 0.07 vs R 0.31: 0.07 > 0.15 * 0.31.
    s, reason = size_trade(ENTRY, STOP, DELTA, 2.00, 2.05, CFG)
    assert s is None
    assert reason == COST_FILTER


def test_risk_budget():
    # R_prem = 1.0 * 0.6 + 0.04 = 0.64 -> $64 > $30.
    s, reason = size_trade(101.00, 100.00, DELTA, BID, ASK, CFG)
    assert s is None
    assert reason == RISK_BUDGET


def test_exit_levels():
    lv = exit_levels(2.00, 0.28, CFG)
    assert lv.take_profit == pytest.approx(3.12)
    assert lv.disaster_stop == pytest.approx(1.58)


def test_spec_net_reward_example():
    # Section 3.3: d*s = 0.24, c = 0.10 -> about 3.7:1 net with costs kept inside R.
    ds, c = 0.24, 0.10
    r_prem = ds + c
    lv = exit_levels(2.00, r_prem, CFG)
    net_win = (lv.take_profit - 2.00) - c
    net_loss = ds + c
    assert net_win == pytest.approx(4 * ds + 3 * c)
    assert net_win / net_loss == pytest.approx(3.7059, abs=1e-4)


def test_target_below_3r_is_rejected():
    with pytest.raises(ValueError):
        Config(target_r=2.5)

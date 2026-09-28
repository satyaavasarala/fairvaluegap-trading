from datetime import date, datetime

from fvg_bot.backtest.htf import COST, net_rs, passes
from fvg_bot.backtest.rebalance import Touch
from fvg_bot.clock import ET
from fvg_bot.strategy.types import Direction

T = datetime(2026, 9, 28, 10, 0, tzinfo=ET)


def touches(rs, stop=0.5):
    out = []
    for r in rs:
        t = Touch("SPY", date(2026, 9, 28), Direction.BULLISH, 1.0, 1.0, T, 100.0, 100.0 - stop)
        t.bracket_r = {1: r, 2: r}
        out.append(t)
    return out


def test_net_rs_subtract_cost_in_r():
    assert net_rs(touches([1.0], stop=0.5), 1) == [1.0 - COST / 0.5]


def test_passes_needs_pooled_bound_above_zero():
    good = touches([1.0, 1.0, -1.0] * 40)  # mean +0.33R gross
    assert passes({"SPY": good, "QQQ": good}, 1)
    coin = touches([1.0, -1.0] * 60)  # mean 0 gross, negative after cost
    assert not passes({"SPY": coin, "QQQ": coin}, 1)


def test_passes_needs_every_symbol_positive():
    good = touches([1.0, 1.0, -1.0] * 200)
    bad = touches([1.0, -1.0, -1.0] * 5)
    assert not passes({"SPY": good, "QQQ": bad}, 1)


def test_passes_rejects_tiny_or_missing_samples():
    assert not passes({"SPY": touches([1.0]), "QQQ": touches([1.0])}, 1)
    assert not passes({"SPY": touches([1.0] * 10), "QQQ": []}, 1)

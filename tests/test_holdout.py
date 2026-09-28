from datetime import date, datetime

from fvg_bot.backtest.breakout import Breakout
from fvg_bot.backtest.holdout import FAIL, INCONCLUSIVE, PASS, decide, holdout_days, main
from fvg_bot.backtest.rebalance import LOSS, WIN
from fvg_bot.clock import ET
from fvg_bot.strategy.types import Direction

T = datetime(2026, 2, 2, 10, 0, tzinfo=ET)


def trades(rs):
    out = []
    for r in rs:
        t = Breakout("SPY", date(2026, 2, 2), "PDH", Direction.BULLISH, T, 100.0, 1.0)
        t.results = {o: {1: (WIN if r > 0 else LOSS, r, 0)} for o in ("worst", "heuristic", "best")}
        out.append(t)
    return out


def test_pass_needs_bound_symbols_and_stress():
    strong = trades([1.0, 1.0, -1.0] * 50)  # +0.33R gross
    assert decide({"SPY": strong, "QQQ": strong}) == PASS


def test_inconclusive_when_positive_but_weak_or_split():
    weak = trades([1.0, -1.0] * 50 + [1.0] * 4)  # small positive mean, bound below 0
    assert decide({"SPY": weak, "QQQ": weak}) == INCONCLUSIVE
    strong = trades([1.0, 1.0, -1.0] * 100)
    losing = trades([1.0, -1.0, -1.0] * 10)
    assert decide({"SPY": strong, "QQQ": losing}) == INCONCLUSIVE


def test_inconclusive_when_edge_dies_under_stress_cost():
    # +0.04R gross: positive at $0.02 (net +0.02) but negative at $0.05 (net -0.01).
    thin = trades([1.04] * 200 + [-1.0] * 0)
    for t in thin:
        t.results["worst"][1] = (WIN, 0.04, 0)
    assert decide({"SPY": thin, "QQQ": thin}) == INCONCLUSIVE


def test_fail_when_mean_not_positive():
    coin = trades([1.0, -1.0] * 50)  # 0 gross, negative after cost
    assert decide({"SPY": coin, "QQQ": coin}) == FAIL
    assert decide({"SPY": [], "QQQ": []}) == INCONCLUSIVE


def test_holdout_days_adds_one_prior_session():
    days = [date(2025, 12, 29), date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 2), date(2026, 1, 5)]
    assert holdout_days(days) == [date(2025, 12, 31), date(2026, 1, 2), date(2026, 1, 5)]


def test_main_refuses_without_confirmation(capsys):
    assert main([]) == 2
    assert "holdout" in capsys.readouterr().err

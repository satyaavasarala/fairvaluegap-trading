import pytest

from fvg_bot.backtest.d9 import PASS, budget_rows, histogram, percentile, sizing_outcomes
from fvg_bot.config import Config
from fvg_bot.options.sizing import COST_FILTER, MIN_STOP, RISK_BUDGET


def test_percentile_interpolates():
    vals = [4.0, 1.0, 3.0, 2.0]
    assert percentile(vals, 0) == 1.0
    assert percentile(vals, 100) == 4.0
    assert percentile(vals, 50) == pytest.approx(2.5)
    with pytest.raises(ValueError):
        percentile([], 50)


def test_histogram_bins_and_overflow():
    lines = histogram([0.01, 0.06, 0.07, 0.30], bin_width=0.05, cap=0.10)
    assert len(lines) == 3
    assert lines[0].endswith(" 1")
    assert lines[1].endswith(" 2")
    assert ">=" in lines[2] and lines[2].endswith(" 1")


def test_sizing_outcomes_match_size_trade_reasons():
    # spread 0.02 + pad 0.02 = 0.04 cost, delta 0.6
    out = sizing_outcomes([0.05, 0.20, 0.40, 1.00], Config(), spread=0.02, delta=0.60)
    assert out[MIN_STOP] == 1  # 0.05 < 0.10
    assert out[COST_FILTER] == 1  # 0.20: R 0.16, cost 0.04 > 0.024
    assert out[PASS] == 1  # 0.40: R 0.28
    assert out[RISK_BUDGET] == 1  # 1.00: R 0.64 -> $64 > $30


def test_budget_rows_separate_budget_from_daily_cap():
    # sd 0.80 -> R 0.52: $52/contract; disaster loss 100 * (0.78 + 0.05) = $83 > $50.
    rows = dict((b, (c, u)) for b, c, u in budget_rows([0.80], Config(), 0.02, 0.60, budgets=(50, 75)))
    assert rows[50] == (0.0, 0.0)
    assert rows[75] == (0.0, 1.0)

from datetime import datetime, timedelta

import pytest

from fvg_bot.backtest.breakout import breakout_study, resolve_bars
from fvg_bot.backtest.rebalance import LOSS, OPEN, WIN, WINDOWS
from fvg_bot.clock import ET
from fvg_bot.strategy.types import Bar, Direction
from tests.test_levels import BARS, D1, D2

T0 = datetime(2026, 9, 25, 10, 0, tzinfo=ET)
FLATTEN = datetime(2026, 9, 25, 15, 45, tzinfo=ET)
LONG, SHORT = Direction.BULLISH, Direction.BEARISH


def bars(*rows):
    return [Bar(T0 + timedelta(minutes=i), *row) for i, row in enumerate(rows)]


# Long from 100.0 with a $0.50 stop: stop 99.5, 1R target 100.5.
def test_entry_bar_with_both_extremes():
    b = bars((99.4, 100.6, 99.4, 100.2))
    assert resolve_bars(LONG, 100.0, 0.5, 1, b, FLATTEN, "worst") == (LOSS, -1.0, 0)
    assert resolve_bars(LONG, 100.0, 0.5, 1, b, FLATTEN, "best") == (WIN, 1.0, 0)


def test_entry_bar_low_before_entry_is_ignored_in_best_case_only():
    b = bars((99.4, 100.2, 99.4, 100.1), (100.1, 100.6, 100.0, 100.5))
    assert resolve_bars(LONG, 100.0, 0.5, 1, b, FLATTEN, "worst")[0] == LOSS
    assert resolve_bars(LONG, 100.0, 0.5, 1, b, FLATTEN, "best") == (WIN, 1.0, 1)


def test_entry_bar_close_beyond_stop_is_a_loss_even_in_best_case():
    b = bars((100.0, 100.2, 99.3, 99.4))
    assert resolve_bars(LONG, 100.0, 0.5, 1, b, FLATTEN, "best") == (LOSS, -1.0, 0)


def test_later_bar_with_both_extremes():
    b = bars((100.0, 100.1, 99.9, 100.0), (100.0, 100.6, 99.4, 100.0))
    assert resolve_bars(LONG, 100.0, 0.5, 1, b, FLATTEN, "worst") == (LOSS, -1.0, 1)
    assert resolve_bars(LONG, 100.0, 0.5, 1, b, FLATTEN, "best") == (WIN, 1.0, 1)


def test_gaps_exit_at_the_open():
    down = bars((100.0, 100.1, 99.9, 100.0), (99.2, 99.3, 99.0, 99.1))
    assert resolve_bars(LONG, 100.0, 0.5, 1, down, FLATTEN, "best") == (LOSS, pytest.approx(-1.6), 1)
    up = bars((100.0, 100.1, 99.9, 100.0), (100.8, 101.0, 100.7, 100.9))
    assert resolve_bars(LONG, 100.0, 0.5, 1, up, FLATTEN, "worst") == (WIN, pytest.approx(1.6), 1)


def test_two_r_target_and_short_mirror():
    b = bars((100.0, 100.1, 99.9, 100.0), (100.0, 100.9, 99.8, 100.8), (100.8, 101.0, 100.7, 100.9))
    assert resolve_bars(LONG, 100.0, 0.5, 2, b, FLATTEN, "worst") == (WIN, 2.0, 2)
    m = [Bar(x.ts, 200 - x.open, 200 - x.low, 200 - x.high, 200 - x.close) for x in b]
    assert resolve_bars(SHORT, 100.0, 0.5, 2, m, FLATTEN, "worst") == (WIN, 2.0, 2)


def test_flatten_and_end_of_data_are_open():
    b = bars((100.0, 100.1, 99.9, 100.0), (100.2, 100.3, 100.1, 100.2))
    assert resolve_bars(LONG, 100.0, 0.5, 1, b, T0 + timedelta(minutes=1), "worst") == (OPEN, pytest.approx(0.4), 1)
    assert resolve_bars(LONG, 100.0, 0.5, 1, b, FLATTEN, "worst") == (OPEN, pytest.approx(0.4), None)
    with pytest.raises(ValueError):
        resolve_bars(LONG, 100.0, 0.5, 1, b, FLATTEN, "heuristic")


def test_breakout_study_trades_through_levels():
    # Same fixture as the fade study: PDH 101 crossed upward at 09:46, ONH 100.4 and ONL 100.0
    # crossed downward at 09:47. Breakouts go with the crossing.
    r = breakout_study("SPY", [D1, D2], BARS.__getitem__, WINDOWS["full"], 0.5, "full")
    assert [t.kind for t in r.trades] == ["PDH", "ONH", "ONL"]
    pdh, onh, onl = r.trades
    assert pdh.direction is LONG and pdh.entry_price == pytest.approx(101.0)
    # 09:47 drops to 100.0: through the 100.5 stop in every ordering.
    for order in ("worst", "heuristic", "best"):
        assert pdh.results[order][1][0] == LOSS

    assert onh.direction is SHORT and onh.entry_price == pytest.approx(100.4)
    # Entry bar high 101.0 is above the 100.9 stop: worst assumes it came after the entry.
    assert onh.results["worst"][1][0] == LOSS
    assert onh.results["best"][1] == (OPEN, pytest.approx(0.8), None)
    assert onh.results["heuristic"][1][0] == OPEN
    assert onh.net_r("best", 1, 0.02) == pytest.approx(0.8 - 0.04)

    assert onl.direction is SHORT and onl.entry_price == pytest.approx(100.0)

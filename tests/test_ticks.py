from datetime import datetime, timedelta

import pytest

from fvg_bot.backtest.ticks import bar_path, synthesize_ticks
from fvg_bot.clock import ET
from fvg_bot.strategy.types import Bar

TS = datetime(2026, 9, 28, 10, 0, tzinfo=ET)


def test_heuristic_order():
    up = Bar(TS, 100.0, 100.5, 99.8, 100.3)
    down = Bar(TS, 100.3, 100.5, 99.8, 100.0)
    assert bar_path(up) == [100.0, 99.8, 100.5, 100.3]
    assert bar_path(down) == [100.3, 100.5, 99.8, 100.0]
    assert bar_path(up, "high_first") == [100.0, 100.5, 99.8, 100.3]
    with pytest.raises(ValueError):
        bar_path(up, "sideways")


def test_ticks_are_continuous_penny_steps():
    ticks = synthesize_ticks(Bar(TS, 100.0, 100.05, 99.97, 100.02))
    prices = [p for _, p in ticks]
    # low first: 100.00 -> 99.97 -> 100.05 -> 100.02
    assert prices == pytest.approx(
        [100.0, 99.99, 99.98, 99.97, 99.98, 99.99, 100.0, 100.01, 100.02, 100.03, 100.04, 100.05, 100.04, 100.03, 100.02]
    )
    assert max(abs(b - a) for a, b in zip(prices, prices[1:])) == pytest.approx(0.01)


def test_sub_penny_extremes_are_hit_exactly():
    prices = [p for _, p in synthesize_ticks(Bar(TS, 100.0, 100.0325, 99.9861, 100.02))]
    assert 99.9861 in prices
    assert 100.0325 in prices
    assert prices[-1] == 100.02


def test_timestamps_strictly_inside_the_minute_and_increasing():
    ticks = synthesize_ticks(Bar(TS, 100.0, 100.3, 99.9, 100.1))
    stamps = [t for t, _ in ticks]
    assert stamps[0] > TS
    assert stamps[-1] < TS + timedelta(minutes=1)
    assert stamps == sorted(stamps)
    assert all(t.tzinfo is ET for t in stamps)


def test_flat_bar_is_single_tick():
    assert [p for _, p in synthesize_ticks(Bar(TS, 100.0, 100.0, 100.0, 100.0))] == [100.0]

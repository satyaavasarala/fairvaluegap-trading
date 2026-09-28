import pytest

from fvg_bot.strategy.fvg import (
    bar_touches,
    find_fvgs,
    fvg_at,
    in_zone,
    is_beyond_far_edge,
    is_invalidated,
)
from fvg_bot.strategy.types import Direction
from tests.bars import hl, make_bars, mirror


def test_bullish_fvg():
    bars = hl([(10.0, 9.0), (12.0, 10.0), (13.0, 11.0)])
    fvg = fvg_at(bars, 2)
    assert fvg.direction is Direction.BULLISH
    assert fvg.bottom == 10.0
    assert fvg.top == 11.0
    assert fvg.mid == 10.5
    assert fvg.far_edge == 10.0
    assert fvg.ts == bars[2].ts


def test_bearish_fvg():
    bars = hl([(13.0, 12.0), (12.0, 10.0), (11.0, 9.0)])
    fvg = fvg_at(bars, 2)
    assert fvg.direction is Direction.BEARISH
    assert fvg.top == 12.0
    assert fvg.bottom == 11.0
    assert fvg.mid == 11.5
    assert fvg.far_edge == 12.0


def test_mirrored_bullish_is_bearish():
    bull = fvg_at(hl([(10.0, 9.0), (12.0, 10.0), (13.0, 11.0)]), 2)
    bear = fvg_at(mirror(hl([(10.0, 9.0), (12.0, 10.0), (13.0, 11.0)])), 2)
    assert bear.direction is Direction.BEARISH
    assert bear.top == pytest.approx(200 - bull.bottom)
    assert bear.bottom == pytest.approx(200 - bull.top)


def test_overlapping_wicks_return_none():
    bars = hl([(10.0, 9.0), (12.0, 10.0), (13.0, 9.5)])
    assert fvg_at(bars, 2) is None


def test_touching_wicks_are_not_a_gap():
    assert fvg_at(hl([(10.0, 9.0), (12.0, 10.0), (13.0, 10.0)]), 2) is None
    assert fvg_at(hl([(13.0, 12.0), (12.0, 10.0), (12.0, 9.0)]), 2) is None


def test_needs_three_candles():
    bars = hl([(10.0, 9.0), (12.0, 10.0)])
    assert fvg_at(bars, 0) is None
    assert fvg_at(bars, 1) is None


def test_find_fvgs_reports_confirming_index():
    bars = hl([(10.0, 9.0), (12.0, 10.0), (13.0, 11.0), (13.5, 12.5), (13.0, 12.6)])
    found = find_fvgs(bars)
    assert [i for i, _ in found] == [2, 3]
    assert all(f.direction is Direction.BULLISH for _, f in found)


def test_in_zone_is_inclusive():
    fvg = fvg_at(hl([(10.0, 9.0), (12.0, 10.0), (13.0, 11.0)]), 2)
    assert in_zone(fvg, 10.0)
    assert in_zone(fvg, 11.0)
    assert in_zone(fvg, 10.5)
    assert not in_zone(fvg, 9.99)
    assert not in_zone(fvg, 11.01)


def test_bar_touches():
    fvg = fvg_at(hl([(10.0, 9.0), (12.0, 10.0), (13.0, 11.0)]), 2)
    assert bar_touches(fvg, hl([(12.0, 10.9)])[0])
    assert bar_touches(fvg, hl([(10.1, 9.0)])[0])
    assert not bar_touches(fvg, hl([(12.0, 11.1)])[0])
    assert not bar_touches(fvg, hl([(9.9, 9.0)])[0])


def test_bullish_invalidation_is_close_based():
    fvg = fvg_at(hl([(10.0, 9.0), (12.0, 10.0), (13.0, 11.0)]), 2)
    wick_only = make_bars([(10.5, 10.6, 9.5, 10.1)])[0]
    closed_below = make_bars([(10.5, 10.6, 9.5, 9.9)])[0]
    assert not is_invalidated(fvg, wick_only)
    assert is_invalidated(fvg, closed_below)


def test_bearish_invalidation_is_close_based():
    fvg = fvg_at(hl([(13.0, 12.0), (12.0, 10.0), (11.0, 9.0)]), 2)
    wick_only = make_bars([(11.5, 12.5, 11.4, 11.9)])[0]
    closed_above = make_bars([(11.5, 12.5, 11.4, 12.1)])[0]
    assert not is_invalidated(fvg, wick_only)
    assert is_invalidated(fvg, closed_above)


def test_tick_beyond_far_edge():
    bull = fvg_at(hl([(10.0, 9.0), (12.0, 10.0), (13.0, 11.0)]), 2)
    assert is_beyond_far_edge(bull, 9.99)
    assert not is_beyond_far_edge(bull, 10.0)
    assert not is_beyond_far_edge(bull, 11.5)

    bear = fvg_at(hl([(13.0, 12.0), (12.0, 10.0), (11.0, 9.0)]), 2)
    assert is_beyond_far_edge(bear, 12.01)
    assert not is_beyond_far_edge(bear, 12.0)
    assert not is_beyond_far_edge(bear, 10.5)

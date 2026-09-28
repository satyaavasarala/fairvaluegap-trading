import pytest

from fvg_bot.strategy.fib import retracement_zone
from fvg_bot.strategy.fvg import fvg_at
from fvg_bot.strategy.setup import find_setup, impulse_range, stop_level
from fvg_bot.strategy.swings import find_choch
from fvg_bot.strategy.types import Direction
from tests.bars import SETUP_SCENARIO as SCENARIO
from tests.bars import SETUP_SCENARIO_CHOCH as CHOCH
from tests.bars import SETUP_SCENARIO_TOUCH as TOUCH
from tests.bars import make_bars, mirror


def test_scenario_choch_index():
    assert find_choch(make_bars(SCENARIO), Direction.BULLISH, start=TOUCH) == CHOCH


def test_impulse_range_bullish():
    bars = make_bars(SCENARIO)
    assert impulse_range(bars, Direction.BULLISH, TOUCH, CHOCH, as_of=5) == (99.8, 101.8)
    assert impulse_range(bars, Direction.BULLISH, TOUCH, CHOCH, as_of=7) == (99.8, 103.0)


def test_no_setup_before_leg_extends():
    # As of idx 6 the leg tops at 102.6: zone [100.8696, 101.2], FVG mid 101.3 is above it.
    assert find_setup(make_bars(SCENARIO), Direction.BULLISH, TOUCH, CHOCH, as_of=6) is None


def test_setup_found_once_leg_extends():
    # As of idx 7 the leg tops at 103.0: zone [101.0224, 101.4] holds the idx-6 mid 101.3,
    # while the newer idx-7 FVG (mid 102.0) sits above the zone.
    s = find_setup(make_bars(SCENARIO), Direction.BULLISH, TOUCH, CHOCH, as_of=7)
    assert s is not None
    assert s.fvg_index == 6
    assert s.choch_index == CHOCH
    assert (s.swing_low, s.swing_high) == (99.8, 103.0)
    assert round(s.zone.low, 4) == 101.0224
    assert round(s.zone.high, 4) == 101.4
    assert s.entry_level == pytest.approx(101.3)
    assert s.stop_level == pytest.approx(100.97)


def test_stop_buffer_and_ratios_are_parameters():
    s = find_setup(
        make_bars(SCENARIO), Direction.BULLISH, TOUCH, CHOCH, as_of=7, stop_buffer=0.05
    )
    assert s.stop_level == pytest.approx(100.95)
    none = find_setup(
        make_bars(SCENARIO), Direction.BULLISH, TOUCH, CHOCH, as_of=7, ratios=(0.618, 0.65)
    )
    assert none is None


def test_window_limits_bars_after_choch():
    # max_bars=1 caps evaluation at idx 6, where nothing qualifies yet.
    bars = make_bars(SCENARIO)
    assert find_setup(bars, Direction.BULLISH, TOUCH, CHOCH, as_of=7, max_bars=1) is None


def test_wrong_direction_finds_nothing():
    bars = make_bars(SCENARIO)
    assert find_setup(bars, Direction.BEARISH, TOUCH, CHOCH, as_of=7) is None


def test_bearish_setup_is_mirror():
    bull = find_setup(make_bars(SCENARIO), Direction.BULLISH, TOUCH, CHOCH, as_of=7)
    bear_bars = mirror(make_bars(SCENARIO))
    assert find_choch(bear_bars, Direction.BEARISH, start=TOUCH) == CHOCH
    bear = find_setup(bear_bars, Direction.BEARISH, TOUCH, CHOCH, as_of=7)
    assert bear is not None
    assert bear.direction is Direction.BEARISH
    assert bear.fvg_index == bull.fvg_index
    assert bear.swing_low == pytest.approx(200 - bull.swing_high)
    assert bear.swing_high == pytest.approx(200 - bull.swing_low)
    assert bear.zone.low == pytest.approx(200 - bull.zone.high)
    assert bear.zone.high == pytest.approx(200 - bull.zone.low)
    assert bear.entry_level == pytest.approx(200 - bull.entry_level)
    assert bear.stop_level == pytest.approx(200 - bull.stop_level)


def test_fvg_poking_below_impulse_origin_is_rejected():
    # Origin low 9.0 (touch..ChoCh). Bar 4 trades entirely below it, so the idx-6 FVG
    # has bottom 8.8 < 9.0 even though its mid sits inside the discount zone.
    bars = make_bars([
        (10.2, 10.5, 10.0, 10.3),
        (10.3, 11.0, 10.2, 10.4),
        (10.4, 10.6, 9.0, 9.5),
        (9.5, 11.5, 9.5, 11.2),
        (8.7, 8.8, 8.0, 8.5),
        (8.5, 12.5, 8.2, 12.4),
        (12.4, 12.6, 12.4, 12.5),
    ])
    assert find_choch(bars, Direction.BULLISH, start=2) == 3
    fvg = fvg_at(bars, 6)
    assert (fvg.bottom, fvg.top) == (8.8, 12.4)
    swing_low, swing_high = impulse_range(bars, Direction.BULLISH, 2, 3, as_of=6)
    assert (swing_low, swing_high) == (9.0, 12.6)
    assert retracement_zone(swing_low, swing_high, Direction.BULLISH).contains(fvg.mid)
    assert find_setup(bars, Direction.BULLISH, 2, 3, as_of=6) is None


def test_stop_level_directions():
    bull = fvg_at(make_bars([(0, 10.0, 9.0, 0), (0, 12.0, 10.0, 0), (0, 13.0, 11.0, 0)]), 2)
    bear = fvg_at(make_bars([(0, 13.0, 12.0, 0), (0, 12.0, 10.0, 0), (0, 11.0, 9.0, 0)]), 2)
    assert stop_level(bull, 0.03) == pytest.approx(9.97)
    assert stop_level(bear, 0.03) == pytest.approx(12.03)

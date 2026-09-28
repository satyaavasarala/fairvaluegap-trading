from fvg_bot.strategy.swings import (
    Swing,
    find_choch,
    is_swing_high,
    is_swing_low,
    last_confirmed_swing_high,
    last_confirmed_swing_low,
)
from fvg_bot.strategy.types import Direction
from tests.bars import hl, make_bars, mirror


def test_swing_high_and_low():
    bars = hl([(10.0, 9.0), (12.0, 8.0), (11.0, 8.5)])
    assert is_swing_high(bars, 1)
    assert is_swing_low(bars, 1)


def test_equal_highs_are_not_swings():
    bars = hl([(10.0, 9.0), (12.0, 9.0), (12.0, 9.0), (11.0, 9.0)])
    assert not is_swing_high(bars, 1)
    assert not is_swing_high(bars, 2)


def test_equal_lows_are_not_swings():
    bars = hl([(10.0, 9.0), (10.0, 8.0), (10.0, 8.0), (10.0, 8.5)])
    assert not is_swing_low(bars, 1)
    assert not is_swing_low(bars, 2)


def test_edges_are_never_swings():
    bars = hl([(12.0, 8.0), (10.0, 9.0), (13.0, 7.0)])
    assert not is_swing_high(bars, 0)
    assert not is_swing_high(bars, 2)
    assert not is_swing_low(bars, 0)
    assert not is_swing_low(bars, 2)


def test_swing_not_visible_until_next_bar_closes():
    bars = hl([(10.0, 9.0), (12.0, 8.0), (11.0, 8.5)])
    assert last_confirmed_swing_high(bars, as_of=1) is None
    assert last_confirmed_swing_high(bars, as_of=2) == Swing(1, 12.0)
    assert last_confirmed_swing_low(bars, as_of=1) is None
    assert last_confirmed_swing_low(bars, as_of=2) == Swing(1, 8.0)


def test_last_confirmed_ignores_bars_after_as_of():
    # A later, higher swing at index 3 must not leak into as_of=2.
    bars = hl([(10.0, 9.0), (12.0, 9.0), (11.0, 9.0), (15.0, 9.0), (14.0, 9.0)])
    assert last_confirmed_swing_high(bars, as_of=2) == Swing(1, 12.0)
    assert last_confirmed_swing_high(bars, as_of=4) == Swing(3, 15.0)


# Descending into a zone: swing high 11.0 at index 1, then lower prices.
BULL_CHOCH_ROWS = [
    (10.5, 10.6, 10.2, 10.5),
    (10.5, 11.0, 10.3, 10.4),
    (10.4, 10.5, 9.8, 9.9),
    (9.9, 11.3, 9.8, 10.9),  # wick above 11.0, close below: not a ChoCh
    (10.9, 11.4, 10.8, 11.1),  # close above 11.0; higher high keeps idx 3 from being a swing
]


def test_rejected_wick_becomes_the_new_swing():
    # Same as above but bar 4 makes a lower high, so bar 3's 11.3 wick is confirmed as the
    # most recent swing high at bar 4's close, and 11.1 no longer clears it.
    rows = BULL_CHOCH_ROWS[:4] + [(10.9, 11.2, 10.8, 11.1)]
    assert find_choch(make_bars(rows), Direction.BULLISH, start=2) is None


def test_bullish_choch_requires_close():
    bars = make_bars(BULL_CHOCH_ROWS)
    assert find_choch(bars, Direction.BULLISH, start=2) == 4


def test_choch_respects_start():
    bars = make_bars(BULL_CHOCH_ROWS)
    assert find_choch(bars, Direction.BULLISH, start=5) is None


def test_choch_respects_end():
    bars = make_bars(BULL_CHOCH_ROWS)
    assert find_choch(bars, Direction.BULLISH, start=2, end=3) is None


def test_bearish_choch_is_mirror():
    bars = mirror(make_bars(BULL_CHOCH_ROWS))
    assert find_choch(bars, Direction.BEARISH, start=2) == 4
    assert find_choch(bars, Direction.BULLISH, start=2) is None


def test_choch_uses_most_recent_swing_not_older_lower_one():
    # Older swing high 11.0 (idx 1), newer higher swing high 12.0 (idx 3).
    # A close at 11.5 clears the old swing but not the most recent one.
    bars = make_bars([
        (10.5, 10.6, 10.2, 10.5),
        (10.5, 11.0, 10.3, 10.4),
        (10.4, 10.5, 9.8, 9.9),
        (9.9, 12.0, 9.8, 11.0),
        (11.0, 11.6, 10.9, 11.5),
    ])
    assert find_choch(bars, Direction.BULLISH, start=2) is None


def test_choch_uses_most_recent_swing_not_older_higher_one():
    # Older swing high 12.0 (idx 1), newer lower swing high 11.0 (idx 3).
    bars = make_bars([
        (10.5, 10.6, 10.2, 10.5),
        (10.5, 12.0, 10.3, 10.4),
        (10.4, 10.5, 9.8, 9.9),
        (9.9, 11.0, 9.7, 10.0),
        (10.0, 10.2, 9.5, 9.6),
        (9.6, 11.6, 9.6, 11.5),
    ])
    assert find_choch(bars, Direction.BULLISH, start=2) == 5


def test_swing_broken_before_start_is_spent():
    # 11.0 is broken at idx 4 and highs keep rising, so no new swing forms. Searching from
    # idx 5 must not re-fire on the next close above the spent 11.0.
    bars = make_bars(BULL_CHOCH_ROWS + [(11.1, 11.5, 11.0, 11.4), (11.4, 11.6, 11.3, 11.5)])
    assert find_choch(bars, Direction.BULLISH, start=4) == 4
    assert find_choch(bars, Direction.BULLISH, start=5) is None


def test_no_swing_no_choch():
    bars = hl([(10.0, 9.0), (11.0, 10.0), (12.0, 11.0), (13.0, 12.0)])
    assert find_choch(bars, Direction.BULLISH, start=0) is None

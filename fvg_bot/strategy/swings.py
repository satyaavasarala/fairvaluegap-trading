from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from fvg_bot.strategy.types import Bar, Direction


@dataclass(frozen=True)
class Swing:
    index: int
    price: float


def is_swing_high(bars: Sequence[Bar], k: int) -> bool:
    if not 0 < k < len(bars) - 1:
        return False
    return bars[k].high > bars[k - 1].high and bars[k].high > bars[k + 1].high


def is_swing_low(bars: Sequence[Bar], k: int) -> bool:
    if not 0 < k < len(bars) - 1:
        return False
    return bars[k].low < bars[k - 1].low and bars[k].low < bars[k + 1].low


def last_confirmed_swing_high(bars: Sequence[Bar], as_of: int) -> Optional[Swing]:
    """Most recent swing high known at the close of bars[as_of] (confirmed by bar k+1 <= as_of)."""
    for k in range(as_of - 1, 0, -1):
        if is_swing_high(bars, k):
            return Swing(k, bars[k].high)
    return None


def last_confirmed_swing_low(bars: Sequence[Bar], as_of: int) -> Optional[Swing]:
    for k in range(as_of - 1, 0, -1):
        if is_swing_low(bars, k):
            return Swing(k, bars[k].low)
    return None


def find_choch(
    bars: Sequence[Bar], direction: Direction, start: int, end: Optional[int] = None
) -> Optional[int]:
    """First index in [start, end] whose close breaks the most recent confirmed swing.

    Bullish breaks swing highs, bearish breaks swing lows. Close-based only.
    A swing already broken before start is spent and cannot be broken again.
    """
    end = len(bars) - 1 if end is None else end
    bullish = direction is Direction.BULLISH
    level: Optional[float] = None
    for i in range(1, end + 1):
        k = i - 1
        if bullish and is_swing_high(bars, k):
            level = bars[k].high
        elif not bullish and is_swing_low(bars, k):
            level = bars[k].low
        if level is None:
            continue
        close = bars[i].close
        if (bullish and close > level) or (not bullish and close < level):
            if i >= start:
                return i
            level = None
    return None

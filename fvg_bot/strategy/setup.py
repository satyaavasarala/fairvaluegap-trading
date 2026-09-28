from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from fvg_bot.strategy.fib import DISCOUNT_ZONE, PriceZone, retracement_zone
from fvg_bot.strategy.fvg import FVG, fvg_at
from fvg_bot.strategy.types import Bar, Direction


@dataclass(frozen=True)
class Setup:
    direction: Direction
    fvg: FVG
    fvg_index: int
    choch_index: int
    swing_low: float
    swing_high: float
    zone: PriceZone
    entry_level: float
    stop_level: float


def impulse_range(
    bars: Sequence[Bar], direction: Direction, touch_index: int, choch_index: int, as_of: int
) -> Tuple[float, float]:
    """(swing_low, swing_high) of the impulse leg.

    The origin extreme spans the rebalance touch through the ChoCh bar and is frozen there;
    the leg extreme spans the ChoCh bar through as_of and keeps extending.
    """
    origin = bars[touch_index : choch_index + 1]
    leg = bars[choch_index : as_of + 1]
    if direction is Direction.BULLISH:
        return min(b.low for b in origin), max(b.high for b in leg)
    return min(b.low for b in leg), max(b.high for b in origin)


def stop_level(fvg: FVG, buffer: float) -> float:
    if fvg.direction is Direction.BULLISH:
        return fvg.bottom - buffer
    return fvg.top + buffer


def find_setup(
    bars: Sequence[Bar],
    direction: Direction,
    touch_index: int,
    choch_index: int,
    as_of: int,
    max_bars: int = 5,
    stop_buffer: float = 0.03,
    ratios: Tuple[float, float] = DISCOUNT_ZONE,
) -> Optional[Setup]:
    """Most recent 1m FVG confirmed within max_bars after the ChoCh that qualifies as of as_of.

    Qualifies = same direction, fully inside the impulse leg, midpoint inside the retracement zone.
    Earlier FVGs are re-checked each bar because the zone moves as the leg extends.
    """
    last = min(as_of, choch_index + max_bars)
    swing_low, swing_high = impulse_range(bars, direction, touch_index, choch_index, last)
    if swing_high <= swing_low:
        return None
    zone = retracement_zone(swing_low, swing_high, direction, ratios)
    for i in range(last, choch_index, -1):
        fvg = fvg_at(bars, i)
        if fvg is None or fvg.direction is not direction:
            continue
        if fvg.bottom < swing_low or fvg.top > swing_high:
            continue
        if not zone.contains(fvg.mid):
            continue
        return Setup(
            direction=direction,
            fvg=fvg,
            fvg_index=i,
            choch_index=choch_index,
            swing_low=swing_low,
            swing_high=swing_high,
            zone=zone,
            entry_level=fvg.mid,
            stop_level=stop_level(fvg, stop_buffer),
        )
    return None

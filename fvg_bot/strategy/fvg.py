from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from fvg_bot.strategy.types import Bar, Direction


@dataclass(frozen=True)
class FVG:
    direction: Direction
    top: float
    bottom: float
    ts: datetime  # start time of candle i, the candle whose close confirms the gap

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def far_edge(self) -> float:
        return self.bottom if self.direction is Direction.BULLISH else self.top


def fvg_at(bars: Sequence[Bar], i: int) -> Optional[FVG]:
    """FVG confirmed by bars[i] (candle i), with bars[i-2] as the anchor."""
    if i < 2:
        return None
    anchor, last = bars[i - 2], bars[i]
    if last.low > anchor.high:
        return FVG(Direction.BULLISH, top=last.low, bottom=anchor.high, ts=last.ts)
    if last.high < anchor.low:
        return FVG(Direction.BEARISH, top=anchor.low, bottom=last.high, ts=last.ts)
    return None


def find_fvgs(bars: Sequence[Bar]) -> List[Tuple[int, FVG]]:
    found = []
    for i in range(2, len(bars)):
        fvg = fvg_at(bars, i)
        if fvg is not None:
            found.append((i, fvg))
    return found


def in_zone(fvg: FVG, price: float) -> bool:
    return fvg.bottom <= price <= fvg.top


def bar_touches(fvg: FVG, bar: Bar) -> bool:
    return bar.low <= fvg.top and bar.high >= fvg.bottom


def is_beyond_far_edge(fvg: FVG, price: float) -> bool:
    if fvg.direction is Direction.BULLISH:
        return price < fvg.bottom
    return price > fvg.top


def is_invalidated(fvg: FVG, bar: Bar) -> bool:
    """Close-based: a wick through the far edge does not invalidate."""
    return is_beyond_far_edge(fvg, bar.close)

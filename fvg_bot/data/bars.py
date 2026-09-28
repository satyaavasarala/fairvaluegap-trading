from __future__ import annotations

from typing import List, Sequence

from fvg_bot.clock import to_et
from fvg_bot.strategy.types import Bar


def aggregate(bars: Sequence[Bar], minutes: int) -> List[Bar]:
    """Roll 1m bars into N-minute bars aligned to the ET clock (09:30, 09:45, ...).

    Bars are stamped with their bucket start. Minutes with no trades simply leave gaps.
    """
    if minutes < 1 or 60 % minutes:
        raise ValueError(f"minutes must divide 60, got {minutes}")
    out: List[Bar] = []
    for b in bars:
        et = to_et(b.ts)
        start = et.replace(minute=et.minute - et.minute % minutes, second=0, microsecond=0)
        if out and out[-1].ts == start:
            last = out[-1]
            out[-1] = Bar(
                ts=start,
                open=last.open,
                high=max(last.high, b.high),
                low=min(last.low, b.low),
                close=b.close,
            )
        else:
            if out and start < out[-1].ts:
                raise ValueError(f"bars out of order at {b.ts}")
            out.append(Bar(ts=start, open=b.open, high=b.high, low=b.low, close=b.close))
    return out

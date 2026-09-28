from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, List, Sequence

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


M1 = timedelta(minutes=1)


class Buckets:
    """Rolls 1m bars into N-minute bars, emitting each as soon as it is known to be complete."""

    def __init__(self, minutes: int, emit: Callable[[Bar], None]):
        self.minutes = minutes
        self.span = timedelta(minutes=minutes)
        self.emit = emit
        self.bars: List[Bar] = []

    def _start(self, ts: datetime) -> datetime:
        return ts.replace(minute=ts.minute - ts.minute % self.minutes, second=0, microsecond=0)

    def add(self, bar: Bar) -> None:
        if self.bars and self._start(bar.ts) != self._start(self.bars[0].ts):
            self.flush()
        self.bars.append(bar)

    def close_if_complete(self, bar: Bar) -> None:
        if bar.ts + M1 == self._start(bar.ts) + self.span:
            self.flush()

    def flush(self) -> None:
        if self.bars:
            self.emit(aggregate(self.bars, self.minutes)[0])
            self.bars = []

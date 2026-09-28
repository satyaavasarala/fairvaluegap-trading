"""Replay cached 1m bars through the entry FSM.

Per 1m bar: synthesized ticks (only while a tick can matter), then the bar close, then the
15m bar close if that minute completes a quarter hour. Extended-hours bars feed the 15m
zones (D1); the FSM sees 1m bars from the session open, as the live bot would.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, List, Optional, Sequence

from fvg_bot.config import Config
from fvg_bot.data.bars import aggregate
from fvg_bot.strategy.fsm import EntryDecision, EntryFSM, State
from fvg_bot.strategy.setup import Setup
from fvg_bot.strategy.types import Bar, Direction
from fvg_bot.backtest.ticks import synthesize_ticks

TICK_STATES = {State.WAIT_REBALANCE, State.HUNT_CHOCH, State.ARMED}
M1 = timedelta(minutes=1)
M15 = timedelta(minutes=15)


@dataclass
class SetupRecord:
    symbol: str
    day: date
    direction: Direction
    armed_at: datetime
    entry_level: float
    stop_level: float
    fvg_bottom: float
    fvg_top: float
    swing_low: float
    swing_high: float
    triggered: bool = False
    trigger_at: Optional[datetime] = None
    trigger_price: Optional[float] = None

    @property
    def stop_distance(self) -> float:
        """At fill if triggered, else at the planned entry (FVG mid)."""
        entry = self.trigger_price if self.triggered else self.entry_level
        return abs(entry - self.stop_level)

    @classmethod
    def from_setup(cls, symbol: str, day: date, armed_at: datetime, s: Setup) -> "SetupRecord":
        return cls(
            symbol=symbol,
            day=day,
            direction=s.direction,
            armed_at=armed_at,
            entry_level=s.entry_level,
            stop_level=s.stop_level,
            fvg_bottom=s.fvg.bottom,
            fvg_top=s.fvg.top,
            swing_low=s.swing_low,
            swing_high=s.swing_high,
        )


@dataclass
class ReplayResult:
    symbol: str
    sessions: int = 0
    setups: List[SetupRecord] = field(default_factory=list)
    reasons: Counter = field(default_factory=Counter)

    @property
    def triggered(self) -> List[SetupRecord]:
        return [s for s in self.setups if s.triggered]


def _approve(setup: Setup, price: float, now: datetime) -> EntryDecision:
    return EntryDecision(True)


def _bucket_start(ts: datetime) -> datetime:
    return ts.replace(minute=ts.minute - ts.minute % 15, second=0, microsecond=0)


def replay(
    symbol: str,
    days: Sequence[date],
    load_day: Callable[[date], List[Bar]],
    cfg: Config,
    tick_step: float = 0.01,
    order: str = "heuristic",
) -> ReplayResult:
    """Every armed setup is recorded; the first trigger each day is assumed filled and ends the day."""
    result = ReplayResult(symbol)
    if not days:
        return result
    fsm = EntryFSM(cfg, _approve, days[0])

    for day in days:
        fsm.start_session(day)
        result.sessions += 1
        current: Optional[SetupRecord] = None
        bucket: List[Bar] = []

        def flush() -> None:
            if bucket:
                fsm.on_15m_bar(aggregate(bucket, 15)[0])
                bucket.clear()

        for bar in load_day(day):
            if bucket and _bucket_start(bar.ts) != _bucket_start(bucket[0].ts):
                flush()
            bucket.append(bar)

            if bar.ts >= fsm.session_open:
                if (
                    fsm.state in TICK_STATES
                    and cfg.entry_start <= bar.ts.time() <= cfg.entry_end
                ):
                    for ts, price in synthesize_ticks(bar, tick_step, order):
                        intent = fsm.on_tick(ts, price)
                        if intent is not None:
                            current.triggered = True
                            current.trigger_at = ts
                            current.trigger_price = price
                            fsm.on_entry_result(ts, filled=True)
                            fsm.on_position_closed(ts)
                            break
                fsm.on_1m_bar(bar)
                if fsm.state is State.ARMED and (current is None or current.armed_at != fsm.armed_at):
                    current = SetupRecord.from_setup(symbol, day, fsm.armed_at, fsm.setup)
                    result.setups.append(current)

            if bar.ts + M1 == _bucket_start(bar.ts) + M15:
                flush()
        flush()
        result.reasons.update(t.reason for t in fsm.transitions)
    return result

"""Replay cached 1m bars through the entry FSM.

Per 1m bar: synthesized ticks (only while a tick can matter), then the LTF bar close if that
minute completes one (every minute when ltf_minutes is 1), then the 15m bar close likewise.
Extended-hours bars feed the 15m zones (D1); the FSM sees LTF bars from the session open,
as the live bot would. Ticks always come from 1m bars, whatever the LTF.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Callable, Dict, List, Optional, Sequence

from fvg_bot.backtest.outcome import CostModel, ExitRule, Outcome, TradeTracker
from fvg_bot.backtest.ticks import synthesize_ticks
from fvg_bot.clock import ET
from fvg_bot.config import Config
from fvg_bot.data.bars import Buckets
from fvg_bot.strategy.fsm import EntryCheck, EntryDecision, EntryFSM, State
from fvg_bot.strategy.setup import Setup
from fvg_bot.strategy.types import Bar, Direction

TICK_STATES = {State.WAIT_REBALANCE, State.HUNT_CHOCH, State.ARMED}
M1 = timedelta(minutes=1)


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
    outcomes: Dict[str, Outcome] = field(default_factory=dict)

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


def approve_all(setup: Setup, price: float, now: datetime) -> EntryDecision:
    return EntryDecision(True)


def regular_close(day: date) -> datetime:
    return datetime.combine(day, time(16, 0), tzinfo=ET)


def replay(
    symbol: str,
    days: Sequence[date],
    load_day: Callable[[date], List[Bar]],
    cfg: Config,
    entry_check: EntryCheck = approve_all,
    exit_rules: Sequence[ExitRule] = (),
    costs: Optional[CostModel] = None,
    session_close: Callable[[date], datetime] = regular_close,
    tick_step: float = 0.01,
    order: str = "heuristic",
) -> ReplayResult:
    """Every armed setup is recorded. The first trigger each day is assumed filled at the
    trigger tick and ends the day's entries; with exit_rules it is then tracked to exit."""
    if exit_rules and costs is None:
        raise ValueError("exit_rules need a CostModel")
    result = ReplayResult(symbol)
    if not days:
        return result
    fsm = EntryFSM(cfg, entry_check, days[0])

    for day in days:
        fsm.start_session(day)
        result.sessions += 1
        flatten_at = session_close(day) - cfg.flatten_before_close
        current: Optional[SetupRecord] = None
        tracker: Optional[TradeTracker] = None
        last_bar: Optional[Bar] = None

        def on_ltf(b: Bar) -> None:
            nonlocal current
            fsm.on_ltf_bar(b)
            if fsm.state is State.ARMED and (current is None or current.armed_at != fsm.armed_at):
                current = SetupRecord.from_setup(symbol, day, fsm.armed_at, fsm.setup)
                result.setups.append(current)

        ltf = Buckets(cfg.ltf_minutes, on_ltf)
        m15 = Buckets(15, fsm.on_15m_bar)

        for bar in load_day(day):
            last_bar = bar
            in_session = bar.ts >= fsm.session_open
            if in_session:
                ltf.add(bar)
            m15.add(bar)

            if in_session:
                fsm_ticks = fsm.state in TICK_STATES and cfg.entry_start <= bar.ts.time() <= cfg.entry_end
                if fsm_ticks or tracker is not None:
                    for ts, price in synthesize_ticks(bar, tick_step, order):
                        if tracker is not None:
                            if tracker.on_tick(ts, price):
                                current.outcomes = tracker.outcomes
                                tracker = None
                            continue
                        if fsm.state not in TICK_STATES:
                            continue
                        intent = fsm.on_tick(ts, price)
                        if intent is None:
                            continue
                        current.triggered = True
                        current.trigger_at = ts
                        current.trigger_price = price
                        fsm.on_entry_result(ts, filled=True)
                        fsm.on_position_closed(ts)
                        if exit_rules:
                            tracker = TradeTracker(
                                intent.setup.direction, ts, price, intent.setup.stop_level,
                                flatten_at, exit_rules, costs, cfg.target_r,
                            )
                ltf.close_if_complete(bar)
            m15.close_if_complete(bar)
        ltf.flush()
        m15.flush()
        if tracker is not None:
            tracker.close_out(last_bar.ts + M1, last_bar.close)
            current.outcomes = tracker.outcomes
        result.reasons.update(t.reason for t in fsm.transitions)
    return result

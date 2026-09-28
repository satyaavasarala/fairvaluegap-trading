from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from fvg_bot.clock import ET, to_et
from fvg_bot.config import Config
from fvg_bot.strategy.fvg import FVG, fvg_at, in_zone, is_beyond_far_edge, is_invalidated
from fvg_bot.strategy.setup import Setup, find_setup
from fvg_bot.strategy.swings import find_choch
from fvg_bot.strategy.types import Bar, Direction

BAR_15M = timedelta(minutes=15)


class State(Enum):
    IDLE = "00_IDLE"
    SCAN_15M = "01_SCAN_15M"
    WAIT_REBALANCE = "02_WAIT_REBALANCE"
    HUNT_CHOCH = "03_HUNT_CHOCH"
    VALIDATE_SETUP = "04_VALIDATE_SETUP"
    ARMED = "05_ARMED"
    SUBMIT_ORDER = "06_SUBMIT_ORDER"
    IN_POSITION = "PM_IN_POSITION"
    DONE = "07_COOLDOWN_DONE"


_WINDOW_STATES = {
    State.SCAN_15M,
    State.WAIT_REBALANCE,
    State.HUNT_CHOCH,
    State.VALIDATE_SETUP,
    State.ARMED,
}


@dataclass(frozen=True)
class Transition:
    ts: datetime
    src: State
    dst: State
    reason: str


@dataclass(frozen=True)
class EntryDecision:
    ok: bool
    reason: str = ""
    plan: Any = None


@dataclass(frozen=True)
class EntryIntent:
    ts: datetime
    setup: Setup
    plan: Any
    trigger_price: float


# (setup, underlying price, now) -> decision. Runs contract selection and sizing on live quotes.
EntryCheck = Callable[[Setup, float, datetime], EntryDecision]


class EntryFSM:
    """Entry states 00-07. Bars are passed when closed, stamped with their start time.

    LTF bars are cfg.ltf_minutes long (1m in the spec).
    """

    def __init__(self, cfg: Config, entry_check: EntryCheck, session_date: date):
        self.cfg = cfg
        self._ltf_len = timedelta(minutes=cfg.ltf_minutes)
        self._entry_check = entry_check
        self.zones: Dict[Direction, FVG] = {}
        self.bars_15m: List[Bar] = []
        self.start_session(session_date)

    def start_session(self, session_date: date) -> None:
        self.session_open = datetime.combine(session_date, self.cfg.session_open, tzinfo=ET)
        self.state = State.IDLE
        self.transitions: List[Transition] = []
        self.bars_ltf: List[Bar] = []
        self.bars_15m = self.bars_15m[-2:]
        if not self.cfg.include_prior_zones:
            self.zones = {}
        self._clear_hunt()

    # ---- inputs -------------------------------------------------------------

    def on_15m_bar(self, bar: Bar) -> None:
        now = to_et(bar.ts) + BAR_15M
        self.bars_15m.append(bar)
        for d, z in list(self.zones.items()):
            if is_invalidated(z, bar):
                del self.zones[d]
        if self.state is State.HUNT_CHOCH and is_invalidated(self.active_zone, bar):
            self._drop_zone(now, "15m close beyond far edge")
        fvg = fvg_at(self.bars_15m, len(self.bars_15m) - 1)
        if fvg is not None and (
            self.cfg.include_prior_zones or to_et(self.bars_15m[-3].ts) >= self.session_open
        ):
            self.zones[fvg.direction] = fvg
        self._on_time(now)
        if self.state in (State.SCAN_15M, State.WAIT_REBALANCE):
            self._go(now, self._scan_state(), self._scan_reason())

    def on_ltf_bar(self, bar: Bar) -> None:
        now = to_et(bar.ts) + self._ltf_len
        self.bars_ltf.append(bar)
        self._on_time(now)
        i = len(self.bars_ltf) - 1
        if self.state is State.HUNT_CHOCH:
            choch = find_choch(self.bars_ltf, self.active_zone.direction, self.choch_search_start, i)
            if choch is not None:
                self.choch_index = choch
                self._go(now, State.VALIDATE_SETUP, "LTF ChoCh")
        elif self.state is State.VALIDATE_SETUP:
            self._validate(now, i)

    def on_tick(self, ts: datetime, price: float) -> Optional[EntryIntent]:
        now = to_et(ts)
        self._on_time(now)
        if self.state is State.WAIT_REBALANCE:
            if now.time() >= self.cfg.entry_start:
                self._check_rebalance(now, price)
        elif self.state is State.HUNT_CHOCH:
            if is_beyond_far_edge(self.active_zone, price):
                self._drop_zone(now, "price beyond 15m far edge")
        elif self.state is State.ARMED:
            return self._armed_tick(now, price)
        return None

    def on_entry_result(self, ts: datetime, filled: bool) -> None:
        self._require(State.SUBMIT_ORDER)
        now = to_et(ts)
        if filled:
            self._go(now, State.IN_POSITION, "entry filled")
        else:
            self._rehunt(now, "entry not filled")
            self._on_time(now)

    def on_position_closed(self, ts: datetime) -> None:
        self._require(State.IN_POSITION)
        self._go(to_et(ts), State.DONE, "position closed")

    # ---- state logic --------------------------------------------------------

    def _on_time(self, now: datetime) -> None:
        if self.state is State.IDLE and now >= self.session_open:
            self._go(now, self._scan_state(), "session open")
        if self.state in _WINDOW_STATES and now.time() > self.cfg.entry_end:
            self._clear_hunt()
            self._go(now, State.DONE, "entry window closed")
        elif self.state is State.ARMED and now - self.armed_at >= self.cfg.arm_timeout:
            self._rehunt(now, "armed timeout")

    def _check_rebalance(self, now: datetime, price: float) -> None:
        for zone in sorted(self.zones.values(), key=lambda z: z.ts, reverse=True):
            if in_zone(zone, price):
                self.active_zone = zone
                self.touch_index = len(self.bars_ltf)
                self.choch_search_start = self.touch_index
                self._go(now, State.HUNT_CHOCH, f"rebalance into {zone.direction.value} 15m FVG")
                return

    def _validate(self, now: datetime, i: int) -> None:
        cfg = self.cfg
        setup = find_setup(
            self.bars_ltf,
            self.active_zone.direction,
            self.touch_index,
            self.choch_index,
            as_of=i,
            max_bars=cfg.setup_window_bars,
            stop_buffer=cfg.stop_buffer,
            ratios=cfg.zone_ratios,
            min_fvg_width=cfg.min_fvg_width,
            stop_mode=cfg.stop_mode,
        )
        if setup is not None:
            decision = self._entry_check(setup, setup.entry_level, now)
            if not decision.ok:
                self._rehunt(now, f"arm check failed: {decision.reason}")
                return
            self.setup = setup
            self.armed_at = now
            self._go(now, State.ARMED, "LTF FVG in discount zone")
        elif i - self.choch_index >= cfg.setup_window_bars:
            self._rehunt(now, "no qualifying LTF FVG within setup window")

    def _armed_tick(self, now: datetime, price: float) -> Optional[EntryIntent]:
        s = self.setup
        bull = s.direction is Direction.BULLISH
        if (price < s.stop_level) if bull else (price > s.stop_level):
            self._rehunt(now, "stop level breached before entry")
            return None
        at_mid = (price <= s.entry_level) if bull else (price >= s.entry_level)
        if at_mid:
            risk = abs(s.entry_level - s.stop_level)
            if abs(price - s.entry_level) > self.cfg.gap_guard_frac * risk:
                self._rehunt(now, "gap guard: first touch too far through mid")
                return None
            decision = self._entry_check(s, price, now)
            if not decision.ok:
                self._rehunt(now, f"entry filter failed: {decision.reason}")
                return None
            self._go(now, State.SUBMIT_ORDER, "touched LTF FVG mid")
            return EntryIntent(now, s, decision.plan, price)
        if not self.cfg.abort_on_new_extreme:
            return None
        if (price > s.swing_high) if bull else (price < s.swing_low):
            self._rehunt(now, "price exceeded swing extreme without retrace")
        return None

    # ---- helpers ------------------------------------------------------------

    def _clear_hunt(self) -> None:
        self.active_zone: Optional[FVG] = None
        self.touch_index: Optional[int] = None
        self.choch_search_start: Optional[int] = None
        self.choch_index: Optional[int] = None
        self.setup: Optional[Setup] = None
        self.armed_at: Optional[datetime] = None

    def _rehunt(self, now: datetime, reason: str) -> None:
        """Back to 03 on the same 15m zone, looking for a fresh ChoCh from the next bar."""
        self.setup = None
        self.armed_at = None
        self.choch_index = None
        self.choch_search_start = len(self.bars_ltf)
        self._go(now, State.HUNT_CHOCH, reason)

    def _drop_zone(self, now: datetime, reason: str) -> None:
        zone = self.active_zone
        if self.zones.get(zone.direction) is zone:
            del self.zones[zone.direction]
        self._clear_hunt()
        self._go(now, self._scan_state(), reason)

    def _scan_state(self) -> State:
        return State.WAIT_REBALANCE if self.zones else State.SCAN_15M

    def _scan_reason(self) -> str:
        return "active 15m FVG" if self.zones else "no active 15m FVG"

    def _go(self, now: datetime, dst: State, reason: str) -> None:
        if dst is self.state:
            return
        self.transitions.append(Transition(now, self.state, dst, reason))
        self.state = dst

    def _require(self, state: State) -> None:
        if self.state is not state:
            raise RuntimeError(f"expected state {state.value}, in {self.state.value}")

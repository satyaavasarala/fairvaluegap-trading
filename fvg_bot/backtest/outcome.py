"""Trade outcomes on the underlying path, converted to option R with a constant-delta proxy.

No theta, no gamma, no historical option quotes: premium change = delta * underlying move,
and costs are an assumed spread plus the exit pad, paid once per round trip.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence

from fvg_bot.config import Config
from fvg_bot.options.sizing import size_trade
from fvg_bot.strategy.fsm import EntryCheck, EntryDecision
from fvg_bot.strategy.setup import Setup
from fvg_bot.strategy.types import Direction

TP, STOP, TIME, EOD = "TP", "STOP", "TIME", "EOD"
PROXY_PREMIUM = 2.00  # only the spread matters to size_trade; the level is arbitrary


@dataclass(frozen=True)
class CostModel:
    spread: float
    delta: float
    exit_pad: float

    @classmethod
    def from_config(cls, cfg: Config, spread: float, delta: float) -> "CostModel":
        return cls(spread=spread, delta=delta, exit_pad=cfg.exit_slippage_pad)

    @property
    def round_trip(self) -> float:
        return self.spread + self.exit_pad

    def r_prem(self, stop_distance: float) -> float:
        return stop_distance * self.delta + self.round_trip

    def net_r(self, move: float, stop_distance: float) -> float:
        return (move * self.delta - self.round_trip) / self.r_prem(stop_distance)

    def entry_check(self, cfg: Config) -> EntryCheck:
        """Runs the real sizing filters against a synthetic quote with this spread and delta."""

        def check(setup: Setup, price: float, now: datetime) -> EntryDecision:
            sizing, reason = size_trade(
                price, setup.stop_level, self.delta, PROXY_PREMIUM, PROXY_PREMIUM + self.spread, cfg
            )
            return EntryDecision(sizing is not None, reason, sizing)

        return check


@dataclass(frozen=True)
class ExitRule:
    name: str
    target: str  # "premium": 4 * R_prem of option gain; "underlying": 4 * stop distance
    time_stop: Optional[timedelta]


def exit_rules() -> List[ExitRule]:
    rules = []
    for target, tag in (("premium", "prem"), ("underlying", "und")):
        for minutes in (30, 45, 60, None):
            ts = timedelta(minutes=minutes) if minutes else None
            rules.append(ExitRule(f"{tag}_{minutes or 'none'}", target, ts))
    return rules


DEFAULT_EXIT = "prem_45"


@dataclass(frozen=True)
class Outcome:
    reason: str
    exit_at: datetime
    exit_price: float
    move: float  # in the trade's favour, underlying dollars
    net_r: float
    gross_r: float  # move / stop distance, before costs


class TradeTracker:
    def __init__(
        self,
        direction: Direction,
        entry_at: datetime,
        entry_price: float,
        stop_level: float,
        flatten_at: datetime,
        rules: Sequence[ExitRule],
        costs: CostModel,
        target_r: float,
    ):
        self.sign = 1.0 if direction is Direction.BULLISH else -1.0
        self.entry_at = entry_at
        self.entry_price = entry_price
        self.stop_level = stop_level
        self.stop_distance = abs(entry_price - stop_level)
        self.flatten_at = flatten_at
        self.costs = costs
        self.pending = {r.name: r for r in rules}
        self.target_move = {
            r.name: (
                target_r * costs.r_prem(self.stop_distance) / costs.delta
                if r.target == "premium"
                else target_r * self.stop_distance
            )
            for r in rules
        }
        self.outcomes: Dict[str, Outcome] = {}

    @property
    def done(self) -> bool:
        return not self.pending

    def _resolve(self, name: str, reason: str, ts: datetime, price: float) -> None:
        move = self.sign * (price - self.entry_price)
        self.outcomes[name] = Outcome(
            reason, ts, price, move, self.costs.net_r(move, self.stop_distance), move / self.stop_distance
        )
        del self.pending[name]

    def on_tick(self, ts: datetime, price: float) -> bool:
        move = self.sign * (price - self.entry_price)
        through_stop = self.sign * (price - self.stop_level) < 0
        for name, rule in list(self.pending.items()):
            if through_stop:
                self._resolve(name, STOP, ts, price)
            elif move >= self.target_move[name]:
                self._resolve(name, TP, ts, price)
            elif ts >= self.flatten_at:
                self._resolve(name, EOD, ts, price)
            elif rule.time_stop is not None and ts - self.entry_at >= rule.time_stop:
                self._resolve(name, TIME, ts, price)
        return self.done

    def close_out(self, ts: datetime, price: float) -> None:
        for name in list(self.pending):
            self._resolve(name, EOD, ts, price)

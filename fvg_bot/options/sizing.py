from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

from fvg_bot.config import Config

MIN_STOP = "stop distance below minimum"
COST_FILTER = "round-trip cost exceeds cost filter"
RISK_BUDGET = "one contract exceeds risk budget"
LOSS_CAP = "disaster-stop loss exceeds daily loss limit"

CONTRACT_MULTIPLIER = 100


@dataclass(frozen=True)
class Sizing:
    stop_distance: float
    round_trip_cost: float
    r_prem: float
    contracts: int

    @property
    def r_dollars(self) -> float:
        return CONTRACT_MULTIPLIER * self.r_prem


@dataclass(frozen=True)
class ExitLevels:
    take_profit: float
    disaster_stop: float


def _floor(x: float) -> int:
    # 30 / (100 * 0.30) evaluates to 0.9999999999999999 in floating point.
    return math.floor(x + 1e-9)


def round_trip_cost(bid: float, ask: float, exit_slippage_pad: float) -> float:
    """(ask - mid) paid on entry + (mid - bid) paid on exit + pad, which reduces to the spread + pad."""
    return (ask - bid) + exit_slippage_pad


def size_trade(
    entry_underlying: float, stop_level: float, delta: float, bid: float, ask: float, cfg: Config
) -> Tuple[Optional[Sizing], str]:
    stop_distance = abs(entry_underlying - stop_level)
    if stop_distance < cfg.min_stop_distance:
        return None, MIN_STOP
    cost = round_trip_cost(bid, ask, cfg.exit_slippage_pad)
    r_prem = stop_distance * abs(delta) + cost
    if cost > cfg.cost_filter_frac * r_prem:
        return None, COST_FILTER
    budget_contracts = _floor(cfg.risk_budget / (CONTRACT_MULTIPLIER * r_prem))
    if budget_contracts < 1:
        return None, RISK_BUDGET
    worst_loss = CONTRACT_MULTIPLIER * (cfg.disaster_r * r_prem + cfg.disaster_slippage_pad)
    contracts = min(budget_contracts, _floor(cfg.daily_loss_limit / worst_loss))
    if contracts < 1:
        return None, LOSS_CAP
    return Sizing(stop_distance, cost, r_prem, contracts), ""


def exit_levels(entry_fill: float, r_prem: float, cfg: Config) -> ExitLevels:
    return ExitLevels(
        take_profit=entry_fill + cfg.target_r * r_prem,
        disaster_stop=entry_fill - cfg.disaster_r * r_prem,
    )

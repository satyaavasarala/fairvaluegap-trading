from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from fvg_bot.config import Config
from fvg_bot.options.selection import OptionQuote, select_contract
from fvg_bot.options.sizing import Sizing, size_trade
from fvg_bot.strategy.fsm import EntryDecision
from fvg_bot.strategy.setup import Setup


@dataclass(frozen=True)
class EntryPlan:
    contract: OptionQuote
    sizing: Sizing


def evaluate_entry(
    setup: Setup,
    underlying_price: float,
    chain: Sequence[OptionQuote],
    now: datetime,
    cfg: Config,
) -> EntryDecision:
    contract, reason = select_contract(chain, setup.direction, now, cfg)
    if contract is None:
        return EntryDecision(False, reason)
    sizing, reason = size_trade(
        underlying_price, setup.stop_level, contract.delta, contract.bid, contract.ask, cfg
    )
    if sizing is None:
        return EntryDecision(False, reason)
    return EntryDecision(True, plan=EntryPlan(contract, sizing))

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Optional, Sequence, Tuple

from fvg_bot.clock import to_et
from fvg_bot.config import Config
from fvg_bot.strategy.types import Direction

NO_0DTE = "no 0DTE contract"
NO_DELTA = "no contract in delta band"
NO_BID = "no bid or crossed quote"
WIDE_SPREAD = "spread too wide"
STALE_QUOTE = "quote too old"
LOW_VOLUME = "volume below minimum"
LOW_OI = "open interest below minimum"
PREMIUM_RANGE = "premium outside sane range"


class Right(Enum):
    CALL = "call"
    PUT = "put"


@dataclass(frozen=True)
class OptionQuote:
    symbol: str
    right: Right
    strike: float
    expiry: date
    bid: float
    ask: float
    delta: float
    volume: int
    open_interest: int
    quote_ts: datetime

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        return self.ask - self.bid


def select_contract(
    chain: Sequence[OptionQuote], direction: Direction, now: datetime, cfg: Config
) -> Tuple[Optional[OptionQuote], str]:
    """Nearest-to-target-delta 0DTE contract; the chosen contract is rejected rather than
    replaced if it fails a liquidity check."""
    right = Right.CALL if direction is Direction.BULLISH else Right.PUT
    today = to_et(now).date()
    todays = [q for q in chain if q.right is right and q.expiry == today]
    if not todays:
        return None, NO_0DTE
    in_band = [q for q in todays if cfg.delta_min <= abs(q.delta) <= cfg.delta_max]
    if not in_band:
        return None, NO_DELTA
    q = min(in_band, key=lambda c: (abs(abs(c.delta) - cfg.target_delta), c.spread))
    if q.bid <= 0 or q.ask < q.bid:
        return None, NO_BID
    if q.spread > cfg.max_spread:
        return None, WIDE_SPREAD
    if now - q.quote_ts >= cfg.max_quote_age:
        return None, STALE_QUOTE
    if q.volume < cfg.min_volume:
        return None, LOW_VOLUME
    if q.open_interest < cfg.min_open_interest:
        return None, LOW_OI
    if not cfg.min_premium <= q.mid <= cfg.max_premium:
        return None, PREMIUM_RANGE
    return q, ""

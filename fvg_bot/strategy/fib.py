from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from fvg_bot.strategy.types import Direction

# Source material calls 50%-61.8% the "Golden Pocket"; the conventional one is 61.8%-65% (D5).
DISCOUNT_ZONE: Tuple[float, float] = (0.5, 0.618)


@dataclass(frozen=True)
class PriceZone:
    low: float
    high: float

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high


def retracement_zone(
    swing_low: float,
    swing_high: float,
    direction: Direction,
    ratios: Tuple[float, float] = DISCOUNT_ZONE,
) -> PriceZone:
    """Retracement band of the impulse, measured back from its extreme.

    Bullish impulse runs low -> high, so the band sits below swing_high (discount).
    Bearish impulse runs high -> low, so the band sits above swing_low (premium).
    """
    near, far = ratios
    if not 0 <= near < far <= 1:
        raise ValueError(f"ratios must satisfy 0 <= near < far <= 1, got {ratios}")
    if swing_high <= swing_low:
        raise ValueError(f"swing_high {swing_high} must exceed swing_low {swing_low}")
    rng = swing_high - swing_low
    if direction is Direction.BULLISH:
        return PriceZone(low=swing_high - far * rng, high=swing_high - near * rng)
    return PriceZone(low=swing_low + near * rng, high=swing_low + far * rng)

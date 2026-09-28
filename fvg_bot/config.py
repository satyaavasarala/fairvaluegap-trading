from __future__ import annotations

from dataclasses import dataclass
from datetime import time, timedelta
from typing import Tuple

from fvg_bot.strategy.fib import DISCOUNT_ZONE


@dataclass(frozen=True)
class Config:
    # Session and entry window, America/New_York
    session_open: time = time(9, 30)
    entry_start: time = time(9, 45)
    entry_end: time = time(11, 30)

    # Setup detection (D1, D2, D5, D6)
    include_prior_zones: bool = True
    stop_buffer: float = 0.03
    zone_ratios: Tuple[float, float] = DISCOUNT_ZONE
    setup_window_bars: int = 5

    # ARMED state (section 6)
    arm_timeout: timedelta = timedelta(minutes=10)
    gap_guard_frac: float = 0.5

    # Contract selection (section 8). Volume, OI and premium bounds are placeholders.
    target_delta: float = 0.60
    delta_min: float = 0.55
    delta_max: float = 0.70
    max_spread: float = 0.05
    max_quote_age: timedelta = timedelta(seconds=2)
    min_volume: int = 100
    min_open_interest: int = 100
    min_premium: float = 0.50
    max_premium: float = 15.00

    # Sizing (section 3.3, D9, D10)
    risk_budget: float = 30.0
    daily_loss_limit: float = 50.0
    target_r: float = 4.0
    disaster_r: float = 1.5
    cost_filter_frac: float = 0.15
    exit_slippage_pad: float = 0.02
    disaster_slippage_pad: float = 0.05  # stop_limit's limit offset below the stop price
    min_stop_distance: float = 0.10

    def __post_init__(self) -> None:
        if self.target_r < 3.0:
            raise ValueError(f"target_r {self.target_r} is below the 3R net minimum")
        if not self.delta_min <= self.target_delta <= self.delta_max:
            raise ValueError("target_delta must lie within [delta_min, delta_max]")
        if not self.session_open <= self.entry_start < self.entry_end:
            raise ValueError("need session_open <= entry_start < entry_end")

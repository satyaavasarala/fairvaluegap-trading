from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Tuple

from fvg_bot.strategy.types import Bar

ORDERS = ("heuristic", "low_first", "high_first")


def _leg(a: float, b: float, step: float) -> List[float]:
    if abs(b - a) < 1e-12:
        return []
    sign = 1.0 if b > a else -1.0
    n = int(abs(b - a) / step + 1e-9)
    out = [a + sign * step * k for k in range(1, n + 1)]
    if not out or abs(out[-1] - b) > 1e-9:
        out.append(b)
    return out


def bar_path(bar: Bar, order: str = "heuristic") -> List[float]:
    """Waypoints through the bar. The heuristic assumes an up bar dipped first."""
    if order == "heuristic":
        order = "low_first" if bar.close >= bar.open else "high_first"
    if order == "low_first":
        return [bar.open, bar.low, bar.high, bar.close]
    if order == "high_first":
        return [bar.open, bar.high, bar.low, bar.close]
    raise ValueError(f"order must be one of {ORDERS}, got {order!r}")


def synthesize_ticks(bar: Bar, step: float = 0.01, order: str = "heuristic") -> List[Tuple[datetime, float]]:
    """Continuous price path through a 1m bar in `step` increments, timestamps strictly inside the bar.

    The only discontinuity is at the open, so gaps between bars survive as real jumps.
    """
    points = bar_path(bar, order)
    prices = [points[0]]
    for a, b in zip(points, points[1:]):
        prices.extend(_leg(a, b, step))
    dt = timedelta(minutes=1) / (len(prices) + 1)
    return [(bar.ts + dt * (k + 1), p) for k, p in enumerate(prices)]

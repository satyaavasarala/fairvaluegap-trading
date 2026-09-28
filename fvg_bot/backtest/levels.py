"""Fade the first touch of the prior-day high/low and the overnight high/low.

PDH/PDL: prior regular session. ONH/ONL: prior day's post-market plus today's premarket.
Side is fixed at the first in-window tick; a touch is the first tick at or through the level
from that side. The trade fades it, back toward the side price came from, with a fixed-dollar
stop so every touch has the same risk. Scored with the bracket tracker from rebalance.py.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from fvg_bot.backtest.rebalance import SESSION_OPEN, StudyResult, Touch, TouchTracker
from fvg_bot.backtest.replay import regular_close
from fvg_bot.backtest.ticks import synthesize_ticks
from fvg_bot.clock import ET
from fvg_bot.strategy.types import Bar, Direction

LEVELS = ("PDH", "PDL", "ONH", "ONL")


def day_levels(
    prior_regular: Sequence[Bar], overnight: Sequence[Bar]
) -> Dict[str, float]:
    levels: Dict[str, float] = {}
    if prior_regular:
        levels["PDH"] = max(b.high for b in prior_regular)
        levels["PDL"] = min(b.low for b in prior_regular)
    if overnight:
        levels["ONH"] = max(b.high for b in overnight)
        levels["ONL"] = min(b.low for b in overnight)
    return levels


def level_study(
    symbol: str,
    days: Sequence[date],
    load_day: Callable[[date], List[Bar]],
    window: Tuple[time, time],
    stop_dollars: float,
    window_name: str = "",
    session_close: Callable[[date], datetime] = regular_close,
) -> StudyResult:
    entry_start, entry_end = window
    result = StudyResult(symbol, window_name)
    prior_regular: List[Bar] = []
    prior_post: List[Bar] = []

    for day in days:
        result.sessions += 1
        bars = load_day(day)
        session_open = datetime.combine(day, SESSION_OPEN, tzinfo=ET)
        close = session_close(day)
        flatten_at = close - timedelta(minutes=15)
        premarket = [b for b in bars if b.ts < session_open]
        levels = day_levels(prior_regular, prior_post + premarket)
        side: Dict[str, int] = {}
        done: set = set()
        trackers: List[TouchTracker] = []

        for bar in bars:
            if bar.ts < session_open:
                continue
            in_window = entry_start <= bar.ts.time() <= entry_end
            if not trackers and not (in_window and len(done) < len(levels)):
                continue
            for ts, price in synthesize_ticks(bar):
                trackers = [trk for trk in trackers if not trk.on_tick(ts, price)]
                if not entry_start <= ts.time() <= entry_end:
                    continue
                for name, level in levels.items():
                    if name in done:
                        continue
                    if name not in side:
                        if price == level:
                            done.add(name)
                        else:
                            side[name] = 1 if price > level else -1
                        continue
                    if (side[name] == 1 and price <= level) or (side[name] == -1 and price >= level):
                        done.add(name)
                        direction = Direction.BULLISH if side[name] == 1 else Direction.BEARISH
                        stop = price - stop_dollars if side[name] == 1 else price + stop_dollars
                        touch = Touch(symbol, day, direction, level, level, ts, price, stop, kind=name)
                        result.touches.append(touch)
                        trackers.append(TouchTracker(touch, flatten_at))
        for trk in trackers:
            trk.close_out()
        prior_regular = [b for b in bars if session_open <= b.ts < close]
        prior_post = [b for b in bars if b.ts >= close]
    return result

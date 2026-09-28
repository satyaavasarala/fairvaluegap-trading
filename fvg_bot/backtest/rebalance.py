"""Does the raw 15m FVG rebalance carry directional information on its own?

Enter at the first tick inside an active 15m FVG during the entry window, in the FVG's
direction, stop beyond the far edge plus a buffer. No ChoCh, no Fib, no LTF FVG. Measured
gross (no option costs) with symmetric brackets and fixed horizons:

  bracket k: did +kR come before -1R? With no information the hit rate is 1 / (1 + k).
  horizon h: signed move h minutes after entry, in units of the stop distance.

Zone rules match the FSM: most recent zone per direction, prior-session zones kept,
invalidated by a 15m close beyond the far edge. Each zone is entered at most once.

    python3 -m fvg_bot.backtest.rebalance
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from multiprocessing import Pool
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from fvg_bot.backtest.d9 import percentile
from fvg_bot.backtest.replay import regular_close
from fvg_bot.backtest.ticks import synthesize_ticks
from fvg_bot.clock import ET
from fvg_bot.data.bars import Buckets
from fvg_bot.data.store import DEFAULT_ROOT, cached_days, load_1m_bars, load_calendar
from fvg_bot.strategy.fvg import FVG, fvg_at, in_zone, is_beyond_far_edge, is_invalidated
from fvg_bot.strategy.types import Bar, Direction

BRACKETS = (1, 2, 4)
HORIZONS = (15, 60)
WIN, LOSS, OPEN = "win", "loss", "open"
SESSION_OPEN = time(9, 30)
WINDOWS = {"am": (time(9, 45), time(11, 30)), "full": (time(9, 45), time(15, 0))}


@dataclass
class Touch:
    symbol: str
    day: date
    direction: Direction
    zone_bottom: float
    zone_top: float
    entry_at: datetime
    entry_price: float
    stop_level: float
    brackets: Dict[int, str] = field(default_factory=dict)
    horizons: Dict[int, Optional[float]] = field(default_factory=dict)

    @property
    def stop_distance(self) -> float:
        return abs(self.entry_price - self.stop_level)


class TouchTracker:
    def __init__(self, touch: Touch, flatten_at: datetime):
        self.t = touch
        self.sign = 1.0 if touch.direction is Direction.BULLISH else -1.0
        self.flatten_at = flatten_at
        self.pending_brackets = list(BRACKETS)
        self.pending_horizons = list(HORIZONS)

    @property
    def done(self) -> bool:
        return not self.pending_brackets and not self.pending_horizons

    def on_tick(self, ts: datetime, price: float) -> bool:
        t = self.t
        if ts >= self.flatten_at:
            self.close_out()
            return True
        move = self.sign * (price - t.entry_price)
        through_stop = self.sign * (price - t.stop_level) < 0
        for k in list(self.pending_brackets):
            if through_stop:
                t.brackets[k] = LOSS
            elif move >= k * t.stop_distance:
                t.brackets[k] = WIN
            else:
                continue
            self.pending_brackets.remove(k)
        for h in list(self.pending_horizons):
            if ts - t.entry_at >= timedelta(minutes=h):
                t.horizons[h] = move / t.stop_distance
                self.pending_horizons.remove(h)
        return self.done

    def close_out(self) -> None:
        for k in self.pending_brackets:
            self.t.brackets[k] = OPEN
        for h in self.pending_horizons:
            self.t.horizons[h] = None
        self.pending_brackets = []
        self.pending_horizons = []


@dataclass
class StudyResult:
    symbol: str
    window: str
    sessions: int = 0
    touches: List[Touch] = field(default_factory=list)
    gapped_through: int = 0


def study(
    symbol: str,
    days: Sequence[date],
    load_day: Callable[[date], List[Bar]],
    window: Tuple[time, time],
    window_name: str = "",
    stop_buffer: float = 0.03,
    session_close: Callable[[date], datetime] = regular_close,
) -> StudyResult:
    entry_start, entry_end = window
    result = StudyResult(symbol, window_name)
    zones: Dict[Direction, FVG] = {}
    consumed: Set[FVG] = set()
    bars_15m: List[Bar] = []

    def on_15m(bar: Bar) -> None:
        bars_15m.append(bar)
        del bars_15m[:-3]
        for d, z in list(zones.items()):
            if is_invalidated(z, bar):
                del zones[d]
        fvg = fvg_at(bars_15m, len(bars_15m) - 1)
        if fvg is not None:
            zones[fvg.direction] = fvg

    m15 = Buckets(15, on_15m)
    for day in days:
        result.sessions += 1
        session_open = datetime.combine(day, SESSION_OPEN, tzinfo=ET)
        flatten_at = session_close(day) - timedelta(minutes=15)
        trackers: List[TouchTracker] = []
        for bar in load_day(day):
            m15.add(bar)
            if bar.ts >= session_open:
                in_window = entry_start <= bar.ts.time() <= entry_end
                live = [z for z in zones.values() if z not in consumed] if in_window else []
                if trackers or live:
                    for ts, price in synthesize_ticks(bar):
                        trackers = [trk for trk in trackers if not trk.on_tick(ts, price)]
                        for z in live:
                            if z in consumed:
                                continue
                            if in_zone(z, price):
                                consumed.add(z)
                                bull = z.direction is Direction.BULLISH
                                touch = Touch(
                                    symbol, day, z.direction, z.bottom, z.top, ts, price,
                                    z.bottom - stop_buffer if bull else z.top + stop_buffer,
                                )
                                result.touches.append(touch)
                                trackers.append(TouchTracker(touch, flatten_at))
                            elif is_beyond_far_edge(z, price):
                                consumed.add(z)
                                result.gapped_through += 1
            m15.close_if_complete(bar)
        for trk in trackers:
            trk.close_out()
    m15.flush()
    return result


# ---- reporting -----------------------------------------------------------------


def _mean_ci(xs: Sequence[float]) -> Tuple[float, float]:
    n = len(xs)
    if n < 2:
        return (xs[0] if xs else float("nan")), float("nan")
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m, 1.96 * sd / math.sqrt(n)


def bracket_rate(touches: Sequence[Touch], k: int) -> Tuple[float, float, int, float]:
    """(win rate among resolved, 95% half-width, resolved count, share left open)."""
    wins = sum(t.brackets[k] == WIN for t in touches)
    losses = sum(t.brackets[k] == LOSS for t in touches)
    n = wins + losses
    if n == 0:
        return float("nan"), float("nan"), 0, float("nan")
    p = wins / n
    return p, 1.96 * math.sqrt(p * (1 - p) / n), n, 1 - n / len(touches)


def report_rows(label: str, touches: Sequence[Touch]) -> str:
    if not touches:
        return f"  {label}  n=   0"
    parts = [f"  {label}  n={len(touches):4d}  stop50=${percentile([t.stop_distance for t in touches], 50):.3f}"]
    for k in BRACKETS:
        p, ci, _, open_share = bracket_rate(touches, k)
        parts.append(f"{k}R {p:4.0%}±{ci * 100:2.0f} (null {1 / (1 + k):.0%}, open {open_share:3.0%})")
    for h in HORIZONS:
        m, ci = _mean_ci([t.horizons[h] for t in touches if t.horizons.get(h) is not None])
        parts.append(f"+{h}m {m:+.2f}±{ci:.2f}R")
    return "  ".join(parts)


def _run(args: Tuple[str, str, Tuple[date, ...], str, str, float]) -> StudyResult:
    symbol, window_name, days, root, feed, buffer = args
    cal = load_calendar(Path(root))
    return study(
        symbol, days, lambda d: load_1m_bars(Path(root), symbol, d, feed), WINDOWS[window_name],
        window_name, buffer, lambda d: cal[d].close if d in cal else regular_close(d),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", nargs="+", default=["SPY", "QQQ"])
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--feed", default="sip")
    p.add_argument("--holdout-start", type=date.fromisoformat, default=date(2026, 1, 1))
    p.add_argument("--buffer", type=float, default=0.03)
    args = p.parse_args(argv)

    symbols = [s.upper() for s in args.symbols]
    tasks = []
    for s in symbols:
        days = tuple(d for d in cached_days(args.root, s, args.feed) if d < args.holdout_start)
        for w in WINDOWS:
            tasks.append((s, w, days, str(args.root), args.feed, args.buffer))
    with Pool(len(tasks)) as pool:
        results = pool.map(_run, tasks)

    print(f"Raw 15m FVG rebalance, gross (no costs), days before {args.holdout_start}. "
          f"Entry at first touch, stop beyond far edge + ${args.buffer:.2f}.")
    print("kR = share of resolved trades reaching +kR before -1R, ±95%; +Nm = mean signed move N min later, in R.")
    for w in WINDOWS:
        rs = [r for r in results if r.window == w]
        lo, hi = WINDOWS[w]
        print(f"\n== window {w} ({lo:%H:%M}-{hi:%H:%M}) | sessions per symbol {rs[0].sessions} | "
              f"zones gapped through without a touch: {sum(r.gapped_through for r in rs)} ==")
        groups = [(r.symbol, r.touches) for r in rs] + [("ALL", [t for r in rs for t in r.touches])]
        for name, touches in groups:
            for dname, d in (("long ", Direction.BULLISH), ("short", Direction.BEARISH)):
                print(report_rows(f"{name:<4} {dname}", [t for t in touches if t.direction is d]))
            print(report_rows(f"{name:<4} both ", touches))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""E6: trade through PDH/PDL/ONH/ONL, scored under worst / heuristic / best intrabar ordering.

Pre-registered in docs/research_log.md (E6) before the first run. The pass bar applies to
the worst ordering only. Do not change tests, costs or the bar without recording it there.

    python3 -m fvg_bot.backtest.breakout
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from multiprocessing import Pool
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from fvg_bot.backtest.htf import COST, PASS_BRACKETS, PASS_Z, passes_values
from fvg_bot.backtest.levels import LEVELS, day_levels
from fvg_bot.backtest.rebalance import (
    LOSS,
    OPEN,
    SESSION_OPEN,
    WIN,
    WINDOWS,
    Touch,
    TouchTracker,
    mean_ci,
)
from fvg_bot.backtest.replay import regular_close
from fvg_bot.backtest.ticks import synthesize_ticks
from fvg_bot.clock import ET
from fvg_bot.data.store import DEFAULT_ROOT, cached_days, load_1m_bars, load_calendar
from fvg_bot.strategy.types import Bar, Direction

ORDERS = ("worst", "heuristic", "best")
STOPS = (0.50, 1.00)

# outcome, move at exit in R, bars after the entry bar until resolution (None if unknown)
Result = Tuple[str, float, Optional[int]]


@dataclass
class Breakout:
    symbol: str
    day: date
    kind: str
    direction: Direction
    entry_at: datetime
    entry_price: float
    stop_dollars: float
    results: Dict[str, Dict[int, Result]] = field(default_factory=dict)

    def net_r(self, order: str, k: int, cost: float = COST) -> float:
        return self.results[order][k][1] - cost / self.stop_dollars


def resolve_bars(
    direction: Direction,
    entry: float,
    stop_dollars: float,
    k: int,
    bars: Sequence[Bar],
    flatten_at: datetime,
    order: str,
) -> Result:
    """Score a bracket from bar ranges alone. bars[0] is the bar the entry happened in."""
    if order not in ("worst", "best"):
        raise ValueError(f"order must be worst or best, got {order!r}")
    sign = 1.0 if direction is Direction.BULLISH else -1.0
    stop = entry - sign * stop_dollars
    target = entry + sign * k * stop_dollars

    def r(price: float) -> float:
        return sign * (price - entry) / stop_dollars

    for j, bar in enumerate(bars):
        if bar.ts >= flatten_at:
            return OPEN, r(bar.open), j
        favourable = bar.high if sign > 0 else bar.low
        adverse = bar.low if sign > 0 else bar.high
        if j > 0:
            if sign * (bar.open - stop) < 0:
                return LOSS, r(bar.open), j
            if sign * (bar.open - target) >= 0:
                return WIN, r(bar.open), j
        hit_target = sign * (favourable - target) >= 0
        if j == 0 and order == "best":
            # Only the close is certainly after the entry.
            hit_stop = sign * (bar.close - stop) < 0
        else:
            hit_stop = sign * (adverse - stop) < 0
        if hit_stop and hit_target:
            return (LOSS, r(stop), j) if order == "worst" else (WIN, r(target), j)
        if hit_stop:
            return LOSS, r(stop), j
        if hit_target:
            return WIN, r(target), j
    return OPEN, r(bars[-1].close), None


def _heuristic(
    trade: Breakout, level: float, bars: Sequence[Bar], entry_ticks: Sequence[Tuple[datetime, float]],
    flatten_at: datetime,
) -> Dict[int, Result]:
    sign = 1.0 if trade.direction is Direction.BULLISH else -1.0
    touch = Touch(trade.symbol, trade.day, trade.direction, level, level, trade.entry_at,
                  trade.entry_price, trade.entry_price - sign * trade.stop_dollars, kind=trade.kind)
    tracker = TouchTracker(touch, flatten_at)

    def feed(ticks) -> bool:
        return any(tracker.on_tick(ts, p) for ts, p in ticks)

    if not feed(entry_ticks) and not any(feed(synthesize_ticks(b)) for b in bars[1:]):
        tracker.close_out()
    return {k: (touch.brackets[k], touch.bracket_r[k], None) for k in PASS_BRACKETS}


@dataclass
class BreakoutResult:
    symbol: str
    window: str
    stop_dollars: float
    sessions: int = 0
    trades: List[Breakout] = field(default_factory=list)


def breakout_study(
    symbol: str,
    days: Sequence[date],
    load_day: Callable[[date], List[Bar]],
    window: Tuple[time, time],
    stop_dollars: float,
    window_name: str = "",
    session_close: Callable[[date], datetime] = regular_close,
) -> BreakoutResult:
    entry_start, entry_end = window
    result = BreakoutResult(symbol, window_name, stop_dollars)
    prior_regular: List[Bar] = []
    prior_post: List[Bar] = []

    for day in days:
        result.sessions += 1
        bars = load_day(day)
        session_open = datetime.combine(day, SESSION_OPEN, tzinfo=ET)
        close = session_close(day)
        flatten_at = close - timedelta(minutes=15)
        levels = day_levels(prior_regular, prior_post + [b for b in bars if b.ts < session_open])
        session = [b for b in bars if b.ts >= session_open]
        side: Dict[str, int] = {}
        done: set = set()

        for idx, bar in enumerate(session):
            if len(done) == len(levels) or not entry_start <= bar.ts.time() <= entry_end:
                continue
            ticks = synthesize_ticks(bar)
            for ti, (ts, price) in enumerate(ticks):
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
                    if not ((side[name] == 1 and price <= level) or (side[name] == -1 and price >= level)):
                        continue
                    done.add(name)
                    direction = Direction.BEARISH if side[name] == 1 else Direction.BULLISH
                    trade = Breakout(symbol, day, name, direction, ts, price, stop_dollars)
                    trade.results = {
                        o: {k: resolve_bars(direction, price, stop_dollars, k, session[idx:], flatten_at, o)
                            for k in PASS_BRACKETS}
                        for o in ("worst", "best")
                    }
                    trade.results["heuristic"] = _heuristic(trade, level, session[idx:], ticks[ti + 1:], flatten_at)
                    result.trades.append(trade)
        prior_regular = [b for b in bars if session_open <= b.ts < close]
        prior_post = [b for b in bars if b.ts >= close]
    return result


# ---- reporting ---------------------------------------------------------------------


def hit_rate(trades: Sequence[Breakout], order: str, k: int) -> Tuple[float, float, float]:
    """(win share among resolved, 95% half-width, open share)."""
    outcomes = [t.results[order][k][0] for t in trades]
    wins, losses = outcomes.count(WIN), outcomes.count(LOSS)
    n = wins + losses
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    p = wins / n
    return p, 1.96 * (p * (1 - p) / n) ** 0.5, 1 - n / len(trades)


def row(label: str, trades: Sequence[Breakout], order: str) -> str:
    parts = [f"  {label:<10} {order:<9} n={len(trades):5d}"]
    for k in PASS_BRACKETS:
        p, ci, open_share = hit_rate(trades, order, k)
        net, net_ci = mean_ci([t.net_r(order, k) for t in trades])
        parts.append(f"{k}R hit {p:4.0%}±{ci * 100:2.0f} (null {1 / (1 + k):.0%}, open {open_share:3.0%}) "
                     f"net {net:+.3f}±{net_ci:.3f}")
    return "  ".join(parts)


def _run(args: Tuple[str, str, float, Tuple[date, ...], str, str]) -> BreakoutResult:
    symbol, window, stop, days, root, feed = args
    cal = load_calendar(Path(root))
    return breakout_study(
        symbol, days, lambda d: load_1m_bars(Path(root), symbol, d, feed), WINDOWS[window], stop, window,
        lambda d: cal[d].close if d in cal else regular_close(d),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", nargs="+", default=["SPY", "QQQ"])
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--feed", default="sip")
    p.add_argument("--holdout-start", type=date.fromisoformat, default=date(2026, 1, 1))
    args = p.parse_args(argv)

    symbols = [s.upper() for s in args.symbols]
    days = {s: tuple(d for d in cached_days(args.root, s, args.feed) if d < args.holdout_start) for s in symbols}
    tasks = [(s, w, st, days[s], str(args.root), args.feed) for st in STOPS for w in WINDOWS for s in symbols]
    with Pool(min(len(tasks), 9)) as pool:
        results = pool.map(_run, tasks)

    print(f"E6 level breakouts. Cost ${COST:.2f}/share, days before {args.holdout_start}. "
          f"PASS judged on the worst ordering: pooled net - {PASS_Z:g}xSE > 0 and every symbol's net > 0.")
    verdicts = []
    for stop in STOPS:
        for w in WINDOWS:
            rs = [r for r in results if r.stop_dollars == stop and r.window == w]
            by_symbol = {r.symbol: r.trades for r in rs}
            pooled = [t for ts in by_symbol.values() for t in ts]
            in_bar = sum(t.results["worst"][1][2] == 0 for t in pooled) / max(len(pooled), 1)
            print(f"\n== stop ${stop:.2f}, window {w} | sessions/symbol {rs[0].sessions} | "
                  f"1R resolved inside the entry bar (worst): {in_bar:.0%} ==")
            for label, trades in list(by_symbol.items()) + [("ALL", pooled)]:
                for order in ORDERS:
                    print(row(label, trades, order))
            for name in LEVELS:
                print(row(f"ALL {name}", [t for t in pooled if t.kind == name], "worst"))
            for k in PASS_BRACKETS:
                ok = passes_values({s: [t.net_r("worst", k) for t in ts] for s, ts in by_symbol.items()})
                verdicts.append((stop, w, k, ok))
                print(f"  -> {k}R bracket (worst): {'PASS' if ok else 'fail'}")

    passed = [v for v in verdicts if v[3]]
    print(f"\n{len(passed)} of {len(verdicts)} pre-registered checks passed"
          + (": " + ", ".join(f"${s:.2f}/{w}/{k}R" for s, w, k, _ in passed) if passed else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())

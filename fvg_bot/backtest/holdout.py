"""E7: the frozen breakout rule, evaluated once on the 2026 holdout.

Pre-registered in docs/research_log.md (E7). Everything below is frozen; changing it after
the run invalidates the test. Requires --i-understand-this-uses-the-holdout.

    python3 -m fvg_bot.backtest.holdout --i-understand-this-uses-the-holdout
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from fvg_bot.backtest.breakout import Breakout, breakout_study, hit_rate
from fvg_bot.backtest.rebalance import WINDOWS, mean_ci
from fvg_bot.backtest.replay import regular_close
from fvg_bot.data.store import DEFAULT_ROOT, cached_days, load_1m_bars, load_calendar

HOLDOUT_START = date(2026, 1, 1)
SYMBOLS = ("SPY", "QQQ")
WINDOW = "am"
STOP = 1.00
K = 1
ORDER = "worst"
COST = 0.02
STRESS_COST = 0.05
Z_ONE_SIDED = 1.645
MIN_N = 30

PASS, INCONCLUSIVE, FAIL = "PASS", "INCONCLUSIVE", "FAIL"


def decide(by_symbol: Dict[str, Sequence[Breakout]]) -> str:
    pooled = [t for ts in by_symbol.values() for t in ts]
    net = [t.net_r(ORDER, K, COST) for t in pooled]
    if not net:
        return INCONCLUSIVE
    mean, half = mean_ci(net, Z_ONE_SIDED)
    if mean <= 0:
        return FAIL
    stress = mean_ci([t.net_r(ORDER, K, STRESS_COST) for t in pooled])[0]
    symbols_positive = all(ts and mean_ci([t.net_r(ORDER, K, COST) for t in ts])[0] > 0 for ts in by_symbol.values())
    if len(net) >= MIN_N and mean - half > 0 and symbols_positive and stress > 0:
        return PASS
    return INCONCLUSIVE


def holdout_days(all_days: Sequence[date], start: date = HOLDOUT_START) -> List[date]:
    """Holdout days plus the one session before them, which only supplies prior-day levels."""
    before = [d for d in all_days if d < start]
    return before[-1:] + [d for d in all_days if d >= start]


def summary(label: str, trades: Sequence[Breakout]) -> str:
    if not trades:
        return f"  {label:<5} n=   0"
    p, ci, open_share = hit_rate(trades, ORDER, K)
    net, net_ci = mean_ci([t.net_r(ORDER, K, COST) for t in trades])
    stress, _ = mean_ci([t.net_r(ORDER, K, STRESS_COST) for t in trades])
    heur = mean_ci([t.net_r("heuristic", K, COST) for t in trades])[0]
    return (f"  {label:<5} n={len(trades):4d}  hit {p:4.0%}±{ci * 100:2.0f} (open {open_share:3.0%})  "
            f"net@${COST:.2f} {net:+.3f}±{net_ci:.3f} (95% two-sided)  net@${STRESS_COST:.2f} {stress:+.3f}  "
            f"[heuristic net {heur:+.3f}]")


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--feed", default="sip")
    p.add_argument("--i-understand-this-uses-the-holdout", dest="confirmed", action="store_true")
    args = p.parse_args(argv)
    if not args.confirmed:
        print("Refusing to run: this spends the 2026 holdout. See docs/research_log.md E7.", file=sys.stderr)
        return 2

    cal = load_calendar(args.root)
    by_symbol: Dict[str, List[Breakout]] = {}
    for symbol in SYMBOLS:
        days = holdout_days(cached_days(args.root, symbol, args.feed))
        r = breakout_study(
            symbol, days, lambda d, s=symbol: load_1m_bars(args.root, s, d, args.feed), WINDOWS[WINDOW], STOP,
            WINDOW, lambda d: cal[d].close if d in cal else regular_close(d),
        )
        by_symbol[symbol] = [t for t in r.trades if t.day >= HOLDOUT_START]
        span = [d for d in days if d >= HOLDOUT_START]
        print(f"{symbol}: {len(span)} holdout sessions, {span[0]} .. {span[-1]}")

    print(f"\nE7 frozen rule: PDH/PDL/ONH/ONL breakout, {WINDOWS[WINDOW][0]:%H:%M}-{WINDOWS[WINDOW][1]:%H:%M}, "
          f"stop ${STOP:.2f}, target {K}R, {ORDER}-case ordering")
    pooled = [t for ts in by_symbol.values() for t in ts]
    for label, trades in list(by_symbol.items()) + [("ALL", pooled)]:
        print(summary(label, trades))
    net = [t.net_r(ORDER, K, COST) for t in pooled]
    if net:
        mean, half = mean_ci(net, Z_ONE_SIDED)
        print(f"\n  one-sided 95% lower bound (pooled, ${COST:.2f}): {mean - half:+.3f}R")
    print(f"\nDECISION: {decide(by_symbol)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

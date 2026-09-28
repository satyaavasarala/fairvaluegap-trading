"""D9: stop-distance distribution of replayed setups, and what it implies for RISK_BUDGET.

    python3 -m fvg_bot.backtest.d9
    python3 -m fvg_bot.backtest.d9 --symbols SPY --spread 0.01 --start 2025-01-01

No historical option quotes exist here, so option costs use an assumed spread (--spread)
on a flat delta (--delta) and go through the real size_trade().
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import Counter
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from fvg_bot.backtest.replay import ReplayResult, SetupRecord, replay
from fvg_bot.config import Config
from fvg_bot.data.store import DEFAULT_ROOT, cached_days, load_1m_bars
from fvg_bot.options.sizing import size_trade

BUDGETS = (25, 30, 40, 50, 75, 100)
PASS = "pass"


def percentile(values: Sequence[float], p: float) -> float:
    s = sorted(values)
    if not s:
        raise ValueError("no values")
    k = (len(s) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def histogram(values: Sequence[float], bin_width: float, cap: float, width: int = 40) -> List[str]:
    """Text histogram; values >= cap fall into one overflow row."""
    nbins = max(1, int(round(cap / bin_width)))
    counts = [0] * (nbins + 1)
    for v in values:
        counts[min(int(v / bin_width + 1e-9), nbins)] += 1
    peak = max(counts) or 1
    lines = []
    for i, c in enumerate(counts):
        label = f">= {nbins * bin_width:5.2f}" if i == nbins else f"{i * bin_width:5.2f}-{(i + 1) * bin_width:5.2f}"
        lines.append(f"  {label:>11} | {'#' * round(c / peak * width):<{width}} {c}")
    return lines


def sizing_outcomes(stop_distances: Iterable[float], cfg: Config, spread: float, delta: float) -> Counter:
    bid = 2.00
    out: Counter = Counter()
    for sd in stop_distances:
        sizing, reason = size_trade(sd, 0.0, delta, bid, bid + spread, cfg)
        out[PASS if sizing else reason] += 1
    return out


def budget_rows(
    stop_distances: Sequence[float], cfg: Config, spread: float, delta: float, budgets=BUDGETS
) -> List[Tuple[float, float, float]]:
    """(budget, pass fraction at cfg.daily_loss_limit, pass fraction with no daily cap)."""
    n = len(stop_distances) or 1
    rows = []
    for b in budgets:
        capped = sizing_outcomes(stop_distances, replace(cfg, risk_budget=b), spread, delta)
        uncapped = sizing_outcomes(
            stop_distances, replace(cfg, risk_budget=b, daily_loss_limit=1e9), spread, delta
        )
        rows.append((b, capped[PASS] / n, uncapped[PASS] / n))
    return rows


def symbol_report(r: ReplayResult, cfg: Config, spread: float, delta: float) -> List[str]:
    trig = r.triggered
    armed_days = len({s.day for s in r.setups})
    lines = [
        f"== {r.symbol} ==",
        f"sessions {r.sessions} | armed setups {len(r.setups)} on {armed_days} days"
        f" | triggered {len(trig)} ({len(trig) / max(r.sessions, 1):.0%} of sessions)",
    ]
    if not trig:
        return lines + ["no triggered setups"]

    sd = [s.stop_distance for s in trig]
    bps = [s.stop_distance / s.trigger_price * 1e4 for s in trig]
    ps = (10, 25, 50, 75, 90)
    lines.append("stop distance at fill   " + "  ".join(f"p{p}={percentile(sd, p):.3f}" for p in ps))
    lines.append("  in bps of price       " + "  ".join(f"p{p}={percentile(bps, p):.1f}" for p in ps))
    lines.append("histogram of stop distance ($):")
    lines += histogram(sd, 0.05, cap=round(percentile(sd, 95) / 0.05) * 0.05 or 0.05)

    cost = spread + cfg.exit_slippage_pad
    lines.append(
        f"sizing with spread ${spread:.2f} + exit pad ${cfg.exit_slippage_pad:.2f}, delta {delta:.2f}, "
        f"RISK_BUDGET ${cfg.risk_budget:.0f}, DAILY_LOSS_LIMIT ${cfg.daily_loss_limit:.0f}:"
    )
    for reason, c in sizing_outcomes(sd, cfg, spread, delta).most_common():
        lines.append(f"  {reason:<45} {c:5d}  {c / len(sd):6.1%}")

    lines.append("RISK_BUDGET   pass@daily_limit   pass@no_daily_cap")
    for b, capped, uncapped in budget_rows(sd, cfg, spread, delta):
        lines.append(f"  ${b:<10.0f} {capped:>16.1%} {uncapped:>19.1%}")

    for p in (50, 75, 90):
        r_prem = percentile(sd, p) * delta + cost
        worst = 100 * (cfg.disaster_r * r_prem + cfg.disaster_slippage_pad)
        lines.append(
            f"  p{p} trade: R ${100 * r_prem:.0f}/contract, disaster loss ${worst:.0f}/contract"
        )
    return lines


def write_setups_csv(path: Path, setups: Sequence[SetupRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "symbol", "day", "direction", "armed_at", "entry_level", "stop_level", "fvg_bottom",
        "fvg_top", "swing_low", "swing_high", "triggered", "trigger_at", "trigger_price",
        "stop_distance",
    ]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for s in setups:
            w.writerow([
                s.symbol, s.day.isoformat(), s.direction.value, s.armed_at.isoformat(),
                s.entry_level, s.stop_level, s.fvg_bottom, s.fvg_top, s.swing_low, s.swing_high,
                s.triggered, s.trigger_at.isoformat() if s.trigger_at else "",
                "" if s.trigger_price is None else s.trigger_price, round(s.stop_distance, 4),
            ])


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", nargs="+", default=["SPY", "QQQ"])
    p.add_argument("--start", type=date.fromisoformat, default=None)
    p.add_argument("--end", type=date.fromisoformat, default=None)
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--feed", default="sip")
    p.add_argument("--spread", type=float, default=0.02, help="assumed option bid-ask spread")
    p.add_argument("--delta", type=float, default=0.60)
    p.add_argument("--out", type=Path, default=Path("backtest_output"))
    args = p.parse_args(argv)

    cfg = Config()
    all_reasons: Counter = Counter()
    for symbol in (s.upper() for s in args.symbols):
        days = [
            d for d in cached_days(args.root, symbol, args.feed)
            if (args.start is None or d >= args.start) and (args.end is None or d <= args.end)
        ]
        if not days:
            print(f"{symbol}: no cached days", file=sys.stderr)
            continue
        t0 = time.monotonic()
        result = replay(symbol, days, lambda d, s=symbol: load_1m_bars(args.root, s, d, args.feed), cfg)
        elapsed = time.monotonic() - t0
        write_setups_csv(args.out / f"d9_setups_{symbol}.csv", result.setups)
        print("\n".join(symbol_report(result, cfg, args.spread, args.delta)))
        print(f"  ({days[0]} .. {days[-1]}, replayed in {elapsed:.0f}s, setups -> {args.out}/d9_setups_{symbol}.csv)\n")
        all_reasons.update(result.reasons)

    print("FSM transition reasons (all symbols):")
    for reason, c in all_reasons.most_common():
        print(f"  {c:7d}  {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

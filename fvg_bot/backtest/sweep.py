"""Variant sweep: D2 buffer, D5 zone, D6 window, stop placement, armed-expiry rule, minimum
1m FVG width. Each variant runs twice: unfiltered (every trigger taken, measures the raw
signal) and filtered (the real sizing filters on a synthetic quote, as the live bot would).

    python3 -m fvg_bot.backtest.sweep                 # spec grid (1m, morning window)
    python3 -m fvg_bot.backtest.sweep --grid ltf      # 1m vs 5m LTF, morning vs full day
    python3 -m fvg_bot.backtest.sweep --spread 0.01 --workers 8

Only days before --holdout-start are used. Leave the holdout alone until variants are
chosen, then evaluate those few on it once.

Outcomes are a constant-delta proxy (no theta, no gamma, assumed spread); see outcome.py.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import math
import os
import pickle
import sys
from time import monotonic
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, time, timedelta
from multiprocessing import Pool
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from fvg_bot.backtest.d9 import percentile
from fvg_bot.backtest.outcome import DEFAULT_EXIT, TP, CostModel, exit_rules
from fvg_bot.backtest.replay import ReplayResult, SetupRecord, regular_close, replay
from fvg_bot.config import Config
from fvg_bot.data.store import DEFAULT_ROOT, cached_days, load_1m_bars, load_calendar

Grid = Dict[str, tuple]
GRIDS: Dict[str, Grid] = {
    "spec": {
        "stop_mode": ("fvg", "swing"),
        "stop_buffer": (0.0, 0.03, 0.05),
        "setup_window_bars": (5, 10, 15),
        "zone_ratios": ((0.5, 0.618), (0.618, 0.65)),
        "abort_on_new_extreme": (True, False),
        "min_fvg_width": (0.0, 0.05),
    },
    "ltf": {
        "ltf_minutes": (1, 5),
        "entry_end": (time(11, 30), time(15, 0)),
        "stop_mode": ("fvg", "swing"),
        "setup_window_bars": (5, 10),
        "abort_on_new_extreme": (True, False),
        "min_fvg_width": (0.0, 0.05),
        "arm_timeout": (timedelta(minutes=10), timedelta(minutes=30)),
    },
}
MIN_N_FOR_RANKING = 30

_FORMAT = {
    "ltf_minutes": lambda v: f"ltf{v}m",
    "entry_end": lambda v: "am  " if v <= time(11, 30) else "full",
    "stop_mode": lambda v: f"{v:<5}",
    "stop_buffer": lambda v: f"buf{v:.2f}",
    "setup_window_bars": lambda v: f"win{v:<2}",
    "zone_ratios": lambda v: f"z{v[0] * 100:g}-{v[1] * 100:g}",
    "abort_on_new_extreme": lambda v: "abort" if v else "keep ",
    "min_fvg_width": lambda v: f"minw{v:.2f}",
    "arm_timeout": lambda v: f"arm{int(v.total_seconds() // 60):<2}",
}

Overrides = Tuple[Tuple[str, object], ...]


def baseline(grid: Grid) -> Overrides:
    cfg = Config()
    return tuple((k, getattr(cfg, k)) for k in grid)


def variant_grid(grid: Grid) -> List[Overrides]:
    keys = list(grid)
    return [tuple(zip(keys, values)) for values in itertools.product(*grid.values())]


def variant_name(ov: Overrides) -> str:
    return " ".join(_FORMAT[k](v) for k, v in ov)


def one_factor_variants(grid: Grid) -> List[Overrides]:
    """Baseline, then each single change from it."""
    base = baseline(grid)
    base_values = dict(base)
    out = [base]
    for key, values in grid.items():
        for v in values:
            if v != base_values[key]:
                out.append(tuple((k, v if k == key else bv) for k, bv in base))
    return out


def pair_variants(grid: Grid, a: str, b: str) -> List[Overrides]:
    """Every combination of keys a and b, everything else at baseline."""
    base = baseline(grid)
    return [
        tuple((k, va if k == a else vb if k == b else bv) for k, bv in base)
        for va in grid[a]
        for vb in grid[b]
    ]


@dataclass(frozen=True)
class Task:
    symbol: str
    overrides: Overrides
    filtered: bool
    days: Tuple[date, ...]
    root: str
    feed: str
    spread: float
    delta: float


_BARS: Dict[Tuple[str, date], list] = {}
_CAL: Dict[str, dict] = {}


def _load(root: str, feed: str, symbol: str, day: date) -> list:
    key = (symbol, day)
    if key not in _BARS:
        _BARS[key] = load_1m_bars(Path(root), symbol, day, feed)
    return _BARS[key]


def run_task(task: Task) -> Tuple[Task, ReplayResult]:
    cfg = replace(Config(), **dict(task.overrides))
    costs = CostModel.from_config(cfg, task.spread, task.delta)
    if task.root not in _CAL:
        _CAL[task.root] = load_calendar(Path(task.root))
    cal = _CAL[task.root]
    kwargs = {"entry_check": costs.entry_check(cfg)} if task.filtered else {}
    result = replay(
        task.symbol,
        task.days,
        lambda d: _load(task.root, task.feed, task.symbol, d),
        cfg,
        exit_rules=exit_rules(),
        costs=costs,
        session_close=lambda d: cal[d].close if d in cal else regular_close(d),
        **kwargs,
    )
    return task, result


@dataclass(frozen=True)
class Stats:
    n: int
    sessions: int
    win: float
    mean_r: float
    ci: float
    gross_r: float
    total_r: float
    stop_p50: float
    cost_ok: float

    @property
    def per_100(self) -> float:
        return 100 * self.n / self.sessions if self.sessions else 0.0


def compute_stats(trades: Sequence[SetupRecord], sessions: int, exit_name: str, costs: CostModel, cfg: Config) -> Stats:
    nan = float("nan")
    if not trades:
        return Stats(0, sessions, nan, nan, nan, nan, 0.0, nan, nan)
    rs = [t.outcomes[exit_name].net_r for t in trades]
    n = len(rs)
    mean = sum(rs) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rs) / (n - 1)) if n > 1 else nan
    return Stats(
        n=n,
        sessions=sessions,
        win=sum(t.outcomes[exit_name].reason == TP for t in trades) / n,
        mean_r=mean,
        ci=1.96 * sd / math.sqrt(n) if n > 1 else nan,
        gross_r=sum(t.outcomes[exit_name].gross_r for t in trades) / n,
        total_r=sum(rs),
        stop_p50=percentile([t.stop_distance for t in trades], 50),
        cost_ok=sum(
            costs.round_trip <= cfg.cost_filter_frac * costs.r_prem(t.stop_distance) for t in trades
        ) / n,
    )


def format_row(label: str, s: Stats) -> str:
    if s.n == 0:
        return f"  {label}  n=   0"
    return (
        f"  {label}  n={s.n:4d} ({s.per_100:4.1f}/100 sess)  stop50=${s.stop_p50:.3f}  "
        f"costOK={s.cost_ok:4.0%}  win={s.win:4.0%}  netR={s.mean_r:+.2f}±{s.ci:.2f}  "
        f"grossR={s.gross_r:+.2f}  totalR={s.total_r:+.0f}"
    )


MODES = {False: "unfilt", True: "filt  "}


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", nargs="+", default=["SPY", "QQQ"])
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--feed", default="sip")
    p.add_argument("--holdout-start", type=date.fromisoformat, default=date(2026, 1, 1))
    p.add_argument("--spread", type=float, default=0.02)
    p.add_argument("--delta", type=float, default=0.60)
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    p.add_argument("--grid", choices=sorted(GRIDS), default="spec")
    p.add_argument("--one-factor", action="store_true", help="only baseline and single changes")
    p.add_argument("--from-cache", action="store_true", help="rebuild the report from the last run's results.pkl")
    p.add_argument("--out", type=Path, default=None, help="default: backtest_output/sweep_<grid>")
    args = p.parse_args(argv)

    symbols = [s.upper() for s in args.symbols]
    days = {s: tuple(d for d in cached_days(args.root, s, args.feed) if d < args.holdout_start) for s in symbols}
    grid = GRIDS[args.grid]
    args.out = args.out or Path(f"backtest_output/sweep_{args.grid}")
    variants = one_factor_variants(grid) if args.one_factor else variant_grid(grid)
    tasks = [
        Task(s, v, f, days[s], str(args.root), args.feed, args.spread, args.delta)
        for v in variants
        for f in (False, True)
        for s in symbols
    ]
    first = min(d[0] for d in days.values())
    last = max(d[-1] for d in days.values())
    print(f"{len(variants)} variants x 2 modes x {len(symbols)} symbols = {len(tasks)} runs, "
          f"{first} .. {last} (holdout from {args.holdout_start} untouched), {args.workers} workers")

    args.out.mkdir(parents=True, exist_ok=True)
    cache = args.out / "results.pkl"
    t0 = monotonic()
    if args.from_cache:
        with open(cache, "rb") as f:
            combined: Dict[Tuple[Overrides, bool], List[ReplayResult]] = pickle.load(f)
        print(f"loaded {cache}")
    else:
        combined = defaultdict(list)
        with Pool(args.workers) as pool:
            for i, (task, result) in enumerate(pool.imap_unordered(run_task, tasks), 1):
                combined[(task.overrides, task.filtered)].append(result)
                if i % 50 == 0 or i == len(tasks):
                    print(f"  {i}/{len(tasks)} runs, {monotonic() - t0:.0f}s", flush=True)
        with open(cache, "wb") as f:
            pickle.dump(dict(combined), f)

    rules = [r.name for r in exit_rules()]
    stats: Dict[Tuple[Overrides, bool, str], Stats] = {}
    with open(args.out / "trades.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["variant", "mode", "symbol", "day", "direction", "trigger_at", "trigger_price",
                    "stop_level", "stop_distance"] + [f"{r}_{c}" for r in rules for c in ("reason", "net_r")])
        for (ov, filtered), results in combined.items():
            cfg = replace(Config(), **dict(ov))
            costs = CostModel.from_config(cfg, args.spread, args.delta)
            trades = [t for r in results for t in r.triggered]
            sessions = sum(r.sessions for r in results)
            for rule in rules:
                stats[(ov, filtered, rule)] = compute_stats(trades, sessions, rule, costs, cfg)
            for t in trades:
                w.writerow([variant_name(ov), MODES[filtered].strip(), t.symbol, t.day, t.direction.value,
                            t.trigger_at.isoformat(), t.trigger_price, t.stop_level, round(t.stop_distance, 4)]
                           + [x for r in rules for x in (t.outcomes[r].reason, round(t.outcomes[r].net_r, 4))])

    with open(args.out / "summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(list(grid) + ["mode", "exit", "n", "sessions", "win", "mean_net_r", "ci95", "gross_r",
                                 "total_net_r", "stop_p50", "cost_ok"])
        for (ov, filtered, rule), s in stats.items():
            w.writerow([dict(ov)[k] for k in grid] + [MODES[filtered].strip(), rule, s.n, s.sessions, s.win,
                                                      s.mean_r, s.ci, s.gross_r, s.total_r, s.stop_p50, s.cost_ok])

    base = baseline(grid)
    print(f"\nGrid '{args.grid}'. Exit rule {DEFAULT_EXIT} unless noted. Spread ${args.spread:.2f}, delta {args.delta:.2f}. "
          f"netR includes costs; grossR = underlying move / stop distance.")
    print("\n== Baseline and one change at a time ==")
    def print_variants(variants: Sequence[Overrides]) -> None:
        for ov in variants:
            for filtered in (False, True):
                if (ov, filtered, DEFAULT_EXIT) in stats:
                    tag = "BASE " if ov == base else "     "
                    print(format_row(f"{tag}{variant_name(ov)} {MODES[filtered]}", stats[(ov, filtered, DEFAULT_EXIT)]))

    print_variants(one_factor_variants(grid))
    if "ltf_minutes" in grid and "entry_end" in grid:
        for stop_mode in grid.get("stop_mode", (Config().stop_mode,)):
            print(f"\n== LTF x entry window, stop {stop_mode}, rest at baseline ==")
            print_variants([
                tuple((k, stop_mode if k == "stop_mode" else v) for k, v in ov)
                for ov in pair_variants(grid, "ltf_minutes", "entry_end")
            ])

    for filtered in (False, True):
        ranked = sorted(
            (
                (s, ov) for (ov, fl, rule), s in stats.items()
                if fl == filtered and rule == DEFAULT_EXIT and s.n >= MIN_N_FOR_RANKING
            ),
            key=lambda x: x[0].mean_r,
            reverse=True,
        )
        print(f"\n== Top 10 by mean net R, {MODES[filtered].strip()}, n >= {MIN_N_FOR_RANKING} "
              f"({len(ranked)} qualify; in-sample, expect regression to the mean) ==")
        for s, ov in ranked[:10]:
            print(format_row(variant_name(ov), s))

    best = max(
        ((s, ov) for (ov, fl, rule), s in stats.items() if not fl and rule == DEFAULT_EXIT and s.n >= MIN_N_FOR_RANKING),
        key=lambda x: x[0].mean_r,
        default=None,
    )
    for label, ov in (("baseline", base), ("top unfiltered", best[1] if best else None)):
        if ov is None or (ov, False, DEFAULT_EXIT) not in stats:
            continue
        print(f"\n== Exit rules (D3 x D4), {label}: {variant_name(ov)}, unfiltered ==")
        for rule in rules:
            print(format_row(f"{rule:<9}", stats[(ov, False, rule)]))

    print(f"\nfiles: {args.out}/summary.csv, {args.out}/trades.csv  ({monotonic() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

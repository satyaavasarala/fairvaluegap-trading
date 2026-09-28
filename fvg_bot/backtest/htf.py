"""E5: higher-timeframe levels with a shares cost model and a $0.50 stop floor.

Pre-registered in docs/research_log.md (E5) before the first run. Do not change the tests,
cost or pass bar here without recording it there.

    python3 -m fvg_bot.backtest.htf
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import date
from multiprocessing import Pool
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from fvg_bot.backtest.d9 import percentile
from fvg_bot.backtest.levels import LEVELS, level_study
from fvg_bot.backtest.rebalance import WINDOWS, StudyResult, Touch, mean_ci, bracket_rate, study
from fvg_bot.backtest.replay import regular_close
from fvg_bot.data.store import DEFAULT_ROOT, cached_days, load_1m_bars, load_calendar

COST = 0.02  # round trip per share: penny spread + $0.01 slippage
STOP_FLOOR = 0.50
PASS_Z = 3.0  # 16 looks; 1.96 would give ~56% odds of a false pass
PASS_BRACKETS = (1, 2)
PASS_MIN_N = 30  # pooled; guards against a tiny zero-variance sample passing

TESTS: Dict[str, dict] = {
    "fvg_1h": {"kind": "fvg", "zone_tf": "60m"},
    "fvg_1d": {"kind": "fvg", "zone_tf": "1d"},
    "levels_0.50": {"kind": "level", "stop": 0.50},
    "levels_1.00": {"kind": "level", "stop": 1.00},
}


def net_rs(touches: Sequence[Touch], k: int, cost: float = COST) -> List[float]:
    return [t.net_r(k, cost) for t in touches]


def passes_values(by_symbol: Dict[str, Sequence[float]], z: float = PASS_Z, min_n: int = PASS_MIN_N) -> bool:
    """Pre-registered bar: pooled mean - z x SE > 0, every symbol's mean > 0, pooled n >= min_n."""
    pooled = [x for xs in by_symbol.values() for x in xs]
    if len(pooled) < min_n or any(not xs for xs in by_symbol.values()):
        return False
    mean, half = mean_ci(pooled, z)
    if math.isnan(half) or mean - half <= 0:
        return False
    return all(mean_ci(xs)[0] > 0 for xs in by_symbol.values())


def passes(by_symbol: Dict[str, Sequence[Touch]], k: int, cost: float = COST, z: float = PASS_Z) -> bool:
    return passes_values({s: net_rs(ts, k, cost) for s, ts in by_symbol.items()}, z)


def _run(args: Tuple[str, str, str, Tuple[date, ...], str, str]) -> Tuple[str, StudyResult]:
    test, symbol, window, days, root, feed = args
    spec = TESTS[test]
    cal = load_calendar(Path(root))
    close = lambda d: cal[d].close if d in cal else regular_close(d)  # noqa: E731
    load = lambda d: load_1m_bars(Path(root), symbol, d, feed)  # noqa: E731
    if spec["kind"] == "fvg":
        r = study(symbol, days, load, WINDOWS[window], window, 0.03, close,
                  zone_tf=spec["zone_tf"], min_stop=STOP_FLOOR)
    else:
        r = level_study(symbol, days, load, WINDOWS[window], spec["stop"], window, close)
    return test, r


def row(label: str, touches: Sequence[Touch]) -> str:
    if not touches:
        return f"  {label:<12} n=   0"
    out = [f"  {label:<12} n={len(touches):5d}  stop50=${percentile([t.stop_distance for t in touches], 50):.2f}"]
    for k in PASS_BRACKETS:
        p, ci, _, open_share = bracket_rate(touches, k)
        gross, _ = mean_ci([t.bracket_r[k] for t in touches])
        net, net_ci = mean_ci(net_rs(touches, k))
        out.append(
            f"{k}R hit {p:4.0%}±{ci * 100:2.0f} (null {1 / (1 + k):.0%}, open {open_share:3.0%}) "
            f"gross {gross:+.3f} net {net:+.3f}±{net_ci:.3f}"
        )
    return "  ".join(out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", nargs="+", default=["SPY", "QQQ"])
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--feed", default="sip")
    p.add_argument("--holdout-start", type=date.fromisoformat, default=date(2026, 1, 1))
    args = p.parse_args(argv)

    symbols = [s.upper() for s in args.symbols]
    days = {s: tuple(d for d in cached_days(args.root, s, args.feed) if d < args.holdout_start) for s in symbols}
    tasks = [(t, s, w, days[s], str(args.root), args.feed) for t in TESTS for w in WINDOWS for s in symbols]
    with Pool(min(len(tasks), 9)) as pool:
        results = pool.map(_run, tasks)

    print(f"E5 higher-timeframe levels. Shares cost ${COST:.2f}/share round trip, stop floor ${STOP_FLOOR:.2f}, "
          f"days before {args.holdout_start}.")
    print(f"hit = +kR before -1R among resolved (±95%); gross/net = mean R per trade, net ±95%. "
          f"PASS needs pooled net - {PASS_Z:g}xSE > 0 and every symbol's net > 0.")
    verdicts = []
    for test in TESTS:
        for w in WINDOWS:
            rs = [r for t, r in results if t == test and r.window == w]
            by_symbol = {r.symbol: r.touches for r in rs}
            skipped = sum(r.skipped_tight for r in rs)
            print(f"\n== {test}, window {w} | sessions/symbol {rs[0].sessions}"
                  + (f" | skipped under stop floor: {skipped}" if TESTS[test]["kind"] == "fvg" else "") + " ==")
            for sym, ts in by_symbol.items():
                print(row(sym, ts))
            pooled = [t for ts in by_symbol.values() for t in ts]
            print(row("ALL", pooled))
            if TESTS[test]["kind"] == "level":
                for name in LEVELS:
                    print(row(f"  ALL {name}", [t for t in pooled if t.kind == name]))
            for k in PASS_BRACKETS:
                ok = passes(by_symbol, k)
                verdicts.append((test, w, k, ok))
                print(f"  -> {k}R bracket: {'PASS' if ok else 'fail'}")

    passed = [v for v in verdicts if v[3]]
    print(f"\n{len(passed)} of {len(verdicts)} pre-registered checks passed"
          + (": " + ", ".join(f"{t}/{w}/{k}R" for t, w, k, _ in passed) if passed else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Download-once cache of Alpaca 1m stock bars and the NYSE calendar.

    python3 -m fvg_bot.data.download --start 2024-02-01
    python3 -m fvg_bot.data.download --symbols SPY --start 2025-01-01 --end 2025-06-30

Days already on disk are never re-requested. Reads APCA_API_KEY_ID / APCA_API_SECRET_KEY.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from fvg_bot.clock import ET, to_et
from fvg_bot.data.alpaca_history import AlpacaHistory
from fvg_bot.data.store import DEFAULT_ROOT, has_day, parse_rfc3339, write_calendar, write_day

CHUNK_DAYS = 10  # ~960 extended-hours bars/day keeps a chunk inside one 10k page


def _year_ranges(start: date, end: date):
    while start <= end:
        stop = min(date(start.year, 12, 31), end)
        yield start, stop
        start = stop + timedelta(days=1)


def download_stock_bars(
    client: AlpacaHistory,
    root: Path,
    symbols: Sequence[str],
    start: date,
    end: date,
    feed: str = "sip",
    today: Optional[date] = None,
    log: Callable[[str], None] = print,
) -> Dict[str, int]:
    """Fill in missing trading days. Returns the number of day files written per symbol."""
    today = today or datetime.now(ET).date()
    if end >= today:
        end = today - timedelta(days=1)
        log(f"end clamped to {end}: the current session is incomplete")
    if end < start:
        return {s: 0 for s in symbols}

    sessions = []
    for a, b in _year_ranges(start, end):
        sessions.extend(client.calendar(a, b))
    write_calendar(root, sessions)
    days = sorted(s.day for s in sessions if start <= s.day <= end)

    written: Dict[str, int] = {}
    for symbol in symbols:
        missing = [d for d in days if not has_day(root, symbol, d, feed)]
        log(f"{symbol}: {len(days) - len(missing)} of {len(days)} days cached, fetching {len(missing)}")
        written[symbol] = 0
        for i in range(0, len(missing), CHUNK_DAYS):
            chunk = missing[i : i + CHUNK_DAYS]
            rows = client.stock_bars(
                symbol,
                datetime.combine(chunk[0], time(0, 0), tzinfo=ET),
                datetime.combine(chunk[-1], time(23, 59, 59), tzinfo=ET),
                feed=feed,
            )
            by_day: Dict[date, List[dict]] = defaultdict(list)
            for r in rows:
                by_day[to_et(parse_rfc3339(r["t"])).date()].append(r)
            for d in chunk:
                if not by_day.get(d):
                    log(f"{symbol} {d}: no bars returned, not cached (will retry next run)")
                    continue
                write_day(root, symbol, d, feed, by_day[d])
                written[symbol] += 1
            log(f"{symbol}: through {chunk[-1]}")
    return written


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", nargs="+", default=["SPY", "QQQ"])
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, default=None, help="default: yesterday (ET)")
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--feed", choices=["sip", "iex"], default="sip")
    args = p.parse_args(argv)

    key, secret = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
    if not key or not secret:
        print("Set APCA_API_KEY_ID and APCA_API_SECRET_KEY (paper keys work).", file=sys.stderr)
        return 2
    today = datetime.now(ET).date()
    written = download_stock_bars(
        AlpacaHistory(key, secret),
        args.root,
        [s.upper() for s in args.symbols],
        args.start,
        args.end or today - timedelta(days=1),
        feed=args.feed,
        today=today,
    )
    print(f"done: {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

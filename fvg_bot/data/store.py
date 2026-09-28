"""On-disk cache of downloaded market data.

historical_data/
  calendar.csv                                  date, open, close (ET, with offset)
  stocks/1min/<feed>/<SYMBOL>/<YYYY-MM-DD>.csv  one file per ET trading day, extended hours
"""
from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence

from fvg_bot.clock import to_et
from fvg_bot.strategy.types import Bar

DEFAULT_ROOT = Path("historical_data")
BAR_FIELDS = ["ts", "open", "high", "low", "close", "volume", "trade_count", "vwap"]
CALENDAR_FIELDS = ["date", "open", "close"]

_FRACTION = re.compile(r"\.(\d+)")


def parse_rfc3339(s: str) -> datetime:
    """Python 3.9's fromisoformat rejects 'Z' and nanosecond fractions; Alpaca sends both."""
    s = s.replace("Z", "+00:00")
    s = _FRACTION.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), s, count=1)
    return datetime.fromisoformat(s)


@dataclass(frozen=True)
class SessionDay:
    day: date
    open: datetime
    close: datetime


def bars_path(root: Path, symbol: str, day: date, feed: str) -> Path:
    return Path(root) / "stocks" / "1min" / feed / symbol.upper() / f"{day.isoformat()}.csv"


def has_day(root: Path, symbol: str, day: date, feed: str) -> bool:
    return bars_path(root, symbol, day, feed).exists()


def _atomic_write_csv(path: Path, fields: Sequence[str], rows: Iterable[Mapping]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fields))
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def write_day(root: Path, symbol: str, day: date, feed: str, alpaca_bars: Sequence[Mapping]) -> Path:
    """alpaca_bars: raw bar dicts from the API (t, o, h, l, c, v, n, vw)."""
    rows = [
        {
            "ts": parse_rfc3339(b["t"]).isoformat(),
            "open": b["o"],
            "high": b["h"],
            "low": b["l"],
            "close": b["c"],
            "volume": b.get("v", ""),
            "trade_count": b.get("n", ""),
            "vwap": b.get("vw", ""),
        }
        for b in alpaca_bars
    ]
    path = bars_path(root, symbol, day, feed)
    _atomic_write_csv(path, BAR_FIELDS, rows)
    return path


def load_1m_bars(root: Path, symbol: str, day: date, feed: str = "sip") -> List[Bar]:
    with open(bars_path(root, symbol, day, feed), newline="") as f:
        return [
            Bar(
                ts=to_et(datetime.fromisoformat(r["ts"])),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
            )
            for r in csv.DictReader(f)
        ]


def cached_days(root: Path, symbol: str, feed: str = "sip") -> List[date]:
    d = Path(root) / "stocks" / "1min" / feed / symbol.upper()
    if not d.exists():
        return []
    return sorted(date.fromisoformat(p.stem) for p in d.glob("*.csv"))


def load_calendar(root: Path) -> Dict[date, SessionDay]:
    path = Path(root) / "calendar.csv"
    if not path.exists():
        return {}
    with open(path, newline="") as f:
        return {
            date.fromisoformat(r["date"]): SessionDay(
                day=date.fromisoformat(r["date"]),
                open=datetime.fromisoformat(r["open"]),
                close=datetime.fromisoformat(r["close"]),
            )
            for r in csv.DictReader(f)
        }


def write_calendar(root: Path, days: Iterable[SessionDay]) -> None:
    merged = load_calendar(root)
    merged.update({d.day: d for d in days})
    _atomic_write_csv(
        Path(root) / "calendar.csv",
        CALENDAR_FIELDS,
        (
            {"date": d.day.isoformat(), "open": d.open.isoformat(), "close": d.close.isoformat()}
            for d in sorted(merged.values(), key=lambda d: d.day)
        ),
    )

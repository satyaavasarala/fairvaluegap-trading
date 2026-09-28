from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fvg_bot.strategy.types import Bar

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 9, 28, 9, 45, tzinfo=ET)


def make_bars(rows):
    """rows of (open, high, low, close), one minute apart."""
    return [
        Bar(ts=T0 + timedelta(minutes=i), open=o, high=h, low=l, close=c)
        for i, (o, h, l, c) in enumerate(rows)
    ]


def hl(rows):
    """rows of (high, low); open and close set to the midpoint."""
    return make_bars([(((h + l) / 2), h, l, ((h + l) / 2)) for h, l in rows])


def mirror(bars, pivot=200.0):
    """Reflect prices through pivot so a bullish pattern becomes its bearish twin."""
    return [
        Bar(ts=b.ts, open=pivot - b.open, high=pivot - b.low, low=pivot - b.high, close=pivot - b.close)
        for b in bars
    ]

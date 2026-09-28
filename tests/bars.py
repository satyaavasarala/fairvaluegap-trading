from datetime import datetime, timedelta

from fvg_bot.clock import ET
from fvg_bot.strategy.types import Bar

T0 = datetime(2026, 9, 28, 9, 45, tzinfo=ET)

# Bullish 1m walk-through used by the setup and FSM tests:
#   idx 1  swing high 101.5
#   idx 2  rebalance touch (price entered the 15m FVG during this bar)
#   idx 3  lowest low 99.8
#   idx 5  close 101.7 > 101.5 -> ChoCh
#   idx 6  1m FVG [101.0, 101.6], mid 101.3
#   idx 7  1m FVG [101.8, 102.2], mid 102.0; leg extends to 103.0
SETUP_SCENARIO = [
    (101.0, 101.2, 100.8, 100.9),
    (100.9, 101.5, 100.7, 100.8),
    (100.8, 100.9, 100.0, 100.2),
    (100.2, 100.5, 99.8, 100.4),
    (100.4, 101.0, 100.3, 100.9),
    (100.9, 101.8, 100.9, 101.7),
    (101.7, 102.6, 101.6, 102.5),
    (102.5, 103.0, 102.2, 102.4),
]
SETUP_SCENARIO_TOUCH = 2
SETUP_SCENARIO_CHOCH = 5


def make_bars(rows, start=T0, step=timedelta(minutes=1)):
    """rows of (open, high, low, close), one step apart."""
    return [
        Bar(ts=start + i * step, open=o, high=h, low=l, close=c)
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

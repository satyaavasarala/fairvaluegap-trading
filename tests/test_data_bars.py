from datetime import datetime, timedelta, timezone

import pytest

from fvg_bot.clock import ET
from fvg_bot.data.bars import aggregate
from fvg_bot.strategy.types import Bar


def minute_bars(start, n, base=100.0):
    return [
        Bar(ts=start + timedelta(minutes=i), open=base + i, high=base + i + 0.5, low=base + i - 0.5, close=base + i + 0.1)
        for i in range(n)
    ]


def test_aligns_to_quarter_hours():
    bars = minute_bars(datetime(2026, 9, 28, 9, 30, tzinfo=ET), 30)
    out = aggregate(bars, 15)
    assert [b.ts for b in out] == [
        datetime(2026, 9, 28, 9, 30, tzinfo=ET),
        datetime(2026, 9, 28, 9, 45, tzinfo=ET),
    ]
    first = out[0]
    assert first.open == 100.0
    assert first.high == 114.5
    assert first.low == 99.5
    assert first.close == pytest.approx(114.1)


def test_partial_leading_bucket_starts_on_clock_boundary():
    out = aggregate(minute_bars(datetime(2026, 9, 28, 9, 37, tzinfo=ET), 10), 15)
    assert [b.ts.time().isoformat() for b in out] == ["09:30:00", "09:45:00"]


def test_gaps_are_skipped_not_filled():
    bars = minute_bars(datetime(2026, 9, 28, 9, 30, tzinfo=ET), 1) + minute_bars(
        datetime(2026, 9, 28, 10, 5, tzinfo=ET), 1
    )
    assert [b.ts.time().isoformat() for b in aggregate(bars, 15)] == ["09:30:00", "10:00:00"]


def test_utc_input_buckets_in_eastern_time_across_dst():
    # 13:30 UTC is 09:30 EDT in September and 08:30 EST in December.
    sep = aggregate(minute_bars(datetime(2026, 9, 28, 13, 30, tzinfo=timezone.utc), 1), 15)
    dec = aggregate(minute_bars(datetime(2026, 12, 7, 14, 30, tzinfo=timezone.utc), 1), 15)
    assert sep[0].ts == datetime(2026, 9, 28, 9, 30, tzinfo=ET)
    assert dec[0].ts == datetime(2026, 12, 7, 9, 30, tzinfo=ET)


def test_out_of_order_raises():
    bars = minute_bars(datetime(2026, 9, 28, 9, 45, tzinfo=ET), 1) + minute_bars(
        datetime(2026, 9, 28, 9, 30, tzinfo=ET), 1
    )
    with pytest.raises(ValueError):
        aggregate(bars, 15)


def test_minutes_must_divide_an_hour():
    with pytest.raises(ValueError):
        aggregate([], 7)

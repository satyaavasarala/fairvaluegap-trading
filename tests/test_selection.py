from datetime import date, datetime, timedelta, timezone

from fvg_bot.clock import ET
from fvg_bot.config import Config
from fvg_bot.options.selection import (
    LOW_OI,
    LOW_VOLUME,
    NO_0DTE,
    NO_BID,
    NO_DELTA,
    PREMIUM_RANGE,
    STALE_QUOTE,
    WIDE_SPREAD,
    OptionQuote,
    Right,
    select_contract,
)
from fvg_bot.strategy.types import Direction

CFG = Config()
TODAY = date(2026, 9, 28)
NOW = datetime(2026, 9, 28, 10, 30, tzinfo=ET)


def quote(delta, bid=2.00, ask=2.02, right=Right.CALL, expiry=TODAY, volume=500, oi=500, age=0.5):
    return OptionQuote(
        symbol=f"SPY-{right.value}-{delta}",
        right=right,
        strike=570.0,
        expiry=expiry,
        bid=bid,
        ask=ask,
        delta=delta,
        volume=volume,
        open_interest=oi,
        quote_ts=NOW - timedelta(seconds=age),
    )


def test_picks_nearest_target_delta_in_band():
    chain = [quote(0.52), quote(0.58), quote(0.63), quote(0.72)]
    q, reason = select_contract(chain, Direction.BULLISH, NOW, CFG)
    assert reason == ""
    assert q.delta == 0.58


def test_tie_prefers_tighter_spread():
    wide, tight = quote(0.60, ask=2.03), quote(0.60, ask=2.01)
    q, _ = select_contract([wide, tight], Direction.BULLISH, NOW, CFG)
    assert q is tight


def test_bearish_uses_puts_with_abs_delta():
    chain = [quote(0.61), quote(-0.61, right=Right.PUT)]
    q, _ = select_contract(chain, Direction.BEARISH, NOW, CFG)
    assert q.right is Right.PUT


def test_no_0dte():
    chain = [quote(0.60, expiry=TODAY + timedelta(days=1))]
    assert select_contract(chain, Direction.BULLISH, NOW, CFG) == (None, NO_0DTE)


def test_today_is_computed_in_eastern_time():
    # 02:00 UTC on the 29th is still the 28th in New York.
    now_utc = datetime(2026, 9, 29, 2, 0, tzinfo=timezone.utc)
    chain = [
        OptionQuote("X", Right.CALL, 570.0, TODAY, 2.00, 2.02, 0.60, 500, 500, now_utc)
    ]
    q, _ = select_contract(chain, Direction.BULLISH, now_utc, CFG)
    assert q is not None


def test_nothing_in_delta_band():
    chain = [quote(0.50), quote(0.75)]
    assert select_contract(chain, Direction.BULLISH, NOW, CFG) == (None, NO_DELTA)


def test_chosen_contract_is_rejected_not_replaced():
    chain = [quote(0.60, ask=2.08), quote(0.62)]
    assert select_contract(chain, Direction.BULLISH, NOW, CFG) == (None, WIDE_SPREAD)


def test_liquidity_rejections():
    cases = [
        (quote(0.60, bid=0.0, ask=0.05), NO_BID),
        (quote(0.60, bid=2.05, ask=2.00), NO_BID),
        (quote(0.60, age=2.0), STALE_QUOTE),
        (quote(0.60, volume=10), LOW_VOLUME),
        (quote(0.60, oi=10), LOW_OI),
        (quote(0.60, bid=0.20, ask=0.22), PREMIUM_RANGE),
        (quote(0.60, bid=20.00, ask=20.02), PREMIUM_RANGE),
    ]
    for q, expected in cases:
        assert select_contract([q], Direction.BULLISH, NOW, CFG) == (None, expected), expected

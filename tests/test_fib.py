import pytest

from fvg_bot.strategy.fib import DISCOUNT_ZONE, PriceZone, retracement_zone
from fvg_bot.strategy.types import Direction


def test_bullish_discount_zone():
    z = retracement_zone(100.0, 110.0, Direction.BULLISH)
    assert round(z.low, 4) == 103.82
    assert round(z.high, 4) == 105.0


def test_bearish_premium_zone():
    z = retracement_zone(100.0, 110.0, Direction.BEARISH)
    assert round(z.low, 4) == 105.0
    assert round(z.high, 4) == 106.18


def test_spy_scale_prices_to_four_decimals():
    z = retracement_zone(571.23, 572.87, Direction.BULLISH)
    assert round(z.low, 4) == 571.8565
    assert round(z.high, 4) == 572.05


def test_conventional_golden_pocket_variant():
    z = retracement_zone(100.0, 110.0, Direction.BULLISH, ratios=(0.618, 0.65))
    assert round(z.low, 4) == 103.5
    assert round(z.high, 4) == 103.82


def test_default_ratios():
    assert DISCOUNT_ZONE == (0.5, 0.618)


def test_contains_is_inclusive():
    z = PriceZone(1.0, 2.0)
    assert z.contains(1.0)
    assert z.contains(2.0)
    assert not z.contains(0.999)
    assert not z.contains(2.001)


@pytest.mark.parametrize("low,high", [(110.0, 100.0), (100.0, 100.0)])
def test_degenerate_range_raises(low, high):
    with pytest.raises(ValueError):
        retracement_zone(low, high, Direction.BULLISH)


@pytest.mark.parametrize("ratios", [(0.618, 0.5), (0.5, 0.5), (-0.1, 0.5), (0.5, 1.1)])
def test_bad_ratios_raise(ratios):
    with pytest.raises(ValueError):
        retracement_zone(100.0, 110.0, Direction.BULLISH, ratios=ratios)

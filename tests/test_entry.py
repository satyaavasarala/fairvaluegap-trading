import pytest

from fvg_bot.config import Config
from fvg_bot.options.entry import evaluate_entry
from fvg_bot.options.selection import NO_0DTE
from fvg_bot.options.sizing import COST_FILTER
from fvg_bot.strategy.setup import find_setup
from fvg_bot.strategy.types import Direction
from tests.bars import SETUP_SCENARIO, make_bars
from tests.test_selection import NOW, quote

CFG = Config()
SETUP = find_setup(make_bars(SETUP_SCENARIO), Direction.BULLISH, 2, 5, as_of=7)


def test_entry_plan():
    # Stop distance 101.3 - 100.97 = 0.33; R = 0.33 * 0.6 + (0.01 + 0.02) = 0.228.
    contract = quote(0.60, bid=2.00, ask=2.01)
    d = evaluate_entry(SETUP, SETUP.entry_level, [contract], NOW, CFG)
    assert d.ok
    assert d.plan.contract is contract
    assert d.plan.sizing.r_prem == pytest.approx(0.228)
    assert d.plan.sizing.contracts == 1


def test_selection_failure_propagates():
    d = evaluate_entry(SETUP, SETUP.entry_level, [], NOW, CFG)
    assert not d.ok
    assert d.reason == NO_0DTE


def test_sizing_failure_propagates():
    d = evaluate_entry(SETUP, SETUP.entry_level, [quote(0.60, bid=2.00, ask=2.05)], NOW, CFG)
    assert not d.ok
    assert d.reason == COST_FILTER

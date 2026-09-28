from datetime import date, datetime

import pytest

from fvg_bot.backtest.outcome import STOP, TP, CostModel, Outcome
from fvg_bot.backtest.replay import SetupRecord
from fvg_bot.backtest.sweep import (
    GRIDS,
    baseline,
    compute_stats,
    one_factor_variants,
    pair_variants,
    variant_grid,
    variant_name,
)
from fvg_bot.clock import ET
from fvg_bot.config import Config
from fvg_bot.strategy.types import Direction

T = datetime(2026, 9, 28, 10, 30, tzinfo=ET)
COSTS = CostModel(0.02, 0.6, 0.02)


@pytest.mark.parametrize("name,size", [("spec", 2 * 3 * 3 * 2 * 2 * 2), ("ltf", 2 ** 7)])
def test_grid_size_and_names_unique(name, size):
    grid = variant_grid(GRIDS[name])
    assert len(grid) == size
    assert len({variant_name(v) for v in grid}) == len(grid)
    for ov in grid:
        Config(**dict(ov))


def test_baselines_are_spec_defaults():
    assert dict(baseline(GRIDS["spec"])) == {
        "stop_mode": "fvg",
        "stop_buffer": 0.03,
        "setup_window_bars": 5,
        "zone_ratios": (0.5, 0.618),
        "abort_on_new_extreme": True,
        "min_fvg_width": 0.0,
    }
    ltf = dict(baseline(GRIDS["ltf"]))
    assert ltf["ltf_minutes"] == 1
    assert ltf["entry_end"] == Config().entry_end
    assert ltf["arm_timeout"] == Config().arm_timeout


@pytest.mark.parametrize("name", sorted(GRIDS))
def test_one_factor_variants_differ_in_one_key(name):
    grid = GRIDS[name]
    base, *others = one_factor_variants(grid)
    b = dict(base)
    assert len(others) == sum(len(v) - 1 for v in grid.values())
    for ov in others:
        assert sum(dict(ov)[k] != b[k] for k in grid) == 1


def test_pair_variants():
    grid = GRIDS["ltf"]
    pairs = pair_variants(grid, "ltf_minutes", "entry_end")
    assert len(pairs) == 4
    assert {(dict(p)["ltf_minutes"], dict(p)["entry_end"]) for p in pairs} == {
        (a, b) for a in grid["ltf_minutes"] for b in grid["entry_end"]
    }
    b = dict(baseline(grid))
    for p in pairs:
        assert all(dict(p)[k] == b[k] for k in grid if k not in ("ltf_minutes", "entry_end"))


def trade(reason, net_r, gross_r, stop_distance):
    t = SetupRecord("SPY", date(2026, 9, 28), Direction.BULLISH, T, 100.0, 100.0 - stop_distance,
                    99.9, 100.1, 99.0, 101.0, triggered=True, trigger_at=T, trigger_price=100.0)
    t.outcomes["prem_45"] = Outcome(reason, T, 0.0, 0.0, net_r, gross_r)
    return t


def test_compute_stats():
    trades = [trade(TP, 3.0, 4.5, 0.5), trade(STOP, -1.0, -1.0, 0.1), trade(STOP, -1.2, -1.1, 0.1)]
    s = compute_stats(trades, 100, "prem_45", COSTS, Config())
    assert s.n == 3
    assert s.per_100 == pytest.approx(3.0)
    assert s.win == pytest.approx(1 / 3)
    assert s.mean_r == pytest.approx(0.8 / 3)
    assert s.total_r == pytest.approx(0.8)
    assert s.gross_r == pytest.approx(2.4 / 3)
    assert s.stop_p50 == pytest.approx(0.1)
    # cost 0.04 <= 0.15 * (0.5 * 0.6 + 0.04) only for the 0.5 stop
    assert s.cost_ok == pytest.approx(1 / 3)
    assert s.ci > 0


def test_compute_stats_empty():
    s = compute_stats([], 100, "prem_45", COSTS, Config())
    assert s.n == 0


@pytest.mark.parametrize("grid", sorted(GRIDS))
def test_main_end_to_end(tmp_path, capsys, grid):
    from datetime import timezone

    from fvg_bot.backtest import sweep
    from fvg_bot.data.store import write_day
    from tests.test_replay import D, TRIGGER_BAR, day_bars

    bars = day_bars([TRIGGER_BAR, (101.4, 103.0, 101.4, 102.95)])
    write_day(tmp_path, "SPY", D, "sip", [
        {"t": b.ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "o": b.open, "h": b.high, "l": b.low, "c": b.close}
        for b in bars
    ])
    out = tmp_path / "out"
    base_args = ["--grid", grid, "--one-factor", "--symbols", "SPY", "--root", str(tmp_path),
                 "--holdout-start", "2027-01-01", "--workers", "1", "--out", str(out)]
    assert sweep.main(base_args) == 0
    first = capsys.readouterr().out
    assert "BASE" in first
    assert (out / "summary.csv").exists() and (out / "trades.csv").exists()

    assert sweep.main(base_args + ["--from-cache"]) == 0
    assert "loaded" in capsys.readouterr().out

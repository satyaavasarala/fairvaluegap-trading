from datetime import date, datetime, timedelta

import pytest

from fvg_bot.clock import ET
from fvg_bot.config import Config
from fvg_bot.strategy.fsm import EntryDecision, EntryFSM, State
from fvg_bot.strategy.types import Direction
from tests.bars import SETUP_SCENARIO, make_bars, mirror

D = date(2026, 9, 28)
M15 = timedelta(minutes=15)


def at(h, m, s=0, day=D):
    return datetime(day.year, day.month, day.day, h, m, s, tzinfo=ET)


# Bullish 15m FVG [99.5, 100.6], confirmed when the 10:00 bar closes at 10:15.
ZONE_ROWS = [
    (99.0, 99.5, 98.8, 99.4),
    (99.4, 101.0, 99.4, 100.9),
    (100.9, 101.5, 100.6, 101.3),
]


class StubCheck:
    def __init__(self, ok=True, reason="stub reject"):
        self.decision = EntryDecision(ok, "" if ok else reason, plan="PLAN" if ok else None)
        self.calls = []

    def __call__(self, setup, price, now):
        self.calls.append((setup, price, now))
        return self.decision


class Run:
    """Feeds the canonical session: 15m zone, rebalance touch at 10:17:30, ChoCh at the
    10:20 bar, setup armed when the 10:22 bar closes (entry 101.3, stop 100.97)."""

    def __init__(self, cfg=None, check=None, flip=False):
        self.flip = flip
        self.check = check or StubCheck()
        self.fsm = EntryFSM(cfg or Config(), self.check, D)
        self.bars_15m = self._maybe_flip(make_bars(ZONE_ROWS, start=at(9, 30), step=M15))
        self.bars_ltf = self._maybe_flip(make_bars(SETUP_SCENARIO, start=at(10, 15)))

    def _maybe_flip(self, bars):
        return mirror(bars) if self.flip else bars

    def px(self, p):
        return 200.0 - p if self.flip else p

    def tick(self, ts, price):
        return self.fsm.on_tick(ts, self.px(price))

    def to_wait(self):
        for b in self.bars_15m:
            self.fsm.on_15m_bar(b)
        return self

    def to_hunt(self):
        self.to_wait()
        self.fsm.on_ltf_bar(self.bars_ltf[0])
        self.fsm.on_ltf_bar(self.bars_ltf[1])
        self.tick(at(10, 17, 30), 100.5)
        return self

    def through_bar(self, last):
        for b in self.bars_ltf[2 : last + 1]:
            self.fsm.on_ltf_bar(b)
        return self

    def to_armed(self):
        return self.to_hunt().through_bar(7)


def reasons(fsm):
    return [t.reason for t in fsm.transitions]


def test_happy_path_bullish():
    r = Run().to_armed()
    fsm = r.fsm
    assert fsm.state is State.ARMED
    assert fsm.setup.fvg_index == 6
    assert fsm.setup.entry_level == pytest.approx(101.3)
    assert fsm.setup.stop_level == pytest.approx(100.97)
    assert r.check.calls[0][1] == pytest.approx(101.3)

    assert r.tick(at(10, 23, 10), 102.0) is None
    intent = r.tick(at(10, 23, 40), 101.28)
    assert intent is not None
    assert intent.plan == "PLAN"
    assert intent.trigger_price == pytest.approx(101.28)
    assert fsm.state is State.SUBMIT_ORDER

    fsm.on_entry_result(at(10, 23, 45), filled=True)
    fsm.on_position_closed(at(10, 50))
    assert [t.dst for t in fsm.transitions] == [
        State.SCAN_15M,
        State.WAIT_REBALANCE,
        State.HUNT_CHOCH,
        State.VALIDATE_SETUP,
        State.ARMED,
        State.SUBMIT_ORDER,
        State.IN_POSITION,
        State.DONE,
    ]
    assert fsm.transitions[3].ts == at(10, 21)
    assert fsm.transitions[4].ts == at(10, 23)


def test_happy_path_bearish_mirror():
    r = Run(flip=True).to_armed()
    assert r.fsm.state is State.ARMED
    assert r.fsm.setup.direction is Direction.BEARISH
    assert r.fsm.setup.entry_level == pytest.approx(98.7)
    assert r.fsm.setup.stop_level == pytest.approx(99.03)
    intent = r.tick(at(10, 23, 40), 101.28)
    assert intent is not None
    assert intent.trigger_price == pytest.approx(98.72)


def test_touch_index_and_choch():
    r = Run().to_hunt()
    assert r.fsm.state is State.HUNT_CHOCH
    assert r.fsm.touch_index == 2
    r.through_bar(5)
    assert r.fsm.state is State.VALIDATE_SETUP
    assert r.fsm.choch_index == 5


def test_no_setup_until_leg_extends():
    r = Run().to_hunt().through_bar(6)
    assert r.fsm.state is State.VALIDATE_SETUP
    assert r.check.calls == []


# ---- ARMED tick ordering ---------------------------------------------------


def test_stop_breach_checked_before_touch():
    r = Run().to_armed()
    assert r.tick(at(10, 23, 30), 100.9) is None
    assert r.fsm.state is State.HUNT_CHOCH
    assert reasons(r.fsm)[-1] == "stop level breached before entry"
    assert r.fsm.choch_search_start == 8
    assert len(r.check.calls) == 1


def test_gap_guard():
    # Mid-to-stop is 0.33, so the guard is 0.165. 101.1 is 0.2 through the mid.
    r = Run().to_armed()
    assert r.tick(at(10, 23, 30), 101.1) is None
    assert r.fsm.state is State.HUNT_CHOCH
    assert reasons(r.fsm)[-1].startswith("gap guard")
    assert len(r.check.calls) == 1


def test_entry_filter_failure_at_touch():
    r = Run().to_armed()
    r.check.decision = EntryDecision(False, "spread too wide")
    assert r.tick(at(10, 23, 30), 101.3) is None
    assert r.fsm.state is State.HUNT_CHOCH
    assert reasons(r.fsm)[-1] == "entry filter failed: spread too wide"


def test_swing_extreme_exceeded():
    r = Run().to_armed()
    r.tick(at(10, 23, 30), 103.05)
    assert r.fsm.state is State.HUNT_CHOCH
    assert reasons(r.fsm)[-1] == "price exceeded swing extreme without retrace"


def test_armed_timeout():
    r = Run().to_armed()
    r.tick(at(10, 32, 59), 102.0)
    assert r.fsm.state is State.ARMED
    r.tick(at(10, 33, 0), 102.0)
    assert r.fsm.state is State.HUNT_CHOCH
    assert reasons(r.fsm)[-1] == "armed timeout"


def test_window_close_wins_over_timeout():
    r = Run().to_armed()
    r.tick(at(11, 30, 1), 102.0)
    assert r.fsm.state is State.DONE
    assert reasons(r.fsm)[-1] == "entry window closed"


def test_arm_check_failure():
    r = Run(check=StubCheck(ok=False)).to_armed()
    assert r.fsm.state is State.HUNT_CHOCH
    assert reasons(r.fsm)[-1] == "arm check failed: stub reject"


def test_entry_not_filled_rehunts():
    r = Run().to_armed()
    r.tick(at(10, 23, 40), 101.28)
    r.fsm.on_entry_result(at(10, 23, 55), filled=False)
    assert r.fsm.state is State.HUNT_CHOCH


def test_entry_result_out_of_state_raises():
    r = Run().to_armed()
    with pytest.raises(RuntimeError):
        r.fsm.on_entry_result(at(10, 24), filled=True)


# ---- 03 / 04 exits -----------------------------------------------------------


def test_setup_window_expiry_does_not_refire_on_spent_swing():
    r = Run(cfg=Config(setup_window_bars=1)).to_hunt().through_bar(6)
    assert r.fsm.state is State.HUNT_CHOCH
    assert reasons(r.fsm)[-1] == "no qualifying LTF FVG within setup window"
    assert r.fsm.choch_search_start == 7
    r.fsm.on_ltf_bar(r.bars_ltf[7])
    assert r.fsm.state is State.HUNT_CHOCH


def test_price_beyond_far_edge_drops_zone():
    r = Run().to_hunt()
    r.tick(at(10, 18), 99.4)
    assert r.fsm.state is State.SCAN_15M
    assert r.fsm.zones == {}


def test_15m_invalidation_during_hunt():
    r = Run().to_hunt()
    r.fsm.on_15m_bar(make_bars([(100.5, 100.6, 99.2, 99.4)], start=at(10, 15))[0])
    assert r.fsm.state is State.SCAN_15M
    assert r.fsm.zones == {}
    assert r.fsm.active_zone is None


# ---- 01 / 02 -----------------------------------------------------------------


def test_15m_close_beyond_far_edge_in_wait():
    r = Run().to_wait()
    assert r.fsm.state is State.WAIT_REBALANCE
    r.fsm.on_15m_bar(make_bars([(100.5, 100.6, 99.2, 99.4)], start=at(10, 15))[0])
    assert r.fsm.state is State.SCAN_15M


def test_15m_wick_beyond_far_edge_does_not_invalidate():
    r = Run().to_wait()
    r.fsm.on_15m_bar(make_bars([(100.5, 100.6, 99.2, 99.6)], start=at(10, 15))[0])
    assert r.fsm.state is State.WAIT_REBALANCE


def _prior_session_fsm(cfg):
    fsm = EntryFSM(cfg, StubCheck(), D)
    prior = date(2026, 9, 25)
    for b in make_bars(ZONE_ROWS, start=at(15, 0, day=prior), step=M15):
        fsm.on_15m_bar(b)
    return fsm


def test_prior_session_zone_and_entry_window_start():
    fsm = _prior_session_fsm(Config())
    assert fsm.state is State.IDLE
    fsm.on_tick(at(9, 40), 100.5)
    assert fsm.state is State.WAIT_REBALANCE
    fsm.on_tick(at(9, 45), 100.5)
    assert fsm.state is State.HUNT_CHOCH


def test_prior_session_zone_ignored_when_d1_off():
    fsm = _prior_session_fsm(Config(include_prior_zones=False))
    fsm.on_tick(at(9, 40), 100.5)
    assert fsm.state is State.SCAN_15M


def test_start_session_resets_but_keeps_prior_zones():
    r = Run().to_armed()
    r.tick(at(11, 31), 102.0)
    assert r.fsm.state is State.DONE
    r.fsm.start_session(date(2026, 9, 29))
    assert r.fsm.state is State.IDLE
    assert r.fsm.bars_ltf == []
    assert r.fsm.transitions == []
    assert Direction.BULLISH in r.fsm.zones


def test_start_session_drops_zones_when_d1_off():
    r = Run(cfg=Config(include_prior_zones=False)).to_wait()
    r.fsm.start_session(date(2026, 9, 29))
    assert r.fsm.zones == {}


def test_naive_timestamp_rejected():
    r = Run().to_wait()
    with pytest.raises(ValueError):
        r.fsm.on_tick(datetime(2026, 9, 28, 10, 0), 100.5)


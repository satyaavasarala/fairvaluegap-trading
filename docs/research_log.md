# Research log: Dual-Timeframe FVG Options Bot

Tracks what has been built, decided, tested and observed against the spec in `CLAUDE.md`.
Newest findings at the top of each section; every number here came from a run in this repo.

## Status (2026-09-28)

**No edge found.** The spec strategy and 272 variants of it lose money in-sample on SPY/QQQ,
and the base layer (the raw 15m FVG rebalance) is indistinguishable from a coin flip.
Execution work (broker, orders, position manager) and buying historical option quotes are
on hold until a new hypothesis shows gross edge on the underlying.

| Commit | Content |
|---|---|
| `89ffbbc` | `strategy/` pure functions: FVG, swings, ChoCh, Fib zone, setup finder |
| `ae54e25` | Sizing (3.3), contract selection (8), entry FSM 00-07 |
| `5476f33` | Alpaca download-once data cache, replay harness, D9 report |
| `4f21477` | Variant sweep, 5m LTF, outcome tracking, raw rebalance study |

---

## 1. Data

- **Source:** Alpaca `/v2/stocks/bars`, SIP feed, 1m, raw (unadjusted) prices, extended hours
  (04:00-20:00 ET). NYSE calendar from `/v3/calendar/NYSE` (includes early closes).
- **Coverage:** SPY and QQQ, 2024-02-01 to 2026-09-25, 665 sessions each (~84 MB).
- **Location:** `historical_data/` (gitignored). Download: `python3 -m fvg_bot.data.download --start 2024-02-01`.
- **In-sample / holdout:** sweeps and studies use days before 2026-01-01 (~481 sessions per
  symbol). **2026-01-01 onward has never been evaluated** and should stay that way until a
  candidate strategy is frozen.

### What the data sources can and cannot provide (checked 2026-09-28)
- Alpaca has historical option **bars and trades only, since Feb 2024**. There is **no
  historical bid/ask quote endpoint and no historical greeks**; the option chain endpoint is
  latest-snapshot only. Tier 3 as written (historical option quotes) needs a paid OPRA
  vendor (e.g. ThetaData, Polygon, Databento).
- yfinance was ruled out from general knowledge, not verified: 1m history only ~30 days,
  no option history.

---

## 2. Spec interpretations and decisions

Section 3 was reconstructed from descriptions, so these choices are baked into the code.
Each is isolated and can be changed.

| Topic | Decision |
|---|---|
| FVG gap | Strict inequality; wicks that touch exactly are not a gap. |
| 15m invalidation | Close beyond far edge only; a wick does not invalidate. |
| Rebalance / 03 zone exit | Tick-based (intrabar), unlike invalidation. |
| Swings | Strict `>` / `<` on both neighbours; equal highs/lows form no swing. Confirmed when bar k+1 closes. |
| ChoCh | First close beyond the most recent swing confirmed at that bar's close. **A swing broken before the search start is spent** (bug fix, see section 6). |
| "Freeze swing_low/high" (sec 6) vs "highest high since ChoCh" (3.2) | Origin extreme frozen at the ChoCh; leg extreme keeps extending through the D6 window. |
| Discount / premium zone | Bullish `[H - 0.618r, H - 0.5r]`; bearish `[L + 0.5r, L + 0.618r]`. |
| Valid setup | Whole LTF FVG inside the impulse leg; its confirming candle closes after the ChoCh bar (so the ChoCh bar can be the FVG's middle candle); most recent qualifying FVG wins; earlier FVGs re-checked each bar as the zone moves. |
| States 01-04 after 11:30 | Go to 07 (spec only said this for 05). |
| Contract selection | Nearest-to-0.60-delta contract is rejected, not replaced, if it fails a liquidity check. |
| Cost estimate | Spread + exit pad, per the 3.3 formula. Does not include the entry slippage cap. |
| New parameter | `disaster_slippage_pad` ($0.05): the stop_limit's limit offset, used in the disaster-loss cap. |
| Placeholders | Min volume/OI 100, premium $0.50-15.00, `min_stop_distance` $0.10. |

### Parameter conflict in the spec
With `DAILY_LOSS_LIMIT` $50, a 1.5R disaster stop and a $0.05 pad, one contract's R is capped
near $0.30/share ($30). The 15% cost filter with ~$0.04 costs needs R >= ~$0.27. The
tradeable band is R in [$0.27, $0.30]: at delta 0.60, an SPY stop of roughly $0.38-0.43.
`RISK_BUDGET` default was set to $30 so the two limits agree; any budget above that does
nothing unless the daily limit rises.

---

## 3. Backtest harness assumptions

These apply to every result below.

- **Ticks:** synthesized from 1m bars as a continuous $0.01-step path, O-L-H-C for up bars,
  O-H-L-C otherwise. Jumps occur only at bar opens, so the gap guard fires on real gaps only.
  Path-order sensitivity (`low_first` / `high_first`) has **not** been run.
- **Bars:** 15m bars built from 1m including premarket (D1 prior-session and premarket zones
  on). The FSM sees LTF bars from 09:30. One FSM instance runs across days.
- **Fills:** first trigger each day is assumed filled at the trigger tick; one trade per day.
- **Option P&L proxy:** constant delta 0.60, spread $0.02 + exit pad $0.02 = $0.04 round trip,
  **no theta, no gamma**. Theta would make long-option results worse, not better.
- **Exits:** stop = underlying through the stop level; flatten at close - 15 min (calendar
  aware). Eight exit rules scored at once: target premium-based (4 x R_prem / delta on the
  underlying) or underlying-based (4 x stop distance), crossed with time stops 30 / 45 / 60 / none.
- **Filtered mode:** runs the real `size_trade` (min stop, cost filter, risk budget, loss cap)
  against a synthetic quote; rejected setups go back to 03 as the live bot would.
- **Metrics:** net R includes costs; gross R = underlying move / stop distance; +/- is a 95% CI.

---

## 4. Experiments

### E4. Raw 15m FVG rebalance (base layer), 2026-09-28
`python3 -m fvg_bot.backtest.rebalance`. Enter at the first tick inside an active 15m FVG in
its direction, stop beyond the far edge + $0.03, no ChoCh/Fib/LTF filters. Each zone entered
once. Gross, in-sample. Bracket k = share of resolved trades reaching +kR before -1R; with no
information that rate is 1/(1+k).

| Window | Touches | +1R first | +2R first | +4R first | +15m | +60m | Stop p50 |
|---|---|---|---|---|---|---|---|
| 09:45-15:00 | 2,813 | 48% +/- 2 | 33% +/- 2 | 19% +/- 2 | +0.09 +/- 0.25R | +0.07 +/- 0.45R | $0.30 |
| 09:45-11:30 | 1,092 | 48% +/- 3 | 33% +/- 3 | 19% +/- 2 | -0.21 +/- 0.39R | -0.56 +/- 0.69R | $0.36 |
| **Null (no information)** | | 50% | 33% | 20% | 0 | 0 | |

- Longs (1,548) and shorts (1,265) in the full window both sit at the null (48/33/18 and
  48/34/20), so 2024-25's up-drift is not hiding a one-sided signal. SPY and QQQ agree.
- The slight sub-50% at 1R implies a fade wins ~52% gross, which is below costs at ~$0.30 stops.
- **Conclusion:** the base layer carries no directional information on SPY/QQQ at this
  resolution. This explains E2/E3: the downstream filters were selecting from a pool with no edge.

### E3. LTF and entry-window sweep, 128 variants, 2026-09-28
`python3 -m fvg_bot.backtest.sweep --grid ltf` (~8.5 min). Grid: LTF 1m/5m x window
morning/full (09:45-15:00) x stop fvg/swing x D6 5/10 x abort/keep x min FVG width 0/0.05
x arm timeout 10/30 min. Exit prem_45.

Everything else at spec baseline, unfiltered:

| Variant | Trades | Win | Net R | Gross R |
|---|---|---|---|---|
| 1m, morning (spec) | 33 | 15% | -0.29 +/- 0.58 | +0.20 |
| 1m, full day | 105 | 10% | **-0.55 +/- 0.27** | -0.19 |
| 5m, morning | 11 | 18% | -0.14 +/- 1.17 | -0.00 |
| 5m, full day | 36 | 8% | -0.63 +/- 0.44 | -0.53 |
| 1m, full day, swing stop | 106 | 4% | -0.33 +/- 0.23 | -0.20 |
| 5m, morning, swing stop | 11 | 0% | +0.21 +/- 0.90 | +0.28 |
| 5m, full day, swing stop | 36 | 0% | -0.23 +/- 0.38 | -0.17 |

- Full day triples trade count; the 1m full-day baseline is the first result with a CI
  entirely below zero.
- 5m gives wider stops ($0.14-0.23 vs $0.08 on the FVG stop) but far fewer setups.
- Best of 92 variants with n >= 30: 1m, morning, swing stop, keep-armed, min width $0.05,
  arm 30: n=69, **-0.01 +/- 0.37R**, gross +0.11. None positive.
- Filtered: only one variant reached n >= 30 (1m full swing D6=10 keep arm30: n=34,
  +0.04 +/- 0.53R, noise). The $30 budget rejects swing stops above ~$0.43.

### E2. Spec-grid sweep, 144 variants, 2026-09-28
`python3 -m fvg_bot.backtest.sweep --grid spec` (~5.5 min). Grid: stop fvg/swing x buffer
0/0.03/0.05 (D2) x D6 5/10/15 x zone 50-61.8 / 61.8-65 (D5) x abort/keep x min FVG width 0/0.05.

Baseline and one change at a time, unfiltered, exit prem_45:

| Change from baseline | Trades | Win | Net R | Gross R | Stop p50 |
|---|---|---|---|---|---|
| Baseline (spec) | 33 | 15% | -0.29 +/- 0.58 | +0.20 | $0.086 |
| Swing stop | 33 | 3% | -0.37 +/- 0.40 | -0.29 | $0.430 |
| Buffer $0.00 | 33 | 9% | -0.59 +/- 0.46 | -0.15 | $0.056 |
| Buffer $0.05 | 33 | 9% | -0.56 +/- 0.48 | -0.35 | $0.106 |
| D6 = 10 | 47 | 15% | -0.32 +/- 0.48 | +0.18 | $0.080 |
| D6 = 15 | 61 | 16% | -0.26 +/- 0.43 | +0.44 | $0.078 |
| Zone 61.8-65% | 10 | 0% | -1.04 +/- 0.02 | -1.07 | $0.105 |
| Keep armed on new extreme | 55 | 9% | -0.58 +/- 0.36 | -0.30 | $0.100 |
| Min FVG width $0.05 | 26 | 15% | -0.25 +/- 0.66 | +0.17 | $0.105 |

- 66 variants reached n >= 30; all negative. Best: swing stop, buffer 0, D6 5, keep-armed,
  min width $0.05: n=45, -0.07 +/- 0.45R, gross +0.04.
- Filtered: no variant reached 30 trades (max 4).
- Exit rules (D3 x D4) on the baseline all land between -0.25 and -0.39R. Exits cannot add
  edge when the average move after entry is ~0.

### E1. D9 stop-distance histogram, 2026-09-28
`python3 -m fvg_bot.backtest.d9`. Full period 2024-02-01 to 2026-09-25, spec defaults.

| | SPY | QQQ |
|---|---|---|
| Armed setups | 111 on 99 days | 108 on 100 days |
| Triggered | 27 (4% of sessions) | 22 (3%) |
| Stop at fill p10 / p50 / p90 | $0.040 / $0.086 / $0.190 | $0.045 / $0.090 / $0.277 |
| Pass sizing filters ($0.02 spread) | 0 | 1 |

- Median R per contract ~$9; costs are ~45% of R. The cost filter needs a stop >= ~$0.38.
- RISK_BUDGET is not the binding constraint: every budget from $25 to $100 gives the same
  pass rate. Rejections are min stop (SPY 63%, QQQ 50%) and cost filter (SPY 37%, QQQ 41%);
  QQQ also had one risk-budget rejection.
- Many 1m FVGs are a few cents wide. Spot check: SPY 2025-07-22, bearish, a 2-cent FVG
  (627.73-627.75), stop $0.04 away. FVG edges, zone math and trigger all verified by hand.
- FSM funnel (both symbols): 1,399 rebalance touches -> 1,671 ChoCh (can refire after
  re-hunt) -> 1,353 expired with no qualifying 1m FVG within D6 -> 219 armed -> 164 abandoned
  because price ran past the swing extreme first -> 49 triggered.

---

## 5. What has not been tested

- Historical option quotes / realistic fills (needs a paid vendor; not justified yet).
- Theta and gamma in option P&L.
- Intrabar path order sensitivity (`order="low_first"` / `"high_first"` in `backtest/ticks.py`).
- Higher HTF zones (1h, 4h, daily) or other symbols (e.g. IWM, single stocks).
- Fading the rebalance as a strategy (only inferred from E4: ~52% gross at 1R).
- The 2026 holdout.

## 6. Bugs found along the way

- **ChoCh reused a spent swing:** after a re-hunt, any close above an already-broken swing
  high fired an instant fake ChoCh. Fixed: a swing broken before the search start is spent.
- **Sweep crashed after finishing its compute** twice (a `Stats` sort, a stale `GRID` name).
  Fixed, plus `results.pkl` is now saved before reporting (`--from-cache`) and an end-to-end
  test covers the report path.
- **Rebalance study keyed zones by `id()`,** which Python can reuse after deletion. Fixed by
  keying on the (frozen, hashable) FVG value.

## 7. Reproducing

```bash
python3 -m pytest -q                                   # 170 tests
python3 -m fvg_bot.data.download --start 2024-02-01    # needs APCA_API_KEY_ID / APCA_API_SECRET_KEY
python3 -m fvg_bot.backtest.d9                         # E1
python3 -m fvg_bot.backtest.sweep --grid spec          # E2
python3 -m fvg_bot.backtest.sweep --grid ltf           # E3
python3 -m fvg_bot.backtest.rebalance                  # E4
```
Outputs go to `backtest_output/` (gitignored): `d9_setups_*.csv`, `sweep_<grid>/summary.csv`,
`sweep_<grid>/trades.csv`, `sweep_<grid>/results.pkl`.

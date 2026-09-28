# Research log: Dual-Timeframe FVG Options Bot

Tracks what has been built, decided, tested and observed against the spec in `CLAUDE.md`.
Newest findings at the top of each section; every number here came from a run in this repo.

## Status (2026-09-28)

**No edge found.** The spec strategy and 272 variants of it lose money in-sample on SPY/QQQ,
and the base layer (the raw 15m FVG rebalance) is indistinguishable from a coin flip.
Higher-timeframe levels with a shares cost model (E5) found nothing pre-registered; a
breakout pattern at prior-day/overnight highs and lows looked promising in-sample with
$1.00 stops (E6) but **failed on the 2026 holdout (E7)**. The holdout is now spent.
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
  symbol). 2026-01-01 to 2026-09-25 (184 sessions) was the holdout; it was **spent once on
  the frozen E7 breakout rule** and is no longer clean. New tests need new data.

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

### E7. Frozen breakout rule on the 2026 holdout (pre-registered 2026-09-28, before any run)
**Recorded deviation:** E6 failed its own bar, which said to drop the pattern. The user chose
(2026-09-28) to spend the holdout on one frozen rule instead. This is that single test.

Frozen rule (no further changes):
- Levels PDH, PDL, ONH, ONL; side fixed at the first tick at or after 09:45; entry at the first
  tick at or through the level from that side, **in the crossing direction**. Each level once
  per day.
- Window 09:45-11:30. Stop **$1.00** from entry, target **+$1.00 (1R)**. Flatten at close - 15m.
- SPY and QQQ. Scored with **worst-case** intrabar ordering (as E6).
- Data: 2026-01-01 to 2026-09-25 (the last cached day). The 2025-12-31 session is loaded only
  to set the first day's prior-day levels; no 2025 trades count.
- In-sample reference (E6, same rule): n=1,299, hit 55%, net +0.073 +/- 0.054R
  (SPY +0.091, QQQ +0.055).

Decision (one look, directional hypothesis, so one-sided 95%: z = 1.645):
- **PASS:** pooled mean net R at $0.02/share minus 1.645 x SE > 0, **and** SPY and QQQ each
  positive, **and** pooled mean net R at a $0.05/share slippage stress > 0, **and** n >= 30.
  Next: design share execution and paper trade forward.
- **INCONCLUSIVE:** pooled mean net R at $0.02 > 0 but not every PASS condition holds.
  Next: log signals forward in paper (no capital) until ~500 new trades, then apply the same bar.
- **FAIL:** pooled mean net R at $0.02 <= 0. Next: drop level breakouts.

Power note: ~500 trades expected, SE ~0.045R. If the true edge is +0.07R, P(PASS) is roughly
45%; at +0.035R, roughly 15%. A non-PASS is therefore weak evidence against a small edge.

**Result (2026-09-28): FAIL.** Run once with
`python3 -m fvg_bot.backtest.holdout --i-understand-this-uses-the-holdout` (output saved to
`backtest_output/holdout_E7.txt`). 184 sessions per symbol, 2026-01-02 to 2026-09-25. Before
the run, the same pipeline on in-sample days reproduced the E6 reference exactly (n=1,299,
+0.073R).

| | n | Hit (worst) | Net R @ $0.02 (95%) | Net R @ $0.05 | Heuristic net @ $0.02 |
|---|---|---|---|---|---|
| SPY | 254 | 51% +/- 6 | +0.002 +/- 0.123 | -0.028 | +0.087 |
| QQQ | 250 | 48% +/- 6 | -0.060 +/- 0.124 | -0.090 | +0.106 |
| **Pooled** | **504** | **50% +/- 4** | **-0.029 +/- 0.087** | **-0.059** | +0.096 |

- Pooled mean net R is below zero, so the pre-registered outcome is FAIL: **level breakouts
  are dropped.** The in-sample +0.073R did not carry over; the hit rate fell from 55% to 50%.
- The heuristic column stayed positive (+0.10R), but the gap between it and the worst case
  widened from ~0.035R in-sample to ~0.125R here. That means more 2026 trades resolved inside
  the entry bar, where the synthetic path cannot be trusted; it is the same effect that
  produced the false $0.50 result in E5/E6.
- The holdout is now spent. Any further test needs new data (forward paper logging) or a
  different market.

### E6. Level breakouts under worst-case intrabar ordering (pre-registered 2026-09-28, before any run)
Hypothesis from E5 (not yet tested): trading **through** PDH/PDL/ONH/ONL beats the null.
Same levels, side rule, touch rule, windows, cost ($0.02/share), fixed stops ($0.50 and
$1.00) and brackets (+1R, +2R before -1R) as E5, but the trade goes **in the crossing
direction**. In-sample only; holdout untouched.

Each trade is scored three ways from the entry bar onward:
- **worst:** any bar whose range reaches both the stop and the target counts as a loss; in
  the entry bar, the bar's adverse extreme is assumed to come after the entry.
- **heuristic:** the synthetic tick path used in E1-E5.
- **best:** target first whenever a bar reaches both; in the entry bar only a close beyond
  the stop counts as adverse.
Gaps through a level at a later bar's open exit at that open. Unresolved at close - 15m:
marked at the flatten bar's open.

**Pass bar (worst ordering only)**, per stop x window x bracket (8 looks, z = 3 kept):
pooled SPY+QQQ mean net R - 3 x SE > 0, each symbol's mean net R > 0, pooled n >= 30.

If a check passes: verify the entry minutes against real SIP trade prints, then evaluate the
frozen rule once on the 2026 holdout. If none passes, the E5 breakout pattern is treated as
unproven (possibly a path artifact) and dropped unless real tick data says otherwise.

**Result (2026-09-28): 0 of 8 checks passed.** `python3 -m fvg_bot.backtest.breakout` (~16 s).
Pooled SPY+QQQ, net R after $0.02/share, +/- 95% (the pass bar used 3 x SE).

| Stop | Window | n | 1R resolved in entry bar | 1R hit worst / heuristic / best | 1R net, worst | 2R net, worst |
|---|---|---|---|---|---|---|
| $0.50 | am | 1,299 | 27% | 42% / 54% / 54% | -0.20 +/- 0.05 | -0.12 +/- 0.08 |
| $0.50 | full | 1,743 | 27% | 42% / 54% / 55% | -0.21 +/- 0.05 | -0.16 +/- 0.07 |
| $1.00 | am | 1,299 | 4% | 55% / 57% / 57% | +0.07 +/- 0.05 | +0.10 +/- 0.08 |
| $1.00 | full | 1,743 | 5% | 54% / 56% / 56% | +0.04 +/- 0.05 | +0.05 +/- 0.06 |

- **$0.50 stops: the E5 pattern was a path artifact.** A quarter of trades resolve inside the
  entry bar, and the hit rate swings from 42% (worst) to 54% (heuristic) on the ordering
  assumption alone. Worst case is clearly negative.
- **$1.00 stops: the ordering barely matters** (4-5% resolve in the entry bar; worst is within
  2-3 points of heuristic). Worst-case net is positive for both symbols and, in the morning
  window, for all four levels. It misses the z = 3 bar narrowly: am 1R lower bound -0.01R,
  am 2R -0.01R; the 95% interval excludes zero for am 1R (+0.02 to +0.13) and am 2R.
- The heuristic column mirrors E5's fade results (e.g. $1.00 full: fade 44%, breakout 56%),
  a consistency check on the two implementations.
- **Caveats:** this is the same in-sample data that suggested the idea, so it is not
  independent evidence. The edge is small (~+0.04 to +0.07R net per trade), and breakout
  entries usually fill worse than the level price: an extra $0.03 of slippage costs 0.03R at
  a $1.00 stop, around half the estimated edge.
- **Per the pre-registration, the pattern is unproven.** Continuing would mean freezing a
  single rule and testing it once on the 2026 holdout (a deliberate, recorded decision, not
  a pass).

### E5. Higher-timeframe levels, shares cost model (pre-registered 2026-09-28, before any run)
Instrument assumption changes to **shares** (SPY/QQQ): round-trip cost $0.02 per share
(penny spread + $0.01 slippage), no option wrapper. Stop floor **$0.50**, so size and cost
stay workable with shares or a 3x ETF ($50 risk at a $0.50 stop is 100 SPY shares, ~$70k,
and costs are ~4% of R).
In-sample only (before 2026-01-01); holdout untouched.

Tests (entry at the first qualifying tick, one entry per zone or level):
1. **1h FVG** (clock-aligned 60m bars from extended hours): trade in the FVG direction, stop
   beyond the far edge + $0.03. Skip if the stop distance is under $0.50.
2. **Daily FVG** (RTH daily bars): same rule.
3. **Levels, fixed $0.50 stop:** prior-day RTH high/low (PDH/PDL) and overnight high/low
   (ONH/ONL, prior 16:00 to 09:30). Side is fixed at the first in-window tick; a touch is the
   first tick at or through the level from that side. Trade the **fade** (back toward the side
   price came from). A fade hit rate below the null means breakouts carry the information.
4. **Levels, fixed $1.00 stop:** same.

Windows 09:45-11:30 and 09:45-15:00. Brackets +1R and +2R before -1R, flatten at close - 15m
(unresolved trades marked at the flatten price).

**Pass bar**, per test x window x bracket (16 looks, so z = 3 instead of 1.96):
- pooled SPY+QQQ mean **net** R (bracket outcome minus $0.02 / stop distance) has a lower
  bound (mean - 3 x SE) above 0, **and**
- SPY and QQQ each have a positive mean net R.
- at least 30 pooled trades (added before the first run, after a unit test showed a tiny
  zero-variance sample could otherwise pass).

A pass only earns a closer look (costs with real quotes, path-order sensitivity, then the
holdout once). It is not a strategy.

**Result (2026-09-28): 0 of 16 checks passed.** `python3 -m fvg_bot.backtest.htf` (~25 s).
Pooled SPY+QQQ; hit = +1R before -1R among resolved; net = mean R after $0.02/share.

| Test | Window | n | Stop p50 | 1R hit (null 50%) | 1R net R | 2R hit (null 33%) | 2R net R |
|---|---|---|---|---|---|---|---|
| 1h FVG | am | 323 | $1.31 | 46% +/- 6 | -0.06 +/- 0.10 | 26% +/- 6 | -0.08 +/- 0.13 |
| 1h FVG | full | 521 | $1.20 | 49% +/- 5 | -0.02 +/- 0.08 | 29% +/- 5 | -0.00 +/- 0.10 |
| Daily FVG | am | 126 | $2.77 | 58% +/- 11 | +0.08 +/- 0.15 | 27% +/- 12 | +0.01 +/- 0.18 |
| Daily FVG | full | 139 | $2.92 | 57% +/- 10 | +0.08 +/- 0.14 | 24% +/- 11 | -0.01 +/- 0.16 |
| Levels fade, $0.50 | am | 1,299 | $0.50 | 46% +/- 3 | -0.12 +/- 0.06 | 30% +/- 2 | -0.15 +/- 0.08 |
| Levels fade, $0.50 | full | 1,743 | $0.50 | 46% +/- 2 | -0.13 +/- 0.05 | 30% +/- 2 | -0.15 +/- 0.07 |
| Levels fade, $1.00 | am | 1,299 | $1.00 | 44% +/- 3 | -0.15 +/- 0.05 | 26% +/- 2 | -0.20 +/- 0.07 |
| Levels fade, $1.00 | full | 1,743 | $1.00 | 44% +/- 3 | -0.13 +/- 0.05 | 26% +/- 2 | -0.16 +/- 0.06 |

- **1h FVG:** at the null. 236-393 touches were skipped for stops under $0.50.
- **Daily FVG:** 57-58% at 1R, but n is small, the interval is wide, 34-37% of trades are
  still open at the flatten (daily stops ~$2.8 rarely resolve in a day), and SPY at 2R is
  below the null. Not a pass; not strong enough to chase.
- **Level fades lose, consistently:** 44-46% at 1R with +/-2-3%, in both symbols, all four
  levels (PDH, PDL, ONH, ONL), both windows and both stop sizes. At the symmetric 1R bracket
  the breakout trade is the exact mirror, so **continuation through the level hit +1R first
  54-56% of the time** (roughly +0.09 to +0.13R net). This was not the pre-registered
  direction, so it is a new hypothesis, not a pass.
- **Artifact risk for that pattern:** the synthetic path moves low-then-high inside an up bar
  (high-then-low inside a down bar). When a bar crosses a level, the path therefore reaches
  that bar's extreme in the crossing direction before any pullback, which favours
  continuation whenever a bracket resolves inside the entry bar. E4's slight sub-50% bounce
  rate at 15m FVGs leans the same way. This must be ruled out (worst-case ordering in the
  entry bar, or real trade ticks) before the breakout idea is taken seriously.

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
- Real trade ticks (Alpaca SIP trades) to settle intrabar ordering, which decided E5-E7.

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
python3 -m pytest -q                                   # 197 tests
python3 -m fvg_bot.data.download --start 2024-02-01    # needs APCA_API_KEY_ID / APCA_API_SECRET_KEY
python3 -m fvg_bot.backtest.d9                         # E1
python3 -m fvg_bot.backtest.sweep --grid spec          # E2
python3 -m fvg_bot.backtest.sweep --grid ltf           # E3
python3 -m fvg_bot.backtest.rebalance                  # E4
python3 -m fvg_bot.backtest.htf                        # E5
python3 -m fvg_bot.backtest.breakout                   # E6
python3 -m fvg_bot.backtest.holdout --i-understand-this-uses-the-holdout   # E7 (already spent)
```
Outputs go to `backtest_output/` (gitignored): `d9_setups_*.csv`, `sweep_<grid>/summary.csv`,
`sweep_<grid>/trades.csv`, `sweep_<grid>/results.pkl`.

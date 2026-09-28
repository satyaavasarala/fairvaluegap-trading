# Dual-Timeframe FVG Options Bot: Design Spec (v2)

**Status:** design only, no code yet. Supersedes the vertical-debit-spread spec.
**Mode:** Alpaca paper trading only until every Tier 4 check in section 10 passes.
**Disclaimer:** this automates one discretionary technical-analysis methodology. Nothing here is a validated edge. Treat the backtest as the judge, not the strategy's reputation.

---

## 1. Decisions locked in

| Area | Decision |
|---|---|
| Instrument | SPY or QQQ single-leg **long call** (bullish) or **long put** (bearish). No spreads, no short options. |
| Expiry | **0DTE only.** The 1DTE-after-1PM branch is deleted (it was unreachable with a 09:45-11:30 entry window). |
| Entry window | 09:45-11:30 ET. |
| Trade limit | 1 trade per day. |
| Strike | Slightly ITM, target abs(delta) about 0.60 (acceptable 0.55-0.70). |
| R definition | **Option premium**, not underlying points (section 3.3). |
| Stop | Script-managed on the **underlying** (tick through stop level plus buffer). Broker-side **disaster stop** on the option as a crash backstop. |
| Take-profit | Script-managed. Not resting at the broker, because it would collide with the disaster stop (both are sells of the same contracts). |
| Order style | Marketable limits only. Never raw market orders on 0DTE options. |
| Heartbeat | Based on **connection health** (ping/pong plus reconnect and reconcile), not a 3-second data-silence timer. |
| EOD flatten | 15:45 ET, or 15 minutes before close on early-close days (read the close time from the Alpaca calendar). |
| Clock | All logic in `America/New_York` via `zoneinfo`. Never use local machine time. |

## 2. Open decisions (defaults are for v1; backtest to settle)

| ID | Question | v1 default |
|---|---|---|
| D1 | Which 15m FVGs count? Three 15m candles must close after the 09:30 open, so the earliest same-day FVG is confirmed at 10:15 ET. | Include prior-session and premarket zones (needs extended-hours bars); make it a config flag. |
| D2 | Stop trigger: tick vs 1m close, and buffer size. | Tick, plus $0.03 buffer. Backtest 0 / 0.03 / 0.05 and close-based. |
| D3 | Take-profit trigger: option premium >= entry + 4R, or underlying reaches entry + 4x stop distance. | Premium-based. Compare both in backtest. |
| D4 | Time stop after entry. | 45 min. Test 30 / 45 / 60 / none. |
| D5 | Fib band. Source material says "Golden Pocket" for 50%-61.8%, but the conventional golden pocket is 61.8%-65%. | Use 50%-61.8%, name it `DISCOUNT_ZONE` in code. Test 61.8%-65% as a variant. |
| D6 | How many 1m bars after ChoCh to wait for a qualifying 1m FVG. | 5. Test 5 / 10 / 15. |
| D7 | Entry trigger. | Underlying touches 1m FVG midpoint, then submit marketable limit (section 7). |
| D8 | Data feed. | SIP for stock bars and OPRA for option quotes in live. IEX-only 1m bars can create or erase FVGs that are not on the consolidated tape. |

## 3. Definitions

The original equations were blank in the source document. The definitions below are reconstructed from its descriptions. **Confirm before coding.**

### 3.1 Candles and FVG
Candle `i` is the most recently closed candle, `i-1` the impulse candle, `i-2` the anchor candle.

- **Bullish FVG:** `Low[i] > High[i-2]`. `bottom = High[i-2]`, `top = Low[i]`, `mid = (top + bottom) / 2`.
- **Bearish FVG:** `High[i] < Low[i-2]`. `top = Low[i-2]`, `bottom = High[i]`.
- **Rebalance:** current price trades inside `[bottom, top]` of an active 15m FVG.
- **Invalidation:** a 15m candle **closes** beyond the opposite boundary (below `bottom` for bullish, above `top` for bearish).

### 3.2 Structure and Fibonacci (bullish shown; bearish mirrors)
- **Swing high at bar k:** `High[k] > High[k-1]` and `High[k] > High[k+1]`. It is only *confirmed* after bar k+1 closes, so no lookahead.
- **Bullish ChoCh:** after rebalance, a 1m **close** above the most recent confirmed 1m swing high. Wick-only crosses are ignored.
- **Impulse range:** `swing_low` = lowest low since the rebalance touch; `swing_high` = highest high since the ChoCh; `range = swing_high - swing_low`.
- **Discount zone:** `[swing_high - 0.618*range, swing_high - 0.5*range]`.
- **Valid setup:** a 1m FVG formed after the ChoCh, inside the impulse leg, whose midpoint lies inside the discount zone.
- **Underlying stop level:** `1m FVG bottom - STOP_BUFFER` (bullish) or `top + STOP_BUFFER` (bearish).

### 3.3 R and sizing
```
stop_distance = abs(entry_underlying - stop_level)
R_prem        = stop_distance * delta + est_round_trip_cost      # premium per share
R_dollars     = 100 * R_prem                                     # per contract
contracts     = floor(RISK_BUDGET / R_dollars)                   # skip trade if < 1
TP_premium    = entry_fill + 4 * R_prem                          # min 3R
disaster_stop = entry_fill - 1.5 * R_prem                        # broker-side backstop
```
- `est_round_trip_cost` = (ask - mid) at entry + (mid - bid) at exit + `EXIT_SLIPPAGE_PAD`.
- **Cost filter:** skip the trade if `est_round_trip_cost > 0.25 * R_prem`. A tight FVG stop can make R only a few cents, and then spread costs alone eat the edge.
- **Minimum stop:** skip if `stop_distance < MIN_STOP_DISTANCE` (tune per symbol).
- **Disaster loss cap:** `contracts * 100 * 1.5 * R_prem` (plus slippage pad) must be <= `DAILY_LOSS_LIMIT`. Reduce contracts until it is.

---

## 4. Entry FSM (states 00-07)

```
[00 IDLE] --clock >= 09:30 ET--> [01 SCAN_15M]

[01 SCAN_15M] --active 15m FVG found--> [02 WAIT_REBALANCE]

[02 WAIT_REBALANCE] --15m close beyond far edge--> [01]
[02 WAIT_REBALANCE] --price enters FVG (inside entry window)--> [03 HUNT_CHOCH]

[03 HUNT_CHOCH] --price exits FVG far edge / 15m invalidation--> [01]
[03 HUNT_CHOCH] --1m ChoCh (close beyond swing)--> [04 VALIDATE_SETUP]

[04 VALIDATE_SETUP] --no qualifying 1m FVG within D6 bars--> [03]
[04 VALIDATE_SETUP] --qualifying 1m FVG in discount zone--> [05 ARMED]

[05 ARMED] --underlying touches FVG mid + all filters pass--> [06 SUBMIT_ORDER]
[05 ARMED] --setup expires (see table)--> [03]

[06 SUBMIT_ORDER] --filled--> [Position Manager P1]
[06 SUBMIT_ORDER] --not filled after retries--> [03]

[Position Manager closed] --> [07 COOLDOWN_DONE] --> [00 IDLE] (next session)
```

New state vs the original spec: **05 ARMED**. The original jumped from validation straight to order submission, but an options order cannot rest at an underlying price, so the bot has to watch the underlying and fire the option order itself.

## 5. Position manager sub-FSM (P0-P5)

```
[P0 ENTRY_PENDING] --fill--> [P1 PLACE_DISASTER_STOP]
[P0] --replace budget exhausted--> cancel, back to Entry FSM state 03 (no trade counted)

[P1 PLACE_DISASTER_STOP] --stop accepted--> [P2 MONITORING]
[P1] --rejected after 2 retries--> [P4 EXITING(reason=STOP_FAIL)]

[P2 MONITORING] --underlying through stop level--> [P4 EXITING(STOP)]
[P2] --premium >= TP_premium--> [P4 EXITING(TP)]
[P2] --time since entry >= TIME_STOP--> [P4 EXITING(TIME)]
[P2] --clock >= flatten time--> [P4 EXITING(EOD)]
[P2] --feed unhealthy--> [P3 DEGRADED]
[P2] --broker stop filled (seen on reconcile)--> [P5 CLOSED]

[P3 DEGRADED] --reconnect + REST reconcile OK--> [P2]
[P3] --unrecovered after 15 s and REST reachable--> [P4 EXITING(PANIC)]
[P3] --REST unreachable--> stay, keep retrying (broker stop is protecting)

[P4 EXITING] --cancel disaster stop, confirm, sell--> [P5 CLOSED]

[P5 CLOSED] --record realized R, update daily P&L--> [07 COOLDOWN_DONE]
```

## 6. Transition detail (guards and actions)

| From | Event / guard | To | Action |
|---|---|---|---|
| 00 | clock >= 09:30 ET | 01 | Load calendar; open data streams. |
| 01 | New 15m FVG (D1 rules) | 02 | Store zone (top, bottom, mid, direction, timestamp). Keep the most recent active zone per direction. |
| 02 | 15m close beyond far edge | 01 | Discard zone. |
| 02 | Price inside zone AND 09:45 <= now <= 11:30 | 03 | Record rebalance touch time and price. |
| 03 | Price leaves far edge or 15m invalidation | 01 | Discard zone. |
| 03 | Bullish/bearish 1m ChoCh (close-based) | 04 | Freeze swing_low/high; start D6 bar counter. |
| 04 | 1m FVG in discount zone within D6 bars | 05 | Compute stop level, R, contracts, TP; run option selection filters (section 8). |
| 04 | Counter expired | 03 | Reset swing tracking. |
| 05 | Underlying touches FVG mid AND all filters pass | 06 | Freeze option contract; submit entry. |
| 05 | Expiry: price exceeds swing extreme without retrace, OR stop level breached, OR 11:30 passed, OR 10 min elapsed (tunable) | 03 (or 07 if 11:30 passed) | Log reason. |
| 05 | Any filter fails (cost, spread, min stop, contracts < 1) | 03 | Log reason. Not a trade. |
| 06 | Filled | P1 | Mark trade counted for the day. |
| P1 | Stop accepted | P2 | Save state to disk. |
| P2 | Any exit trigger | P4 | Set exit reason. |
| P4 | Exit filled | P5 | Reconcile via REST. |
| P5 | Any | 07 | If realized loss reaches DAILY_LOSS_LIMIT, lock engine for the session. |
| 07 | Next session | 00 | Reset daily counters. |

---

## 7. Order handling rules

- **Entry:** marketable limit at `ask + up to ENTRY_SLIPPAGE_CAP` (default $0.02). Use the **replace** endpoint at most 2 times over about 10 seconds. If still unfilled, cancel, confirm the cancel, and abort the setup.
- **Exit sequence (every exit reason):**
  1. Set state to EXITING and block any other exit path.
  2. Cancel the broker disaster stop and poll until its status is terminal. If it turns out to be *filled*, the position is already flat: go to CLOSED.
  3. Submit a sell limit at `bid - EXIT_PAD` (start at $0.02). Poll status; replace toward a lower price in steps if unfilled. Never submit a second sell while one is live.
  4. After N failed steps, escalate to a wider limit; last resort is a market order only if the option is still quoting a sane spread.
- **Idempotency:** unique `client_order_id` per intent. Check order and position status before every action.
- **Persistence:** write state (FSM state, order ids, entry fill, stop/TP levels) to SQLite on every transition.
- **Startup reconciliation:** query open orders and positions. A position with no matching state gets flattened (or adopted into P2 with a warning). Orders with no matching state get cancelled.
- **Rate limits:** budget API calls; do not poll faster than the limit allows.

## 8. Option selection and filters

1. Fetch the 0DTE chain for the trade direction. Skip if no 0DTE expiry exists today.
2. Choose the contract nearest target delta 0.60, restricted to 0.55-0.70.
3. Reject if any of these fail: bid-ask spread <= `MAX_SPREAD` (default $0.05; tune per symbol), quote age < 2 s, volume and open interest above minimums, premium within sane range.
4. Compute R, cost filter, contracts (section 3.3). Reject if contracts < 1.

## 9. Risk guardrails

| Parameter | Threshold | Action |
|---|---|---|
| Risk budget per trade | $25-50 (`RISK_BUDGET`) | Size contracts to it; skip if 1 contract exceeds it. |
| Disaster-stop loss | <= `DAILY_LOSS_LIMIT` | Reduce contracts until true. |
| Daily loss limit | $50 | Lock engine for the session. |
| Max daily trades | 1 | Go to 07. |
| Entry window | 09:45-11:30 ET | Ignore triggers outside it. |
| Time stop | D4 | Exit with reason TIME. |
| EOD flatten | 15:45 ET / close - 15 min | Cancel orders, flatten. |
| Feed health | ping/pong plus reconcile | Section 5, P3. |

## 10. Validation plan

- **Tier 1, unit tests (pure logic):** FVG detection (including overlapping wicks returning none), swing detection with no lookahead, ChoCh fires on close only, Fib math to 4 decimals, R/sizing/cost-filter math, exit-sequence ordering with a fake broker.
- **Tier 2, integration (Alpaca paper):** verify current API behaviour before relying on it (section 11). Assert order payload shape, contract selection, DTE filter, and that the entry never exceeds the slippage cap.
- **Tier 3, backtest:** must use **historical option quotes** for the actual contracts, not underlying price moves. Include spread and slippage. Use as long a history as available: six months at one trade per day gives roughly 40-80 trades, too few to distinguish an edge from luck at a ~30% win rate. Report confidence intervals, and test the D2-D6 variants without cherry-picking (hold out a final period).
- **Tier 4, paper burn-in (15+ trading days):** zero unhandled exceptions; log every state transition with timestamps; compare intended vs actual fills (alert if slippage exceeds the cost filter's assumption); confirm EOD flatten works unattended; kill the connection mid-trade and confirm reconnect and reconcile within 15 s; kill the process mid-trade and confirm the broker-side stop remains and startup reconciliation handles it.

## 11. Verify against current Alpaca docs before coding

- Whether single-leg options support stop and stop-limit orders, and how they trigger (option price, not underlying).
- Whether OCO or bracket orders are supported for options (the design assumes they are **not**).
- Options data entitlements (OPRA), snapshot vs streaming quotes, and rate limits.
- Account approval level required for long options, and any day-trading rules that apply to your account size.
- Order **replace** support and behaviour for options.

## 12. Suggested project layout (for Claude Code)

```
fvg_bot/
  config.py          # all parameters above, loaded from env/yaml
  clock.py           # ET time, calendar, early closes
  data/              # bar aggregation, quote cache, feed health
  strategy/
    fvg.py  swings.py  fib.py  setup.py   # pure functions, no I/O
    fsm.py                                # entry FSM
  options/           # chain fetch, selection, sizing, filters
  execution/
    broker.py        # thin Alpaca wrapper (paper/live switch)
    orders.py        # entry / exit sequences, idempotency
    position_mgr.py  # sub-FSM P0-P5
  store.py           # SQLite state persistence and reconcile
  backtest/          # replay engine using option quotes
tests/
```

Build order: `strategy/` pure functions with tests, then the backtest harness, then `execution/` against a fake broker, then Alpaca paper.

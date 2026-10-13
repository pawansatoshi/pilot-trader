# Stage 3A: autonomous workflow / backend

## Layers and the boundary that keeps exchange fixes local
```
market data > scan (bot.scan) > workflow.classify > fsm (setup states) > AI review > execute()/risk_gate > order layer > exchange
                                                                                          |  publishes domain events on `bus`
workflow (subscribers): FSM transitions, Telegram, outcome        views.py (read models) > api/index.py /api/v2/*
```
- **Order layer** (`bot.py`: execute, protect, real_entry, real_exit, manage, close, move_sl, reconcile, cancel_row, blocked, algo_*): talks to the workflow ONLY through `bus.publish(...)` / `bus.blocks(...)`. It never references `workflow` or `fsm` (enforced by a test).
- **State side** (`fsm.py`, `workflow.py`, `events.py`, `scanner.py`, `bus.py`, `views.py`): never imports `bot`, `ccxt` or `requests` (enforced by a test).
- So when Stage 2.5 finds an exchange-specific problem, change `real_entry` / `protect` / `algo_place` / `real_exit` / `exit_fill`. As long as the same bus events are published, nothing in the state machine changes. (Test: `test_replacing_the_exchange_adapter_does_not_touch_the_workflow`.)

Bus events published by the order layer: `gate_result`, `order_submitted`, `trade_opened` (only after fill AND protection), `execution_failed`, `trade_closed`. Guard asked by the order layer: `bus.blocks()` (health gate; a crashing guard blocks).

## Setup state machine (`fsm.py`, tables `setups`, `setup_events`)
WAITING (virtual, no row) > WATCHING > NEAR_SETUP > CANDIDATE > AI_REVIEW > RISK_CHECK > EXECUTION > POSITION > CLOSED > OUTCOME > COOLDOWN > WAITING.
Rejection: CANDIDATE / AI_REVIEW / RISK_CHECK / EXECUTION > REJECTED > COOLDOWN. CANDIDATE > RISK_CHECK directly only under `NO_AI_POLICY=rules`. An illegal edge raises `IllegalTransition` (all legal and illegal edges are tested). One live row per (market, mode, symbol) (DB unique index).
Tiers are display tiers over the EXISTING score, not a new strategy: CANDIDATE = score >= MIN_SCORE (today's `strong`), NEAR_SETUP = within 0.2 below, WATCHING = score >= 1.0 (EMAs aligned), else WAITING. WATCHING/NEAR changes need `TIER_CONFIRM_SCANS` (default 2) consecutive observations; CANDIDATE is immediate so the trade flow is not delayed. Spot short signals stay WAITING.
Cooldowns keep parity with the old 1 h AI cache: 3600 s after an AI answer, a risk-gate rejection or an execution failure; `SCAN_SECONDS - 10` when the AI was unavailable (retry next scan).
**Restart rule (`workflow.sweep`, at startup and before every scan):** AI_REVIEW / RISK_CHECK left behind go back to CANDIDATE and are re-qualified with fresh data (never executed from stale state); EXECUTION is resolved from the real trade row (OPEN > POSITION, fresh PENDING left to `reconcile()`, nothing > REJECTED); POSITION whose trade closed while down > CLOSED/OUTCOME/COOLDOWN; expired cooldowns end; unobserved WATCHING/NEAR/CANDIDATE are dropped.

## Events (table `logs`, typed by `kind`)
bot_started, health_checked, exchange_connected, account_synced, universe_loaded, scan_started, scan_done, setup_state_changed, evidence_built, ai_started, ai_verdict, risk_verdict, order_submitted, fill_confirmed, protection_confirmed, trade_opened, stop_moved, trade_closed, outcome_recorded, system_state_changed, bot_stopped, trade_recovered (+ plain `msg` rows). Each row can carry symbol / setup_id / review_id / trade_id / data. In DEMO the order/fill/protection events are labelled "[DEMO simulated]". Retention: 5000 rows or 14 days; setup_events and ended setups 30 days (`C.prune()`).

## Scanner snapshots (table `snapshot`, one row per market+mode)
Real counters of the last scan (universe, checked, failed, qualifying, seconds, next scan time) plus one row per checked symbol (price, trend, direction, score, RSI, volume, state, reason) and per-state counts. Written only when the content changes. `scan_state_<market>` shows "scanning" while a scan runs.

## Health gating (`workflow.system`)
BLOCKED (no scan, no AI call, no new order; positions still managed; gate reason `SYSTEM_BLOCKED`): market data check failed for that market; Real: API key check failed for that market, futures conditional-order (algo) check failed; 3 consecutive data/account failures; health check older than 15 min. While blocked a cheap probe lets the failure counter recover; diag runs every 60 s while unsafe (300 s otherwise).
DEGRADED (trading continues): OpenRouter or Telegram problem. (AI unavailable already means "no trade" by the Stage 2 policy.)
Start always runs a health check before the first scan. State changes produce one event and one Telegram alert.

## Telegram families
SETUP DETECTED (when a symbol becomes CANDIDATE, max once per 2 h per symbol, indicative levels, "not a trade yet"), TRADE OPENED (only after fill + protection confirmed; also after a restart recovery), TRADE CLOSED, RISK/HEALTH ALERT (everything else with level warn/err; info level uses "STATUS").

## Read endpoints for the dashboard (all from the database)
`/api/v2/status`, `/scanner?market=`, `/setups`, `/setup?id=`, `/events?since_id=`, `/positions`, `/infra`. The old `/api/state` and the old page are unchanged.

## Environment
`TIER_CONFIRM_SCANS=2`. Everything else unchanged. `SCAN_SECONDS` default stays 300 (a faster scan would delay position management because the worker is single-threaded).

## Deliberately not in 3A
UI (3B), multi-timeframe evidence packet / skeptic / model registry (4), worker WebSockets, Redis, tick storage, native spot STOP_LOSS, any Binance Demo validation.

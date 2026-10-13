# Stage 3 architecture proposal (design only, nothing implemented)

## A. Current architecture
- **Worker (Voroa, `bot.py`, 30 s loop):** per market `tick()`: manage open trades, equity, daily goal/loss, scan every `SCAN_SECONDS` (default 300), snapshot of 15m candles for ~8 symbols into table `chart` (every 55 s), `diag()` every 5 min.
- **Pipeline already enforced in code:** scan (`tech`, 50 symbols, REST) > `strong` list (score >= 1.5) > AI (`ask`, validated by `judge_pick`) > `execute()` > `risk_gate()` > PENDING row + exchange order > `protect()` > OPEN > `manage()`/`sim_step()` > `close()` > `reviews` outcome; shadow tracking; `reconcile()`.
- **State:** Postgres: `settings` (k/v), `trades`, `reviews`, `decisions`, `logs`, `daily`, `chart`.
- **API (Vercel, `api/index.py`):** one fat `GET /api/state` (settings + 5 queries), `/api/chart`, controls. Cookie password auth.
- **Frontend:** one HTML string in `page.py`: 2 market cards, 4 tabs, polling every 8 s, Lightweight Charts with a 15m snapshot + browser WebSocket for the live candle.

## B. Current UI/UX
Cards + tabs: good for "what is it doing" sentences, weak as a terminal. Missing: timeframes, scanner view, setup states, intelligence panel, typed timeline, volume, position panel, desktop layout, per-step process visibility. Single scan cadence (5 min) makes "scanning" look idle between scans.

## C. Target architecture (same layers you listed, mapped to what exists)
market data (REST now, WS later) > features (`tech`, new MTF features for top candidates) > setup engine (**new: state machine**) > evidence builder (**new, deterministic**) > AI reviewer (existing `ask`/`judge_pick`) > central gate (**existing `risk_gate`, unchanged**) > order manager (existing `execute`/`protect`) > exchange > position manager (existing `manage`) > outcome (existing `close`/reviews/shadow) > DB (+ **events, setups**) > API > dashboard + Telegram.
Authority rule: only the worker + exchange decide that a setup, order or position exists. The API returns database rows; the frontend only draws them. A "TRADE OPENED" event is emitted in exactly one place: after fill confirmed AND protection confirmed.

## D. Target user workflow (every step is an event row, so the UI shows it)
Start pressed > `bot_started` > `health_checked` (pass/fail) > `exchange_connected` > `account_synced` > `universe_loaded` > `scan_started` (n symbols) > `scan_done` (checked / qualifying / next scan) > `setup_state_changed` (WATCHING / NEAR / CANDIDATE with reasons) > `evidence_built` > `ai_started` > `ai_verdict` > `risk_verdict` (+ reasons) > `order_submitted` > `fill_confirmed` > `protection_confirmed` > `trade_opened` > `stop_moved` > `trade_closed` > `outcome_recorded` (R) > back to scanning. No setup: the status bar shows live counters from the last real scan ("42 checked, 0 qualifying, next scan 18 s").

## E. State machine (per market, symbol, direction)
`WAITING > WATCHING > NEAR_SETUP > CANDIDATE > AI_REVIEW > RISK_CHECK > EXECUTION > POSITION > CLOSED > OUTCOME > COOLDOWN > WAITING`
Rejection edges: CANDIDATE, AI_REVIEW, RISK_CHECK > REJECTED > COOLDOWN (default 60 min). A table of allowed edges is enforced in code; an illegal edge is a bug that raises.
- WAITING/WATCHING/NEAR are **display tiers over the existing score**, not a new strategy: WAITING = no EMA alignment; WATCHING = aligned but score < 1.3; NEAR_SETUP = 1.3 <= score < `MIN_SCORE`; CANDIDATE = score >= `MIN_SCORE` (today's `strong`). Hysteresis (2 consecutive scans) prevents flapping.
- Persistence: rows in `setups` + append-only `setup_events` for symbols at WATCHING or higher. WAITING is not stored. **Restart rule:** WAITING/WATCHING/NEAR are recomputed; AI_REVIEW/RISK_CHECK older than 2 min go back to CANDIDATE and are re-qualified with fresh data (never executed from stale state); EXECUTION is resolved by the existing `reconcile()`; POSITION/CLOSED/OUTCOME are derived from `trades`/`reviews`.

## F. Realtime data flow (constrained by the free stack)
Facts: Voroa worker has no public URL; Vercel functions cannot hold a WebSocket server. So "worker WebSocket to frontend" is **not possible without paying or adding a public service**.
- **Market data to the screen:** the browser connects directly to Binance public WebSockets (kline stream for the selected symbol/timeframe; optional `!miniTicker@arr` for scanner prices). Real data, zero backend load. Historical candles for 5m/15m/1h/4h come from Binance REST in the browser, with the DB snapshot as fallback.
- **Bot state to the screen:** worker writes meaningful state only; the dashboard polls light endpoints (3-5 s while the tab is visible). No ticks in Postgres.
- Worker may later use Binance WS (klines, user-data stream for faster fills): Stage 4, optional.

## G. Database and retention
New: `setups`, `setup_events`; `logs` gets typed columns (kind, symbol, setup_id, review_id, trade_id, data JSON) and becomes the event timeline; one upserted `snapshot` row per market (scanner table + counters, written only when changed, at most every 30 s).
Persist: trades, orders/fills (inside `trades.extra` + events), setup events, reviews, risk decisions (`gate_reasons`), shadow, health changes, daily. Prune: events 14 days (cap 5 000 rows), setup_events 30 days, terminal setups 30 days; trades/reviews/daily kept. Neon free compute-hours warning from earlier still applies.

## H. Files and functions that must change
- `common.py`: `init()` (tables + log columns), new `emit()`, `prune()`.
- new `events.py`: event kinds, Telegram templates. new `fsm.py`: transition table, `transition()`, `restore()`. new `scanner.py`: `classify()`, MTF features, snapshot builder (scoring math reused, not changed).
- `bot.py`: `scan()` (calls fsm/emit at each stage), `execute()` (emit order_submitted / fill_confirmed / protection_confirmed / trade_opened), `manage()`, `move_sl()`, `close()` (emit), `tick()` (snapshot, system state), `diag()` (latency, system state), `main()` (restore).
- `risk.py`: one extra input `system_ok` => reason `SYSTEM_BLOCKED` (new trades only).
- `api/index.py`: `/api/status` (cheap), `/api/scanner`, `/api/setup`, `/api/events?since=`, `/api/chart?tf=`, keep existing endpoints.
- Front end: move `page.py` to static `public/` (`index.html`, `app.js`, `style.css`), no framework.
- tests: new files only.

## I. Must NOT change
`risk_gate`/`build_plan`/`open_risk`, `execute` idempotency (PENDING, unique index, client ids), `reconcile`, `sim_step`, shadow + metrics, `judge_pick`, AI budget/cooldown, Demo/Real and Futures/Spot separation, strategy scoring and thresholds, daily +8% / -3% logic.

## J. Stage 3 vs Stage 4
- **Stage 3A (backend):** events, state machine, scanner snapshot, system health gating, Telegram taxonomy, retention. Review gate.
- **Stage 3B (UI):** terminal dashboard on top of 3A. Review gate.
- **Stage 4:** full MTF evidence packet to the AI, analyst/skeptic second call, model registry, offline analytics, optional worker WebSockets.
- Small bridge inside 3A: the single AI call may return `grade, case_for, case_against, fatal_flaw, invalidation`; validated; shown if present ("not provided" otherwise); a non-null `fatal_flaw` can only veto. Execution authority unchanged.

## K. Tests required
FSM legal/illegal edges; restart restore per state; no trade/event "opened" without fill (fake exchange); candidate Telegram never says "opened"; system block stops new trades but `manage` continues; events/snapshot content equals DB truth; retention prune; API contract (runs when FastAPI is installed); scanner counts match a mocked scan; all 65 existing tests stay green.

## L. Acceptance criteria
1. Every step of the section D workflow appears as an event with a real timestamp and reference.
2. After Start, no screen is ever static for more than one scan interval without a visible scanning state.
3. A restart during each state leaves no duplicate order and no orphan state.
4. `trade_opened` exists only for rows with a confirmed fill and confirmed protection.
5. Chart markers/lines equal `trades` + events, nothing else.
6. Health failure of Binance data, exchange keys (Real), algo capability or DB blocks new trades within one tick; AI/Telegram failure only degrades.
7. Phone: status, balance/P&L, position, chart, entry/SL/TP, AI verdict, timeline visible without opening menus.
8. Zero changes in `risk.py` gate rules (only the `system_ok` input).

## M. Classification
**MUST:** state machine + persistence; typed event timeline; scanner snapshot with real counters; no-fake-UI contract; Telegram taxonomy; system health gating; chart timeframes + volume + real markers/lines; status bar; daily target/loss UX; AI-as-reviewer panel (existing fields only); retention.
**SHOULD:** AI JSON bridge fields; deterministic MTF features for top candidates; static front-end files; Binance latency in health; scan cadence 60 s; stop-move markers; API contract tests.
**NICE:** browser `!miniTicker` scanner prices; crosshair tooltip details; worker WebSocket / user-data stream; theme toggle.
**NOT WORTH THE COMPLEXITY:** WebSocket server for the frontend on this stack; Redis/pub-sub; React/Next rewrite; storing ticks; AI call per symbol; decorative animation; browser end-to-end suite; multi-user; AI chat.

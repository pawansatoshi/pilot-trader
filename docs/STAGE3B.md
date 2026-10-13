# Stage 3B: trading terminal UI

Static site in `public/` (no build step, no framework): `index.html`, `app.css`, `js/{format,chartmodel,render,feed,app}.js`. Backend additions are read-only: `views.dashboard()` (one aggregated, labelled read model), `views.timeline()`, `views.trades()`, `views.ai_view()`, `views.workflow_view()`, `views.recent_rejections()`; routes `/api/v2/dashboard`, `/api/v2/trades`, `/api/v2/timeline` next to the seven Stage 3A routes. The old Stage 1-3A page is kept at `/legacy`.

## Authority
The backend decides everything that is displayed: lock labels (`TARGET REACHED — NEW TRADES LOCKED`, `DAILY LOSS LIMIT — NEW TRADES PAUSED`), `NEW TRADES BLOCKED` + exact reasons + "Existing positions are still being managed.", the 12 workflow steps, setup vs trade, AI fields. The page only renders them (`render.js` = pure string functions). Its only write calls are the existing `/api/market` (Start/Stop) and `/api/mode` (Demo/Real, typed REAL + server-side lock). No order, close-all, reset or risk endpoint is reachable from the new UI (tests enforce the list).

## Workflow display
START > HEALTH CHECK > SCANNING > SETUP DETECTED > AI REVIEW > RISK CHECK > EXECUTION > EXCHANGE FILL > PROTECTION > POSITION > CLOSED > OUTCOME. Each step is derived in `views.workflow_view` from real state: run flag + start time, health timestamp, the FSM state of the focus setup, and the `order_submitted` / `fill_confirmed` / `protection_confirmed` events that actually exist. Steps are `done / active / pending / skipped / off`. SETUP DETECTED is never presented as a trade: the setup panel says "SETUP, NOT A TRADE YET ... No order has been sent" until the FSM reaches POSITION ("TRADE OPEN"), and levels are labelled INDICATIVE (setup) or ACTUAL (exchange fill).

## Chart and live data
Browser > Binance public REST klines (history) + WebSocket kline stream (live), 5m / 15m / 1h / 4h, zoom/pan/crosshair/volume from Lightweight Charts. `feed.js` is the integration boundary (injected fetch/WebSocket; statuses connecting / live / stale / disconnected / unavailable; exponential backoff; stale watchdog; late events from an old subscription are ignored). Markers, entry/SL/TP lines, stop moves and exits come from `/api/v2/trades` (real rows), live P&L/R from the live price (unrealized, before fees). If Binance cannot be reached the page says so and falls back to the worker's own delayed 15m snapshot; nothing is simulated. No WebSocket server, no Redis, no tick storage.

## Layout
Desktop >= 1200 px: 3 columns (scanner + health | chart + levels + position | setup + AI + timeline). 900-1199 px: 2 columns. Mobile < 900 px: single column in this order: bot status/workflow, balance and daily limits, open position, chart, entry/SL/TP, setup, AI review, timeline, health, scanner (encoded in CSS `order`, asserted by a test).

## Run / deploy
Vercel: `public/` is served statically at `/`; `/api/*` and `/legacy` go to the Python function (`vercel.json`). Local: `uvicorn api.index:app` serves the terminal at `/` (needs `DASH_PASSWORD` and `DATABASE_URL` or SQLite).
Tests: `python -m unittest discover -s tests -v` (also runs the JS suites) or `node --test "tests/js/*.test.mjs"`. Static previews rendered from real backend fixtures (fake exchange): `docs/preview/*.html`.

## Known limitations
See the Stage 3B report: never run in a real browser, `app.js` glue is syntax-checked but not unit-tested, FastAPI routes not executed here, Binance WebSocket never connected in the sandbox, AI panel fields beyond verdict/confidence/reason are "not provided" until Stage 4.

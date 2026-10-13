# Pilot: Binance Spot + Futures auto-trader (Demo / Real)

**Stack:** GitHub (code) + Voroa (24/7 worker = the bot) + Neon (database) + Vercel (dashboard) + OpenRouter (free AI) + Telegram (alerts)

## What it does
- Two buttons: **Start Futures** and **Start Spot** (each has its own balance, goal and stop). **Demo | Real** switch on top.
- Futures: long + short, max leverage 3. Spot: long only, no leverage.
- Every step is shown live under "What the bot is doing" (scanning, setup found, AI asked, trade opened, stop moved, goal reached).
- Daily goal +8%: after that, no new trades that day (profit locked). Daily loss -3%: paused. Next day starts **automatically** with the new, compounded balance, and trades only when a high-probability setup exists.
- Chart shows BUY/SELL markers, ENTRY / STOP / TARGET lines, live price.
- Health tab + red banner tell the exact problem and how to fix it. Telegram sends the same.

## Free OpenRouter budget (50 messages/day)
Rules scan 50 coins for free. AI is called only when a high-probability setup exists, max once per 15 min, never twice for the same coin within 1 hour, hard cap 45/day (every retry counts). If the budget is used, a model is busy or the key fails, the bot continues on strict rules and says so. Free model list is re-fetched every 6 h, so a changed free model is picked up automatically. Nothing is spent while the daily goal is locked.

## Safety layer (Stage 1) and measurement (Stage 2)
- **One `risk_gate()` before every order** (risk.py): daily +8% lock / -3% loss limit, open-risk reservation (worst case if every stop hits must fit inside the daily loss budget), max positions, duplicates, stale evidence / price drift, effective R:R after fees + slippage, minimum stop distance, exchange tick/step/min-notional, balance. Demo and Real use the same gate and the same cost model.
- **AI can only approve or veto** the engine's direction. Invalid output or no AI = no trade (set `NO_AI_POLICY=rules` to trade on rules alone at the same threshold). Entry/stop/target/size/leverage always come from code.
- **Idempotent execution:** PENDING row + DB unique lock + exchange client order id; on restart `reconcile()` recovers or cancels half-finished orders. Real: the exchange is the source of truth (positions, balance, orphan alert).
- **Every AI review is stored** (table `reviews`). Rejected / unavailable setups are tracked as shadow trades with the same exit logic. Scorecard: AI tab. Nothing is fed back into the AI prompt; nothing auto-tunes.
- **Binance conditional orders:** futures SL/TP use the Algo Order API (`/fapi/v1/algoOrder`) since classic STOP_MARKET answers -4120 (2025-12-09). Needs a recent ccxt; Health tab checks it. Spot SL = STOP_LOSS_LIMIT + a watchdog that escalates to a market sell if the limit cannot fill.
- **Demo environment:** `BINANCE_ENV=demo` (Binance Demo Trading). Legacy testnet is not supported on purpose.
- **Terminal UI (Stage 3B):** `public/` is served at `/` (static), the old page at `/legacy`; design in `docs/STAGE3B.md`, previews in `docs/preview/`. JS tests: `node --test "tests/js/*.test.mjs"`.
- **Status of every stage: `docs/STATUS.md`** (Stage 2.5 Binance Demo validation is BLOCKED / NOT RUN). Stage 3A design: `docs/STAGE3A.md`.
- Docs: `docs/TESTNET_CHECKLIST.md`, `docs/RISK_MODEL.md` (what the risk model can and cannot protect), validators: `tools/testnet_validate.py`, `tools/demo_scenarios.py` (results in `testnet_report.json`).
- Tests: `python -m unittest discover -s tests -v`

## Setup (in this order)
1. **GitHub:** push this folder as a private repo.
2. **Neon:** copy the *pooled connection string* (ends with `?sslmode=require`). That is `DATABASE_URL`. The Neon API token is not needed.
3. **Voroa worker** (Background Worker): build `pip install -r requirements-worker.txt`, start `python bot.py`. Env vars: see `.env.example` (DATABASE_URL, OPENROUTER_API_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, ALLOW_REAL).
4. **Vercel:** import the repo (framework: Other). Env vars: `DATABASE_URL`, `DASH_PASSWORD`, `ALLOW_REAL`. Set the function region near your Neon region.
5. Open the Vercel URL, sign in, stay on **Demo**, press **Start Futures**. Check the Health tab: everything should be green.

## Real mode (locked by default)
Set `ALLOW_REAL=1` on Vercel and worker, add `BINANCE_API_KEY/SECRET` on the worker, press **Real**, type REAL. Key permissions: Reading + Spot/Futures trading only, never Withdrawals. Use `BINANCE_TESTNET=1` first. Real order code is not tested on live Binance.

## Know before you start
- **Neon free = 100 compute-hours/month.** A worker that talks to the DB every 30 s keeps it awake 24/7 (about 180 h), so the free quota can run out after ~2.5 weeks. Options: use Voroa's free Postgres or another always-on free Postgres for `DATABASE_URL`, or upgrade Neon. Works with any Postgres.
- Binance must answer from the Voroa server (a "restricted location" error shows in Health).
- Free Voroa worker must stay awake 24/7 (check Voroa free-instance docs).
- 8% per day is a target, not a promise. Realistic: 1% risk per trade, 1:2 reward.
- Local test: `pip install -r requirements-worker.txt fastapi uvicorn; python bot.py` (SQLite) and `uvicorn api.index:app`.

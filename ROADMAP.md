# Roadmap

## Phase 0: Accounts (day 1)
GitHub private repo, Neon DB, OpenRouter key, Telegram bot (BotFather + chat id), Vercel, Voroa. No Binance keys yet.

## Phase 1: Deploy in Demo (day 1-2)
Worker on Voroa, dashboard on Vercel, Health tab all green, Telegram test alert received. Press Start Futures and Start Spot in Demo.

## Phase 2: Demo evaluation (2-4 weeks)
Go on only if: 100+ demo trades, profit factor > 1.3, max drawdown < 10%, positive after fees in 3 of 4 weeks, no stuck positions, no missed alerts. Otherwise tune MIN_SCORE / RISK_PCT and use "Reset demo balance".

## Phase 3: Binance testnet (week 3-5)
BINANCE_TESTNET=1 + ALLOW_REAL=1. Verify entry, stop, target, stop move, close, Close-all. If a stop order fails the bot closes the position and reports it.

## Phase 4: Small live (week 5+)
Futures key: Reading + Futures only (no Withdrawals). Start with $50-100 you can lose. Watch alerts daily for a week. Keep RISK_PCT=1, MAX_LEVERAGE=3.

## Phase 5: Improve
Liquidation feed, ETF flows, news filter, backtester, Telegram commands (/status, /stop).

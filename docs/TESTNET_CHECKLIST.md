# Binance DEMO validation checklist
Environment: **Binance Demo Trading** (keys from the demo environment), `BINANCE_ENV=demo`, `ALLOW_REAL=1` on the worker and on Vercel, latest ccxt (`pip install -U ccxt`). The legacy testnet is not supported (its prices differ from the live data the scanner uses).
Run, in this order (all on Binance **Demo** keys):
1. `python tools/testnet_validate.py --trigger --tp --spot-gap` (exchange behaviour: items 1-10, 16-18). Run it plain first, then with the flags; `--trigger/--tp` need the price to move 0.05% within 3 minutes, otherwise the item is INCONCLUSIVE, never PASS.
2. `ALLOW_REAL=1 python tools/demo_scenarios.py` (bot code against the exchange, throw-away SQLite: items 11-15, 19-20).
3. Items 21-24 and the real worker restart are MANUAL (see table).
Results merge into `testnet_report.json`; `python tools/report.py` prints the 24-item table. A blank example is in `docs/testnet_report.TEMPLATE.json`.
Statuses: PASS = observed on Binance Demo; FAIL = tested and failed; INCONCLUSIVE = could not be triggered/verified; MANUAL = needs a manual market condition; NOT RUN.
The table below is the manual version of the same list.
Mark each item PASS only when the evidence is in the dashboard AND on Binance.

| # | Item | How | PASS when |
|---|------|-----|-----------|
| 1 | Futures market entry | validator F LONG entry; bot opens one | order filled, position on Binance, clientOrderId `pl<review_id>` visible |
| 2 | Futures LONG | bot trade | SL and TP are **algo** orders (Binance "Conditional" list), side SELL, qty = position |
| 3 | Futures SHORT | bot trade or validator | same, side BUY, stop above, target below |
| 4 | Futures SL | validator `--trigger` or wait | position closed by the stop; booked reason SL; exit price = real fill; TP algo order auto-cancelled |
| 5 | Futures TP | wait / move target near price manually on demo | closed by TP; SL cancelled; R positive |
| 6 | Spot LONG | bot spot trade | market buy filled, STOP_LOSS_LIMIT placed (limit below stop) |
| 7 | Spot SL | wait for a stop or trigger by hand on demo | booked SL with real fill; if the limit does not fill, Telegram "Escalating to a market sell" and position closed |
| 8 | Cancel / recovery | cancel the SL by hand in Binance while the bot runs | within ~30 s "Protective order ... restored" (or position closed if it cannot be restored) |
| 9 | Timeout after fill | validator step F lookup by client id; unit test `test_timeout_after_fill_is_not_retried` | exactly one position, no second entry order |
| 10 | Worker restart | restart the Voroa worker with an open position and during a scan | position still managed, no duplicate order, PENDING rows resolved |
| 11 | Duplicate prevention | restart + trigger the same setup again | second attempt shows `DUPLICATE`; one position |
| 12 | DB / exchange reconciliation | open a position by hand on Binance demo | Telegram "Binance holds X but the bot has no record" within 10 min; bot does not touch it |
| 13 | Telegram | Health tab > send test by forcing an alert (stop the worker 3 min) | setup, trade opened, trade closed, health alert all arrive |
| 14 | OpenRouter | Health tab | key valid, calls used today increments, AI-unavailable state = no trade |
| 15 | Vercel dashboard | open from phone and desktop | state matches Binance; Demo/Real and Start/Stop work; no stale data older than the poll interval |
| 16 | Daily +8% lock | set `DAILY_TARGET_PCT=0.5` for the test | after target: "Daily goal reached", no new trades, open positions still managed |
| 17 | Daily -3% protection | set `DAILY_LOSS_LIMIT_PCT=0.5` | new trades refused (`LOSS_LIMIT` / `OPEN_RISK` in the review), open positions managed |
| 18 | Next-day compounding | change `TZ_OFFSET_MIN` so midnight passes during the test, or edit `dstart_*` | new day starts with the updated balance, bot resumes by itself |

Record unexpected results in `testnet_report.json` + screenshots. Do not go to a funded account until all 18 pass twice.

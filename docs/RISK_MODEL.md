# What the risk gate protects against, and what it cannot

## Open-risk reservation (the -3% daily protection)
Before every order: `worst_case = -realized_today + open_risk(all active positions) + risk(new trade) <= 3% x day-start equity`.
- `risk(new trade)` and each open position = quantity x distance to the stop **+ exit fee + exit slippage** (same cost model).
- A stop at/after breakeven reserves only its costs. Orphan exchange positions (stop unknown) reserve costs only.
- Realized P&L of today is signed: earlier profit makes room, earlier losses use it up. The check is vs. day-start equity.
- Separately `tick()` pauses new trades when equity (incl. unrealized) is at -3%, and locks at +8%.

## Protects against
Several positions each risking 1% adding up past the daily limit; trading after the limit is reached; a stale or drifted signal; poor R:R after costs; tiny stops that fees/slippage eat; wrong tick/step/min-notional; duplicate orders and positions; unprotected positions (entry without a stop is closed again); a vanished stop (restored or position closed); a half-finished order after a restart.

## Cannot protect against (be honest with yourself)
- **Gap / slippage beyond the assumed 0.02%.** A stop-market fills at the next available price. In a fast move the real loss can exceed 1R. The reservation does not add a buffer for this. (Observed in the unit test of an escalated spot stop: -2.55R.)
- **Everything falling together.** Alts are correlated: three long alts are close to one bet, and in a crash all stops fill with slippage at once. The gate does not cap correlated exposure.
- **Spot stop-limit not filling.** STOP_LOSS_LIMIT turns into a plain LIMIT sell after the trigger. If price gaps below the limit, it rests unfilled. The watchdog (every 30 s) cancels it and sells at market, so the exit can be worse than the stop. It cannot act between polls or while the worker is down.
- **Worker / database / network outage.** Exchange-side stops (futures algo orders, spot stop-limit) still exist, but the take-profit logic for spot, trailing moves, new-trade blocking and alerts stop.
- **Liquidation.** Isolated margin and max 3x leverage put liquidation far beyond a ~1-2% stop, but a gap through the stop, mark-price spikes or ADL can still hit it. Stops are not a guarantee against liquidation.
- **Funding fees.** Not in the risk calculation. Positions are limited to 6 hours but can cross a funding time; normally tiny, can be larger in extreme funding.
- **Real fees differ** from the assumed 0.05% futures / 0.1% spot (VIP tier, BNB discount). Stage 1 assumes the pessimistic standard rate.
- **Partial fills** are handled for the spot stop; the P&L booked is approximate until compared with the exchange statement.
- **Daily limits are relative to day-start equity** and are measured on this bot's trades; manual trades on the same account are not seen except through equity.
- **The strategy itself has no proven edge.** The risk system limits damage; it does not create profit.

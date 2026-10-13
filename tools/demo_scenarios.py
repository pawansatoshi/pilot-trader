"""Bot-level validation on Binance DEMO using the real bot code (execute / manage / reconcile) with a throw-away SQLite database.
   BINANCE_ENV=demo ALLOW_REAL=1 BINANCE_API_KEY=.. BINANCE_API_SECRET=.. python tools/demo_scenarios.py [--symbol BTC]
Never touches your Neon database. Harness only relaxes the R:R / min-stop / evidence-age checks so that tiny exchange tests are not blocked; every other gate rule stays active.
Items it covers: 11 recovery, 12 timeout-after-fill, 13 restart (simulated part), 14 reconciliation, 15 duplicates, 19/20 Telegram + OpenRouter API checks. Others stay MANUAL."""
import os, sys, time, json, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path[:0] = [ROOT, os.path.dirname(os.path.abspath(__file__))]
from report import *

def scenarios(fx, base, bot, C):
    FS = f"{base}/USDT:USDT"; SYM = base + "USDT"; mid = fx.market(FS)["id"]
    npos = lambda: [p for p in fx.fetch_positions([FS]) if abs(float(p.get("contracts") or 0)) > 0]
    algo_ids = lambda: {str(o.get("algoId")): (o.get("orderType") or o.get("type")) for o in fx.fapiPrivateGetOpenAlgoOrders({"symbol": mid})}
    trade = lambda: [dict(r) for r in C.q("SELECT * FROM trades WHERE status='OPEN' AND mode='real' AND symbol=?", (SYM,), True)]
    def flat():
        try: fx.fapiPrivateDeleteAlgoOpenOrders({"symbol": mid})
        except Exception: pass
        for p in npos(): fx.create_order(FS, "market", "sell" if p["side"] == "long" else "buy", abs(float(p["contracts"])), None, {"reduceOnly": True})
        C.q("UPDATE trades SET status='CANCELLED' WHERE status IN ('OPEN','PENDING')")
    def opener():
        c = C.tech(SYM, "futures"); c["ts"] = time.time(); rid = bot.new_review("futures", "real", c, "LONG", "scenario", {}, "APPROVE", .9, 0)
        return bot.execute("futures", "real", c, "LONG", "scenario", rid), rid
    def need_open():
        ok, _ = opener()
        if not ok or not trade(): raise Exception("bot could not open the position: " + str(C.q("SELECT gate_reasons, decision FROM reviews ORDER BY id DESC LIMIT 1", fetch=True)))
        return trade()[0]
    C.CFG.update(min_eff_rr=0, min_stop_pct=0, max_age=10 ** 6, drift_atr=10 ** 6); C.put("run_futures", "1", True)
    environment(harness_note="R:R, min-stop, evidence-age and drift checks relaxed for exchange tests only", bot_algo_close_mode=bot._algo_mode[0])
    try:
        flat()   # 15 duplicate prevention
        ok1, _ = opener(); ok2, rid2 = opener(); n = len(npos()); rows = trade()
        step(15, "second order for the same symbol is refused; exactly one exchange position and one DB trade", lambda: f"ok1={ok1} ok2={ok2} positions={n} db_open={len(rows)} reason={C.q('SELECT gate_reasons FROM reviews WHERE id=?', (rid2,), True)[0]['gate_reasons'][:90]}" if (ok1 and not ok2 and n == 1 and len(rows) == 1) else (_ for _ in ()).throw(Exception(f"ok1={ok1} ok2={ok2} positions={n} db_open={len(rows)}")))
        # 11 recovery: cancel the stop by hand on the exchange, the bot must restore it
        t = trade()[0]; sl_id = (json.loads(t["extra"]).get("sl_id")); fx.fapiPrivateDeleteAlgoOrder({"algoId": sl_id}); step(11, "protective stop cancelled by hand on the exchange", lambda: sl_id not in algo_ids() or (_ for _ in ()).throw(Exception("still listed")))
        bot.manage(dict(t)); step(11, "bot restored the missing stop on the next check", lambda: (lambda d: d if "STOP_MARKET" in d.values() and "TAKE_PROFIT_MARKET" in d.values() and trade() else (_ for _ in ()).throw(Exception(f"not restored: {d}")))(algo_ids()))
        # 13 restart (simulated): brand-new process state, same DB + same exchange position
        before = (len(npos()), len(algo_ids())); C.close(); C._cache.clear(); bot._ex.clear(); C.refresh(); bot._ex["futures"] = fx; bot.reconcile(); bot.manage(dict(trade()[0]))
        step(13, "simulated restart (fresh client + caches): position still managed, no duplicate order, protection intact", lambda: f"before={before} after={(len(npos()), len(algo_ids()))}" if before == (len(npos()), len(algo_ids())) and len(trade()) == 1 else (_ for _ in ()).throw(Exception(f"before={before} after={(len(npos()), len(algo_ids()))} open_rows={len(trade())}")))
        rec(13, "real restart of the Voroa worker while a position is open", "MANUAL", "redeploy/restart the worker with an open demo position and watch the dashboard + Telegram")
        # 12 timeout after fill
        flat(); t0 = int(fx.fetch_time()); orig = fx.create_order; flaky = {"done": False}
        def wrapped(sym, typ, side, qty, price=None, params=None):
            o = orig(sym, typ, side, qty, price, params)
            if typ == "market" and not (params or {}).get("reduceOnly") and not flaky["done"]: flaky["done"] = True; raise Exception("simulated: request timed out after the exchange had accepted the order")
            return o
        fx.create_order = wrapped
        try: ok, _ = opener()
        finally: fx.create_order = orig
        buys = sum(x["amount"] for x in fx.fetch_my_trades(FS, t0) if x["side"] == "buy"); pos = sum(abs(float(p["contracts"])) for p in npos())
        step(12, "timeout after the exchange accepted the entry: found by clientOrderId, one entry, one trade, protected", lambda: f"opened={ok} entry_fills_total={buys} position={pos} db_open={len(trade())} algo={sorted(algo_ids().values())}" if ok and len(trade()) == 1 and abs(buys - pos) < 1e-9 + 0 and len(algo_ids()) == 2 else (_ for _ in ()).throw(Exception(f"opened={ok} buys={buys} position={pos} db_open={len(trade())} algo={algo_ids()}")))
        # 14 reconciliation: a position the bot knows nothing about
        flat(); fx.create_order(FS, "market", "buy", float(fx.amount_to_precision(FS, max((fx.market(FS)["limits"]["cost"].get("min") or 100) * 1.3 / fx.fetch_ticker(FS)["last"], fx.market(FS)["limits"]["amount"].get("min") or 0))), None, {"clientOrderId": f"orph{int(time.time())}"})
        bot.reconcile(); msg = C.q("SELECT msg FROM logs WHERE msg LIKE 'Binance holds%' ORDER BY id DESC LIMIT 1", fetch=True)
        step(14, "position opened outside the bot is detected, reported, and left untouched", lambda: f"{msg[0]['msg'][:110]} | still open on exchange: {len(npos()) == 1}" if msg and len(npos()) == 1 else (_ for _ in ()).throw(Exception(f"alert={bool(msg)} positions={len(npos())}")))
    finally:
        try: flat()
        except Exception as e: rec(15, "cleanup flatten", "FAIL", f"CLOSE THE DEMO POSITION MANUALLY: {e}")

def api_checks(requests):
    t, c, k = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID"), os.getenv("OPENROUTER_API_KEY")
    if not (t and c): rec(19, "Telegram", "INCONCLUSIVE", "TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set")
    else:
        step(19, "Telegram Bot API accepts the token (getMe)", lambda: requests.get(f"https://api.telegram.org/bot{t}/getMe", timeout=15).json()["result"]["username"])
        step(19, "test message sent", lambda: (lambda r: "sent" if r.get("ok") else (_ for _ in ()).throw(Exception(r)))(requests.post(f"https://api.telegram.org/bot{t}/sendMessage", json={"chat_id": c, "text": "Pilot demo validation: Telegram test message"}, timeout=15).json()))
        rec(19, "message actually arrived on your phone, and setup / opened / closed / health alerts look distinct", "MANUAL", "confirm by eye")
    if not k: rec(20, "OpenRouter", "INCONCLUSIVE", "OPENROUTER_API_KEY not set")
    else:
        step(20, "OpenRouter key valid", lambda: (lambda r: r.json().get("data", {}) if r.status_code == 200 else (_ for _ in ()).throw(Exception(f"HTTP {r.status_code}")))(requests.get("https://openrouter.ai/api/v1/auth/key", headers={"Authorization": "Bearer " + k}, timeout=15)))
        rec(20, "AI budget counter, cooldown, 429 handling and 'AI unavailable = no trade' behavior", "MANUAL", "observe in the Health/AI tab over a day with real scans")
    for i, how in ((21, "open the Vercel dashboard on phone + desktop while a demo position is open; compare every number with Binance Demo"), (22, "set DAILY_TARGET_PCT=0.5, let a demo trade win"), (23, "set DAILY_LOSS_LIMIT_PCT=0.5, let a demo trade lose"), (24, "let the day roll over (or move TZ_OFFSET_MIN) with a running bot")): rec(i, ITEMS[i], "MANUAL", how)

def main():
    if (os.getenv("BINANCE_ENV") or "").lower() != "demo" or os.getenv("ALLOW_REAL") != "1": sys.exit("Refusing to run: needs BINANCE_ENV=demo and ALLOW_REAL=1 (demo keys only).")
    os.environ.pop("DATABASE_URL", None); os.environ["SQLITE_PATH"] = os.path.join(tempfile.mkdtemp(), "scenarios.db")   # never touch the real database
    import requests, common as C, bot
    base = sys.argv[sys.argv.index("--symbol") + 1] if "--symbol" in sys.argv else "BTC"
    C.init(); C.refresh(); C.put("mode", "real", True); fx = bot.rex("futures")
    environment(scenario_database="throw-away SQLite", bot_ccxt_version=__import__("ccxt").__version__)
    scenarios(fx, base, bot, C); api_checks(requests); table()
if __name__ == "__main__": main()

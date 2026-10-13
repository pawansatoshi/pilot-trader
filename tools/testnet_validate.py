"""Binance DEMO validation of the exchange API behavior. Refuses to run unless BINANCE_ENV=demo.
   BINANCE_ENV=demo BINANCE_API_KEY=.. BINANCE_API_SECRET=.. python tools/testnet_validate.py [--trigger] [--tp] [--spot-gap] [--symbol BTC] [--dist 0.0005]
It places tiny orders on the demo exchange. PASS = observed on the exchange, FAIL = tested and failed, INCONCLUSIVE = could not be triggered/verified, MANUAL = needs a manual condition.
Bot-level items (restart, reconcile, duplicates, timeout, Telegram, OpenRouter...) are in tools/demo_scenarios.py. Results merge into testnet_report.json."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report import *
def arg(n, d): return sys.argv[sys.argv.index(n) + 1] if n in sys.argv else d
def main():
    if (os.getenv("BINANCE_ENV") or "").lower() != "demo": sys.exit("Refusing to run: set BINANCE_ENV=demo and use Binance Demo Trading keys.")
    import ccxt
    key, sec = os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET")
    if not (key and sec): sys.exit("Set BINANCE_API_KEY / BINANCE_API_SECRET (demo keys).")
    def mk(cls):
        e = cls({"apiKey": key, "secret": sec, "enableRateLimit": True, "options": {"adjustForTimeDifference": True}})
        if not hasattr(e, "enable_demo_trading"): sys.exit("ccxt too old for demo trading: pip install -U ccxt")
        e.enable_demo_trading(True); e.load_markets(); return e
    base, dist = arg("--symbol", "BTC"), float(arg("--dist", 0.0005))
    fx, sp = mk(ccxt.binanceusdm), mk(ccxt.binance)
    environment(ccxt_version=ccxt.__version__, binance_environment="DEMO TRADING (enable_demo_trading)", futures_api_urls=str((fx.urls.get("api") or {}).get("fapiPrivate")), spot_api_urls=str((sp.urls.get("api") or {}).get("private")), symbol=base, started=time.strftime("%Y-%m-%d %H:%M:%S"), trigger_distance_pct=dist * 100)
    run_futures(fx, base, ccxt, dist, "--trigger" in sys.argv, "--tp" in sys.argv); run_spot(sp, base, dist, "--spot-gap" in sys.argv)
    table(); print(f"\nReport: {PATH}. Evidence (raw exchange responses) is inside the file under 'evidence'.")

def qty_for(e, s, notional):
    m = e.market(s); px = e.fetch_ticker(s)["last"]; q = max(notional * 1.3 / px, (m["limits"]["amount"].get("min") or 0)); return float(e.amount_to_precision(s, q)), px

def run_futures(fx, base, ccxt, dist, do_sl, do_tp):
    FS = f"{base}/USDT:USDT"; fid = fx.market(FS)["id"]; mn = fx.market(FS)["limits"]["cost"].get("min") or 100
    def algo(side, typ, trig, qty, mode):
        p = {"algoType": "CONDITIONAL", "symbol": fid, "side": side, "type": typ, "workingType": "CONTRACT_PRICE", "triggerPrice": fx.price_to_precision(FS, trig), "clientAlgoId": f"tv{typ[:2]}{int(time.time() * 1000) % 10**8}"}
        p.update({"quantity": fx.amount_to_precision(FS, qty), "reduceOnly": "true"} if mode == "qty" else {"closePosition": "true"})
        r = fx.fapiPrivatePostAlgoOrder(p); evidence(f"futures_{typ}_{mode}_request", p); evidence(f"futures_{typ}_{mode}_response", r)
        if not (r.get("algoId") or r.get("clientAlgoId")): raise Exception(f"no algoId in response: {r}")
        return r
    def open_ids(): return [str(o.get("algoId")) for o in fx.fapiPrivateGetOpenAlgoOrders({"symbol": fid})]
    def npos(): return [p for p in fx.fetch_positions([FS]) if abs(float(p.get("contracts") or 0)) > 0]
    def flat():
        try: fx.fapiPrivateDeleteAlgoOpenOrders({"symbol": fid})
        except Exception: pass
        for p in npos(): fx.create_order(FS, "market", "sell" if p["side"] == "long" else "buy", abs(float(p["contracts"])), None, {"reduceOnly": True})
    step(4, "ccxt has algo bindings", lambda: all(hasattr(fx, n) for n in ("fapiPrivatePostAlgoOrder", "fapiPrivateDeleteAlgoOrder", "fapiPrivateDeleteAlgoOpenOrders", "fapiPrivateGetOpenAlgoOrders", "fapiPrivateGetAlgoOrder")) or (_ for _ in ()).throw(Exception("missing bindings: pip install -U ccxt")))
    ok, pm = step(1, "account is in One-way mode", lambda: (lambda r: r if str(r.get("dualSidePosition")).lower() != "true" else (_ for _ in ()).throw(Exception(f"Hedge mode: {r}")))(fx.fapiPrivateGetPositionSideDual())); evidence("futures_position_mode", pm)
    def side_test(long):
        tag = "LONG" if long else "SHORT"; q, px = qty_for(fx, FS, mn); cid = f"tv{tag[0]}{int(time.time())}"; ex, opp, k = ("buy", "SELL", 1) if long else ("sell", "BUY", -1)
        ok, o = step(1, f"{tag} market entry with clientOrderId", lambda: fx.create_order(FS, "market", ex, q, None, {"clientOrderId": cid})); evidence(f"futures_{tag}_entry_response", o)
        if not ok: return
        step(10, f"{tag} lookup by clientOrderId (origClientOrderId)", lambda: (lambda r: f"found, status={r['status']}, id={r['id']}")(fx.fetch_order(None, FS, {"origClientOrderId": cid})))
        step(2 if long else 3, f"{tag} position visible on exchange", lambda: (lambda p: p and [(x["side"], x["contracts"], x["entryPrice"]) for x in p] or (_ for _ in ()).throw(Exception("no position")))(npos()))
        for typ, item in (("STOP_MARKET", 4), ("TAKE_PROFIT_MARKET", 5)):
            for mode in ("qty", "close"):
                trig = px * (1 - .05 * k) if typ == "STOP_MARKET" else px * (1 + .05 * k)
                ok, r = step(item, f"{tag} {typ} algo, {'quantity+reduceOnly' if mode == 'qty' else 'closePosition'}", lambda: algo(opp, typ, trig, q, mode)); evidence(f"algo_mode_{typ}_{mode}_works", ok)
                if ok: step(7, f"{tag} cancel that {typ} order by algoId and verify it is gone", lambda: (fx.fapiPrivateDeleteAlgoOrder({"algoId": r["algoId"]}), None)[1] or (str(r["algoId"]) not in open_ids() or (_ for _ in ()).throw(Exception("still listed after cancel"))) and "cancelled and not listed")
        ok1, sl = step(4, f"{tag} STOP_MARKET algo kept for listing", lambda: algo(opp, "STOP_MARKET", px * (1 - .05 * k), q, "qty"))
        ok2, tp = step(5, f"{tag} TAKE_PROFIT_MARKET algo", lambda: algo(opp, "TAKE_PROFIT_MARKET", px * (1 + .05 * k), q, "qty"))
        step(6, f"{tag} open algo orders listed (SL and TP)", lambda: (lambda l: l if len(l) >= 2 else (_ for _ in ()).throw(Exception(f"expected 2, got {l}")))([(o.get("orderType") or o.get("type"), o.get("algoStatus")) for o in fx.fapiPrivateGetOpenAlgoOrders({"symbol": fid})]))
        def classic():
            try: r = fx.create_order(FS, "STOP_MARKET", opp.lower(), q, None, {"stopPrice": px * (1 - .05 * k), "reduceOnly": True}); evidence(f"futures_{tag}_classic_createOrder_STOP_MARKET", r); return f"ACCEPTED (this ccxt routes conditional orders itself): {str(r)[:120]}"
            except Exception as e: evidence(f"futures_{tag}_classic_createOrder_STOP_MARKET_error", str(e)); return f"rejected as expected: {str(e)[:150]}"
        step(4, f"{tag} [info] classic createOrder STOP_MARKET behaviour", classic)
        flat(); step(7, f"{tag} flatten, cancel remaining algo orders, verify empty", lambda: (not npos() and not open_ids()) and "flat, no algo orders left" or (_ for _ in ()).throw(Exception(f"left: {npos()} {open_ids()}")))
    def watch(item, label, typ, side_open, side_close, mult):
        q, px = qty_for(fx, FS, mn); fx.create_order(FS, "market", side_open, q, None, {"clientOrderId": f"tvW{int(time.time())}"}); t0 = time.time(); trig = px * (1 + mult * dist)
        ok, r = step(item, f"{label}: {typ} placed {dist * 100:.3f}% from {px:.2f}", lambda: algo(side_close, typ, trig, q, "qty"))
        if not ok: return
        def wait():
            while time.time() - t0 < 180:
                if not npos():
                    tr = [x for x in fx.fetch_my_trades(FS, int(t0 * 1000)) if x["side"] == side_close.lower()]; vol = sum(x["amount"] for x in tr)
                    fill = sum(x["price"] * x["amount"] for x in tr) / vol if vol else None; evidence(f"{label}_trigger_vs_fill", dict(trigger=trig, fill=fill, slippage_pct=(abs(fill - trig) / trig * 100 if fill else None), seconds=round(time.time() - t0), trades=tr))
                    return f"fired after {time.time() - t0:.0f}s: trigger {trig:.2f}, actual fill {fill}, leftover algo orders {open_ids()}"
                time.sleep(3)
            raise Inconclusive("price did not reach the trigger within 180s; nothing was proven. Retry later or use a larger --dist.")
        step(item, f"{label}: trigger fires and the position is closed", wait); flat()
    try:
        flat(); side_test(True); side_test(False)
        if do_sl: watch(8, "SL", "STOP_MARKET", "buy", "SELL", -1)
        else: rec(8, "actual SL trigger", "MANUAL", "run with --trigger")
        if do_tp: watch(9, "TP", "TAKE_PROFIT_MARKET", "buy", "SELL", 1)
        else: rec(9, "actual TP behavior", "MANUAL", "run with --tp")
    finally: flat()

def run_spot(sp, base, dist, gap):
    SS = f"{base}/USDT"; o = {}
    q, px = qty_for(sp, SS, 11)
    ok, b = step(16, "spot market BUY", lambda: sp.create_order(SS, "market", "buy", q)); evidence("spot_market_buy_response", b)
    if not ok: return
    free = lambda: float(sp.fetch_balance()[base]["free"]); amt = float(sp.amount_to_precision(SS, free()))
    def sellback():
        try: sp.cancel_all_orders(SS)
        except Exception: pass
        try: return sp.create_order(SS, "market", "sell", float(sp.amount_to_precision(SS, free())))
        except Exception as e: rec(16, "cleanup sell-back", "FAIL", e)
    try:
        stop = px * .99; lim = stop * .998
        ok, st = step(17, "STOP_LOSS_LIMIT sell placed (stop -1%, limit -1.2%)", lambda: sp.create_order(SS, "STOP_LOSS_LIMIT", "sell", amt, float(sp.price_to_precision(SS, lim)), {"stopPrice": float(sp.price_to_precision(SS, stop))})); evidence("spot_stop_loss_limit_response", st)
        if ok:
            step(17, "order rests as open (not triggered, not filled)", lambda: (lambda r: f"status={r['status']} workingTime={r['info'].get('workingTime')} type={r['type']}")(sp.fetch_order(st["id"], SS)))
            step(17, "symbol supports native STOP_LOSS (market stop)? (information only, not implemented)", lambda: "STOP_LOSS" in sp.market(SS)["info"].get("orderTypes", []))
            step(17, "cancel the stop", lambda: sp.cancel_order(st["id"], SS)["status"])
        if not gap: rec(18, "stop triggers but limit does not fill", "MANUAL", "run with --spot-gap (forced scenario) or wait for a real gap"); return
        # FORCED scenario: limit deliberately ABOVE the market, so once the stop triggers the resulting LIMIT sell cannot fill. This is not a natural gap; it shows what the exchange does with a triggered-but-unfillable stop-limit.
        stop2 = px * (1 - dist); lim2 = px * 1.02; t0 = time.time()
        ok, st2 = step(18, f"forced scenario: stop {stop2:.2f}, limit {lim2:.2f} (above market)", lambda: sp.create_order(SS, "STOP_LOSS_LIMIT", "sell", amt, float(sp.price_to_precision(SS, lim2)), {"stopPrice": float(sp.price_to_precision(SS, stop2))}))
        if not ok: rec(18, "forced scenario could not be placed on this exchange", "INCONCLUSIVE", "the exchange rejected the setup (see FAIL above); natural gap still MANUAL"); return
        def wait():
            while time.time() - t0 < 180:
                r = sp.fetch_order(st2["id"], SS); last = sp.fetch_ticker(SS)["last"]; wt = r["info"].get("workingTime")
                if last <= stop2 or (wt not in (None, -1, "-1")):
                    evidence("spot_triggered_unfilled_order", dict(order=r["info"], last_price=last, stop=stop2, limit=lim2, status=r["status"], filled=r.get("filled")))
                    if r["status"] == "open" and not (r.get("filled") or 0): return f"OBSERVED: price {last} <= stop {stop2:.2f}, workingTime={wt}, order still status=open, filled=0 (limit {lim2:.2f} unreachable)"
                    raise Exception(f"unexpected state after trigger: status={r['status']} filled={r.get('filled')}")
                time.sleep(3)
            raise Inconclusive("price never reached the stop within 180s; the triggered-but-unfilled state was not observed")
        ok, _ = step(18, "stop triggered but LIMIT order stays unfilled (observed)", wait)
        def escalate():
            sp.cancel_order(st2["id"], SS); r = sp.create_order(SS, "market", "sell", float(sp.amount_to_precision(SS, free()))); evidence("spot_escalation_market_sell", r)
            return f"cancelled the resting limit, market sell status={r.get('status')} average={r.get('average')}"
        if ok: step(18, "escalation: cancel the resting order, then market sell what is left", escalate)
    finally: sellback()

if __name__ == "__main__": main()

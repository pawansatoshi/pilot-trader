import json, re, time, requests
from common import *
from risk import *
import bus, events, workflow
PROMPT_VER = "v1-s12"
class Dup(Exception): pass
KEY, SEC, ORK = E("BINANCE_API_KEY"), E("BINANCE_API_SECRET"), E("OPENROUTER_API_KEY")
SLIP = 0.0002
_ex, _m, _err = {}, [0, []], {}
class Skip(Exception): pass
class Warn(Exception): pass
dirn = lambda t: 1 if t["side"] == "LONG" else -1
fee = lambda mk: 0.0005 if mk == "futures" else 0.001
csym = lambda s, mk: s[:-4] + ("/USDT:USDT" if mk == "futures" else "/USDT")
def fl(k, d=0):
    try: return float(get(k, d))
    except Exception: return float(d)

def rex(mk):
    if mk not in _ex:
        import ccxt
        e = (ccxt.binanceusdm if mk == "futures" else ccxt.binance)({"apiKey": KEY, "secret": SEC, "enableRateLimit": True, "options": {"adjustForTimeDifference": True}})
        env = (E("BINANCE_ENV") or ("demo" if E("BINANCE_TESTNET") == "1" else "")).lower()
        if env == "demo":     # Binance Demo Trading mirrors live market prices, so the scanner's live public data stays valid
            if not hasattr(e, "enable_demo_trading"): raise RuntimeError("ccxt is too old for Binance demo trading: pip install -U ccxt")
            e.enable_demo_trading(True)
        elif env: raise RuntimeError(f"BINANCE_ENV={env!r} is not supported: the legacy testnet has its own prices, which would not match the live market data the scanner uses. Use BINANCE_ENV=demo.")
        e.load_markets(); _ex[mk] = e
    return _ex[mk]
def problem(mk, ctx, e):
    w, f = explain(e); k = (mk, ctx, w); fresh = time.time() - _err.get(k, 0) > 3600; _err[k] = time.time()
    log(f"{ctx}: {w}. Fix: {f}", alert=fresh, mk=mk, level="err")
def say(mk, text, log_it=True, level="info"):
    if text != get("scan_" + mk):
        put("scan_" + mk, text)
        if log_it: log(text, mk=mk, level=level)

# ---------- free OpenRouter models: daily budget + automatic fallback ----------
def free_models():
    if time.time() - _m[0] > 21600 or not _m[1]:
        ids = []
        for x in jget("https://openrouter.ai/api/v1/models")["data"]:
            p = x.get("pricing") or {}
            try:
                if float(p["prompt"]) == 0 and float(p["completion"]) == 0: ids.append((x.get("context_length") or 0, x["id"]))
            except Exception: pass
        _m[:] = [time.time(), [i for _, i in sorted(ids, reverse=True)]]
    return _m[1]
def llm_used():
    d, n = get("llm_day", "|0").split("|"); return int(n) if d == utc_day() else 0
def llm_can():
    if time.time() < fl("llm_block"): return False, get("llm_err", "AI paused")
    if llm_used() >= CFG["llm_cap"]: return False, f"daily free AI budget used ({llm_used()}/{CFG['llm_cap']}), resets 00:00 UTC"
    if time.time() - fl("llm_last") < CFG["llm_gap"]: return False, "AI cooling down to save the free daily budget"
    return True, ""
SYS = ("You are a cautious crypto trader. Capital protection comes first; skip anything weak. Reply ONLY with JSON: "
       '{"picks":[{"symbol":"X","action":"LONG|SHORT|SKIP","confidence":0.0-1.0,"reason":"max 15 words"}]}. Spot can only LONG.')
def ask(ctx):
    ok, why = llm_can()
    if not ok: return None, None, why
    put("llm_last", time.time())
    try: models = free_models()
    except Exception as e: return None, None, "could not load free model list: " + explain(e)[0]
    err = "no free model answered"
    for mid in models[:3]:                       # fallback chain; every attempt counts toward the daily budget
        put("llm_day", f"{utc_day()}|{llm_used() + 1}")
        try:
            r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": "Bearer " + (ORK or "")}, timeout=45,
                json={"model": mid, "temperature": 0.2, "max_tokens": 500, "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": ctx}]})
            if r.status_code == 429: put("llm_block", time.time() + 1800); put("llm_err", "OpenRouter free limit reached (429), AI paused 30 min"); return None, None, get("llm_err")
            if r.status_code in (401, 403): put("llm_block", time.time() + 3600); put("llm_err", "OpenRouter key rejected, check OPENROUTER_API_KEY"); return None, None, get("llm_err")
            if r.status_code != 200: err = f"{mid} answered HTTP {r.status_code}"; continue
            txt = r.json()["choices"][0]["message"]["content"]
            return mid, json.loads(re.search(r"\{.*\}", txt, re.S).group()), ""
        except Exception as e: err = f"{mid}: {str(e)[:60]}"
    put("llm_err", err); return None, None, err

# ---------- balance ----------
def open_trades(mk, m): return q("SELECT * FROM trades WHERE status='OPEN' AND market=? AND mode=?", (mk, m), True)
def active_trades(mk, m): return q("SELECT * FROM trades WHERE status IN ('PENDING','OPEN') AND market=? AND mode=?", (mk, m), True)
def equity(mk, mode):
    if mode == "demo":
        eq = fl(f"demo_start_{mk}", CFG["start"]) + sum(r["pnl"] or 0 for r in q("SELECT pnl FROM trades WHERE mode='demo' AND market=? AND status='CLOSED'", (mk,), True))
        return eq + sum((last_price(t["symbol"], mk) - t["entry"]) * t["qty"] * dirn(t) for t in open_trades(mk, "demo"))
    b = rex(mk).fetch_balance()
    if mk == "futures": return float(b["info"].get("totalMarginBalance") or b["total"]["USDT"])
    return float(b["total"].get("USDT", 0)) + sum(t["qty"] * last_price(t["symbol"], "spot") for t in open_trades("spot", "real"))

# ---------- real-exchange helpers ----------
# Binance USDS-M futures moved conditional orders (STOP_MARKET / TAKE_PROFIT_MARKET ...) to the Algo Order API on 2025-12-09:
# POST /fapi/v1/order now answers -4120 STOP_ORDER_SWITCH_ALGO. We call the raw ccxt bindings of the algo endpoints directly,
# so the result does not depend on how a given ccxt version routes createOrder().
ALGO_NAMES = ("fapiPrivatePostAlgoOrder", "fapiPrivateDeleteAlgoOrder", "fapiPrivateDeleteAlgoOpenOrders", "fapiPrivateGetOpenAlgoOrders", "fapiPrivateGetAlgoOrder")
_algo_mode = [E("ALGO_CLOSE_MODE", "qty")]; _pos_mode = [0, True]
def has_algo(e): return all(hasattr(e, n) for n in ALGO_NAMES)
def mid(e, s): return e.market(s)["id"]
def one_way(e):   # algo orders below assume One-way mode (positionSide=BOTH)
    if time.time() - _pos_mode[0] > 600: _pos_mode[:] = [time.time(), str(e.fapiPrivateGetPositionSideDual().get("dualSidePosition")).lower() != "true"]
    return _pos_mode[1]
def algo_place(t, typ, trigger):
    e = rex("futures"); s = csym(t["symbol"], "futures"); d = dirn(t)
    p = {"algoType": "CONDITIONAL", "symbol": mid(e, s), "side": "SELL" if d == 1 else "BUY", "type": typ, "workingType": "CONTRACT_PRICE",
         "triggerPrice": e.price_to_precision(s, trigger), "clientAlgoId": f"{t.get('cid') or 'x'}{typ[:2]}{int(time.time()) % 10 ** 6}"[:36]}
    first = None
    for m in (_algo_mode[0], "close" if _algo_mode[0] == "qty" else "qty"):   # quantity+reduceOnly, or closePosition: remember what the exchange accepts
        body = dict(p, **({"quantity": e.amount_to_precision(s, t["qty"]), "reduceOnly": "true"} if m == "qty" else {"closePosition": "true"}))
        try: r = e.fapiPrivatePostAlgoOrder(body); _algo_mode[0] = m; return str(r.get("algoId") or r.get("clientAlgoId"))
        except Exception as err: first = first or err
    raise first
def algo_open(sym):
    e = rex("futures"); r = e.fapiPrivateGetOpenAlgoOrders({"symbol": mid(e, csym(sym, "futures"))})
    return r if isinstance(r, list) else (r.get("orders") or r.get("data") or [])
def algo_cancel(i): rex("futures").fapiPrivateDeleteAlgoOrder({"algoId": i})
def cancel_protection(mk, sym):
    e = rex(mk); s = csym(sym, mk)
    try: e.cancel_all_orders(s)
    except Exception: pass
    if mk == "futures":
        try: e.fapiPrivateDeleteAlgoOpenOrders({"symbol": mid(e, s)})
        except Exception as err: problem(mk, f"cancelling protective orders of {sym}", err)
def place_stop(t, price):
    if t["market"] == "futures": return algo_place(t, "STOP_MARKET", price)
    e = rex("spot"); s = csym(t["symbol"], "spot")
    # Spot has no native stop-market here: STOP_LOSS_LIMIT becomes a plain LIMIT sell once triggered and can stay unfilled if price gaps below the limit
    # (watchdog in manage() escalates to a market sell in that case).
    return e.create_order(s, "STOP_LOSS_LIMIT", "sell", t["qty"], float(e.price_to_precision(s, price * (1 - CFG["stop_gap"]))), {"stopPrice": float(e.price_to_precision(s, price))})["id"]
def real_exit(t):
    e = rex(t["market"]); s = csym(t["symbol"], t["market"])
    if t["market"] == "futures":
        o = e.create_order(s, "market", "sell" if dirn(t) == 1 else "buy", t["qty"], None, {"reduceOnly": True})
    else:
        try: e.cancel_all_orders(s)
        except Exception: pass
        try: o = e.create_order(s, "market", "sell", float(e.amount_to_precision(s, min(t["qty"], float(e.fetch_balance()[t["symbol"][:-4]]["free"])))))
        except Exception: place_stop(t, t["sl"]); raise
    return float(o.get("average") or 0) or None

def real_free(mk):
    b = rex(mk).fetch_balance()
    return float((b.get("info") or {}).get("availableBalance") or b["free"]["USDT"]) if mk == "futures" else float(b["free"].get("USDT", 0))
def exch_positions(mk):   # exchange = source of truth for futures positions
    out = []
    for p in rex(mk).fetch_positions():
        n = float(p.get("contracts") or 0)
        if abs(n) > 0:
            en = float(p.get("entryPrice") or 0); out.append(dict(symbol=p["symbol"].split("/")[0] + "USDT", side="SHORT" if (p.get("side") == "short" or n < 0) else "LONG", entry=en, qty=abs(n), sl=en))
    return out
def lookup_order(e, s, cid):
    try:
        o = e.fetch_order(None, s, {"origClientOrderId": cid})
        return None if o.get("status") in ("canceled", "rejected", "expired") else o
    except Exception: return None
def acct_state(mk, mode, eq):
    ds = get(f"dstart_{mk}_{mode}", "|0").split("|"); dstart = float(ds[1]) if float(ds[1] or 0) > 0 else eq
    act = [dict(r) for r in q("SELECT symbol, side, entry, qty, sl FROM trades WHERE status IN ('PENDING','OPEN') AND market=? AND mode=?", (mk, mode), True)]
    realized = sum(r["pnl"] or 0 for r in q("SELECT pnl FROM trades WHERE status='CLOSED' AND market=? AND mode=? AND closed>=?", (mk, mode, day_start_ts()), True))
    if mode == "real":
        free = real_free(mk)
        if mk == "futures": known = {t["symbol"] for t in act}; act += [p for p in exch_positions(mk) if p["symbol"] not in known]
    else: free = eq - sum(t["entry"] * t["qty"] / (CFG["lev"] if mk == "futures" else 1) for t in act)
    return dict(dstart=dstart, active=act, realized=realized, free=free)
def protect(t):
    ex = {}; e = rex(t["market"])
    if t["market"] == "futures":
        for o in algo_open(t["symbol"]):   # adopt protective orders that already exist (restart safety)
            ty = (o.get("orderType") or o.get("type") or "").upper()
            if ty == "TAKE_PROFIT_MARKET": ex["tp_id"] = str(o["algoId"])
            elif ty == "STOP_MARKET": ex["sl_id"] = str(o["algoId"])
    else:
        for o in e.fetch_open_orders(csym(t["symbol"], "spot")):
            if "STOP" in (o.get("type") or "").upper(): ex["sl_id"] = o["id"]
    if "sl_id" not in ex: ex["sl_id"] = place_stop(t, t["sl"])
    if t["market"] == "futures" and "tp_id" not in ex: ex["tp_id"] = algo_place(t, "TAKE_PROFIT_MARKET", t["tp"])
    return ex
def real_entry(mk, sym, side, qty, cid):
    e = rex(mk); s = csym(sym, mk); d = 1 if side == "LONG" else -1
    if mk == "futures":
        if not has_algo(e): raise Exception("algo order endpoints missing in this ccxt version: pip install -U ccxt (conditional orders need the Algo Order API)")
        if not one_way(e): raise Exception("Futures account is in Hedge Mode: switch to One-way Mode (positionSide=BOTH)")
        if any(p["symbol"] == sym for p in exch_positions(mk)): raise Dup("exchange already holds a position on " + sym)
        try: e.set_margin_mode("isolated", s)
        except Exception: pass
        e.set_leverage(int(CFG["lev"]), s)
    elif e.fetch_open_orders(s): raise Dup("open orders already exist on " + sym)
    qty = float(e.amount_to_precision(s, qty))
    try: o = e.create_order(s, "market", "buy" if d == 1 else "sell", qty, None, {"clientOrderId": cid})
    except Exception:      # outcome unknown (timeout...): ask the exchange by client id, never blindly retry
        o = lookup_order(e, s, cid)
        if not o: raise
    if float(o.get("filled") or 0) <= 0: raise Exception("entry order not filled")
    return o
def cancel_row(t, why, decision="EXEC_FAIL"):
    q("UPDATE trades SET status='CANCELLED', reason=? WHERE id=?", (why[:200], t["id"]))
    if t.get("review_id"): q("UPDATE reviews SET decision=?, gate_reasons=? WHERE id=?", (decision, why[:300], t["review_id"]))
    bus.publish("execution_failed", mk=t["market"], mode=t["mode"], symbol=t["symbol"], reason=why)
def blocked(rid, mk, sym, reasons, mode="demo"):
    q("UPDATE reviews SET gate_ok=0, gate_reasons=?, decision='GATE_REJECT' WHERE id=?", (" | ".join(reasons)[:500], rid))
    log(f"Trade blocked by risk gate ({sym}): " + "; ".join(reasons), mk=mk, level="warn")
    bus.publish("gate_result", mk=mk, mode=mode, symbol=sym, rid=rid, ok=False, reasons=list(reasons), plan=None); return False

def execute(mk, mode, c, side, why, rid):
    """gate -> PENDING row (DB lock) -> exchange order (client id) -> protect -> OPEN. Idempotent across retries/restarts."""
    t0 = time.time(); sym = c["sym"]
    try: eq = equity(mk, mode); live = last_price(sym, mk); flt = symfilters(sym, mk); st = acct_state(mk, mode, eq)
    except Exception as e: return blocked(rid, mk, sym, [f"EXCHANGE: cannot read exchange state ({explain(e)[0]})"], mode)
    g = risk_gate(dict(mk=mk, mode=mode, side=side, symbol=sym, live=live, evid_price=c["price"], evid_age=time.time() - c["ts"], atr=c["atr"], eq=eq,
                       dstart=st["dstart"], realized_today=st["realized"], active=st["active"], free=st["free"], flt=flt, cfg=CFG, real_allowed=real_ok(), blocks=bus.blocks(mk=mk, mode=mode)))
    pl = g["plan"]; q("UPDATE reviews SET gate_ok=?, gate_reasons=?, entry=?, sl=?, tp=?, risk=? WHERE id=?", (1 if g["ok"] else 0, " | ".join(g["reasons"])[:500], pl["entry"], pl["sl"], pl["tp"], pl["R"], rid))
    if not g["ok"]: return blocked(rid, mk, sym, g["reasons"], mode)
    bus.publish("gate_result", mk=mk, mode=mode, symbol=sym, rid=rid, ok=True, reasons=[], plan=pl)
    cid = f"pl{rid}"
    try: q("INSERT INTO trades(market,mode,symbol,side,qty,entry,sl,tp,risk,opened,status,extra,cid,review_id) VALUES(?,?,?,?,?,?,?,?,?,?,'PENDING','{}',?,?)",
           (mk, mode, sym, side, pl["qty"], pl["entry"], pl["sl"], pl["tp"], pl["R"], time.time(), cid, rid))
    except Exception as e:
        if "unique" in str(e).lower() or "duplicate" in str(e).lower(): return blocked(rid, mk, sym, ["DUPLICATE: an order/position for this symbol or review already exists (DB lock)"], mode)
        raise
    t = dict(q("SELECT * FROM trades WHERE cid=?", (cid,), True)[0]); d = 1 if side == "LONG" else -1; fill, qty, ex = pl["entry"], pl["qty"], {}
    sim = mode != "real"; tagm = "[DEMO simulated] " if sim else ""
    log(f"{tagm}Order submitted: {'BUY' if d == 1 else 'SELL'} {pl['qty']} {sym} (client id {cid})", mk=mk, kind="order_submitted", symbol=sym, review_id=rid, trade_id=t["id"])
    bus.publish("order_submitted", mk=mk, mode=mode, symbol=sym, side=side, rid=rid, cid=cid, qty=pl["qty"])
    o = None
    if mode == "real":
        try:
            o = real_entry(mk, sym, side, qty, cid); fill = float(o.get("average") or o.get("price") or live)
            qty = float(rex(mk).amount_to_precision(csym(sym, mk), float(o["filled"]) * (.999 if mk == "spot" else 1)))
            t.update(qty=qty, entry=fill)
            if (fill - pl["sl"]) * d <= 0 or (pl["tp"] - fill) * d <= 0: raise Exception("fill price is beyond the stop/target")
            ex = protect(t)
        except Dup as e: cancel_row(t, str(e), "GATE_REJECT"); log(f"{sym}: {e}", mk=mk, level="warn"); return False
        except Exception as e:
            if o:   # entry exists but is unprotected -> never leave a naked position
                try: real_exit(dict(t, tp=pl["tp"]))
                except Exception as e2: problem(mk, f"{sym}: could not close unprotected position, CLOSE IT MANUALLY", e2)
            cancel_row(t, f"execution failed: {explain(e)[0]}"); problem(mk, f"opening {sym}", e); return False
    log(f"{tagm}{'Fill at' if sim else 'Exchange fill confirmed:'} {sym} {qty:g} @ {fill:.5g}", mk=mk, kind="fill_confirmed", symbol=sym, trade_id=t["id"], data=dict(fill=fill, qty=qty, simulated=sim))
    log(f"{tagm}{'Stop/target recorded, monitored by the bot' if sim else 'Stop protection confirmed on the exchange'} for {sym}", mk=mk, kind="protection_confirmed", symbol=sym, trade_id=t["id"], data=dict(ids=ex, simulated=sim))
    ms = int((time.time() - t0) * 1000); R = abs(fill - pl["sl"])
    q("UPDATE trades SET status='OPEN', entry=?, qty=?, risk=?, extra=?, latency_ms=? WHERE id=?", (fill, qty, R, json.dumps(ex), ms, t["id"]))
    q("UPDATE reviews SET decision='OPENED', trade_id=?, exec_ms=?, entry=?, out_kind='TRADE', out_status='TRACKING' WHERE id=?", (t["id"], ms, fill, rid))
    log(f"Opened {side} {sym} at {fill:.5g} | stop-loss {pl['sl']:.5g} | target {pl['tp']:.5g} | eff R:R {pl['eff_rr']:.2f} | risk {pl['risk_amt']:.2f} USDT | {why}", False, mk, "ok", kind="trade_opened", symbol=sym, trade_id=t["id"], review_id=rid, data=dict(side=side, entry=fill, qty=qty, sl=pl["sl"], tp=pl["tp"], eff_rr=pl["eff_rr"], simulated=sim))
    bus.publish("trade_opened", mk=mk, mode=mode, symbol=sym, side=side, trade_id=t["id"], entry=fill, qty=qty, sl=pl["sl"], tp=pl["tp"], eff_rr=pl["eff_rr"], risk=pl["risk_amt"], simulated=sim); return True

def close(t, px, reason, done=False, mfe=None, mae=None):
    mk = t["market"]; ex = json.loads(t.get("extra") or "{}")
    if t["mode"] == "real" and not done: px = real_exit(t) or px
    if t["mode"] == "real": cancel_protection(mk, t["symbol"])
    pnl = (px - t["entry"]) * t["qty"] * dirn(t) - fee(mk) * (t["entry"] + px) * t["qty"]
    R = t["risk"] or 1; rm = pnl / (t["qty"] * R); mf = (ex.get("mfe", 0) if mfe is None else mfe) / R; ma = (ex.get("mae", 0) if mae is None else mae) / R
    q("UPDATE trades SET status='CLOSED', exit=?, closed=?, pnl=?, reason=?, r_mult=?, mfe_r=?, mae_r=? WHERE id=?", (px, time.time(), pnl, reason, rm, mf, ma, t["id"]))
    if t.get("review_id"): q("UPDATE reviews SET out_status='DONE', exit_px=?, pnl=?, r_mult=?, mfe_r=?, mae_r=?, exit_reason=?, closed_ts=? WHERE id=?", (px, pnl, rm, mf, ma, reason, time.time(), t["review_id"]))
    nm = {"SL": "stop-loss hit", "TP": "target hit", "TIME": "6h time limit", "MANUAL": "closed manually"}.get(reason, reason)
    log(f"Closed {t['symbol']} ({nm}) at {px:.5g}: {pnl:+.2f} USDT ({rm:+.2f}R)", False, mk, "ok" if pnl >= 0 else "warn", kind="trade_closed", symbol=t["symbol"], trade_id=t["id"], review_id=t.get("review_id"), data=dict(exit=px, pnl=pnl, r=rm, reason=reason))
    bus.publish("trade_closed", mk=mk, mode=t["mode"], symbol=t["symbol"], side=t["side"], trade_id=t["id"], exit_px=px, pnl=pnl, r=rm, reason=reason)
def move_sl(t, new):
    ex = json.loads(t["extra"] or "{}")
    if t["mode"] == "real" and ex.get("sl_id"):
        try:
            if t["market"] == "futures": algo_cancel(ex["sl_id"])
            else: rex("spot").cancel_order(ex["sl_id"], csym(t["symbol"], "spot"))
            ex["sl_id"] = place_stop(dict(t, sl=new), new)
            q("UPDATE trades SET extra=? WHERE id=?", (json.dumps(ex), t["id"])); t["extra"] = json.dumps(ex)
        except Exception as e: problem(t["market"], f"moving stop of {t['symbol']}", e)
    q("UPDATE trades SET sl=? WHERE id=?", (new, t["id"])); t["sl"] = new
    log(f"Stop-loss of {t['symbol']} moved to {new:.5g} (locking profit)", mk=t["market"], kind="stop_moved", symbol=t["symbol"], trade_id=t["id"], data=dict(new_sl=new))

def candles_since(sym, mk, chk_ms):   # catches up after worker downtime instead of looking at a fixed 20 minutes
    return klines(sym, "1m", max(5, min(1000, int((time.time() * 1000 - chk_ms) / 60000) + 3)), mk)
def exit_fill(t):   # exchange is the source of truth: use real fills, then infer SL/TP from where it exited
    mk, d = t["market"], dirn(t); px = None
    try:
        tr = [x for x in rex(mk).fetch_my_trades(csym(t["symbol"], mk), int(t["opened"] * 1000)) if x["side"] == ("sell" if d == 1 else "buy") and (x.get("timestamp") or 0) >= t["opened"] * 1000]
        vol = sum(float(x["amount"]) for x in tr)
        if vol > 0: px = sum(float(x["price"]) * float(x["amount"]) for x in tr) / vol
    except Exception: pass
    if px is None: px = last_price(t["symbol"], mk)
    tol = .5 * (t["risk"] or 1); ds, dt = abs(px - t["sl"]), abs(px - t["tp"])
    return px, ("SL" if ds <= tol and ds <= dt else "TP" if dt <= tol else "CLOSED")
def repair(t, ex):   # a protective order vanished while the position is open: restore it or leave the market
    try:
        ex.update(protect(t)); t["extra"] = json.dumps(ex); q("UPDATE trades SET extra=? WHERE id=?", (t["extra"], t["id"]))
        log(f"Protective order of {t['symbol']} was missing and has been restored", True, t["market"], "warn"); return ex
    except Exception as err:
        problem(t["market"], f"{t['symbol']}: protection missing and could not be restored, closing the position", err)
        try: close(t, last_price(t["symbol"], t["market"]), "UNPROTECTED")
        except Exception as e2: problem(t["market"], f"{t['symbol']}: could not close unprotected position, CLOSE IT MANUALLY", e2)
        return None
def manage(t):
    mk = t["market"]; ex = json.loads(t["extra"] or "{}")
    if t["mode"] == "real":
        e = rex(mk); s = csym(t["symbol"], mk)
        if mk == "futures":
            gone = not [p for p in e.fetch_positions([s]) if abs(float(p.get("contracts") or 0)) > 0]
            if not gone:
                ids = {str(o.get("algoId")) for o in algo_open(t["symbol"])}
                if str(ex.get("sl_id")) not in ids or str(ex.get("tp_id")) not in ids:
                    ex = repair(t, ex)
                    if ex is None: return
        else:
            o = e.fetch_order(ex["sl_id"], s); st = o.get("status"); gone = st == "closed"
            if st in ("canceled", "expired", "rejected"):
                ex = repair(t, ex)
                if ex is None: return
            elif st == "open":   # stop may have triggered into a LIMIT order that the market ran away from
                px = last_price(t["symbol"], mk)
                if px < t["sl"] * (1 - CFG["stop_gap"]):
                    log(f"{t['symbol']}: stop-limit cannot fill (price {px:.5g} is below the limit, filled {o.get('filled') or 0}). Escalating to a market sell.", True, mk, "warn")
                    return close(t, px, "SL_ESCALATED")
        if gone:
            px, why = exit_fill(t); return close(t, px, why, done=True)
    p = dict(side=t["side"], entry=t["entry"], sl=t["sl"], tp=t["tp"], risk=t["risk"], chk=ex.get("chk", t["opened"] * 1000 - 60000), mfe=ex.get("mfe", 0), mae=ex.get("mae", 0))
    r = sim_step(p, candles_since(t["symbol"], mk, p["chk"]))
    ex.update(chk=r["chk"], mfe=r["mfe"], mae=r["mae"]); t["extra"] = json.dumps(ex)
    if r["done"]: return close(t, r["exit"], r["reason"], mfe=r["mfe"], mae=r["mae"])
    if r["sl"] != t["sl"]: move_sl(t, r["sl"])
    q("UPDATE trades SET extra=? WHERE id=?", (t["extra"], t["id"]))
    if time.time() - t["opened"] > 6 * 3600: close(t, last_price(t["symbol"], mk), "TIME", mfe=r["mfe"], mae=r["mae"])

# ---------- shadow tracking (what would have happened to setups we did NOT trade) ----------
def start_shadow(rid, mk, c, side, live, flt):
    d = 1 if side == "LONG" else -1; pl = build_plan(side, live, c["atr"], 1000, mk, CFG, flt)
    if not ((pl["entry"] - pl["sl"]) * d > 0 and (pl["tp"] - pl["entry"]) * d > 0): return
    if q("SELECT 1 FROM reviews WHERE out_kind='SHADOW' AND out_status='TRACKING' AND market=? AND symbol=? AND direction=?", (mk, c["sym"], side), True): return
    if q("SELECT COUNT(*) n FROM reviews WHERE out_kind='SHADOW' AND out_status='TRACKING' AND market=?", (mk,), True)[0]["n"] >= 12: return
    q("UPDATE reviews SET entry=?, sl=?, tp=?, risk=?, out_kind='SHADOW', out_status='TRACKING', state=? WHERE id=?",
      (pl["entry"], pl["sl"], pl["tp"], pl["R"], json.dumps(dict(chk=time.time() * 1000, mfe=0, mae=0, sl=pl["sl"])), rid))
def shadow_update(mk):
    for r in q("SELECT * FROM reviews WHERE out_kind='SHADOW' AND out_status='TRACKING' AND market=? ORDER BY id LIMIT 12", (mk,), True):
        try:
            st = json.loads(r["state"]); p = dict(side=r["direction"], entry=r["entry"], sl=st["sl"], tp=r["tp"], risk=r["risk"], chk=st["chk"], mfe=st["mfe"], mae=st["mae"])
            res = sim_step(p, candles_since(r["symbol"], mk, p["chk"]))
            if not res["done"] and time.time() - r["ts"] > 6 * 3600: res = dict(res, done=True, exit=last_price(r["symbol"], mk), reason="TIME")
            if res["done"]:
                q("UPDATE reviews SET out_status='DONE', exit_px=?, r_mult=?, mfe_r=?, mae_r=?, exit_reason=?, closed_ts=? WHERE id=?",
                  (res["exit"], r_net(r["direction"], r["entry"], res["exit"], r["risk"], fee(mk)), res["mfe"] / r["risk"], res["mae"] / r["risk"], res["reason"], time.time(), r["id"]))
            else: q("UPDATE reviews SET state=? WHERE id=?", (json.dumps(dict(chk=res["chk"], mfe=res["mfe"], mae=res["mae"], sl=res["sl"])), r["id"]))
        except Exception as e: problem(mk, f"tracking shadow {r['symbol']}", e)

# ---------- restart safety ----------
def reconcile():
    now = time.time()
    for t in q("SELECT * FROM trades WHERE status='PENDING'", fetch=True):
        if now - t["opened"] < 30: continue
        try:
            if t["mode"] == "demo": cancel_row(t, "worker stopped before the order completed"); continue
            e = rex(t["market"]); s = csym(t["symbol"], t["market"]); o = lookup_order(e, s, t["cid"])
            if not o or float(o.get("filled") or 0) <= 0: cancel_row(t, "entry order never filled"); continue
            fill = float(o.get("average") or t["entry"]); qty = float(o["filled"]) * (.999 if t["market"] == "spot" else 1); t2 = dict(t, entry=fill, qty=qty)
            try: ex = protect(t2)
            except Exception: real_exit(t2); cancel_row(t, "entry filled but could not be protected: position closed"); continue
            q("UPDATE trades SET status='OPEN', entry=?, qty=?, risk=?, extra=? WHERE id=?", (fill, qty, abs(fill - t["sl"]), json.dumps(ex), t["id"]))
            if t.get("review_id"): q("UPDATE reviews SET decision='OPENED', trade_id=?, entry=?, out_kind='TRADE', out_status='TRACKING' WHERE id=?", (t["id"], fill, t["review_id"]))
            log(f"Recovered {t['symbol']} after restart: order was filled, protection confirmed", True, t["market"], "warn", kind="trade_recovered", symbol=t["symbol"], trade_id=t["id"])
            bus.publish("trade_opened", mk=t["market"], mode=t["mode"], symbol=t["symbol"], side=t["side"], trade_id=t["id"], entry=fill, qty=qty, sl=t["sl"], tp=t["tp"], eff_rr=None, risk=None, simulated=False)
        except Exception as e: problem(t["market"], f"recovering pending {t['symbol']}", e)
    if real_ok() and KEY and SEC:
        try:
            db = {r["symbol"] for r in q("SELECT symbol FROM trades WHERE status IN ('PENDING','OPEN') AND market='futures' AND mode='real'", fetch=True)}
            for p in exch_positions("futures"):
                if p["symbol"] not in db and now - fl("orph_" + p["symbol"]) > 3600:
                    put("orph_" + p["symbol"], now); log(f"Binance holds {p['symbol']} but the bot has no record of it. The bot will not touch it: close it manually.", True, "futures", "err")
        except Exception as e: problem("futures", "comparing with exchange positions", e)

# ---------- scan -> (AI) -> deterministic gate -> trade ----------
def new_review(mk, mode, c, rd, model, raw, verdict, conf, ai_ms):
    rk = f"{int(time.time() * 1000)}-{mk}-{mode}-{c['sym']}"
    q("INSERT INTO reviews(rkey,ts,market,mode,symbol,direction,model,prompt_ver,ai_response,ai_verdict,confidence,rule_score,ai_ms,evid_price,decision,out_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING','NONE')",
      (rk, time.time(), mk, mode, c["sym"], rd, model or "-", PROMPT_VER, json.dumps(raw, default=str)[:1500], verdict, conf, c["score"], ai_ms, c["price"]))
    return q("SELECT id FROM reviews WHERE rkey=?", (rk,), True)[0]["id"]

def scan(mk, mode, eq):
    held = {t["symbol"] for t in active_trades(mk, mode)}; cands = []; tried = 0; t_scan = time.time(); workflow.sweep(mk, mode)
    todo = [t for t in top_symbols(mk) if t["symbol"] not in held]; workflow.scan_started(mk, mode, len(todo))
    for t in todo:
        tried += 1
        try: x = tech(t["symbol"], mk); x["ts"] = time.time(); cands.append(x)
        except Exception: pass
    if not tried: return say(mk, f"All top coins already have open positions. Next scan in {CFG['scan'] // 60} min.", log_it=False)
    if not cands: workflow.record(mk, False); raise Exception("no candle data returned by Binance")
    workflow.record(mk, True)
    cands.sort(key=lambda c: -abs(c["score"])); top = cands[:5]; put("watch_" + mk, json.dumps([c["sym"] for c in top[:3]]))
    if mk == "futures":
        pi = {x["symbol"]: x for x in jget(F + "/fapi/v1/premiumIndex")}; sp = {x["symbol"]: float(x["price"]) for x in jget(SP + "/api/v3/ticker/price")}
    for c in top:
        c.update(extras(c["sym"], mk)); sg = 1 if c["score"] > 0 else -1 if c["score"] < 0 else 0
        if mk == "futures":
            fr = float(pi[c["sym"]]["lastFundingRate"]) * 100; c["funding_pct"] = round(fr, 4)
            if c["sym"] in sp: c["basis_pct"] = round((float(pi[c["sym"]]["markPrice"]) / sp[c["sym"]] - 1) * 100, 3)
            if abs(fr) > .05: c["score"] -= .3 * (1 if fr > 0 else -1)
            if c["oi_chg_pct"] > 0: c["score"] += .3 * sg
        if c["depth_imb"] * sg > .2: c["score"] += .2 * sg
        c["score"] = round(c["score"], 2)
    strong = workflow.classify(mk, mode, cands, top)    # ONE deterministic bar (score >= MIN_SCORE) with or without AI; the FSM adds cooldown + state, never a new rule
    workflow.scan_finished(mk, mode, tried, len(cands), len(strong), time.time() - t_scan)
    nxt = f"Next scan in {CFG['scan'] // 60} min."
    if not strong:
        return say(mk, f"Market is quiet: no high-probability setup (best: {top[0]['sym']}, score {top[0]['score']}). {nxt}", log_it=not get("scan_" + mk, "").startswith("Market is quiet"))
    fresh = [c for c in strong if time.time() - fl(f"asked_{mk}_{c['sym']}") > 3600]
    if not fresh: return say(mk, "Setups found but AI already reviewed them in the last hour. Waiting for new ones. " + nxt, log_it=False)
    names = ", ".join(c["sym"] for c in fresh); ctx = ""
    if llm_can()[0]:
        try: fng = jget("https://api.alternative.me/fng/")["data"][0]["value"]
        except Exception: fng = "n/a"
        ctx = json.dumps({"market": mk, "fear_greed": fng, "btc_trend_score": tech("BTCUSDT", mk)["score"], "candidates": [{k: v for k, v in c.items() if k not in ("atr", "ts")} for c in fresh]})
    workflow.begin_review(mk, mode, fresh, bool(ctx)); t_ai = time.time(); model, res, why_not = ask(ctx) if ctx else (None, None, llm_can()[1]); ai_ms = int((time.time() - t_ai) * 1000) if ctx else 0
    if model:
        put("model", model); log(f"Free AI model {model} reviewed {names} ({llm_used()}/{CFG['llm_cap']} free calls used today)", mk=mk)
        for c in fresh: put(f"asked_{mk}_{c['sym']}", time.time())
    else: log(f"AI not used this scan: {why_not}.", mk=mk, level="warn"); put("model", "unavailable")
    plist = (res or {}).get("picks") if isinstance(res, dict) else None
    picks = {p.get("symbol"): p for p in plist if isinstance(p, dict)} if isinstance(plist, list) else {}; taken = 0
    for c in sorted(fresh, key=lambda c: -abs(c["score"])):
        rd = "LONG" if c["score"] > 0 else "SHORT"; p = picks.get(c["sym"]); src = "ai"
        if res is None: verdict, conf, why = "UNAVAILABLE", 0, why_not or "AI unavailable"
        elif not isinstance(plist, list): verdict, conf, why = "INVALID", 0, "AI response has no picks list"
        else: verdict, conf, why = judge_pick(p, rd)
        rid = new_review(mk, mode, c, rd, model, p if p is not None else (res if res is not None else {"unavailable": why}), verdict, conf, ai_ms)
        if verdict in ("INVALID", "UNAVAILABLE") and CFG["no_ai"] == "rules": verdict, src, why = "APPROVE", "rules", f"no valid AI ({why}); NO_AI_POLICY=rules, same threshold"
        workflow.ai_verdict(mk, mode, c, verdict, conf, why, model); go = verdict == "APPROVE"
        if verdict in ("INVALID", "UNAVAILABLE") and src == "rules": go = True
        if go: workflow.to_risk(mk, mode, c["sym"], rd)
        else: workflow.reject(mk, mode, c["sym"], f"AI {verdict.lower()}: {why}"[:200], 3600 if model else max(30, CFG["scan"] - 10))
        q("INSERT INTO decisions(ts,market,mode,symbol,score,action,source,model,why) VALUES(?,?,?,?,?,?,?,?,?)", (time.time(), mk, mode, c["sym"], c["score"], rd if go else "SKIP", src, model or "-", why[:200]))
        log(f"{c['sym']} {rd}: {'AI approved' if go and src == 'ai' else 'rules approved' if go else 'NOT traded (AI ' + verdict.lower() + ')'}: {why}", mk=mk, level="ok" if go else "info")
        if go:
            if taken < 2 and len(active_trades(mk, mode)) < CFG["maxpos"]:
                try: taken += execute(mk, mode, c, rd, why, rid)
                except Exception as e: q("UPDATE reviews SET decision='EXEC_FAIL' WHERE id=?", (rid,)); problem(mk, f"opening {c['sym']}", e); workflow.reject(mk, mode, c["sym"], f"execution error: {explain(e)[0]}", 3600)
            else: q("UPDATE reviews SET decision='GATE_REJECT', gate_ok=0, gate_reasons='MAX_POS: no free slot' WHERE id=?", (rid,)); workflow.reject(mk, mode, c["sym"], "no free position slot", 3600)
        else:
            dec = {"REJECT": "AI_REJECT", "INVALID": "AI_INVALID", "UNAVAILABLE": "AI_UNAVAILABLE_SKIP"}[verdict]; q("UPDATE reviews SET decision=? WHERE id=?", (dec, rid))
            try: start_shadow(rid, mk, c, rd, last_price(c["sym"], mk), symfilters(c["sym"], mk))
            except Exception as e: problem(mk, f"starting shadow tracking for {c['sym']}", e)
    workflow.write_snapshot(mk, mode); say(mk, (f"Entered {taken} trade(s). " if taken else "Setups found but none were traded (AI/risk checks). ") + nxt, log_it=False)

# ---------- health checks (exact problem + fix) ----------
def diag():
    out = []
    def chk(i, name, fn):
        t0 = time.time(); ms = lambda: int((time.time() - t0) * 1000)
        try: out.append(dict(id=i, name=name, status="ok", msg=fn() or "OK", ms=ms()))
        except Skip as s: out.append(dict(id=i, name=name, status="skip", msg=str(s), ms=ms()))
        except Warn as s: out.append(dict(id=i, name=name, status="warn", msg=str(s), fix="Add the missing value in the worker environment variables.", ms=ms()))
        except Exception as e: w, f = explain(e); out.append(dict(id=i, name=name, status="fail", msg=w, fix=f, ms=ms()))
    def keys(mk):
        if not (KEY and SEC): raise Skip("No Binance keys set (only needed for Real mode)")
        rex(mk).fetch_balance(); return "API key works"
    def orouter():
        if not ORK: raise Warn("OPENROUTER_API_KEY missing: AI is off, bot uses strict rules only")
        r = requests.get("https://openrouter.ai/api/v1/auth/key", headers={"Authorization": "Bearer " + ORK}, timeout=15)
        if r.status_code != 200: raise Exception(f"{r.status_code} Client Error: OpenRouter key rejected")
        return f"Key valid, {len(free_models())} free models, {llm_used()}/{CFG['llm_cap']} free calls used today" + (f". Last problem: {get('llm_err')}" if get("llm_err") else "")
    def telegram():
        if not E("TELEGRAM_TOKEN"): raise Skip("Not set (alerts are off)")
        if not E("TELEGRAM_CHAT_ID"): raise Warn("TELEGRAM_CHAT_ID missing")
        r = requests.get(f"https://api.telegram.org/bot{E('TELEGRAM_TOKEN')}/getMe", timeout=15)
        if r.status_code != 200: raise Exception(f"{r.status_code} Client Error: Telegram token rejected")
    def algo():
        if not (KEY and SEC): raise Skip("No Binance keys set (only needed for Real mode)")
        e = rex("futures")
        if not has_algo(e): raise Exception("algo order endpoints missing in this ccxt version: pip install -U ccxt")
        if not one_way(e): raise Exception("Futures account is in Hedge Mode: switch to One-way Mode")
        return f"Algo order API available, One-way mode, env={(E('BINANCE_ENV') or ('demo' if E('BINANCE_TESTNET') == '1' else 'live'))}"
    chk("fut", "Binance Futures data", lambda: jget(F + "/fapi/v1/ping") == {} and "Reachable")
    chk("spot", "Binance Spot data", lambda: jget(SP + "/api/v3/ping") == {} and "Reachable")
    chk("kf", "Binance Futures API key", lambda: keys("futures")); chk("ks", "Binance Spot API key", lambda: keys("spot"))
    chk("algo", "Binance conditional orders (Algo API)", algo); chk("or", "OpenRouter free AI", orouter); chk("tg", "Telegram alerts", telegram)
    try: prev = {h["id"]: h["status"] for h in json.loads(get("health", "[]"))}
    except Exception: prev = {}
    for h in out:
        if h["status"] == "fail" and prev.get(h["id"]) != "fail": tg(f"❌ {h['name']}: {h['msg']}. Fix: {h.get('fix', '')}")
    put("health", json.dumps(out)); put("health_ts", time.time())

# ---------- main loop ----------
def tick(mk):
    mode = get("mode", "demo"); run = get("run_" + mk) == "1"; day = today()
    for m in ("demo", "real"):
        for t in open_trades(mk, m):
            try: manage(t)
            except Exception as e: problem(mk, f"managing {t['symbol']}", e)
    if get("closeall_" + mk) == "1":
        for t in open_trades(mk, mode):
            try: close(t, last_price(t["symbol"], mk), "MANUAL")
            except Exception as e: problem(mk, f"closing {t['symbol']}", e)
        put("closeall_" + mk, "0", force=True)
    shadow_update(mk); workflow.sweep(mk)
    prev = get("prev_run_" + mk, "0")
    if run and prev != "1": put("prev_run_" + mk, "1"); workflow.on_start(mk, mode, diag)          # START > HEALTH CHECK before the first scan
    elif not run and prev == "1": put("prev_run_" + mk, "0"); workflow.on_stop(mk)
    sysm = workflow.refresh_system(mk, mode); blk = [r for r in sysm["reasons"] if r["sev"] == "block"]
    try: eq = equity(mk, mode)
    except Exception as e: workflow.record(mk, False); problem(mk, "reading balance", e); put("state_" + mk, "error"); put("status_" + mk, "paused"); put("now_" + mk, "Cannot read the balance. Open the Health tab for the exact problem."); return
    put(f"eq_{mk}_{mode}", round(eq, 4))
    if run and get("synced_" + mk) != get("started_" + mk): put("synced_" + mk, get("started_" + mk)); events.emit("account_synced", f"Account synchronized ({mode}): balance {eq:.2f} USDT", mk, "ok")
    ds = get(f"dstart_{mk}_{mode}", "|0").split("|")
    if ds[0] != day:
        if ds[0] and float(ds[1]) > 0: log(f"Day {ds[0]} finished at {(eq / float(ds[1]) - 1) * 100:+.2f}%. New day starts with {eq:.2f} USDT (profit compounded).", True, mk, "ok")
        put(f"dstart_{mk}_{mode}", f"{day}|{eq}"); ds = [day, str(eq)]
    st = float(ds[1]); dp = eq / st - 1 if st > 0 else 0; put(f"dpnl_{mk}_{mode}", round(dp * 100, 2))
    q("INSERT INTO daily(day,market,mode,s_eq,e_eq) VALUES(?,?,?,?,?) ON CONFLICT(day,market,mode) DO UPDATE SET e_eq=excluded.e_eq", (day, mk, mode, st, eq))
    halt = "goal" if dp >= CFG["target"] else "loss" if dp <= -CFG["loss"] else ""
    if halt and get(f"halted_{mk}_{mode}") != day + halt:
        put(f"halted_{mk}_{mode}", day + halt)
        log(f"Daily goal +{CFG['target'] * 100:.0f}% reached: profit locked, no new trades today." if halt == "goal" else f"Daily loss limit -{CFG['loss'] * 100:.0f}% reached: trading paused for today.", True, mk, "ok" if halt == "goal" else "warn")
    n_open = len(active_trades(mk, mode))
    if run and not halt and n_open < CFG["maxpos"] and time.time() >= fl("next_" + mk):
        put("next_" + mk, time.time() + CFG["scan"])
        if blk:   # health gate: no scan, no AI call, no order. Open positions are still managed above; a cheap probe lets the failure counter recover.
            say(mk, "New trades are blocked: " + "; ".join(r["msg"] for r in blk), log_it=False); workflow.probe(mk)
        else:
            say(mk, f"Scanning top 50 {mk} coins...", log_it=False)
            try: scan(mk, mode, eq)
            except Exception as e: workflow.record(mk, False); put("next_" + mk, time.time() + 90); say(mk, "Scan failed, retrying in 90 seconds.", log_it=False); problem(mk, "scanning the market", e)
    n_open = len(open_trades(mk, mode)); left = 86400 - (time.time() + CFG["tz"] * 60) % 86400
    put("state_" + mk, halt or ("scanning" if run else "stopped"))
    if halt: now = ("Daily goal reached, profit locked. " if halt == "goal" else "Loss limit reached, paused. ") + f"Resumes automatically in {int(left // 3600)}h {int(left % 3600 // 60)}m with the new balance."
    elif not run: now = "Stopped. Press Start to begin." if not n_open else f"Stopped: no new trades. Still managing {n_open} open position(s)."
    else: now = get("scan_" + mk, "Starting up...") + (f" Managing {n_open} open position(s)." if n_open else "")
    put("now_" + mk, now)
    ss = json.loads(get("scan_state_" + mk, "{}") or "{}"); scanned = ss.get("finished", 0) > fl("started_" + mk)
    put("status_" + mk, "locked" if halt == "goal" else "paused" if (halt == "loss" or (run and blk)) else "trading" if (run and n_open) else "starting" if (run and not scanned) else "scanning" if run else "stopped")
    workflow.write_snapshot(mk, mode)
    if time.time() - fl("snap_" + mk) > 55:
        put("snap_" + mk, time.time())
        for s in list({t["symbol"] for m in ("demo", "real") for t in open_trades(mk, m)} | set(json.loads(get("watch_" + mk, "[]"))) | {"BTCUSDT"})[:8]:
            try: q("INSERT INTO chart(symbol,market,ts,data) VALUES(?,?,?,?) ON CONFLICT(symbol,market) DO UPDATE SET ts=excluded.ts, data=excluded.data", (s, mk, time.time(), json.dumps(klines(s, "15m", 150, mk))))
            except Exception: pass

def main():
    while True:
        try: init(); refresh(); break
        except Exception as e: print("DATABASE CONNECTION FAILED:", explain(e), flush=True); time.sleep(30)
    workflow.configure(filters=lambda s, mk: symfilters(s, mk)); workflow.install(); log("Worker started"); last_diag = n = last_rec = 0
    try: reconcile(); workflow.sweep()
    except Exception as e: print("reconcile failed:", explain(e), flush=True)
    while True:
        try:
            refresh(); put("hb", time.time(), force=True)
            if get("mode") == "real" and not (real_ok() and KEY and SEC): put("mode", "demo", force=True); log("Real mode needs ALLOW_REAL=1 and Binance keys on the worker. Switched back to Demo.", True, level="err")
            for mk in ("futures", "spot"): tick(mk)
            if time.time() - last_rec > 600: reconcile(); last_rec = time.time()
            unsafe = any(get("system_state_" + m, "safe") != "safe" for m in ("futures", "spot"))
            if time.time() - last_diag > (60 if unsafe else 300): diag(); last_diag = time.time()
            if n % 240 == 0: prune()
        except Exception as e: print("loop error:", explain(e), flush=True)
        n += 1; time.sleep(30)
if __name__ == "__main__": main()

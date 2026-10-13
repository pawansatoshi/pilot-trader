"""Autonomous workflow: drives the setup state machine, scanner snapshots, health gating and the Telegram families.
Depends only on pure modules (fsm, events, bus, scanner, common, risk). It NEVER imports bot/ccxt: the order layer reaches it through `bus`."""
import json, time, hashlib
import common as C, fsm, events, bus, scanner
from risk import build_plan
_hyst, _last, _snap, _cfg, _inst = {}, {}, {}, {"filters": lambda s, mk: None}, [False]
RANK = {s: i for i, s in enumerate(("POSITION", "EXECUTION", "RISK_CHECK", "AI_REVIEW", "CANDIDATE", "NEAR_SETUP", "WATCHING", "COOLDOWN", "WAITING"))}
def configure(**kw): _cfg.update(kw)
def fl(k, d=0):
    try: return float(C.get(k, d))
    except Exception: return float(d)

# ---------- scan -> setup states ----------
def scan_started(mk, mode, universe):
    seq = int(fl("scan_seq_" + mk)) + 1; C.put("scan_seq_" + mk, seq); C.put("scan_state_" + mk, json.dumps(dict(phase="scanning", started=time.time(), universe=universe, seq=seq)))
    events.emit("universe_loaded", f"{universe} {mk} symbols in the scan universe", mk); events.emit("scan_started", f"Scan #{seq}: checking {universe} {mk} symbols", mk, data=dict(seq=seq, universe=universe)); return seq
def _announce(mk, mode, c, row, direction):
    if time.time() - fl(f"al_{mk}_{c['sym']}") <= 7200: return
    C.put(f"al_{mk}_{c['sym']}", time.time()); plan = None
    try: plan = build_plan(direction, c["price"], c["atr"], 1000, mk, C.CFG, _cfg["filters"](c["sym"], mk))
    except Exception: pass
    events.send(events.fmt_setup(mk, mode, c["sym"], direction, scanner.describe(c, mk, C.CFG["thr"]), plan))
def observe(mk, mode, c, tier_, reason):
    sym = c["sym"]; d = scanner.direction(c["score"]) or "LONG"; row = fsm.active(mk, mode, sym)
    if row and row["state"] in fsm.BUSY: fsm.touch(row, c["score"]); return row["state"]                       # flow-owned states are never overwritten by the scanner
    if row and tier_ != "WAITING" and row["direction"] != d: fsm.end(row, "direction changed"); row = None
    cur = row["state"] if row else "WAITING"; key = (mk, mode, sym)
    if tier_ == cur:
        _hyst.pop(key, None)
        if row: fsm.touch(row, c["score"], reason)
        return cur
    if tier_ != "CANDIDATE":                                  # display tiers need N consecutive observations; CANDIDATE is immediate (never delays the trade flow)
        t, n = _hyst.get(key, (None, 0)); n = n + 1 if t == tier_ else 1; _hyst[key] = (tier_, n)
        if n < C.CFG["tier_confirm"]:
            if row: fsm.touch(row, c["score"])
            return cur
    _hyst.pop(key, None)
    if tier_ == "WAITING": fsm.end(row, "setup faded"); return "WAITING"
    row = fsm.create(mk, mode, sym, d, tier_, c["score"], reason) if row is None else fsm.move(row, tier_, reason, score=c["score"], last_seen=time.time())
    if tier_ == "CANDIDATE": _announce(mk, mode, c, row, d)
    return row["state"]
def classify(mk, mode, cands, top):
    """Tier every checked symbol, drive the FSM, return the symbols that are CANDIDATE now (= the engine's `strong` list, minus cooldowns)."""
    topset = {c["sym"] for c in top}; thr = C.CFG["thr"]; ts = time.time(); rows, qual = [], []
    for c in cands:
        t = scanner.tier(c["score"], mk, thr); why = scanner.describe(c, mk, thr)
        if t == "CANDIDATE" and c["sym"] not in topset: t = "NEAR_SETUP"; why += "; queued: only the 5 strongest are reviewed per scan"
        st = observe(mk, mode, c, t, why)
        row = dict(symbol=c["sym"], price=c["price"], trend=scanner.trend_label(c["score"]), direction=scanner.direction(c["score"]), score=round(c["score"], 2), vol=c["vol"], rsi=c["rsi"], reason=why, ts=ts)
        if t in ("CANDIDATE", "NEAR_SETUP") and row["direction"] and c.get("atr"):     # indicative levels from the same plan builder the gate uses: display only, never an order
            try:
                p = build_plan(row["direction"], c["price"], c["atr"], 1000, mk, C.CFG, _cfg["filters"](c["sym"], mk)); row["levels"] = dict(entry=p["entry"], sl=p["sl"], tp=p["tp"], eff_rr=round(p["eff_rr"], 2), indicative=True)
            except Exception: pass
        rows.append(row)
        if st == "CANDIDATE" and t == "CANDIDATE": qual.append(c)
    _last[(mk, mode)] = dict(rows=rows, ts=ts, stats={}); return qual
def scan_finished(mk, mode, universe, checked, qualifying, secs):
    seq = int(fl("scan_seq_" + mk)); nxt = fl("next_" + mk)
    C.put("scan_state_" + mk, json.dumps(dict(phase="idle", seq=seq, finished=time.time(), universe=universe, checked=checked, failed=universe - checked, qualifying=qualifying, seconds=round(secs, 1))))
    _last.setdefault((mk, mode), dict(rows=[], ts=time.time(), stats={}))["stats"] = dict(seq=seq, universe=universe, checked=checked, failed=universe - checked, qualifying=qualifying, seconds=round(secs, 1), next_scan_ts=nxt)
    events.emit("scan_done", f"Scan #{seq}: {checked} symbols checked, {qualifying} qualifying setup(s), next scan in {max(0, int(nxt - time.time()))}s", mk, data=_last[(mk, mode)]["stats"]); write_snapshot(mk, mode)
def write_snapshot(mk, mode):
    last = _last.get((mk, mode)); live = {r["symbol"]: r for r in C.q("SELECT symbol, state, direction, cooldown_until, trade_id FROM setups WHERE market=? AND mode=? AND active=1", (mk, mode), True)}
    rows = []
    for r in (last or {}).get("rows", []):
        s = live.pop(r["symbol"], None); rows.append(dict(r, state=s["state"] if s else "WAITING", cooldown_until=s["cooldown_until"] if s else None))
    for sym, s in live.items(): rows.append(dict(symbol=sym, state=s["state"], direction=s["direction"], price=None, trend=None, score=None, reason="not in the latest scan", ts=None, cooldown_until=s["cooldown_until"]))
    rows.sort(key=lambda r: (RANK.get(r["state"], 99), -abs(r["score"] or 0)))
    counts = {}
    for r in rows: counts[r["state"]] = counts.get(r["state"], 0) + 1
    p = dict(market=mk, mode=mode, stats=(last or {}).get("stats", {}), counts=counts, rows=rows, scan_ts=(last or {}).get("ts"))
    h = hashlib.md5(json.dumps(p, sort_keys=True, default=str).encode()).hexdigest()
    if _snap.get((mk, mode)) == h: return False
    C.q("INSERT INTO snapshot(market,mode,ts,data) VALUES(?,?,?,?) ON CONFLICT(market, mode) DO UPDATE SET ts=excluded.ts, data=excluded.data", (mk, mode, time.time(), json.dumps(p, default=str))); _snap[(mk, mode)] = h; return True

# ---------- review / risk / execution transitions (called by the scan flow and by bus subscribers) ----------
def _row(mk, mode, sym, side, need):
    """Active row advanced to `need` (CANDIDATE < RISK_CHECK < EXECUTION < POSITION). Direct executions without scanner history get an implicit row."""
    order = {"CANDIDATE": 0, "AI_REVIEW": 0, "RISK_CHECK": 1, "EXECUTION": 2, "POSITION": 3}; seq = ["CANDIDATE", "RISK_CHECK", "EXECUTION", "POSITION"]
    row = fsm.active(mk, mode, sym)
    if row and row["state"] == "COOLDOWN": fsm.end(row, "superseded by a direct execution"); row = None
    if row is None: return fsm.create(mk, mode, sym, side, need, reason="created at " + need, implicit=True)
    if row["state"] in ("WATCHING", "NEAR_SETUP"): row = fsm.move(row, "CANDIDATE", "direct execution")
    if row["state"] not in order: return row
    for s in seq[order[row["state"]] + 1: order[need] + 1]: row = fsm.move(row, s, "catch-up")
    return row
def begin_review(mk, mode, fresh, attempted):
    for c in fresh:
        ev = {k: v for k, v in c.items() if k not in ("atr", "ts")}; events.emit("evidence_built", f"{c['sym']}: evidence packet built (score {c['score']:+.2f}, RSI {c['rsi']}, volume x{c['vol']})", mk, symbol=c["sym"], data=ev)
        row = fsm.active(mk, mode, c["sym"])
        if attempted and row and row["state"] == "CANDIDATE": fsm.move(row, "AI_REVIEW", "evidence sent to the AI reviewer")
    if attempted: events.emit("ai_started", "AI review started: " + ", ".join(c["sym"] for c in fresh), mk)
def ai_verdict(mk, mode, c, verdict, conf, reason, model):
    events.emit("ai_verdict", f"{c['sym']}: AI {verdict}" + (f" ({conf * 100:.0f}%)" if verdict in ("APPROVE", "REJECT") else "") + f": {reason}", mk, "ok" if verdict == "APPROVE" else "info", symbol=c["sym"], data=dict(verdict=verdict, confidence=conf, model=model))
def to_risk(mk, mode, sym, side): _row(mk, mode, sym, side, "RISK_CHECK")
def reject(mk, mode, sym, reason, cooldown_s):
    row = fsm.active(mk, mode, sym)
    if row and row["state"] in ("CANDIDATE", "AI_REVIEW", "RISK_CHECK", "EXECUTION"): fsm.reject(row, reason, cooldown_s)

# ---------- restart safety ----------
def sweep(mk=None, mode=None):
    """Runs before every scan and at startup. The worker is single-threaded, so any in-flight state found here was left by a crash or error."""
    now = time.time(); w, a = "", []
    if mk: w += " AND market=?"; a.append(mk)
    if mode: w += " AND mode=?"; a.append(mode)
    for r in C.q("SELECT * FROM setups WHERE active=1" + w, tuple(a), True):
        st = r["state"]
        try:
            if st in ("AI_REVIEW", "RISK_CHECK"): fsm.move(r, "CANDIDATE", "interrupted (restart/error): re-qualify with fresh data, never executed from stale state")
            elif st == "EXECUTION":
                t = C.q("SELECT * FROM trades WHERE market=? AND mode=? AND symbol=? ORDER BY id DESC LIMIT 1", (r["market"], r["mode"], r["symbol"]), True)
                if t and t[0]["status"] == "OPEN": fsm.move(r, "POSITION", "order was filled (recovered)", trade_id=t[0]["id"])
                elif t and t[0]["status"] == "PENDING" and now - t[0]["opened"] < 90: pass          # reconcile() resolves it
                else: fsm.reject(r, "execution interrupted: no live order or position exists", 3600)
            elif st == "POSITION":
                t = C.q("SELECT * FROM trades WHERE id=?", (r["trade_id"],), True) if r["trade_id"] else []
                if not (t and t[0]["status"] == "OPEN"): fsm.finish(r, (t[0]["r_mult"] or 0) if t else 0, (t[0]["pnl"] or 0) if t else 0, "position is no longer open", 3600)
            elif st == "COOLDOWN" and (r["cooldown_until"] or 0) <= now: fsm.end(r, "cooldown over")
            elif st in ("WATCHING", "NEAR_SETUP", "CANDIDATE") and now - (r["last_seen"] or r["updated"]) > 3 * C.CFG["scan"] + 60: fsm.end(r, "no longer observed")
        except Exception as e: print(f"sweep failed for {r['symbol']}: {type(e).__name__}: {e}", flush=True)

# ---------- bus subscribers: the order layer publishes, the workflow reacts ----------
def _on_gate(mk, mode, symbol, rid, ok, reasons, plan=None, **_):
    events.emit("risk_verdict", f"{symbol}: risk gate " + ("APPROVED" if ok else "REJECTED: " + "; ".join(reasons)), mk, "ok" if ok else "warn", symbol=symbol, review_id=rid, data=dict(ok=ok, reasons=reasons, plan=plan))
    if not ok: reject(mk, mode, symbol, "risk gate: " + "; ".join(reasons)[:200], 3600)
def _on_submitted(mk, mode, symbol, side, rid, **_): _row(mk, mode, symbol, side, "EXECUTION")
def _on_opened(mk, mode, symbol, side, trade_id, entry, qty, sl, tp, eff_rr, risk, simulated, **_):
    row = _row(mk, mode, symbol, side, "POSITION"); C.q("UPDATE setups SET trade_id=? WHERE id=?", (trade_id, row["id"]))
    events.send(events.fmt_opened(mk, mode, symbol, side, entry, qty, sl, tp, eff_rr, risk, simulated))
def _on_failed(mk, mode, symbol, reason, **_): reject(mk, mode, symbol, "execution failed: " + reason[:200], 3600)
def _on_closed(mk, mode, symbol, side, trade_id, exit_px, pnl, r, reason, **_):
    row = _row(mk, mode, symbol, side, "POSITION")
    if row["state"] == "POSITION": fsm.finish(row, r, pnl, reason, 3600)
    events.emit("outcome_recorded", f"{symbol}: outcome recorded {r:+.2f}R ({pnl:+.2f} USDT, {reason})", mk, "ok" if pnl >= 0 else "warn", symbol=symbol, trade_id=trade_id, data=dict(r=r, pnl=pnl, reason=reason))
    events.send(events.fmt_closed(mk, mode, symbol, side, exit_px, pnl, r, reason))
def install():
    if _inst[0]: return
    for topic, fn in (("gate_result", _on_gate), ("order_submitted", _on_submitted), ("trade_opened", _on_opened), ("execution_failed", _on_failed), ("trade_closed", _on_closed)): bus.subscribe(topic, fn)
    bus.guard(block_reason); _inst[0] = True
def uninstall(): bus.reset(); _inst[0] = False

# ---------- health gating ----------
def system(mk, mode):
    """safe | degraded | blocked for NEW trades. Existing positions are always still managed. AI/Telegram problems only degrade."""
    items = {h["id"]: h for h in json.loads(C.get("health", "[]") or "[]")}; R = []
    def add(sev, i, msg, fix=""): R.append(dict(sev=sev, item=i, msg=msg, fix=fix))
    data_id = "fut" if mk == "futures" else "spot"; h = items.get(data_id)
    if h and h["status"] == "fail": add("block", data_id, f"Binance {mk} market data: {h['msg']}", h.get("fix", ""))
    if mode == "real":
        k = items.get("kf" if mk == "futures" else "ks")
        if k and k["status"] == "fail": add("block", k["id"], f"Binance {mk} API key: {k['msg']}", k.get("fix", ""))
        if mk == "futures" and items.get("algo", {}).get("status") == "fail": add("block", "algo", "Binance conditional orders: " + items["algo"]["msg"], items["algo"].get("fix", ""))
    ts = fl("health_ts")
    if ts and time.time() - ts > 900: add("block", "health", f"health check is stale ({int((time.time() - ts) / 60)} min old)", "Check the worker logs: the health loop stopped running.")
    n = int(fl("consec_" + mk))
    if n >= 3: add("block", "data", f"{n} consecutive market-data/account failures", "Binance or the network is failing; the bot probes again automatically.")
    for i, nm in (("or", "OpenRouter AI"), ("tg", "Telegram")):
        x = items.get(i)
        if x and x["status"] in ("fail", "warn"): add("degrade", i, f"{nm}: {x['msg']}", x.get("fix", ""))
    st = "blocked" if any(r["sev"] == "block" for r in R) else "degraded" if R else "safe"; return dict(state=st, reasons=R)
def block_reason(mk=None, mode=None, **_):
    s = system(mk, mode); b = [r["msg"] for r in s["reasons"] if r["sev"] == "block"]; return "; ".join(b) if b else None   # bus guard => risk_gate SYSTEM_BLOCKED
def refresh_system(mk, mode):
    s = system(mk, mode); prev = C.get("system_state_" + mk, "safe"); C.put("system_" + mk, json.dumps(s))
    if s["state"] != prev:
        C.put("system_state_" + mk, s["state"]); lvl = "err" if s["state"] == "blocked" else "warn" if s["state"] == "degraded" else "ok"
        events.emit("system_state_changed", f"System state [{mk}]: {prev} > {s['state']}" + ("".join(" | " + r["msg"] for r in s["reasons"])), mk, lvl, data=s)
        events.send(events.fmt_health(s["state"], mk, s["reasons"]) if s["state"] != "safe" else f"RISK/HEALTH ALERT [{mk}]\nsystem state: SAFE (recovered from {prev})")
    return s
def record(mk, ok):
    n = 0 if ok else int(fl("consec_" + mk)) + 1
    if str(n) != C.get("consec_" + mk, "0"): C.put("consec_" + mk, n)
def probe(mk):
    """While blocked no scan runs, so this cheap data call is what lets the counter recover."""
    try: C.klines("BTCUSDT", "15m", 5, mk); record(mk, True); return True
    except Exception: return False

# ---------- start / stop ----------
def on_start(mk, mode, diag_fn):
    C.put("started_" + mk, time.time()); events.emit("bot_started", f"{mk.capitalize()} bot started ({mode} mode)", mk, "ok")
    try: diag_fn()
    except Exception as e: events.emit("health_checked", f"Health check could not run: {e}", mk, "err"); return
    items = json.loads(C.get("health", "[]") or "[]"); bad = [h for h in items if h["status"] == "fail"]
    events.emit("health_checked", f"Health check: {sum(h['status'] == 'ok' for h in items)} ok, {len(bad)} failed" + "".join(f" | {h['name']}: {h['msg']}" for h in bad), mk, "err" if bad else "ok", data=items)
    d = {h["id"]: h for h in items}
    if d.get("fut" if mk == "futures" else "spot", {}).get("status") == "ok": events.emit("exchange_connected", f"Binance {mk} market data reachable" + (f" ({d['fut' if mk == 'futures' else 'spot'].get('ms')} ms)" if d.get("fut" if mk == "futures" else "spot", {}).get("ms") is not None else ""), mk, "ok")
def on_stop(mk): events.emit("bot_stopped", f"{mk.capitalize()} bot stopped: no new trades; open positions stay managed", mk, "warn")

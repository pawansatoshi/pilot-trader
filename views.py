"""Read models for the (future) dashboard: pure functions over the database. They return what the backend recorded, nothing else:
no synthesized trades, signals or AI output. FastAPI handlers in api/index.py are thin wrappers around these."""
import json, time
import common as C
def _j(x, d=None):
    try: return json.loads(x) if x else d
    except Exception: return d
def _f(x):
    try: return float(x)
    except Exception: return None
def status(mode=None):
    S = C.refresh(); mode = mode or S.get("mode", "demo"); out = dict(mode=mode, real_allowed=C.real_ok(), now=time.time(), worker_heartbeat_age=(time.time() - _f(S["hb"])) if S.get("hb") else None, markets={})
    for mk in ("futures", "spot"):
        n = C.q("SELECT COUNT(*) n FROM trades WHERE status='OPEN' AND market=? AND mode=?", (mk, mode), True)[0]["n"]
        out["markets"][mk] = dict(running=S.get("run_" + mk) == "1", status=S.get("status_" + mk, "stopped"), detail=S.get("now_" + mk, ""), equity=_f(S.get(f"eq_{mk}_{mode}")), day_pnl_pct=_f(S.get(f"dpnl_{mk}_{mode}")),
            target_pct=C.CFG["target"] * 100, loss_limit_pct=C.CFG["loss"] * 100, open_positions=n, next_scan_ts=_f(S.get("next_" + mk)), scan=_j(S.get("scan_state_" + mk)), system=_j(S.get("system_" + mk), dict(state="safe", reasons=[])))
    return out
def scanner(mk, mode=None):
    mode = mode or C.refresh().get("mode", "demo"); r = C.q("SELECT ts, data FROM snapshot WHERE market=? AND mode=?", (mk, mode), True)
    if not r: return dict(market=mk, mode=mode, rows=[], counts={}, stats={}, note="no scan has completed yet")
    return dict(_j(r[0]["data"], {}), snapshot_ts=r[0]["ts"])
def setups(mk=None, mode=None, active_only=True, limit=100):
    w, a = [], []
    if mk: w.append("market=?"); a.append(mk)
    if mode: w.append("mode=?"); a.append(mode)
    if active_only: w.append("active=1")
    return C.q("SELECT * FROM setups" + (" WHERE " + " AND ".join(w) if w else "") + " ORDER BY updated DESC LIMIT ?", (*a, limit), True)
def setup_detail(sid):
    s = C.q("SELECT * FROM setups WHERE id=?", (sid,), True)
    if not s: return None
    s = s[0]; ev = C.q("SELECT ts, from_state, to_state, reason FROM setup_events WHERE setup_id=? ORDER BY id", (sid,), True)
    rv = C.q("SELECT id, ts, model, prompt_ver, ai_verdict, confidence, ai_response, ai_ms, gate_ok, gate_reasons, decision, entry, sl, tp, rule_score FROM reviews WHERE market=? AND mode=? AND symbol=? AND ts>=? ORDER BY id DESC LIMIT 1", (s["market"], s["mode"], s["symbol"], s["created"] - 1), True)
    rv = rv[0] if rv else None
    if rv: rv["ai_response"] = _j(rv["ai_response"], rv["ai_response"])
    tr = C.q("SELECT * FROM trades WHERE id=?", (s["trade_id"],), True) if s["trade_id"] else []
    return dict(setup=s, events=ev, review=rv, trade=tr[0] if tr else None)
def events(since_id=0, limit=100, mk=None, kinds=None):
    w, a = ["id>?"], [since_id]
    if mk: w.append("(market=? OR market='')"); a.append(mk)
    if kinds: w.append("kind IN (" + ",".join("?" * len(kinds)) + ")"); a += list(kinds)
    rows = C.q("SELECT * FROM logs WHERE " + " AND ".join(w) + " ORDER BY id DESC LIMIT ?", (*a, limit), True)
    for r in rows: r["data"] = _j(r["data"])
    return sorted(rows, key=lambda r: r["id"])
def positions(mode=None):
    S = C.refresh(); mode = mode or S.get("mode", "demo"); age = (time.time() - _f(S["hb"])) if S.get("hb") else None
    rows = C.q("SELECT * FROM trades WHERE status='OPEN' AND mode=? ORDER BY id DESC", (mode,), True)
    for r in rows: r["exchange_verified"] = (age is not None and age < 120) if mode == "real" else None; r["simulated"] = mode == "demo"
    return rows
def infra():
    S = C.refresh(); items = _j(S.get("health"), []); hb = _f(S.get("hb")); used = 0; d = S.get("llm_day", "|0").split("|")
    if d[0] == C.utc_day(): used = int(d[1])
    try: C.q("SELECT 1 AS ok", fetch=True); db = dict(ok=True)
    except Exception as e: db = dict(ok=False, error=C.explain(e)[0])
    pick = lambda i: next((h for h in items if h["id"] == i), None)
    return dict(worker=dict(ok=hb is not None and time.time() - hb < 150, heartbeat_age=(time.time() - hb) if hb else None), database=db, binance_futures=pick("fut"), binance_spot=pick("spot"), keys=dict(futures=pick("kf"), spot=pick("ks"), algo=pick("algo")),
        openrouter=dict(check=pick("or"), calls_used=used, calls_remaining=max(0, C.CFG["llm_cap"] - used), cap=C.CFG["llm_cap"], model=S.get("model"), last_error=S.get("llm_err"), state="paused" if _f(S.get("llm_block")) and _f(S.get("llm_block")) > time.time() else "available"),
        telegram=pick("tg"), health_checked_at=_f(S.get("health_ts")), system={mk: _j(S.get("system_" + mk), dict(state="safe", reasons=[])) for mk in ("futures", "spot")})


# ======================= Stage 3B: terminal read model (everything below only reshapes rows the backend already recorded) =======================
MARKETS = ("futures", "spot")
STATE_LABEL = {"WAITING": "WAITING", "WATCHING": "WATCHING", "NEAR_SETUP": "NEAR SETUP", "CANDIDATE": "SETUP DETECTED", "AI_REVIEW": "AI REVIEW", "RISK_CHECK": "RISK CHECK", "EXECUTION": "EXECUTION", "POSITION": "POSITION", "CLOSED": "CLOSED", "OUTCOME": "OUTCOME", "REJECTED": "SETUP REJECTED", "COOLDOWN": "COOLDOWN"}
KIND_LABEL = {"bot_started": "BOT STARTED", "bot_stopped": "BOT STOPPED", "health_checked": "HEALTH CHECK", "exchange_connected": "EXCHANGE CONNECTED", "account_synced": "ACCOUNT SYNCED", "universe_loaded": "UNIVERSE LOADED", "scan_started": "MARKET SCAN", "scan_done": "MARKET SCAN",
              "evidence_built": "EVIDENCE", "ai_started": "AI REVIEW", "ai_verdict": "AI VERDICT", "risk_verdict": "RISK CHECK", "order_submitted": "ORDER SUBMITTED", "fill_confirmed": "FILL CONFIRMED", "protection_confirmed": "PROTECTION CONFIRMED",
              "trade_opened": "TRADE OPENED", "stop_moved": "STOP MOVED", "trade_closed": "TRADE CLOSED", "outcome_recorded": "OUTCOME", "trade_recovered": "TRADE RECOVERED", "system_state_changed": "SYSTEM STATE"}
GROUP = {"setup_state_changed": "setup", "evidence_built": "setup", "ai_started": "ai", "ai_verdict": "ai", "risk_verdict": "risk", "order_submitted": "order", "fill_confirmed": "order", "protection_confirmed": "order", "trade_opened": "trade", "stop_moved": "trade", "trade_closed": "trade", "outcome_recorded": "trade", "trade_recovered": "trade"}
TRADE_KINDS = {"order_submitted", "fill_confirmed", "protection_confirmed", "trade_opened", "stop_moved", "trade_closed", "outcome_recorded", "trade_recovered"}
DECISION_STATUS = {"PENDING": "in review", "OPENED": "approved by AI, risk gate passed, trade opened", "GATE_REJECT": "risk gate rejected the trade", "AI_REJECT": "rejected by AI", "AI_INVALID": "AI answer invalid: no trade", "AI_UNAVAILABLE_SKIP": "AI unavailable: no trade", "EXEC_FAIL": "execution failed"}
AUTHORITY = "AI is a reviewer. It cannot change size, leverage, entry, stop or target, and it cannot bypass the risk gate."
STEPS = (("START", "START"), ("HEALTH", "HEALTH CHECK"), ("SCANNING", "SCANNING"), ("SETUP", "SETUP DETECTED"), ("AI", "AI REVIEW"), ("RISK", "RISK CHECK"), ("EXECUTION", "EXECUTION"), ("FILL", "EXCHANGE FILL"), ("PROTECTION", "PROTECTION"), ("POSITION", "POSITION"), ("CLOSED", "CLOSED"), ("OUTCOME", "OUTCOME"))
RANK = {"CANDIDATE": 1, "AI_REVIEW": 2, "RISK_CHECK": 3, "EXECUTION": 4, "POSITION": 5}

def _modes(rows):
    t = {r["trade_id"] for r in rows if r.get("trade_id")}; s = {r["setup_id"] for r in rows if r.get("setup_id")}; tm, sm = {}, {}
    if t: tm = {r["id"]: r["mode"] for r in C.q("SELECT id, mode FROM trades WHERE id IN (" + ",".join("?" * len(t)) + ")", tuple(t), True)}
    if s: sm = {r["id"]: r["mode"] for r in C.q("SELECT id, mode FROM setups WHERE id IN (" + ",".join("?" * len(s)) + ")", tuple(s), True)}
    return tm, sm
def timeline(mk=None, mode=None, since_id=0, limit=60, include_msgs=False):
    """Labelled event stream. Events linked to a trade/setup of another mode are left out; DEMO trade events are flagged simulated."""
    w, a = ["id>?"], [since_id]
    if mk: w.append("(market=? OR market='' OR market IS NULL)"); a.append(mk)
    rows = C.q("SELECT * FROM logs WHERE " + " AND ".join(w) + " ORDER BY id DESC LIMIT ?", (*a, limit * 3), True); tm, sm = _modes(rows); out = []
    for r in rows:
        k = r["kind"] or "msg"; d = _j(r["data"]); dd = d if isinstance(d, dict) else {}; m = tm.get(r["trade_id"]) or sm.get(r["setup_id"])   # data can be a list (health_checked)
        if mode and m and m != mode: continue
        if k == "msg" and not (include_msgs or r["level"] in ("warn", "err")): continue
        label = ("ALERT" if k == "msg" else STATE_LABEL.get(dd.get("to"), "SETUP STATE") if k == "setup_state_changed" else KIND_LABEL.get(k, k.replace("_", " ").upper()))
        sim = bool((k in TRADE_KINDS and (m or mode) == "demo") or dd.get("simulated") or str(r["msg"]).startswith("[DEMO simulated]"))
        out.append(dict(id=r["id"], ts=r["ts"], kind=k, label=label, group=GROUP.get(k, "system"), text=r["msg"], level=r["level"] or "info", symbol=r["symbol"], market=r["market"], mode=m, simulated=sim, setup_id=r["setup_id"], trade_id=r["trade_id"], data=d))
        if len(out) >= limit: break
    return sorted(out, key=lambda e: e["id"])
def trades(mk, mode=None, symbol=None, limit=30):
    mode = mode or C.refresh().get("mode", "demo"); w, a = ["market=?", "mode=?", "status IN ('OPEN','CLOSED')"], [mk, mode]
    if symbol: w.append("symbol=?"); a.append(symbol)
    rows = C.q("SELECT id, market, mode, symbol, side, qty, entry, sl, tp, risk, opened, status, exit, closed, pnl, reason, r_mult, mfe_r, mae_r, review_id FROM trades WHERE " + " AND ".join(w) + " ORDER BY id DESC LIMIT ?", (*a, limit), True)
    ids = [r["id"] for r in rows]; mv = {}
    if ids:
        for e in C.q("SELECT trade_id, ts, data FROM logs WHERE kind='stop_moved' AND trade_id IN (" + ",".join("?" * len(ids)) + ") ORDER BY id", tuple(ids), True): mv.setdefault(e["trade_id"], []).append(dict(ts=e["ts"], new_sl=(_j(e["data"]) or {}).get("new_sl")))
    for r in rows: r["stop_moves"] = mv.get(r["id"], []); r["simulated"] = r["mode"] == "demo"
    return rows
def ai_view(rv):
    """Only what the AI actually returned. Missing fields stay None (the UI prints 'not provided')."""
    if not rv: return None
    resp = rv.get("ai_response"); p = resp if isinstance(resp, dict) and "action" in resp else {}; conf = rv.get("confidence")
    return dict(verdict=rv.get("ai_verdict"), confidence_pct=round(conf * 100) if conf is not None else None, grade=p.get("grade"), reason=p.get("reason"), case_for=p.get("case_for"), case_against=p.get("case_against"), fatal_flaw=p.get("fatal_flaw"), invalidation=p.get("invalidation"),
        model=None if rv.get("model") in (None, "-") else rv.get("model"), prompt_version=rv.get("prompt_ver"), latency_ms=rv.get("ai_ms"), decision=rv.get("decision"), status=DECISION_STATUS.get(rv.get("decision"), rv.get("decision")), gate_ok=rv.get("gate_ok"), gate_reasons=rv.get("gate_reasons"), authority=AUTHORITY)
def _review_for(s):
    r = C.q("SELECT id, ts, model, prompt_ver, ai_verdict, confidence, ai_response, ai_ms, gate_ok, gate_reasons, decision, entry, sl, tp, rule_score FROM reviews WHERE market=? AND mode=? AND symbol=? AND ts>=? ORDER BY id DESC LIMIT 1", (s["market"], s["mode"], s["symbol"], s["created"] - 1), True)
    if r: r[0]["ai_response"] = _j(r[0]["ai_response"], r[0]["ai_response"])
    return r[0] if r else None
def recent_rejections(mk, mode, hours=24, limit=10):
    return C.q("SELECT e.ts, e.from_state, e.reason, s.symbol, s.direction FROM setup_events e JOIN setups s ON s.id=e.setup_id WHERE e.to_state='REJECTED' AND s.market=? AND s.mode=? AND e.ts>? ORDER BY e.id DESC LIMIT ?", (mk, mode, time.time() - hours * 3600, limit), True)
def _focus(mk, mode):
    rows = C.q("SELECT * FROM setups WHERE market=? AND mode=? AND active=1 AND state IN ('EXECUTION','RISK_CHECK','AI_REVIEW','CANDIDATE','POSITION')", (mk, mode), True)
    if not rows: return None
    order = {"EXECUTION": 0, "RISK_CHECK": 1, "AI_REVIEW": 2, "CANDIDATE": 3, "POSITION": 4}
    return sorted(rows, key=lambda r: (order[r["state"]], -(r["updated"] or 0)))[0]
def workflow_view(mk, mode, S, focus, review, now):
    """The 12-step pipeline, derived from real state: bot flags, health timestamps, FSM state and the order events that exist for the focus setup."""
    run = S.get("run_" + mk) == "1"; started = _f(S.get("started_" + mk)) or 0; hts = _f(S.get("health_ts")) or 0; st = {c: "pending" for c, _ in STEPS}
    last = C.q("SELECT ts FROM logs WHERE kind='outcome_recorded' AND market=? ORDER BY id DESC LIMIT 1", (mk,), True); recent = bool(last and now - last[0]["ts"] < 900)
    if run:
        st["START"] = "done"; st["HEALTH"] = "done" if hts >= started else "active"
        if st["HEALTH"] == "done": st["SCANNING"] = "active"
    else: st.update(START="off", HEALTH="off", SCANNING="off")
    stage = "STOPPED" if not run else "HEALTH" if st["HEALTH"] == "active" else "SCANNING"
    if focus:
        r = RANK[focus["state"]]; ev = {x["kind"] for x in C.q("SELECT kind FROM logs WHERE market=? AND symbol=? AND ts>=? AND kind IN ('order_submitted','fill_confirmed','protection_confirmed')", (mk, focus["symbol"], focus["state_since"] if focus["state"] == "EXECUTION" else (focus["created"] or 0)), True)}
        if run: st["SCANNING"] = "done"
        st["SETUP"] = "active" if r == 1 else "done"; stage = "SETUP"
        st["AI"] = "pending" if r < 2 else "active" if r == 2 else ("skipped" if (review is None or review.get("model") in (None, "-")) else "done")
        st["RISK"] = "pending" if r < 3 else "active" if r == 3 else "done"; st["EXECUTION"] = "pending" if r < 4 else "active" if r == 4 else "done"
        if r >= 5: st.update(FILL="done", PROTECTION="done", POSITION="active")
        elif r == 4: st["FILL"] = "done" if "fill_confirmed" in ev else "active" if "order_submitted" in ev else "pending"; st["PROTECTION"] = "done" if "protection_confirmed" in ev else "active" if "fill_confirmed" in ev else "pending"
        stage = {1: "SETUP", 2: "AI", 3: "RISK", 4: "EXECUTION", 5: "POSITION"}[r]
        if r == 4: stage = "PROTECTION" if st["PROTECTION"] == "active" else "FILL" if st["FILL"] == "active" else "EXECUTION"
    elif recent: st["CLOSED"] = st["OUTCOME"] = "done"
    return dict(stage=stage, label=dict(STEPS).get(stage, "STOPPED"), steps=[dict(code=c, label=l, state=st[c]) for c, l in STEPS], recent_outcome=recent)
def _tile(h):
    if not h: return dict(status="unknown", label="UNKNOWN", msg="not checked yet", fix=None, ms=None)
    return dict(status=h["status"], label={"ok": "OK", "fail": "FAILED", "warn": "WARNING", "skip": "NOT CONFIGURED"}.get(h["status"], h["status"].upper()), msg=h.get("msg"), fix=h.get("fix"), ms=h.get("ms"))
def _market(mk, mode, S, st, now):
    snap = scanner(mk, mode); stats = snap.get("stats") or {}; focus = _focus(mk, mode); rv = _review_for(focus) if focus else None
    dstart = _f((S.get(f"dstart_{mk}_{mode}", "|").split("|") + [""])[1]); eq = st["equity"]; dp = st["day_pnl_pct"] or 0.0; tgt, lim = C.CFG["target"] * 100, C.CFG["loss"] * 100; state = S.get("state_" + mk, "stopped")
    sysm = st["system"] or dict(state="safe", reasons=[]); blocks = [r for r in sysm["reasons"] if r["sev"] == "block"]
    lock = dict(code="TARGET_REACHED", label="TARGET REACHED — NEW TRADES LOCKED", detail="Open positions keep following normal risk management. Trading resumes automatically next day with the updated balance.") if state == "goal" else \
           dict(code="LOSS_LIMIT", label="DAILY LOSS LIMIT — NEW TRADES PAUSED", detail="Open positions keep following normal risk management. Trading resumes automatically next day with the updated balance.") if state == "loss" else None
    pos = positions(mode); pos = [p for p in pos if p["market"] == mk]; setup = None
    if focus:
        lv = (rv or {}).get("entry") and dict(entry=rv["entry"], sl=rv["sl"], tp=rv["tp"])
        row = next((r for r in snap["rows"] if r["symbol"] == focus["symbol"]), None) or {}
        tr = C.q("SELECT * FROM trades WHERE id=?", (focus["trade_id"],), True) if focus["trade_id"] else []
        setup = dict(id=focus["id"], symbol=focus["symbol"], direction=focus["direction"], state=focus["state"], state_label=STATE_LABEL[focus["state"]], since=focus["state_since"], reason=focus["reason"], score=focus["score"], is_trade=focus["state"] == "POSITION",
                     headline="TRADE OPEN" if focus["state"] == "POSITION" else "SETUP, NOT A TRADE YET", levels=(dict(tr[0] and dict(entry=tr[0]["entry"], sl=tr[0]["sl"], tp=tr[0]["tp"]), kind="actual") if tr else dict(lv or row.get("levels") or {}, kind="indicative")), ai=ai_view(rv))
    lastout = C.q("SELECT ts, symbol, data FROM logs WHERE kind='outcome_recorded' AND market=? ORDER BY id DESC LIMIT 1", (mk,), True)
    return dict(running=st["running"], status=dict(code=st["status"], label={"stopped": "STOPPED", "starting": "STARTING", "scanning": "SCANNING", "trading": "IN POSITION", "paused": "PAUSED", "locked": "LOCKED"}.get(st["status"], str(st["status"]).upper()), detail=st["detail"]),
        balance=eq, day_start=dstart, day_pnl_abs=(eq - dstart) if (eq is not None and dstart) else None, day_pnl_pct=dp, target=dict(pct=tgt, progress=max(0.0, min(1.0, max(dp, 0) / tgt)), reached=dp >= tgt), protection=dict(pct=lim, used=max(0.0, min(1.0, max(-dp, 0) / lim)), hit=dp <= -lim),
        lock=lock, blocked=dict(label="NEW TRADES BLOCKED", reasons=[dict(msg=r["msg"], fix=r.get("fix")) for r in blocks], note="Existing positions are still being managed.") if blocks else None, degraded=[dict(msg=r["msg"], fix=r.get("fix")) for r in sysm["reasons"] if r["sev"] == "degrade"], system_state=sysm["state"],
        workflow=workflow_view(mk, mode, S, focus, rv, now), scan=dict(stats=stats, phase=(st["scan"] or {}).get("phase"), next_scan_ts=st["next_scan_ts"], counts=snap.get("counts", {}), note=snap.get("note")), rows=snap.get("rows", []), rejections=recent_rejections(mk, mode), active_setup=setup, positions=pos,
        last_outcome=dict(ts=lastout[0]["ts"], symbol=lastout[0]["symbol"], **(_j(lastout[0]["data"]) or {})) if lastout else None, timeline=timeline(mk, mode, 0, 40))
def dashboard(mode=None):
    """One call = the whole terminal. All fields come from the database; derived labels (lock, blocked, workflow step) are computed here so the frontend only renders."""
    S = C.refresh(); st = status(mode); mode = st["mode"]; now = time.time(); inf = infra(); k = inf["keys"]; op = inf["openrouter"]
    return dict(now=now, mode=mode, real_allowed=C.real_ok(), mode_banner=dict(label="REAL: live money" if mode == "real" else "DEMO: simulated fills, no real orders", real=mode == "real", real_locked=not C.real_ok()),
        infra=dict(binance=dict(futures=_tile(inf["binance_futures"]), spot=_tile(inf["binance_spot"]), keys=dict(futures=_tile(k["futures"]), spot=_tile(k["spot"])), algo=_tile(k["algo"])),
                   worker=dict(label="ALIVE" if inf["worker"]["ok"] else "OFFLINE" if inf["worker"]["heartbeat_age"] is not None else "UNKNOWN", ok=inf["worker"]["ok"], heartbeat_age=inf["worker"]["heartbeat_age"]), database=dict(label="CONNECTED" if inf["database"]["ok"] else "FAILED", ok=inf["database"]["ok"], error=inf["database"].get("error")),
                   openrouter=dict(_tile(op["check"]), calls_used=op["calls_used"], calls_remaining=op["calls_remaining"], cap=op["cap"], model=op["model"], state=op["state"], last_error=op["last_error"]), telegram=_tile(inf["telegram"]), checked_at=inf["health_checked_at"]),
        markets={mk: _market(mk, mode, S, st["markets"][mk], now) for mk in MARKETS})

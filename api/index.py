import os, sys, time, json, hmac, hashlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import common as C
import views
from page import HTML, LOGIN
PW = os.getenv("DASH_PASSWORD", "")
TOK = hmac.new((PW + "|pilot").encode(), b"ok", hashlib.sha256).hexdigest()
app = FastAPI()
ok = lambda r: bool(PW) and hmac.compare_digest(r.cookies.get("t", ""), TOK)
def flt(x):
    try: return float(x)
    except Exception: return None
def guard(r): return None if ok(r) else JSONResponse({"err": "login"}, 401)

@app.post("/api/login")
async def login(r: Request):
    j = await r.json()
    if PW and hmac.compare_digest(str(j.get("pw", "")), PW):
        res = JSONResponse({"ok": True}); res.set_cookie("t", TOK, httponly=True, samesite="strict", secure=r.headers.get("x-forwarded-proto") == "https", max_age=86400 * 30); return res
    time.sleep(1); return JSONResponse({"ok": False, "err": "DASH_PASSWORD is not set on Vercel" if not PW else "Wrong password"}, 401)
@app.get("/legacy", response_class=HTMLResponse)
def legacy(r: Request): return HTML if ok(r) else LOGIN        # the Stage 1-3A page, kept intact; the terminal UI is the static site in public/

@app.get("/api/state")
def state(r: Request):
    if guard(r): return guard(r)
    try: S = C.refresh()
    except Exception as e:
        w, f = C.explain(e); return JSONResponse({"db_error": w, "db_fix": f}, 200)
    mode = S.get("mode", "demo"); mk_out = {}
    for mk in ("futures", "spot"):
        mk_out[mk] = dict(running=S.get("run_" + mk) == "1", state=S.get("state_" + mk, "stopped"), now=S.get("now_" + mk, "Worker has not reported yet."),
            next=flt(S.get("next_" + mk)) or 0, eq=flt(S.get(f"eq_{mk}_{mode}")), dpnl=flt(S.get(f"dpnl_{mk}_{mode}")) or 0)
    hb = flt(S.get("hb")); used = 0
    d = S.get("llm_day", "|0").split("|")
    if d[0] == C.utc_day(): used = int(d[1])
    return dict(mode=mode, real_allowed=C.real_ok(), m=mk_out, cfg=dict(loss=C.CFG["loss"] * 100, target=C.CFG["target"] * 100, cap=C.CFG["llm_cap"]),
        hb_age=(time.time() - hb) if hb else None, model=S.get("model", ""), llm_used=used, llm_err=S.get("llm_err", ""), health=json.loads(S.get("health", "[]")),
        open=C.q("SELECT * FROM trades WHERE status='OPEN' AND mode=? ORDER BY id DESC", (mode,), True),
        closed=C.q("SELECT * FROM trades WHERE status='CLOSED' AND mode=? ORDER BY id DESC LIMIT 40", (mode,), True),
        dec=C.q("SELECT * FROM decisions WHERE mode=? ORDER BY id DESC LIMIT 30", (mode,), True),
        logs=C.q("SELECT * FROM logs ORDER BY id DESC LIMIT 80", fetch=True),
        daily=C.q("SELECT * FROM daily WHERE mode=? ORDER BY day DESC, market LIMIT 20", (mode,), True),
        metrics=C.metrics(mode),
        charts=C.q("SELECT symbol, market, ts FROM chart ORDER BY market, symbol", fetch=True))

@app.get("/api/chart")
def chart(r: Request, symbol: str, market: str):
    if guard(r): return guard(r)
    if not symbol.isalnum() or market not in ("futures", "spot"): return JSONResponse({"err": "bad request"}, 400)
    row = C.q("SELECT ts, data FROM chart WHERE symbol=? AND market=?", (symbol.upper(), market), True)
    mode = C.refresh().get("mode", "demo")
    return dict(k=json.loads(row[0]["data"]) if row else [], ts=row[0]["ts"] if row else 0,
        trades=C.q("SELECT * FROM trades WHERE symbol=? AND market=? AND mode=? ORDER BY id DESC LIMIT 20", (symbol.upper(), market, mode), True))

@app.post("/api/mode")
async def mode(r: Request):
    if guard(r): return guard(r)
    j = await r.json(); C.refresh()
    if j.get("mode") == "real":
        if not C.real_ok(): return JSONResponse({"err": "Real mode is locked. Set ALLOW_REAL=1 on Vercel and on the Voroa worker."}, 400)
        if j.get("confirm") != "REAL": return JSONResponse({"err": "Type REAL to confirm."}, 400)
        C.put("mode", "real", True); C.log("Switched to REAL mode (live money)", True, level="warn")
    else: C.put("mode", "demo", True); C.log("Switched to DEMO mode")
    return {"ok": True}
@app.post("/api/market")
async def market(r: Request):
    if guard(r): return guard(r)
    j = await r.json(); mk = j.get("market"); C.refresh()
    if mk not in ("futures", "spot"): return JSONResponse({"err": "bad market"}, 400)
    if j.get("on") and C.get("mode") == "real" and not C.real_ok(): return JSONResponse({"err": "Real mode is locked."}, 400)
    C.put("run_" + mk, "1" if j.get("on") else "0", True); C.put("next_" + mk, "0", True)
    C.log(f"{mk.capitalize()} {'started' if j.get('on') else 'stopped'} ({C.get('mode', 'demo')} mode)", mk=mk, level="ok"); return {"ok": True}
@app.post("/api/closeall")
async def closeall(r: Request):
    if guard(r): return guard(r)
    mk = (await r.json()).get("market")
    if mk in ("futures", "spot"): C.put("closeall_" + mk, "1", True); C.log("Close-all requested", mk=mk, level="warn")
    return {"ok": True}
@app.post("/api/reset")
async def reset(r: Request):
    if guard(r): return guard(r)
    j = await r.json(); mk = j.get("market")
    if mk not in ("futures", "spot"): return JSONResponse({"err": "bad market"}, 400)
    for t in ("trades", "decisions", "daily"): C.q(f"DELETE FROM {t} WHERE mode='demo' AND market=?", (mk,))
    C.put(f"demo_start_{mk}", float(j.get("amount") or C.CFG["start"]), True); C.put(f"dstart_{mk}_demo", "|0", True); return {"ok": True}

# ---------- Stage 3A read endpoints for the dashboard (all data comes from the database, nothing is synthesized) ----------
def _safe(fn, *a, **k):
    try: return fn(*a, **k)
    except Exception as e:
        w, f = C.explain(e); return JSONResponse({"db_error": w, "db_fix": f}, 503)
@app.get("/api/v2/status")
def v_status(r: Request, mode: str = None):
    return guard(r) or _safe(views.status, mode)
@app.get("/api/v2/scanner")
def v_scanner(r: Request, market: str, mode: str = None):
    return guard(r) or (JSONResponse({"err": "bad market"}, 400) if market not in ("futures", "spot") else _safe(views.scanner, market, mode))
@app.get("/api/v2/setups")
def v_setups(r: Request, market: str = None, mode: str = None, active: int = 1, limit: int = 100):
    return guard(r) or _safe(views.setups, market, mode, bool(active), min(limit, 500))
@app.get("/api/v2/setup")
def v_setup(r: Request, id: int):
    if guard(r): return guard(r)
    d = _safe(views.setup_detail, id); return d if d is not None else JSONResponse({"err": "not found"}, 404)
@app.get("/api/v2/events")
def v_events(r: Request, since_id: int = 0, limit: int = 100, market: str = None, kinds: str = None):
    return guard(r) or _safe(views.events, since_id, min(limit, 500), market, kinds.split(",") if kinds else None)
@app.get("/api/v2/positions")
def v_positions(r: Request, mode: str = None):
    return guard(r) or _safe(views.positions, mode)
@app.get("/api/v2/infra")
def v_infra(r: Request): return guard(r) or _safe(views.infra)

@app.get("/api/v2/dashboard")
def v_dashboard(r: Request, mode: str = None): return guard(r) or _safe(views.dashboard, mode)
@app.get("/api/v2/trades")
def v_trades(r: Request, market: str, mode: str = None, symbol: str = None, limit: int = 30):
    return guard(r) or (JSONResponse({"err": "bad market"}, 400) if market not in ("futures", "spot") else _safe(views.trades, market, mode, symbol, min(limit, 100)))
@app.get("/api/v2/timeline")
def v_timeline(r: Request, market: str = None, mode: str = None, since_id: int = 0, limit: int = 60, all: int = 0):
    return guard(r) or _safe(views.timeline, market, mode, since_id, min(limit, 200), bool(all))

# Local development: serve the static terminal (on Vercel, public/ is served by the CDN and only /api/* and /legacy reach this function)
PUBLIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "public")
if os.path.isdir(PUBLIC):
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="terminal")

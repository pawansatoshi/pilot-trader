import os, time, json, re, sqlite3, requests
E = os.getenv
DB_URL = E("DATABASE_URL"); PG = bool(DB_URL)
F, SP = "https://fapi.binance.com", "https://api.binance.com"
CFG = dict(start=float(E("START_BALANCE", 1000)), risk=float(E("RISK_PCT", 1)) / 100, lev=float(E("MAX_LEVERAGE", 3)),
           maxpos=int(E("MAX_POSITIONS", 3)), loss=float(E("DAILY_LOSS_LIMIT_PCT", 3)) / 100, target=float(E("DAILY_TARGET_PCT", 8)) / 100,
           scan=int(E("SCAN_SECONDS", 300)), thr=float(E("MIN_SCORE", 1.5)), llm_cap=int(E("OPENROUTER_DAILY_LIMIT", 50)) - 5,
           llm_gap=int(E("LLM_MIN_GAP_MIN", 15)) * 60, tz=int(E("TZ_OFFSET_MIN", 330)),
           fee_f=0.0005, fee_s=0.001, slip=0.0002, min_eff_rr=float(E("MIN_EFF_RR", 1.5)), min_stop_pct=float(E("MIN_STOP_PCT", 0.3)),
           drift_atr=float(E("MAX_DRIFT_ATR", 0.3)), max_age=int(E("MAX_EVIDENCE_AGE_SEC", 240)), no_ai=E("NO_AI_POLICY", "skip"), stop_gap=float(E("SPOT_STOP_LIMIT_GAP_PCT", 0.2)) / 100, tier_confirm=int(E("TIER_CONFIRM_SCANS", 2)))
def real_ok(): return E("ALLOW_REAL") == "1"
def today(): return time.strftime("%Y-%m-%d", time.gmtime(time.time() + CFG["tz"] * 60))
def utc_day(): return time.strftime("%Y-%m-%d", time.gmtime())
def day_start_ts(): return (time.time() + CFG["tz"] * 60) // 86400 * 86400 - CFG["tz"] * 60

# ---------- database (Postgres/Neon, SQLite fallback for local) ----------
_c = None
def q(sql, args=(), fetch=False):
    global _c
    for attempt in (0, 1):
        try:
            if _c is None:
                if PG: import psycopg2; _c = psycopg2.connect(DB_URL, connect_timeout=10)
                else: _c = sqlite3.connect(E("SQLITE_PATH", "pilot.db"), timeout=30, check_same_thread=False); _c.row_factory = sqlite3.Row
            if PG: import psycopg2.extras; cur = _c.cursor(cursor_factory=psycopg2.extras.RealDictCursor); sql2 = sql.replace("?", "%s")
            else: cur = _c.cursor(); sql2 = sql
            cur.execute(sql2, args); rows = [dict(r) for r in cur.fetchall()] if fetch else None
            _c.commit(); return rows
        except Exception:
            try: _c.close()
            except Exception: pass
            _c = None
            if attempt: raise
def init():
    pk = "SERIAL PRIMARY KEY" if PG else "INTEGER PRIMARY KEY AUTOINCREMENT"; D = "DOUBLE PRECISION"
    for s in ["CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT)",
        f"CREATE TABLE IF NOT EXISTS trades(id {pk}, market TEXT, mode TEXT, symbol TEXT, side TEXT, qty {D}, entry {D}, sl {D}, tp {D}, risk {D}, opened {D}, status TEXT, exit {D}, closed {D}, pnl {D}, reason TEXT, extra TEXT, cid TEXT, review_id INTEGER, latency_ms {D}, mfe_r {D}, mae_r {D}, r_mult {D})",
        f"CREATE TABLE IF NOT EXISTS decisions(id {pk}, ts {D}, market TEXT, mode TEXT, symbol TEXT, score {D}, action TEXT, source TEXT, model TEXT, why TEXT)",
        f"CREATE TABLE IF NOT EXISTS logs(id {pk}, ts {D}, market TEXT, level TEXT, msg TEXT, kind TEXT, symbol TEXT, setup_id INTEGER, review_id INTEGER, trade_id INTEGER, data TEXT)",
        f"CREATE TABLE IF NOT EXISTS setups(id {pk}, market TEXT, mode TEXT, symbol TEXT, direction TEXT, state TEXT, state_since {D}, score {D}, reason TEXT, review_id INTEGER, trade_id INTEGER, cooldown_until {D}, outcome_r {D}, created {D}, updated {D}, last_seen {D}, closed_at {D}, active INTEGER, data TEXT)",
        f"CREATE TABLE IF NOT EXISTS setup_events(id {pk}, setup_id INTEGER, ts {D}, from_state TEXT, to_state TEXT, reason TEXT, data TEXT)",
        f"CREATE TABLE IF NOT EXISTS snapshot(market TEXT, mode TEXT, ts {D}, data TEXT, PRIMARY KEY(market, mode))",
        f"CREATE TABLE IF NOT EXISTS daily(id {pk}, day TEXT, market TEXT, mode TEXT, s_eq {D}, e_eq {D}, UNIQUE(day, market, mode))",
        f"CREATE TABLE IF NOT EXISTS chart(symbol TEXT, market TEXT, ts {D}, data TEXT, PRIMARY KEY(symbol, market))",
        f"""CREATE TABLE IF NOT EXISTS reviews(id {pk}, rkey TEXT UNIQUE, ts {D}, market TEXT, mode TEXT, symbol TEXT, direction TEXT, model TEXT, prompt_ver TEXT,
            ai_response TEXT, ai_verdict TEXT, confidence {D}, rule_score {D}, ai_ms {D}, evid_price {D}, gate_ok INTEGER, gate_reasons TEXT, decision TEXT, trade_id INTEGER,
            entry {D}, sl {D}, tp {D}, risk {D}, exec_ms {D}, out_kind TEXT, out_status TEXT, exit_px {D}, pnl {D}, r_mult {D}, mfe_r {D}, mae_r {D}, exit_reason TEXT, closed_ts {D}, state TEXT)"""]: q(s)
    for col, typ in [("cid", "TEXT"), ("review_id", "INTEGER"), ("latency_ms", D), ("mfe_r", D), ("mae_r", D), ("r_mult", D)]:   # migrate older databases
        try: q(f"ALTER TABLE trades ADD COLUMN {col} {typ}")
        except Exception: pass
    q("CREATE UNIQUE INDEX IF NOT EXISTS uq_active ON trades(market, mode, symbol) WHERE status IN ('PENDING','OPEN')")   # DB-level duplicate-position lock
    q("CREATE UNIQUE INDEX IF NOT EXISTS uq_cid ON trades(cid) WHERE cid IS NOT NULL")
    for col, typ in [("kind", "TEXT"), ("symbol", "TEXT"), ("setup_id", "INTEGER"), ("review_id", "INTEGER"), ("trade_id", "INTEGER"), ("data", "TEXT")]:   # migrate older logs tables
        try: q(f"ALTER TABLE logs ADD COLUMN {col} {typ}")
        except Exception: pass
    q("CREATE UNIQUE INDEX IF NOT EXISTS uq_setup_active ON setups(market, mode, symbol) WHERE active=1")   # one live setup per symbol

def close():
    global _c
    if _c is not None:
        try: _c.close()
        except Exception: pass
        _c = None

_cache = {}
def refresh():
    _cache.clear(); _cache.update({r["k"]: r["v"] for r in q("SELECT k,v FROM settings", fetch=True)}); return _cache
def get(k, d=None): return _cache.get(k, d)
def put(k, v, force=False):
    v = str(v)
    if _cache.get(k) == v and not force: return
    q("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v)); _cache[k] = v

def tg(m):
    t, c = E("TELEGRAM_TOKEN"), E("TELEGRAM_CHAT_ID")
    if t and c:
        try: requests.post(f"https://api.telegram.org/bot{t}/sendMessage", json={"chat_id": c, "text": m}, timeout=10)
        except Exception: pass
def log(m, alert=False, mk="", level="info", kind="msg", symbol=None, setup_id=None, review_id=None, trade_id=None, data=None):
    """Every log row is a typed event for the timeline (kind), optionally linked to a setup / review / trade."""
    print(m, flush=True)
    try: q("INSERT INTO logs(ts,market,level,msg,kind,symbol,setup_id,review_id,trade_id,data) VALUES(?,?,?,?,?,?,?,?,?,?)",
           (time.time(), mk, level, m[:400], kind, symbol, setup_id, review_id, trade_id, json.dumps(data, default=str)[:3000] if data is not None else None))
    except Exception as e: print("log failed:", e)
    if alert: tg(("RISK/HEALTH ALERT" if level in ("warn", "err") else "STATUS") + (f" [{mk}]" if mk else "") + "\n" + m)

def prune():   # retention: events 14 days / 5000 rows, setup events and ended setups 30 days
    q("DELETE FROM logs WHERE id < (SELECT COALESCE(MAX(id), 0) FROM logs) - 5000"); q("DELETE FROM logs WHERE ts < ?", (time.time() - 14 * 86400,))
    q("DELETE FROM decisions WHERE id < (SELECT COALESCE(MAX(id), 0) FROM decisions) - 800"); cut = time.time() - 30 * 86400
    q("DELETE FROM setup_events WHERE ts < ?", (cut,)); q("DELETE FROM setups WHERE active=0 AND COALESCE(closed_at, updated) < ?", (cut,))

RULES = [("451 client", "Binance blocks this server's location (HTTP 451)", "Run the worker in a Binance-supported region (Singapore/Europe)."),
 ("restricted location", "Binance blocks this server's location", "Run the worker in a Binance-supported region (Singapore/Europe)."),
 ("-4120", "Binance rejected a conditional order sent to the old endpoint (-4120 STOP_ORDER_SWITCH_ALGO)", "Conditional orders must use the Algo Order API: update ccxt (pip install -U ccxt) and redeploy the worker."),
 ("algo order endpoints missing", "This ccxt version has no Algo Order endpoints", "pip install -U ccxt, then redeploy the worker."),
 ("hedge mode", "Futures account is in Hedge Mode", "Binance Futures > Preferences > Position Mode > One-way Mode."),
 ("-2015", "Binance rejected the API key (-2015)", "Check key + secret, enable Spot/Futures trading permission, fix or remove the IP whitelist."),
 ("-2014", "Binance API key format is invalid (-2014)", "Re-copy BINANCE_API_KEY / BINANCE_API_SECRET without spaces."),
 ("-1021", "Server clock differs from Binance (-1021)", "Restart the worker; time sync is automatic."),
 ("-2019", "Not enough margin (-2019)", "Add USDT to the Futures wallet."),
 ("insufficient", "Not enough balance for this order", "Add USDT or lower MAX_POSITIONS."),
 ("notional", "Order is below Binance's minimum size", "Increase the balance or lower MAX_POSITIONS."),
 ("401 client", "API key rejected (HTTP 401)", "Re-check the key in the environment variables."),
 ("403 client", "Access denied (HTTP 403)", "Re-check the key and its permissions."),
 ("429 client", "Rate limit reached (HTTP 429)", "Nothing to do: the bot retries on the next cycle."),
 ("password authentication", "Database login failed", "Copy DATABASE_URL again from Neon (pooled connection string)."),
 ("could not translate host", "Database host not found", "DATABASE_URL is wrong. Copy the connection string from Neon."),
 ("sslmode", "Database needs SSL", "Add ?sslmode=require to DATABASE_URL."),
 ("name or service not known", "Network/DNS error", "Service may be down. Retry in a minute."),
 ("timed out", "Request timed out", "Service is slow or down. The bot retries automatically."),
 ("max retries", "Cannot reach the service", "Service may be down or blocked from this server.")]
def explain(e):
    s = str(e).lower()
    for k, w, f in RULES:
        if k in s: return w, f
    return str(e)[:160], "Open the worker logs on Voroa for the full error."

# ---------- Binance public data (futures + spot) ----------
def jget(u, p=None):
    r = requests.get(u, params=p, timeout=15); r.raise_for_status(); return r.json()
STABLE = {"USDC", "FDUSD", "TUSD", "BUSD", "USDP", "EUR", "DAI", "AEUR", "XUSD", "USD1", "USDE", "PAXG"}
def top_symbols(mk, n=50):
    t = jget((F + "/fapi/v1/ticker/24hr") if mk == "futures" else (SP + "/api/v3/ticker/24hr"))
    t = [x for x in t if x["symbol"].endswith("USDT") and "_" not in x["symbol"] and x["symbol"][:-4] not in STABLE]
    return sorted(t, key=lambda x: -float(x["quoteVolume"]))[:n]
def klines(sym, iv="15m", n=100, mk="futures"):
    u = (F + "/fapi/v1/klines") if mk == "futures" else (SP + "/api/v3/klines")
    return [[float(c[i]) for i in range(6)] for c in jget(u, {"symbol": sym, "interval": iv, "limit": n})]
def last_price(sym, mk="futures"):
    return float(jget((F + "/fapi/v1/ticker/price") if mk == "futures" else (SP + "/api/v3/ticker/price"), {"symbol": sym})["price"])

def ema(v, n):
    k = 2 / (n + 1); e = v[0]; out = [e]
    for x in v[1:]: e = x * k + e * (1 - k); out.append(e)
    return out
def rsi(c, n=14):
    g = l = 0
    for i in range(1, n + 1): d = c[i] - c[i - 1]; g += max(d, 0); l += max(-d, 0)
    g /= n; l /= n
    for i in range(n + 1, len(c)): d = c[i] - c[i - 1]; g = (g * (n - 1) + max(d, 0)) / n; l = (l * (n - 1) + max(-d, 0)) / n
    return 100 if l == 0 else 100 - 100 / (1 + g / l)
def atr(k, n=14):
    tr = [max(k[i][2] - k[i][3], abs(k[i][2] - k[i - 1][4]), abs(k[i][3] - k[i - 1][4])) for i in range(1, len(k))]
    return sum(tr[-n:]) / n
def tech(sym, mk):
    k = klines(sym, "15m", 100, mk); c = [x[4] for x in k]
    e9, e21, e50 = ema(c, 9)[-1], ema(c, 21)[-1], ema(c, 50)[-1]; r = rsi(c)
    vol = k[-1][5] / (sum(x[5] for x in k[-21:-1]) / 20 + 1e-9)
    s = 1 if e9 > e21 > e50 else -1 if e9 < e21 < e50 else 0
    if s > 0 and 50 < r < 70: s += .5
    if s < 0 and 30 < r < 50: s -= .5
    if vol > 1.5: s *= 1.3
    return dict(sym=sym, price=c[-1], atr=atr(k), rsi=round(r, 1), vol=round(vol, 2), score=s)
def extras(sym, mk):
    d = dict(depth_imb=0)
    if mk == "futures":
        d.update(oi_chg_pct=0, long_short=1)
        try:
            h = jget(F + "/futures/data/openInterestHist", {"symbol": sym, "period": "15m", "limit": 8})
            d["oi_chg_pct"] = round((float(h[-1]["sumOpenInterestValue"]) / float(h[0]["sumOpenInterestValue"]) - 1) * 100, 2)
        except Exception: pass
        try: d["long_short"] = float(jget(F + "/futures/data/globalLongShortAccountRatio", {"symbol": sym, "period": "15m", "limit": 1})[-1]["longShortRatio"])
        except Exception: pass
    try:
        b = jget((F + "/fapi/v1/depth") if mk == "futures" else (SP + "/api/v3/depth"), {"symbol": sym, "limit": 20})
        bv = sum(float(x[1]) for x in b["bids"]); av = sum(float(x[1]) for x in b["asks"]); d["depth_imb"] = round((bv - av) / (bv + av), 2)
    except Exception: pass
    return d

# ---------- exchange filters (tick / step / min notional / tradable) ----------
_inf = {}
def symfilters(sym, mk):
    if mk not in _inf or time.time() - _inf[mk][0] > 21600:
        d = jget((F + "/fapi/v1/exchangeInfo") if mk == "futures" else (SP + "/api/v3/exchangeInfo")); m = {}
        for x in d["symbols"]:
            f = {y["filterType"]: y for y in x["filters"]}; pf = f.get("PRICE_FILTER", {}); lf = f.get("MARKET_LOT_SIZE") or f.get("LOT_SIZE") or {}
            if not float(lf.get("stepSize", 0) or 0): lf = f.get("LOT_SIZE", {})
            mn = f.get("MIN_NOTIONAL") or f.get("NOTIONAL") or {}
            tradable = x.get("status") == "TRADING" and (x.get("contractType") == "PERPETUAL" if mk == "futures" else x.get("isSpotTradingAllowed", True))
            m[x["symbol"]] = dict(ok=bool(tradable), tick=float(pf.get("tickSize", 0) or 0), step=float(lf.get("stepSize", 0) or 0),
                                  minq=float(lf.get("minQty", 0) or 0), minnotional=float(mn.get("notional") or mn.get("minNotional") or 5))
        _inf[mk] = (time.time(), m)
    return _inf[mk][1].get(sym)

# ---------- measurement: AI value ----------
def _grp(rows):
    r = [x["r_mult"] for x in rows if x["r_mult"] is not None]; n = len(r)
    return dict(n=n, wins=sum(1 for v in r if v > 0), win_pct=round(100 * sum(1 for v in r if v > 0) / n, 1) if n else None,
                avg_r=round(sum(r) / n, 3) if n else None, sum_r=round(sum(r), 3))
def metrics(mode=None, market=None):
    w, a = ["out_status='DONE'"], []
    if mode: w.append("mode=?"); a.append(mode)
    if market: w.append("market=?"); a.append(market)
    rows = q("SELECT decision, r_mult FROM reviews WHERE " + " AND ".join(w), tuple(a), True)
    pick = lambda *ds: [x for x in rows if x["decision"] in ds]
    appr, rej, unav = pick("OPENED"), pick("AI_REJECT"), pick("AI_INVALID", "AI_UNAVAILABLE_SKIP")
    track = q("SELECT COUNT(*) c FROM reviews WHERE out_status='TRACKING'" + ("" if not mode else " AND mode=?"), (mode,) if mode else (), True)[0]["c"]
    rj = _grp(rej)
    return dict(ai_approved=_grp(appr), ai_rejected_shadow=rj, ai_unavailable_shadow=_grp(unav), rules_only=_grp(appr + rej + unav),
        ai_veto_value_r=round(-rj["sum_r"], 3), false_rejects=sum(1 for x in rej if (x["r_mult"] or 0) > 0), correct_rejects=sum(1 for x in rej if (x["r_mult"] or 0) <= 0),
        losing_approvals=sum(1 for x in appr if (x["r_mult"] or 0) <= 0), still_tracking=track)

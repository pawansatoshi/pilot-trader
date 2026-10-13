"""Pure risk / simulation logic. No network, no DB: everything is passed in, so it is fully testable."""
import math

def _clean(x): return float(f"{x:.12g}")
def floor_to(x, step): return _clean(math.floor(x / step + 1e-9) * step) if step else x
def round_to(x, tick): return _clean(round(x / tick) * tick) if tick else x

def costs(mk, cfg):
    return cfg["fee_f"] if mk == "futures" else cfg["fee_s"], cfg["slip"]

def build_plan(side, live, atr, eq, mk, cfg, flt):
    """Deterministic plan from LIVE price. Direction comes only from the engine; AI never touches this."""
    d = 1 if side == "LONG" else -1; fee, slip = costs(mk, cfg)
    entry = live * (1 + d * slip); R0 = 1.5 * atr
    tick = (flt or {}).get("tick") or 0; step = (flt or {}).get("step") or 0
    sl = round_to(entry - d * R0, tick); tp = round_to(entry + d * 2 * R0, tick)
    R = abs(entry - sl); lev = cfg["lev"] if mk == "futures" else .95
    qty = min(eq * cfg["risk"] / R, eq * lev / cfg["maxpos"] / entry) if R > 0 else 0
    qty = floor_to(qty, step)
    reward = (tp - entry) * d - fee * (entry + tp) - slip * tp         # net of fees + exit slippage
    risk_net = (entry - sl) * d + fee * (entry + sl) + slip * sl       # stop loss incl. fees + slippage
    return dict(side=side, entry=entry, sl=sl, tp=tp, R=R, qty=qty, notional=qty * entry, lev=lev,
                eff_rr=(reward / risk_net) if risk_net > 0 else 0, risk_amt=qty * risk_net, stop_pct=R / entry * 100)

def open_risk(active, mk, cfg):
    """Worst-case loss still at stake on open positions if every stop is hit: price distance to the stop (0 once the stop is at/after
    breakeven) PLUS exit fee and exit slippage, the same cost model as a new trade. Orphan exchange positions (stop unknown) count costs only."""
    fee, slip = costs(mk, cfg); tot = 0
    for t in active:
        d = 1 if t["side"] == "LONG" else -1
        tot += t["qty"] * (max(0, (t["entry"] - t["sl"]) * d) + fee * (t["entry"] + t["sl"]) + slip * t["sl"])
    return tot

def risk_gate(i):
    """Single centralized pre-trade gate. Returns dict(ok, reasons, plan). Every failed rule is listed."""
    c, why = i["cfg"], []
    flt = i.get("flt")
    for b in i.get("blocks") or []: why.append("SYSTEM_BLOCKED: " + b)      # health gate (new trades only); default: no blocks
    if not flt or not flt.get("ok"): why.append("SYMBOL: exchange info missing or symbol not tradable")
    if i["mode"] == "real" and not i.get("real_allowed"): why.append("MODE_LOCKED: real trading not enabled")
    dp = i["eq"] / i["dstart"] - 1 if i["dstart"] > 0 else 0
    if dp >= c["target"]: why.append(f"GOAL: daily target reached ({dp * 100:+.2f}%)")
    if dp <= -c["loss"]: why.append(f"LOSS_LIMIT: daily loss limit reached ({dp * 100:+.2f}%)")
    act = i["active"]
    if len(act) >= c["maxpos"]: why.append(f"MAX_POS: {len(act)} positions already active")
    if any(t["symbol"] == i["symbol"] for t in act): why.append("DUPLICATE: position already active on this symbol")
    if i["evid_age"] > c["max_age"]: why.append(f"STALE: evidence is {i['evid_age']:.0f}s old")
    drift = abs(i["live"] - i["evid_price"]) / i["atr"] if i["atr"] > 0 else 99
    if drift > c["drift_atr"]: why.append(f"DRIFT: price moved {drift:.2f} ATR since the signal")
    p = build_plan(i["side"], i["live"], i["atr"], i["eq"], i["mk"], c, flt)
    d = 1 if i["side"] == "LONG" else -1
    if not ((p["entry"] - p["sl"]) * d > 0 and (p["tp"] - p["entry"]) * d > 0): why.append("LEVELS: stop/target on the wrong side after rounding")
    if p["stop_pct"] < c["min_stop_pct"]: why.append(f"MIN_STOP: stop distance {p['stop_pct']:.2f}% < {c['min_stop_pct']}%")
    if p["eff_rr"] < c["min_eff_rr"]: why.append(f"RR: effective R:R {p['eff_rr']:.2f} after fees/slippage < {c['min_eff_rr']}")
    if flt and flt.get("ok"):
        if p["qty"] <= 0 or p["qty"] < (flt.get("minq") or 0): why.append("PRECISION: quantity below exchange minimum lot")
        elif p["notional"] < (flt.get("minnotional") or 0): why.append(f"MIN_NOTIONAL: order {p['notional']:.2f} < exchange minimum {flt['minnotional']}")
    worst = -i["realized_today"] + open_risk(act, i["mk"], c) + p["risk_amt"]          # worst case if every stop is hit
    if worst > c["loss"] * i["dstart"]: why.append(f"OPEN_RISK: worst case {worst:.2f} > daily loss budget {c['loss'] * i['dstart']:.2f}")
    need = p["notional"] / (c["lev"] if i["mk"] == "futures" else 1)
    if need * 1.02 > i["free"]: why.append(f"BALANCE: needs {need:.2f}, free {i['free']:.2f}")
    return dict(ok=not why, reasons=why, plan=p)

def judge_pick(p, rd):
    """Strict validation of one AI pick. Returns (verdict, confidence, reason). verdict: APPROVE | REJECT | INVALID.
    Any extra keys (entry/stop/size/leverage...) are ignored on purpose: AI cannot change the trade."""
    if not isinstance(p, dict): return "INVALID", 0, "pick missing or not an object"
    act = p.get("action")
    if act not in ("LONG", "SHORT", "SKIP"): return "INVALID", 0, f"bad action {act!r}"
    try: conf = float(p.get("confidence"))
    except (TypeError, ValueError): return "INVALID", 0, f"bad confidence {p.get('confidence')!r}"
    if isinstance(p.get("confidence"), bool) or math.isnan(conf) or conf < 0 or conf > 100: return "INVALID", 0, "confidence out of range"
    conf = conf / 100 if conf > 1 else conf
    reason = str(p.get("reason", ""))[:200]
    return ("APPROVE" if act == rd and conf >= .6 else "REJECT"), conf, reason

def sim_step(p, candles, n_min=60000):
    """Walk 1m candles. p: side, entry, sl, tp, risk, chk(ms), mfe, mae (price units). Same logic for demo, real(software) and shadow.
    SL is assumed hit before TP inside one candle (conservative). Returns p with p['done'], p['exit'], p['reason']."""
    d = 1 if p["side"] == "LONG" else -1; en, R = p["entry"], p["risk"]; p["done"] = False
    for c in candles:
        if c[0] < p["chk"]: continue
        hi, lo = (c[2], c[3]) if d == 1 else (c[3], c[2])
        if (lo - p["sl"]) * d <= 0:
            p["mae"] = max(p["mae"], (en - p["sl"]) * d); return dict(p, done=True, exit=p["sl"], reason="SL")
        p["mae"] = max(p["mae"], (en - lo) * d)
        if (p["tp"] - hi) * d <= 0:
            p["mfe"] = max(p["mfe"], (p["tp"] - en) * d); return dict(p, done=True, exit=p["tp"], reason="TP")
        fav = (hi - en) * d; p["mfe"] = max(p["mfe"], fav)
        new = hi - d * .75 * R if fav >= 1.5 * R else en if fav >= R else p["sl"]
        if (new - p["sl"]) * d > 0: p["sl"] = new; p["chk"] = c[0] + n_min
        else: p["chk"] = c[0]
    return p

def r_net(side, entry, exit_px, R, fee):
    d = 1 if side == "LONG" else -1
    return (((exit_px - entry) * d) - fee * (entry + exit_px)) / R if R > 0 else 0

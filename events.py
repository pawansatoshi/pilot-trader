"""Typed events (timeline rows in `logs`) and the four Telegram message families: SETUP DETECTED / TRADE OPENED / TRADE CLOSED / RISK-HEALTH ALERT."""
import common as C
def emit(kind, text, mk="", level="info", **refs): C.log(text, False, mk, level, kind=kind, **refs)
def send(text): C.tg(text)
_f = lambda x: "n/a" if x is None else f"{x:.6g}"
def fmt_setup(mk, mode, sym, direction, reason, plan=None):
    L = ["SETUP DETECTED", f"{sym} {direction}  [{mk.upper()} / {mode.upper()}]", reason]
    if plan: L += [f"entry (indicative): {_f(plan['entry'])}", f"sl: {_f(plan['sl'])}", f"tp: {_f(plan['tp'])}", f"rr after costs: {plan['eff_rr']:.2f}"]
    return "\n".join(L + ["waiting for ai/risk confirmation. This is not a trade yet."])
def fmt_opened(mk, mode, sym, side, entry, qty, sl, tp, rr, risk, simulated):
    return "\n".join(["TRADE OPENED", f"{sym} {side}  [{mk.upper()} / {mode.upper()}]", f"entry: {_f(entry)}", f"size: {_f(qty)}", f"sl: {_f(sl)}", f"tp: {_f(tp)}", f"risk: {_f(risk)} USDT   rr after costs: {_f(rr)}",
                     "DEMO: simulated fill, stop/target monitored by the bot" if simulated else "exchange fill confirmed, stop protection confirmed"])
def fmt_closed(mk, mode, sym, side, exit_px, pnl, r, reason):
    return "\n".join(["TRADE CLOSED", f"{sym} {side}  [{mk.upper()} / {mode.upper()}]", f"exit: {_f(exit_px)}   reason: {reason}", f"pnl: {pnl:+.2f} USDT   R: {r:+.2f}"])
def fmt_health(state, mk, reasons):
    L = [f"RISK/HEALTH ALERT [{mk}]", f"system state: {state.upper()}"]
    for r in reasons: L.append(f"- {r['msg']}" + (f" | fix: {r['fix']}" if r.get("fix") else ""))
    return "\n".join(L + (["new trades blocked; open positions keep being managed"] if state == "blocked" else []))

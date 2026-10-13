"""Pure scanner helpers: display tiers over the EXISTING score (not a new strategy) and human-readable reasons."""
def tier(score, mk, thr):
    if mk == "spot" and score < 0: return "WAITING"            # spot can only go long
    s = abs(score)
    if s >= thr: return "CANDIDATE"
    if s >= thr - 0.2: return "NEAR_SETUP"
    if s >= min(1.0, thr - 0.2): return "WATCHING"
    return "WAITING"
def direction(score): return "LONG" if score > 0 else "SHORT" if score < 0 else None
def trend_label(score): return "up" if score >= 1 else "down" if score <= -1 else "flat"
def describe(c, mk, thr):
    s = c["score"]; p = [("EMAs aligned " + ("up" if s > 0 else "down")) if abs(s) >= 1 else "no EMA alignment", f"score {s:+.2f} (needs ±{thr:g})", f"RSI {c['rsi']}", f"volume x{c['vol']}"]
    if "oi_chg_pct" in c: p.append(f"OI {c['oi_chg_pct']:+.2f}%")
    if "funding_pct" in c: p.append(f"funding {c['funding_pct']:+.4f}%")
    if mk == "spot" and s < 0: p.append("short signal ignored on spot")
    return "; ".join(p)

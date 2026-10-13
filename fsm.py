"""Persistent setup state machine. Pure persistence + rules: no exchange, no ccxt, no bot imports.
WAITING is virtual (no row). One live row per (market, mode, symbol). Every transition is validated against ALLOWED and appended to setup_events."""
import time, json
import common as C, events
STATES = ("WAITING", "WATCHING", "NEAR_SETUP", "CANDIDATE", "AI_REVIEW", "RISK_CHECK", "EXECUTION", "POSITION", "CLOSED", "OUTCOME", "REJECTED", "COOLDOWN")
ALLOWED = {
    "WAITING": {"WATCHING", "NEAR_SETUP", "CANDIDATE"},
    "WATCHING": {"WAITING", "NEAR_SETUP", "CANDIDATE"},
    "NEAR_SETUP": {"WAITING", "WATCHING", "CANDIDATE"},
    "CANDIDATE": {"WAITING", "WATCHING", "NEAR_SETUP", "AI_REVIEW", "RISK_CHECK", "REJECTED"},   # RISK_CHECK directly = NO_AI_POLICY=rules
    "AI_REVIEW": {"RISK_CHECK", "REJECTED", "CANDIDATE"},                                        # CANDIDATE = interrupted, re-qualify
    "RISK_CHECK": {"EXECUTION", "REJECTED", "CANDIDATE"},
    "EXECUTION": {"POSITION", "REJECTED"},
    "POSITION": {"CLOSED"}, "CLOSED": {"OUTCOME"}, "OUTCOME": {"COOLDOWN"}, "REJECTED": {"COOLDOWN"},
    "COOLDOWN": {"WAITING", "WATCHING"}}
BUSY = {"AI_REVIEW", "RISK_CHECK", "EXECUTION", "POSITION", "COOLDOWN"}      # states the scanner must not overwrite
class IllegalTransition(Exception): pass

def get(sid): r = C.q("SELECT * FROM setups WHERE id=?", (sid,), True); return r[0] if r else None
def active(mk, mode, sym): r = C.q("SELECT * FROM setups WHERE market=? AND mode=? AND symbol=? AND active=1", (mk, mode, sym), True); return r[0] if r else None
def _check(frm, to):
    if to not in ALLOWED.get(frm, ()): raise IllegalTransition(f"{frm} -> {to}")
def _event(row, frm, to, reason, data=None):
    C.q("INSERT INTO setup_events(setup_id,ts,from_state,to_state,reason,data) VALUES(?,?,?,?,?,?)", (row["id"], time.time(), frm, to, (reason or "")[:300], json.dumps(data, default=str) if data is not None else None))
    events.emit("setup_state_changed", f"{row['symbol']} {row['direction']}: {frm} > {to}" + (f" ({reason})" if reason else ""), row["market"], symbol=row["symbol"], setup_id=row["id"], data=dict(frm=frm, to=to))

def create(mk, mode, sym, direction, state, score=None, reason="", implicit=False):
    """New live setup. implicit=True is only for a direct execution that has no scanner history (still logged as such)."""
    if not implicit: _check("WAITING", state)
    now = time.time()
    C.q("INSERT INTO setups(market,mode,symbol,direction,state,state_since,score,reason,created,updated,last_seen,active,data) VALUES(?,?,?,?,?,?,?,?,?,?,?,1,'{}')", (mk, mode, sym, direction, state, now, score, reason[:300], now, now, now))
    row = active(mk, mode, sym); _event(row, "WAITING", state, reason + (" [direct execution, no scanner history]" if implicit else "")); return row
def move(row, to, reason="", data=None, **fields):
    row = get(row["id"]); _check(row["state"], to); now = time.time(); sets = {"state": to, "state_since": now, "updated": now, "reason": (reason or "")[:300], **fields}
    C.q(f"UPDATE setups SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), row["id"])); _event(row, row["state"], to, reason, data); return get(row["id"])
def touch(row, score, reason=None):
    C.q("UPDATE setups SET score=?, last_seen=?, reason=COALESCE(?, reason) WHERE id=?", (score, time.time(), reason and reason[:300], row["id"]))
def end(row, reason):
    """Back to WAITING: the live row is closed (history stays in setup_events)."""
    row = get(row["id"]); _check(row["state"], "WAITING"); now = time.time()
    C.q("UPDATE setups SET active=0, closed_at=?, updated=?, state='WAITING' WHERE id=?", (now, now, row["id"])); _event(row, row["state"], "WAITING", reason); return get(row["id"])
def reject(row, reason, cooldown_s):
    row = move(row, "REJECTED", reason); return move(row, "COOLDOWN", f"cooldown {int(cooldown_s)}s", cooldown_until=time.time() + cooldown_s)
def finish(row, r, pnl, reason, cooldown_s):
    row = move(row, "CLOSED", reason); row = move(row, "OUTCOME", f"{r:+.2f}R, pnl {pnl:+.2f} USDT", outcome_r=r); return move(row, "COOLDOWN", f"cooldown {int(cooldown_s)}s", cooldown_until=time.time() + cooldown_s)

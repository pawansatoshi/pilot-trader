"""Shared report writer: testnet_report.json with 24 items, PASS / FAIL / INCONCLUSIVE / MANUAL / NOT RUN. Run `python tools/report.py` to print it."""
import json, os, time
PATH = os.getenv("REPORT_PATH", "testnet_report.json")
ITEMS = {1: "Futures market entry", 2: "Futures LONG", 3: "Futures SHORT", 4: "Futures STOP_MARKET via Algo Order API", 5: "Futures TAKE_PROFIT_MARKET via Algo Order API",
 6: "Algo order listing", 7: "Algo order cancellation", 8: "Actual SL trigger (--trigger)", 9: "Actual TP behavior (--tp)", 10: "clientOrderId lookup", 11: "Cancel / recovery",
 12: "Timeout-after-fill creates no duplicate", 13: "Worker restart with an open position", 14: "DB / exchange reconciliation", 15: "Duplicate prevention", 16: "Spot market BUY",
 17: "Spot STOP_LOSS_LIMIT", 18: "Spot watchdog escalation (as far as Demo allows)", 19: "Telegram alerts", 20: "OpenRouter behavior", 21: "Vercel dashboard state",
 22: "+8% daily lock", 23: "-3% daily protection", 24: "Next-day compounding"}
RANK = {"FAIL": 3, "INCONCLUSIVE": 2, "MANUAL": 1, "PASS": 0}; _seen = set()
def load():
    if os.path.exists(PATH):
        with open(PATH) as f: return json.load(f)
    return {"environment": {}, "evidence": {}, "items": {str(i): {"name": n, "status": "NOT RUN", "steps": []} for i, n in ITEMS.items()}}
def save(d):
    for it in d["items"].values(): it["status"] = max((s["status"] for s in it["steps"]), key=lambda x: RANK[x]) if it["steps"] else "NOT RUN"
    with open(PATH, "w") as f: json.dump(d, f, indent=1, default=str)
def rec(item, name, status, detail=""):
    d = load()
    if item not in _seen: _seen.add(item); d["items"][str(item)]["steps"] = []
    d["items"][str(item)]["steps"].append(dict(step=name, status=status, detail=str(detail)[:600], at=time.strftime("%Y-%m-%d %H:%M:%S")))
    save(d); print(f"[{status:12}] #{item} {name}: {str(detail)[:200]}", flush=True)
def evidence(key, val):
    d = load(); d["evidence"][key] = json.loads(json.dumps(val, default=str)); save(d)
def environment(**kw):
    d = load(); d["environment"].update(json.loads(json.dumps(kw, default=str))); save(d)
class Inconclusive(Exception): pass
def step(item, name, fn):
    """Run fn. Returns (ok, value). Exceptions -> FAIL, Inconclusive -> INCONCLUSIVE."""
    try: v = fn(); rec(item, name, "PASS", v if v is not None else "ok"); return True, v
    except Inconclusive as e: rec(item, name, "INCONCLUSIVE", e); return False, None
    except Exception as e: rec(item, name, "FAIL", f"{type(e).__name__}: {e}"); return False, None
def table():
    d = load(); print("\n#  STATUS        ITEM")
    for k, it in sorted(d["items"].items(), key=lambda x: int(x[0])): print(f"{k:>2} {it['status']:13} {it['name']}")
    c = {}; [c.__setitem__(it["status"], c.get(it["status"], 0) + 1) for it in d["items"].values()]; print("\n" + ", ".join(f"{v} {k}" for k, v in c.items()))
if __name__ == "__main__": table()

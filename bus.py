"""Tiny in-process event bus + guards. The exchange/order layer (bot.py) publishes domain events and asks guards; it never imports the
setup state machine. So an exchange-specific fix touches only the order layer: the workflow keeps working because the events stay the same."""
_subs, _guards = {}, []
def subscribe(topic, fn): _subs.setdefault(topic, []).append(fn)
def guard(fn): _guards.append(fn)
def reset(): _subs.clear(); _guards.clear()
def publish(topic, **kw):
    for fn in list(_subs.get(topic, [])):
        try: fn(**kw)
        except Exception as e: print(f"subscriber for {topic} failed: {type(e).__name__}: {e}", flush=True)   # never break the order layer
def blocks(**kw):
    """Reasons that forbid NEW trades. A guard that crashes blocks (fail closed)."""
    out = []
    for g in list(_guards):
        try:
            r = g(**kw)
            if r: out.append(str(r))
        except Exception as e: out.append(f"guard failed: {type(e).__name__}: {e}")
    return out

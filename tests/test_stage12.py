import os, sys, time, json, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as C, bot, risk
from risk import risk_gate, build_plan, judge_pick, sim_step
SYM = "TESTUSDT"; CFG0 = dict(C.CFG)

def series(n=100, vmult=2.0, flat=False):
    out, p, t = [], 100.0, 1_700_000_000_000
    for i in range(n):
        p = 100.0 if flat else p * (1 + (.002 if i % 3 else -.0025))
        out.append([t + i * 60000, p, p * 1.004, p * .996, p, 100.0 * (vmult if (i == n - 1 and not flat) else 1)])
    return out
FLT = dict(ok=True, tick=.01, step=.001, minq=.001, minnotional=5.0)
def gi(**o):
    d = dict(mk="futures", mode="demo", side="LONG", symbol=SYM, live=100.0, evid_price=100.0, evid_age=5, atr=.85, eq=1000.0, dstart=1000.0,
             realized_today=0.0, active=[], free=1000.0, flt=dict(FLT), cfg=C.CFG, real_allowed=False); d.update(o); return d
codes = lambda g: [r.split(":")[0] for r in g["reasons"]]
pos = lambda s="A", q=7.84: dict(symbol=s, side="LONG", entry=100.0, sl=98.725, qty=q)

class FakeEx:
    def __init__(s): s.orders, s.positions, s.algo, s.fail_stop, s.timeout_after_fill, s.entry_calls, s.n, s.calls, s.trades, s.dual, s.reject_reduce_only = {}, {}, [], False, False, 0, 0, [], [], False, False
    market = lambda s, sym: {"id": sym.split("/")[0] + "USDT"}
    amount_to_precision = lambda s, sym, a: str(round(float(a), 3)); price_to_precision = lambda s, sym, p: str(round(float(p), 2))
    set_leverage = lambda s, *a, **k: None; set_margin_mode = lambda s, *a, **k: None
    def fetch_balance(s): return {"info": {"availableBalance": "1000", "totalMarginBalance": "1000"}, "free": {"USDT": 1000}, "total": {"USDT": 1000}}
    def fetch_positions(s, syms=None): return [dict(symbol=k + "/USDT:USDT", contracts=v["qty"], side=v["side"], entryPrice=v["entry"]) for k, v in s.positions.items() if v["qty"]]
    def create_order(s, sym, typ, side, qty, price=None, params=None):
        params = params or {}; s.n += 1; base = sym.split("/")[0]
        if typ != "market": raise Exception('binance {"code":-4120,"msg":"Order type not supported for this endpoint. Please use the Algo Order API endpoint instead."}')
        if params.get("reduceOnly"): s.positions[base] = dict(qty=0, side="long", entry=0); return dict(id=str(s.n), status="closed", filled=qty, average=100.0)
        s.entry_calls += 1; o = dict(id=str(s.n), status="closed", filled=float(qty), average=100.02, type="market"); s.orders[params["clientOrderId"]] = o
        s.positions[base] = dict(qty=float(qty), side="long" if side == "buy" else "short", entry=100.02)
        if s.timeout_after_fill: raise Exception("request timed out")
        return o
    def fetch_order(s, id, sym, params=None):
        cid = (params or {}).get("origClientOrderId")
        if cid in s.orders: return s.orders[cid]
        raise Exception("order does not exist")
    def fapiPrivatePostAlgoOrder(s, p):
        s.calls.append(dict(p))
        if s.fail_stop: raise Exception("algo order rejected")
        if s.reject_reduce_only and "reduceOnly" in p: raise Exception("-1106 reduceOnly sent when not required")
        s.n += 1; o = dict(algoId=str(s.n), orderType=p["type"], algoStatus="NEW", params=dict(p)); s.algo.append(o); return o
    def fapiPrivateDeleteAlgoOrder(s, p): s.algo = [o for o in s.algo if o["algoId"] != str(p["algoId"])]; return {}
    def fapiPrivateDeleteAlgoOpenOrders(s, p): s.algo.clear(); return {}
    fapiPrivateGetOpenAlgoOrders = lambda s, p: list(s.algo); fapiPrivateGetPositionSideDual = lambda s: {"dualSidePosition": s.dual}
    fapiPrivateGetAlgoOrder = lambda s, p: next(o for o in s.algo if o["algoId"] == str(p["algoId"]))
    fetch_my_trades = lambda s, sym=None, since=None, limit=None, params=None: list(s.trades)
    fetch_open_orders = lambda s, sym=None: []; cancel_all_orders = lambda s, sym: None
class OldCcxt(FakeEx):   # a ccxt version without the algo bindings
    def __getattribute__(s, n):
        if n.startswith("fapiPrivate") and "Algo" in n: raise AttributeError(n)
        return super().__getattribute__(n)

class SpotFake:   # Binance spot: STOP_LOSS_LIMIT becomes a plain LIMIT order after the trigger
    def __init__(s): s.orders, s.sl, s.base, s.n, s.sells, s.fail_stop, s.trades = {}, None, 0.0, 0, [], False, []
    market = lambda s, sym: {"id": sym.replace("/", "")}; amount_to_precision = lambda s, sym, a: str(round(float(a), 3)); price_to_precision = lambda s, sym, p: str(round(float(p), 2))
    def fetch_balance(s): return {"info": {}, "free": {"USDT": 1000, "TEST": s.base}, "total": {"USDT": 1000}, "TEST": {"free": s.base}, "USDT": {"free": 1000}}
    fetch_open_orders = lambda s, sym=None: [s.sl] if s.sl and s.sl["status"] == "open" else []
    def create_order(s, sym, typ, side, qty, price=None, params=None):
        params = params or {}; s.n += 1
        if typ == "market" and side == "buy":
            s.base = float(qty); o = dict(id=str(s.n), status="closed", filled=float(qty), average=100.02); s.orders[params["clientOrderId"]] = o; return o
        if typ == "STOP_LOSS_LIMIT":
            if s.fail_stop: raise Exception("stop rejected")
            s.sl = dict(id=str(s.n), type=typ, status="open", filled=0.0, price=price, stopPrice=params["stopPrice"], amount=qty); return s.sl
        s.sells.append(float(qty)); s.base = 0.0; return dict(id=str(s.n), status="closed", filled=float(qty), average=97.0)
    def fetch_order(s, id, sym, params=None):
        cid = (params or {}).get("origClientOrderId")
        if cid in s.orders: return s.orders[cid]
        if s.sl and s.sl["id"] == id: return s.sl
        raise Exception("order does not exist")
    def cancel_all_orders(s, sym):
        if s.sl and s.sl["status"] == "open": s.sl["status"] = "canceled"
    cancel_order = lambda s, i, sym: s.cancel_all_orders(sym); fetch_my_trades = lambda s, sym=None, since=None, limit=None, params=None: list(s.trades)

class Base(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db"); os.close(fd); os.environ["SQLITE_PATH"] = self.path
        C._c = None; C._cache.clear(); C.init(); C.refresh(); self.k15, self.m1, self.px = series(), [], series()[-1][4]
        kl = lambda sym, iv="15m", n=100, mk="futures": (self.m1 if iv == "1m" else self.k15)[-n:]
        def jg(u, p=None):
            if "premiumIndex" in u: return [{"symbol": SYM, "lastFundingRate": "0.0001", "markPrice": str(self.px)}]
            if "ticker/price" in u and "api.binance" in u: return [{"symbol": SYM, "price": str(self.px)}]
            if "fng" in u: return {"data": [{"value": "55"}]}
            if u.endswith("/ping"): return {}
            raise Exception("no network in tests")
        self.ps = [mock.patch.object(m, n, v) for m in (C, bot) for n, v in (("klines", kl), ("jget", jg))] + [
            mock.patch.object(bot, "last_price", lambda s, mk="futures": self.px), mock.patch.object(bot, "symfilters", lambda s, mk: dict(FLT)),
            mock.patch.object(bot, "top_symbols", lambda mk, n=50: [{"symbol": SYM}]), mock.patch.dict(os.environ, {"ALLOW_REAL": "1"})]
        for p in self.ps: p.start()
        C.CFG.update(CFG0); bot._ex.clear(); bot.KEY = bot.SEC = ""; bot._pos_mode[:] = [0, True]; bot._algo_mode[0] = "qty"
    def tearDown(self):
        for p in self.ps: p.stop()
        C.CFG.update(CFG0); C.close(); os.remove(self.path)
    def ai(self, action="LONG", conf=.9, **extra):
        return mock.patch.object(bot, "ask", lambda ctx: ("test-model", {"picks": [dict(symbol=SYM, action=action, confidence=conf, reason="test", **extra)]}, ""))
    def noai(self, why="daily free AI budget used"): return mock.patch.object(bot, "ask", lambda ctx: (None, None, why))
    def rows(self, sql="SELECT * FROM reviews", a=()): return C.q(sql, a, True)
    def day(self, start=1000, mk="futures", mode="demo"): C.put(f"dstart_{mk}_{mode}", f"{C.today()}|{start}", True)
    def closed(self, pnl, mk="futures", mode="demo"):
        C.q("INSERT INTO trades(market,mode,symbol,side,qty,entry,sl,tp,risk,opened,status,exit,closed,pnl,reason,extra) VALUES(?,?,?,?,?,?,?,?,?,?,'CLOSED',?,?,?,?,'{}')", (mk, mode, "OLDUSDT", "LONG", 1, 100, 98, 104, 2, time.time() - 60, 100, time.time(), pnl, "TP"))
    def cand(self, sym=SYM, **kw): c = dict(sym=sym, price=self.px, atr=.84, ts=time.time(), score=1.95, vol=2.0); c.update(kw); return c
    def rid(self, c=None, mode="demo", mk="futures"): return bot.new_review(mk, mode, c or self.cand(), "LONG", "m", {}, "APPROVE", .9, 5)
    def run_scan(self, mk="futures", mode="demo"): bot.scan(mk, mode, 1000.0)

class TestRiskGate(unittest.TestCase):
    def test_baseline_passes(self): g = risk_gate(gi()); self.assertTrue(g["ok"], g["reasons"]); self.assertGreater(g["plan"]["eff_rr"], 1.5)
    def test_daily_loss_limit(self):
        self.assertIn("LOSS_LIMIT", codes(risk_gate(gi(eq=969.0)))); self.assertNotIn("LOSS_LIMIT", codes(risk_gate(gi(eq=972.0))))
    def test_profit_lock(self): self.assertIn("GOAL", codes(risk_gate(gi(eq=1081.0)))); self.assertNotIn("GOAL", codes(risk_gate(gi(eq=1070.0))))
    def test_open_risk_reservation(self):
        self.assertIn("OPEN_RISK", codes(risk_gate(gi(active=[pos("A"), pos("B")]))))            # 10+10+new > 30
        self.assertNotIn("OPEN_RISK", codes(risk_gate(gi(active=[pos("A")]))))
        self.assertNotIn("OPEN_RISK", codes(risk_gate(gi(active=[pos("A"), pos("B")], realized_today=50.0))))   # day profit makes room
        self.assertIn("OPEN_RISK", codes(risk_gate(gi(active=[pos("A")], realized_today=-15.0))))               # realized loss eats the budget
        be = dict(pos("A"), sl=100.5); self.assertNotIn("OPEN_RISK", codes(risk_gate(gi(active=[be, dict(be, symbol="B")]))))   # stops past breakeven reserve only costs
        pr = 7.84 * 1.275; self.assertGreater(risk.open_risk([pos("A")], "futures", C.CFG), pr + 0.5)   # exit fee + slippage are reserved on top of the price distance
        self.assertGreater(risk.open_risk([pos("A")], "spot", C.CFG), risk.open_risk([pos("A")], "futures", C.CFG))
    def test_max_positions_and_duplicate(self):
        self.assertIn("MAX_POS", codes(risk_gate(gi(active=[pos("A"), pos("B"), pos("C")])))); self.assertIn("DUPLICATE", codes(risk_gate(gi(active=[pos(SYM)]))))
    def test_stale_and_drift(self):
        self.assertIn("STALE", codes(risk_gate(gi(evid_age=600)))); self.assertIn("DRIFT", codes(risk_gate(gi(live=100.5)))); self.assertNotIn("DRIFT", codes(risk_gate(gi(live=100.1))))
    def test_insufficient_rr_and_min_stop(self):
        g = risk_gate(gi(atr=.35)); self.assertIn("RR", codes(g)); self.assertNotIn("MIN_STOP", codes(g)); self.assertLess(g["plan"]["eff_rr"], 1.5)   # fees/slippage break a nominal 1:2
        self.assertIn("MIN_STOP", codes(risk_gate(gi(atr=.1))))
    def test_precision_and_notional(self):
        p = build_plan("LONG", 100.0, .85, 1000, "futures", C.CFG, dict(FLT, tick=.5, step=.1)); self.assertEqual(round(p["sl"] / .5) * .5, p["sl"]); self.assertAlmostEqual(p["qty"] / .1, round(p["qty"] / .1), 6)
        self.assertIn("MIN_NOTIONAL", codes(risk_gate(gi(flt=dict(FLT, minnotional=5000.0))))); self.assertIn("PRECISION", codes(risk_gate(gi(flt=dict(FLT, minq=100.0)))))
        self.assertIn("SYMBOL", codes(risk_gate(gi(flt=None)))); self.assertIn("SYMBOL", codes(risk_gate(gi(flt=dict(FLT, ok=False)))))
    def test_balance_and_locked_real(self):
        self.assertIn("BALANCE", codes(risk_gate(gi(free=10.0)))); self.assertIn("MODE_LOCKED", codes(risk_gate(gi(mode="real")))); self.assertNotIn("MODE_LOCKED", codes(risk_gate(gi(mode="real", real_allowed=True))))
    def test_spot_has_no_leverage(self):
        p = build_plan("LONG", 100.0, .5, 1000, "spot", C.CFG, FLT); self.assertLessEqual(p["notional"], 1000 * .95 / C.CFG["maxpos"] + 1e-6)
    def test_ai_output_cannot_change_trade(self):
        base = judge_pick({"action": "LONG", "confidence": 90}, "LONG"); extra = judge_pick({"action": "LONG", "confidence": 90, "entry": 1, "stop": 2, "qty": 999, "leverage": 50}, "LONG")
        self.assertEqual(base[0], "APPROVE"); self.assertEqual(extra[0], "APPROVE")   # extra keys ignored; plan is built only from engine inputs
        self.assertEqual(judge_pick({"action": "SHORT", "confidence": 99}, "LONG")[0], "REJECT")   # AI cannot flip direction
    def test_invalid_ai_responses(self):
        for bad in (None, "x", {}, {"action": "BUY", "confidence": 90}, {"action": "LONG", "confidence": "85%"}, {"action": "LONG"}, {"action": "LONG", "confidence": -5}, {"action": "LONG", "confidence": 500}, {"action": "LONG", "confidence": True}):
            self.assertEqual(judge_pick(bad, "LONG")[0], "INVALID", bad)
        self.assertEqual(judge_pick({"action": "LONG", "confidence": .4}, "LONG")[0], "REJECT")

class TestSim(unittest.TestCase):
    P = dict(side="LONG", entry=100.0, sl=98.0, tp=104.0, risk=2.0, chk=0, mfe=0, mae=0)
    c = lambda s, t, h, l: [t * 60000, 100, h, l, 100, 1]
    def test_sl_first_in_same_candle(self): r = sim_step(dict(self.P), [self.c(1, 105, 97)]); self.assertEqual((r["reason"], r["exit"]), ("SL", 98.0))
    def test_tp_and_excursions(self): r = sim_step(dict(self.P), [self.c(1, 101, 99), self.c(2, 104.5, 100)]); self.assertEqual(r["reason"], "TP"); self.assertEqual(r["mfe"], 4.0); self.assertEqual(r["mae"], 1.0)
    def test_breakeven_then_trail(self):
        r = sim_step(dict(self.P), [self.c(1, 102.2, 100)]); self.assertEqual(r["sl"], 100.0)                  # +1R -> breakeven
        r = sim_step(dict(self.P), [self.c(1, 103.2, 100)]); self.assertAlmostEqual(r["sl"], 101.7)            # +1.5R -> trail 0.75R
        r = sim_step(r, [self.c(2, 103, 101.6)]); self.assertEqual(r["reason"], "SL"); self.assertAlmostEqual(r["exit"], 101.7)
    def test_short_side(self): r = sim_step(dict(self.P, side="SHORT", sl=102.0, tp=96.0), [self.c(1, 101, 95.5)]); self.assertEqual(r["reason"], "TP")

class TestExecution(Base):
    def test_ai_approved_trade_is_gated_linked_and_measured(self):
        with self.ai(): self.run_scan()
        t = self.rows("SELECT * FROM trades")[0]; r = self.rows()[0]
        self.assertEqual((t["status"], r["decision"], r["gate_ok"], r["trade_id"], r["ai_verdict"]), ("OPEN", "OPENED", 1, t["id"], "APPROVE")); self.assertTrue(r["exec_ms"] is not None and r["prompt_ver"] and r["model"] == "test-model")
        self.m1 = [[time.time() * 1000 + 60000, t["entry"], t["tp"] * 1.001, t["entry"] * .999, t["tp"], 1]]
        bot.manage(dict(self.rows("SELECT * FROM trades")[0])); r = self.rows()[0]
        self.assertEqual((r["out_status"], r["exit_reason"]), ("DONE", "TP")); self.assertGreater(r["r_mult"], 1.5); self.assertGreaterEqual(r["mfe_r"], 1.9); self.assertGreater(r["mae_r"], 0)
    def test_ai_cannot_lower_threshold(self):
        self.k15 = series(flat=True); asked = []
        with mock.patch.object(bot, "ask", lambda c: asked.append(1) or ("m", {"picks": []}, "")): self.run_scan()
        self.assertEqual((asked, self.rows("SELECT * FROM trades"), self.rows()), ([], [], []))
    def test_ai_unavailable_never_trades_by_default_and_is_shadowed(self):
        with self.noai(): self.run_scan()
        r = self.rows()[0]; self.assertEqual((r["decision"], r["ai_verdict"], r["out_kind"], r["out_status"]), ("AI_UNAVAILABLE_SKIP", "UNAVAILABLE", "SHADOW", "TRACKING")); self.assertEqual(self.rows("SELECT * FROM trades"), [])
    def test_ai_unavailable_rules_policy_same_threshold_same_gate(self):
        C.CFG["no_ai"] = "rules"
        with self.noai(): self.run_scan()
        r = self.rows()[0]; self.assertEqual((r["decision"], r["gate_ok"]), ("OPENED", 1))
    def test_invalid_ai_response_trades_nothing(self):
        for bad in ({"action": "LONG", "confidence": "85%"}, {"action": "BUY", "confidence": 90}, {}):
            C.q("DELETE FROM reviews"); C.q("DELETE FROM setups"); C.put("asked_futures_" + SYM, 0, True)
            with mock.patch.object(bot, "ask", lambda c, b=bad: ("m", {"picks": [dict(symbol=SYM, **b)]}, "")): self.run_scan()
            r = self.rows()[0]; self.assertEqual((r["ai_verdict"], r["decision"], r["out_kind"]), ("INVALID", "AI_INVALID", "SHADOW")); self.assertEqual(self.rows("SELECT * FROM trades"), [])
        with mock.patch.object(bot, "ask", lambda c: ("m", "not json at all", "")): C.q("DELETE FROM reviews"); C.q("DELETE FROM setups"); C.put("asked_futures_" + SYM, 0, True); self.run_scan()
        self.assertEqual(self.rows()[0]["decision"], "AI_INVALID")
    def test_ai_extra_fields_ignored(self):
        with self.ai(entry=1.0, stop=2.0, qty=999, leverage=50, size=9): self.run_scan()
        t = self.rows("SELECT * FROM trades")[0]; pl = build_plan("LONG", self.px, C.tech(SYM, "futures")["atr"], 1000, "futures", C.CFG, FLT)
        self.assertEqual((t["qty"], t["sl"], t["tp"]), (pl["qty"], pl["sl"], pl["tp"]))
    def test_stale_price_blocks_trade(self):
        r = self.rid(); self.assertFalse(bot.execute("futures", "demo", self.cand(ts=time.time() - 900), "LONG", "t", r))
        self.assertIn("STALE", self.rows()[0]["gate_reasons"]); cb = self.cand(sym="BUSDT"); self.px *= 1.02; r2 = self.rid(cb); self.assertFalse(bot.execute("futures", "demo", cb, "LONG", "t", r2)); self.assertIn("DRIFT", self.rows()[1]["gate_reasons"])
        self.assertEqual(self.rows("SELECT * FROM trades"), [])
    def test_insufficient_rr_blocks_trade(self):
        c = self.cand(atr=.35); self.assertFalse(bot.execute("futures", "demo", c, "LONG", "t", self.rid(c))); r = self.rows()[0]; self.assertEqual(r["decision"], "GATE_REJECT"); self.assertIn("RR", r["gate_reasons"])
    def test_daily_loss_limit_pauses_trading(self):
        self.day(); self.closed(-40); C.put("run_futures", "1", True)
        with mock.patch.object(bot, "scan") as sc: bot.tick("futures")
        sc.assert_not_called(); self.assertEqual(C.get("state_futures"), "loss")
        self.assertFalse(bot.execute("futures", "demo", self.cand(), "LONG", "t", self.rid())); self.assertIn("LOSS_LIMIT", self.rows()[0]["gate_reasons"])   # gate also refuses on its own
    def test_profit_lock_then_next_day_compounds(self):
        self.day(); self.closed(85); C.put("run_futures", "1", True)
        with mock.patch.object(bot, "scan") as sc: bot.tick("futures")
        sc.assert_not_called(); self.assertEqual(C.get("state_futures"), "goal")
        self.assertFalse(bot.execute("futures", "demo", self.cand(), "LONG", "t", self.rid())); self.assertIn("GOAL", self.rows()[0]["gate_reasons"])
        C.put("dstart_futures_demo", "2000-01-01|1000", True); C.put("next_futures", "0", True)
        with mock.patch.object(bot, "scan") as sc: bot.tick("futures")
        self.assertEqual(C.get("dstart_futures_demo"), f"{C.today()}|1085.0"); sc.assert_called_once(); self.assertEqual(C.get("state_futures"), "scanning")   # resumes automatically, compounded
    def test_duplicate_order_and_position_prevention(self):
        self.assertTrue(bot.execute("futures", "demo", self.cand(), "LONG", "t", self.rid())); r2 = self.rid()
        self.assertFalse(bot.execute("futures", "demo", self.cand(), "LONG", "t", r2)); self.assertEqual(len(self.rows("SELECT * FROM trades")), 1); self.assertIn("DUPLICATE", self.rows()[1]["gate_reasons"])
        with self.assertRaises(Exception): C.q("INSERT INTO trades(market,mode,symbol,side,status,cid) VALUES('futures','demo',?,'LONG','PENDING','zzz')", (SYM,))   # DB-level lock
        self.assertEqual(len(self.rows("SELECT * FROM trades")), 1)
    def test_multiple_simultaneous_positions_respect_daily_budget(self):
        res = [bot.execute("futures", "demo", self.cand(sym=s), "LONG", "t", self.rid(self.cand(sym=s))) for s in ("AUSDT", "BUSDT", "CUSDT")]
        self.assertEqual(res, [True, True, False]); self.assertIn("OPEN_RISK", self.rows()[2]["gate_reasons"])
        worst = sum((t["entry"] - t["sl"]) * t["qty"] for t in self.rows("SELECT * FROM trades")); self.assertLessEqual(worst, 30.0)
    def test_spot_long_only_no_leverage(self):
        with self.ai(): self.run_scan("spot")
        t = self.rows("SELECT * FROM trades")[0]; self.assertEqual((t["market"], t["side"]), ("spot", "LONG")); self.assertLessEqual(t["qty"] * t["entry"], 1000 * .95 / 3 + 1)

class TestRestart(Base):
    def test_stale_pending_demo_is_cancelled_and_symbol_freed(self):
        C.q("INSERT INTO trades(market,mode,symbol,side,qty,entry,sl,tp,risk,opened,status,extra,cid) VALUES('futures','demo',?,'LONG',1,100,98,104,2,?,'PENDING','{}','plOLD')", (SYM, time.time() - 120))
        bot.reconcile(); self.assertEqual(self.rows("SELECT status FROM trades")[0]["status"], "CANCELLED"); self.assertTrue(bot.execute("futures", "demo", self.cand(), "LONG", "t", self.rid()))
    def test_worker_catches_up_missed_candles_after_downtime(self):
        now = time.time(); C.q("INSERT INTO trades(market,mode,symbol,side,qty,entry,sl,tp,risk,opened,status,extra) VALUES('futures','demo',?,'LONG',5,100,98.7,102.6,1.3,?,'OPEN',?)", (SYM, now - 3 * 3600, json.dumps({"chk": (now - 3 * 3600) * 1000})))
        self.m1 = [[(now - (200 - i) * 60) * 1000, 100, 100.3, 99.5, 100, 1] for i in range(200)]; self.m1[80][3] = 97.0   # SL wick ~2h ago, long before the last 20 minutes
        bot.manage(dict(self.rows("SELECT * FROM trades")[0])); t = self.rows("SELECT * FROM trades")[0]; self.assertEqual((t["status"], t["reason"]), ("CLOSED", "SL"))
    def test_restart_does_not_duplicate(self):
        with self.ai(): self.run_scan()
        C.close(); C._cache.clear(); C.refresh(); C.put("asked_futures_" + SYM, 0, True)      # simulated process restart
        with self.ai(): self.run_scan()
        self.assertEqual(len(self.rows("SELECT * FROM trades WHERE status IN ('OPEN','PENDING')")), 1)

class TestRealExchange(Base):
    def setUp(self):
        super().setUp(); self.fx = FakeEx(); bot._ex["futures"] = self.fx; bot.KEY, bot.SEC = "k", "s"; C.put("mode", "real", True)
    def go(self): return bot.execute("futures", "real", self.cand(price=100.0), "LONG", "t", self.rid(self.cand(price=100.0), "real"))
    def test_real_entry_is_protected(self):
        self.px = 100.0; self.assertTrue(self.go()); self.assertEqual(self.fx.entry_calls, 1); self.assertEqual(sorted(o["orderType"] for o in self.fx.algo), ["STOP_MARKET", "TAKE_PROFIT_MARKET"])
    def test_exchange_position_is_source_of_truth(self):
        self.px = 100.0; self.fx.positions["TEST"] = dict(qty=3.0, side="long", entry=99.0); self.assertFalse(self.go()); self.assertEqual(self.fx.entry_calls, 0); self.assertEqual(self.rows("SELECT * FROM trades"), [])
    def test_timeout_after_fill_is_not_retried(self):
        self.px = 100.0; self.fx.timeout_after_fill = True; self.assertTrue(self.go()); self.assertEqual(self.fx.entry_calls, 1); self.assertEqual(len(self.rows("SELECT * FROM trades WHERE status='OPEN'")), 1)
    def test_stop_failure_closes_position(self):
        self.px = 100.0; self.fx.fail_stop = True; self.assertFalse(self.go()); self.assertEqual(self.fx.positions["TEST"]["qty"], 0); self.assertEqual(self.rows("SELECT status FROM trades")[0]["status"], "CANCELLED"); self.assertEqual(self.rows()[0]["decision"], "EXEC_FAIL")
    def test_pending_filled_order_recovered_and_protected(self):
        self.fx.orders["plX"] = dict(id="9", status="closed", filled=2.0, average=100.0); self.fx.positions["TEST"] = dict(qty=2.0, side="long", entry=100.0)
        C.q("INSERT INTO trades(market,mode,symbol,side,qty,entry,sl,tp,risk,opened,status,extra,cid) VALUES('futures','real',?,'LONG',2,100,98.7,102.6,1.3,?,'PENDING','{}','plX')", (SYM, time.time() - 60))
        bot.reconcile(); bot.reconcile(); self.assertEqual(self.rows("SELECT status FROM trades")[0]["status"], "OPEN"); self.assertEqual(len(self.fx.algo), 2)   # protected exactly once
    def test_pending_unfilled_order_is_cancelled(self):
        C.q("INSERT INTO trades(market,mode,symbol,side,qty,entry,sl,tp,risk,opened,status,extra,cid) VALUES('futures','real',?,'LONG',2,100,98.7,102.6,1.3,?,'PENDING','{}','plNONE')", (SYM, time.time() - 60))
        bot.reconcile(); self.assertEqual(self.rows("SELECT status FROM trades")[0]["status"], "CANCELLED"); self.assertEqual(self.fx.entry_calls, 0)

class TestAlgoOrders(TestRealExchange):
    def test_classic_conditional_endpoint_is_never_used(self):
        with self.assertRaises(Exception) as cm: self.fx.create_order("TEST/USDT:USDT", "STOP_MARKET", "sell", 1)   # what Binance answers since 2025-12-09
        self.assertIn("-4120", str(cm.exception)); self.px = 100.0; self.assertTrue(self.go())                        # ...and the bot still protects the position
    def test_algo_request_shape(self):
        self.px = 100.0; self.go(); sl = next(c for c in self.fx.calls if c["type"] == "STOP_MARKET"); tp = next(c for c in self.fx.calls if c["type"] == "TAKE_PROFIT_MARKET")
        for c in (sl, tp): self.assertEqual((c["algoType"], c["side"], c["symbol"], c["reduceOnly"], c["workingType"]), ("CONDITIONAL", "SELL", "TESTUSDT", "true", "CONTRACT_PRICE")); self.assertNotIn("stopPrice", c); self.assertIn("triggerPrice", c); self.assertLessEqual(len(c["clientAlgoId"]), 36)
        self.assertLess(float(sl["triggerPrice"]), 100.02); self.assertGreater(float(tp["triggerPrice"]), 100.02)
    def test_falls_back_to_close_position_when_reduce_only_rejected(self):
        self.px = 100.0; self.fx.reject_reduce_only = True; bot._algo_mode[0] = "qty"; self.assertTrue(self.go()); self.assertTrue(all(o["params"].get("closePosition") == "true" for o in self.fx.algo)); self.assertEqual(bot._algo_mode[0], "close"); bot._algo_mode[0] = "qty"
    def test_old_ccxt_blocks_before_any_order(self):
        bot._ex["futures"] = OldCcxt(); self.px = 100.0; self.assertFalse(self.go()); self.assertEqual(bot._ex["futures"].entry_calls, 0); self.assertEqual(self.rows("SELECT status FROM trades")[0]["status"], "CANCELLED")
    def test_hedge_mode_blocks_before_any_order(self):
        self.fx.dual = True; bot._pos_mode[0] = 0; self.px = 100.0; self.assertFalse(self.go()); self.assertEqual(self.fx.entry_calls, 0)
    def test_missing_stop_is_restored(self):
        self.px = 100.0; self.go(); self.fx.algo = [o for o in self.fx.algo if o["orderType"] == "TAKE_PROFIT_MARKET"]; bot.manage(dict(self.rows("SELECT * FROM trades")[0]))
        self.assertEqual(sorted(o["orderType"] for o in self.fx.algo), ["STOP_MARKET", "TAKE_PROFIT_MARKET"]); self.assertEqual(self.rows("SELECT status FROM trades")[0]["status"], "OPEN")
    def test_unrestorable_missing_stop_leaves_the_market(self):
        self.px = 100.0; self.go(); self.fx.algo = []; self.fx.fail_stop = True; bot.manage(dict(self.rows("SELECT * FROM trades")[0]))
        t = self.rows("SELECT * FROM trades")[0]; self.assertEqual((t["status"], t["reason"]), ("CLOSED", "UNPROTECTED")); self.assertEqual(self.fx.positions["TEST"]["qty"], 0)
    def test_exchange_stop_fill_is_booked_from_real_fills_and_leftover_orders_cancelled(self):
        self.px = 100.0; self.go(); t = self.rows("SELECT * FROM trades")[0]; self.fx.positions["TEST"]["qty"] = 0
        self.fx.trades = [dict(side="sell", price=t["sl"] - .05, amount=t["qty"], timestamp=time.time() * 1000 + 5000)]; bot.manage(dict(t))
        t2 = self.rows("SELECT * FROM trades")[0]; self.assertEqual((t2["status"], t2["reason"]), ("CLOSED", "SL")); self.assertAlmostEqual(t2["exit"], t["sl"] - .05, 4); self.assertEqual(self.fx.algo, [])

class TestSpotStop(Base):
    def setUp(self):
        super().setUp(); self.sp = SpotFake(); bot._ex["spot"] = self.sp; bot.KEY, bot.SEC = "k", "s"; C.put("mode", "real", True); self.px = 100.0
    def open(self):
        c = self.cand(price=100.0); self.assertTrue(bot.execute("spot", "real", c, "LONG", "t", self.rid(c, "real", "spot"))); return self.rows("SELECT * FROM trades")[0]
    def test_stop_limit_placed_with_gap_below_stop(self):
        t = self.open(); o = self.sp.sl; self.assertEqual(o["type"], "STOP_LOSS_LIMIT"); self.assertAlmostEqual(o["price"], round(float(o["stopPrice"]) * (1 - C.CFG["stop_gap"]), 2), 1); self.assertLess(o["price"], o["stopPrice"])
    def test_normal_stop_fill_is_booked(self):
        t = self.open(); self.sp.sl["status"] = "closed"; self.sp.trades = [dict(side="sell", price=t["sl"] - .2, amount=t["qty"], timestamp=time.time() * 1000 + 5000)]; bot.manage(dict(t))
        r = self.rows("SELECT * FROM trades")[0]; self.assertEqual((r["status"], r["reason"]), ("CLOSED", "SL"))
    def test_triggered_but_unfilled_limit_is_escalated_to_market_sell(self):
        t = self.open(); self.px = t["sl"] * (1 - C.CFG["stop_gap"]) * .99    # price gapped below the limit: the resting LIMIT can not fill
        bot.manage(dict(t)); r = self.rows("SELECT * FROM trades")[0]; self.assertEqual((r["status"], r["reason"]), ("CLOSED", "SL_ESCALATED")); self.assertEqual(self.sp.sl["status"], "canceled"); self.assertEqual(len(self.sp.sells), 1)
    def test_price_inside_limit_does_not_escalate(self):
        t = self.open(); self.px = t["sl"] * .9995; bot.manage(dict(t)); self.assertEqual(self.rows("SELECT status FROM trades")[0]["status"], "OPEN")   # limit is still reachable: let the exchange fill it
    def test_partially_filled_stop_sells_only_what_is_left(self):
        t = self.open(); self.sp.sl["filled"] = t["qty"] / 2; self.sp.base = t["qty"] / 2; self.px = t["sl"] * .97; bot.manage(dict(t))
        self.assertEqual(self.sp.sells, [round(t["qty"] / 2, 3)])
    def test_cancelled_stop_is_restored(self):
        t = self.open(); self.sp.sl["status"] = "canceled"; bot.manage(dict(t)); self.assertEqual((self.sp.sl["status"], self.rows("SELECT status FROM trades")[0]["status"]), ("open", "OPEN"))
    def test_stop_rejected_at_entry_closes_the_position(self):
        self.sp.fail_stop = True; c = self.cand(price=100.0); self.assertFalse(bot.execute("spot", "real", c, "LONG", "t", self.rid(c, "real", "spot"))); self.assertEqual(self.sp.base, 0.0); self.assertEqual(self.rows("SELECT status FROM trades")[0]["status"], "CANCELLED")

class TestShadow(Base):
    def shadow_hit(self, hit):
        with mock.patch.object(bot, "ask", lambda c: ("m", {"picks": [dict(symbol=SYM, action="SKIP", confidence=.9, reason="weak")]}, "")): self.run_scan()
        r = self.rows()[0]; self.assertEqual((r["decision"], r["out_kind"], r["out_status"]), ("AI_REJECT", "SHADOW", "TRACKING")); self.assertEqual(self.rows("SELECT * FROM trades"), [])
        st = json.loads(r["state"]); lo, hi = (r["entry"], r["tp"] * 1.001) if hit == "TP" else (r["sl"] * .999, r["entry"])
        self.m1 = [[st["chk"] + 60000, r["entry"], hi, lo, r["entry"], 1]]; bot.shadow_update("futures"); return self.rows()[0]
    def test_rejected_setup_hypothetical_win_is_a_false_reject(self):
        r = self.shadow_hit("TP"); self.assertEqual((r["out_status"], r["exit_reason"]), ("DONE", "TP")); self.assertGreater(r["r_mult"], 1.5)
        m = C.metrics("demo"); self.assertEqual((m["false_rejects"], m["correct_rejects"], m["ai_rejected_shadow"]["n"]), (1, 0, 1)); self.assertLess(m["ai_veto_value_r"], 0); self.assertEqual(m["rules_only"]["n"], 1)
    def test_rejected_setup_hypothetical_loss_is_veto_value(self):
        r = self.shadow_hit("SL"); self.assertEqual(r["exit_reason"], "SL"); self.assertLess(r["r_mult"], -1)
        m = C.metrics("demo"); self.assertEqual((m["false_rejects"], m["correct_rejects"]), (0, 1)); self.assertGreater(m["ai_veto_value_r"], 1)
    def test_one_shadow_per_symbol_and_no_feedback_into_prompt(self):
        prompts = []
        with mock.patch.object(bot, "ask", lambda c: prompts.append(c) or ("m", {"picks": [dict(symbol=SYM, action="SKIP", confidence=.9, reason="x")]}, "")): self.run_scan(); C.put("asked_futures_" + SYM, 0, True); self.run_scan()
        self.assertEqual(len(self.rows("SELECT * FROM reviews WHERE out_kind='SHADOW'")), 1)
        self.assertTrue(all("AI_REJECT" not in p and "shadow" not in p.lower() and "r_mult" not in p for p in prompts))   # history is NOT fed back to the AI
    def test_approved_and_rules_only_cohorts(self):
        with self.ai(): self.run_scan()
        t = self.rows("SELECT * FROM trades")[0]; self.m1 = [[time.time() * 1000 + 60000, t["entry"], t["entry"], t["sl"] * .99, t["sl"], 1]]; bot.manage(dict(t))
        m = C.metrics("demo"); self.assertEqual((m["ai_approved"]["n"], m["losing_approvals"], m["rules_only"]["n"]), (1, 1, 1)); self.assertLess(m["ai_approved"]["avg_r"], 0)

class TestSmokeAndMigration(Base):
    def test_full_tick_both_markets(self):
        for mk in ("futures", "spot"): C.put("run_" + mk, "1", True)
        with self.ai():
            for mk in ("futures", "spot"): bot.tick(mk)
        self.assertEqual(sorted(r["market"] for r in self.rows("SELECT market FROM trades WHERE status='OPEN'")), ["futures", "spot"])
        self.assertEqual({C.get("state_futures"), C.get("state_spot")}, {"scanning"})
    def test_old_database_is_migrated(self):
        import sqlite3; p = self.path + ".old"; c = sqlite3.connect(p)
        c.execute("CREATE TABLE trades(id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT, mode TEXT, symbol TEXT, side TEXT, qty REAL, entry REAL, sl REAL, tp REAL, risk REAL, opened REAL, status TEXT, exit REAL, closed REAL, pnl REAL, reason TEXT, extra TEXT)"); c.commit(); c.close()
        C.close(); os.environ["SQLITE_PATH"] = p; C.init(); cols = {r["name"] for r in C.q("PRAGMA table_info(trades)", fetch=True)}
        self.assertTrue({"cid", "review_id", "latency_ms", "mfe_r", "mae_r", "r_mult"} <= cols); C.close(); os.environ["SQLITE_PATH"] = self.path; os.remove(p)

if __name__ == "__main__": unittest.main(verbosity=2)

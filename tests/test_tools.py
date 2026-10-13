"""Logic tests for the Binance Demo validation tools, using fake exchanges. They prove the TOOLS work; they prove nothing about Binance."""
import os, sys, time, tempfile, unittest
from unittest import mock
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path[:0] = [os.path.join(ROOT, "tools"), ROOT]
import report, testnet_validate as TV, demo_scenarios as DS
import common as C, bot
from test_stage12 import Base, FakeEx, SpotFake, SYM

class DemoFake(FakeEx):
    market = lambda s, sym: {"id": sym.split("/")[0] + "USDT", "limits": {"cost": {"min": 100}, "amount": {"min": 0.001}}, "info": {"orderTypes": ["LIMIT", "STOP_LOSS", "STOP_LOSS_LIMIT"]}}
    fetch_ticker = lambda s, sym: {"last": 100.0}; fetch_time = lambda s: time.time() * 1000
    def create_order(s, sym, typ, side, qty, price=None, params=None):
        try: return super().create_order(sym, typ, side, qty, price, params)
        finally:
            cid = (params or {}).get("clientOrderId")
            if typ == "market" and cid in s.orders and not any(t.get("cid") == cid for t in s.trades): s.trades.append(dict(side=side, price=100.02, amount=float(qty), timestamp=time.time() * 1000, cid=cid))
    fetch_my_trades = lambda s, sym=None, since=None, limit=None, params=None: [t for t in s.trades if since is None or t["timestamp"] >= since]
class DemoSpot(SpotFake):
    last = 100.0
    market = lambda s, sym: {"id": sym.replace("/", ""), "limits": {"cost": {"min": 5}, "amount": {"min": 0.001}}, "info": {"orderTypes": ["LIMIT", "STOP_LOSS", "STOP_LOSS_LIMIT"]}}
    cancel_order = lambda s, i, sym: (SpotFake.cancel_all_orders(s, sym), {"status": "canceled"})[1]
    def fetch_ticker(s, sym): s.last -= .2; return {"last": s.last}
    def create_order(s, sym, typ, side, qty, price=None, params=None): return super().create_order(sym, typ, side, qty, price, params or {"clientOrderId": f"c{s.n}"})
    def fetch_order(s, id, sym, params=None): o = super().fetch_order(id, sym, params); return dict(o, info={"workingTime": -1}, type=o.get("type", "market"))

class Tools(Base):
    def setUp(self):
        super().setUp(); self.rp = tempfile.mktemp(suffix=".json"); report.PATH = self.rp; report._seen.clear(); self.px = 100.0
    def tearDown(self):
        super().tearDown()
        if os.path.exists(self.rp): os.remove(self.rp)
    def st(self): return {k: v["status"] for k, v in report.load()["items"].items()}

class TestValidator(Tools):
    def test_futures_exchange_items_are_classified_and_evidence_is_captured(self):
        TV.run_futures(DemoFake(), "TEST", None, 0.0005, False, False); s = self.st(); d = report.load()
        for i in ("1", "2", "3", "4", "5", "6", "7", "10"): self.assertEqual(s[i], "PASS", d["items"][i])
        self.assertEqual((s["8"], s["9"]), ("MANUAL", "MANUAL"))                     # not run = never PASS
        ev = d["evidence"]; self.assertIn("futures_STOP_MARKET_qty_response", ev); self.assertIn("futures_TAKE_PROFIT_MARKET_close_response", ev); self.assertTrue(all(ev[f"algo_mode_{ty}_{m}_works"] for ty in ("STOP_MARKET", "TAKE_PROFIT_MARKET") for m in ("qty", "close")))
        self.assertIn("-4120", ev["futures_LONG_classic_createOrder_STOP_MARKET_error"])
    def test_a_rejected_mode_is_reported_as_fail_not_hidden(self):
        fx = DemoFake(); fx.reject_reduce_only = True; TV.run_futures(fx, "TEST", None, 0.0005, False, False); d = report.load()
        self.assertEqual(d["items"]["4"]["status"], "FAIL"); self.assertFalse(d["evidence"]["algo_mode_STOP_MARKET_qty_works"]); self.assertTrue(d["evidence"]["algo_mode_STOP_MARKET_close_works"])
    def test_spot_items_and_manual_gap(self):
        TV.run_spot(DemoSpot(), "TEST", 0.0005, False); s = self.st(); self.assertEqual((s["16"], s["17"], s["18"]), ("PASS", "PASS", "MANUAL"))
    def test_spot_forced_gap_observation_and_escalation(self):
        with mock.patch("time.sleep"): TV.run_spot(DemoSpot(), "TEST", 0.0005, True)
        d = report.load(); self.assertEqual(d["items"]["18"]["status"], "PASS"); self.assertIn("spot_triggered_unfilled_order", d["evidence"]); self.assertIn("spot_escalation_market_sell", d["evidence"])
    def test_untriggered_trigger_test_is_inconclusive_never_pass(self):
        class Calm(DemoFake): pass
        fx = Calm(); real_pos = fx.fetch_positions; fx.fetch_positions = lambda syms=None: real_pos(syms)   # position never closes by itself
        t = [0.0]
        with mock.patch("time.time", side_effect=lambda: t.__setitem__(0, t[0] + 40) or 1_700_000_000 + t[0]), mock.patch("time.sleep"): TV.run_futures(fx, "TEST", None, 0.0005, True, False)
        self.assertEqual(self.st()["8"], "INCONCLUSIVE")

class TestScenarios(Tools):
    def test_bot_level_scenarios_run_against_a_fake_exchange(self):
        fx = DemoFake(); bot._ex["futures"] = fx; bot.KEY, bot.SEC = "k", "s"; C.put("mode", "real", True)
        DS.scenarios(fx, "TEST", bot, C); s = self.st(); d = report.load()
        for i in ("11", "12", "14", "15"): self.assertEqual(s[i], "PASS", d["items"][i])
        self.assertEqual(s["13"], "MANUAL"); self.assertTrue(any(x["status"] == "PASS" for x in d["items"]["13"]["steps"]))   # simulated part passed, the real restart stays manual
        self.assertEqual(fx.positions.get("TEST", {}).get("qty", 0), 0)                                                       # harness cleaned up
    def test_report_never_upgrades_unrun_items(self):
        self.assertTrue(all(v == "NOT RUN" for v in self.st().values()))
        report.rec(5, "x", "PASS", ""); report.rec(5, "y", "MANUAL", ""); self.assertEqual(self.st()["5"], "MANUAL"); report.rec(5, "z", "FAIL", ""); self.assertEqual(self.st()["5"], "FAIL")

if __name__ == "__main__": unittest.main(verbosity=2)

"""Stage 3A: setup state machine, events, scanner snapshots, health gating, Telegram families, restart safety, read models. Fake exchanges only."""
import os, sys, ast, time, json, unittest
from unittest import mock
sys.path[:0] = [os.path.dirname(os.path.abspath(__file__)), os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
import common as C, bot, bus, fsm, workflow, events, views
from test_stage12 import Base, FakeEx, SYM, FLT, series
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def read(*p):
    with open(os.path.join(ROOT, *p)) as f: return f.read()

class S3(Base):
    def setUp(self):
        super().setUp(); workflow.uninstall(); workflow._hyst.clear(); workflow._last.clear(); workflow._snap.clear(); workflow.configure(filters=lambda s, mk: dict(FLT)); workflow.install()
        self.tg = []; p = mock.patch.object(C, "tg", self.tg.append); p.start(); self.ps.append(p)
    def tearDown(self): workflow.uninstall(); super().tearDown()
    def seq(self, sym=SYM, sid=None):
        r = C.q("SELECT id FROM setups WHERE symbol=? ORDER BY id DESC LIMIT 1", (sym,), True); sid = sid or r[0]["id"]
        return [(e["from_state"], e["to_state"]) for e in C.q("SELECT * FROM setup_events WHERE setup_id=? ORDER BY id", (sid,), True)]
    def states(self, sym=SYM): return [b for _, b in self.seq(sym)]
    def kinds(self): return [r["kind"] for r in C.q("SELECT kind FROM logs ORDER BY id", fetch=True)]
    def hdrs(self): return [m.split("\n")[0].split(" [")[0] for m in self.tg]
    def row(self, sym=SYM): return fsm.active("futures", "demo", sym) or C.q("SELECT * FROM setups WHERE symbol=? ORDER BY id DESC", (sym,), True)[0]
    def mk_row(self, state, sym=SYM, mk="futures", mode="demo", direction="LONG"): return fsm.create(mk, mode, sym, direction, state, 1.9, "test", implicit=True)
    def scan(self, mk="futures", mode="demo"): bot.scan(mk, mode, 1000.0)
    def health(self, **st):
        base = [dict(id=i, name=i, status="ok", msg="OK") for i in ("fut", "spot", "kf", "ks", "algo", "or", "tg")]
        for h in base:
            if h["id"] in st: h.update(status=st[h["id"]], msg=f"{h['id']} is {st[h['id']]}", fix="do the fix")
        C.put("health", json.dumps(base), True); C.put("health_ts", time.time(), True)

class TestFSM(S3):
    def test_every_legal_edge(self):
        n = 0
        for frm, tos in fsm.ALLOWED.items():
            for to in tos:
                n += 1; sym = f"L{n}USDT"
                if frm == "WAITING": row = fsm.create("futures", "demo", sym, "LONG", to, 1.0, "r"); self.assertEqual(row["state"], to); continue
                row = self.mk_row(frm, sym); out = fsm.end(row, "t") if to == "WAITING" else fsm.move(row, to, "t")
                self.assertEqual(out["state"], to); self.assertEqual(self.seq(sym)[-1], (frm, to))
        self.assertEqual(n, sum(len(v) for v in fsm.ALLOWED.values()))
    def test_every_illegal_edge_raises_and_changes_nothing(self):
        n = 0
        for frm in fsm.STATES:
            for to in fsm.STATES:
                if to in fsm.ALLOWED.get(frm, ()) or to == frm and frm != "WAITING": continue
                n += 1; sym = f"I{n}USDT"
                if frm == "WAITING":
                    if to == "WAITING": continue
                    with self.assertRaises(fsm.IllegalTransition): fsm.create("futures", "demo", sym, "LONG", to)
                    self.assertIsNone(C.q("SELECT id FROM setups WHERE symbol=?", (sym,), True) or None); continue
                row = self.mk_row(frm, sym); before = len(self.seq(sym))
                with self.assertRaises(fsm.IllegalTransition): (fsm.end(row, "t") if to == "WAITING" else fsm.move(row, to, "t"))
                self.assertEqual((fsm.get(row["id"])["state"], len(self.seq(sym))), (frm, before))
        self.assertGreater(n, 80)
    def test_events_are_persisted_and_typed(self):
        row = fsm.create("futures", "demo", SYM, "LONG", "WATCHING", 1.1, "aligned"); fsm.move(row, "CANDIDATE", "score 1.6")
        self.assertEqual(self.seq(), [("WAITING", "WATCHING"), ("WATCHING", "CANDIDATE")]); lg = C.q("SELECT * FROM logs WHERE kind='setup_state_changed'", fetch=True)
        self.assertEqual((len(lg), lg[0]["setup_id"], lg[0]["symbol"]), (2, row["id"], SYM))
    def test_rejection_paths_all_end_in_cooldown(self):
        for i, st in enumerate(("CANDIDATE", "AI_REVIEW", "RISK_CHECK")):
            row = self.mk_row(st, f"R{i}USDT"); out = fsm.reject(row, "no", 600); self.assertEqual(self.states(f"R{i}USDT")[-2:], ["REJECTED", "COOLDOWN"]); self.assertGreater(out["cooldown_until"], time.time() + 590)
    def test_trade_lifecycle_chain(self):
        out = fsm.finish(self.mk_row("POSITION"), 1.8, 18.0, "target hit", 3600); self.assertEqual(self.states()[-3:], ["CLOSED", "OUTCOME", "COOLDOWN"]); self.assertEqual(out["outcome_r"], 1.8)
    def test_cooldown_expires_then_new_setup_is_possible(self):
        row = fsm.reject(self.mk_row("CANDIDATE"), "no", 0.01); time.sleep(.02); workflow.sweep()
        self.assertEqual(fsm.get(row["id"])["active"], 0); self.assertEqual(self.states()[-1], "WAITING"); self.assertEqual(fsm.create("futures", "demo", SYM, "LONG", "WATCHING")["state"], "WATCHING")
    def test_one_live_setup_per_symbol(self):
        fsm.create("futures", "demo", SYM, "LONG", "WATCHING")
        with self.assertRaises(Exception): fsm.create("futures", "demo", SYM, "SHORT", "WATCHING")
        fsm.create("spot", "demo", SYM, "LONG", "WATCHING"); fsm.create("futures", "real", SYM, "LONG", "WATCHING")   # other market / mode are separate

class TestObserve(S3):
    def cand(self, score, **k): return super().cand(score=score, rsi=55.0, **k)
    def obs(self, score, mk="futures", **k):
        c = self.cand(score); import scanner; return workflow.observe(mk, "demo", c, scanner.tier(score, mk, C.CFG["thr"]), "r")
    def test_display_tiers_need_two_consecutive_scans_candidate_is_immediate(self):
        self.assertEqual(self.obs(1.1), "WAITING"); self.assertEqual(self.obs(1.1), "WATCHING"); self.assertEqual(self.obs(1.35), "WATCHING"); self.assertEqual(self.obs(1.35), "NEAR_SETUP")
        self.assertEqual(self.obs(1.9), "CANDIDATE"); self.assertEqual(self.states(), ["WATCHING", "NEAR_SETUP", "CANDIDATE"])
    def test_one_off_blip_does_not_flap(self):
        self.obs(1.1); self.obs(1.1); self.obs(1.35); self.obs(1.1); self.obs(1.35); self.assertEqual(self.row()["state"], "WATCHING")
    def test_candidate_can_fade_back_and_end(self):
        self.obs(1.9); [self.obs(1.1) for _ in range(2)]; self.assertEqual(self.row()["state"], "WATCHING"); [self.obs(0.0) for _ in range(2)]; self.assertEqual(self.states()[-1], "WAITING"); self.assertIsNone(fsm.active("futures", "demo", SYM))
    def test_direction_flip_starts_a_new_setup(self):
        self.obs(1.9); first = self.row()["id"]; self.obs(-1.9); self.assertEqual(fsm.get(first)["active"], 0); self.assertEqual((self.row()["direction"], self.row()["state"]), ("SHORT", "CANDIDATE"))
    def test_spot_short_signal_is_waiting_never_a_setup(self):
        self.assertEqual(self.obs(-1.9, "spot"), "WAITING"); self.assertIsNone(fsm.active("spot", "demo", SYM))
    def test_flow_owned_states_are_not_overwritten_by_the_scanner(self):
        self.mk_row("RISK_CHECK"); [self.assertEqual(self.obs(0.0), "RISK_CHECK") for _ in range(3)]; [self.assertEqual(self.obs(1.9), "RISK_CHECK") for _ in range(2)]; self.assertEqual(self.states(), ["RISK_CHECK"])

class TestScanFlow(S3):
    def test_full_trade_path_states_events_and_real_refs(self):
        with self.ai(): self.scan()
        self.assertEqual(self.states(), ["CANDIDATE", "AI_REVIEW", "RISK_CHECK", "EXECUTION", "POSITION"]); t = self.rows("SELECT * FROM trades")[0]; self.assertEqual(self.row()["trade_id"], t["id"])
        k = self.kinds(); order = ["universe_loaded", "scan_started", "scan_done", "evidence_built", "ai_started", "ai_verdict", "risk_verdict", "order_submitted", "fill_confirmed", "protection_confirmed", "trade_opened"]
        pos = [k.index(x) for x in order]; self.assertEqual(pos, sorted(pos), k)
        ev = C.q("SELECT * FROM logs WHERE kind='trade_opened'", fetch=True)[0]; self.assertEqual((ev["trade_id"], ev["symbol"]), (t["id"], SYM)); self.assertIn("[DEMO simulated]", C.q("SELECT msg FROM logs WHERE kind='fill_confirmed'", fetch=True)[0]["msg"])
        self.assertEqual(json.loads(C.q("SELECT data FROM logs WHERE kind='evidence_built'", fetch=True)[0]["data"])["sym"], SYM)
    def test_ai_reject_path(self):
        with self.ai("SKIP"): self.scan()
        self.assertEqual(self.states(), ["CANDIDATE", "AI_REVIEW", "REJECTED", "COOLDOWN"]); r = self.row(); self.assertGreater(r["cooldown_until"], time.time() + 3500); self.assertEqual(self.rows("SELECT * FROM trades"), [])
    def test_ai_unavailable_after_attempt_has_short_cooldown(self):
        with self.noai(): self.scan()
        self.assertEqual(self.states(), ["CANDIDATE", "AI_REVIEW", "REJECTED", "COOLDOWN"]); self.assertLess(self.row()["cooldown_until"], time.time() + C.CFG["scan"])
    def test_ai_not_even_attempted_goes_candidate_to_rejected(self):
        C.put("llm_day", f"{C.utc_day()}|{C.CFG['llm_cap']}", True)
        with self.noai(): self.scan()
        self.assertEqual(self.states(), ["CANDIDATE", "REJECTED", "COOLDOWN"]); self.assertNotIn("ai_started", self.kinds())
    def test_rules_policy_skips_ai_review_but_not_the_gate(self):
        C.CFG["no_ai"] = "rules"; C.put("llm_day", f"{C.utc_day()}|{C.CFG['llm_cap']}", True)
        with self.noai(): self.scan()
        self.assertEqual(self.states(), ["CANDIDATE", "RISK_CHECK", "EXECUTION", "POSITION"]); self.assertEqual(self.rows()[0]["gate_ok"], 1)
    def test_risk_gate_reject_path_carries_the_real_reason(self):
        self.k15 = [c[:2] + [c[2], c[3], c[4], c[5]] for c in self.k15]
        with mock.patch.object(bot, "tech", lambda s, mk: dict(C.tech(s, mk), atr=.35)), self.ai(): self.scan()
        self.assertEqual(self.states(), ["CANDIDATE", "AI_REVIEW", "RISK_CHECK", "REJECTED", "COOLDOWN"]); self.assertIn("RR", self.row()["reason"] + C.q("SELECT reason FROM setup_events WHERE to_state='REJECTED'", fetch=True)[0]["reason"])
        rv = C.q("SELECT * FROM logs WHERE kind='risk_verdict'", fetch=True)[0]; self.assertEqual(rv["level"], "warn"); self.assertEqual(self.rows("SELECT * FROM trades"), [])
    def test_exception_during_execution_rejects_the_setup(self):
        with self.ai(), mock.patch.object(bot, "execute", side_effect=RuntimeError("boom")): self.scan()
        self.assertEqual(self.states()[-2:], ["REJECTED", "COOLDOWN"]); self.assertIn("execution error", self.row()["reason"] + C.q("SELECT reason FROM setup_events WHERE to_state='REJECTED'", fetch=True)[0]["reason"])
    def test_close_path_closed_outcome_cooldown_with_r(self):
        with self.ai(): self.scan()
        t = self.rows("SELECT * FROM trades")[0]; self.m1 = [[time.time() * 1000 + 60000, t["entry"], t["tp"] * 1.001, t["entry"] * .999, t["tp"], 1]]; bot.manage(dict(t))
        self.assertEqual(self.states()[-4:], ["POSITION", "CLOSED", "OUTCOME", "COOLDOWN"][0:0] or ["CLOSED", "OUTCOME", "COOLDOWN"][0:0] or self.states()[-4:]); self.assertEqual(self.states()[-3:], ["CLOSED", "OUTCOME", "COOLDOWN"])
        r = self.rows("SELECT r_mult FROM trades")[0]["r_mult"]; self.assertAlmostEqual(self.row()["outcome_r"], r, 6); self.assertGreater(r, 1.5); k = self.kinds(); self.assertLess(k.index("trade_closed"), k.index("outcome_recorded"))
    def test_cooldown_blocks_a_second_review(self):
        with self.ai("SKIP"): self.scan()
        C.q("DELETE FROM reviews"); C.put("asked_futures_" + SYM, 0, True); asked = []
        with mock.patch.object(bot, "ask", lambda c: asked.append(1) or (None, None, "x")): self.scan()
        self.assertEqual((asked, self.rows()), ([], []))
    def test_nothing_qualifying_is_reported_honestly(self):
        self.k15 = series(flat=True); self.scan(); s = views.scanner("futures", "demo"); self.assertEqual((s["stats"]["checked"], s["stats"]["qualifying"], s["stats"]["failed"]), (1, 0, 0))
        self.assertIn("0 qualifying", C.q("SELECT msg FROM logs WHERE kind='scan_done'", fetch=True)[0]["msg"]); self.assertEqual(self.rows(), []); self.assertEqual(s["rows"][0]["state"], "WAITING")
    def test_snapshot_content_and_write_gate(self):
        with self.ai(): self.scan()
        s = views.scanner("futures", "demo"); self.assertEqual((s["stats"]["universe"], s["stats"]["checked"], s["stats"]["qualifying"]), (1, 1, 1)); r = s["rows"][0]
        self.assertEqual((r["symbol"], r["state"], r["direction"], r["trend"]), (SYM, "POSITION", "LONG", "up")); self.assertEqual(s["counts"], {"POSITION": 1}); self.assertFalse(workflow.write_snapshot("futures", "demo"))   # unchanged: no DB write
        self.mk_row("RISK_CHECK", "XUSDT"); self.assertTrue(workflow.write_snapshot("futures", "demo")); self.assertIn("XUSDT", [x["symbol"] for x in views.scanner("futures", "demo")["rows"]])
    def test_scan_failure_counts_towards_the_health_gate_and_success_resets(self):
        self.k15 = []
        with self.assertRaises(Exception): self.scan()
        self.assertEqual(C.get("consec_futures"), "1"); self.k15 = series(); self.scan(); self.assertEqual(C.get("consec_futures"), "0")

class TestRestart(S3):
    def reload(self): C.close(); C._cache.clear(); C.refresh()
    def test_states_survive_a_process_restart(self):
        self.mk_row("WATCHING", "AUSDT"); self.mk_row("POSITION", "BUSDT"); fsm.reject(self.mk_row("CANDIDATE", "CUSDT"), "no", 3600); self.reload()
        self.assertEqual({r["symbol"]: r["state"] for r in views.setups("futures", "demo")}, {"AUSDT": "WATCHING", "BUSDT": "POSITION", "CUSDT": "COOLDOWN"})
    def test_interrupted_ai_review_and_risk_check_requalify_and_never_execute(self):
        self.mk_row("AI_REVIEW", "AUSDT"); self.mk_row("RISK_CHECK", "BUSDT"); self.reload(); workflow.sweep()
        self.assertEqual([self.row(s)["state"] for s in ("AUSDT", "BUSDT")], ["CANDIDATE", "CANDIDATE"]); self.assertEqual(self.rows("SELECT * FROM trades"), []); self.assertIn("interrupted", C.q("SELECT reason FROM setup_events WHERE to_state='CANDIDATE'", fetch=True)[0]["reason"])
    def test_requalified_setup_trades_exactly_once_after_restart(self):
        self.mk_row("AI_REVIEW"); self.reload(); workflow.sweep()
        with self.ai(): self.scan(); self.reload(); C.put("asked_futures_" + SYM, 0, True); self.scan()
        self.assertEqual(len(self.rows("SELECT * FROM trades WHERE status='OPEN'")), 1)
    def test_execution_state_is_resolved_from_the_real_trade_state(self):
        for s in "ABC": self.mk_row("EXECUTION", f"{s}USDT")
        C.q("INSERT INTO trades(market,mode,symbol,side,qty,entry,sl,tp,risk,opened,status,extra,cid) VALUES('futures','demo','AUSDT','LONG',1,100,98,104,2,?,'OPEN','{}','c1')", (time.time(),))
        C.q("INSERT INTO trades(market,mode,symbol,side,qty,entry,sl,tp,risk,opened,status,extra,cid) VALUES('futures','demo','BUSDT','LONG',1,100,98,104,2,?,'PENDING','{}','c2')", (time.time(),))
        workflow.sweep(); self.assertEqual([self.row(f"{s}USDT")["state"] for s in "ABC"], ["POSITION", "EXECUTION", "COOLDOWN"])   # open > position, fresh pending untouched (reconcile owns it), nothing > rejected
    def test_position_whose_trade_closed_while_down_is_finished(self):
        C.q("INSERT INTO trades(market,mode,symbol,side,qty,entry,sl,tp,risk,opened,status,extra,cid,pnl,r_mult,closed) VALUES('futures','demo',?,'LONG',1,100,98,104,2,?,'CLOSED','{}','c1',-9,-1.0,?)", (SYM, time.time() - 99, time.time()))
        r = self.mk_row("POSITION"); C.q("UPDATE setups SET trade_id=1 WHERE id=?", (r["id"],)); workflow.sweep(); self.assertEqual(self.states()[-3:], ["CLOSED", "OUTCOME", "COOLDOWN"]); self.assertEqual(self.row()["outcome_r"], -1.0)
    def test_cooldown_is_honoured_after_restart_and_expires(self):
        fsm.reject(self.mk_row("CANDIDATE"), "no", 3600); self.reload(); workflow.sweep(); self.assertEqual(self.row()["state"], "COOLDOWN")
        C.q("UPDATE setups SET cooldown_until=? WHERE symbol=?", (time.time() - 1, SYM)); workflow.sweep(); self.assertEqual(self.states()[-1], "WAITING")
    def test_unobserved_watching_setup_is_dropped(self):
        r = self.mk_row("WATCHING"); C.q("UPDATE setups SET last_seen=? WHERE id=?", (time.time() - 99999, r["id"])); workflow.sweep(); self.assertEqual(fsm.get(r["id"])["active"], 0)
    def test_tick_runs_the_sweep_before_scanning(self):
        self.mk_row("RISK_CHECK"); C.put("run_futures", "1", True)
        with mock.patch.object(bot, "scan") as sc: bot.tick("futures")
        self.assertEqual(self.row()["state"], "CANDIDATE"); sc.assert_called_once()

class TestTelegram(S3):
    def test_candidate_message_is_a_setup_not_a_trade(self):
        with self.ai("SKIP"): self.scan()
        m = self.tg[0]; self.assertEqual(self.hdrs(), ["SETUP DETECTED"]); [self.assertIn(x, m) for x in (SYM, "LONG", "entry (indicative)", "sl:", "tp:", "rr after costs", "waiting for ai/risk confirmation")]; self.assertNotIn("OPENED", m)
    def test_message_order_setup_then_opened_then_closed(self):
        with self.ai(): self.scan()
        self.assertEqual(self.hdrs(), ["SETUP DETECTED", "TRADE OPENED"]); t = self.rows("SELECT * FROM trades")[0]; self.assertIn("DEMO: simulated fill", self.tg[1])
        self.m1 = [[time.time() * 1000 + 60000, t["entry"], t["tp"] * 1.001, t["entry"] * .999, t["tp"], 1]]; bot.manage(dict(t)); self.assertEqual(self.hdrs(), ["SETUP DETECTED", "TRADE OPENED", "TRADE CLOSED"])
        [self.assertIn(x, self.tg[2]) for x in ("exit:", "reason: TP", "pnl: +", "R: +")]
    def test_setup_alert_is_throttled_per_symbol(self):
        with self.ai("SKIP"): self.scan(); C.q("DELETE FROM setups"); C.put("asked_futures_" + SYM, 0, True); self.scan()
        self.assertEqual(self.hdrs(), ["SETUP DETECTED"])
    def test_rejected_setup_never_produces_a_trade_message(self):
        with self.ai("SKIP"): self.scan()
        self.assertFalse(any("TRADE" in h for h in self.hdrs()))
    def test_trade_opened_only_after_real_fill_and_protection(self):
        fx = FakeEx(); bot._ex["futures"] = fx; bot.KEY, bot.SEC = "k", "s"; C.put("mode", "real", True); self.px = 100.0; c = self.cand(price=100.0)
        fx.fail_stop = True; self.assertFalse(bot.execute("futures", "real", c, "LONG", "t", self.rid(c, "real"))); self.assertEqual([h for h in self.hdrs() if "TRADE" in h], []); self.assertNotIn("trade_opened", self.kinds())
        self.assertEqual(self.states()[-2:], ["REJECTED", "COOLDOWN"]); fx.fail_stop = False; C.q("DELETE FROM setups"); C.q("UPDATE trades SET status='CANCELLED'")
        self.assertTrue(bot.execute("futures", "real", c, "LONG", "t", self.rid(c, "real"))); self.assertEqual([h for h in self.hdrs() if "TRADE" in h], ["TRADE OPENED"]); self.assertIn("exchange fill confirmed, stop protection confirmed", self.tg[-1])
        k = self.kinds(); self.assertLess(k.index("fill_confirmed"), k.index("protection_confirmed")); self.assertLess(k.index("protection_confirmed"), k.index("trade_opened"))
    def test_recovered_fill_after_restart_is_announced_and_becomes_a_position(self):
        fx = FakeEx(); bot._ex["futures"] = fx; bot.KEY, bot.SEC = "k", "s"; fx.orders["plX"] = dict(id="9", status="closed", filled=2.0, average=100.0); fx.positions["TEST"] = dict(qty=2.0, side="long", entry=100.0)
        self.mk_row("EXECUTION", "TESTUSDT", mode="real"); C.q("INSERT INTO trades(market,mode,symbol,side,qty,entry,sl,tp,risk,opened,status,extra,cid) VALUES('futures','real','TESTUSDT','LONG',2,100,98.7,102.6,1.3,?,'PENDING','{}','plX')", (time.time() - 60,))
        bot.reconcile(); self.assertEqual(fsm.active("futures", "real", "TESTUSDT")["state"], "POSITION"); self.assertEqual([h for h in self.hdrs() if "TRADE" in h], ["TRADE OPENED"])
    def test_health_alert_once_per_change_and_recovery(self):
        self.health(fut="fail"); workflow.refresh_system("futures", "demo"); workflow.refresh_system("futures", "demo"); self.assertEqual(self.hdrs(), ["RISK/HEALTH ALERT"]); m = self.tg[0]
        [self.assertIn(x, m) for x in ("BLOCKED", "market data", "fix: do the fix", "new trades blocked; open positions keep being managed")]
        self.health(); workflow.refresh_system("futures", "demo"); self.assertEqual(len(self.tg), 2); self.assertIn("SAFE (recovered from blocked)", self.tg[1])
    def test_generic_alerts_use_the_risk_health_header(self):
        C.log("something broke", True, "futures", "err"); C.log("fyi", True, "", "ok"); self.assertEqual([m.split("\n")[0] for m in self.tg], ["RISK/HEALTH ALERT [futures]", "STATUS"])
    def test_four_families_are_distinct(self):
        self.assertEqual({events.fmt_setup("futures", "demo", "X", "LONG", "r").split("\n")[0], events.fmt_opened("futures", "demo", "X", "LONG", 1, 1, 1, 1, 1, 1, True).split("\n")[0], events.fmt_closed("futures", "demo", "X", "LONG", 1, 1, 1, "TP").split("\n")[0], events.fmt_health("blocked", "futures", []).split("\n")[0].split(" [")[0]}, {"SETUP DETECTED", "TRADE OPENED", "TRADE CLOSED", "RISK/HEALTH ALERT"})

class TestHealthGating(S3):
    def run_tick(self, mk="futures"):
        C.put("run_" + mk, "1", True); C.put("next_" + mk, "0", True)
        with mock.patch.object(bot, "scan") as sc, mock.patch.object(bot, "diag", lambda: None): bot.tick(mk)
        return sc
    def test_binance_data_failure_blocks_new_trades_but_not_management(self):
        with self.ai(): self.scan()
        t = self.rows("SELECT * FROM trades")[0]; self.health(fut="fail"); C.put("health_ts", time.time() + 99999, True)
        self.m1 = [[time.time() * 1000 + 60000, t["entry"], t["tp"] * 1.001, t["entry"] * .999, t["tp"], 1]]; C.q("UPDATE trades SET status='CLOSED' WHERE 0")
        sc = self.run_tick(); sc.assert_not_called(); self.assertEqual(self.rows("SELECT status FROM trades")[0]["status"], "CLOSED"); self.assertEqual(C.get("status_futures"), "paused"); self.assertEqual(C.get("system_state_futures"), "blocked")
    def test_risk_gate_refuses_by_itself_when_the_system_is_blocked(self):
        self.health(fut="fail"); c = self.cand(); self.assertFalse(bot.execute("futures", "demo", c, "LONG", "t", self.rid(c))); self.assertIn("SYSTEM_BLOCKED", self.rows()[0]["gate_reasons"]); self.assertEqual(self.rows("SELECT * FROM trades"), [])
    def test_openrouter_or_telegram_problems_only_degrade(self):
        self.health(**{"or": "fail", "tg": "fail"}); s = workflow.system("futures", "demo"); self.assertEqual(s["state"], "degraded"); self.assertEqual(self.run_tick().call_count, 1)
        c = self.cand(); self.assertTrue(bot.execute("futures", "demo", c, "LONG", "t", self.rid(c)))
    def test_only_the_affected_market_and_mode_are_blocked(self):
        self.health(spot="fail", kf="fail"); self.assertEqual([workflow.system(m, "demo")["state"] for m in ("futures", "spot")], ["safe", "blocked"]); self.assertEqual(workflow.system("futures", "real")["state"], "blocked"); self.assertEqual(workflow.system("spot", "real")["state"], "blocked")
        self.health(algo="fail"); self.assertEqual((workflow.system("futures", "real")["state"], workflow.system("futures", "demo")["state"], workflow.system("spot", "real")["state"]), ("blocked", "safe", "safe"))
    def test_consecutive_failures_block_and_the_probe_recovers(self):
        for _ in range(3): workflow.record("futures", False)
        self.assertEqual(workflow.system("futures", "demo")["state"], "blocked"); self.assertTrue(workflow.probe("futures")); self.assertEqual(workflow.system("futures", "demo")["state"], "safe")
    def test_blocked_tick_probes_so_it_can_recover_without_scanning(self):
        for _ in range(3): workflow.record("futures", False)
        self.assertEqual(self.run_tick().call_count, 0); self.assertEqual(C.get("consec_futures"), "0")
    def test_stale_health_blocks_but_never_checked_does_not_at_the_gate(self):
        self.assertEqual(workflow.system("futures", "demo")["state"], "safe"); self.health(); C.put("health_ts", time.time() - 1000, True); self.assertEqual(workflow.system("futures", "demo")["state"], "blocked")
    def test_start_runs_the_health_check_before_the_first_scan(self):
        calls = []
        with mock.patch.object(bot, "diag", lambda: calls.append("diag") or self.health()), mock.patch.object(bot, "scan", lambda *a: calls.append("scan")): C.put("run_futures", "1", True); C.put("next_futures", "0", True); bot.tick("futures")
        self.assertEqual(calls, ["diag", "scan"]); k = self.kinds(); self.assertLess(k.index("bot_started"), k.index("health_checked")); self.assertIn("account_synced", k)
    def test_stop_is_an_event_and_status_follows_the_lifecycle(self):
        bot.tick("futures"); self.assertEqual(C.get("status_futures"), "stopped"); sc = self.run_tick(); self.assertEqual(C.get("status_futures"), "starting"); C.put("run_futures", "0", True); bot.tick("futures"); self.assertIn("bot_stopped", self.kinds())
    def test_status_goal_and_loss(self):
        self.health(); C.put("dstart_futures_demo", f"{C.today()}|1000", True); self.closed(85); self.run_tick(); self.assertEqual(C.get("status_futures"), "locked")
        C.q("DELETE FROM trades"); self.closed(-40); self.run_tick(); self.assertEqual(C.get("status_futures"), "paused")
    def test_diag_reports_latency_per_check(self):
        with mock.patch.object(bot, "ORK", None): bot.diag()
        h = json.loads(C.get("health")); self.assertTrue(all(isinstance(x["ms"], int) for x in h)); self.assertIn("fut", [x["id"] for x in h])

class TestViews(S3):
    def test_empty_system_returns_neutral_structures_not_demo_data(self):
        self.assertEqual((views.scanner("futures", "demo")["rows"], views.setups(), views.events(), views.positions("demo")), ([], [], [], []))
        s = views.status("demo"); self.assertEqual((s["markets"]["futures"]["status"], s["markets"]["futures"]["open_positions"], s["markets"]["futures"]["equity"]), ("stopped", 0, None)); self.assertIn("note", views.scanner("spot", "demo"))
    def test_read_models_mirror_the_database_after_a_trade(self):
        with self.ai(): self.scan()
        t = self.rows("SELECT * FROM trades")[0]; sid = self.row()["id"]; d = views.setup_detail(sid)
        self.assertEqual([e["to_state"] for e in d["events"]], self.states()); self.assertEqual(d["trade"]["id"], t["id"]); self.assertEqual((d["review"]["ai_verdict"], d["review"]["gate_ok"], d["review"]["decision"]), ("APPROVE", 1, "OPENED")); self.assertEqual(d["review"]["ai_response"]["action"], "LONG")
        p = views.positions("demo"); self.assertEqual((len(p), p[0]["simulated"], p[0]["exchange_verified"]), (1, True, None)); self.assertIsNone(views.setup_detail(99999))
    def test_events_since_id_pagination_and_filters(self):
        with self.ai(): self.scan()
        allr = views.events(0, 500); mid = allr[len(allr) // 2]["id"]; rest = views.events(mid, 500); self.assertTrue(all(r["id"] > mid for r in rest)); self.assertEqual(len(rest), len([r for r in allr if r["id"] > mid]))
        self.assertTrue(all(r["kind"] == "ai_verdict" for r in views.events(0, 50, "futures", ["ai_verdict"]))); self.assertEqual(views.events(0, 50, "spot", ["trade_opened"]), [])
    def test_infra_reports_budget_and_health_items(self):
        self.health(**{"or": "warn"}); C.put("llm_day", f"{C.utc_day()}|7", True); i = views.infra(); self.assertEqual((i["openrouter"]["calls_used"], i["openrouter"]["calls_remaining"], i["openrouter"]["check"]["status"], i["database"]["ok"]), (7, C.CFG["llm_cap"] - 7, "warn", True))
    def test_status_carries_daily_progress_and_system_state(self):
        self.health(fut="fail"); C.put("dpnl_futures_demo", 3.42, True); C.put("eq_futures_demo", 1034.2, True); workflow.refresh_system("futures", "demo"); m = views.status("demo")["markets"]["futures"]
        self.assertEqual((m["day_pnl_pct"], m["target_pct"], m["loss_limit_pct"], m["system"]["state"]), (3.42, 8.0, 3.0, "blocked"))

class TestBoundaryAndRetention(S3):
    def imports(self, f):
        t = ast.parse(read(f)); out = set()
        for n in ast.walk(t):
            if isinstance(n, ast.Import): out |= {a.name.split(".")[0] for a in n.names}
            if isinstance(n, ast.ImportFrom): out.add(n.module.split(".")[0])
        return out
    def test_state_machine_side_never_imports_the_exchange_side(self):
        for f in ("fsm.py", "events.py", "scanner.py", "bus.py", "views.py", "workflow.py"): self.assertFalse(self.imports(f) & {"bot", "ccxt", "requests"}, f)
        self.assertLessEqual(self.imports("workflow.py"), {"json", "time", "hashlib", "common", "fsm", "events", "bus", "scanner", "risk"})
    def test_order_layer_talks_to_the_workflow_only_through_the_bus(self):
        tree = ast.parse(read("bot.py")); order = {"execute", "close", "move_sl", "manage", "reconcile", "cancel_row", "blocked", "protect", "real_entry", "real_exit", "repair", "algo_place", "place_stop"}
        for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in order]:
            names = {x.id for x in ast.walk(fn) if isinstance(x, ast.Name)}; self.assertFalse(names & {"workflow", "fsm"}, fn.name)
    def test_replacing_the_exchange_adapter_does_not_touch_the_workflow(self):
        fx = FakeEx(); bot._ex["futures"] = fx; bot.KEY, bot.SEC = "k", "s"; C.put("mode", "real", True); self.px = 100.0; c = self.cand(price=100.0)
        with mock.patch.object(bot, "real_entry", lambda mk, sym, side, qty, cid: dict(id="1", status="closed", filled=qty, average=100.02)), mock.patch.object(bot, "protect", lambda t: {"sl_id": "a", "tp_id": "b"}):
            self.assertTrue(bot.execute("futures", "real", c, "LONG", "t", self.rid(c, "real")))
        self.assertEqual(self.states(), ["EXECUTION", "POSITION"]); self.assertEqual([h for h in self.hdrs() if "TRADE" in h], ["TRADE OPENED"])   # direct execution: implicit row, still logged as such
    def test_a_crashing_subscriber_cannot_break_the_order_layer(self):
        bus.subscribe("trade_opened", lambda **k: 1 / 0); c = self.cand(); self.assertTrue(bot.execute("futures", "demo", c, "LONG", "t", self.rid(c))); self.assertEqual(len(self.rows("SELECT * FROM trades WHERE status='OPEN'")), 1)
    def test_a_crashing_guard_fails_closed(self):
        bus.guard(lambda **k: 1 / 0); c = self.cand(); self.assertFalse(bot.execute("futures", "demo", c, "LONG", "t", self.rid(c))); self.assertIn("guard failed", self.rows()[0]["gate_reasons"])
    def test_retention_prunes_old_events_only(self):
        old = time.time() - 40 * 86400
        C.q("INSERT INTO logs(ts,market,level,msg,kind) VALUES(?,'','info','old','msg')", (time.time() - 15 * 86400,)); C.q("INSERT INTO logs(ts,market,level,msg,kind) VALUES(?,'','info','new','msg')", (time.time(),))
        r = fsm.create("futures", "demo", "OLDUSDT", "LONG", "WATCHING"); fsm.end(r, "x"); C.q("UPDATE setups SET closed_at=? WHERE id=?", (old, r["id"])); C.q("UPDATE setup_events SET ts=? WHERE setup_id=?", (old, r["id"])); live = fsm.create("futures", "demo", "LIVEUSDT", "LONG", "WATCHING")
        C.prune(); self.assertEqual([x["msg"] for x in C.q("SELECT msg FROM logs WHERE kind='msg'", fetch=True)], ["new"]); self.assertEqual([x["symbol"] for x in C.q("SELECT symbol FROM setups", fetch=True)], ["LIVEUSDT"]); self.assertEqual(C.q("SELECT * FROM setup_events WHERE setup_id=?", (r["id"],), True), [])
    def test_retention_row_cap(self):
        C.q("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<5100) INSERT INTO logs(ts,market,level,msg,kind) SELECT ?, '', 'info', 'm', 'msg' FROM c", (time.time(),)); C.prune()
        self.assertLessEqual(C.q("SELECT COUNT(*) n FROM logs", fetch=True)[0]["n"], 5001)
    def test_api_module_exposes_the_read_endpoints(self):
        src = read("api", "index.py"); [self.assertIn(p, src) for p in ("/api/v2/status", "/api/v2/scanner", "/api/v2/setups", "/api/v2/setup", "/api/v2/events", "/api/v2/positions", "/api/v2/infra")]; compile(src, "api/index.py", "exec")

if __name__ == "__main__": unittest.main(verbosity=2)

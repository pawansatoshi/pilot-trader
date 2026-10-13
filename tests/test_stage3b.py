"""Stage 3B: terminal read model (views.dashboard & co), static front-end structure, JS suites, and a backend -> frontend contract test.
Fake exchanges only. FastAPI is not installed in the sandbox: the HTTP layer is checked structurally, the data functions behind it are tested."""
import os, re, sys, json, time, shutil, tempfile, subprocess, unittest
from unittest import mock
sys.path[:0] = [os.path.dirname(os.path.abspath(__file__)), os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
import common as C, bot, fsm, workflow, views
from test_stage12 import SYM, FLT, series, FakeEx
from test_stage3a import S3
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def read(*p):
    with open(os.path.join(ROOT, *p)) as f: return f.read()
HAVE_NODE = shutil.which("node") is not None
FIX = tempfile.mkdtemp(prefix="dash_fixtures_")

class D3B(S3):
    def dash(self, mode="demo"): d = views.dashboard(mode); json.dumps(d); return d      # must always be JSON-serializable
    def m(self, mk="futures", mode="demo"): return self.dash(mode)["markets"][mk]
    def dump(self, name):
        d = self.dash()
        for dest in (FIX, os.environ.get("DASH_FIXTURE_DIR")):
            if dest:
                os.makedirs(dest, exist_ok=True)
                with open(os.path.join(dest, name + ".json"), "w") as f: json.dump(d, f)
    def start(self, mk="futures"): C.put("run_" + mk, "1", True); C.put("started_" + mk, time.time() - 5, True); self.health(); C.put("health_ts", time.time(), True)
    def review(self, c=None, mode="demo", mk="futures"): return bot.new_review(mk, mode, c or self.cand(), "LONG", "m1", {"symbol": SYM, "action": "LONG", "confidence": .8, "reason": "trend and volume agree"}, "APPROVE", .8, 1500)
    def tick(self, mk="futures"):   # what the worker does every 30 s (heartbeat, balance, daily limits, status), without a scan or a health check
        C.put("hb", time.time(), True)
        with mock.patch.object(bot, "scan"), mock.patch.object(bot, "diag", lambda: C.put("health_ts", time.time(), True)): bot.tick(mk)   # diag keeps the current results, only stamps "checked now"
    def steps(self, m): return {s["code"]: s["state"] for s in m["workflow"]["steps"]}
    def open_trade(self):
        with self.ai(): self.scan()
        return self.rows("SELECT * FROM trades")[0]
    def close_tp(self, t):
        self.m1 = [[time.time() * 1000 + 60000, t["entry"], t["tp"] * 1.001, t["entry"] * .999, t["tp"], 1]]; bot.manage(dict(t))

class TestDashboardStates(D3B):
    def test_empty_stopped_state_shows_no_invented_data(self):
        d = self.dash(); m = d["markets"]["futures"]; self.assertEqual((m["status"]["code"], m["running"], m["balance"], m["rows"], m["positions"], m["active_setup"], m["timeline"], m["lock"], m["blocked"], m["last_outcome"]), ("stopped", False, None, [], [], None, [], None, None, None))
        self.assertEqual(m["workflow"]["stage"], "STOPPED"); self.assertTrue(all(s["state"] == "off" or s["state"] == "pending" for s in m["workflow"]["steps"])); self.assertIn("no scan has completed", m["scan"]["note"]); self.assertEqual(d["infra"]["binance"]["futures"]["label"], "UNKNOWN"); self.assertEqual(d["infra"]["worker"]["label"], "UNKNOWN"); self.dump("01_empty")
    def test_starting_then_scanning_no_setup(self):
        C.put("run_futures", "1", True); C.put("started_futures", time.time(), True); C.put("status_futures", "starting", True); m = self.m(); self.assertEqual((m["workflow"]["stage"], self.steps(m)["START"], self.steps(m)["HEALTH"]), ("HEALTH", "done", "active"))
        self.health(); C.put("health_ts", time.time() + 1, True); k = self.k15; self.k15 = series(flat=True); self.scan(); C.put("status_futures", "scanning", True); m = self.m()
        self.assertEqual((m["workflow"]["stage"], self.steps(m)["HEALTH"], self.steps(m)["SCANNING"], self.steps(m)["SETUP"]), ("SCANNING", "done", "active", "pending")); self.assertEqual((m["scan"]["stats"]["checked"], m["scan"]["stats"]["qualifying"]), (1, 0)); self.assertEqual(m["active_setup"], None); self.dump("02_scanning")
    def test_candidate_is_a_setup_not_a_trade(self):
        self.start(); c = dict(C.tech(SYM, "futures"), ts=time.time()); self.assertEqual(workflow.classify("futures", "demo", [c], [c]), [c]); workflow.write_snapshot("futures", "demo")   # the real scanner path, stopped before the AI step
        m = self.m(); s = m["active_setup"]; self.assertIsNotNone(s["levels"]["entry"]); self.assertTrue(m["rows"][0]["levels"]["indicative"]); self.assertEqual((s["state"], s["state_label"], s["is_trade"], s["headline"], s["levels"]["kind"]), ("CANDIDATE", "SETUP DETECTED", False, "SETUP, NOT A TRADE YET", "indicative")); self.assertEqual(m["positions"], [])
        st = self.steps(m); self.assertEqual((m["workflow"]["stage"], st["SETUP"], st["AI"], st["EXECUTION"], st["POSITION"]), ("SETUP", "active", "pending", "pending", "pending")); self.dump("03_candidate")
    def test_ai_review_and_risk_check_states(self):
        self.start(); row = self.mk_row("AI_REVIEW"); self.review(); m = self.m(); st = self.steps(m); self.assertEqual((m["workflow"]["stage"], st["SETUP"], st["AI"], st["RISK"]), ("AI", "done", "active", "pending")); self.assertEqual(m["active_setup"]["ai"]["verdict"], "APPROVE")
        fsm.move(row, "RISK_CHECK", "x"); m = self.m(); st = self.steps(m); self.assertEqual((m["workflow"]["stage"], st["AI"], st["RISK"], st["EXECUTION"]), ("RISK", "done", "active", "pending"))
    def test_execution_fill_and_protection_are_real_events(self):
        self.start(); row = self.mk_row("EXECUTION"); self.review(); m = self.m(); self.assertEqual((m["workflow"]["stage"], self.steps(m)["FILL"]), ("EXECUTION", "pending"))
        C.log("submitted", kind="order_submitted", mk="futures", symbol=SYM); m = self.m(); self.assertEqual((m["workflow"]["stage"], self.steps(m)["FILL"]), ("FILL", "active"))
        C.log("filled", kind="fill_confirmed", mk="futures", symbol=SYM); m = self.m(); self.assertEqual((m["workflow"]["stage"], self.steps(m)["FILL"], self.steps(m)["PROTECTION"]), ("PROTECTION", "done", "active"))
        C.log("protected", kind="protection_confirmed", mk="futures", symbol=SYM); st = self.steps(self.m()); self.assertEqual((st["PROTECTION"], st["POSITION"]), ("done", "pending")); self.assertEqual(self.m()["positions"], [])   # still no trade until the backend says POSITION
    def test_open_position_is_the_backend_trade(self):
        self.start(); t = self.open_trade(); self.tick(); m = self.m(); p = m["positions"][0]; s = m["active_setup"]
        self.assertEqual((p["id"], p["symbol"], p["side"], p["entry"], p["sl"], p["tp"], p["qty"], p["simulated"]), (t["id"], SYM, "LONG", t["entry"], t["sl"], t["tp"], t["qty"], True)); self.assertEqual((s["is_trade"], s["headline"], s["levels"]["kind"], s["levels"]["entry"]), (True, "TRADE OPEN", "actual", t["entry"]))
        st = self.steps(m); self.assertEqual((m["workflow"]["stage"], [st[x] for x in ("SETUP", "AI", "RISK", "EXECUTION", "FILL", "PROTECTION", "POSITION")]), ("POSITION", ["done"] * 6 + ["active"])); self.assertEqual(s["ai"]["model"], "test-model"); self.assertEqual(m["status"]["code"] in ("scanning", "trading", "stopped", "starting"), True); self.dump("04_position")
    def test_closed_trade_outcome_and_return_to_scanning(self):
        self.start(); t = self.open_trade(); self.close_tp(t); self.tick(); m = self.m(); r = self.rows("SELECT r_mult, pnl FROM trades")[0]
        self.assertIsNone(m["active_setup"]); self.assertEqual(m["positions"], []); self.assertEqual((m["last_outcome"]["symbol"], round(m["last_outcome"]["r"], 6), m["last_outcome"]["reason"]), (SYM, round(r["r_mult"], 6), "TP")); st = self.steps(m); self.assertEqual((m["workflow"]["stage"], st["SCANNING"], st["CLOSED"], st["OUTCOME"]), ("SCANNING", "active", "done", "done")); self.dump("05_closed")
    def test_blocked_state_exact_reasons_and_positions_still_listed(self):
        self.start(); self.open_trade(); self.health(fut="fail"); self.tick(); m = self.m()
        self.assertEqual((m["blocked"]["label"], m["blocked"]["note"], m["system_state"], m["status"]["code"]), ("NEW TRADES BLOCKED", "Existing positions are still being managed.", "blocked", "paused")); self.assertIn("fut is fail", m["blocked"]["reasons"][0]["msg"]); self.assertEqual(m["blocked"]["reasons"][0]["fix"], "do the fix"); self.assertEqual(len(m["positions"]), 1); self.dump("06_blocked")
    def test_target_locked_exact_label_and_state(self):
        self.health(); self.day(); self.closed(85); C.put("run_futures", "1", True); C.put("health_ts", time.time() + 1e6, True)
        self.tick()
        m = self.m(); self.assertEqual((m["lock"]["code"], m["lock"]["label"], m["status"]["code"], m["target"]["reached"], m["target"]["progress"]), ("TARGET_REACHED", "TARGET REACHED — NEW TRADES LOCKED", "locked", True, 1.0)); self.assertIn("normal risk management", m["lock"]["detail"]); self.assertGreater(m["day_pnl_abs"], 80); self.dump("07_locked")
    def test_loss_limit_exact_label_and_state(self):
        self.health(); self.day(); self.closed(-40); C.put("run_futures", "1", True); C.put("health_ts", time.time() + 1e6, True)
        self.tick()
        m = self.m(); self.assertEqual((m["lock"]["code"], m["lock"]["label"], m["status"]["code"], m["protection"]["hit"], m["protection"]["used"]), ("LOSS_LIMIT", "DAILY LOSS LIMIT — NEW TRADES PAUSED", "paused", True, 1.0)); self.assertLess(m["day_pnl_pct"], -3); self.dump("08_loss")
    def test_progress_meters_are_proportional(self):
        C.put("dpnl_futures_demo", 4.0, True); C.put("eq_futures_demo", 1040, True); self.day(); m = self.m(); self.assertEqual((m["target"]["progress"], m["target"]["reached"], m["protection"]["used"]), (0.5, False, 0.0)); C.put("dpnl_futures_demo", -1.5, True); m = self.m(); self.assertEqual((m["target"]["progress"], m["protection"]["used"]), (0.0, 0.5))
    def test_demo_and_real_views_are_separate_and_flagged(self):
        self.start(); self.open_trade(); self.assertEqual(len(self.m()["positions"]), 1); r = views.dashboard("real"); self.assertEqual((r["mode"], r["mode_banner"]["real"], r["markets"]["futures"]["positions"]), ("real", True, [])); self.assertTrue(r["mode_banner"]["label"].startswith("REAL")); self.assertTrue(self.dash()["mode_banner"]["label"].startswith("DEMO"))
        with mock.patch.object(C, "real_ok", lambda: False): self.assertTrue(views.dashboard("real")["mode_banner"]["real_locked"])
    def test_infra_tiles_reflect_real_checks(self):
        self.health(tg="fail", **{"or": "warn"}); C.put("llm_day", f"{C.utc_day()}|9", True); C.put("model", "x/free", True); C.put("llm_block", time.time() + 600, True); i = self.dash()["infra"]
        self.assertEqual((i["telegram"]["label"], i["openrouter"]["label"], i["openrouter"]["calls_remaining"], i["openrouter"]["model"], i["openrouter"]["state"], i["binance"]["futures"]["label"], i["database"]["label"]), ("FAILED", "WARNING", C.CFG["llm_cap"] - 9, "x/free", "paused", "OK", "CONNECTED"))
        C.put("health", json.dumps([dict(id="tg", name="tg", status="skip", msg="Not set")]), True); self.assertEqual(self.dash()["infra"]["telegram"]["label"], "NOT CONFIGURED")
        C.put("hb", time.time() - 600, True); self.assertEqual(self.dash()["infra"]["worker"]["label"], "OFFLINE"); C.put("hb", time.time(), True); self.assertEqual(self.dash()["infra"]["worker"]["label"], "ALIVE")
    def test_scanner_rows_carry_indicative_levels_from_the_real_plan(self):
        with self.ai("SKIP"): self.scan()
        r = [x for x in self.m()["scan"] and self.m()["rows"] if x["symbol"] == SYM][0]; lv = r["levels"]; self.assertTrue(lv["indicative"]); self.assertLess(lv["sl"], lv["entry"]); self.assertLess(lv["entry"], lv["tp"]); self.assertGreater(lv["eff_rr"], 1)
    def test_nothing_in_the_model_exists_outside_the_database(self):
        self.start(); t = self.open_trade(); m = self.m(); self.assertEqual({p["id"] for p in m["positions"]}, {x["id"] for x in self.rows("SELECT id FROM trades WHERE status='OPEN'")})
        ids = {x["id"] for x in C.q("SELECT id FROM logs", fetch=True)}; self.assertTrue(all(e["id"] in ids for e in m["timeline"])); self.assertTrue(set(r["symbol"] for r in m["rows"]) <= {SYM})

class TestTimelineMapping(D3B):
    def test_setup_then_trade_are_distinct_labelled_events_in_order(self):
        self.open_trade(); tl = views.timeline("futures", "demo", 0, 100); lab = [e["label"] for e in tl]; self.assertEqual(lab.index("SETUP DETECTED") < lab.index("AI REVIEW") < lab.index("RISK CHECK") < lab.index("ORDER SUBMITTED") < lab.index("FILL CONFIRMED") < lab.index("PROTECTION CONFIRMED") < lab.index("TRADE OPENED"), True)
        for want in ("MARKET SCAN", "EVIDENCE", "AI VERDICT", "EXECUTION", "POSITION"): self.assertIn(want, lab)
        self.assertTrue(all(tl[i]["id"] < tl[i + 1]["id"] for i in range(len(tl) - 1))); self.assertNotEqual(next(e for e in tl if e["label"] == "SETUP DETECTED")["kind"], next(e for e in tl if e["label"] == "TRADE OPENED")["kind"])
    def test_close_and_outcome_events_and_stop_moves(self):
        t = self.open_trade(); C.log(f"Stop-loss of {SYM} moved", mk="futures", kind="stop_moved", symbol=SYM, trade_id=t["id"], data=dict(new_sl=t["entry"])); self.close_tp(t); lab = [e["label"] for e in views.timeline("futures", "demo", 0, 200)]
        for want in ("STOP MOVED", "TRADE CLOSED", "OUTCOME", "COOLDOWN", "SETUP DETECTED"): self.assertIn(want, lab)
        by = {e["kind"]: e for e in views.timeline("futures", "demo", 0, 200)}; self.assertTrue(by["trade_closed"]["simulated"] and by["outcome_recorded"]["simulated"] and by["stop_moved"]["simulated"])   # demo P&L is simulated and must say so
        self.assertFalse(any(e["simulated"] for e in views.timeline("futures", "demo", 0, 200) if e["kind"] in ("scan_done", "bot_started", "ai_verdict")))
        self.assertLess(lab.index("TRADE CLOSED"), lab.index("OUTCOME"))
    def test_demo_events_are_flagged_simulated_and_system_events_are_not(self):
        self.start(); C.log("Futures bot started", kind="bot_started", mk="futures"); self.open_trade(); tl = views.timeline("futures", "demo", 0, 200); by = {e["kind"]: e for e in tl}
        self.assertTrue(all(by[k]["simulated"] for k in ("order_submitted", "fill_confirmed", "protection_confirmed", "trade_opened"))); self.assertFalse(by["bot_started"]["simulated"]); self.assertFalse(by["scan_done"]["simulated"]); self.assertIn("[DEMO simulated]", by["fill_confirmed"]["text"])
    def test_events_of_another_mode_are_left_out(self):
        self.open_trade(); self.assertNotIn("trade_opened", [e["kind"] for e in views.timeline("futures", "real", 0, 200)]); self.assertIn("trade_opened", [e["kind"] for e in views.timeline("futures", "demo", 0, 200)])
    def test_plain_messages_only_when_they_are_warnings_or_errors(self):
        C.log("just chatter", mk="futures"); C.log("something failed", mk="futures", level="err"); k = [e["text"] for e in views.timeline("futures", None, 0, 50)]; self.assertEqual(k, ["something failed"]); self.assertEqual(views.timeline("futures", None, 0, 50)[0]["label"], "ALERT"); self.assertEqual(len(views.timeline("futures", None, 0, 50, include_msgs=True)), 2)
    def test_events_whose_data_is_a_list_do_not_break_the_timeline(self):   # health_checked stores the list of checks
        C.log("Health check: 7 ok", kind="health_checked", mk="futures", data=[dict(id="fut", status="ok")]); e = views.timeline("futures", None, 0, 10)[0]; self.assertEqual((e["label"], e["simulated"], isinstance(e["data"], list)), ("HEALTH CHECK", False, True))
    def test_since_id_and_limit(self):
        for i in range(5): C.log(f"e{i}", kind="scan_done", mk="futures")
        allr = views.timeline("futures", None, 0, 100); self.assertEqual(len(views.timeline("futures", None, allr[1]["id"], 100)), 3); self.assertEqual(len(views.timeline("futures", None, 0, 2)), 2)

class TestAiAndTradeViews(D3B):
    def test_only_existing_ai_fields_are_exposed(self):
        self.open_trade(); a = self.m()["active_setup"]["ai"]; self.assertEqual((a["verdict"], a["model"], a["decision"], a["status"], a["gate_ok"]), ("APPROVE", "test-model", "OPENED", "approved by AI, risk gate passed, trade opened", 1)); self.assertEqual((a["grade"], a["case_for"], a["case_against"], a["fatal_flaw"], a["invalidation"]), (None, None, None, None, None)); self.assertEqual(a["reason"], "test"); self.assertIn("cannot bypass the risk gate", a["authority"]); self.assertIsInstance(a["confidence_pct"], int)
    def test_extra_fields_appear_when_the_ai_really_returned_them(self):
        rv = dict(ai_response={"action": "LONG", "confidence": .9, "reason": "r", "grade": "A", "case_against": ["funding high"], "fatal_flaw": None}, ai_verdict="APPROVE", confidence=.9, model="m", prompt_ver="v", ai_ms=900, decision="PENDING", gate_ok=None, gate_reasons=None)
        a = views.ai_view(rv); self.assertEqual((a["grade"], a["case_against"], a["latency_ms"], a["status"], a["confidence_pct"]), ("A", ["funding high"], 900, "in review", 90)); self.assertIsNone(views.ai_view(None)); self.assertIsNone(views.ai_view(dict(rv, model="-"))["model"])
    def test_every_decision_has_a_readable_status(self):
        for k in ("PENDING", "OPENED", "GATE_REJECT", "AI_REJECT", "AI_INVALID", "AI_UNAVAILABLE_SKIP", "EXEC_FAIL"): self.assertTrue(views.DECISION_STATUS[k])
    def test_rejected_setups_are_listed_with_their_real_reason(self):
        with self.ai("SKIP"): self.scan()
        r = self.m()["rejections"]; self.assertEqual((len(r), r[0]["symbol"], r[0]["from_state"]), (1, SYM, "AI_REVIEW")); self.assertIn("AI reject", r[0]["reason"]); self.assertEqual(views.recent_rejections("spot", "demo"), [])
    def test_trades_view_has_markers_data_and_stop_moves(self):
        t = self.open_trade(); C.log("moved", mk="futures", kind="stop_moved", symbol=SYM, trade_id=t["id"], data=dict(new_sl=t["entry"])); self.close_tp(t); v = views.trades("futures", "demo", SYM); r = v[0]
        self.assertEqual((r["status"], r["reason"], r["simulated"], len(r["stop_moves"]), r["stop_moves"][0]["new_sl"]), ("CLOSED", "TP", True, 1, t["entry"])); self.assertGreater(r["r_mult"], 1.5); self.assertEqual(views.trades("futures", "demo", "OTHERUSDT"), []); self.assertEqual(views.trades("spot", "demo"), [])

class TestFrontendStatic(unittest.TestCase):
    def test_html_has_every_panel_exactly_once(self):
        h = read("public", "index.html"); panels = re.findall(r'data-panel="(\w+)"', h); self.assertEqual(sorted(panels), sorted(["brand", "banners", "status", "pnl", "scanner", "health", "chart", "levels", "position", "setup", "ai", "timeline"])); self.assertIn('type="module" src="js/app.js"', h); self.assertIn('name="viewport"', h)
    def test_mobile_priority_order_is_encoded_in_css(self):
        css = read("public", "app.css"); m = re.search(r"@media \(max-width:899px\)\{(.*)\n\}", css, re.S); self.assertIsNotNone(m); mob = m.group(1)
        order = {k: int(v) for k, v in re.findall(r'\[data-panel="(\w+)"\]\{order:(\d+)\}', mob)}; want = ["brand", "banners", "status", "pnl", "position", "chart", "levels", "setup", "ai", "timeline", "health", "scanner"]
        self.assertEqual(sorted(order, key=order.get), want)   # status, balance/P&L, position, chart, entry/SL/TP, setup, AI, timeline, health
        self.assertIn(".top,.col{display:contents}", mob); self.assertIn("flex-direction:column", mob)
    def test_desktop_and_tablet_layouts_exist(self):
        css = read("public", "app.css"); self.assertIn("grid-template-columns:360px minmax(0,1fr) 380px", css); self.assertRegex(css, r"@media \(max-width:1199px\) and \(min-width:900px\)"); self.assertIn("prefers-reduced-motion", css); self.assertIn("overflow-x:auto", css)
    def test_frontend_cannot_create_trades_or_orders(self):
        js = "".join(read("public", "js", f) for f in os.listdir(os.path.join(ROOT, "public", "js")) if f.endswith(".js")); posts = set(re.findall(r"api\('(/api/[\w/]+)',", js)); self.assertEqual(posts, {"/api/market", "/api/mode"}); self.assertIn("/api/login", js)
        for bad in ("closeall", "reset", "create_order", "createOrder", "newOrder", "/fapi/v1/order", "api/v3/order", "risk_gate", "localStorage", "Math.random"): self.assertNotIn(bad, js, bad)
        self.assertTrue(re.findall(r"fetch\(", js)); self.assertNotRegex(js, r"method:\s*'(PUT|DELETE)'")
    def test_only_public_binance_market_data_endpoints_are_used(self):
        feed = read("public", "js", "feed.js"); self.assertEqual(set(re.findall(r"https://[\w.]+/[\w/]*klines", feed)), {"https://fapi.binance.com/fapi/v1/klines", "https://api.binance.com/api/v3/klines"}); self.assertEqual(set(re.findall(r"wss://[\w.:]+/ws/", feed)), {"wss://fstream.binance.com/ws/", "wss://stream.binance.com:9443/ws/"})
        for f in os.listdir(os.path.join(ROOT, "public", "js")):
            if f.endswith(".js"): self.assertNotIn("WebSocketServer", read("public", "js", f))
    def test_no_fake_activity_generators_in_the_frontend(self):
        js = "".join(read("public", "js", f) for f in os.listdir(os.path.join(ROOT, "public", "js")) if f.endswith(".js")); [self.assertNotIn(x, js) for x in ("random()", "faker", "mock", "demoData", "sampleData", "setInterval(() => S.live")]
    def test_routes_and_deployment_config(self):
        api = read("api", "index.py"); [self.assertIn(p, api) for p in ("/api/v2/dashboard", "/api/v2/trades", "/api/v2/timeline", "/api/v2/status", "/api/v2/scanner", "/api/v2/setups", "/api/v2/setup", "/api/v2/events", "/api/v2/positions", "/api/v2/infra", '"/legacy"', "StaticFiles")]; compile(api, "api/index.py", "exec")
        vc = json.loads(read("vercel.json")); self.assertEqual([r["source"] for r in vc["rewrites"]], ["/api/(.*)", "/legacy"]); self.assertNotIn("/(.*)", [r["source"] for r in vc["rewrites"]])   # static terminal at /, API + legacy page through the function
    def test_every_write_endpoint_the_ui_uses_still_requires_login(self):
        api = read("api", "index.py")
        for name in ("mode", "market"): self.assertRegex(api, rf"async def {name}\(r: Request\):\n    if guard\(r\): return guard\(r\)")
        for ep in ("v_dashboard", "v_trades", "v_timeline", "v_status", "v_scanner", "v_setups", "v_setup", "v_events", "v_positions", "v_infra"): body = re.search(rf"def {ep}\(.*?(?=\n@app|\n# Local development|\Z)", api, re.S).group(0); self.assertIn("guard(r)", body, ep)

@unittest.skipUnless(HAVE_NODE, "node not installed")
class TestNodeSuites(unittest.TestCase):
    def test_javascript_unit_tests(self):
        r = subprocess.run(["node", "--test", "tests/js/*.test.mjs"], cwd=ROOT, capture_output=True, text=True); n = re.search(r"# pass (\d+)", r.stdout); f = re.search(r"# fail (\d+)", r.stdout)
        self.assertEqual((r.returncode, f and f.group(1)), (0, "0"), r.stdout[-1500:]); self.assertGreaterEqual(int(n.group(1)), 36)
    def test_zz_backend_to_frontend_contract(self):
        files = sorted(os.listdir(FIX)); self.assertGreaterEqual(len(files), 8, files)    # fixtures are dumped by the TestDashboardStates tests (real backend output)
        r = subprocess.run(["node", "tests/js/contract_check.mjs", FIX], cwd=ROOT, capture_output=True, text=True); self.assertEqual(r.returncode, 0, r.stderr); out = json.loads(r.stdout)
        for name, v in out.items(): self.assertEqual(v["issues"], [], name)
        F = lambda n, k: out[n]["flags"]["futures"][k]
        self.assertIn("No scan has run yet" if False else "Bot stopped", F("01_empty", "setup")); self.assertIn("Bot stopped: no scan has run yet", F("01_empty", "scan"))
        self.assertIn("SETUP, NOT A TRADE YET", F("03_candidate", "setup")); self.assertNotIn("TRADE OPEN<", F("03_candidate", "setup")); self.assertIn("INDICATIVE", F("03_candidate", "levels")); self.assertIn("No open position", F("03_candidate", "position"))
        p = F("04_position", "position"); self.assertIn("DEMO: simulated", p); self.assertIn("+", p); self.assertIn("ACTUAL", F("04_position", "levels")); self.assertIn("TRADE OPEN", F("04_position", "setup")); self.assertIn("TRADE OPENED", F("04_position", "timeline")); self.assertIn("pill demo", F("04_position", "timeline"))
        self.assertIn("Last outcome", F("05_closed", "position")); self.assertIn("OUTCOME", F("05_closed", "timeline"))
        b = F("06_blocked", "banners"); self.assertIn("NEW TRADES BLOCKED", b); self.assertIn("Existing positions are still being managed.", b); self.assertIn("SYSTEM: BLOCKED", F("06_blocked", "health")); self.assertIn("SOLUSDT" if False else SYM, F("06_blocked", "position"))
        self.assertIn("TARGET REACHED — NEW TRADES LOCKED", F("07_locked", "banners")); self.assertIn("REACHED", F("07_locked", "pnl")); self.assertIn("DAILY LOSS LIMIT — NEW TRADES PAUSED", F("08_loss", "banners")); self.assertIn("LIMIT HIT", F("08_loss", "pnl"))
        self.assertNotIn("TARGET REACHED", F("08_loss", "banners")); self.assertNotIn("DAILY LOSS LIMIT", F("07_locked", "banners"))

if __name__ == "__main__": unittest.main(verbosity=2)

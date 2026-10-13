import test from 'node:test'; import assert from 'node:assert/strict';
import * as R from '../../public/js/render.js';
import { dash, market, tile, pos, setup, ai } from './helpers.mjs';
const has = (s, ...x) => x.forEach(t => assert.ok(s.includes(t), `missing "${t}" in ${s.slice(0, 300)}`)); const lacks = (s, ...x) => x.forEach(t => assert.ok(!s.includes(t), `unexpected "${t}"`));
const f = (m, d) => R.banners(dash(m, d), 'futures');
test('clean state shows no banners', () => assert.equal(f({}), ''));
test('target locked banner uses the backend label', () => { const s = f({ lock: { code: 'TARGET_REACHED', label: 'TARGET REACHED — NEW TRADES LOCKED', detail: 'Open positions keep following normal risk management.' } }); has(s, 'TARGET REACHED — NEW TRADES LOCKED', 'Open positions keep following normal risk management') });
test('loss limit banner', () => has(f({ lock: { code: 'LOSS_LIMIT', label: 'DAILY LOSS LIMIT — NEW TRADES PAUSED', detail: 'd' } }), 'DAILY LOSS LIMIT — NEW TRADES PAUSED'));
test('blocked banner: exact reasons, fixes, and the "still managed" note', () => { const s = f({ blocked: { label: 'NEW TRADES BLOCKED', note: 'Existing positions are still being managed.', reasons: [{ msg: 'Binance futures market data: timeout', fix: 'Retry in a minute.' }] } }); has(s, 'NEW TRADES BLOCKED', 'Existing positions are still being managed.', 'Binance futures market data: timeout', 'Fix: Retry in a minute.') });
test('degraded, real and worker-offline banners', () => { has(f({ degraded: [{ msg: 'OpenRouter AI: key missing' }] }), 'DEGRADED', 'trading continues'); has(f({}, { mode_banner: { label: 'REAL: live money', real: true, real_locked: false } }), 'REAL MODE', 'live money'); const w = dash(); w.infra.worker = { label: 'OFFLINE', ok: false, heartbeat_age: 600 }; has(R.banners(w, 'futures'), 'WORKER OFFLINE', 'Last signal 10 min ago') });
test('stepper renders the 12 backend steps with their states', () => { const s = R.stepper(market()); assert.equal((s.match(/class="step /g) || []).length, 12); has(s, 'step done', 'step active', 'step pending', 'STAGE', 'SCANNING', 'Market is quiet') });
test('pnl panel: target reached and protection hit', () => {
  has(R.pnlPanel(market({ day_pnl_abs: 85, day_pnl_pct: 8.5, target: { pct: 8, progress: 1, reached: true } })), 'REACHED', '+8.50%', 'meter goal hit'); has(R.pnlPanel(market({ day_pnl_abs: -31, day_pnl_pct: -3.1, protection: { pct: 3, used: 1, hit: true } })), 'LIMIT HIT: new trades paused', '-3.10%');
  has(R.pnlPanel(market()), 'TARGET +8%', 'PROTECTION -3%', '0% used');
});
test('scanner: no scan yet, nothing qualifying, and a populated table', () => {
  has(R.scanPanel(market({ running: false }), { now: 1 }), 'Bot stopped: no scan has run yet'); has(R.scanPanel(market(), { now: 1 }), 'Scan running: no scan has completed yet');
  const quiet = market({ scan: { stats: { seq: 3, checked: 42, qualifying: 0, seconds: 18.4 }, phase: 'idle', next_scan_ts: 130, counts: {} }, rows: [{ symbol: 'BTCUSDT', state: 'WAITING' }] }); has(R.scanPanel(quiet, { now: 100 }), '42</b> checked', '0</b> qualifying', 'scan 18.4s', 'data-countdown="130"', '0 qualifying setups, nothing is developing'); lacks(R.scanPanel(quiet, { now: 100 }), 'data-symbol="BTCUSDT"');
  has(R.scanPanel(quiet, { now: 100, showAll: true }), 'data-symbol="BTCUSDT"');
  const rows = [{ symbol: 'SOLUSDT', state: 'CANDIDATE', direction: 'LONG', reason: 'aligned', levels: { entry: 100, sl: 98.7, tp: 102.6 } }, { symbol: 'ETHUSDT', state: 'NEAR_SETUP', direction: 'SHORT', reason: 'close' }];
  const s = R.scanPanel(market({ scan: { stats: { seq: 1, checked: 2, qualifying: 1, seconds: 3 }, phase: 'idle', next_scan_ts: 9, counts: {} }, rows, rejections: [{ ts: 1700000000, symbol: 'XRPUSDT', direction: 'LONG', from_state: 'AI_REVIEW', reason: 'AI reject: weak' }] }), { now: 1 });
  has(s, 'SETUP DETECTED', 'NEAR SETUP', '100.0000 / 98.7000 / 102.6000', 'indicative', 'Rejected setups', 'XRPUSDT', 'from AI REVIEW', 'AI reject: weak', '1</b> rejected (24h)');
});
test('SETUP DETECTED is not TRADE OPENED: setup panel vs trade panel', () => {
  const a = R.setupPanel(market({ active_setup: setup() })); has(a, 'SETUP, NOT A TRADE YET', 'No order has been sent', 'issetup', 'SETUP DETECTED'); lacks(a, 'TRADE OPEN');
  const b = R.setupPanel(market({ active_setup: setup({ state: 'POSITION', is_trade: true, headline: 'TRADE OPEN' }) })); has(b, 'TRADE OPEN', 'istrade'); lacks(b, 'No order has been sent', 'NOT A TRADE');
  has(R.setupPanel(market()), 'No setup under review. The bot is scanning.'); has(R.setupPanel(market({ running: false })), 'Bot stopped.');
});
test('levels: INDICATIVE for a setup, ACTUAL for a position', () => {
  const a = R.levelsPanel(market({ active_setup: setup() }), null); has(a, 'INDICATIVE', 'not an order', '1.70'); const b = R.levelsPanel(market({ positions: [pos()] }), null); has(b, 'ACTUAL', 'from the exchange fill', 'No live price'); lacks(b, 'INDICATIVE'); has(R.levelsPanel(market(), null), 'No position and no setup with levels');
});
test('position panel: live P&L and R from the live price, honest without one, demo flag', () => {
  const m = market({ positions: [pos()] }); const live = R.positionPanel(m, { symbol: 'SOLUSDT', price: 103 }); has(live, '+6.00 USDT', '+1.50R', 'DEMO: simulated', 'live Binance price', 'before fees'); const none = R.positionPanel(m, { symbol: 'BTCUSDT', price: 5 }); has(none, 'no live price'); lacks(none, '+6.00');
  has(R.positionPanel(market({ positions: [pos({ simulated: false, exchange_verified: false })] }), null), 'REAL', 'exchange state unverified'); has(R.positionPanel(market({ positions: [pos({ stop_moves: [{ ts: 1, new_sl: 100 }] })] }), null), 'Stop moved 1x');
  has(R.positionPanel(market(), null), 'No open position'); has(R.positionPanel(market({ last_outcome: { symbol: 'SOLUSDT', r: 1.8, pnl: 18, reason: 'TP' } }), null), 'Last outcome', '+1.80R');
});
test('AI panel shows only what exists', () => {
  const s = R.aiPanel(market({ active_setup: setup({ ai: ai() }) })); has(s, 'VERDICT APPROVE', '78%', 'trend and volume agree', 'm/free', '2,300 ms', 'not provided', 'cannot change size, leverage, entry, stop or target'); lacks(s, 'GRADE');
  has(R.aiPanel(market({ active_setup: setup({ ai: ai({ grade: 'A', case_against: ['funding high'], fatal_flaw: 'none', gate_ok: 0, gate_reasons: 'RR: low' }) }) })), 'GRADE A', 'funding high', 'rejected: RR: low'); has(R.aiPanel(market({ active_setup: setup() })), 'No AI review for the current setup yet'); has(R.aiPanel(market()), 'No AI review');
});
test('timeline: real timestamps, labels, DEMO tag, html escaped, empty state', () => {
  const tl = [{ id: 1, ts: 1700000000, kind: 'setup_state_changed', label: 'SETUP DETECTED', group: 'setup', level: 'info', text: 'SOL', simulated: false }, { id: 2, ts: 1700000100, kind: 'trade_opened', label: 'TRADE OPENED', group: 'trade', level: 'ok', text: '<b>x</b>', simulated: true }];   // backend order: oldest first
  const s = R.timelinePanel(market({ timeline: tl })); has(s, '22:13:20', 'TRADE OPENED', 'SETUP DETECTED', 'pill demo', '&lt;b&gt;x&lt;/b&gt;'); lacks(s, '<b>x</b>'); assert.ok(s.indexOf('TRADE OPENED') < s.indexOf('SETUP DETECTED'), 'newest first'); has(R.timelinePanel(market()), 'No events yet');
});
test('health: blocked label, tiles, fixes, not-configured', () => {
  const d = dash({ system_state: 'blocked', blocked: { label: 'NEW TRADES BLOCKED', note: 'n', reasons: [] } }); d.infra.binance.futures = tile('fail', { msg: 'HTTP 451', fix: 'Use a supported region.' }); d.infra.telegram = tile('skip');
  const s = R.healthPanel(d, 'futures'); has(s, 'SYSTEM: BLOCKED', 'open positions still managed', 'HTTP 451', 'Fix: Use a supported region.', 'NOT CONFIGURED', 'ALIVE', 'CONNECTED', '40/45 calls left today', 'm/free', 'CONDITIONAL ORDERS');
  has(R.healthPanel(dash(), 'futures'), 'SYSTEM: SAFE'); lacks(R.healthPanel(dash(), 'spot'), 'CONDITIONAL ORDERS');
});
test('brand bar: market tabs, demo/real state, locked real, start/stop', () => {
  const s = R.brand(dash(), 'futures'); has(s, 'data-action="market:spot"', 'Real (locked)', 'disabled', 'Stop Futures', 'DEMO: simulated fills'); lacks(R.brand(dash({}, { real_allowed: true }), 'futures'), 'Real (locked)'); has(R.brand(dash({ running: false }), 'futures'), 'Start Futures');
  has(R.brand(dash({}, { real_allowed: true, mode_banner: { label: 'REAL: live money', real: true, real_locked: false } }), 'futures'), 'seg real on', 'REAL: live money');
});
test('hostile symbol names are escaped everywhere', () => {
  const evil = '<script>alert(1)</script>'; const m = market({ rows: [{ symbol: evil, state: 'CANDIDATE', direction: 'LONG', reason: evil }], scan: { stats: { seq: 1, checked: 1, qualifying: 1 }, phase: 'idle', counts: {} }, positions: [pos({ symbol: evil })], active_setup: setup({ symbol: evil, reason: evil }), timeline: [{ id: 1, ts: 1, kind: 'x', label: evil, group: 'g', level: 'info', text: evil }] });
  for (const out of [R.scanPanel(m, { now: 1 }), R.positionPanel(m, null), R.setupPanel(m), R.timelinePanel(m)]) lacks(out, '<script>');
});

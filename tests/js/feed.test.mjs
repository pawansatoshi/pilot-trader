import test from 'node:test'; import assert from 'node:assert/strict';
import { createFeed, restUrl, wsUrl, parseKlineMessage } from '../../public/js/feed.js';
class FakeWS { static all = []; constructor(url) { this.url = url; this.closed = false; FakeWS.all.push(this); } close() { this.closed = true; } open() { this.onopen && this.onopen(); } msg(o) { this.onmessage && this.onmessage({ data: typeof o === 'string' ? o : JSON.stringify(o) }); } drop() { this.onclose && this.onclose(); } }
const kline = (t, c, x = false) => ({ e: 'kline', k: { t, o: '1', h: '2', l: '0.5', c: String(c), v: '10', x } });
const rows = [[1700000000000, '1', '2', '0.5', '1.5', '10'], [1700000900000, '1.5', '2', '1', '1.8', '12']];
function rig(o = {}) {
  FakeWS.all = []; const ev = { status: [], candles: [], candle: [], tick: [] }, timers = []; let t = 1000, id = 0; const clock = { t: () => t, set: v => { t = v } };
  const feed = createFeed({ fetchImpl: o.fetchImpl === undefined ? async () => ({ ok: true, json: async () => rows }) : o.fetchImpl, WebSocketImpl: 'ws' in o ? o.ws : FakeWS, now: clock.t, setTimer: (fn, ms) => { timers.push({ fn, ms, id: ++id }); return id; }, clearTimer: i => { const k = timers.findIndex(x => x.id === i); if (k >= 0) timers.splice(k, 1); },
    onStatus: (s, w) => ev.status.push(w ? `${s}:${w}` : s), onCandles: c => ev.candles.push(c), onCandle: (c, x) => ev.candle.push([c, x]), onTick: p => ev.tick.push(p), staleMs: 20000 });
  return { feed, ev, timers, clock };
}
test('urls: futures and spot, REST and WebSocket', () => { assert.equal(restUrl('futures', 'BTCUSDT', '15m', 5), 'https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=15m&limit=5'); assert.equal(restUrl('spot', 'ETHUSDT', '4h'), 'https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval=4h&limit=300'); assert.equal(wsUrl('futures', 'BTCUSDT', '5m'), 'wss://fstream.binance.com/ws/btcusdt@kline_5m'); assert.equal(wsUrl('spot', 'ETHUSDT', '1h'), 'wss://stream.binance.com:9443/ws/ethusdt@kline_1h') });
test('kline message parsing: plain, combined-stream, garbage', () => {
  assert.deepEqual(parseKlineMessage(JSON.stringify(kline(1700000000000, 1.2, true))), { candle: { time: 1700000000, open: 1, high: 2, low: 0.5, close: 1.2, volume: 10 }, closed: true }); assert.equal(parseKlineMessage(JSON.stringify({ stream: 's', data: kline(1700000000000, 3) })).candle.close, 3);
  for (const bad of ['not json', '{}', '{"k":{}}', 'null', '[]']) assert.equal(parseKlineMessage(bad), null);
});
test('history then live: candles from Binance REST, updates from the WebSocket', async () => {
  const { feed, ev } = rig(); await feed.start('futures', 'BTCUSDT', '15m'); assert.equal(ev.candles.length, 1); assert.equal(ev.candles[0].length, 2); assert.equal(ev.candles[0][1].close, 1.8); const ws = FakeWS.all[0]; assert.equal(ws.url, 'wss://fstream.binance.com/ws/btcusdt@kline_15m');
  ws.open(); assert.equal(feed.status(), 'live'); ws.msg(kline(1700000900000, 1.9)); ws.msg(kline(1700001800000, 2.1, false)); assert.deepEqual(ev.tick, [1.9, 2.1]); assert.equal(ev.candle[1][0].time, 1700001800); assert.ok(ev.status.includes('live'));
});
test('Binance history unreachable: honest status, no candles, nothing simulated', async () => {
  const { feed, ev } = rig({ fetchImpl: async () => { throw new Error('network down'); } }); await feed.start('spot', 'BTCUSDT', '1h'); assert.equal(ev.candles.length, 0); assert.ok(ev.status.some(s => s.startsWith('unavailable:Binance history unreachable: network down')));
  const r2 = rig({ fetchImpl: async () => ({ ok: false, status: 451 }) }); await r2.feed.start('futures', 'BTCUSDT', '5m'); assert.ok(r2.ev.status.some(s => s.includes('HTTP 451'))); assert.equal(r2.ev.candles.length, 0);
  const r3 = rig({ fetchImpl: async () => ({ ok: true, json: async () => ({ code: -1 }) }) }); await r3.feed.start('futures', 'BTCUSDT', '5m'); assert.ok(r3.ev.status.some(s => s.includes('unexpected response')));
});
test('no WebSocket support: unavailable, never fake live', async () => { const { feed, ev } = rig({ ws: null }); await feed.start('futures', 'BTCUSDT', '15m'); assert.equal(feed.status(), 'unavailable'); assert.ok(!ev.status.includes('live')); assert.equal(ev.tick.length, 0) });
test('no fetch available: unavailable with a reason', async () => { const { feed, ev } = rig({ fetchImpl: null }); await feed.start('futures', 'BTCUSDT', '15m'); assert.ok(ev.status.some(s => s.startsWith('unavailable:no fetch'))) });
test('dropped connection reconnects with exponential backoff and resets after success', async () => {
  const { feed, ev, timers } = rig(); await feed.start('futures', 'BTCUSDT', '15m'); FakeWS.all[0].open(); const delays = [];
  for (let i = 0; i < 4; i++) { FakeWS.all[FakeWS.all.length - 1].drop(); const r = timers.filter(t => t.ms < 20000 && t.ms >= 1000).pop(); delays.push(r.ms); timers.splice(timers.indexOf(r), 1); r.fn(); }
  assert.deepEqual(delays, [1000, 2000, 4000, 8000]); assert.ok(ev.status.includes('disconnected:connection closed')); FakeWS.all[FakeWS.all.length - 1].open(); assert.equal(feed.status(), 'live'); FakeWS.all[FakeWS.all.length - 1].drop(); assert.equal(timers.filter(t => t.ms === 1000).length >= 1, true);
});
test('backoff is capped', async () => { const { feed, timers } = rig(); await feed.start('futures', 'BTCUSDT', '15m'); let last = 0; for (let i = 0; i < 12; i++) { FakeWS.all[FakeWS.all.length - 1].drop(); const r = timers.filter(t => t.ms >= 1000 && t.ms !== 5000).pop(); last = r.ms; timers.splice(timers.indexOf(r), 1); r.fn(); } assert.equal(last, 30000) });
test('silent connection is detected as stale and reconnected', async () => {
  const { feed, ev, timers, clock } = rig(); await feed.start('futures', 'BTCUSDT', '15m'); const ws = FakeWS.all[0]; ws.open(); clock.set(1000 + 25000); const check = timers.find(t => t.ms === 5000); check.fn();
  assert.equal(feed.status(), 'stale'); assert.ok(ev.status.some(s => s.startsWith('stale:'))); assert.equal(ws.closed, true); assert.ok(timers.some(t => t.ms === 1000));
});
test('switching symbol ignores late events from the previous subscription', async () => {
  const { feed, ev } = rig(); await feed.start('futures', 'BTCUSDT', '15m'); const old = FakeWS.all[0]; old.open(); await feed.start('futures', 'ETHUSDT', '15m'); const n = FakeWS.all.length; old.msg(kline(1700000000000, 99)); old.drop(); assert.equal(ev.tick.length, 0); assert.equal(old.closed, true); assert.equal(FakeWS.all.length, n);
});
test('stop() closes the socket and silences everything', async () => { const { feed, ev, timers } = rig(); await feed.start('spot', 'BTCUSDT', '4h'); const ws = FakeWS.all[0]; ws.open(); feed.stop(); assert.equal(ws.closed, true); assert.equal(timers.length, 0); assert.equal(feed.status(), 'idle'); ws.msg(kline(1700000000000, 5)); assert.equal(ev.tick.length, 0) });
test('malformed websocket frames are ignored', async () => { const { feed, ev } = rig(); await feed.start('futures', 'BTCUSDT', '15m'); const ws = FakeWS.all[0]; ws.open(); ws.msg('garbage'); ws.msg('{"x":1}'); assert.equal(ev.tick.length, 0); assert.equal(feed.status(), 'live') });

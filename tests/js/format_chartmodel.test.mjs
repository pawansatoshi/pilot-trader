import test from 'node:test'; import assert from 'node:assert/strict';
import { esc, num, price, signed, tone, clock, countdown, age } from '../../public/js/format.js';
import { bucket, unrealized, buildMarkers, buildLines, toCandle, TF_SECONDS } from '../../public/js/chartmodel.js';
import { pos } from './helpers.mjs';
test('esc neutralises html', () => assert.equal(esc('<img src=x onerror="a">&\''), '&lt;img src=x onerror=&quot;a&quot;&gt;&amp;&#39;'));
test('number formatting never prints NaN or undefined', () => { for (const f of [num, price, signed]) for (const v of [null, undefined, '', NaN, 'abc']) assert.equal(f(v), '–'); assert.equal(signed(1.5), '+1.50'); assert.equal(signed(-1.5), '-1.50'); assert.equal(price(0.012345), '0.012345'); assert.equal(price(65000.1), '65,000.10') });
test('tone / countdown / age / clock', () => { assert.deepEqual([tone(1), tone(-1), tone(0), tone(null)], ['up', 'down', 'flat', 'flat']); assert.equal(countdown(272), '4:32'); assert.equal(countdown(0), 'due'); assert.equal(countdown(null), '–'); assert.equal(age(45), '45s'); assert.equal(age(600), '10 min'); assert.equal(clock(1700000000, 'UTC'), '22:13:20'); assert.equal(clock(null), '–') });
test('candle buckets align to each timeframe', () => { for (const [tf, s] of Object.entries(TF_SECONDS)) { const t = 1700000123; assert.equal(bucket(t, tf) % s, 0); assert.ok(t - bucket(t, tf) < s) } });
test('kline array -> candle', () => assert.deepEqual(toCandle([1700000000000, '1', '2', '0.5', '1.5', '10']), { time: 1700000000, open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 }));
test('unrealized pnl and R from a live price (long and short)', () => {
  const l = unrealized(pos(), 103); assert.equal(l.pnl, 6); assert.equal(l.r, 1.5); const s = unrealized(pos({ side: 'SHORT', entry: 100, sl: 102 }), 103); assert.equal(s.pnl, -6); assert.equal(s.r, -1.5);
  assert.equal(unrealized(pos(), null), null); assert.equal(unrealized(pos(), undefined), null); assert.equal(unrealized(pos(), NaN), null); assert.equal(unrealized(pos({ risk: 0 }), 101).r, null);
});
test('markers come only from real trades: BUY/SELL, stop moves, exit with reason and R', () => {
  const tr = [pos({ id: 1, opened: 1700000100, status: 'CLOSED', closed: 1700003700, exit: 104, reason: 'TP', pnl: 8, r_mult: 2, stop_moves: [{ ts: 1700001000, new_sl: 100 }] }), pos({ id: 2, side: 'SHORT', opened: 1700009000, status: 'OPEN', simulated: false })];
  const m = buildMarkers(tr, '15m'); assert.deepEqual(m.map(x => x.text.split(' ')[0]), ['BUY', 'SL', 'TP', 'SELL']); assert.equal(m[0].shape, 'arrowUp'); assert.ok(m[0].text.includes('(demo)')); assert.ok(m[2].text.includes('+2.00R')); assert.equal(m[3].shape, 'arrowDown'); assert.ok(!m[3].text.includes('demo'));
  assert.ok(m.every((x, i) => i === 0 || m[i - 1].time <= x.time)); assert.deepEqual(buildMarkers([], '5m'), []);
});
test('price lines exist only for OPEN trades and equal the stored levels', () => {
  const l = buildLines([pos({ status: 'OPEN' }), pos({ id: 2, status: 'CLOSED' })]); assert.deepEqual(l.map(x => [x.title, x.price]), [['ENTRY SOLUSDT', 100], ['SL', 98], ['TP', 104]]); assert.deepEqual(buildLines([]), []);
});

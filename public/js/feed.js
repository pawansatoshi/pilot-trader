// Browser -> Binance public market data. This is the ONLY market-data path of the UI: REST history + WebSocket kline stream.
// The WebSocket/fetch implementations are injected, so the boundary is testable without a network. No data is ever synthesized:
// when Binance cannot be reached the status says so and no candles are emitted.
import { toCandle } from './chartmodel.js';
export const REST = { futures: 'https://fapi.binance.com/fapi/v1/klines', spot: 'https://api.binance.com/api/v3/klines' };
export const WS = { futures: 'wss://fstream.binance.com/ws/', spot: 'wss://stream.binance.com:9443/ws/' };
export const restUrl = (mk, sym, tf, limit = 300) => `${REST[mk]}?symbol=${encodeURIComponent(sym)}&interval=${tf}&limit=${limit}`;
export const wsUrl = (mk, sym, tf) => `${WS[mk]}${sym.toLowerCase()}@kline_${tf}`;
export function parseKlineMessage(raw) {
  let m; try { m = JSON.parse(raw); } catch { return null; }
  const k = (m && m.data ? m.data : m)?.k; if (!k || k.t == null) return null;
  return { candle: { time: Math.floor(k.t / 1000), open: +k.o, high: +k.h, low: +k.l, close: +k.c, volume: +k.v }, closed: !!k.x };
}
export function createFeed({ fetchImpl, WebSocketImpl, setTimer = setTimeout, clearTimer = clearTimeout, now = () => Date.now(), onStatus = () => {}, onCandles = () => {}, onCandle = () => {}, onTick = () => {}, staleMs = 20000, maxBackoff = 30000 } = {}) {
  let token = 0, ws = null, timers = [], status = 'idle', backoff = 1000, last = 0, target = null;
  const set = (s, why = '') => { if (s !== status || why) { status = s; onStatus(s, why); } };
  const clearAll = () => { timers.forEach(clearTimer); timers = []; if (ws) { try { ws.onclose = ws.onerror = ws.onmessage = ws.onopen = null; ws.close(); } catch {} ws = null; } };
  const later = (fn, ms) => { const t = setTimer(fn, ms); timers.push(t); return t; };
  async function history(my) {
    if (!fetchImpl) { set('unavailable', 'no fetch available'); return false; }
    try {
      const r = await fetchImpl(restUrl(target.market, target.symbol, target.tf)); if (my !== token) return false;
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const rows = await r.json(); if (my !== token) return false;
      if (!Array.isArray(rows)) throw new Error('unexpected response');
      onCandles(rows.map(toCandle)); return true;
    } catch (e) { if (my === token) set('unavailable', `Binance history unreachable: ${e.message}`); return false; }
  }
  function connect(my) {
    if (my !== token) return;
    if (!WebSocketImpl) { set('unavailable', 'WebSocket not supported in this browser'); return; }
    set('connecting'); let sock;
    try { sock = new WebSocketImpl(wsUrl(target.market, target.symbol, target.tf)); } catch (e) { set('disconnected', e.message); retry(my); return; }
    ws = sock; last = now();
    sock.onopen = () => { if (my === token) { backoff = 1000; last = now(); set('live'); watch(my); } };
    sock.onmessage = ev => {
      if (my !== token) return; const p = parseKlineMessage(ev.data); if (!p) return; last = now(); if (status !== 'live') set('live');
      onCandle(p.candle, p.closed); onTick(p.candle.close);
    };
    const drop = why => () => { if (my === token) { set('disconnected', why); retry(my); } };
    sock.onclose = drop('connection closed'); sock.onerror = drop('connection error');
  }
  function watch(my) { later(function check() { if (my !== token) return; if (now() - last > staleMs) { set('stale', 'no data from Binance'); clearAll(); retry(my); return; } later(check, Math.max(1000, staleMs / 4)); }, staleMs / 4); }
  function retry(my) { if (my !== token) return; clearAll(); const wait = backoff; backoff = Math.min(backoff * 2, maxBackoff); later(() => connect(my), wait); }
  return {
    status: () => status,
    async start(market, symbol, tf) { this.stop(); token += 1; const my = token; target = { market, symbol, tf }; backoff = 1000; set('connecting'); await history(my); if (my === token) connect(my); },
    stop() { token += 1; clearAll(); target = null; status = 'idle'; },
  };
}

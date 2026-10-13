// Pure chart model: turns REAL backend trades into markers / price lines, and live prices into P&L. Never invents anything.
import { num, price, signed } from './format.js';
export const TF_SECONDS = { '5m': 300, '15m': 900, '1h': 3600, '4h': 14400 };
export const bucket = (ts, tf) => Math.floor(ts / TF_SECONDS[tf]) * TF_SECONDS[tf];
export const dir = side => (side === 'LONG' ? 1 : -1);
/** Unrealized P&L from a LIVE price. Before fees/slippage; null when there is no live price. */
export function unrealized(pos, px) {
  if (px === null || px === undefined || !Number.isFinite(Number(px))) return null;
  const move = (Number(px) - pos.entry) * dir(pos.side), pnl = move * pos.qty;
  return { pnl, r: pos.risk > 0 ? move / pos.risk : null, pct: pos.entry ? (move / pos.entry) * 100 : null };
}
export function buildMarkers(trades, tf) {
  const out = [];
  for (const t of trades) {
    const buy = t.side === 'LONG';
    out.push({ time: bucket(t.opened, tf), position: buy ? 'belowBar' : 'aboveBar', shape: buy ? 'arrowUp' : 'arrowDown', color: buy ? '#19c37d' : '#f0506e', text: `${buy ? 'BUY' : 'SELL'} ${price(t.entry)}${t.simulated ? ' (demo)' : ''}` });
    for (const m of t.stop_moves || []) out.push({ time: bucket(m.ts, tf), position: 'inBar', shape: 'square', color: '#e5a82b', text: `SL > ${price(m.new_sl)}` });
    if (t.status === 'CLOSED' && t.closed) out.push({ time: bucket(t.closed, tf), position: 'inBar', shape: 'circle', color: (t.pnl || 0) >= 0 ? '#19c37d' : '#f0506e', text: `${t.reason || 'EXIT'} ${price(t.exit)} ${signed(t.r_mult, 2, 'R')}` });
  }
  return out.sort((a, b) => a.time - b.time);
}
export function buildLines(trades) {
  const out = [];
  for (const t of trades.filter(t => t.status === 'OPEN')) {
    out.push({ price: t.entry, color: '#8aa0b6', title: `ENTRY ${t.symbol}` }, { price: t.sl, color: '#f0506e', title: 'SL' }, { price: t.tp, color: '#19c37d', title: 'TP' });
  }
  return out;
}
export const toCandle = k => ({ time: Math.floor(k[0] / 1000), open: +k[1], high: +k[2], low: +k[3], close: +k[4], volume: +k[5] });
export const volumeBar = c => ({ time: c.time, value: c.volume, color: c.close >= c.open ? 'rgba(25,195,125,.35)' : 'rgba(240,80,110,.35)' });

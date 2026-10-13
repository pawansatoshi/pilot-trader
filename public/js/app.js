// Glue only: fetch backend state, render it, wire the chart to Binance. The page never creates trades or orders: its only write
// actions are the existing Start/Stop and Demo/Real endpoints (the backend enforces every rule).
import * as R from './render.js';
import { createFeed } from './feed.js';
import { buildMarkers, buildLines, toCandle, volumeBar } from './chartmodel.js';
import { esc, price, num } from './format.js';
const $ = id => document.getElementById(id);
const S = { dash: null, market: 'futures', tf: '15m', symbol: null, userSymbol: null, trades: [], live: { price: null, symbol: null }, feedStatus: 'idle', showAll: false, offset: 0, html: {}, key: '', lines: [], chart: null, candles: null, vol: null, note: '' };
const nowS = () => Date.now() / 1000 + S.offset;
const set = (id, html) => { if (S.html[id] !== html) { $(id).innerHTML = html; S.html[id] = html; } };
async function api(path, body) {
  let r; try { r = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}); } catch { return { err: 'Cannot reach the server. Check your connection.' }; }
  if (r.status === 401) { $('login').hidden = false; return null; }
  try { return await r.json(); } catch { return { err: `Unexpected response (HTTP ${r.status})` }; }
}
async function poll() {
  const d = await api('/api/v2/dashboard'); if (!d) return;
  if (d.err || d.db_error) { set('banners', `<div class="banner blocked"><b>BACKEND UNAVAILABLE</b><span>${esc(d.db_error || d.err)} ${esc(d.db_fix || '')}</span></div>`); return; }
  S.dash = d; S.offset = d.now - Date.now() / 1000; $('login').hidden = true; render(); ensureFeed(); refreshTrades();
}
function chipSymbols(m) {
  const s = [...m.positions.map(p => p.symbol), m.active_setup && m.active_setup.symbol, ...m.rows.filter(r => r.state !== 'WAITING').slice(0, 5).map(r => r.symbol), 'BTCUSDT'].filter(Boolean);
  return [...new Set(s)].slice(0, 8);
}
function pickSymbol(m) { return S.userSymbol || (m.positions[0] && m.positions[0].symbol) || (m.active_setup && m.active_setup.symbol) || (m.rows.find(r => r.state !== 'WAITING') || {}).symbol || 'BTCUSDT'; }
function render() {
  const d = S.dash, m = d.markets[S.market], ctx = { now: nowS(), live: S.live, showAll: S.showAll };
  set('brand', R.brand(d, S.market)); set('banners', R.banners(d, S.market)); set('status-body', R.statusLine(d, S.market) + R.stepper(m)); set('pnl-body', R.pnlPanel(m));
  set('scanner-body', R.scanPanel(m, ctx)); set('health-body', R.healthPanel(d, S.market)); set('setup-body', R.setupPanel(m)); set('ai-body', R.aiPanel(m)); set('timeline-body', R.timelinePanel(m));
  renderLive(); const sym = pickSymbol(m);
  set('symchips', chipSymbols(m).map(x => `<button class="chip${x === sym ? ' on' : ''}" data-action="sym:${esc(x)}">${esc(x.replace('USDT', ''))}</button>`).join(''));
  set('tfs', ['5m', '15m', '1h', '4h'].map(t => `<button class="tfbtn${t === S.tf ? ' on' : ''}" data-action="tf:${t}">${t}</button>`).join(''));
}
let liveTimer = null; const scheduleLive = () => { if (!liveTimer) liveTimer = setTimeout(() => { liveTimer = null; if (S.dash) renderLive(); }, 400); };
function renderLive() {
  const m = S.dash.markets[S.market]; set('levels-body', R.levelsPanel(m, S.live)); set('position-body', R.positionPanel(m, S.live));
  set('livebar', `<span class="px">${S.live.price != null && S.live.symbol === S.symbol ? price(S.live.price) : '–'}</span><span class="muted">${esc(S.symbol || '')} · ${S.live.price != null ? 'live Binance price' : 'no live price'}</span>`);
}
const FEED_LABEL = { live: 'LIVE', connecting: 'CONNECTING', stale: 'STALE', disconnected: 'DISCONNECTED', unavailable: 'NO LIVE FEED', idle: 'NO FEED' };
function setFeed(status, why) {
  S.feedStatus = status; const el = $('feedstatus'); el.className = 'feed ' + status; el.innerHTML = `<i></i>${FEED_LABEL[status] || status}`;
  S.note = status === 'live' ? '' : status === 'unavailable' ? `Live Binance feed unavailable (${why || 'unknown'}). Showing the worker's 15m snapshot instead (delayed). No data is simulated.` : why ? `Binance feed: ${why}` : '';
  $('chartnote').textContent = S.note; $('chartnote').style.display = S.note ? '' : 'none';
  if (status === 'unavailable') loadSnapshot();
}
const feed = createFeed({ fetchImpl: (...a) => fetch(...a), WebSocketImpl: typeof WebSocket !== 'undefined' ? WebSocket : null, onStatus: setFeed,
  onCandles: c => { if (!S.chart) return; S.candles.setData(c); S.vol.setData(c.map(volumeBar)); S.chart.timeScale().fitContent(); overlays(); },
  onCandle: c => { if (S.candles) { S.candles.update(c); S.vol.update(volumeBar(c)); } },
  onTick: p => { S.live = { price: p, symbol: S.symbol }; scheduleLive(); } });
function makeChart() {
  if (S.chart) return true;
  if (typeof LightweightCharts === 'undefined') { $('chartnote').textContent = 'Chart library could not be loaded (cdnjs blocked?). All other panels still show backend state.'; return false; }
  S.chart = LightweightCharts.createChart($('chart'), { autoSize: true, layout: { background: { color: '#10161e' }, textColor: '#7b8a9b' }, grid: { vertLines: { color: '#171f2a' }, horzLines: { color: '#171f2a' } }, crosshair: { mode: 0 }, rightPriceScale: { borderColor: '#1f2a37' }, timeScale: { borderColor: '#1f2a37', timeVisible: true } });
  S.candles = S.chart.addCandlestickSeries({ upColor: '#19c37d', downColor: '#f0506e', borderVisible: false, wickUpColor: '#19c37d', wickDownColor: '#f0506e' });
  S.vol = S.chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' }); S.chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } }); return true;
}
function ensureFeed() {
  const m = S.dash.markets[S.market]; S.symbol = pickSymbol(m); const key = `${S.market}|${S.symbol}|${S.tf}`;
  if (key === S.key || !makeChart()) return; S.key = key; S.live = { price: null, symbol: S.symbol }; S.trades = []; feed.start(S.market, S.symbol, S.tf);
}
async function loadSnapshot() {   // fallback only: the worker's own 15m candles from the database
  const r = await api(`/api/chart?symbol=${S.symbol}&market=${S.market}`); if (!r || !r.k || !r.k.length || !S.chart) return;
  const c = r.k.map(k => toCandle(k)); S.candles.setData(c); S.vol.setData(c.map(volumeBar)); S.chart.timeScale().fitContent(); overlays();
}
async function refreshTrades() {
  if (!S.symbol) return; const t = await api(`/api/v2/trades?market=${S.market}&symbol=${S.symbol}`); if (Array.isArray(t)) { S.trades = t; overlays(); }
}
function overlays() {
  if (!S.chart) return; S.lines.forEach(l => S.candles.removePriceLine(l)); S.lines = buildLines(S.trades).map(l => S.candles.createPriceLine({ price: l.price, color: l.color, title: l.title, lineWidth: 1, lineStyle: 2 }));
  S.candles.setMarkers(buildMarkers(S.trades, S.tf));
}
document.addEventListener('click', async ev => {
  const b = ev.target.closest('[data-action]'); if (!b) return; const [a, v] = b.dataset.action.split(':');
  if (a === 'market') { S.market = v; S.userSymbol = null; S.key = ''; }
  else if (a === 'tf') { S.tf = v; S.key = ''; }
  else if (a === 'sym') { S.userSymbol = v; S.key = ''; }
  else if (a === 'showall') S.showAll = !S.showAll;
  else if (a === 'toggle') {
    const run = S.dash.markets[v].running; if (!run && S.dash.mode === 'real' && !confirm(`Start ${v} with REAL money?`)) return;
    const r = await api('/api/market', { market: v, on: !run }); if (r && r.err) alert(r.err); await poll(); return;
  } else if (a === 'mode') {
    const body = { mode: v }; if (v === 'real') { if (!S.dash.real_allowed) return alert('Real trading is locked (ALLOW_REAL is not set on the server).'); if (!confirm('REAL mode trades with live money. Continue?')) return; body.confirm = prompt('Type REAL to confirm'); }
    const r = await api('/api/mode', body); if (r && r.err) alert(r.err); await poll(); return;
  }
  render(); ensureFeed();
});
$('login-form').addEventListener('submit', async e => { e.preventDefault(); const r = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pw: $('pw').value }) }); const j = await r.json().catch(() => ({})); if (j.ok) { $('login-err').textContent = ''; poll(); } else $('login-err').textContent = j.err || 'Login failed'; });
setInterval(() => document.querySelectorAll('[data-countdown]').forEach(e => { const s = +e.dataset.countdown - nowS(); e.textContent = s <= 0 ? 'due' : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`; }), 1000);
setInterval(() => { if (!document.hidden) poll(); }, 5000); document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); }); poll();

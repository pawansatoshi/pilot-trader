// Pure renderers: backend view-model -> HTML strings. The frontend decides nothing: every label, lock, block and workflow step comes from the backend.
import { esc, num, price, signed, tone, clock, countdown, age, isNum } from './format.js';
import { unrealized } from './chartmodel.js';
const pill = (cls, text) => `<span class="pill ${esc(cls)}">${esc(text)}</span>`;
const dash = '<span class="muted">not provided</span>';
const val = x => (x === null || x === undefined || x === '' ? dash : esc(Array.isArray(x) ? x.join('; ') : x));
const STATE_CLASS = { WAITING: 'idle', WATCHING: 'watch', NEAR_SETUP: 'near', CANDIDATE: 'cand', AI_REVIEW: 'ai', RISK_CHECK: 'risk', EXECUTION: 'exec', POSITION: 'pos', CLOSED: 'idle', OUTCOME: 'idle', REJECTED: 'rej', COOLDOWN: 'cool' };
export const STATE_LABEL = { WAITING: 'WAITING', WATCHING: 'WATCHING', NEAR_SETUP: 'NEAR SETUP', CANDIDATE: 'SETUP DETECTED', AI_REVIEW: 'AI REVIEW', RISK_CHECK: 'RISK CHECK', EXECUTION: 'EXECUTION', POSITION: 'POSITION', CLOSED: 'CLOSED', OUTCOME: 'OUTCOME', REJECTED: 'REJECTED', COOLDOWN: 'COOLDOWN' };
export const statePill = s => pill('st-' + (STATE_CLASS[s] || 'idle'), STATE_LABEL[s] || s);

export function brand(d, market, ctx = {}) {
  const m = d.markets[market], real = d.mode_banner.real;
  const mk = ['futures', 'spot'].map(k => `<button class="tab${k === market ? ' on' : ''}" data-action="market:${k}">${k === 'futures' ? 'Futures' : 'Spot'}</button>`).join('');
  const mode = `<button class="seg${!real ? ' on' : ''}" data-action="mode:demo">Demo</button><button class="seg real${real ? ' on' : ''}" data-action="mode:real"${d.real_allowed ? '' : ' disabled title="Real trading is locked (ALLOW_REAL is not set)"'}>Real${d.real_allowed ? '' : ' (locked)'}</button>`;
  const run = `<button class="run ${m.running ? 'stop' : 'start'}" data-action="toggle:${market}">${m.running ? 'Stop' : 'Start'} ${market === 'futures' ? 'Futures' : 'Spot'}</button>`;
  return `<div class="brandbar"><strong class="logo">PILOT</strong><nav class="tabs">${mk}</nav><div class="modes">${mode}</div>${run}${pill(real ? 'real' : 'demo', d.mode_banner.label)}</div>`;
}
export function banners(d, market) {
  const m = d.markets[market], out = [];
  if (d.mode_banner.real) out.push(`<div class="banner real"><b>REAL MODE</b> live money. Orders are sent to Binance.</div>`);
  if (m.lock) out.push(`<div class="banner ${m.lock.code === 'TARGET_REACHED' ? 'goal' : 'loss'}"><b>${esc(m.lock.label)}</b><span>${esc(m.lock.detail)}</span></div>`);
  if (m.blocked) out.push(`<div class="banner blocked"><b>${esc(m.blocked.label)}</b><span>${esc(m.blocked.note)}</span><ul>${m.blocked.reasons.map(r => `<li>${esc(r.msg)}${r.fix ? ` <em>Fix: ${esc(r.fix)}</em>` : ''}</li>`).join('')}</ul></div>`);
  if (m.degraded && m.degraded.length) out.push(`<div class="banner degraded"><b>DEGRADED</b> trading continues. ${m.degraded.map(r => esc(r.msg)).join(' | ')}</div>`);
  if (!d.infra.worker.ok) out.push(`<div class="banner blocked"><b>WORKER ${esc(d.infra.worker.label)}</b><span>${d.infra.worker.heartbeat_age == null ? 'The worker has never reported.' : `Last signal ${esc(age(d.infra.worker.heartbeat_age))} ago.`} Nothing shown here is being updated until it is back. Check the Voroa worker logs.</span></div>`);
  return out.join('');
}
export function stepper(m) {
  const w = m.workflow;
  return `<div class="stage"><span class="label">STAGE</span> ${pill('stage-' + (w.stage === 'STOPPED' ? 'off' : 'on'), w.label)}<span class="detail">${esc(m.status.detail || '')}</span></div>` +
    `<ol class="steps">${w.steps.map(s => `<li class="step ${esc(s.state)}" data-step="${esc(s.code)}" title="${esc(s.label)}: ${esc(s.state)}"><i></i>${esc(s.label)}</li>`).join('')}</ol>`;
}
export function pnlPanel(m) {
  const t = m.target, p = m.protection;
  return `<div class="kpis"><div class="kpi"><span class="label">BALANCE</span><b class="mono">${num(m.balance)} <small>USDT</small></b></div>` +
    `<div class="kpi"><span class="label">TODAY</span><b class="mono ${tone(m.day_pnl_abs)}">${signed(m.day_pnl_abs)} <small>USDT</small></b><span class="mono ${tone(m.day_pnl_pct)}">${signed(m.day_pnl_pct, 2, '%')}</span></div>` +
    `<div class="kpi"><span class="label">POSITIONS</span><b class="mono">${m.positions.length}</b></div></div>` +
    `<div class="meters"><div class="meter goal${t.reached ? ' hit' : ''}"><span class="label">TARGET +${num(t.pct, 0)}%</span><div class="bar"><i style="width:${Math.round(t.progress * 100)}%"></i></div><span class="mono">${Math.round(t.progress * 100)}% ${t.reached ? 'REACHED' : ''}</span></div>` +
    `<div class="meter loss${p.hit ? ' hit' : ''}"><span class="label">PROTECTION -${num(p.pct, 0)}%</span><div class="bar"><i style="width:${Math.round(p.used * 100)}%"></i></div><span class="mono">${p.hit ? 'LIMIT HIT: new trades paused' : Math.round(p.used * 100) + '% used'}</span></div></div>`;
}
export function scanPanel(m, ctx = {}) {
  const s = m.scan.stats || {}, rows = m.rows || [];
  if (!rows.length && !s.seq) return `<div class="empty">${m.running ? 'Scan running: no scan has completed yet.' : 'Bot stopped: no scan has run yet.'}</div>`;
  const next = isNum(m.scan.next_scan_ts) ? `<span data-countdown="${m.scan.next_scan_ts}">${countdown(m.scan.next_scan_ts - ctx.now)}</span>` : '–';
  const shown = (ctx.showAll ? rows : rows.filter(r => r.state !== 'WAITING')).slice(0, ctx.showAll ? 60 : 14);
  const head = `<div class="scanstats mono"><span><b>${s.checked ?? '–'}</b> checked</span><span><b>${s.qualifying ?? '–'}</b> qualifying</span><span><b>${m.rejections.length}</b> rejected (24h)</span><span>scan ${isNum(s.seconds) ? num(s.seconds, 1) + 's' : '–'}</span><span>next ${m.scan.phase === 'scanning' ? 'scanning now' : next}</span></div>`;
  const body = shown.length ? `<table class="grid"><thead><tr><th>Symbol</th><th>State</th><th>Dir</th><th title="Indicative: not an order">Entry / SL / TP (indicative)</th><th>Why</th></tr></thead><tbody>${shown.map(r => `<tr data-symbol="${esc(r.symbol)}" class="${r.state === 'CANDIDATE' || r.state === 'POSITION' ? 'hot' : ''}"><td><button class="link" data-action="sym:${esc(r.symbol)}">${esc(String(r.symbol).replace('USDT', ''))}</button></td><td>${statePill(r.state)}</td><td class="${r.direction === 'LONG' ? 'up' : r.direction === 'SHORT' ? 'down' : ''}">${esc(r.direction || '–')}</td>` +
    `<td class="mono">${r.levels ? `${price(r.levels.entry)} / ${price(r.levels.sl)} / ${price(r.levels.tp)}` : '–'}</td><td class="why">${esc(r.reason || '')}</td></tr>`).join('')}</tbody></table>` : `<div class="empty">Scanning: ${s.checked ?? 0} symbols checked, 0 qualifying setups, nothing is developing.</div>`;
  const rej = m.rejections.length ? `<div class="subhead">Rejected setups</div><ul class="rejlist">${m.rejections.map(r => `<li><span class="mono">${clock(r.ts)}</span> <b>${esc(r.symbol)}</b> ${esc(r.direction)} <span class="muted">from ${esc(STATE_LABEL[r.from_state] || r.from_state)}</span> ${esc(r.reason)}</li>`).join('')}</ul>` : '';
  const more = rows.some(r => r.state === 'WAITING') ? `<button class="link" data-action="showall">${ctx.showAll ? 'show only active' : 'show all symbols'}</button>` : '';
  return head + body + more + rej;
}
export function levelsPanel(m, live) {
  const pos = m.positions[0], su = m.active_setup;
  if (pos) { const u = unrealized(pos, live && live.price); return `<div class="levels actual"><div class="tagline">${pill('pos', 'ACTUAL')} <span class="muted">from the exchange fill</span></div>${levelRow(pos.entry, pos.sl, pos.tp)}${u ? '' : '<div class="muted">No live price: P&L not shown.</div>'}</div>`; }
  if (su && su.levels && su.levels.entry != null) return `<div class="levels indicative"><div class="tagline">${pill('cand', 'INDICATIVE')} <span class="muted">${esc(su.symbol)} ${esc(su.direction)}: not an order</span></div>${levelRow(su.levels.entry, su.levels.sl, su.levels.tp, su.levels.eff_rr)}</div>`;
  return '<div class="empty">No position and no setup with levels.</div>';
}
const levelRow = (e, s, t, rr) => `<div class="lv"><div><span class="label">ENTRY</span><b class="mono">${price(e)}</b></div><div><span class="label">STOP LOSS</span><b class="mono down">${price(s)}</b></div><div><span class="label">TAKE PROFIT</span><b class="mono up">${price(t)}</b></div>${rr != null ? `<div><span class="label">R:R AFTER COSTS</span><b class="mono">${num(rr)}</b></div>` : ''}</div>`;
export function positionPanel(m, live) {
  if (!m.positions.length) return `<div class="empty">No open position.${m.last_outcome ? ` Last outcome: <b>${esc(m.last_outcome.symbol)}</b> <span class="${tone(m.last_outcome.r)} mono">${signed(m.last_outcome.r, 2, 'R')}</span> (${signed(m.last_outcome.pnl)} USDT, ${esc(m.last_outcome.reason)})` : ''}</div>`;
  return m.positions.map(p => { const px = live && live.symbol === p.symbol ? live.price : null, u = unrealized(p, px);
    return `<div class="pos" data-trade="${p.id}"><div class="poshead"><b>${esc(p.symbol)}</b> <span class="${p.side === 'LONG' ? 'up' : 'down'}">${esc(p.side)}</span> ${p.simulated ? pill('demo', 'DEMO: simulated') : pill('real', 'REAL')}${p.exchange_verified === false ? pill('warn', 'exchange state unverified') : ''}</div>` +
      `<div class="posgrid mono"><div><span class="label">SIZE</span>${num(p.qty, 4)}</div><div><span class="label">ENTRY</span>${price(p.entry)}</div><div><span class="label">LIVE PRICE</span>${isNum(px) ? price(px) : '<span class="muted">no live price</span>'}</div>` +
      `<div><span class="label">UNREALIZED P&amp;L</span><b class="${tone(u && u.pnl)}">${u ? signed(u.pnl) + ' USDT' : '–'}</b></div><div><span class="label">R MULTIPLE</span><b class="${tone(u && u.r)}">${u && u.r != null ? signed(u.r, 2, 'R') : '–'}</b></div><div><span class="label">STOP / TARGET</span>${price(p.sl)} / ${price(p.tp)}</div></div>` +
      `<div class="muted small">Unrealized from the live Binance price, before fees.${p.stop_moves && p.stop_moves.length ? ` Stop moved ${p.stop_moves.length}x.` : ''} Opened ${clock(p.opened)}.</div></div>`; }).join('');
}
export function setupPanel(m) {
  const s = m.active_setup;
  if (!s) return `<div class="empty">${m.running ? 'No setup under review. The bot is scanning.' : 'Bot stopped.'}</div>`;
  return `<div class="setup ${s.is_trade ? 'istrade' : 'issetup'}"><div class="headline">${esc(s.headline)}</div><div class="srow"><b>${esc(s.symbol)}</b> <span class="${s.direction === 'LONG' ? 'up' : 'down'}">${esc(s.direction)}</span> ${statePill(s.state)} <span class="muted">since ${clock(s.since)}</span></div>` +
    `<div class="why">${esc(s.reason || '')}</div>${s.is_trade ? '' : '<div class="note">Detected by the scanner. No order has been sent. A trade exists only after the exchange fill and protection are confirmed.</div>'}</div>`;
}
export function aiPanel(m) {
  const a = m.active_setup && m.active_setup.ai;
  if (!a) return '<div class="empty">No AI review for the current setup yet.</div>';
  return `<div class="ai"><div class="aihead">${pill('ai-' + String(a.verdict).toLowerCase(), 'VERDICT ' + (a.verdict || 'n/a'))} ${isNum(a.confidence_pct) ? `<b class="mono">${a.confidence_pct}%</b>` : ''} ${a.grade ? pill('grade', 'GRADE ' + a.grade) : ''}</div>` +
    `<dl><dt>Status</dt><dd>${val(a.status)}</dd><dt>Reason</dt><dd>${val(a.reason)}</dd><dt>Case for</dt><dd>${val(a.case_for)}</dd><dt>Case against</dt><dd>${val(a.case_against)}</dd><dt>Fatal flaw</dt><dd>${val(a.fatal_flaw)}</dd><dt>Invalidation</dt><dd>${val(a.invalidation)}</dd>` +
    `<dt>Model</dt><dd>${val(a.model)}</dd><dt>Latency</dt><dd>${isNum(a.latency_ms) ? num(a.latency_ms, 0) + ' ms' : dash}</dd><dt>Risk gate</dt><dd>${a.gate_ok == null ? dash : a.gate_ok ? 'passed' : esc('rejected: ' + (a.gate_reasons || ''))}</dd></dl><div class="note">${esc(a.authority)}</div></div>`;
}
export function timelinePanel(m) {
  const ev = (m.timeline || []).slice().reverse();
  if (!ev.length) return '<div class="empty">No events yet. Start the bot: every step it takes will appear here with its real timestamp.</div>';
  return `<ol class="timeline">${ev.map(e => `<li class="ev ${esc(e.group)} ${esc(e.level)}" data-kind="${esc(e.kind)}"><time class="mono">${clock(e.ts)}</time><span class="evlabel">${esc(e.label)}</span>${e.simulated ? '<span class="pill demo">DEMO</span>' : ''}<span class="evtext">${esc(e.text)}</span></li>`).join('')}</ol>`;
}
export function healthPanel(d, market) {
  const i = d.infra, m = d.markets[market], tile = (name, t, extra = '') => `<div class="tile ${esc(t.status || (t.ok ? 'ok' : 'fail'))}"><span class="label">${esc(name)}</span><b>${esc(t.label)}</b>${isNum(t.ms) ? `<span class="mono muted">${t.ms} ms</span>` : ''}<span class="muted small">${esc(extra || t.msg || '')}</span>${t.fix ? `<span class="small fix">Fix: ${esc(t.fix)}</span>` : ''}</div>`;
  const b = i.binance, f = market === 'futures' ? b.futures : b.spot, or = i.openrouter;
  const sysCls = m.system_state === 'blocked' ? 'blocked' : m.system_state === 'degraded' ? 'degraded' : 'safe';
  return `<div class="syslabel ${sysCls}">SYSTEM: ${esc(m.system_state.toUpperCase())}${m.blocked ? ' (NEW TRADES BLOCKED, open positions still managed)' : ''}</div><div class="tiles">` +
    tile('BINANCE ' + market.toUpperCase(), f) + tile('WORKER', { label: i.worker.label, status: i.worker.ok ? 'ok' : 'fail' }, i.worker.heartbeat_age == null ? 'never reported' : 'last signal ' + age(i.worker.heartbeat_age) + ' ago') + tile('DATABASE', { label: i.database.label, status: i.database.ok ? 'ok' : 'fail' }, i.database.error || '') +
    tile('OPENROUTER', or, `${or.calls_remaining}/${or.cap} calls left today · model ${or.model || 'none'}${or.state === 'paused' ? ' · AI paused' : ''}${or.last_error ? ' · ' + or.last_error : ''}`) + tile('TELEGRAM', i.telegram) + (market === 'futures' ? tile('CONDITIONAL ORDERS', b.algo) : '') + `</div>`;
}
export function statusLine(d, market) { const m = d.markets[market]; return `${pill('status-' + m.status.code, m.status.label)}`; }

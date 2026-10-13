// Renders REAL backend dashboards (JSON dumped by views.dashboard in the Python tests) through every renderer.
import fs from 'node:fs'; import path from 'node:path';
import * as R from '../../public/js/render.js';
const dir = process.argv[2], out = {};
for (const f of fs.readdirSync(dir).filter(x => x.endsWith('.json')).sort()) {
  const d = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8')), name = f.replace('.json', ''), issues = [], flags = {};
  for (const mk of ['futures', 'spot']) {
    const m = d.markets[mk], live = m.positions[0] ? { symbol: m.positions[0].symbol, price: m.positions[0].entry * 1.01 } : null;
    const parts = { brand: R.brand(d, mk), banners: R.banners(d, mk), stepper: R.stepper(m), pnl: R.pnlPanel(m), scan: R.scanPanel(m, { now: d.now, showAll: true }), levels: R.levelsPanel(m, live), position: R.positionPanel(m, live), setup: R.setupPanel(m), ai: R.aiPanel(m), timeline: R.timelinePanel(m), health: R.healthPanel(d, mk) };
    for (const [k, v] of Object.entries(parts)) { if (typeof v !== 'string') issues.push(`${mk}.${k}: not a string`); if (/undefined|NaN|\[object|null</.test(v)) issues.push(`${mk}.${k}: ${(v.match(/undefined|NaN|\[object|null</) || [])[0]}`); }
    flags[mk] = { banners: parts.banners, stepper: parts.stepper, setup: parts.setup, position: parts.position, levels: parts.levels, ai: parts.ai, timeline: parts.timeline, health: parts.health, scan: parts.scan, pnl: parts.pnl };
  }
  out[name] = { issues, flags };
}
console.log(JSON.stringify(out));

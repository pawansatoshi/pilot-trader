// Static preview (no browser needed): renders REAL backend dashboards (JSON from views.dashboard) with the real render.js into the real layout + CSS.
// The chart cannot be shown here: it needs the browser's connection to Binance.  Usage: node tools/render_preview.mjs <fixtures dir> <out dir>
import fs from 'node:fs'; import path from 'node:path'; import { fileURLToPath } from 'node:url';
import * as R from '../public/js/render.js';
const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..'), [fx, out] = process.argv.slice(2); fs.mkdirSync(out, { recursive: true });
const tpl = fs.readFileSync(path.join(root, 'public/index.html'), 'utf8'), css = fs.readFileSync(path.join(root, 'public/app.css'), 'utf8');
const PICK = { '01_empty': 'stopped, nothing recorded yet', '03_candidate': 'setup detected: NOT a trade yet', '04_position': 'open position (demo, simulated fill)', '06_blocked': 'new trades blocked by health gating', '07_locked': '+8% target reached: new trades locked', '08_loss': '-3% loss limit: new trades paused' };
for (const [name, title] of Object.entries(PICK)) {
  const f = path.join(fx, name + '.json'); if (!fs.existsSync(f)) continue; const d = JSON.parse(fs.readFileSync(f, 'utf8')), mk = 'futures', m = d.markets[mk], live = null, ctx = { now: d.now, live, showAll: true };
  const note = `<div class="banner degraded"><b>STATIC PREVIEW: ${title}</b><span>Rendered by public/js/render.js from backend output produced against a FAKE exchange in the test sandbox. The chart needs the browser connection to Binance and is not rendered here. This file is documentation, not part of the product.</span></div>`;
  let h = tpl.replace('<link rel="stylesheet" href="app.css">', `<style>${css}</style>`).replace(/<script[^>]*><\/script>/g, '').replace(/<script[^>]*>[\s\S]*?<\/script>/g, '');
  const fill = (id, html) => { h = h.replace(new RegExp(`(id="${id}"[^>]*>)(</div>|</section>)`), `$1${html.replace(/\$/g, '$$$$')}$2`); };
  fill('brand', R.brand(d, mk)); fill('banners', note + R.banners(d, mk)); fill('status-body', R.statusLine(d, mk) + R.stepper(m)); fill('pnl-body', R.pnlPanel(m)); fill('scanner-body', R.scanPanel(m, ctx)); fill('health-body', R.healthPanel(d, mk));
  fill('levels-body', R.levelsPanel(m, live)); fill('position-body', R.positionPanel(m, live)); fill('setup-body', R.setupPanel(m)); fill('ai-body', R.aiPanel(m)); fill('timeline-body', R.timelinePanel(m));
  h = h.replace('<div id="chart" role="img" aria-label="Candlestick chart"></div>', '<div id="chart" role="img" aria-label="Candlestick chart"><div class="empty" style="padding:90px 0;text-align:center">Live candlestick chart renders in the browser from the Binance WebSocket (5m / 15m / 1h / 4h). Not available in this static preview.</div></div>').replace('NO FEED', 'NO FEED (preview)');
  fs.writeFileSync(path.join(out, name + '.html'), h);
}
console.log(fs.readdirSync(out).join('\n'));

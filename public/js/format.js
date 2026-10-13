// Pure formatting helpers. No DOM, no network.
export const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const isNum = x => x !== null && x !== undefined && x !== '' && Number.isFinite(Number(x));
export const num = (x, d = 2) => isNum(x) ? Number(x).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }) : '–';
export const price = x => !isNum(x) ? '–' : Math.abs(x) >= 1000 ? num(x, 2) : Math.abs(x) >= 1 ? num(x, 4) : num(x, 6);
export const signed = (x, d = 2, suffix = '') => !isNum(x) ? '–' : (Number(x) > 0 ? '+' : '') + num(x, d) + suffix;
export const tone = x => !isNum(x) || Number(x) === 0 ? 'flat' : Number(x) > 0 ? 'up' : 'down';
export const clock = (ts, tz) => !isNum(ts) ? '–' : new Date(ts * 1000).toLocaleTimeString('en-GB', { hour12: false, timeZone: tz });
export const countdown = sec => !isNum(sec) ? '–' : sec <= 0 ? 'due' : `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, '0')}`;
export const age = sec => !isNum(sec) ? '–' : sec < 90 ? `${Math.round(sec)}s` : sec < 5400 ? `${Math.round(sec / 60)} min` : `${(sec / 3600).toFixed(1)} h`;

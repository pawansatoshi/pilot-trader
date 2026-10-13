LOGIN = r"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Pilot</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&display=swap" rel=stylesheet>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f3f5f9;font:16px 'IBM Plex Sans',system-ui,sans-serif;color:#0c2038}
.b{background:#fff;border:1px solid #e3e8f0;border-radius:16px;padding:28px;width:min(340px,88vw)}h1{margin:0 0 4px;font-size:24px}p{margin:0 0 18px;color:#6b7c93}
input{width:100%;box-sizing:border-box;padding:13px;border:1px solid #cfd8e6;border-radius:10px;font-size:16px;margin-bottom:12px}
button{width:100%;padding:13px;border:0;border-radius:10px;background:#2850d6;color:#fff;font-size:16px;font-weight:600}#e{color:#d6404e;min-height:20px;margin-top:10px}</style></head>
<body><div class=b><h1>Pilot</h1><p>Sign in to your trading dashboard</p><input id=p type=password placeholder=Password autofocus onkeydown="if(event.key=='Enter')go()"><button onclick=go()>Sign in</button><div id=e></div></div>
<script>async function go(){const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pw:p.value})});const j=await r.json();if(j.ok)location='/';else e.textContent=j.err}</script></body></html>"""

HTML = r"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Pilot</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel=stylesheet>
<script src="https://cdnjs.cloudflare.com/ajax/libs/lightweight-charts/4.1.3/lightweight-charts.standalone.production.js"></script>
<style>
:root{--bg:#f3f5f9;--card:#fff;--ink:#0c2038;--mut:#6b7c93;--line:#e3e8f0;--pri:#2850d6;--up:#0d9f6e;--dn:#d6404e;--warn:#b97a0a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 'IBM Plex Sans',system-ui,sans-serif;font-variant-numeric:tabular-nums;padding-bottom:84px}
body.real{border-top:4px solid var(--dn)}.wrap{max-width:880px;margin:auto;padding:12px}
header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}h1{margin:0;font-size:20px;letter-spacing:-.3px}
.seg{display:flex;background:#e6ebf3;border-radius:999px;padding:3px}.seg button{border:0;background:none;padding:7px 16px;border-radius:999px;font:inherit;font-weight:500;color:var(--mut);cursor:pointer}
.seg button.on{background:#fff;color:var(--ink);box-shadow:0 1px 2px #0002}.seg button.on.r{background:var(--dn);color:#fff}
.alert{background:#fff4f4;border:1px solid #f1c0c4;border-radius:12px;padding:10px 12px;margin-bottom:12px;color:#8f1d28}.alert.w{background:#fff9ec;border-color:#ecd49a;color:#7a5200}.alert b{display:block}
.mks{display:grid;gap:10px;grid-template-columns:1fr 1fr}@media(max-width:560px){.mks{grid-template-columns:1fr}}
.mk{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px;cursor:pointer}.mk.sel{border-color:var(--pri);box-shadow:0 0 0 1px var(--pri)}
.top{display:flex;justify-content:space-between;align-items:center}.top b{font-size:16px}.badge{font-size:12px;padding:3px 9px;border-radius:999px;background:#eef1f6;color:var(--mut)}
.badge.scanning{background:#e4f6ee;color:var(--up)}.badge.goal{background:#e4f6ee;color:var(--up)}.badge.loss,.badge.error{background:#fdeaec;color:var(--dn)}
.bal{font-size:28px;font-weight:600;letter-spacing:-.5px;margin:8px 0 2px}.bal small{font-size:13px;color:var(--mut);font-weight:400}
.meter{position:relative;height:8px;background:#eef1f6;border-radius:6px;margin:8px 0 4px}.meter i{position:absolute;top:0;height:8px;border-radius:6px}.meter u{position:absolute;top:-3px;width:2px;height:14px;background:var(--ink);opacity:.35}
.mut{color:var(--mut)}.up{color:var(--up)}.dn{color:var(--dn)}.sm{font-size:12px}
.now{display:flex;gap:8px;align-items:flex-start;margin:10px 0;min-height:40px}.dot{flex:none;width:9px;height:9px;border-radius:50%;background:#b8c3d3;margin-top:6px}.dot.live{background:var(--up);animation:p 1.6s infinite}
@keyframes p{50%{opacity:.35}}@media(prefers-reduced-motion:reduce){.dot.live{animation:none}}
.go{width:100%;padding:12px;border-radius:10px;border:1px solid var(--pri);background:var(--pri);color:#fff;font:inherit;font-weight:600;font-size:15px;cursor:pointer}.go.stop{background:#fff;color:var(--pri)}
.go:focus-visible,.seg button:focus-visible,.chip:focus-visible,.tab:focus-visible{outline:2px solid var(--pri);outline-offset:2px}
.pane{display:none;margin-top:12px}.pane.on{display:block}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px;margin-bottom:12px;overflow-x:auto}
.card h3{margin:0 0 8px;font-size:14px}.chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}.chip{border:1px solid var(--line);background:#fff;border-radius:999px;padding:5px 12px;font:inherit;cursor:pointer}.chip.on{background:var(--ink);color:#fff;border-color:var(--ink)}
#ch{height:320px}.px{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px}.px b{font-size:18px}
.feed div{display:flex;gap:10px;padding:7px 0;border-bottom:1px solid var(--line)}.feed div:last-child{border:0}.feed time{flex:none;color:var(--mut);font-size:12px;width:62px}.feed i{flex:none;width:8px;height:8px;border-radius:50%;margin-top:6px;background:#b8c3d3}
.feed .ok i{background:var(--up)}.feed .warn i{background:var(--warn)}.feed .err i{background:var(--dn)}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:7px 6px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}th{color:var(--mut);font-weight:500}.wrapc{white-space:normal;min-width:170px}
.pill{font-size:12px;padding:2px 8px;border-radius:999px}.pill.L{background:#e4f6ee;color:var(--up)}.pill.S{background:#fdeaec;color:var(--dn)}
.hl{display:flex;gap:10px;padding:9px 0;border-bottom:1px solid var(--line)}.hl:last-child{border:0}.hl .s{flex:none;width:22px;height:22px;border-radius:50%;display:grid;place-items:center;color:#fff;font-size:12px;background:#b8c3d3}
.hl .s.ok{background:var(--up)}.hl .s.fail{background:var(--dn)}.hl .s.warn{background:var(--warn)}.bar{height:8px;background:#eef1f6;border-radius:6px;margin:8px 0}.bar i{display:block;height:8px;border-radius:6px;background:var(--pri)}
nav{position:fixed;left:0;right:0;bottom:0;background:#fff;border-top:1px solid var(--line);display:flex;justify-content:center;padding-bottom:env(safe-area-inset-bottom)}
.tab{flex:1;max-width:220px;border:0;background:none;padding:13px 4px;font:inherit;color:var(--mut);cursor:pointer;font-weight:500}.tab.on{color:var(--pri);box-shadow:inset 0 2px 0 var(--pri)}
.ghost{border:1px solid var(--line);background:#fff;border-radius:10px;padding:8px 12px;font:inherit;cursor:pointer}
</style></head><body><div class=wrap>
<header><h1>Pilot</h1><div class=seg><button id=bd onclick="setMode('demo')">Demo</button><button id=br onclick="setMode('real')">Real</button></div></header>
<div id=al></div>
<div class=mks><article class=mk id=mk-futures onclick="pick('futures')"></article><article class=mk id=mk-spot onclick="pick('spot')"></article></div>
<section class="pane on" id=p-live>
 <div class=card><div class=px><span id=cs class=chips></span><span class="mut sm" id=lv></span></div><div class=px><b id=pr>-</b><span class="mut sm" id=sy></span></div><div id=ch></div></div>
 <div class=card><h3>What the bot is doing</h3><div class=feed id=feed></div></div></section>
<section class=pane id=p-trades>
 <div class=card><h3>Open positions</h3><div id=op></div><p><button class=ghost onclick=closeAll()>Close all positions</button> <button class=ghost id=rs onclick=resetDemo()>Reset demo balance</button></p></div>
 <div class=card><h3>Daily results (compounding)</h3><div id=dy></div></div>
 <div class=card><h3>Closed trades</h3><div id=cl></div></div></section>
<section class=pane id=p-ai>
 <div class=card><h3>Free AI budget today</h3><div id=ab></div></div>
 <div class=card><h3>AI scorecard (net R, measured)</h3><div id=sc></div></div>
 <div class=card><h3>AI decisions</h3><div id=dc></div></div></section>
<section class=pane id=p-health><div class=card><h3>System health</h3><div id=hl></div></div></section>
</div>
<nav><button class="tab on" data-t=live onclick="tabTo('live')">Live</button><button class=tab data-t=trades onclick="tabTo('trades')">Trades</button><button class=tab data-t=ai onclick="tabTo('ai')">AI</button><button class=tab data-t=health onclick="tabTo('health')">Health</button></nav>
<script>
const $=s=>document.querySelector(s);let S=null,sel='futures',sym=null,chart,series,lines=[],ws=null;
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const n=(x,d=2)=>x==null||isNaN(x)?'-':Number(x).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const hm=t=>new Date(t*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'});
const dt=t=>new Date(t*1000).toLocaleString([],{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
const cls=x=>x>=0?'up':'dn',NAME={futures:'Futures',spot:'Spot'};
const BADGE={scanning:'Running',stopped:'Stopped',goal:'Goal reached',loss:'Paused (loss limit)',error:'Needs attention'};
async function api(p,b){try{const r=await fetch(p,b?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}:{});if(r.status==401){location='/';return null}return await r.json()}catch(e){return{err:'Cannot reach the dashboard server. Check your internet.'}}}
function tabTo(t){document.querySelectorAll('.pane').forEach(x=>x.classList.toggle('on',x.id=='p-'+t));document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x.dataset.t==t));if(t=='live'&&chart)chart.applyOptions({})}
function pick(m){sel=m;sym=null;render()}
async function setMode(m){const b={mode:m};if(m=='real'){if(!S.real_allowed)return alert('Real mode is locked. Set ALLOW_REAL=1 on Vercel and on the Voroa worker.');
 if(!confirm('REAL mode trades with live money. Continue?'))return;b.confirm=prompt('Type REAL to confirm')}
 const r=await api('/api/mode',b);if(r&&r.err)alert(r.err);load()}
async function toggle(e,m){e.stopPropagation();const on=!S.m[m].running;
 if(on&&S.mode=='real'&&!confirm('Start '+NAME[m]+' with REAL money?'))return;
 const r=await api('/api/market',{market:m,on});if(r&&r.err)alert(r.err);load()}
async function closeAll(){if(confirm('Close all open '+NAME[sel]+' positions at market price?')){await api('/api/closeall',{market:sel});load()}}
async function resetDemo(){if(S.mode!='demo')return alert('Switch to Demo first.');const a=prompt('New demo balance (USDT)','1000');if(a&&confirm('Delete all demo '+NAME[sel]+' trades?')){await api('/api/reset',{market:sel,amount:a});load()}}
function tbl(h,r){return r.length?'<table><tr>'+h.map(x=>'<th>'+x+'</th>').join('')+'</tr>'+r.join('')+'</table>':'<span class=mut>Nothing yet.</span>'}
function cards(){const c=S.cfg,T=c.loss+c.target,z=c.loss/T*100;
 for(const m of['futures','spot']){const x=S.m[m],dp=x.dpnl||0,w=Math.min(Math.abs(dp),dp>=0?c.target:c.loss)/T*100,l=dp>=0?z:z-w;
  const el=$('#mk-'+m);el.className='mk'+(sel==m?' sel':'');
  el.innerHTML=`<div class=top><b>${NAME[m]}</b><span class="badge ${x.state}">${BADGE[x.state]||x.state}</span></div>
  <div class=bal>${x.eq==null?'-':n(x.eq)} <small>USDT</small></div>
  <div class=meter><i style="left:${l}%;width:${w}%;background:${dp>=0?'var(--up)':'var(--dn)'}"></i><u style="left:${z}%"></u></div>
  <div class="sm mut">Today <b class=${cls(dp)}>${dp>=0?'+':''}${n(dp)}%</b> &nbsp;·&nbsp; goal +${c.target}% &nbsp;·&nbsp; stop -${c.loss}%</div>
  <div class=now><span class="dot ${x.state=='scanning'?'live':''}"></span><span>${esc(x.now)}<span class="mut sm" data-next="${x.next}"></span></span></div>
  <button class="go ${x.running?'stop':''}" onclick="toggle(event,'${m}')">${x.running?'Stop':'Start'} ${NAME[m]}</button>`}}
function tick(){document.querySelectorAll('[data-next]').forEach(e=>{const t=+e.dataset.next-Date.now()/1000;e.textContent=t>0&&t<3600&&e.closest('.mk').querySelector('.badge.scanning')?' · next scan '+Math.floor(t/60)+':'+String(Math.floor(t%60)).padStart(2,'0'):''})}
function problems(){const a=[];if(S.db_error)a.push(['err','Dashboard cannot reach the database: '+S.db_error,S.db_fix]);
 else{if(S.hb_age==null)a.push(['err','Worker has never reported.','Deploy the Voroa worker (python bot.py) with the same DATABASE_URL.']);
 else if(S.hb_age>150)a.push(['err','Worker is offline (last signal '+Math.round(S.hb_age/60)+' min ago).','Open Voroa > Worker > Logs. Usual causes: crashed, wrong DATABASE_URL, free instance asleep.']);
 (S.health||[]).filter(h=>h.status=='fail'||h.status=='warn').forEach(h=>a.push([h.status=='fail'?'err':'w',h.name+': '+h.msg,h.fix]))}
 $('#al').innerHTML=a.map(p=>`<div class="alert ${p[0]=='w'?'w':''}"><b>${esc(p[1])}</b>${p[2]?'Fix: '+esc(p[2]):''}</div>`).join('')}
function render(){if(!S)return;if(S.db_error){problems();return}
 $('#bd').className=S.mode=='demo'?'on':'';$('#br').className=S.mode=='real'?'on r':'';document.body.classList.toggle('real',S.mode=='real');$('#rs').style.display=S.mode=='demo'?'':'none';
 problems();cards();tick();
 const op=S.open.filter(t=>t.market==sel),cl=S.closed.filter(t=>t.market==sel);
 $('#op').innerHTML=tbl(['Coin','Side','Entry','Stop','Target','Size'],op.map(t=>`<tr><td>${esc(t.symbol)}</td><td><span class="pill ${t.side[0]}">${t.side}</span></td><td>${n(t.entry,5)}</td><td>${n(t.sl,5)}</td><td>${n(t.tp,5)}</td><td>${n(t.qty,4)}</td></tr>`));
 $('#cl').innerHTML=tbl(['Closed','Coin','Side','Result','P&L'],cl.map(t=>`<tr><td>${dt(t.closed)}</td><td>${esc(t.symbol)}</td><td>${t.side}</td><td>${esc(t.reason)}</td><td class=${cls(t.pnl)}>${n(t.pnl)}</td></tr>`));
 $('#dy').innerHTML=tbl(['Day','Start','End','Change'],S.daily.filter(d=>d.market==sel).map(d=>`<tr><td>${esc(d.day)}</td><td>${n(d.s_eq)}</td><td>${n(d.e_eq)}</td><td class=${cls(d.e_eq-d.s_eq)}>${n((d.e_eq/d.s_eq-1)*100)}%</td></tr>`));
 $('#dc').innerHTML=tbl(['Time','Coin','Score','Decision','By','Why'],S.dec.filter(d=>d.market==sel).map(d=>`<tr><td>${dt(d.ts)}</td><td>${esc(d.symbol)}</td><td>${n(d.score,1)}</td><td>${esc(d.action)}</td><td>${esc(d.source)}</td><td class=wrapc>${esc(d.why)}</td></tr>`));
 const M=S.metrics||{},g=x=>x&&x.n?`${x.n} trades · win ${x.win_pct}% · avg ${n(x.avg_r)}R · total ${n(x.sum_r)}R`:'no closed samples yet';
 $('#sc').innerHTML=`<div>AI-approved (traded): <b>${g(M.ai_approved)}</b></div><div>AI-rejected (shadow): <b>${g(M.ai_rejected_shadow)}</b></div><div>AI unavailable/invalid (shadow): <b>${g(M.ai_unavailable_shadow)}</b></div><div>Rules only (all): <b>${g(M.rules_only)}</b></div><div class=sm style='margin-top:6px'>AI veto value: <b>${n(M.ai_veto_value_r)}R</b> (positive = AI saved R) · false rejects ${M.false_rejects||0} · correct rejects ${M.correct_rejects||0} · losing approvals ${M.losing_approvals||0} · still tracking ${M.still_tracking||0}</div>`;
 const pc=Math.min(100,S.llm_used/S.cfg.cap*100);$('#ab').innerHTML=`<b>${S.llm_used} / ${S.cfg.cap}</b> calls used today <span class=mut>(resets 00:00 UTC)</span><div class=bar><i style="width:${pc}%"></i></div><div class="sm mut">Model: ${esc(S.model||'-')}. The bot asks AI only for high-probability setups, otherwise it uses strict rules.${S.llm_err?'<br>Last AI problem: '+esc(S.llm_err):''}</div>`;
 const hl=[{status:S.hb_age!=null&&S.hb_age<150?'ok':'fail',name:'Worker (Voroa)',msg:S.hb_age==null?'Never reported':S.hb_age<150?'Running':'Offline for '+Math.round(S.hb_age/60)+' min'},...(S.health||[])];
 $('#hl').innerHTML=hl.map(h=>`<div class=hl><span class="s ${h.status}">${h.status=='ok'?'✓':h.status=='fail'?'!':h.status=='warn'?'!':'–'}</span><div><b>${esc(h.name)}</b><div class=mut>${esc(h.msg)}</div>${h.fix?'<div class=sm>Fix: '+esc(h.fix)+'</div>':''}</div></div>`).join('');
 $('#feed').innerHTML=S.logs.filter(l=>!l.market||l.market==sel).slice(0,40).map(l=>`<div class="${l.level}"><time>${hm(l.ts)}</time><i></i><span>${esc(l.msg)}</span></div>`).join('')||'<span class=mut>Press Start. Every step the bot takes will appear here.</span>';
 const syms=[...new Set([...op.map(t=>t.symbol),...S.charts.filter(c=>c.market==sel).map(c=>c.symbol)])];
 if(!sym||!syms.includes(sym)){sym=syms[0]||null}
 $('#cs').innerHTML=syms.map(s=>`<button class="chip ${s==sym?'on':''}" onclick="selSym('${s}')">${esc(s.replace('USDT',''))}</button>`).join('')||'<span class=mut>Chart appears after the worker starts.</span>';
 if(sym&&sym!==loaded)loadChart()}
let loaded=null;function selSym(s){sym=s;render()}
async function loadChart(){const m=sel,s=sym;loaded=s;const d=await api('/api/chart?symbol='+s+'&market='+m);if(!d||!d.k||sym!==s)return;
 if(!chart){chart=LightweightCharts.createChart($('#ch'),{layout:{background:{color:'#ffffff'},textColor:'#6b7c93'},grid:{vertLines:{color:'#eef1f6'},horzLines:{color:'#eef1f6'}},rightPriceScale:{borderColor:'#e3e8f0'},timeScale:{timeVisible:true,borderColor:'#e3e8f0'},autoSize:true});
  series=chart.addCandlestickSeries({upColor:'#0d9f6e',downColor:'#d6404e',borderVisible:false,wickUpColor:'#0d9f6e',wickDownColor:'#d6404e'})}
 series.setData(d.k.map(c=>({time:c[0]/1000,open:c[1],high:c[2],low:c[3],close:c[4]})));
 lines.forEach(l=>series.removePriceLine(l));lines=[];const mk=[],r=t=>Math.floor(t/900)*900;
 d.trades.forEach(t=>{const L=t.side=='LONG';mk.push({time:r(t.opened),position:L?'belowBar':'aboveBar',color:L?'#0d9f6e':'#d6404e',shape:L?'arrowUp':'arrowDown',text:L?'BUY':'SELL'});
  if(t.status=='CLOSED')mk.push({time:r(t.closed),position:'inBar',color:'#b97a0a',shape:'circle',text:t.reason+' '+n(t.pnl)});
  else[['entry','#6b7c93'],['sl','#d6404e'],['tp','#0d9f6e']].forEach(([k,c])=>lines.push(series.createPriceLine({price:t[k],color:c,title:k=='sl'?'STOP':k=='tp'?'TARGET':'ENTRY',lineWidth:1})))});
 series.setMarkers(mk.sort((a,b)=>a.time-b.time));const last=d.k[d.k.length-1];if(last)$('#pr').textContent=n(last[4],last[4]<1?5:2);$('#sy').textContent=s+' · 15m · '+NAME[m];
 $('#lv').textContent='snapshot '+Math.round(Date.now()/1000-d.ts)+'s old';
 if(ws)try{ws.close()}catch(e){}
 try{ws=new WebSocket((m=='futures'?'wss://fstream.binance.com/ws/':'wss://stream.binance.com:9443/ws/')+s.toLowerCase()+'@kline_15m');
  ws.onopen=()=>$('#lv').innerHTML='<span class=up>● live</span>';
  ws.onmessage=e=>{const k=JSON.parse(e.data).k;series.update({time:k.t/1000,open:+k.o,high:+k.h,low:+k.l,close:+k.c});$('#pr').textContent=n(+k.c,+k.c<1?5:2)}}catch(e){}}
async function load(){const s=await api('/api/state');if(!s)return;S=s;render()}
load();setInterval(load,8000);setInterval(tick,1000);
</script></body></html>"""

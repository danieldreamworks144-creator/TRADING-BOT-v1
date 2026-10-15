"""The trading platform dashboard: dark navy, purple/blue accents, live panels.

Single self-contained HTML page. It polls /api/state and /api/action so every
card, chart and button is backed by real backend data -- no fake numbers, no
hardcoded fills. Demo mode is indicated in a persistent banner and on every
simulated record.

Layout: left sidebar nav, account overview, live market chart, OKX + Bybit
connection panels, simulated balances, open simulated positions, trade history,
P&L, bot controls, risk settings, live activity feed with timestamps."""
from __future__ import annotations

import json

# Dark navy theme tokens (page is standalone, not a Gideon widget, so fixed
# palette is fine here -- it is the product's own brand theme).
CSS = """
:root{--bg:#0a0f1e;--panel:#111a2e;--panel2:#16223a;--border:#22304d;--text:#e6edf7;
--muted:#7f92b0;--accent:#7c5cff;--accent2:#2f80ed;--ok:#22c55e;--warn:#f5a623;--danger:#ef4444;
--nav:#0c1425}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:14px/1.5 ui-sans-serif,system-ui,Segoe UI,Roboto,sans-serif}
.shell{display:grid;grid-template-columns:230px 1fr;min-height:100vh}
.sidebar{background:var(--nav);border-right:1px solid var(--border);padding:22px 16px;position:sticky;top:0;height:100vh}
.brand{font-size:18px;font-weight:750;letter-spacing:.3px;margin-bottom:4px}
.brand span{background:linear-gradient(90deg,var(--accent),var(--accent2));-webkit-background-clip:text;background-clip:text;color:transparent}
.brand-sub{font-size:11px;color:var(--muted);margin-bottom:26px}
.nav a{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:9px;color:var(--muted);
text-decoration:none;font-size:13px;margin:2px 0;cursor:pointer}
.nav a.active{background:linear-gradient(90deg,var(--accent),var(--accent2));color:#fff}
.nav a:hover:not(.active){background:var(--panel);color:var(--text)}
.nav .dot{width:7px;height:7px;border-radius:99px;background:currentColor;opacity:.7}
.main{padding:22px 26px 60px}
.demo-banner{background:linear-gradient(90deg,#2a1f00,#3a2a00);border:1px solid var(--warn);
color:#ffd27a;padding:11px 16px;border-radius:11px;font-size:12.5px;display:flex;
align-items:center;gap:11px;margin-bottom:18px}
.demo-banner .pill{background:var(--warn);color:#221800;font-weight:750;padding:3px 11px;border-radius:99px;font-size:11px;letter-spacing:.4px}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}
.topbar h1{font-size:21px;margin:0}
.sync{font-size:11.5px;color:var(--muted);text-align:right}
.sync b{color:var(--ok)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(178px,1fr));gap:13px;margin-bottom:18px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:13px;padding:15px 16px}
.card .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.card .val{font-size:23px;font-weight:680;margin:5px 0 2px;font-variant-numeric:tabular-nums}
.card .sub{font-size:11px;color:var(--muted)}
.val.ok{color:var(--ok)}.val.bad{color:var(--danger)}.val.accent{color:var(--accent)}
.grid2{display:grid;grid-template-columns:1.7fr 1fr;gap:15px}
.grid3{display:grid;grid-template-columns:1fr 1fr;gap:15px}
.panel{background:var(--panel);border:1px solid var(--border);border-radius:13px;padding:16px;margin-bottom:15px}
.panel h2{font-size:13px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);margin:0 0 12px;display:flex;justify-content:space-between;align-items:center}
.tag{font-size:10px;padding:2px 9px;border-radius:99px;letter-spacing:.4px;font-weight:700}
.tag.demo{background:#3a2a00;color:#ffd27a}.tag.ok{background:#0d3320;color:#4ade80}
.tag.err{background:#3a1414;color:#fca5a5}.tag.pub{background:#1e2a45;color:#9db8e6}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);
text-align:right;padding:7px 8px;border-bottom:1px solid var(--border)}
th:first-child,td:first-child{text-align:left}
td{padding:8px;font-size:12.5px;text-align:right;border-bottom:1px solid var(--border)}
tr:hover td{background:var(--panel2)}
.pos{color:var(--ok)}.neg{color:var(--danger)}
.feed{max-height:360px;overflow-y:auto}
.feed .item{display:flex;gap:10px;padding:8px 4px;border-bottom:1px solid var(--border);font-size:12px}
.feed .t{color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap;font-size:11px}
.feed .k{padding:1px 8px;border-radius:6px;font-size:9.5px;font-weight:700;height:fit-content;text-transform:uppercase}
.k.trade{background:#1e2a45;color:#9db8e6}.k.risk{background:#3a1414;color:#fca5a5}
.k.engine{background:#2a1f4a;color:#c4b5fd}.k.error{background:#3a1414;color:#fca5a5}
.k.system{background:#16223a;color:var(--muted)}
.conn{display:flex;align-items:center;justify-content:space-between;padding:12px 14px;
background:var(--panel2);border:1px solid var(--border);border-radius:11px;margin-bottom:9px}
.conn .name{font-weight:650;font-size:14px}.conn .meta{font-size:11px;color:var(--muted);margin-top:2px}
.conn .px{font-variant-numeric:tabular-nums;font-size:15px;font-weight:650}
button{background:linear-gradient(90deg,var(--accent),var(--accent2));color:#fff;border:0;
padding:9px 16px;border-radius:9px;font-size:12.5px;font-weight:620;cursor:pointer}
button:hover{filter:brightness(1.1)}button.ghost{background:var(--panel2);color:var(--text);border:1px solid var(--border)}
button.danger{background:linear-gradient(90deg,#b91c1c,#ef4444)}
button:disabled{opacity:.5;cursor:not-allowed}
.controls{display:flex;flex-wrap:wrap;gap:9px;align-items:center}
input,select{background:var(--panel2);border:1px solid var(--border);color:var(--text);
padding:8px 11px;border-radius:9px;font-size:12.5px}
label.field{font-size:11px;color:var(--muted);display:flex;flex-direction:column;gap:5px}
.risk-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.chart-wrap{position:relative}
svg.chart{width:100%;height:230px;display:block}
.axis{stroke:var(--border);stroke-width:1}
.err{color:var(--danger);font-size:12px}
.muted{color:var(--muted)}
footer{color:var(--muted);font-size:11px;margin-top:20px;line-height:1.7}
@media(max-width:920px){.shell{grid-template-columns:1fr}.sidebar{position:relative;height:auto}.grid2,.grid3{grid-template-columns:1fr}}
"""

JS = """
const state = { tab:'overview' };
async function api(path, body){
  const r = await fetch(path, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : undefined);
  return r.json();
}
function money(v, dp=2){ return (v==null?'—':Number(v).toLocaleString(undefined,{minimumFractionDigits:dp,maximumFractionDigits:dp})); }
function pnlCls(v){ return v==null?'':(v>=0?'pos':'neg'); }
function pnlSign(v){ return v==null?'—':(v>=0?'+':'')+money(v); }
function tsShort(s){ return s? s.replace('T',' ').replace('+00:00','') : ''; }

function drawChart(candles){
  const svg = document.getElementById('chart'); if(!svg) return;
  if(!candles || candles.length<2){ svg.innerHTML='<text x="12" y="24" fill="#7f92b0" font-size="12">waiting for market data…</text>'; return; }
  const w=svg.clientWidth||600, h=230, pad=14;
  const closes=candles.map(c=>c.close); const min=Math.min(...closes), max=Math.max(...closes); const span=(max-min)||1;
  const x=i=>pad+i*(w-2*pad)/(closes.length-1); const y=v=>pad+(max-v)*(h-2*pad)/span;
  let d='M'+closes.map((v,i)=>x(i).toFixed(1)+','+y(v).toFixed(1)).join(' L');
  let area=d+` L${x(closes.length-1).toFixed(1)},${h-pad} L${x(0).toFixed(1)},${h-pad} Z`;
  const up=closes[closes.length-1]>=closes[0]; const col=up?'#22c55e':'#ef4444';
  svg.innerHTML=`<defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1">
    <stop offset="0" stop-color="${col}" stop-opacity="0.35"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>
    <line class="axis" x1="${pad}" y1="${h-pad}" x2="${w-pad}" y2="${h-pad}"/>
    <path d="${area}" fill="url(#g)"/><path d="${d}" fill="none" stroke="${col}" stroke-width="2"/>
    <text x="${pad}" y="16" fill="#7f92b0" font-size="11">high ${money(max)}</text>
    <text x="${pad}" y="${h-4}" fill="#7f92b0" font-size="11">low ${money(min)}</text>`;
}

function render(s){
  const a=s.account||{};
  document.getElementById('sync').innerHTML=`last sync <b>${tsShort(s.ts)}</b> · UTC`;
  document.getElementById('equity').textContent=money(a.equity);
  document.getElementById('cash').textContent=money(a.cash);
  document.getElementById('posval').textContent=money(a.positions_value);
  const upnl=a.unrealized_pnl||0, rpnl=a.realized_pnl||0, tp=a.total_pnl||0;
  const e1=document.getElementById('upnl'); e1.textContent=pnlSign(upnl); e1.className='val '+pnlCls(upnl);
  const e2=document.getElementById('rpnl'); e2.textContent=pnlSign(rpnl); e2.className='val '+pnlCls(rpnl);
  const e3=document.getElementById('tpnl'); e3.textContent=pnlSign(tp); e3.className='val '+pnlCls(tp);
  document.getElementById('equity').className='val '+pnlCls(tp);
  document.getElementById('nopen').textContent=a.open_positions||0;

  // connections
  const cc=document.getElementById('conns'); cc.innerHTML='';
  (s.connections||[]).forEach(c=>{
    const cls=c.status==='connected'?'ok':(c.status==='error'?'err':'pub');
    const price=c.last_price!=null?money(c.last_price):'—';
    cc.insertAdjacentHTML('beforeend',`<div class="conn"><div>
      <div class="name">${c.exchange.toUpperCase()} <span class="tag ${cls}">${c.status}</span>
      ${c.has_credentials?'<span class="tag demo">creds</span>':'<span class="tag pub">public data</span>'}</div>
      <div class="meta">${c.detail||''} · ${c.credential_hint||''}</div></div>
      <div class="px">${price}</div></div>`);
  });

  // positions
  const pt=document.getElementById('positions');
  if(!s.positions || s.positions.length===0){ pt.innerHTML='<tr><td colspan="6" class="muted" style="text-align:center">no open simulated positions</td></tr>'; }
  else{ pt.innerHTML=s.positions.map(p=>{
    const upl=p.unrealized_pnl||0;
    return `<tr><td>${p.pair}</td><td>${money(p.amount,6)}</td><td>${money(p.entry_price)}</td>
      <td>${money(p.mark_price)}</td><td class="${pnlCls(upl)}">${pnlSign(upl)}</td>
      <td><span class="tag demo">demo</span></td></tr>`;}).join(''); }

  // trade history
  const tt=document.getElementById('trades');
  if(!s.trades || s.trades.length===0){ tt.innerHTML='<tr><td colspan="7" class="muted" style="text-align:center">no simulated trades yet</td></tr>'; }
  else{ tt.innerHTML=s.trades.map(o=>{
    const p=o.pnl!=null?`<span class="${pnlCls(o.pnl)}">${pnlSign(o.pnl)}</span>`:'<span class="muted">—</span>';
    return `<tr><td>${tsShort(o.ts)}</td><td class="${o.side==='buy'?'pos':'neg'}">${o.side.toUpperCase()}</td>
      <td>${money(o.amount,6)}</td><td>${money(o.price)}</td><td>${money(o.fee,4)}</td>
      <td>${p}</td><td><span class="tag demo">demo</span></td></tr>`;}).join(''); }

  // feed
  const fd=document.getElementById('feed');
  fd.innerHTML=(s.events||[]).map(e=>`<div class="item">
    <span class="t">${tsShort(e.ts)}</span><span class="k ${e.kind}">${e.kind}</span>
    <span>${e.message}</span></div>`).join('') || '<div class="muted">no events</div>';

  // bot + risk
  const running=s.bot && s.bot.running;
  document.getElementById('botstate').innerHTML= running
    ? '<span class="tag ok">RUNNING</span>' : '<span class="tag demo">STOPPED</span>';
  const rl=s.risk||{};
  document.getElementById('riskstatus').innerHTML= rl.paused
    ? `<span class="tag err">PAUSED</span> <span class="err">${rl.pause_reason||''}</span>`
    : '<span class="tag ok">WITHIN LIMITS</span>';
  document.getElementById('daypnl').textContent=pnlSign(rl.day_realized_pnl);

  // errors
  const el=document.getElementById('errors');
  el.innerHTML=(s.errors||[]).slice(0,6).map(e=>`<div class="item"><span class="t">${tsShort(e.ts)}</span><span class="k error">err</span><span>${e.where}: ${e.message}</span></div>`).join('')
    || '<div class="muted">no errors</div>';

  if(s.candles) drawChart(s.candles);
}

function setTab(t){ state.tab=t; document.querySelectorAll('.nav a').forEach(x=>x.classList.remove('active'));
  const el=document.querySelector(`.nav a[data-tab="${t}"]`); if(el) el.classList.add('active');
  ['overview','trades','risk','system'].forEach(id=>{ const p=document.getElementById('panel-'+id); if(p)p.style.display=(id===t)?'block':'none'; });
}

async function refresh(){ try{ const s=await api('/api/state'); render(s); }catch(e){ document.getElementById('sync').innerHTML='<span class="err">backend unreachable</span>'; } }
async function action(name, payload){ await api('/api/action', Object.assign({name},payload||{})); await refresh(); }

window.addEventListener('resize',()=>refresh());
document.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('.nav a').forEach(a=>a.addEventListener('click',e=>{e.preventDefault();setTab(a.dataset.tab);}));
  document.getElementById('btn-start').addEventListener('click',()=>action('start'));
  document.getElementById('btn-stop').addEventListener('click',()=>action('stop'));
  document.getElementById('btn-step').addEventListener('click',()=>action('step'));
  document.getElementById('btn-buy').addEventListener('click',()=>action('manual',{side:'buy'}));
  document.getElementById('btn-sell').addEventListener('click',()=>action('manual',{side:'sell'}));
  document.getElementById('btn-reset').addEventListener('click',()=>action('reset_day'));
  document.getElementById('btn-risk').addEventListener('click',()=>action('risk',{
    max_position_usd:parseFloat(document.getElementById('r_pos').value),
    max_daily_loss_usd:parseFloat(document.getElementById('r_loss').value),
    max_open_positions:parseInt(document.getElementById('r_open').value),
    max_total_exposure_usd:parseFloat(document.getElementById('r_exp').value)}));
  document.getElementById('btn-restart').addEventListener('click',()=>action('restart'));
  refresh(); setInterval(refresh, 3000);
});
"""


def render(state_bootstrap: dict) -> str:
    s = json.dumps(state_bootstrap)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trading Platform — Demo</title><style>{CSS}</style></head><body>
<div class="shell">
<aside class="sidebar">
  <div class="brand"><span>◆</span> Apex<span>Trade</span></div>
  <div class="brand-sub">automated trading platform</div>
  <nav class="nav">
    <a data-tab="overview" class="active"><span class="dot"></span>Overview</a>
    <a data-tab="trades"><span class="dot"></span>Trades &amp; Positions</a>
    <a data-tab="risk"><span class="dot"></span>Risk &amp; Controls</a>
    <a data-tab="system"><span class="dot"></span>System &amp; Exchange</a>
  </nav>
  <div style="margin-top:28px;padding:12px;background:var(--panel);border:1px solid var(--border);border-radius:11px">
    <div class="lbl" style="font-size:10px;color:var(--muted);text-transform:uppercase">mode</div>
    <div style="font-size:15px;font-weight:700;color:#ffd27a">DEMO / PAPER</div>
    <div class="muted" style="font-size:10.5px;margin-top:4px">All trades simulated. No real orders. No withdrawals.</div>
  </div>
</aside>
<main class="main">
  <div class="demo-banner"><span class="pill">DEMO MODE</span>
    Every balance, position and trade below is SIMULATED against live market prices.
    Real trading is disabled and not built into this platform.</div>
  <div class="topbar"><h1>Account Overview</h1><div class="sync" id="sync">connecting…</div></div>

  <div id="panel-overview">
    <div class="cards">
      <div class="card"><div class="lbl">Total equity</div><div class="val accent" id="equity">—</div><div class="sub">simulated</div></div>
      <div class="card"><div class="lbl">Available cash</div><div class="val" id="cash">—</div><div class="sub">simulated</div></div>
      <div class="card"><div class="lbl">In positions</div><div class="val" id="posval">—</div><div class="sub">mark-to-market</div></div>
      <div class="card"><div class="lbl">Unrealized P&amp;L</div><div class="val" id="upnl">—</div><div class="sub">open positions</div></div>
      <div class="card"><div class="lbl">Realized P&amp;L</div><div class="val" id="rpnl">—</div><div class="sub">closed trades</div></div>
      <div class="card"><div class="lbl">Total P&amp;L</div><div class="val" id="tpnl">—</div><div class="sub">since start</div></div>
    </div>
    <div class="grid2">
      <div class="panel"><h2>Live market chart <span class="tag ok">LIVE DATA</span></h2>
        <div class="chart-wrap"><svg class="chart" id="chart"></svg></div></div>
      <div class="panel"><h2>Exchange connections</h2><div id="conns"></div>
        <div class="muted" style="font-size:11px;margin-top:8px">Public market data is fetched live. API secrets are stored
        on the backend only, never sent to this page and never logged.</div></div>
    </div>
  </div>

  <div id="panel-trades" style="display:none">
    <div class="panel"><h2>Open simulated positions</h2>
      <table><thead><tr><th>pair</th><th>amount</th><th>entry</th><th>mark</th><th>unrealized</th><th>mode</th></tr></thead>
      <tbody id="positions"></tbody></table></div>
    <div class="panel"><h2>Trade history</h2>
      <table><thead><tr><th>time</th><th>side</th><th>amount</th><th>price</th><th>fee</th><th>p&amp;l</th><th>mode</th></tr></thead>
      <tbody id="trades"></tbody></table></div>
    <div class="panel"><h2>Live activity feed</h2><div class="feed" id="feed"></div></div>
  </div>

  <div id="panel-risk" style="display:none">
    <div class="panel"><h2>Bot controls <span id="botstate"></span></h2>
      <div class="controls">
        <button id="btn-start">Start bot</button>
        <button id="btn-stop" class="ghost">Stop</button>
        <button id="btn-step" class="ghost">Run one step</button>
        <button id="btn-buy" class="ghost">Manual demo buy</button>
        <button id="btn-sell" class="ghost">Manual demo sell</button>
        <button id="btn-restart" class="ghost">Restart (rebuild from DB)</button>
      </div>
      <div class="muted" style="font-size:11px;margin-top:9px">The engine evaluates an SMA crossover on live candles, executes
      SIMULATED fills, and records why each trade triggered.</div></div>
    <div class="panel"><h2>Risk management <span id="riskstatus"></span></h2>
      <div class="risk-grid">
        <label class="field">Max position (USD)<input id="r_pos" type="number" value="500"></label>
        <label class="field">Max daily loss (USD)<input id="r_loss" type="number" value="100"></label>
        <label class="field">Max open positions<input id="r_open" type="number" value="5"></label>
        <label class="field">Max total exposure (USD)<input id="r_exp" type="number" value="1500"></label>
      </div>
      <div class="controls" style="margin-top:12px"><button id="btn-risk">Save limits</button>
        <button id="btn-reset" class="ghost">Reset daily loss</button>
        <span class="muted" style="font-size:12px">Today's realized P&amp;L: <b id="daypnl">—</b></span></div>
      <div class="muted" style="font-size:11px;margin-top:9px">Trading auto-pauses when any limit is reached and stays paused
      until a limit is reset or adjusted.</div></div>
    <div class="panel"><h2>Errors</h2><div class="feed" id="errors"></div></div>
  </div>

  <div id="panel-system" style="display:none">
    <div class="panel"><h2>Security &amp; data</h2>
      <ul class="muted" style="font-size:12px;line-height:1.9">
        <<li>API secrets are read from environment variables on the backend only.</li>
        <li>Secrets are never written to logs, the database, or the frontend.</li>
        <li>Credential validation confirms presence without exposing values.</li>
        <li>The platform has no withdrawal, transfer or funding capability.</li>
        <li>All stored trades are tagged <b>mode=demo</b> and are simulated.</li>
        <li>Real-money trading is not implemented and cannot be enabled here.</li>
      </ul></div>
  </div>

  <footer>ApexTrade demo platform · simulated trading only · not financial advice ·
    market data from OKX/Bybit public endpoints · no real orders are ever placed.</footer>
</main>
</div>
<script>{JS}</script>
<script>window.__BOOT__={s};</script>
</body></html>"""

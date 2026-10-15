"""Trading console UI: one self-contained HTML file, zero CDN, zero build step.

Design rules:
- Open the file, it works. No network, no tailwind CDN, no chart library --
  vanilla CSS + SVG + about 60 lines of JS (tabs, sortable columns, tooltips).
- The UI has one job: make the honest numbers impossible to skim past. Equity
  vs buy&hold is always shown together; the ENSEMBLE row is always visible in
  the leaderboard; the "no real orders" status is never hidden.
- Theme: CSS variables with dark fallbacks so it matches the Gideon dashboard
  when embedded and still looks right opened straight from disk."""
from __future__ import annotations

import html
import json
import os

REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

# ---------------------------------------------------------------- charts ---
def _pts(values, w, h, pad=8):
    if not values:
        return []
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    n = len(values)
    return [(pad + i * (w - 2 * pad) / max(n - 1, 1),
             pad + (hi - v) * (h - 2 * pad) / span) for i, v in enumerate(values)]


def _path(points):
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points)


def equity_svg(curve, buy_hold=None, w=960, h=260):
    if not curve:
        return "<svg viewBox='0 0 960 260'></svg>"
    all_v = list(curve) + (list(buy_hold) if buy_hold else [])
    lo, hi = min(all_v), max(all_v)
    span = (hi - lo) or 1.0
    pad = 8

    def scale(vals):
        return [(pad + i * (w - 2 * pad) / max(len(vals) - 1, 1),
                 pad + (hi - v) * (h - 2 * pad) / span) for i, v in enumerate(vals)]

    p_main, p_bh = scale(curve), scale(buy_hold) if buy_hold else None
    area = _path(p_main) + f" L{p_main[-1][0]:.1f},{h - pad} L{p_main[0][0]:.1f},{h - pad} Z"
    bh = f'<path d="{_path(p_bh)}" fill="none" stroke="var(--muted)" stroke-width="1.5" stroke-dasharray="5 4"/>' if p_bh else ""
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" fill="var(--accent)"><title>#{i}: {curve[i]:,.2f}</title></circle>'
                   for i, (x, y) in enumerate(p_main) if i % max(len(p_main) // 40, 1) == 0)
    return (f'<svg viewBox="0 0 {w} {h}" class="w-full h-[220px] md:h-[260px]" preserveAspectRatio="none">'
            f'<path d="{area}" fill="var(--accent)" opacity="0.12"/>'
            f'<path d="{_path(p_main)}" fill="none" stroke="var(--accent)" stroke-width="2"/>{bh}{dots}</svg>')


def drawdown_svg(curve, w=960, h=110):
    if not curve:
        return "<svg viewBox='0 0 960 110'></svg>"
    peak, dd = -1e18, []
    for v in curve:
        peak = max(peak, v)
        dd.append((v / peak - 1.0) * 100 if peak > 0 else 0.0)
    lo = min(dd) or -1.0
    pts = [(8 + i * (w - 16) / max(len(dd) - 1, 1), 8 + (d / lo) * (h - 16)) for i, d in enumerate(dd)]
    area = _path(pts) + f" L{pts[-1][0]:.1f},8 L{pts[0][0]:.1f},8 Z"
    return (f'<svg viewBox="0 0 {w} {h}" class="w-full h-[100px]" preserveAspectRatio="none">'
            f'<path d="{area}" fill="var(--danger)" opacity="0.35"/>'
            f'<path d="{_path(pts)}" fill="none" stroke="var(--danger)" stroke-width="1.5"/></svg>')


# ------------------------------------------------------------------ data ---
def _fmt(v, pct=False, money=False):
    if v is None:
        return "—"
    if pct:
        return f"{v * 100:+.2f}%"
    if money:
        return f"{v:,.2f}"
    return f"{v:+.3f}" if isinstance(v, float) else str(v)


def _leaderboard(rows, buy_hold_return):
    def cell(r, key, pct=False, money=False):
        v = r.get(key)
        tone = ""
        if isinstance(v, (int, float)) and key in ("total_return", "sharpe"):
            tone = "ok" if v > 0 else "danger"
        cls = f' class="num t-{tone}"' if tone else ' class="num"'
        return f"<td{cls}>{_fmt(v, pct=pct, money=money)}</td>"

    best = {k: max((r.get(k) or -1e18) for r in rows) for k in ("sharpe", "total_return", "profit_factor")}
    body = []
    for i, r in enumerate(rows, 1):
        name = r["name"]
        hl = ' class="row-ensemble"' if name == "ENSEMBLE" else ""
        medal = ""
        if (r.get("sharpe") or -1e18) == best["sharpe"]:
            medal += ' <span class="chip chip-ok">top sharpe</span>'
        if (r.get("total_return") or -1e18) == best["total_return"]:
            medal += ' <span class="chip chip-ok">top return</span>'
        body.append(
            f'<tr{hl} data-name="{html.escape(name)}"><td class="name">{html.escape(name)}{medal}</td>'
            f'<td class="num">{i}</td>'
            + cell(r, "total_return", pct=True)
            + cell(r, "sharpe")
            + cell(r, "max_drawdown", pct=True)
            + cell(r, "profit_factor")
            + f'<td class="num">{_fmt(r.get("win_rate"), pct=True)}</td>'
            + f'<td class="num">{r.get("num_trades", 0)}</td>'
            + f'<td class="num">{"HALTED" if r.get("halted") else "ok"}</td></tr>')
    bh_row = (f'<tr class="row-bench"><td class="name">buy &amp; hold (benchmark)</td><td class="num">—</td>'
              f'<td class="num">{_fmt(buy_hold_return, pct=True)}</td>'
              f'<td class="num">—</td><td class="num">—</td><td class="num">—</td><td class="num">—</td>'
              f'<td class="num">1</td><td class="num">—</td></tr>')
    return "".join(body) + bh_row


def _trades_table(trades):
    if not trades:
        return '<p class="muted">No trades yet.</p>'
    rows = "".join(
        f'<tr data-pnl="{1 if t.pnl >= 0 else 0}"><td>{html.escape(str(t.entry_ts))[:16]}</td>'
        f"<td>{html.escape(str(t.exit_ts))[:16]}</td>"
        f'<td class="num">{t.entry:,.2f}</td><td class="num">{t.exit:,.2f}</td>'
        f'<td class="num {"t-ok" if t.pnl >= 0 else "t-danger"}">{t.pnl_pct * 100:+.2f}%</td>'
        f'<td class="num {"t-ok" if t.pnl >= 0 else "t-danger"}">{t.pnl:+,.4f}</td>'
        f'<td class="left">{html.escape(str(t.reason))}</td></tr>'
        for t in trades[-100:])
    return (f'<table id="trades"><thead><tr><th>entry</th><th>exit</th><th>entry px</th>'
            f'<th>exit px</th><th>pnl %</th><th>pnl USDC</th><th class="left">reason</th></tr></thead>'
            f"<tbody>{rows}</tbody></table>")


# ---------------------------------------------------------------- console --
def render_console(harness_result: dict, curve: list, metrics: dict,
                   trades: list | None = None, meta: dict | None = None,
                   notes: list | None = None) -> str:
    trades = trades or []
    meta = meta or {}
    rows = harness_result["rows"]
    regimes = harness_result.get("regimes", {})
    total_regime = sum(regimes.values()) or 1
    regime_bars = "".join(
        f'<div class="reg-row"><span class="reg-name">{html.escape(r)}</span>'
        f'<div class="reg-track"><div class="reg-fill" style="width:{c / total_regime * 100:.1f}%"></div></div>'
        f'<span class="reg-pct">{c / total_regime * 100:.0f}%</span></div>'
        for r, c in regimes.items())
    ens_rank = harness_result.get("ensemble_rank")
    ens_note = (f"#{ens_rank + 1} of {len(rows)} contenders" if ens_rank is not None else "—")
    kpis = [
        ("final equity", f"{metrics.get('final_equity', 0):,.2f}", meta.get("quote", "USDC"), "accent"),
        ("total return", _fmt(metrics.get("total_return"), pct=True), "after fees + slippage",
         "ok" if (metrics.get("total_return") or 0) >= 0 else "danger"),
        ("vs buy & hold", _fmt((metrics.get("total_return") or 0) - harness_result.get("buy_hold_return", 0), pct=True),
         "the only benchmark that matters", ""),
        ("max drawdown", _fmt(metrics.get("max_drawdown"), pct=True), "worst peak-to-trough", "danger"),
        ("Sharpe", _fmt(metrics.get("sharpe")), "return per unit of pain", ""),
        ("win rate", _fmt(metrics.get("win_rate"), pct=True),
         f"PF {_fmt(metrics.get('profit_factor'))} · {metrics.get('num_trades', 0)} trades", ""),
        ("ensemble rank", ens_note, "of all strategies on this data", "accent"),
        ("halted", "YES — kill-switch fired" if meta.get("halted") else "no", "50% drawdown guard", "danger" if meta.get("halted") else "muted"),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="kpi-label">{label}</div>'
        f'<div class="kpi-value {"t-" + tone if tone and tone not in ("accent", "muted") else ""}'
        f'{" kpi-accent" if tone == "accent" else ""}">{value}</div>'
        f'<div class="kpi-sub">{sub}</div></div>'
        for label, value, sub, tone in kpis)
    notes_html = "".join(f"<li>{html.escape(n)}</li>" for n in (notes or []))
    status = ("HALTED — drawdown kill-switch fired" if meta.get("halted")
              else "research mode — no real orders placed")
    status_cls = "bar-danger" if meta.get("halted") else "bar-ok"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trading console — {html.escape(meta.get('pair', 'ETH/USDC'))}</title>
<style>
:root{{--bg:#0c0e12;--card:#141821;--border:#242b38;--text:#e7eaf0;--muted:#8b94a7;--accent:#5b9dff;--ok:#3ecf8e;--warn:#f5b83d;--danger:#f2555a}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);
font:14px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
.wrap{{max-width:1100px;margin:0 auto;padding:20px 16px 40px}}
h1{{font-size:20px;margin:0 0 2px}} h2{{font-size:13px;letter-spacing:.08em;text-transform:uppercase;
color:var(--muted);margin:0 0 10px}} .muted{{color:var(--muted)}}
.bar{{display:flex;align-items:center;gap:10px;padding:8px 14px;border-radius:10px;font-size:12.5px;margin:12px 0}}
.bar-ok{{background:color-mix(in srgb,var(--ok) 12%,transparent);border:1px solid color-mix(in srgb,var(--ok) 35%,transparent)}}
.bar-danger{{background:color-mix(in srgb,var(--danger) 14%,transparent);border:1px solid color-mix(in srgb,var(--danger) 40%,transparent)}}
.grid{{display:grid;gap:12px}} .kpis{{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}}
.kpi{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px 14px}}
.kpi-label{{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}}
.kpi-value{{font-size:21px;font-weight:650;margin:3px 0 1px;font-variant-numeric:tabular-nums}}
.kpi-accent{{color:var(--accent)}} .kpi-sub{{font-size:11px;color:var(--muted)}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px}}
.t-ok{{color:var(--ok)}} .t-danger{{color:var(--danger)}}
.tabs{{display:flex;gap:6px;margin:18px 0 12px}} .tab{{padding:7px 16px;border-radius:9px;cursor:pointer;
border:1px solid var(--border);background:transparent;color:var(--muted);font-size:13px}}
.tab.active{{background:var(--accent);border-color:var(--accent);color:#fff}}
.panel{{display:none}} .panel.active{{display:block}}
table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}
th{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);text-align:right;
padding:7px 9px;border-bottom:1px solid var(--border);cursor:pointer;user-select:none;white-space:nowrap}}
th.left,td.left{{text-align:left}} th:first-child,td:first-child{{text-align:left}}
td{{padding:7px 9px;border-bottom:1px solid var(--border);font-size:12.5px;text-align:right;white-space:nowrap}}
tr.row-ensemble{{background:color-mix(in srgb,var(--accent) 13%,transparent);font-weight:600}}
tr.row-bench{{color:var(--muted);font-style:italic}}
.name{{max-width:230px;overflow:hidden;text-overflow:ellipsis}}
.chip{{display:inline-block;font-size:9.5px;padding:1px 7px;border-radius:99px;margin-left:6px;
vertical-align:1px;text-transform:uppercase;letter-spacing:.05em;font-weight:600}}
.chip-ok{{background:color-mix(in srgb,var(--ok) 22%,transparent);color:var(--ok)}}
.reg-row{{display:flex;align-items:center;gap:10px;margin:7px 0;font-size:12px}}
.reg-name{{width:90px;color:var(--muted)}} .reg-track{{flex:1;height:8px;background:var(--border);border-radius:99px;overflow:hidden}}
.reg-fill{{height:100%;background:var(--accent);border-radius:99px}} .reg-pct{{width:36px;text-align:right;color:var(--muted)}}
.filters{{display:flex;gap:6px;margin:0 0 10px}} .filter{{padding:5px 13px;border-radius:8px;cursor:pointer;
border:1px solid var(--border);background:transparent;color:var(--muted);font-size:12px}}
.filter.active{{background:var(--accent);border-color:var(--accent);color:#fff}}
footer{{margin-top:22px;font-size:11.5px;color:var(--muted);line-height:1.7}}
ul{{margin:6px 0 0;padding-left:18px;font-size:12px;color:var(--muted)}}
</style></head><body><div class="wrap">

<div class="bar {status_cls}"><strong>{html.escape(status)}</strong>
<span class="muted">· {html.escape(meta.get('pair', 'ETH/USDC'))} · {html.escape(str(meta.get('data', 'data')))} ·
fees {meta.get('fee_bps', 10):.0f}bps + slippage {meta.get('slippage_bps', 5):.0f}bps per fill</span></div>

<h1>Trading console</h1>
<p class="muted" style="margin:0 0 14px">Every strategy on the same data, same costs, same rules — ranked by evidence.</p>

<div class="grid kpis">{kpi_html}</div>

<div class="tabs">
<div class="tab active" data-tab="overview">Overview</div>
<div class="tab" data-tab="strategies">Strategy harness ({len(rows)})</div>
<div class="tab" data-tab="trades">Trade log ({len(trades)})</div>
</div>

<div class="panel active" id="overview">
<div class="card" style="margin-bottom:12px"><h2>Equity vs buy &amp; hold</h2>{equity_svg(curve, meta.get('buy_hold'))}</div>
<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(280px,1fr))">
<div class="card"><h2>Drawdown</h2>{drawdown_svg(curve)}</div>
<div class="card"><h2>Market regime share</h2>{regime_bars or '<p class="muted">—</p>'}</div>
</div>
</div>

<div class="panel" id="strategies">
<div class="card"><h2>Leaderboard — click a column to sort</h2>
<table id="board"><thead><tr>
<th data-k="0">strategy</th><th data-k="1" data-n>#</th><th data-k="2" data-n>return</th>
<th data-k="3" data-n>sharpe</th><th data-k="4" data-n>max DD</th><th data-k="5" data-n>PF</th>
<th data-k="6" data-n>win %</th><th data-k="7" data-n>trades</th><th data-k="8" data-n>status</th>
</tr></thead><tbody>{_leaderboard(rows, harness_result.get('buy_hold_return', 0.0))}</tbody></table>
<p class="muted" style="font-size:11.5px;margin:10px 0 0">ENSEMBLE = all strategies cooperating, regime-gated, weighted by
evidence. Sharpe ranks the table because return without pain-adjustment is how accounts die.</p></div>
</div>

<div class="panel" id="trades">
<div class="card"><h2>Closed trades</h2>
<div class="filters">
<div class="filter active" data-f="all">all</div>
<div class="filter" data-f="1">winners</div>
<div class="filter" data-f="0">losers</div>
</div>
{_trades_table(trades)}</div>
</div>

<footer>
<strong>Read this honestly.</strong> These are TESTED results on historical or seeded-synthetic data after
fees and slippage — not a promise of future income. No strategy can promise positive income; the harness
routes weight toward what survived testing and away from what didn't. Research software, not financial
advice. No real orders are placed by this system.
{('<ul>' + notes_html + '</ul>') if notes_html else ''}
</footer>
</div>

<script>
document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>{{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
  t.classList.add('active');
  document.getElementById(t.dataset.tab).classList.add('active');
}}));
document.querySelectorAll('.filter').forEach(f=>f.addEventListener('click',()=>{{
  document.querySelectorAll('.filter').forEach(x=>x.classList.remove('active'));
  f.classList.add('active');
  document.querySelectorAll('#trades tbody tr').forEach(r=>{{
    r.style.display=(f.dataset.f==='all'||r.dataset.pnl===f.dataset.f)?'':'none';
  }});
}}));
document.querySelectorAll('#board th').forEach(th=>th.addEventListener('click',()=>{{
  const k=+th.dataset.k, numeric=th.dataset.n!==undefined;
  const tb=document.querySelector('#board tbody');
  const rows=[...tb.querySelectorAll('tr')];
  const dir=th.dataset.dir==='asc'?-1:1;
  th.dataset.dir=dir===1?'asc':'desc';
  rows.sort((a,b)=>{{
    const x=a.children[k].textContent.trim(), y=b.children[k].textContent.trim();
    return numeric?(parseFloat(x.replace(/[+%]|HALTED|ok/g,''))||0)-(parseFloat(y.replace(/[+%]|HALTED|ok/g,''))||0)||x.localeCompare(y)
      :dir*x.localeCompare(y);
  }}).forEach(r=>tb.appendChild(r));
}}));
</script>
</body></html>"""


def write_console(path: str | None = None, **kw) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = path or os.path.join(REPORTS_DIR, "console.html")
    with open(path, "w") as f:
        f.write(render_console(**kw))
    return path

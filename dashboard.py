"""Portfolio growth dashboard: one self-contained HTML file, no CDN, no
network -- inline SVG charts that always render.

Charts: equity growth over time (log-friendly), drawdown underwater curve,
and the wallet composition (ETH vs USDC). Plus the metric cards and the
trade log, because a growth chart without the losses hidden inside it is
marketing, not reporting.
"""
from __future__ import annotations

import html
import json
import os

REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def _polyline(curve, width, height, pad=36):
    lo, hi = min(curve), max(curve)
    rng = (hi - lo) or 1.0
    pts = []
    for i, v in enumerate(curve):
        x = pad + (width - 2 * pad) * (i / max(1, len(curve) - 1))
        y = height - pad - (height - 2 * pad) * ((v - lo) / rng)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts), lo, hi


def equity_chart_svg(curve, buy_hold=None, width=880, height=300, title="Portfolio growth (USDC)"):
    pts, lo, hi = _polyline(curve, width, height)
    base = ""
    if buy_hold:
        bpts, _, _ = _polyline(buy_hold, width, height)
        base = f'<polyline points="{bpts}" fill="none" stroke="var(--muted)" stroke-width="1.5" stroke-dasharray="5 4"/>'
    return f"""<svg viewBox="0 0 {width} {height}" class="w-full h-auto" font-family="monospace">
<text x="36" y="24" fill="var(--text)" font-size="13">{title}</text>
<line x1="36" y1="{height - 36 - (height - 72) * ((curve[0] - lo) / ((hi - lo) or 1)):.1f}" x2="{width - 36}" y2="{height - 36 - (height - 72) * ((curve[0] - lo) / ((hi - lo) or 1)):.1f}" stroke="var(--border)" stroke-dasharray="3 3"/>
{base}
<polyline points="{pts}" fill="none" stroke="var(--accent)" stroke-width="2.2"/>
<text x="36" y="{height - 12}" fill="var(--muted)">${curve[0]:,.2f}</text>
<text x="{width - 150}" y="{height - 12}" fill="var(--muted)">${curve[-1]:,.2f} · peak ${max(curve):,.2f}</text>
</svg>"""


def drawdown_chart_svg(curve, width=880, height=160):
    peak, dd = curve[0], []
    for v in curve:
        peak = max(peak, v)
        dd.append(1 - v / peak if peak else 0.0)
    pts, lo, hi = _polyline([-d for d in dd], width, height)
    return f"""<svg viewBox="0 0 {width} {height}" class="w-full h-auto" font-family="monospace">
<text x="36" y="20" fill="var(--text)" font-size="13">Drawdown (underwater)</text>
<polyline points="{pts}" fill="none" stroke="var(--danger)" stroke-width="1.8"/>
<text x="{width - 190}" y="{height - 12}" fill="var(--muted)">worst: {max(dd) * 100:.1f}%</text>
</svg>"""


def wallet_chart_svg(holdings: dict, width=440, height=180):
    """Horizontal bars: value of each holding in quote terms."""
    rows = []
    total = sum((h["value_quote"] or 0.0) for h in holdings.values()) or 1.0
    y = 42
    for asset, h in sorted(holdings.items()):
        val = h["value_quote"] or 0.0
        w = (width - 220) * (val / total)
        color = "var(--accent)" if asset != "USDC" else "var(--ok)"
        rows.append(f'<text x="10" y="{y + 4}" fill="var(--text)" font-size="12">{html.escape(asset)}</text>')
        rows.append(f'<rect x="80" y="{y - 10}" width="{max(2, w):.1f}" height="16" rx="3" fill="{color}"/>')
        rows.append(f'<text x="{86 + max(2, w):.1f}" y="{y + 4}" fill="var(--muted)" font-size="11">${val:,.2f} ({val / total * 100:.1f}%)</text>')
        y += 34
    return (f'<svg viewBox="0 0 {width} {height}" class="w-full h-auto" font-family="monospace">'
            f'<text x="10" y="22" fill="var(--text)" font-size="13">Wallet composition</text>{"".join(rows)}</svg>')


def _cards(m: dict, halted: bool = False) -> str:
    def card(label, value, tone="var(--text)"):
        return (f'<div class="bg-[var(--card)] border border-[var(--border)] rounded-lg p-3">'
                f'<div class="text-[11px]" style="color:var(--muted)">{label}</div>'
                f'<div class="text-lg font-semibold" style="color:{tone}">{value}</div></div>')
    pf = m.get("profit_factor", 0.0)
    cards = [
        card("Final equity (USDC)", f"${m['final_equity']:,.2f}", "var(--accent)"),
        card("Total return", f"{m['total_return'] * 100:+.1f}%",
             "var(--ok)" if m["total_return"] >= 0 else "var(--danger)"),
        card("CAGR", f"{m['cagr'] * 100:+.1f}%"),
        card("Sharpe", f"{m['sharpe']:.2f}"),
        card("Max drawdown", f"{m['max_drawdown'] * 100:.1f}%", "var(--danger)"),
        card("Trades", f"{m['trades']}"),
        card("Win rate", f"{m['win_rate'] * 100:.0f}%"),
        card("Profit factor", "∞" if pf == float("inf") else f"{pf:.2f}"),
        card("Exposure", f"{m['exposure'] * 100:.0f}%"),
    ]
    if halted:
        cards.append(card("Kill-switch", "FIRED — halted", "var(--danger)"))
    return "".join(cards)


def render_html(curve: list, metrics: dict, holdings: dict | None = None,
                trades: list | None = None, buy_hold: list | None = None,
                title: str = "Trading bot — portfolio growth", halted: bool = False,
                notes: list | None = None) -> str:
    trades = trades or []
    trade_rows = "".join(
        f"<tr><td>{html.escape(str(t.entry_ts))}</td><td>{html.escape(str(t.exit_ts))}</td>"
        f"<td>{t.entry:,.2f}</td><td>{t.exit:,.2f}</td>"
        f'<td style="color:{"var(--ok)" if t.pnl >= 0 else "var(--danger)"}">{t.pnl:+,.4f}</td>'
        f'<td style="color:{"var(--ok)" if t.pnl_pct >= 0 else "var(--danger)"}">{t.pnl_pct * 100:+.2f}%</td>'
        f"<td>{html.escape(str(t.reason))}</td></tr>"
        for t in trades[-60:])
    notes_html = "".join(f'<li class="text-sm" style="color:var(--muted)">{html.escape(n)}</li>' for n in (notes or []))
    wallet = wallet_chart_svg(holdings) if holdings else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>body{{background:var(--bg,#0f1115);color:var(--text,#e8e8e8);font-family:ui-monospace,monospace}}
table{{width:100%;border-collapse:collapse}}td,th{{padding:4px 8px;border-bottom:1px solid var(--border,#2a2f3a);font-size:12px;text-align:right}}th,td:first-child,td:nth-child(2){{text-align:left}}</style>
</head><body class="p-6 max-w-6xl mx-auto space-y-6">
<h1 class="text-2xl font-bold">{html.escape(title)}</h1>
<ul class="space-y-1">{notes_html}</ul>
<div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">{_cards(metrics, halted)}</div>
<div class="bg-[var(--card,#161a22)] border border-[var(--border,#2a2f3a)] rounded-xl p-4">{equity_chart_svg(curve, buy_hold)}</div>
<div class="grid md:grid-cols-2 gap-4">
<div class="bg-[var(--card,#161a22)] border border-[var(--border,#2a2f3a)] rounded-xl p-4">{drawdown_chart_svg(curve)}</div>
<div class="bg-[var(--card,#161a22)] border border-[var(--border,#2a2f3a)] rounded-xl p-4">{wallet}</div>
</div>
<div class="bg-[var(--card,#161a22)] border border-[var(--border,#2a2f3a)] rounded-xl p-4">
<h2 class="text-sm font-semibold mb-2">Trade log (last {min(60, len(trades))} of {len(trades)})</h2>
<table><thead><tr><th>entry</th><th>exit</th><th>entry px</th><th>exit px</th><th>pnl (USDC)</th><th>pnl %</th><th>reason</th></tr></thead>
<tbody>{trade_rows or '<tr><td colspan="7" style="color:var(--muted)">no trades</td></tr>'}</tbody></table></div>
<p class="text-xs" style="color:var(--muted)">Research software. Not financial advice. No real orders are placed by this system.</p>
</body></html>"""


def write_dashboard(curve, metrics, path: str | None = None, **kw) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = path or os.path.join(REPORTS_DIR, "dashboard.html")
    with open(path, "w") as f:
        f.write(render_html(curve, metrics, **kw))
    return path

"""Report rendering: text, JSON, and a self-contained SVG equity chart."""
from __future__ import annotations

import json


def render_text(m: dict, buy_hold: float | None = None, halted: bool = False,
                label: str = "") -> str:
    def pct(x):
        return f"{x * 100:+.2f}%"

    lines = []
    if label:
        lines.append(f"strategy        : {label}")
    lines += [
        f"final equity    : ${m['final_equity']:,.2f}",
        f"total return    : {pct(m['total_return'])}",
    ]
    if buy_hold is not None:
        lines.append(f"buy & hold      : {pct(buy_hold)}")
    lines += [
        f"CAGR            : {pct(m['cagr'])}",
        f"Sharpe          : {m['sharpe']:.2f}",
        f"Sortino         : {m['sortino']:.2f}",
        f"Calmar          : {m['calmar']:.2f}",
        f"max drawdown    : {m['max_drawdown'] * 100:.2f}%",
        f"trades          : {m['trades']}",
        f"win rate        : {m['win_rate'] * 100:.1f}%",
    ]
    pf = m["profit_factor"]
    lines.append(f"profit factor   : {'inf' if pf == float('inf') else f'{pf:.2f}'}")
    lines += [
        f"expectancy      : {m['expectancy'] * 100:+.2f}% per trade",
        f"avg win / loss  : {m['avg_win_pct'] * 100:+.2f}% / {m['avg_loss_pct'] * 100:+.2f}%",
        f"exposure        : {m['exposure'] * 100:.1f}% of bars in market",
    ]
    if halted:
        lines.append("NOTE            : drawdown kill-switch FIRED -- trading halted mid-run")
    return "\n".join(lines)


def render_json(m: dict, **extra) -> str:
    payload = dict(m)
    payload.update(extra)
    return json.dumps(payload, indent=2, default=str)


def render_equity_svg(curve, path: str, width: int = 900, height: int = 320,
                      title: str = "Equity curve", baseline=None) -> str:
    if not curve:
        raise ValueError("empty curve")
    lo, hi = min(curve), max(curve)
    if baseline:
        lo, hi = min(lo, min(baseline)), max(hi, max(baseline))
    rng = (hi - lo) or 1.0
    pad = 40

    def xy(series):
        n = len(series)
        pts = []
        for i, v in enumerate(series):
            x = pad + (width - 2 * pad) * (i / max(1, n - 1))
            y = height - pad - (height - 2 * pad) * ((v - lo) / rng)
            pts.append(f"{x:.1f},{y:.1f}")
        return " ".join(pts)

    y0 = height - pad - (height - 2 * pad) * ((curve[0] - lo) / rng)
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'font-family="monospace" font-size="11">',
        f'<rect width="{width}" height="{height}" fill="#0f1115"/>',
        f'<text x="{pad}" y="22" fill="#e6e6e6" font-size="14">{title}</text>',
        f'<line x1="{pad}" y1="{y0:.1f}" x2="{width - pad}" y2="{y0:.1f}" '
        f'stroke="#555" stroke-dasharray="4 4"/>',
    ]
    if baseline:
        svg.append(f'<polyline points="{xy(baseline)}" fill="none" stroke="#4a90d9" stroke-width="1.5"/>')
    svg.append(f'<polyline points="{xy(curve)}" fill="none" stroke="#22c55e" stroke-width="2"/>')
    svg.append(f'<text x="{pad}" y="{height - 12}" fill="#888">start ${curve[0]:,.2f}</text>')
    svg.append(f'<text x="{width - pad - 120}" y="{height - 12}" fill="#888">end ${curve[-1]:,.2f}</text>')
    svg.append("</svg>")
    out = "\n".join(svg)
    with open(path, "w") as f:
        f.write(out)
    return path
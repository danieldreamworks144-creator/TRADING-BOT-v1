"""Self-improvement loop: every idea -- from a stronger model, research, or
hand-written -- passes through the SAME validation gate before it can change
the bot. The gate is walk-forward plus sanity floors; the verdict is recorded.

This is the mechanism behind "keep making it better": not blind accumulation
of features, but candidate -> test -> adopt/reject with evidence.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from backtest import compute_metrics, run_backtest
from config import BacktestConfig
from walkforward import walk_forward

REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

@dataclass
class GateResult:
    name: str
    adopted: bool
    reason: str
    oos_sharpe: float = 0.0
    oos_trades: int = 0
    max_dd: float = 0.0
    robustness: float = 0.0
    evidence: dict = field(default_factory=dict)


# Minimum evidence to adopt anything. Deliberately strict: a strategy that
# cannot clear these on history it never saw is a story, not a strategy.
MIN_OOS_SHARPE = 0.3
MIN_TRADES = 20
MAX_DRAWDOWN = 0.40
MIN_ROBUSTNESS = 0.35   # in-sample skill must substantially survive out-of-sample


def validate(strategy_key: str, bars, cfg: BacktestConfig | None = None,
             name: str | None = None) -> GateResult:
    """Run the validation gate on a strategy (by registry key). Returns an
    evidence-backed verdict; nothing is adopted on charm alone."""
    cfg = cfg or BacktestConfig()
    name = name or strategy_key
    wf = walk_forward(bars, strategy_key, cfg)
    if not wf.windows:
        return GateResult(name, False, "not enough data for even one walk-forward window")

    oos_sharpe = wf.avg_oos_sharpe
    oos_trades = sum(w.out_sample_trades for w in wf.windows)
    max_dd = max((w.out_sample_dd for w in wf.windows), default=1.0)
    robust = wf.robustness

    reasons = []
    if oos_sharpe < MIN_OOS_SHARPE:
        reasons.append(f"oos sharpe {oos_sharpe:.2f} < {MIN_OOS_SHARPE}")
    if 0 <= oos_trades < MIN_TRADES:
        reasons.append(f"only {oos_trades} out-of-sample trades < {MIN_TRADES}")
    if max_dd > MAX_DRAWDOWN:
        reasons.append(f"out-of-sample drawdown {max_dd:.0%} > {MAX_DRAWDOWN:.0%}")
    if robust < MIN_ROBUSTNESS:
        reasons.append(f"robustness {robust:.2f} < {MIN_ROBUSTNESS} (curve-fit risk)")

    adopted = not reasons
    return GateResult(
        name=name, adopted=adopted,
        reason="passed all gates" if adopted else "; ".join(reasons),
        oos_sharpe=oos_sharpe, oos_trades=oos_trades,
        max_dd=max_dd, robustness=robust,
        evidence={"windows": len(wf.windows)},
    )


def record(result: GateResult, path: str | None = None) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = path or os.path.join(REPORTS_DIR, "improvement-log.jsonl")
    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             **asdict(result)}
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return path

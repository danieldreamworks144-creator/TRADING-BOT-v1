"""Strategy harness: put EVERY strategy on the same data, the same costs, and
the same rules -- then let the evidence rank them.

This is the "harness all the strategies" layer:
  - every registered strategy runs head to head on identical bars/fees/slippage;
  - the regime-gated ENSEMBLE runs alongside them as the combined contender;
  - each strategy is scored honestly (return, Sharpe, drawdown, profit factor,
    win rate, exposure) and ranked by Sharpe -- return per unit of pain;
  - the regime distribution is reported so you can see WHERE each edge came from.

What this does NOT do: promise positive income. A harness can only route capital
toward what survived testing and away from what didn't. If nothing survives, the
honest output is "stay flat" -- and that is a feature, not a failure."""
from __future__ import annotations

from backtest import compute_metrics, run_backtest
from config import BacktestConfig
from regime import detect_regime
from risk import RiskManager
from strategy import STRATEGIES


def regime_distribution(bars, warmup: int = 100) -> dict:
    """How much of the data lived in each market regime. No lookahead: each
    label uses only bars up to i."""
    out: dict[str, int] = {}
    for i in range(min(warmup, len(bars)), len(bars)):
        r = detect_regime(bars, i)
        out[r] = out.get(r, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def score_strategy(name: str, strategy, bars, cfg: BacktestConfig) -> dict:
    res = run_backtest(bars, strategy, cfg, RiskManager(cfg))
    m = compute_metrics(res.curve, res.trades, res.bars_in_market)
    m["name"] = name
    m["halted"] = res.halted
    m["final_equity"] = res.curve[-1] if res.curve else cfg.starting_capital
    return m


def harness(bars, ensemble_strategy, cfg: BacktestConfig,
            extra: dict | None = None) -> dict:
    """Run every registered strategy AND the ensemble on the same bars.

    `ensemble_strategy` is the combined contender (e.g. run_full.EnsembleStrategy);
    `extra` can add named strategies that aren't in the registry (e.g. an
    advisor-proposed one) -- it gets scored under the same rules."""
    rows = [score_strategy(key, factory(), bars, cfg)
            for key, factory in STRATEGIES.items()]
    rows.append(score_strategy("ENSEMBLE", ensemble_strategy, bars, cfg))
    for name, strat in (extra or {}).items():
        rows.append(score_strategy(name, strat, bars, cfg))
    rows.sort(key=lambda r: r["sharpe"], reverse=True)
    bh_return = (bars[-1].close / bars[0].close - 1) if bars else 0.0
    buy_hold = {
        "name": "buy & hold (benchmark)",
        "total_return": bh_return,
        "sharpe": 0.0,  # a single unmanaged holding has no risk-adjusted score here
    }
    return {
        "rows": rows,
        "buy_hold_return": bh_return,
        "regimes": regime_distribution(bars),
        "bars": len(bars),
        "best": rows[0]["name"] if rows else None,
        "ensemble_rank": next((i for i, r in enumerate(rows)
                               if r["name"] == "ENSEMBLE"), None),
    }

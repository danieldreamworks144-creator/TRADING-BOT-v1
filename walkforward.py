"""Walk-forward validation: tune on an in-sample window, verify on the NEXT
window the optimizer never saw. This is the only honest way to answer "is this
strategy real, or did I just curve-fit the history?"

An overfit parameter set dies out-of-sample. A real one survives."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from backtest import compute_metrics, run_backtest
from config import BacktestConfig
from risk import RiskManager
from strategy import STRATEGY_CLASSES, PARAM_GRIDS


@dataclass
class WindowResult:
    in_sample_sharpe: float
    out_sample_sharpe: float
    out_sample_return: float
    out_sample_dd: float
    best_params: dict
    out_sample_trades: int = 0


@dataclass
class WalkForwardResult:
    windows: list = field(default_factory=list)
    stitched_equity: list = field(default_factory=list)

    @property
    def avg_oos_sharpe(self) -> float:
        return (sum(w.out_sample_sharpe for w in self.windows) / len(self.windows)
                if self.windows else 0.0)

    @property
    def robustness(self) -> float:
        """How much skill survives the walk out of sample, 0..1."""
        is_avg = sum(w.in_sample_sharpe for w in self.windows) / len(self.windows) if self.windows else 0.0
        if is_avg <= 0:
            return 0.0
        return max(0.0, min(1.0, self.avg_oos_sharpe / is_avg))


def _grid(params: dict):
    keys = sorted(params)
    if not keys:
        return [{}]
    for values in itertools.product(*(params[k] for k in keys)):
        yield dict(zip(keys, values))


def walk_forward(bars, strategy_key: str, cfg: BacktestConfig,
                 n_windows: int = 4, train_frac: float = 0.6) -> WalkForwardResult:
    if strategy_key not in STRATEGY_CLASSES:
        raise ValueError(f"unknown strategy: {strategy_key}")
    grid = list(_grid(PARAM_GRIDS[strategy_key]))
    n = len(bars)
    fold = n // (n_windows + 1)
    if fold < 50:
        raise ValueError("not enough bars for walk-forward -- need more history")

    out = WalkForwardResult()
    for w in range(n_windows):
        test_start = (w + 1) * fold
        test_end = test_start + fold
        train = bars[:test_start]
        test = bars[test_start:test_end]

        best, best_sharpe = None, float("-inf")
        for params in grid:
            strat = STRATEGY_CLASSES[strategy_key](**params)
            r = run_backtest(train, strat, cfg, RiskManager(cfg))
            m = compute_metrics(r.curve, r.trades, r.bars_in_market)
            score = m["sharpe"]
            if score > best_sharpe:
                best_sharpe, best = score, params

        oos_strat = STRATEGY_CLASSES[strategy_key](**best)
        oos = run_backtest(test, oos_strat, cfg, RiskManager(cfg))
        om = compute_metrics(oos.curve, oos.trades, oos.bars_in_market)

        out.windows.append(WindowResult(
            in_sample_sharpe=best_sharpe,
            out_sample_sharpe=om["sharpe"],
            out_sample_return=om["total_return"],
            out_sample_dd=om["max_drawdown"],
            best_params=best,
            out_sample_trades=om["trades"],
        ))
        base = out.stitched_equity[-1] if out.stitched_equity else cfg.starting_capital
        scale = base / oos.curve[0] if oos.curve[0] else 1.0
        out.stitched_equity.extend(v * scale for v in oos.curve)
    return out
"""Multi-asset portfolio engine -- the account is denominated in the QUOTE
asset (USDC by default); each BASE asset (ETH, BTC, ...) trades in its own
capital sleeve and every PnL number is reported in quote (USDC).

Sleeve model: each asset gets `weight * capital` of quote currency and trades
independently. That is deliberately simple and honest -- it does NOT net
margin across assets, so it can never show you fantasy cross-collateralized
returns. Correlation between sleeves still affects the combined curve, which
is exactly what the combined drawdown will show you."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from backtest import Result, compute_metrics, run_backtest
from config import BacktestConfig
from risk import RiskManager


@dataclass
class PortfolioResult:
    quote_asset: str = "USDC"
    sleeves: dict = field(default_factory=dict)      # symbol -> Result
    sleeve_metrics: dict = field(default_factory=dict)  # symbol -> metrics dict
    combined_curve: list = field(default_factory=list)

    def combined_metrics(self) -> dict:
        all_trades = [t for r in self.sleeves.values() for t in r.trades]
        in_market = sum(r.bars_in_market for r in self.sleeves.values())
        n = len(self.combined_curve)
        return compute_metrics(self.combined_curve, all_trades,
                               min(in_market, n) if n else 0)


def equal_weights(symbols) -> dict:
    w = 1.0 / len(symbols)
    return {s: w for s in symbols}


def run_portfolio(bars_by_symbol: dict, strategy_factory, cfg: BacktestConfig,
                  weights: dict | None = None) -> PortfolioResult:
    symbols = list(bars_by_symbol)
    if not symbols:
        raise ValueError("no assets given")
    weights = dict(weights) if weights else equal_weights(symbols)
    total_w = sum(weights.get(s, 0.0) for s in symbols)
    if total_w <= 0:
        raise ValueError("weights must sum to > 0")
    for s in symbols:
        if s not in weights or weights[s] <= 0:
            raise ValueError(f"missing/zero weight for {s}")

    out = PortfolioResult(quote_asset=cfg.quote_asset)
    curves = []
    for sym in symbols:
        sleeve_cfg = replace(cfg,
                             starting_capital=cfg.starting_capital * weights[sym] / total_w,
                             pair=f"{sym}/{cfg.quote_asset}",
                             base_asset=sym)
        res = run_backtest(bars_by_symbol[sym], strategy_factory(), sleeve_cfg,
                           RiskManager(sleeve_cfg))
        out.sleeves[sym] = res
        out.sleeve_metrics[sym] = compute_metrics(res.curve, res.trades, res.bars_in_market)
        curves.append(res.curve)

    # combine sleeve equity curves in quote currency (pad the short ones flat)
    n = max(len(c) for c in curves)
    combined = []
    for i in range(n):
        combined.append(sum(c[i] if i < len(c) else c[-1] for c in curves))
    out.combined_curve = combined
    return out
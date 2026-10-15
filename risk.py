"""Position sizing and stop placement -- the part that decides whether a good
strategy survives a bad month.

Sizing rules (all of them lose to 'all in' on the way up and beat it on the way
down, which is the entire point):
- fixed-fraction risk: risk at most `risk_per_trade` of equity between entry
  and stop, so one loss costs a known, small amount.
- volatility targeting (optional): scale size so the position's annualized vol
  matches `vol_target`, instead of treating calm and violent markets alike.
"""
from __future__ import annotations

from config import BacktestConfig


class RiskManager:
    def __init__(self, cfg: BacktestConfig):
        self.cfg = cfg

    def size(self, equity: float, price: float, atr_value=None) -> float:
        cfg = self.cfg
        if price <= 0 or equity <= 0:
            return 0.0
        if cfg.vol_target > 0 and atr_value:
            asset_vol = (atr_value / price) * (252 ** 0.5)  # annualized
            qty = (equity * cfg.vol_target) / (price * asset_vol) if asset_vol > 0 else 0.0
        else:
            stop_dist = price * cfg.stop_loss_pct
            qty = (equity * cfg.risk_per_trade) / stop_dist if stop_dist > 0 else 0.0
        max_qty = (equity * cfg.max_position_frac) / price
        return max(0.0, min(qty, max_qty))

    def initial_stop(self, entry_price: float, atr_value=None) -> float:
        cfg = self.cfg
        if cfg.stop_mode == "atr" and atr_value:
            return entry_price - cfg.atr_stop_mult * atr_value
        return entry_price * (1 - cfg.stop_loss_pct)
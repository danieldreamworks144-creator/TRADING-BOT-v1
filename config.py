"""Backtest configuration. Fees and slippage are ON by default -- a backtest
without them is a fantasy, and fantasy numbers are how people get hurt."""
from dataclasses import dataclass


@dataclass
class BacktestConfig:
    starting_capital: float = 1.0
    fee_bps: float = 10.0          # 0.10% per fill (taker fee, conservative)
    slippage_bps: float = 5.0      # 0.05% adverse price movement per fill
    risk_per_trade: float = 0.02   # risk 2% of equity per trade
    stop_loss_pct: float = 0.05    # exit if price falls 5% below entry
    max_position_frac: float = 1.0  # never deploy more than 100% of equity
    max_drawdown_halt: float = 0.50  # kill-switch: stop trading after 50% DD
    # exit rules
    stop_mode: str = "fixed"        # 'fixed' (percent) or 'atr' (volatility-based)
    atr_period: int = 14
    atr_stop_mult: float = 2.0      # ATR stop distance multiplier
    trailing_stop_pct: float = 0.0  # 0 disables; 0.08 trails 8% below peak
    take_profit_pct: float = 0.0    # 0 disables
    cooldown_bars: int = 0          # wait N bars after a stop-out before re-entry
    vol_target: float = 0.0         # annualized vol target (0.10 = 10%); 0 disables
    # assets: the account is denominated in the QUOTE asset (USDC by default);
    # the bot trades BASE assets (ETH, BTC, ...) priced in the quote asset.
    pair: str = "ETH/USDC"
    base_asset: str = "ETH"
    quote_asset: str = "USDC"
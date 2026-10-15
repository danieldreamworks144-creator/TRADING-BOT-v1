"""Strategy interface and two baseline strategies.

A strategy sees bars 0..i and returns LONG / FLAT / HOLD. It never sees the
future -- if a strategy needs more history than index i, it returns HOLD.
"""
from __future__ import annotations

import indicators as ind

LONG = "LONG"
FLAT = "FLAT"
HOLD = "HOLD"


class Strategy:
    name = "base"

    def on_bar(self, i: int, bars) -> str:
        raise NotImplementedError


def _sma(bars, i: int, period: int) -> float:
    return sum(b.close for b in bars[i - period + 1:i + 1]) / period


class SmaCrossover(Strategy):
    """Long while the fast SMA is above the slow SMA, flat otherwise."""
    name = "sma-crossover"

    def __init__(self, fast: int = 20, slow: int = 50):
        if fast >= slow:
            raise ValueError("fast period must be < slow period")
        self.fast = fast
        self.slow = slow

    def on_bar(self, i, bars):
        if i < self.slow:
            return HOLD
        return LONG if _sma(bars, i, self.fast) > _sma(bars, i, self.slow) else FLAT


class RsiReversion(Strategy):
    """Buy oversold dips, exit when RSI recovers."""
    name = "rsi-reversion"

    def __init__(self, period: int = 14, buy_below: float = 30.0, sell_above: float = 70.0):
        self.period = period
        self.buy_below = buy_below
        self.sell_above = sell_above

    def _rsi(self, bars, i: int) -> float:
        gains, losses = [], []
        for j in range(i - self.period + 1, i + 1):
            change = bars[j].close - bars[j - 1].close if j > 0 else 0.0
            gains.append(max(change, 0.0))
            losses.append(max(-change, 0.0))
        avg_loss = sum(losses) / self.period
        avg_gain = sum(gains) / self.period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - 100.0 / (1.0 + rs)

    def on_bar(self, i, bars):
        if i < self.period + 1:
            return HOLD
        rsi = self._rsi(bars, i)
        if rsi < self.buy_below:
            return LONG
        if rsi > self.sell_above:
            return FLAT
        return HOLD


class BuyHold(Strategy):
    """Baseline: always long. Everything else must beat this after costs."""
    name = "buy-hold"
    warmup = 1

    def on_bar(self, i, bars):
        return LONG


class BollingerReversion(Strategy):
    """Buy closes below the lower band, exit back at the middle band."""
    name = "bollinger-reversion"

    def __init__(self, period: int = 20, num_std: float = 2.0):
        self.period = period
        self.num_std = num_std
        self.warmup = period

    def on_bar(self, i, bars):
        if i < self.period:
            return HOLD
        closes = [b.close for b in bars[:i + 1]]
        mid, _, lower = ind.bollinger(closes, self.period, self.num_std)
        if bars[i].close < lower[i]:
            return LONG
        if bars[i].close > mid[i]:
            return FLAT
        return HOLD


class Momentum(Strategy):
    """Long when rate-of-change is meaningfully positive, flat when negative."""
    name = "momentum"

    def __init__(self, lookback: int = 20, threshold: float = 0.0):
        self.lookback = lookback
        self.threshold = threshold
        self.warmup = lookback

    def on_bar(self, i, bars):
        if i < self.lookback:
            return HOLD
        r = (bars[i].close / bars[i - self.lookback].close - 1) * 100
        if r > self.threshold:
            return LONG
        if r < -self.threshold:
            return FLAT
        return HOLD


class MacdCross(Strategy):
    """Long while the MACD histogram is positive."""
    name = "macd-cross"

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self.fast, self.slow, self.signal = fast, slow, signal
        self.warmup = slow + signal

    def on_bar(self, i, bars):
        if i < self.warmup:
            return HOLD
        closes = [b.close for b in bars[:i + 1]]
        hist = ind.macd(closes, self.fast, self.slow, self.signal)[2]
        v = hist[i]
        return LONG if (v is not None and v > 0) else FLAT


class DonchianBreakout(Strategy):
    """Channel breakout: enter on new highs, exit on new lows."""
    name = "donchian-breakout"

    def __init__(self, entry: int = 20, exit: int = 10):
        self.entry = entry
        self.exit = exit
        self.warmup = entry + 1

    def on_bar(self, i, bars):
        if i <= self.entry:
            return HOLD
        prior_high = max(b.high for b in bars[i - self.entry:i])
        prior_low = min(b.low for b in bars[i - self.exit:i])
        if bars[i].close >= prior_high:
            return LONG
        if bars[i].close <= prior_low:
            return FLAT
        return HOLD


STRATEGIES = {
    "buy-hold": lambda: BuyHold(),
    "sma": lambda: SmaCrossover(20, 50),
    "rsi": lambda: RsiReversion(14, 30.0, 70.0),
    "bollinger": lambda: BollingerReversion(20, 2.0),
    "momentum": lambda: Momentum(20, 0.0),
    "macd": lambda: MacdCross(12, 26, 9),
    "breakout": lambda: DonchianBreakout(20, 10),
}

# Class registry for the walk-forward optimizer (constructed with **params).
STRATEGY_CLASSES = {
    "buy-hold": BuyHold,
    "sma": SmaCrossover,
    "rsi": RsiReversion,
    "bollinger": BollingerReversion,
    "momentum": Momentum,
    "macd": MacdCross,
    "breakout": DonchianBreakout,
}

# Small, deliberately boring grids. Big grids are how you curve-fit.
PARAM_GRIDS = {
    "buy-hold": {},
    "sma": {"fast": [10, 20, 30], "slow": [50, 100]},
    "rsi": {"period": [7, 14], "buy_below": [25.0, 30.0], "sell_above": [65.0, 70.0]},
    "bollinger": {"period": [20], "num_std": [1.5, 2.0, 2.5]},
    "momentum": {"lookback": [10, 20, 40], "threshold": [0.0, 2.0]},
    "macd": {"fast": [12], "slow": [26], "signal": [9]},
    "breakout": {"entry": [20, 55], "exit": [10, 20]},
}
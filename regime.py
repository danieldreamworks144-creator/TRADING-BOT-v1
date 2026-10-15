"""Market regime detection: classify each bar as TRENDING_UP, TRENDING_DOWN,
RANGING, or HIGH_VOL, using only information available at that bar.

This is the core of "strategic decision making". Different strategies make
money in different regimes -- trend followers ride trending markets and get
chopped up in ranges; mean reversion does the opposite. The honest fix is not
a magic signal, it is knowing which game is being played right now and only
letting strategies vote when their game is on.

No lookahead: every regime value at index i is computed from bars 0..i only.
"""
from __future__ import annotations

import indicators as ind

REGIME_TREND_UP = "trend_up"
REGIME_TREND_DOWN = "trend_down"
REGIME_RANGING = "ranging"
REGIME_HIGH_VOL = "high_vol"

# Which regimes each strategy family is allowed to trade in.
REGIME_PROFILES = {
    "trend": {REGIME_TREND_UP, REGIME_TREND_DOWN},
    "reversion": {REGIME_RANGING},
    "breakout": {REGIME_TREND_UP, REGIME_HIGH_VOL},
    "always": {REGIME_TREND_UP, REGIME_TREND_DOWN, REGIME_RANGING, REGIME_HIGH_VOL},
}


def detect_regime(bars, i: int, trend_len: int = 100, adx_len: int = 14,
                  chop_thresh: float = 0.03, vol_len: int = 20,
                  vol_thresh: float | None = None) -> str:
    """Classify the market at bar i.

    - HIGH_VOL: current ATR% is vol_thresh x its own rolling median (default 1.75x).
    - TREND_UP / TREND_DOWN: close above/below the trend_len SMA by more than
      chop_thresh (as a fraction of price) AND the trend is slope-consistent.
    - RANGING: everything else (price glued to its mean, directionless).
    """
    if i < max(trend_len, vol_len * 2, adx_len) + 1:
        return REGIME_RANGING  # not enough history: default to the safest label

    closes = [b.close for b in bars[:i + 1]]
    px = closes[i]

    # --- volatility regime -------------------------------------------------
    atr_series = ind.atr(bars, vol_len)
    atr = atr_series[i]
    if atr is not None and px > 0:
        atr_pct = atr / px
        hist = [a / b.close for a, b in zip(atr_series[max(0, i - vol_len * 2):i + 1],
                                            bars[max(0, i - vol_len * 2):i + 1])
                if a is not None and b.close > 0]
        if hist:
            med = sorted(hist)[len(hist) // 2]
            thresh = vol_thresh if vol_thresh is not None else 1.75 * med
            if atr_pct > thresh and med > 0:
                return REGIME_HIGH_VOL

    # --- trend vs range ----------------------------------------------------
    mean = sum(closes[i - trend_len + 1:i + 1]) / trend_len
    dist = (px - mean) / mean if mean else 0.0

    # slope consistency: fraction of the last 20 bars where close > its own SMA
    # (or < its own SMA for downtrends). Chop flips sides constantly; a real
    # trend does not.
    ups = downs = 0
    for j in range(max(trend_len, i - 19), i + 1):
        m = sum(closes[j - trend_len + 1:j + 1]) / trend_len
        if closes[j] > m:
            ups += 1
        elif closes[j] < m:
            downs += 1
    n = ups + downs or 1

    if dist > chop_thresh and ups / n >= 0.70:
        return REGIME_TREND_UP
    if dist < -chop_thresh and downs / n >= 0.70:
        return REGIME_TREND_DOWN
    return REGIME_RANGING


def regime_series(bars, **kw) -> list:
    """Regime label per bar (aligned to bars)."""
    return [detect_regime(bars, i, **kw) for i in range(len(bars))]
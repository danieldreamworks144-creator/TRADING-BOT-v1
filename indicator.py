"""Technical indicators -- pure Python, causal (no lookahead).

Every series is aligned to the input bars: warmup positions are None, so a
consumer can never accidentally read an indicator before it exists."""
from __future__ import annotations


def sma(values, period: int):
    out = [None] * len(values)
    if period <= 0:
        return out
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= period:
            run -= values[i - period]
        if i >= period - 1:
            out[i] = run / period
    return out


def stdev(values, period: int):
    out = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / period
        out[i] = var ** 0.5
    return out


def ema(values, period: int):
    out = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1)
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(closes, period: int = 14):
    """Wilder RSI."""
    out = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    avg_g, avg_l = gains / period, losses / period
    out[period] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1 + avg_g / avg_l)
    for i in range(period + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(ch, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-ch, 0.0)) / period
        out[i] = 100.0 if avg_l == 0 else 100.0 - 100.0 / (1 + avg_g / avg_l)
    return out


def atr(bars, period: int = 14):
    """Wilder average true range."""
    out = [None] * len(bars)
    if len(bars) <= period:
        return out
    trs = []
    for i, b in enumerate(bars):
        if i == 0:
            trs.append(b.high - b.low)
        else:
            pc = bars[i - 1].close
            trs.append(max(b.high - b.low, abs(b.high - pc), abs(b.low - pc)))
    prev = sum(trs[1:period + 1]) / period
    out[period] = prev
    for i in range(period + 1, len(bars)):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def bollinger(closes, period: int = 20, num_std: float = 2.0):
    mid = sma(closes, period)
    sd = stdev(closes, period)
    upper = [None if m is None else m + num_std * s for m, s in zip(mid, sd)]
    lower = [None if m is None else m - num_std * s for m, s in zip(mid, sd)]
    return mid, upper, lower


def macd(closes, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = [None] * len(closes)
    start = slow - 1
    for i in range(start, len(closes)):
        macd_line[i] = ema_fast[i] - ema_slow[i]
    signal_line = [None] * len(closes)
    hist = [None] * len(closes)
    defined = [v for v in macd_line if v is not None]
    if len(defined) >= signal:
        sig = ema(defined, signal)
        j = 0
        for i in range(start, len(closes)):
            if sig[j] is not None:
                signal_line[i] = sig[j]
                hist[i] = macd_line[i] - sig[j]
            j += 1
    return macd_line, signal_line, hist


def donchian(bars, period: int):
    """Prior-period channel (excludes the current bar -- no lookahead)."""
    upper = [None] * len(bars)
    lower = [None] * len(bars)
    for i in range(period, len(bars)):
        window = bars[i - period:i]
        upper[i] = max(b.high for b in window)
        lower[i] = min(b.low for b in window)
    return upper, lower


def roc(closes, period: int = 10):
    out = [None] * len(closes)
    for i in range(period, len(closes)):
        out[i] = (closes[i] / closes[i - period] - 1) * 100
    return out
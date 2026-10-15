"""OHLCV bar loading and synthetic sample generation -- stdlib only."""
from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Bar:
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def generate_synthetic(n: int = 750, seed: int = 7, start_price: float = 100.0):
    """Random-walk OHLCV with regime shifts. For testing the ENGINE, not for
    proving a strategy makes money -- synthetic data flatters every strategy."""
    rng = random.Random(seed)
    bars = []
    price = start_price
    drift = 0.0002
    vol = 0.015
    d = date(2024, 1, 2)
    for i in range(n):
        if i % 120 == 0:
            drift = rng.uniform(-0.0006, 0.0012)
            vol = rng.uniform(0.008, 0.03)
        ret = rng.gauss(drift, vol)
        o = price
        c = max(0.01, price * (1 + ret))
        hi = max(o, c) * (1 + abs(rng.gauss(0, vol / 2)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, vol / 2)))
        bars.append(Bar(d.isoformat(), round(o, 4), round(hi, 4),
                        round(lo, 4), round(c, 4), round(rng.uniform(1e3, 1e5), 2)))
        price = c
        d += timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
    return bars


def load_ohlcv(path: str):
    bars = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            bars.append(Bar(row["ts"], float(row["open"]), float(row["high"]),
                            float(row["low"]), float(row["close"]),
                            float(row.get("volume", 0) or 0)))
    if not bars:
        raise ValueError(f"no bars found in {path}")
    return bars


def save_ohlcv(path: str, bars):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for b in bars:
            w.writerow([b.ts, b.open, b.high, b.low, b.close, b.volume])


def fetch_csv(url: str):
    """Load OHLCV from a public CSV URL. Accepts ts/date + open/high/low/close
    columns in any case; maps common naming schemes (Date, Open...)."""
    import io
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "gideon-backtester/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    bars = []
    for row in reader:
        low = {k.lower().strip(): v for k, v in row.items() if k}
        ts = low.get("ts") or low.get("date") or low.get("timestamp") or low.get("time")
        try:
            bars.append(Bar(str(ts), float(low["open"]), float(low["high"]),
                            float(low["low"]), float(low["close"]),
                            float(low.get("volume") or 0)))
        except (KeyError, TypeError, ValueError):
            continue
    if not bars:
        raise ValueError(f"no parseable OHLCV rows in {url}")
    return bars
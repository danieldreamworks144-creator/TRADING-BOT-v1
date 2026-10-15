"""Exchange integration layer: OKX + Bybit, demo-first, secret-safe.

What this layer does:
- Pulls PUBLIC market data (tickers/candles) from OKX and Bybit -- no keys needed.
- Reports a clear connection status per exchange (connected / degraded / error).
- Validates API CONFIGURATION without ever exposing the secret: a key is either
  present or not; the secret is never logged, printed, returned to the frontend,
  or written to the database.
- Has NO withdrawal, transfer, or funding call anywhere in it. It only READS.

Credential handling:
- Keys come ONLY from environment variables on the backend (this process).
- `validate_credentials()` returns a status word and a redacted hint like
  "key set (****a1b2)" -- never the secret itself.
- This module never returns a secret to any caller. Treat any change that makes
  it do so as a security bug.

Demo-first: everything here is READ-ONLY. Order placement is deliberately not
part of this layer's public surface for the platform build -- the simulator in
sim.py handles all trading. If live order code is ever added, it must go behind
the same three gates as exchange.py and default to disabled.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# The exchanges this platform is built against.
SUPPORTED = {
    "okx": {"ccxt_id": "okx", "key_env": "OKX_API_KEY", "secret_env": "OKX_API_SECRET",
            "passphrase_env": "OKX_API_PASSPHRASE"},
    "bybit": {"ccxt_id": "bybit", "key_env": "BYBIT_API_KEY", "secret_env": "BYBIT_API_SECRET",
              "passphrase_env": None},
}


@dataclass
class ConnectionStatus:
    exchange: str
    status: str            # connected | degraded | error | unsupported
    detail: str = ""
    last_price: float | None = None
    has_credentials: bool = False
    credential_hint: str = ""    # redacted, e.g. "key set (****a1b2)"
    ts: str = ""


def _ccxt(exchange: str):
    import ccxt
    if exchange not in SUPPORTED:
        raise ValueError(f"unsupported exchange: {exchange}")
    cls = getattr(ccxt, SUPPORTED[exchange]["ccxt_id"])
    return cls({"enableRateLimit": True, "timeout": 9000})


def _credential_hint(exchange: str) -> tuple[bool, str]:
    """Presence + redacted hint. Never returns the secret."""
    cfg = SUPPORTED[exchange]
    key = os.environ.get(cfg["key_env"], "")
    secret = os.environ.get(cfg["secret_env"], "")
    if not key and not secret:
        return False, "no credentials configured (public data only)"
    if not key or not secret:
        return False, "incomplete credentials (need both key and secret)"
    tail = key[-4:] if len(key) >= 4 else "****"
    return True, f"key set (****{tail})"


def validate_credentials(exchange: str) -> dict:
    """Check credential CONFIG without exposing secrets.

    Returns a dict safe to show a user/log: status word + redacted hint.
    Does NOT call the exchange (that would be `connection_status`)."""
    if exchange not in SUPPORTED:
        return {"exchange": exchange, "status": "unsupported",
                "detail": f"supported: {', '.join(SUPPORTED)}", "has_credentials": False,
                "credential_hint": ""}
    has, hint = _credential_hint(exchange)
    cfg = SUPPORTED[exchange]
    envs = [cfg["key_env"], cfg["secret_env"]] + ([cfg["passphrase_env"]] if cfg["passphrase_env"] else [])
    return {
        "exchange": exchange,
        "status": "configured" if has else "public-only",
        "detail": f"reads credentials from: {', '.join(envs)}",
        "has_credentials": has,
        "credential_hint": hint,
    }


def fetch_ticker(exchange: str, pair: str) -> float:
    """Public last price. Raises on failure -- callers handle the status."""
    ex = _ccxt(exchange)
    t = ex.fetch_ticker(pair)
    return float(t["last"])


def fetch_ohlcv(exchange: str, pair: str, timeframe: str = "1m", limit: int = 120) -> list[dict]:
    """Public candles as dicts (ts, open, high, low, close, volume)."""
    ex = _ccxt(exchange)
    raw = ex.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)
    out = []
    for ts, o, h, l, c, v in raw:
        out.append({"ts": ts, "open": float(o), "high": float(h),
                    "low": float(l), "close": float(c), "volume": float(v or 0)})
    return out


def connection_status(exchange: str, pair: str = "ETH/USDC") -> ConnectionStatus:
    """Live connection probe: can we read public data right now?"""
    from store import now_ts
    if exchange not in SUPPORTED:
        return ConnectionStatus(exchange, "unsupported", ts=now_ts())
    has, hint = _credential_hint(exchange)
    try:
        px = fetch_ticker(exchange, pair)
        return ConnectionStatus(exchange, "connected", "public market data reachable",
                                last_price=px, has_credentials=has, credential_hint=hint, ts=now_ts())
    except Exception as e:  # network, geo-block, rate limit...
        return ConnectionStatus(exchange, "error", f"{type(e).__name__}: {str(e)[:120]}",
                                has_credentials=has, credential_hint=hint, ts=now_ts())


def all_status(pair: str = "ETH/USDC") -> dict[str, ConnectionStatus]:
    return {name: connection_status(name, pair) for name in SUPPORTED}

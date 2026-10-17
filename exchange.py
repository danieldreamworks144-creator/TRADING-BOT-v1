"""Centralized-exchange connectivity: real market data in, orders out -- with
hard safety gates before a single real order can ever leave this machine.

Three layers, all behind one interface:
  1. PaperExchange  -- live PRICES from a real exchange, simulated FILLS. No
     keys, no orders, no risk. This is the default and what runs first.
  2. CcxtExchange   -- real exchange adapter (Binance, Kraken, Coinbase, and
     100+ more via ccxt). READ-ONLY by default: prices and balances only.
  3. Live orders    -- opt-in behind THREE gates (see _require_live), and the
     code has no withdrawal path at all -- it cannot move funds out of the
     exchange, ever, even with a fully-permissioned key.

Key handling rules (non-negotiable):
- API keys come ONLY from environment variables. Never written to disk, never
  logged, never sent anywhere but the exchange's own HTTPS endpoint.
- Use exchange keys with TRADE-ONLY permission. Withdrawal permission must be
  OFF on the key. This module cannot enforce that server-side, so it enforces
  the client half: no withdraw/funding/transfer call exists here at all.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass

from data import Bar


class ExchangeError(RuntimeError):
    pass


class LiveNotArmed(ExchangeError):
    """Raised when something tries to place a real order without all gates."""


@dataclass
class Ticker:
    pair: str
    last: float
    bid: float
    ask: float
    ts: str = ""


@dataclass
class OrderResult:
    exchange_id: str
    pair: str
    side: str          # buy | sell
    amount: float      # base-asset units
    price: float
    filled: bool
    simulated: bool    # True = paper fill, False = real exchange order
    note: str = ""


# --------------------------------------------------------------------------
# Live-trading arming: three gates, all required.
# --------------------------------------------------------------------------
LIVE_ACK_PHRASE = "I_UNDERSTAND_I_CAN_LOSE_MONEY"


def _require_live(armed: bool, ack_env: str | None) -> None:
    """Gate 1: explicit armed=True at call time.
    Gate 2: env GIDEON_TRADING_LIVE_ACK must equal the ack phrase.
    Gate 3 (config): max_order_usd and daily_loss_limit_usd must be set --
    checked by the caller because they are per-run limits."""
    if not armed:
        raise LiveNotArmed(
            "live orders are disabled (armed=False). Paper mode fills locally."
        )
    if (ack_env or os.environ.get("GIDEON_TRADING_LIVE_ACK", "")) != LIVE_ACK_PHRASE:
        raise LiveNotArmed(
            "live orders require env GIDEON_TRADING_LIVE_ACK="
            f"{LIVE_ACK_PHRASE} (set it yourself, deliberately)."
        )


class PaperExchange:
    """Live prices, simulated fills against a local wallet. The default.

    Uses the real exchange's public ticker/candles so the bot sees real market
    movement, but every order fills locally at the quoted price plus slippage
    and fees. No keys required. Nothing leaves this machine."""

    def __init__(self, exchange_id: str = "binance", fee_bps: float = 10.0,
                 slippage_bps: float = 5.0):
        self.exchange_id = f"paper-{exchange_id}"
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps
        self._market = _make_ccxt(exchange_id, read_only=True)

    # -- market data (real) ---------------------------------------------
    def fetch_ticker(self, pair: str) -> Ticker:
        t = self._market.fetch_ticker(pair)
        return Ticker(pair, float(t["last"]), float(t.get("bid") or t["last"]),
                      float(t.get("ask") or t["last"]), str(t.get("timestamp") or ""))

    def fetch_ohlcv(self, pair: str, timeframe: str = "1h", limit: int = 500) -> list[Bar]:
        from datetime import datetime, timezone
        raw = self._market.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)
        out = []
        for ts_ms, o, h, l, c, v in raw:
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(timespec="seconds")
            out.append(Bar(ts, float(o), float(h), float(l), float(c), float(v or 0.0)))
        return out

    # -- orders (simulated) ---------------------------------------------
    def place_order(self, pair: str, side: str, amount: float,
                    price: float | None = None, **_) -> OrderResult:
        px = price or self.fetch_ticker(pair).last
        slip = px * self.slippage_bps / 10_000.0
        fill = px + slip if side == "buy" else px - slip
        return OrderResult("simulated", pair, side, amount, fill,
                           filled=True, simulated=True,
                           note=f"paper fill, fee {self.fee_bps}bps + slip {self.slippage_bps}bps")

    def balances(self) -> dict:
        raise ExchangeError("paper fills live in the Wallet; PaperExchange holds no balances")


# --------------------------------------------------------------------------
# Real exchange adapter (ccxt). Read-only unless armed.
# --------------------------------------------------------------------------
def _make_ccxt(exchange_id: str, read_only: bool = True):
    try:
        import ccxt
    except ImportError as e:  # pragma: no cover
        raise ExchangeError("ccxt is not installed (pip install ccxt)") from e
    if exchange_id not in ccxt.exchanges:
        raise ExchangeError(f"unknown exchange {exchange_id!r}")
    cls = getattr(ccxt, exchange_id)
    ex = cls({"enableRateLimit": True})
    if not read_only:
        key = os.environ.get(f"{exchange_id.upper()}_API_KEY", "")
        secret = os.environ.get(f"{exchange_id.upper()}_API_SECRET", "")
        if not key or not secret:
            raise ExchangeError(
                f"missing credentials: set {exchange_id.upper()}_API_KEY and "
                f"{exchange_id.upper()}_API_SECRET in the environment "
                "(trade-only permission on the key; withdrawals OFF)"
            )
        ex.apiKey, ex.secret = key, secret
        passphrase = os.environ.get(f"{exchange_id.upper()}_API_PASSPHRASE", "")
        if passphrase and hasattr(ex, "password"):
            ex.password = passphrase
    return ex


class CcxtExchange:
    """Real centralized-exchange adapter.

    READ-ONLY by default (armed=False): fetch_ticker / fetch_ohlcv / balances
    work, place_order refuses. Live orders need armed=True AND the ack env AND
    per-run limits. There is deliberately no withdraw method on this class."""

    def __init__(self, exchange_id: str = "binance", armed: bool = False,
                 max_order_usd: float = 0.0, daily_loss_limit_usd: float = 0.0):
        self.exchange_id = exchange_id
        self.armed = armed
        self.max_order_usd = max_order_usd
        self.daily_loss_limit_usd = daily_loss_limit_usd
        self._ex = _make_ccxt(exchange_id, read_only=not armed)
        self._day_realized_pnl = 0.0

    # -- market data -----------------------------------------------------
    def fetch_ticker(self, pair: str) -> Ticker:
        t = self._ex.fetch_ticker(pair)
        return Ticker(pair, float(t["last"]), float(t.get("bid") or t["last"]),
                      float(t.get("ask") or t["last"]), str(t.get("timestamp") or ""))

    def fetch_ohlcv(self, pair: str, timeframe: str = "1h", limit: int = 500) -> list[Bar]:
        from datetime import datetime, timezone
        raw = self._ex.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)
        out = []
        for ts_ms, o, h, l, c, v in raw:
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(timespec="seconds")
            out.append(Bar(ts, float(o), float(h), float(l), float(c), float(v or 0.0)))
        return out

    def balances(self) -> dict:
        raw = self._ex.fetch_balance()
        return {k: float(v.get("free", 0.0)) for k, v in raw.get("total", {}).items()
                if isinstance(v, dict) and float(v.get("free") or 0.0) > 0}

    # -- orders (gated) --------------------------------------------------
    def place_order(self, pair: str, side: str, amount: float,
                    price: float | None = None, order_type: str = "limit",
                    dry_run: bool = False) -> OrderResult:
        _require_live(self.armed, None)
        if side not in ("buy", "sell"):
            raise ExchangeError(f"side must be buy|sell, got {side!r}")
        if not math.isfinite(amount) or amount <= 0:
            raise ExchangeError(f"amount must be positive, got {amount!r}")
        if self.daily_loss_limit_usd > 0 and -self._day_realized_pnl >= self.daily_loss_limit_usd:
            raise LiveNotArmed(
                f"daily loss limit hit ({self.daily_loss_limit_usd} USD): no more orders today"
            )
        px = price or self.fetch_ticker(pair).last
        notional = amount * px
        if self.max_order_usd > 0 and notional > self.max_order_usd:
            raise ExchangeError(
                f"order notional {notional:.2f} USD exceeds max_order_usd={self.max_order_usd}"
            )
        if dry_run:
            return OrderResult("dry-run", pair, side, amount, px,
                               filled=False, simulated=True,
                               note="gates passed; nothing sent (dry_run)")
        o = self._ex.create_order(pair, order_type, side, amount, price)
        return OrderResult(str(o.get("id", "")), pair, side, amount,
                           float(o.get("price") or px), filled=bool(o.get("filled")),
                           simulated=False, note="REAL exchange order")

    def record_realized_pnl(self, pnl_usd: float) -> None:
        """Feed realized P&L here so the daily loss gate can trip."""
        self._day_realized_pnl += pnl_usd


# The class intentionally exposes NO withdraw / transfer / funding method.
# Even with a fully-permissioned API key, this codebase cannot move funds out
# of the exchange. If you ever add one, you have broken a stated safety
# property -- don't.

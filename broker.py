"""Execution adapters: the ONE seam between a decided trade and a fill.

  DemoExecutor -- simulated fills, mode='demo'. Nothing leaves this machine.
  LiveExecutor -- REAL orders with REAL assets on a real exchange (OKX, Bybit,
                  and 100+ more via ccxt). OFF by default, and construction
                  refuses unless ALL gates are open:
                    1. armed=True passed explicitly at construction
                    2. env GIDEON_TRADING_LIVE_ACK=I_UNDERSTAND_I_CAN_LOSE_MONEY
                    3. max_order_usd > 0 AND daily_loss_limit_usd > 0
                    4. trade-only API keys in env (withdrawals OFF on the key)

The engine never touches an exchange directly -- it asks an executor. Swapping
the executor is the ONLY difference between demo and real trading. Demo is the
default everywhere.

Still true in live mode, permanently:
- No withdraw / transfer / funding call exists in this codebase. Even fully
  armed with real keys, this bot cannot move funds OUT of the exchange.
- Keys are read from environment variables only: never stored, never logged,
  never returned to the frontend.
- Every real fill is recorded with its exchange order id as external_ref so
  the ledger can be reconciled against the exchange."""
from __future__ import annotations

import os
from dataclasses import dataclass

FEE_RATE = 0.001  # 0.10% estimated taker fee (demo math; live records estimates too)


@dataclass
class ExecutionResult:
    ok: bool
    simulated: bool
    amount: float = 0.0        # base-asset units
    price: float = 0.0         # fill (or estimated fill) price
    fee: float = 0.0
    external_ref: str = ""     # exchange order id (live only)
    note: str = ""
    error: str = ""


class DemoExecutor:
    """Simulated fills. The default. Sends nothing anywhere."""
    mode = "demo"

    def execute(self, pair: str, side: str, amount: float, price: float,
                reason: str = "") -> ExecutionResult:
        fee = amount * price * FEE_RATE
        return ExecutionResult(ok=True, simulated=True, amount=amount,
                               price=price, fee=fee,
                               note=f"demo fill ({reason})" if reason else "demo fill")


class LiveExecutor:
    """REAL orders with REAL assets. Every gate is checked at construction --
    if any gate is closed, this object refuses to exist, so nothing downstream
    can accidentally trade live."""
    mode = "live"

    def __init__(self, exchange_id: str = "okx", armed: bool = False,
                 ack_env: str | None = None,
                 max_order_usd: float = 0.0, daily_loss_limit_usd: float = 0.0):
        from exchange import CcxtExchange, LiveNotArmed, LIVE_ACK_PHRASE
        if not armed:
            raise LiveNotArmed("live trading requires armed=True (gate 1)")
        ack = ack_env if ack_env is not None else os.environ.get("GIDEON_TRADING_LIVE_ACK", "")
        if ack != LIVE_ACK_PHRASE:
            raise LiveNotArmed(
                "live trading requires env "
                f"GIDEON_TRADING_LIVE_ACK={LIVE_ACK_PHRASE} (gate 2)")
        if max_order_usd <= 0 or daily_loss_limit_usd <= 0:
            raise LiveNotArmed(
                "live trading requires BOTH --max-order-usd and "
                "--daily-loss-limit-usd > 0 (gate 3)")
        self.exchange_id = exchange_id
        self._ex = CcxtExchange(exchange_id, armed=True,
                                max_order_usd=max_order_usd,
                                daily_loss_limit_usd=daily_loss_limit_usd)

    def execute(self, pair: str, side: str, amount: float, price: float,
                reason: str = "") -> ExecutionResult:
        try:
            res = self._ex.place_order(pair, side, amount,
                                       price=None, order_type="market")
            fee = amount * (res.price or price) * FEE_RATE
            return ExecutionResult(ok=True, simulated=False, amount=amount,
                                   price=res.price or price, fee=fee,
                                   external_ref=res.exchange_id,
                                   note=f"REAL ORDER ({res.note})")
        except Exception as e:  # fail safe: report, never crash the platform
            return ExecutionResult(ok=False, simulated=False,
                                   error=f"{type(e).__name__}: {str(e)[:140]}")

    def record_realized_pnl(self, pnl_usd: float) -> None:
        """Feed realized P&L so the daily-loss gate trips on real results too."""
        self._ex.record_realized_pnl(pnl_usd)


def build_executor(mode: str, exchange_id: str, **gates) -> object:
    """Factory: 'demo' -> DemoExecutor (always allowed);
    'live' -> LiveExecutor (refuses unless every gate is open)."""
    if mode == "demo":
        return DemoExecutor()
    if mode == "live":
        return LiveExecutor(exchange_id, **gates)
    raise ValueError(f"unknown mode: {mode!r} (use 'demo' or 'live')")

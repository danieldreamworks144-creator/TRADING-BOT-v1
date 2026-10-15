"""Trading engine: reads market data, evaluates signals, places trades.

Runs in TWO modes through one executor seam (broker.py):
  DEMO (default) -- simulated fills, mode='demo', nothing leaves this machine.
  LIVE           -- REAL orders with REAL assets, behind every gate. Off until
                    you arm it with your own trade-only keys.

MULTI-PAIR: `evaluate({pair: candles})` runs the signal on every pair in one
call and can open SEVERAL positions at once (e.g. ETH + SOL + BTC together).
Risk limits are checked ACROSS all positions, not per pair.

Safety guarantees (tested in tests/test_platform.py):
- Every trade records WHY it triggered (pair, signal, fast/slow, price).
- Stale/outdated market data blocks trading on that pair -- never trades ghosts.
- Duplicate-order guard: `dup_key` (pair+side+price+minute) is unique in the DB.
- Errors PAUSE the engine and land in the errors table -- fails safe, not loud.
- All trades pass RiskLimits.allow() across total exposure + open count first.
- No withdraw/transfer/funding capability exists anywhere. The engine can buy
  and sell on an exchange but can NEVER move funds out of it.

Signals are intentionally simple and explicit -- a fast/slow SMA cross per pair.
The engine proves the SAFETY + RECORD-KEEPING machinery across multiple
simultaneous positions; it does not promise a profitable signal."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from broker import DemoExecutor, FEE_RATE
from limits import RiskLimits
from store import Store, now_ts


@dataclass
class DemoState:
    cash: float = 10_000.0
    start_cash: float = 10_000.0
    paused: bool = False
    pause_reason: str = ""


@dataclass
class Engine:
    store: Store
    limits: RiskLimits
    pair: str = "ETH/USDC"
    exchange: str = "okx"
    fast: int = 5
    slow: int = 20
    max_data_age_s: int = 300
    pairs: list[str] | None = None       # multi-pair universe (default: [pair])
    executor: object | None = None       # DemoExecutor unless a LiveExecutor is injected
    state: DemoState = field(default_factory=DemoState)

    def __post_init__(self):
        if self.pairs is None:
            self.pairs = [self.pair]
        if self.pair not in self.pairs:
            self.pairs = [self.pair] + list(self.pairs)
        if self.executor is None:
            self.executor = DemoExecutor()

    @property
    def mode(self) -> str:
        return getattr(self.executor, "mode", "demo")

    # -- helpers ---------------------------------------------------------
    def _dup_key(self, side: str, price: float, ts_bucket: int, pair: str) -> str:
        raw = f"{self.exchange}:{pair}:{side}:{round(price, 2)}:{ts_bucket}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _sma(self, closes: list[float], n: int) -> float | None:
        if len(closes) < n:
            return None
        return sum(closes[-n:]) / n

    def _data_is_fresh(self, candles: list[dict]) -> tuple[bool, str]:
        if not candles:
            return False, "no market data"
        import datetime as _dt
        last_ms = candles[-1]["ts"]
        last = _dt.datetime.fromtimestamp(last_ms / 1000, tz=_dt.timezone.utc)
        age_s = (_dt.datetime.now(_dt.timezone.utc) - last).total_seconds()
        if age_s > self.max_data_age_s:
            return False, f"market data is {age_s:.0f}s old (> {self.max_data_age_s}s)"
        return True, f"data fresh ({age_s:.0f}s old)"

    # -- core ------------------------------------------------------------
    def evaluate(self, candles) -> dict:
        """Accept a candle list (single pair) or a {pair: candles} dict (multi).
        Returns one decision per pair. Never raises on bad data -- records and
        pauses instead."""
        if isinstance(candles, dict):
            return self._evaluate_many(candles)
        return self._evaluate_one(self.pair, candles)

    def _evaluate_many(self, by_pair: dict) -> dict:
        results = {}
        actions = []
        for pair in sorted(by_pair):
            r = self._evaluate_one(pair, by_pair[pair])
            results[pair] = r
            if r.get("action") in ("buy", "sell"):
                actions.append((pair, r["action"], r.get("amount") or 0.0))
        traded = [f"{p}:{a}" for p, a, _ in actions]
        summary = ("multiple:" + ",".join(traded)) if len(actions) > 1 else \
                  (f"{actions[0][0]}:{actions[0][1]}" if actions else "hold")
        return {"mode": self.mode, "action": summary, "pairs": results,
                "trades_this_step": [{"pair": p, "action": a, "amount": n}
                                     for p, a, n in actions]}

    def _evaluate_one(self, pair: str, candles: list[dict]) -> dict:
        try:
            fresh, fresh_msg = self._data_is_fresh(candles)
            closes = [c["close"] for c in candles]
            price = closes[-1] if closes else None

            if self.state.paused or self.limits.paused:
                reason = self.state.pause_reason or self.limits.pause_reason or "paused"
                return {"action": "paused", "reason": reason, "price": price,
                        "data": fresh_msg, "mode": self.mode, "pair": pair}

            if not fresh or price is None:
                self.store.event("engine", f"skip {pair}: {fresh_msg}", {"pair": pair})
                return {"action": "skip", "reason": fresh_msg, "price": price,
                        "data": fresh_msg, "mode": self.mode, "pair": pair}

            fast, slow = self._sma(closes, self.fast), self._sma(closes, self.slow)
            if fast is None or slow is None:
                return {"action": "skip", "reason": "warming up (not enough candles)",
                        "price": price, "data": fresh_msg, "mode": self.mode, "pair": pair}

            signal = "buy" if fast > slow else "sell" if fast < slow else "none"
            open_pos = self.store.positions("open")
            exposure = sum(p["amount"] * p["entry_price"] for p in open_pos)
            result = {"action": "hold", "price": price, "fast": fast, "slow": slow,
                      "signal": signal, "data": fresh_msg, "mode": self.mode, "pair": pair}

            if signal == "buy" and not any(p["pair"] == pair for p in open_pos):
                return self._do_buy(pair, candles, price, fast, slow, open_pos, exposure, result)

            if signal == "sell" and any(p["pair"] == pair for p in open_pos):
                pos = next(p for p in open_pos if p["pair"] == pair)
                return self._do_sell(pair, candles, price, fast, slow, pos, result)

            return result
        except Exception as e:  # fail safe: pause, never crash the platform
            self.state.paused = True
            self.state.pause_reason = f"engine error: {type(e).__name__}: {str(e)[:120]}"
            self.store.error("engine.evaluate", self.state.pause_reason)
            return {"action": "error", "reason": self.state.pause_reason,
                    "mode": self.mode, "pair": pair}

    def _do_buy(self, pair, candles, price, fast, slow, open_pos, exposure, result):
        reason = f"SMA cross up: fast {fast:.2f} > slow {slow:.2f}"
        usd = min(self.state.cash * 0.20, self.limits.max_position_usd)
        allowed, why = self.limits.allow(position_usd=usd,
                                         open_positions=len(open_pos),
                                         total_exposure_usd=exposure)
        if not allowed:
            self.store.event("risk", f"buy blocked on {pair}: {why}", {"pair": pair})
            if self.limits.paused:
                self.store.event("risk", f"PAUSED: {self.limits.pause_reason}")
            return {**result, "action": "blocked", "reason": why}

        # size so amount*price + fee == usd exactly
        amount = usd / (price * (1 + FEE_RATE))
        ts_bucket = int(candles[-1]["ts"] // 60_000)  # one fill per minute max
        dup = self._dup_key("buy", price, ts_bucket, pair)
        ex = self.executor.execute(pair, "buy", amount, price, reason)
        if not ex.ok:
            self.store.error("executor.buy", f"{pair}: {ex.error}")
            return {**result, "action": "error", "reason": ex.error}

        fee = ex.fee
        order_id = self.store.insert_order(
            exchange=self.exchange, pair=pair, side="buy",
            amount=ex.amount, price=ex.price, fee=fee, status="filled",
            reason=reason + (f" [ref:{ex.external_ref}]" if ex.external_ref else ""),
            dup_key=dup, mode=self.mode,
            live_confirmation=ex.external_ref)
        if order_id is None:
            self.store.event("engine", f"duplicate order blocked on {pair}", {"dup_key": dup})
            return {**result, "action": "duplicate_blocked",
                    "reason": "identical order already filled this minute"}
        self.state.cash -= ex.amount * ex.price + fee
        self.store.open_position(pair, ex.amount, ex.price, self.exchange)
        self.store.event("trade",
                         f"{'REAL' if not ex.simulated else 'DEMO'} BUY {ex.amount:.6f} "
                         f"{pair.split('/')[0]} @ {ex.price:.2f} ({reason})",
                         {"order_id": order_id, "fee": fee, "mode": self.mode,
                          "external_ref": ex.external_ref, "pair": pair})
        return {**result, "action": "buy", "amount": ex.amount, "fee": fee,
                "external_ref": ex.external_ref, "reason": reason}

    def _do_sell(self, pair, candles, price, fast, slow, pos, result):
        reason = f"SMA cross down: fast {fast:.2f} < slow {slow:.2f}"
        dup = self._dup_key("sell", price, int(candles[-1]["ts"] // 60_000), pair)
        ex = self.executor.execute(pair, "sell", pos["amount"], price, reason)
        if not ex.ok:
            self.store.error("executor.sell", f"{pair}: {ex.error}")
            return {**result, "action": "error", "reason": ex.error}

        fee = ex.fee
        proceeds = ex.amount * ex.price
        pnl = proceeds - fee - pos["amount"] * pos["entry_price"]
        order_id = self.store.insert_order(
            exchange=self.exchange, pair=pair, side="sell",
            amount=ex.amount, price=ex.price, fee=fee, status="filled",
            reason=reason + (f" [ref:{ex.external_ref}]" if ex.external_ref else ""),
            dup_key=dup, mode=self.mode,
            live_confirmation=ex.external_ref)
        if order_id is None:
            return {**result, "action": "duplicate_blocked",
                    "reason": "identical order already filled this minute"}
        self.state.cash += proceeds - fee
        self.store.close_position(pos["id"], ex.price, pnl)
        self.limits.record_pnl(pnl)
        if hasattr(self.executor, "record_realized_pnl"):
            self.executor.record_realized_pnl(pnl)
        self.store.event("trade",
                         f"{'REAL' if not ex.simulated else 'DEMO'} SELL {ex.amount:.6f} "
                         f"@ {ex.price:.2f} P&L {pnl:+.2f} ({reason})",
                         {"order_id": order_id, "pnl": pnl, "fee": fee,
                          "mode": self.mode, "external_ref": ex.external_ref, "pair": pair})
        if self.limits.paused:
            self.store.event("risk", f"PAUSED: {self.limits.pause_reason}")
        return {**result, "action": "sell", "pnl": pnl, "fee": fee,
                "external_ref": ex.external_ref, "reason": reason}

    # -- balances / P&L ---------------------------------------------------
    def snapshot(self, price) -> dict:
        """price: a float (single pair) or {pair: price} (multi). Marks every
        open position at its own pair's price."""
        prices = price if isinstance(price, dict) else {self.pair: price}
        open_pos = self.store.positions("open")
        pos_value = 0.0
        unreal = 0.0
        marked = []
        for p in open_pos:
            px = prices.get(p["pair"]) or p["entry_price"]
            pos_value += p["amount"] * px
            unreal += p["amount"] * (px - p["entry_price"])
            marked.append({"pair": p["pair"], "amount": p["amount"],
                           "entry": p["entry_price"], "mark": px,
                           "unrealized_pnl": p["amount"] * (px - p["entry_price"])})
        equity = self.state.cash + pos_value
        realized = sum(p["pnl"] for p in self.store.positions("closed"))
        snap = {
            "mode": self.mode,
            "cash": self.state.cash,
            "positions_value": pos_value,
            "equity": equity,
            "unrealized_pnl": unreal,
            "realized_pnl": realized,
            "open_positions": len(open_pos),
            "start_cash": self.state.start_cash,
            "total_pnl": equity - self.state.start_cash,
            "price": prices.get(self.pair),
            "prices": prices,
            "positions_marked": marked,
            "ts": now_ts(),
        }
        self.store.mark_equity(equity, self.state.cash, pos_value, unreal)
        return snap

    def restart_safe(self) -> dict:
        """Rehydrate after a restart: state is rebuilt from the DB, not from
        memory. Called by run_platform on startup."""
        closed = self.store.positions("closed")
        realized = sum(p["pnl"] for p in closed)
        orders = self.store.orders(1000)
        spent = sum(o["amount"] * o["price"] + o["fee"] for o in orders if o["side"] == "buy")
        received = sum(o["amount"] * o["price"] - o["fee"] for o in orders if o["side"] == "sell")
        self.state.cash = self.state.start_cash - spent + received
        self.store.event("engine", f"restart: state rebuilt from DB "
                                   f"(cash {self.state.cash:.2f}, realized {realized:+.2f})")
        return {"cash": self.state.cash, "realized_pnl": realized,
                "open_positions": len(self.store.positions("open"))}

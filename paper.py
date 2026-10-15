"""Paper trading: replay the most recent bars live, one bar at a time, with
zero real money and zero exchange connectivity.

This is the layer that must run and survive BEFORE any live-trading talk.
Its state file records every fill so a run can be audited after the fact."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from backtest import compute_metrics
from config import BacktestConfig
from data import Bar
from risk import RiskManager


@dataclass
class PaperState:
    cash: float
    shares: float = 0.0
    entry_price: float = 0.0
    entry_ts: str = ""
    entry_equity: float = 0.0
    entry_stop: float = 0.0
    peak_price: float = 0.0
    curve: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    bars_seen: int = 0

    @property
    def equity(self) -> float:
        px = self.entry_price if self.shares > 0 else 1.0
        return self.cash + self.shares * px


class PaperTrader:
    """Feed bars one at a time in chronological order. State persists to JSON
    so a crashed or restarted session resumes exactly where it left off."""

    def __init__(self, strategy, cfg: BacktestConfig, state_path: str = "paper_state.json"):
        self.strategy = strategy
        self.cfg = cfg
        self.risk = RiskManager(cfg)
        self.state_path = state_path
        self.state = (self._load() or PaperState(cfg.starting_capital))

    # -- persistence -----------------------------------------------------
    def _load(self):
        if os.path.exists(self.state_path):
            with open(self.state_path) as f:
                return PaperState(**json.load(f))
        return None

    def save(self):
        with open(self.state_path, "w") as f:
            json.dump(asdict(self.state), f, indent=2)

    # -- core loop -------------------------------------------------------
    def on_bar(self, bar: Bar, atr_value=None) -> str | None:
        """Advance one bar. Returns the action taken ('buy'/'sell:reason') or None."""
        s = self.state
        px = bar.close
        action = None

        if s.shares > 0:
            s.peak_price = max(s.peak_price, px)
            stop = s.entry_stop
            trailing = s.peak_price * (1 - self.cfg.trailing_stop_pct) if self.cfg.trailing_stop_pct > 0 else None
            if trailing:
                stop = max(stop, trailing)
            if px <= stop:
                action = "sell:" + ("trailing-stop" if trailing and stop >= trailing else "stop-loss")
            elif self.cfg.take_profit_pct > 0 and px >= s.entry_price * (1 + self.cfg.take_profit_pct):
                action = "sell:take-profit"

        if action is None:
            sig = self.strategy.on_bar(s.bars_seen, _BarsView(self))
            if sig == "FLAT" and s.shares > 0:
                action = "sell:signal"
            elif sig == "LONG" and s.shares == 0:
                qty = self.risk.size(s.cash, px, atr_value)
                fee_frac = self.cfg.fee_bps / 10000
                # clamp to what cash covers INCLUDING the fee (see backtest.py)
                affordable = s.cash / (px * (1 + fee_frac)) if px > 0 else 0.0
                qty = min(qty, affordable)
                cost = qty * px
                fee = cost * fee_frac
                if qty > 0 and cost + fee <= s.cash + 1e-9:
                    s.cash -= cost + fee
                    s.shares = qty
                    s.entry_price = px
                    s.entry_ts = bar.ts
                    s.entry_equity = s.cash + qty * px
                    s.entry_stop = self.risk.initial_stop(px, atr_value)
                    s.peak_price = px
                    action = "buy"

        if action and action.startswith("sell"):
            sell_px = px * (1 - self.cfg.slippage_bps / 10000)
            proceeds = s.shares * sell_px
            fee = proceeds * self.cfg.fee_bps / 10000
            pnl = (sell_px - s.entry_price) * s.shares - fee
            s.cash += proceeds - fee
            s.trades.append({"entry_ts": s.entry_ts, "exit_ts": bar.ts,
                             "entry": s.entry_price, "exit": sell_px,
                             "shares": s.shares, "pnl": pnl,
                             "pnl_pct": (sell_px / s.entry_price - 1) if s.entry_price else 0.0,
                             "reason": action.split(":", 1)[1]})
            s.shares = 0.0

        s.bars_seen += 1
        s.curve.append(s.cash + s.shares * px)
        self.save()
        return action

    def metrics(self) -> dict:
        return compute_metrics(self.state.curve, _paper_trades(self.state), self.state.shares and 1 or 0)


class _BarsView:
    """Minimal bars-like view backed by PaperTrader history for strategy calls
    that index the bar list (warmup checks use s.bars_seen)."""

    def __init__(self, trader: PaperTrader):
        self._t = trader

    def __len__(self):
        return self._t.state.bars_seen

    def __getitem__(self, i):
        raise IndexError("paper mode exposes only the current bar to strategies")


def _paper_trades(state: PaperState):
    from backtest import Trade
    return [Trade(t["entry_ts"], t["exit_ts"], t["entry"], t["exit"],
                  t["shares"], t["pnl"], t["pnl_pct"], 0.0, t["reason"])
            for t in state.trades]

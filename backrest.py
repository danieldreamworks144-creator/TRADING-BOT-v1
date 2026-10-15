"""Backtest engine: fees, slippage, stop-loss, position sizing, drawdown halt."""
from __future__ import annotations

from dataclasses import dataclass, field

import indicators as ind
from config import BacktestConfig
from risk import RiskManager


@dataclass
class Trade:
    entry_ts: str
    exit_ts: str
    entry: float
    exit: float
    shares: float
    pnl: float
    pnl_pct: float
    eq_pct: float
    reason: str


@dataclass
class Result:
    curve: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    halted: bool = False
    bars_in_market: int = 0


def run_backtest(bars, strategy, cfg: BacktestConfig, risk: RiskManager | None = None) -> Result:
    risk = risk or RiskManager(cfg)
    need_atr = cfg.stop_mode == "atr" or cfg.vol_target > 0
    atr_series = ind.atr(bars, cfg.atr_period) if need_atr else None
    cash = cfg.starting_capital
    shares = 0.0
    entry_price = 0.0
    entry_ts = ""
    entry_equity = 0.0
    entry_stop = 0.0
    peak_price = 0.0
    cooldown = 0
    peak = cash
    halted = False
    bars_in_market = 0
    curve, trades = [], []

    def close_position(px, ts, reason):
        nonlocal cash, shares
        sell_px = px * (1 - cfg.slippage_bps / 10000)
        proceeds = shares * sell_px
        fee = proceeds * cfg.fee_bps / 10000
        pnl = (sell_px - entry_price) * shares - fee
        cash += proceeds - fee
        trades.append(Trade(entry_ts, ts, entry_price, sell_px, shares, pnl,
                            (sell_px / entry_price - 1) if entry_price else 0.0,
                            pnl / entry_equity if entry_equity else 0.0, reason))
        shares = 0.0

    for i, bar in enumerate(bars):
        px = bar.close
        a = atr_series[i] if atr_series is not None else None
        just_exited = False

        if cooldown > 0:
            cooldown -= 1

        if shares > 0:
            peak_price = max(peak_price, px)
            stop = entry_stop
            trailing = None
            if cfg.trailing_stop_pct > 0:
                trailing = peak_price * (1 - cfg.trailing_stop_pct)
                stop = max(stop, trailing)
            if px <= stop:
                reason = "trailing-stop" if (trailing is not None and stop >= trailing) else "stop-loss"
                close_position(px, bar.ts, reason)
                cooldown, just_exited = cfg.cooldown_bars, True
            elif cfg.take_profit_pct > 0 and px >= entry_price * (1 + cfg.take_profit_pct):
                close_position(px, bar.ts, "take-profit")
                just_exited = True

        if not halted:
            sig = strategy.on_bar(i, bars)
            if sig == "FLAT" and shares > 0:
                close_position(px, bar.ts, "signal")
                just_exited = True
            elif sig == "LONG" and shares == 0 and cooldown == 0 and not just_exited:
                buy_px = px * (1 + cfg.slippage_bps / 10000)
                qty = risk.size(cash, buy_px, a)
                fee_frac = cfg.fee_bps / 10000
                # Clamp to what cash can cover INCLUDING the fee. Without this,
                # a max-capacity size prices at exactly `cash`, then the fee
                # pushes the cost over cash and the trade is silently skipped --
                # which is how the drawdown kill-switch never fired in testing.
                affordable = cash / (buy_px * (1 + fee_frac)) if buy_px > 0 else 0.0
                qty = min(qty, affordable)
                cost = qty * buy_px
                fee = cost * fee_frac
                if qty > 0 and cost + fee <= cash + 1e-9:
                    cash -= cost + fee
                    shares = qty
                    entry_price = buy_px
                    entry_ts = bar.ts
                    entry_equity = cash + shares * buy_px
                    entry_stop = risk.initial_stop(buy_px, a)
                    peak_price = buy_px

        eq = cash + shares * px
        peak = max(peak, eq)
        if not halted and peak > 0 and (1 - eq / peak) >= cfg.max_drawdown_halt:
            halted = True
            if shares > 0:
                close_position(px, bar.ts, "drawdown-halt")
                eq = cash
        curve.append(eq)
        if shares > 0:
            bars_in_market += 1

    if shares > 0:
        close_position(bars[-1].close, bars[-1].ts, "end-of-data")
        curve[-1] = cash
    return Result(curve, trades, halted, bars_in_market)


def compute_metrics(curve, trades, bars_in_market: int = 0, bars_per_year: int = 252) -> dict:
    start, end = curve[0], curve[-1]
    total = end / start - 1
    years = len(curve) / bars_per_year
    cagr = (end / start) ** (1 / years) - 1 if years > 0 and end > 0 else 0.0
    rets = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve))]
    mean = sum(rets) / len(rets) if rets else 0.0
    var = sum((r - mean) ** 2 for r in rets) / len(rets) if rets else 0.0
    std = var ** 0.5
    sharpe = (mean / std) * (bars_per_year ** 0.5) if std > 0 else 0.0
    downside = (sum(min(r, 0.0) ** 2 for r in rets) / len(rets)) ** 0.5 if rets else 0.0
    sortino = (mean / downside) * (bars_per_year ** 0.5) if downside > 0 else 0.0
    peak, mdd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    calmar = cagr / mdd if mdd > 0 else 0.0
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    win_pcts = [t.pnl_pct for t in wins]
    loss_pcts = [t.pnl_pct for t in losses]
    return {
        "total_return": total,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_drawdown": mdd,
        "trades": len(trades),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else float("inf"),
        "expectancy": sum(t.pnl_pct for t in trades) / len(trades) if trades else 0.0,
        "avg_win_pct": sum(win_pcts) / len(win_pcts) if win_pcts else 0.0,
        "avg_loss_pct": sum(loss_pcts) / len(loss_pcts) if loss_pcts else 0.0,
        "exposure": bars_in_market / len(curve) if curve else 0.0,
        "final_equity": end,
    }
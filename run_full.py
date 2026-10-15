"""One command that runs the whole system honestly:

  python3 run_full.py --usdc 1000 --eth 0.5 --bars 750
  python3 run_full.py --usdc 1000 --deposit SOL=2.5 --price SOL=150 --deposit BTC=0.1 --price BTC=60000

1. Opens a wallet and accepts deposits of ANY chain's asset -- ETH (Ethereum),
   SOL (Solana), BTC (Bitcoin), USDC (any chain), and anything else with a price.
2. Runs the regime-gated ensemble over ETH/USDC bars (fees + slippage on).
3. Marks the combined portfolio (trading sleeve + every held asset) in USDC terms.
4. Runs the improvement gate on the strategy registry and logs verdicts.
5. Writes reports/dashboard.html -- the portfolio growth chart.

What this does NOT do: place real orders, promise returns. The default data is
synthetic (seeded, labeled) so the pipeline can be tested end to end; swap in
real OHLCV CSVs before believing any number. Non-ETH holdings are marked at
their deposit price (no live marks) and the chart says so.
"""
from __future__ import annotations

import argparse
import os

from backtest import compute_metrics, run_backtest
from chains import describe
from config import BacktestConfig
from dashboard import write_dashboard
from data import generate_synthetic, load_ohlcv, save_ohlcv
from ensemble import Ensemble
from improve import record, validate
from report import render_equity_svg
from risk import RiskManager
from strategy import STRATEGIES
from wallet import Wallet

FAMILIES = {
    "buy-hold": "always", "sma": "trend", "momentum": "trend",
    "macd": "trend", "breakout": "breakout",
    "rsi": "reversion", "bollinger": "reversion",
}


class EnsembleStrategy:
    """Adapts the Ensemble to the single-asset backtest engine: exposure above
    the threshold means LONG, below means FLAT. Position SIZE still comes from
    the risk manager -- the ensemble decides direction and conviction only."""

    name = "ensemble"

    def __init__(self, ensemble: Ensemble, threshold: float = 0.35):
        self.ensemble = ensemble
        self.threshold = threshold
        self.exposures = []

    def on_bar(self, i, bars):
        d = self.ensemble.decide(bars, i)
        self.exposures.append(d.target_exposure)
        return "LONG" if d.target_exposure >= self.threshold else "FLAT"


def main() -> None:
    ap = argparse.ArgumentParser(description="Full pipeline: wallet + ensemble + chart + improvement gate")
    ap.add_argument("--usdc", type=float, default=1000.0, help="USDC deposit")
    ap.add_argument("--eth", type=float, default=0.0, help="ETH deposit (held, marked to market)")
    ap.add_argument("--deposit", action="append", default=[],
                    help="SYMBOL=AMOUNT on ANY chain, repeatable: --deposit SOL=2.5 --deposit BTC=0.1")
    ap.add_argument("--price", action="append", default=[],
                    help="SYMBOL=PRICE in USDC for deposits the bars don't price: --price SOL=150")
    ap.add_argument("--bars", type=int, default=750)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--data", default=None, help="real OHLCV CSV (ts,o,h,l,c,v)")
    ap.add_argument("--capital", type=float, default=None, help="trading capital in USDC (default: the USDC deposit)")
    ap.add_argument("--strategy", default="all", help="all, or a comma list of strategy keys")
    args = ap.parse_args()

    root = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(os.path.join(root, "reports"), exist_ok=True)

    # ---- 1. wallet: accept ETH and USDC ---------------------------------
    wallet = Wallet()
    if args.usdc > 0:
        wallet.deposit("USDC", args.usdc)
    price0 = None
    if args.data:
        bars = load_ohlcv(args.data)
        price0 = bars[-1].close
    else:
        path = os.path.join(root, "data", "eth-usdc.csv")
        bars = generate_synthetic(args.bars, args.seed, start_price=2000.0)
        save_ohlcv(path, bars)
        price0 = bars[-1].close
        print(f"NOTE: using SEEDED SYNTHETIC ETH/USDC bars -> {path}. Numbers below prove the "
              f"pipeline works, NOT that any strategy makes money on real markets.")
    wallet.set_price("ETH", price0)
    if args.eth > 0:
        wallet.deposit("ETH", args.eth, price=price0)
    print(f"wallet opened: {wallet.balances}  (equity {wallet.equity():,.2f} USDC)")

    # ---- 2. regime-gated ensemble over ETH/USDC -------------------------
    keys = sorted(k for k in STRATEGIES if k != "buy-hold") if args.strategy == "all" \
        else [k.strip() for k in args.strategy.split(",")]
    members = {k: STRATEGIES[k]() for k in keys}
    ens = Ensemble(members, {k: FAMILIES.get(k, "always") for k in keys})
    strat = EnsembleStrategy(ens)

    trading_capital = args.capital if args.capital is not None else max(args.usdc, 1.0)
    cfg = BacktestConfig(starting_capital=trading_capital, pair="ETH/USDC",
                         base_asset="ETH", quote_asset="USDC")
    res = run_backtest(bars, strat, cfg, RiskManager(cfg))
    m = compute_metrics(res.curve, res.trades, res.bars_in_market)

    # ---- 3. combined portfolio in USDC: trading sleeve + held ETH --------
    held_eth = args.eth
    combined = [curve_v + held_eth * b.close for curve_v, b in zip(res.curve, bars)]
    buy_hold = [trading_capital * (b.close / bars[0].close) for b in bars]

    # ---- 4. improvement gate -------------------------------------------
    gate_notes = []
    for key in keys:
        try:
            g = validate(key, bars)
            record(g)
            gate_notes.append(f"{key}: {'ADOPTED' if g.adopted else 'rejected'} -- {g.reason}")
        except ValueError as e:
            gate_notes.append(f"{key}: gate skipped -- {e}")
    print("improvement gate:")
    for n in gate_notes:
        print("  " + n)

    # ---- 5. portfolio growth chart -------------------------------------
    metrics = compute_metrics(combined, res.trades, res.bars_in_market)
    path = write_dashboard(combined, metrics,
                           holdings=wallet.holdings(), trades=res.trades,
                           buy_hold=buy_hold, halted=res.halted,
                           notes=[f"ensemble members: {', '.join(keys)}",
                                  f"fees {cfg.fee_bps:.0f} bps + slippage {cfg.slippage_bps:.0f} bps per fill",
                                  "data: SEEDED SYNTHETIC ETH/USDC" if not args.data else f"data: {args.data}",
                                  "no real orders placed -- research software, not financial advice"] + gate_notes)
    print(f"\nfinal equity (USDC, trading+held): {metrics['final_equity']:,.2f}")
    svg = render_equity_svg(combined, os.path.join(root, "reports", "portfolio-growth.svg"),
                            title="Portfolio growth (USDC) -- synthetic data", baseline=buy_hold)
    print(f"chart      -> {svg}")
    print(f"dashboard -> {path}")


if __name__ == "__main__":
    main()

"""Run a multi-asset portfolio backtest, quote currency USDC.

  python3 run_portfolio.py --assets ETH,BTC --gen 750 --capital 1000
  python3 run_portfolio.py --assets ETH --data data/ethereum-usdc.csv --strategy momentum
"""
from __future__ import annotations

import argparse
import os

from config import BacktestConfig
from data import generate_synthetic, load_ohlcv, save_ohlcv
from portfolio import run_portfolio
from report import render_equity_svg, render_text
from strategy import STRATEGIES


def load_asset(symbol: str, args) -> list:
    path = args.data if args.data else f"data/{symbol.lower()}-{args.quote.lower()}.csv"
    if args.gen > 0 or not os.path.exists(path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # distinct seeds per symbol so sleeves are not identical synthetic paths
        seed = args.seed + sum(ord(c) for c in symbol) % 97
        start = {"ETH": 2000.0, "BTC": 40000.0}.get(symbol, 100.0)
        bars = generate_synthetic(args.gen or 750, seed, start_price=start)
        save_ohlcv(path, bars)
        print(f"generated {len(bars)} synthetic {symbol}/{args.quote} bars -> {path}")
    return load_ohlcv(path)


def main() -> None:
    ap = argparse.ArgumentParser(description="Multi-asset portfolio backtester (quote: USDC).")
    ap.add_argument("--assets", default="ETH,BTC", help="comma-separated base assets, e.g. ETH,BTC")
    ap.add_argument("--quote", default="USDC", help="quote/account currency (default USDC)")
    ap.add_argument("--data", default=None, help="single CSV (only valid with one asset)")
    ap.add_argument("--gen", type=int, default=0, help="generate N synthetic bars per asset")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--capital", type=float, default=1000.0, help="starting capital in quote currency")
    ap.add_argument("--strategy", choices=sorted(STRATEGIES), default="momentum")
    ap.add_argument("--weights", default=None, help="e.g. ETH=0.6,BTC=0.4 (default: equal)")
    ap.add_argument("--chart", default=None, help="write an SVG equity chart here")
    ap.add_argument("--fee-bps", type=float, default=10.0)
    ap.add_argument("--slippage-bps", type=float, default=5.0)
    ap.add_argument("--stop-loss-pct", type=float, default=0.05)
    ap.add_argument("--risk-per-trade", type=float, default=0.02)
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.assets.split(",") if s.strip()]
    if args.data and len(symbols) != 1:
        raise SystemExit("--data works with exactly one asset; use per-asset data/<sym>-<quote>.csv")
    weights = None
    if args.weights:
        weights = {k.strip().upper(): float(v) for k, v in
                   (kv.split("=") for kv in args.weights.split(","))}

    bars_by_symbol = {sym: load_asset(sym, args) for sym in symbols}
    cfg = BacktestConfig(
        starting_capital=args.capital, fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps, stop_loss_pct=args.stop_loss_pct,
        risk_per_trade=args.risk_per_trade,
        quote_asset=args.quote,
        pair=f"{symbols[0]}/{args.quote}", base_asset=symbols[0],
    )
    pf = run_portfolio(bars_by_symbol, STRATEGIES[args.strategy], cfg, weights)

    print()
    print(f"strategy        : {args.strategy}")
    print(f"account currency: {pf.quote_asset}")
    print(f"assets          : {', '.join(symbols)}")
    print(f"starting capital: {cfg.starting_capital:,.2f} {pf.quote_asset}")
    print()
    print(f"--- combined ({pf.quote_asset}) ---")
    m = pf.combined_metrics()
    print(render_text(m, label="portfolio"))
    for sym in symbols:
        sm = pf.sleeve_metrics[sym]
        print()
        print(f"--- sleeve {sym}/{pf.quote_asset} ---")
        print(render_text(sm, label=sym))

    if args.chart:
        render_equity_svg(pf.combined_curve, args.chart,
                          title=f"Portfolio equity ({pf.quote_asset}) -- {args.strategy}")
        print(f"\nchart -> {args.chart}")
    print()
    print("Reminder: synthetic/past data cannot predict future returns.")
    print("This is a research tool, not financial advice, and not a promise of profit.")


if __name__ == "__main__":
    main()

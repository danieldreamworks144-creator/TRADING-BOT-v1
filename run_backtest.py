#!/usr/bin/env python3
"""Run a backtest and print an honest performance report.

  python3 run_backtest.py --gen 750 --capital 1 --strategy sma
  python3 run_backtest.py --data data/ethereum-usdc.csv --pair ETH/USDC --chart eq.svg
  python3 run_backtest.py --url <public-csv-url> --strategy momentum --mc 2000 --walkforward 4
"""
from __future__ import annotations

import argparse
import os

from backtest import compute_metrics, run_backtest
from config import BacktestConfig
from data import generate_synthetic, fetch_csv, load_ohlcv, save_ohlcv
from montecarlo import monte_carlo
from report import render_equity_svg, render_json, render_text
from strategy import STRATEGIES
from walkforward import walk_forward


def main() -> None:
    ap = argparse.ArgumentParser(description="Honest backtester (no profit promises).")
    ap.add_argument("--data", default="data/sample_ohlcv.csv")
    ap.add_argument("--url", default=None, help="load OHLCV from a public CSV URL instead of --data")
    ap.add_argument("--gen", type=int, default=0, help="generate N synthetic bars instead of loading --data")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--pair", default="ETH/USDC", help="label, e.g. ETH/USDC, BTC/USDC")
    ap.add_argument("--capital", type=float, default=1.0, help="starting capital in quote currency")
    ap.add_argument("--strategy", choices=sorted(STRATEGIES), default="sma")
    ap.add_argument("--fee-bps", type=float, default=10.0)
    ap.add_argument("--slippage-bps", type=float, default=5.0)
    ap.add_argument("--stop-loss-pct", type=float, default=0.05)
    ap.add_argument("--risk-per-trade", type=float, default=0.02)
    ap.add_argument("--stop-mode", choices=["fixed", "atr"], default="fixed")
    ap.add_argument("--atr-stop-mult", type=float, default=2.0)
    ap.add_argument("--trailing-stop-pct", type=float, default=0.0)
    ap.add_argument("--take-profit-pct", type=float, default=0.0)
    ap.add_argument("--cooldown-bars", type=int, default=0)
    ap.add_argument("--vol-target", type=float, default=0.0)
    ap.add_argument("--chart", default=None, help="write an SVG equity chart to this path")
    ap.add_argument("--json", dest="json_out", default=None, help="write metrics JSON to this path")
    ap.add_argument("--mc", type=int, default=0, help="run N Monte Carlo trade shuffles")
    ap.add_argument("--walkforward", type=int, default=0, help="walk-forward with N out-of-sample windows")
    args = ap.parse_args()

    if args.url:
        bars = fetch_csv(args.url)
        print(f"loaded {len(bars)} bars from {args.url}")
    elif args.gen > 0:
        os.makedirs(os.path.dirname(args.data) or ".", exist_ok=True)
        bars = generate_synthetic(args.gen, args.seed)
        save_ohlcv(args.data, bars)
        print(f"generated {len(bars)} synthetic bars -> {args.data}")
    else:
        bars = load_ohlcv(args.data)

    base, quote = (args.pair.split("/", 1) + [""])[:2] if "/" in args.pair else (args.pair, "USDC")
    cfg = BacktestConfig(
        starting_capital=args.capital, fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps, stop_loss_pct=args.stop_loss_pct,
        risk_per_trade=args.risk_per_trade, stop_mode=args.stop_mode,
        atr_stop_mult=args.atr_stop_mult, trailing_stop_pct=args.trailing_stop_pct,
        take_profit_pct=args.take_profit_pct, cooldown_bars=args.cooldown_bars,
        vol_target=args.vol_target, pair=args.pair,
        base_asset=base or "ETH", quote_asset=quote or "USDC",
    )
    strategy = STRATEGIES[args.strategy]()
    res = run_backtest(bars, strategy, cfg)
    m = compute_metrics(res.curve, res.trades, res.bars_in_market)
    buy_hold = bars[-1].close / bars[0].close - 1

    print()
    print(f"pair            : {cfg.pair}  (account in {cfg.quote_asset})")
    print(f"bars            : {len(bars)}  ({bars[0].ts} -> {bars[-1].ts})")
    print(f"starting capital: {cfg.starting_capital:,.2f} {cfg.quote_asset}")
    print(render_text(m, buy_hold, res.halted, label=strategy.name))
    if res.trades:
        worst = min(res.trades, key=lambda t: t.pnl)
        print(f"worst trade     : {worst.pnl_pct * 100:+.2f}% on {worst.entry_ts} ({worst.reason})")

    if args.mc:
        mc = monte_carlo([t.eq_pct for t in res.trades], 1.0, runs=args.mc)
        print()
        print(f"--- Monte Carlo ({args.mc} trade-order shuffles, equity multiples) ---")
        print(f"median final    : {mc.median_final:,.3f}x")
        print(f"5th percentile  : {mc.p5_final:,.3f}x   (bad luck case)")
        print(f"95th percentile : {mc.p95_final:,.3f}x   (good luck case)")
        print(f"median max DD   : {mc.median_max_dd * 100:.1f}%")
        print(f"95th pct max DD : {mc.p95_max_dd * 100:.1f}%")
        print(f"ruin probability: {mc.ruin_prob * 100:.1f}% of sequences end below start")

    if args.walkforward:
        wf = walk_forward(bars, args.strategy, cfg, n_windows=args.walkforward)
        print()
        print(f"--- Walk-forward ({args.walkforward} out-of-sample windows) ---")
        for i, w in enumerate(wf.windows, 1):
            print(f"window {i}: IS sharpe {w.in_sample_sharpe:+.2f} -> "
                  f"OOS sharpe {w.out_sample_sharpe:+.2f} | OOS return "
                  f"{w.out_sample_return * 100:+.1f}% | OOS DD {w.out_sample_dd * 100:.1f}% | "
                  f"params {w.best_params}")
        print(f"avg OOS sharpe  : {wf.avg_oos_sharpe:+.2f}")
        print(f"robustness      : {wf.robustness * 100:.0f}% of in-sample skill survived")

    if args.chart:
        render_equity_svg(res.curve, args.chart, title=f"{strategy.name} -- {cfg.pair}")
        print(f"\nchart -> {args.chart}")
    if args.json_out:
        with open(args.json_out, "w") as f:
            f.write(render_json(m, pair=cfg.pair, strategy=strategy.name,
                                halted=res.halted, buy_hold=buy_hold))
        print(f"json  -> {args.json_out}")

    print()
    print("Reminder: synthetic/past data cannot predict future returns.")
    print("This is a research tool, not financial advice, and not a promise of profit.")


if __name__ == "__main__":
    main()

"""Compare every strategy on the same data, ranked by out-of-sample-style
metrics. Fast way to see which logic deserves a walk-forward run next.

  python3 compare_strategies.py --gen 750
  python3 compare_strategies.py --data data/ethereum-usdc.csv
"""
from __future__ import annotations

import argparse
import os

from backtest import compute_metrics, run_backtest
from config import BacktestConfig
from data import generate_synthetic, load_ohlcv, save_ohlcv
from strategy import STRATEGIES


def main() -> None:
    ap = argparse.ArgumentParser(description="Strategy comparison table.")
    ap.add_argument("--data", default="data/sample_ohlcv.csv")
    ap.add_argument("--gen", type=int, default=0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--capital", type=float, default=1000.0)
    ap.add_argument("--quote", default="USDC")
    ap.add_argument("--fee-bps", type=float, default=10.0)
    ap.add_argument("--slippage-bps", type=float, default=5.0)
    args = ap.parse_args()

    if args.gen > 0:
        os.makedirs(os.path.dirname(args.data) or ".", exist_ok=True)
        bars = generate_synthetic(args.gen, args.seed)
        save_ohlcv(args.data, bars)
    else:
        bars = load_ohlcv(args.data)

    cfg = BacktestConfig(starting_capital=args.capital, fee_bps=args.fee_bps,
                         slippage_bps=args.slippage_bps, quote_asset=args.quote)
    rows = []
    for key, factory in STRATEGIES.items():
        r = run_backtest(bars, factory(), cfg)
        m = compute_metrics(r.curve, r.trades, r.bars_in_market)
        rows.append((key, m))

    rows.sort(key=lambda kv: kv[1]["sharpe"], reverse=True)
    bh = bars[-1].close / bars[0].close - 1
    print(f"\n{len(bars)} bars | capital {args.capital:,.2f} {args.quote} | "
          f"fees {args.fee_bps}bps + slippage {args.slippage_bps}bps | buy&hold {bh * 100:+.1f}%\n")
    hdr = f"{'strategy':<18}{'return':>10}{'CAGR':>9}{'Sharpe':>8}{'maxDD':>8}{'trades':>8}{'win%':>7}{'PF':>6}{'expos':>7}"
    print(hdr)
    print("-" * len(hdr))
    for key, m in rows:
        pf = m["profit_factor"]
        print(f"{key:<18}{m['total_return'] * 100:>9.1f}%{m['cagr'] * 100:>8.1f}%"
              f"{m['sharpe']:>8.2f}{m['max_drawdown'] * 100:>7.1f}%{m['trades']:>8}"
              f"{m['win_rate'] * 100:>6.0f}%{'inf' if pf == float('inf') else f'{pf:.2f}':>6}"
              f"{m['exposure'] * 100:>6.0f}%")
    print("\nNote: same-data ranking inflates every entry. Run --walkforward on a winner before believing it.")


if __name__ == "__main__":
    main()
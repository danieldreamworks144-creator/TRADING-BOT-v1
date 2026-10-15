"""Connect the trading bot to a REAL centralized exchange.

  # paper mode (default): real live prices, simulated fills, no keys needed
  python3 run_live.py --exchange binance --pair ETH/USDC --once

  # continuous paper trading on live prices
  python3 run_live.py --exchange kraken --pair ETH/USDC --interval 60

  # read-only: what balances does the exchange show? (keys required)
  python3 run_live.py --exchange binance --balances

  # DRY RUN against the real order path -- every gate runs, nothing is sent
  python3 run_live.py --exchange binance --live-dry-run --amount 0.01

Everything here is paper-first by default. Real orders require:
  1. --armed
  2. env GIDEON_TRADING_LIVE_ACK="I_UNDERSTAND_I_CAN_LOSE_MONEY"
  3. --max-order-usd and --daily-loss-limit-usd both set
  4. trade-only API keys in the environment (withdrawals OFF on the key)
There is no withdrawal code path anywhere in this project."""
from __future__ import annotations

import argparse
import os
import sys
import time

from config import BacktestConfig
from data import Bar
from exchange import CcxtExchange, ExchangeError, LiveNotArmed, PaperExchange
from paper import PaperTrader
from strategy import STRATEGIES


def build_trader(strategy_key: str, start_usdc: float, state_path: str) -> PaperTrader:
    cfg = BacktestConfig(starting_capital=start_usdc)
    return PaperTrader(STRATEGIES[strategy_key](), cfg, state_path=state_path)


def sync_from_exchange(exchange, trader: PaperTrader, pair: str, base: str, quote: str):
    """Optional: seed the paper wallet with what the exchange says you hold.
    Read-only -- it copies balances in, it never moves anything out."""
    if isinstance(exchange, PaperExchange):
        return  # paper wallet is seeded from --start-usdc; nothing to sync
    try:
        bals = exchange.balances()
    except Exception as e:
        print(f"[balances] could not read: {e}")
        return
    trader.state.cash = bals.get(quote, trader.state.cash)
    trader.state.shares = bals.get(base, trader.state.shares)
    print(f"[balances] synced from {exchange.exchange_id}: "
          f"{quote}={trader.state.cash} {base}={trader.state.shares}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--exchange", default="binance", help="ccxt exchange id (binance, kraken, ...)")
    p.add_argument("--pair", default="ETH/USDC")
    p.add_argument("--strategy", default="sma", choices=sorted(STRATEGIES))
    p.add_argument("--start-usdc", type=float, default=1000.0)
    p.add_argument("--state", default="live_paper_state.json")
    p.add_argument("--interval", type=int, default=60, help="seconds between bars in loop mode")
    p.add_argument("--once", action="store_true", help="one bar, then exit")
    p.add_argument("--balances", action="store_true", help="read-only: print exchange balances")
    p.add_argument("--live-dry-run", action="store_true",
                   help="run the REAL order path's gates but send nothing")
    p.add_argument("--armed", action="store_true",
                   help="enable real orders (also needs GIDEON_TRADING_LIVE_ACK + limits)")
    p.add_argument("--amount", type=float, default=0.0, help="base-asset amount for a test order")
    p.add_argument("--side", default="buy", choices=["buy", "sell"])
    p.add_argument("--max-order-usd", type=float, default=0.0)
    p.add_argument("--daily-loss-limit-usd", type=float, default=0.0)
    args = p.parse_args(argv)

    base, quote = args.pair.split("/")
    live = args.armed or args.live_dry_run

    if live:
        try:
            ex = CcxtExchange(args.exchange, armed=True,
                              max_order_usd=args.max_order_usd,
                              daily_loss_limit_usd=args.daily_loss_limit_usd)
        except ExchangeError as e:
            print(f"REFUSED: {e}")
            return 3
    else:
        ex = PaperExchange(args.exchange)

    if args.balances:
        if isinstance(ex, PaperExchange):
            print("--balances needs a real exchange adapter; drop paper defaults:")
            print("  python3 run_live.py --exchange binance --balances --armed")
            return 2
        for asset, free in sorted(ex.balances().items()):
            print(f"{asset:>10}: {free}")
        return 0

    if args.live_dry_run or (args.armed and args.amount > 0):
        try:
            res = ex.place_order(args.pair, args.side, args.amount,
                                 order_type="limit", dry_run=args.live_dry_run)
        except LiveNotArmed as e:
            print(f"REFUSED: {e}")
            return 3
        print(f"order: {res}")
        return 0

    # -- paper trading on live prices ------------------------------------
    trader = build_trader(args.strategy, args.start_usdc, args.state)
    sync_from_exchange(ex, trader, args.pair, base, quote)
    print(f"[paper] strategy={args.strategy} pair={args.pair} "
          f"source={ex.exchange_id} equity={trader.state.equity:.2f} {quote}")

    while True:
        bars = ex.fetch_ohlcv(args.pair, timeframe="1m", limit=200)
        bar = bars[-1]
        action = trader.on_bar(bar)
        trader.save()
        mark = trader.state.equity
        print(f"[{bar.ts}] close={bar.close:.2f} action={action or '-'} "
              f"equity={mark:.2f} {quote} trades={len(trader.state.trades)}")
        if args.once:
            print(f"[done] one bar processed. state saved to {args.state}")
            return 0
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[stop] state saved; no live orders were placed "
                  f"(mode={'LIVE' if live else 'paper-on-live-prices'})")
            return 0


if __name__ == "__main__":
    sys.exit(main())

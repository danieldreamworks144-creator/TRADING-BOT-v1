# Trading bot framework (backtest + risk engine)

## The honest part first

**No trading bot turns $1 into millions.** If one could, its owner would not sell it.
What actually happens with strategies that look like they can is: they carry ruin-level
risk, work until they don't, and blow up the account. The realistic, defensible goal is
a *process*: test a strategy with costs included, measure the drawdown, size positions
so one bad trade can't end you, and only then consider risking real money you can afford
to lose.

This project gives you that process. It now connects to real centralized exchanges
(Binance, Kraken, Coinbase, ... via ccxt) -- but **paper-first**: it reads live real
prices and fills every order in a local simulation by default. Real order placement
exists behind three deliberate gates and is off until you arm it yourself.

## What's here

| File | What it does |
|---|---|
| `config.py` | Fees, slippage, stop-loss, position sizing, drawdown kill-switch |
| `data.py` | Load OHLCV CSVs + generate synthetic sample data (testing only) |
| `strategy.py` | Strategy interface + SMA crossover + RSI mean-reversion baselines |
| `backtest.py` | Event-driven engine (no lookahead) + honest metrics |
| `run_backtest.py` | CLI: run it and print the report |
| `wallet.py` | Multi-asset wallet (ETH + USDC), immutable ledger, mark-to-market |
| `regime.py` | Market regime detection (trend / ranging / high-vol), no lookahead |
| `ensemble.py` | Strategy voting, regime-gated, slow performance weighting |
| `improve.py` | Self-improvement gate: walk-forward validation, adopt/reject with evidence |
| `advisor.py` | Sources ideas from stronger models (OpenAI/Anthropic/remote) -- PROPOSES only |
| `dashboard.py` | Self-contained HTML portfolio growth charts (equity, drawdown, wallet) |
| `run_full.py` | One command: wallet + ensemble + charts + improvement gate |
| `exchange.py` | Centralized-exchange connectivity (ccxt): live prices, gated order path |
| `run_live.py` | Trade on REAL exchange prices (paper fills default), gated live mode |

## Run it

```bash
cd /data/workspace/trading-bot
python3 run_backtest.py --gen 750 --capital 1 --strategy sma
python3 run_backtest.py --data data/sample_ohlcv.csv --strategy rsi --capital 1000
python3 run_full.py --usdc 1000 --eth 0.25 --bars 750   # full pipeline + charts
python3 -m unittest discover -s tests                     # 44 tests
```

Output lands in `reports/`: `dashboard.html` (portfolio growth charts),
`portfolio-growth.svg`, `improvement-log.jsonl` (every adopt/reject verdict).

## Connect it to a centralized exchange

```bash
# paper mode on REAL live prices -- no keys, nothing sent (default)
python3 run_live.py --exchange binance --pair ETH/USDC --once
python3 run_live.py --exchange kraken --pair ETH/USDC --interval 60   # loop

# read-only: what does the exchange say you hold? (keys required)
python3 run_live.py --exchange binance --balances --armed

# exercise the REAL order path's gates, send nothing (dry run)
python3 run_live.py --exchange binance --live-dry-run --amount 0.01 \
    --max-order-usd 50 --daily-loss-limit-usd 10
```

Real orders require **all three gates** before anything is sent:

1. `--armed` on the command line,
2. `GIDEON_TRADING_LIVE_ACK=I_UNDERSTAND_I_CAN_LOSE_MONEY` set in your environment,
3. `--max-order-usd` AND `--daily-loss-limit-usd` both set.

**API keys**: set `BINANCE_API_KEY` / `BINANCE_API_SECRET` (or `KRAKEN_...`,
`COINBASE_...`) in your environment. Use **trade-only** keys with withdrawal
permission OFF. Keys are read from the environment only -- never stored, never
logged. And this codebase has **no withdraw/transfer method at all**: it cannot
move funds out of an exchange even with a fully-permissioned key.

## Read the report honestly

- **total return vs buy & hold** -- if the strategy can't beat holding the asset on
  test data after fees, it has no business trading live.
- **max drawdown** -- the number that decides whether a human can actually sit through
  the strategy. 50%+ drawdowns are where people abandon the plan at the bottom.
- **Sharpe** -- return per unit of pain. Below ~1 on test data is unremarkable.
- **win rate alone means nothing** without profit factor -- a 90% win rate loses money
  if the 10% of losses are 20x the wins.

## Rules baked into the code

1. Fees (10 bps) and slippage (5 bps) are applied on every fill. No cost-free fantasy.
2. Stop-loss on every position (default 5%).
3. Position sizing risks a fixed fraction of equity (default 2%) -- not "all in".
4. A 50% drawdown halts trading entirely for the rest of the run (kill-switch).
5. Strategies never see future bars. `HOLD` is returned when there isn't enough history.

## Not included on purpose

- Un-gated live trading. Live orders need `--armed` + the ack env var + limits.
- Withdrawal / fund-transfer capability of any kind -- the code cannot move funds
  out of an exchange, and that is permanent by design.
- Leverage, martingale, or "compounding to millions" sizing -- these are account-killers.
- Any performance promise. Past or synthetic results never guarantee future returns.

This is research software, not financial advice. Only ever risk money you can lose.

## FAQ (the questions everyone asks)

**Do I have to give it a wallet or real money?**
No. It never touches a real wallet or private key -- and if any tool ever asks you
for a private key, that tool is a scam. The `Wallet` in `wallet.py` is a local
simulation: you tell it "1000 USDC and 0.25 ETH" and it tracks those numbers in a
ledger file. It can now READ exchange balances and live prices (trade-only API keys,
withdrawals OFF), but paper mode moves no real money and there is no code path that
can withdraw or transfer funds. Real funds stay wherever they are.

**What is the prediction? What gain/loss percentage will I get?**
There is no prediction. There are only TESTED statistics on historical (or seeded
synthetic) data: return %, max drawdown %, Sharpe, win rate, profit factor -- shown
gain AND loss per strategy. Anyone quoting you a future percentage with confidence
is guessing. This system's job is to tell you what happened when the strategy met
data it never saw, and to reject the ones that fall apart.

**How does it work?**
1. You deposit (simulated) ETH and/or USDC into the wallet.
2. The regime detector labels the market: trending / ranging / high volatility.
3. The ensemble of strategies votes; each strategy only votes in regimes its family
   suits; weights adapt slowly to recent closed-trade performance.
4. The risk manager sizes every position (2% risk per trade), stops are enforced,
   fees + slippage are charged on every fill, and a 50% drawdown halts everything.
5. Every closed trade is scored as gain/loss % and shown in the charts.
6. The improvement gate (walk-forward validation) tests each strategy on data it
   never tuned on and records adopt/reject verdicts with evidence.

**What does "source ideas from higher AI models" mean here?**
`advisor.py` can ask stronger models (OpenAI/Anthropic/remote endpoints via env
keys) for strategy ideas aimed at the system's CURRENT gaps. The advisor may only
PROPOSE; the walk-forward gate is the only thing allowed to ADOPT. Ideas that
can't survive data they never saw are rejected and logged -- smarter words are
not evidence.

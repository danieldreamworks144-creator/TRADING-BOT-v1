"""Monte Carlo analysis: shuffle the actual trade results to answer "how much
of this curve was skill vs. the order the trades happened to arrive in?"

Same trades, same edge, different sequences -- the spread of outcomes is the
honest risk of the strategy, not the single lucky backtest line."""
from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class MonteCarloResult:
    final_equities: list = field(default_factory=list)
    max_drawdowns: list = field(default_factory=list)

    def percentile(self, values, q: float) -> float:
        s = sorted(values)
        if not s:
            return 0.0
        idx = min(len(s) - 1, max(0, int(q * len(s))))
        return s[idx]

    @property
    def median_final(self) -> float:
        return self.percentile(self.final_equities, 0.5)

    @property
    def p5_final(self) -> float:
        return self.percentile(self.final_equities, 0.05)

    @property
    def p95_final(self) -> float:
        return self.percentile(self.final_equities, 0.95)

    @property
    def ruin_prob(self) -> float:
        """Share of sequences ending below starting capital."""
        if not self.final_equities:
            return 0.0
        return sum(1 for e in self.final_equities if e < 1.0) / len(self.final_equities)

    @property
    def median_max_dd(self) -> float:
        return self.percentile(self.max_drawdowns, 0.5)

    @property
    def p95_max_dd(self) -> float:
        return self.percentile(self.max_drawdowns, 0.95)


def monte_carlo(trade_returns, starting_capital: float = 1.0,
                runs: int = 2000, seed: int = 11) -> MonteCarloResult:
    """trade_returns: list of per-trade PnL fractions of equity at entry
    (Trade.eq_pct from the engine). Compounds them in random order."""
    if not trade_returns:
        return MonteCarloResult()
    rng = random.Random(seed)
    out = MonteCarloResult()
    n = len(trade_returns)
    for _ in range(runs):
        seq = [trade_returns[rng.randrange(n)] for _ in range(n)]
        eq = starting_capital
        peak, mdd = eq, 0.0
        for r in seq:
            eq *= (1 + r)
            eq = max(eq, 0.0)
            peak = max(peak, eq)
            if peak > 0:
                mdd = max(mdd, 1 - eq / peak)
        out.final_equities.append(eq)
        out.max_drawdowns.append(mdd)
    return out
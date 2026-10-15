"""Strategy ensemble: multiple strategies vote, weighted by recent
performance and gated by market regime. This is where "good strategic
decision making" actually lives.

Anti-fool rules baked in:
- No lookahead: weights at bar i only reflect trades closed by bar i.
- Performance weighting is SLOW (exponentially smoothed hit rate), so one
  lucky trade cannot make a strategy the boss.
- Regime gating: a strategy only votes in regimes its family is built for.
- Disagreement is allowed to produce a SMALL position, not a forced all-in.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from regime import REGIME_PROFILES, detect_regime

LONG, FLAT, HOLD = "LONG", "FLAT", "HOLD"
_SIG = {LONG: 1.0, FLAT: 0.0, HOLD: None}


@dataclass
class Vote:
    strategy: str
    family: str
    signal: str          # LONG / FLAT / HOLD
    confidence: float    # 0..1
    weight: float        # 0..1 after performance weighting


@dataclass
class EnsembleDecision:
    target_exposure: float   # 0..1 (fraction of equity to have at risk)
    regime: str
    votes: list = field(default_factory=list)
    note: str = ""


class Ensemble:
    """Combines strategy signals into a single target exposure."""

    def __init__(self, strategies: dict, families: dict | None = None,
                 perf_halflife: float = 20.0, min_weight: float = 0.25):
        """strategies: name -> Strategy instance (needs .on_bar(i, bars) and
        optional .family). families: name -> family key for regime gating."""
        self.strategies = strategies
        self.families = families or {}
        self.halflife = perf_halflife
        self.min_weight = min_weight
        # smoothed performance score per strategy, starts neutral
        self.score = {name: 0.0 for name in strategies}
        self.decisions: list = []

    # -- feedback ---------------------------------------------------------
    def observe(self, name: str, outcome: float) -> None:
        """Feed a closed-trade outcome (-1..1) into a strategy's score. Slow,
        so weights adapt without chasing noise."""
        alpha = 2.0 / (self.halflife + 1.0)
        self.score[name] = (1 - alpha) * self.score[name] + alpha * max(-1.0, min(1.0, outcome))

    def _weights(self) -> dict:
        raw = {n: max(self.min_weight, 1.0 + self.score[n]) for n in self.strategies}
        total = sum(raw.values()) or 1.0
        return {n: w / total for n, w in raw.items()}

    # -- decision ---------------------------------------------------------
    def decide(self, bars, i: int, regime: str | None = None) -> EnsembleDecision:
        regime = regime or detect_regime(bars, i)
        weights = self._weights()
        votes = []
        bull, bear, abstain, w_active = 0.0, 0.0, 0.0, 0.0

        for name, strat in self.strategies.items():
            family = self.families.get(name, getattr(strat, "family", "always"))
            allowed = REGIME_PROFILES.get(family, REGIME_PROFILES["always"])
            sig = strat.on_bar(i, bars)
            conf = getattr(strat, "confidence", lambda idx, brs: 0.6)(i, bars) if sig != HOLD else 0.0
            if sig == HOLD or regime not in allowed:
                votes.append(Vote(name, family, sig, 0.0, weights[name]))
                abstain += weights[name]
                continue
            val = _SIG[sig]
            bull += weights[name] * (val * conf)
            bear += weights[name] * ((1 - val) * conf)
            w_active += weights[name] * conf
            votes.append(Vote(name, family, sig, conf, weights[name]))

        if w_active <= 1e-9:
            return EnsembleDecision(0.0, regime, votes, "no strategy active in this regime")

        # net conviction in 0..1; scaled by how much of the ensemble is awake
        net = max(0.0, min(1.0, (bull - bear) / w_active))
        participation = 1.0 - abstain  # fraction of weight voting
        target = net * (0.5 + 0.5 * participation)
        note = f"regime={regime} active_weight={w_active:.2f} abstain={abstain:.2f}"
        decision = EnsembleDecision(target, regime, votes, note)
        self.decisions.append(decision)
        return decision
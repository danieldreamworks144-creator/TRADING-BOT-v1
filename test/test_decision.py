"""Tests for the decision-making layer: wallet accounting, regime detection,
ensemble gating, and the improvement gate wiring. Run: python3 -m pytest tests/
(stdlib unittest works too: python3 -m unittest discover -s tests)"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import Bar, generate_synthetic
from ensemble import Ensemble
from improve import MIN_OOS_SHARPE, validate
from regime import (REGIME_HIGH_VOL, REGIME_RANGING, REGIME_TREND_UP,
                    detect_regime, regime_series)
from strategy import STRATEGIES
from wallet import Wallet


class TestWallet(unittest.TestCase):
    def test_accepts_usdc_and_eth(self):
        w = Wallet()
        w.deposit("USDC", 1000.0)
        w.set_price("ETH", 2000.0)
        w.deposit("ETH", 0.5)
        self.assertAlmostEqual(w.equity(), 2000.0)

    def test_buy_and_sell_roundtrip_with_fees(self):
        w = Wallet()
        w.deposit("USDC", 100.0)
        bought = w.buy("ETH", 100.0, price=2000.0, fee_frac=0.001)
        self.assertGreater(bought, 0)
        self.assertAlmostEqual(w.balances["USDC"], 0.0)
        net = w.sell("ETH", bought, price=2000.0, fee_frac=0.001)
        self.assertLess(net, 100.0)  # fees cost money -- not a free lunch
        self.assertAlmostEqual(w.equity(), net)

    def test_cannot_sell_what_you_do_not_have(self):
        w = Wallet()
        w.deposit("USDC", 10.0)
        with self.assertRaises(ValueError):
            w.sell("ETH", 1.0, price=2000.0)

    def test_equity_refuses_unknown_prices(self):
        w = Wallet()
        w.deposit("USDC", 10.0)
        w.balances["ETH"] = 1.0  # unpriced ETH smuggled in
        with self.assertRaises(ValueError):
            w.equity()

    def test_ledger_and_persistence(self):
        w = Wallet()
        w.deposit("USDC", 50.0)
        w.set_price("ETH", 1000.0)
        w.buy("ETH", 50.0, price=1000.0)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "w.json")
            w.save(p)
            w2 = Wallet.load(p)
            self.assertAlmostEqual(w2.equity(), w.equity())
            self.assertEqual(len(w2.ledger), len(w.ledger))


class TestRegime(unittest.TestCase):
    def setUp(self):
        self.up = []
        px = 100.0
        for i in range(200):
            px *= 1.01  # steady uptrend
            self.up.append(Bar(f"2024-01-{i % 28 + 1:02d}", px, px * 1.005, px * 0.995, px, 1000))

    def test_detects_trend_up(self):
        self.assertEqual(detect_regime(self.up, 199), REGIME_TREND_UP)

    def test_ranging_on_noise(self):
        import random
        rng = random.Random(3)
        bars, px = [], 100.0
        for i in range(200):
            o = px
            px = max(1.0, px * (1 + rng.gauss(0, 0.002)))
            bars.append(Bar(f"2024-02-{i % 28 + 1:02d}", o, max(o, px), min(o, px), px, 1000))
        self.assertIn(detect_regime(bars, 199), (REGIME_RANGING, REGIME_HIGH_VOL))

    def test_no_lookahead_labels(self):
        labels = regime_series(self.up)
        self.assertEqual(len(labels), len(self.up))
        self.assertEqual(labels[5], REGIME_RANGING)  # early bars: not enough history


class TestEnsemble(unittest.TestCase):
    def test_all_flat_gives_zero_exposure(self):
        class AlwaysFlat:
            def on_bar(self, i, bars):
                return "FLAT"
        e = Ensemble({"flat": AlwaysFlat()}, {"flat": "always"})
        bars = generate_synthetic(120, seed=1)
        d = e.decide(bars, 119)
        self.assertAlmostEqual(d.target_exposure, 0.0)

    def test_confident_long_gives_exposure(self):
        class AlwaysLong:
            def on_bar(self, i, bars):
                return "LONG"
            def confidence(self, i, bars):
                return 1.0
        e = Ensemble({"long": AlwaysLong()}, {"long": "always"})
        bars = generate_synthetic(120, seed=1)
        d = e.decide(bars, 119)
        self.assertGreater(d.target_exposure, 0.0)

    def test_regime_gating_blocks_reversion_in_trend(self):
        class AlwaysLong:
            def on_bar(self, i, bars):
                return "LONG"
        e = Ensemble({"rev": AlwaysLong()}, {"rev": "reversion"})
        bars = generate_synthetic(300, seed=2)
        # force a trend_up regime label so reversion must abstain
        d = e.decide(bars, 299, regime=REGIME_TREND_UP)
        self.assertAlmostEqual(d.target_exposure, 0.0)

    def test_performance_feedback_moves_weights(self):
        class S:
            def on_bar(self, i, bars):
                return "HOLD"
        e = Ensemble({"a": S(), "b": S()}, {"a": "always", "b": "always"})
        for _ in range(50):
            e.observe("a", 1.0)
            e.observe("b", -1.0)
        w = e._weights()
        self.assertGreater(w["a"], w["b"])


class TestImproveGate(unittest.TestCase):
    def test_gate_is_strict_by_default(self):
        # thresholds exist and are demanding -- adoption is not the default
        self.assertGreaterEqual(MIN_OOS_SHARPE, 0.3)

    def test_validate_runs_and_records(self):
        bars = generate_synthetic(400, seed=5)
        r = validate("momentum", bars)
        self.assertIn(r.adopted, (True, False))
        self.assertEqual(bool(r.reason), True)
        self.assertGreaterEqual(r.oos_trades, 0)


class TestRegistry(unittest.TestCase):
    def test_every_strategy_constructs(self):
        for key, factory in STRATEGIES.items():
            s = factory()
            bars = generate_synthetic(60, seed=9)
            sig = s.on_bar(59, bars)
            self.assertIn(sig, ("LONG", "FLAT", "HOLD"))


if __name__ == "__main__":
    unittest.main()

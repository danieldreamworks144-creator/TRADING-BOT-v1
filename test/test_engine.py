"""Test suite -- stdlib unittest. Run: python3 -m unittest discover -s tests -v"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import compute_metrics, run_backtest
from config import BacktestConfig
from data import generate_synthetic
from montecarlo import monte_carlo
from risk import RiskManager
from strategy import STRATEGIES, STRATEGY_CLASSES, SmaCrossover
from walkforward import walk_forward


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.bars = generate_synthetic(300, seed=3)
        self.cfg = BacktestConfig(starting_capital=1000.0)

    def test_all_strategies_run(self):
        for key, factory in STRATEGIES.items():
            with self.subTest(strategy=key):
                r = run_backtest(self.bars, factory(), self.cfg)
                self.assertGreaterEqual(len(r.curve), len(self.bars))
                self.assertGreaterEqual(r.curve[-1], 0.0)

    def test_no_lookahead_hold(self):
        """A strategy asked for a bar it cannot see must return HOLD."""
        strat = SmaCrossover(20, 50)
        self.assertEqual(strat.on_bar(10, self.bars), "HOLD")

    def test_fees_are_charged(self):
        """With zero fees a round trip is symmetric; with fees it must lose money."""
        class AlwaysLong:
            name = "always-long"
            def on_bar(self, i, bars):
                return "LONG"

        cfg_free = BacktestConfig(starting_capital=1000.0, fee_bps=0.0, slippage_bps=0.0)
        cfg_cost = BacktestConfig(starting_capital=1000.0, fee_bps=50.0, slippage_bps=25.0)
        a = run_backtest(self.bars, AlwaysLong(), cfg_free)
        b = run_backtest(self.bars, AlwaysLong(), cfg_cost)
        self.assertGreater(a.curve[-1], b.curve[-1])

    def test_stop_loss_bounds_loss(self):
        class BuyThenTank:
            name = "tank"
            def on_bar(self, i, bars):
                return "LONG" if i == 0 else "HOLD"
        r = run_backtest(self.bars, BuyThenTank(), self.cfg)
        if r.trades:
            worst = min(t.pnl_pct for t in r.trades)
            self.assertGreaterEqual(worst, -(self.cfg.stop_loss_pct + 0.02),
                                    "stop-loss failed to bound the loss")

    def test_drawdown_halt_fires(self):
        cfg = BacktestConfig(starting_capital=1000.0, max_drawdown_halt=0.01,
                             stop_loss_pct=0.9, risk_per_trade=1.0)
        class BuyHoldAlways:
            name = "bh"
            def on_bar(self, i, bars):
                return "LONG"
        r = run_backtest(self.bars, BuyHoldAlways(), cfg)
        self.assertTrue(r.halted, "kill-switch should fire on a 1% drawdown limit")

    def test_metrics_sane(self):
        r = run_backtest(self.bars, STRATEGIES["sma"](), self.cfg)
        m = compute_metrics(r.curve, r.trades, r.bars_in_market)
        self.assertGreaterEqual(m["win_rate"], 0.0)
        self.assertLessEqual(m["win_rate"], 1.0)
        self.assertGreaterEqual(m["max_drawdown"], 0.0)
        self.assertLessEqual(m["exposure"], 1.0)


class TestRisk(unittest.TestCase):
    def test_sizing_risks_bounded_fraction(self):
        cfg = BacktestConfig(risk_per_trade=0.02, stop_loss_pct=0.05)
        rm = RiskManager(cfg)
        qty = rm.size(1000.0, 50.0)
        stop = rm.initial_stop(50.0)
        loss_if_stopped = (50.0 - stop) * qty
        self.assertLessEqual(loss_if_stopped, 1000.0 * 0.02 + 1e-6)

    def test_vol_target_caps_size(self):
        cfg = BacktestConfig(vol_target=0.10)
        rm = RiskManager(cfg)
        calm = rm.size(1000.0, 50.0, atr_value=0.5)
        wild = rm.size(1000.0, 50.0, atr_value=5.0)
        self.assertGreater(calm, wild)

    def test_atr_stop_wider_than_fixed(self):
        cfg = BacktestConfig(stop_mode="atr", atr_stop_mult=2.0, stop_loss_pct=0.05)
        rm = RiskManager(cfg)
        self.assertLess(rm.initial_stop(100.0, atr_value=4.0), 95.0)


class TestValidation(unittest.TestCase):
    def test_walk_forward_runs(self):
        bars = generate_synthetic(400, seed=5)
        wf = walk_forward(bars, "momentum", BacktestConfig(), n_windows=3)
        self.assertEqual(len(wf.windows), 3)
        self.assertTrue(wf.stitched_equity)

    def test_monte_carlo_distribution(self):
        returns = [0.01, -0.005, 0.02, -0.01, 0.005] * 20
        mc = monte_carlo(returns, runs=500, seed=1)
        self.assertEqual(len(mc.final_equities), 500)
        self.assertLessEqual(mc.p5_final, mc.median_final)
        self.assertLessEqual(mc.median_final, mc.p95_final)

    def test_monte_carlo_empty(self):
        mc = monte_carlo([])
        self.assertEqual(mc.median_final, 0.0)
        self.assertEqual(mc.ruin_prob, 0.0)


if __name__ == "__main__":
    unittest.main()

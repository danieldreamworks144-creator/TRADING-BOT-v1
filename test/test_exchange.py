"""Tests for the exchange connectivity layer -- especially the safety gates.
The single most important assertion in this repo: a live order cannot leave
this machine without deliberate arming."""
import os
import unittest
from unittest import mock

from data import Bar
from exchange import (CcxtExchange, ExchangeError, LiveNotArmed, OrderResult,
                      PaperExchange, Ticker, _require_live)


class FakeCcxt:
    """Minimal stand-in for a ccxt exchange so no network is touched."""
    exchanges = ["binance", "kraken"]

    def __init__(self, cfg=None):
        self.cfg = cfg or {}
        self.orders = []

    def fetch_ticker(self, pair):
        return {"last": 2000.0, "bid": 1999.5, "ask": 2000.5, "timestamp": 0}

    def fetch_ohlcv(self, pair, timeframe="1h", limit=500):
        return [[0, 1990.0, 2010.0, 1980.0, 2000.0, 12.5]]

    def fetch_balance(self):
        return {"total": {"USDC": {"free": 500.0}, "ETH": {"free": 0.25},
                          "XYZ": {"free": 0.0}}}

    def create_order(self, pair, order_type, side, amount, price):
        self.orders.append((pair, order_type, side, amount, price))
        return {"id": "12345", "price": price, "filled": amount}


def _patch_ccxt(fake):
    import exchange
    return mock.patch.object(exchange, "_make_ccxt",
                             lambda *a, **k: fake)


class TestGates(unittest.TestCase):
    def test_unarmed_refuses(self):
        with self.assertRaises(LiveNotArmed):
            _require_live(False, None)

    def test_armed_without_ack_refuses(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(LiveNotArmed):
                _require_live(True, None)

    def test_armed_with_wrong_ack_refuses(self):
        with self.assertRaises(LiveNotArmed):
            _require_live(True, "sure-why-not")

    def test_armed_with_correct_ack_passes(self):
        from exchange import LIVE_ACK_PHRASE
        _require_live(True, LIVE_ACK_PHRASE)  # must not raise

    def test_env_ack_passes(self):
        from exchange import LIVE_ACK_PHRASE
        with mock.patch.dict(os.environ, {"GIDEON_TRADING_LIVE_ACK": LIVE_ACK_PHRASE}):
            _require_live(True, None)


class TestPaperExchange(unittest.TestCase):
    def test_paper_order_is_always_simulated(self):
        fake = FakeCcxt()
        with _patch_ccxt(fake):
            px = PaperExchange("binance")
            res = px.place_order("ETH/USDC", "buy", 0.1, price=2000.0)
        self.assertIsInstance(res, OrderResult)
        self.assertTrue(res.simulated)
        self.assertEqual(fake.orders, [])  # nothing reached the exchange

    def test_paper_slippage_moves_against_you(self):
        with _patch_ccxt(FakeCcxt()):
            px = PaperExchange("binance", slippage_bps=10)
            buy = px.place_order("ETH/USDC", "buy", 1.0, price=100.0)
            sell = px.place_order("ETH/USDC", "sell", 1.0, price=100.0)
        self.assertGreater(buy.price, 100.0)
        self.assertLess(sell.price, 100.0)

    def test_real_ticker_parses(self):
        with _patch_ccxt(FakeCcxt()):
            t = PaperExchange("binance").fetch_ticker("ETH/USDC")
        self.assertIsInstance(t, Ticker)
        self.assertEqual(t.last, 2000.0)

    def test_real_ohlcv_becomes_bars(self):
        with _patch_ccxt(FakeCcxt()):
            bars = PaperExchange("binance").fetch_ohlcv("ETH/USDC")
        self.assertEqual(len(bars), 1)
        self.assertIsInstance(bars[0], Bar)
        self.assertEqual(bars[0].close, 2000.0)


class TestLiveOrderGates(unittest.TestCase):
    def _armed_exchange(self, **kw):
        from exchange import LIVE_ACK_PHRASE
        old = os.environ.get("GIDEON_TRADING_LIVE_ACK")
        os.environ["GIDEON_TRADING_LIVE_ACK"] = LIVE_ACK_PHRASE

        def _restore():
            if old is None:
                os.environ.pop("GIDEON_TRADING_LIVE_ACK", None)
            else:
                os.environ["GIDEON_TRADING_LIVE_ACK"] = old

        self.addCleanup(_restore)
        fake = FakeCcxt()
        with _patch_ccxt(fake):
            ex = CcxtExchange("binance", armed=True, **kw)
        ex._ex = fake
        return ex, fake

    def test_unarmed_adapter_refuses_orders(self):
        with _patch_ccxt(FakeCcxt()):
            ex = CcxtExchange("binance", armed=False)
            with self.assertRaises(LiveNotArmed):
                ex.place_order("ETH/USDC", "buy", 0.1)

    def test_bad_side_refused(self):
        ex, _ = self._armed_exchange()
        with self.assertRaises(ExchangeError):
            ex.place_order("ETH/USDC", "short", 0.1)

    def test_nonpositive_amount_refused(self):
        ex, _ = self._armed_exchange()
        for bad in (0.0, -1.0, float("nan")):
            with self.assertRaises(ExchangeError):
                ex.place_order("ETH/USDC", "buy", bad)

    def test_max_order_usd_enforced(self):
        ex, fake = self._armed_exchange(max_order_usd=100.0)
        with self.assertRaises(ExchangeError):
            ex.place_order("ETH/USDC", "buy", 1.0, price=2000.0)  # 2000 USD
        self.assertEqual(fake.orders, [])

    def test_daily_loss_limit_blocks(self):
        ex, fake = self._armed_exchange(daily_loss_limit_usd=50.0)
        ex.record_realized_pnl(-60.0)
        with self.assertRaises(LiveNotArmed):
            ex.place_order("ETH/USDC", "buy", 0.001, price=100.0)
        self.assertEqual(fake.orders, [])

    def test_dry_run_sends_nothing(self):
        ex, fake = self._armed_exchange(max_order_usd=1e9)
        res = ex.place_order("ETH/USDC", "buy", 0.001, price=100.0, dry_run=True)
        self.assertEqual(fake.orders, [])
        self.assertTrue(res.simulated)
        self.assertIn("dry_run", res.note)

    def test_live_order_reaches_exchange_when_all_gates_pass(self):
        ex, fake = self._armed_exchange(max_order_usd=1e9)
        res = ex.place_order("ETH/USDC", "buy", 0.001, price=100.0)
        self.assertFalse(res.simulated)
        self.assertEqual(len(fake.orders), 1)

    def test_no_withdraw_capability_exists(self):
        self.assertFalse(hasattr(CcxtExchange, "withdraw"))
        self.assertFalse(hasattr(PaperExchange, "withdraw"))
        self.assertFalse(hasattr(CcxtExchange, "transfer"))


class TestMakeCcxt(unittest.TestCase):
    """Regression: _make_ccxt must assign passphrase to ex.password."""

    def test_password_assigned_when_passphrase_present(self):
        """Regression: _make_ccxt must assign passphrase to ex.password."""
        import sys
        fake_ccxt = mock.MagicMock()
        fake_ccxt.exchanges = ["binance"]
        mock_exchange_instance = mock.MagicMock()
        mock_exchange_instance.password = None
        fake_ccxt.binance.return_value = mock_exchange_instance
        with mock.patch.dict(sys.modules, {"ccxt": fake_ccxt}):
            with mock.patch.dict(os.environ, {
                "BINANCE_API_KEY": "K", "BINANCE_API_SECRET": "S",
                "BINANCE_API_PASSPHRASE": "P"
            }):
                from exchange import _make_ccxt
                _make_ccxt("binance", read_only=False)
        self.assertEqual(mock_exchange_instance.password, "P")

    def test_backtest_module_imports(self):
        """Regression: backtest.py must be importable (was backrest.py)."""
        import backtest
        self.assertTrue(hasattr(backtest, "run_backtest"))
        self.assertTrue(hasattr(backtest, "compute_metrics"))


if __name__ == "__main__":
    unittest.main()

"""Platform tests: the spec's checklist made executable.

Covered: API connection failure, invalid/stale market data, duplicate orders,
database/credential errors, risk-limit enforcement + auto-pause, restart
rebuild, secret redaction and access controls (no secrets in DB/logs/API,
demo-only enforcement, no withdrawal surface)."""
import os
import tempfile
import unittest
from unittest import mock

import market
import run_platform
from limits import RiskLimits
from sim import Engine
from store import Store, redact, REDACTED


def candles_up(n=30, base=100.0, ts_end=None):
    """Fresh rising candles that trigger a buy signal."""
    import time
    end = ts_end or int(time.time() * 1000)
    out = []
    for i in range(n):
        px = base + i * 1.0
        out.append({"ts": end - (n - i) * 60_000, "open": px, "high": px + 1,
                    "low": px - 1, "close": px, "volume": 10})
    return out


class _FakePlatform:
    """Minimal stand-in with the attributes _manual_fill touches."""
    def __init__(self, store, engine, limits):
        self.store = store
        self.engine = engine
        self.limits = limits
        self.exchange = "okx"
        self.pair = "ETH/USDC"
        import threading
        self._lock = threading.Lock()
        self.last_candles = candles_up()


class PlatformTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "t.db"))
        self.limits = RiskLimits(max_position_usd=500, max_daily_loss_usd=100,
                                max_open_positions=5, max_total_exposure_usd=1500)
        self.engine = Engine(store=self.store, limits=self.limits)

    def tearDown(self):
        self.tmp.cleanup()


class TestConnectionFailure(PlatformTestBase):
    def test_connection_error_is_reported_not_raised(self):
        with mock.patch.object(market, "fetch_ticker", side_effect=ConnectionError("boom")):
            st = market.connection_status("okx", "ETH/USDC")
        self.assertEqual(st.status, "error")
        self.assertIn("boom", st.detail)

    def test_engine_skips_on_no_market_data(self):
        r = self.engine.evaluate([])
        self.assertEqual(r["action"], "skip")
        self.assertIn("no market data", r["reason"])


class TestInvalidMarketData(PlatformTestBase):
    def test_stale_data_blocks_trading(self):
        stale = candles_up(ts_end=1)  # 1970 timestamps = ancient
        r = self.engine.evaluate(stale)
        self.assertEqual(r["action"], "skip")
        self.assertIn("old", r["reason"])
        self.assertEqual(self.store.orders(), [])  # nothing traded

    def test_malformed_candle_pauses_instead_of_crashing(self):
        bad = candles_up() + [{"ts": "not-a-number", "close": None}]
        r = self.engine.evaluate(bad)
        self.assertEqual(r["action"], "error")
        self.assertTrue(self.engine.state.paused)
        self.assertEqual(len(self.store.errors()), 1)


class TestDuplicateOrders(PlatformTestBase):
    def test_same_signal_same_minute_fills_once(self):
        cs = candles_up()
        r1 = self.engine.evaluate(cs)
        self.assertEqual(r1["action"], "buy")
        r2 = self.engine.evaluate(cs)  # identical signal, position already open
        self.assertNotEqual(r2["action"], "buy")          # never double-buys
        self.assertEqual(len(self.store.orders()), 1)      # exactly one fill recorded

    def test_duplicate_guard_blocks_identical_rebuy_same_minute(self):
        cs = candles_up()
        self.engine.evaluate(cs)                          # buy -> position open
        # flatten it manually (same minute, same price)
        run_platform._manual_fill(self.store, self.engine, self.limits,
                                  _FakePlatform(self.store, self.engine, self.limits),
                                  "sell", cs[-1]["close"])
        before = len(self.store.orders())
        r = self.engine.evaluate(cs)                      # same price/minute again
        self.assertEqual(r["action"], "duplicate_blocked")
        self.assertEqual(len(self.store.orders()), before)  # no new fill

    def test_dup_key_is_unique_in_db(self):
        ok = self.store.insert_order(exchange="okx", pair="ETH/USDC", side="buy",
                                     amount=1, price=100, fee=0, status="filled",
                                     reason="t", dup_key="same")
        dup = self.store.insert_order(exchange="okx", pair="ETH/USDC", side="buy",
                                      amount=1, price=100, fee=0, status="filled",
                                      reason="t", dup_key="same")
        self.assertIsNotNone(ok)
        self.assertIsNone(dup)


class TestDatabaseErrors(PlatformTestBase):
    def test_store_rejects_credential_config_keys(self):
        for bad in ("api_key", "API_SECRET", "passphrase", "password", "token"):
            with self.assertRaises(ValueError):
                self.store.set_config(bad, "x")

    def test_store_rejects_non_demo_orders(self):
        with self.assertRaises(ValueError):
            self.store.insert_order(exchange="okx", pair="ETH/USDC", side="buy",
                                    amount=1, price=1, fee=0, status="filled",
                                    reason="t", mode="live")

    def test_engine_pauses_on_store_failure(self):
        with mock.patch.object(self.store, "insert_order", side_effect=OSError("disk full")):
            r = self.engine.evaluate(candles_up())
        self.assertEqual(r["action"], "error")
        self.assertTrue(self.engine.state.paused)
        self.assertIn("disk full", r["reason"])

    def test_redact_strips_secrets_everywhere(self):
        out = redact({"api_key": "AKIA123", "nested": {"secret": "s3cr3t"},
                      "passphrase": "p", "safe": "value", "list": [{"token": "t"}]})
        self.assertEqual(out["api_key"], REDACTED)
        self.assertEqual(out["nested"]["secret"], REDACTED)
        self.assertEqual(out["passphrase"], REDACTED)
        self.assertEqual(out["list"][0]["token"], REDACTED)
        self.assertEqual(out["safe"], "value")


class TestRiskLimits(PlatformTestBase):
    def test_position_size_limit(self):
        allowed, why = self.limits.allow(position_usd=600, open_positions=0, total_exposure_usd=0)
        self.assertFalse(allowed)
        self.assertIn("max_position_usd", why)

    def test_max_open_positions(self):
        allowed, why = self.limits.allow(position_usd=10, open_positions=5, total_exposure_usd=50)
        self.assertFalse(allowed)
        self.assertIn("max_open_positions", why)

    def test_max_exposure(self):
        allowed, why = self.limits.allow(position_usd=400, open_positions=2, total_exposure_usd=1300)
        self.assertFalse(allowed)
        self.assertIn("max_total_exposure_usd", why)

    def test_daily_loss_trips_and_pauses(self):
        self.limits.record_pnl(-120.0)
        self.assertTrue(self.limits.paused)
        allowed, why = self.limits.allow(position_usd=10, open_positions=0, total_exposure_usd=0)
        self.assertFalse(allowed)
        self.assertIn("paused", why)

    def test_engine_caps_position_size_to_limit(self):
        self.limits.max_position_usd = 1.0  # engine sizes to min(cash*0.2, limit)
        r = self.engine.evaluate(candles_up())
        self.assertEqual(r["action"], "buy")
        # the fill notional never exceeds the configured limit
        order = self.store.orders()[0]
        self.assertLessEqual(order["amount"] * order["price"] + order["fee"],
                             1.0 + 1e-6)

    def test_engine_blocks_when_daily_loss_trips(self):
        self.limits.record_pnl(-120.0)  # trips + pauses
        r = self.engine.evaluate(candles_up())
        self.assertIn(r["action"], ("blocked", "paused"))
        self.assertEqual(self.store.orders(), [])  # nothing traded while paused

    def test_reset_resumes(self):
        self.limits.record_pnl(-120.0)
        self.limits.resume()
        self.assertFalse(self.limits.paused)


class TestRestart(PlatformTestBase):
    def test_state_rebuilt_from_db_after_restart(self):
        self.engine.evaluate(candles_up())  # one simulated buy
        orders_before = len(self.store.orders())
        # "restart": fresh engine over the same DB
        e2 = Engine(store=self.store, limits=self.limits)
        info = e2.restart_safe()
        self.assertEqual(len(self.store.orders()), orders_before)  # nothing lost/duplicated
        self.assertEqual(info["open_positions"], 1)
        self.assertAlmostEqual(info["cash"], e2.state.cash)
        # cash accounting matches the stored orders
        buys = sum(o["amount"] * o["price"] + o["fee"] for o in self.store.orders() if o["side"] == "buy")
        self.assertAlmostEqual(e2.state.cash, self.engine.state.start_cash - buys)

    def test_demo_state_persists_equity_marks(self):
        self.engine.evaluate(candles_up())
        self.engine.snapshot(110.0)
        marks = self.store.equity_marks()
        self.assertGreaterEqual(len(marks), 1)
        self.assertIn("equity", marks[-1])


class TestAuthAndAccess(PlatformTestBase):
    def test_validate_credentials_never_returns_secret(self):
        with mock.patch.dict(os.environ, {"OKX_API_KEY": "MYKEY12345",
                                          "OKX_API_SECRET": "SUPERSECRETVALUE"}):
            v = market.validate_credentials("okx")
        blob = str(v)
        self.assertNotIn("SUPERSECRETVALUE", blob)
        self.assertNotIn("MYKEY12345", blob)
        self.assertTrue(v["has_credentials"])
        self.assertIn("****", v["credential_hint"])

    def test_events_never_contain_secrets(self):
        self.store.event("system", "hello", {"api_secret": "LEAKME", "note": "ok"})
        ev = self.store.events(1)[0]
        self.assertNotIn("LEAKME", str(ev))
        self.assertIn(REDACTED, ev["meta"])

    def test_no_withdrawal_surface_anywhere(self):
        for mod in (market,):
            for name in dir(mod):
                self.assertNotIn("withdraw", name.lower())
                self.assertNotIn("transfer", name.lower())
        self.assertFalse(hasattr(Engine, "withdraw"))

    def test_action_handler_rejects_unknown_and_keeps_demo(self):
        class FakePlatform:
            def __init__(self, store):
                self.store = store
                self.limits = RiskLimits()
                self.engine = Engine(store=store, limits=self.limits)
                self.exchange = "okx"
                self.pair = "ETH/USDC"
                self._lock = __import__("threading").Lock()
                self.last_candles = candles_up()
        p = FakePlatform(self.store)
        r = run_platform.handle_action(p, {"name": "launch_missiles"})
        self.assertFalse(r["ok"])
        r2 = run_platform.handle_action(p, {"name": "manual", "side": "buy"})
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["result"]["mode"], "demo")
        self.assertEqual(self.store.orders()[0]["mode"], "demo")

    def test_server_refuses_non_local_bind(self):
        rc = run_platform.main(["--host", "0.0.0.0", "--port", "0"])
        self.assertEqual(rc, 2)


class TestSecretKeyRegex(PlatformTestBase):
    """Regression: _SECRET_KEY regex must match credential-like config keys."""

    def test_regex_matches_api_key_variants(self):
        from store import _SECRET_KEY
        for key in ("api_key", "apikey", "API_KEY", "secret", "API_SECRET",
                    "passphrase", "password", "token", "authorization", "cookie"):
            self.assertTrue(_SECRET_KEY.search(key),
                            f"_SECRET_KEY should match {key!r}")

    def test_regex_does_not_match_safe_keys(self):
        from store import _SECRET_KEY
        for key in ("risk_limits", "theme", "pair", "exchange", "interval"):
            self.assertIsNone(_SECRET_KEY.search(key),
                              f"_SECRET_KEY should NOT match {key!r}")


if __name__ == "__main__":
    unittest.main()

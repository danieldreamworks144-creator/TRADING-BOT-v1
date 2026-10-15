"""The trading platform server: API + dashboard on 127.0.0.1, demo-only.

  python3 run_platform.py --port 8787
  # then open http://127.0.0.1:8787/

Design:
- Binds 127.0.0.1 ONLY. Not exposed to the network.
- Demo-only: the server offers no live-order endpoint. /api/action only drives
  the simulator.
- A background loop fetches real market data (OKX/Bybit public), runs the demo
  engine step, marks equity, and writes activity events. The UI polls /api/state.
- Credentials (if any) live in env vars server-side and are never returned.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import market
from limits import RiskLimits
from platform_ui import render
from sim import Engine
from store import Store

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


class Platform:
    """Shared state between the HTTP handler and the background loop."""

    def __init__(self, store: Store, pair: str = "ETH/USDC", exchange: str = "okx",
                 interval_s: float = 5.0):
        self.store = store
        self.pair = pair
        self.exchange = exchange
        self.interval_s = interval_s
        self.limits = RiskLimits()
        self.engine = Engine(store=store, limits=self.limits, pair=pair, exchange=exchange)
        self.running = False
        self.last_candles: list[dict] = []
        self.last_action: dict = {}
        self.conn_cache: dict = {}
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._restore_config()

    def _restore_config(self):
        risk = self.store.get_config("risk_limits")
        if risk:
            for k in ("max_position_usd", "max_daily_loss_usd", "max_open_positions",
                      "max_total_exposure_usd"):
                if k in risk:
                    setattr(self.limits, k, risk[k])
        restored = self.engine.restart_safe()
        self.store.event("system", "platform started (DEMO mode)", restored)

    # -- market + engine -------------------------------------------------
    def refresh_market(self) -> list[dict]:
        try:
            candles = market.fetch_ohlcv(self.exchange, self.pair, "1m", 120)
            with self._lock:
                self.last_candles = candles
            return candles
        except Exception as e:
            self.store.error("market.fetch_ohlcv", f"{self.exchange}: {type(e).__name__}: {str(e)[:120]}")
            with self._lock:
                return self.last_candles

    def refresh_connections(self):
        try:
            self.conn_cache = {name: market.connection_status(name, self.pair)
                               for name in market.SUPPORTED}
        except Exception as e:
            self.store.error("market.connection_status", str(e)[:120])

    def step(self) -> dict:
        candles = self.refresh_market()
        with self._lock:
            action = self.engine.evaluate(candles) if candles else {
                "action": "skip", "reason": "no market data", "mode": "demo"}
            self.last_action = action
        return action

    def snapshot(self) -> dict:
        with self._lock:
            candles = self.last_candles
        price = candles[-1]["close"] if candles else None
        acct = self.engine.snapshot(price)
        open_pos = self.store.positions("open")
        for p in open_pos:
            p["mark_price"] = price or p["entry_price"]
            p["unrealized_pnl"] = p["amount"] * ((price or p["entry_price"]) - p["entry_price"])
        orders = self.store.orders(60)
        for o in orders:
            if o["side"] == "sell":
                o["pnl"] = None  # per-order P&L is computed on close; see events
        return {
            "ts": __import__("store").now_ts(),
            "mode": "demo",
            "pair": self.pair,
            "account": acct,
            "positions": open_pos,
            "trades": orders,
            "events": self.store.events(40),
            "errors": self.store.errors(15),
            "risk": self.limits.as_dict(),
            "bot": {"running": self.running, "last_action": self.last_action,
                    "interval_s": self.interval_s, "exchange": self.exchange},
            "connections": [vars(c) for c in self.conn_cache.values()],
            "candles": [{"close": c["close"], "ts": c["ts"]} for c in candles[-120:]],
        }

    # -- loop -------------------------------------------------------------
    def loop(self):
        self.refresh_connections()
        conn_tick = 0
        while not self._stop.is_set():
            try:
                if self.running:
                    self.step()
                else:
                    self.refresh_market()
                conn_tick += 1
                if conn_tick % 12 == 0:   # ~once a minute
                    self.refresh_connections()
            except Exception as e:
                self.store.error("platform.loop", f"{type(e).__name__}: {str(e)[:120]}")
            self._stop.wait(self.interval_s)

    def start_loop(self):
        t = threading.Thread(target=self.loop, daemon=True)
        t.start()

    def stop_loop(self):
        self._stop.set()


def make_handler(platform: Platform):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the default access log out of stderr noise
            pass

        def _send(self, obj, status=200, content="application/json"):
            body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", content)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/api/state"):
                try:
                    self._send(platform.snapshot())
                except Exception as e:
                    platform.store.error("api.state", str(e)[:120])
                    self._send({"error": str(e)[:200]}, 500)
            elif self.path in ("/", "/index.html"):
                self._send(render(platform.snapshot()).encode(), content="text/html; charset=utf-8")
            else:
                self._send({"error": "not found"}, 404)

        def do_POST(self):
            if not self.path.startswith("/api/action"):
                self._send({"error": "not found"}, 404)
                return
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n) or b"{}")
                self._send(handle_action(platform, payload))
            except Exception as e:
                platform.store.error("api.action", str(e)[:120])
                self._send({"error": str(e)[:200]}, 500)

    return Handler


def handle_action(platform: Platform, payload: dict) -> dict:
    name = payload.get("name")
    st, eng, lim = platform.store, platform.engine, platform.limits
    if name == "start":
        platform.running = True
        st.event("system", "bot started (DEMO)")
        return {"ok": True, "running": True}
    if name == "stop":
        platform.running = False
        st.event("system", "bot stopped")
        return {"ok": True, "running": False}
    if name == "step":
        return {"ok": True, "result": platform.step()}
    if name == "manual":
        side = payload.get("side", "buy")
        with platform._lock:
            candles = platform.last_candles
        price = candles[-1]["close"] if candles else None
        if price is None:
            return {"ok": False, "error": "no market data yet"}
        return {"ok": True, "result": _manual_fill(st, eng, lim, platform, side, price)}
    if name == "reset_day":
        lim.resume()
        st.event("risk", "daily loss counter reset / resumed")
        return {"ok": True}
    if name == "risk":
        for k in ("max_position_usd", "max_daily_loss_usd", "max_open_positions",
                  "max_total_exposure_usd"):
            if k in payload and payload[k] is not None:
                setattr(lim, k, payload[k])
        lim.resume()
        st.set_config("risk_limits", lim.as_dict())
        st.event("system", f"risk limits updated: {lim.as_dict()}")
        return {"ok": True}
    if name == "restart":
        with platform._lock:
            result = eng.restart_safe()
        return {"ok": True, "result": result}
    return {"ok": False, "error": f"unknown action: {name}"}


def _manual_fill(st: Store, eng: Engine, lim: RiskLimits, platform: Platform,
                 side: str, price: float) -> dict:
    """Manual SIMULATED fill from the UI buttons -- same rules as the engine."""
    open_pos = st.positions("open")
    exposure = sum(p["amount"] * p["entry_price"] for p in open_pos)
    if side == "buy":
        usd = min(eng.state.cash * 0.10, lim.max_position_usd)
        allowed, why = lim.allow(position_usd=usd, open_positions=len(open_pos),
                                 total_exposure_usd=exposure)
        if not allowed:
            st.event("risk", f"manual buy blocked: {why}")
            return {"action": "blocked", "reason": why, "mode": "demo"}
        fee = usd * 0.001
        amount = (usd - fee) / price
        oid = st.insert_order(exchange=platform.exchange, pair=platform.pair, side="buy",
                              amount=amount, price=price, fee=fee, status="filled",
                              reason="manual demo buy from UI")
        eng.state.cash -= usd
        st.open_position(platform.pair, amount, price, platform.exchange)
        st.event("trade", f"DEMO BUY {amount:.6f} @ {price:.2f} (manual)", {"order_id": oid})
        return {"action": "buy", "amount": amount, "mode": "demo"}
    # sell: flatten one matching open position
    match = next((p for p in open_pos if p["pair"] == platform.pair), None)
    if not match:
        return {"action": "blocked", "reason": "no open position to sell", "mode": "demo"}
    proceeds = match["amount"] * price
    fee = proceeds * 0.001
    pnl = proceeds - fee - match["amount"] * match["entry_price"]
    oid = st.insert_order(exchange=platform.exchange, pair=platform.pair, side="sell",
                          amount=match["amount"], price=price, fee=fee, status="filled",
                          reason="manual demo sell from UI")
    eng.state.cash += proceeds - fee
    st.close_position(match["id"], price, pnl)
    lim.record_pnl(pnl)
    st.event("trade", f"DEMO SELL {match['amount']:.6f} @ {price:.2f} P&L {pnl:+.2f} (manual)",
             {"order_id": oid, "pnl": pnl})
    return {"action": "sell", "pnl": pnl, "mode": "demo"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Demo trading platform (simulated only).")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1", help="bind address (127.0.0.1 only)")
    ap.add_argument("--pair", default="ETH/USDC")
    ap.add_argument("--exchange", default="okx", choices=list(market.SUPPORTED))
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--db", default=os.path.join(DATA_DIR, "platform.db"))
    args = ap.parse_args(argv)

    if args.host not in ("127.0.0.1", "localhost"):
        print("REFUSED: this demo server binds 127.0.0.1 only.")
        return 2

    store = Store(args.db)
    platform = Platform(store, pair=args.pair, exchange=args.exchange,
                        interval_s=args.interval)
    platform.refresh_market()
    platform.start_loop()

    srv = ThreadingHTTPServer((args.host, args.port), make_handler(platform))
    print(f"ApexTrade DEMO platform on http://{args.host}:{args.port}/  (ctrl-c to stop)")
    print("  mode: DEMO -- all trades simulated, no real orders, no withdrawals")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        platform.stop_loop()
        store.event("system", "platform shut down")
        print("\nbye -- state saved in", args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

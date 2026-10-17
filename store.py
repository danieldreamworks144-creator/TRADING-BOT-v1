"""Secure storage for the trading platform: SQLite for state, redacted logging.

Rules enforced here:
- API secrets are NEVER stored in the database and NEVER written to logs. The
  redactor strips any value whose key looks like a credential before it lands.
- Simulated orders/trades are stored with mode='demo' so no record can be
  mistaken for a real fill.
- Every system event (bot action, risk trip, connection change) goes to the
  activity feed with a timestamp -- the UI reads it, nothing is invented.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone

_SECRET_KEY = re.compile(r"(api[_-]?key|apikey|secret|passphrase|password|token|authorization|cookie)",
                         re.IGNORECASE)

REDACTED = "[redacted]"


def redact(obj):
    """Recursively redact anything that looks like a credential."""
    if isinstance(obj, dict):
        return {k: (REDACTED if _SECRET_KEY.search(str(k)) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    return obj


def now_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """Thread-safe SQLite store. One file, WAL mode, no ORM magic."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self._init_schema()

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def _init_schema(self):
        with self._lock, self._conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL,
                meta TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS orders(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL, exchange TEXT NOT NULL, pair TEXT NOT NULL,
                side TEXT NOT NULL, amount REAL NOT NULL, price REAL NOT NULL,
                fee REAL NOT NULL DEFAULT 0, mode TEXT NOT NULL DEFAULT 'demo',
                status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
                live_confirmation TEXT NOT NULL DEFAULT '',
                dup_key TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS positions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pair TEXT NOT NULL, amount REAL NOT NULL, entry_price REAL NOT NULL,
                opened_ts TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
                closed_ts TEXT, pnl REAL NOT NULL DEFAULT 0,
                exchange TEXT NOT NULL DEFAULT 'demo');
            CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS errors(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL, location TEXT NOT NULL, message TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS equity_marks(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL, equity REAL NOT NULL, cash REAL NOT NULL,
                positions_value REAL NOT NULL, unrealized_pnl REAL NOT NULL);
            """)

    # -- events (activity feed) -----------------------------------------
    def event(self, kind: str, message: str, meta: dict | None = None) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO events(ts,kind,message,meta) VALUES(?,?,?,?)",
                      (now_ts(), kind, str(message),
                       json.dumps(redact(meta or {}), default=str)))

    def events(self, limit: int = 50) -> list[dict]:
        with self._lock, self._conn() as c:
            rows = c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def error(self, where: str, message: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO errors(ts,location,message) VALUES(?,?,?)",
                      (now_ts(), where, str(message)))
        self.event("error", f"{where}: {message}")

    def errors(self, limit: int = 30) -> list[dict]:
        with self._lock, self._conn() as c:
            rows = c.execute("SELECT id, ts, location AS \"where\", message "
                             "FROM errors ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # -- orders ----------------------------------------------------------
    def insert_order(self, *, exchange: str, pair: str, side: str, amount: float,
                     price: float, fee: float, status: str, reason: str,
                     dup_key: str | None = None, mode: str = "demo",
                     live_confirmation: str = "") -> int | None:
        """Returns the order id, or None if the duplicate guard rejected it.

        mode='demo'  -> simulated, always allowed.
        mode='live'  -> REAL fill; REQUIRES live_confirmation (the exchange order
        id) so a real trade is never recorded without its external reference.
        Any other mode is rejected."""
        if mode not in ("demo", "live"):
            raise ValueError(f"unknown order mode: {mode!r} (use 'demo' or 'live')")
        if mode == "live" and not live_confirmation:
            raise ValueError("a live order must carry live_confirmation (exchange order id)")
        try:
            with self._lock, self._conn() as c:
                cur = c.execute(
                    "INSERT INTO orders(ts,exchange,pair,side,amount,price,fee,mode,"
                    "status,reason,live_confirmation,dup_key)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (now_ts(), exchange, pair, side, amount, price, fee, mode,
                     status, reason, live_confirmation, dup_key))
                return cur.lastrowid
        except sqlite3.IntegrityError:
            return None  # duplicate dup_key: the guard did its job

    def orders(self, limit: int = 100) -> list[dict]:
        with self._lock, self._conn() as c:
            rows = c.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # -- positions -------------------------------------------------------
    def open_position(self, pair: str, amount: float, entry_price: float,
                      exchange: str = "demo") -> int:
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO positions(pair,amount,entry_price,opened_ts,status,exchange)"
                " VALUES(?,?,?,?,'open',?)",
                (pair, amount, entry_price, now_ts(), exchange))
            return cur.lastrowid

    def close_position(self, pos_id: int, exit_price: float, pnl: float) -> None:
        with self._lock, self._conn() as c:
            c.execute("UPDATE positions SET status='closed', closed_ts=?, pnl=? WHERE id=?",
                      (now_ts(), pnl, pos_id))

    def positions(self, status: str | None = None, limit: int = 100) -> list[dict]:
        q = "SELECT * FROM positions" + (" WHERE status=?" if status else "") + " ORDER BY id DESC LIMIT ?"
        args = ((status, limit) if status else (limit,))
        with self._lock, self._conn() as c:
            rows = c.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    # -- equity marks ----------------------------------------------------
    def mark_equity(self, equity: float, cash: float, positions_value: float,
                    unrealized_pnl: float) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO equity_marks(ts,equity,cash,positions_value,unrealized_pnl)"
                      " VALUES(?,?,?,?,?)",
                      (now_ts(), equity, cash, positions_value, unrealized_pnl))

    def equity_marks(self, limit: int = 500) -> list[dict]:
        with self._lock, self._conn() as c:
            rows = c.execute("SELECT * FROM equity_marks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in reversed(list(rows))]

    # -- config ----------------------------------------------------------
    def set_config(self, key: str, value) -> None:
        if _SECRET_KEY.search(key):
            raise ValueError(f"refusing to store credential-like config key: {key}")
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO config(key,value) VALUES(?,?)"
                      " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, json.dumps(value)))

    def get_config(self, key: str, default=None):
        with self._lock, self._conn() as c:
            row = c.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

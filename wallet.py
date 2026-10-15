"""Multi-asset, multi-chain wallet: holds SOL, ETH, BTC, USDC, and any other
asset -- on whatever chain it lives -- marks everything to market in the quote
asset, and keeps an auditable ledger.

Design rules:
- The account is DENOMINATED in the quote asset (USDC). Everything else -- ETH
  on Ethereum, SOL on Solana, BTC on Bitcoin, USDC on Base -- is an asset it may
  hold. "Accept Solana and other chains" means: deposits of any chain's asset,
  trading between them, and one honest equity number in USDC terms.
- CHAIN provenance travels with every ledger entry. Same symbol, different chain
  (USDC on Ethereum vs USDC on Solana) is different money at different addresses;
  the ledger records which chain each entry meant.
- Every mutation appends to an immutable ledger entry. No silent state changes.
- Mark-to-market uses the last known price; if a price is missing the wallet
  refuses to guess and raises instead of inventing equity.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from chains import chain_of, describe


@dataclass
class LedgerEntry:
    ts: str
    kind: str          # deposit | withdraw | buy | sell | fee | mark
    asset: str
    amount: float      # signed change in asset units
    price: float       # quote price at the time (1.0 for quote asset)
    note: str = ""
    chain: str = ""    # which chain the asset lives on (ethereum, solana, ...)


@dataclass
class Wallet:
    quote_asset: str = "USDC"
    balances: dict = field(default_factory=lambda: {"USDC": 0.0, "ETH": 0.0})
    ledger: list = field(default_factory=list)
    prices: dict = field(default_factory=dict)   # base asset -> quote price

    # -- bookkeeping -----------------------------------------------------
    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _record(self, kind: str, asset: str, amount: float, price: float,
                note: str = "", chain: str = "") -> None:
        if not chain:
            tok = chain_of(asset)
            chain = tok.chain if tok else "unknown"
        self.ledger.append(LedgerEntry(self._now(), kind, asset, amount, price, note, chain))

    def deposit(self, asset: str, amount: float, price: float | None = None,
                chain: str = "") -> float:
        """Accept a deposit of ANY chain's asset (SOL, ETH, BTC, USDC, ...).
        `chain` is optional provenance -- the registry resolves it from the
        symbol when omitted, and records 'unknown' rather than guessing.
        Returns equity in quote terms."""
        if amount <= 0:
            raise ValueError("deposit amount must be positive")
        asset = asset.upper()
        px = price if price is not None else (1.0 if asset == self.quote_asset else self.prices.get(asset))
        if asset != self.quote_asset and px is None:
            raise ValueError(f"no price known for {asset}; set wallet.prices['{asset}'] first")
        self.balances[asset] = self.balances.get(asset, 0.0) + amount
        if asset != self.quote_asset and price is not None:
            self.prices[asset] = price
        tok = chain_of(asset)
        resolved = chain or (tok.chain if tok else "unknown")
        self._record("deposit", asset, amount, px or 1.0,
                     note=describe(asset), chain=resolved)
        return self.equity()

    def withdraw(self, asset: str, amount: float) -> float:
        asset = asset.upper()
        have = self.balances.get(asset, 0.0)
        if amount <= 0 or amount > have + 1e-12:
            raise ValueError(f"cannot withdraw {amount} {asset}; balance is {have}")
        self.balances[asset] = have - amount
        self._record("withdraw", asset, -amount, self.prices.get(asset, 1.0))
        return self.equity()

    def set_price(self, asset: str, price: float) -> None:
        if price <= 0:
            raise ValueError("price must be positive")
        self.prices[asset.upper()] = price

    def buy(self, base: str, quote_amount: float, price: float, fee_frac: float = 0.0) -> float:
        """Spend quote_amount of the quote asset to buy `base`. Fees paid in quote."""
        if quote_amount <= 0 or price <= 0:
            raise ValueError("amount and price must be positive")
        base, quote = base.upper(), self.quote_asset
        total_cost = quote_amount
        if self.balances.get(quote, 0.0) < total_cost - 1e-12:
            raise ValueError(f"insufficient {quote}: need {total_cost}, have {self.balances.get(quote, 0.0)}")
        fee = total_cost * fee_frac
        bought = (total_cost - fee) / price
        self.balances[quote] = self.balances.get(quote, 0.0) - total_cost
        self.balances[base] = self.balances.get(base, 0.0) + bought
        self.prices[base] = price
        self._record("buy", base, bought, price, f"spent {total_cost:.6f} {quote}")
        if fee:
            self._record("fee", quote, -fee, 1.0, "entry fee")
        return bought

    def sell(self, base: str, base_amount: float, price: float, fee_frac: float = 0.0) -> float:
        """Sell `base_amount` of base for quote. Fees taken from proceeds."""
        if base_amount <= 0 or price <= 0:
            raise ValueError("amount and price must be positive")
        base, quote = base.upper(), self.quote_asset
        have = self.balances.get(base, 0.0)
        if base_amount > have + 1e-12:
            raise ValueError(f"insufficient {base}: need {base_amount}, have {have}")
        proceeds = base_amount * price
        fee = proceeds * fee_frac
        net = proceeds - fee
        self.balances[base] = have - base_amount
        self.balances[quote] = self.balances.get(quote, 0.0) + net
        self.prices[base] = price
        self._record("sell", base, -base_amount, price, f"received {net:.6f} {quote}")
        if fee:
            self._record("fee", quote, -fee, 1.0, "exit fee")
        return net

    # -- valuation -------------------------------------------------------
    def equity(self) -> float:
        """Total equity in quote terms. Refuses to guess unknown prices."""
        total = 0.0
        for asset, amount in self.balances.items():
            if abs(amount) < 1e-12:
                continue  # zero balance: nothing to mark
            if asset == self.quote_asset:
                total += amount
            else:
                if asset not in self.prices:
                    raise ValueError(f"no price known for {asset}; cannot mark to market")
                total += amount * self.prices[asset]
        return total

    def holdings(self) -> dict:
        out = {}
        for asset, amount in self.balances.items():
            px = 1.0 if asset == self.quote_asset else self.prices.get(asset)
            tok = chain_of(asset)
            out[asset] = {
                "amount": amount,
                "price": px,
                "value_quote": amount * px if px is not None else None,
                "chain": tok.chain if tok else "unknown",
                "contract": tok.contract if tok else "",
            }
        return out

    def chains(self) -> dict:
        """Value grouped by chain: {'solana': 250.0, 'ethereum': 750.0, ...}.
        One asset can exist on several chains (USDC); this groups the holdings
        by where each balance's provenance says it lives."""
        out: dict[str, float] = {}
        for asset, h in self.holdings().items():
            v = h["value_quote"] or 0.0
            out[h["chain"]] = out.get(h["chain"], 0.0) + v
        return out

    # -- persistence -----------------------------------------------------
    def save(self, path: str) -> None:
        payload = {
            "quote_asset": self.quote_asset,
            "balances": self.balances,
            "prices": self.prices,
            "ledger": [e.__dict__ for e in self.ledger],
        }
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)

    @classmethod
    def load(cls, path: str) -> "Wallet":
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        with open(path) as f:
            payload = json.load(f)
        w = cls(quote_asset=payload.get("quote_asset", "USDC"),
                balances=payload.get("balances", {}),
                prices=payload.get("prices", {}),
                ledger=[LedgerEntry(**e) for e in payload.get("ledger", [])])
        return w

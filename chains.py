"""Chain registry: which asset lives on which chain, and where its contracts are.

The bot's unit of account stays the quote asset (USDC). A CHAIN is metadata that
travels with every asset: where it was issued, what contract/mint represents it,
and whether the deposit address is chain-specific. This matters for exactly two
reasons, and both are safety:
  1. A USDC deposit on Solana and a USDC deposit on Ethereum are the same dollars
     to the wallet -- but the ADDRESS you send to is completely different. Send
     to the wrong chain and the funds are gone. The registry exists so the bot can
     TELL you which chain it means instead of leaving it implicit.
  2. Exchanges list SOL/USDC, BTC/USDC, ETH/USDC -- different chains, same trading
     interface. `chain_of()` keeps the provenance straight in the ledger.

The registry is deliberately small and known-good. `register_chain()` extends it.
Unknown symbols are not fatal: the wallet accepts them with chain='unknown' and
says so in the ledger, rather than inventing a chain."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Token:
    symbol: str
    chain: str
    kind: str            # native | token
    contract: str = ""   # ERC-20 address, SPL mint, etc. "" for native coins
    note: str = ""


@dataclass
class Chain:
    name: str
    display: str
    native_symbol: str
    tokens: dict = field(default_factory=dict)   # symbol -> Token


CHAINS: dict[str, Chain] = {}
_TOKEN_INDEX: dict[str, list[Token]] = {}


def register_chain(chain: Chain) -> None:
    CHAINS[chain.name] = chain
    for tok in chain.tokens.values():
        _TOKEN_INDEX.setdefault(tok.symbol.upper(), []).append(tok)


# -- the known-good registry -----------------------------------------------
# Contract/mint addresses below are the canonical, widely-published ones.
# Verify against the project's own docs before sending anything anywhere.
register_chain(Chain("ethereum", "Ethereum", "ETH", {
    "ETH": Token("ETH", "ethereum", "native", note="gas asset"),
    "USDC": Token("USDC", "ethereum", "token",
                  "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "ERC-20"),
    "USDT": Token("USDT", "ethereum", "token",
                  "0xdAC17F958D2ee523a2206206994597C13D831ec7", "ERC-20"),
    "WETH": Token("WETH", "ethereum", "token",
                  "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "ERC-20"),
}))

register_chain(Chain("solana", "Solana", "SOL", {
    "SOL": Token("SOL", "solana", "native", note="gas asset; also written SOL.sol"),
    "USDC": Token("USDC", "solana", "token",
                  "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "SPL mint"),
}))

register_chain(Chain("bitcoin", "Bitcoin", "BTC", {
    "BTC": Token("BTC", "bitcoin", "native"),
    "WBTC": Token("WBTC", "bitcoin", "token",
                  "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
                  "ERC-20 on Ethereum; wrapped BTC"),
}))

register_chain(Chain("base", "Base", "ETH", {
    "ETH": Token("ETH", "base", "native", note="Base is an L2; gas is ETH"),
    "USDC": Token("USDC", "base", "token",
                  "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "ERC-20 on Base"),
}))

register_chain(Chain("polygon", "Polygon", "POL", {
    "POL": Token("POL", "polygon", "native", note="gas asset (formerly MATIC)"),
    "USDC": Token("USDC", "polygon", "token",
                  "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", "ERC-20 on Polygon"),
}))

register_chain(Chain("arbitrum", "Arbitrum One", "ETH", {
    "ETH": Token("ETH", "arbitrum", "native", note="L2; gas is ETH"),
    "USDC": Token("USDC", "arbitrum", "token",
                  "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", "ERC-20 on Arbitrum"),
}))


# -- lookups ----------------------------------------------------------------
def chain_of(symbol: str) -> Token | None:
    """Where does this symbol live? Returns the canonical token or None.

    Same-symbol collisions (USDC exists on 5+ chains) resolve to the FIRST
    registered chain and the token carries .chain so callers can be explicit."""
    toks = _TOKEN_INDEX.get(symbol.upper())
    return toks[0] if toks else None


def chains_of(symbol: str) -> list[Token]:
    """Every chain this symbol exists on -- for when the chain matters
    (deposit addresses) and guessing would lose funds."""
    return list(_TOKEN_INDEX.get(symbol.upper(), []))


def describe(symbol: str) -> str:
    """Human-readable provenance line for a symbol, for ledgers and reports."""
    toks = chains_of(symbol)
    if not toks:
        return f"{symbol.upper()}: unknown chain (accepted, tracked, unverified)"
    parts = [f"{t.chain}" + (f" [{t.kind}, {t.contract[:10]}...]" if t.contract else f" [{t.kind}]")
             for t in toks]
    return f"{symbol.upper()}: " + "; ".join(parts)


def known_symbols() -> list[str]:
    return sorted(_TOKEN_INDEX)

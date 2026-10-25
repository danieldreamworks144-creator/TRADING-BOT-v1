#!/usr/bin/env python3
"""Paper-only Solana meme-token market screener.

This program reads public DEX Screener snapshots for mint addresses you supply.
It emits PAPER_WATCH or NO_TRADE labels for research. It does not connect to a
wallet, sign transactions, submit swaps, or simulate fills or profit/loss.

Example:
    python3 solana_meme_trader.py --mints MINT_ADDRESS
    python3 solana_meme_trader.py --mints-file watchlist.txt --interval 60
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_ROOT = "https://api.dexscreener.com/tokens/v1/solana/"
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BASE58_PATTERN = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
MAX_MINTS = 30
MAX_RESPONSE_BYTES = 2_000_000
USER_AGENT = "solana-meme-paper-screener/1.0"


class ScanError(RuntimeError):
    """A bounded public market-data scan could not be completed."""


def is_valid_mint(mint: str) -> bool:
    """Return True only for Base58 strings decoding to 32 bytes."""
    if not isinstance(mint, str) or not BASE58_PATTERN.fullmatch(mint):
        return False
    value = 0
    for character in mint:
        value = value * 58 + BASE58_ALPHABET.index(character)
    decoded_length = (value.bit_length() + 7) // 8
    leading_zero_bytes = len(mint) - len(mint.lstrip("1"))
    return decoded_length + leading_zero_bytes == 32


def parse_mints(raw: str) -> list[str]:
    """Parse and validate a comma-separated list of unique mint addresses."""
    mints = [item.strip() for item in raw.split(",") if item.strip()]
    if not mints:
        raise ValueError("provide at least one Solana mint address")
    if len(mints) > MAX_MINTS:
        raise ValueError(f"at most {MAX_MINTS} mints may be scanned at once")
    if len(set(mints)) != len(mints):
        raise ValueError("duplicate mint addresses are not allowed")
    if any(not is_valid_mint(mint) for mint in mints):
        raise ValueError("each mint must be a Base58-encoded 32-byte Solana public key")
    return mints


def read_mints_file(path: str) -> list[str]:
    """Read one mint per line; blank lines and # comments are ignored."""
    lines = Path(path).expanduser().read_text(encoding="utf-8").splitlines()
    values = [line.split("#", 1)[0].strip() for line in lines]
    return parse_mints(",".join(value for value in values if value))


def fetch_pairs(mints: list[str], *, timeout: float = 12.0) -> list[dict[str, Any]]:
    """Fetch one bounded public DEX Screener snapshot; no credentials are sent."""
    if not mints or len(mints) > MAX_MINTS:
        raise ValueError(f"mint count must be between 1 and {MAX_MINTS}")
    if len(set(mints)) != len(mints) or any(not is_valid_mint(mint) for mint in mints):
        raise ValueError("mint list must contain unique valid 32-byte public keys")
    request = Request(
        API_ROOT + ",".join(mints),
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload_bytes = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise ScanError(f"market-data service returned HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError) as exc:
        raise ScanError(f"market-data request failed ({type(exc).__name__})") from None
    if len(payload_bytes) > MAX_RESPONSE_BYTES:
        raise ScanError("market-data response exceeded the size limit")
    try:
        payload = json.loads(payload_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ScanError("market-data service returned invalid JSON") from None
    if not isinstance(payload, list):
        raise ScanError("market-data service returned an unexpected response")
    return [item for item in payload if isinstance(item, dict)]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _count(mapping: Any, key: str) -> int | None:
    if not isinstance(mapping, dict):
        return None
    value = mapping.get(key)
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if isinstance(value, float) and value != result:
        return None
    return result if result >= 0 else None


def best_pair(mint: str, pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose the deepest matching Solana pool, preferring known liquidity."""
    matches: list[tuple[float, dict[str, Any]]] = []
    for pair in pairs:
        if str(pair.get("chainId", "")).lower() != "solana":
            continue
        base = pair.get("baseToken")
        quote = pair.get("quoteToken")
        if not isinstance(base, dict) or not isinstance(quote, dict):
            continue
        if mint not in (base.get("address"), quote.get("address")):
            continue
        liquidity_map = pair.get("liquidity")
        liquidity = _number(liquidity_map.get("usd")) if isinstance(liquidity_map, dict) else None
        matches.append((liquidity if liquidity is not None else -1.0, pair))
    return max(matches, key=lambda item: item[0])[1] if matches else None


def screen_pair(
    mint: str,
    pair: dict[str, Any] | None,
    *,
    now_ms: int | None = None,
    min_liquidity_usd: float = 50_000.0,
    min_h1_volume_usd: float = 20_000.0,
    min_h1_transactions: int = 50,
    min_pair_age_minutes: float = 1_440.0,
    min_m5_change_pct: float = 0.5,
    max_m5_change_pct: float = 3.0,
    min_buy_sell_ratio: float = 1.25,
) -> dict[str, Any]:
    """Apply explicit provisional market-data filters; never creates an order."""
    if pair is None:
        return {"mint": mint, "status": "NO_DATA", "reasons": ["no Solana pool found"]}

    base = pair.get("baseToken")
    quote = pair.get("quoteToken")
    base = base if isinstance(base, dict) else {}
    quote = quote if isinstance(quote, dict) else {}
    is_base = base.get("address") == mint
    token = base if is_base else quote

    liquidity_map = pair.get("liquidity")
    liquidity_map = liquidity_map if isinstance(liquidity_map, dict) else {}
    volume_map = pair.get("volume")
    volume_map = volume_map if isinstance(volume_map, dict) else {}
    change_map = pair.get("priceChange")
    change_map = change_map if isinstance(change_map, dict) else {}
    txns_map = pair.get("txns")
    txns_map = txns_map if isinstance(txns_map, dict) else {}
    h1_txns = txns_map.get("h1")
    h1_txns = h1_txns if isinstance(h1_txns, dict) else {}

    liquidity = _number(liquidity_map.get("usd"))
    volume_h1 = _number(volume_map.get("h1"))
    buys = _count(h1_txns, "buys")
    sells = _count(h1_txns, "sells")
    price = _number(pair.get("priceUsd")) if is_base else None
    m5_change = _number(change_map.get("m5"))
    created_ms = _number(pair.get("pairCreatedAt"))
    scan_time_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    age_minutes = ((scan_time_ms - created_ms) / 60_000.0
                   if created_ms is not None else None)

    reasons: list[str] = []
    if str(pair.get("chainId", "")).lower() != "solana":
        reasons.append("pool is not identified as Solana")
    if not is_base:
        reasons.append("mint is quote-side; base-token price cannot be attributed safely")
    if price is None or price <= 0:
        reasons.append("valid base-token USD price unavailable")
    if liquidity is None or liquidity < min_liquidity_usd:
        reasons.append(f"liquidity below ${min_liquidity_usd:,.0f} or unavailable")
    if volume_h1 is None or volume_h1 < min_h1_volume_usd:
        reasons.append(f"one-hour volume below ${min_h1_volume_usd:,.0f} or unavailable")
    if buys is None or sells is None:
        reasons.append("one-hour buy/sell counts unavailable")
    else:
        if buys + sells < min_h1_transactions:
            reasons.append(f"fewer than {min_h1_transactions} one-hour transactions")
        if buys + sells == 0:
            reasons.append("no one-hour transactions")
        elif sells == 0:
            reasons.append("no sell-side transactions; buy/sell ratio is not verifiable")
        elif buys / sells < min_buy_sell_ratio:
            reasons.append(f"buy/sell ratio below {min_buy_sell_ratio:g}")
    if age_minutes is None or age_minutes < min_pair_age_minutes:
        reasons.append(f"pool younger than {min_pair_age_minutes:g} minutes or age unavailable")
    elif age_minutes > 10 * 365 * 24 * 60:
        reasons.append("pool timestamp is implausibly old")
    if age_minutes is not None and age_minutes < -2:
        reasons.append("pool timestamp is in the future")
    if m5_change is None or not min_m5_change_pct <= m5_change <= max_m5_change_pct:
        reasons.append(
            f"five-minute change outside {min_m5_change_pct:g}% to {max_m5_change_pct:g}%"
        )

    return {
        "mint": mint,
        "symbol": str(token.get("symbol", ""))[:32] or None,
        "status": "PAPER_WATCH" if not reasons else "NO_TRADE",
        "price_usd": price,
        "liquidity_usd": liquidity,
        "volume_h1_usd": volume_h1,
        "buys_h1": buys,
        "sells_h1": sells,
        "pair_age_minutes": round(age_minutes, 2) if age_minutes is not None else None,
        "change_m5_pct": m5_change,
        "dex": str(pair.get("dexId", ""))[:40] or None,
        "reasons": reasons,
    }


def scan_once(mints: list[str], **filters: Any) -> dict[str, Any]:
    """Fetch one snapshot and produce a result for every requested mint."""
    pairs = fetch_pairs(mints)
    results = [screen_pair(mint, best_pair(mint, pairs), **filters) for mint in mints]
    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "mode": "paper_market_data_only",
        "results": results,
        "disclaimer": (
            "PAPER_WATCH is only a provisional data filter, not a buy signal. "
            "This program sends no transactions and models no fills or profitability."
        ),
    }


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number") from None
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return number


def _nonnegative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only Solana pool screener; reports paper watch labels only."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--mints", help="comma-separated list of 1-30 Solana mint addresses")
    source.add_argument("--mints-file", help="text file containing one Solana mint per line")
    parser.add_argument("--min-liquidity-usd", type=_positive_float, default=50_000.0)
    parser.add_argument("--min-h1-volume-usd", type=_positive_float, default=20_000.0)
    parser.add_argument("--min-h1-transactions", type=_nonnegative_int, default=50)
    parser.add_argument("--min-pair-age-minutes", type=_positive_float, default=1_440.0)
    parser.add_argument("--min-m5-change-pct", type=float, default=0.5)
    parser.add_argument("--max-m5-change-pct", type=float, default=3.0)
    parser.add_argument("--min-buy-sell-ratio", type=_positive_float, default=1.25)
    parser.add_argument(
        "--interval", type=_nonnegative_int, default=0,
        help="repeat scan every N seconds; 0 means one scan (repeats require >=30)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        mints = parse_mints(args.mints) if args.mints is not None else read_mints_file(args.mints_file)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))

    if args.interval and args.interval < 30:
        parser.error("--interval must be 0 or at least 30 seconds")
    if (not math.isfinite(args.min_m5_change_pct)
            or not math.isfinite(args.max_m5_change_pct)
            or args.min_m5_change_pct > args.max_m5_change_pct):
        parser.error("five-minute change bounds must be finite and minimum <= maximum")

    filters = {
        "min_liquidity_usd": args.min_liquidity_usd,
        "min_h1_volume_usd": args.min_h1_volume_usd,
        "min_h1_transactions": args.min_h1_transactions,
        "min_pair_age_minutes": args.min_pair_age_minutes,
        "min_m5_change_pct": args.min_m5_change_pct,
        "max_m5_change_pct": args.max_m5_change_pct,
        "min_buy_sell_ratio": args.min_buy_sell_ratio,
    }
    try:
        while True:
            print(json.dumps(scan_once(mints, **filters), indent=2, allow_nan=False), flush=True)
            if not args.interval:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130
    except ScanError as exc:
        print(json.dumps({"mode": "paper_market_data_only", "error": str(exc)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

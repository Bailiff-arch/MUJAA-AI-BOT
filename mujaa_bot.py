"""MUJAA AI BOT — paper-trading foundation.

Milestone 1 collects public Solana market snapshots only.
It does not connect to wallets, sign transactions, or place orders.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SNAPSHOT_FILE = DATA_DIR / "market_snapshots.jsonl"
LOG_FILE = DATA_DIR / "engine.log"
SEARCH_TERMS = os.getenv("MUJAA_SEARCH_TERMS", "SOL,USDC").split(",")
MIN_LIQUIDITY_USD = float(os.getenv("MUJAA_MIN_LIQUIDITY_USD", "10000"))
MIN_VOLUME_24H_USD = float(os.getenv("MUJAA_MIN_VOLUME_24H_USD", "10000"))
DEXSCREENER_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"

DATA_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")],
)
logger = logging.getLogger("mujaa")


def fetch_solana_pairs(term: str) -> list[dict[str, Any]]:
    """Fetch public pair data and keep Solana pairs only."""
    response = requests.get(
        DEXSCREENER_SEARCH_URL,
        params={"q": term.strip()},
        timeout=20,
        headers={"User-Agent": "MUJAA-AI-BOT/0.1 paper-trading"},
    )
    response.raise_for_status()
    pairs = response.json().get("pairs") or []
    return [
        pair for pair in pairs
        if pair.get("chainId") == "solana"
        and pair.get("baseToken", {}).get("address")
    ]


def as_number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def collect_market_snapshot() -> dict[str, Any]:
    """Collect, filter, de-duplicate and persist a timestamped snapshot."""
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    errors: list[str] = []

    for term in SEARCH_TERMS:
        try:
            for pair in fetch_solana_pairs(term):
                pair_address = pair.get("pairAddress", "")
                if not pair_address or pair_address in seen:
                    continue
                seen.add(pair_address)

                liquidity = as_number((pair.get("liquidity") or {}).get("usd"))
                volume_24h = as_number((pair.get("volume") or {}).get("h24"))
                if liquidity < MIN_LIQUIDITY_USD or volume_24h < MIN_VOLUME_24H_USD:
                    continue

                candidates.append({
                    "chain": "solana",
                    "pair_address": pair_address,
                    "dex_id": pair.get("dexId"),
                    "token_name": pair.get("baseToken", {}).get("name"),
                    "symbol": pair.get("baseToken", {}).get("symbol"),
                    "token_address": pair.get("baseToken", {}).get("address"),
                    "quote_symbol": pair.get("quoteToken", {}).get("symbol"),
                    "price_usd": as_number(pair.get("priceUsd")),
                    "liquidity_usd": liquidity,
                    "volume_24h_usd": volume_24h,
                    "price_change_1h_pct": as_number((pair.get("priceChange") or {}).get("h1")),
                    "price_change_24h_pct": as_number((pair.get("priceChange") or {}).get("h24")),
                    "pair_url": pair.get("url"),
                    "pair_created_at_ms": pair.get("pairCreatedAt"),
                })
        except requests.RequestException as exc:
            message = f"{term}: {exc}"
            errors.append(message)
            logger.warning("Market-data request failed: %s", message)

    candidates.sort(key=lambda item: item["volume_24h_usd"], reverse=True)
    snapshot = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "engine": "market-data-only",
        "paper_only": True,
        "candidate_count": len(candidates),
        "filters": {
            "min_liquidity_usd": MIN_LIQUIDITY_USD,
            "min_volume_24h_usd": MIN_VOLUME_24H_USD,
        },
        "errors": errors,
        "candidates": candidates[:50],
    }

    with SNAPSHOT_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, separators=(",", ":")) + "\n")

    logger.info(
        "Snapshot saved: %d candidates; %d data-source errors",
        snapshot["candidate_count"], len(errors),
    )
    return snapshot


if __name__ == "__main__":
    result = collect_market_snapshot()
    print(json.dumps({
        "timestamp_utc": result["timestamp_utc"],
        "paper_only": result["paper_only"],
        "candidate_count": result["candidate_count"],
        "errors": result["errors"],
        "saved_to": str(SNAPSHOT_FILE.relative_to(ROOT)),
    }, indent=2))

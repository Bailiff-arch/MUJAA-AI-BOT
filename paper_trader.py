#!/usr/bin/env python3
"""MUJAA paper-only simulator. Never connects to a wallet or exchange."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA = Path("data")
SNAPSHOTS = DATA / "market_snapshots.jsonl"
STATE = DATA / "paper_portfolio.json"
LOG = DATA / "engine.log"

STARTING_CASH = float(os.getenv("STARTING_CASH", "100"))
FEE_RATE = float(os.getenv("FEE_RATE", "0.0025"))
SLIPPAGE = float(os.getenv("SLIPPAGE", "0.005"))
STOP_LOSS = float(os.getenv("STOP_LOSS", "0.05"))
TAKE_PROFIT = float(os.getenv("TAKE_PROFIT", "0.10"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))
MAX_POSITION_FRACTION = float(os.getenv("MAX_POSITION_FRACTION", "0.20"))
MIN_LIQUIDITY_USD = float(os.getenv("MUJAA_MIN_LIQUIDITY_USD", "10000"))
MIN_VOLUME_24H_USD = float(os.getenv("MUJAA_MIN_VOLUME_24H_USD", "10000"))
ALLOWED_QUOTES = {"SOL", "USDC", "USDT"}
EXCLUDED_BASES = {"SOL", "USDC", "USDT", "USDS", "DAI", "USDE", "USD1"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{utc_now()} [PAPER] {message}\n")
    print(message)


def number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def load_latest_snapshot() -> dict[str, Any] | None:
    if not SNAPSHOTS.exists():
        return None
    lines = [line for line in SNAPSHOTS.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return None
    # Ignore malformed trailing lines but do not silently accept an invalid latest record.
    return json.loads(lines[-1])


def normalize(item: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize the collector's compact schema and raw DexScreener pair schema."""
    if not isinstance(item, dict):
        return None
    if isinstance(item.get("pair"), dict):
        item = item["pair"]

    # Schema emitted by mujaa_bot.py.
    if "token_address" in item or "price_usd" in item:
        chain = item.get("chain", "solana")
        symbol = item.get("symbol") or ""
        address = item.get("token_address") or ""
        quote = item.get("quote_symbol") or ""
        price = number(item.get("price_usd"))
        liquidity = number(item.get("liquidity_usd"))
        volume = number(item.get("volume_24h_usd"))
        h1 = number(item.get("price_change_1h_pct"))
        h24 = number(item.get("price_change_24h_pct"))
    else:
        chain = item.get("chainId", "solana")
        base = item.get("baseToken") or {}
        quote_obj = item.get("quoteToken") or {}
        change = item.get("priceChange") or {}
        symbol = base.get("symbol") or ""
        address = base.get("address") or ""
        quote = quote_obj.get("symbol") or ""
        price = number(item.get("priceUsd"))
        liquidity = number((item.get("liquidity") or {}).get("usd"))
        volume = number((item.get("volume") or {}).get("h24"))
        h1 = number(change.get("h1"))
        h24 = number(change.get("h24"))

    symbol = str(symbol).strip().upper()
    quote = str(quote).strip().upper()
    if str(chain).lower() != "solana" or not address or not symbol:
        return None
    if symbol in EXCLUDED_BASES or quote not in ALLOWED_QUOTES:
        return None
    if price <= 0:
        return None
    return {
        "symbol": symbol, "address": str(address), "quote": quote,
        "price": price, "liquidity": liquidity, "volume24h": volume,
        "h1": h1, "h24": h24,
    }


def load_state() -> dict[str, Any]:
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding="utf-8"))
        required = {"mode", "starting_cash", "cash", "positions", "trades"}
        if not isinstance(state, dict) or not required.issubset(state):
            raise ValueError("Existing paper portfolio is invalid; refusing to reset virtual funds.")
        if state.get("mode") != "paper_only":
            raise ValueError("Portfolio mode is not paper_only; refusing to continue.")
        return state
    return {
        "mode": "paper_only",
        "created_at": utc_now(),
        "starting_cash": STARTING_CASH,
        "cash": STARTING_CASH,
        "positions": {},
        "trades": [],
        "last_updated": None,
    }


def save_state(state: dict[str, Any]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    state["last_updated"] = utc_now()
    temp = STATE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(STATE)


def eligible_candidates(snapshot: dict[str, Any] | None) -> tuple[list[dict[str, Any]], str | None]:
    if not snapshot:
        return [], None
    candidates = snapshot.get("candidates", [])
    if not isinstance(candidates, list):
        return [], snapshot.get("timestamp_utc")
    normalized = [normalize(x) for x in candidates]
    valid = [
        p for p in normalized if p
        and p["liquidity"] >= MIN_LIQUIDITY_USD
        and p["volume24h"] >= MIN_VOLUME_24H_USD
        and p["h1"] > 0 and p["h24"] > 0
    ]
    valid.sort(key=lambda p: p["volume24h"], reverse=True)
    # One position per token address; deduplicate repeated pools for the same token.
    unique: dict[str, dict[str, Any]] = {}
    for pair in valid:
        unique.setdefault(pair["address"], pair)
    return list(unique.values()), snapshot.get("timestamp_utc")


def run_simulation() -> dict[str, Any]:
    DATA.mkdir(parents=True, exist_ok=True)
    snapshot = load_latest_snapshot()
    candidates, snapshot_time = eligible_candidates(snapshot)
    state = load_state()
    positions = state["positions"]
    prices = {p["address"]: p for p in candidates}

    # Update marks from every normalized pair in the snapshot, even if momentum cooled.
    raw_candidates = (snapshot or {}).get("candidates", [])
    for raw in raw_candidates if isinstance(raw_candidates, list) else []:
        pair = normalize(raw)
        if pair and pair["address"] in positions:
            positions[pair["address"]]["last_price"] = pair["price"]

    # Close positions at stop loss / take profit using the latest observed price.
    for address in list(positions):
        pos = positions[address]
        price = number(pos.get("last_price")) or number(pos["entry_price"])
        entry = number(pos["entry_price"])
        reason = "stop_loss" if price <= entry * (1 - STOP_LOSS) else (
            "take_profit" if price >= entry * (1 + TAKE_PROFIT) else None
        )
        if reason:
            proceeds = number(pos["quantity"]) * price * (1 - SLIPPAGE) * (1 - FEE_RATE)
            pnl = proceeds - number(pos["cost_basis"])
            state["cash"] += proceeds
            state["trades"].append({
                "time": utc_now(), "side": "SELL", "symbol": pos["symbol"],
                "address": address, "price": price, "quantity": pos["quantity"],
                "proceeds": round(proceeds, 8), "pnl": round(pnl, 8), "reason": reason,
            })
            del positions[address]
            log(f"SELL {pos['symbol']} reason={reason} realized_pnl=${pnl:.6f}")

    # Calculate equity before sizing new positions.
    position_value = sum(
        number(p["quantity"]) * (number(p.get("last_price")) or number(p["entry_price"]))
        for p in positions.values()
    )
    equity = number(state["cash"]) + position_value

    for pair in candidates:
        if len(positions) >= MAX_POSITIONS:
            break
        if pair["address"] in positions:
            continue
        budget = min(number(state["cash"]), equity * MAX_POSITION_FRACTION)
        if budget <= 1:
            break
        execution_price = pair["price"] * (1 + SLIPPAGE)
        quantity = (budget * (1 - FEE_RATE)) / execution_price
        state["cash"] -= budget
        positions[pair["address"]] = {
            "symbol": pair["symbol"], "quantity": quantity,
            "entry_price": execution_price, "last_price": pair["price"],
            "cost_basis": budget, "opened_at": utc_now(),
        }
        state["trades"].append({
            "time": utc_now(), "side": "BUY", "symbol": pair["symbol"],
            "address": pair["address"], "price": execution_price,
            "quantity": quantity, "cost": round(budget, 8),
        })
        log(f"BUY {pair['symbol']} virtual_cost=${budget:.2f}")

    position_value = sum(
        number(p["quantity"]) * (number(p.get("last_price")) or number(p["entry_price"]))
        for p in positions.values()
    )
    equity = number(state["cash"]) + position_value
    realized = sum(number(t.get("pnl")) for t in state["trades"] if t.get("side") == "SELL")
    unrealized = sum(
        number(p["quantity"]) * (number(p.get("last_price")) or number(p["entry_price"]))
        - number(p["cost_basis"])
        for p in positions.values()
    )
    state["last_snapshot_timestamp"] = snapshot_time
    state["summary"] = {
        "starting_cash": round(number(state["starting_cash"]), 8),
        "cash": round(number(state["cash"]), 8),
        "position_value": round(position_value, 8),
        "equity": round(equity, 8),
        "realized_pnl": round(realized, 8),
        "unrealized_pnl": round(unrealized, 8),
        "open_positions": len(positions),
        "trade_count": len(state["trades"]),
        "last_snapshot_timestamp": snapshot_time,
    }
    save_state(state)
    log(
        f"SUMMARY equity=${equity:.2f} cash=${state['cash']:.2f} "
        f"open_positions={len(positions)} trades={len(state['trades'])}"
    )
    return state["summary"]


if __name__ == "__main__":
    result = run_simulation()
    print(json.dumps(result, indent=2))

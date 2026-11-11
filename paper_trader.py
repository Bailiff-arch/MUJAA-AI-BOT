#!/usr/bin/env python3
"""MUJAA AI BOT — paper-only portfolio simulator. No exchange or wallet access."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

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

def log(message):
    DATA.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{stamp} [PAPER] {message}\n")
    print(message)

def load_latest_pairs():
    if not SNAPSHOTS.exists():
        return []
    lines = SNAPSHOTS.read_text(encoding="utf-8").splitlines()
    if not lines:
        return []
    raw = json.loads(lines[-1])
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("pairs", "data", "results"):
            if isinstance(raw.get(key), list):
                return raw[key]
        if raw.get("pair") or raw.get("baseToken"):
            return [raw]
    return []

def normalize(pair):
    # Accept either a raw DexScreener pair or a wrapper containing "pair".
    if isinstance(pair, dict) and isinstance(pair.get("pair"), dict):
        pair = pair["pair"]
    try:
        price = float(pair.get("priceUsd") or 0)
        liquidity = float((pair.get("liquidity") or {}).get("usd") or pair.get("liquidityUsd") or 0)
        volume = float((pair.get("volume") or {}).get("h24") or pair.get("volume24h") or 0)
        change = pair.get("priceChange") or {}
        h1 = float(change.get("h1") or pair.get("change1h") or 0)
        h24 = float(change.get("h24") or pair.get("change24h") or 0)
        base = pair.get("baseToken") or {}
        symbol = base.get("symbol") or pair.get("symbol") or "UNKNOWN"
        address = base.get("address") or pair.get("baseTokenAddress") or ""
        chain = pair.get("chainId") or "solana"
        if chain != "solana" or price <= 0 or not address:
            return None
        return {"symbol": symbol, "address": address, "price": price,
                "liquidity": liquidity, "volume24h": volume, "h1": h1, "h24": h24}
    except (TypeError, ValueError, AttributeError):
        return None

def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {
        "mode": "paper_only",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "starting_cash": STARTING_CASH,
        "cash": STARTING_CASH,
        "positions": {},
        "trades": [],
        "last_updated": None
    }

def save_state(state):
    DATA.mkdir(exist_ok=True)
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE)

def main():
    DATA.mkdir(exist_ok=True)
    state = load_state()
    pairs = [p for p in (normalize(x) for x in load_latest_pairs()) if p]
    by_address = {p["address"]: p for p in pairs}
    positions = state["positions"]

    # Mark open positions using the latest available price.
    for address, pos in positions.items():
        if address in by_address:
            pos["last_price"] = by_address[address]["price"]

    # Exit at simulated stop-loss or take-profit.
    for address in list(positions):
        pos = positions[address]
        price = float(pos.get("last_price") or pos["entry_price"])
        entry = float(pos["entry_price"])
        reason = "stop_loss" if price <= entry * (1 - STOP_LOSS) else (
            "take_profit" if price >= entry * (1 + TAKE_PROFIT) else None)
        if reason:
            proceeds = pos["quantity"] * price * (1 - SLIPPAGE) * (1 - FEE_RATE)
            pnl = proceeds - pos["cost_basis"]
            state["cash"] += proceeds
            state["trades"].append({
                "time": datetime.now(timezone.utc).isoformat(), "side": "SELL",
                "symbol": pos["symbol"], "address": address, "price": price,
                "quantity": pos["quantity"], "pnl": round(pnl, 6), "reason": reason
            })
            del positions[address]
            log(f"SELL {pos['symbol']} reason={reason} pnl=${pnl:.4f}")

    # Equity and position sizing. All profits remain in the virtual account.
    position_value = sum(p["quantity"] * p.get("last_price", p["entry_price"])
                         for p in positions.values())
    equity = state["cash"] + position_value
    candidates = sorted(
        [p for p in pairs if p["liquidity"] >= 10000 and p["volume24h"] >= 10000
         and p["h1"] > 0 and p["h24"] > 0 and p["address"] not in positions],
        key=lambda p: p["volume24h"], reverse=True
    )

    for p in candidates:
        if len(positions) >= MAX_POSITIONS:
            break
        # Cap each new position at 20% of current equity and available cash.
        budget = min(state["cash"], equity * MAX_POSITION_FRACTION)
        if budget <= 1:
            break
        execution_price = p["price"] * (1 + SLIPPAGE)
        quantity = (budget * (1 - FEE_RATE)) / execution_price
        cost = budget
        state["cash"] -= cost
        positions[p["address"]] = {
            "symbol": p["symbol"], "quantity": quantity,
            "entry_price": execution_price, "last_price": p["price"],
            "cost_basis": cost, "opened_at": datetime.now(timezone.utc).isoformat()
        }
        state["trades"].append({
            "time": datetime.now(timezone.utc).isoformat(), "side": "BUY",
            "symbol": p["symbol"], "address": p["address"],
            "price": execution_price, "quantity": quantity, "cost": cost
        })
        log(f"BUY {p['symbol']} virtual_cost=${cost:.2f}")

    position_value = sum(p["quantity"] * p.get("last_price", p["entry_price"])
                         for p in positions.values())
    equity = state["cash"] + position_value
    realized = sum(float(t.get("pnl", 0)) for t in state["trades"] if t["side"] == "SELL")
    state["summary"] = {
        "cash": round(state["cash"], 6),
        "position_value": round(position_value, 6),
        "equity": round(equity, 6),
        "realized_pnl": round(realized, 6),
        "unrealized_pnl": round(sum(
            p["quantity"] * p.get("last_price", p["entry_price"]) - p["cost_basis"]
            for p in positions.values()), 6),
        "open_positions": len(positions),
        "trade_count": len(state["trades"])
    }
    save_state(state)
    log(f"SUMMARY equity=${equity:.2f} cash=${state['cash']:.2f} "
        f"open_positions={len(positions)} trades={len(state['trades'])}")

if __name__ == "__main__":
    main()

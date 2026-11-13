"""Pure, paper-only exit rules for MUJAA. No network, wallet, or order execution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExitConfig:
    fee_rate: float = 0.0025
    slippage: float = 0.005
    take_profit_net: float = 0.01       # +1% net after estimated exit costs
    stop_loss_price: float = 0.03       # 3% below execution entry price
    trailing_distance: float = 0.01     # 1% below the highest observed price

    def validate(self) -> None:
        for name in ("fee_rate", "slippage", "take_profit_net", "stop_loss_price", "trailing_distance"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or value < 0 or value >= 1:
                raise ValueError(f"{name} must be between 0 (inclusive) and 1 (exclusive)")


def estimated_net_pnl(position: dict[str, Any], market_price: float, config: ExitConfig) -> float:
    """Estimate proceeds after exit slippage and fee, less the original cash cost."""
    quantity = float(position["quantity"])
    cost_basis = float(position["cost_basis"])
    if quantity <= 0 or cost_basis <= 0 or market_price <= 0:
        raise ValueError("position quantity, cost_basis, and market_price must be positive")
    proceeds = quantity * market_price * (1 - config.slippage) * (1 - config.fee_rate)
    return proceeds - cost_basis


def estimated_break_even_price(position: dict[str, Any], config: ExitConfig) -> float:
    """Market price needed to recover cost_basis after estimated exit costs."""
    quantity = float(position["quantity"])
    cost_basis = float(position["cost_basis"])
    if quantity <= 0 or cost_basis <= 0:
        raise ValueError("position quantity and cost_basis must be positive")
    return cost_basis / (quantity * (1 - config.slippage) * (1 - config.fee_rate))


def evaluate_tick(
    position: dict[str, Any], market_price: float, config: ExitConfig = ExitConfig()
) -> dict[str, Any]:
    """Update high-water mark and return an exit decision without changing input.

    Exit reasons: take_profit_net, stop_loss, trailing_stop, or None.
    The caller is responsible for persisting the returned position and simulating a sale.
    """
    config.validate()
    if market_price <= 0:
        raise ValueError("market_price must be positive")

    updated = dict(position)
    entry = float(updated["entry_price"])
    quantity = float(updated["quantity"])
    cost_basis = float(updated["cost_basis"])
    if entry <= 0 or quantity <= 0 or cost_basis <= 0:
        raise ValueError("position entry_price, quantity, and cost_basis must be positive")

    previous_high = float(updated.get("highest_price", updated.get("last_price", entry)))
    high = max(previous_high, market_price)
    updated["highest_price"] = high
    updated["last_price"] = market_price

    net_pnl = estimated_net_pnl(updated, market_price, config)
    net_return = net_pnl / cost_basis
    break_even = estimated_break_even_price(updated, config)
    hard_stop = entry * (1 - config.stop_loss_price)
    trailing_armed = high >= break_even
    trailing_stop = max(break_even, high * (1 - config.trailing_distance)) if trailing_armed else None

    reason = None
    if net_return >= config.take_profit_net:
        reason = "take_profit_net"
    elif market_price <= hard_stop:
        reason = "stop_loss"
    elif trailing_armed and market_price <= trailing_stop:
        reason = "trailing_stop"

    updated["trailing_armed"] = trailing_armed
    updated["break_even_price"] = break_even
    updated["trailing_stop_price"] = trailing_stop

    return {
        "position": updated,
        "exit_reason": reason,
        "estimated_net_pnl": net_pnl,
        "estimated_net_return": net_return,
        "break_even_price": break_even,
        "hard_stop_price": hard_stop,
        "trailing_stop_price": trailing_stop,
    }

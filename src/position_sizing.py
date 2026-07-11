from __future__ import annotations

from dataclasses import dataclass

from src.config import Settings


def hard_stop_price(entry_price: float, side: str, hard_stop_distance_pct: float) -> float | None:
    if entry_price <= 0 or hard_stop_distance_pct <= 0:
        return None
    distance = hard_stop_distance_pct / 100
    if side == "sell":
        return entry_price * (1 + distance)
    if side == "buy":
        return entry_price * (1 - distance)
    return None


def second_entry_trigger_price(entry_price: float, side: str, second_order_distance_pct: float) -> float | None:
    if entry_price <= 0 or second_order_distance_pct <= 0:
        return None
    distance = second_order_distance_pct / 100
    if side == "sell":
        return entry_price * (1 + distance)
    if side == "buy":
        return entry_price * (1 - distance)
    return None


@dataclass(frozen=True)
class PositionSize:
    qty: float
    notional: float
    risk_budget: float
    risk_per_share: float
    reason: str

    @property
    def approved(self) -> bool:
        return self.qty > 0 and self.notional > 0


def calculate_position_size(
    *,
    settings: Settings,
    equity: float,
    buying_power: float,
    entry_price: float,
    hard_stop_price: float | None,
    current_symbol_risk_used: float = 0.0,
    portfolio_remaining_notional: float | None = None,
) -> PositionSize:
    if entry_price <= 0:
        return PositionSize(0, 0, 0, 0, "entry price must be positive")
    if settings.require_hard_stop and (hard_stop_price is None or hard_stop_price <= 0):
        return PositionSize(0, 0, 0, 0, "hard stop is required")
    if hard_stop_price is None or hard_stop_price <= 0:
        return PositionSize(0, 0, 0, 0, "hard stop is missing")

    risk_per_share = abs(entry_price - hard_stop_price)
    if risk_per_share <= 0:
        return PositionSize(0, 0, 0, 0, "risk per share must be positive")

    total_risk_budget = equity * (settings.max_loss_per_symbol_equity_pct / 100)
    risk_budget = max(total_risk_budget - current_symbol_risk_used, 0)
    if risk_budget <= 0:
        return PositionSize(0, 0, total_risk_budget, risk_per_share, "symbol risk budget exhausted")

    equity_pct_qty = (equity * (settings.first_order_equity_pct / 100)) / entry_price
    risk_based_qty = risk_budget / risk_per_share
    max_notional_qty = (
        settings.max_notional_per_order / entry_price
        if settings.max_notional_per_order > 0
        else float("inf")
    )
    buying_power_qty = max(buying_power - settings.min_buying_power_reserve, 0) / entry_price
    portfolio_qty = (
        portfolio_remaining_notional / entry_price
        if portfolio_remaining_notional is not None and portfolio_remaining_notional >= 0
        else float("inf")
    )
    qty = max(min(equity_pct_qty, risk_based_qty, max_notional_qty, buying_power_qty, portfolio_qty), 0)
    notional = round(qty * entry_price, 2)
    if qty <= 0 or notional <= 0:
        return PositionSize(0, 0, total_risk_budget, risk_per_share, "position size resolved to zero")
    return PositionSize(round(qty, 6), notional, total_risk_budget, risk_per_share, "approved")

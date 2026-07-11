from __future__ import annotations

from dataclasses import dataclass

from src.strategy import Signal


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str


@dataclass(frozen=True)
class RiskLimits:
    allowed_symbols: list[str]
    max_notional_per_order: float
    min_cash_reserve: float
    can_submit_orders: bool


def evaluate_signal(
    signal: Signal,
    account: dict,
    limits: RiskLimits,
    positions: list[dict] | None = None,
) -> RiskDecision:
    if signal.side == "hold":
        return RiskDecision(False, "hold signal")

    if signal.symbol.upper() not in {symbol.upper() for symbol in limits.allowed_symbols}:
        return RiskDecision(False, f"{signal.symbol} is not in whitelist")

    if signal.notional <= 0 and (signal.qty is None or signal.qty <= 0):
        return RiskDecision(False, "notional or qty must be positive")

    if signal.notional > 0 and limits.max_notional_per_order > 0 and signal.notional > limits.max_notional_per_order:
        return RiskDecision(False, "notional exceeds per-order limit")

    cash = float(account.get("cash", 0))
    buying_power = float(account.get("buying_power", cash) or 0)
    if (
        signal.side == "buy"
        and signal.position_intent != "buy_to_close"
        and cash - signal.notional < limits.min_cash_reserve
    ):
        return RiskDecision(False, "cash reserve would be breached")
    if signal.position_intent in {"buy_to_open", "sell_to_open"} and signal.notional > buying_power:
        return RiskDecision(False, "rejected_insufficient_buying_power")

    matching_position = next(
        (
            position
            for position in positions or []
            if str(position.get("symbol", "")).upper() == signal.symbol.upper()
        ),
        None,
    )
    current_qty = float(matching_position.get("qty", 0)) if matching_position else 0.0

    if signal.position_intent == "buy_to_open" and current_qty < 0:
        return RiskDecision(False, "cannot open a long while a short position already exists")
    if signal.position_intent == "buy_to_open" and current_qty > 0 and not signal.allow_position_add:
        return RiskDecision(False, "cannot open a long while a position already exists")

    if signal.position_intent == "sell_to_open" and current_qty > 0:
        return RiskDecision(False, "cannot open a short while a long position already exists")
    if signal.position_intent == "sell_to_open" and current_qty < 0 and not signal.allow_position_add:
        return RiskDecision(False, "cannot open a short while a position already exists")

    if signal.position_intent == "buy_to_close" and current_qty >= 0:
        return RiskDecision(False, "cannot buy to close without an existing short position")

    if signal.position_intent == "sell_to_close" and current_qty <= 0:
        return RiskDecision(False, "cannot sell to close without an existing long position")

    if signal.side == "sell" and signal.position_intent != "sell_to_open":
        if current_qty <= 0:
            return RiskDecision(False, "cannot sell without an existing long position")

    if not limits.can_submit_orders:
        return RiskDecision(False, "trading disabled or dry-run enabled")

    return RiskDecision(True, "approved")

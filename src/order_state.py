from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


OrderLifecycleState = Literal[
    "SIGNAL_DETECTED",
    "RISK_CHECKING",
    "ENTRY_SUBMITTING",
    "ENTRY_ACCEPTED",
    "ENTRY_PARTIALLY_FILLED",
    "ENTRY_CANCEL_PENDING",
    "ENTRY_CANCELED",
    "ENTRY_FILLED",
    "ENTRY_REJECTED",
    "EXIT_SUBMITTING",
    "EXIT_ACCEPTED",
    "EXIT_PARTIALLY_FILLED",
    "EXIT_CANCEL_PENDING",
    "EXIT_FILLED",
    "CLOSED",
    "MANUAL_REVIEW",
]


TERMINAL_STATES = {
    "ENTRY_CANCELED",
    "ENTRY_FILLED",
    "ENTRY_REJECTED",
    "EXIT_FILLED",
    "CLOSED",
    "MANUAL_REVIEW",
}


@dataclass
class OrderLifecycle:
    client_order_id: str
    symbol: str
    side: str
    intended_qty: float = 0.0
    intended_notional: float = 0.0
    state: OrderLifecycleState = "SIGNAL_DETECTED"
    alpaca_order_id: str | None = None
    filled_qty: float = 0.0
    average_fill_price: float | None = None
    reprice_attempts: int = 0
    cancel_requested_at: datetime | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def remaining_qty(self) -> float:
        if self.intended_qty <= 0:
            return 0.0
        return max(self.intended_qty - self.filled_qty, 0.0)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def cancel_pending(self) -> bool:
        return self.state in {"ENTRY_CANCEL_PENDING", "EXIT_CANCEL_PENDING"}

    @property
    def replacement_allowed(self) -> bool:
        return self.state in {"ENTRY_CANCELED"} and self.remaining_qty > 0

    def mark_submitting(self, *, exit_order: bool = False) -> None:
        self.state = "EXIT_SUBMITTING" if exit_order else "ENTRY_SUBMITTING"
        self.updated_at = datetime.now(timezone.utc)

    def mark_accepted(self, alpaca_order_id: str | None = None, *, exit_order: bool = False) -> None:
        self.alpaca_order_id = alpaca_order_id or self.alpaca_order_id
        self.state = "EXIT_ACCEPTED" if exit_order else "ENTRY_ACCEPTED"
        self.updated_at = datetime.now(timezone.utc)

    def mark_cancel_pending(self, *, exit_order: bool = False) -> None:
        self.state = "EXIT_CANCEL_PENDING" if exit_order else "ENTRY_CANCEL_PENDING"
        self.cancel_requested_at = datetime.now(timezone.utc)
        self.updated_at = self.cancel_requested_at

    def apply_trade_update(self, event: str, order: dict) -> None:
        event = event.lower()
        filled_qty = _as_float(order.get("filled_qty"), self.filled_qty)
        if filled_qty >= self.filled_qty:
            self.filled_qty = filled_qty
        avg_price = _as_float(order.get("filled_avg_price"), 0)
        if avg_price > 0:
            self.average_fill_price = avg_price

        if event in {"new", "accepted"}:
            self.mark_accepted(str(order.get("id") or self.alpaca_order_id or ""))
        elif event == "partial_fill":
            self.state = "ENTRY_PARTIALLY_FILLED" if self.side in {"buy", "sell"} else self.state
        elif event == "fill":
            self.state = "ENTRY_FILLED" if self.state.startswith("ENTRY") else "EXIT_FILLED"
        elif event == "canceled":
            self.state = "ENTRY_CANCELED" if self.state.startswith("ENTRY") else "CLOSED"
        elif event in {"rejected", "expired"}:
            self.state = "ENTRY_REJECTED" if self.state.startswith("ENTRY") else "MANUAL_REVIEW"
        elif event == "replaced":
            self.state = "MANUAL_REVIEW"
        self.updated_at = datetime.now(timezone.utc)


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

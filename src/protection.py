from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.alpaca_client import AlpacaClient, AlpacaError
from src.broker import close_symbol_position, submit_protective_stop_order
from src.config import Settings
from src.journal import TradeJournal
from src.position_sizing import hard_stop_price


PROTECTIVE_STATES = {
    "POSITION_UNPROTECTED",
    "STOP_SUBMITTING",
    "STOP_ACCEPTED",
    "STOP_REJECTED",
    "STOP_TRIGGERED",
    "EMERGENCY_EXIT",
}


@dataclass
class StopOrderRecord:
    client_order_id: str
    qty: float
    stop_price: float
    order_id: str | None = None
    status: str = "submitted"
    submitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class SymbolProtectionState:
    symbol: str
    position_direction: str
    state: str = "POSITION_UNPROTECTED"
    total_filled_qty: float = 0.0
    total_entry_notional: float = 0.0
    protected_qty: float = 0.0
    stop_orders: list[StopOrderRecord] = field(default_factory=list)
    emergency_exit_order: dict[str, Any] | None = None
    last_error: str | None = None

    @property
    def avg_entry_price(self) -> float:
        if self.total_filled_qty <= 0:
            return 0.0
        return self.total_entry_notional / self.total_filled_qty

    @property
    def unprotected_qty(self) -> float:
        return max(self.total_filled_qty - self.protected_qty, 0.0)


class ProtectiveStopManager:
    def __init__(self, *, settings: Settings, client: AlpacaClient, journal: TradeJournal):
        self.settings = settings
        self.client = client
        self.journal = journal
        self.symbols: dict[str, SymbolProtectionState] = {}
        self.entry_filled_qty: dict[str, float] = {}
        self.stop_to_symbol: dict[str, str] = {}
        self.global_halt = False
        self.halt_reason: str | None = None

    def is_protective_stop_id(self, client_order_id: str) -> bool:
        return client_order_id in self.stop_to_symbol or "-STP" in client_order_id

    def on_entry_fill_update(self, *, client_order_id: str, event: str, order: dict[str, Any]) -> None:
        if event not in {"partial_fill", "fill"}:
            return
        symbol = str(order.get("symbol") or "").upper()
        if not symbol:
            return
        filled_qty = _as_float(order.get("filled_qty"), 0.0)
        avg_price = _as_float(order.get("filled_avg_price"), 0.0)
        if filled_qty <= 0 or avg_price <= 0:
            return

        previous_qty = self.entry_filled_qty.get(client_order_id, 0.0)
        new_qty = round(filled_qty - previous_qty, 6)
        if new_qty <= 0:
            return
        self.entry_filled_qty[client_order_id] = filled_qty

        side = str(order.get("side") or "").lower()
        direction = "short" if side == "sell" else "long" if side == "buy" else ""
        if direction not in {"long", "short"}:
            return

        state = self.symbols.get(symbol)
        if state is None:
            state = SymbolProtectionState(symbol=symbol, position_direction=direction)
            self.symbols[symbol] = state
        state.position_direction = direction
        state.total_filled_qty = round(state.total_filled_qty + new_qty, 6)
        state.total_entry_notional = round(state.total_entry_notional + (new_qty * avg_price), 6)
        state.state = "POSITION_UNPROTECTED"

        stop_price = hard_stop_price(avg_price, side, self.settings.hard_stop_distance_pct)
        if stop_price is None:
            self._fail_and_emergency_exit(
                state,
                reason="hard stop price is missing",
                entry_client_order_id=client_order_id,
            )
            return
        self._submit_incremental_stop(
            state,
            qty=new_qty,
            stop_price=stop_price,
            entry_client_order_id=client_order_id,
        )

    def on_stop_trade_update(self, *, client_order_id: str, event: str, order: dict[str, Any]) -> None:
        symbol = self.stop_to_symbol.get(client_order_id) or str(order.get("symbol") or "").upper()
        state = self.symbols.get(symbol)
        if state is None:
            return
        for stop_order in state.stop_orders:
            if stop_order.client_order_id == client_order_id:
                stop_order.status = event
                break

        if event == "rejected":
            state.state = "STOP_REJECTED"
            self._emergency_exit(state, reason="protective stop rejected by broker")
        elif event == "fill":
            state.state = "STOP_TRIGGERED"
        elif event == "canceled":
            state.state = "STOP_CANCELED"

    def _submit_incremental_stop(
        self,
        state: SymbolProtectionState,
        *,
        qty: float,
        stop_price: float,
        entry_client_order_id: str,
    ) -> None:
        state.state = "STOP_SUBMITTING"
        stop_client_order_id = _protective_client_order_id(
            entry_client_order_id=entry_client_order_id,
            symbol=state.symbol,
            sequence=len(state.stop_orders) + 1,
        )
        try:
            result = submit_protective_stop_order(
                self.client,
                symbol=state.symbol,
                position_direction=state.position_direction,
                qty=qty,
                stop_price=stop_price,
                client_order_id=stop_client_order_id,
                dry_run=self.settings.dry_run,
            )
        except AlpacaError as exc:
            self._fail_and_emergency_exit(
                state,
                reason=f"protective stop submit failed: {exc}",
                entry_client_order_id=entry_client_order_id,
            )
            return

        order_id = str((result.get("response") or {}).get("id") or "")
        state.stop_orders.append(
            StopOrderRecord(
                client_order_id=stop_client_order_id,
                qty=qty,
                stop_price=stop_price,
                order_id=order_id or None,
            ),
        )
        self.stop_to_symbol[stop_client_order_id] = state.symbol
        state.protected_qty = round(state.protected_qty + qty, 6)
        state.state = "STOP_ACCEPTED"
        self.journal.record(
            "protective_stop_submitted",
            {
                "symbol": state.symbol,
                "position_direction": state.position_direction,
                "entry_client_order_id": entry_client_order_id,
                "stop_client_order_id": stop_client_order_id,
                "qty": qty,
                "protected_qty": state.protected_qty,
                "total_filled_qty": state.total_filled_qty,
                "stop_price": round(stop_price, 2),
                "result": result,
            },
        )

    def _fail_and_emergency_exit(
        self,
        state: SymbolProtectionState,
        *,
        reason: str,
        entry_client_order_id: str,
    ) -> None:
        state.state = "STOP_REJECTED"
        state.last_error = reason
        self.journal.record(
            "protective_stop_submit_failed",
            {
                "symbol": state.symbol,
                "entry_client_order_id": entry_client_order_id,
                "reason": reason,
                "unprotected_qty": state.unprotected_qty,
            },
        )
        self._emergency_exit(state, reason=reason)

    def _emergency_exit(self, state: SymbolProtectionState, *, reason: str) -> None:
        state.state = "EMERGENCY_EXIT"
        self.global_halt = True
        self.halt_reason = reason
        try:
            result = {"dry_run": True} if self.settings.dry_run else close_symbol_position(self.client, state.symbol)
            state.emergency_exit_order = result
            self.journal.record(
                "emergency_exit_submitted",
                {
                    "symbol": state.symbol,
                    "reason": reason,
                    "protected_qty": state.protected_qty,
                    "total_filled_qty": state.total_filled_qty,
                    "result": result,
                },
            )
        except AlpacaError as exc:
            state.last_error = str(exc)
            self.journal.record(
                "emergency_exit_failed",
                {"symbol": state.symbol, "reason": reason, "error": str(exc)},
            )


def _protective_client_order_id(*, entry_client_order_id: str, symbol: str, sequence: int) -> str:
    candidate = f"{entry_client_order_id}-STP{sequence:02d}"
    if len(candidate) <= 128:
        return candidate
    digest = hashlib.sha1(entry_client_order_id.encode("utf-8")).hexdigest()[:10]
    return f"MR-STP-{symbol.upper()}-{digest}-{sequence:02d}"


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

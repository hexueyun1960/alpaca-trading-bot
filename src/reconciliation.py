from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone

from src.alpaca_client import AlpacaClient, AlpacaError
from src.config import Settings
from src.journal import TradeJournal
from src.strategy import SymbolTradeState, position_direction


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _current_trade_date(clock: dict) -> str:
    raw = str(clock.get("timestamp") or datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw).date().isoformat()
    except ValueError:
        return datetime.now(timezone.utc).date().isoformat()


def _state_for(symbol_states: dict[str, SymbolTradeState], symbol: str, trade_date: str) -> SymbolTradeState:
    state = symbol_states.get(symbol.upper())
    if state is None or state.trade_date != trade_date:
        state = SymbolTradeState(symbol=symbol.upper(), trade_date=trade_date)
        symbol_states[symbol.upper()] = state
    return state


@dataclass(frozen=True)
class ReconciliationResult:
    ok: bool
    reason: str
    account: dict
    positions: list[dict]
    open_orders: list[dict]
    day_orders: list[dict]
    fills: list[dict]
    trading_date: str | None = None

    @property
    def allow_new_entries(self) -> bool:
        return self.ok

    @property
    def allow_risk_exits(self) -> bool:
        return True


def _day_start_iso(trading_date: str) -> str:
    return datetime.combine(datetime.fromisoformat(trading_date).date(), time.min, tzinfo=timezone.utc).isoformat()


def _fills_by_symbol(fills: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for fill in fills:
        symbol = str(fill.get("symbol", "")).upper()
        if not symbol:
            continue
        grouped.setdefault(symbol, []).append(fill)
    return grouped


def reconcile_broker_state(
    *,
    client: AlpacaClient,
    settings: Settings,
    journal: TradeJournal,
    symbol_states: dict[str, SymbolTradeState],
    clock: dict | None = None,
) -> ReconciliationResult:
    try:
        account = client.get_account()
        positions = client.get_positions()
        open_orders = client.get_orders(status="open")
        clock = clock or client.get_clock()
        trading_date = _current_trade_date(clock)
        day_orders = client.get_orders(status="all", after=_day_start_iso(trading_date), limit=500, direction="desc")
        fills = client.get_account_activities("FILL", date=trading_date)
    except AlpacaError as exc:
        result = ReconciliationResult(
            ok=False,
            reason=str(exc),
            account={},
            positions=[],
            open_orders=[],
            day_orders=[],
            fills=[],
        )
        journal.record(
            "reconciliation_failed",
            {
                "reason": result.reason,
                "halt_on_failure": settings.halt_on_reconciliation_failure,
                "allow_new_entries": False,
                "allow_risk_exits": True,
            },
        )
        return result

    account_equity = _as_float(account.get("equity"))
    fills_for_symbol = _fills_by_symbol(fills)
    symbols = {
        *(str(position.get("symbol", "")).upper() for position in positions),
        *(str(order.get("symbol", "")).upper() for order in open_orders),
        *fills_for_symbol.keys(),
    }
    symbols = {symbol for symbol in symbols if symbol}

    for symbol in symbols:
        state = _state_for(symbol_states, symbol, trading_date)
        broker_position = next(
            (position for position in positions if str(position.get("symbol", "")).upper() == symbol),
            None,
        )
        symbol_orders = [order for order in open_orders if str(order.get("symbol", "")).upper() == symbol]
        direction = position_direction(broker_position)
        symbol_fills = fills_for_symbol.get(symbol, [])

        if direction:
            state.position_direction = direction
            state.state = "SECOND_ORDER_PENDING" if symbol_orders else "POSITION_ACTIVE"
            state.closed_for_day = False
            state.entries_filled = max(state.entries_filled, 1)
        elif symbol_orders:
            state.state = "ENTRY_SUBMITTED"
            state.closed_for_day = False
        elif symbol_fills:
            state.closed_for_day = True
            state.state = "CLOSED_FOR_DAY"

        state.metadata["reconciled_at"] = datetime.now(timezone.utc).isoformat()
        state.metadata["broker_open_order_count"] = len(symbol_orders)
        state.metadata["broker_fill_count_today"] = len(symbol_fills)

    result = ReconciliationResult(
        ok=True,
        reason="reconciled",
        account=account,
        positions=positions,
        open_orders=open_orders,
        day_orders=day_orders,
        fills=fills,
        trading_date=trading_date,
    )
    journal.record(
        "reconciliation_succeeded",
        {
            "trading_date": trading_date,
            "account_equity": account_equity,
            "cash": account.get("cash"),
            "buying_power": account.get("buying_power"),
            "daytrading_buying_power": account.get("daytrading_buying_power"),
            "position_count": len(positions),
            "open_order_count": len(open_orders),
            "day_order_count": len(day_orders),
            "fill_count": len(fills),
            "symbols": sorted(symbols),
        },
    )
    return result

from __future__ import annotations

import time
from collections.abc import Callable

from src.alpaca_client import AlpacaClient
from src.strategy import Signal


TERMINAL_ORDER_STATUSES = {
    "filled",
    "canceled",
    "expired",
    "rejected",
    "replaced",
    "done_for_day",
}


def build_order(signal: Signal) -> dict:
    if signal.side not in {"buy", "sell"}:
        raise ValueError("Only buy and sell signals can become orders.")
    if signal.order_type == "limit" and signal.limit_price is None:
        raise ValueError("Limit orders require a limit price.")

    order = {
        "symbol": signal.symbol,
        "side": signal.side,
        "type": signal.order_type,
        "time_in_force": "day",
        "extended_hours": False,
    }
    if signal.order_type == "limit":
        order["limit_price"] = str(round(signal.limit_price or 0, 2))
    if signal.position_intent:
        order["position_intent"] = signal.position_intent
    if signal.qty is not None:
        order["qty"] = str(round(signal.qty, 6))
    else:
        order["notional"] = str(round(signal.notional, 2))
    return order


def build_market_order(signal: Signal) -> dict:
    return build_order(signal)


def wait_for_order_status(
    client: AlpacaClient,
    order_id: str,
    *,
    max_attempts: int = 3,
    delay_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    latest = {}
    for attempt in range(max_attempts):
        latest = client.get_order(order_id)
        status = str(latest.get("status", "")).lower()
        if status in TERMINAL_ORDER_STATUSES:
            break
        if attempt < max_attempts - 1:
            sleep(delay_seconds)
    return latest


def submit_or_preview_order(
    client: AlpacaClient,
    signal: Signal,
    *,
    dry_run: bool,
    wait_for_status: bool = False,
    status_attempts: int = 3,
    status_delay_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    order = build_order(signal)
    if dry_run:
        return {"submitted": False, "dry_run": True, "order": order}

    response = client.submit_order(order)
    result = {
        "submitted": True,
        "dry_run": False,
        "order": order,
        "response": response,
    }

    order_id = response.get("id")
    if wait_for_status and order_id:
        result["latest_status"] = wait_for_order_status(
            client,
            str(order_id),
            max_attempts=status_attempts,
            delay_seconds=status_delay_seconds,
            sleep=sleep,
        )

    return result


def cancel_symbol_orders(client: AlpacaClient, symbol: str, open_orders: list[dict]) -> list[dict]:
    results = []
    for order in open_orders:
        if str(order.get("symbol", "")).upper() != symbol.upper():
            continue
        order_id = order.get("id")
        if not order_id:
            continue
        results.append({"order_id": order_id, "response": client.cancel_order(str(order_id))})
    return results


def close_symbol_position(client: AlpacaClient, symbol: str) -> dict:
    return client.close_position(symbol)

from __future__ import annotations

from src.alpaca_client import AlpacaClient
from src.strategy import Signal


def build_market_order(signal: Signal) -> dict:
    if signal.side not in {"buy", "sell"}:
        raise ValueError("Only buy and sell signals can become orders.")

    order = {
        "symbol": signal.symbol,
        "side": signal.side,
        "type": "market",
        "time_in_force": "day",
        "extended_hours": False,
    }
    if signal.position_intent:
        order["position_intent"] = signal.position_intent
    if signal.qty is not None:
        order["qty"] = str(round(signal.qty, 6))
    else:
        order["notional"] = str(round(signal.notional, 2))
    return order


def submit_or_preview_order(client: AlpacaClient, signal: Signal, *, dry_run: bool) -> dict:
    order = build_market_order(signal)
    if dry_run:
        return {"dry_run": True, "order": order}
    return client.submit_order(order)

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from src.alpaca_client import AlpacaClient, AlpacaError
from src.bot import _asset_eligible_for_dynamic_universe, _asset_symbol, _chunks, build_client
from src.broker import cancel_symbol_orders, submit_or_preview_order
from src.config import Settings, load_settings
from src.execution import acquire_execution_context
from src.journal import TradeJournal
from src.order_state import OrderLifecycle
from src.protection import ProtectiveStopManager
from src.realtime_engine import RealtimeSignalEngine
from src.reconciliation import reconcile_broker_state
from src.risk import RiskLimits, evaluate_signal
from src.strategy import Signal
from src.strategy import SymbolTradeState, mark_first_fill, second_entry_limit_signal


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TERMINAL_ORDER_EVENTS = {"fill", "canceled", "expired", "rejected"}


def _loads_message(message: str | bytes) -> list[dict[str, Any]]:
    if isinstance(message, bytes):
        message = message.decode("utf-8", errors="replace")
    payload = json.loads(message)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def _market_stream_url(settings: Settings) -> str:
    return f"wss://stream.data.alpaca.markets/v2/{settings.market_data_feed}"


def _trading_stream_url(settings: Settings) -> str:
    if settings.is_paper_base_url:
        return "wss://paper-api.alpaca.markets/stream"
    return "wss://api.alpaca.markets/stream"


def _session_open_from_daily_bars(bars: list[dict[str, Any]], now: datetime | None = None) -> float | None:
    if not bars:
        return None
    current = now or datetime.now(timezone.utc)
    parsed: list[tuple[datetime, float]] = []
    for bar in bars:
        open_price = _as_float(bar.get("o", bar.get("open")), 0.0)
        if open_price <= 0:
            continue
        timestamp = _parse_timestamp(bar.get("t", bar.get("timestamp")))
        parsed.append((timestamp, open_price))
    if not parsed:
        return None
    parsed.sort(key=lambda item: item[0])
    today_open = [open_price for timestamp, open_price in parsed if timestamp.date() == current.date()]
    if today_open:
        return today_open[-1]
    return None


def _load_rankable_universe(client: AlpacaClient, settings: Settings) -> dict[str, float]:
    assets = client.get_assets(status="active", asset_class="us_equity")
    symbols = sorted(
        {
            _asset_symbol(asset)
            for asset in assets
            if _asset_eligible_for_dynamic_universe(asset, settings)
        },
    )
    if settings.universe_max_symbols > 0:
        symbols = symbols[: settings.universe_max_symbols]
    session_opens: dict[str, float] = {}
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=10)
    for chunk in _chunks(symbols, settings.universe_chunk_size):
        bars_by_symbol = client.get_stock_bars_multi(
            chunk,
            timeframe="1Day",
            limit=max(2 * len(chunk), 1),
            start=start.isoformat(),
            end=end.isoformat(),
            feed=settings.market_data_feed,
        )
        for symbol in chunk:
            session_open = _session_open_from_daily_bars(bars_by_symbol.get(symbol, []), now=end)
            if session_open and session_open > 0:
                session_opens[symbol] = session_open
    return session_opens


def _parse_timestamp(value: object) -> datetime:
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class StreamRuntime:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = build_client(settings)
        self.journal = TradeJournal(settings.journal_path)
        self.shadow_journal = TradeJournal(settings.shadow_journal_path)
        self.execution_context = acquire_execution_context(settings, "stream")
        self.engine = RealtimeSignalEngine(settings)
        try:
            session_opens = _load_rankable_universe(self.client, settings)
            self.engine.set_session_opens(session_opens)
            self.journal.record(
                "stream_rankable_universe_loaded",
                {
                    "symbols_with_session_open": len(session_opens),
                    "filters": "active+tradable+etb",
                    "ranking_formula": "current_price / session_open - 1",
                    "top_gainers": settings.top_gainers_count,
                    "top_losers": settings.top_losers_count,
                    "retention_seconds": settings.candidate_retention_seconds,
                },
            )
        except AlpacaError as exc:
            self.journal.record(
                "stream_rankable_universe_error",
                {"reason": str(exc), "filters": "active+tradable+etb"},
            )
        self.symbol_states: dict[str, SymbolTradeState] = {}
        self.second_entry_symbols: set[str] = set()
        self.lock = threading.Lock()
        self.market_connected = False
        self.trading_connected = False
        self.pending_orders: dict[str, dict[str, Any]] = {}
        self.order_lifecycles: dict[str, OrderLifecycle] = {}
        self.protection = ProtectiveStopManager(settings=settings, client=self.client, journal=self.journal)
        self.latest_quote_subscription: set[str] = set()
        self.reconciliation_ok = False
        reconciliation = reconcile_broker_state(
            client=self.client,
            settings=settings,
            journal=self.journal,
            symbol_states=self.symbol_states,
        )
        self.reconciliation_ok = reconciliation.ok

    @property
    def streams_ready(self) -> bool:
        return self.market_connected and self.trading_connected

    @property
    def can_open_new_positions(self) -> bool:
        return (
            self.streams_ready
            and self.reconciliation_ok
            and self.execution_context.can_open_new_positions
            and self.settings.enable_stream_strategy
            and not self.protection.global_halt
        )

    def _risk_limits(self, allowed_symbols: list[str]) -> RiskLimits:
        return RiskLimits(
            allowed_symbols=allowed_symbols,
            max_notional_per_order=self.settings.max_notional_per_order,
            min_cash_reserve=self.settings.min_cash_reserve,
            can_submit_orders=(
                self.execution_context.can_submit_orders
                and self.settings.enable_stream_strategy
                and not self.protection.global_halt
            ),
        )

    def _submit_signal(self, signal: Signal) -> None:
        with self.lock:
            if signal.client_order_id and signal.client_order_id in self.pending_orders:
                self.journal.record(
                    "realtime_order_duplicate_suppressed",
                    {"symbol": signal.symbol, "client_order_id": signal.client_order_id},
                )
                return
            self.engine.lock_order(signal)
            if signal.client_order_id:
                lifecycle = OrderLifecycle(
                    client_order_id=signal.client_order_id,
                    symbol=signal.symbol,
                    side=signal.side,
                    intended_qty=signal.qty or 0.0,
                    intended_notional=signal.notional,
                    state="RISK_CHECKING",
                )
                self.order_lifecycles[signal.client_order_id] = lifecycle

        try:
            account = self.client.get_account()
            positions = self.client.get_positions()
            open_orders = self.client.get_orders(status="open")
        except AlpacaError as exc:
            self.engine.release_order(signal.symbol)
            self.journal.record("realtime_context_error", {"symbol": signal.symbol, "error": str(exc)})
            return

        if signal.position_intent in {"buy_to_open", "sell_to_open"} and len(positions) >= self.settings.max_open_positions:
            self.engine.release_order(signal.symbol)
            self.journal.record(
                "realtime_risk_rejected",
                {"symbol": signal.symbol, "reason": "max_open_positions reached"},
            )
            return
        if signal.position_intent in {"buy_to_close", "sell_to_close"}:
            try:
                cancel_results = cancel_symbol_orders(self.client, signal.symbol, open_orders)
                if cancel_results:
                    self.journal.record(
                        "realtime_exit_canceled_open_orders",
                        {"symbol": signal.symbol, "results": cancel_results},
                    )
            except AlpacaError as exc:
                self.journal.record(
                    "realtime_exit_cancel_error",
                    {"symbol": signal.symbol, "error": str(exc)},
                )
                return

        entry_order_intents = {"buy_to_open", "sell_to_open"}
        if any(
            str(order.get("symbol", "")).upper() == signal.symbol
            and str(order.get("position_intent") or "").lower() in entry_order_intents
            for order in open_orders
        ):
            self.engine.release_order(signal.symbol)
            self.journal.record(
                "realtime_risk_rejected",
                {"symbol": signal.symbol, "reason": "symbol has pending open order"},
            )
            return

        decision = evaluate_signal(
            signal,
            account,
            self._risk_limits(self.engine.active_candidates()),
            positions=positions,
        )
        self.journal.record(
            "realtime_signal",
            {
                "symbol": signal.symbol,
                "signal": signal,
                "risk_decision": decision,
                "websocket_connected": self.can_open_new_positions,
            },
        )
        if self.execution_context.effective_mode == "shadow" or not self.can_open_new_positions:
            self.shadow_journal.record(
                "shadow_theoretical_order",
                {
                    "symbol": signal.symbol,
                    "signal": signal,
                    "risk_decision": decision,
                    "execution_context": self.execution_context.payload(),
                },
            )
            self.engine.release_order(signal.symbol)
            return

        if not decision.approved:
            self.engine.release_order(signal.symbol)
            return

        try:
            if signal.client_order_id in self.order_lifecycles:
                self.order_lifecycles[signal.client_order_id].mark_submitting()
            result = submit_or_preview_order(
                self.client,
                signal,
                dry_run=self.settings.dry_run,
                wait_for_status=False,
            )
        except AlpacaError as exc:
            self.engine.release_order(signal.symbol)
            self.journal.record(
                "realtime_order_error",
                {"symbol": signal.symbol, "signal": signal, "error": str(exc)},
            )
            return

        with self.lock:
            if signal.client_order_id:
                lifecycle = self.order_lifecycles.get(signal.client_order_id)
                if lifecycle:
                    lifecycle.mark_accepted(str((result.get("response") or {}).get("id") or ""))
                self.pending_orders[signal.client_order_id] = {
                    "symbol": signal.symbol,
                    "submitted_at": datetime.now(timezone.utc),
                    "order_id": (result.get("response") or {}).get("id"),
                    "attempts": 1,
                }
        self.journal.record("realtime_order_submitted", {"symbol": signal.symbol, "result": result})

    def _position_for_symbol(self, positions: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
        return next(
            (
                position
                for position in positions
                if str(position.get("symbol", "")).upper() == symbol.upper()
            ),
            None,
        )

    def _maybe_submit_second_entry(self, *, symbol: str, account: dict[str, Any], positions: list[dict[str, Any]]) -> bool:
        state = self.symbol_states.get(symbol)
        latest = self.engine.state_for(symbol).latest_bar
        if state is None or latest is None or symbol in self.second_entry_symbols:
            return False
        signal = second_entry_limit_signal(
            state,
            second_order_distance_pct=self.settings.second_order_distance_pct,
            fixed_entry_notional=self.settings.fixed_entry_notional,
            current_price=latest.close,
            max_entries_per_cycle=self.settings.max_entries_per_cycle,
        )
        if signal.side == "hold":
            return False
        qty = int(self.settings.fixed_entry_notional / latest.close) if latest.close > 0 else 0
        if qty <= 0:
            self.journal.record("realtime_second_entry_rejected", {"symbol": symbol, "reason": "fixed notional too small"})
            return False
        signal = Signal(
            **{
                **signal.__dict__,
                "qty": float(qty),
                "notional": round(qty * latest.close, 2),
                "client_order_id": signal.client_order_id
                or f"MR2-{symbol}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            }
        )
        self._submit_signal(signal)
        self.second_entry_symbols.add(symbol)
        return True

    def maybe_submit_for_symbol(self, symbol: str) -> None:
        if not self.streams_ready or not self.reconciliation_ok:
            return
        try:
            account = self.client.get_account()
            positions = self.client.get_positions()
        except AlpacaError as exc:
            self.journal.record("realtime_context_error", {"symbol": symbol, "error": str(exc)})
            return
        position = self._position_for_symbol(positions, symbol)
        if position is not None:
            exit_decision = self.engine.build_exit_signal(symbol, position)
            if exit_decision.signal is not None:
                self._submit_signal(exit_decision.signal)
                return
            if self.settings.enable_second_entry and self._maybe_submit_second_entry(
                symbol=symbol,
                account=account,
                positions=positions,
            ):
                return
        equity = float(account.get("equity", 0) or 0)
        buying_power = float(account.get("buying_power", equity) or 0)
        decision = self.engine.build_entry_signal(symbol, account_equity=equity, buying_power=buying_power)
        if decision.signal is None:
            return
        self._submit_signal(decision.signal)

    def cancel_timed_out_orders(self) -> None:
        now = datetime.now(timezone.utc)
        expired: list[tuple[str, dict[str, Any]]] = []
        with self.lock:
            for client_order_id, order in self.pending_orders.items():
                submitted_at = order.get("submitted_at")
                if isinstance(submitted_at, datetime) and submitted_at + timedelta(
                    seconds=self.settings.order_timeout_seconds
                ) <= now:
                    expired.append((client_order_id, dict(order)))
        for client_order_id, order in expired:
            order_id = order.get("order_id")
            symbol = str(order.get("symbol", "")).upper()
            try:
                lifecycle = self.order_lifecycles.get(client_order_id)
                if lifecycle:
                    lifecycle.mark_cancel_pending()
                if order_id:
                    response = self.client.cancel_order(str(order_id))
                else:
                    response = {}
                self.journal.record(
                    "realtime_order_timeout_cancel",
                    {"symbol": symbol, "client_order_id": client_order_id, "response": response},
                )
            except AlpacaError as exc:
                self.journal.record(
                    "realtime_order_cancel_error",
                    {"symbol": symbol, "client_order_id": client_order_id, "error": str(exc)},
                )
            finally:
                with self.lock:
                    self.pending_orders.pop(client_order_id, None)
                self.engine.release_order(symbol)

    def handle_trade_update(self, message: dict[str, Any]) -> None:
        event = str(message.get("event", "")).lower()
        order = message.get("order") if isinstance(message.get("order"), dict) else {}
        client_order_id = str(order.get("client_order_id") or "")
        symbol = str(order.get("symbol") or "").upper()
        if client_order_id:
            lifecycle = self.order_lifecycles.get(client_order_id)
            if lifecycle is None:
                lifecycle = OrderLifecycle(
                    client_order_id=client_order_id,
                    symbol=symbol,
                    side=str(order.get("side") or ""),
                    intended_qty=float(order.get("qty") or 0),
                    intended_notional=float(order.get("notional") or 0),
                    alpaca_order_id=str(order.get("id") or ""),
                )
                self.order_lifecycles[client_order_id] = lifecycle
            lifecycle.apply_trade_update(event, order)
        self.journal.record("trade_update", {"event": event, "order": order})
        if client_order_id and self.protection.is_protective_stop_id(client_order_id):
            self.protection.on_stop_trade_update(
                client_order_id=client_order_id,
                event=event,
                order=order,
            )
        elif str(order.get("position_intent") or "").lower() in {"buy_to_open", "sell_to_open"}:
            self.protection.on_entry_fill_update(
                client_order_id=client_order_id,
                event=event,
                order=order,
            )
            filled_qty = _as_float(order.get("filled_qty"), 0.0)
            filled_avg_price = _as_float(order.get("filled_avg_price"), 0.0)
            if event in {"partial_fill", "fill"} and symbol and filled_qty > 0 and filled_avg_price > 0:
                state = self.symbol_states.get(symbol)
                if state is None:
                    state = SymbolTradeState(symbol=symbol)
                    self.symbol_states[symbol] = state
                if state.entries_filled < 1:
                    direction = "short" if str(order.get("side") or "").lower() == "sell" else "long"
                    mark_first_fill(
                        state,
                        fill_price=filled_avg_price,
                        filled_qty=filled_qty,
                        filled_notional=round(filled_qty * filled_avg_price, 2),
                        position_direction_value=direction,
                        filled_at=str(order.get("filled_at") or datetime.now(timezone.utc).isoformat()),
                    )
                    state.entries_submitted = max(state.entries_submitted, 1)
                elif client_order_id.startswith("MR2-"):
                    state.entries_filled = max(state.entries_filled, 2)
                    state.entries_submitted = max(state.entries_submitted, 2)
                    state.second_filled_qty = filled_qty
                    state.second_filled_notional = round(filled_qty * filled_avg_price, 2)
                    state.state = "POSITION_ACTIVE"
        if event in TERMINAL_ORDER_EVENTS and client_order_id:
            with self.lock:
                self.pending_orders.pop(client_order_id, None)
            if symbol:
                self.engine.release_order(symbol)


class MarketDataStream:
    def __init__(self, runtime: StreamRuntime):
        self.runtime = runtime
        self.ws = None

    def subscribe(self) -> None:
        if self.ws is None:
            return
        bars = ["*"] if self.runtime.settings.websocket_broad_bars else self.runtime.settings.symbols
        high_priority = self.runtime.engine.high_priority_symbols()
        if set(high_priority) != self.runtime.latest_quote_subscription:
            self.runtime.latest_quote_subscription = set(high_priority)
        payload = {
            "action": "subscribe",
            "bars": bars,
            "quotes": high_priority,
            "trades": high_priority,
        }
        self.ws.send(json.dumps(payload))

    def on_open(self, ws) -> None:
        self.ws = ws
        ws.send(
            json.dumps(
                {
                    "action": "auth",
                    "key": self.runtime.settings.api_key_id,
                    "secret": self.runtime.settings.api_secret_key,
                },
            ),
        )

    def on_message(self, _ws, message) -> None:
        for item in _loads_message(message):
            msg_type = item.get("T")
            if msg_type == "success" and item.get("msg") == "authenticated":
                self.runtime.market_connected = True
                self.runtime.engine.websocket_connected = self.runtime.streams_ready
                self.subscribe()
                self.runtime.journal.record("market_stream_authenticated", {"url": _market_stream_url(self.runtime.settings)})
                continue
            if msg_type == "subscription":
                self.runtime.journal.record("market_stream_subscription", item)
                continue
            if msg_type == "b":
                candidates = self.runtime.engine.on_bar(item)
                if candidates:
                    self.runtime.journal.record(
                        "realtime_candidate_rankings_updated",
                        {
                            "updated_symbol": str(item.get("S", "")).upper(),
                            "ranked_symbols": candidates,
                            "candidates": self.runtime.engine.active_candidates(),
                        },
                    )
                    self.subscribe()
                self.runtime.cancel_timed_out_orders()
                continue
            if msg_type == "q":
                self.runtime.engine.on_quote(item)
                self.runtime.maybe_submit_for_symbol(str(item.get("S", "")).upper())
                self.runtime.cancel_timed_out_orders()

    def on_error(self, _ws, error) -> None:
        self.runtime.market_connected = False
        self.runtime.engine.websocket_connected = False
        self.runtime.journal.record("market_stream_error", {"error": str(error)})

    def on_close(self, _ws, _status_code, _message) -> None:
        self.runtime.market_connected = False
        self.runtime.engine.websocket_connected = False
        self.runtime.journal.record("market_stream_closed", {})

    def run_forever(self) -> None:
        import websocket

        self.ws = websocket.WebSocketApp(
            _market_stream_url(self.runtime.settings),
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        self.ws.run_forever(ping_interval=20, ping_timeout=10)


class TradingUpdateStream:
    def __init__(self, runtime: StreamRuntime):
        self.runtime = runtime
        self.ws = None

    def on_open(self, ws) -> None:
        self.ws = ws
        ws.send(
            json.dumps(
                {
                    "action": "auth",
                    "key": self.runtime.settings.api_key_id,
                    "secret": self.runtime.settings.api_secret_key,
                },
            ),
        )

    def on_message(self, ws, message) -> None:
        for item in _loads_message(message):
            stream = item.get("stream")
            data = item.get("data")
            if stream == "authorization" and isinstance(data, dict) and data.get("status") == "authorized":
                self.runtime.trading_connected = True
                self.runtime.engine.websocket_connected = self.runtime.streams_ready
                ws.send(json.dumps({"action": "listen", "data": {"streams": ["trade_updates"]}}))
                self.runtime.journal.record("trading_stream_authenticated", {"url": _trading_stream_url(self.runtime.settings)})
                continue
            if stream == "trade_updates" and isinstance(data, dict):
                self.runtime.handle_trade_update(data)

    def on_error(self, _ws, error) -> None:
        self.runtime.trading_connected = False
        self.runtime.engine.websocket_connected = False
        self.runtime.journal.record("trading_stream_error", {"error": str(error)})

    def on_close(self, _ws, _status_code, _message) -> None:
        self.runtime.trading_connected = False
        self.runtime.engine.websocket_connected = False
        self.runtime.journal.record("trading_stream_closed", {})

    def run_forever(self) -> None:
        import websocket

        self.ws = websocket.WebSocketApp(
            _trading_stream_url(self.runtime.settings),
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        self.ws.run_forever(ping_interval=20, ping_timeout=10)


def run_stream_monitor(settings: Settings | None = None) -> int:
    settings = settings or load_settings()
    runtime = StreamRuntime(settings)
    runtime.journal.record(
        "stream_monitor_started",
        {
            "market_stream_url": _market_stream_url(settings),
            "trading_stream_url": _trading_stream_url(settings),
            "broad_bars": settings.websocket_broad_bars,
            "dry_run": settings.dry_run,
            "enable_trading": settings.enable_trading,
        },
    )

    trading_thread = threading.Thread(target=TradingUpdateStream(runtime).run_forever, daemon=True)
    trading_thread.start()
    try:
        MarketDataStream(runtime).run_forever()
    except KeyboardInterrupt:
        runtime.journal.record("stream_monitor_stopped", {"reason": "keyboard_interrupt"})
        return 0
    finally:
        time.sleep(0.1)
    return 1


if __name__ == "__main__":
    raise SystemExit(run_stream_monitor())

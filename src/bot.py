from __future__ import annotations

import logging
import math
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.alpaca_client import AlpacaClient, AlpacaError
from src.broker import cancel_symbol_orders, submit_or_preview_order
from src.config import Settings, load_settings
from src.execution import ExecutionContext, acquire_execution_context
from src.data import load_recent_bars, load_session_bars
from src.journal import TradeJournal
from src.risk import RiskLimits, evaluate_signal
from src.strategy import (
    Signal,
    SymbolTradeState,
    calculate_vwap_deviation_pct,
    evaluate_vwap_mean_reversion_asset,
    latest_price_and_session_vwap,
    mark_first_fill,
    position_direction,
    second_entry_limit_signal,
    vwap_mean_reversion_entry_signal,
    vwap_mean_reversion_exit_signal,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

try:
    NY_TZ = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:
    NY_TZ = None
SYMBOL_STATES: dict[str, SymbolTradeState] = {}
UNIVERSE_CACHE: dict[str, object] = {}
MIN_1MIN_SESSION_BAR_LIMIT = 390


def build_client(settings: Settings) -> AlpacaClient:
    return AlpacaClient(
        api_key_id=settings.api_key_id,
        api_secret_key=settings.api_secret_key,
        base_url=settings.base_url,
        data_url=settings.data_url,
    )


def _position_for_symbol(positions: list[dict], symbol: str) -> dict | None:
    return next(
        (
            position
            for position in positions
            if str(position.get("symbol", "")).upper() == symbol.upper()
        ),
        None,
    )


def _orders_for_symbol(open_orders: list[dict], symbol: str) -> list[dict]:
    return [order for order in open_orders if str(order.get("symbol", "")).upper() == symbol.upper()]


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _chunks(values: list[str], size: int) -> list[list[str]]:
    chunk_size = max(size, 1)
    return [values[index : index + chunk_size] for index in range(0, len(values), chunk_size)]


def _nth_sunday(year: int, month: int, nth: int) -> datetime:
    day = datetime(year, month, 1, tzinfo=timezone.utc)
    days_until_sunday = (6 - day.weekday()) % 7
    return day + timedelta(days=days_until_sunday + 7 * (nth - 1))


def _eastern_offset_for_utc(dt_utc: datetime) -> timezone:
    dst_start = _nth_sunday(dt_utc.year, 3, 2).replace(hour=7)
    dst_end = _nth_sunday(dt_utc.year, 11, 1).replace(hour=6)
    return timezone(timedelta(hours=-4 if dst_start <= dt_utc < dst_end else -5))


def _to_new_york(dt: datetime) -> datetime:
    if NY_TZ is not None:
        return dt.astimezone(NY_TZ)
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.astimezone(_eastern_offset_for_utc(dt_utc))


def _now_new_york() -> datetime:
    return _to_new_york(datetime.now(timezone.utc))


def _clock_timestamp(clock: dict) -> datetime:
    raw = clock.get("timestamp")
    if raw:
        value = str(raw).replace("Z", "+00:00")
        try:
            return _to_new_york(datetime.fromisoformat(value))
        except ValueError:
            pass
    return _now_new_york()


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def _current_trade_date(clock: dict) -> str:
    return _clock_timestamp(clock).date().isoformat()


def _session_flags(settings: Settings, clock: dict) -> dict[str, bool]:
    now = _clock_timestamp(clock).time()
    regular = bool(clock.get("is_open")) and time(9, 30) <= now < time(16, 0)
    return {
        "regular": regular,
        "allow_new_entries": regular and now < _parse_hhmm(settings.no_new_entries_after),
        "force_flatten": regular and now >= _parse_hhmm(settings.force_flatten_time),
        "final_position_check": regular and now >= _parse_hhmm(settings.final_position_check_time),
    }


def _state_for(symbol: str, trade_date: str) -> SymbolTradeState:
    state = SYMBOL_STATES.get(symbol.upper())
    if state is None or state.trade_date != trade_date:
        state = SymbolTradeState(symbol=symbol.upper(), trade_date=trade_date)
        SYMBOL_STATES[symbol.upper()] = state
    return state


def _journal_payload(
    *,
    symbol: str,
    event_type: str,
    state: SymbolTradeState | None = None,
    signal: Signal | None = None,
    account_equity: float | None = None,
    reason: str | None = None,
    extra: dict | None = None,
) -> dict:
    payload = {
        "symbol": symbol,
        "event_type": event_type,
        "price": signal.price if signal else None,
        "vwap": signal.vwap if signal else None,
        "vwap_deviation_pct": signal.vwap_deviation_pct if signal else None,
        "position_direction": signal.position_direction if signal else state.position_direction if state else None,
        "qty": signal.qty if signal else None,
        "notional": signal.notional if signal else None,
        "order_type": signal.order_type if signal else None,
        "limit_price": signal.limit_price if signal else None,
        "account_equity": account_equity,
        "state": state.state if state else None,
        "reason": reason or (signal.reason if signal else None),
    }
    if extra:
        payload.update(extra)
    return payload


def _quote_prices(quote: dict) -> tuple[float | None, float | None]:
    bid = quote.get("bp", quote.get("bid_price"))
    ask = quote.get("ap", quote.get("ask_price"))
    bid_value = _as_float(bid, 0)
    ask_value = _as_float(ask, 0)
    return (bid_value or None), (ask_value or None)


def _rejection_event_type(reasons: list[str]) -> str:
    if any("easy to borrow" in reason for reason in reasons):
        return "etb_rejected"
    if any("spread_pct" in reason or "bid/ask" in reason for reason in reasons):
        return "spread_rejected"
    return "universe_rejected"


def _asset_symbol(asset: dict) -> str:
    return str(asset.get("symbol", "")).upper()


def _asset_class(asset: dict) -> str:
    return str(asset.get("class", asset.get("asset_class", ""))).lower()


def _asset_exchange(asset: dict) -> str:
    return str(asset.get("exchange", "")).upper()


def _borrow_allowed(asset: dict, settings: Settings) -> bool:
    borrow_status = asset.get("borrow_status")
    if borrow_status is not None and str(borrow_status).strip():
        return str(borrow_status).strip().lower() in set(settings.allowed_borrow_statuses)
    if "easy_to_borrow" in asset:
        return _as_bool(asset.get("easy_to_borrow"))
    return not settings.require_etb


def _asset_eligible_for_dynamic_universe(asset: dict, settings: Settings) -> bool:
    if str(asset.get("status", "")).lower() != "active":
        return False
    asset_class = _asset_class(asset)
    if asset_class and asset_class != "us_equity":
        return False
    if settings.require_tradable and not _as_bool(asset.get("tradable")):
        return False
    if settings.require_etb and not _borrow_allowed(asset, settings):
        return False
    return bool(_asset_symbol(asset))


def _latest_daily_close(bars: list[dict]) -> float | None:
    for bar in reversed(bars):
        close = _as_float(bar.get("c", bar.get("close")), 0)
        if close > 0:
            return close
    return None


def _average_daily_dollar_volume(bars: list[dict]) -> float | None:
    values = []
    for bar in bars:
        close = _as_float(bar.get("c", bar.get("close")), 0)
        volume = _as_float(bar.get("v", bar.get("volume")), 0)
        if close > 0 and volume > 0:
            values.append(close * volume)
    if not values:
        return None
    return sum(values) / len(values)


def _universe_cache_key(settings: Settings) -> tuple:
    return (
        settings.universe_max_symbols,
        settings.require_etb,
        settings.require_tradable,
        tuple(settings.allowed_borrow_statuses),
    )


def _cached_universe_symbols(settings: Settings) -> list[str] | None:
    expires_at = UNIVERSE_CACHE.get("expires_at")
    key = UNIVERSE_CACHE.get("key")
    symbols = UNIVERSE_CACHE.get("symbols")
    if not isinstance(expires_at, datetime) or key != _universe_cache_key(settings):
        return None
    if expires_at <= datetime.now(timezone.utc):
        return None
    return list(symbols) if isinstance(symbols, list) else None


def _cache_universe_symbols(settings: Settings, symbols: list[str]) -> None:
    UNIVERSE_CACHE.clear()
    UNIVERSE_CACHE.update(
        {
            "key": _universe_cache_key(settings),
            "symbols": list(symbols),
            "expires_at": datetime.now(timezone.utc)
            + timedelta(seconds=max(settings.universe_refresh_interval_seconds, 0)),
        },
    )


def _prefilter_symbols_by_daily_bars(
    *,
    client: AlpacaClient,
    symbols: list[str],
    settings: Settings,
) -> tuple[list[str], dict[str, dict[str, float | None]]]:
    if not symbols:
        return [], {}

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=60)
    selected: list[tuple[str, float]] = []
    metrics: dict[str, dict[str, float | None]] = {}

    for chunk in _chunks(symbols, settings.universe_chunk_size):
        bars_by_symbol = client.get_stock_bars_multi(
            chunk,
            timeframe="1Day",
            limit=max(30 * len(chunk), 1),
            start=start.isoformat(),
            end=end.isoformat(),
        )
        for symbol in chunk:
            bars = bars_by_symbol.get(symbol, [])
            price = _latest_daily_close(bars)
            avg_volume = _average_daily_volume(bars)
            avg_dollar_volume = _average_daily_dollar_volume(bars)
            metrics[symbol] = {
                "price": price,
                "avg_daily_volume_30d": avg_volume,
                "avg_daily_dollar_volume_30d": avg_dollar_volume,
            }
            if price is None or price <= settings.min_price:
                continue
            if settings.max_price > 0 and price > settings.max_price:
                continue
            if avg_volume is None or avg_volume <= settings.min_avg_daily_volume_30d:
                continue
            if (
                settings.min_avg_daily_dollar_volume_30d > 0
                and (avg_dollar_volume is None or avg_dollar_volume <= settings.min_avg_daily_dollar_volume_30d)
            ):
                continue
            selected.append((symbol, avg_dollar_volume or 0))

    selected.sort(key=lambda item: (-item[1], item[0]))
    symbols_sorted = [symbol for symbol, _dollar_volume in selected]
    if settings.universe_max_symbols > 0:
        symbols_sorted = symbols_sorted[: settings.universe_max_symbols]
    return symbols_sorted, metrics


def _dynamic_universe_symbols(
    *,
    client: AlpacaClient,
    settings: Settings,
    journal: TradeJournal,
) -> list[str]:
    if not settings.dynamic_universe:
        return settings.symbols

    cached = _cached_universe_symbols(settings)
    if cached is not None:
        journal.record(
            "dynamic_universe_cached",
            {
                "selected_count": len(cached),
                "expires_at": (UNIVERSE_CACHE.get("expires_at") or "").isoformat()
                if isinstance(UNIVERSE_CACHE.get("expires_at"), datetime)
                else None,
                "symbols": cached,
            },
        )
        return cached

    if settings.universe_max_symbols < 0:
        journal.record(
            "dynamic_universe_error",
            {
                "reason": "ALPACA_UNIVERSE_MAX_SYMBOLS must be >= 0",
                "max_symbols": settings.universe_max_symbols,
            },
        )
        return []

    try:
        assets = client.get_assets(status="active", asset_class="us_equity")
    except AlpacaError as exc:
        journal.record(
            "dynamic_universe_error",
            {
                "reason": str(exc),
            },
        )
        logging.error("Unable to load dynamic universe; skipping new entries: %s", exc)
        return []

    active_assets = [asset for asset in assets if str(asset.get("status", "")).lower() == "active"]
    tradable_filtered = [
        asset for asset in active_assets if not settings.require_tradable or _as_bool(asset.get("tradable"))
    ]
    etb_filtered = [asset for asset in tradable_filtered if not settings.require_etb or _borrow_allowed(asset, settings)]
    symbols = sorted({_asset_symbol(asset) for asset in etb_filtered if _asset_eligible_for_dynamic_universe(asset, settings)})
    shortable_count = sum(1 for asset in etb_filtered if _as_bool(asset.get("shortable")))
    if not symbols:
        journal.record(
            "dynamic_universe_empty",
            {
                "asset_count": len(assets),
                "stage": "asset_filter",
            },
        )
        return []

    selected_symbols = symbols[: settings.universe_max_symbols] if settings.universe_max_symbols > 0 else symbols

    _cache_universe_symbols(settings, selected_symbols)

    journal.record(
        "dynamic_universe_selected",
        {
            "asset_count": len(assets),
            "active_assets_count": len(active_assets),
            "tradable_filtered_count": len(tradable_filtered),
            "etb_filtered_count": len(etb_filtered),
            "basic_eligible_count": len(symbols),
            "shortable_count": shortable_count,
            "final_universe_count": len(selected_symbols),
            "selected_count": len(selected_symbols),
            "max_symbols": settings.universe_max_symbols,
            "universe_limited": settings.universe_max_symbols > 0,
            "sort": "symbol_asc",
            "stage": "active_tradable_etb",
            "filters": {
                "require_etb": settings.require_etb,
                "require_tradable": settings.require_tradable,
                "short_require_etb": settings.short_require_etb,
                "short_require_shortable": settings.short_require_shortable,
                "allowed_borrow_statuses": settings.allowed_borrow_statuses,
            },
            "symbols": selected_symbols,
        },
    )
    return selected_symbols


def _strategy_bar_limit(settings: Settings) -> int:
    if settings.timeframe.strip().lower() == "1min":
        return max(settings.bar_limit, MIN_1MIN_SESSION_BAR_LIMIT)
    return settings.bar_limit


def _latest_price_and_vwap_from_bars(bars: list[dict]) -> tuple[float | None, float | None]:
    price_and_vwap = latest_price_and_session_vwap(bars)
    if price_and_vwap is None:
        return None, None
    return price_and_vwap


def _average_daily_volume(bars: list[dict]) -> float | None:
    volumes = [
        _as_float(bar.get("v", bar.get("volume")), 0)
        for bar in bars
        if _as_float(bar.get("v", bar.get("volume")), 0) > 0
    ]
    if not volumes:
        return None
    return sum(volumes) / len(volumes)


def _dry_run_position_from_state(state: SymbolTradeState, price: float | None) -> dict | None:
    if state.entries_filled <= 0 or state.first_fill_price is None or state.position_direction is None:
        return None
    first_qty = state.first_filled_qty
    second_qty = state.second_filled_qty if state.entries_filled >= 2 else 0.0
    total_qty = first_qty + second_qty
    if total_qty <= 0:
        return None

    entry_notional = state.first_filled_notional + state.second_filled_notional
    avg_entry = entry_notional / total_qty if total_qty > 0 else state.first_fill_price
    mark = price or avg_entry
    if state.position_direction == "short":
        signed_qty = -total_qty
        unrealized_pl = (avg_entry - mark) * total_qty
    else:
        signed_qty = total_qty
        unrealized_pl = (mark - avg_entry) * total_qty
    return {
        "symbol": state.symbol,
        "qty": str(signed_qty),
        "unrealized_pl": str(round(unrealized_pl, 2)),
    }


def _sync_state(
    *,
    symbol: str,
    trade_date: str,
    positions: list[dict],
    open_orders: list[dict],
    journal: TradeJournal,
    account_equity: float,
    dry_run: bool = False,
) -> SymbolTradeState:
    state = _state_for(symbol, trade_date)
    if dry_run and state.state not in {"NO_POSITION", "CLOSED_FOR_DAY"}:
        return state
    position = _position_for_symbol(positions, symbol)
    symbol_orders = _orders_for_symbol(open_orders, symbol)
    direction = position_direction(position)

    if direction:
        if state.state in {"NO_POSITION", "CLOSED_FOR_DAY"}:
            journal.record(
                "state_mismatch",
                _journal_payload(
                    symbol=symbol,
                    event_type="state_mismatch",
                    state=state,
                    account_equity=account_equity,
                    reason="broker has position while local state did not",
                    extra={"broker_position": position},
                ),
            )
        state.position_direction = direction
        state.state = "SECOND_ORDER_PENDING" if symbol_orders else "POSITION_ACTIVE"
        return state

    if symbol_orders:
        if state.state in {"NO_POSITION", "CLOSED_FOR_DAY"}:
            journal.record(
                "state_mismatch",
                _journal_payload(
                    symbol=symbol,
                    event_type="state_mismatch",
                    state=state,
                    account_equity=account_equity,
                    reason="broker has open orders while local state did not",
                    extra={"open_orders": symbol_orders},
                ),
            )
        state.state = "SECOND_ORDER_PENDING" if state.second_order_id else "ENTRY_SUBMITTED"
        return state

    if not state.closed_for_day and state.state not in {"NO_POSITION", "CLOSED_FOR_DAY"}:
        state.state = "NO_POSITION"
    return state


def _record_cancel(
    *,
    client: AlpacaClient,
    journal: TradeJournal,
    symbol: str,
    state: SymbolTradeState,
    open_orders: list[dict],
    account_equity: float,
    dry_run: bool,
) -> None:
    symbol_orders = _orders_for_symbol(open_orders, symbol)
    if not symbol_orders:
        return

    journal.record(
        "order_cancel_submitted",
        _journal_payload(
            symbol=symbol,
            event_type="order_cancel_submitted",
            state=state,
            account_equity=account_equity,
            reason="cancel symbol open orders",
            extra={"dry_run": dry_run, "open_order_count": len(symbol_orders)},
        ),
    )
    results = [] if dry_run else cancel_symbol_orders(client, symbol, open_orders)
    journal.record(
        "order_cancel_confirmed",
        _journal_payload(
            symbol=symbol,
            event_type="order_cancel_confirmed",
            state=state,
            account_equity=account_equity,
            reason="symbol open orders canceled",
            extra={"dry_run": dry_run, "results": results},
        ),
    )


def _close_for_day(
    *,
    client: AlpacaClient,
    journal: TradeJournal,
    symbol: str,
    state: SymbolTradeState,
    signal: Signal,
    account: dict,
    limits: RiskLimits,
    positions: list[dict],
    open_orders: list[dict],
    settings: Settings,
    event_type: str,
) -> None:
    account_equity = _as_float(account.get("equity"))
    state.state = "EXITING"
    _record_cancel(
        client=client,
        journal=journal,
        symbol=symbol,
        state=state,
        open_orders=open_orders,
        account_equity=account_equity,
        dry_run=settings.dry_run,
    )

    journal.record(
        event_type,
        _journal_payload(
            symbol=symbol,
            event_type=event_type,
            state=state,
            signal=signal,
            account_equity=account_equity,
        ),
    )

    risk_positions = positions
    if settings.dry_run and _position_for_symbol(risk_positions, symbol) is None:
        simulated_position = _dry_run_position_from_state(state, signal.price)
        if simulated_position is not None:
            risk_positions = [*positions, simulated_position]

    decision = evaluate_signal(signal, account, limits, risk_positions)
    if not decision.approved and not (
        settings.dry_run and decision.reason == "trading disabled or dry-run enabled"
    ):
        journal.record(
            "position_close_rejected",
            _journal_payload(
                symbol=symbol,
                event_type="position_close_rejected",
                state=state,
                signal=signal,
                account_equity=account_equity,
                reason=decision.reason,
            ),
        )
        return

    journal.record(
        "position_close_submitted",
        _journal_payload(
            symbol=symbol,
            event_type="position_close_submitted",
            state=state,
            signal=signal,
            account_equity=account_equity,
            extra={"dry_run": settings.dry_run},
        ),
    )
    result = submit_or_preview_order(
        client,
        signal,
        dry_run=settings.dry_run,
        wait_for_status=not settings.dry_run,
    )
    journal.record(
        "position_close_filled",
        _journal_payload(
            symbol=symbol,
            event_type="position_close_filled",
            state=state,
            signal=signal,
            account_equity=account_equity,
            extra={"result": result},
        ),
    )
    state.state = "CLOSED_FOR_DAY"
    state.closed_for_day = True
    journal.record(
        "closed_for_day",
        _journal_payload(
            symbol=symbol,
            event_type="closed_for_day",
            state=state,
            signal=signal,
            account_equity=account_equity,
        ),
    )


def _filled_details(result: dict, fallback_price: float) -> tuple[float, float, float, str]:
    status = result.get("latest_status") or result.get("response") or {}
    fill_price = _as_float(status.get("filled_avg_price"), fallback_price)
    filled_qty = _as_float(status.get("filled_qty"), 0.0)
    if filled_qty <= 0:
        notional = _as_float(status.get("filled_notional"), 0.0)
        filled_qty = notional / fill_price if fill_price > 0 and notional > 0 else 0.0
    filled_notional = round(fill_price * filled_qty, 2)
    filled_at = str(status.get("filled_at") or _now_new_york().isoformat())
    return fill_price, filled_qty, filled_notional, filled_at


def _apply_fixed_notional_qty(signal: Signal, *, price: float, fixed_notional: float) -> Signal:
    if price <= 0 or fixed_notional <= 0:
        return signal
    qty = math.floor(fixed_notional / price)
    if qty <= 0:
        return replace(signal, qty=0, notional=0)
    return replace(signal, qty=float(qty), notional=round(qty * price, 2), order_type="market", limit_price=None)


def _submit_second_order(
    *,
    client: AlpacaClient,
    journal: TradeJournal,
    state: SymbolTradeState,
    account: dict,
    limits: RiskLimits,
    positions: list[dict],
    settings: Settings,
    current_price: float | None = None,
) -> None:
    if not settings.enable_second_entry:
        journal.record(
            "second_order_disabled",
            {
                "symbol": state.symbol,
                "state": state.state,
                "reason": "ALPACA_ENABLE_SECOND_ENTRY=false",
            },
        )
        return
    account_equity = _as_float(account.get("equity"))
    second_signal = second_entry_limit_signal(
        state,
        second_order_distance_pct=settings.second_order_distance_pct,
        fixed_entry_notional=settings.fixed_entry_notional,
        current_price=current_price,
        max_entries_per_cycle=settings.max_entries_per_cycle,
    )
    if second_signal.side == "hold":
        return
    execution_price = current_price or second_signal.price or state.first_fill_price or 0
    second_signal = _apply_fixed_notional_qty(
        second_signal,
        price=execution_price,
        fixed_notional=settings.fixed_entry_notional,
    )

    decision = evaluate_signal(second_signal, account, limits, positions)
    journal.record(
        "second_order_preview" if settings.dry_run else "second_order_submitted",
        _journal_payload(
            symbol=state.symbol,
            event_type="second_order_preview" if settings.dry_run else "second_order_submitted",
            state=state,
            signal=second_signal,
            account_equity=account_equity,
            reason=decision.reason,
            extra={"risk_decision": decision, "dry_run": settings.dry_run},
        ),
    )
    if not decision.approved and not (
        settings.dry_run and decision.reason == "trading disabled or dry-run enabled"
    ):
        return

    result = submit_or_preview_order(
        client,
        second_signal,
        dry_run=settings.dry_run,
        wait_for_status=not settings.dry_run,
    )
    state.state = "SECOND_ORDER_PENDING"
    state.entries_submitted = max(state.entries_submitted, 2)
    state.second_limit_price = execution_price
    state.second_order_id = str((result.get("response") or {}).get("id") or "dry-run-second-order")
    if settings.dry_run:
        fill_price = execution_price
        filled_qty = second_signal.qty or 0.0
        state.second_filled_qty = filled_qty
        state.second_filled_notional = round(filled_qty * fill_price, 2)
        state.entries_filled = 2
        state.state = "POSITION_ACTIVE"
        journal.record(
            "second_order_filled",
            _journal_payload(
                symbol=state.symbol,
                event_type="second_order_filled",
                state=state,
                signal=second_signal,
                account_equity=account_equity,
                reason="dry-run second market order filled",
                extra={
                    "result": result,
                    "fill_price": fill_price,
                    "filled_qty": filled_qty,
                    "filled_notional": state.second_filled_notional,
                },
            ),
        )
    else:
        fill_price, filled_qty, filled_notional, _filled_at = _filled_details(result, execution_price)
        if filled_qty > 0:
            state.second_filled_qty = filled_qty
            state.second_filled_notional = filled_notional
            state.entries_filled = 2
            state.state = "POSITION_ACTIVE"
            journal.record(
                "second_order_filled",
                _journal_payload(
                    symbol=state.symbol,
                    event_type="second_order_filled",
                    state=state,
                    signal=second_signal,
                    account_equity=account_equity,
                    reason="second market order filled",
                    extra={
                        "result": result,
                        "fill_price": fill_price,
                        "filled_qty": filled_qty,
                        "filled_notional": filled_notional,
                    },
                ),
            )
    if not settings.dry_run:
        journal.record(
            "second_order_submitted",
            _journal_payload(
                symbol=state.symbol,
                event_type="second_order_submitted",
                state=state,
                signal=second_signal,
                account_equity=account_equity,
                extra={"result": result},
            ),
        )


def _simulate_second_fill(
    *,
    journal: TradeJournal,
    state: SymbolTradeState,
    price: float | None,
    account_equity: float,
    dry_run: bool = False,
) -> None:
    if not dry_run:
        return
    if state.state != "SECOND_ORDER_PENDING" or state.second_limit_price is None or price is None:
        return
    if state.position_direction == "short" and price < state.second_limit_price:
        return
    if state.position_direction == "long" and price > state.second_limit_price:
        return

    qty = state.first_filled_notional / state.second_limit_price
    state.second_filled_qty = qty
    state.second_filled_notional = round(qty * state.second_limit_price, 2)
    state.entries_filled = 2
    state.state = "POSITION_ACTIVE"
    journal.record(
        "second_order_filled",
        _journal_payload(
            symbol=state.symbol,
            event_type="second_order_filled",
            state=state,
            account_equity=account_equity,
            reason="dry-run second limit price reached",
            extra={
                "fill_price": state.second_limit_price,
                "filled_qty": qty,
                "filled_notional": state.second_filled_notional,
            },
        ),
    )


def _handle_force_flatten(
    *,
    client: AlpacaClient,
    journal: TradeJournal,
    settings: Settings,
    account: dict,
    limits: RiskLimits,
    positions: list[dict],
    open_orders: list[dict],
    trade_date: str,
) -> None:
    account_equity = _as_float(account.get("equity"))
    symbols = {str(position.get("symbol", "")).upper() for position in positions}
    symbols.update(str(order.get("symbol", "")).upper() for order in open_orders)
    symbols.update(settings.symbols)
    symbols.update(SYMBOL_STATES)
    for symbol in sorted(symbol for symbol in symbols if symbol):
        state = _sync_state(
            symbol=symbol,
            trade_date=trade_date,
            positions=positions,
            open_orders=open_orders,
            journal=journal,
            account_equity=account_equity,
            dry_run=settings.dry_run,
        )
        position = _position_for_symbol(positions, symbol)
        if settings.dry_run and position is None:
            position = _dry_run_position_from_state(state, None)
        qty = abs(_as_float((position or {}).get("qty")))
        if qty <= 0 and not _orders_for_symbol(open_orders, symbol):
            continue
        direction = position_direction(position) or state.position_direction

        price = None
        vwap = None
        deviation_pct = None
        bars = []
        try:
            bars = load_session_bars(
                client,
                symbol,
                timeframe=settings.timeframe,
                limit=_strategy_bar_limit(settings),
            )
            price, vwap = _latest_price_and_vwap_from_bars(bars)
            if price is not None and vwap is not None:
                deviation_pct = calculate_vwap_deviation_pct(price, vwap)
        except AlpacaError as exc:
            logging.error("[%s] Unable to load bars before force flatten: %s", symbol, exc)

        event_type = "force_close_signal"
        if position is not None and bars:
            loss_signal = vwap_mean_reversion_exit_signal(
                symbol,
                bars,
                position=position,
                account_equity=account_equity,
                max_loss_equity_pct=settings.max_loss_per_symbol_equity_pct,
            )
            if loss_signal.side in {"buy", "sell"} and loss_signal.reason.startswith("max loss reached"):
                signal = loss_signal
                _close_for_day(
                    client=client,
                    journal=journal,
                    symbol=symbol,
                    state=state,
                    signal=signal,
                    account=account,
                    limits=limits,
                    positions=positions,
                    open_orders=open_orders,
                    settings=settings,
                    event_type="max_loss_exit_signal",
                )
                continue

        signal = Signal(
            symbol=symbol,
            side="buy" if direction == "short" else "sell",
            reason="force close before market close",
            qty=qty if qty > 0 else None,
            position_intent="buy_to_close" if direction == "short" else "sell_to_close",
            position_direction=direction,
            price=price,
            vwap=vwap,
            vwap_deviation_pct=deviation_pct,
        )
        if qty <= 0:
            _record_cancel(
                client=client,
                journal=journal,
                symbol=symbol,
                state=state,
                open_orders=open_orders,
                account_equity=account_equity,
                dry_run=settings.dry_run,
            )
            state.state = "CLOSED_FOR_DAY"
            state.closed_for_day = True
            journal.record(
                "closed_for_day",
                _journal_payload(
                    symbol=symbol,
                    event_type="closed_for_day",
                    state=state,
                    account_equity=account_equity,
                    reason="no position remains after canceling orders",
                ),
            )
            continue
        _close_for_day(
            client=client,
            journal=journal,
            symbol=symbol,
            state=state,
            signal=signal,
            account=account,
            limits=limits,
            positions=positions,
            open_orders=open_orders,
            settings=settings,
            event_type=event_type,
        )


def run_once(
    settings: Settings | None = None,
    *,
    execution_context: ExecutionContext | None = None,
    reconciliation_ok: bool = True,
) -> int:
    settings = settings or load_settings()
    owns_execution_context = execution_context is None
    execution_context = execution_context or acquire_execution_context(settings, "rest")
    client = build_client(settings)
    journal = TradeJournal(settings.journal_path)
    journal.record("execution_context", execution_context.payload())

    try:
        account = client.get_account()
        positions = client.get_positions()
        open_orders = client.get_orders(status="open")
        clock = client.get_clock()
    except AlpacaError as exc:
        logging.error("Unable to load account state: %s", exc)
        if owns_execution_context:
            execution_context.release()
        return 1

    account_equity = _as_float(account.get("equity"))
    trade_date = _current_trade_date(clock)
    session = _session_flags(settings, clock)

    if not session["regular"]:
        journal.record("market_closed", {"clock": clock, "session": session})
        logging.info("Market is closed or outside regular session; skipping new entries.")
        if owns_execution_context:
            execution_context.release()
        return 0

    if session["force_flatten"] or session["final_position_check"]:
        flatten_symbols = set(settings.symbols)
        flatten_symbols.update(str(position.get("symbol", "")).upper() for position in positions)
        flatten_symbols.update(str(order.get("symbol", "")).upper() for order in open_orders)
        flatten_symbols.update(SYMBOL_STATES)
        limits = RiskLimits(
            allowed_symbols=sorted(symbol for symbol in flatten_symbols if symbol),
            max_notional_per_order=settings.max_notional_per_order,
            min_cash_reserve=settings.min_cash_reserve,
            can_submit_orders=execution_context.allow_risk_exits,
        )
        _handle_force_flatten(
            client=client,
            journal=journal,
            settings=settings,
            account=account,
            limits=limits,
            positions=positions,
            open_orders=open_orders,
            trade_date=trade_date,
        )
        if owns_execution_context:
            execution_context.release()
        return 0

    if not reconciliation_ok and settings.halt_on_reconciliation_failure:
        journal.record(
            "new_entries_halted",
            {
                "reason": "reconciliation failed",
                "execution_context": execution_context.payload(),
            },
        )
        if owns_execution_context:
            execution_context.release()
        return 0

    scan_symbols = _dynamic_universe_symbols(client=client, settings=settings, journal=journal)
    scan_symbol_set = set(scan_symbols)
    scan_symbol_set.update(str(position.get("symbol", "")).upper() for position in positions)
    scan_symbol_set.update(str(order.get("symbol", "")).upper() for order in open_orders)
    scan_symbol_set.update(SYMBOL_STATES)
    scan_symbols = sorted(symbol for symbol in scan_symbol_set if symbol)

    limits = RiskLimits(
        allowed_symbols=scan_symbols,
        max_notional_per_order=settings.max_notional_per_order,
        min_cash_reserve=settings.min_cash_reserve,
        can_submit_orders=execution_context.can_submit_orders,
    )

    for symbol in scan_symbols:
        state = _sync_state(
            symbol=symbol,
            trade_date=trade_date,
            positions=positions,
            open_orders=open_orders,
            journal=journal,
            account_equity=account_equity,
            dry_run=settings.dry_run,
        )
        position = _position_for_symbol(positions, symbol)

        try:
            bars = load_session_bars(
                client,
                symbol,
                timeframe=settings.timeframe,
                limit=_strategy_bar_limit(settings),
            )
        except AlpacaError as exc:
            logging.error("[%s] Unable to load bars: %s", symbol, exc)
            continue

        price, vwap = _latest_price_and_vwap_from_bars(bars)
        _simulate_second_fill(
            journal=journal,
            state=state,
            price=price,
            account_equity=account_equity,
            dry_run=settings.dry_run,
        )
        if settings.dry_run and position is None:
            position = _dry_run_position_from_state(state, price)

        exit_signal = vwap_mean_reversion_exit_signal(
            symbol,
            bars,
            position=position,
            account_equity=account_equity,
            max_loss_equity_pct=settings.max_loss_per_symbol_equity_pct,
        )
        if exit_signal.side in {"buy", "sell"}:
            event_type = (
                "max_loss_exit_signal"
                if exit_signal.reason.startswith("max loss reached")
                else "vwap_exit_signal"
            )
            _close_for_day(
                client=client,
                journal=journal,
                symbol=symbol,
                state=state,
                signal=exit_signal,
                account=account,
                limits=limits,
                positions=positions,
                open_orders=open_orders,
                settings=settings,
                event_type=event_type,
            )
            logging.info("[%s] exit=%s reason=%s", symbol, exit_signal.side, exit_signal.reason)
            continue

        if state.state == "FIRST_FILLED":
            _submit_second_order(
                client=client,
                journal=journal,
                state=state,
                account=account,
                limits=limits,
                positions=positions,
                settings=settings,
                current_price=price,
            )
            continue

        if not session["allow_new_entries"]:
            continue
        if state.locked:
            continue

        entry_signal = vwap_mean_reversion_entry_signal(
            symbol,
            bars,
            account_equity=account_equity,
            state=state,
            entry_deviation_pct=settings.vwap_entry_deviation_pct,
            first_order_equity_pct=settings.first_order_equity_pct,
            fixed_entry_notional=settings.fixed_entry_notional,
        )
        if entry_signal.side == "hold":
            journal.record(
                "decision",
                _journal_payload(
                    symbol=symbol,
                    event_type="decision",
                    state=state,
                    signal=entry_signal,
                    account_equity=account_equity,
                ),
            )
            continue

        try:
            asset = client.get_asset(symbol)
            quote = client.get_latest_quote(symbol)
            daily_bars = load_recent_bars(
                client,
                symbol,
                timeframe="1Day",
                limit=30,
                lookback_days=60,
            )
        except AlpacaError as exc:
            logging.error("[%s] Unable to load asset, quote, or daily volume details: %s", symbol, exc)
            continue

        bid, ask = _quote_prices(quote)
        avg_daily_volume_30d = _average_daily_volume(daily_bars)
        eligibility = evaluate_vwap_mean_reversion_asset(
            asset,
            price=price,
            bid=bid,
            ask=ask,
            avg_daily_volume_30d=avg_daily_volume_30d,
            min_price=settings.min_price,
            min_avg_daily_volume_30d=settings.min_avg_daily_volume_30d,
            require_etb=settings.short_require_etb if entry_signal.position_direction == "short" else False,
            max_spread_pct=settings.max_spread_pct,
            require_shortable=settings.short_require_shortable if entry_signal.position_direction == "short" else False,
        )
        if not eligibility.eligible:
            event_type = _rejection_event_type(eligibility.reasons)
            journal.record(
                event_type,
                _journal_payload(
                    symbol=symbol,
                    event_type=event_type,
                    state=state,
                    signal=entry_signal,
                    account_equity=account_equity,
                    reason="; ".join(eligibility.reasons),
                    extra={
                        "asset_eligibility": eligibility,
                        "quote": quote,
                        "avg_daily_volume_30d": avg_daily_volume_30d,
                    },
                ),
            )
            logging.info("[%s] skipped: %s", symbol, "; ".join(eligibility.reasons))
            continue

        entry_price = entry_signal.price or price or 0
        entry_signal = _apply_fixed_notional_qty(
            entry_signal,
            price=entry_price,
            fixed_notional=settings.fixed_entry_notional,
        )
        if not entry_signal.qty or entry_signal.qty <= 0:
            journal.record(
                "fixed_notional_rejected",
                _journal_payload(
                    symbol=symbol,
                    event_type="fixed_notional_rejected",
                    state=state,
                    signal=entry_signal,
                    account_equity=account_equity,
                    reason="fixed notional is too small for one whole share",
                    extra={
                        "entry_price": entry_price,
                        "fixed_entry_notional": settings.fixed_entry_notional,
                    },
                ),
            )
            logging.info("[%s] fixed notional rejected: price is too high for target notional", symbol)
            continue

        decision = evaluate_signal(entry_signal, account, limits, positions)
        journal.record(
            "entry_signal",
            _journal_payload(
                symbol=symbol,
                event_type="entry_signal",
                state=state,
                signal=entry_signal,
                account_equity=account_equity,
                reason=decision.reason,
                extra={
                    "risk_decision": decision,
                    "asset_eligibility": eligibility,
                    "avg_daily_volume_30d": avg_daily_volume_30d,
                    "dry_run": settings.dry_run,
                    "enable_trading": settings.enable_trading,
                    "execution_context": execution_context.payload(),
                    "fixed_entry_notional": settings.fixed_entry_notional,
                },
            ),
        )

        if not decision.approved and not (
            settings.dry_run and decision.reason == "trading disabled or dry-run enabled"
        ):
            logging.info("[%s] entry rejected: %s", symbol, decision.reason)
            continue

        journal.record(
            "entry_order_preview" if settings.dry_run else "entry_order_submitted",
            _journal_payload(
                symbol=symbol,
                event_type="entry_order_preview" if settings.dry_run else "entry_order_submitted",
                state=state,
                signal=entry_signal,
                account_equity=account_equity,
                extra={"dry_run": settings.dry_run},
            ),
        )
        try:
            order_dry_run = settings.dry_run or not execution_context.can_submit_orders
            result = submit_or_preview_order(
                client,
                entry_signal,
                dry_run=order_dry_run,
                wait_for_status=not order_dry_run,
            )
        except AlpacaError as exc:
            journal.record(
                "order_error",
                _journal_payload(
                    symbol=symbol,
                    event_type="order_error",
                    state=state,
                    signal=entry_signal,
                    account_equity=account_equity,
                    reason=str(exc),
                ),
            )
            logging.error("[%s] order failed: %s", symbol, exc)
            continue

        state.state = "ENTRY_SUBMITTED"
        state.entries_submitted = 1
        fallback_price = entry_signal.price or price or 0
        if settings.dry_run:
            fill_price = fallback_price
            filled_qty = entry_signal.notional / fill_price if fill_price > 0 else 0
            filled_notional = round(fill_price * filled_qty, 2)
            filled_at = _now_new_york().isoformat()
        else:
            fill_price, filled_qty, filled_notional, filled_at = _filled_details(result, fallback_price)

        if filled_qty > 0 and entry_signal.position_direction:
            mark_first_fill(
                state,
                fill_price=fill_price,
                filled_qty=filled_qty,
                filled_notional=filled_notional,
                position_direction_value=entry_signal.position_direction,
                filled_at=filled_at,
            )
            journal.record(
                "entry_order_filled",
                _journal_payload(
                    symbol=symbol,
                    event_type="entry_order_filled",
                    state=state,
                    signal=entry_signal,
                    account_equity=account_equity,
                    extra={
                        "result": result,
                        "fill_price": fill_price,
                        "filled_qty": filled_qty,
                        "filled_notional": filled_notional,
                    },
                ),
            )
            _submit_second_order(
                client=client,
                journal=journal,
                state=state,
                account=account,
                limits=limits,
                positions=positions,
                settings=settings,
                current_price=price,
            )

        logging.info(
            "[%s] signal=%s reason=%s risk=%s",
            symbol,
            entry_signal.side,
            entry_signal.reason,
            decision.reason,
        )

    if owns_execution_context:
        execution_context.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_once())

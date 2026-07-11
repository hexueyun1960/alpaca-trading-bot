from __future__ import annotations

from dataclasses import dataclass

from src.config import Settings, load_settings


@dataclass(frozen=True)
class ConfigCheck:
    name: str
    ok: bool
    message: str


def validate_settings(settings: Settings) -> list[ConfigCheck]:
    is_paper_base_url = settings.is_paper_base_url
    can_submit_orders = settings.can_submit_orders
    wants_live_orders = settings.enable_trading and not settings.dry_run and not is_paper_base_url

    checks = [
        ConfigCheck(
            "api_key_id",
            bool(settings.api_key_id),
            "ALPACA_API_KEY_ID is set" if settings.api_key_id else "ALPACA_API_KEY_ID is missing",
        ),
        ConfigCheck(
            "api_secret_key",
            bool(settings.api_secret_key),
            "ALPACA_API_SECRET_KEY is set"
            if settings.api_secret_key
            else "ALPACA_API_SECRET_KEY is missing",
        ),
        ConfigCheck(
            "base_url",
            bool(settings.base_url),
            f"base URL is {settings.base_url}" if settings.base_url else "ALPACA_BASE_URL is missing",
        ),
        ConfigCheck(
            "live_trading_guard",
            not wants_live_orders or settings.allow_live_trading,
            "safe: live trading is blocked unless ALPACA_ALLOW_LIVE_TRADING=true"
            if not wants_live_orders
            else "live trading explicitly allowed by ALPACA_ALLOW_LIVE_TRADING=true"
            if settings.allow_live_trading
            else "blocked: live endpoint requires ALPACA_ALLOW_LIVE_TRADING=true",
        ),
        ConfigCheck(
            "symbols",
            settings.dynamic_universe or bool(settings.symbols),
            "dynamic universe enabled"
            if settings.dynamic_universe
            else f"symbols: {', '.join(settings.symbols)}"
            if settings.symbols
            else "no symbols configured",
        ),
        ConfigCheck(
            "dynamic_universe",
            not settings.dynamic_universe or settings.universe_max_symbols >= 0,
            "dynamic universe max symbols is unlimited after prefilters"
            if settings.dynamic_universe and settings.universe_max_symbols == 0
            else f"dynamic universe max symbols is {settings.universe_max_symbols} after prefilters"
            if settings.dynamic_universe and settings.universe_max_symbols > 0
            else "fixed ALPACA_SYMBOLS universe"
            if not settings.dynamic_universe
            else "ALPACA_UNIVERSE_MAX_SYMBOLS must be >= 0 when dynamic universe is enabled",
        ),
        ConfigCheck(
            "execution_mode",
            settings.execution_mode in {"rest", "stream", "shadow"} and bool(settings.instance_id),
            (
                f"execution mode is {settings.execution_mode}; "
                f"instance_id={settings.instance_id}; "
                f"leader_lock_required={settings.require_leader_lock}"
            )
            if settings.execution_mode in {"rest", "stream", "shadow"} and bool(settings.instance_id)
            else "ALPACA_EXECUTION_MODE must be rest, stream, or shadow and ALPACA_INSTANCE_ID is required",
        ),
        ConfigCheck(
            "dynamic_universe_refresh",
            not settings.dynamic_universe
            or (settings.universe_refresh_interval_seconds >= 60 and settings.universe_chunk_size >= 1),
            (
                "dynamic universe refresh="
                f"{settings.universe_refresh_interval_seconds}s chunk_size={settings.universe_chunk_size}"
            )
            if settings.dynamic_universe
            and settings.universe_refresh_interval_seconds >= 60
            and settings.universe_chunk_size >= 1
            else "fixed ALPACA_SYMBOLS universe"
            if not settings.dynamic_universe
            else "ALPACA_UNIVERSE_REFRESH_INTERVAL_SECONDS should be >= 60 and ALPACA_UNIVERSE_CHUNK_SIZE >= 1",
        ),
        ConfigCheck(
            "universe_filters",
            bool(settings.allowed_borrow_statuses) and settings.require_tradable and settings.require_etb,
            (
                "initial universe filters: active=true tradable=true ETB=true; "
                f"allowed_borrow_statuses={','.join(settings.allowed_borrow_statuses)}; "
                f"shortable_check_before_short={settings.short_require_shortable}"
            ),
        ),
        ConfigCheck(
            "websocket_scanner",
            not settings.use_websocket
            or (
                settings.max_candidate_symbols >= 1
                and settings.max_high_priority_symbols >= 1
                and settings.max_high_priority_symbols <= settings.max_candidate_symbols
                and settings.stale_quote_seconds >= 1
                and settings.order_timeout_seconds >= 1
                and settings.max_slippage_pct >= 0
            ),
            (
                "websocket scanner enabled: "
                f"feed={settings.market_data_feed} "
                f"broad_bars={settings.websocket_broad_bars} "
                f"candidates={settings.max_candidate_symbols} "
                f"high_priority={settings.max_high_priority_symbols} "
                f"top_gainers={settings.top_gainers_count} "
                f"top_losers={settings.top_losers_count} "
                f"retention={settings.candidate_retention_seconds}s "
                f"stale_quote={settings.stale_quote_seconds}s "
                f"order_timeout={settings.order_timeout_seconds}s"
            )
            if settings.use_websocket
            and settings.max_candidate_symbols >= 1
            and settings.max_high_priority_symbols >= 1
            and settings.max_high_priority_symbols <= settings.max_candidate_symbols
            and settings.stale_quote_seconds >= 1
            and settings.order_timeout_seconds >= 1
            and settings.max_slippage_pct >= 0
            else "websocket scanner disabled"
            if not settings.use_websocket
            else "invalid websocket scanner settings",
        ),
        ConfigCheck(
            "risk_sizing",
            settings.max_loss_per_symbol_equity_pct > 0
            and settings.min_buying_power_reserve >= 0
            and settings.max_open_positions >= 1
            and settings.max_gross_exposure_equity_pct > 0,
            (
                f"symbol_risk={settings.max_loss_per_symbol_equity_pct}% "
                f"require_hard_stop={settings.require_hard_stop} "
                f"max_open_positions={settings.max_open_positions} "
                f"gross_exposure={settings.max_gross_exposure_equity_pct}%"
            ),
        ),
        ConfigCheck(
            "circuit_breakers",
            settings.max_order_rejections_per_day >= 1 and settings.max_consecutive_api_errors >= 1,
            (
                f"stream_disconnect={settings.halt_on_stream_disconnect} "
                f"trade_stream_disconnect={settings.halt_on_trade_stream_disconnect} "
                f"reconciliation_failure={settings.halt_on_reconciliation_failure} "
                f"max_order_rejections={settings.max_order_rejections_per_day} "
                f"max_api_errors={settings.max_consecutive_api_errors}"
            ),
        ),
        ConfigCheck(
            "bar_limit",
            settings.bar_limit >= 1,
            "bar limit is valid; 1Min session VWAP loads at least 390 bars"
            if settings.bar_limit >= 1
            else "ALPACA_BAR_LIMIT should be at least 1",
        ),
        ConfigCheck(
            "market_data_lookback_days",
            settings.market_data_lookback_days >= 1,
            f"market data lookback is {settings.market_data_lookback_days} days",
        ),
        ConfigCheck(
            "max_notional_per_order",
            settings.max_notional_per_order >= 0,
            "max order notional is unlimited by local cap"
            if settings.max_notional_per_order == 0
            else f"max order notional is {settings.max_notional_per_order}",
        ),
        ConfigCheck(
            "short_spike_notional",
            settings.short_spike_notional > 0
            and (settings.max_notional_per_order == 0 or settings.short_spike_notional <= settings.max_notional_per_order),
            f"short spike notional is {settings.short_spike_notional}",
        ),
        ConfigCheck(
            "vwap_strategy",
            settings.vwap_entry_deviation_pct > 0
            and settings.first_order_equity_pct > 0
            and settings.second_order_distance_pct > 0
            and settings.hard_stop_distance_pct > 0
            and settings.max_loss_per_symbol_equity_pct > 0,
            (
                "VWAP mean-reversion params are set: "
                f"entry={settings.vwap_entry_deviation_pct}%, "
                f"first={settings.first_order_equity_pct}%, "
                f"second_distance={settings.second_order_distance_pct}%, "
                f"hard_stop_distance={settings.hard_stop_distance_pct}%, "
                f"max_loss={settings.max_loss_per_symbol_equity_pct}%"
            ),
        ),
        ConfigCheck(
            "hard_stop_vs_second_entry",
            not settings.enable_second_entry
            or settings.hard_stop_distance_pct > settings.second_order_distance_pct,
            "second entry disabled; hard stop distance is independent"
            if not settings.enable_second_entry
            else (
                "hard stop is beyond second-entry trigger: "
                f"hard_stop={settings.hard_stop_distance_pct}% "
                f"second_entry={settings.second_order_distance_pct}%"
            )
            if settings.hard_stop_distance_pct > settings.second_order_distance_pct
            else (
                "ALPACA_HARD_STOP_DISTANCE_PCT must be greater than "
                "ALPACA_SECOND_ORDER_DISTANCE_PCT when second entry is enabled"
            ),
        ),
        ConfigCheck(
            "journal_path",
            bool(settings.journal_path),
            f"journal path is {settings.journal_path}",
        ),
        ConfigCheck(
            "monitor_interval_seconds",
            settings.monitor_interval_seconds >= 5,
            f"monitor interval is {settings.monitor_interval_seconds} seconds"
            if settings.monitor_interval_seconds >= 5
            else "ALPACA_MONITOR_INTERVAL_SECONDS should be at least 5",
        ),
        ConfigCheck(
            "live_order_guard",
            not wants_live_orders or settings.allow_live_trading,
            "paper order submission is enabled"
            if can_submit_orders and is_paper_base_url
            else "live order submission is explicitly enabled"
            if can_submit_orders and settings.allow_live_trading
            else "safe: dry-run is on or trading is disabled"
            if not wants_live_orders
            else "blocked: live order submission requires ALPACA_ALLOW_LIVE_TRADING=true",
        ),
    ]
    return checks


def main() -> int:
    settings = load_settings()
    checks = validate_settings(settings)

    for check in checks:
        status = "OK" if check.ok else "FAIL"
        print(f"[{status}] {check.name}: {check.message}")

    return 0 if all(check.ok for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

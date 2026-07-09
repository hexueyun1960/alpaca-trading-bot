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
            not settings.dynamic_universe or settings.universe_max_symbols > 0,
            f"dynamic universe max symbols is {settings.universe_max_symbols}"
            if settings.dynamic_universe and settings.universe_max_symbols > 0
            else "fixed ALPACA_SYMBOLS universe"
            if not settings.dynamic_universe
            else "ALPACA_UNIVERSE_MAX_SYMBOLS must be positive when dynamic universe is enabled",
        ),
        ConfigCheck(
            "bar_limit",
            settings.bar_limit >= 1,
            "bar limit supports latest 1-minute VWAP signal"
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
            and settings.max_loss_per_symbol_equity_pct > 0,
            (
                "VWAP mean-reversion params are set: "
                f"entry={settings.vwap_entry_deviation_pct}%, "
                f"first={settings.first_order_equity_pct}%, "
                f"second_distance={settings.second_order_distance_pct}%, "
                f"max_loss={settings.max_loss_per_symbol_equity_pct}%"
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

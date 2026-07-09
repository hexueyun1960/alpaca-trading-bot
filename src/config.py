from __future__ import annotations

import os
from dataclasses import dataclass


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)


def _as_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def _as_symbols(value: str | None) -> list[str]:
    if not value:
        return ["SPY"]
    return [symbol.strip().upper() for symbol in value.split(",") if symbol.strip()]


@dataclass(frozen=True)
class Settings:
    api_key_id: str
    api_secret_key: str
    base_url: str
    data_url: str
    symbols: list[str]
    timeframe: str
    bar_limit: int
    market_data_lookback_days: int
    max_notional_per_order: float
    short_spike_notional: float
    min_cash_reserve: float
    dry_run: bool
    enable_trading: bool
    journal_path: str
    monitor_interval_seconds: int
    allow_live_trading: bool = False
    dynamic_universe: bool = False
    universe_max_symbols: int = 100
    min_price: float = 1.0
    min_avg_daily_volume_30d: float = 3_000_000.0
    require_etb: bool = True
    max_spread_pct: float = 0.5
    vwap_entry_deviation_pct: float = 4.0
    first_order_equity_pct: float = 8.0
    second_order_distance_pct: float = 4.0
    second_order_equity_pct: float = 8.0
    max_loss_per_symbol_equity_pct: float = 1.0
    one_trade_cycle_per_symbol_per_day: bool = True
    max_entries_per_cycle: int = 2
    no_new_entries_after: str = "15:30"
    force_flatten_time: str = "15:55"
    final_position_check_time: str = "15:58"
    regular_session_only: bool = True
    no_overnight: bool = True

    @property
    def is_paper_base_url(self) -> bool:
        return "paper-api.alpaca.markets" in self.base_url

    @property
    def can_submit_orders(self) -> bool:
        if not self.enable_trading or self.dry_run:
            return False
        return self.is_paper_base_url or self.allow_live_trading


def load_settings() -> Settings:
    _load_dotenv()

    return Settings(
        api_key_id=os.getenv("ALPACA_API_KEY_ID", ""),
        api_secret_key=os.getenv("ALPACA_API_SECRET_KEY", ""),
        base_url=os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/"),
        data_url=os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets").rstrip("/"),
        symbols=_as_symbols(os.getenv("ALPACA_SYMBOLS")),
        timeframe=os.getenv("ALPACA_TIMEFRAME", "1Min"),
        bar_limit=_as_int("ALPACA_BAR_LIMIT", 60),
        market_data_lookback_days=_as_int("ALPACA_MARKET_DATA_LOOKBACK_DAYS", 5),
        max_notional_per_order=_as_float("ALPACA_MAX_NOTIONAL_PER_ORDER", 0.0),
        short_spike_notional=_as_float("ALPACA_SHORT_SPIKE_NOTIONAL", 500.0),
        min_cash_reserve=_as_float("ALPACA_MIN_CASH_RESERVE", 1000.0),
        dry_run=_as_bool(os.getenv("ALPACA_DRY_RUN"), default=True),
        enable_trading=_as_bool(os.getenv("ALPACA_ENABLE_TRADING"), default=False),
        journal_path=os.getenv("ALPACA_JOURNAL_PATH", "logs/trade_journal.jsonl"),
        monitor_interval_seconds=_as_int("ALPACA_MONITOR_INTERVAL_SECONDS", 60),
        allow_live_trading=_as_bool(os.getenv("ALPACA_ALLOW_LIVE_TRADING"), default=False),
        dynamic_universe=_as_bool(os.getenv("ALPACA_DYNAMIC_UNIVERSE"), default=False),
        universe_max_symbols=_as_int("ALPACA_UNIVERSE_MAX_SYMBOLS", 100),
        min_price=_as_float("ALPACA_MIN_PRICE", 1.0),
        min_avg_daily_volume_30d=_as_float("ALPACA_MIN_AVG_DAILY_VOLUME_30D", 3_000_000.0),
        require_etb=_as_bool(os.getenv("ALPACA_REQUIRE_ETB"), default=True),
        max_spread_pct=_as_float("ALPACA_MAX_SPREAD_PCT", 0.5),
        vwap_entry_deviation_pct=_as_float("ALPACA_VWAP_ENTRY_DEVIATION_PCT", 4.0),
        first_order_equity_pct=_as_float("ALPACA_FIRST_ORDER_EQUITY_PCT", 8.0),
        second_order_distance_pct=_as_float("ALPACA_SECOND_ORDER_DISTANCE_PCT", 4.0),
        second_order_equity_pct=_as_float("ALPACA_SECOND_ORDER_EQUITY_PCT", 8.0),
        max_loss_per_symbol_equity_pct=_as_float("ALPACA_MAX_LOSS_PER_SYMBOL_EQUITY_PCT", 1.0),
        one_trade_cycle_per_symbol_per_day=_as_bool(
            os.getenv("ALPACA_ONE_TRADE_CYCLE_PER_SYMBOL_PER_DAY"),
            default=True,
        ),
        max_entries_per_cycle=_as_int("ALPACA_MAX_ENTRIES_PER_CYCLE", 2),
        no_new_entries_after=os.getenv("ALPACA_NO_NEW_ENTRIES_AFTER", "15:30"),
        force_flatten_time=os.getenv("ALPACA_FORCE_FLATTEN_TIME", "15:55"),
        final_position_check_time=os.getenv("ALPACA_FINAL_POSITION_CHECK_TIME", "15:58"),
        regular_session_only=_as_bool(os.getenv("ALPACA_REGULAR_SESSION_ONLY"), default=True),
        no_overnight=_as_bool(os.getenv("ALPACA_NO_OVERNIGHT"), default=True),
    )

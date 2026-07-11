from __future__ import annotations

import os
from dataclasses import dataclass, field


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


def _as_upper_list(value: str | None, default: list[str]) -> list[str]:
    if value is None or value.strip() == "":
        return default
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def _as_lower_list(value: str | None, default: list[str]) -> list[str]:
    if value is None or value.strip() == "":
        return default
    return [item.strip().lower() for item in value.split(",") if item.strip()]


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
    execution_mode: str = "rest"
    instance_id: str = "alpaca-local"
    require_leader_lock: bool = True
    leader_lock_path: str = "logs/execution_leader.lock"
    halt_on_reconciliation_failure: bool = True
    halt_on_trade_stream_disconnect: bool = True
    allow_live_trading: bool = False
    dynamic_universe: bool = False
    universe_max_symbols: int = 100
    universe_refresh_interval_seconds: int = 3600
    universe_chunk_size: int = 200
    allowed_exchanges: list[str] = field(default_factory=lambda: ["NYSE", "NASDAQ", "ARCA", "AMEX"])
    min_price: float = 1.0
    max_price: float = 0.0
    min_avg_daily_volume_30d: float = 3_000_000.0
    min_avg_daily_dollar_volume_30d: float = 0.0
    require_etb: bool = True
    require_tradable: bool = True
    short_require_shortable: bool = True
    short_require_etb: bool = True
    require_shortable: bool = True
    require_fractionable: bool = False
    allowed_borrow_statuses: list[str] = field(default_factory=lambda: ["easy_to_borrow", "available"])
    max_spread_pct: float = 0.5
    use_websocket: bool = False
    use_market_data_stream: bool = True
    use_trade_update_stream: bool = True
    enable_stream_strategy: bool = False
    market_data_feed: str = "iex"
    websocket_broad_bars: bool = True
    max_candidate_symbols: int = 100
    max_high_priority_symbols: int = 20
    candidate_ttl_seconds: int = 900
    candidate_retention_seconds: int = 900
    top_gainers_count: int = 20
    top_losers_count: int = 20
    min_5m_gain_pct: float = 8.0
    min_15m_gain_pct: float = 15.0
    min_relative_volume: float = 3.0
    rvol_lookback_days: int = 20
    rvol_min_minutes_after_open: int = 5
    min_intraday_dollar_volume: float = 500_000.0
    candidate_enter_score: float = 70.0
    candidate_exit_score: float = 50.0
    candidate_max_age_seconds: int = 300
    stale_quote_seconds: int = 3
    stale_trade_seconds: int = 3
    stale_bar_seconds: int = 90
    max_event_loop_lag_ms: int = 250
    max_slippage_pct: float = 0.2
    max_entry_slippage_pct: float = 0.15
    max_exit_slippage_pct: float = 0.30
    spread_rules: str = "1:0.50,2:0.35,5:0.25,20:0.15"
    max_absolute_spread: float = 0.10
    order_timeout_seconds: int = 20
    entry_order_timeout_seconds: int = 5
    exit_order_timeout_seconds: int = 10
    max_reprice_attempts: int = 2
    entry_max_reprice_attempts: int = 2
    exit_max_reprice_attempts: int = 3
    revalidate_signal_before_reprice: bool = True
    max_open_positions: int = 3
    max_daily_loss: float = 30.0
    vwap_entry_deviation_pct: float = 4.0
    first_order_equity_pct: float = 8.0
    fixed_entry_notional: float = 240.0
    second_order_distance_pct: float = 4.0
    second_order_equity_pct: float = 8.0
    hard_stop_distance_pct: float = 6.0
    max_loss_per_symbol_equity_pct: float = 1.0
    require_hard_stop: bool = True
    enable_second_entry: bool = True
    max_gross_exposure_equity_pct: float = 40.0
    max_net_long_exposure_equity_pct: float = 25.0
    max_net_short_exposure_equity_pct: float = 25.0
    max_daily_loss_equity_pct: float = 1.5
    max_daily_loss_absolute: float = 30.0
    min_buying_power_reserve: float = 1000.0
    halt_on_stream_disconnect: bool = True
    halt_on_repeated_order_rejection: bool = True
    max_order_rejections_per_day: int = 3
    max_consecutive_api_errors: int = 5
    timezone: str = "America/New_York"
    shadow_journal_path: str = "logs/shadow_journal.jsonl"
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
        execution_mode=os.getenv("ALPACA_EXECUTION_MODE", "rest").strip().lower(),
        instance_id=os.getenv("ALPACA_INSTANCE_ID", "alpaca-local"),
        require_leader_lock=_as_bool(os.getenv("ALPACA_REQUIRE_LEADER_LOCK"), default=True),
        leader_lock_path=os.getenv("ALPACA_LEADER_LOCK_PATH", "logs/execution_leader.lock"),
        halt_on_reconciliation_failure=_as_bool(
            os.getenv("ALPACA_HALT_ON_RECONCILIATION_FAILURE"),
            default=True,
        ),
        halt_on_trade_stream_disconnect=_as_bool(
            os.getenv("ALPACA_HALT_ON_TRADE_STREAM_DISCONNECT"),
            default=True,
        ),
        allow_live_trading=_as_bool(os.getenv("ALPACA_ALLOW_LIVE_TRADING"), default=False),
        dynamic_universe=_as_bool(os.getenv("ALPACA_DYNAMIC_UNIVERSE"), default=False),
        universe_max_symbols=_as_int("ALPACA_UNIVERSE_MAX_SYMBOLS", 100),
        universe_refresh_interval_seconds=_as_int("ALPACA_UNIVERSE_REFRESH_INTERVAL_SECONDS", 3600),
        universe_chunk_size=_as_int("ALPACA_UNIVERSE_CHUNK_SIZE", 200),
        allowed_exchanges=_as_upper_list(
            os.getenv("ALPACA_ALLOWED_EXCHANGES"),
            ["NYSE", "NASDAQ", "ARCA", "AMEX"],
        ),
        min_price=_as_float("ALPACA_MIN_PRICE", 1.0),
        max_price=_as_float("ALPACA_MAX_PRICE", 0.0),
        min_avg_daily_volume_30d=_as_float("ALPACA_MIN_AVG_DAILY_VOLUME_30D", 3_000_000.0),
        min_avg_daily_dollar_volume_30d=_as_float("ALPACA_MIN_AVG_DAILY_DOLLAR_VOLUME_30D", 0.0),
        require_etb=_as_bool(os.getenv("ALPACA_REQUIRE_ETB"), default=True),
        require_tradable=_as_bool(os.getenv("ALPACA_REQUIRE_TRADABLE"), default=True),
        short_require_shortable=_as_bool(os.getenv("ALPACA_SHORT_REQUIRE_SHORTABLE"), default=True),
        short_require_etb=_as_bool(os.getenv("ALPACA_SHORT_REQUIRE_ETB"), default=True),
        require_shortable=_as_bool(os.getenv("ALPACA_REQUIRE_SHORTABLE"), default=True),
        require_fractionable=_as_bool(os.getenv("ALPACA_REQUIRE_FRACTIONABLE"), default=False),
        allowed_borrow_statuses=_as_lower_list(
            os.getenv("ALPACA_ALLOWED_BORROW_STATUSES"),
            ["easy_to_borrow", "available"],
        ),
        max_spread_pct=_as_float("ALPACA_MAX_SPREAD_PCT", 0.5),
        use_websocket=_as_bool(os.getenv("ALPACA_USE_WEBSOCKET"), default=False),
        use_market_data_stream=_as_bool(os.getenv("ALPACA_USE_MARKET_DATA_STREAM"), default=True),
        use_trade_update_stream=_as_bool(os.getenv("ALPACA_USE_TRADE_UPDATE_STREAM"), default=True),
        enable_stream_strategy=_as_bool(os.getenv("ALPACA_ENABLE_STREAM_STRATEGY"), default=False),
        market_data_feed=os.getenv("ALPACA_MARKET_DATA_FEED", "iex"),
        websocket_broad_bars=_as_bool(os.getenv("ALPACA_WEBSOCKET_BROAD_BARS"), default=True),
        max_candidate_symbols=_as_int("ALPACA_MAX_CANDIDATE_SYMBOLS", 100),
        max_high_priority_symbols=_as_int("ALPACA_MAX_HIGH_PRIORITY_SYMBOLS", 20),
        candidate_ttl_seconds=_as_int("ALPACA_CANDIDATE_TTL_SECONDS", 900),
        candidate_retention_seconds=_as_int(
            "ALPACA_CANDIDATE_RETENTION_SECONDS",
            _as_int("ALPACA_CANDIDATE_TTL_SECONDS", 900),
        ),
        top_gainers_count=_as_int("ALPACA_TOP_GAINERS_COUNT", 20),
        top_losers_count=_as_int("ALPACA_TOP_LOSERS_COUNT", 20),
        min_5m_gain_pct=_as_float("ALPACA_MIN_5M_GAIN_PCT", 8.0),
        min_15m_gain_pct=_as_float("ALPACA_MIN_15M_GAIN_PCT", 15.0),
        min_relative_volume=_as_float("ALPACA_MIN_RELATIVE_VOLUME", 3.0),
        rvol_lookback_days=_as_int("ALPACA_RVOL_LOOKBACK_DAYS", 20),
        rvol_min_minutes_after_open=_as_int("ALPACA_RVOL_MIN_MINUTES_AFTER_OPEN", 5),
        min_intraday_dollar_volume=_as_float("ALPACA_MIN_INTRADAY_DOLLAR_VOLUME", 500_000.0),
        candidate_enter_score=_as_float("ALPACA_CANDIDATE_ENTER_SCORE", 70.0),
        candidate_exit_score=_as_float("ALPACA_CANDIDATE_EXIT_SCORE", 50.0),
        candidate_max_age_seconds=_as_int("ALPACA_CANDIDATE_MAX_AGE_SECONDS", 300),
        stale_quote_seconds=_as_int("ALPACA_STALE_QUOTE_SECONDS", 3),
        stale_trade_seconds=_as_int("ALPACA_STALE_TRADE_SECONDS", 3),
        stale_bar_seconds=_as_int("ALPACA_STALE_BAR_SECONDS", 90),
        max_event_loop_lag_ms=_as_int("ALPACA_MAX_EVENT_LOOP_LAG_MS", 250),
        max_slippage_pct=_as_float("ALPACA_MAX_SLIPPAGE_PCT", 0.2),
        max_entry_slippage_pct=_as_float("ALPACA_MAX_ENTRY_SLIPPAGE_PCT", 0.15),
        max_exit_slippage_pct=_as_float("ALPACA_MAX_EXIT_SLIPPAGE_PCT", 0.30),
        spread_rules=os.getenv("ALPACA_SPREAD_RULES", "1:0.50,2:0.35,5:0.25,20:0.15"),
        max_absolute_spread=_as_float("ALPACA_MAX_ABSOLUTE_SPREAD", 0.10),
        order_timeout_seconds=_as_int("ALPACA_ORDER_TIMEOUT_SECONDS", 20),
        entry_order_timeout_seconds=_as_int("ALPACA_ENTRY_ORDER_TIMEOUT_SECONDS", 5),
        exit_order_timeout_seconds=_as_int("ALPACA_EXIT_ORDER_TIMEOUT_SECONDS", 10),
        max_reprice_attempts=_as_int("ALPACA_MAX_REPRICE_ATTEMPTS", 2),
        entry_max_reprice_attempts=_as_int("ALPACA_ENTRY_MAX_REPRICE_ATTEMPTS", 2),
        exit_max_reprice_attempts=_as_int("ALPACA_EXIT_MAX_REPRICE_ATTEMPTS", 3),
        revalidate_signal_before_reprice=_as_bool(
            os.getenv("ALPACA_REVALIDATE_SIGNAL_BEFORE_REPRICE"),
            default=True,
        ),
        max_open_positions=_as_int("ALPACA_MAX_OPEN_POSITIONS", 3),
        max_daily_loss=_as_float("ALPACA_MAX_DAILY_LOSS", 30.0),
        vwap_entry_deviation_pct=_as_float("ALPACA_VWAP_ENTRY_DEVIATION_PCT", 4.0),
        first_order_equity_pct=_as_float("ALPACA_FIRST_ORDER_EQUITY_PCT", 8.0),
        fixed_entry_notional=_as_float("ALPACA_FIXED_ENTRY_NOTIONAL", 240.0),
        second_order_distance_pct=_as_float("ALPACA_SECOND_ORDER_DISTANCE_PCT", 4.0),
        second_order_equity_pct=_as_float("ALPACA_SECOND_ORDER_EQUITY_PCT", 8.0),
        hard_stop_distance_pct=_as_float("ALPACA_HARD_STOP_DISTANCE_PCT", 6.0),
        max_loss_per_symbol_equity_pct=_as_float("ALPACA_MAX_LOSS_PER_SYMBOL_EQUITY_PCT", 1.0),
        one_trade_cycle_per_symbol_per_day=_as_bool(
            os.getenv("ALPACA_ONE_TRADE_CYCLE_PER_SYMBOL_PER_DAY"),
            default=True,
        ),
        max_entries_per_cycle=_as_int("ALPACA_MAX_ENTRIES_PER_CYCLE", 2),
        require_hard_stop=_as_bool(os.getenv("ALPACA_REQUIRE_HARD_STOP"), default=True),
        enable_second_entry=_as_bool(os.getenv("ALPACA_ENABLE_SECOND_ENTRY"), default=True),
        max_gross_exposure_equity_pct=_as_float("ALPACA_MAX_GROSS_EXPOSURE_EQUITY_PCT", 40.0),
        max_net_long_exposure_equity_pct=_as_float("ALPACA_MAX_NET_LONG_EXPOSURE_EQUITY_PCT", 25.0),
        max_net_short_exposure_equity_pct=_as_float("ALPACA_MAX_NET_SHORT_EXPOSURE_EQUITY_PCT", 25.0),
        max_daily_loss_equity_pct=_as_float("ALPACA_MAX_DAILY_LOSS_EQUITY_PCT", 1.5),
        max_daily_loss_absolute=_as_float("ALPACA_MAX_DAILY_LOSS_ABSOLUTE", 30.0),
        min_buying_power_reserve=_as_float("ALPACA_MIN_BUYING_POWER_RESERVE", 1000.0),
        halt_on_stream_disconnect=_as_bool(os.getenv("ALPACA_HALT_ON_STREAM_DISCONNECT"), default=True),
        halt_on_repeated_order_rejection=_as_bool(
            os.getenv("ALPACA_HALT_ON_REPEATED_ORDER_REJECTION"),
            default=True,
        ),
        max_order_rejections_per_day=_as_int("ALPACA_MAX_ORDER_REJECTIONS_PER_DAY", 3),
        max_consecutive_api_errors=_as_int("ALPACA_MAX_CONSECUTIVE_API_ERRORS", 5),
        timezone=os.getenv("ALPACA_TIMEZONE", "America/New_York"),
        shadow_journal_path=os.getenv("ALPACA_SHADOW_JOURNAL_PATH", "logs/shadow_journal.jsonl"),
        no_new_entries_after=os.getenv("ALPACA_NO_NEW_ENTRIES_AFTER", "15:30"),
        force_flatten_time=os.getenv("ALPACA_FORCE_FLATTEN_TIME", "15:55"),
        final_position_check_time=os.getenv("ALPACA_FINAL_POSITION_CHECK_TIME", "15:58"),
        regular_session_only=_as_bool(os.getenv("ALPACA_REGULAR_SESSION_ONLY"), default=True),
        no_overnight=_as_bool(os.getenv("ALPACA_NO_OVERNIGHT"), default=True),
    )

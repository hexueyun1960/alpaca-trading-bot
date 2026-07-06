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

    @property
    def can_submit_orders(self) -> bool:
        return self.enable_trading and not self.dry_run


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
        max_notional_per_order=_as_float("ALPACA_MAX_NOTIONAL_PER_ORDER", 500.0),
        short_spike_notional=_as_float("ALPACA_SHORT_SPIKE_NOTIONAL", 500.0),
        min_cash_reserve=_as_float("ALPACA_MIN_CASH_RESERVE", 1000.0),
        dry_run=_as_bool(os.getenv("ALPACA_DRY_RUN"), default=True),
        enable_trading=_as_bool(os.getenv("ALPACA_ENABLE_TRADING"), default=False),
        journal_path=os.getenv("ALPACA_JOURNAL_PATH", "logs/trade_journal.jsonl"),
        monitor_interval_seconds=_as_int("ALPACA_MONITOR_INTERVAL_SECONDS", 60),
    )

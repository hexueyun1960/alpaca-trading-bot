from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.alpaca_client import AlpacaClient


try:
    NY_TZ = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:
    NY_TZ = None


def _to_new_york(dt: datetime) -> datetime:
    if NY_TZ is not None:
        return dt.astimezone(NY_TZ)
    return dt.astimezone(timezone(timedelta(hours=-4)))


def load_recent_bars(
    client: AlpacaClient,
    symbol: str,
    *,
    timeframe: str,
    limit: int,
    lookback_days: int,
) -> list[dict]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    return client.get_stock_bars(
        symbol,
        timeframe=timeframe,
        limit=limit,
        start=start.isoformat(),
        end=end.isoformat(),
    )


def load_session_bars(
    client: AlpacaClient,
    symbol: str,
    *,
    timeframe: str,
    limit: int,
) -> list[dict]:
    end = datetime.now(timezone.utc)
    end_ny = _to_new_york(end)
    session_start_ny = datetime.combine(end_ny.date(), time(9, 30), tzinfo=end_ny.tzinfo)
    return client.get_stock_bars(
        symbol,
        timeframe=timeframe,
        limit=limit,
        start=session_start_ny.astimezone(timezone.utc).isoformat(),
        end=end.isoformat(),
    )

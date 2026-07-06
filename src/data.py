from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.alpaca_client import AlpacaClient


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

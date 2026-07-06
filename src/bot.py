from __future__ import annotations

import logging

from src.alpaca_client import AlpacaClient, AlpacaError
from src.broker import submit_or_preview_order
from src.config import Settings, load_settings
from src.data import load_recent_bars
from src.journal import TradeJournal
from src.risk import RiskLimits, evaluate_signal
from src.strategy import evaluate_short_spike_asset, vwap_spike_short_signal


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


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


def run_once(settings: Settings | None = None) -> int:
    settings = settings or load_settings()
    client = build_client(settings)
    journal = TradeJournal(settings.journal_path)

    try:
        account = client.get_account()
        positions = client.get_positions()
        clock = client.get_clock()
    except AlpacaError as exc:
        logging.error("Unable to load account state: %s", exc)
        return 1

    if not clock.get("is_open"):
        journal.record("market_closed", {"clock": clock})
        logging.info("Market is closed; skipping regular-hours strategy run.")
        return 0

    limits = RiskLimits(
        allowed_symbols=settings.symbols,
        max_notional_per_order=settings.max_notional_per_order,
        min_cash_reserve=settings.min_cash_reserve,
        can_submit_orders=settings.can_submit_orders,
    )

    for symbol in settings.symbols:
        try:
            asset = client.get_asset(symbol)
        except AlpacaError as exc:
            logging.error("[%s] Unable to load asset details: %s", symbol, exc)
            continue

        eligibility = evaluate_short_spike_asset(asset)
        if not eligibility.eligible:
            journal.record(
                "asset_rejected",
                {"symbol": symbol, "reasons": eligibility.reasons, "asset": asset},
            )
            logging.info("[%s] skipped: %s", symbol, "; ".join(eligibility.reasons))
            continue

        try:
            bars = load_recent_bars(
                client,
                symbol,
                timeframe=settings.timeframe,
                limit=settings.bar_limit,
                lookback_days=settings.market_data_lookback_days,
            )
        except AlpacaError as exc:
            logging.error("[%s] Unable to load bars: %s", symbol, exc)
            continue

        signal = vwap_spike_short_signal(
            symbol,
            bars,
            position=_position_for_symbol(positions, symbol),
            notional=settings.short_spike_notional,
        )
        decision = evaluate_signal(signal, account, limits, positions)

        journal.record(
            "decision",
            {
                "symbol": symbol,
                "signal": signal,
                "risk_decision": decision,
                "bar_count": len(bars),
                "asset_eligibility": eligibility,
                "dry_run": settings.dry_run,
                "enable_trading": settings.enable_trading,
            },
        )

        logging.info(
            "[%s] signal=%s reason=%s risk=%s",
            symbol,
            signal.side,
            signal.reason,
            decision.reason,
        )

        if (
            not decision.approved
            and settings.dry_run
            and decision.reason == "trading disabled or dry-run enabled"
            and signal.side in {"buy", "sell"}
        ):
            preview = submit_or_preview_order(client, signal, dry_run=True)
            journal.record("order_preview", {"symbol": symbol, "result": preview})
            logging.info("[%s] dry-run preview=%s", symbol, preview)
            continue

        if decision.approved:
            result = submit_or_preview_order(client, signal, dry_run=settings.dry_run)
            journal.record("order_result", {"symbol": symbol, "result": result})
            logging.info("[%s] order result=%s", symbol, result)

    return 0


if __name__ == "__main__":
    raise SystemExit(run_once())

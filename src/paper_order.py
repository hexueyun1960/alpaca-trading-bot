from __future__ import annotations

import argparse
import logging
from typing import Sequence

from src.alpaca_client import AlpacaError
from src.bot import build_client
from src.broker import submit_or_preview_order
from src.config import Settings, load_settings
from src.journal import TradeJournal
from src.risk import RiskLimits, evaluate_signal
from src.strategy import PositionIntent, Signal


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


POSITION_INTENTS: tuple[PositionIntent, ...] = (
    "buy_to_open",
    "buy_to_close",
    "sell_to_open",
    "sell_to_close",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Submit or preview one guarded Alpaca paper market order.",
    )
    parser.add_argument("--symbol", required=True, help="Ticker symbol, for example SPY.")
    parser.add_argument("--side", required=True, choices=("buy", "sell"))

    size = parser.add_mutually_exclusive_group(required=True)
    size.add_argument("--notional", type=float, help="Dollar amount to trade.")
    size.add_argument("--qty", type=float, help="Share quantity to trade.")

    parser.add_argument(
        "--position-intent",
        choices=POSITION_INTENTS,
        help="Defaults to buy_to_open for buys and sell_to_open for sells.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required when settings allow a real paper order submission.",
    )
    parser.add_argument(
        "--allow-queued",
        action="store_true",
        help="Allow submitting while the market is closed so Alpaca may queue the day order.",
    )
    parser.add_argument(
        "--status-attempts",
        type=int,
        default=3,
        help="How many times to query the order after submission.",
    )
    parser.add_argument(
        "--status-delay-seconds",
        type=float,
        default=1.0,
        help="Delay between post-submit status queries.",
    )
    return parser


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _default_position_intent(side: str) -> PositionIntent:
    return "buy_to_open" if side == "buy" else "sell_to_open"


def build_signal_from_args(args: argparse.Namespace) -> Signal:
    position_intent = args.position_intent or _default_position_intent(args.side)
    notional = float(args.notional or 0)
    qty = float(args.qty) if args.qty is not None else None
    return Signal(
        symbol=args.symbol.upper(),
        side=args.side,
        reason="manual paper order command",
        notional=notional,
        qty=qty,
        position_intent=position_intent,
    )


def validate_order_args(args: argparse.Namespace) -> list[str]:
    errors = []
    if args.notional is not None and args.notional <= 0:
        errors.append("--notional must be positive")
    if args.qty is not None and args.qty <= 0:
        errors.append("--qty must be positive")
    if args.qty is not None and args.position_intent not in {"buy_to_close", "sell_to_close"}:
        errors.append("--qty is only allowed with buy_to_close or sell_to_close")
    if args.status_attempts < 1:
        errors.append("--status-attempts must be at least 1")
    if args.status_delay_seconds < 0:
        errors.append("--status-delay-seconds cannot be negative")
    return errors


def _asset_rejection_reasons(asset: dict, signal: Signal) -> list[str]:
    reasons = []
    if str(asset.get("status", "")).lower() != "active":
        reasons.append("asset must be active")
    if not _as_bool(asset.get("tradable")):
        reasons.append("asset must be tradable")
    if signal.position_intent == "sell_to_open":
        if not _as_bool(asset.get("shortable")):
            reasons.append("asset must be shortable")
        if not _as_bool(asset.get("easy_to_borrow")):
            reasons.append("asset must be easy to borrow")
    return reasons


def run_paper_order(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = settings or load_settings()

    errors = validate_order_args(args)
    if errors:
        for error in errors:
            logging.error(error)
        return 2

    if not settings.is_paper_base_url:
        logging.error("Refusing paper_order command because ALPACA_BASE_URL is not the paper endpoint.")
        return 1

    if settings.can_submit_orders and not args.confirm:
        logging.error("Refusing to submit a paper order without --confirm.")
        return 1

    client = build_client(settings)
    journal = TradeJournal(settings.journal_path)
    signal = build_signal_from_args(args)

    try:
        account = client.get_account()
        positions = client.get_positions()
        clock = client.get_clock()
        asset = client.get_asset(signal.symbol)
    except AlpacaError as exc:
        journal.record("paper_order_error", {"symbol": signal.symbol, "error": str(exc)})
        logging.error("[%s] unable to load order context: %s", signal.symbol, exc)
        return 1

    if settings.can_submit_orders and not clock.get("is_open") and not args.allow_queued:
        journal.record("paper_order_market_closed", {"symbol": signal.symbol, "clock": clock})
        logging.error("[%s] market is closed; use --allow-queued to allow a queued day order.", signal.symbol)
        return 1

    asset_reasons = _asset_rejection_reasons(asset, signal)
    if asset_reasons:
        journal.record(
            "paper_order_asset_rejected",
            {"symbol": signal.symbol, "reasons": asset_reasons, "asset": asset},
        )
        logging.error("[%s] asset rejected: %s", signal.symbol, "; ".join(asset_reasons))
        return 1

    limits = RiskLimits(
        allowed_symbols=settings.symbols,
        max_notional_per_order=settings.max_notional_per_order,
        min_cash_reserve=settings.min_cash_reserve,
        can_submit_orders=settings.can_submit_orders,
    )
    decision = evaluate_signal(signal, account, limits, positions)
    journal.record(
        "paper_order_decision",
        {
            "symbol": signal.symbol,
            "signal": signal,
            "risk_decision": decision,
            "dry_run": settings.dry_run,
            "enable_trading": settings.enable_trading,
        },
    )

    if not decision.approved:
        if settings.dry_run and decision.reason == "trading disabled or dry-run enabled":
            preview = submit_or_preview_order(client, signal, dry_run=True)
            journal.record("paper_order_preview", {"symbol": signal.symbol, "result": preview})
            logging.info("[%s] dry-run preview=%s", signal.symbol, preview)
            return 0

        logging.error("[%s] risk rejected order: %s", signal.symbol, decision.reason)
        return 1

    try:
        result = submit_or_preview_order(
            client,
            signal,
            dry_run=settings.dry_run,
            wait_for_status=True,
            status_attempts=args.status_attempts,
            status_delay_seconds=args.status_delay_seconds,
        )
    except AlpacaError as exc:
        journal.record("paper_order_error", {"symbol": signal.symbol, "signal": signal, "error": str(exc)})
        logging.error("[%s] order failed: %s", signal.symbol, exc)
        return 1

    journal.record("paper_order_result", {"symbol": signal.symbol, "result": result})
    logging.info("[%s] paper order result=%s", signal.symbol, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_paper_order())

import unittest
from pathlib import Path
from unittest.mock import patch

from src.config import Settings
from src.paper_order import build_parser, build_signal_from_args, run_paper_order, validate_order_args


def make_settings(**overrides):
    values = {
        "api_key_id": "key",
        "api_secret_key": "secret",
        "base_url": "https://paper-api.alpaca.markets",
        "data_url": "https://data.alpaca.markets",
        "symbols": ["SPY"],
        "timeframe": "1Min",
        "bar_limit": 60,
        "market_data_lookback_days": 5,
        "max_notional_per_order": 500,
        "short_spike_notional": 500,
        "min_cash_reserve": 1000,
        "dry_run": True,
        "enable_trading": False,
        "journal_path": "logs/trade_journal.jsonl",
        "monitor_interval_seconds": 60,
    }
    values.update(overrides)
    return Settings(**values)


class PaperOrderTests(unittest.TestCase):
    def test_builds_manual_buy_signal_with_default_intent(self):
        args = build_parser().parse_args(["--symbol", "spy", "--side", "buy", "--notional", "25"])

        signal = build_signal_from_args(args)

        self.assertEqual(signal.symbol, "SPY")
        self.assertEqual(signal.side, "buy")
        self.assertEqual(signal.notional, 25)
        self.assertEqual(signal.position_intent, "buy_to_open")

    def test_builds_manual_short_signal_with_default_intent(self):
        args = build_parser().parse_args(["--symbol", "SPY", "--side", "sell", "--notional", "25"])

        signal = build_signal_from_args(args)

        self.assertEqual(signal.position_intent, "sell_to_open")

    def test_rejects_qty_for_open_orders(self):
        args = build_parser().parse_args(["--symbol", "SPY", "--side", "buy", "--qty", "1"])

        errors = validate_order_args(args)

        self.assertIn("--qty is only allowed with buy_to_close or sell_to_close", errors)

    def test_refuses_non_paper_endpoint(self):
        exit_code = run_paper_order(
            ["--symbol", "SPY", "--side", "buy", "--notional", "25"],
            settings=make_settings(base_url="https://api.alpaca.markets"),
        )

        self.assertEqual(exit_code, 1)

    def test_requires_confirm_when_real_paper_submission_is_enabled(self):
        exit_code = run_paper_order(
            ["--symbol", "SPY", "--side", "buy", "--notional", "25"],
            settings=make_settings(dry_run=False, enable_trading=True),
        )

        self.assertEqual(exit_code, 1)

    def test_dry_run_preview_is_allowed_while_market_is_closed(self):
        class FakeClient:
            def get_account(self):
                return {"cash": "5000"}

            def get_positions(self):
                return []

            def get_clock(self):
                return {"is_open": False}

            def get_asset(self, _symbol):
                return {
                    "symbol": "SPY",
                    "status": "active",
                    "tradable": True,
                    "shortable": True,
                    "easy_to_borrow": True,
                }

        journal_path = Path("logs/test_paper_order_preview.jsonl")
        journal_path.unlink(missing_ok=True)
        self.addCleanup(lambda: journal_path.unlink(missing_ok=True))

        with patch("src.paper_order.build_client", return_value=FakeClient()):
            exit_code = run_paper_order(
                ["--symbol", "SPY", "--side", "buy", "--notional", "25"],
                settings=make_settings(journal_path=str(journal_path)),
            )

        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()

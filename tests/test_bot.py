import json
import unittest
from pathlib import Path
from unittest.mock import patch

from src.bot import SYMBOL_STATES, run_once
from src.config import Settings


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
        "max_notional_per_order": 0,
        "short_spike_notional": 500,
        "min_cash_reserve": 1000,
        "dry_run": True,
        "enable_trading": False,
        "journal_path": "logs/test_bot_vwap.jsonl",
        "monitor_interval_seconds": 60,
    }
    values.update(overrides)
    return Settings(**values)


class FakeClient:
    def __init__(self, bars, daily_bars=None):
        self.bars = bars
        self.daily_bars = daily_bars or [{"v": 5_000_000} for _ in range(30)]

    def get_account(self):
        return {"cash": "100000", "buying_power": "100000", "equity": "10000"}

    def get_positions(self):
        return []

    def get_orders(self, status="open"):
        return []

    def get_clock(self):
        return {"is_open": True, "timestamp": "2026-07-08T14:00:00Z"}

    def get_asset(self, symbol):
        return {
            "symbol": symbol,
            "status": "active",
            "tradable": True,
            "shortable": True,
            "easy_to_borrow": True,
        }

    def get_latest_quote(self, _symbol):
        return {"bp": 103.99, "ap": 104.0}

    def get_stock_bars(self, _symbol, *, timeframe, **_kwargs):
        if timeframe == "1Day":
            return self.daily_bars
        return self.bars


class BotVwapCycleTests(unittest.TestCase):
    def setUp(self):
        SYMBOL_STATES.clear()
        self.journal_path = Path("logs/test_bot_vwap.jsonl")
        self.journal_path.unlink(missing_ok=True)

    def tearDown(self):
        self.journal_path.unlink(missing_ok=True)
        SYMBOL_STATES.clear()

    def _events(self):
        return [
            json.loads(line)["event_type"]
            for line in self.journal_path.read_text(encoding="utf-8").splitlines()
        ]

    def test_dry_run_simulates_entry_second_order_and_vwap_exit(self):
        settings = make_settings(journal_path=str(self.journal_path))

        with patch("src.bot.build_client", return_value=FakeClient([{"c": 104, "vw": 100}])):
            self.assertEqual(run_once(settings), 0)

        self.assertIn("entry_order_preview", self._events())
        self.assertIn("second_order_preview", self._events())

        with patch("src.bot.build_client", return_value=FakeClient([{"c": 100, "vw": 100}])):
            self.assertEqual(run_once(settings), 0)

        events = self._events()
        self.assertIn("vwap_exit_signal", events)
        self.assertIn("position_close_submitted", events)
        self.assertIn("closed_for_day", events)


if __name__ == "__main__":
    unittest.main()

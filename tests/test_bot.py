import json
import unittest
from pathlib import Path
from unittest.mock import patch

from src.alpaca_client import AlpacaError
from src.bot import SYMBOL_STATES, UNIVERSE_CACHE, run_once
from src.config import Settings


def vwap_bar(close, vwap, volume=1000):
    return {"c": close, "vw": vwap, "v": volume}


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
    def __init__(
        self,
        bars,
        daily_bars=None,
        assets=None,
        timestamp="2026-07-08T14:00:00Z",
        fail_get_assets=False,
    ):
        self.bars = bars
        self.daily_bars = daily_bars or [{"c": 100, "v": 5_000_000} for _ in range(30)]
        self.timestamp = timestamp
        self.fail_get_assets = fail_get_assets
        self.assets = assets or [
            {
                "symbol": "SPY",
                "class": "us_equity",
                "status": "active",
                "tradable": True,
                "shortable": True,
                "easy_to_borrow": True,
            }
        ]

    def get_account(self):
        return {"cash": "100000", "buying_power": "100000", "equity": "10000"}

    def get_positions(self):
        return []

    def get_orders(self, status="open"):
        return []

    def get_clock(self):
        return {"is_open": True, "timestamp": self.timestamp}

    def get_asset(self, symbol):
        return {
            "symbol": symbol,
            "status": "active",
            "tradable": True,
            "shortable": True,
            "easy_to_borrow": True,
        }

    def get_assets(self, **_kwargs):
        if self.fail_get_assets:
            raise AlpacaError("assets unavailable")
        return self.assets

    def get_latest_quote(self, _symbol):
        return {"bp": 103.99, "ap": 104.0}

    def get_stock_bars(self, _symbol, *, timeframe, **_kwargs):
        if timeframe == "1Day":
            return self.daily_bars
        return self.bars

    def get_stock_bars_multi(self, symbols, *, timeframe, **_kwargs):
        if timeframe != "1Day":
            return {symbol: self.bars for symbol in symbols}
        return {symbol: self.daily_bars for symbol in symbols}


class BotVwapCycleTests(unittest.TestCase):
    def setUp(self):
        SYMBOL_STATES.clear()
        UNIVERSE_CACHE.clear()
        self.journal_path = Path("logs/test_bot_vwap.jsonl")
        self.journal_path.unlink(missing_ok=True)

    def tearDown(self):
        self.journal_path.unlink(missing_ok=True)
        SYMBOL_STATES.clear()
        UNIVERSE_CACHE.clear()

    def _events(self):
        if not self.journal_path.exists():
            return []
        return [
            json.loads(line)["event_type"]
            for line in self.journal_path.read_text(encoding="utf-8").splitlines()
        ]

    def _records(self):
        if not self.journal_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.journal_path.read_text(encoding="utf-8").splitlines()
        ]

    def test_dry_run_simulates_entry_second_order_and_vwap_exit(self):
        settings = make_settings(journal_path=str(self.journal_path), enable_second_entry=True, fixed_entry_notional=240)

        with patch("src.bot.build_client", return_value=FakeClient([vwap_bar(104, 100)])):
            self.assertEqual(run_once(settings), 0)

        self.assertIn("entry_order_preview", self._events())

        with patch("src.bot.build_client", return_value=FakeClient([vwap_bar(108.2, 100)])):
            self.assertEqual(run_once(settings), 0)

        self.assertIn("second_order_preview", self._events())

        with patch("src.bot.build_client", return_value=FakeClient([vwap_bar(100, 100)])):
            self.assertEqual(run_once(settings), 0)

        events = self._events()
        self.assertIn("vwap_exit_signal", events)
        self.assertIn("position_close_submitted", events)
        self.assertIn("closed_for_day", events)

        records = self._records()
        entry = next(record for record in records if record["event_type"] == "entry_order_preview")
        second = next(record for record in records if record["event_type"] == "second_order_preview")
        exit_record = next(record for record in records if record["event_type"] == "position_close_submitted")
        self.assertEqual(entry["payload"]["order_type"], "market")
        self.assertEqual(second["payload"]["order_type"], "market")
        self.assertEqual(exit_record["payload"]["order_type"], "market")

    def test_dynamic_universe_uses_only_active_tradable_etb_before_symbol_cap(self):
        settings = make_settings(
            symbols=["SPY"],
            dynamic_universe=True,
            universe_max_symbols=1,
            journal_path=str(self.journal_path),
        )
        assets = [
            {
                "symbol": "MSFT",
                "class": "us_equity",
                "exchange": "NASDAQ",
                "status": "active",
                "tradable": True,
                "shortable": True,
                "easy_to_borrow": True,
            },
            {
                "symbol": "AAPL",
                "class": "us_equity",
                "exchange": "NASDAQ",
                "status": "active",
                "tradable": True,
                "shortable": True,
                "easy_to_borrow": True,
            },
            {
                "symbol": "ZZZZ",
                "class": "us_equity",
                "exchange": "NASDAQ",
                "status": "active",
                "tradable": True,
                "shortable": True,
                "easy_to_borrow": False,
            },
        ]

        with patch(
            "src.bot.build_client",
            return_value=FakeClient([vwap_bar(104, 100)], assets=assets),
        ):
            self.assertEqual(run_once(settings), 0)

        records = self._records()
        selected = next(record for record in records if record["event_type"] == "dynamic_universe_selected")
        entry = next(record for record in records if record["event_type"] == "entry_order_preview")

        self.assertEqual(selected["payload"]["symbols"], ["AAPL"])
        self.assertEqual(selected["payload"]["basic_eligible_count"], 2)
        self.assertEqual(selected["payload"]["etb_filtered_count"], 2)
        self.assertEqual(selected["payload"]["stage"], "active_tradable_etb")
        self.assertEqual(entry["payload"]["symbol"], "AAPL")

    def test_dynamic_universe_zero_max_scans_all_selected_assets(self):
        settings = make_settings(
            symbols=["SPY"],
            dynamic_universe=True,
            universe_max_symbols=0,
            journal_path=str(self.journal_path),
        )
        assets = [
            {
                "symbol": "MSFT",
                "class": "us_equity",
                "exchange": "NASDAQ",
                "status": "active",
                "tradable": True,
                "shortable": True,
                "easy_to_borrow": True,
            },
            {
                "symbol": "AAPL",
                "class": "us_equity",
                "exchange": "NASDAQ",
                "status": "active",
                "tradable": True,
                "shortable": True,
                "easy_to_borrow": True,
            },
        ]

        with patch(
            "src.bot.build_client",
            return_value=FakeClient([vwap_bar(104, 100)], assets=assets),
        ):
            self.assertEqual(run_once(settings), 0)

        records = self._records()
        selected = next(record for record in records if record["event_type"] == "dynamic_universe_selected")
        entries = [record for record in records if record["event_type"] == "entry_order_preview"]

        self.assertEqual(selected["payload"]["symbols"], ["AAPL", "MSFT"])
        self.assertFalse(selected["payload"]["universe_limited"])
        self.assertEqual([entry["payload"]["symbol"] for entry in entries], ["AAPL", "MSFT"])

    def test_skips_new_entries_after_1530_new_york_time(self):
        settings = make_settings(journal_path=str(self.journal_path))

        with patch(
            "src.bot.build_client",
            return_value=FakeClient(
                [vwap_bar(104, 100)],
                timestamp="2026-07-08T19:31:00Z",
            ),
        ):
            self.assertEqual(run_once(settings), 0)

        self.assertNotIn("entry_order_preview", self._events())

    def test_dynamic_universe_failure_does_not_fallback_to_new_entries(self):
        settings = make_settings(
            symbols=["SPY"],
            dynamic_universe=True,
            journal_path=str(self.journal_path),
        )

        with patch(
            "src.bot.build_client",
            return_value=FakeClient(
                [vwap_bar(104, 100)],
                fail_get_assets=True,
            ),
        ):
            self.assertEqual(run_once(settings), 0)

        events = self._events()
        self.assertIn("dynamic_universe_error", events)
        self.assertNotIn("entry_order_preview", events)


if __name__ == "__main__":
    unittest.main()

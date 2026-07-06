import unittest

from src.config import Settings
from src.doctor import validate_settings


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


class DoctorTests(unittest.TestCase):
    def test_valid_settings_pass(self):
        checks = validate_settings(make_settings())

        self.assertTrue(all(check.ok for check in checks))

    def test_missing_key_fails(self):
        checks = validate_settings(make_settings(api_key_id=""))

        self.assertFalse(all(check.ok for check in checks))

    def test_live_order_guard_allows_enabled_paper_orders(self):
        checks = validate_settings(make_settings(dry_run=False, enable_trading=True))
        guard = next(check for check in checks if check.name == "live_order_guard")

        self.assertTrue(guard.ok)

    def test_live_order_guard_rejects_enabled_non_paper_orders(self):
        checks = validate_settings(
            make_settings(
                base_url="https://api.alpaca.markets",
                dry_run=False,
                enable_trading=True,
            )
        )
        guard = next(check for check in checks if check.name == "live_order_guard")

        self.assertFalse(guard.ok)

    def test_rejects_too_short_monitor_interval(self):
        checks = validate_settings(make_settings(monitor_interval_seconds=1))
        interval = next(check for check in checks if check.name == "monitor_interval_seconds")

        self.assertFalse(interval.ok)


if __name__ == "__main__":
    unittest.main()

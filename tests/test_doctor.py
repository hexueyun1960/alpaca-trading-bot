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

    def test_settings_blocks_live_orders_without_explicit_allow(self):
        settings = make_settings(
            base_url="https://api.alpaca.markets",
            dry_run=False,
            enable_trading=True,
        )

        self.assertFalse(settings.can_submit_orders)

    def test_settings_allows_live_orders_only_when_explicitly_allowed(self):
        settings = make_settings(
            base_url="https://api.alpaca.markets",
            dry_run=False,
            enable_trading=True,
            allow_live_trading=True,
        )

        self.assertTrue(settings.can_submit_orders)

    def test_rejects_too_short_monitor_interval(self):
        checks = validate_settings(make_settings(monitor_interval_seconds=1))
        interval = next(check for check in checks if check.name == "monitor_interval_seconds")

        self.assertFalse(interval.ok)

    def test_dynamic_universe_zero_max_means_unlimited(self):
        checks = validate_settings(make_settings(dynamic_universe=True, universe_max_symbols=0))
        dynamic_universe = next(check for check in checks if check.name == "dynamic_universe")

        self.assertTrue(dynamic_universe.ok)
        self.assertEqual(dynamic_universe.message, "dynamic universe max symbols is unlimited after prefilters")

    def test_dynamic_universe_rejects_negative_max(self):
        checks = validate_settings(make_settings(dynamic_universe=True, universe_max_symbols=-1))
        dynamic_universe = next(check for check in checks if check.name == "dynamic_universe")

        self.assertFalse(dynamic_universe.ok)

    def test_second_entry_requires_hard_stop_beyond_trigger(self):
        checks = validate_settings(
            make_settings(
                enable_second_entry=True,
                second_order_distance_pct=4,
                hard_stop_distance_pct=4,
            )
        )
        hard_stop_check = next(check for check in checks if check.name == "hard_stop_vs_second_entry")

        self.assertFalse(hard_stop_check.ok)


if __name__ == "__main__":
    unittest.main()

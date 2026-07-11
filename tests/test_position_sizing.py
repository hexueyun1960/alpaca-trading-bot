import unittest

from src.config import Settings
from src.position_sizing import calculate_position_size


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
        "dry_run": False,
        "enable_trading": True,
        "journal_path": "logs/trade_journal.jsonl",
        "monitor_interval_seconds": 60,
        "first_order_equity_pct": 8,
        "max_loss_per_symbol_equity_pct": 1,
        "min_buying_power_reserve": 1000,
    }
    values.update(overrides)
    return Settings(**values)


class PositionSizingTests(unittest.TestCase):
    def test_requires_hard_stop_when_configured(self):
        size = calculate_position_size(
            settings=make_settings(require_hard_stop=True),
            equity=10_000,
            buying_power=10_000,
            entry_price=10,
            hard_stop_price=None,
        )

        self.assertFalse(size.approved)
        self.assertEqual(size.reason, "hard stop is required")

    def test_takes_minimum_of_notional_and_risk_limits(self):
        size = calculate_position_size(
            settings=make_settings(max_notional_per_order=500),
            equity=10_000,
            buying_power=10_000,
            entry_price=10,
            hard_stop_price=12,
        )

        self.assertTrue(size.approved)
        self.assertEqual(size.notional, 500)
        self.assertEqual(size.qty, 50)


if __name__ == "__main__":
    unittest.main()

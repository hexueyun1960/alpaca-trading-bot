import unittest

from src.config import Settings
from src.monitor import run_monitor


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


class MonitorTests(unittest.TestCase):
    def test_runs_configured_number_of_cycles(self):
        cycles = []
        sleeps = []

        def run_cycle(settings):
            cycles.append(settings.symbols)
            return 0

        exit_code = run_monitor(
            make_settings(),
            run_cycle=run_cycle,
            sleep=sleeps.append,
            max_cycles=3,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(cycles), 3)
        self.assertEqual(sleeps, [60, 60])

    def test_continues_after_cycle_exception(self):
        calls = []

        def run_cycle(_settings):
            calls.append("called")
            if len(calls) == 1:
                raise RuntimeError("boom")
            return 0

        exit_code = run_monitor(
            make_settings(),
            run_cycle=run_cycle,
            sleep=lambda _seconds: None,
            max_cycles=2,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()

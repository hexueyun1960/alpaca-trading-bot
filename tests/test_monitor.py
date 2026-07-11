import unittest
from types import SimpleNamespace

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


class FakeJournal:
    def __init__(self):
        self.events = []

    def record(self, event_type, payload):
        self.events.append({"event_type": event_type, "payload": payload})


class MonitorTests(unittest.TestCase):
    def test_runs_configured_number_of_cycles(self):
        cycles = []
        sleeps = []
        journal = FakeJournal()

        def run_cycle(settings):
            cycles.append(settings.symbols)
            return 0

        exit_code = run_monitor(
            make_settings(),
            run_cycle=run_cycle,
            sleep=sleeps.append,
            journal_factory=lambda _path: journal,
            reconcile=lambda **_kwargs: SimpleNamespace(ok=True),
            max_cycles=3,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(cycles), 3)
        self.assertEqual(sleeps, [60, 60])
        self.assertEqual(
            [event["event_type"] for event in journal.events],
            [
                "monitor_started",
                "monitor_cycle_started",
                "monitor_cycle_finished",
                "monitor_cycle_started",
                "monitor_cycle_finished",
                "monitor_cycle_started",
                "monitor_cycle_finished",
                "monitor_stopped",
            ],
        )

    def test_continues_after_cycle_exception(self):
        calls = []
        journal = FakeJournal()

        def run_cycle(_settings):
            calls.append("called")
            if len(calls) == 1:
                raise RuntimeError("boom")
            return 0

        exit_code = run_monitor(
            make_settings(),
            run_cycle=run_cycle,
            sleep=lambda _seconds: None,
            journal_factory=lambda _path: journal,
            reconcile=lambda **_kwargs: SimpleNamespace(ok=True),
            max_cycles=2,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(calls), 2)
        self.assertIn("monitor_cycle_error", [event["event_type"] for event in journal.events])


if __name__ == "__main__":
    unittest.main()

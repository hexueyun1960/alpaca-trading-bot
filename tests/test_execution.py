import tempfile
import unittest
from pathlib import Path

from src.config import Settings
from src.execution import acquire_execution_context


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
    }
    values.update(overrides)
    return Settings(**values)


class ExecutionContextTests(unittest.TestCase):
    def test_only_one_context_gets_file_leader_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "leader.lock")
            first = acquire_execution_context(make_settings(leader_lock_path=path), "rest")
            second = acquire_execution_context(make_settings(leader_lock_path=path), "stream")

            self.assertTrue(first.leader_lock_acquired)
            self.assertFalse(second.leader_lock_acquired)
            self.assertEqual(second.effective_mode, "shadow")

            first.release()
            second.release()

    def test_execution_mode_mismatch_forces_shadow(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = acquire_execution_context(
                make_settings(leader_lock_path=str(Path(tmp) / "leader.lock"), execution_mode="rest"),
                "stream",
            )

            self.assertEqual(context.effective_mode, "shadow")
            self.assertFalse(context.can_submit_orders)
            context.release()

    def test_shadow_mode_does_not_acquire_execution_leader_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "leader.lock")
            shadow = acquire_execution_context(make_settings(leader_lock_path=path, execution_mode="shadow"), "stream")
            rest = acquire_execution_context(make_settings(leader_lock_path=path, execution_mode="rest"), "rest")

            self.assertFalse(shadow.leader_lock_acquired)
            self.assertEqual(shadow.effective_mode, "shadow")
            self.assertTrue(rest.leader_lock_acquired)

            shadow.release()
            rest.release()


if __name__ == "__main__":
    unittest.main()

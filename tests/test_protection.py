import json
import unittest
from pathlib import Path

from src.alpaca_client import AlpacaError
from src.broker import build_protective_stop_order
from src.config import Settings
from src.journal import TradeJournal
from src.protection import ProtectiveStopManager


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
        "journal_path": "logs/test_protection.jsonl",
        "monitor_interval_seconds": 60,
        "hard_stop_distance_pct": 6,
    }
    values.update(overrides)
    return Settings(**values)


class FakeProtectionClient:
    def __init__(self, *, fail_submit=False):
        self.fail_submit = fail_submit
        self.orders = []
        self.closed_symbols = []

    def submit_order(self, order):
        if self.fail_submit:
            raise AlpacaError("stop rejected before accept")
        self.orders.append(order)
        return {"id": f"order-{len(self.orders)}", "status": "accepted"}

    def close_position(self, symbol):
        self.closed_symbols.append(symbol)
        return {"symbol": symbol, "status": "close_submitted"}


class ProtectiveStopTests(unittest.TestCase):
    def setUp(self):
        self.journal_path = Path("logs/test_protection.jsonl")
        self.journal_path.unlink(missing_ok=True)

    def tearDown(self):
        self.journal_path.unlink(missing_ok=True)

    def records(self):
        if not self.journal_path.exists():
            return []
        return [json.loads(line) for line in self.journal_path.read_text(encoding="utf-8").splitlines()]

    def test_builds_short_protective_buy_stop(self):
        order = build_protective_stop_order(
            symbol="aapl",
            position_direction="short",
            qty=30,
            stop_price=106,
            client_order_id="MR-AAPL-STP01",
        )

        self.assertEqual(order["symbol"], "AAPL")
        self.assertEqual(order["side"], "buy")
        self.assertEqual(order["type"], "stop")
        self.assertEqual(order["stop_price"], "106")
        self.assertEqual(order["position_intent"], "buy_to_close")

    def test_partial_fills_append_incremental_protective_stops(self):
        client = FakeProtectionClient()
        manager = ProtectiveStopManager(
            settings=make_settings(journal_path=str(self.journal_path)),
            client=client,
            journal=TradeJournal(str(self.journal_path)),
        )

        manager.on_entry_fill_update(
            client_order_id="MR-AAPL-SELL-20260710T153015Z-01",
            event="partial_fill",
            order={
                "symbol": "AAPL",
                "side": "sell",
                "position_intent": "sell_to_open",
                "filled_qty": "30",
                "filled_avg_price": "100",
            },
        )
        manager.on_entry_fill_update(
            client_order_id="MR-AAPL-SELL-20260710T153015Z-01",
            event="partial_fill",
            order={
                "symbol": "AAPL",
                "side": "sell",
                "position_intent": "sell_to_open",
                "filled_qty": "50",
                "filled_avg_price": "101",
            },
        )

        self.assertEqual([order["qty"] for order in client.orders], ["30.0", "20.0"])
        self.assertEqual([order["side"] for order in client.orders], ["buy", "buy"])
        self.assertEqual(client.orders[0]["stop_price"], "106.0")
        self.assertEqual(client.orders[1]["stop_price"], "107.06")
        state = manager.symbols["AAPL"]
        self.assertEqual(state.protected_qty, 50)
        self.assertEqual(state.state, "STOP_ACCEPTED")

    def test_stop_submit_failure_triggers_emergency_exit_and_global_halt(self):
        client = FakeProtectionClient(fail_submit=True)
        manager = ProtectiveStopManager(
            settings=make_settings(journal_path=str(self.journal_path)),
            client=client,
            journal=TradeJournal(str(self.journal_path)),
        )

        manager.on_entry_fill_update(
            client_order_id="MR-AAPL-SELL-20260710T153015Z-01",
            event="partial_fill",
            order={
                "symbol": "AAPL",
                "side": "sell",
                "position_intent": "sell_to_open",
                "filled_qty": "30",
                "filled_avg_price": "100",
            },
        )

        self.assertTrue(manager.global_halt)
        self.assertEqual(client.closed_symbols, ["AAPL"])
        self.assertEqual(manager.symbols["AAPL"].state, "EMERGENCY_EXIT")
        self.assertIn("emergency_exit_submitted", [record["event_type"] for record in self.records()])

    def test_broker_rejected_protective_stop_triggers_emergency_exit(self):
        client = FakeProtectionClient()
        manager = ProtectiveStopManager(
            settings=make_settings(journal_path=str(self.journal_path)),
            client=client,
            journal=TradeJournal(str(self.journal_path)),
        )
        manager.on_entry_fill_update(
            client_order_id="MR-AAPL-SELL-20260710T153015Z-01",
            event="partial_fill",
            order={
                "symbol": "AAPL",
                "side": "sell",
                "position_intent": "sell_to_open",
                "filled_qty": "30",
                "filled_avg_price": "100",
            },
        )
        stop_client_order_id = client.orders[0]["client_order_id"]

        manager.on_stop_trade_update(
            client_order_id=stop_client_order_id,
            event="rejected",
            order={"symbol": "AAPL"},
        )

        self.assertTrue(manager.global_halt)
        self.assertEqual(client.closed_symbols, ["AAPL"])
        self.assertEqual(manager.symbols["AAPL"].state, "EMERGENCY_EXIT")


if __name__ == "__main__":
    unittest.main()

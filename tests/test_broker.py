import unittest

from src.broker import build_market_order, submit_or_preview_order, wait_for_order_status
from src.strategy import Signal


class FakeClient:
    def __init__(self):
        self.submitted_orders = []
        self.status_calls = 0

    def submit_order(self, order):
        self.submitted_orders.append(order)
        return {"id": "order-1", "status": "accepted"}

    def get_order(self, order_id):
        self.status_calls += 1
        if self.status_calls == 1:
            return {"id": order_id, "status": "accepted"}
        return {"id": order_id, "status": "filled"}


class BrokerTests(unittest.TestCase):
    def test_builds_short_open_market_order(self):
        order = build_market_order(
            Signal(
                symbol="SPY",
                side="sell",
                reason="test",
                notional=500,
                position_intent="sell_to_open",
            )
        )

        self.assertEqual(order["side"], "sell")
        self.assertEqual(order["notional"], "500")
        self.assertEqual(order["position_intent"], "sell_to_open")
        self.assertFalse(order["extended_hours"])

    def test_builds_short_close_market_order_with_qty(self):
        order = build_market_order(
            Signal(
                symbol="SPY",
                side="buy",
                reason="test",
                qty=1.2345678,
                position_intent="buy_to_close",
            )
        )

        self.assertEqual(order["side"], "buy")
        self.assertEqual(order["qty"], "1.234568")
        self.assertEqual(order["position_intent"], "buy_to_close")
        self.assertNotIn("notional", order)

    def test_builds_second_entry_limit_order(self):
        order = build_market_order(
            Signal(
                symbol="SPY",
                side="sell",
                reason="test",
                notional=800,
                position_intent="sell_to_open",
                order_type="limit",
                limit_price=104,
            )
        )

        self.assertEqual(order["type"], "limit")
        self.assertEqual(order["limit_price"], "104")
        self.assertEqual(order["notional"], "800")

    def test_dry_run_returns_preview_without_submission(self):
        client = FakeClient()

        result = submit_or_preview_order(
            client,
            Signal(symbol="SPY", side="buy", reason="test", notional=25),
            dry_run=True,
        )

        self.assertFalse(result["submitted"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(client.submitted_orders, [])

    def test_real_submit_can_fetch_latest_order_status(self):
        client = FakeClient()

        result = submit_or_preview_order(
            client,
            Signal(symbol="SPY", side="buy", reason="test", notional=25),
            dry_run=False,
            wait_for_status=True,
            status_delay_seconds=0,
        )

        self.assertTrue(result["submitted"])
        self.assertEqual(result["response"]["id"], "order-1")
        self.assertEqual(result["latest_status"]["status"], "filled")

    def test_wait_for_order_status_stops_on_terminal_status(self):
        client = FakeClient()

        latest = wait_for_order_status(client, "order-1", delay_seconds=0)

        self.assertEqual(latest["status"], "filled")
        self.assertEqual(client.status_calls, 2)


if __name__ == "__main__":
    unittest.main()

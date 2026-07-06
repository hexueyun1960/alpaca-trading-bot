import unittest

from src.broker import build_market_order
from src.strategy import Signal


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


if __name__ == "__main__":
    unittest.main()

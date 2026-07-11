import unittest

from src.order_state import OrderLifecycle


class OrderLifecycleTests(unittest.TestCase):
    def test_cancel_pending_blocks_replacement_until_canceled(self):
        lifecycle = OrderLifecycle(
            client_order_id="MR-20260710-XYZ-C01-E1-SHORT-A0",
            symbol="XYZ",
            side="sell",
            intended_qty=100,
        )

        lifecycle.mark_accepted("order-1")
        lifecycle.mark_cancel_pending()

        self.assertTrue(lifecycle.cancel_pending)
        self.assertFalse(lifecycle.replacement_allowed)

        lifecycle.apply_trade_update("canceled", {"filled_qty": "25", "filled_avg_price": "10.5"})

        self.assertEqual(lifecycle.state, "ENTRY_CANCELED")
        self.assertEqual(lifecycle.filled_qty, 25)
        self.assertEqual(lifecycle.remaining_qty, 75)
        self.assertTrue(lifecycle.replacement_allowed)

    def test_fill_is_terminal(self):
        lifecycle = OrderLifecycle(
            client_order_id="MR-20260710-XYZ-C01-E1-SHORT-A0",
            symbol="XYZ",
            side="sell",
            intended_qty=10,
        )

        lifecycle.mark_accepted("order-1")
        lifecycle.apply_trade_update("fill", {"filled_qty": "10", "filled_avg_price": "10"})

        self.assertEqual(lifecycle.state, "ENTRY_FILLED")
        self.assertTrue(lifecycle.is_terminal)


if __name__ == "__main__":
    unittest.main()

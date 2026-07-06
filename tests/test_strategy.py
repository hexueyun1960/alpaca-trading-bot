import unittest

from src.strategy import (
    evaluate_short_spike_asset,
    moving_average_signal,
    vwap_spike_short_signal,
)


def bars_from_closes(closes):
    return [{"c": close} for close in closes]


class MovingAverageSignalTests(unittest.TestCase):
    def test_buy_when_short_average_above_long_average(self):
        bars = bars_from_closes([10] * 20 + [20] * 5)
        signal = moving_average_signal("SPY", bars, short_window=5, long_window=20)

        self.assertEqual(signal.side, "buy")

    def test_sell_when_short_average_below_long_average(self):
        bars = bars_from_closes([20] * 20 + [10] * 5)
        signal = moving_average_signal("SPY", bars, short_window=5, long_window=20)

        self.assertEqual(signal.side, "sell")

    def test_hold_when_not_enough_bars(self):
        signal = moving_average_signal("SPY", bars_from_closes([1, 2, 3]))

        self.assertEqual(signal.side, "hold")


class VwapSpikeShortSignalTests(unittest.TestCase):
    def test_opens_short_when_price_is_more_than_4_5_percent_above_vwap(self):
        signal = vwap_spike_short_signal("SPY", [{"c": 105, "vw": 100}])

        self.assertEqual(signal.side, "sell")
        self.assertEqual(signal.position_intent, "sell_to_open")
        self.assertEqual(signal.notional, 500)

    def test_holds_when_price_is_exactly_4_5_percent_above_vwap(self):
        signal = vwap_spike_short_signal("SPY", [{"c": 104.5, "vw": 100}])

        self.assertEqual(signal.side, "hold")

    def test_covers_short_when_price_pierces_vwap(self):
        signal = vwap_spike_short_signal(
            "SPY",
            [{"c": 99.9, "vw": 100}],
            position={"symbol": "SPY", "qty": "-1.25"},
        )

        self.assertEqual(signal.side, "buy")
        self.assertEqual(signal.position_intent, "buy_to_close")
        self.assertEqual(signal.qty, 1.25)


class ShortSpikeAssetEligibilityTests(unittest.TestCase):
    def test_accepts_active_tradable_etb_fractionable_asset(self):
        eligibility = evaluate_short_spike_asset(
            {
                "symbol": "SPY",
                "status": "active",
                "tradable": True,
                "shortable": True,
                "easy_to_borrow": True,
                "fractionable": True,
            }
        )

        self.assertTrue(eligibility.eligible)

    def test_rejects_asset_that_is_not_easy_to_borrow(self):
        eligibility = evaluate_short_spike_asset(
            {
                "symbol": "XYZ",
                "status": "active",
                "tradable": True,
                "shortable": True,
                "easy_to_borrow": False,
                "fractionable": True,
            }
        )

        self.assertFalse(eligibility.eligible)
        self.assertIn("asset must be easy to borrow", eligibility.reasons)


if __name__ == "__main__":
    unittest.main()

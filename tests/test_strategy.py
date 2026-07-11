import unittest

from src.strategy import (
    SymbolTradeState,
    evaluate_short_spike_asset,
    evaluate_vwap_mean_reversion_asset,
    moving_average_signal,
    second_entry_limit_signal,
    vwap_mean_reversion_entry_signal,
    vwap_mean_reversion_exit_signal,
    vwap_spike_short_signal,
)


def bars_from_closes(closes):
    return [{"c": close} for close in closes]


def vwap_bar(close, vwap, volume=1000, timestamp=None):
    bar = {"c": close, "vw": vwap, "v": volume}
    if timestamp is not None:
        bar["t"] = timestamp
    return bar


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
        signal = vwap_spike_short_signal("SPY", [vwap_bar(105, 100)])

        self.assertEqual(signal.side, "sell")
        self.assertEqual(signal.position_intent, "sell_to_open")
        self.assertEqual(signal.notional, 500)

    def test_holds_when_price_is_exactly_4_5_percent_above_vwap(self):
        signal = vwap_spike_short_signal("SPY", [vwap_bar(104.5, 100)])

        self.assertEqual(signal.side, "hold")

    def test_covers_short_when_price_pierces_vwap(self):
        signal = vwap_spike_short_signal(
            "SPY",
            [vwap_bar(99.9, 100)],
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

    def test_rejects_asset_without_daily_volume_from_market_data(self):
        eligibility = evaluate_vwap_mean_reversion_asset(
            {
                "symbol": "XYZ",
                "status": "active",
                "tradable": True,
                "shortable": True,
                "easy_to_borrow": True,
            },
            price=2.0,
            bid=1.995,
            ask=2.0,
            avg_daily_volume_30d=None,
        )

        self.assertFalse(eligibility.eligible)
        self.assertIn("avg_daily_volume_30d is required", eligibility.reasons)


class VwapMeanReversionStrategyTests(unittest.TestCase):
    def test_opens_short_at_positive_4_percent_vwap_deviation(self):
        signal = vwap_mean_reversion_entry_signal(
            "SPY",
            [vwap_bar(104, 100)],
            account_equity=10_000,
            fixed_entry_notional=240,
        )

        self.assertEqual(signal.side, "sell")
        self.assertEqual(signal.position_intent, "sell_to_open")
        self.assertEqual(signal.position_direction, "short")
        self.assertEqual(signal.order_type, "market")
        self.assertEqual(signal.notional, 240)

    def test_uses_volume_weighted_session_vwap_instead_of_latest_bar_vwap(self):
        signal = vwap_mean_reversion_entry_signal(
            "SPY",
            [
                vwap_bar(100, 100, volume=100),
                vwap_bar(104, 104, volume=100),
            ],
            account_equity=10_000,
        )

        self.assertEqual(signal.side, "hold")
        self.assertAlmostEqual(signal.vwap or 0, 102.0)
        self.assertIn("session VWAP", signal.reason)

    def test_opens_long_at_negative_4_percent_vwap_deviation(self):
        signal = vwap_mean_reversion_entry_signal(
            "SPY",
            [vwap_bar(96, 100)],
            account_equity=10_000,
            fixed_entry_notional=240,
        )

        self.assertEqual(signal.side, "buy")
        self.assertEqual(signal.position_intent, "buy_to_open")
        self.assertEqual(signal.position_direction, "long")
        self.assertEqual(signal.order_type, "market")
        self.assertEqual(signal.notional, 240)

    def test_closes_short_when_price_crosses_back_to_vwap(self):
        signal = vwap_mean_reversion_exit_signal(
            "SPY",
            [vwap_bar(100, 100)],
            position={"symbol": "SPY", "qty": "-2", "unrealized_pl": "-10"},
            account_equity=10_000,
        )

        self.assertEqual(signal.side, "buy")
        self.assertEqual(signal.position_intent, "buy_to_close")
        self.assertEqual(signal.qty, 2)

    def test_max_loss_exit_takes_priority_before_vwap_cross(self):
        signal = vwap_mean_reversion_exit_signal(
            "SPY",
            [vwap_bar(105, 100)],
            position={"symbol": "SPY", "qty": "-2", "unrealized_pl": "-101"},
            account_equity=10_000,
        )

        self.assertEqual(signal.side, "buy")
        self.assertIn("max loss reached", signal.reason)

    def test_second_short_market_order_triggers_4_percent_above_first_fill(self):
        state = SymbolTradeState(
            symbol="SPY",
            state="FIRST_FILLED",
            position_direction="short",
            first_fill_price=100,
            first_filled_notional=240,
            entries_submitted=1,
            entries_filled=1,
        )

        waiting = second_entry_limit_signal(state, current_price=103.99, fixed_entry_notional=240)
        signal = second_entry_limit_signal(state, current_price=104, fixed_entry_notional=240)

        self.assertEqual(waiting.side, "hold")
        self.assertEqual(signal.side, "sell")
        self.assertEqual(signal.order_type, "market")
        self.assertIsNone(signal.limit_price)
        self.assertEqual(signal.notional, 240)
        self.assertTrue(signal.allow_position_add)

    def test_second_order_is_blocked_when_cycle_entry_limit_is_reached(self):
        state = SymbolTradeState(
            symbol="SPY",
            state="FIRST_FILLED",
            position_direction="long",
            first_fill_price=100,
            first_filled_notional=800,
            entries_submitted=1,
            entries_filled=1,
        )

        signal = second_entry_limit_signal(state, max_entries_per_cycle=1)

        self.assertEqual(signal.side, "hold")
        self.assertIn("second entry already submitted", signal.reason)


if __name__ == "__main__":
    unittest.main()

import unittest

from src.risk import RiskLimits, evaluate_signal
from src.strategy import Signal


class RiskTests(unittest.TestCase):
    def test_rejects_symbol_outside_whitelist(self):
        signal = Signal(symbol="TSLA", side="buy", reason="test", notional=50)
        limits = RiskLimits(["SPY"], 100, 1000, True)

        decision = evaluate_signal(signal, {"cash": "5000"}, limits)

        self.assertFalse(decision.approved)

    def test_rejects_when_trading_disabled(self):
        signal = Signal(symbol="SPY", side="buy", reason="test", notional=50)
        limits = RiskLimits(["SPY"], 100, 1000, False)

        decision = evaluate_signal(signal, {"cash": "5000"}, limits)

        self.assertFalse(decision.approved)

    def test_approves_valid_buy(self):
        signal = Signal(symbol="SPY", side="buy", reason="test", notional=50)
        limits = RiskLimits(["SPY"], 100, 1000, True)

        decision = evaluate_signal(signal, {"cash": "5000"}, limits)

        self.assertTrue(decision.approved)

    def test_rejects_sell_without_position(self):
        signal = Signal(symbol="SPY", side="sell", reason="test", notional=50)
        limits = RiskLimits(["SPY"], 100, 1000, True)

        decision = evaluate_signal(signal, {"cash": "5000"}, limits, positions=[])

        self.assertFalse(decision.approved)

    def test_approves_sell_with_position(self):
        signal = Signal(symbol="SPY", side="sell", reason="test", notional=50)
        limits = RiskLimits(["SPY"], 100, 1000, True)

        decision = evaluate_signal(
            signal,
            {"cash": "5000"},
            limits,
            positions=[{"symbol": "SPY", "qty": "1"}],
        )

        self.assertTrue(decision.approved)

    def test_approves_short_open_without_position(self):
        signal = Signal(
            symbol="SPY",
            side="sell",
            reason="test",
            notional=50,
            position_intent="sell_to_open",
        )
        limits = RiskLimits(["SPY"], 100, 1000, True)

        decision = evaluate_signal(signal, {"cash": "5000"}, limits, positions=[])

        self.assertTrue(decision.approved)

    def test_rejects_short_open_when_position_exists(self):
        signal = Signal(
            symbol="SPY",
            side="sell",
            reason="test",
            notional=50,
            position_intent="sell_to_open",
        )
        limits = RiskLimits(["SPY"], 100, 1000, True)

        decision = evaluate_signal(
            signal,
            {"cash": "5000"},
            limits,
            positions=[{"symbol": "SPY", "qty": "-1"}],
        )

        self.assertFalse(decision.approved)

    def test_allows_second_short_limit_order_when_short_position_exists(self):
        signal = Signal(
            symbol="SPY",
            side="sell",
            reason="test",
            notional=50,
            position_intent="sell_to_open",
            order_type="limit",
            limit_price=104,
        )
        limits = RiskLimits(["SPY"], 100, 1000, True)

        decision = evaluate_signal(
            signal,
            {"cash": "5000", "buying_power": "5000"},
            limits,
            positions=[{"symbol": "SPY", "qty": "-1"}],
        )

        self.assertTrue(decision.approved)

    def test_approves_buy_to_close_existing_short(self):
        signal = Signal(
            symbol="SPY",
            side="buy",
            reason="test",
            qty=1,
            position_intent="buy_to_close",
        )
        limits = RiskLimits(["SPY"], 100, 1000, True)

        decision = evaluate_signal(
            signal,
            {"cash": "1000"},
            limits,
            positions=[{"symbol": "SPY", "qty": "-1"}],
        )

        self.assertTrue(decision.approved)

    def test_rejects_buy_to_close_without_short(self):
        signal = Signal(
            symbol="SPY",
            side="buy",
            reason="test",
            qty=1,
            position_intent="buy_to_close",
        )
        limits = RiskLimits(["SPY"], 100, 1000, True)

        decision = evaluate_signal(signal, {"cash": "5000"}, limits, positions=[])

        self.assertFalse(decision.approved)


if __name__ == "__main__":
    unittest.main()

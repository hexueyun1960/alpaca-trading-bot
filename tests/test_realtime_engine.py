import unittest
from datetime import datetime, timedelta, timezone

from src.config import Settings
from src.position_sizing import hard_stop_price, second_entry_trigger_price
from src.realtime_engine import RealtimeSignalEngine, build_client_order_id, marketable_limit_price


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
        "journal_path": "logs/test_realtime_engine.jsonl",
        "monitor_interval_seconds": 60,
        "min_5m_gain_pct": 8,
        "min_15m_gain_pct": 15,
        "min_relative_volume": 3,
        "min_intraday_dollar_volume": 1000,
        "max_spread_pct": 1.0,
        "stale_quote_seconds": 3,
        "max_slippage_pct": 0.2,
    }
    values.update(overrides)
    return Settings(**values)


def bar(symbol, minute, close, volume=1000, vwap=100, open_price=10):
    timestamp = datetime(2026, 7, 10, 14, minute, tzinfo=timezone.utc)
    return {
        "T": "b",
        "S": symbol,
        "o": open_price,
        "c": close,
        "vw": vwap,
        "v": volume,
        "t": timestamp.isoformat().replace("+00:00", "Z"),
    }


class RealtimeSignalEngineTests(unittest.TestCase):
    def test_selects_candidates_from_etb_pool_top_gainers_and_losers(self):
        engine = RealtimeSignalEngine(make_settings(top_gainers_count=1, top_losers_count=1))
        engine.set_session_opens({"AAPL": 10, "MSFT": 10, "TSLA": 10})
        now = datetime(2026, 7, 10, 14, 15, tzinfo=timezone.utc)

        engine.on_bar(bar("AAPL", 15, 11, vwap=10.5))
        engine.on_bar(bar("MSFT", 15, 8, vwap=9))
        selected = engine.on_bar(bar("TSLA", 15, 10.5, vwap=10))

        self.assertEqual(selected, ["AAPL", "MSFT"])
        self.assertEqual(engine.active_candidates(now), ["AAPL", "MSFT"])

    def test_retains_candidate_after_leaving_top_twenty_window(self):
        engine = RealtimeSignalEngine(make_settings(top_gainers_count=1, top_losers_count=1, candidate_retention_seconds=900))
        engine.set_session_opens({"AAPL": 10, "MSFT": 10, "TSLA": 10})
        now = datetime(2026, 7, 10, 14, 15, tzinfo=timezone.utc)

        engine.on_bar(bar("AAPL", 15, 12, vwap=10))
        engine.on_bar(bar("MSFT", 15, 9, vwap=10))
        self.assertIn("AAPL", engine.active_candidates(now))
        engine.on_bar(bar("TSLA", 16, 13, vwap=10))

        self.assertIn("AAPL", engine.active_candidates(now + timedelta(minutes=1)))
        self.assertNotIn("AAPL", engine.active_candidates(now + timedelta(minutes=16)))

    def test_uses_bar_open_as_session_open_when_not_preloaded(self):
        engine = RealtimeSignalEngine(make_settings(top_gainers_count=1, top_losers_count=1))
        now = datetime(2026, 7, 10, 14, 15, tzinfo=timezone.utc)

        selected = engine.on_bar(bar("AAPL", 15, 12, vwap=10, open_price=10))

        self.assertEqual(selected, ["AAPL"])
        self.assertIn("AAPL", engine.active_candidates(now))

    def test_builds_fixed_notional_short_market_signal_with_client_order_id(self):
        settings = make_settings(hard_stop_distance_pct=6, second_order_distance_pct=4, enable_second_entry=False)
        engine = RealtimeSignalEngine(settings)
        engine.websocket_connected = True
        engine.set_session_open("AAPL", 10)
        now = datetime(2026, 7, 10, 14, 15, 1, tzinfo=timezone.utc)

        for minute in range(16):
            close = 10
            if minute == 15:
                close = 12
            engine.on_bar(bar("AAPL", minute, close, volume=10_000 if minute == 15 else 1000, vwap=10))
        engine.on_quote({"T": "q", "S": "AAPL", "bp": 11.99, "ap": 12.0, "t": now.isoformat()})

        decision = engine.build_short_signal("AAPL", account_equity=10_000, now=now)

        self.assertIsNotNone(decision.signal)
        signal = decision.signal
        self.assertEqual(signal.side, "sell")
        self.assertEqual(signal.order_type, "market")
        self.assertIsNone(signal.limit_price)
        self.assertEqual(signal.qty, 20)
        self.assertEqual(signal.notional, 240)
        self.assertTrue(signal.client_order_id.startswith("MR-AAPL-SELL-"))

    def test_builds_fixed_notional_long_market_signal(self):
        engine = RealtimeSignalEngine(make_settings())
        engine.websocket_connected = True
        engine.set_session_open("AAPL", 10)
        now = datetime(2026, 7, 10, 14, 15, 1, tzinfo=timezone.utc)
        engine.on_bar(bar("AAPL", 15, 9.5, vwap=10))
        engine.on_quote({"T": "q", "S": "AAPL", "bp": 9.49, "ap": 9.5, "t": now.isoformat()})

        decision = engine.build_entry_signal("AAPL", account_equity=10_000, now=now)

        self.assertIsNotNone(decision.signal)
        self.assertEqual(decision.signal.side, "buy")
        self.assertEqual(decision.signal.position_intent, "buy_to_open")
        self.assertEqual(decision.signal.order_type, "market")

    def test_builds_market_exit_when_position_crosses_vwap(self):
        engine = RealtimeSignalEngine(make_settings())
        now = datetime(2026, 7, 10, 14, 15, 1, tzinfo=timezone.utc)
        engine.on_bar(bar("AAPL", 15, 10, vwap=10, open_price=10))

        decision = engine.build_exit_signal("AAPL", {"symbol": "AAPL", "qty": "-20"}, now=now)

        self.assertIsNotNone(decision.signal)
        self.assertEqual(decision.signal.side, "buy")
        self.assertEqual(decision.signal.position_intent, "buy_to_close")
        self.assertEqual(decision.signal.order_type, "market")

    def test_hard_stop_is_independent_from_second_entry_trigger(self):
        short_second = second_entry_trigger_price(100, "sell", 4)
        short_stop = hard_stop_price(100, "sell", 6)
        long_second = second_entry_trigger_price(100, "buy", 4)
        long_stop = hard_stop_price(100, "buy", 6)

        self.assertGreater(short_stop, short_second)
        self.assertLess(long_stop, long_second)

    def test_entry_signal_no_longer_blocks_on_hard_stop_distance(self):
        settings = make_settings(hard_stop_distance_pct=4, second_order_distance_pct=4, enable_second_entry=True)
        engine = RealtimeSignalEngine(settings)
        engine.websocket_connected = True
        engine.set_session_open("AAPL", 10)
        now = datetime(2026, 7, 10, 14, 15, 1, tzinfo=timezone.utc)

        for minute in range(16):
            close = 10
            if minute == 15:
                close = 12
            engine.on_bar(bar("AAPL", minute, close, volume=10_000 if minute == 15 else 1000, vwap=10))
        engine.on_quote({"T": "q", "S": "AAPL", "bp": 11.99, "ap": 12.0, "t": now.isoformat()})

        decision = engine.build_short_signal("AAPL", account_equity=10_000, now=now)

        self.assertIsNotNone(decision.signal)

    def test_rejects_when_websocket_disconnected_quote_stale_or_spread_wide(self):
        settings = make_settings()
        engine = RealtimeSignalEngine(settings)
        engine.set_session_open("AAPL", 10)
        now = datetime(2026, 7, 10, 14, 15, 1, tzinfo=timezone.utc)
        for minute in range(16):
            engine.on_bar(
                bar(
                    "AAPL",
                    minute,
                    10 if minute < 15 else 12,
                    volume=10_000 if minute == 15 else 1000,
                    vwap=10,
                )
            )

        self.assertEqual(engine.build_short_signal("AAPL", account_equity=10_000, now=now).reason, "websocket disconnected")

        engine.websocket_connected = True
        engine.on_quote({"T": "q", "S": "AAPL", "bp": 11.99, "ap": 12.0, "t": (now - timedelta(seconds=10)).isoformat()})
        self.assertEqual(engine.build_short_signal("AAPL", account_equity=10_000, now=now).reason, "quote is stale")

        engine.on_quote({"T": "q", "S": "AAPL", "bp": 11.0, "ap": 12.0, "t": now.isoformat()})
        self.assertEqual(engine.build_short_signal("AAPL", account_equity=10_000, now=now).reason, "spread is too wide")

    def test_symbol_lock_suppresses_repeat_signal(self):
        engine = RealtimeSignalEngine(make_settings())
        engine.websocket_connected = True
        engine.set_session_open("AAPL", 10)
        now = datetime(2026, 7, 10, 14, 15, 1, tzinfo=timezone.utc)
        for minute in range(16):
            engine.on_bar(
                bar(
                    "AAPL",
                    minute,
                    10 if minute < 15 else 12,
                    volume=10_000 if minute == 15 else 1000,
                    vwap=10,
                )
            )
        engine.on_quote({"T": "q", "S": "AAPL", "bp": 11.99, "ap": 12.0, "t": now.isoformat()})

        decision = engine.build_short_signal("AAPL", account_equity=10_000, now=now)
        engine.lock_order(decision.signal)

        self.assertEqual(engine.build_short_signal("AAPL", account_equity=10_000, now=now).reason, "symbol trigger locked or order pending")

    def test_order_id_and_limit_helpers(self):
        stamp = datetime(2026, 7, 10, 15, 30, 15, tzinfo=timezone.utc)

        self.assertEqual(build_client_order_id("MR", "tsla", "sell", stamp), "MR-TSLA-SELL-20260710T153015Z-01")
        self.assertEqual(marketable_limit_price("buy", 10, 10.1, 0.2), 10.12)


if __name__ == "__main__":
    unittest.main()

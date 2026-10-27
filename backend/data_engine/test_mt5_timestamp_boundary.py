"""Offline tests for explicit MT5 connection and timestamp quarantine.

No broker, database, network, or trading operations.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import os
import sys
import types
import unittest
from unittest.mock import patch

# Permit execution in CI environments without Windows-only MT5.
try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = types.ModuleType("MetaTrader5")

    names = (
        "TIMEFRAME_M1",
        "TIMEFRAME_M5",
        "TIMEFRAME_M15",
        "TIMEFRAME_M30",
        "TIMEFRAME_H1",
        "TIMEFRAME_H4",
        "TIMEFRAME_D1",
        "COPY_TICKS_ALL",
    )

    for value, name in enumerate(names, start=1):
        setattr(mt5, name, value)

    sys.modules["MetaTrader5"] = mt5

from backend.data_engine import mt5_provider as provider_module
from backend.data_engine.mt5_provider import (
    MT5Provider,
    MT5TimestampIntegrityError,
    MT5_INITIALIZE_TIMEOUT_MS,
)

NOW = datetime(
    2026, 10, 14, 23, 3, 24,
    tzinfo=timezone.utc,
)


def fake_tick(delta_seconds=0, *, consistent=True):
    ms = int(
        (
            NOW + timedelta(seconds=delta_seconds)
        ).timestamp() * 1000
    )

    return SimpleNamespace(
        time=ms // 1000 + (1 if not consistent else 0),
        time_msc=ms,
        bid=1.10,
        ask=1.11,
    )


class MT5TimestampBoundaryTests(unittest.TestCase):

    def setUp(self):
        self.clock = patch.object(
            provider_module,
            "_utc_now",
            return_value=NOW,
        )
        self.clock.start()
        self.addCleanup(self.clock.stop)

        self.worker = MT5Provider()
        self.worker.connected = True

    def test_01_current_utc_quote_is_accepted_unchanged(self):
        with patch.object(
            mt5, "symbol_info_tick",
            return_value=fake_tick(),
            create=True,
        ):
            price = self.worker.get_price("EURUSD")

        self.assertEqual(
            price["time_msc"],
            int(NOW.timestamp()) * 1000,
        )

    def test_02_three_hour_future_quote_is_blocked(self):
        with patch.object(
            mt5, "symbol_info_tick",
            return_value=fake_tick(10800),
            create=True,
        ):
            with self.assertRaisesRegex(
                MT5TimestampIntegrityError,
                "10800.0s ahead",
            ):
                self.worker.get_price("EURUSD")

    def test_03_stale_quote_is_blocked(self):
        with patch.object(
            mt5, "symbol_info_tick",
            return_value=fake_tick(-600),
            create=True,
        ):
            with self.assertRaisesRegex(
                MT5TimestampIntegrityError,
                "old",
            ):
                self.worker.get_price("EURUSD")

    def test_04_current_quote_boundaries(self):
        for delta in (4.9, -179.9):
            with self.subTest(delta=delta):
                with patch.object(
                    mt5, "symbol_info_tick",
                    return_value=fake_tick(delta),
                    create=True,
                ):
                    self.assertIsNotNone(
                        self.worker.get_price("EURUSD")
                    )

    def test_05_disagreeing_tick_clocks_are_blocked(self):
        with patch.object(
            mt5, "symbol_info_tick",
            return_value=fake_tick(
                0, consistent=False
            ),
            create=True,
        ):
            with self.assertRaisesRegex(
                MT5TimestampIntegrityError,
                "disagree",
            ):
                self.worker.get_price("EURUSD")

    def test_06_no_history_query_when_live_clock_skewed(self):
        with patch.object(
            mt5, "symbol_info_tick",
            return_value=fake_tick(10800),
            create=True,
        ), patch.object(
            mt5, "copy_ticks_range",
            create=True,
        ) as read:
            with self.assertRaises(
                MT5TimestampIntegrityError
            ):
                self.worker.get_ticks_range(
                    "EURUSD",
                    NOW - timedelta(minutes=5),
                    NOW,
                )

            read.assert_not_called()

    def test_07_no_candle_query_when_live_clock_skewed(self):
        with patch.object(
            mt5, "symbol_info_tick",
            return_value=fake_tick(10800),
            create=True,
        ), patch.object(
            mt5, "copy_rates_range",
            create=True,
        ) as read:
            with self.assertRaises(
                MT5TimestampIntegrityError
            ):
                self.worker.get_candles(
                    "EURUSD",
                    "M5",
                    NOW - timedelta(minutes=5),
                    NOW,
                )

            read.assert_not_called()

    def test_08_future_history_rows_fail_entire_batch(self):
        future = [{
            "time_msc": fake_tick(10800).time_msc
        }]

        with patch.object(
            mt5, "symbol_info_tick",
            return_value=fake_tick(),
            create=True,
        ), patch.object(
            mt5, "copy_ticks_range",
            return_value=future,
            create=True,
        ):
            with self.assertRaisesRegex(
                MT5TimestampIntegrityError,
                "ahead",
            ):
                self.worker.get_ticks_range(
                    "EURUSD",
                    NOW - timedelta(minutes=5),
                    NOW,
                )

    def test_09_future_candle_row_fails_entire_batch(self):
        future = [{
            "time": int(NOW.timestamp()) + 10800
        }]

        with patch.object(
            mt5, "symbol_info_tick",
            return_value=fake_tick(),
            create=True,
        ), patch.object(
            mt5, "copy_rates_range",
            return_value=future,
            create=True,
        ):
            with self.assertRaisesRegex(
                MT5TimestampIntegrityError,
                "ahead",
            ):
                self.worker.get_candles(
                    "EURUSD",
                    "M5",
                    NOW - timedelta(minutes=5),
                    NOW,
                )

    def test_10_explicit_path_matches_manual_connection(self):
        self.worker.connected = False

        path = r"C:\Program Files\MetaTrader 5\terminal64.exe"

        with patch.dict(
            os.environ,
            {"MT5_TERMINAL_PATH": path},
        ), patch(
            "os.path.isabs",
            return_value=True,
        ), patch(
            "os.path.isfile",
            return_value=True,
        ), patch.object(
            mt5, "shutdown",
            create=True,
        ), patch.object(
            mt5, "initialize",
            return_value=True,
            create=True,
        ) as init, patch.object(
            mt5, "terminal_info",
            return_value=SimpleNamespace(connected=True),
            create=True,
        ):
            self.assertTrue(self.worker.connect())

            init.assert_called_once_with(
                path,
                timeout=MT5_INITIALIZE_TIMEOUT_MS,
            )

    def test_11_invalid_explicit_path_does_not_fallback(self):
        self.worker.connected = False

        with patch.dict(
            os.environ,
            {"MT5_TERMINAL_PATH": "not-a-path"},
        ), patch(
            "os.path.isabs",
            return_value=False,
        ), patch.object(
            mt5, "shutdown",
            create=True,
        ), patch.object(
            mt5, "initialize",
            create=True,
        ) as init:
            self.assertFalse(self.worker.connect())
            init.assert_not_called()

    def test_12_autodiscovery_uses_60_second_timeout(self):
        self.worker.connected = False

        with patch.dict(
            os.environ,
            {"MT5_TERMINAL_PATH": ""},
        ), patch.object(
            mt5, "shutdown",
            create=True,
        ), patch.object(
            mt5, "initialize",
            return_value=True,
            create=True,
        ) as init, patch.object(
            mt5, "terminal_info",
            return_value=SimpleNamespace(connected=True),
            create=True,
        ):
            self.assertTrue(self.worker.connect())

            init.assert_called_once_with(
                timeout=MT5_INITIALIZE_TIMEOUT_MS
            )

    def test_13_unaware_history_range_is_rejected(self):
        with patch.object(
            mt5, "symbol_info_tick",
            create=True,
        ) as quote:
            with self.assertRaisesRegex(
                ValueError,
                "timezone-aware",
            ):
                self.worker.get_ticks_range(
                    "EURUSD",
                    datetime(2026, 10, 14),
                    NOW,
                )

            quote.assert_not_called()

    def test_14_normal_historical_tick_range_is_unchanged(self):
        history = [{
            "time_msc": int(
                (
                    NOW - timedelta(minutes=1)
                ).timestamp() * 1000
            )
        }]

        with patch.object(
            mt5, "symbol_info_tick",
            return_value=fake_tick(),
            create=True,
        ), patch.object(
            mt5, "copy_ticks_range",
            return_value=history,
            create=True,
        ) as read:
            result = self.worker.get_ticks_range(
                "EURUSD",
                NOW - timedelta(minutes=5),
                NOW,
            )

            self.assertIs(result, history)

            args, kwargs = read.call_args

            self.assertEqual(
                args[1:3],
                (
                    NOW - timedelta(minutes=5),
                    NOW,
                ),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
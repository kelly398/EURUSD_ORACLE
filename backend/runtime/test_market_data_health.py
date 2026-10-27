"""Offline terminal diagnostics and worker shutdown boundary checks."""
from datetime import timedelta
from types import SimpleNamespace
import importlib
import json
import unittest
from unittest.mock import Mock, patch

from backend.data_engine.test_mt5_timestamp_boundary import NOW, fake_tick
from backend.data_engine import mt5_provider
from backend.runtime.check_market_data import inspect_terminal


class TerminalHealthTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.api.initialize.return_value = True
        self.api.terminal_info.return_value = SimpleNamespace(connected=True)
        self.api.symbol_info_tick.return_value = fake_tick()
        self.clock = patch.object(mt5_provider, "_utc_now", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_connected_current_quote_is_healthy(self):
        report = inspect_terminal(self.api, path="terminal64.exe", timeout_ms=60000)
        self.assertTrue(report["quote_valid"])
        self.api.initialize.assert_called_once_with("terminal64.exe", timeout=60000)
        self.api.shutdown.assert_called_once()

    def test_shifted_raw_quote_remains_quarantined(self):
        self.api.symbol_info_tick.return_value = fake_tick(7200)
        report = inspect_terminal(self.api)
        self.assertTrue(report["connected"])
        self.assertFalse(report["quote_valid"])
        self.assertEqual(report["blocker"], "QUOTE_TIMESTAMP_NOT_VERIFIED")
        self.assertIn("raw_quote_at", report)

    def test_ipc_failure_does_not_expose_error_message(self):
        self.api.initialize.return_value = False
        self.api.last_error.return_value = (-10005, "SENSITIVE_TERMINAL_DETAILS")
        report = inspect_terminal(self.api)
        self.assertEqual(report["error_code"], -10005)
        self.assertNotIn("SENSITIVE", json.dumps(report))
        self.api.symbol_info_tick.assert_not_called()
        self.api.shutdown.assert_called_once()

    def test_disconnected_terminal_never_reads_price(self):
        self.api.terminal_info.return_value = SimpleNamespace(connected=False)
        self.assertEqual(inspect_terminal(self.api)["blocker"], "TERMINAL_DISCONNECTED")
        self.api.symbol_info_tick.assert_not_called()

    def test_terminal_exception_is_redacted_and_connection_closed(self):
        self.api.initialize.side_effect = RuntimeError("SENSITIVE_DETAILS")
        report = inspect_terminal(self.api)
        self.assertEqual(report["error_type"], "RuntimeError")
        self.assertNotIn("SENSITIVE", json.dumps(report))
        self.api.shutdown.assert_called_once()

    def test_missing_quote_is_blocked(self):
        self.api.symbol_info_tick.return_value = None
        self.assertFalse(inspect_terminal(self.api)["quote_valid"])


class ShutdownTests(unittest.TestCase):
    def test_stop_during_reconnect_exits_without_traceback_and_releases_lock(self):
        for name in ("backend.data_engine.run_tick_ingestor", "backend.candle_engine.run_mt5_bar_ingestor"):
            module = importlib.import_module(name)
            lock = Mock()
            lock.acquire.return_value = True
            with self.subTest(worker=name), patch.object(module, "SingleInstanceLock", return_value=lock), \
                    patch.object(module, "_install_signal_handlers"), \
                    patch.object(module, "run_worker", side_effect=KeyboardInterrupt), patch("builtins.print"):
                module.main()
            lock.release.assert_called_once()


if __name__ == "__main__":
    unittest.main()

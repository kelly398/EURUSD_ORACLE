"""Read-only MT5/DB health probe. Never ingests data or sends orders."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os

from backend.data_engine.mt5_provider import _check_live_tick, MT5TimestampIntegrityError


def inspect_terminal(api, *, path=None, timeout_ms=10000):
    report = {"initialized": False, "connected": False, "quote_valid": False}
    try:
        args = (path,) if path else ()
        if not api.initialize(*args, timeout=timeout_ms):
            report["error_code"] = api.last_error()[0]
            return report
        report["initialized"] = True
        terminal = api.terminal_info()
        report["connected"] = bool(terminal and terminal.connected)
        if not report["connected"]:
            report["blocker"] = "TERMINAL_DISCONNECTED"
            return report
        tick = api.symbol_info_tick("EURUSD")
        if tick is not None:
            raw_at = datetime.fromtimestamp(tick.time_msc / 1000, timezone.utc)
            report["raw_quote_at"] = raw_at.isoformat()
            report["raw_quote_skew_seconds"] = round(
                (raw_at - datetime.now(timezone.utc)).total_seconds(), 3)
        try:
            _check_live_tick(tick, "EURUSD")
        except MT5TimestampIntegrityError:
            report["blocker"] = "QUOTE_TIMESTAMP_NOT_VERIFIED"
            return report
        report["quote_valid"] = True
        report["quote_at"] = datetime.fromtimestamp(tick.time_msc / 1000, timezone.utc).isoformat()
        return report
    except Exception as error:
        # Do not emit terminal/account details or connection strings.
        report["error_type"] = type(error).__name__
        return report
    finally:
        api.shutdown()


def inspect_database(engine, *, now):
    from sqlalchemy import text
    report = {"read_only": True, "fresh": False}
    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                connection.execute(text("SET LOCAL statement_timeout = '10000ms'"))
                tick = connection.execute(text(
                    "SELECT max(timestamp) FROM ticks WHERE symbol = :symbol"
                ), {"symbol": "EURUSD"}).scalar_one()
                bar = connection.execute(text(
                    "SELECT timestamp, is_complete FROM broker_candles "
                    "WHERE symbol = :symbol AND timeframe = 'M5' AND source = 'MT5' "
                    "ORDER BY timestamp DESC LIMIT 1"
                ), {"symbol": "EURUSD"}).first()
        boundary = now.replace(minute=now.minute // 5 * 5, second=0, microsecond=0)
        expected = boundary - timedelta(minutes=5)
        report.update(latest_tick=tick.isoformat() if tick else None,
                      latest_bar=bar.timestamp.isoformat() if bar else None,
                      expected_bar=expected.isoformat())
        report["fresh"] = bool(tick and bar and -5 <= (now - tick).total_seconds() <= 120
                               and bar.is_complete and bar.timestamp == expected)
    except Exception as error:
        report["error_type"] = type(error).__name__
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terminal-path")
    parser.add_argument("--timeout-ms", type=int, default=60000, choices=range(1000, 60001))
    args = parser.parse_args()
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    import MetaTrader5 as mt5
    from backend.database.connection import engine
    from backend.market_state.session_engine import SessionEngine
    terminal = inspect_terminal(mt5, path=args.terminal_path or os.getenv("MT5_TERMINAL_PATH"),
                                timeout_ms=args.timeout_ms)
    now = datetime.now(timezone.utc)
    database = inspect_database(engine, now=now)
    market_open = SessionEngine().analyze(now).market_open
    status = "pass" if terminal["quote_valid"] and database["fresh"] else "blocked"
    if not market_open:
        status = "market_closed"
    print(json.dumps(dict(checked_at=now.isoformat(), status=status,
                          market_open=market_open, terminal=terminal, database=database), indent=2))
    return {"pass": 0, "blocked": 2, "market_closed": 3}[status]


if __name__ == "__main__":
    raise SystemExit(main())

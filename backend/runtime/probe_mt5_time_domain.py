"""Bounded, read-only evidence for MT5 payload and query timestamp conventions.

Prints no account identifiers, credentials or account balances. Does not modify
market tables, normalize stored timestamps, fit models or send orders.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import time


def _instant(seconds):
    return datetime.fromtimestamp(float(seconds), timezone.utc).isoformat()


def probe(api, *, terminal_path=None, samples=3):
    report = {"schema_version": 1, "read_only": True, "initialized": False,
              "observations": [], "query_evidence": [], "archive_samples": []}
    try:
        args = (terminal_path,) if terminal_path else ()
        if not api.initialize(*args, timeout=60000):
            report["error_code"] = api.last_error()[0]
            return report
        report["initialized"] = True
        terminal = api.terminal_info()
        if terminal is None or not terminal.connected:
            report["blocker"] = "TERMINAL_DISCONNECTED"
            return report
        account = api.account_info()
        company = str(getattr(account, "company", ""))
        server = str(getattr(account, "server", ""))
        report["hfm_company_match"] = any(name in company.lower()
            for name in ("hf markets", "hfmarkets", "hotforex", "hfm investments"))
        report["company_sha256"] = sha256(company.encode()).hexdigest() if company else None
        report["server_sha256"] = sha256(server.encode()).hexdigest() if server else None
        for index in range(samples):
            before = datetime.now(timezone.utc)
            tick = api.symbol_info_tick("EURUSD")
            after = datetime.now(timezone.utc)
            if tick is None:
                report["blocker"] = "QUOTE_MISSING"
                return report
            report["observations"].append(dict(received_before=before.isoformat(),
                received_after=after.isoformat(), raw_time=int(tick.time),
                raw_time_msc=int(tick.time_msc), raw_rendered_as_utc=_instant(tick.time_msc / 1000),
                skew_seconds=tick.time_msc / 1000 - after.timestamp()))
            if index + 1 < samples:
                time.sleep(1)
        # Independently inspect query behavior. A time shift in payloads does not
        # prove that API query parameters require the same shift.
        end_utc = datetime.now(timezone.utc) - timedelta(seconds=10)
        raw_end = datetime.fromtimestamp(int(tick.time) - 10, timezone.utc)
        for label, end in (("utc_request", end_utc), ("raw_clock_request", raw_end)):
            start = end - timedelta(seconds=10)
            rows = api.copy_ticks_range("EURUSD", start, end, api.COPY_TICKS_ALL)
            item = dict(kind=label, requested_start=start.isoformat(), requested_end=end.isoformat(),
                        row_count=len(rows) if rows is not None else None)
            if rows is not None and len(rows):
                item.update(first_raw_msc=int(rows[0]["time_msc"]), last_raw_msc=int(rows[-1]["time_msc"]))
            report["query_evidence"].append(item)
        from sqlalchemy import text
        from backend.database.connection import engine
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                connection.execute(text("SET LOCAL statement_timeout='10000ms'"))
                anchors = [datetime(2025,10,24,12,tzinfo=timezone.utc),
                           datetime(2025,10,27,12,tzinfo=timezone.utc),
                           datetime(2026,3,27,12,tzinfo=timezone.utc),
                           datetime(2026,3,30,12,tzinfo=timezone.utc)]
                latest = connection.execute(text("SELECT max(timestamp) FROM broker_candles "
                    "WHERE symbol='EURUSD' AND timeframe='M5' AND source='MT5'")).scalar_one()
                if latest:
                    anchors.append(latest)
                for anchor in anchors:
                    stored = connection.execute(text("SELECT timestamp, open, high, low, close "
                        "FROM broker_candles WHERE symbol='EURUSD' AND timeframe='M5' AND source='MT5' "
                        "AND timestamp>=:start AND timestamp<:end ORDER BY timestamp LIMIT 1"),
                        dict(start=anchor, end=anchor+timedelta(minutes=5))).mappings().first()
                    if stored is None:
                        continue
                    stamp = stored["timestamp"]
                    rows = api.copy_rates_range("EURUSD", api.TIMEFRAME_M5,
                                                 stamp, stamp+timedelta(seconds=299))
                    matching = [row for row in rows if int(row["time"]) == int(stamp.timestamp())] if rows is not None else []
                    same = bool(matching) and all(abs(float(matching[0][key])-float(stored[key])) < 1e-9
                                                   for key in ("open","high","low","close"))
                    tick_row = connection.execute(text("SELECT timestamp,source_time_msc FROM ticks "
                        "WHERE symbol='EURUSD' AND timestamp>=:start AND timestamp<:end "
                        "ORDER BY timestamp LIMIT 1"), dict(start=stamp,end=stamp+timedelta(minutes=5))).mappings().first()
                    report["archive_samples"].append(dict(stored_timestamp=stamp.isoformat(),
                        mt5_raw_bar_matches_ohlc=same,
                        stored_tick_timestamp_equals_raw_clock=bool(tick_row and
                            abs(tick_row["timestamp"].timestamp()*1000-tick_row["source_time_msc"])<0.1)))
        report["historical_utc_certified"] = False
        return report
    except Exception as error:
        report["error_type"] = type(error).__name__
        return report
    finally:
        api.shutdown()


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args = parser.parse_args()
    from dotenv import load_dotenv
    import os
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    import MetaTrader5 as mt5
    result = probe(mt5, terminal_path=os.getenv("MT5_TERMINAL_PATH"))
    if args.output:
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False),encoding='utf-8')
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result.get("archive_samples") and not result.get("error_type") else 2


if __name__ == "__main__":
    raise SystemExit(main())

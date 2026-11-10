import argparse

from datetime import (
    date,
    datetime,
    timedelta,
    timezone
)
from backend.data_engine.tick_identity import (
    create_tick_fingerprint
)

import MetaTrader5 as mt5

from sqlalchemy import text

from sqlalchemy.dialects.postgresql import (
    insert as pg_insert
)

from backend.database.connection import (
    engine
)

from backend.database.models import (
    BrokerCandle,
    Tick
)


SYMBOL = "EURUSD"

TIMEFRAME = "M5"

SOURCE = "MT5"

TICK_BATCH_SIZE = 5000

BAR_BATCH_SIZE = 1000


def parse_date(
    value
):

    return datetime.strptime(
        value,
        "%Y-%m-%d"
    ).date()


def parse_arguments():

    parser = argparse.ArgumentParser(

        description=(
            "Restart-safe historical "
            "EUR/USD MT5 backfill."
        )
    )


    parser.add_argument(
        "--start",
        required=True,
        type=parse_date
    )


    parser.add_argument(
        "--end",
        required=True,
        type=parse_date
    )


    parser.add_argument(
        "--bars-only",
        action="store_true"
    )


    return parser.parse_args()


def database_size_bytes():

    with engine.connect() as connection:

        result = connection.execute(

            text(
                "SELECT "
                "pg_database_size("
                "current_database()"
                ")"
            )
        )


        return int(
            result.scalar_one()
        )


def format_bytes(
    value
):

    units = (
        "B",
        "KB",
        "MB",
        "GB",
        "TB"
    )


    size = float(
        value
    )


    for unit in units:

        if size < 1024.0:

            return (
                f"{size:.2f} {unit}"
            )


        size /= 1024.0


    return (
        f"{size:.2f} PB"
    )


    # float.hex() preserves the exact
    # binary float representation and
    # produces deterministic text.

    identity = "|".join(
        (
            symbol,
            str(
                int(
                    time_msc
                )
            ),
            float(
                bid
            ).hex(),
            float(
                ask
            ).hex(),
            float(
                last
            ).hex(),
            float(
                volume
            ).hex(),
            str(
                int(
                    flags
                )
            ),
            float(
                volume_real
            ).hex()
        )
    )


    return (
        sha256(
            identity.encode(
                "utf-8"
            )
        )
        .hexdigest()
    )


def chunked(
    rows,
    size
):

    for start in range(
        0,
        len(rows),
        size
    ):

        yield rows[
            start:
            start + size
        ]


def fetch_bars(
    day_start,
    day_end
):

    rates = mt5.copy_rates_range(

        SYMBOL,

        mt5.TIMEFRAME_M5,

        day_start,

        day_end
    )


    if rates is None:

        return []


    rows = []


    for rate in rates:

        timestamp = datetime.fromtimestamp(

            int(
                rate["time"]
            ),

            tz=timezone.utc
        )


        # MT5 range calls may include
        # the right boundary.
        if not (
            day_start
            <= timestamp
            < day_end
        ):

            continue


        rows.append(
            {
                "symbol":
                    SYMBOL,

                "timeframe":
                    TIMEFRAME,

                "timestamp":
                    timestamp,

                "open":
                    float(
                        rate["open"]
                    ),

                "high":
                    float(
                        rate["high"]
                    ),

                "low":
                    float(
                        rate["low"]
                    ),

                "close":
                    float(
                        rate["close"]
                    ),

                "tick_volume":
                    int(
                        rate["tick_volume"]
                    ),

                "spread_points":
                    int(
                        rate["spread"]
                    ),

                "real_volume":
                    float(
                        rate["real_volume"]
                    ),

                "is_complete":
                    True,

                "source":
                    SOURCE
            }
        )


    return rows


def fetch_ticks(
    day_start,
    day_end
):

    raw_ticks = mt5.copy_ticks_range(

        SYMBOL,

        day_start,

        day_end,

        mt5.COPY_TICKS_ALL
    )


    if raw_ticks is None:

        return []


    rows = []


    for tick in raw_ticks:

        time_msc = int(
            tick["time_msc"]
        )


        timestamp = datetime.fromtimestamp(

            time_msc / 1000.0,

            tz=timezone.utc
        )


        if not (
            day_start
            <= timestamp
            < day_end
        ):

            continue


        bid = float(
            tick["bid"]
        )


        ask = float(
            tick["ask"]
        )


        last = float(
            tick["last"]
        )


        volume = float(
            tick["volume"]
        )


        flags = int(
            tick["flags"]
        )


        volume_real = float(
            tick["volume_real"]
        )


        spread = (
            ask - bid
        )


        spread_pips = (
            spread
            * 10000.0
        )


        fingerprint = (
            create_tick_fingerprint(

                symbol=SYMBOL,

                time_msc=time_msc,

                bid=bid,

                ask=ask,

                last=last,

                volume=volume,

                flags=flags,

                volume_real=
                    volume_real
            )
        )


        rows.append(
            {
                "symbol":
                    SYMBOL,

                "timestamp":
                    timestamp,

                "source_time_msc":
                    time_msc,

                "bid":
                    bid,

                "ask":
                    ask,

                "last":
                    last,

                "volume":
                    volume,

                "flags":
                    flags,

                "volume_real":
                    volume_real,

                "spread":
                    spread,

                "spread_pips":
                    spread_pips,

                "fingerprint":
                    fingerprint
            }
        )


    return rows


def insert_bars(
    rows
):

    if not rows:

        return 0


    # The feed-aware schema and readers are not active yet. Reject the whole
    # batch before opening a transaction, including mixed legacy/feed batches.
    if any("feed_id" in row for row in rows):

        raise RuntimeError(
            "Feed-labelled historical candle writes are blocked pending "
            "approved database migration and feed-isolated readers."
        )


    inserted = 0


    for batch in chunked(
        rows,
        BAR_BATCH_SIZE
    ):

        statement = (

            pg_insert(
                BrokerCandle
            )

            .values(
                batch
            )

            .on_conflict_do_nothing()
        )


        with engine.begin() as connection:

            result = connection.execute(
                statement
            )


            if (
                result.rowcount
                is not None

                and

                result.rowcount > 0
            ):

                inserted += (
                    result.rowcount
                )


    return inserted


def insert_ticks(
    rows
):

    if not rows:

        return 0


    # Keep the tick writer aligned with the candle writer: callers cannot
    # opt into feed-labelled rows until reader and cutover gates are approved.
    if any("feed_id" in row for row in rows):

        raise RuntimeError(
            "Feed-labelled historical tick writes are blocked pending "
            "approved database migration and feed-isolated readers."
        )


    inserted = 0


    for batch in chunked(
        rows,
        TICK_BATCH_SIZE
    ):

        statement = (

            pg_insert(
                Tick
            )

            .values(
                batch
            )

            .on_conflict_do_nothing(
                index_elements=[
                    "fingerprint"
                ]
            )
        )


        with engine.begin() as connection:

            result = connection.execute(
                statement
            )


            if (
                result.rowcount
                is not None

                and

                result.rowcount > 0
            ):

                inserted += (
                    result.rowcount
                )


    return inserted


def main():

    arguments = (
        parse_arguments()
    )


    start_date = (
        arguments.start
    )


    end_date = (
        arguments.end
    )


    bars_only = (
        arguments.bars_only
    )


    if (
        end_date
        < start_date
    ):

        raise ValueError(
            "--end must be on or "
            "after --start."
        )


    print(
        "=" * 74
    )

    print(
        "EUR/USD ORACLE "
        "HISTORICAL MT5 BACKFILL"
    )

    print(
        "=" * 74
    )


    print(
        f"\nSymbol: "
        f"{SYMBOL}"
    )


    print(
        f"Start date: "
        f"{start_date}"
    )


    print(
        f"End date: "
        f"{end_date}"
    )


    print(
        f"Bars only: "
        f"{bars_only}"
    )


    database_before = (
        database_size_bytes()
    )


    print(
        "\nDatabase before: "
        f"{format_bytes(database_before)}"
    )


    if not mt5.initialize():

        raise RuntimeError(
            "MT5 initialization failed: "
            f"{mt5.last_error()}"
        )


    try:

        if not mt5.symbol_select(
            SYMBOL,
            True
        ):

            raise RuntimeError(
                "Unable to select EURUSD."
            )


        current_date = (
            start_date
        )


        total_bars_fetched = 0

        total_bars_inserted = 0

        total_ticks_fetched = 0

        total_ticks_inserted = 0

        days_processed = 0


        while (
            current_date
            <= end_date
        ):

            day_start = datetime(

                current_date.year,

                current_date.month,

                current_date.day,

                tzinfo=timezone.utc
            )


            day_end = (

                day_start

                + timedelta(
                    days=1
                )
            )


            bars = (
                fetch_bars(
                    day_start,
                    day_end
                )
            )


            bars_inserted = (
                insert_bars(
                    bars
                )
            )


            total_bars_fetched += (
                len(bars)
            )


            total_bars_inserted += (
                bars_inserted
            )


            if bars_only:

                tick_count = 0

                ticks_inserted = 0


            else:

                ticks = (
                    fetch_ticks(
                        day_start,
                        day_end
                    )
                )


                tick_count = (
                    len(ticks)
                )


                ticks_inserted = (
                    insert_ticks(
                        ticks
                    )
                )


                total_ticks_fetched += (
                    tick_count
                )


                total_ticks_inserted += (
                    ticks_inserted
                )


                # Release potentially large
                # daily tick collection before
                # moving to the next day.
                del ticks


            print(
                f"{current_date} | "
                f"bars fetched="
                f"{len(bars):>3} | "
                f"bars inserted="
                f"{bars_inserted:>3} | "
                f"ticks fetched="
                f"{tick_count:>7} | "
                f"ticks inserted="
                f"{ticks_inserted:>7}"
            )


            days_processed += 1


            current_date += timedelta(
                days=1
            )


        database_after = (
            database_size_bytes()
        )


        growth = (

            database_after
            - database_before
        )


        print(
            "\n"
            + "-" * 74
        )

        print(
            "BACKFILL SUMMARY"
        )

        print(
            "-" * 74
        )


        print(
            f"Days processed: "
            f"{days_processed}"
        )


        print(
            f"Bars fetched: "
            f"{total_bars_fetched}"
        )


        print(
            f"Bars inserted: "
            f"{total_bars_inserted}"
        )


        if not bars_only:

            print(
                f"Ticks fetched: "
                f"{total_ticks_fetched}"
            )


            print(
                f"Ticks inserted: "
                f"{total_ticks_inserted}"
            )


        print(
            f"\nDatabase after: "
            f"{format_bytes(database_after)}"
        )


        print(
            f"Database growth: "
            f"{format_bytes(max(0, growth))}"
        )


        print(
            "\n"
            + "=" * 74
        )

        print(
            "HISTORICAL BACKFILL: COMPLETE"
        )

        print(
            "=" * 74
        )


    finally:

        mt5.shutdown()


if __name__ == "__main__":

    main()

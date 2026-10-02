import argparse

from datetime import (
    datetime,
    timedelta,
    timezone
)

import MetaTrader5 as mt5

from sqlalchemy import (
    func,
    select
)

from backend.database.connection import (
    SessionLocal
)

from backend.database.models import (
    BrokerCandle,
    Tick
)

from backend.data_engine.tick_identity import (
    create_tick_fingerprint
)


SYMBOL = "EURUSD"

TIMEFRAME = "M5"

SOURCE = "MT5"


def parse_date(
    value
):

    return datetime.strptime(
        value,
        "%Y-%m-%d"
    ).date()


def parse_arguments():

    parser = argparse.ArgumentParser()


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


    return parser.parse_args()


def mt5_counts(
    start,
    end
):

    bars = mt5.copy_rates_range(

        SYMBOL,

        mt5.TIMEFRAME_M5,

        start,

        end
    )


    ticks = mt5.copy_ticks_range(

        SYMBOL,

        start,

        end,

        mt5.COPY_TICKS_ALL
    )


    bar_count = 0

    if bars is not None:

        for bar in bars:

            timestamp = (
                datetime.fromtimestamp(

                    int(
                        bar["time"]
                    ),

                    tz=timezone.utc
                )
            )


            if (
                start
                <= timestamp
                < end
            ):

                bar_count += 1


    tick_fingerprints = set()

    if ticks is not None:

        for tick in ticks:

            timestamp = (
                datetime.fromtimestamp(

                     int(
                        tick["time_msc"]
                    )
                    / 1000.0,

                    tz=timezone.utc
                )
            )


            if not (
                start
                <= timestamp
                < end
            ):

                continue


            fingerprint = (
                create_tick_fingerprint(

                    symbol=SYMBOL,

                    time_msc=int(
                        tick["time_msc"]
                    ),

                    bid=float(
                        tick["bid"]
                        ),

                    ask=float(
                        tick["ask"]
                    ),

                    last=float(
                        tick["last"]
                    ),

                    volume=float(
                        tick["volume"]
                    ),

                    flags=int(
                        tick["flags"]
                    ),

                    volume_real=float(
                        tick["volume_real"]
                    )
                )
            )


            tick_fingerprints.add(
                fingerprint
            )


    tick_count = len(
        tick_fingerprints
    )

    return (
        bar_count,
        tick_count
    )


def database_counts(
    start,
    end
):

    session = (
        SessionLocal()
    )


    try:

        bar_statement = (

            select(
                func.count(
                    BrokerCandle.id
                )
            )

            .where(

                BrokerCandle.symbol
                == SYMBOL,

                BrokerCandle.timeframe
                == TIMEFRAME,

                BrokerCandle.source
                == SOURCE,

                BrokerCandle.timestamp
                >= start,

                BrokerCandle.timestamp
                < end
            )
        )


        tick_statement = (

            select(
                func.count(
                    Tick.id
                )
            )

            .where(

                Tick.symbol
                == SYMBOL,

                Tick.timestamp
                >= start,

                Tick.timestamp
                < end
            )
        )


        bar_count = session.execute(
            bar_statement
        ).scalar_one()


        tick_count = session.execute(
            tick_statement
        ).scalar_one()


        return (
            int(
                bar_count
            ),
            int(
                tick_count
            )
        )


    finally:

        session.close()


def main():

    arguments = (
        parse_arguments()
    )


    print(
        "=" * 78
    )

    print(
        "EUR/USD ORACLE "
        "HISTORICAL BACKFILL AUDIT"
    )

    print(
        "=" * 78
    )


    if not mt5.initialize():

        raise RuntimeError(
            "MT5 initialization failed: "
            f"{mt5.last_error()}"
        )


    mismatches = 0
    failed_days = []


    try:

        current_date = (
            arguments.start
        )


        while (
            current_date
            <= arguments.end
        ):

            start = datetime(

                current_date.year,

                current_date.month,

                current_date.day,

                tzinfo=timezone.utc
            )


            end = (

                start

                + timedelta(
                    days=1
                )
            )


            mt5_bars, mt5_ticks = (
                mt5_counts(
                    start,
                    end
                )
            )


            db_bars, db_ticks = (
                database_counts(
                    start,
                    end
                )
            )


            bar_match = (
                mt5_bars
                == db_bars
            )


            tick_match = (
                mt5_ticks
                == db_ticks
            )


            status = (

                "PASS"

                if (
                    bar_match
                    and tick_match
                )

                else "FAIL"
            )


            if status == "FAIL":

                mismatches += 1

                failed_days.append(
                    {
                        "date": current_date,
                        "mt5_bars": mt5_bars,
                        "db_bars": db_bars,
                        "mt5_ticks": mt5_ticks,
                        "db_ticks": db_ticks,
                    }
                )


            print(
                f"{current_date} | "
                f"M5 MT5={mt5_bars:>3} "
                f"DB={db_bars:>3} | "
                f"ticks MT5={mt5_ticks:>7} "
                f"DB={db_ticks:>7} | "
                f"{status}"
            )


            current_date += timedelta(
                days=1
            )


        print(
            "\n"
            + "-" * 78
        )


        print(
            f"Mismatched days: "
            f"{mismatches}"
        )


        print(
            "\nFailed days:"
        )


        for failure in failed_days:

            print(
                failure
            )


        if mismatches != 0:

            raise RuntimeError(
                "Historical backfill audit "
                "found mismatched days."
            )


        print(
            "\n"
            + "=" * 78
        )

        print(
            "HISTORICAL BACKFILL "
            "AUDIT: PASS"
        )

        print(
            "=" * 78
        )


    finally:

        mt5.shutdown()


if __name__ == "__main__":

    main()

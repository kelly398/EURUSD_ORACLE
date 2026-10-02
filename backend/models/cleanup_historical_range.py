import argparse

from datetime import (
    datetime,
    timedelta,
    timezone
)

from sqlalchemy import delete

from backend.database.connection import (
    SessionLocal
)

from backend.database.models import (
    BrokerCandle,
    Tick
)


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
            "Delete historical EUR/USD "
            "backfill rows for a date range."
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


    return parser.parse_args()


def main():

    arguments = (
        parse_arguments()
    )


    if (
        arguments.end
        < arguments.start
    ):

        raise ValueError(
            "--end must be on or "
            "after --start."
        )


    start = datetime(

        arguments.start.year,

        arguments.start.month,

        arguments.start.day,

        tzinfo=timezone.utc
    )


    end = datetime(

        arguments.end.year,

        arguments.end.month,

        arguments.end.day,

        tzinfo=timezone.utc
    )


    end = (

        end

        + timedelta(
            days=1
        )
    )


    print(
        "=" * 72
    )

    print(
        "EUR/USD ORACLE "
        "HISTORICAL RANGE CLEANUP"
    )

    print(
        "=" * 72
    )


    print(
        f"\nStart: {start}"
    )


    print(
        f"End exclusive: {end}"
    )


    session = (
        SessionLocal()
    )


    try:

        tick_statement = (

            delete(
                Tick
            )

            .where(

                Tick.symbol
                == "EURUSD",

                Tick.timestamp
                >= start,

                Tick.timestamp
                < end
            )
        )


        candle_statement = (

            delete(
                BrokerCandle
            )

            .where(

                BrokerCandle.symbol
                == "EURUSD",

                BrokerCandle.timeframe
                == "M5",

                BrokerCandle.source
                == "MT5",

                BrokerCandle.timestamp
                >= start,

                BrokerCandle.timestamp
                < end
            )
        )


        tick_result = (
            session.execute(
                tick_statement
            )
        )


        candle_result = (
            session.execute(
                candle_statement
            )
        )


        session.commit()


        print(
            "\nTicks deleted: "
            f"{tick_result.rowcount}"
        )


        print(
            "Broker candles deleted: "
            f"{candle_result.rowcount}"
        )


        print(
            "\n"
            + "=" * 72
        )

        print(
            "HISTORICAL RANGE "
            "CLEANUP: COMPLETE"
        )

        print(
            "=" * 72
        )


    except Exception:

        session.rollback()

        raise


    finally:

        session.close()


if __name__ == "__main__":

    main()
    
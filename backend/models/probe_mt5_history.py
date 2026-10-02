import argparse

from datetime import (
    datetime,
    time,
    timedelta,
    timezone
)

import MetaTrader5 as mt5


DEFAULT_SYMBOL = "EURUSD"

DEFAULT_LOOKBACK_DAYS = 365

PROBE_WINDOW_MINUTES = 65

MINIMUM_BAR_COUNT = 12

MINIMUM_TICK_COUNT = 25


def parse_arguments():

    parser = argparse.ArgumentParser(

        description=(
            "Probe MT5 historical EUR/USD "
            "M5 and tick availability."
        )
    )


    parser.add_argument(

        "--symbol",

        default=DEFAULT_SYMBOL
    )


    parser.add_argument(

        "--days",

        type=int,

        default=DEFAULT_LOOKBACK_DAYS
    )


    return parser.parse_args()


def broker_now_utc(
    symbol
):

    tick = (
        mt5.symbol_info_tick(
            symbol
        )
    )


    if tick is None:

        raise RuntimeError(
            "Unable to obtain current "
            "MT5 symbol tick."
        )


    if (
        getattr(
            tick,
            "time_msc",
            0
        )
        > 0
    ):

        return datetime.fromtimestamp(

            tick.time_msc
            / 1000.0,

            tz=timezone.utc
        )


    return datetime.fromtimestamp(

        tick.time,

        tz=timezone.utc
    )


def create_probe_window(
    probe_date
):

    # Use a liquid part of the FX day.
    #
    # We inspect the 65 minutes ending
    # at 12:00 UTC.
    end = datetime.combine(

        probe_date,

        time(
            hour=12,
            minute=0
        ),

        tzinfo=timezone.utc
    )


    start = (

        end

        - timedelta(
            minutes=
                PROBE_WINDOW_MINUTES
        )
    )


    return start, end


def get_bar_count(
    symbol,
    start,
    end
):

    rates = (
        mt5.copy_rates_range(

            symbol,

            mt5.TIMEFRAME_M5,

            start,

            end
        )
    )


    if rates is None:

        return 0


    return len(
        rates
    )


def get_tick_count(
    symbol,
    start,
    end
):

    ticks = (
        mt5.copy_ticks_range(

            symbol,

            start,

            end,

            mt5.COPY_TICKS_ALL
        )
    )


    if ticks is None:

        return 0


    return len(
        ticks
    )


def main():

    arguments = (
        parse_arguments()
    )


    symbol = (
        arguments.symbol
    )


    lookback_days = (
        arguments.days
    )


    if lookback_days <= 0:

        raise ValueError(
            "--days must be greater "
            "than zero."
        )


    print(
        "=" * 72
    )

    print(
        "EUR/USD ORACLE "
        "MT5 HISTORICAL COVERAGE PROBE"
    )

    print(
        "=" * 72
    )


    if not mt5.initialize():

        error = (
            mt5.last_error()
        )

        raise RuntimeError(
            "MT5 initialization failed: "
            f"{error}"
        )


    try:

        if not mt5.symbol_select(
            symbol,
            True
        ):

            raise RuntimeError(
                f"Unable to select "
                f"{symbol} in MT5."
            )


        broker_now = (
            broker_now_utc(
                symbol
            )
        )


        print(
            f"\nSymbol: "
            f"{symbol}"
        )


        print(
            f"MT5 broker time: "
            f"{broker_now}"
        )


        print(
            f"Requested lookback: "
            f"{lookback_days} days"
        )


        print(
            f"Probe window: "
            f"{PROBE_WINDOW_MINUTES} minutes "
            "ending 12:00 UTC"
        )


        oldest_bar_date = None

        oldest_tick_date = None

        newest_bar_date = None

        newest_tick_date = None


        weekdays_tested = 0

        bar_days_available = 0

        tick_days_available = 0


        first_recent_bar_count = None

        first_recent_tick_count = None


        print(
            "\nProbing historical availability..."
        )


        for days_back in range(
            lookback_days
        ):

            probe_date = (

                broker_now.date()

                - timedelta(
                    days=days_back
                )
            )


            # Monday = 0
            # Friday = 4
            #
            # Skip weekends because their
            # absence tells us nothing about
            # historical storage coverage.
            if (
                probe_date.weekday()
                >= 5
            ):

                continue


            start, end = (
                create_probe_window(
                    probe_date
                )
            )


            # Do not probe a future window
            # on the current broker day.
            if start >= broker_now:

                continue


            if end > broker_now:

                end = broker_now


            weekdays_tested += 1


            bar_count = (
                get_bar_count(

                    symbol,

                    start,

                    end
                )
            )


            tick_count = (
                get_tick_count(

                    symbol,

                    start,

                    end
                )
            )


            if (
                first_recent_bar_count
                is None
            ):

                first_recent_bar_count = (
                    bar_count
                )


            if (
                first_recent_tick_count
                is None
            ):

                first_recent_tick_count = (
                    tick_count
                )


            bar_usable = (

                bar_count
                >= MINIMUM_BAR_COUNT
            )


            tick_usable = (

                tick_count
                >= MINIMUM_TICK_COUNT
            )


            if bar_usable:

                bar_days_available += 1


                if newest_bar_date is None:

                    newest_bar_date = (
                        probe_date
                    )


                oldest_bar_date = (
                    probe_date
                )


            if tick_usable:

                tick_days_available += 1


                if newest_tick_date is None:

                    newest_tick_date = (
                        probe_date
                    )


                oldest_tick_date = (
                    probe_date
                )


            # Small progress marker every
            # approximately 50 weekdays.
            if (
                weekdays_tested
                % 50
                == 0
            ):

                print(
                    f"Checked "
                    f"{weekdays_tested} "
                    "weekdays..."
                )


        print(
            "\n"
            + "-" * 72
        )

        print(
            "COVERAGE SUMMARY"
        )

        print(
            "-" * 72
        )


        print(
            f"Weekdays tested: "
            f"{weekdays_tested}"
        )


        print(
            f"\nUsable M5 days: "
            f"{bar_days_available}"
        )


        print(
            f"Newest usable M5 date: "
            f"{newest_bar_date}"
        )


        print(
            f"Oldest usable M5 date: "
            f"{oldest_bar_date}"
        )


        print(
            f"\nUsable tick days: "
            f"{tick_days_available}"
        )


        print(
            f"Newest usable tick date: "
            f"{newest_tick_date}"
        )


        print(
            f"Oldest usable tick date: "
            f"{oldest_tick_date}"
        )


        print(
            "\nMost recent probe counts:"
        )


        print(
            f"M5 bars: "
            f"{first_recent_bar_count}"
        )


        print(
            f"Ticks: "
            f"{first_recent_tick_count}"
        )


        if (
            oldest_bar_date is not None

            and

            oldest_tick_date is not None
        ):

            common_start = max(

                oldest_bar_date,

                oldest_tick_date
            )


            common_days = (

                broker_now.date()
                - common_start

            ).days


            print(
                "\n"
                + "-" * 72
            )

            print(
                "ML TRAINING COVERAGE"
            )

            print(
                "-" * 72
            )


            print(
                "Approximate common "
                "M5 + tick start: "
                f"{common_start}"
            )


            print(
                "Approximate common "
                "calendar span: "
                f"{common_days} days"
            )


        else:

            print(
                "\nNo common historical "
                "M5/tick coverage was "
                "identified."
            )


        print(
            "\n"
            + "=" * 72
        )

        print(
            "MT5 HISTORICAL COVERAGE "
            "PROBE: COMPLETE"
        )

        print(
            "=" * 72
        )


    finally:

        mt5.shutdown()


if __name__ == "__main__":

    main()
    
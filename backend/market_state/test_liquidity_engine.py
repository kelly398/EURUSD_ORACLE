from datetime import (
    datetime,
    timedelta,
    timezone
)

from backend.market_state.schemas import (
    SpreadState,
    TickSample
)

from backend.market_state.liquidity_engine import (
    LiquidityEngine
)

from backend.market_state.tick_repository import (
    TickRepository
)

from backend.market_state.candle_repository import (
    CandleRepository
)


def create_ticks(
    recent_spread,
    recent_ticks_per_minute,
    baseline_spread=1.50,
    baseline_ticks_per_minute=4
):

    as_of = datetime(
        2026,
        1,
        1,
        12,
        0,
        tzinfo=timezone.utc
    )


    start = (
        as_of
        - timedelta(
            minutes=65
        )
    )


    ticks = []


    for minute in range(65):

        is_recent = (
            minute >= 60
        )


        if is_recent:

            spread = (
                recent_spread
            )

            ticks_per_minute = (
                recent_ticks_per_minute
            )

        else:

            spread = (
                baseline_spread
            )

            ticks_per_minute = (
                baseline_ticks_per_minute
            )


        for tick_index in range(
            ticks_per_minute
        ):

            seconds = int(

                (
                    tick_index + 1
                )

                * 60

                / (
                    ticks_per_minute
                    + 1
                )
            )


            timestamp = (

                start

                + timedelta(
                    minutes=minute,
                    seconds=seconds
                )
            )


            bid = 1.10000


            ask = (

                bid

                + (
                    spread
                    / 10000
                )
            )


            source_time_msc = int(

                timestamp.timestamp()
                * 1000

            ) + tick_index


            ticks.append(

                TickSample(

                    symbol="EURUSD",

                    timestamp=
                        timestamp,

                    source_time_msc=
                        source_time_msc,

                    bid=bid,

                    ask=ask,

                    spread_pips=
                        spread
                )
            )


    return (
        ticks,
        as_of
    )


def print_result(
    title,
    result
):

    print(
        f"\n{title}"
    )

    print(
        "-" * 60
    )

    print(
        "Spread state: "
        f"{result.state.spread_state.value}"
    )

    print(
        "Recent average spread: "
        f"{result.recent_average_spread_pips:.4f} pips"
    )

    print(
        "Baseline average spread: "
        f"{result.baseline_average_spread_pips:.4f} pips"
    )

    print(
        "Spread ratio: "
        f"{result.spread_ratio:.4f}"
    )

    print(
        "Tick activity: "
        f"{result.state.tick_activity}"
    )

    print(
        "Recent ticks/minute: "
        f"{result.recent_ticks_per_minute:.4f}"
    )

    print(
        "Baseline ticks/minute: "
        f"{result.baseline_ticks_per_minute:.4f}"
    )

    print(
        "Tick activity ratio: "
        f"{result.tick_activity_ratio:.4f}"
    )

    print(
        "Recent ticks: "
        f"{result.recent_tick_count}"
    )

    print(
        "Baseline ticks: "
        f"{result.baseline_tick_count}"
    )


def main():

    engine = LiquidityEngine()


    # =================================
    # TIGHT SPREAD
    # =================================

    (
        tight_ticks,
        tight_as_of
    ) = create_ticks(

        recent_spread=0.80,

        recent_ticks_per_minute=4
    )


    tight_result = (
        engine.analyze_detailed(
            tight_ticks,
            tight_as_of
        )
    )


    print_result(
        "SYNTHETIC TIGHT LIQUIDITY",
        tight_result
    )


    assert (
        tight_result
        .state
        .spread_state
        == SpreadState.TIGHT
    )


    # =================================
    # NORMAL SPREAD
    # =================================

    (
        normal_ticks,
        normal_as_of
    ) = create_ticks(

        recent_spread=1.50,

        recent_ticks_per_minute=4
    )


    normal_result = (
        engine.analyze_detailed(
            normal_ticks,
            normal_as_of
        )
    )


    print_result(
        "SYNTHETIC NORMAL LIQUIDITY",
        normal_result
    )


    assert (
        normal_result
        .state
        .spread_state
        == SpreadState.NORMAL
    )


    # =================================
    # WIDE SPREAD
    # =================================

    (
        wide_ticks,
        wide_as_of
    ) = create_ticks(

        recent_spread=2.70,

        recent_ticks_per_minute=4
    )


    wide_result = (
        engine.analyze_detailed(
            wide_ticks,
            wide_as_of
        )
    )


    print_result(
        "SYNTHETIC WIDE LIQUIDITY",
        wide_result
    )


    assert (
        wide_result
        .state
        .spread_state
        == SpreadState.WIDE
    )


    # =================================
    # EXTREME SPREAD
    # =================================

    (
        extreme_ticks,
        extreme_as_of
    ) = create_ticks(

        recent_spread=4.50,

        recent_ticks_per_minute=4
    )


    extreme_result = (
        engine.analyze_detailed(
            extreme_ticks,
            extreme_as_of
        )
    )


    print_result(
        "SYNTHETIC EXTREME LIQUIDITY",
        extreme_result
    )


    assert (
        extreme_result
        .state
        .spread_state
        == SpreadState.EXTREME
    )


    # =================================
    # LOW TICK ACTIVITY
    # =================================

    (
        low_activity_ticks,
        low_activity_as_of
    ) = create_ticks(

        recent_spread=1.50,

        recent_ticks_per_minute=1
    )


    low_activity_result = (
        engine.analyze_detailed(
            low_activity_ticks,
            low_activity_as_of
        )
    )


    print_result(
        "SYNTHETIC LOW TICK ACTIVITY",
        low_activity_result
    )


    assert (
        low_activity_result
        .state
        .tick_activity
        == "low"
    )


    # =================================
    # LIVE HFM / MT5 DATA
    # =================================

    candle_repository = (
        CandleRepository(
            source="MT5"
        )
    )


    latest_candles = (
        candle_repository.get_latest(

            symbol="EURUSD",

            timeframe="M5",

            limit=1
        )
    )


    print(
        "\n"
        + "=" * 60
    )

    print(
        "LIVE EUR/USD LIQUIDITY TEST"
    )

    print(
        "=" * 60
    )


    if not latest_candles:

        print(
            "No canonical candle "
            "available."
        )

    else:

        latest_candle = (
            latest_candles[-1]
        )


        # broker_candles.timestamp is
        # the OPEN time of the M5 bar.
        #
        # Add five minutes so the
        # liquidity window ends exactly
        # when this completed candle ended.
        as_of = (

            latest_candle.timestamp

            + timedelta(
                minutes=5
            )
        )


        tick_repository = (
            TickRepository()
        )


        start = (

            as_of

            - timedelta(
                minutes=65
            )
        )


        live_ticks = (
            tick_repository.get_between(

                start=start,

                end=as_of,

                symbol="EURUSD"
            )
        )


        print(
            f"Aligned candle: "
            f"{latest_candle.timestamp}"
        )

        print(
            f"Liquidity as-of: "
            f"{as_of}"
        )

        print(
            f"Ticks loaded: "
            f"{len(live_ticks)}"
        )


        if len(live_ticks) < 25:

            print(
                "Not enough raw ticks "
                "for live liquidity test."
            )

        else:

            live_result = (
                engine.analyze_detailed(
                    live_ticks,
                    as_of
                )
            )


            print_result(
                "CURRENT EUR/USD M5 LIQUIDITY",
                live_result
            )


    print(
        "\n"
        + "=" * 60
    )

    print(
        "LIQUIDITY ENGINE TEST: PASS"
    )

    print(
        "=" * 60
    )


if __name__ == "__main__":

    main()
    
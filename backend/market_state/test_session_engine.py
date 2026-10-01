from datetime import (
    datetime,
    timedelta,
    timezone
)

from backend.market_state.session_engine import (
    SessionEngine
)

from backend.market_state.candle_repository import (
    CandleRepository
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
        f"Session: "
        f"{result.session}"
    )

    print(
        f"Market open: "
        f"{result.market_open}"
    )

    print(
        "Active sessions: "
        f"{result.active_sessions}"
    )

    print(
        f"Overlap: "
        f"{result.overlap}"
    )

    print(
        f"UTC: "
        f"{result.utc_time}"
    )

    print(
        f"London: "
        f"{result.london_time}"
    )

    print(
        f"New York: "
        f"{result.new_york_time}"
    )

    print(
        f"Tokyo: "
        f"{result.tokyo_time}"
    )


def main():

    engine = SessionEngine()


    # =================================
    # TOKYO SESSION
    # =================================

    tokyo_time = datetime(
        2026,
        1,
        15,
        1,
        0,
        tzinfo=timezone.utc
    )


    tokyo_result = (
        engine.analyze(
            tokyo_time
        )
    )


    print_result(
        "SYNTHETIC TOKYO SESSION",
        tokyo_result
    )


    assert (
        tokyo_result.session
        == "tokyo"
    )


    assert (
        tokyo_result.market_open
        is True
    )


    # =================================
    # LONDON SESSION
    # =================================

    london_time = datetime(
        2026,
        1,
        15,
        10,
        0,
        tzinfo=timezone.utc
    )


    london_result = (
        engine.analyze(
            london_time
        )
    )


    print_result(
        "SYNTHETIC LONDON SESSION",
        london_result
    )


    assert (
        london_result.session
        == "london"
    )


    assert (
        london_result.market_open
        is True
    )


    # =================================
    # LONDON / NEW YORK OVERLAP
    # =================================

    overlap_time = datetime(
        2026,
        1,
        15,
        14,
        0,
        tzinfo=timezone.utc
    )


    overlap_result = (
        engine.analyze(
            overlap_time
        )
    )


    print_result(
        "SYNTHETIC LONDON/NY OVERLAP",
        overlap_result
    )


    assert (
        overlap_result.session
        == "london_new_york_overlap"
    )


    assert (
        overlap_result.overlap
        is True
    )


    assert (
        "london"
        in overlap_result.active_sessions
    )


    assert (
        "new_york"
        in overlap_result.active_sessions
    )


    # =================================
    # SUMMER DST OVERLAP
    # =================================

    summer_overlap_time = datetime(
        2026,
        7,
        15,
        12,
        30,
        tzinfo=timezone.utc
    )


    summer_result = (
        engine.analyze(
            summer_overlap_time
        )
    )


    print_result(
        "SYNTHETIC SUMMER DST OVERLAP",
        summer_result
    )


    assert (
        summer_result.session
        == "london_new_york_overlap"
    )


    assert (
        summer_result.overlap
        is True
    )


    assert (
        summer_result.london_time.hour
        == 13
    )


    assert (
        summer_result.new_york_time.hour
        == 8
    )


    # =================================
    # OFF-SESSION PERIOD
    # =================================

    off_session_time = datetime(
        2026,
        1,
        15,
        23,
        0,
        tzinfo=timezone.utc
    )


    off_session_result = (
        engine.analyze(
            off_session_time
        )
    )


    print_result(
        "SYNTHETIC OFF-SESSION PERIOD",
        off_session_result
    )


    assert (
        off_session_result.session
        == "off_session"
    )


    assert (
        off_session_result.market_open
        is True
    )


    # =================================
    # WEEKEND CLOSED
    # =================================

    weekend_time = datetime(
        2026,
        1,
        17,
        12,
        0,
        tzinfo=timezone.utc
    )


    weekend_result = (
        engine.analyze(
            weekend_time
        )
    )


    print_result(
        "SYNTHETIC WEEKEND",
        weekend_result
    )


    assert (
        weekend_result.session
        == "market_closed"
    )


    assert (
        weekend_result.market_open
        is False
    )


    assert (
        len(
            weekend_result.active_sessions
        )
        == 0
    )


    # =================================
    # SUNDAY REOPEN
    # =================================

    sunday_reopen = datetime(
        2026,
        1,
        18,
        23,
        0,
        tzinfo=timezone.utc
    )


    sunday_result = (
        engine.analyze(
            sunday_reopen
        )
    )


    print_result(
        "SYNTHETIC SUNDAY REOPEN",
        sunday_result
    )


    assert (
        sunday_result.market_open
        is True
    )


    # =================================
    # LIVE CANONICAL EUR/USD BAR
    # =================================

    repository = CandleRepository(
        source="MT5"
    )


    candles = (
        repository.get_latest(
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
        "LIVE EUR/USD SESSION TEST"
    )

    print(
        "=" * 60
    )


    if not candles:

        print(
            "No canonical M5 "
            "candle available."
        )

    else:

        latest_candle = (
            candles[-1]
        )


        # broker_candles.timestamp
        # is the OPEN time of the
        # completed M5 candle.
        #
        # Add five minutes so the
        # session analysis is aligned
        # to the CLOSE of that candle.
        as_of = (
            latest_candle.timestamp
            + timedelta(
                minutes=5
            )
        )


        live_result = (
            engine.analyze(
                as_of
            )
        )


        print(
            f"Aligned candle: "
            f"{latest_candle.timestamp}"
        )


        print(
            f"Session as-of: "
            f"{as_of}"
        )


        print_result(
            "CURRENT EUR/USD SESSION",
            live_result
        )


    print(
        "\n"
        + "=" * 60
    )

    print(
        "SESSION ENGINE TEST: PASS"
    )

    print(
        "=" * 60
    )


if __name__ == "__main__":

    main()
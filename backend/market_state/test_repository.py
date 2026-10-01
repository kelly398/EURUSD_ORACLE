from backend.market_state.candle_repository import (
    CandleRepository
)


def main():

    repository = CandleRepository(
        source="MT5"
    )


    candles = repository.get_latest(

        symbol="EURUSD",

        timeframe="M5",

        limit=20
    )


    print(
        "=" * 60
    )

    print(
        "EUR/USD ORACLE "
        "CANDLE REPOSITORY TEST"
    )

    print(
        "=" * 60
    )


    print(
        f"Candles returned: "
        f"{len(candles)}"
    )


    if not candles:

        print(
            "TEST FAILED: "
            "No candles returned."
        )

        return


    # --------------------------------
    # CHRONOLOGICAL ORDER
    # --------------------------------

    timestamps = [

        candle.timestamp

        for candle in candles
    ]


    chronological = (
        timestamps
        == sorted(timestamps)
    )


    print(
        f"Chronological order: "
        f"{chronological}"
    )


    # --------------------------------
    # SYMBOL CONSISTENCY
    # --------------------------------

    symbol_valid = all(

        candle.symbol
        == "EURUSD"

        for candle in candles
    )


    print(
        f"Symbol consistency: "
        f"{symbol_valid}"
    )


    # --------------------------------
    # TIMEFRAME CONSISTENCY
    # --------------------------------

    timeframe_valid = all(

        candle.timeframe
        == "M5"

        for candle in candles
    )


    print(
        f"Timeframe consistency: "
        f"{timeframe_valid}"
    )


    # --------------------------------
    # SOURCE CONSISTENCY
    # --------------------------------

    source_valid = all(

        candle.source
        == "MT5"

        for candle in candles
    )


    print(
        f"Canonical source: "
        f"{source_valid}"
    )


    # --------------------------------
    # DISPLAY RANGE
    # --------------------------------

    first = candles[0]

    latest = candles[-1]


    print(
        "\nOldest candle:"
    )

    print(
        first
    )


    print(
        "\nLatest candle:"
    )

    print(
        latest
    )


    # --------------------------------
    # FINAL RESULT
    # --------------------------------

    passed = all(
        [
            chronological,
            symbol_valid,
            timeframe_valid,
            source_valid
        ]
    )


    print(
        "\n"
        + "=" * 60
    )


    if passed:

        print(
            "REPOSITORY TEST: PASS"
        )

    else:

        print(
            "REPOSITORY TEST: FAIL"
        )


    print(
        "=" * 60
    )


if __name__ == "__main__":

    main()
    
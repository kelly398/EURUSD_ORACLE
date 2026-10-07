from datetime import (
    date,
    datetime,
    timedelta,
    timezone
)

from types import (
    SimpleNamespace
)

from backend.institutional.cot_development_dataset import (
    COTDevelopmentDatasetBuilder,
    DevelopmentSplitWindow
)

from backend.institutional.cot_schemas import (
    COTPositioningSnapshot,
    COT_REPORT_TYPE_TFF_FUTURES_ONLY,
    EURO_FX_CFTC_CONTRACT_MARKET_CODE
)


def make_cot(
    index
):

    return COTPositioningSnapshot(

        report_type=(
            COT_REPORT_TYPE_TFF_FUTURES_ONLY
        ),

        cftc_contract_market_code=(
            EURO_FX_CFTC_CONTRACT_MARKET_CODE
        ),

        market_and_exchange_names=(
            "EURO FX - "
            "CHICAGO MERCANTILE EXCHANGE"
        ),

        contract_market_name="EURO FX",

        commodity_name=(
            "EUROPEAN CURRENCY UNIT"
        ),

        report_date=(
            date(
                2024,
                1,
                2
            )
            + timedelta(
                days=(
                    7
                    * index
                )
            )
        ),

        available_at=(
            datetime(
                2024,
                1,
                5,
                12,
                0,
                8,
                tzinfo=timezone.utc
            )
            + timedelta(
                days=(
                    7
                    * index
                )
            )
        ),

        open_interest=(
            1_000_000
            + index
        ),

        dealer_long=(
            100_000
            + index
        ),

        dealer_short=
            300_000,

        dealer_spread=
            8_000,

        asset_manager_long=(
            400_000
            + (
                index
                * 100
            )
        ),

        asset_manager_short=
            300_000,

        asset_manager_spread=
            40_000,

        leveraged_money_long=(
            200_000
            + (
                index
                * 50
            )
        ),

        leveraged_money_short=
            250_000,

        leveraged_money_spread=
            20_000,

        other_reportables_long=
            28_000,

        other_reportables_short=
            20_000,

        other_reportables_spread=
            4_000,

        nonreportable_long=
            80_000,

        nonreportable_short=
            58_000,

        source_record_id=(
            f"development-{index}"
        ),

        content_hash=(
            f"{index + 1:064x}"
        ),

        source="CFTC",

        ingested_at=(
            datetime(
                2026,
                1,
                1,
                tzinfo=timezone.utc
            )
        )
    )


def make_candles(
    start,
    count
):

    rows = []


    for index in range(
        count
    ):

        price = (
            1.1000
            + (
                index
                * 0.00001
            )
        )


        rows.append(

            SimpleNamespace(

                timestamp=(
                    start
                    + timedelta(
                        minutes=(
                            5
                            * index
                        )
                    )
                ),

                open=
                    price,

                close=(
                    price
                    + 0.00002
                )
            )
        )


    return tuple(
        rows
    )


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "COT DEVELOPMENT DATASET TEST"
    )

    print(
        "=" * 70
    )


    builder = (
        COTDevelopmentDatasetBuilder()
    )


    cot_history = tuple(

        make_cot(
            index
        )

        for index
        in range(
            53
        )
    )


    current = (
        cot_history[-1]
    )


    candle_start = (
        current.available_at
        .replace(
            second=0,
            microsecond=0
        )
    )


    candles = (
        make_candles(
            start=candle_start,
            count=1300
        )
    )


    candle_timestamps = tuple(

        candle.timestamp

        for candle
        in candles
    )


    split = (
        DevelopmentSplitWindow(

            name="train",

            start=(
                candle_start
                - timedelta(
                    hours=1
                )
            ),

            end=(
                candle_start
                + timedelta(
                    days=10
                )
            )
        )
    )


    event = builder._build_event(

        cot_item=
            current,

        cot_history=
            cot_history,

        candles=
            candles,

        candle_timestamps=
            candle_timestamps,

        split_window=
            split
    )


    assert (
        event
        is not None
    )


    # available_at is 12:00:08.
    #
    # The 12:00 candle is forbidden.
    # Entry must be 12:05.
    assert (
        event.entry_timestamp
        == (
            candle_start
            + timedelta(
                minutes=5
            )
        )
    )


    print(
        "\nStrict post-publication entry: PASS"
    )


    # Entry index is 1.
    #
    # 288 completed bars means the
    # target bar index is:
    #
    # 1 + 288 - 1 = 288.
    expected_1d_bar = (
        candles[
            288
        ]
    )

    expected_3d_bar = (
        candles[
            864
        ]
    )

    expected_4d_bar = (
        candles[
            1152
        ]
    )


    assert (
        event.target_1d_timestamp
        == (
            expected_1d_bar.timestamp
            + timedelta(
                minutes=5
            )
        )
    )

    assert (
        event.target_3d_timestamp
        == (
            expected_3d_bar.timestamp
            + timedelta(
                minutes=5
            )
        )
    )

    assert (
        event.target_4d_timestamp
        == (
            expected_4d_bar.timestamp
            + timedelta(
                minutes=5
            )
        )
    )


    print(
        "Exact completed-bar horizons: PASS"
    )


    expected_1d_return = (

        float(
            expected_1d_bar.close
            - event.entry_open
        )

        / 0.0001
    )


    expected_3d_return = (

        float(
            expected_3d_bar.close
            - event.entry_open
        )

        / 0.0001
    )


    expected_4d_return = (

        float(
            expected_4d_bar.close
            - event.entry_open
        )

        / 0.0001
    )


    assert abs(
        event.return_1d_pips
        - expected_1d_return
    ) < 1e-12

    assert abs(
        event.return_3d_pips
        - expected_3d_return
    ) < 1e-12

    assert abs(
        event.return_4d_pips
        - expected_4d_return
    ) < 1e-12


    print(
        "Forward-return formulas: PASS"
    )


    assert (
        event.feature_vector.report_date
        == current.report_date
    )

    assert (
        event.feature_vector.available_at
        == current.available_at
    )


    print(
        "Feature/event causal alignment: PASS"
    )


    # ==================================
    # SPLIT-BOUNDARY REJECTION
    # ==================================

    too_short_split = (
        DevelopmentSplitWindow(

            name="train",

            start=split.start,

            end=(
                event.target_4d_timestamp
                - timedelta(
                    microseconds=1
                )
            )
        )
    )


    rejected = (
        builder._build_event(

            cot_item=
                current,

            cot_history=
                cot_history,

            candles=
                candles,

            candle_timestamps=
                candle_timestamps,

            split_window=
                too_short_split
        )
    )


    assert (
        rejected
        is None
    )


    print(
        "Cross-split outcome rejection: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "COT DEVELOPMENT DATASET TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
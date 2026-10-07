from datetime import (
    date,
    datetime,
    timezone
)

from pydantic import ValidationError

from backend.institutional.cot_schemas import (
    COTPositioningSnapshot,
    COT_REPORT_TYPE_TFF_FUTURES_ONLY,
    EURO_FX_CFTC_CONTRACT_MARKET_CODE
)


def build_valid_snapshot():

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

        commodity_name="EURO FX",

        report_date=date(
            2026,
            9,
            29
        ),

        available_at=datetime(
            2026,
            10,
            2,
            19,
            30,
            tzinfo=timezone.utc
        ),

        open_interest=853_959,

        dealer_long=47_546,
        dealer_short=259_712,
        dealer_spread=7_214,

        asset_manager_long=500_353,
        asset_manager_short=279_750,
        asset_manager_spread=43_567,

        leveraged_money_long=124_857,
        leveraged_money_short=164_122,
        leveraged_money_spread=19_175,

        other_reportables_long=27_547,
        other_reportables_short=19_398,
        other_reportables_spread=3_635,

        nonreportable_long=80_065,
        nonreportable_short=57_386,

        source_record_id=(
            "test-euro-fx-2026-09-29"
        ),

        content_hash=(
            "a" * 64
        ),

        source="CFTC",

        ingested_at=datetime(
            2026,
            10,
            2,
            19,
            35,
            tzinfo=timezone.utc
        )
    )


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "COT SCHEMA CONTRACT TEST"
    )

    print(
        "=" * 70
    )


    snapshot = (
        build_valid_snapshot()
    )


    assert (
        snapshot.report_type
        == COT_REPORT_TYPE_TFF_FUTURES_ONLY
    )

    assert (
        snapshot.cftc_contract_market_code
        == "099741"
    )

    assert (
        snapshot.report_date
        == date(
            2026,
            9,
            29
        )
    )

    assert (
        snapshot.available_at
        > datetime(
            2026,
            9,
            29,
            tzinfo=timezone.utc
        )
    )

    print(
        "\nFrozen immutable schema: PASS"
    )

    print(
        "Leading-zero contract code: PASS"
    )

    print(
        "Report/publication separation: PASS"
    )


    # ==================================
    # IMMUTABILITY
    # ==================================

    immutable_blocked = False

    try:

        snapshot.open_interest = 1

    except Exception:

        immutable_blocked = True


    assert immutable_blocked

    print(
        "Mutation rejection: PASS"
    )


    # ==================================
    # NAIVE TIMESTAMP REJECTION
    # ==================================

    try:

        COTPositioningSnapshot(
            **{
                **snapshot.model_dump(),
                "available_at":
                    datetime(
                        2026,
                        10,
                        2,
                        19,
                        30
                    )
            }
        )

    except ValidationError:

        pass

    else:

        raise AssertionError(
            "Naive available_at "
            "was incorrectly accepted."
        )


    print(
        "Timezone enforcement: PASS"
    )


    # ==================================
    # IMPOSSIBLE TEMPORAL ORDER
    # ==================================

    try:

        COTPositioningSnapshot(
            **{
                **snapshot.model_dump(),
                "available_at":
                    datetime(
                        2026,
                        9,
                        28,
                        19,
                        30,
                        tzinfo=timezone.utc
                    )
            }
        )

    except ValidationError:

        pass

    else:

        raise AssertionError(
            "available_at before report_date "
            "was incorrectly accepted."
        )


    print(
        "Temporal-order guard: PASS"
    )


    # ==================================
    # NEGATIVE POSITION REJECTION
    # ==================================

    try:

        COTPositioningSnapshot(
            **{
                **snapshot.model_dump(),
                "dealer_long": -1
            }
        )

    except ValidationError:

        pass

    else:

        raise AssertionError(
            "Negative COT position "
            "was incorrectly accepted."
        )


    print(
        "Non-negative positions: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "COT SCHEMA CONTRACT TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
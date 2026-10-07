from datetime import (
    date,
    datetime,
    timedelta,
    timezone
)

from statistics import (
    fmean,
    pstdev
)

from pydantic import (
    ValidationError
)

from backend.institutional.cot_feature_builder import (
    COTFeatureBuilder
)

from backend.institutional.cot_research_protocol import (
    CONTEXT_FEATURES,
    DIRECTIONAL_CANDIDATE_FEATURES,
    FEATURE_VERSION
)

from backend.institutional.cot_schemas import (
    COTPositioningSnapshot,
    COT_REPORT_TYPE_TFF_FUTURES_ONLY,
    EURO_FX_CFTC_CONTRACT_MARKET_CODE
)


def make_snapshot(
    index,
    *,
    contract_code=(
        EURO_FX_CFTC_CONTRACT_MARKET_CODE
    ),
    open_interest=None
):

    report_date = (
        date(
            2025,
            1,
            7
        )
        + timedelta(
            days=(
                7
                * index
            )
        )
    )


    available_at = (
        datetime(
            2025,
            1,
            10,
            20,
            30,
            tzinfo=timezone.utc
        )
        + timedelta(
            days=(
                7
                * index
            )
        )
    )


    if open_interest is None:

        open_interest = (
            1_000_000
            + (
                index
                * 1_000
            )
        )


    return COTPositioningSnapshot(

        report_type=(
            COT_REPORT_TYPE_TFF_FUTURES_ONLY
        ),

        cftc_contract_market_code=(
            contract_code
        ),

        market_and_exchange_names=(
            "EURO FX - "
            "CHICAGO MERCANTILE EXCHANGE"
        ),

        contract_market_name=(
            "EURO FX"
        ),

        commodity_name=(
            "EUROPEAN CURRENCY UNIT"
        ),

        report_date=
            report_date,

        available_at=
            available_at,

        open_interest=
            open_interest,

        dealer_long=(
            100_000
            + (
                index
                * 300
            )
        ),

        dealer_short=(
            300_000
            + (
                index
                * 50
            )
        ),

        dealer_spread=
            8_000,

        asset_manager_long=(
            400_000
            + (
                index
                * 1_500
            )
        ),

        asset_manager_short=(
            300_000
            + (
                index
                * 200
            )
        ),

        asset_manager_spread=
            40_000,

        leveraged_money_long=(
            200_000
            + (
                index
                * 500
            )
        ),

        leveraged_money_short=(
            250_000
            + (
                index
                * 100
            )
        ),

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
            f"synthetic-{index}"
        ),

        content_hash=(
            f"{index:064x}"
        ),

        source="CFTC",

        ingested_at=
            available_at
    )


def net_share(
    item,
    long_field,
    short_field
):

    return (

        float(
            getattr(
                item,
                long_field
            )
            - getattr(
                item,
                short_field
            )
        )

        / float(
            item.open_interest
        )
    )


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "COT FEATURE BUILDER TEST"
    )

    print(
        "=" * 70
    )


    history = tuple(

        make_snapshot(
            index
        )

        for index
        in range(
            53
        )
    )


    builder = (
        COTFeatureBuilder()
    )


    vector = (
        builder.build(
            history
        )
    )


    expected_names = (
        DIRECTIONAL_CANDIDATE_FEATURES
        + CONTEXT_FEATURES
    )


    assert (
        vector.feature_version
        == FEATURE_VERSION
    )

    assert (
        vector.feature_names
        == expected_names
    )

    assert (
        len(
            vector.values
        )
        == 11
    )


    print(
        "\nFrozen feature schema: PASS"
    )


    current = (
        history[-1]
    )

    previous = (
        history[-2]
    )

    four_ago = (
        history[-5]
    )


    current_asset = (
        net_share(
            current,
            "asset_manager_long",
            "asset_manager_short"
        )
    )

    previous_asset = (
        net_share(
            previous,
            "asset_manager_long",
            "asset_manager_short"
        )
    )

    asset_four_ago = (
        net_share(
            four_ago,
            "asset_manager_long",
            "asset_manager_short"
        )
    )


    current_leveraged = (
        net_share(
            current,
            "leveraged_money_long",
            "leveraged_money_short"
        )
    )

    previous_leveraged = (
        net_share(
            previous,
            "leveraged_money_long",
            "leveraged_money_short"
        )
    )

    leveraged_four_ago = (
        net_share(
            four_ago,
            "leveraged_money_long",
            "leveraged_money_short"
        )
    )


    current_dealer = (
        net_share(
            current,
            "dealer_long",
            "dealer_short"
        )
    )


    reference = (
        history[:-1]
    )


    asset_reference = tuple(

        net_share(
            item,
            "asset_manager_long",
            "asset_manager_short"
        )

        for item
        in reference
    )


    leveraged_reference = tuple(

        net_share(
            item,
            "leveraged_money_long",
            "leveraged_money_short"
        )

        for item
        in reference
    )


    expected_asset_z = (

        current_asset
        - fmean(
            asset_reference
        )

    ) / pstdev(
        asset_reference
    )


    expected_leveraged_z = (

        current_leveraged
        - fmean(
            leveraged_reference
        )

    ) / pstdev(
        leveraged_reference
    )


    features = (
        vector.as_dict()
    )


    assert abs(
        features[
            "asset_manager_net_share"
        ]
        - current_asset
    ) < 1e-12


    assert abs(
        features[
            "leveraged_money_net_share"
        ]
        - current_leveraged
    ) < 1e-12


    assert abs(
        features[
            "asset_manager_net_share_change_1"
        ]
        - (
            current_asset
            - previous_asset
        )
    ) < 1e-12


    assert abs(
        features[
            "leveraged_money_net_share_change_1"
        ]
        - (
            current_leveraged
            - previous_leveraged
        )
    ) < 1e-12


    assert abs(
        features[
            "asset_manager_net_share_change_4"
        ]
        - (
            current_asset
            - asset_four_ago
        )
    ) < 1e-12


    assert abs(
        features[
            "leveraged_money_net_share_change_4"
        ]
        - (
            current_leveraged
            - leveraged_four_ago
        )
    ) < 1e-12


    assert abs(
        features[
            "asset_manager_minus_leveraged_net_share"
        ]
        - (
            current_asset
            - current_leveraged
        )
    ) < 1e-12


    assert abs(
        features[
            "dealer_net_share"
        ]
        - current_dealer
    ) < 1e-12


    expected_oi_change = (

        float(
            current.open_interest
            - previous.open_interest
        )

        / float(
            previous.open_interest
        )
    )


    assert abs(
        features[
            "open_interest_change_1"
        ]
        - expected_oi_change
    ) < 1e-12


    assert abs(
        features[
            "asset_manager_net_share_z52"
        ]
        - expected_asset_z
    ) < 1e-12


    assert abs(
        features[
            "leveraged_money_net_share_z52"
        ]
        - expected_leveraged_z
    ) < 1e-12


    print(
        "Net-share formulas: PASS"
    )

    print(
        "1-report changes: PASS"
    )

    print(
        "4-report changes: PASS"
    )

    print(
        "Open-interest change: PASS"
    )

    print(
        "Prior-52 z-scores: PASS"
    )


    assert (
        vector.report_date
        == current.report_date
    )

    assert (
        vector.available_at
        == current.available_at
    )


    print(
        "Current-report identity: PASS"
    )


    # ==================================
    # IMMUTABILITY
    # ==================================

    try:

        vector.values = tuple()

    except ValidationError:

        pass

    else:

        raise AssertionError(
            "COT feature vector "
            "was mutable."
        )


    print(
        "Frozen feature vector: PASS"
    )


    # ==================================
    # MINIMUM HISTORY
    # ==================================

    try:

        builder.build(
            history[:-1]
        )

    except ValueError:

        pass

    else:

        raise AssertionError(
            "52-report history was "
            "incorrectly accepted."
        )


    print(
        "53-report minimum history: PASS"
    )


    # ==================================
    # CHRONOLOGICAL ORDER
    # ==================================

    bad_order = list(
        history
    )

    bad_order[-1], bad_order[-2] = (
        bad_order[-2],
        bad_order[-1]
    )


    try:

        builder.build(
            tuple(
                bad_order
            )
        )

    except ValueError:

        pass

    else:

        raise AssertionError(
            "Out-of-order COT history "
            "was accepted."
        )


    print(
        "Chronological history guard: PASS"
    )


    # ==================================
    # MIXED CONTRACT
    # ==================================

    mixed_contract = (
        history[:-1]
        + (
            make_snapshot(
                52,
                contract_code="000000"
            ),
        )
    )


    try:

        builder.build(
            mixed_contract
        )

    except ValueError:

        pass

    else:

        raise AssertionError(
            "Mixed COT contract history "
            "was accepted."
        )


    print(
        "Single-contract guard: PASS"
    )


    # ==================================
    # ZERO OPEN INTEREST
    # ==================================

    zero_oi_history = (
        history[:-1]
        + (
            make_snapshot(
                52,
                open_interest=0
            ),
        )
    )


    try:

        builder.build(
            zero_oi_history
        )

    except ValueError:

        pass

    else:

        raise AssertionError(
            "Zero open interest was "
            "incorrectly accepted."
        )


    print(
        "Positive-open-interest guard: PASS"
    )


    # ==================================
    # ZERO VARIANCE REFERENCE
    # ==================================

    constant_history = tuple(

        history[0].model_copy(
            update={
                "report_date":
                    (
                        history[0].report_date
                        + timedelta(
                            days=(
                                7
                                * index
                            )
                        )
                    ),

                "available_at":
                    (
                        history[0].available_at
                        + timedelta(
                            days=(
                                7
                                * index
                            )
                        )
                    ),

                "source_record_id":
                    f"constant-{index}",

                "content_hash":
                    f"{index + 1000:064x}"
            }
        )

        for index
        in range(
            53
        )
    )


    try:

        builder.build(
            constant_history
        )

    except ValueError:

        pass

    else:

        raise AssertionError(
            "Zero-variance rolling "
            "reference was accepted."
        )


    print(
        "Zero-variance z-score guard: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "COT FEATURE BUILDER TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
from backend.institutional.cot_research_protocol import (
    ALLOW_FEATURE_SEARCH,
    ALLOW_MULTIVARIATE_MODEL,
    ALLOW_THRESHOLD_TUNING,
    CONTEXT_FEATURES,
    DEVELOPMENT_SPLITS,
    DIRECTIONAL_CANDIDATE_FEATURES,
    M5_BARS_PER_TRADING_DAY,
    MINIMUM_COT_HISTORY_REPORTS,
    OBSERVED_RELEASE_EVENT_COUNTS,
    PRIMARY_HORIZON_BARS,
    ROLLING_REFERENCE_REPORTS,
    SEALED_SPLIT,
    SECONDARY_HORIZON_BARS,
    TEST_EVALUATED
)


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "SEAT 22 COT RESEARCH PROTOCOL TEST"
    )

    print(
        "=" * 70
    )


    assert (
        M5_BARS_PER_TRADING_DAY
        == 288
    )

    assert (
        SECONDARY_HORIZON_BARS
        == (
            288,
            864
        )
    )

    assert (
        PRIMARY_HORIZON_BARS
        == 1152
    )


    print(
        "\nCOT-specific horizons: PASS"
    )


    assert (
        MINIMUM_COT_HISTORY_REPORTS
        == 53
    )

    assert (
        ROLLING_REFERENCE_REPORTS
        == 52
    )


    print(
        "Causal rolling-history contract: PASS"
    )


    assert (
        DEVELOPMENT_SPLITS
        == (
            "train",
            "validation"
        )
    )

    assert (
        SEALED_SPLIT
        == "test"
    )

    assert (
        TEST_EVALUATED
        is False
    )


    print(
        "Reserved test remains sealed: PASS"
    )


    assert (
        OBSERVED_RELEASE_EVENT_COUNTS
        == {
            "train": 37,
            "validation": 7,
            "test": 8
        }
    )


    print(
        "Small-sample disclosure: PASS"
    )


    assert (
        len(
            DIRECTIONAL_CANDIDATE_FEATURES
        )
        == len(
            set(
                DIRECTIONAL_CANDIDATE_FEATURES
            )
        )
    )

    assert (
        len(
            CONTEXT_FEATURES
        )
        == len(
            set(
                CONTEXT_FEATURES
            )
        )
    )

    assert not (
        set(
            DIRECTIONAL_CANDIDATE_FEATURES
        )
        & set(
            CONTEXT_FEATURES
        )
    )


    print(
        "Pre-registered feature schema: PASS"
    )


    assert (
        ALLOW_MULTIVARIATE_MODEL
        is False
    )

    assert (
        ALLOW_FEATURE_SEARCH
        is False
    )

    assert (
        ALLOW_THRESHOLD_TUNING
        is False
    )


    print(
        "Anti-overfit governance: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "COT RESEARCH PROTOCOL TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
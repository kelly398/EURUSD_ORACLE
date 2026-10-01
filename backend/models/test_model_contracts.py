from datetime import (
    datetime,
    timedelta,
    timezone
)

from backend.models.artifact_contracts import (
    ModelArtifactMetadata,
    ModelFamily
)

from backend.models.compatibility import (
    ModelCompatibilityChecker
)

from backend.models.feature_contracts import (
    ModelFeatureVector
)

from backend.models.labeling import (
    DirectionLabeler
)

from backend.models.training_contracts import (
    LabelSpecification,
    TargetLabel
)


def create_features():

    names = (
        "feature_a",
        "feature_b",
        "feature_c"
    )


    return ModelFeatureVector(

        symbol="EURUSD",

        timeframe="M5",

        as_of=datetime(
            2026,
            1,
            5,
            12,
            0,
            tzinfo=timezone.utc
        ),

        feature_version=
            "test_feature_v1",

        feature_names=
            names,

        values=(
            1.0,
            2.0,
            3.0
        )
    )


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "MODEL CONTRACT TEST"
    )

    print(
        "=" * 70
    )


    features = (
        create_features()
    )


    label_spec = (
        LabelSpecification(

            label_version=
                "test_direction_v1",

            horizon_bars=3,

            timeframe_minutes=5,

            neutral_band_pips=1.0
        )
    )


    labeler = (
        DirectionLabeler(
            label_spec
        )
    )


    # ==================================
    # LONG LABEL
    # ==================================

    long_sample = (
        labeler.create_sample(

            features=
                features,

            current_close=
                1.10000,

            future_close=
                1.10025
        )
    )


    assert (
        long_sample.target_label
        == TargetLabel.LONG
    )


    # ==================================
    # SHORT LABEL
    # ==================================

    short_sample = (
        labeler.create_sample(

            features=
                features,

            current_close=
                1.10000,

            future_close=
                1.09975
        )
    )


    assert (
        short_sample.target_label
        == TargetLabel.SHORT
    )


    # ==================================
    # NEUTRAL LABEL
    # ==================================

    neutral_sample = (
        labeler.create_sample(

            features=
                features,

            current_close=
                1.10000,

            future_close=
                1.10005
        )
    )


    assert (
        neutral_sample.target_label
        == TargetLabel.NEUTRAL
    )


    expected_target_time = (

        features.as_of

        + timedelta(
            minutes=15
        )
    )


    assert (
        long_sample.target_available_at
        == expected_target_time
    )


    print(
        "\nLabel contract: PASS"
    )


    # ==================================
    # ARTIFACT METADATA
    # ==================================

    metadata = (
        ModelArtifactMetadata(

            model_id=
                "test_rf_model",

            model_family=
                ModelFamily.RANDOM_FOREST,

            model_version=
                "1.0.0",

            symbol=
                "EURUSD",

            timeframe=
                "M5",

            feature_version=
                features.feature_version,

            feature_names=
                features.feature_names,

            label_specification=
                label_spec,

            trained_from=datetime(
                2025,
                1,
                1,
                tzinfo=timezone.utc
            ),

            trained_through=datetime(
                2025,
                12,
                1,
                tzinfo=timezone.utc
            ),

            created_at=datetime(
                2026,
                1,
                1,
                tzinfo=timezone.utc
            ),

            training_sample_count=
                10000,

            validation_sample_count=
                2000
        )
    )


    checker = (
        ModelCompatibilityChecker()
    )


    compatible = (
        checker.check(
            metadata,
            features
        )
    )


    assert (
        compatible.compatible
        is True
    )


    assert (
        compatible.reasons
        == tuple()
    )


    print(
        "Compatible artifact: PASS"
    )


    # ==================================
    # FEATURE VERSION MISMATCH
    # ==================================

    incompatible_features = (
        ModelFeatureVector(

            symbol=
                features.symbol,

            timeframe=
                features.timeframe,

            as_of=
                features.as_of,

            feature_version=
                "different_feature_v2",

            feature_names=
                features.feature_names,

            values=
                features.values
        )
    )


    mismatch = (
        checker.check(

            metadata,

            incompatible_features
        )
    )


    assert (
        mismatch.compatible
        is False
    )


    assert (
        "feature version mismatch"
        in mismatch.reasons
    )


    print(
        "Feature-version rejection: PASS"
    )


    # ==================================
    # FEATURE ORDER MISMATCH
    # ==================================

    reordered_features = (
        ModelFeatureVector(

            symbol=
                features.symbol,

            timeframe=
                features.timeframe,

            as_of=
                features.as_of,

            feature_version=
                features.feature_version,

            feature_names=(
                "feature_b",
                "feature_a",
                "feature_c"
            ),

            values=(
                2.0,
                1.0,
                3.0
            )
        )
    )


    order_mismatch = (
        checker.check(

            metadata,

            reordered_features
        )
    )


    assert (
        order_mismatch.compatible
        is False
    )


    assert (
        "ordered feature schema mismatch"
        in order_mismatch.reasons
    )


    print(
        "Feature-order rejection: PASS"
    )


    print(
        "\nFeature signature: "
        f"{metadata.feature_signature}"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "MODEL CONTRACT "
        "TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
    
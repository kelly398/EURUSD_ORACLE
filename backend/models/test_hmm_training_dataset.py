from collections import (
    Counter,
)

from datetime import (
    timedelta,
)

import numpy as np

from backend.models.hmm_training_dataset import (
    DATASET_VERSION,
    EXPECTED_SOURCE_COUNTS,
    HMMDevelopmentDatasetLoader,
    HMM_FEATURE_VERSION,
    LABEL_VERSION,
    SOURCE_FEATURE_VERSION,
)


def assert_split_contract(
    split
):

    assert (
        split.observation_count
        > 0
    )


    assert (
        split.feature_count
        == 8
    )


    assert (
        split.values.shape
        == (
            split.observation_count,
            8,
        )
    )


    assert (
        split.label_ids.shape
        == (
            split.observation_count,
        )
    )


    assert (
        split.target_move_pips.shape
        == (
            split.observation_count,
        )
    )


    assert (
        len(
            split.timestamps
        )
        == split.observation_count
    )


    assert (
        len(
            split.target_timestamps
        )
        == split.observation_count
    )


    assert (
        len(
            split.labels
        )
        == split.observation_count
    )


    assert (
        sum(
            split.segment_lengths
        )
        == split.observation_count
    )


    assert (
        split.source_row_count
        == (
            split.observation_count
            + split.dropped_unaligned_count
        )
    )


    assert np.isfinite(
        split.values
    ).all()


    assert np.isfinite(
        split.target_move_pips
    ).all()


    assert (
        split.values.flags.writeable
        is False
    )


    assert (
        split.label_ids.flags.writeable
        is False
    )


    assert (
        split.target_move_pips.flags.writeable
        is False
    )


    assert (
        set(
            split.labels
        )
        <= {
            "short",
            "neutral",
            "long",
        }
    )


    assert (
        set(
            split.label_ids.tolist()
        )
        <= {
            0,
            1,
            2,
        }
    )


    for (
        feature_timestamp,
        target_timestamp,
    ) in zip(
        split.timestamps,
        split.target_timestamps,
    ):

        assert (
            target_timestamp
            > feature_timestamp
        )


    offset = (
        0
    )


    for length in (
        split.segment_lengths
    ):

        assert (
            length
            > 0
        )


        segment_timestamps = (
            split.timestamps[
                offset:
                offset + length
            ]
        )


        for previous, current in zip(
            segment_timestamps,
            segment_timestamps[
                1:
            ],
        ):

            assert (
                current
                - previous
                == timedelta(
                    minutes=5
                )
            )


        offset += (
            length
        )


    assert (
        offset
        == split.observation_count
    )


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "HMM DEVELOPMENT DATASET TEST"
    )

    print(
        "=" * 70
    )


    loader = (
        HMMDevelopmentDatasetLoader(
            enforce_frozen_counts=True
        )
    )


    dataset = (
        loader.load()
    )


    print(
        f"\nDataset version: "
        f"{dataset.dataset_version}"
    )

    print(
        f"Source feature version: "
        f"{dataset.source_feature_version}"
    )

    print(
        f"HMM feature version: "
        f"{dataset.hmm_feature_version}"
    )

    print(
        f"Label version: "
        f"{dataset.label_version}"
    )


    print(
        "\nTRAIN"
    )

    print(
        f"Source rows: "
        f"{dataset.train.source_row_count:,}"
    )

    print(
        f"Aligned observations: "
        f"{dataset.train.observation_count:,}"
    )

    print(
        f"Dropped after HMM "
        f"continuity/warm-up rules: "
        f"{dataset.train.dropped_unaligned_count:,}"
    )

    print(
        f"Segments: "
        f"{len(dataset.train.segment_lengths):,}"
    )

    print(
        f"First: "
        f"{dataset.train.first_timestamp}"
    )

    print(
        f"Last: "
        f"{dataset.train.last_timestamp}"
    )

    print(
        "Labels: "
        f"{dict(Counter(dataset.train.labels))}"
    )


    print(
        "\nVALIDATION"
    )

    print(
        f"Source rows: "
        f"{dataset.validation.source_row_count:,}"
    )

    print(
        f"Aligned observations: "
        f"{dataset.validation.observation_count:,}"
    )

    print(
        f"Dropped after HMM "
        f"continuity/warm-up rules: "
        f"{dataset.validation.dropped_unaligned_count:,}"
    )

    print(
        f"Segments: "
        f"{len(dataset.validation.segment_lengths):,}"
    )

    print(
        f"First: "
        f"{dataset.validation.first_timestamp}"
    )

    print(
        f"Last: "
        f"{dataset.validation.last_timestamp}"
    )

    print(
        "Labels: "
        f"{dict(Counter(dataset.validation.labels))}"
    )


    # ==================================
    # FROZEN SOURCE CONTRACT
    # ==================================

    assert (
        dataset.dataset_version
        == DATASET_VERSION
    )


    assert (
        dataset.source_feature_version
        == SOURCE_FEATURE_VERSION
    )


    assert (
        dataset.hmm_feature_version
        == HMM_FEATURE_VERSION
    )


    assert (
        dataset.label_version
        == LABEL_VERSION
    )


    assert (
        dataset.train.source_row_count
        == EXPECTED_SOURCE_COUNTS[
            "train"
        ]
    )


    assert (
        dataset.validation.source_row_count
        == EXPECTED_SOURCE_COUNTS[
            "validation"
        ]
    )


    # ==================================
    # TEST SPLIT MUST REMAIN UNTOUCHED
    # ==================================

    assert (
        dataset.test_loaded
        is False
    )


    # ==================================
    # FEATURE CONTRACT
    # ==================================

    assert (
        dataset.feature_names
        == (
            "return_1_pips",
            "return_3_pips",
            "body_pips",
            "range_pips",
            "close_location",
            "realized_volatility_12_pips",
            "trend_slope_12_pips_per_bar",
            "log_tick_volume_ratio_12",
        )
    )


    assert_split_contract(
        dataset.train
    )


    assert_split_contract(
        dataset.validation
    )


    # ==================================
    # CHRONOLOGICAL ISOLATION
    # ==================================

    assert (
        dataset.train.last_timestamp
        < dataset.validation.first_timestamp
    )


    # No exact timestamp may appear in
    # both development splits.
    train_timestamp_set = set(
        dataset.train.timestamps
    )


    validation_timestamp_set = set(
        dataset.validation.timestamps
    )


    assert (
        train_timestamp_set
        .isdisjoint(
            validation_timestamp_set
        )
    )


    print(
        "\nFrozen source row counts: PASS"
    )

    print(
        "8-feature HMM alignment: PASS"
    )

    print(
        "Post-gap warm-up exclusion: PASS"
    )

    print(
        "Segment continuity contract: PASS"
    )

    print(
        "Target chronology: PASS"
    )

    print(
        "Train/validation isolation: PASS"
    )

    print(
        "Immutable array contract: PASS"
    )

    print(
        "Reserved test queried: NO"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "HMM DEVELOPMENT DATASET "
        "TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
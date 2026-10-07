import hashlib
import json
from datetime import timedelta
from pathlib import Path

import numpy as np

from sqlalchemy import select

from backend.database.connection import (
    SessionLocal,
)

from backend.market_state.candle_repository import (
    CandleRepository,
)

from backend.models.dataset_models import (
    MLTrainingSample,
)

from backend.models.hmm_core import (
    DiagonalGaussianHMMParameters,
    HMMFeatureStandardizer,
    causal_forward_filter,
)

from backend.models.hmm_observation_builder import (
    HMMObservationBuilder,
)

from backend.models.hmm_training_dataset import (
    DATASET_VERSION,
    HMM_FEATURE_VERSION,
    LABEL_TO_ID,
    LABEL_VERSION,
    SOURCE_FEATURE_VERSION,
    SYMBOL,
    TIMEFRAME,
)

from backend.models.train_hmm_experiment import (
    evaluate_directional_probabilities,
)


SELECTED_STATE_COUNT = 5
SELECTED_SEED = 11

PRE_TEST_SELECTION_COMMIT = (
    "c79ba5c"
)

EXPECTED_PARAMETER_SHA256 = (
    "4383DE12C975E8B656F3931F27ADE954"
    "148905AC69FE8E9448099E0B6012D26F"
)

EXPECTED_TEST_SOURCE_COUNT = (
    10817
)

SELECTED_DIRECTORY = (
    Path(
        "backend/models/experiments"
    )
    / "hmm_eurusd_m5_v1_k5_seed_11"
)

PARAMETER_PATH = (
    SELECTED_DIRECTORY
    / "candidate_parameters.npz"
)

METADATA_PATH = (
    SELECTED_DIRECTORY
    / "metadata.json"
)

TRAINING_METRICS_PATH = (
    SELECTED_DIRECTORY
    / "training_metrics.json"
)

VALIDATION_METRICS_PATH = (
    SELECTED_DIRECTORY
    / "validation_metrics.json"
)

TRAIN_DIAGNOSTICS_PATH = (
    SELECTED_DIRECTORY
    / "state_diagnostics.json"
)

SELECTION_PATH = (
    Path(
        "backend/models/experiments"
    )
    / "hmm_eurusd_m5_v1_selection.json"
)

OUTPUT_PATH = (
    SELECTED_DIRECTORY
    / "final_test_evaluation.json"
)

STATE_NAMES = (
    "high_volatility_turbulence",
    "mild_bearish_impulse",
    "mild_bullish_impulse",
    "strong_bearish_expansion",
    "strong_bullish_expansion",
)


def read_json(
    path,
):
    with open(
        path,
        "r",
        encoding="utf-8",
    ) as handle:
        return json.load(
            handle
        )


def write_json(
    path,
    payload,
):
    with open(
        path,
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            payload,
            handle,
            indent=2,
            sort_keys=True,
        )

        handle.write(
            "\n"
        )


def sha256_file(
    path,
):
    digest = (
        hashlib.sha256()
    )

    with open(
        path,
        "rb",
    ) as handle:

        while True:
            chunk = handle.read(
                1024 * 1024
            )

            if not chunk:
                break

            digest.update(
                chunk
            )

    return (
        digest
        .hexdigest()
        .upper()
    )


def validate_frozen_selection():
    if OUTPUT_PATH.exists():
        raise FileExistsError(
            "Reserved HMM test evaluation "
            "already exists. Refusing to "
            "evaluate the test split again."
        )

    selection = read_json(
        SELECTION_PATH
    )

    metadata = read_json(
        METADATA_PATH
    )

    if (
        selection[
            "selection_status"
        ]
        != "FROZEN_BEFORE_TEST"
    ):
        raise RuntimeError(
            "HMM selection was not frozen "
            "before test evaluation."
        )

    if (
        selection[
            "selected_state_count"
        ]
        != SELECTED_STATE_COUNT
    ):
        raise RuntimeError(
            "Frozen selected state count "
            "has changed."
        )

    if (
        selection[
            "selected_seed"
        ]
        != SELECTED_SEED
    ):
        raise RuntimeError(
            "Frozen selected seed "
            "has changed."
        )

    if (
        selection[
            "test_evaluated"
        ]
        is not False
    ):
        raise RuntimeError(
            "Frozen selection metadata "
            "already reports test access."
        )

    if (
        metadata[
            "state_count"
        ]
        != SELECTED_STATE_COUNT
    ):
        raise RuntimeError(
            "Candidate state-count mismatch."
        )

    if (
        metadata[
            "seed"
        ]
        != SELECTED_SEED
    ):
        raise RuntimeError(
            "Candidate seed mismatch."
        )

    if (
        metadata[
            "reserved_test_loaded"
        ]
        is not False
    ):
        raise RuntimeError(
            "Candidate metadata already "
            "reports test access."
        )

    actual_hash = (
        sha256_file(
            PARAMETER_PATH
        )
    )

    if (
        actual_hash
        != EXPECTED_PARAMETER_SHA256
    ):
        raise RuntimeError(
            "Selected candidate parameter "
            "SHA-256 mismatch.\n"
            f"Expected: "
            f"{EXPECTED_PARAMETER_SHA256}\n"
            f"Actual:   "
            f"{actual_hash}"
        )

    return (
        selection,
        metadata,
        actual_hash,
    )


def load_test_rows():
    session = (
        SessionLocal()
    )

    try:
        statement = (
            select(
                MLTrainingSample
            )
            .where(
                MLTrainingSample.symbol
                == SYMBOL,

                MLTrainingSample.timeframe
                == TIMEFRAME,

                MLTrainingSample.feature_version
                == SOURCE_FEATURE_VERSION,

                MLTrainingSample.label_version
                == LABEL_VERSION,

                MLTrainingSample.split
                == "test",
            )
            .order_by(
                MLTrainingSample
                .feature_timestamp
                .asc()
            )
        )

        rows = (
            session.execute(
                statement
            )
            .scalars()
            .all()
        )

        return tuple(
            rows
        )

    finally:
        session.close()


def validate_test_rows(
    rows,
):
    if (
        len(rows)
        != EXPECTED_TEST_SOURCE_COUNT
    ):
        raise RuntimeError(
            "Frozen test source row-count "
            "drift. "
            f"Expected "
            f"{EXPECTED_TEST_SOURCE_COUNT}, "
            f"received {len(rows)}."
        )

    seen = set()

    previous = None

    for row in rows:
        if (
            row.split
            != "test"
        ):
            raise RuntimeError(
                "Non-test row entered "
                "reserved test evaluation."
            )

        if (
            row.feature_timestamp
            in seen
        ):
            raise RuntimeError(
                "Duplicate test feature "
                "timestamp."
            )

        seen.add(
            row.feature_timestamp
        )

        if (
            previous
            is not None

            and

            row.feature_timestamp
            <= previous
        ):
            raise RuntimeError(
                "Test rows are not strictly "
                "chronological."
            )

        if (
            row.target_timestamp
            <= row.feature_timestamp
        ):
            raise RuntimeError(
                "Test target timestamp is "
                "not after feature timestamp."
            )

        if (
            row.target_label
            not in LABEL_TO_ID
        ):
            raise RuntimeError(
                "Unexpected test label: "
                f"{row.target_label}"
            )

        previous = (
            row.feature_timestamp
        )


def load_test_candles(
    rows,
):
    first_timestamp = (
        rows[
            0
        ].feature_timestamp
    )

    last_timestamp = (
        rows[
            -1
        ].feature_timestamp
    )

    # Warm-up is only for construction of
    # causal candle observations.
    #
    # The latent filter itself will reset
    # at the beginning of the reserved
    # test split.
    start = (
        first_timestamp
        - timedelta(
            days=2
        )
    )

    end = (
        last_timestamp
        + timedelta(
            minutes=1
        )
    )

    repository = (
        CandleRepository(
            source="MT5"
        )
    )

    candles = (
        repository.get_between(
            start=
                start,

            end=
                end,

            symbol=
                SYMBOL,

            timeframe=
                TIMEFRAME,
        )
    )

    if not candles:
        raise RuntimeError(
            "No candles available for "
            "reserved HMM test evaluation."
        )

    return tuple(
        candles
    )


def build_observation_lookup(
    sequence,
):
    lookup = {}

    offset = 0

    for segment_id, length in enumerate(
        sequence.segment_lengths
    ):
        for local_index in range(
            length
        ):
            absolute_index = (
                offset
                + local_index
            )

            timestamp = (
                sequence
                .as_of_timestamps[
                    absolute_index
                ]
            )

            if (
                timestamp
                in lookup
            ):
                raise RuntimeError(
                    "Duplicate HMM observation "
                    "timestamp."
                )

            lookup[
                timestamp
            ] = (
                sequence.values[
                    absolute_index
                ],
                segment_id,
            )

        offset += (
            length
        )

    if (
        offset
        != sequence.observation_count
    ):
        raise RuntimeError(
            "Observation segment mapping "
            "is incomplete."
        )

    return lookup


def build_segment_lengths(
    timestamps,
    source_segment_ids,
):
    if not timestamps:
        return tuple()

    expected_delta = (
        timedelta(
            minutes=5
        )
    )

    lengths = []

    current_length = (
        1
    )

    for index in range(
        1,
        len(
            timestamps
        ),
    ):
        same_source_segment = (
            source_segment_ids[
                index
            ]
            == source_segment_ids[
                index - 1
            ]
        )

        contiguous = (
            timestamps[
                index
            ]
            - timestamps[
                index - 1
            ]
            == expected_delta
        )

        if (
            same_source_segment
            and
            contiguous
        ):
            current_length += (
                1
            )

        else:
            lengths.append(
                current_length
            )

            current_length = (
                1
            )

    lengths.append(
        current_length
    )

    return tuple(
        lengths
    )


def align_test_rows(
    rows,
    observation_lookup,
):
    timestamps = []
    target_timestamps = []
    labels = []
    label_ids = []
    values = []
    source_segment_ids = []

    dropped = (
        0
    )

    for row in rows:
        observation = (
            observation_lookup.get(
                row.feature_timestamp
            )
        )

        if (
            observation
            is None
        ):
            dropped += (
                1
            )

            continue

        (
            observation_values,
            source_segment_id,
        ) = observation

        timestamps.append(
            row.feature_timestamp
        )

        target_timestamps.append(
            row.target_timestamp
        )

        labels.append(
            row.target_label
        )

        label_ids.append(
            LABEL_TO_ID[
                row.target_label
            ]
        )

        values.append(
            observation_values
        )

        source_segment_ids.append(
            source_segment_id
        )

    if not values:
        raise RuntimeError(
            "No reserved test rows aligned "
            "to valid HMM observations."
        )

    matrix = np.asarray(
        values,
        dtype=np.float64,
    )

    label_array = np.asarray(
        label_ids,
        dtype=np.int64,
    )

    if not np.isfinite(
        matrix
    ).all():
        raise RuntimeError(
            "Reserved test HMM matrix "
            "contains NaN or infinity."
        )

    segment_lengths = (
        build_segment_lengths(
            timestamps=
                timestamps,

            source_segment_ids=
                source_segment_ids,
        )
    )

    if (
        sum(
            segment_lengths
        )
        != matrix.shape[
            0
        ]
    ):
        raise RuntimeError(
            "Reserved test HMM segments "
            "do not cover all observations."
        )

    return {
        "timestamps":
            tuple(
                timestamps
            ),

        "target_timestamps":
            tuple(
                target_timestamps
            ),

        "labels":
            tuple(
                labels
            ),

        "label_ids":
            label_array,

        "values":
            matrix,

        "segment_lengths":
            segment_lengths,

        "dropped":
            dropped,
    }


def posterior_entropy_metrics(
    probabilities,
):
    probabilities = np.asarray(
        probabilities,
        dtype=np.float64,
    )

    clipped = np.clip(
        probabilities,
        1e-15,
        1.0,
    )

    entropy = -np.sum(
        clipped
        * np.log(
            clipped
        ),
        axis=1,
    )

    normalized_entropy = (
        entropy
        / np.log(
            probabilities.shape[
                1
            ]
        )
    )

    confidence = (
        1.0
        - normalized_entropy
    )

    dominant_probability = (
        np.max(
            probabilities,
            axis=1,
        )
    )

    return {
        "mean_normalized_entropy":
            float(
                np.mean(
                    normalized_entropy
                )
            ),

        "mean_context_confidence":
            float(
                np.mean(
                    confidence
                )
            ),

        "median_context_confidence":
            float(
                np.median(
                    confidence
                )
            ),

        "mean_dominant_state_probability":
            float(
                np.mean(
                    dominant_probability
                )
            ),

        "median_dominant_state_probability":
            float(
                np.median(
                    dominant_probability
                )
            ),
    }


def main():
    print(
        "=" * 78
    )

    print(
        "EUR/USD ORACLE "
        "HMM RESERVED TEST EVALUATION"
    )

    print(
        "=" * 78
    )

    print(
        "WARNING: THIS COMMAND OPENS "
        "THE RESERVED TEST SPLIT."
    )

    print(
        "Evaluation is guarded against "
        "a second execution."
    )

    (
        selection,
        metadata,
        actual_parameter_hash,
    ) = validate_frozen_selection()

    training_metrics = (
        read_json(
            TRAINING_METRICS_PATH
        )
    )

    validation_metrics = (
        read_json(
            VALIDATION_METRICS_PATH
        )
    )

    train_diagnostics = (
        read_json(
            TRAIN_DIAGNOSTICS_PATH
        )
    )

    print(
        "\nFrozen selection verified."
    )

    print(
        f"Pre-test commit: "
        f"{PRE_TEST_SELECTION_COMMIT}"
    )

    print(
        f"Selected K: "
        f"{SELECTED_STATE_COUNT}"
    )

    print(
        f"Selected seed: "
        f"{SELECTED_SEED}"
    )

    print(
        "Parameter SHA-256: "
        f"{actual_parameter_hash}"
    )

    print(
        "\nOpening reserved test split..."
    )

    rows = (
        load_test_rows()
    )

    validate_test_rows(
        rows
    )

    print(
        f"Frozen source rows: "
        f"{len(rows):,}"
    )

    candles = (
        load_test_candles(
            rows
        )
    )

    observation_builder = (
        HMMObservationBuilder(
            symbol=
                SYMBOL,

            timeframe=
                TIMEFRAME,

            timeframe_minutes=
                5,
        )
    )

    sequence = (
        observation_builder.build(
            candles
        )
    )

    if (
        sequence.feature_version
        != HMM_FEATURE_VERSION
    ):
        raise RuntimeError(
            "Reserved test HMM feature "
            "version mismatch."
        )

    if (
        tuple(
            sequence.feature_names
        )
        != tuple(
            metadata[
                "feature_names"
            ]
        )
    ):
        raise RuntimeError(
            "Reserved test HMM feature "
            "contract differs from "
            "selected candidate."
        )

    observation_lookup = (
        build_observation_lookup(
            sequence
        )
    )

    test = (
        align_test_rows(
            rows=
                rows,

            observation_lookup=
                observation_lookup,
        )
    )

    test_observation_count = (
        test[
            "values"
        ].shape[
            0
        ]
    )

    print(
        f"Aligned observations: "
        f"{test_observation_count:,}"
    )

    print(
        "Dropped after HMM "
        "continuity/warm-up rules: "
        f"{test['dropped']:,}"
    )

    print(
        f"Test sequences: "
        f"{len(test['segment_lengths']):,}"
    )

    print(
        f"First test as-of: "
        f"{test['timestamps'][0]}"
    )

    print(
        f"Last test as-of: "
        f"{test['timestamps'][-1]}"
    )

    with np.load(
        PARAMETER_PATH,
        allow_pickle=False,
    ) as artifact:

        start_probability = np.asarray(
            artifact[
                "start_probability"
            ],
            dtype=np.float64,
        )

        transition_matrix = np.asarray(
            artifact[
                "transition_matrix"
            ],
            dtype=np.float64,
        )

        means = np.asarray(
            artifact[
                "means"
            ],
            dtype=np.float64,
        )

        variances = np.asarray(
            artifact[
                "variances"
            ],
            dtype=np.float64,
        )

        standardizer_mean = np.asarray(
            artifact[
                "standardizer_mean"
            ],
            dtype=np.float64,
        )

        standardizer_scale = np.asarray(
            artifact[
                "standardizer_scale"
            ],
            dtype=np.float64,
        )

        state_outcome_probability = (
            np.asarray(
                artifact[
                    "state_outcome_probability"
                ],
                dtype=np.float64,
            )
        )

    feature_names = tuple(
        metadata[
            "feature_names"
        ]
    )

    parameters = (
        DiagonalGaussianHMMParameters(
            feature_names=
                feature_names,

            start_probability=
                start_probability,

            transition_matrix=
                transition_matrix,

            means=
                means,

            variances=
                variances,
        )
    )

    standardizer = (
        HMMFeatureStandardizer(
            feature_names=
                feature_names,

            mean=
                standardizer_mean,

            scale=
                standardizer_scale,
        )
    )

    test_scaled = (
        standardizer.transform(
            values=
                test[
                    "values"
                ],

            feature_names=
                feature_names,
        )
    )

    filtered = (
        causal_forward_filter(
            values=
                test_scaled,

            parameters=
                parameters,

            segment_lengths=
                test[
                    "segment_lengths"
                ],
        )
    )

    test_ll = (
        filtered.log_likelihood
    )

    test_ll_per_observation = (
        test_ll
        / test_observation_count
    )

    posterior = (
        filtered
        .filtered_state_probabilities
    )

    test_occupancy = (
        posterior.mean(
            axis=0
        )
    )

    train_occupancy = (
        np.asarray(
            train_diagnostics[
                "occupancy"
            ],
            dtype=np.float64,
        )
    )

    occupancy_difference = (
        test_occupancy
        - train_occupancy
    )

    occupancy_mae = float(
        np.mean(
            np.abs(
                occupancy_difference
            )
        )
    )

    occupancy_max_abs = float(
        np.max(
            np.abs(
                occupancy_difference
            )
        )
    )

    dominant_state = np.argmax(
        posterior,
        axis=1,
    )

    dominant_state_counts = (
        np.bincount(
            dominant_state,
            minlength=
                SELECTED_STATE_COUNT,
        )
    )

    entropy_metrics = (
        posterior_entropy_metrics(
            posterior
        )
    )

    class_probability = (
        posterior
        @ state_outcome_probability
    )

    class_probability = np.clip(
        class_probability,
        1e-12,
        1.0,
    )

    class_probability = (
        class_probability
        / class_probability.sum(
            axis=1,
            keepdims=True,
        )
    )

    directional_metrics = (
        evaluate_directional_probabilities(
            labels=
                test[
                    "label_ids"
                ],

            probabilities=
                class_probability,
        )
    )

    train_ll_per_observation = (
        float(
            training_metrics[
                "log_likelihood_per_observation"
            ]
        )
    )

    validation_ll_per_observation = (
        float(
            validation_metrics[
                "log_likelihood_per_observation"
            ]
        )
    )

    print(
        "\nPRIMARY REGIME MODEL METRICS"
    )

    print(
        "Train log-likelihood / obs: "
        f"{train_ll_per_observation:.6f}"
    )

    print(
        "Validation log-likelihood / obs: "
        f"{validation_ll_per_observation:.6f}"
    )

    print(
        "Test log-likelihood / obs: "
        f"{test_ll_per_observation:.6f}"
    )

    print(
        "\nTEST REGIME OCCUPANCY"
    )

    for state_index in range(
        SELECTED_STATE_COUNT
    ):
        print(
            f"State {state_index} "
            f"({STATE_NAMES[state_index]}): "
            f"train="
            f"{train_occupancy[state_index]:.4f} "
            f"test="
            f"{test_occupancy[state_index]:.4f} "
            f"delta="
            f"{occupancy_difference[state_index]:+.4f} "
            f"dominant_count="
            f"{dominant_state_counts[state_index]:,}"
        )

    print(
        f"Occupancy MAE: "
        f"{occupancy_mae:.6f}"
    )

    print(
        f"Occupancy max abs delta: "
        f"{occupancy_max_abs:.6f}"
    )

    print(
        "\nPOSTERIOR CERTAINTY"
    )

    print(
        "Mean context confidence: "
        f"{entropy_metrics['mean_context_confidence']:.6f}"
    )

    print(
        "Median context confidence: "
        f"{entropy_metrics['median_context_confidence']:.6f}"
    )

    print(
        "Mean dominant-state probability: "
        f"{entropy_metrics['mean_dominant_state_probability']:.6f}"
    )

    print(
        "\nDIRECTIONAL DIAGNOSTICS "
        "- SECONDARY"
    )

    print(
        f"Accuracy: "
        f"{directional_metrics['accuracy']:.6f}"
    )

    print(
        f"Macro F1: "
        f"{directional_metrics['macro_f1']:.6f}"
    )

    print(
        f"Log loss: "
        f"{directional_metrics['log_loss']:.6f}"
    )

    print(
        f"Brier: "
        f"{directional_metrics['brier_score']:.6f}"
    )

    print(
        f"ECE: "
        f"{directional_metrics['ece']:.6f}"
    )

    artifact_payload = {
        "model_name":
            "hidden_markov_eurusd_m5_v1",

        "evaluation_type":
            "ONE_TIME_RESERVED_TEST",

        "pre_test_selection_commit":
            PRE_TEST_SELECTION_COMMIT,

        "parameter_sha256":
            actual_parameter_hash,

        "dataset_version":
            DATASET_VERSION,

        "source_feature_version":
            SOURCE_FEATURE_VERSION,

        "hmm_feature_version":
            HMM_FEATURE_VERSION,

        "label_version":
            LABEL_VERSION,

        "selected_state_count":
            SELECTED_STATE_COUNT,

        "selected_seed":
            SELECTED_SEED,

        "state_names":
            list(
                STATE_NAMES
            ),

        "source_test_rows":
            len(
                rows
            ),

        "aligned_test_observations":
            test_observation_count,

        "dropped_unaligned_test_rows":
            test[
                "dropped"
            ],

        "test_sequence_count":
            len(
                test[
                    "segment_lengths"
                ]
            ),

        "first_test_timestamp":
            test[
                "timestamps"
            ][
                0
            ].isoformat(),

        "last_test_timestamp":
            test[
                "timestamps"
            ][
                -1
            ].isoformat(),

        "log_likelihood": {
            "train_per_observation":
                train_ll_per_observation,

            "validation_per_observation":
                validation_ll_per_observation,

            "test":
                float(
                    test_ll
                ),

            "test_per_observation":
                float(
                    test_ll_per_observation
                ),
        },

        "regime_occupancy": {
            "train":
                train_occupancy.tolist(),

            "test":
                test_occupancy.tolist(),

            "test_minus_train":
                occupancy_difference.tolist(),

            "mae":
                occupancy_mae,

            "maximum_absolute_delta":
                occupancy_max_abs,

            "dominant_state_counts":
                dominant_state_counts.tolist(),
        },

        "posterior_certainty":
            entropy_metrics,

        "directional_diagnostics_secondary":
            directional_metrics,

        "agent_policy":
            selection[
                "agent_policy"
            ],

        "refit_performed":
            False,

        "calibration_performed":
            False,

        "selection_changed_after_test":
            False,

        "test_evaluated":
            True,
    }

    write_json(
        OUTPUT_PATH,
        artifact_payload,
    )

    print(
        "\nRefit performed: NO"
    )

    print(
        "Calibration performed: NO"
    )

    print(
        "Model selection changed: NO"
    )

    print(
        "Reserved test evaluated: YES"
    )

    print(
        f"Final test artifact: "
        f"{OUTPUT_PATH}"
    )

    print(
        "\n"
        + "=" * 78
    )

    print(
        "HMM RESERVED TEST "
        "EVALUATION: COMPLETE"
    )

    print(
        "=" * 78
    )


if __name__ == "__main__":
    main()
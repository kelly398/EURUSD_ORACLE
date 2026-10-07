import argparse
import json
import math
import time
import warnings

from pathlib import Path

import numpy as np

from hmmlearn.hmm import GaussianHMM

from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    log_loss,
)

from backend.models.hmm_core import (
    DiagonalGaussianHMMParameters,
    HMMFeatureStandardizer,
    causal_forward_filter,
)

from backend.models.hmm_training_dataset import (
    HMMDevelopmentDatasetLoader,
)


MODEL_NAME = "hidden_markov_eurusd_m5_v1"

ALLOWED_STATE_COUNTS = (
    3,
    4,
    5,
)

ALLOWED_SEEDS = (
    11,
    29,
    47,
)

CLASS_NAMES = (
    "short",
    "neutral",
    "long",
)

CLASS_IDS = np.asarray(
    [0, 1, 2],
    dtype=np.int64,
)

DIRICHLET_ALPHA = 1.0

MAX_ITERATIONS = 300
TOLERANCE = 1e-4
MIN_COVARIANCE = 1e-4

COLLAPSED_OCCUPANCY_THRESHOLD = 0.01
NEAR_ABSORBING_THRESHOLD = 0.995

ECE_BIN_COUNT = 15


def json_safe(value):
    if isinstance(
        value,
        np.ndarray,
    ):
        return value.tolist()

    if isinstance(
        value,
        np.integer,
    ):
        return int(value)

    if isinstance(
        value,
        np.floating,
    ):
        return float(value)

    if isinstance(
        value,
        Path,
    ):
        return str(value)

    raise TypeError(
        f"Unsupported JSON type: {type(value)}"
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
            default=json_safe,
        )

        handle.write(
            "\n"
        )


def extract_diagonal_covariances(
    model,
):
    covariances = np.asarray(
        model.covars_,
        dtype=np.float64,
    )

    if (
        covariances.ndim
        == 2
    ):
        return covariances.copy()

    if (
        covariances.ndim
        == 3
    ):
        return np.diagonal(
            covariances,
            axis1=1,
            axis2=2,
        ).copy()

    raise RuntimeError(
        "Unexpected hmmlearn covariance "
        f"shape: {covariances.shape}"
    )


def multiclass_brier_score(
    labels,
    probabilities,
):
    labels = np.asarray(
        labels,
        dtype=np.int64,
    )

    probabilities = np.asarray(
        probabilities,
        dtype=np.float64,
    )

    one_hot = np.eye(
        3,
        dtype=np.float64,
    )[labels]

    return float(
        np.mean(
            np.sum(
                (
                    probabilities
                    - one_hot
                )
                ** 2,
                axis=1,
            )
        )
    )


def expected_calibration_error(
    labels,
    probabilities,
    bin_count=ECE_BIN_COUNT,
):
    labels = np.asarray(
        labels,
        dtype=np.int64,
    )

    probabilities = np.asarray(
        probabilities,
        dtype=np.float64,
    )

    predicted = np.argmax(
        probabilities,
        axis=1,
    )

    confidence = np.max(
        probabilities,
        axis=1,
    )

    correct = (
        predicted
        == labels
    ).astype(
        np.float64
    )

    boundaries = np.linspace(
        0.0,
        1.0,
        bin_count + 1,
    )

    total = labels.shape[0]

    ece = 0.0

    for index in range(
        bin_count
    ):
        lower = boundaries[
            index
        ]

        upper = boundaries[
            index + 1
        ]

        if (
            index
            == bin_count - 1
        ):
            mask = (
                (confidence >= lower)
                & (confidence <= upper)
            )

        else:
            mask = (
                (confidence >= lower)
                & (confidence < upper)
            )

        count = int(
            mask.sum()
        )

        if (
            count
            == 0
        ):
            continue

        bin_accuracy = float(
            correct[
                mask
            ].mean()
        )

        bin_confidence = float(
            confidence[
                mask
            ].mean()
        )

        ece += (
            count
            / total
        ) * abs(
            bin_accuracy
            - bin_confidence
        )

    return float(
        ece
    )


def evaluate_directional_probabilities(
    labels,
    probabilities,
):
    labels = np.asarray(
        labels,
        dtype=np.int64,
    )

    probabilities = np.asarray(
        probabilities,
        dtype=np.float64,
    )

    if (
        probabilities.shape
        != (
            labels.shape[0],
            3,
        )
    ):
        raise ValueError(
            "Directional probability "
            "matrix shape is invalid."
        )

    if not np.isfinite(
        probabilities
    ).all():
        raise ValueError(
            "Directional probabilities "
            "contain NaN or infinity."
        )

    if not np.allclose(
        probabilities.sum(
            axis=1
        ),
        1.0,
        atol=1e-10,
        rtol=0.0,
    ):
        raise ValueError(
            "Directional probabilities "
            "do not sum to one."
        )

    predicted = np.argmax(
        probabilities,
        axis=1,
    )

    matrix = confusion_matrix(
        labels,
        predicted,
        labels=CLASS_IDS,
    )

    return {
        "accuracy":
            float(
                accuracy_score(
                    labels,
                    predicted,
                )
            ),

        "macro_f1":
            float(
                f1_score(
                    labels,
                    predicted,
                    labels=CLASS_IDS,
                    average="macro",
                    zero_division=0,
                )
            ),

        "log_loss":
            float(
                log_loss(
                    labels,
                    probabilities,
                    labels=CLASS_IDS,
                )
            ),

        "brier_score":
            multiclass_brier_score(
                labels,
                probabilities,
            ),

        "ece":
            expected_calibration_error(
                labels,
                probabilities,
            ),

        "confusion_matrix":
            matrix.tolist(),
    }


def build_state_outcome_probabilities(
    filtered_state_probability,
    labels,
    alpha=DIRICHLET_ALPHA,
):
    filtered = np.asarray(
        filtered_state_probability,
        dtype=np.float64,
    )

    labels = np.asarray(
        labels,
        dtype=np.int64,
    )

    if (
        filtered.shape[0]
        != labels.shape[0]
    ):
        raise ValueError(
            "State posterior and label "
            "counts do not align."
        )

    state_count = (
        filtered.shape[1]
    )

    one_hot = np.eye(
        3,
        dtype=np.float64,
    )[labels]

    weighted_counts = (
        filtered.T
        @ one_hot
    )

    smoothed_counts = (
        weighted_counts
        + alpha
    )

    probabilities = (
        smoothed_counts
        / smoothed_counts.sum(
            axis=1,
            keepdims=True,
        )
    )

    return (
        weighted_counts,
        probabilities,
    )


def hmm_parameter_count(
    state_count,
    feature_count,
):
    start_free = (
        state_count
        - 1
    )

    transition_free = (
        state_count
        * (
            state_count
            - 1
        )
    )

    emission_means = (
        state_count
        * feature_count
    )

    emission_variances = (
        state_count
        * feature_count
    )

    return int(
        start_free
        + transition_free
        + emission_means
        + emission_variances
    )


def information_criteria(
    log_likelihood,
    observation_count,
    parameter_count,
):
    aic = (
        2.0
        * parameter_count
        - 2.0
        * log_likelihood
    )

    bic = (
        math.log(
            observation_count
        )
        * parameter_count
        - 2.0
        * log_likelihood
    )

    return (
        float(aic),
        float(bic),
    )


def transition_entropy(
    transition_matrix,
):
    matrix = np.asarray(
        transition_matrix,
        dtype=np.float64,
    )

    state_count = (
        matrix.shape[0]
    )

    entropies = []

    normalized = []

    for row in matrix:
        positive = row[
            row > 0.0
        ]

        entropy = float(
            -np.sum(
                positive
                * np.log(
                    positive
                )
            )
        )

        entropies.append(
            entropy
        )

        if (
            state_count
            > 1
        ):
            normalized.append(
                float(
                    entropy
                    / math.log(
                        state_count
                    )
                )
            )

        else:
            normalized.append(
                0.0
            )

    return (
        entropies,
        normalized,
    )


def state_diagnostics(
    parameters,
    filtered_train,
    standardizer,
):
    probabilities = (
        filtered_train
        .filtered_state_probabilities
    )

    occupancy = (
        probabilities.sum(
            axis=0
        )
        / probabilities.shape[
            0
        ]
    )

    persistence = np.diag(
        parameters.transition_matrix
    )

    expected_duration = []

    for probability in (
        persistence
    ):
        if (
            probability
            >= 1.0
        ):
            duration = (
                math.inf
            )

        else:
            duration = (
                1.0
                / (
                    1.0
                    - probability
                )
            )

        expected_duration.append(
            float(duration)
        )

    entropy, normalized_entropy = (
        transition_entropy(
            parameters.transition_matrix
        )
    )

    raw_emission_means = (
        parameters.means
        * standardizer.scale[
            None,
            :
        ]
        + standardizer.mean[
            None,
            :
        ]
    )

    collapsed_states = [
        int(index)

        for index, value
        in enumerate(
            occupancy
        )

        if (
            value
            < COLLAPSED_OCCUPANCY_THRESHOLD
        )
    ]

    near_absorbing_states = [
        int(index)

        for index, value
        in enumerate(
            persistence
        )

        if (
            value
            >= NEAR_ABSORBING_THRESHOLD
        )
    ]

    return {
        "occupancy":
            occupancy.tolist(),

        "persistence":
            persistence.tolist(),

        "expected_duration_bars":
            expected_duration,

        "transition_entropy":
            entropy,

        "transition_entropy_normalized":
            normalized_entropy,

        "mean_normalized_transition_entropy":
            float(
                np.mean(
                    normalized_entropy
                )
            ),

        "collapsed_states":
            collapsed_states,

        "near_absorbing_states":
            near_absorbing_states,

        "minimum_variance":
            float(
                np.min(
                    parameters.variances
                )
            ),

        "maximum_variance":
            float(
                np.max(
                    parameters.variances
                )
            ),

        "standardized_emission_means":
            parameters.means.tolist(),

        "raw_emission_means":
            raw_emission_means.tolist(),
    }


def state_outcome_payload(
    weighted_counts,
    probabilities,
):
    rows = []

    for state_index in range(
        probabilities.shape[
            0
        ]
    ):
        rows.append(
            {
                "state":
                    int(
                        state_index
                    ),

                "effective_label_counts":
                    {
                        "short":
                            float(
                                weighted_counts[
                                    state_index,
                                    0
                                ]
                            ),

                        "neutral":
                            float(
                                weighted_counts[
                                    state_index,
                                    1
                                ]
                            ),

                        "long":
                            float(
                                weighted_counts[
                                    state_index,
                                    2
                                ]
                            ),
                    },

                "probabilities":
                    {
                        "short":
                            float(
                                probabilities[
                                    state_index,
                                    0
                                ]
                            ),

                        "neutral":
                            float(
                                probabilities[
                                    state_index,
                                    1
                                ]
                            ),

                        "long":
                            float(
                                probabilities[
                                    state_index,
                                    2
                                ]
                            ),
                    },
            }
        )

    return rows


def run_experiment(
    state_count,
    seed,
):
    if (
        state_count
        not in ALLOWED_STATE_COUNTS
    ):
        raise ValueError(
            f"state_count must be one of "
            f"{ALLOWED_STATE_COUNTS}."
        )

    if (
        seed
        not in ALLOWED_SEEDS
    ):
        raise ValueError(
            f"seed must be one of "
            f"{ALLOWED_SEEDS}."
        )

    output_directory = (
        Path(
            "backend/models/experiments"
        )
        / (
            f"hmm_eurusd_m5_v1_"
            f"k{state_count}_seed_{seed}"
        )
    )

    if (
        output_directory.exists()
    ):
        raise FileExistsError(
            "Experiment directory already "
            f"exists: {output_directory}"
        )

    output_directory.mkdir(
        parents=True,
        exist_ok=False,
    )

    print(
        "=" * 78
    )

    print(
        "EUR/USD ORACLE "
        "HIDDEN MARKOV MODEL EXPERIMENT"
    )

    print(
        "=" * 78
    )

    print(
        f"Model: {MODEL_NAME}"
    )

    print(
        f"States: {state_count}"
    )

    print(
        f"Seed: {seed}"
    )

    print(
        "Test split: SEALED"
    )

    print(
        "\nLoading frozen development "
        "dataset..."
    )

    dataset = (
        HMMDevelopmentDatasetLoader(
            enforce_frozen_counts=True
        )
        .load()
    )

    if (
        dataset.test_loaded
        is not False
    ):
        raise RuntimeError(
            "Reserved test split was loaded."
        )

    train = (
        dataset.train
    )

    validation = (
        dataset.validation
    )

    print(
        f"Train observations: "
        f"{train.observation_count:,}"
    )

    print(
        f"Train sequences: "
        f"{len(train.segment_lengths):,}"
    )

    print(
        f"Validation observations: "
        f"{validation.observation_count:,}"
    )

    print(
        f"Validation sequences: "
        f"{len(validation.segment_lengths):,}"
    )

    print(
        "\nFitting train-only "
        "standardizer..."
    )

    standardizer = (
        HMMFeatureStandardizer.fit(
            values=
                train.values,

            feature_names=
                dataset.feature_names,
        )
    )

    train_scaled = (
        standardizer.transform(
            values=
                train.values,

            feature_names=
                dataset.feature_names,
        )
    )

    validation_scaled = (
        standardizer.transform(
            values=
                validation.values,

            feature_names=
                dataset.feature_names,
        )
    )

    model = GaussianHMM(
        n_components=
            state_count,

        covariance_type=
            "diag",

        n_iter=
            MAX_ITERATIONS,

        tol=
            TOLERANCE,

        min_covar=
            MIN_COVARIANCE,

        random_state=
            seed,

        implementation=
            "log",
    )

    print(
        "\nBeginning unsupervised "
        "EM training..."
    )

    started = (
        time.perf_counter()
    )

    with warnings.catch_warnings(
        record=True
    ) as captured_warnings:
        warnings.simplefilter(
            "always"
        )

        model.fit(
            train_scaled,
            lengths=
                train.segment_lengths,
        )

    fit_seconds = (
        time.perf_counter()
        - started
    )

    converged = bool(
        model.monitor_.converged
    )

    iterations = int(
        model.monitor_.iter
    )

    history = [
        float(value)

        for value
        in model.monitor_.history
    ]

    print(
        f"Converged: {converged}"
    )

    print(
        f"Iterations: {iterations}"
    )

    print(
        f"Fit time: "
        f"{fit_seconds:.2f} sec"
    )

    if not converged:
        raise RuntimeError(
            "HMM failed to converge."
        )

    variances = (
        extract_diagonal_covariances(
            model
        )
    )

    parameters = (
        DiagonalGaussianHMMParameters(
            feature_names=
                dataset.feature_names,

            start_probability=
                model.startprob_,

            transition_matrix=
                model.transmat_,

            means=
                model.means_,

            variances=
                variances,
        )
    )

    print(
        "\nRunning causal filtering..."
    )

    train_filtered = (
        causal_forward_filter(
            values=
                train_scaled,

            parameters=
                parameters,

            segment_lengths=
                train.segment_lengths,
        )
    )

    validation_filtered = (
        causal_forward_filter(
            values=
                validation_scaled,

            parameters=
                parameters,

            segment_lengths=
                validation.segment_lengths,
        )
    )

    train_log_likelihood = (
        train_filtered.log_likelihood
    )

    validation_log_likelihood = (
        validation_filtered.log_likelihood
    )

    train_ll_per_observation = (
        train_log_likelihood
        / train.observation_count
    )

    validation_ll_per_observation = (
        validation_log_likelihood
        / validation.observation_count
    )

    parameter_count = (
        hmm_parameter_count(
            state_count=
                state_count,

            feature_count=
                train.feature_count,
        )
    )

    aic, bic = (
        information_criteria(
            log_likelihood=
                train_log_likelihood,

            observation_count=
                train.observation_count,

            parameter_count=
                parameter_count,
        )
    )

    (
        weighted_counts,
        state_outcome_probability,
    ) = (
        build_state_outcome_probabilities(
            filtered_state_probability=
                train_filtered
                .filtered_state_probabilities,

            labels=
                train.label_ids,

            alpha=
                DIRICHLET_ALPHA,
        )
    )

    validation_class_probability = (
        validation_filtered
        .filtered_state_probabilities

        @ state_outcome_probability
    )

    validation_class_probability = (
        np.clip(
            validation_class_probability,
            1e-12,
            1.0,
        )
    )

    validation_class_probability = (
        validation_class_probability
        / validation_class_probability.sum(
            axis=1,
            keepdims=True,
        )
    )

    validation_metrics = (
        evaluate_directional_probabilities(
            labels=
                validation.label_ids,

            probabilities=
                validation_class_probability,
        )
    )

    diagnostics = (
        state_diagnostics(
            parameters=
                parameters,

            filtered_train=
                train_filtered,

            standardizer=
                standardizer,
        )
    )

    print(
        "\nMODEL FIT"
    )

    print(
        f"Train log-likelihood / obs: "
        f"{train_ll_per_observation:.6f}"
    )

    print(
        f"Validation log-likelihood / obs: "
        f"{validation_ll_per_observation:.6f}"
    )

    print(
        f"AIC: {aic:.2f}"
    )

    print(
        f"BIC: {bic:.2f}"
    )

    print(
        "\nSTATE DIAGNOSTICS"
    )

    for state_index in range(
        state_count
    ):
        print(
            f"State {state_index}: "
            f"occupancy="
            f"{diagnostics['occupancy'][state_index]:.4f} "
            f"persistence="
            f"{diagnostics['persistence'][state_index]:.4f} "
            f"duration="
            f"{diagnostics['expected_duration_bars'][state_index]:.2f}"
        )

    print(
        f"Collapsed states: "
        f"{diagnostics['collapsed_states']}"
    )

    print(
        f"Near-absorbing states: "
        f"{diagnostics['near_absorbing_states']}"
    )

    print(
        "\nVALIDATION DIRECTIONAL "
        "DIAGNOSTICS — SECONDARY"
    )

    print(
        f"Accuracy: "
        f"{validation_metrics['accuracy']:.6f}"
    )

    print(
        f"Macro F1: "
        f"{validation_metrics['macro_f1']:.6f}"
    )

    print(
        f"Log loss: "
        f"{validation_metrics['log_loss']:.6f}"
    )

    print(
        f"Brier: "
        f"{validation_metrics['brier_score']:.6f}"
    )

    print(
        f"ECE: "
        f"{validation_metrics['ece']:.6f}"
    )

    np.savez_compressed(
        output_directory
        / "candidate_parameters.npz",

        start_probability=
            parameters.start_probability,

        transition_matrix=
            parameters.transition_matrix,

        means=
            parameters.means,

        variances=
            parameters.variances,

        standardizer_mean=
            standardizer.mean,

        standardizer_scale=
            standardizer.scale,

        state_outcome_probability=
            state_outcome_probability,
    )

    write_json(
        output_directory
        / "metadata.json",

        {
            "model_name":
                MODEL_NAME,

            "artifact_status":
                "EXPERIMENT_ONLY",

            "production_ready":
                False,

            "test_evaluated":
                False,

            "state_count":
                state_count,

            "seed":
                seed,

            "covariance_type":
                "diag",

            "dataset_version":
                dataset.dataset_version,

            "source_feature_version":
                dataset.source_feature_version,

            "hmm_feature_version":
                dataset.hmm_feature_version,

            "label_version":
                dataset.label_version,

            "feature_names":
                list(
                    dataset.feature_names
                ),

            "feature_count":
                train.feature_count,

            "train_observations":
                train.observation_count,

            "train_sequences":
                len(
                    train.segment_lengths
                ),

            "validation_observations":
                validation.observation_count,

            "validation_sequences":
                len(
                    validation.segment_lengths
                ),

            "reserved_test_loaded":
                False,

            "dirichlet_alpha":
                DIRICHLET_ALPHA,
        },
    )

    write_json(
        output_directory
        / "training_metrics.json",

        {
            "converged":
                converged,

            "iterations":
                iterations,

            "fit_seconds":
                fit_seconds,

            "log_likelihood":
                train_log_likelihood,

            "log_likelihood_per_observation":
                train_ll_per_observation,

            "parameter_count":
                parameter_count,

            "aic":
                aic,

            "bic":
                bic,

            "monitor_history":
                history,

            "warnings":
                [
                    str(
                        warning.message
                    )

                    for warning
                    in captured_warnings
                ],
        },
    )

    write_json(
        output_directory
        / "validation_metrics.json",

        {
            "log_likelihood":
                validation_log_likelihood,

            "log_likelihood_per_observation":
                validation_ll_per_observation,

            "directional_diagnostics":
                validation_metrics,

            "selection_note":
                (
                    "Directional diagnostics "
                    "are secondary and must "
                    "not be used alone to "
                    "select latent state count."
                ),

            "test_evaluated":
                False,
        },
    )

    write_json(
        output_directory
        / "state_diagnostics.json",

        diagnostics,
    )

    write_json(
        output_directory
        / "state_outcome_probabilities.json",

        {
            "class_order":
                list(
                    CLASS_NAMES
                ),

            "dirichlet_alpha":
                DIRICHLET_ALPHA,

            "states":
                state_outcome_payload(
                    weighted_counts=
                        weighted_counts,

                    probabilities=
                        state_outcome_probability,
                ),

            "labels_used":
                "TRAIN_ONLY",
        },
    )

    write_json(
        output_directory
        / "transition_matrix.json",

        {
            "start_probability":
                parameters
                .start_probability
                .tolist(),

            "transition_matrix":
                parameters
                .transition_matrix
                .tolist(),
        },
    )

    print(
        "\nTest split evaluated: NO"
    )

    print(
        f"Artifacts: "
        f"{output_directory}"
    )

    print(
        "\n"
        + "=" * 78
    )

    print(
        "HMM EXPERIMENT COMPLETE"
    )

    print(
        "=" * 78
    )


def parse_arguments():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--states",
        type=int,
        required=True,
        choices=
            ALLOWED_STATE_COUNTS,
    )

    parser.add_argument(
        "--seed",
        type=int,
        required=True,
        choices=
            ALLOWED_SEEDS,
    )

    return parser.parse_args()


def main():
    arguments = (
        parse_arguments()
    )

    run_experiment(
        state_count=
            arguments.states,

        seed=
            arguments.seed,
    )


if __name__ == "__main__":
    main()
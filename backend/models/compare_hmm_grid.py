import json

from dataclasses import (
    dataclass,
)

from itertools import (
    combinations,
)

from pathlib import (
    Path,
)

import numpy as np

from scipy.optimize import (
    linear_sum_assignment,
)


EXPERIMENT_ROOT = Path(
    "backend/models/experiments"
)

MODEL_PREFIX = (
    "hmm_eurusd_m5_v1"
)

STATE_COUNTS = (
    3,
    4,
    5,
)

SEEDS = (
    11,
    29,
    47,
)


@dataclass(
    frozen=True
)
class Candidate:

    state_count: int

    seed: int

    path: Path

    start_probability: np.ndarray

    transition_matrix: np.ndarray

    means: np.ndarray

    variances: np.ndarray

    state_outcome_probability: np.ndarray

    occupancy: np.ndarray

    persistence: np.ndarray

    train_ll_per_observation: float

    validation_ll_per_observation: float

    aic: float

    bic: float

    validation_accuracy: float

    validation_macro_f1: float

    validation_log_loss: float

    validation_brier: float

    validation_ece: float


def read_json(
    path
):
    with open(
        path,
        "r",
        encoding="utf-8",
    ) as handle:

        return json.load(
            handle
        )


def load_candidate(
    state_count,
    seed,
):
    path = (
        EXPERIMENT_ROOT
        / (
            f"{MODEL_PREFIX}_"
            f"k{state_count}_"
            f"seed_{seed}"
        )
    )

    if not path.exists():
        raise FileNotFoundError(
            f"Missing HMM experiment: {path}"
        )

    metadata = read_json(
        path
        / "metadata.json"
    )

    training = read_json(
        path
        / "training_metrics.json"
    )

    validation = read_json(
        path
        / "validation_metrics.json"
    )

    diagnostics = read_json(
        path
        / "state_diagnostics.json"
    )

    if (
        metadata[
            "state_count"
        ]
        != state_count
    ):
        raise RuntimeError(
            "State-count metadata mismatch."
        )

    if (
        metadata[
            "seed"
        ]
        != seed
    ):
        raise RuntimeError(
            "Seed metadata mismatch."
        )

    if (
        metadata[
            "reserved_test_loaded"
        ]
        is not False
    ):
        raise RuntimeError(
            "A candidate reports that the "
            "reserved test split was loaded."
        )

    if (
        validation[
            "test_evaluated"
        ]
        is not False
    ):
        raise RuntimeError(
            "A candidate reports that the "
            "reserved test split was evaluated."
        )

    if (
        training[
            "converged"
        ]
        is not True
    ):
        raise RuntimeError(
            "Grid comparison refuses a "
            "non-converged candidate."
        )

    artifact = np.load(
        path
        / "candidate_parameters.npz"
    )

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

    state_outcome_probability = np.asarray(
        artifact[
            "state_outcome_probability"
        ],
        dtype=np.float64,
    )

    occupancy = np.asarray(
        diagnostics[
            "occupancy"
        ],
        dtype=np.float64,
    )

    persistence = np.asarray(
        diagnostics[
            "persistence"
        ],
        dtype=np.float64,
    )

    expected_shape = (
        state_count,
        means.shape[
            1
        ],
    )

    if (
        means.shape
        != expected_shape
    ):
        raise RuntimeError(
            "Emission mean shape mismatch."
        )

    if (
        variances.shape
        != expected_shape
    ):
        raise RuntimeError(
            "Emission variance shape mismatch."
        )

    if (
        transition_matrix.shape
        != (
            state_count,
            state_count,
        )
    ):
        raise RuntimeError(
            "Transition matrix shape mismatch."
        )

    if (
        state_outcome_probability.shape
        != (
            state_count,
            3,
        )
    ):
        raise RuntimeError(
            "State/outcome probability "
            "shape mismatch."
        )

    directional = (
        validation[
            "directional_diagnostics"
        ]
    )

    return Candidate(
        state_count=
            state_count,

        seed=
            seed,

        path=
            path,

        start_probability=
            start_probability,

        transition_matrix=
            transition_matrix,

        means=
            means,

        variances=
            variances,

        state_outcome_probability=
            state_outcome_probability,

        occupancy=
            occupancy,

        persistence=
            persistence,

        train_ll_per_observation=
            float(
                training[
                    "log_likelihood_per_observation"
                ]
            ),

        validation_ll_per_observation=
            float(
                validation[
                    "log_likelihood_per_observation"
                ]
            ),

        aic=
            float(
                training[
                    "aic"
                ]
            ),

        bic=
            float(
                training[
                    "bic"
                ]
            ),

        validation_accuracy=
            float(
                directional[
                    "accuracy"
                ]
            ),

        validation_macro_f1=
            float(
                directional[
                    "macro_f1"
                ]
            ),

        validation_log_loss=
            float(
                directional[
                    "log_loss"
                ]
            ),

        validation_brier=
            float(
                directional[
                    "brier_score"
                ]
            ),

        validation_ece=
            float(
                directional[
                    "ece"
                ]
            ),
    )


def state_matching_cost(
    reference,
    candidate,
):
    if (
        reference.state_count
        != candidate.state_count
    ):
        raise ValueError(
            "State matching requires equal K."
        )

    state_count = (
        reference.state_count
    )

    cost = np.empty(
        (
            state_count,
            state_count,
        ),
        dtype=np.float64,
    )

    for reference_state in range(
        state_count
    ):
        for candidate_state in range(
            state_count
        ):
            mean_distance = float(
                np.sqrt(
                    np.mean(
                        (
                            reference.means[
                                reference_state
                            ]
                            - candidate.means[
                                candidate_state
                            ]
                        )
                        ** 2
                    )
                )
            )

            log_variance_distance = float(
                np.sqrt(
                    np.mean(
                        (
                            np.log(
                                reference.variances[
                                    reference_state
                                ]
                            )
                            - np.log(
                                candidate.variances[
                                    candidate_state
                                ]
                            )
                        )
                        ** 2
                    )
                )
            )

            occupancy_distance = abs(
                float(
                    reference.occupancy[
                        reference_state
                    ]
                    - candidate.occupancy[
                        candidate_state
                    ]
                )
            )

            persistence_distance = abs(
                float(
                    reference.persistence[
                        reference_state
                    ]
                    - candidate.persistence[
                        candidate_state
                    ]
                )
            )

            # Emission structure is the
            # primary identity of a state.
            # Occupancy and persistence are
            # secondary tie-breakers.
            cost[
                reference_state,
                candidate_state,
            ] = (
                mean_distance

                + 0.25
                * log_variance_distance

                + 0.25
                * occupancy_distance

                + 0.25
                * persistence_distance
            )

    return cost


def match_candidate(
    reference,
    candidate,
):
    cost = state_matching_cost(
        reference,
        candidate,
    )

    rows, columns = (
        linear_sum_assignment(
            cost
        )
    )

    permutation = np.empty(
        reference.state_count,
        dtype=np.int64,
    )

    permutation[
        rows
    ] = columns

    matched_costs = cost[
        rows,
        columns,
    ]

    return (
        permutation,
        matched_costs,
    )


def compare_pair(
    reference,
    candidate,
):
    (
        permutation,
        matched_costs,
    ) = match_candidate(
        reference,
        candidate,
    )

    means = candidate.means[
        permutation
    ]

    variances = candidate.variances[
        permutation
    ]

    occupancy = candidate.occupancy[
        permutation
    ]

    persistence = candidate.persistence[
        permutation
    ]

    outcomes = (
        candidate
        .state_outcome_probability[
            permutation
        ]
    )

    start_probability = (
        candidate.start_probability[
            permutation
        ]
    )

    transition = (
        candidate.transition_matrix[
            np.ix_(
                permutation,
                permutation,
            )
        ]
    )

    emission_mean_difference = (
        reference.means
        - means
    )

    log_variance_difference = (
        np.log(
            reference.variances
        )
        - np.log(
            variances
        )
    )

    transition_difference = (
        reference.transition_matrix
        - transition
    )

    return {
        "reference_seed":
            reference.seed,

        "candidate_seed":
            candidate.seed,

        "permutation_to_reference":
            permutation.tolist(),

        "mean_matching_cost":
            float(
                np.mean(
                    matched_costs
                )
            ),

        "max_matching_cost":
            float(
                np.max(
                    matched_costs
                )
            ),

        "emission_mean_rmse":
            float(
                np.sqrt(
                    np.mean(
                        emission_mean_difference
                        ** 2
                    )
                )
            ),

        "emission_mean_max_abs":
            float(
                np.max(
                    np.abs(
                        emission_mean_difference
                    )
                )
            ),

        "log_variance_rmse":
            float(
                np.sqrt(
                    np.mean(
                        log_variance_difference
                        ** 2
                    )
                )
            ),

        "occupancy_mae":
            float(
                np.mean(
                    np.abs(
                        reference.occupancy
                        - occupancy
                    )
                )
            ),

        "persistence_mae":
            float(
                np.mean(
                    np.abs(
                        reference.persistence
                        - persistence
                    )
                )
            ),

        "start_probability_mae":
            float(
                np.mean(
                    np.abs(
                        reference.start_probability
                        - start_probability
                    )
                )
            ),

        "transition_mae":
            float(
                np.mean(
                    np.abs(
                        transition_difference
                    )
                )
            ),

        "transition_max_abs":
            float(
                np.max(
                    np.abs(
                        transition_difference
                    )
                )
            ),

        "state_outcome_probability_mae":
            float(
                np.mean(
                    np.abs(
                        reference
                        .state_outcome_probability

                        - outcomes
                    )
                )
            ),
    }


def aggregate(
    values,
):
    array = np.asarray(
        values,
        dtype=np.float64,
    )

    return {
        "mean":
            float(
                np.mean(
                    array
                )
            ),

        "std":
            float(
                np.std(
                    array,
                    ddof=0,
                )
            ),

        "minimum":
            float(
                np.min(
                    array
                )
            ),

        "maximum":
            float(
                np.max(
                    array
                )
            ),

        "range":
            float(
                np.max(
                    array
                )
                - np.min(
                    array
                )
            ),
    }


def summarize_state_count(
    candidates,
):
    pairwise = []

    for left, right in combinations(
        candidates,
        2,
    ):
        pairwise.append(
            compare_pair(
                left,
                right,
            )
        )

    minimum_occupancy = min(
        float(
            np.min(
                candidate.occupancy
            )
        )
        for candidate
        in candidates
    )

    maximum_persistence = max(
        float(
            np.max(
                candidate.persistence
            )
        )
        for candidate
        in candidates
    )

    return {
        "state_count":
            candidates[
                0
            ].state_count,

        "train_log_likelihood_per_observation":
            aggregate(
                [
                    candidate
                    .train_ll_per_observation

                    for candidate
                    in candidates
                ]
            ),

        "validation_log_likelihood_per_observation":
            aggregate(
                [
                    candidate
                    .validation_ll_per_observation

                    for candidate
                    in candidates
                ]
            ),

        "aic":
            aggregate(
                [
                    candidate.aic

                    for candidate
                    in candidates
                ]
            ),

        "bic":
            aggregate(
                [
                    candidate.bic

                    for candidate
                    in candidates
                ]
            ),

        "validation_accuracy":
            aggregate(
                [
                    candidate
                    .validation_accuracy

                    for candidate
                    in candidates
                ]
            ),

        "validation_macro_f1":
            aggregate(
                [
                    candidate
                    .validation_macro_f1

                    for candidate
                    in candidates
                ]
            ),

        "validation_log_loss":
            aggregate(
                [
                    candidate
                    .validation_log_loss

                    for candidate
                    in candidates
                ]
            ),

        "minimum_state_occupancy_across_seeds":
            minimum_occupancy,

        "maximum_state_persistence_across_seeds":
            maximum_persistence,

        "pairwise_state_stability":
            pairwise,

        "maximum_pairwise_emission_mean_rmse":
            max(
                row[
                    "emission_mean_rmse"
                ]
                for row
                in pairwise
            ),

        "maximum_pairwise_transition_mae":
            max(
                row[
                    "transition_mae"
                ]
                for row
                in pairwise
            ),

        "maximum_pairwise_occupancy_mae":
            max(
                row[
                    "occupancy_mae"
                ]
                for row
                in pairwise
            ),

        "maximum_pairwise_persistence_mae":
            max(
                row[
                    "persistence_mae"
                ]
                for row
                in pairwise
            ),

        "maximum_pairwise_state_outcome_mae":
            max(
                row[
                    "state_outcome_probability_mae"
                ]
                for row
                in pairwise
            ),
    }


def validate_grid(
    candidates
):
    if (
        len(
            candidates
        )
        != 9
    ):
        raise RuntimeError(
            "HMM grid must contain exactly "
            "nine candidates."
        )

    keys = {
        (
            candidate.state_count,
            candidate.seed,
        )

        for candidate
        in candidates
    }

    expected = {
        (
            state_count,
            seed,
        )

        for state_count
        in STATE_COUNTS

        for seed
        in SEEDS
    }

    if (
        keys
        != expected
    ):
        raise RuntimeError(
            "HMM candidate grid is incomplete."
        )


def main():
    print(
        "=" * 78
    )

    print(
        "EUR/USD ORACLE "
        "HMM GRID STABILITY AUDIT"
    )

    print(
        "=" * 78
    )

    candidates = [
        load_candidate(
            state_count,
            seed,
        )

        for state_count
        in STATE_COUNTS

        for seed
        in SEEDS
    ]

    validate_grid(
        candidates
    )

    summaries = []

    for state_count in (
        STATE_COUNTS
    ):
        group = [
            candidate

            for candidate
            in candidates

            if (
                candidate.state_count
                == state_count
            )
        ]

        summary = (
            summarize_state_count(
                group
            )
        )

        summaries.append(
            summary
        )

        validation_ll = (
            summary[
                "validation_log_likelihood_per_observation"
            ]
        )

        bic = (
            summary[
                "bic"
            ]
        )

        macro_f1 = (
            summary[
                "validation_macro_f1"
            ]
        )

        print(
            f"\nK={state_count}"
        )

        print(
            "  Validation LL/obs "
            f"mean={validation_ll['mean']:.6f} "
            f"std={validation_ll['std']:.6f} "
            f"range={validation_ll['range']:.6f}"
        )

        print(
            "  BIC "
            f"mean={bic['mean']:.2f} "
            f"range={bic['range']:.2f}"
        )

        print(
            "  Secondary macro F1 "
            f"mean={macro_f1['mean']:.6f} "
            f"range={macro_f1['range']:.6f}"
        )

        print(
            "  Minimum occupancy "
            f"{summary['minimum_state_occupancy_across_seeds']:.4f}"
        )

        print(
            "  Maximum persistence "
            f"{summary['maximum_state_persistence_across_seeds']:.4f}"
        )

        print(
            "  Max pairwise emission RMSE "
            f"{summary['maximum_pairwise_emission_mean_rmse']:.6e}"
        )

        print(
            "  Max pairwise transition MAE "
            f"{summary['maximum_pairwise_transition_mae']:.6e}"
        )

        print(
            "  Max pairwise occupancy MAE "
            f"{summary['maximum_pairwise_occupancy_mae']:.6e}"
        )

        print(
            "  Max pairwise persistence MAE "
            f"{summary['maximum_pairwise_persistence_mae']:.6e}"
        )

        print(
            "  Max pairwise outcome MAE "
            f"{summary['maximum_pairwise_state_outcome_mae']:.6e}"
        )

        print(
            "\n  Pairwise state matching:"
        )

        for pair in (
            summary[
                "pairwise_state_stability"
            ]
        ):
            print(
                "    "
                f"{pair['reference_seed']} "
                f"vs "
                f"{pair['candidate_seed']} "
                f"| permutation="
                f"{pair['permutation_to_reference']} "
                f"| emission_rmse="
                f"{pair['emission_mean_rmse']:.6e} "
                f"| transition_mae="
                f"{pair['transition_mae']:.6e}"
            )

    output_path = (
        EXPERIMENT_ROOT
        / (
            "hmm_eurusd_m5_v1_"
            "grid_comparison.json"
        )
    )

    with open(
        output_path,
        "w",
        encoding="utf-8",
    ) as handle:

        json.dump(
            {
                "model":
                    "hidden_markov_eurusd_m5_v1",

                "state_counts":
                    list(
                        STATE_COUNTS
                    ),

                "seeds":
                    list(
                        SEEDS
                    ),

                "candidate_count":
                    len(
                        candidates
                    ),

                "reserved_test_evaluated":
                    False,

                "selection_status":
                    "NOT_SELECTED",

                "summaries":
                    summaries,
            },
            handle,
            indent=2,
            sort_keys=True,
        )

        handle.write(
            "\n"
        )

    print(
        "\nReserved test evaluated: NO"
    )

    print(
        f"Audit artifact: "
        f"{output_path}"
    )

    print(
        "\n"
        + "=" * 78
    )

    print(
        "HMM GRID STABILITY AUDIT: PASS"
    )

    print(
        "=" * 78
    )


if __name__ == "__main__":
    main()
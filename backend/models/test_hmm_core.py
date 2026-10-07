import numpy as np

from hmmlearn.hmm import (
    GaussianHMM,
)

from backend.models.hmm_core import (
    DiagonalGaussianHMMParameters,
    HMMFeatureStandardizer,
    causal_forward_filter,
)


FEATURE_NAMES = (
    "return_1_pips",
    "return_3_pips",
    "body_pips",
    "range_pips",
    "close_location",
    "realized_volatility_12_pips",
    "trend_slope_12_pips_per_bar",
    "log_tick_volume_ratio_12",
)


def extract_diagonal_covariances(
    model
):
    covariances = np.asarray(
        model.covars_,
        dtype=np.float64,
    )

    if covariances.ndim == 2:
        return covariances

    if covariances.ndim == 3:
        return np.diagonal(
            covariances,
            axis1=1,
            axis2=2,
        ).copy()

    raise RuntimeError(
        "Unexpected hmmlearn covariance "
        f"shape: {covariances.shape}"
    )


def make_synthetic_data():
    rng = np.random.default_rng(
        20261007
    )

    feature_count = len(
        FEATURE_NAMES
    )

    train_a = rng.normal(
        loc=-0.6,
        scale=0.8,
        size=(
            90,
            feature_count,
        ),
    )

    train_b = rng.normal(
        loc=0.9,
        scale=1.1,
        size=(
            80,
            feature_count,
        ),
    )

    train_c = rng.normal(
        loc=0.1,
        scale=0.55,
        size=(
            70,
            feature_count,
        ),
    )

    train = np.vstack(
        (
            train_a,
            train_b,
            train_c,
        )
    )

    train_lengths = (
        90,
        80,
        70,
    )

    validation = rng.normal(
        loc=4.0,
        scale=1.7,
        size=(
            40,
            feature_count,
        ),
    )

    return (
        train,
        train_lengths,
        validation,
    )


def main():
    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "HMM NUMERICAL CORE TEST"
    )

    print(
        "=" * 70
    )

    (
        train,
        train_lengths,
        validation,
    ) = make_synthetic_data()

    # ==================================
    # TRAIN-ONLY STANDARDIZATION
    # ==================================

    standardizer = (
        HMMFeatureStandardizer.fit(
            values=
                train,

            feature_names=
                FEATURE_NAMES,
        )
    )

    train_scaled = (
        standardizer.transform(
            values=
                train,

            feature_names=
                FEATURE_NAMES,
        )
    )

    validation_scaled = (
        standardizer.transform(
            values=
                validation,

            feature_names=
                FEATURE_NAMES,
        )
    )

    maximum_train_mean_error = (
        float(
            np.max(
                np.abs(
                    train_scaled.mean(
                        axis=0
                    )
                )
            )
        )
    )

    maximum_train_std_error = (
        float(
            np.max(
                np.abs(
                    train_scaled.std(
                        axis=0,
                        ddof=0,
                    )
                    - 1.0
                )
            )
        )
    )

    manual_validation = (
        (
            validation
            - standardizer.mean
        )
        / standardizer.scale
    )

    assert np.allclose(
        validation_scaled,
        manual_validation,
        atol=0.0,
        rtol=0.0,
    )

    # Validation is intentionally shifted.
    # If it were standardized using its own
    # statistics, its means would be near 0.
    # They must NOT be near zero here.
    assert (
        np.max(
            np.abs(
                validation_scaled.mean(
                    axis=0
                )
            )
        )
        > 1.0
    )

    assert (
        maximum_train_mean_error
        < 1e-12
    )

    assert (
        maximum_train_std_error
        < 1e-12
    )

    print(
        "\nTrain mean max error: "
        f"{maximum_train_mean_error:.3e}"
    )

    print(
        "Train std max error: "
        f"{maximum_train_std_error:.3e}"
    )

    # ==================================
    # FIT REFERENCE HMM
    # ==================================

    reference = GaussianHMM(
        n_components=4,
        covariance_type="diag",
        n_iter=100,
        tol=1e-6,
        random_state=11,
        min_covar=1e-5,
        implementation="log",
    )

    reference.fit(
        train_scaled,
        lengths=
            train_lengths,
    )

    assert (
        reference.monitor_.converged
    )

    variances = (
        extract_diagonal_covariances(
            reference
        )
    )

    parameters = (
        DiagonalGaussianHMMParameters(
            feature_names=
                FEATURE_NAMES,

            start_probability=
                reference.startprob_,

            transition_matrix=
                reference.transmat_,

            means=
                reference.means_,

            variances=
                variances,
        )
    )

    # ==================================
    # OUR FORWARD FILTER
    # ==================================

    ours = causal_forward_filter(
        values=
            train_scaled,

        parameters=
            parameters,

        segment_lengths=
            train_lengths,
    )

    assert (
        ours.observation_count
        == train_scaled.shape[0]
    )

    assert (
        ours.state_count
        == 4
    )

    assert np.allclose(
        ours.filtered_state_probabilities.sum(
            axis=1
        ),
        1.0,
        atol=1e-12,
        rtol=0.0,
    )

    # ==================================
    # TOTAL LOG-LIKELIHOOD
    # ==================================
    #
    # hmmlearn.score(X, lengths) is a
    # forward-algorithm sequence
    # likelihood, not a smoothed-state
    # comparison.
    # ==================================

    reference_log_likelihood = (
        float(
            reference.score(
                train_scaled,
                lengths=
                    train_lengths,
            )
        )
    )

    likelihood_error = abs(
        ours.log_likelihood
        - reference_log_likelihood
    )

    print(
        "\nOur log likelihood: "
        f"{ours.log_likelihood:.12f}"
    )

    print(
        "hmmlearn likelihood: "
        f"{reference_log_likelihood:.12f}"
    )

    print(
        "Likelihood absolute error: "
        f"{likelihood_error:.3e}"
    )

    assert (
        likelihood_error
        < 1e-8
    )

    # ==================================
    # PREFIX-BY-PREFIX CAUSAL PROOF
    # ==================================
    #
    # hmmlearn predict_proba() normally
    # returns smoothed posteriors for all
    # positions in the sequence.
    #
    # But for the LAST observation of a
    # supplied prefix, there are no future
    # observations available. Therefore:
    #
    # P(S_t | X_1 ... X_t)
    #
    # at prefix[-1] is exactly the causal
    # filtering distribution we need.
    #
    # We compare every prefix independently.
    # ==================================

    offset = 0
    maximum_prefix_error = (
        0.0
    )

    prefixes_checked = (
        0
    )

    for length in train_lengths:
        segment = train_scaled[
            offset:
            offset + length
        ]

        our_segment = (
            ours.filtered_state_probabilities[
                offset:
                offset + length
            ]
        )

        for prefix_length in range(
            1,
            length + 1,
        ):
            prefix = segment[
                :prefix_length
            ]

            reference_latest = (
                reference.predict_proba(
                    prefix
                )[
                    -1
                ]
            )

            our_latest = (
                our_segment[
                    prefix_length - 1
                ]
            )

            error = float(
                np.max(
                    np.abs(
                        reference_latest
                        - our_latest
                    )
                )
            )

            maximum_prefix_error = max(
                maximum_prefix_error,
                error,
            )

            prefixes_checked += (
                1
            )

        offset += (
            length
        )

    print(
        "\nCausal prefixes checked: "
        f"{prefixes_checked}"
    )

    print(
        "Maximum causal posterior error: "
        f"{maximum_prefix_error:.3e}"
    )

    assert (
        maximum_prefix_error
        < 1e-9
    )

    # ==================================
    # SEGMENT RESET PROOF
    # ==================================

    second_start = (
        train_lengths[
            0
        ]
    )

    second_segment_first = (
        train_scaled[
            second_start:
            second_start + 1
        ]
    )

    reference_reset = (
        reference.predict_proba(
            second_segment_first
        )[
            -1
        ]
    )

    ours_reset = (
        ours.filtered_state_probabilities[
            second_start
        ]
    )

    assert np.allclose(
        reference_reset,
        ours_reset,
        atol=1e-10,
        rtol=0.0,
    )

    # ==================================
    # FUTURE-OBSERVATION ISOLATION
    # ==================================

    first_segment = train_scaled[
        :train_lengths[
            0
        ]
    ]

    first_parameters_result = (
        causal_forward_filter(
            values=
                first_segment,

            parameters=
                parameters,
        )
    )

    extreme_future = np.full(
        (
            25,
            train_scaled.shape[1],
        ),
        50.0,
        dtype=np.float64,
    )

    extended_segment = np.vstack(
        (
            first_segment,
            extreme_future,
        )
    )

    extended_result = (
        causal_forward_filter(
            values=
                extended_segment,

            parameters=
                parameters,
        )
    )

    assert np.array_equal(
        first_parameters_result
        .filtered_state_probabilities,

        extended_result
        .filtered_state_probabilities[
            :first_segment.shape[
                0
            ]
        ],
    )

    # ==================================
    # FEATURE ORDER REJECTION
    # ==================================

    rejected = False

    try:
        standardizer.transform(
            values=
                train,

            feature_names=
                tuple(
                    reversed(
                        FEATURE_NAMES
                    )
                ),
        )

    except ValueError:
        rejected = True

    assert rejected

    print(
        "\nTrain-only standardization: PASS"
    )

    print(
        "Feature-order enforcement: PASS"
    )

    print(
        "Diagonal Gaussian emissions: PASS"
    )

    print(
        "Forward likelihood vs hmmlearn: PASS"
    )

    print(
        "Prefix-by-prefix causal filtering: PASS"
    )

    print(
        "Sequence reset semantics: PASS"
    )

    print(
        "Future observation isolation: PASS"
    )

    print(
        "\n"
        + "=" * 70
    )

    print(
        "HMM NUMERICAL CORE TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":
    main()
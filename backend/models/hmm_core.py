from dataclasses import dataclass

import numpy as np


STANDARDIZER_EPSILON = 1e-12
PROBABILITY_TOLERANCE = 1e-10


@dataclass(frozen=True)
class HMMFeatureStandardizer:
    feature_names: tuple[str, ...]
    mean: np.ndarray
    scale: np.ndarray

    @classmethod
    def fit(
        cls,
        values,
        feature_names,
    ):
        matrix = np.asarray(
            values,
            dtype=np.float64,
        )

        feature_names = tuple(
            feature_names
        )

        if matrix.ndim != 2:
            raise ValueError(
                "HMM standardizer requires "
                "a two-dimensional matrix."
            )

        if matrix.shape[1] != len(
            feature_names
        ):
            raise ValueError(
                "HMM standardizer feature "
                "width does not match names."
            )

        if not np.isfinite(
            matrix
        ).all():
            raise ValueError(
                "HMM standardizer input "
                "contains NaN or infinity."
            )

        mean = matrix.mean(
            axis=0,
            dtype=np.float64,
        )

        scale = matrix.std(
            axis=0,
            ddof=0,
            dtype=np.float64,
        )

        scale = np.where(
            scale > STANDARDIZER_EPSILON,
            scale,
            1.0,
        )

        mean = np.asarray(
            mean,
            dtype=np.float64,
        )

        scale = np.asarray(
            scale,
            dtype=np.float64,
        )

        mean.setflags(
            write=False
        )

        scale.setflags(
            write=False
        )

        return cls(
            feature_names=
                feature_names,

            mean=
                mean,

            scale=
                scale,
        )

    def transform(
        self,
        values,
        feature_names,
    ):
        feature_names = tuple(
            feature_names
        )

        if (
            feature_names
            != self.feature_names
        ):
            raise ValueError(
                "HMM feature order does not "
                "match fitted standardizer."
            )

        matrix = np.asarray(
            values,
            dtype=np.float64,
        )

        if matrix.ndim != 2:
            raise ValueError(
                "HMM transform requires "
                "a two-dimensional matrix."
            )

        if (
            matrix.shape[1]
            != len(
                self.feature_names
            )
        ):
            raise ValueError(
                "HMM transform feature "
                "width mismatch."
            )

        if not np.isfinite(
            matrix
        ).all():
            raise ValueError(
                "HMM transform input "
                "contains NaN or infinity."
            )

        transformed = (
            matrix
            - self.mean
        ) / self.scale

        if not np.isfinite(
            transformed
        ).all():
            raise RuntimeError(
                "HMM standardized matrix "
                "contains NaN or infinity."
            )

        return np.asarray(
            transformed,
            dtype=np.float64,
        )


@dataclass(frozen=True)
class DiagonalGaussianHMMParameters:
    feature_names: tuple[str, ...]
    start_probability: np.ndarray
    transition_matrix: np.ndarray
    means: np.ndarray
    variances: np.ndarray

    def __post_init__(
        self
    ):
        feature_names = tuple(
            self.feature_names
        )

        start_probability = np.asarray(
            self.start_probability,
            dtype=np.float64,
        ).copy()

        transition_matrix = np.asarray(
            self.transition_matrix,
            dtype=np.float64,
        ).copy()

        means = np.asarray(
            self.means,
            dtype=np.float64,
        ).copy()

        variances = np.asarray(
            self.variances,
            dtype=np.float64,
        ).copy()

        if start_probability.ndim != 1:
            raise ValueError(
                "HMM start probabilities "
                "must be one-dimensional."
            )

        state_count = (
            start_probability.shape[0]
        )

        if state_count < 2:
            raise ValueError(
                "HMM requires at least "
                "two states."
            )

        if (
            transition_matrix.shape
            != (
                state_count,
                state_count,
            )
        ):
            raise ValueError(
                "HMM transition matrix "
                "shape is invalid."
            )

        if means.ndim != 2:
            raise ValueError(
                "HMM means must be "
                "two-dimensional."
            )

        if (
            means.shape[0]
            != state_count
        ):
            raise ValueError(
                "HMM means state count "
                "does not match."
            )

        if (
            means.shape[1]
            != len(
                feature_names
            )
        ):
            raise ValueError(
                "HMM means feature width "
                "does not match."
            )

        if (
            variances.shape
            != means.shape
        ):
            raise ValueError(
                "HMM diagonal variance "
                "shape does not match means."
            )

        arrays = (
            start_probability,
            transition_matrix,
            means,
            variances,
        )

        for array in arrays:
            if not np.isfinite(
                array
            ).all():
                raise ValueError(
                    "HMM parameters contain "
                    "NaN or infinity."
                )

        if np.any(
            start_probability < 0.0
        ):
            raise ValueError(
                "HMM start probabilities "
                "cannot be negative."
            )

        if np.any(
            transition_matrix < 0.0
        ):
            raise ValueError(
                "HMM transition probabilities "
                "cannot be negative."
            )

        if np.any(
            variances <= 0.0
        ):
            raise ValueError(
                "HMM variances must be "
                "strictly positive."
            )

        if not np.isclose(
            start_probability.sum(),
            1.0,
            atol=
                PROBABILITY_TOLERANCE,
            rtol=0.0,
        ):
            raise ValueError(
                "HMM start probabilities "
                "must sum to one."
            )

        if not np.allclose(
            transition_matrix.sum(
                axis=1
            ),
            1.0,
            atol=
                PROBABILITY_TOLERANCE,
            rtol=0.0,
        ):
            raise ValueError(
                "Every HMM transition row "
                "must sum to one."
            )

        for array in arrays:
            array.setflags(
                write=False
            )

        object.__setattr__(
            self,
            "feature_names",
            feature_names,
        )

        object.__setattr__(
            self,
            "start_probability",
            start_probability,
        )

        object.__setattr__(
            self,
            "transition_matrix",
            transition_matrix,
        )

        object.__setattr__(
            self,
            "means",
            means,
        )

        object.__setattr__(
            self,
            "variances",
            variances,
        )

    @property
    def state_count(
        self
    ):
        return int(
            self.start_probability.shape[0]
        )

    @property
    def feature_count(
        self
    ):
        return int(
            self.means.shape[1]
        )


@dataclass(frozen=True)
class HMMCausalFilterResult:
    filtered_state_probabilities: np.ndarray
    log_likelihood: float
    segment_lengths: tuple[int, ...]

    @property
    def observation_count(
        self
    ):
        return int(
            self.filtered_state_probabilities.shape[
                0
            ]
        )

    @property
    def state_count(
        self
    ):
        return int(
            self.filtered_state_probabilities.shape[
                1
            ]
        )

    @property
    def latest_state_probability(
        self
    ):
        return (
            self.filtered_state_probabilities[
                -1
            ]
        )


def _safe_log_probability(
    probability
):
    probability = np.asarray(
        probability,
        dtype=np.float64,
    )

    result = np.full(
        probability.shape,
        -np.inf,
        dtype=np.float64,
    )

    positive = (
        probability
        > 0.0
    )

    result[
        positive
    ] = np.log(
        probability[
            positive
        ]
    )

    return result


def _logsumexp(
    values,
    axis=None,
):
    return np.logaddexp.reduce(
        values,
        axis=axis,
    )


def diagonal_gaussian_log_emission(
    values,
    parameters,
):
    matrix = np.asarray(
        values,
        dtype=np.float64,
    )

    if matrix.ndim != 2:
        raise ValueError(
            "HMM observations must be "
            "two-dimensional."
        )

    if (
        matrix.shape[1]
        != parameters.feature_count
    ):
        raise ValueError(
            "HMM observation width does "
            "not match model parameters."
        )

    if not np.isfinite(
        matrix
    ).all():
        raise ValueError(
            "HMM observations contain "
            "NaN or infinity."
        )

    difference = (
        matrix[:, None, :]
        - parameters.means[
            None,
            :,
            :
        ]
    )

    quadratic = np.sum(
        (
            difference
            * difference
        )
        / parameters.variances[
            None,
            :,
            :
        ],
        axis=2,
    )

    log_normalizer = np.sum(
        np.log(
            2.0
            * np.pi
            * parameters.variances
        ),
        axis=1,
    )

    return -0.5 * (
        quadratic
        + log_normalizer[
            None,
            :
        ]
    )


def causal_forward_filter(
    values,
    parameters,
    segment_lengths=None,
):
    matrix = np.asarray(
        values,
        dtype=np.float64,
    )

    if matrix.ndim != 2:
        raise ValueError(
            "HMM forward filter requires "
            "a two-dimensional matrix."
        )

    observation_count = (
        matrix.shape[0]
    )

    if observation_count == 0:
        raise ValueError(
            "HMM forward filter requires "
            "at least one observation."
        )

    if (
        matrix.shape[1]
        != parameters.feature_count
    ):
        raise ValueError(
            "HMM forward input feature "
            "width mismatch."
        )

    if segment_lengths is None:
        segment_lengths = (
            observation_count,
        )

    segment_lengths = tuple(
        int(length)
        for length
        in segment_lengths
    )

    if any(
        length <= 0
        for length
        in segment_lengths
    ):
        raise ValueError(
            "HMM segment lengths must "
            "be positive."
        )

    if (
        sum(
            segment_lengths
        )
        != observation_count
    ):
        raise ValueError(
            "HMM segment lengths do not "
            "cover all observations."
        )

    log_emission = (
        diagonal_gaussian_log_emission(
            matrix,
            parameters,
        )
    )

    log_start = (
        _safe_log_probability(
            parameters.start_probability
        )
    )

    log_transition = (
        _safe_log_probability(
            parameters.transition_matrix
        )
    )

    filtered = np.empty(
        (
            observation_count,
            parameters.state_count,
        ),
        dtype=np.float64,
    )

    total_log_likelihood = (
        0.0
    )

    offset = (
        0
    )

    for length in segment_lengths:
        segment_end = (
            offset
            + length
        )

        first_log_alpha = (
            log_start
            + log_emission[
                offset
            ]
        )

        first_scale = (
            _logsumexp(
                first_log_alpha
            )
        )

        if not np.isfinite(
            first_scale
        ):
            raise RuntimeError(
                "HMM forward filter became "
                "numerically impossible at "
                "segment start."
            )

        log_alpha = (
            first_log_alpha
            - first_scale
        )

        filtered[
            offset
        ] = np.exp(
            log_alpha
        )

        total_log_likelihood += (
            float(
                first_scale
            )
        )

        for index in range(
            offset + 1,
            segment_end,
        ):
            predicted_log_state = (
                _logsumexp(
                    (
                        log_alpha[
                            :,
                            None
                        ]
                        + log_transition
                    ),
                    axis=0,
                )
            )

            next_log_alpha = (
                predicted_log_state
                + log_emission[
                    index
                ]
            )

            scale = (
                _logsumexp(
                    next_log_alpha
                )
            )

            if not np.isfinite(
                scale
            ):
                raise RuntimeError(
                    "HMM forward filter became "
                    "numerically impossible."
                )

            log_alpha = (
                next_log_alpha
                - scale
            )

            filtered[
                index
            ] = np.exp(
                log_alpha
            )

            total_log_likelihood += (
                float(
                    scale
                )
            )

        offset = (
            segment_end
        )

    if not np.isfinite(
        filtered
    ).all():
        raise RuntimeError(
            "HMM filtered probabilities "
            "contain NaN or infinity."
        )

    if not np.allclose(
        filtered.sum(
            axis=1
        ),
        1.0,
        atol=1e-12,
        rtol=0.0,
    ):
        raise RuntimeError(
            "HMM filtered state "
            "probabilities do not sum "
            "to one."
        )

    filtered.setflags(
        write=False
    )

    return HMMCausalFilterResult(
        filtered_state_probabilities=
            filtered,

        log_likelihood=
            float(
                total_log_likelihood
            ),

        segment_lengths=
            segment_lengths,
    )
import hashlib
import json

from pathlib import Path

import numpy as np

from backend.models.hmm_core import (
    DiagonalGaussianHMMParameters,
    HMMFeatureStandardizer,
)

from backend.models.hmm_observation_builder import (
    HMMObservationBuilder,
)


class HMMModelRegistry:

    MODEL_NAME = (
        "hidden_markov_eurusd_m5_v1"
    )

    EXPECTED_FILES = (
        "parameters.npz",
        "metadata.json",
        "evaluation.json",
        "checksums.json",
    )

    PARAMETER_KEYS = {
        "start_probability",
        "transition_matrix",
        "means",
        "variances",
        "standardizer_mean",
        "standardizer_scale",
        "state_outcome_probability",
    }


    def __init__(
        self,
        registry_path=(
            "backend/models/registry/"
            "hidden_markov_eurusd_m5_v1"
        ),
    ):
        self.registry_path = Path(
            registry_path
        )

        self._artifact = (
            None
        )


    @staticmethod
    def _sha256_file(
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


    @staticmethod
    def _read_json(
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


    def _validate_files(
        self,
    ):
        if not (
            self.registry_path
            .is_dir()
        ):
            raise FileNotFoundError(
                "HMM production registry "
                f"does not exist: "
                f"{self.registry_path}"
            )

        for filename in (
            self.EXPECTED_FILES
        ):
            path = (
                self.registry_path
                / filename
            )

            if not path.is_file():
                raise FileNotFoundError(
                    "HMM production artifact "
                    f"is missing: {path}"
                )


    def _validate_checksums(
        self,
        checksums,
    ):
        for filename in (
            "parameters.npz",
            "metadata.json",
            "evaluation.json",
        ):
            expected = (
                checksums.get(
                    filename
                )
            )

            if not isinstance(
                expected,
                str,
            ):
                raise RuntimeError(
                    "HMM checksum manifest "
                    f"is missing {filename}."
                )

            actual = (
                self._sha256_file(
                    self.registry_path
                    / filename
                )
            )

            if (
                actual
                != expected
            ):
                raise RuntimeError(
                    "HMM production checksum "
                    f"mismatch for {filename}."
                )


    @staticmethod
    def _freeze_array(
        value,
        dtype,
    ):
        array = np.asarray(
            value,
            dtype=dtype,
        ).copy()

        array.setflags(
            write=False
        )

        return array


    def _validate_metadata(
        self,
        metadata,
    ):
        if (
            metadata.get(
                "model_name"
            )
            != self.MODEL_NAME
        ):
            raise RuntimeError(
                "Unexpected HMM production "
                "model name."
            )

        if (
            metadata.get(
                "artifact_status"
            )
            != "PRODUCTION"
        ):
            raise RuntimeError(
                "HMM artifact is not marked "
                "for production."
            )

        if (
            metadata.get(
                "production_ready"
            )
            is not True
        ):
            raise RuntimeError(
                "HMM artifact is not "
                "production-ready."
            )

        if (
            metadata.get(
                "runtime_dependency_hmmlearn"
            )
            is not False
        ):
            raise RuntimeError(
                "Production HMM must not "
                "depend on hmmlearn."
            )

        if (
            metadata.get(
                "symbol"
            )
            != "EURUSD"
        ):
            raise RuntimeError(
                "Unexpected HMM symbol."
            )

        if (
            metadata.get(
                "timeframe"
            )
            != "M5"
        ):
            raise RuntimeError(
                "Unexpected HMM timeframe."
            )

        if (
            int(
                metadata.get(
                    "timeframe_minutes"
                )
            )
            != 5
        ):
            raise RuntimeError(
                "Unexpected HMM timeframe "
                "minutes."
            )

        if (
            metadata.get(
                "hmm_feature_version"
            )
            != HMMObservationBuilder
            .FEATURE_VERSION
        ):
            raise RuntimeError(
                "Production HMM feature "
                "version differs from the "
                "live observation builder."
            )

        model_features = tuple(
            metadata.get(
                "feature_names",
                (),
            )
        )

        live_features = tuple(
            HMMObservationBuilder
            .FEATURE_NAMES
        )

        if (
            model_features
            != live_features
        ):
            raise RuntimeError(
                "Production HMM feature "
                "order differs from the "
                "live observation builder."
            )

        if (
            int(
                metadata.get(
                    "feature_count"
                )
            )
            != len(
                live_features
            )
        ):
            raise RuntimeError(
                "Production HMM feature "
                "count mismatch."
            )

        state_count = int(
            metadata.get(
                "state_count"
            )
        )

        state_names = tuple(
            metadata.get(
                "state_names",
                (),
            )
        )

        if (
            len(
                state_names
            )
            != state_count
        ):
            raise RuntimeError(
                "Production HMM state-name "
                "count mismatch."
            )

        if (
            len(
                set(
                    state_names
                )
            )
            != state_count
        ):
            raise RuntimeError(
                "Production HMM state names "
                "must be unique."
            )

        if (
            metadata.get(
                "signal_type"
            )
            != "contextual"
        ):
            raise RuntimeError(
                "Production HMM must remain "
                "contextual."
            )

        if (
            metadata.get(
                "direction_policy"
            )
            != "neutral"
        ):
            raise RuntimeError(
                "Production HMM direction "
                "policy must remain neutral."
            )

        if (
            metadata.get(
                "score_policy"
            )
            != "fixed_zero"
        ):
            raise RuntimeError(
                "Production HMM score policy "
                "must remain fixed-zero."
            )


    def _load_parameters(
        self,
        metadata,
    ):
        path = (
            self.registry_path
            / "parameters.npz"
        )

        with np.load(
            path,
            allow_pickle=False,
        ) as artifact:

            keys = set(
                artifact.files
            )

            missing = (
                self.PARAMETER_KEYS
                - keys
            )

            if missing:
                raise RuntimeError(
                    "HMM production parameter "
                    "artifact is missing keys: "
                    f"{sorted(missing)}"
                )

            start_probability = (
                self._freeze_array(
                    artifact[
                        "start_probability"
                    ],
                    np.float64,
                )
            )

            transition_matrix = (
                self._freeze_array(
                    artifact[
                        "transition_matrix"
                    ],
                    np.float64,
                )
            )

            means = (
                self._freeze_array(
                    artifact[
                        "means"
                    ],
                    np.float64,
                )
            )

            variances = (
                self._freeze_array(
                    artifact[
                        "variances"
                    ],
                    np.float64,
                )
            )

            standardizer_mean = (
                self._freeze_array(
                    artifact[
                        "standardizer_mean"
                    ],
                    np.float64,
                )
            )

            standardizer_scale = (
                self._freeze_array(
                    artifact[
                        "standardizer_scale"
                    ],
                    np.float64,
                )
            )

            state_outcome_probability = (
                self._freeze_array(
                    artifact[
                        "state_outcome_probability"
                    ],
                    np.float64,
                )
            )

        state_count = int(
            metadata[
                "state_count"
            ]
        )

        feature_count = int(
            metadata[
                "feature_count"
            ]
        )

        if (
            standardizer_mean.shape
            != (
                feature_count,
            )
        ):
            raise RuntimeError(
                "HMM standardizer mean "
                "shape mismatch."
            )

        if (
            standardizer_scale.shape
            != (
                feature_count,
            )
        ):
            raise RuntimeError(
                "HMM standardizer scale "
                "shape mismatch."
            )

        if not np.isfinite(
            standardizer_mean
        ).all():
            raise RuntimeError(
                "HMM standardizer mean "
                "contains invalid values."
            )

        if not np.isfinite(
            standardizer_scale
        ).all():
            raise RuntimeError(
                "HMM standardizer scale "
                "contains invalid values."
            )

        if np.any(
            standardizer_scale
            <= 0.0
        ):
            raise RuntimeError(
                "HMM standardizer scales "
                "must be positive."
            )

        if (
            state_outcome_probability.shape
            != (
                state_count,
                3,
            )
        ):
            raise RuntimeError(
                "HMM state/outcome "
                "probability shape mismatch."
            )

        if not np.isfinite(
            state_outcome_probability
        ).all():
            raise RuntimeError(
                "HMM state/outcome "
                "probabilities contain "
                "invalid values."
            )

        if np.any(
            state_outcome_probability
            < 0.0
        ):
            raise RuntimeError(
                "HMM state/outcome "
                "probabilities cannot be "
                "negative."
            )

        if not np.allclose(
            state_outcome_probability.sum(
                axis=1
            ),
            1.0,
            atol=1e-10,
            rtol=0.0,
        ):
            raise RuntimeError(
                "HMM state/outcome "
                "probability rows must "
                "sum to one."
            )

        parameters = (
            DiagonalGaussianHMMParameters(
                feature_names=
                    tuple(
                        metadata[
                            "feature_names"
                        ]
                    ),

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
                    tuple(
                        metadata[
                            "feature_names"
                        ]
                    ),

                mean=
                    standardizer_mean,

                scale=
                    standardizer_scale,
            )
        )

        return {
            "parameters":
                parameters,

            "standardizer":
                standardizer,

            "state_outcome_probability":
                state_outcome_probability,
        }


    def load(
        self,
    ):
        if (
            self._artifact
            is not None
        ):
            return (
                self._artifact
            )

        self._validate_files()

        checksums = (
            self._read_json(
                self.registry_path
                / "checksums.json"
            )
        )

        self._validate_checksums(
            checksums
        )

        metadata = (
            self._read_json(
                self.registry_path
                / "metadata.json"
            )
        )

        evaluation = (
            self._read_json(
                self.registry_path
                / "evaluation.json"
            )
        )

        self._validate_metadata(
            metadata
        )

        selected_hash = (
            metadata[
                "selected_parameter_sha256"
            ]
        )

        actual_parameter_hash = (
            self._sha256_file(
                self.registry_path
                / "parameters.npz"
            )
        )

        if (
            actual_parameter_hash
            != selected_hash
        ):
            raise RuntimeError(
                "Production HMM parameter "
                "bytes differ from selected "
                "candidate."
            )

        parameter_artifact = (
            self._load_parameters(
                metadata
            )
        )

        artifact = {
            "metadata":
                metadata,

            "evaluation":
                evaluation,

            "checksums":
                checksums,

            "parameters":
                parameter_artifact[
                    "parameters"
                ],

            "standardizer":
                parameter_artifact[
                    "standardizer"
                ],

            "state_outcome_probability":
                parameter_artifact[
                    "state_outcome_probability"
                ],

            "state_names":
                tuple(
                    metadata[
                        "state_names"
                    ]
                ),
        }

        self._artifact = (
            artifact
        )

        return artifact
import json
import shutil
import sys
import tempfile

from pathlib import Path

import numpy as np

from backend.models.hmm_core import (
    causal_forward_filter,
)

from backend.models.hmm_model_registry import (
    HMMModelRegistry,
)

from backend.models.hmm_observation_builder import (
    HMMObservationBuilder,
)


EXPECTED_SHA256 = (
    "4383DE12C975E8B656F3931F27ADE954"
    "148905AC69FE8E9448099E0B6012D26F"
)


def main():
    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "HMM PRODUCTION REGISTRY TEST"
    )

    print(
        "=" * 70
    )

    assert (
        "hmmlearn"
        not in sys.modules
    )

    registry = (
        HMMModelRegistry()
    )

    artifact = (
        registry.load()
    )

    metadata = (
        artifact[
            "metadata"
        ]
    )

    parameters = (
        artifact[
            "parameters"
        ]
    )

    standardizer = (
        artifact[
            "standardizer"
        ]
    )

    state_outcome = (
        artifact[
            "state_outcome_probability"
        ]
    )

    state_names = (
        artifact[
            "state_names"
        ]
    )

    assert (
        metadata[
            "model_name"
        ]
        == "hidden_markov_eurusd_m5_v1"
    )

    assert (
        metadata[
            "artifact_status"
        ]
        == "PRODUCTION"
    )

    assert (
        metadata[
            "production_ready"
        ]
        is True
    )

    assert (
        metadata[
            "runtime_dependency_hmmlearn"
        ]
        is False
    )

    assert (
        metadata[
            "state_count"
        ]
        == 5
    )

    assert (
        metadata[
            "selected_seed"
        ]
        == 11
    )

    assert (
        metadata[
            "selected_parameter_sha256"
        ]
        == EXPECTED_SHA256
    )

    assert (
        tuple(
            metadata[
                "feature_names"
            ]
        )
        == tuple(
            HMMObservationBuilder
            .FEATURE_NAMES
        )
    )

    assert (
        state_names
        == (
            "high_volatility_turbulence",
            "mild_bearish_impulse",
            "mild_bullish_impulse",
            "strong_bearish_expansion",
            "strong_bullish_expansion",
        )
    )

    assert (
        parameters.state_count
        == 5
    )

    assert (
        parameters.feature_count
        == 8
    )

    assert (
        parameters.transition_matrix.shape
        == (
            5,
            5,
        )
    )

    assert (
        parameters.means.shape
        == (
            5,
            8,
        )
    )

    assert (
        parameters.variances.shape
        == (
            5,
            8,
        )
    )

    assert (
        standardizer.mean.shape
        == (
            8,
        )
    )

    assert (
        standardizer.scale.shape
        == (
            8,
        )
    )

    assert (
        state_outcome.shape
        == (
            5,
            3,
        )
    )

    assert np.allclose(
        state_outcome.sum(
            axis=1
        ),
        1.0,
        atol=1e-10,
        rtol=0.0,
    )

    # ==================================
    # STANDARDIZER CONTRACT
    # ==================================

    mean_matrix = np.asarray(
        [
            standardizer.mean
        ],
        dtype=np.float64,
    )

    standardized_mean = (
        standardizer.transform(
            values=
                mean_matrix,

            feature_names=
                metadata[
                    "feature_names"
                ],
        )
    )

    assert np.allclose(
        standardized_mean,
        0.0,
        atol=1e-12,
        rtol=0.0,
    )

    # ==================================
    # CAUSAL INFERENCE SMOKE TEST
    # ==================================

    synthetic_standardized = (
        np.zeros(
            (
                20,
                8,
            ),
            dtype=np.float64,
        )
    )

    filtered = (
        causal_forward_filter(
            values=
                synthetic_standardized,

            parameters=
                parameters,

            segment_lengths=
                (
                    10,
                    10,
                ),
        )
    )

    assert (
        filtered
        .filtered_state_probabilities
        .shape
        == (
            20,
            5,
        )
    )

    assert np.allclose(
        filtered
        .filtered_state_probabilities
        .sum(
            axis=1
        ),
        1.0,
        atol=1e-12,
        rtol=0.0,
    )

    # ==================================
    # LAZY CACHE CONTRACT
    # ==================================

    artifact_again = (
        registry.load()
    )

    assert (
        artifact_again
        is artifact
    )

    # ==================================
    # CHECKSUM TAMPER DETECTION
    # ==================================

    source_registry = Path(
        "backend/models/registry/"
        "hidden_markov_eurusd_m5_v1"
    )

    with tempfile.TemporaryDirectory() as temp:
        copied_registry = (
            Path(
                temp
            )
            / "registry"
        )

        shutil.copytree(
            source_registry,
            copied_registry,
        )

        metadata_path = (
            copied_registry
            / "metadata.json"
        )

        with open(
            metadata_path,
            "r",
            encoding="utf-8",
        ) as handle:
            tampered = json.load(
                handle
            )

        tampered[
            "selected_seed"
        ] = 47

        with open(
            metadata_path,
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                tampered,
                handle,
                indent=2,
                sort_keys=True,
            )

            handle.write(
                "\n"
            )

        rejected = False

        try:
            (
                HMMModelRegistry(
                    registry_path=
                        copied_registry
                )
                .load()
            )

        except RuntimeError:
            rejected = True

        assert rejected

    assert (
        "hmmlearn"
        not in sys.modules
    )

    print(
        "\nProduction metadata: PASS"
    )

    print(
        "Selected parameter identity: PASS"
    )

    print(
        "Checksum verification: PASS"
    )

    print(
        "Feature-order contract: PASS"
    )

    print(
        "State semantic contract: PASS"
    )

    print(
        "Standardizer contract: PASS"
    )

    print(
        "Causal inference smoke test: PASS"
    )

    print(
        "Lazy registry cache: PASS"
    )

    print(
        "Tamper detection: PASS"
    )

    print(
        "Runtime hmmlearn import: NO"
    )

    print(
        "\n"
        + "=" * 70
    )

    print(
        "HMM PRODUCTION REGISTRY TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":
    main()
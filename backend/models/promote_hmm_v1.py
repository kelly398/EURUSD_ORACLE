import hashlib
import json
import shutil

from pathlib import Path


MODEL_NAME = (
    "hidden_markov_eurusd_m5_v1"
)

EXPECTED_PARAMETER_SHA256 = (
    "4383DE12C975E8B656F3931F27ADE954"
    "148905AC69FE8E9448099E0B6012D26F"
)

EXPECTED_PRE_TEST_COMMIT = (
    "c79ba5c"
)

EXPECTED_STATE_COUNT = (
    5
)

EXPECTED_SEED = (
    11
)


EXPERIMENT_ROOT = Path(
    "backend/models/experiments"
)

SOURCE_DIRECTORY = (
    EXPERIMENT_ROOT
    / "hmm_eurusd_m5_v1_k5_seed_11"
)

SOURCE_PARAMETER_PATH = (
    SOURCE_DIRECTORY
    / "candidate_parameters.npz"
)

SOURCE_METADATA_PATH = (
    SOURCE_DIRECTORY
    / "metadata.json"
)

FINAL_TEST_PATH = (
    SOURCE_DIRECTORY
    / "final_test_evaluation.json"
)

SELECTION_PATH = (
    EXPERIMENT_ROOT
    / "hmm_eurusd_m5_v1_selection.json"
)

FINAL_DECISION_PATH = (
    EXPERIMENT_ROOT
    / "hmm_eurusd_m5_v1_final_decision.json"
)


REGISTRY_ROOT = Path(
    "backend/models/registry"
)

DESTINATION_DIRECTORY = (
    REGISTRY_ROOT
    / MODEL_NAME
)

TEMP_DIRECTORY = (
    REGISTRY_ROOT
    / (
        MODEL_NAME
        + ".tmp"
    )
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


def validate_source():
    required = (
        SOURCE_PARAMETER_PATH,
        SOURCE_METADATA_PATH,
        FINAL_TEST_PATH,
        SELECTION_PATH,
        FINAL_DECISION_PATH,
    )

    for path in required:
        if not path.exists():
            raise FileNotFoundError(
                f"Required HMM promotion "
                f"source is missing: {path}"
            )

    source_hash = (
        sha256_file(
            SOURCE_PARAMETER_PATH
        )
    )

    if (
        source_hash
        != EXPECTED_PARAMETER_SHA256
    ):
        raise RuntimeError(
            "Frozen HMM candidate SHA-256 "
            "does not match the pre-test "
            "selection.\n"
            f"Expected: "
            f"{EXPECTED_PARAMETER_SHA256}\n"
            f"Actual:   "
            f"{source_hash}"
        )

    source_metadata = (
        read_json(
            SOURCE_METADATA_PATH
        )
    )

    selection = (
        read_json(
            SELECTION_PATH
        )
    )

    final_test = (
        read_json(
            FINAL_TEST_PATH
        )
    )

    final_decision = (
        read_json(
            FINAL_DECISION_PATH
        )
    )

    if (
        selection[
            "selection_status"
        ]
        != "FROZEN_BEFORE_TEST"
    ):
        raise RuntimeError(
            "Pre-test HMM selection is not "
            "frozen."
        )

    if (
        selection[
            "selected_state_count"
        ]
        != EXPECTED_STATE_COUNT
    ):
        raise RuntimeError(
            "Frozen HMM state count changed."
        )

    if (
        selection[
            "selected_seed"
        ]
        != EXPECTED_SEED
    ):
        raise RuntimeError(
            "Frozen HMM seed changed."
        )

    if (
        selection[
            "test_evaluated"
        ]
        is not False
    ):
        raise RuntimeError(
            "Pre-test selection artifact "
            "was modified after test access."
        )

    if (
        source_metadata[
            "state_count"
        ]
        != EXPECTED_STATE_COUNT
    ):
        raise RuntimeError(
            "Candidate metadata state-count "
            "mismatch."
        )

    if (
        source_metadata[
            "seed"
        ]
        != EXPECTED_SEED
    ):
        raise RuntimeError(
            "Candidate metadata seed "
            "mismatch."
        )

    if (
        final_test[
            "test_evaluated"
        ]
        is not True
    ):
        raise RuntimeError(
            "Reserved test evaluation is "
            "not complete."
        )

    if (
        final_test[
            "parameter_sha256"
        ]
        != EXPECTED_PARAMETER_SHA256
    ):
        raise RuntimeError(
            "Reserved-test parameter hash "
            "does not match selected model."
        )

    if (
        final_test[
            "pre_test_selection_commit"
        ]
        != EXPECTED_PRE_TEST_COMMIT
    ):
        raise RuntimeError(
            "Reserved-test pre-test commit "
            "does not match frozen commit."
        )

    if (
        final_test[
            "refit_performed"
        ]
        is not False
    ):
        raise RuntimeError(
            "HMM was refit after opening "
            "the reserved test."
        )

    if (
        final_test[
            "calibration_performed"
        ]
        is not False
    ):
        raise RuntimeError(
            "HMM was calibrated after opening "
            "the reserved test."
        )

    if (
        final_test[
            "selection_changed_after_test"
        ]
        is not False
    ):
        raise RuntimeError(
            "HMM selection changed after "
            "reserved-test evaluation."
        )

    if (
        final_decision[
            "decision_status"
        ]
        != "ACCEPTED_FOR_PRODUCTION_CONTEXTUAL"
    ):
        raise RuntimeError(
            "HMM final decision does not "
            "approve production contextual "
            "use."
        )

    if (
        final_decision[
            "parameter_sha256"
        ]
        != EXPECTED_PARAMETER_SHA256
    ):
        raise RuntimeError(
            "Final HMM decision parameter "
            "hash mismatch."
        )

    if (
        final_decision[
            "selected_state_count"
        ]
        != EXPECTED_STATE_COUNT
    ):
        raise RuntimeError(
            "Final HMM decision state-count "
            "mismatch."
        )

    if (
        final_decision[
            "selected_seed"
        ]
        != EXPECTED_SEED
    ):
        raise RuntimeError(
            "Final HMM decision seed "
            "mismatch."
        )

    if (
        len(
            final_decision[
                "state_semantics"
            ]
        )
        != EXPECTED_STATE_COUNT
    ):
        raise RuntimeError(
            "Final HMM state semantic count "
            "is invalid."
        )

    return (
        source_metadata,
        selection,
        final_test,
        final_decision,
        source_hash,
    )


def main():
    print(
        "=" * 78
    )

    print(
        "EUR/USD ORACLE "
        "HMM V1 PRODUCTION PROMOTION"
    )

    print(
        "=" * 78
    )

    if (
        DESTINATION_DIRECTORY.exists()
    ):
        raise FileExistsError(
            "Production HMM registry already "
            f"exists: {DESTINATION_DIRECTORY}"
        )

    if (
        TEMP_DIRECTORY.exists()
    ):
        raise FileExistsError(
            "Temporary HMM promotion directory "
            f"already exists: {TEMP_DIRECTORY}"
        )

    (
        source_metadata,
        selection,
        final_test,
        final_decision,
        source_hash,
    ) = validate_source()

    print(
        "\nFrozen candidate verified."
    )

    print(
        f"State count: "
        f"{EXPECTED_STATE_COUNT}"
    )

    print(
        f"Seed: "
        f"{EXPECTED_SEED}"
    )

    print(
        f"Pre-test commit: "
        f"{EXPECTED_PRE_TEST_COMMIT}"
    )

    print(
        f"Source SHA-256: "
        f"{source_hash}"
    )

    TEMP_DIRECTORY.mkdir(
        parents=True,
        exist_ok=False,
    )

    try:
        production_parameter_path = (
            TEMP_DIRECTORY
            / "parameters.npz"
        )

        shutil.copy2(
            SOURCE_PARAMETER_PATH,
            production_parameter_path,
        )

        production_hash = (
            sha256_file(
                production_parameter_path
            )
        )

        if (
            production_hash
            != source_hash
        ):
            raise RuntimeError(
                "Production parameter copy "
                "does not match selected "
                "candidate bytes."
            )

        state_names = tuple(
            final_decision[
                "state_semantics"
            ]
        )

        metadata = {
            "model_name":
                MODEL_NAME,

            "artifact_status":
                "PRODUCTION",

            "production_ready":
                True,

            "model_role":
                "latent_market_regime_context",

            "runtime_dependency_hmmlearn":
                False,

            "symbol":
                "EURUSD",

            "timeframe":
                "M5",

            "timeframe_minutes":
                5,

            "dataset_version":
                source_metadata[
                    "dataset_version"
                ],

            "source_feature_version":
                source_metadata[
                    "source_feature_version"
                ],

            "hmm_feature_version":
                source_metadata[
                    "hmm_feature_version"
                ],

            "label_version":
                source_metadata[
                    "label_version"
                ],

            "feature_names":
                source_metadata[
                    "feature_names"
                ],

            "feature_count":
                source_metadata[
                    "feature_count"
                ],

            "state_count":
                EXPECTED_STATE_COUNT,

            "selected_seed":
                EXPECTED_SEED,

            "state_names":
                list(
                    state_names
                ),

            "covariance_type":
                "diag",

            "signal_type":
                "contextual",

            "direction_policy":
                "neutral",

            "score_policy":
                "fixed_zero",

            "confidence_policy":
                (
                    "1 - normalized_entropy("
                    "current_state_posterior)"
                ),

            "directional_probabilities_policy":
                "secondary_evidence_only",

            "selected_parameter_sha256":
                EXPECTED_PARAMETER_SHA256,

            "pre_test_selection_commit":
                EXPECTED_PRE_TEST_COMMIT,

            "source_candidate":
                (
                    "hmm_eurusd_m5_v1_"
                    "k5_seed_11"
                ),

            "test_evaluated":
                True,

            "refit_after_test":
                False,

            "calibration_after_test":
                False,

            "selection_changed_after_test":
                False,
        }

        evaluation = {
            "decision_status":
                final_decision[
                    "decision_status"
                ],

            "reserved_test":
                final_decision[
                    "reserved_test"
                ],

            "directional_diagnostics_secondary":
                final_decision[
                    "directional_diagnostics_secondary"
                ],

            "production_monitoring":
                final_decision[
                    "production_monitoring"
                ],

            "full_reserved_test_evaluation":
                final_test,
        }

        write_json(
            TEMP_DIRECTORY
            / "metadata.json",
            metadata,
        )

        write_json(
            TEMP_DIRECTORY
            / "evaluation.json",
            evaluation,
        )

        checksummed_files = (
            "parameters.npz",
            "metadata.json",
            "evaluation.json",
        )

        checksums = {
            filename:
                sha256_file(
                    TEMP_DIRECTORY
                    / filename
                )

            for filename
            in checksummed_files
        }

        checksums[
            "selected_source_parameter_sha256"
        ] = (
            EXPECTED_PARAMETER_SHA256
        )

        write_json(
            TEMP_DIRECTORY
            / "checksums.json",
            checksums,
        )

        TEMP_DIRECTORY.rename(
            DESTINATION_DIRECTORY
        )

    except Exception:
        if (
            TEMP_DIRECTORY.exists()
        ):
            shutil.rmtree(
                TEMP_DIRECTORY
            )

        raise

    print(
        "\nProduction registry created:"
    )

    print(
        DESTINATION_DIRECTORY
    )

    print(
        "\nProduction parameter SHA-256:"
    )

    print(
        sha256_file(
            DESTINATION_DIRECTORY
            / "parameters.npz"
        )
    )

    print(
        "\nRuntime hmmlearn dependency: NO"
    )

    print(
        "Refit performed: NO"
    )

    print(
        "Parameter bytes changed: NO"
    )

    print(
        "\n"
        + "=" * 78
    )

    print(
        "HMM V1 PRODUCTION PROMOTION: PASS"
    )

    print(
        "=" * 78
    )


if __name__ == "__main__":
    main()
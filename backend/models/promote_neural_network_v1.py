import hashlib
import json
import shutil

from datetime import (
    datetime,
    timezone
)

from pathlib import Path


MODEL_NAME = (
    "neural_network_eurusd_m5_v1"
)

SEEDS = (
    11,
    29,
    47
)

DATASET_VERSION = (
    "eurusd_m5_full_20251002_20261001_v1"
)

FEATURE_VERSION = (
    "eurusd_m5_v1"
)

LABEL_VERSION = (
    "eurusd_m5_direction_v1"
)


REPO_ROOT = (
    Path(__file__)
    .resolve()
    .parents[2]
)


EXPERIMENT_ROOT = (
    REPO_ROOT
    / "backend"
    / "models"
    / "experiments"
)


ENSEMBLE_DIR = (
    EXPERIMENT_ROOT
    / (
        "neural_network_eurusd_m5_v1"
        "_ensemble_11_29_47"
    )
)


REGISTRY_DIR = (
    REPO_ROOT
    / "backend"
    / "models"
    / "registry"
    / MODEL_NAME
)


def read_json(
    path
):

    with open(
        path,
        "r",
        encoding="utf-8"
    ) as file:

        return json.load(
            file
        )


def write_json(
    path,
    payload
):

    with open(
        path,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            payload,
            file,
            indent=2
        )


def sha256_file(
    path
):

    digest = hashlib.sha256()


    with open(
        path,
        "rb"
    ) as file:

        while True:

            chunk = file.read(
                1024 * 1024
            )


            if not chunk:

                break


            digest.update(
                chunk
            )


    return digest.hexdigest()


def member_directory(
    seed
):

    return (

        EXPERIMENT_ROOT

        / (
            f"{MODEL_NAME}"
            f"_seed_{seed:02d}"
        )
    )


def main():

    print(
        "=" * 78
    )

    print(
        "EUR/USD ORACLE "
        "NEURAL NETWORK V1 PROMOTION"
    )

    print(
        "=" * 78
    )


    if REGISTRY_DIR.exists():

        raise RuntimeError(

            "Production registry already exists:\n"
            f"{REGISTRY_DIR}\n\n"
            "Refusing to overwrite a frozen "
            "production model."
        )


    calibration = read_json(

        ENSEMBLE_DIR
        / "calibration.json"
    )


    final_evaluation = read_json(

        ENSEMBLE_DIR
        / "final_test_evaluation.json"
    )


    temperature = float(
        calibration[
            "temperature"
        ]
    )


    if tuple(
        calibration[
            "member_seeds"
        ]
    ) != SEEDS:

        raise RuntimeError(
            "Calibration seed contract mismatch."
        )


    if (
        final_evaluation[
            "test_set_consumed"
        ]
        is not True
    ):

        raise RuntimeError(
            "Final test evaluation is incomplete."
        )


    if (
        final_evaluation[
            "v1_frozen_after_test"
        ]
        is not True
    ):

        raise RuntimeError(
            "Seat 12 v1 is not marked frozen."
        )


    reference_metadata = read_json(

        member_directory(
            SEEDS[0]
        )
        / "metadata.json"
    )


    feature_names = tuple(
        reference_metadata[
            "features"
        ]
    )


    if (
        len(
            feature_names
        )
        != 27
    ):

        raise RuntimeError(
            "Expected exactly 27 features."
        )


    # Verify all three members share the
    # exact same data/feature contract.

    for seed in SEEDS:

        metadata = read_json(

            member_directory(
                seed
            )
            / "metadata.json"
        )


        if (
            metadata[
                "seed"
            ]
            != seed
        ):

            raise RuntimeError(
                f"Seed {seed} metadata mismatch."
            )


        if tuple(
            metadata[
                "features"
            ]
        ) != feature_names:

            raise RuntimeError(
                f"Seed {seed} feature mismatch."
            )


        if (
            metadata[
                "dataset_version"
            ]
            != DATASET_VERSION
        ):

            raise RuntimeError(
                f"Seed {seed} dataset mismatch."
            )


        if (
            metadata[
                "feature_version"
            ]
            != FEATURE_VERSION
        ):

            raise RuntimeError(
                f"Seed {seed} feature version mismatch."
            )


        if (
            metadata[
                "label_version"
            ]
            != LABEL_VERSION
        ):

            raise RuntimeError(
                f"Seed {seed} label version mismatch."
            )


    REGISTRY_DIR.mkdir(
        parents=True,
        exist_ok=False
    )


    # --------------------------------------------------------
    # COPY FROZEN MODEL MEMBERS
    # --------------------------------------------------------

    for seed in SEEDS:

        source = (

            member_directory(
                seed
            )
            / "candidate.pt"
        )


        destination = (

            REGISTRY_DIR
            / f"member_{seed:02d}.pt"
        )


        shutil.copy2(
            source,
            destination
        )


    # All members use identical train-only
    # normalization statistics.

    shutil.copy2(

        member_directory(
            SEEDS[0]
        )
        / "normalization.npz",

        REGISTRY_DIR
        / "normalization.npz"
    )


    shutil.copy2(

        member_directory(
            SEEDS[0]
        )
        / "architecture.json",

        REGISTRY_DIR
        / "architecture.json"
    )


    shutil.copy2(

        ENSEMBLE_DIR
        / "calibration.json",

        REGISTRY_DIR
        / "calibration.json"
    )


    shutil.copy2(

        ENSEMBLE_DIR
        / "final_test_evaluation.json",

        REGISTRY_DIR
        / "evaluation.json"
    )


    # --------------------------------------------------------
    # PRODUCTION METADATA
    # --------------------------------------------------------

    metadata = {

        "model_name":
            MODEL_NAME,

        "artifact_version":
            "1.0.0",

        "artifact_status":
            "PRODUCTION",

        "production_ready":
            True,

        "promoted_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "dataset_version":
            DATASET_VERSION,

        "feature_version":
            FEATURE_VERSION,

        "label_version":
            LABEL_VERSION,

        "symbol":
            "EURUSD",

        "timeframe":
            "M5",

        "feature_count":
            len(
                feature_names
            ),

        "features":
            list(
                feature_names
            ),

        "classes": [
            0,
            1,
            2
        ],

        "class_mapping": {
            "0": "short",
            "1": "neutral",
            "2": "long"
        },

        "ensemble_method":
            "mean_probability",

        "member_seeds":
            list(
                SEEDS
            ),

        "member_files": [
            f"member_{seed:02d}.pt"
            for seed
            in SEEDS
        ],

        "normalization_file":
            "normalization.npz",

        "architecture_file":
            "architecture.json",

        "calibration_file":
            "calibration.json",

        "temperature":
            temperature,

        "parameters_per_member":
            278065,

        "member_count":
            len(
                SEEDS
            ),

        "total_parameters":
            278065
            * len(
                SEEDS
            ),

        "test_accuracy":
            final_evaluation[
                "calibrated_ensemble"
            ][
                "accuracy"
            ],

        "test_macro_f1":
            final_evaluation[
                "calibrated_ensemble"
            ][
                "macro_f1"
            ],

        "test_log_loss":
            final_evaluation[
                "calibrated_ensemble"
            ][
                "log_loss"
            ]
    }


    write_json(

        REGISTRY_DIR
        / "metadata.json",

        metadata
    )


    # --------------------------------------------------------
    # SHA-256 ARTIFACT MANIFEST
    # --------------------------------------------------------

    protected_files = [

        f"member_{seed:02d}.pt"
        for seed
        in SEEDS

    ] + [

        "normalization.npz",
        "architecture.json",
        "calibration.json",
        "evaluation.json",
        "metadata.json"
    ]


    hashes = {}


    for filename in (
        protected_files
    ):

        path = (
            REGISTRY_DIR
            / filename
        )


        hashes[
            filename
        ] = sha256_file(
            path
        )


    write_json(

        REGISTRY_DIR
        / "checksums.json",

        {
            "algorithm":
                "SHA256",

            "files":
                hashes
        }
    )


    print()
    print(
        f"Production registry:"
    )

    print(
        REGISTRY_DIR
    )


    print()
    print(
        f"Members: {SEEDS}"
    )

    print(
        f"Temperature: "
        f"{temperature:.6f}"
    )

    print(
        f"Features: "
        f"{len(feature_names)}"
    )

    print(
        "Production ready: YES"
    )


    print()
    print(
        "=" * 78
    )

    print(
        "NEURAL NETWORK V1 "
        "PROMOTION COMPLETE"
    )

    print(
        "=" * 78
    )


if __name__ == "__main__":

    main()
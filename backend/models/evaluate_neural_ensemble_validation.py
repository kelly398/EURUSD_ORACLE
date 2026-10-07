from pathlib import Path
import json
import time

import numpy as np
import torch

from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    log_loss
)

from backend.models.neural_network_architecture import (
    NeuralNetworkConfig,
    OracleNeuralNetwork
)

from backend.models.neural_training_utils import (
    FeatureStandardizer,
    encode_labels
)

from backend.models.training_dataset import (
    TrainingDatasetLoader
)

from backend.models.train_neural_network_experiment import (
    confidence_analysis,
    expected_calibration_error,
    multiclass_brier_score
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

MODEL_NAME = (
    "neural_network_eurusd_m5_v1"
)

SEEDS = (
    11,
    29,
    47
)

CALIBRATION_FRACTION = (
    0.30
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


def load_member(
    seed,
    expected_feature_names,
    device
):

    directory = (
        member_directory(
            seed
        )
    )


    metadata = read_json(
        directory
        / "metadata.json"
    )


    if (
        metadata[
            "artifact_status"
        ]
        != "EXPERIMENT_ONLY"
    ):

        raise RuntimeError(
            f"Seed {seed} has unexpected "
            "artifact status."
        )


    if (
        metadata[
            "production_ready"
        ]
        is not False
    ):

        raise RuntimeError(
            f"Seed {seed} is unexpectedly "
            "marked production ready."
        )


    if (
        metadata[
            "seed"
        ]
        != seed
    ):

        raise RuntimeError(
            f"Seed metadata mismatch "
            f"for {seed}."
        )


    for key, expected in (
        (
            "dataset_version",
            DATASET_VERSION
        ),
        (
            "feature_version",
            FEATURE_VERSION
        ),
        (
            "label_version",
            LABEL_VERSION
        )
    ):

        if (
            metadata[
                key
            ]
            != expected
        ):

            raise RuntimeError(
                f"Seed {seed} "
                f"{key} mismatch."
            )


    artifact_features = tuple(
        metadata[
            "features"
        ]
    )


    if (
        artifact_features
        != expected_feature_names
    ):

        raise RuntimeError(
            f"Seed {seed} feature "
            "contract mismatch."
        )


    architecture_payload = (
        read_json(
            directory
            / "architecture.json"
        )
    )


    config = (
        NeuralNetworkConfig(
            **architecture_payload
        )
    )


    model = (
        OracleNeuralNetwork(
            config
        )
    )


    state_dict = torch.load(

        directory
        / "candidate.pt",

        map_location=
            device,

        weights_only=True
    )


    model.load_state_dict(

        state_dict,

        strict=True
    )


    model = model.to(
        device
    )


    model.eval()


    standardizer = (
        FeatureStandardizer.load(

            directory
            / "normalization.npz"
        )
    )


    if (
        standardizer.feature_names
        != expected_feature_names
    ):

        raise RuntimeError(
            f"Seed {seed} normalization "
            "feature contract mismatch."
        )


    return (
        model,
        standardizer,
        metadata
    )


def model_probabilities(
    model,
    X,
    device
):

    tensor = torch.from_numpy(
        np.asarray(
            X,
            dtype=np.float32
        )
    ).to(
        device
    )


    with torch.inference_mode():

        logits = model(
            tensor
        )


        if not torch.isfinite(
            logits
        ).all():

            raise RuntimeError(
                "Neural member produced "
                "non-finite logits."
            )


        probabilities = torch.softmax(

            logits,

            dim=1
        )


    return (

        probabilities
        .cpu()
        .numpy()
    )


def evaluate_probabilities(
    targets,
    probabilities
):

    predictions = np.argmax(
        probabilities,
        axis=1
    )


    return {

        "accuracy":
            float(
                accuracy_score(
                    targets,
                    predictions
                )
            ),

        "macro_f1":
            float(
                f1_score(
                    targets,
                    predictions,
                    average="macro",
                    zero_division=0
                )
            ),

        "log_loss":
            float(
                log_loss(

                    targets,

                    probabilities,

                    labels=[
                        0,
                        1,
                        2
                    ]
                )
            ),

        "multiclass_brier_score":
            multiclass_brier_score(

                targets,

                probabilities
            ),

        "expected_calibration_error":
            expected_calibration_error(

                targets,

                probabilities
            ),

        "confusion_matrix":
            confusion_matrix(

                targets,

                predictions,

                labels=[
                    0,
                    1,
                    2
                ]

            ).tolist(),

        "confidence_analysis":
            confidence_analysis(

                targets,

                probabilities
            )
    }


def disagreement_analysis(
    member_probabilities
):

    # shape:
    # members x samples x classes

    probabilities = np.stack(

        member_probabilities,

        axis=0
    )


    member_predictions = np.argmax(

        probabilities,

        axis=2
    )


    unanimous = np.all(

        member_predictions
        == member_predictions[
            0:1,
            :
        ],

        axis=0
    )


    all_different = (

        (
            member_predictions[0]
            != member_predictions[1]
        )

        &

        (
            member_predictions[0]
            != member_predictions[2]
        )

        &

        (
            member_predictions[1]
            != member_predictions[2]
        )
    )


    two_to_one = (

        ~unanimous

        &

        ~all_different
    )


    pair_01 = (
        member_predictions[0]
        == member_predictions[1]
    )


    pair_02 = (
        member_predictions[0]
        == member_predictions[2]
    )


    pair_12 = (
        member_predictions[1]
        == member_predictions[2]
    )


    pairwise_agreement = np.mean(

        np.concatenate(
            (
                pair_01,
                pair_02,
                pair_12
            )
        )
    )


    probability_std = np.std(

        probabilities,

        axis=0
    )


    sample_disagreement = np.mean(

        probability_std,

        axis=1
    )


    return {

        "unanimous_class_rate":
            float(
                np.mean(
                    unanimous
                )
            ),

        "two_to_one_class_rate":
            float(
                np.mean(
                    two_to_one
                )
            ),

        "all_different_class_rate":
            float(
                np.mean(
                    all_different
                )
            ),

        "mean_pairwise_class_agreement":
            float(
                pairwise_agreement
            ),

        "probability_disagreement_mean":
            float(
                np.mean(
                    sample_disagreement
                )
            ),

        "probability_disagreement_median":
            float(
                np.median(
                    sample_disagreement
                )
            ),

        "probability_disagreement_p95":
            float(
                np.percentile(
                    sample_disagreement,
                    95
                )
            ),

        "probability_disagreement_max":
            float(
                np.max(
                    sample_disagreement
                )
            )
    }


def benchmark_ensemble_latency(
    models,
    sample,
    device,
    warmup_iterations=200,
    measured_iterations=2000
):

    sample_tensor = torch.from_numpy(

        np.asarray(
            sample,
            dtype=np.float32
        )

    ).to(
        device
    )


    for model in models:

        model.eval()


    def run_once():

        probabilities = []


        for model in models:

            logits = model(
                sample_tensor
            )


            probabilities.append(

                torch.softmax(
                    logits,
                    dim=1
                )
            )


        return torch.stack(

            probabilities,

            dim=0

        ).mean(
            dim=0
        )


    with torch.inference_mode():

        for _ in range(
            warmup_iterations
        ):

            run_once()


        measurements = []


        for _ in range(
            measured_iterations
        ):

            start = (
                time.perf_counter_ns()
            )


            run_once()


            end = (
                time.perf_counter_ns()
            )


            measurements.append(

                (
                    end
                    - start
                )

                / 1_000_000.0
            )


    values = np.asarray(
        measurements,
        dtype=np.float64
    )


    return {

        "members":
            len(
                models
            ),

        "warmup_iterations":
            warmup_iterations,

        "measured_iterations":
            measured_iterations,

        "mean_ms":
            float(
                np.mean(
                    values
                )
            ),

        "median_ms":
            float(
                np.median(
                    values
                )
            ),

        "p95_ms":
            float(
                np.percentile(
                    values,
                    95
                )
            ),

        "p99_ms":
            float(
                np.percentile(
                    values,
                    99
                )
            ),

        "minimum_ms":
            float(
                np.min(
                    values
                )
            ),

        "maximum_ms":
            float(
                np.max(
                    values
                )
            )
    }


def main():

    print(
        "=" * 78
    )

    print(
        "EUR/USD ORACLE "
        "NEURAL ENSEMBLE VALIDATION"
    )

    print(
        "=" * 78
    )


    device = torch.device(
        "cpu"
    )


    loader = TrainingDatasetLoader(

        dataset_version=
            DATASET_VERSION,

        feature_version=
            FEATURE_VERSION,

        label_version=
            LABEL_VERSION
    )


    dataset = loader.load()


    feature_names = tuple(
        dataset.feature_names
    )


    X_validation = np.asarray(

        dataset.X_validation,

        dtype=np.float32
    )


    y_validation = encode_labels(
        dataset.y_validation
    )


    split_index = int(

        len(
            X_validation
        )

        * (
            1.0
            - CALIBRATION_FRACTION
        )
    )


    X_matched_raw = (
        X_validation[
            :split_index
        ]
    )


    y_matched = (
        y_validation[
            :split_index
        ]
    )


    assert (
        len(
            X_matched_raw
        )
        == 7758
    )


    print(
        f"Validation samples: "
        f"{len(X_matched_raw):,}"
    )


    print(
        f"Members: "
        f"{SEEDS}"
    )


    models = []

    standardizers = []

    member_metadata = []


    for seed in SEEDS:

        (
            model,
            standardizer,
            metadata
        ) = load_member(

            seed=
                seed,

            expected_feature_names=
                feature_names,

            device=
                device
        )


        models.append(
            model
        )


        standardizers.append(
            standardizer
        )


        member_metadata.append(
            metadata
        )


    # All ensemble members were trained
    # from the exact same training data.
    # Therefore their normalization
    # artifacts must be identical.

    reference_standardizer = (
        standardizers[0]
    )


    for (
        seed,
        standardizer
    ) in zip(
        SEEDS[1:],
        standardizers[1:]
    ):

        if not np.array_equal(

            reference_standardizer.mean,

            standardizer.mean
        ):

            raise RuntimeError(
                f"Seed {seed} normalization "
                "mean differs."
            )


        if not np.array_equal(

            reference_standardizer.scale,

            standardizer.scale
        ):

            raise RuntimeError(
                f"Seed {seed} normalization "
                "scale differs."
            )


    X_matched = (
        reference_standardizer
        .transform(

            X_matched_raw,

            feature_names=
                feature_names
        )
    )


    member_probabilities = []

    member_metrics = {}


    print()


    for (
        seed,
        model
    ) in zip(
        SEEDS,
        models
    ):

        probabilities = (
            model_probabilities(

                model=
                    model,

                X=
                    X_matched,

                device=
                    device
            )
        )


        member_probabilities.append(
            probabilities
        )


        metrics = (
            evaluate_probabilities(

                y_matched,

                probabilities
            )
        )


        member_metrics[
            str(seed)
        ] = metrics


        print(
            f"Seed {seed:02d} | "
            f"acc="
            f"{metrics['accuracy']:.6f} | "
            f"f1="
            f"{metrics['macro_f1']:.6f} | "
            f"logloss="
            f"{metrics['log_loss']:.6f}"
        )


    # ========================================================
    # PROBABILITY ENSEMBLE
    # ========================================================

    ensemble_probabilities = np.mean(

        np.stack(

            member_probabilities,

            axis=0
        ),

        axis=0
    )


    row_sums = np.sum(

        ensemble_probabilities,

        axis=1
    )


    if not np.allclose(

        row_sums,

        1.0,

        atol=1e-6
    ):

        raise RuntimeError(
            "Ensemble probabilities do "
            "not sum to one."
        )


    ensemble_metrics = (
        evaluate_probabilities(

            y_matched,

            ensemble_probabilities
        )
    )


    disagreement = (
        disagreement_analysis(

            member_probabilities
        )
    )


    latency = (
        benchmark_ensemble_latency(

            models=
                models,

            sample=
                X_matched[
                    :1
                ],

            device=
                device
        )
    )


    # ========================================================
    # SAVE EXPERIMENT RESULTS
    # ========================================================

    ENSEMBLE_DIR.mkdir(

        parents=True,

        exist_ok=True
    )


    manifest = {

        "model_name":
            MODEL_NAME,

        "artifact_status":
            "EXPERIMENT_ONLY",

        "production_ready":
            False,

        "ensemble_method":
            "mean_probability",

        "member_seeds":
            list(
                SEEDS
            ),

        "member_directories": [

            str(
                member_directory(
                    seed
                )
            )

            for seed
            in SEEDS
        ],

        "dataset_version":
            DATASET_VERSION,

        "feature_version":
            FEATURE_VERSION,

        "label_version":
            LABEL_VERSION,

        "validation_samples":
            len(
                X_matched
            ),

        "calibration_evaluated":
            False,

        "test_evaluated":
            False
    }


    write_json(

        ENSEMBLE_DIR
        / "ensemble_manifest.json",

        manifest
    )


    write_json(

        ENSEMBLE_DIR
        / "member_validation_metrics.json",

        member_metrics
    )


    write_json(

        ENSEMBLE_DIR
        / "validation_metrics.json",

        ensemble_metrics
    )


    write_json(

        ENSEMBLE_DIR
        / "disagreement_metrics.json",

        disagreement
    )


    write_json(

        ENSEMBLE_DIR
        / "latency.json",

        latency
    )


    # ========================================================
    # REPORT
    # ========================================================

    print()
    print(
        "=" * 78
    )

    print(
        "3-MEMBER ENSEMBLE"
    )

    print(
        "=" * 78
    )


    print(
        f"Accuracy: "
        f"{ensemble_metrics['accuracy']:.6f}"
    )


    print(
        f"Macro F1: "
        f"{ensemble_metrics['macro_f1']:.6f}"
    )


    print(
        f"Log loss: "
        f"{ensemble_metrics['log_loss']:.6f}"
    )


    print(
        "Brier score: "
        f"{ensemble_metrics['multiclass_brier_score']:.6f}"
    )


    print(
        "ECE: "
        f"{ensemble_metrics['expected_calibration_error']:.6f}"
    )


    print()
    print(
        "Confusion matrix "
        "[short, neutral, long]:"
    )


    for row in (
        ensemble_metrics[
            "confusion_matrix"
        ]
    ):

        print(
            row
        )


    print()
    print(
        "Confidence analysis:"
    )


    for (
        threshold,
        values
    ) in ensemble_metrics[
        "confidence_analysis"
    ].items():

        print(
            f">= {threshold}: "
            f"{values}"
        )


    print()
    print(
        "ENSEMBLE AGREEMENT"
    )


    print(
        "Unanimous: "
        f"{disagreement['unanimous_class_rate']:.4f}"
    )


    print(
        "Two-to-one: "
        f"{disagreement['two_to_one_class_rate']:.4f}"
    )


    print(
        "All different: "
        f"{disagreement['all_different_class_rate']:.4f}"
    )


    print(
        "Mean pairwise agreement: "
        f"{disagreement['mean_pairwise_class_agreement']:.4f}"
    )


    print(
        "Probability disagreement mean: "
        f"{disagreement['probability_disagreement_mean']:.6f}"
    )


    print(
        "Probability disagreement P95: "
        f"{disagreement['probability_disagreement_p95']:.6f}"
    )


    print()
    print(
        "3-member warm inference latency:"
    )


    print(
        f"Median: "
        f"{latency['median_ms']:.4f} ms"
    )


    print(
        f"P95: "
        f"{latency['p95_ms']:.4f} ms"
    )


    print(
        f"P99: "
        f"{latency['p99_ms']:.4f} ms"
    )


    print()
    print(
        "Calibration set evaluated: NO"
    )

    print(
        "Test set evaluated: NO"
    )


    print()
    print(
        f"Artifacts: "
        f"{ENSEMBLE_DIR}"
    )


    print()
    print(
        "=" * 78
    )

    print(
        "NEURAL ENSEMBLE "
        "VALIDATION COMPLETE"
    )

    print(
        "=" * 78
    )


if __name__ == "__main__":

    main()
from datetime import (
    datetime,
    timezone
)

import json
from pathlib import Path
import time

import numpy as np
import torch

from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    log_loss
)

from backend.models.neural_network_architecture import (
    NeuralNetworkConfig,
    OracleNeuralNetwork,
    count_trainable_parameters
)

from backend.models.neural_training_engine import (
    NeuralTrainingConfig,
    train_model
)

from backend.models.neural_training_utils import (
    build_data_loaders,
    prepare_neural_data,
    set_reproducible_seed
)

from backend.models.training_dataset import (
    TrainingDatasetLoader
)


# ============================================================
# FROZEN DATA CONTRACT
# ============================================================

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

SEED = 11


REPO_ROOT = (
    Path(__file__)
    .resolve()
    .parents[2]
)


EXPERIMENT_DIR = (

    REPO_ROOT
    / "backend"
    / "models"
    / "experiments"
    / f"{MODEL_NAME}_seed_{SEED:02d}"
)


# ============================================================
# HELPERS
# ============================================================

def class_distribution(
    labels
):

    counts = np.bincount(
        np.asarray(
            labels,
            dtype=np.int64
        ),
        minlength=3
    )


    return {
        "short": int(
            counts[0]
        ),
        "neutral": int(
            counts[1]
        ),
        "long": int(
            counts[2]
        )
    }


def collect_probabilities(
    model,
    data_loader,
    device
):

    model.eval()


    probability_batches = []

    target_batches = []


    with torch.inference_mode():

        for X_batch, y_batch in (
            data_loader
        ):

            X_batch = X_batch.to(
                device
            )


            logits = model(
                X_batch
            )


            if not torch.isfinite(
                logits
            ).all():

                raise RuntimeError(
                    "Non-finite logits detected "
                    "during probability collection."
                )


            probabilities = torch.softmax(
                logits,
                dim=1
            )


            probability_batches.append(

                probabilities
                .cpu()
                .numpy()
            )


            target_batches.append(

                y_batch
                .cpu()
                .numpy()
            )


    probabilities = np.concatenate(
        probability_batches,
        axis=0
    )


    targets = np.concatenate(
        target_batches,
        axis=0
    )


    return (
        probabilities,
        targets
    )


def multiclass_brier_score(
    targets,
    probabilities
):

    one_hot = np.eye(
        3,
        dtype=np.float64
    )[
        targets
    ]


    return float(

        np.mean(

            np.sum(

                (
                    probabilities
                    - one_hot
                )
                ** 2,

                axis=1
            )
        )
    )


def expected_calibration_error(
    targets,
    probabilities,
    bins=15
):

    predictions = np.argmax(
        probabilities,
        axis=1
    )


    confidence = np.max(
        probabilities,
        axis=1
    )


    correct = (
        predictions
        == targets
    ).astype(
        np.float64
    )


    boundaries = np.linspace(
        0.0,
        1.0,
        bins + 1
    )


    error = 0.0


    for index in range(
        bins
    ):

        lower = boundaries[
            index
        ]


        upper = boundaries[
            index + 1
        ]


        if (
            index
            == bins - 1
        ):

            mask = (
                (confidence >= lower)
                &
                (confidence <= upper)
            )

        else:

            mask = (
                (confidence >= lower)
                &
                (confidence < upper)
            )


        count = int(
            mask.sum()
        )


        if count == 0:

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


        weight = (
            count
            / len(
                targets
            )
        )


        error += (

            weight

            * abs(
                bin_accuracy
                - bin_confidence
            )
        )


    return float(
        error
    )


def confidence_analysis(
    targets,
    probabilities
):

    predictions = np.argmax(
        probabilities,
        axis=1
    )


    confidence = np.max(
        probabilities,
        axis=1
    )


    analysis = {}


    for threshold in (
        0.50,
        0.55,
        0.60,
        0.65,
        0.70,
        0.75,
        0.80
    ):

        mask = (
            confidence
            >= threshold
        )


        sample_count = int(
            mask.sum()
        )


        if sample_count == 0:

            analysis[
                str(
                    threshold
                )
            ] = {
                "samples": 0,
                "coverage": 0.0,
                "accuracy": None
            }

            continue


        accuracy = float(

            np.mean(

                predictions[
                    mask
                ]

                == targets[
                    mask
                ]
            )
        )


        analysis[
            str(
                threshold
            )
        ] = {
            "samples":
                sample_count,

            "coverage":
                float(
                    sample_count
                    / len(
                        targets
                    )
                ),

            "accuracy":
                accuracy
        }


    return analysis


def benchmark_latency(
    model,
    sample,
    device,
    warmup_iterations=200,
    measured_iterations=2000
):

    model.eval()


    sample = sample.to(
        device
    )


    with torch.inference_mode():

        for _ in range(
            warmup_iterations
        ):

            model(
                sample
            )


        measurements = []


        for _ in range(
            measured_iterations
        ):

            start = (
                time.perf_counter_ns()
            )


            model(
                sample
            )


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


# ============================================================
# EXPERIMENT
# ============================================================

def main():

    print(
        "=" * 78
    )

    print(
        "EUR/USD ORACLE "
        "NEURAL NETWORK EXPERIMENT"
    )

    print(
        "=" * 78
    )


    print(
        f"Model: {MODEL_NAME}"
    )

    print(
        f"Seed: {SEED}"
    )

    print(
        f"PyTorch: {torch.__version__}"
    )

    print(
        f"CPU threads: "
        f"{torch.get_num_threads()}"
    )

    print(
        f"Interop threads: "
        f"{torch.get_num_interop_threads()}"
    )


    # --------------------------------------------------------
    # REPRODUCIBILITY
    # --------------------------------------------------------

    set_reproducible_seed(
        SEED
    )


    device = torch.device(
        "cpu"
    )


    # --------------------------------------------------------
    # LOAD FROZEN DATASET
    # --------------------------------------------------------

    print()
    print(
        "Loading frozen dataset..."
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


    data = prepare_neural_data(

        dataset=

            dataset,

        calibration_fraction=
            0.30
    )


    print(
        f"Train samples: "
        f"{len(data.X_train):,}"
    )

    print(
        f"Validation samples: "
        f"{len(data.X_validation):,}"
    )

    print(
        f"Reserved calibration samples: "
        f"{len(data.X_calibration):,}"
    )

    print(
        f"Reserved test samples: "
        f"{len(data.X_test):,}"
    )


    print()
    print(
        "Training class distribution:"
    )

    print(
        class_distribution(
            data.y_train
        )
    )


    print(
        "Validation class distribution:"
    )

    print(
        class_distribution(
            data.y_validation
        )
    )


    # --------------------------------------------------------
    # DATA LOADERS
    # --------------------------------------------------------

    loaders = build_data_loaders(

        data=

            data,

        batch_size=
            512,

        seed=
            SEED
    )


    # --------------------------------------------------------
    # MODEL
    # --------------------------------------------------------

    network_config = (
        NeuralNetworkConfig(

            input_dim=
                len(
                    data.feature_names
                ),

            hidden_dim=
                128,

            expansion_dim=
                256,

            residual_blocks=
                4,

            head_dim=
                64,

            dropout=
                0.12,

            residual_dropout=
                0.06,

            num_classes=
                3
        )
    )


    model = OracleNeuralNetwork(
        network_config
    )


    parameter_count = (
        count_trainable_parameters(
            model
        )
    )


    print()
    print(
        f"Trainable parameters: "
        f"{parameter_count:,}"
    )


    if (
        parameter_count
        != 278_065
    ):

        raise RuntimeError(
            "Unexpected architecture "
            f"parameter count: "
            f"{parameter_count:,}"
        )


    # --------------------------------------------------------
    # TRAINING CONFIG
    # --------------------------------------------------------

    training_config = (
        NeuralTrainingConfig(

            max_epochs=
                80,

            learning_rate=
                4e-4,

            minimum_learning_rate=
                1e-5,

            warmup_epochs=
                4,

            weight_decay=
                3e-4,

            gradient_clip_norm=
                1.0,

            label_smoothing=
                0.02,

            patience=
                12,

            minimum_improvement=
                1e-4
        )
    )


    print()
    print(
        "Beginning chronological "
        "validation training..."
    )

    print()


    # --------------------------------------------------------
    # TRAIN
    # --------------------------------------------------------

    training_started = (
        time.perf_counter()
    )


    training_result = train_model(

        model=
            model,

        train_loader=
            loaders.train,

        validation_loader=
            loaders.validation,

        config=
            training_config,

        device=
            device,

        verbose=True
    )


    training_elapsed_seconds = (

        time.perf_counter()

        - training_started
    )


    # --------------------------------------------------------
    # VALIDATION-ONLY ANALYSIS
    # --------------------------------------------------------

    validation_probabilities, validation_targets = (
        collect_probabilities(

            model=
                model,

            data_loader=
                loaders.validation,

            device=
                device
        )
    )


    validation_predictions = (
        np.argmax(
            validation_probabilities,
            axis=1
        )
    )


    validation_accuracy = float(
        accuracy_score(
            validation_targets,
            validation_predictions
        )
    )


    validation_macro_f1 = float(
        f1_score(
            validation_targets,
            validation_predictions,
            average="macro",
            zero_division=0
        )
    )


    validation_log_loss = float(
        log_loss(

            validation_targets,

            validation_probabilities,

            labels=[
                0,
                1,
                2
            ]
        )
    )


    validation_brier = (
        multiclass_brier_score(

            validation_targets,

            validation_probabilities
        )
    )


    validation_ece = (
        expected_calibration_error(

            validation_targets,

            validation_probabilities
        )
    )


    validation_confusion = (
        confusion_matrix(

            validation_targets,

            validation_predictions,

            labels=[
                0,
                1,
                2
            ]
        )
        .tolist()
    )


    validation_report = (
        classification_report(

            validation_targets,

            validation_predictions,

            labels=[
                0,
                1,
                2
            ],

            target_names=[
                "short",
                "neutral",
                "long"
            ],

            zero_division=0,

            output_dict=True
        )
    )


    confidence_metrics = (
        confidence_analysis(

            validation_targets,

            validation_probabilities
        )
    )


    # --------------------------------------------------------
    # LIVE-LIKE SINGLE-SNAPSHOT LATENCY
    # --------------------------------------------------------

    sample = torch.from_numpy(

        data.X_validation[
            :1
        ]
    )


    latency = benchmark_latency(

        model=
            model,

        sample=
            sample,

        device=
            device
    )


    # --------------------------------------------------------
    # SAVE EXPERIMENT ARTIFACTS
    # --------------------------------------------------------

    EXPERIMENT_DIR.mkdir(

        parents=True,

        exist_ok=True
    )


    weights_path = (

        EXPERIMENT_DIR
        / "candidate.pt"
    )


    torch.save(

        model.state_dict(),

        weights_path
    )


    data.standardizer.save(

        EXPERIMENT_DIR
        / "normalization.npz"
    )


    write_json(

        EXPERIMENT_DIR
        / "architecture.json",

        network_config.as_dict()
    )


    write_json(

        EXPERIMENT_DIR
        / "training_config.json",

        training_config.as_dict()
    )


    write_json(

        EXPERIMENT_DIR
        / "training_history.json",

        training_result
        .history_as_dicts()
    )


    validation_metrics = {

        "accuracy":
            validation_accuracy,

        "macro_f1":
            validation_macro_f1,

        "log_loss":
            validation_log_loss,

        "multiclass_brier_score":
            validation_brier,

        "expected_calibration_error":
            validation_ece,

        "confusion_matrix":
            validation_confusion,

        "classification_report":
            validation_report,

        "confidence_analysis":
            confidence_metrics
    }


    write_json(

        EXPERIMENT_DIR
        / "validation_metrics.json",

        validation_metrics
    )


    write_json(

        EXPERIMENT_DIR
        / "latency.json",

        latency
    )


    metadata = {

        "model_name":
            MODEL_NAME,

        "artifact_status":
            "EXPERIMENT_ONLY",

        "production_ready":
            False,

        "seed":
            SEED,

        "created_at":
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
                data.feature_names
            ),

        "features":
            list(
                data.feature_names
            ),

        "class_mapping": {
            "0": "short",
            "1": "neutral",
            "2": "long"
        },

        "train_samples":
            len(
                data.X_train
            ),

        "validation_samples":
            len(
                data.X_validation
            ),

        "reserved_calibration_samples":
            len(
                data.X_calibration
            ),

        "reserved_test_samples":
            len(
                data.X_test
            ),

        "calibration_evaluated":
            False,

        "test_evaluated":
            False,

        "parameter_count":
            parameter_count,

        "best_epoch":
            training_result.best_epoch,

        "epochs_completed":
            training_result.epochs_completed,

        "stopped_early":
            training_result.stopped_early,

        "training_elapsed_seconds":
            training_elapsed_seconds,

        "pytorch_version":
            torch.__version__,

        "device":
            str(
                device
            ),

        "torch_threads":
            torch.get_num_threads(),

        "torch_interop_threads":
            torch.get_num_interop_threads()
    }


    write_json(

        EXPERIMENT_DIR
        / "metadata.json",

        metadata
    )


    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    print()
    print(
        "=" * 78
    )

    print(
        "EXPERIMENT RESULT"
    )

    print(
        "=" * 78
    )


    print(
        f"Best epoch: "
        f"{training_result.best_epoch}"
    )


    print(
        f"Epochs completed: "
        f"{training_result.epochs_completed}"
    )


    print(
        f"Early stopped: "
        f"{training_result.stopped_early}"
    )


    print()


    print(
        "VALIDATION ONLY"
    )


    print(
        f"Accuracy: "
        f"{validation_accuracy:.6f}"
    )


    print(
        f"Macro F1: "
        f"{validation_macro_f1:.6f}"
    )


    print(
        f"Log loss: "
        f"{validation_log_loss:.6f}"
    )


    print(
        f"Brier score: "
        f"{validation_brier:.6f}"
    )


    print(
        f"ECE: "
        f"{validation_ece:.6f}"
    )


    print()


    print(
        "Confusion matrix "
        "[short, neutral, long]:"
    )


    for row in (
        validation_confusion
    ):

        print(
            row
        )


    print()
    print(
        "Confidence analysis:"
    )


    for threshold, values in (
        confidence_metrics.items()
    ):

        print(
            f">= {threshold}: "
            f"{values}"
        )


    print()
    print(
        "Single-snapshot warm latency:"
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
        f"{EXPERIMENT_DIR}"
    )


    print()
    print(
        "=" * 78
    )

    print(
        "NEURAL EXPERIMENT COMPLETE"
    )

    print(
        "=" * 78
    )


if __name__ == "__main__":

    main()
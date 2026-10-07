import json
from pathlib import Path

import joblib
import numpy as np

from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    log_loss
)

from backend.models.training_dataset import (
    TrainingDatasetLoader
)

from backend.models.neural_training_utils import (
    encode_labels
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


XGBOOST_DIR = (
    REPO_ROOT
    / "backend"
    / "models"
    / "registry"
    / "xgboost_eurusd_m5_v1"
)


NEURAL_DIR = (
    REPO_ROOT
    / "backend"
    / "models"
    / "experiments"
    / "neural_network_eurusd_m5_v1_seed_11"
)


CALIBRATION_FRACTION = (
    0.30
)


def multiclass_brier_score(
    targets,
    probabilities
):

    one_hot = np.eye(
        3,
        dtype=np.float64
    )[targets]


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


        if index == bins - 1:

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


        error += (

            (
                count
                / len(targets)
            )

            * abs(
                bin_accuracy
                - bin_confidence
            )
        )


    return float(
        error
    )


def confidence_summary(
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


    result = {}


    for threshold in (
        0.50,
        0.60,
        0.70,
        0.80
    ):

        mask = (
            confidence
            >= threshold
        )


        samples = int(
            mask.sum()
        )


        if samples == 0:

            result[
                threshold
            ] = (
                0,
                0.0,
                None
            )

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


        result[
            threshold
        ] = (

            samples,

            float(
                samples
                / len(targets)
            ),

            accuracy
        )


    return result


def main():

    print(
        "=" * 78
    )

    print(
        "SEAT 11 vs SEAT 12 "
        "MATCHED VALIDATION COMPARISON"
    )

    print(
        "=" * 78
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


    X_matched = (
        X_validation[
            :split_index
        ]
    )


    y_matched = (
        y_validation[
            :split_index
        ]
    )


    print(
        f"Matched samples: "
        f"{len(X_matched):,}"
    )


    assert (
        len(X_matched)
        == 7758
    )


    # ========================================================
    # VERIFY XGBOOST CONTRACT
    # ========================================================

    with open(
        XGBOOST_DIR
        / "metadata.json",
        "r",
        encoding="utf-8"
    ) as file:

        metadata = json.load(
            file
        )


    assert (
        metadata[
            "dataset_version"
        ]
        == DATASET_VERSION
    )


    assert (
        metadata[
            "feature_version"
        ]
        == FEATURE_VERSION
    )


    assert (
        metadata[
            "label_version"
        ]
        == LABEL_VERSION
    )


    assert tuple(
        metadata[
            "features"
        ]
    ) == tuple(
        dataset.feature_names
    )


    model = joblib.load(

        XGBOOST_DIR
        / "model.pkl"
    )


    classes = tuple(

        int(value)

        for value
        in model.classes_
    )


    assert (
        classes
        == (
            0,
            1,
            2
        )
    )


    # ========================================================
    # MATCHED XGBOOST INFERENCE
    # ========================================================

    probabilities = (
        model.predict_proba(
            X_matched
        )
    )


    predictions = np.argmax(
        probabilities,
        axis=1
    )


    accuracy = float(
        accuracy_score(
            y_matched,
            predictions
        )
    )


    macro_f1 = float(
        f1_score(
            y_matched,
            predictions,
            average="macro",
            zero_division=0
        )
    )


    loss = float(
        log_loss(

            y_matched,

            probabilities,

            labels=[
                0,
                1,
                2
            ]
        )
    )


    brier = (
        multiclass_brier_score(

            y_matched,

            probabilities
        )
    )


    ece = (
        expected_calibration_error(

            y_matched,

            probabilities
        )
    )


    matrix = (
        confusion_matrix(

            y_matched,

            predictions,

            labels=[
                0,
                1,
                2
            ]
        )
    )


    # ========================================================
    # LOAD NEURAL RESULT
    # ========================================================

    with open(
        NEURAL_DIR
        / "validation_metrics.json",
        "r",
        encoding="utf-8"
    ) as file:

        neural = json.load(
            file
        )


    # ========================================================
    # MAJORITY BASELINE
    # ========================================================

    counts = np.bincount(
        y_matched,
        minlength=3
    )


    majority_class = int(
        np.argmax(
            counts
        )
    )


    majority_predictions = np.full(

        len(
            y_matched
        ),

        majority_class,

        dtype=np.int64
    )


    majority_accuracy = float(
        accuracy_score(
            y_matched,
            majority_predictions
        )
    )


    majority_f1 = float(
        f1_score(
            y_matched,
            majority_predictions,
            average="macro",
            zero_division=0
        )
    )


    # ========================================================
    # REPORT
    # ========================================================

    print()
    print(
        "MAJORITY BASELINE"
    )

    print(
        f"Accuracy: "
        f"{majority_accuracy:.6f}"
    )

    print(
        f"Macro F1: "
        f"{majority_f1:.6f}"
    )


    print()
    print(
        "SEAT 11 — XGBOOST"
    )

    print(
        f"Accuracy: "
        f"{accuracy:.6f}"
    )

    print(
        f"Macro F1: "
        f"{macro_f1:.6f}"
    )

    print(
        f"Log loss: "
        f"{loss:.6f}"
    )

    print(
        f"Brier: "
        f"{brier:.6f}"
    )

    print(
        f"ECE: "
        f"{ece:.6f}"
    )


    print()
    print(
        "XGBoost confusion matrix:"
    )


    for row in matrix:

        print(
            row.tolist()
        )


    print()
    print(
        "XGBoost confidence:"
    )


    for (
        threshold,
        (
            samples,
            coverage,
            threshold_accuracy
        )
    ) in confidence_summary(
        y_matched,
        probabilities
    ).items():

        print(

            f">= {threshold:.2f}: "
            f"samples={samples} "
            f"coverage={coverage:.4f} "
            f"accuracy="
            f"{threshold_accuracy}"
        )


    print()
    print(
        "SEAT 12 — NEURAL"
    )

    print(
        f"Accuracy: "
        f"{neural['accuracy']:.6f}"
    )

    print(
        f"Macro F1: "
        f"{neural['macro_f1']:.6f}"
    )

    print(
        f"Log loss: "
        f"{neural['log_loss']:.6f}"
    )

    print(
        "Brier: "
        f"{neural['multiclass_brier_score']:.6f}"
    )

    print(
        "ECE: "
        f"{neural['expected_calibration_error']:.6f}"
    )


    print()
    print(
        "NEURAL - XGBOOST"
    )

    print(
        "Accuracy difference: "
        f"{neural['accuracy'] - accuracy:+.6f}"
    )

    print(
        "Macro F1 difference: "
        f"{neural['macro_f1'] - macro_f1:+.6f}"
    )

    print(
        "Log-loss difference: "
        f"{neural['log_loss'] - loss:+.6f}"
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
        "=" * 78
    )

    print(
        "MATCHED VALIDATION "
        "COMPARISON COMPLETE"
    )

    print(
        "=" * 78
    )


if __name__ == "__main__":

    main()
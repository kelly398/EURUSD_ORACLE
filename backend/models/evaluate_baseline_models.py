import json
from pathlib import Path

import numpy as np
import joblib

from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
)

from backend.models.training_dataset import (
    TrainingDatasetLoader,
)


MODEL_DIR = Path(
    "backend/models/registry/xgboost_eurusd_m5_v1"
)

DATASET_VERSION = (
    "eurusd_m5_full_20251002_20261001_v1"
)

FEATURE_VERSION = "eurusd_m5_v1"

LABEL_VERSION = (
    "eurusd_m5_direction_v1"
)


LABEL_MAP = {
    "short": 0,
    "neutral": 1,
    "long": 2,
}


def encode_labels(labels):
    return [
        LABEL_MAP[label]
        for label in labels
    ]


def evaluate_split(
    name,
    model,
    X,
    y,
):
    y = np.array(y)
    predictions = model.predict(X)

    probabilities = model.predict_proba(X)

    confidence = probabilities.max(
        axis=1
    )

    result = {
        "accuracy": float(
            accuracy_score(
                y,
                predictions,
            )
        ),
        "macro_f1": float(
            f1_score(
                y,
                predictions,
                average="macro",
            )
        ),
        "confusion_matrix": (
            confusion_matrix(
                y,
                predictions,
            )
            .tolist()
        ),
        "confidence": {
            "average": float(
                confidence.mean()
            ),
            "thresholds": {},
        },
    }

    for threshold in (
        0.60,
        0.70,
        0.80,
    ):
        mask = confidence >= threshold

        if mask.sum() == 0:
            continue

        result["confidence"]["thresholds"][
            str(threshold)
        ] = {
            "samples": int(
                mask.sum()
            ),
            "accuracy": float(
                (
                    predictions[mask]
                    ==
                    y[mask]
                ).mean()
            ),
        }

    return result


def main():

    print("=" * 70)
    print("MODEL EVALUATION")
    print("=" * 70)


    model = joblib.load(
        MODEL_DIR / "model.pkl"
    )


    loader = TrainingDatasetLoader(
        dataset_version=DATASET_VERSION,
        feature_version=FEATURE_VERSION,
        label_version=LABEL_VERSION,
    )

    dataset = loader.load()


    validation_y = encode_labels(
        dataset.y_validation
    )

    test_y = encode_labels(
        dataset.y_test
    )


    evaluation = {
        "model": (
            "xgboost_eurusd_m5_v1"
        ),
        "dataset_version": (
            DATASET_VERSION
        ),
        "feature_version": (
            FEATURE_VERSION
        ),
        "label_version": (
            LABEL_VERSION
        ),

        "validation": evaluate_split(
            "validation",
            model,
            dataset.X_validation,
            validation_y,
        ),

        "test": evaluate_split(
            "test",
            model,
            dataset.X_test,
            test_y,
        ),
    }


    with open(
        MODEL_DIR / "evaluation.json",
        "w",
    ) as file:
        json.dump(
            evaluation,
            file,
            indent=2,
        )


    print()
    print("Validation accuracy:",
          evaluation["validation"]["accuracy"])

    print(
        "Validation macro F1:",
        evaluation["validation"]["macro_f1"],
    )

    print()

    print(
        "Test accuracy:",
        evaluation["test"]["accuracy"],
    )

    print(
        "Test macro F1:",
        evaluation["test"]["macro_f1"],
    )

    print()
    print(
        "Evaluation saved:"
    )
    print(
        MODEL_DIR / "evaluation.json"
    )

    print("=" * 70)
    print("MODEL EVALUATION: PASS")
    print("=" * 70)


if __name__ == "__main__":
    main()

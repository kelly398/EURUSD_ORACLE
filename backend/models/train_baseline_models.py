from pathlib import Path
import json
import numpy as np

from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    f1_score,
    confusion_matrix,
)
from xgboost import XGBClassifier
import joblib

from backend.models.training_dataset import (
    TrainingDatasetLoader,
)

from datetime import datetime, timezone

DATASET_VERSION = "eurusd_m5_full_20251002_20261001_v1"
FEATURE_VERSION = "eurusd_m5_v1"
LABEL_VERSION = "eurusd_m5_direction_v1"

MODEL_DIR = Path(
    "backend/models/registry/xgboost_eurusd_m5_v1"
)

MODEL_DIR.mkdir(
    parents=True,
    exist_ok=True,
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


def evaluate(
    name,
    model,
    X,
    y,
):
    predictions = model.predict(X)

    print("=" * 70)
    print(name)
    print("=" * 70)

    print(
        "Accuracy:",
        accuracy_score(
            y,
            predictions
        )
    )

    print(
        "Macro F1:",
        f1_score(
            y,
            predictions,
            average="macro",
        )
    )

    print(
        classification_report(
            y,
            predictions,
        )
    )


def main():

    loader = TrainingDatasetLoader(
        dataset_version=DATASET_VERSION,
        feature_version=FEATURE_VERSION,
        label_version=LABEL_VERSION,
    )

    dataset = loader.load()

    y_train = encode_labels(
        dataset.y_train
    )

    y_validation = encode_labels(
        dataset.y_validation
    )

    y_test = encode_labels(
        dataset.y_test
    )

    print(
        "Training samples:",
        len(dataset.X_train)
    )

    print(
        "Validation samples:",
        len(dataset.X_validation)
    )


    logistic = LogisticRegression(
        max_iter=1000,
    )

    logistic.fit(
        dataset.X_train,
        y_train,
    )

    evaluate(
        "LOGISTIC REGRESSION",
        logistic,
        dataset.X_validation,
        y_validation,
    )


    xgb = XGBClassifier(
        objective="multi:softprob",
        num_class=3,
        n_estimators=300,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
    )

    xgb.fit(
        dataset.X_train,
        y_train,
    )


    evaluate(
        "XGBOOST",
        xgb,
        dataset.X_validation,
        y_validation,
    )

    evaluate(
        "XGBOOST TEST",
        xgb,
        dataset.X_test,
        encode_labels(dataset.y_test),
    )


    # ==========================================
    # XGBOOST CONFIDENCE ANALYSIS
    # ==========================================

    probabilities = xgb.predict_proba(
        dataset.X_validation
    )

    confidence = probabilities.max(
        axis=1
    )

    predictions = probabilities.argmax(
        axis=1
    )

    print("=" * 70)
    print("HIGH CONFIDENCE CLASS ANALYSIS")
    print("=" * 70)

    for threshold in (0.6, 0.7):

        mask = confidence >= threshold

        print(
            f"\nConfidence >= {threshold}"
        )

        for class_id, name in (
            (0, "short"),
            (1, "neutral"),
            (2, "long"),
        ):

            class_mask = (
                mask
                &
                (predictions == class_id)
            )

            if class_mask.sum() == 0:
                continue

            accuracy = (
                predictions[class_mask]
                ==
                np.array(y_validation)[class_mask]
            ).mean()

            print(
                name,
                "signals=",
                class_mask.sum(),
                "accuracy=",
                round(float(accuracy), 4),
            )

        print("=" * 70)
        print("XGBOOST CONFIDENCE ANALYSIS")
        print("=" * 70)

        print(
            "Average confidence:",
            confidence.mean()
        )

        for threshold in (0.60, 0.70, 0.80):

            mask = confidence >= threshold

            if mask.sum() == 0:
                continue

            accuracy = (
                predictions[mask]
                ==
                np.array(y_validation)[mask]
            ).mean()

            print(
                f"Confidence >= {threshold}:",
                f"samples={mask.sum()}",
                f"accuracy={accuracy:.4f}"
            )

    # ==========================================
    # SAVE MODEL ARTIFACTS
    # ==========================================

    joblib.dump(
        xgb,
        MODEL_DIR / "model.pkl"
    )


    validation_predictions = xgb.predict(
        dataset.X_validation
    )

    test_predictions = xgb.predict(
        dataset.X_test
    )


    metrics = {
        "validation": {
            "accuracy": float(
                accuracy_score(
                    y_validation,
                    validation_predictions,
                )
            ),
            "macro_f1": float(
                f1_score(
                    y_validation,
                    validation_predictions,
                    average="macro",
                )
            ),
            "confusion_matrix": (
                confusion_matrix(
                    y_validation,
                    validation_predictions,
                )
                .tolist()
            ),
        },

        "test": {
            "accuracy": float(
                accuracy_score(
                    y_test,
                    test_predictions,
                )
            ),
            "macro_f1": float(
                f1_score(
                    y_test,
                    test_predictions,
                    average="macro",
                )
            ),
            "confusion_matrix": (
                confusion_matrix(
                    y_test,
                    test_predictions,
                )
                .tolist()
            ),
        },
    }


    with open(
        MODEL_DIR / "metrics.json",
        "w",
    ) as file:
        json.dump(
            metrics,
            file,
            indent=2,
        )


    feature_importance = {
        name: float(value)
        for name, value in sorted(
            zip(
                dataset.feature_names,
                xgb.feature_importances_,
            ),
            key=lambda x: x[1],
            reverse=True,
        )
    }


    with open(
        MODEL_DIR / "feature_importance.json",
        "w",
    ) as file:
        json.dump(
            feature_importance,
            file,
            indent=2,
        )


    metadata = {
        "model_name": "xgboost_eurusd_m5_v1",
        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "dataset_version": DATASET_VERSION,
        "feature_version": FEATURE_VERSION,
        "label_version": LABEL_VERSION,

        "symbol": "EURUSD",
        "timeframe": "M5",

        "feature_count": len(
            dataset.feature_names
        ),

        "features": list(
            dataset.feature_names
        ),
    }


    with open(
        MODEL_DIR / "metadata.json",
        "w",
    ) as file:
        json.dump(
            metadata,
            file,
            indent=2,
        )


    print("=" * 70)
    print("BASELINE TRAINING COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()

import json

from datetime import (
    datetime,
    timezone
)

from pathlib import Path

import joblib
import numpy as np
import torch

from sklearn.metrics import (
    accuracy_score,
    f1_score,
    log_loss
)

from backend.models.training_dataset import (
    TrainingDatasetLoader
)

from backend.models.neural_training_utils import (
    encode_labels
)

from backend.models.evaluate_neural_ensemble_validation import (
    DATASET_VERSION,
    FEATURE_VERSION,
    LABEL_VERSION,
    MODEL_NAME,
    SEEDS,
    ENSEMBLE_DIR,
    load_member,
    model_probabilities,
    evaluate_probabilities,
    disagreement_analysis
)

from backend.models.calibrate_neural_ensemble import (
    apply_temperature
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


FINAL_TEST_PATH = (
    ENSEMBLE_DIR
    / "final_test_evaluation.json"
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


def main():

    print(
        "=" * 78
    )

    print(
        "EUR/USD ORACLE "
        "SEAT 12 FINAL TEST EVALUATION"
    )

    print(
        "=" * 78
    )


    # ========================================================
    # ONE-TIME TEST GUARD
    # ========================================================

    if FINAL_TEST_PATH.exists():

        raise RuntimeError(

            "Seat 12 v1 final test evaluation "
            "already exists:\n"
            f"{FINAL_TEST_PATH}\n\n"
            "Refusing to re-evaluate the test set."
        )


    device = torch.device(
        "cpu"
    )


    # ========================================================
    # VERIFY FROZEN CALIBRATION
    # ========================================================

    calibration_path = (
        ENSEMBLE_DIR
        / "calibration.json"
    )


    if not calibration_path.exists():

        raise RuntimeError(
            "Frozen calibration artifact "
            "does not exist."
        )


    calibration = read_json(
        calibration_path
    )


    if (
        calibration[
            "model_name"
        ]
        != MODEL_NAME
    ):

        raise RuntimeError(
            "Calibration model name mismatch."
        )


    if tuple(
        calibration[
            "member_seeds"
        ]
    ) != tuple(
        SEEDS
    ):

        raise RuntimeError(
            "Calibration ensemble seed "
            "contract mismatch."
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
            calibration[
                key
            ]
            != expected
        ):

            raise RuntimeError(
                f"Calibration {key} mismatch."
            )


    temperature = float(
        calibration[
            "temperature"
        ]
    )


    if not (
        0.05
        <= temperature
        <= 20.0
    ):

        raise RuntimeError(
            "Frozen temperature is invalid."
        )


    print(
        f"Frozen temperature: "
        f"{temperature:.6f}"
    )


    print(
        f"Frozen ensemble seeds: "
        f"{SEEDS}"
    )


    # ========================================================
    # LOAD FROZEN DATASET
    # ========================================================

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


    X_test_raw = np.asarray(

        dataset.X_test,

        dtype=np.float32
    )


    y_test = encode_labels(
        dataset.y_test
    )


    if (
        len(
            X_test_raw
        )
        != 10817
    ):

        raise RuntimeError(
            "Unexpected test sample count: "
            f"{len(X_test_raw)}"
        )


    print(
        f"Untouched test samples: "
        f"{len(X_test_raw):,}"
    )


    # ========================================================
    # LOAD FROZEN NEURAL MEMBERS
    # ========================================================

    models = []

    standardizers = []


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


        print(
            f"Loaded frozen member "
            f"seed {seed}"
        )


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


    # ========================================================
    # TRANSFORM TEST USING TRAIN-ONLY STATISTICS
    # ========================================================

    X_test = (
        reference_standardizer
        .transform(

            X_test_raw,

            feature_names=
                feature_names
        )
    )


    # ========================================================
    # FROZEN ENSEMBLE INFERENCE
    # ========================================================

    member_probabilities = []


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
                    X_test,

                device=
                    device
            )
        )


        member_probabilities.append(
            probabilities
        )


    raw_ensemble_probabilities = np.mean(

        np.stack(

            member_probabilities,

            axis=0
        ),

        axis=0
    )


    raw_row_sums = np.sum(

        raw_ensemble_probabilities,

        axis=1
    )


    if not np.allclose(

        raw_row_sums,

        1.0,

        atol=1e-6
    ):

        raise RuntimeError(
            "Raw ensemble probabilities "
            "do not sum to one."
        )


    calibrated_probabilities = (
        apply_temperature(

            raw_ensemble_probabilities,

            temperature
        )
    )


    calibrated_row_sums = np.sum(

        calibrated_probabilities,

        axis=1
    )


    if not np.allclose(

        calibrated_row_sums,

        1.0,

        atol=1e-6
    ):

        raise RuntimeError(
            "Calibrated probabilities "
            "do not sum to one."
        )


    # Temperature scaling must preserve
    # class ranking.

    raw_predictions = np.argmax(

        raw_ensemble_probabilities,

        axis=1
    )


    calibrated_predictions = np.argmax(

        calibrated_probabilities,

        axis=1
    )


    if not np.array_equal(

        raw_predictions,

        calibrated_predictions
    ):

        raise RuntimeError(
            "Temperature scaling changed "
            "test class predictions."
        )


    raw_metrics = (
        evaluate_probabilities(

            y_test,

            raw_ensemble_probabilities
        )
    )


    calibrated_metrics = (
        evaluate_probabilities(

            y_test,

            calibrated_probabilities
        )
    )


    disagreement = (
        disagreement_analysis(

            member_probabilities
        )
    )


    # ========================================================
    # FROZEN SEAT 11 COMPARATOR
    # ========================================================

    xgb_metadata = read_json(

        XGBOOST_DIR
        / "metadata.json"
    )


    if tuple(
        xgb_metadata[
            "features"
        ]
    ) != feature_names:

        raise RuntimeError(
            "Seat 11 feature order mismatch."
        )


    xgb_model = joblib.load(

        XGBOOST_DIR
        / "model.pkl"
    )


    if tuple(

        int(
            value
        )

        for value
        in xgb_model.classes_

    ) != (
        0,
        1,
        2
    ):

        raise RuntimeError(
            "Unexpected XGBoost class order."
        )


    xgb_probabilities = (
        xgb_model.predict_proba(
            X_test_raw
        )
    )


    xgb_predictions = np.argmax(

        xgb_probabilities,

        axis=1
    )


    xgb_metrics = {

        "accuracy":
            float(
                accuracy_score(
                    y_test,
                    xgb_predictions
                )
            ),

        "macro_f1":
            float(
                f1_score(
                    y_test,
                    xgb_predictions,
                    average="macro",
                    zero_division=0
                )
            ),

        "log_loss":
            float(
                log_loss(

                    y_test,

                    xgb_probabilities,

                    labels=[
                        0,
                        1,
                        2
                    ]
                )
            )
    }


    # ========================================================
    # FINAL RESULT ARTIFACT
    # ========================================================

    result = {

        "model_name":
            MODEL_NAME,

        "evaluation_status":
            "FINAL_TEST_EVALUATION",

        "evaluated_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "dataset_version":
            DATASET_VERSION,

        "feature_version":
            FEATURE_VERSION,

        "label_version":
            LABEL_VERSION,

        "test_samples":
            len(
                y_test
            ),

        "ensemble_method":
            "mean_probability",

        "member_seeds":
            list(
                SEEDS
            ),

        "temperature":
            temperature,

        "class_predictions_preserved_after_calibration":
            True,

        "raw_ensemble":
            raw_metrics,

        "calibrated_ensemble":
            calibrated_metrics,

        "ensemble_disagreement":
            disagreement,

        "seat_11_xgboost_comparator":
            xgb_metrics,

        "test_set_consumed":
            True,

        "v1_frozen_after_test":
            True,

        "note": (
            "This is the final untouched "
            "test evaluation for Seat 12 v1. "
            "Future architecture or training "
            "changes must use a new model "
            "version rather than tuning v1 "
            "against these results."
        )
    }


    # Write only after all inference and
    # validation checks have completed
    # successfully.

    write_json(

        FINAL_TEST_PATH,

        result
    )


    # ========================================================
    # REPORT
    # ========================================================

    print()
    print(
        "=" * 78
    )

    print(
        "FINAL OUT-OF-SAMPLE RESULT"
    )

    print(
        "=" * 78
    )


    print()
    print(
        "SEAT 12 — RAW ENSEMBLE"
    )


    print(
        f"Accuracy: "
        f"{raw_metrics['accuracy']:.6f}"
    )

    print(
        f"Macro F1: "
        f"{raw_metrics['macro_f1']:.6f}"
    )

    print(
        f"Log loss: "
        f"{raw_metrics['log_loss']:.6f}"
    )

    print(
        "Brier: "
        f"{raw_metrics['multiclass_brier_score']:.6f}"
    )

    print(
        "ECE: "
        f"{raw_metrics['expected_calibration_error']:.6f}"
    )


    print()
    print(
        "SEAT 12 — CALIBRATED ENSEMBLE"
    )


    print(
        f"Accuracy: "
        f"{calibrated_metrics['accuracy']:.6f}"
    )

    print(
        f"Macro F1: "
        f"{calibrated_metrics['macro_f1']:.6f}"
    )

    print(
        f"Log loss: "
        f"{calibrated_metrics['log_loss']:.6f}"
    )

    print(
        "Brier: "
        f"{calibrated_metrics['multiclass_brier_score']:.6f}"
    )

    print(
        "ECE: "
        f"{calibrated_metrics['expected_calibration_error']:.6f}"
    )


    print()
    print(
        "Seat 12 confusion matrix "
        "[short, neutral, long]:"
    )


    for row in (
        calibrated_metrics[
            "confusion_matrix"
        ]
    ):

        print(
            row
        )


    print()
    print(
        "Seat 12 confidence analysis:"
    )


    for (
        threshold,
        values
    ) in calibrated_metrics[
        "confidence_analysis"
    ].items():

        print(
            f">= {threshold}: "
            f"{values}"
        )


    print()
    print(
        "SEAT 12 ENSEMBLE AGREEMENT"
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


    print()
    print(
        "SEAT 11 — XGBOOST COMPARATOR"
    )


    print(
        f"Accuracy: "
        f"{xgb_metrics['accuracy']:.6f}"
    )

    print(
        f"Macro F1: "
        f"{xgb_metrics['macro_f1']:.6f}"
    )

    print(
        f"Log loss: "
        f"{xgb_metrics['log_loss']:.6f}"
    )


    print()
    print(
        "SEAT 12 - SEAT 11"
    )


    print(
        "Accuracy difference: "
        f"{calibrated_metrics['accuracy'] - xgb_metrics['accuracy']:+.6f}"
    )

    print(
        "Macro F1 difference: "
        f"{calibrated_metrics['macro_f1'] - xgb_metrics['macro_f1']:+.6f}"
    )

    print(
        "Log-loss difference: "
        f"{calibrated_metrics['log_loss'] - xgb_metrics['log_loss']:+.6f}"
    )


    print()
    print(
        "Test set consumed: YES"
    )

    print(
        "Seat 12 v1 frozen after test: YES"
    )


    print()
    print(
        "Final test artifact:"
    )

    print(
        FINAL_TEST_PATH
    )


    print()
    print(
        "=" * 78
    )

    print(
        "SEAT 12 FINAL TEST "
        "EVALUATION COMPLETE"
    )

    print(
        "=" * 78
    )


if __name__ == "__main__":

    main()
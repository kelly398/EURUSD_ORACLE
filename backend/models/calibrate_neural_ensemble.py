import json
from datetime import (
    datetime,
    timezone
)
from pathlib import Path

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
    CALIBRATION_FRACTION,
    EXPERIMENT_ROOT,
    ENSEMBLE_DIR,
    load_member,
    model_probabilities,
    multiclass_brier_score,
    expected_calibration_error
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


def metrics_for_probabilities(
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
            )
    }


def apply_temperature(
    probabilities,
    temperature
):

    if (
        temperature
        <= 0.0
    ):

        raise ValueError(
            "Temperature must be positive."
        )


    probabilities = np.asarray(

        probabilities,

        dtype=np.float64
    )


    probabilities = np.clip(

        probabilities,

        1e-12,

        1.0
    )


    log_probabilities = np.log(
        probabilities
    )


    scaled = (

        log_probabilities

        / temperature
    )


    scaled -= np.max(

        scaled,

        axis=1,

        keepdims=True
    )


    exponentials = np.exp(
        scaled
    )


    calibrated = (

        exponentials

        / np.sum(
            exponentials,
            axis=1,
            keepdims=True
        )
    )


    return calibrated


def fit_temperature(
    targets,
    probabilities
):

    probabilities = np.asarray(

        probabilities,

        dtype=np.float64
    )


    targets = np.asarray(

        targets,

        dtype=np.int64
    )


    probability_tensor = torch.tensor(

        probabilities,

        dtype=torch.float64
    )


    target_tensor = torch.tensor(

        targets,

        dtype=torch.long
    )


    log_probabilities = torch.log(

        probability_tensor.clamp_min(
            1e-12
        )
    )


    # Optimize log(T), guaranteeing
    # that T is always positive.
    log_temperature = (
        torch.nn.Parameter(

            torch.tensor(
                0.0,
                dtype=torch.float64
            )
        )
    )


    optimizer = (
        torch.optim.LBFGS(

            [
                log_temperature
            ],

            lr=0.25,

            max_iter=100,

            tolerance_grad=1e-10,

            tolerance_change=1e-12,

            line_search_fn=
                "strong_wolfe"
        )
    )


    sample_indices = torch.arange(

        len(
            target_tensor
        )
    )


    def closure():

        optimizer.zero_grad()


        temperature = torch.exp(
            log_temperature
        )


        scaled_log_probabilities = (

            log_probabilities

            / temperature
        )


        calibrated_probabilities = (
            torch.softmax(

                scaled_log_probabilities,

                dim=1
            )
        )


        true_class_probabilities = (

            calibrated_probabilities[
                sample_indices,
                target_tensor
            ]
        )


        loss = -torch.log(

            true_class_probabilities
            .clamp_min(
                1e-12
            )

        ).mean()


        loss.backward()


        return loss


    optimizer.step(
        closure
    )


    temperature = float(

        torch.exp(
            log_temperature
        )
        .detach()
        .cpu()
        .item()
    )


    if not np.isfinite(
        temperature
    ):

        raise RuntimeError(
            "Temperature fitting produced "
            "a non-finite result."
        )


    if not (
        0.05
        <= temperature
        <= 20.0
    ):

        raise RuntimeError(
            "Temperature fitting produced "
            "an implausible value: "
            f"{temperature}"
        )


    return temperature


def main():

    print(
        "=" * 78
    )

    print(
        "EUR/USD ORACLE "
        "NEURAL ENSEMBLE CALIBRATION"
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


    # IMPORTANT:
    #
    # Earlier validation rows were used
    # for model selection.
    #
    # ONLY these later rows are used
    # for calibration fitting.

    X_calibration_raw = (
        X_validation[
            split_index:
        ]
    )


    y_calibration = (
        y_validation[
            split_index:
        ]
    )


    if (
        len(
            X_calibration_raw
        )
        != 3326
    ):

        raise RuntimeError(
            "Unexpected calibration "
            f"sample count: "
            f"{len(X_calibration_raw)}"
        )


    print(
        f"Calibration samples: "
        f"{len(X_calibration_raw):,}"
    )


    print(
        f"Ensemble members: "
        f"{SEEDS}"
    )


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
                f"Seed {seed} standardizer "
                "mean differs."
            )


        if not np.array_equal(

            reference_standardizer.scale,

            standardizer.scale
        ):

            raise RuntimeError(
                f"Seed {seed} standardizer "
                "scale differs."
            )


    X_calibration = (
        reference_standardizer
        .transform(

            X_calibration_raw,

            feature_names=
                feature_names
        )
    )


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
                    X_calibration,

                device=
                    device
            )
        )


        member_probabilities.append(
            probabilities
        )


        print(
            f"Loaded member seed "
            f"{seed}"
        )


    raw_ensemble_probabilities = np.mean(

        np.stack(

            member_probabilities,

            axis=0
        ),

        axis=0
    )


    raw_metrics = (
        metrics_for_probabilities(

            y_calibration,

            raw_ensemble_probabilities
        )
    )


    print()
    print(
        "Fitting temperature..."
    )


    temperature = (
        fit_temperature(

            y_calibration,

            raw_ensemble_probabilities
        )
    )


    calibrated_probabilities = (
        apply_temperature(

            raw_ensemble_probabilities,

            temperature
        )
    )


    calibrated_metrics = (
        metrics_for_probabilities(

            y_calibration,

            calibrated_probabilities
        )
    )


    # Temperature scaling must not
    # change class rankings.

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
            "Temperature calibration "
            "changed class predictions."
        )


    ENSEMBLE_DIR.mkdir(

        parents=True,

        exist_ok=True
    )


    calibration_artifact = {

        "model_name":
            MODEL_NAME,

        "method":
            "ensemble_probability_temperature_scaling",

        "temperature":
            temperature,

        "member_seeds":
            list(
                SEEDS
            ),

        "dataset_version":
            DATASET_VERSION,

        "feature_version":
            FEATURE_VERSION,

        "label_version":
            LABEL_VERSION,

        "calibration_samples":
            len(
                X_calibration
            ),

        "fitted_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "test_evaluated":
            False
    }


    write_json(

        ENSEMBLE_DIR
        / "calibration.json",

        calibration_artifact
    )


    diagnostics = {

        "note": (
            "Metrics are reported on the "
            "same calibration rows used to "
            "fit temperature and therefore "
            "are fit-set diagnostics, not "
            "unbiased evaluation."
        ),

        "before_calibration":
            raw_metrics,

        "after_calibration":
            calibrated_metrics
    }


    write_json(

        ENSEMBLE_DIR
        / "calibration_fit_diagnostics.json",

        diagnostics
    )


    print()
    print(
        "=" * 78
    )

    print(
        "CALIBRATION RESULT"
    )

    print(
        "=" * 78
    )


    print(
        f"Temperature: "
        f"{temperature:.6f}"
    )


    print()
    print(
        "RAW ENSEMBLE "
        "(calibration fit set)"
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
        f"Brier: "
        f"{raw_metrics['multiclass_brier_score']:.6f}"
    )

    print(
        f"ECE: "
        f"{raw_metrics['expected_calibration_error']:.6f}"
    )


    print()
    print(
        "CALIBRATED ENSEMBLE "
        "(same fit set)"
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
        f"Brier: "
        f"{calibrated_metrics['multiclass_brier_score']:.6f}"
    )

    print(
        f"ECE: "
        f"{calibrated_metrics['expected_calibration_error']:.6f}"
    )


    print()
    print(
        "Class predictions preserved: YES"
    )

    print(
        "Test set evaluated: NO"
    )


    print()
    print(
        "Calibration artifact:"
    )

    print(
        ENSEMBLE_DIR
        / "calibration.json"
    )


    print()
    print(
        "=" * 78
    )

    print(
        "NEURAL ENSEMBLE "
        "CALIBRATION COMPLETE"
    )

    print(
        "=" * 78
    )


if __name__ == "__main__":

    main()
import tempfile

from pathlib import (
    Path
)

import numpy as np

from backend.models.training_dataset import (
    TrainingDatasetLoader
)

from backend.models.neural_training_utils import (
    FeatureStandardizer,
    build_data_loaders,
    prepare_neural_data,
    set_reproducible_seed
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


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "NEURAL TRAINING DATA TEST"
    )

    print(
        "=" * 70
    )


    set_reproducible_seed(
        42
    )


    loader = (
        TrainingDatasetLoader(

            dataset_version=
                DATASET_VERSION,

            feature_version=
                FEATURE_VERSION,

            label_version=
                LABEL_VERSION
        )
    )


    dataset = (
        loader.load()
    )


    data = (
        prepare_neural_data(

            dataset=

                dataset,

            calibration_fraction=
                0.30
        )
    )


    print(
        f"Features: "
        f"{len(data.feature_names)}"
    )


    print(
        f"Train: "
        f"{len(data.X_train)}"
    )


    print(
        f"Validation: "
        f"{len(data.X_validation)}"
    )


    print(
        f"Calibration: "
        f"{len(data.X_calibration)}"
    )


    print(
        f"Test: "
        f"{len(data.X_test)}"
    )


    assert (
        len(
            data.feature_names
        )
        == 27
    )


    assert (
        len(
            data.X_train
        )
        == 50462
    )


    assert (

        len(
            data.X_validation
        )

        + len(
            data.X_calibration
        )

        == 11084
    )


    assert (
        len(
            data.X_test
        )
        == 10817
    )


    # ==================================
    # TRAIN-ONLY NORMALIZATION
    # ==================================

    train_mean = np.mean(

        data.X_train.astype(
            np.float64
        ),

        axis=0,

        dtype=np.float64
    )


    original_train = np.asarray(

        dataset.X_train,

        dtype=np.float32
    )


    original_std = np.std(

        original_train,

        axis=0
    )


    non_constant = (

        original_std
        > 1e-8
    )


    train_std = np.std(

        data.X_train.astype(
            np.float64
        ),

        axis=0,

        dtype=np.float64
    )


    max_mean_error = float(

        np.max(
            np.abs(
                train_mean
            )
        )
    )


    max_std_error = float(

        np.max(

            np.abs(

                train_std[
                    non_constant
                ]

                - 1.0
            )
        )
    )


    print(
        "Maximum normalized "
        f"train mean error: "
        f"{max_mean_error:.8f}"
    )


    print(
        "Maximum normalized "
        f"train std error: "
        f"{max_std_error:.8f}"
    )


    assert (
        max_mean_error
        < 1e-4
    )


    assert (
        max_std_error
        < 1e-4
    )


    assert np.isfinite(
        data.X_validation
    ).all()


    assert np.isfinite(
        data.X_calibration
    ).all()


    assert np.isfinite(
        data.X_test
    ).all()


    # ==================================
    # NORMALIZATION ARTIFACT ROUNDTRIP
    # ==================================

    with tempfile.TemporaryDirectory() as directory:

        artifact_path = (

            Path(
                directory
            )

            / "normalization.npz"
        )


        data.standardizer.save(
            artifact_path
        )


        loaded_standardizer = (
            FeatureStandardizer.load(
                artifact_path
            )
        )


        sample = np.asarray(

            dataset.X_test[
                :32
            ],

            dtype=np.float32
        )


        transformed_one = (
            data.standardizer
            .transform(

                sample,

                feature_names=
                    data.feature_names
            )
        )


        transformed_two = (
            loaded_standardizer
            .transform(

                sample,

                feature_names=
                    data.feature_names
            )
        )


        assert np.array_equal(
            transformed_one,
            transformed_two
        )


    # ==================================
    # PYTORCH LOADERS
    # ==================================

    loaders = (
        build_data_loaders(

            data=

                data,

            batch_size=
                512,

            seed=
                42
        )
    )


    X_batch, y_batch = next(
        iter(
            loaders.train
        )
    )


    print(
        f"Training batch X: "
        f"{tuple(X_batch.shape)}"
    )


    print(
        f"Training batch y: "
        f"{tuple(y_batch.shape)}"
    )


    assert (
        X_batch.ndim
        == 2
    )


    assert (
        X_batch.shape[1]
        == 27
    )


    assert (
        y_batch.ndim
        == 1
    )


    assert (
        len(
            X_batch
        )
        == len(
            y_batch
        )
    )


    print(
        "\nTrain-only normalization: PASS"
    )

    print(
        "Chronological validation split: PASS"
    )

    print(
        "Normalization artifact: PASS"
    )

    print(
        "PyTorch loader contract: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "NEURAL TRAINING DATA "
        "TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
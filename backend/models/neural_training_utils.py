import random

from dataclasses import (
    dataclass
)

from pathlib import (
    Path
)

import numpy as np

import torch

from torch.utils.data import (
    DataLoader,
    TensorDataset
)


LABEL_MAP = {

    "short":
        0,

    "neutral":
        1,

    "long":
        2
}


INVERSE_LABEL_MAP = {

    value:
        key

    for key, value
    in LABEL_MAP.items()
}


NORMALIZATION_EPSILON = (
    1e-8
)


def set_reproducible_seed(
    seed
):

    if not isinstance(
        seed,
        int
    ):

        raise TypeError(
            "seed must be an integer."
        )


    random.seed(
        seed
    )


    np.random.seed(
        seed
    )


    torch.manual_seed(
        seed
    )


    # CPU operations used by this model
    # should remain deterministic.
    torch.use_deterministic_algorithms(
        True,
        warn_only=True
    )


def encode_labels(
    labels
):

    encoded = []


    for label in labels:

        if (
            label
            not in LABEL_MAP
        ):

            raise ValueError(
                "Unknown neural training "
                f"label: {label}"
            )


        encoded.append(
            LABEL_MAP[
                label
            ]
        )


    return np.asarray(
        encoded,
        dtype=np.int64
    )


def as_feature_matrix(
    values,
    expected_feature_count=None
):

    matrix = np.asarray(
        values,
        dtype=np.float32
    )


    if (
        matrix.ndim
        != 2
    ):

        raise ValueError(
            "Feature matrix must have "
            "shape (samples, features)."
        )


    if (
        matrix.shape[0]
        == 0
    ):

        raise ValueError(
            "Feature matrix cannot "
            "be empty."
        )


    if (
        expected_feature_count
        is not None

        and

        matrix.shape[1]
        != expected_feature_count
    ):

        raise ValueError(
            "Feature matrix contains "
            f"{matrix.shape[1]} features; "
            f"expected "
            f"{expected_feature_count}."
        )


    if not np.isfinite(
        matrix
    ).all():

        raise ValueError(
            "Feature matrix contains "
            "NaN or infinite values."
        )


    return matrix


class FeatureStandardizer:

    def __init__(
        self,
        feature_names,
        mean,
        scale
    ):

        self.feature_names = tuple(
            feature_names
        )


        self.mean = np.asarray(
            mean,
            dtype=np.float64
        )


        self.scale = np.asarray(
            scale,
            dtype=np.float64
        )


        self._validate()


    def _validate(
        self
    ):

        feature_count = len(
            self.feature_names
        )


        if (
            feature_count
            == 0
        ):

            raise ValueError(
                "Standardizer requires "
                "feature names."
            )


        if (
            len(
                set(
                    self.feature_names
                )
            )
            != feature_count
        ):

            raise ValueError(
                "Standardizer feature "
                "names must be unique."
            )


        if (
            self.mean.shape
            != (
                feature_count,
            )
        ):

            raise ValueError(
                "Standardizer mean shape "
                "does not match features."
            )


        if (
            self.scale.shape
            != (
                feature_count,
            )
        ):

            raise ValueError(
                "Standardizer scale shape "
                "does not match features."
            )


        if not np.isfinite(
            self.mean
        ).all():

            raise ValueError(
                "Standardizer mean contains "
                "non-finite values."
            )


        if not np.isfinite(
            self.scale
        ).all():

            raise ValueError(
                "Standardizer scale contains "
                "non-finite values."
            )


        if np.any(
            self.scale <= 0.0
        ):

            raise ValueError(
                "Standardizer scale values "
                "must be positive."
            )


    @classmethod
    def fit(
        cls,
        X_train,
        feature_names
    ):

        feature_names = tuple(
            feature_names
        )


        matrix = (
            as_feature_matrix(

                X_train,

                expected_feature_count=
                    len(
                        feature_names
                    )
            )
        )


        # IMPORTANT:
        #
        # These statistics are calculated
        # from TRAIN only.
        #
        # Validation/calibration/test data
        # must never enter this method.
        mean = np.mean(

            matrix,

            axis=0,

            dtype=np.float64
        )


        standard_deviation = np.std(

            matrix,

            axis=0,

            dtype=np.float64
        )


        scale = np.where(

            standard_deviation
            > NORMALIZATION_EPSILON,

            standard_deviation,

            1.0
        )


        return cls(

            feature_names=
                feature_names,

            mean=
                mean,

            scale=
                scale
        )


    def transform(
        self,
        values,
        feature_names=None
    ):

        if (
            feature_names
            is not None
        ):

            received_names = tuple(
                feature_names
            )


            if (
                received_names
                != self.feature_names
            ):

                raise ValueError(
                    "Feature order does not "
                    "match normalization "
                    "artifact."
                )


        matrix = (
            as_feature_matrix(

                values,

                expected_feature_count=
                    len(
                        self.feature_names
                    )
            )
        )


        normalized = (

            (
                matrix.astype(
                    np.float64
                )

                - self.mean
            )

            / self.scale
        )


        normalized = (
            normalized.astype(
                np.float32
            )
        )


        if not np.isfinite(
            normalized
        ).all():

            raise ValueError(
                "Normalized feature matrix "
                "contains non-finite values."
            )


        return normalized


    def save(
        self,
        path
    ):

        path = Path(
            path
        )


        path.parent.mkdir(

            parents=True,

            exist_ok=True
        )


        np.savez(

            path,

            feature_names=
                np.asarray(
                    self.feature_names,
                    dtype=np.str_
                ),

            mean=
                self.mean,

            scale=
                self.scale
        )


    @classmethod
    def load(
        cls,
        path
    ):

        path = Path(
            path
        )


        with np.load(
            path,
            allow_pickle=False
        ) as artifact:

            feature_names = tuple(

                str(
                    value
                )

                for value
                in artifact[
                    "feature_names"
                ]
            )


            mean = (
                artifact[
                    "mean"
                ]
            )


            scale = (
                artifact[
                    "scale"
                ]
            )


        return cls(

            feature_names=
                feature_names,

            mean=
                mean,

            scale=
                scale
        )


@dataclass(
    frozen=True
)
class ChronologicalSplit:

    X_earlier: np.ndarray

    y_earlier: np.ndarray

    X_later: np.ndarray

    y_later: np.ndarray


def chronological_split(
    X,
    y,
    later_fraction=0.30
):

    if not (
        0.0
        < later_fraction
        < 1.0
    ):

        raise ValueError(
            "later_fraction must be "
            "between zero and one."
        )


    X = np.asarray(
        X
    )


    y = np.asarray(
        y
    )


    if (
        len(X)
        != len(y)
    ):

        raise ValueError(
            "Feature and label counts "
            "must match."
        )


    if (
        len(X)
        < 2
    ):

        raise ValueError(
            "Chronological split requires "
            "at least two samples."
        )


    split_index = int(

        len(X)

        * (
            1.0
            - later_fraction
        )
    )


    split_index = max(

        1,

        min(
            split_index,
            len(X) - 1
        )
    )


    return ChronologicalSplit(

        X_earlier=
            X[
                :split_index
            ],

        y_earlier=
            y[
                :split_index
            ],

        X_later=
            X[
                split_index:
            ],

        y_later=
            y[
                split_index:
            ]
    )


@dataclass(
    frozen=True
)
class PreparedNeuralData:

    feature_names: tuple[
        str,
        ...
    ]

    standardizer: FeatureStandardizer

    X_train: np.ndarray

    y_train: np.ndarray

    X_validation: np.ndarray

    y_validation: np.ndarray

    X_calibration: np.ndarray

    y_calibration: np.ndarray

    X_test: np.ndarray

    y_test: np.ndarray


def prepare_neural_data(
    dataset,
    calibration_fraction=0.30
):

    feature_names = tuple(
        dataset.feature_names
    )


    feature_count = len(
        feature_names
    )


    if (
        feature_count
        != 27
    ):

        raise ValueError(
            "ORACLE neural model "
            f"requires 27 features; "
            f"received "
            f"{feature_count}."
        )


    X_train_raw = (
        as_feature_matrix(

            dataset.X_train,

            expected_feature_count=
                feature_count
        )
    )


    X_validation_raw = (
        as_feature_matrix(

            dataset.X_validation,

            expected_feature_count=
                feature_count
        )
    )


    X_test_raw = (
        as_feature_matrix(

            dataset.X_test,

            expected_feature_count=
                feature_count
        )
    )


    y_train = (
        encode_labels(
            dataset.y_train
        )
    )


    y_validation_full = (
        encode_labels(
            dataset.y_validation
        )
    )


    y_test = (
        encode_labels(
            dataset.y_test
        )
    )


    # ==================================
    # TRAIN-ONLY NORMALIZATION
    # ==================================

    standardizer = (
        FeatureStandardizer.fit(

            X_train=
                X_train_raw,

            feature_names=
                feature_names
        )
    )


    X_train = (
        standardizer.transform(

            X_train_raw,

            feature_names=
                feature_names
        )
    )


    X_validation_full = (
        standardizer.transform(

            X_validation_raw,

            feature_names=
                feature_names
        )
    )


    X_test = (
        standardizer.transform(

            X_test_raw,

            feature_names=
                feature_names
        )
    )


    # ==================================
    # CHRONOLOGICAL VALIDATION SPLIT
    # ==================================
    #
    # Earlier validation:
    #     model selection / early stop
    #
    # Later validation:
    #     probability calibration
    #
    # Order is preserved. No shuffle.
    # ==================================

    validation_split = (
        chronological_split(

            X=
                X_validation_full,

            y=
                y_validation_full,

            later_fraction=
                calibration_fraction
        )
    )


    return PreparedNeuralData(

        feature_names=
            feature_names,

        standardizer=
            standardizer,

        X_train=
            X_train,

        y_train=
            y_train,

        X_validation=
            validation_split
            .X_earlier,

        y_validation=
            validation_split
            .y_earlier,

        X_calibration=
            validation_split
            .X_later,

        y_calibration=
            validation_split
            .y_later,

        X_test=
            X_test,

        y_test=
            y_test
    )


@dataclass(
    frozen=True
)
class NeuralDataLoaders:

    train: DataLoader

    validation: DataLoader

    calibration: DataLoader

    test: DataLoader


def _make_tensor_dataset(
    X,
    y
):

    X_tensor = torch.from_numpy(
        np.asarray(
            X,
            dtype=np.float32
        )
    )


    y_tensor = torch.from_numpy(
        np.asarray(
            y,
            dtype=np.int64
        )
    )


    return TensorDataset(
        X_tensor,
        y_tensor
    )


def build_data_loaders(
    data,
    batch_size=512,
    seed=42
):

    if (
        batch_size
        <= 0
    ):

        raise ValueError(
            "batch_size must be "
            "greater than zero."
        )


    generator = (
        torch.Generator()
    )


    generator.manual_seed(
        seed
    )


    train_dataset = (
        _make_tensor_dataset(

            data.X_train,

            data.y_train
        )
    )


    validation_dataset = (
        _make_tensor_dataset(

            data.X_validation,

            data.y_validation
        )
    )


    calibration_dataset = (
        _make_tensor_dataset(

            data.X_calibration,

            data.y_calibration
        )
    )


    test_dataset = (
        _make_tensor_dataset(

            data.X_test,

            data.y_test
        )
    )


    return NeuralDataLoaders(

        train=
            DataLoader(

                train_dataset,

                batch_size=
                    batch_size,

                shuffle=True,

                generator=
                    generator,

                num_workers=0,

                drop_last=False
            ),

        validation=
            DataLoader(

                validation_dataset,

                batch_size=
                    batch_size,

                shuffle=False,

                num_workers=0,

                drop_last=False
            ),

        calibration=
            DataLoader(

                calibration_dataset,

                batch_size=
                    batch_size,

                shuffle=False,

                num_workers=0,

                drop_last=False
            ),

        test=
            DataLoader(

                test_dataset,

                batch_size=
                    batch_size,

                shuffle=False,

                num_workers=0,

                drop_last=False
            )
    )
    
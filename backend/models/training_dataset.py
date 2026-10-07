from dataclasses import dataclass
from typing import Tuple

from sqlalchemy import select

from backend.database.connection import SessionLocal
from backend.models.dataset_models import MLTrainingSample


@dataclass(frozen=True)
class TrainingDataset:
    X_train: list
    y_train: list

    X_validation: list
    y_validation: list

    X_test: list
    y_test: list

    feature_names: Tuple[str, ...]


class TrainingDatasetLoader:

    def __init__(
        self,
        dataset_version: str,
        feature_version: str,
        label_version: str,
        symbol: str = "EURUSD",
        timeframe: str = "M5",
    ):
        self.dataset_version = dataset_version
        self.feature_version = feature_version
        self.label_version = label_version
        self.symbol = symbol
        self.timeframe = timeframe


    def load(self) -> TrainingDataset:

        statement = (
            select(MLTrainingSample)
            .where(
                MLTrainingSample.symbol == self.symbol,
                MLTrainingSample.timeframe == self.timeframe,
                MLTrainingSample.dataset_version
                == self.dataset_version,
                MLTrainingSample.feature_version
                == self.feature_version,
                MLTrainingSample.label_version
                == self.label_version,
            )
            .order_by(
                MLTrainingSample.feature_timestamp.asc()
            )
        )

        train_x = []
        train_y = []

        validation_x = []
        validation_y = []

        test_x = []
        test_y = []

        feature_names = None

        with SessionLocal() as session:

            rows = (
                session.execute(statement)
                .scalars()
                .all()
            )

        for row in rows:

            features = row.feature_json

            if feature_names is None:
                feature_names = tuple(
                    sorted(features.keys())
                )

            vector = [
                features[name]
                for name in feature_names
            ]

            label = row.target_label

            if row.split == "train":
                train_x.append(vector)
                train_y.append(label)

            elif row.split == "validation":
                validation_x.append(vector)
                validation_y.append(label)

            elif row.split == "test":
                test_x.append(vector)
                test_y.append(label)

        if feature_names is None:
            raise ValueError(
                "No training samples found."
            )

        return TrainingDataset(
            X_train=train_x,
            y_train=train_y,
            X_validation=validation_x,
            y_validation=validation_y,
            X_test=test_x,
            y_test=test_y,
            feature_names=feature_names,
        )
        
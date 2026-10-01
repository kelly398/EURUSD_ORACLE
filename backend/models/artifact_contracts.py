from datetime import datetime
from enum import Enum
from hashlib import sha256

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator
)

from backend.models.training_contracts import (
    LabelSpecification
)


class ModelFamily(str, Enum):

    RANDOM_FOREST = (
        "random_forest"
    )

    XGBOOST = (
        "xgboost"
    )

    NEURAL_NETWORK = (
        "neural_network"
    )

    HIDDEN_MARKOV_MODEL = (
        "hidden_markov_model"
    )


class ModelArtifactMetadata(BaseModel):

    model_config = ConfigDict(
        frozen=True
    )

    model_id: str = Field(
        min_length=1
    )

    model_family: ModelFamily

    model_version: str = Field(
        min_length=1
    )

    symbol: str

    timeframe: str

    feature_version: str

    feature_names: tuple[
        str,
        ...
    ]

    label_specification: (
        LabelSpecification
    )

    trained_from: datetime

    trained_through: datetime

    created_at: datetime

    training_sample_count: int = Field(
        gt=0
    )

    validation_sample_count: int = Field(
        gt=0
    )

    artifact_sha256: str | None = None


    @model_validator(
        mode="after"
    )
    def validate_metadata(self):

        if (
            self.trained_through
            <= self.trained_from
        ):

            raise ValueError(
                "trained_through must be "
                "later than trained_from."
            )


        if not self.feature_names:

            raise ValueError(
                "Model artifact requires "
                "feature names."
            )


        if (
            len(
                set(
                    self.feature_names
                )
            )
            != len(
                self.feature_names
            )
        ):

            raise ValueError(
                "Model feature names "
                "must be unique."
            )


        return self


    @property
    def feature_signature(self):

        joined = "\n".join(
            self.feature_names
        )

        return (
            sha256(
                joined.encode(
                    "utf-8"
                )
            )
            .hexdigest()
        )
        
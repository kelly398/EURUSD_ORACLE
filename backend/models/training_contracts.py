from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator
)

from backend.models.feature_contracts import (
    ModelFeatureVector
)


class TargetLabel(str, Enum):

    LONG = "long"

    SHORT = "short"

    NEUTRAL = "neutral"


class LabelSpecification(BaseModel):

    model_config = ConfigDict(
        frozen=True
    )

    label_version: str = (
        "eurusd_m5_direction_v1"
    )

    horizon_bars: int = Field(
        gt=0
    )

    timeframe_minutes: int = Field(
        gt=0
    )

    neutral_band_pips: float = Field(
        ge=0.0
    )


    @property
    def horizon_minutes(self):

        return (
            self.horizon_bars
            * self.timeframe_minutes
        )


class TrainingSample(BaseModel):

    model_config = ConfigDict(
        frozen=True
    )

    features: ModelFeatureVector

    label_specification: (
        LabelSpecification
    )

    target_label: TargetLabel

    target_move_pips: float

    feature_as_of: datetime

    target_available_at: datetime

    split: Literal["train", "validation", "test"] | None = None


    @model_validator(
        mode="after"
    )
    def validate_training_sample(self):

        if (
            self.feature_as_of
            != self.features.as_of
        ):

            raise ValueError(
                "Training sample feature "
                "timestamp does not match "
                "feature vector timestamp."
            )


        if (
            self.target_available_at
            <= self.feature_as_of
        ):

            raise ValueError(
                "Target availability must "
                "occur after feature time."
            )


        expected_minutes = (
            self.label_specification
            .horizon_minutes
        )


        actual_minutes = (

            self.target_available_at
            - self.feature_as_of

        ).total_seconds() / 60.0


        if (
            abs(
                actual_minutes
                - expected_minutes
            )
            > 1e-9
        ):

            raise ValueError(
                "Target timestamp does not "
                "match configured forecast "
                "horizon."
            )


        neutral_band = (
            self.label_specification
            .neutral_band_pips
        )


        if (
            self.target_label
            == TargetLabel.LONG

            and

            self.target_move_pips
            <= neutral_band
        ):

            raise ValueError(
                "LONG label is inconsistent "
                "with target move."
            )


        if (
            self.target_label
            == TargetLabel.SHORT

            and

            self.target_move_pips
            >= -neutral_band
        ):

            raise ValueError(
                "SHORT label is inconsistent "
                "with target move."
            )


        if (
            self.target_label
            == TargetLabel.NEUTRAL

            and

            abs(
                self.target_move_pips
            )
            > neutral_band
        ):

            raise ValueError(
                "NEUTRAL label is inconsistent "
                "with target move."
            )


        return self
        

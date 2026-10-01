from datetime import datetime
from math import isfinite

from pydantic import (
    BaseModel,
    ConfigDict,
    model_validator
)


class ModelFeatureVector(BaseModel):

    model_config = ConfigDict(
        frozen=True
    )

    symbol: str

    timeframe: str

    as_of: datetime

    feature_version: str

    feature_names: tuple[
        str,
        ...
    ]

    values: tuple[
        float,
        ...
    ]


    @model_validator(
        mode="after"
    )
    def validate_features(self):

        if not self.feature_names:

            raise ValueError(
                "Feature vector cannot be empty."
            )


        if (
            len(self.feature_names)
            != len(self.values)
        ):

            raise ValueError(
                "Feature names and values "
                "must have equal length."
            )


        if (
            len(set(self.feature_names))
            != len(self.feature_names)
        ):

            raise ValueError(
                "Feature names must be unique."
            )


        for value in self.values:

            if not isfinite(value):

                raise ValueError(
                    "Feature vector contains "
                    "NaN or infinite value."
                )


        return self


    def as_dict(self):

        return dict(
            zip(
                self.feature_names,
                self.values
            )
        )
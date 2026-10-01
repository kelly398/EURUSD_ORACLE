from datetime import timedelta

from backend.models.training_contracts import (
    LabelSpecification,
    TargetLabel,
    TrainingSample
)


class DirectionLabeler:

    def __init__(
        self,
        specification:
            LabelSpecification
    ):

        self.specification = (
            specification
        )


    def classify_move(
        self,
        move_pips
    ):

        neutral_band = (
            self.specification
            .neutral_band_pips
        )


        if (
            move_pips
            > neutral_band
        ):

            return (
                TargetLabel.LONG
            )


        if (
            move_pips
            < -neutral_band
        ):

            return (
                TargetLabel.SHORT
            )


        return (
            TargetLabel.NEUTRAL
        )


    def create_sample(
        self,
        features,
        current_close,
        future_close
    ):

        if current_close <= 0:

            raise ValueError(
                "current_close must "
                "be positive."
            )


        if future_close <= 0:

            raise ValueError(
                "future_close must "
                "be positive."
            )


        target_move_pips = (

            future_close
            - current_close

        ) * 10000.0


        target_label = (
            self.classify_move(
                target_move_pips
            )
        )


        target_available_at = (

            features.as_of

            + timedelta(
                minutes=
                    self.specification
                    .horizon_minutes
            )
        )


        return TrainingSample(

            features=
                features,

            label_specification=
                self.specification,

            target_label=
                target_label,

            target_move_pips=
                target_move_pips,

            feature_as_of=
                features.as_of,

            target_available_at=
                target_available_at
        )
        
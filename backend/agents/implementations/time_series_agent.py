from math import tanh
from statistics import (
    mean,
    pstdev
)

from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory
)


class TimeSeriesAgent(BaseAgent):

    agent_id = "time_series"

    agent_name = "Time Series"

    category = AgentCategory.STATISTICAL

    version = "1.0.0"


    def __init__(
        self,
        lookback=120
    ):

        self.lookback = lookback

        super().__init__()


    def analyze(
        self,
        context
    ):

        candles = list(
            context.candles
        )


        if (
            len(candles)
            < self.lookback + 2
        ):

            raise RuntimeError(
                "Insufficient candles for "
                "Time Series agent."
            )


        closes = [

            candle.close

            for candle
            in candles[
                -(
                    self.lookback
                    + 2
                ):
            ]
        ]


        returns = [

            current
            - previous

            for previous, current
            in zip(
                closes,
                closes[1:]
            )
        ]


        x_values = (
            returns[:-1]
        )


        y_values = (
            returns[1:]
        )


        x_mean = mean(
            x_values
        )


        y_mean = mean(
            y_values
        )


        denominator = sum(

            (
                value
                - x_mean
            ) ** 2

            for value
            in x_values
        )


        if denominator <= 0:

            slope = 0.0

        else:

            slope = (

                sum(

                    (
                        x_value
                        - x_mean
                    )

                    * (
                        y_value
                        - y_mean
                    )

                    for x_value, y_value
                    in zip(
                        x_values,
                        y_values
                    )
                )

                / denominator
            )


        intercept = (

            y_mean

            - slope
            * x_mean
        )


        latest_return = (
            returns[-1]
        )


        forecast = (

            intercept

            + slope
            * latest_return
        )


        return_std = pstdev(
            y_values
        )


        if return_std <= 0:

            normalized_forecast = 0.0

        else:

            normalized_forecast = (

                forecast
                / return_std
            )


        score = tanh(
            normalized_forecast
        )


        predictions = [

            intercept
            + slope * x_value

            for x_value
            in x_values
        ]


        total_variance = sum(

            (
                actual
                - y_mean
            ) ** 2

            for actual
            in y_values
        )


        residual_variance = sum(

            (
                actual
                - predicted
            ) ** 2

            for actual, predicted
            in zip(
                y_values,
                predictions
            )
        )


        if total_variance <= 0:

            r_squared = 0.0

        else:

            r_squared = max(

                0.0,

                1.0
                - (
                    residual_variance
                    / total_variance
                )
            )


        confidence = min(

            1.0,

            (
                0.40
                * abs(score)
            )

            + (
                0.60
                * r_squared
            )
        )


        return self.build_opinion(

            context=context,

            score=score,

            confidence=confidence,

            rationale=(
                "AR(1)-style short-horizon "
                "forecast estimated the next "
                "M5 return from recent serial "
                "dependence."
            ),

            evidence={
                "ar_slope":
                    slope,

                "ar_intercept":
                    intercept,

                "forecast_pips":
                    forecast * 10000,

                "normalized_forecast":
                    normalized_forecast,

                "r_squared":
                    r_squared,

                "lookback":
                    self.lookback
            }
        )
        
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


class MeanReversionAgent(BaseAgent):

    agent_id = "mean_reversion"

    agent_name = "Mean Reversion"

    category = AgentCategory.STATISTICAL

    version = "1.0.0"


    def __init__(
        self,
        lookback=40,
        minimum_z=1.0
    ):

        self.lookback = lookback

        self.minimum_z = (
            minimum_z
        )

        super().__init__()


    def analyze(
        self,
        context
    ):

        candles = list(
            context.candles
        )


        if len(candles) < self.lookback:

            raise RuntimeError(
                "Insufficient candles for "
                "Mean Reversion agent."
            )


        closes = [

            candle.close

            for candle
            in candles[
                -self.lookback:
            ]
        ]


        latest = (
            closes[-1]
        )


        baseline = (
            closes[:-1]
        )


        baseline_mean = mean(
            baseline
        )


        baseline_std = pstdev(
            baseline
        )


        if baseline_std <= 0:

            z_score = 0.0

        else:

            z_score = (

                latest
                - baseline_mean

            ) / baseline_std


        if (
            abs(z_score)
            < self.minimum_z
        ):

            score = 0.0


        else:

            # Positive displacement implies
            # a bearish reversion hypothesis.
            #
            # Negative displacement implies
            # a bullish reversion hypothesis.
            score = -tanh(
                z_score / 2.0
            )


        trend = (
            context.market_state.trend
        )


        if trend is None:

            trend_penalty = 1.0

        else:

            # Mean-reversion evidence becomes
            # less trustworthy as established
            # trend strength increases.
            trend_penalty = (

                1.0

                - 0.50
                * trend.strength
            )


        confidence = (

            min(
                1.0,
                abs(score)
            )

            * trend_penalty
        )


        return self.build_opinion(

            context=context,

            score=score,

            confidence=confidence,

            rationale=(
                "Latest close is "
                f"{z_score:.3f} standard "
                "deviations from its recent "
                "price baseline."
            ),

            evidence={
                "z_score":
                    z_score,

                "baseline_mean":
                    baseline_mean,

                "baseline_std":
                    baseline_std,

                "trend_penalty":
                    trend_penalty,

                "lookback":
                    self.lookback
            }
        )
        
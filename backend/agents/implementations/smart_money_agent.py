from statistics import mean

from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory
)

from backend.agents.microstructure_features import (
    MicrostructureFeatureEngine
)


class SmartMoneyAgent(
    BaseAgent
):

    agent_id = "smart_money"

    agent_name = "Smart Money"

    category = (
        AgentCategory.MICROSTRUCTURE
    )

    version = "1.0.0"


    def __init__(
        self,
        lookback=20,
        displacement_threshold=1.50,
        body_fraction_threshold=0.60
    ):

        self.lookback = lookback

        self.displacement_threshold = (
            displacement_threshold
        )

        self.body_fraction_threshold = (
            body_fraction_threshold
        )

        self.feature_engine = (
            MicrostructureFeatureEngine()
        )

        super().__init__()


    def analyze(
        self,
        context
    ):

        if not context.ticks:

            raise RuntimeError(
                "Smart Money agent requires "
                "raw tick data."
            )


        if (
            len(context.candles)
            < self.lookback + 1
        ):

            raise RuntimeError(
                "Insufficient candles for "
                "Smart Money agent."
            )


        features = (
            self.feature_engine.analyze(

                ticks=
                    context.ticks,

                as_of=
                    context.as_of
            )
        )


        latest = (
            context.candles[-1]
        )


        history = (

            context.candles[
                -(
                    self.lookback
                    + 1
                ):
                -1
            ]
        )


        average_range = mean(

            candle.high
            - candle.low

            for candle in history
        )


        average_range = max(
            average_range,
            0.000001
        )


        latest_range = (

            latest.high
            - latest.low
        )


        latest_body = abs(

            latest.close
            - latest.open
        )


        displacement_ratio = (

            latest_range
            / average_range
        )


        if latest_range <= 0:

            body_fraction = 0.0

        else:

            body_fraction = (

                latest_body
                / latest_range
            )


        prior_high = max(

            candle.high

            for candle in history
        )


        prior_low = min(

            candle.low

            for candle in history
        )


        bullish_displacement = (

            latest.close
            > latest.open

            and

            latest.close
            > prior_high

            and

            displacement_ratio
            >= self.displacement_threshold

            and

            body_fraction
            >= self.body_fraction_threshold
        )


        bearish_displacement = (

            latest.close
            < latest.open

            and

            latest.close
            < prior_low

            and

            displacement_ratio
            >= self.displacement_threshold

            and

            body_fraction
            >= self.body_fraction_threshold
        )


        if bullish_displacement:

            quote_support = max(
                0.0,
                features.update_imbalance
            )


            score = min(

                1.0,

                0.50

                + 0.25
                * min(
                    1.0,
                    (
                        displacement_ratio
                        - self.displacement_threshold
                    )
                )

                + 0.25
                * quote_support
            )


            signal = (
                "bullish_displacement"
            )


        elif bearish_displacement:

            quote_support = max(
                0.0,
                -features.update_imbalance
            )


            magnitude = min(

                1.0,

                0.50

                + 0.25
                * min(
                    1.0,
                    (
                        displacement_ratio
                        - self.displacement_threshold
                    )
                )

                + 0.25
                * quote_support
            )


            score = -(
                magnitude
            )


            signal = (
                "bearish_displacement"
            )


        else:

            score = 0.0

            signal = "none"


        activity_quality = min(
            1.0,
            features.tick_activity_ratio
        )


        if (
            features.spread_ratio
            <= 1.0
        ):

            spread_quality = 1.0

        else:

            spread_quality = min(

                1.0,

                1.0
                / features.spread_ratio
            )


        confidence = (

            abs(score)
            * activity_quality
            * spread_quality
        )


        confidence = min(
            1.0,
            confidence
        )


        return self.build_opinion(

            context=context,

            score=score,

            confidence=confidence,

            rationale=(
                "Institutional-style displacement "
                "proxy evaluated range expansion, "
                "body dominance, range breakout "
                "and synchronized quote activity. "
                f"Signal: {signal}."
            ),

            evidence={

                "signal":
                    signal,

                "displacement_ratio":
                    displacement_ratio,

                "body_fraction":
                    body_fraction,

                "prior_high":
                    prior_high,

                "prior_low":
                    prior_low,

                "update_imbalance":
                    features.update_imbalance,

                "activity_ratio":
                    features.tick_activity_ratio,

                "spread_ratio":
                    features.spread_ratio,

                "institutional_flow_observed":
                    False,

                "heuristic_proxy":
                    True
            }
        )
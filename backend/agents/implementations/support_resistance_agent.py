from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory
)


class SupportResistanceAgent(
    BaseAgent
):

    agent_id = "support_resistance"

    agent_name = "Support / Resistance"

    category = AgentCategory.TECHNICAL

    version = "1.0.0"


    def __init__(
        self,
        lookback=40,
        breakout_buffer_fraction=0.10
    ):

        self.lookback = lookback

        self.breakout_buffer_fraction = (
            breakout_buffer_fraction
        )

        super().__init__()


    @staticmethod
    def _true_ranges(
        candles
    ):

        ranges = []


        for index, candle in enumerate(
            candles
        ):

            if index == 0:

                true_range = (
                    candle.high
                    - candle.low
                )

            else:

                previous_close = (
                    candles[
                        index - 1
                    ].close
                )


                true_range = max(

                    candle.high
                    - candle.low,

                    abs(
                        candle.high
                        - previous_close
                    ),

                    abs(
                        candle.low
                        - previous_close
                    )
                )


            ranges.append(
                true_range
            )


        return ranges


    def analyze(
        self,
        context
    ):

        candles = list(
            context.candles
        )


        if (
            len(candles)
            < self.lookback + 1
        ):

            raise RuntimeError(
                "Insufficient candles for "
                "Support / Resistance agent."
            )


        latest = (
            candles[-1]
        )


        history = (

            candles[
                -(
                    self.lookback
                    + 1
                ):
                -1
            ]
        )


        resistance = max(

            candle.high

            for candle in history
        )


        support = min(

            candle.low

            for candle in history
        )


        ranges = (
            self._true_ranges(
                candles[-21:]
            )
        )


        atr = (

            sum(
                ranges[-20:]
            )

            / len(
                ranges[-20:]
            )
        )


        buffer = (

            atr
            * self.breakout_buffer_fraction
        )


        # ==================================
        # BREAKOUT ABOVE RESISTANCE
        # ==================================

        if (
            latest.close
            > resistance + buffer
        ):

            distance = (

                latest.close
                - resistance

            )


            score = min(

                1.0,

                distance
                / max(
                    atr,
                    0.000001
                )
            )


            score = max(
                0.25,
                score
            )


            rationale = (
                "Latest completed candle "
                "closed above historical "
                "resistance."
            )


        # ==================================
        # BREAKDOWN BELOW SUPPORT
        # ==================================

        elif (
            latest.close
            < support - buffer
        ):

            distance = (

                support
                - latest.close
            )


            magnitude = min(

                1.0,

                distance
                / max(
                    atr,
                    0.000001
                )
            )


            score = -max(
                0.25,
                magnitude
            )


            rationale = (
                "Latest completed candle "
                "closed below historical "
                "support."
            )


        # ==================================
        # INSIDE ESTABLISHED RANGE
        # ==================================

        else:

            score = 0.0


            rationale = (
                "Latest completed price "
                "remains inside established "
                "support and resistance."
            )


        confidence = min(
            1.0,
            abs(score)
        )


        return self.build_opinion(

            context=context,

            score=score,

            confidence=confidence,

            rationale=rationale,

            evidence={

                "support":
                    support,

                "resistance":
                    resistance,

                "latest_close":
                    latest.close,

                "atr":
                    atr,

                "breakout_buffer":
                    buffer,

                "lookback":
                    self.lookback
            }
        )
        
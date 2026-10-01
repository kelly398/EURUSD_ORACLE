from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory
)


class FibonacciAgent(BaseAgent):

    agent_id = "fibonacci"

    agent_name = "Fibonacci"

    category = AgentCategory.TECHNICAL

    version = "1.0.0"


    def __init__(
        self,
        lookback=60
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


        if len(candles) < self.lookback:

            raise RuntimeError(
                "Insufficient candles for "
                "Fibonacci agent."
            )


        recent = (
            candles[
                -self.lookback:
            ]
        )


        structure = (
            context.market_state.structure
        )


        if structure is None:

            raise RuntimeError(
                "Market structure is unavailable."
            )


        highest_index = max(

            range(
                len(recent)
            ),

            key=lambda index:
                recent[index].high
        )


        lowest_index = min(

            range(
                len(recent)
            ),

            key=lambda index:
                recent[index].low
        )


        highest = (
            recent[
                highest_index
            ].high
        )


        lowest = (
            recent[
                lowest_index
            ].low
        )


        price_range = (
            highest
            - lowest
        )


        latest_close = (
            recent[-1].close
        )


        if price_range <= 0:

            score = 0.0
            retracement = None


        elif (
            structure.label
            in (
                "higher_highs_higher_lows",
                "bullish_transition"
            )
            and lowest_index
            < highest_index
        ):

            retracement = (

                highest
                - latest_close

            ) / price_range


            # Preferred bullish
            # retracement region:
            # 38.2% -> 61.8%.
            if (
                0.382
                <= retracement
                <= 0.618
            ):

                distance_from_half = abs(
                    retracement
                    - 0.5
                )


                score = (

                    1.0

                    - min(
                        1.0,
                        distance_from_half
                        / 0.118
                    )
                )


                score = max(
                    0.25,
                    score
                )

            else:

                score = 0.0


        elif (
            structure.label
            in (
                "lower_highs_lower_lows",
                "bearish_transition"
            )
            and highest_index
            < lowest_index
        ):

            retracement = (

                latest_close
                - lowest

            ) / price_range


            if (
                0.382
                <= retracement
                <= 0.618
            ):

                distance_from_half = abs(
                    retracement
                    - 0.5
                )


                magnitude = (

                    1.0

                    - min(
                        1.0,
                        distance_from_half
                        / 0.118
                    )
                )


                score = -max(
                    0.25,
                    magnitude
                )

            else:

                score = 0.0


        else:

            score = 0.0
            retracement = None


        confidence = min(
            1.0,
            abs(score)
        )


        return self.build_opinion(

            context=context,

            score=score,

            confidence=confidence,

            rationale=(
                "Fibonacci analysis evaluated "
                "the latest completed price "
                "against the dominant recent "
                "swing and confirmed structure."
            ),

            evidence={

                "structure":
                    structure.label,

                "swing_high":
                    highest,

                "swing_low":
                    lowest,

                "latest_close":
                    latest_close,

                "retracement":
                    retracement,

                "lookback":
                    self.lookback
            }
        )
        
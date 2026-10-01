from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory
)


class PatternRecognitionAgent(
    BaseAgent
):

    agent_id = "pattern_recognition"

    agent_name = "Pattern Recognition"

    category = AgentCategory.TECHNICAL

    version = "1.0.0"


    def analyze(
        self,
        context
    ):

        candles = list(
            context.candles
        )


        if len(candles) < 3:

            raise RuntimeError(
                "Insufficient candles for "
                "Pattern Recognition agent."
            )


        previous = (
            candles[-2]
        )

        latest = (
            candles[-1]
        )


        latest_body = abs(
            latest.close
            - latest.open
        )


        latest_range = (
            latest.high
            - latest.low
        )


        upper_wick = (

            latest.high

            - max(
                latest.open,
                latest.close
            )
        )


        lower_wick = (

            min(
                latest.open,
                latest.close
            )

            - latest.low
        )


        bullish_engulfing = (

            previous.close
            < previous.open

            and

            latest.close
            > latest.open

            and

            latest.open
            <= previous.close

            and

            latest.close
            >= previous.open
        )


        bearish_engulfing = (

            previous.close
            > previous.open

            and

            latest.close
            < latest.open

            and

            latest.open
            >= previous.close

            and

            latest.close
            <= previous.open
        )


        bullish_pin = (

            latest_range > 0

            and

            lower_wick
            >= 2.0
            * max(
                latest_body,
                0.000001
            )

            and

            upper_wick
            <= lower_wick
            * 0.50
        )


        bearish_pin = (

            latest_range > 0

            and

            upper_wick
            >= 2.0
            * max(
                latest_body,
                0.000001
            )

            and

            lower_wick
            <= upper_wick
            * 0.50
        )


        if bullish_engulfing:

            score = 0.80
            pattern = "bullish_engulfing"


        elif bearish_engulfing:

            score = -0.80
            pattern = "bearish_engulfing"


        elif bullish_pin:

            score = 0.55
            pattern = "bullish_pin_bar"


        elif bearish_pin:

            score = -0.55
            pattern = "bearish_pin_bar"


        else:

            score = 0.0
            pattern = "none"


        confidence = min(
            1.0,
            abs(score)
        )


        return self.build_opinion(

            context=context,

            score=score,

            confidence=confidence,

            rationale=(
                "Pattern Recognition found "
                f"{pattern} on the latest "
                "completed candle sequence."
            ),

            evidence={

                "pattern":
                    pattern,

                "bullish_engulfing":
                    bullish_engulfing,

                "bearish_engulfing":
                    bearish_engulfing,

                "bullish_pin":
                    bullish_pin,

                "bearish_pin":
                    bearish_pin
            }
        )
        
from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory
)


class PriceActionAgent(BaseAgent):

    agent_id = "price_action"

    agent_name = "Price Action"

    category = AgentCategory.TECHNICAL

    version = "1.0.0"


    def __init__(
        self,
        lookback=8
    ):

        self.lookback = lookback

        super().__init__()


    @staticmethod
    def _clamp(
        value,
        minimum=-1.0,
        maximum=1.0
    ):

        return max(
            minimum,
            min(
                maximum,
                value
            )
        )


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
                "Price Action agent."
            )


        recent = (
            candles[
                -self.lookback:
            ]
        )


        # ==================================
        # 1. BODY DIRECTION PRESSURE
        # ==================================

        signed_body = sum(

            candle.close
            - candle.open

            for candle in recent
        )


        total_range = sum(

            candle.high
            - candle.low

            for candle in recent
        )


        if total_range <= 0:

            body_score = 0.0

        else:

            body_score = (
                signed_body
                / total_range
            )


        body_score = (
            self._clamp(
                body_score
            )
        )


        # ==================================
        # 2. LATEST CLOSE LOCATION
        # ==================================

        latest = (
            recent[-1]
        )


        latest_range = (
            latest.high
            - latest.low
        )


        if latest_range <= 0:

            close_location_score = 0.0

        else:

            close_location = (

                latest.close
                - latest.low

            ) / latest_range


            # 0   -> -1
            # 0.5 ->  0
            # 1   -> +1
            close_location_score = (

                2.0
                * close_location

                - 1.0
            )


        close_location_score = (
            self._clamp(
                close_location_score
            )
        )


        # ==================================
        # 3. RECENT CLOSE PROGRESSION
        # ==================================

        positive = 0
        negative = 0


        for previous, current in zip(
            recent,
            recent[1:]
        ):

            if (
                current.close
                > previous.close
            ):

                positive += 1


            elif (
                current.close
                < previous.close
            ):

                negative += 1


        directional = (
            positive
            + negative
        )


        if directional == 0:

            progression_score = 0.0

        else:

            progression_score = (

                positive
                - negative

            ) / directional


        # ==================================
        # COMPOSITE
        # ==================================

        score = (

            0.45
            * body_score

            + 0.30
            * close_location_score

            + 0.25
            * progression_score
        )


        score = (
            self._clamp(
                score
            )
        )


        confidence = min(
            1.0,
            abs(score)
        )


        return self.build_opinion(

            context=context,

            score=score,

            confidence=confidence,

            rationale=(
                "Recent candle bodies, closing "
                "location and close progression "
                "produce a price-action score of "
                f"{score:.4f}."
            ),

            evidence={

                "body_score":
                    body_score,

                "close_location_score":
                    close_location_score,

                "progression_score":
                    progression_score,

                "lookback":
                    self.lookback
            }
        )
        
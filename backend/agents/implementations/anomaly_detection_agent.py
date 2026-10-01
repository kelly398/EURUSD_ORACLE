from statistics import (
    mean,
    pstdev
)

from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory,
    AgentSignalType
)


class AnomalyDetectionAgent(
    BaseAgent
):

    agent_id = "anomaly_detection"

    agent_name = "Anomaly Detection"

    category = AgentCategory.STATISTICAL

    signal_type = (
        AgentSignalType.CONTEXTUAL
    )

    version = "1.0.0"


    def __init__(
        self,
        lookback=100
    ):

        self.lookback = lookback

        super().__init__()


    @staticmethod
    def _true_ranges(
        candles
    ):

        values = []


        for index, candle in enumerate(
            candles
        ):

            if index == 0:

                value = (
                    candle.high
                    - candle.low
                )

            else:

                previous_close = (
                    candles[
                        index - 1
                    ].close
                )


                value = max(

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


            values.append(
                value
            )


        return values


    @staticmethod
    def _positive_z(
        latest,
        baseline
    ):

        baseline_mean = mean(
            baseline
        )


        baseline_std = pstdev(
            baseline
        )


        if baseline_std <= 0:

            if latest > baseline_mean:
                return 5.0

            return 0.0


        return max(

            0.0,

            (
                latest
                - baseline_mean
            )

            / baseline_std
        )


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
                "Anomaly Detection agent."
            )


        recent = (
            candles[
                -(
                    self.lookback
                    + 2
                ):
            ]
        )


        true_ranges = (
            self._true_ranges(
                recent
            )
        )


        closes = [

            candle.close
            for candle in recent
        ]


        absolute_returns = [

            abs(
                current
                - previous
            )

            for previous, current
            in zip(
                closes,
                closes[1:]
            )
        ]


        range_z = (
            self._positive_z(

                true_ranges[-1],

                true_ranges[
                    -(
                        self.lookback
                        + 1
                    ):
                    -1
                ]
            )
        )


        return_z = (
            self._positive_z(

                absolute_returns[-1],

                absolute_returns[
                    -(
                        self.lookback
                        + 1
                    ):
                    -1
                ]
            )
        )


        anomaly_score = max(
            range_z,
            return_z
        )


        if anomaly_score >= 3.0:

            anomaly_state = "extreme"


        elif anomaly_score >= 2.0:

            anomaly_state = "elevated"


        else:

            anomaly_state = "normal"


        # An anomaly is risk/context
        # information, not inherently
        # bullish or bearish.
        score = 0.0


        confidence = min(
            1.0,
            anomaly_score / 3.0
        )


        return self.build_opinion(

            context=context,

            score=score,

            confidence=confidence,

            rationale=(
                "Latest range and absolute "
                "return were compared with "
                "their recent statistical "
                "distributions. "
                f"Anomaly state: "
                f"{anomaly_state}."
            ),

            evidence={
                "anomaly_state":
                    anomaly_state,

                "anomaly_score":
                    anomaly_score,

                "range_z":
                    range_z,

                "absolute_return_z":
                    return_z,

                "directional_signal":
                    False,

                "lookback":
                    self.lookback
            }
        )
        
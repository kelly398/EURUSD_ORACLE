from statistics import median

from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory
)


class StatisticalProbabilityAgent(
    BaseAgent
):

    agent_id = "statistical_probability"

    agent_name = "Statistical Probability"

    category = AgentCategory.STATISTICAL

    version = "1.0.0"


    def __init__(
        self,
        lookback=180,
        fast_window=3,
        slow_window=12,
        minimum_matches=8
    ):

        self.lookback = lookback
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.minimum_matches = minimum_matches

        super().__init__()


    @staticmethod
    def _state(
        value,
        threshold
    ):

        if value > threshold:
            return 1

        if value < -threshold:
            return -1

        return 0


    def analyze(
        self,
        context
    ):

        candles = list(
            context.candles[
                -self.lookback:
            ]
        )


        required = (
            self.slow_window
            + 20
        )


        if len(candles) < required:

            raise RuntimeError(
                "Insufficient candles for "
                "Statistical Probability agent."
            )


        closes = [
            candle.close
            for candle in candles
        ]


        one_bar_moves = [

            current - previous

            for previous, current
            in zip(
                closes,
                closes[1:]
            )
        ]


        absolute_moves = [
            abs(move)
            for move in one_bar_moves
            if move != 0
        ]


        if absolute_moves:

            state_threshold = (
                median(
                    absolute_moves
                )
                * 0.5
            )

        else:

            state_threshold = (
                0.000001
            )


        outcome_threshold = (
            state_threshold
            * 0.25
        )


        latest_index = (
            len(closes) - 1
        )


        current_fast = (

            closes[latest_index]
            - closes[
                latest_index
                - self.fast_window
            ]
        )


        current_slow = (

            closes[latest_index]
            - closes[
                latest_index
                - self.slow_window
            ]
        )


        current_fast_state = (
            self._state(
                current_fast,
                state_threshold
            )
        )


        current_slow_state = (
            self._state(
                current_slow,
                state_threshold
            )
        )


        strict_outcomes = []
        relaxed_outcomes = []
        all_outcomes = []


        for index in range(
            self.slow_window,
            len(closes) - 1
        ):

            historical_fast = (

                closes[index]
                - closes[
                    index
                    - self.fast_window
                ]
            )


            historical_slow = (

                closes[index]
                - closes[
                    index
                    - self.slow_window
                ]
            )


            fast_state = (
                self._state(
                    historical_fast,
                    state_threshold
                )
            )


            slow_state = (
                self._state(
                    historical_slow,
                    state_threshold
                )
            )


            next_move = (

                closes[
                    index + 1
                ]
                - closes[index]
            )


            if (
                next_move
                > outcome_threshold
            ):

                outcome = 1


            elif (
                next_move
                < -outcome_threshold
            ):

                outcome = -1


            else:

                outcome = 0


            all_outcomes.append(
                outcome
            )


            if (
                fast_state
                == current_fast_state
            ):

                relaxed_outcomes.append(
                    outcome
                )


            if (
                fast_state
                == current_fast_state

                and

                slow_state
                == current_slow_state
            ):

                strict_outcomes.append(
                    outcome
                )


        if (
            len(strict_outcomes)
            >= self.minimum_matches
        ):

            outcomes = strict_outcomes
            match_mode = "fast_and_slow"


        elif (
            len(relaxed_outcomes)
            >= self.minimum_matches
        ):

            outcomes = relaxed_outcomes
            match_mode = "fast_only"


        else:

            outcomes = all_outcomes
            match_mode = "unconditional_fallback"


        if not outcomes:

            raise RuntimeError(
                "No historical probability "
                "observations available."
            )


        total = len(outcomes)


        up_count = sum(
            1
            for value in outcomes
            if value > 0
        )


        down_count = sum(
            1
            for value in outcomes
            if value < 0
        )


        flat_count = (

            total
            - up_count
            - down_count
        )


        probability_up = (
            up_count
            / total
        )


        probability_down = (
            down_count
            / total
        )


        probability_flat = (
            flat_count
            / total
        )


        score = (

            probability_up
            - probability_down
        )


        sample_factor = min(
            1.0,
            total / 30.0
        )


        edge_factor = min(
            1.0,
            abs(score) / 0.35
        )


        confidence = (

            sample_factor
            * edge_factor
        )


        return self.build_opinion(

            context=context,

            score=score,

            confidence=confidence,

            rationale=(
                "Empirical historical analogues "
                f"produce P(up)="
                f"{probability_up:.3f}, "
                f"P(down)="
                f"{probability_down:.3f}."
            ),

            evidence={
                "probability_up":
                    probability_up,

                "probability_down":
                    probability_down,

                "probability_flat":
                    probability_flat,

                "sample_size":
                    total,

                "match_mode":
                    match_mode,

                "fast_state":
                    current_fast_state,

                "slow_state":
                    current_slow_state
            }
        )
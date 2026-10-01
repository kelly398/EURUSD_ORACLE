from abc import ABC
from datetime import timedelta
from math import tanh
from statistics import mean

from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory
)

from backend.market_state.session_engine import (
    SessionEngine
)


class SessionBehaviorAgent(
    BaseAgent,
    ABC
):

    category = AgentCategory.SESSION

    session_name = ""

    minimum_session_candles = 3


    def __init__(self):

        self.session_engine = (
            SessionEngine()
        )

        super().__init__()


    def _session_active(
        self,
        as_of
    ):

        result = (
            self.session_engine.analyze(
                as_of
            )
        )

        return (
            self.session_name
            in result.active_sessions
        )


    def _current_session_candles(
        self,
        context
    ):

        selected = []


        current_session = (
            self.session_engine.analyze(
                context.as_of
            )
        )


        if self.session_name == "london":

            current_local_date = (
                current_session
                .london_time
                .date()
            )


        elif self.session_name == "new_york":

            current_local_date = (
                current_session
                .new_york_time
                .date()
            )


        else:

            raise RuntimeError(
                "Unsupported session name."
            )


        for candle in context.candles:

            # Candle timestamps represent
            # OPEN time.
            candle_close = (

                candle.timestamp

                + timedelta(
                    minutes=5
                )
            )


            session_result = (
                self.session_engine.analyze(
                    candle_close
                )
            )


            if (
                self.session_name
                not in session_result.active_sessions
            ):

                continue


            if self.session_name == "london":

                candle_local_date = (
                    session_result
                    .london_time
                    .date()
                )


            else:

                candle_local_date = (
                    session_result
                    .new_york_time
                    .date()
                )


            if (
                candle_local_date
                == current_local_date
            ):

                selected.append(
                    candle
                )


        return selected


    @staticmethod
    def _true_range(
        candle,
        previous_close
    ):

        return max(

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


    def analyze(
        self,
        context
    ):

        # ==================================
        # SESSION MUST CURRENTLY BE ACTIVE
        # ==================================

        if not self._session_active(
            context.as_of
        ):

            return self.build_opinion(

                context=context,

                score=0.0,

                confidence=0.0,

                rationale=(
                    f"{self.agent_name} is "
                    "outside its active trading "
                    "session and abstains."
                ),

                evidence={
                    "session":
                        self.session_name,

                    "session_active":
                        False,

                    "session_candle_count":
                        0
                }
            )


        session_candles = (
            self._current_session_candles(
                context
            )
        )


        if (
            len(session_candles)
            < self.minimum_session_candles
        ):

            return self.build_opinion(

                context=context,

                score=0.0,

                confidence=0.0,

                rationale=(
                    f"{self.agent_name} is active, "
                    "but insufficient completed "
                    "session candles are available."
                ),

                evidence={
                    "session":
                        self.session_name,

                    "session_active":
                        True,

                    "session_candle_count":
                        len(
                            session_candles
                        )
                }
            )


        # ==================================
        # SESSION NET MOVE
        # ==================================

        session_open = (
            session_candles[0].open
        )


        session_close = (
            session_candles[-1].close
        )


        net_move = (

            session_close
            - session_open
        )


        # ==================================
        # AVERAGE TRUE RANGE
        # ==================================

        true_ranges = []


        for index, candle in enumerate(
            session_candles
        ):

            if index == 0:

                true_range = (
                    candle.high
                    - candle.low
                )

            else:

                true_range = (
                    self._true_range(

                        candle,

                        session_candles[
                            index - 1
                        ].close
                    )
                )


            true_ranges.append(
                true_range
            )


        average_range = max(

            mean(
                true_ranges
            ),

            0.000001
        )


        normalized_move = (

            net_move
            / average_range
        )


        move_score = tanh(

            normalized_move
            / 3.0
        )


        # ==================================
        # DIRECTIONAL PERSISTENCE
        # ==================================

        up_closes = 0
        down_closes = 0


        for previous, current in zip(
            session_candles,
            session_candles[1:]
        ):

            if (
                current.close
                > previous.close
            ):

                up_closes += 1


            elif (
                current.close
                < previous.close
            ):

                down_closes += 1


        directional = (

            up_closes
            + down_closes
        )


        if directional == 0:

            persistence_score = 0.0

        else:

            persistence_score = (

                up_closes
                - down_closes

            ) / directional


        # ==================================
        # RECENT SESSION IMPULSE
        # ==================================

        recent = (
            session_candles[-3:]
        )


        recent_move = (

            recent[-1].close
            - recent[0].open
        )


        recent_score = tanh(

            recent_move
            / (
                average_range
                * 2.0
            )
        )


        # ==================================
        # COMPOSITE
        # ==================================

        score = (

            0.50
            * move_score

            + 0.30
            * persistence_score

            + 0.20
            * recent_score
        )


        score = max(
            -1.0,
            min(
                1.0,
                score
            )
        )


        # Confidence increases as the
        # session develops but is capped
        # by actual signal strength.
        sample_factor = min(

            1.0,

            len(
                session_candles
            ) / 12.0
        )


        confidence = min(

            1.0,

            abs(score)
            * sample_factor
        )


        return self.build_opinion(

            context=context,

            score=score,

            confidence=confidence,

            rationale=(
                f"{self.agent_name} evaluated "
                "the current session's net move, "
                "directional persistence and "
                "recent M5 impulse."
            ),

            evidence={

                "session":
                    self.session_name,

                "session_active":
                    True,

                "session_candle_count":
                    len(
                        session_candles
                    ),

                "session_move_pips":
                    net_move
                    * 10000.0,

                "move_score":
                    move_score,

                "persistence_score":
                    persistence_score,

                "recent_score":
                    recent_score
            }
        )
        
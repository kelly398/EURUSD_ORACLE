from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from backend.market_state.schemas import (
    CandleBar,
    StructureState
)


@dataclass(frozen=True)
class SwingPoint:
    index: int
    timestamp: datetime
    price: float
    kind: str


@dataclass(frozen=True)
class StructureEngineResult:
    state: StructureState

    structure_score: float

    high_relation: str
    low_relation: str

    previous_swing_high: SwingPoint | None
    latest_swing_high: SwingPoint | None

    previous_swing_low: SwingPoint | None
    latest_swing_low: SwingPoint | None

    swing_high_count: int
    swing_low_count: int

    average_true_range_pips: float
    tolerance_pips: float


class MarketStructureEngine:

    def __init__(
        self,
        left_bars=2,
        right_bars=2,
        atr_window=20,
        tolerance_atr_fraction=0.10,
        minimum_candles=60
    ):

        self.left_bars = left_bars
        self.right_bars = right_bars

        self.atr_window = atr_window

        self.tolerance_atr_fraction = (
            tolerance_atr_fraction
        )

        self.minimum_candles = (
            minimum_candles
        )


    @staticmethod
    def _true_ranges(candles):

        ranges = []

        for index, candle in enumerate(candles):

            if index == 0:

                true_range = (
                    candle.high
                    - candle.low
                )

            else:

                previous_close = (
                    candles[index - 1].close
                )

                true_range = max(
                    candle.high - candle.low,
                    abs(
                        candle.high
                        - previous_close
                    ),
                    abs(
                        candle.low
                        - previous_close
                    )
                )

            ranges.append(true_range)

        return ranges


    def _validate(self, candles):

        required = max(
            self.minimum_candles,
            self.atr_window + 1,
            (
                self.left_bars
                + self.right_bars
                + 1
            )
        )

        if len(candles) < required:

            raise ValueError(
                "MarketStructureEngine requires "
                f"at least {required} "
                "completed candles."
            )


        symbol = candles[0].symbol
        timeframe = candles[0].timeframe


        for candle in candles:

            if candle.symbol != symbol:

                raise ValueError(
                    "Mixed symbols supplied "
                    "to MarketStructureEngine."
                )


            if candle.timeframe != timeframe:

                raise ValueError(
                    "Mixed timeframes supplied "
                    "to MarketStructureEngine."
                )


        for previous, current in zip(
            candles,
            candles[1:]
        ):

            if (
                current.timestamp
                <= previous.timestamp
            ):

                raise ValueError(
                    "Candles must be in "
                    "strict chronological order."
                )


    def _find_swings(self, candles):

        swing_highs = []
        swing_lows = []


        for index in range(
            self.left_bars,
            len(candles)
            - self.right_bars
        ):

            candle = candles[index]


            left = candles[
                 index - self.left_bars:
                 index
            ]


            right = candles[
                index + 1:
                index + 1
                + self.right_bars
            ]


            left_highs = [
                item.high
                for item in left
            ]


            right_highs = [
                item.high
                for item in right
            ]


            left_lows = [
                item.low
                for item in left
            ]


            right_lows = [
                item.low
                for item in right
            ]


            # ==================================
            # PLATEAU-AWARE SWING HIGH
            # ==================================
            #
            # Equal highs are allowed on the
            # left side, but price must be
            # strictly below the candidate
            # on the right side.
            #
            # This selects the LAST candle
            # in an equal-high plateau as the
            # confirmed swing high.
            # ==================================

            is_swing_high = (

                candle.high
                 >= max(left_highs)

                and

                candle.high
                > max(right_highs)
            )


            # ==================================
            # PLATEAU-AWARE SWING LOW
            # ==================================
            #
            # Equal lows are allowed on the
            # left side, but price must move
            # strictly above the candidate
            # afterward.
            #
            # This selects the LAST candle
            # in an equal-low plateau.
            # ==================================

            is_swing_low = (

                candle.low
                <= min(left_lows)

                and

                candle.low
                < min(right_lows)
            )


            if is_swing_high:

                swing_highs.append(
                    SwingPoint(
                        index=index,
                        timestamp=candle.timestamp,
                        price=candle.high,
                        kind="high"
                    )
                )


            if is_swing_low:

                swing_lows.append(
                    SwingPoint(
                         index=index,
                        timestamp=candle.timestamp,
                        price=candle.low,
                        kind="low"
                    )
                )


        return (
            swing_highs,
            swing_lows
        )


    @staticmethod
    def _compare(
        latest,
        previous,
        tolerance
    ):

        difference = (
            latest - previous
        )


        if difference > tolerance:

            return 1


        if difference < -tolerance:

            return -1


        return 0


    @staticmethod
    def _relation_name(
        relation,
        point_type
    ):

        if point_type == "high":

            if relation > 0:
                return "higher_high"

            if relation < 0:
                return "lower_high"

            return "equal_high"


        if relation > 0:
            return "higher_low"

        if relation < 0:
            return "lower_low"

        return "equal_low"


    @staticmethod
    def _comparison_confidence(
        latest,
        previous,
        tolerance,
        relation
    ):

        difference = abs(
            latest - previous
        )


        if tolerance <= 0:

            return 1.0


        if relation == 0:

            return max(
                0.0,
                min(
                    1.0,
                    1.0
                    - (
                        difference
                        / tolerance
                    )
                )
            )


        return min(
            1.0,
            difference
            / (
                tolerance * 3.0
            )
        )


    def analyze_detailed(
        self,
        candles: Sequence[CandleBar]
    ) -> StructureEngineResult:

        candles = list(candles)


        self._validate(candles)


        # ==================================
        # VOLATILITY-BASED TOLERANCE
        # ==================================

        true_ranges = (
            self._true_ranges(candles)
        )


        recent_ranges = (
            true_ranges[
                -self.atr_window:
            ]
        )


        average_true_range = (
            sum(recent_ranges)
            / len(recent_ranges)
        )


        average_true_range_pips = (
            average_true_range
            * 10000
        )


        tolerance = max(
            average_true_range
            * self.tolerance_atr_fraction,
            0.000001
        )


        tolerance_pips = (
            tolerance
            * 10000
        )


        # ==================================
        # CONFIRMED SWINGS
        # ==================================

        (
            swing_highs,
            swing_lows
        ) = self._find_swings(
            candles
        )


        if (
            len(swing_highs) < 2
            or len(swing_lows) < 2
        ):

            state = StructureState(
                label="insufficient_structure",
                confidence=0.0
            )


            return StructureEngineResult(
                state=state,
                structure_score=0.0,

                high_relation="unknown",
                low_relation="unknown",

                previous_swing_high=None,
                latest_swing_high=None,

                previous_swing_low=None,
                latest_swing_low=None,

                swing_high_count=len(
                    swing_highs
                ),

                swing_low_count=len(
                    swing_lows
                ),

                average_true_range_pips=
                    average_true_range_pips,

                tolerance_pips=
                    tolerance_pips
            )


        previous_high = (
            swing_highs[-2]
        )

        latest_high = (
            swing_highs[-1]
        )


        previous_low = (
            swing_lows[-2]
        )

        latest_low = (
            swing_lows[-1]
        )


        # ==================================
        # HIGH / LOW RELATIONSHIPS
        # ==================================

        high_relation_value = (
            self._compare(
                latest_high.price,
                previous_high.price,
                tolerance
            )
        )


        low_relation_value = (
            self._compare(
                latest_low.price,
                previous_low.price,
                tolerance
            )
        )


        high_relation = (
            self._relation_name(
                high_relation_value,
                "high"
            )
        )


        low_relation = (
            self._relation_name(
                low_relation_value,
                "low"
            )
        )


        # ==================================
        # CONFIDENCE
        # ==================================

        high_confidence = (
            self._comparison_confidence(
                latest_high.price,
                previous_high.price,
                tolerance,
                high_relation_value
            )
        )


        low_confidence = (
            self._comparison_confidence(
                latest_low.price,
                previous_low.price,
                tolerance,
                low_relation_value
            )
        )


        confidence = (
            high_confidence
            + low_confidence
        ) / 2


        confidence = max(
            0.0,
            min(
                1.0,
                confidence
            )
        )


        # ==================================
        # STRUCTURE CLASSIFICATION
        # ==================================

        if (
            high_relation_value == 1
            and low_relation_value == 1
        ):

            label = (
                "higher_highs_higher_lows"
            )

            structure_score = (
                confidence
            )


        elif (
            high_relation_value == -1
            and low_relation_value == -1
        ):

            label = (
                "lower_highs_lower_lows"
            )

            structure_score = (
                -confidence
            )


        elif (
            high_relation_value == 1
            and low_relation_value == -1
        ):

            label = (
                "expanding_range"
            )

            structure_score = 0.0


        elif (
            high_relation_value == -1
            and low_relation_value == 1
        ):

            label = (
                "contracting_range"
            )

            structure_score = 0.0


        elif (
            (
                high_relation_value == 1
                and low_relation_value == 0
            )
            or
            (
                high_relation_value == 0
                and low_relation_value == 1
            )
        ):

            label = (
                "bullish_transition"
            )

            structure_score = (
                0.5
                * confidence
            )


        elif (
            (
                high_relation_value == -1
                and low_relation_value == 0
            )
            or
            (
                high_relation_value == 0
                and low_relation_value == -1
            )
        ):

            label = (
                "bearish_transition"
            )

            structure_score = (
                -0.5
                * confidence
            )


        else:

            label = "range"

            structure_score = 0.0


        state = StructureState(
            label=label,
            confidence=confidence
        )


        return StructureEngineResult(
            state=state,

            structure_score=
                structure_score,

            high_relation=
                high_relation,

            low_relation=
                low_relation,

            previous_swing_high=
                previous_high,

            latest_swing_high=
                latest_high,

            previous_swing_low=
                previous_low,

            latest_swing_low=
                latest_low,

            swing_high_count=
                len(swing_highs),

            swing_low_count=
                len(swing_lows),

            average_true_range_pips=
                average_true_range_pips,

            tolerance_pips=
                tolerance_pips
        )


    def analyze(
        self,
        candles: Sequence[CandleBar]
    ) -> StructureState:

        result = (
            self.analyze_detailed(
                candles
            )
        )

        return result.state

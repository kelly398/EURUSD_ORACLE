from dataclasses import dataclass
from math import tanh
from typing import Sequence

from backend.market_state.schemas import (
    CandleBar,
    MarketDirection,
    TrendState
)


@dataclass(frozen=True)
class TrendEngineResult:

    state: TrendState

    composite_score: float

    regression_score: float

    ema_score: float

    price_location_score: float

    persistence_score: float

    structure_score: float

    average_true_range_pips: float


class TrendEngine:

    def __init__(
        self,
        fast_period=8,
        slow_period=21,
        slope_window=30,
        persistence_window=20,
        structure_window=20,
        atr_window=20,
        minimum_candles=60,
        neutral_threshold=0.20
    ):

        self.fast_period = fast_period
        self.slow_period = slow_period

        self.slope_window = slope_window

        self.persistence_window = (
            persistence_window
        )

        self.structure_window = (
            structure_window
        )

        self.atr_window = atr_window

        self.minimum_candles = (
            minimum_candles
        )

        self.neutral_threshold = (
            neutral_threshold
        )


    @staticmethod
    def _clamp(
        value,
        minimum=-1.0,
        maximum=1.0
    ):

        return max(
            minimum,
            min(maximum, value)
        )


    @staticmethod
    def _ema(
        values,
        period
    ):

        if len(values) < period:

            raise ValueError(
                f"Need at least {period} "
                "values for EMA."
            )


        alpha = 2 / (
            period + 1
        )


        ema = values[0]


        for value in values[1:]:

            ema = (
                alpha * value
                + (1 - alpha) * ema
            )


        return ema


    @staticmethod
    def _regression_slope(
        values
    ):

        count = len(values)


        if count < 2:

            return 0.0


        x_mean = (
            count - 1
        ) / 2


        y_mean = (
            sum(values)
            / count
        )


        numerator = sum(

            (
                index - x_mean
            )
            * (
                value - y_mean
            )

            for index, value
            in enumerate(values)
        )


        denominator = sum(

            (
                index - x_mean
            ) ** 2

            for index
            in range(count)
        )


        if denominator == 0:

            return 0.0


        return (
            numerator
            / denominator
        )


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


    def _validate(
        self,
        candles
    ):

        if len(candles) < self.minimum_candles:

            raise ValueError(
                "TrendEngine requires at least "
                f"{self.minimum_candles} "
                "completed candles."
            )


        first_symbol = (
            candles[0].symbol
        )

        first_timeframe = (
            candles[0].timeframe
        )


        for candle in candles:

            if (
                candle.symbol
                != first_symbol
            ):

                raise ValueError(
                    "Mixed symbols supplied "
                    "to TrendEngine."
                )


            if (
                candle.timeframe
                != first_timeframe
            ):

                raise ValueError(
                    "Mixed timeframes supplied "
                    "to TrendEngine."
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
                    "strict chronological "
                    "order."
                )


    def _structure_score(
        self,
        candles
    ):

        recent = candles[
            -self.structure_window:
        ]


        midpoint = (
            len(recent) // 2
        )


        previous_half = (
            recent[:midpoint]
        )

        current_half = (
            recent[midpoint:]
        )


        previous_high = max(
            candle.high
            for candle
            in previous_half
        )

        current_high = max(
            candle.high
            for candle
            in current_half
        )


        previous_low = min(
            candle.low
            for candle
            in previous_half
        )

        current_low = min(
            candle.low
            for candle
            in current_half
        )


        bullish_points = 0

        bearish_points = 0


        if current_high > previous_high:

            bullish_points += 1

        elif current_high < previous_high:

            bearish_points += 1


        if current_low > previous_low:

            bullish_points += 1

        elif current_low < previous_low:

            bearish_points += 1


        return (
            bullish_points
            - bearish_points
        ) / 2


    def analyze_detailed(
        self,
        candles: Sequence[CandleBar]
    ) -> TrendEngineResult:

        candles = list(
            candles
        )


        self._validate(
            candles
        )


        closes = [
            candle.close
            for candle
            in candles
        ]


        # ==================================
        # VOLATILITY SCALE
        # ==================================

        true_ranges = (
            self._true_ranges(
                candles
            )
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


        # Prevent division by a
        # near-zero volatility value.
        volatility_scale = max(
            average_true_range_pips,
            0.1
        )


        # ==================================
        # 1. REGRESSION SLOPE
        # ==================================

        regression_closes = closes[
            -self.slope_window:
        ]


        slope = (
            self._regression_slope(
                regression_closes
            )
        )


        slope_pips_per_bar = (
            slope * 10000
        )


        regression_score = tanh(

            4.0
            * (
                slope_pips_per_bar
                / volatility_scale
            )
        )


        # ==================================
        # 2. EMA STRUCTURE
        # ==================================

        fast_ema = self._ema(
            closes,
            self.fast_period
        )


        slow_ema = self._ema(
            closes,
            self.slow_period
        )


        ema_separation_pips = (

            fast_ema
            - slow_ema

        ) * 10000


        ema_score = tanh(

            ema_separation_pips
            / volatility_scale
        )


        # ==================================
        # 3. PRICE LOCATION
        # ==================================

        price_distance_pips = (

            closes[-1]
            - slow_ema

        ) * 10000


        price_location_score = tanh(

            price_distance_pips
            / volatility_scale
        )


        # ==================================
        # 4. DIRECTIONAL PERSISTENCE
        # ==================================

        recent_closes = closes[
            -(
                self.persistence_window
                + 1
            ):
        ]


        changes = [

            current - previous

            for previous, current
            in zip(
                recent_closes,
                recent_closes[1:]
            )
        ]


        positive_moves = sum(
            1
            for change
            in changes
            if change > 0
        )


        negative_moves = sum(
            1
            for change
            in changes
            if change < 0
        )


        directional_moves = (
            positive_moves
            + negative_moves
        )


        if directional_moves == 0:

            persistence_score = 0.0

        else:

            persistence_score = (

                positive_moves
                - negative_moves

            ) / directional_moves


        # ==================================
        # 5. MARKET STRUCTURE
        # ==================================

        structure_score = (
            self._structure_score(
                candles
            )
        )


        # ==================================
        # COMPOSITE TREND SCORE
        # ==================================

        composite_score = (

            0.30
            * regression_score

            + 0.25
            * ema_score

            + 0.15
            * price_location_score

            + 0.15
            * persistence_score

            + 0.15
            * structure_score
        )


        composite_score = (
            self._clamp(
                composite_score
            )
        )


        # ==================================
        # CLASSIFICATION
        # ==================================

        if (
            composite_score
            >= self.neutral_threshold
        ):

            direction = (
                MarketDirection.BULLISH
            )


        elif (
            composite_score
            <= -self.neutral_threshold
        ):

            direction = (
                MarketDirection.BEARISH
            )


        else:

            direction = (
                MarketDirection.NEUTRAL
            )


        strength = min(
            abs(composite_score),
            1.0
        )


        state = TrendState(

            direction=direction,

            strength=strength,

            slope_pips_per_bar=
                slope_pips_per_bar
        )


        return TrendEngineResult(

            state=state,

            composite_score=
                composite_score,

            regression_score=
                regression_score,

            ema_score=
                ema_score,

            price_location_score=
                price_location_score,

            persistence_score=
                persistence_score,

            structure_score=
                structure_score,

            average_true_range_pips=
                average_true_range_pips
        )


    def analyze(
        self,
        candles: Sequence[CandleBar]
    ) -> TrendState:

        result = (
            self.analyze_detailed(
                candles
            )
        )

        return result.state
        
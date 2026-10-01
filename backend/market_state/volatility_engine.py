from dataclasses import dataclass
from statistics import median, pstdev
from typing import Sequence

from backend.market_state.schemas import (
    CandleBar,
    VolatilityRegime,
    VolatilityState
)


@dataclass(frozen=True)
class VolatilityEngineResult:

    state: VolatilityState

    composite_ratio: float

    atr_ratio: float

    realized_volatility_ratio: float

    range_shock_ratio: float

    baseline_atr_pips: float

    current_atr_pips: float

    baseline_realized_volatility_pips: float

    realized_volatility_pips: float

    baseline_range_pips: float


class VolatilityEngine:

    def __init__(
        self,
        atr_period=14,
        realized_window=20,
        baseline_window=100,
        minimum_candles=140,
        low_threshold=0.70,
        high_threshold=1.40,
        extreme_threshold=2.20
    ):

        self.atr_period = atr_period

        self.realized_window = (
            realized_window
        )

        self.baseline_window = (
            baseline_window
        )

        self.minimum_candles = (
            minimum_candles
        )

        self.low_threshold = (
            low_threshold
        )

        self.high_threshold = (
            high_threshold
        )

        self.extreme_threshold = (
            extreme_threshold
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


    @staticmethod
    def _rolling_mean(
        values,
        window
    ):

        results = []

        for index in range(
            window - 1,
            len(values)
        ):

            sample = values[
                index - window + 1:
                index + 1
            ]

            results.append(
                sum(sample)
                / window
            )

        return results


    @staticmethod
    def _rolling_std(
        values,
        window
    ):

        results = []

        for index in range(
            window - 1,
            len(values)
        ):

            sample = values[
                index - window + 1:
                index + 1
            ]

            results.append(
                pstdev(sample)
            )

        return results


    def _validate(
        self,
        candles
    ):

        required_candles = max(

            self.minimum_candles,

            self.baseline_window
            + self.atr_period
            + 1,

            self.baseline_window
            + self.realized_window
            + 2
        )


        if len(candles) < required_candles:

            raise ValueError(
                "VolatilityEngine requires "
                f"at least "
                f"{required_candles} "
                "completed candles."
            )


        symbol = (
            candles[0].symbol
        )

        timeframe = (
            candles[0].timeframe
        )


        for candle in candles:

            if candle.symbol != symbol:

                raise ValueError(
                    "Mixed symbols supplied "
                    "to VolatilityEngine."
                )


            if (
                candle.timeframe
                != timeframe
            ):

                raise ValueError(
                    "Mixed timeframes supplied "
                    "to VolatilityEngine."
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


    def analyze_detailed(
        self,
        candles: Sequence[CandleBar]
    ) -> VolatilityEngineResult:

        candles = list(
            candles
        )

        self._validate(
            candles
        )


        # ==================================
        # TRUE RANGE
        # ==================================

        true_ranges = (
            self._true_ranges(
                candles
            )
        )


        # ==================================
        # CURRENT + HISTORICAL ATR
        # ==================================

        rolling_atrs = (
            self._rolling_mean(
                true_ranges,
                self.atr_period
            )
        )


        current_atr = (
            rolling_atrs[-1]
        )


        historical_atrs = (
            rolling_atrs[
                -(
                    self.baseline_window
                    + 1
                ):
                -1
            ]
        )


        baseline_atr = (
            median(
                historical_atrs
            )
        )


        if baseline_atr <= 0:

            atr_ratio = 1.0

        else:

            atr_ratio = (
                current_atr
                / baseline_atr
            )


        # ==================================
        # REALIZED VOLATILITY
        # ==================================

        closes = [

            candle.close

            for candle in candles
        ]


        close_changes = [

            current
            - previous

            for previous, current
            in zip(
                closes,
                closes[1:]
            )
        ]


        rolling_realized = (
            self._rolling_std(
                close_changes,
                self.realized_window
            )
        )


        realized_volatility = (
            rolling_realized[-1]
        )


        historical_realized = (

            rolling_realized[
                -(
                    self.baseline_window
                    + 1
                ):
                -1
            ]
        )


        baseline_realized = (
            median(
                historical_realized
            )
        )


        if baseline_realized <= 0:

            if realized_volatility <= 0:

                realized_ratio = 1.0

            else:

                realized_ratio = 2.0

        else:

            realized_ratio = (

                realized_volatility
                / baseline_realized
            )


        # ==================================
        # LATEST RANGE SHOCK
        # ==================================

        latest_true_range = (
            true_ranges[-1]
        )


        historical_ranges = (

            true_ranges[
                -(
                    self.baseline_window
                    + 1
                ):
                -1
            ]
        )


        baseline_range = (
            median(
                historical_ranges
            )
        )


        if baseline_range <= 0:

            range_shock_ratio = 1.0

        else:

            range_shock_ratio = (

                latest_true_range
                / baseline_range
            )


        # ==================================
        # COMPOSITE VOLATILITY RATIO
        # ==================================

        composite_ratio = (

            0.50
            * atr_ratio

            + 0.35
            * realized_ratio

            + 0.15
            * range_shock_ratio
        )


        # ==================================
        # VOLATILITY REGIME
        # ==================================

        if (
            composite_ratio
            < self.low_threshold
        ):

            regime = (
                VolatilityRegime.LOW
            )


        elif (
            composite_ratio
            < self.high_threshold
        ):

            regime = (
                VolatilityRegime.NORMAL
            )


        elif (
            composite_ratio
            < self.extreme_threshold
        ):

            regime = (
                VolatilityRegime.HIGH
            )


        else:

            regime = (
                VolatilityRegime.EXTREME
            )


        # ==================================
        # CONVERT TO PIPS
        # ==================================

        current_atr_pips = (
            current_atr
            * 10000
        )


        baseline_atr_pips = (
            baseline_atr
            * 10000
        )


        realized_volatility_pips = (

            realized_volatility
            * 10000
        )


        baseline_realized_pips = (

            baseline_realized
            * 10000
        )


        baseline_range_pips = (

            baseline_range
            * 10000
        )


        # ==================================
        # PUBLIC STATE
        # ==================================

        state = VolatilityState(

            regime=regime,

            atr_pips=
                current_atr_pips,

            realized_volatility=
                realized_volatility_pips
        )


        # ==================================
        # DIAGNOSTIC RESULT
        # ==================================

        return VolatilityEngineResult(

            state=state,

            composite_ratio=
                composite_ratio,

            atr_ratio=
                atr_ratio,

            realized_volatility_ratio=
                realized_ratio,

            range_shock_ratio=
                range_shock_ratio,

            baseline_atr_pips=
                baseline_atr_pips,

            current_atr_pips=
                current_atr_pips,

            baseline_realized_volatility_pips=
                baseline_realized_pips,

            realized_volatility_pips=
                realized_volatility_pips,

            baseline_range_pips=
                baseline_range_pips
        )


    def analyze(
        self,
        candles: Sequence[CandleBar]
    ) -> VolatilityState:

        result = (
            self.analyze_detailed(
                candles
            )
        )

        return result.state
        
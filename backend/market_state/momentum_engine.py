from dataclasses import dataclass
from math import tanh
from typing import Sequence

from backend.market_state.schemas import (
    CandleBar,
    MomentumSnapshot,
    MomentumState
)


@dataclass(frozen=True)
class MomentumEngineResult:

    state: MomentumSnapshot

    composite_score: float

    impulse_score: float

    rsi_score: float

    acceleration_score: float

    body_pressure_score: float

    rsi: float

    net_impulse_pips: float

    average_true_range_pips: float


class MomentumEngine:

    def __init__(
        self,
        impulse_window=10,
        rsi_period=14,
        acceleration_window=5,
        body_window=5,
        atr_window=20,
        minimum_candles=60,
        neutral_threshold=0.15
    ):

        self.impulse_window = (
            impulse_window
        )

        self.rsi_period = (
            rsi_period
        )

        self.acceleration_window = (
            acceleration_window
        )

        self.body_window = (
            body_window
        )

        self.atr_window = (
            atr_window
        )

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
            min(
                maximum,
                value
            )
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
    def _calculate_rsi(
        closes,
        period
    ):

        changes = [

            current - previous

            for previous, current
            in zip(
                closes,
                closes[1:]
            )
        ]


        recent = (
            changes[-period:]
        )


        gains = [

            change
            if change > 0
            else 0.0

            for change in recent
        ]


        losses = [

            abs(change)
            if change < 0
            else 0.0

            for change in recent
        ]


        average_gain = (
            sum(gains)
            / period
        )


        average_loss = (
            sum(losses)
            / period
        )


        if (
            average_gain == 0
            and average_loss == 0
        ):

            return 50.0


        if average_loss == 0:

            return 100.0


        if average_gain == 0:

            return 0.0


        relative_strength = (
            average_gain
            / average_loss
        )


        return (

            100

            - (
                100
                / (
                    1
                    + relative_strength
                )
            )
        )


    def _validate(
        self,
        candles
    ):

        required = max(

            self.minimum_candles,

            self.rsi_period + 1,

            self.impulse_window + 1,

            (
                self.acceleration_window
                * 2
                + 1
            ),

            self.body_window,

            self.atr_window + 1
        )


        if len(candles) < required:

            raise ValueError(
                "MomentumEngine requires "
                f"at least {required} "
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
                    "to MomentumEngine."
                )


            if (
                candle.timeframe
                != timeframe
            ):

                raise ValueError(
                    "Mixed timeframes supplied "
                    "to MomentumEngine."
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
    ) -> MomentumEngineResult:

        candles = list(
            candles
        )


        self._validate(
            candles
        )


        closes = [

            candle.close

            for candle in candles
        ]


        # ==================================
        # VOLATILITY NORMALIZATION
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

            / len(
                recent_ranges
            )
        )


        atr_pips = (

            average_true_range
            * 10000
        )


        volatility_scale = max(
            atr_pips,
            0.1
        )


        # ==================================
        # 1. NET PRICE IMPULSE
        # ==================================

        impulse_start = (

            closes[
                -(
                    self.impulse_window
                    + 1
                )
            ]
        )


        impulse_end = (
            closes[-1]
        )


        net_impulse_pips = (

            impulse_end
            - impulse_start

        ) * 10000


        normalized_impulse = (

            net_impulse_pips

            / (
                volatility_scale
                * self.impulse_window
            )
        )


        impulse_score = tanh(

            3.0
            * normalized_impulse
        )


        # ==================================
        # 2. RSI PRESSURE
        # ==================================

        rsi = (
            self._calculate_rsi(
                closes,
                self.rsi_period
            )
        )


        rsi_score = (
            self._clamp(
                (
                    rsi - 50.0
                )
                / 25.0
            )
        )


        # ==================================
        # 3. MOMENTUM ACCELERATION
        # ==================================

        close_changes = [

            current - previous

            for previous, current
            in zip(
                closes,
                closes[1:]
            )
        ]


        recent_changes = (

            close_changes[
                -self.acceleration_window:
            ]
        )


        previous_changes = (

            close_changes[
                -(
                    self.acceleration_window
                    * 2
                )
                :
                -self.acceleration_window
            ]
        )


        recent_mean = (

            sum(recent_changes)

            / len(
                recent_changes
            )
        )


        previous_mean = (

            sum(previous_changes)

            / len(
                previous_changes
            )
        )


        acceleration_pips = (

            recent_mean
            - previous_mean

        ) * 10000


        acceleration_score = tanh(

            4.0

            * (
                acceleration_pips
                / volatility_scale
            )
        )


        # ==================================
        # 4. CANDLE BODY PRESSURE
        # ==================================

        recent_body_candles = (

            candles[
                -self.body_window:
            ]
        )


        body_sum_pips = (

            sum(

                candle.close
                - candle.open

                for candle
                in recent_body_candles
            )

            * 10000
        )


        normalized_body_pressure = (

            body_sum_pips

            / (
                volatility_scale
                * self.body_window
            )
        )


        body_pressure_score = tanh(

            3.0
            * normalized_body_pressure
        )


        # ==================================
        # COMPOSITE MOMENTUM
        # ==================================

        composite_score = (

            0.35
            * impulse_score

            + 0.25
            * rsi_score

            + 0.20
            * acceleration_score

            + 0.20
            * body_pressure_score
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

            momentum_state = (
                MomentumState.POSITIVE
            )


        elif (
            composite_score
            <= -self.neutral_threshold
        ):

            momentum_state = (
                MomentumState.NEGATIVE
            )


        else:

            momentum_state = (
                MomentumState.NEUTRAL
            )


        strength = min(

            abs(
                composite_score
            ),

            1.0
        )


        state = MomentumSnapshot(

            state=momentum_state,

            strength=strength
        )


        return MomentumEngineResult(

            state=state,

            composite_score=
                composite_score,

            impulse_score=
                impulse_score,

            rsi_score=
                rsi_score,

            acceleration_score=
                acceleration_score,

            body_pressure_score=
                body_pressure_score,

            rsi=rsi,

            net_impulse_pips=
                net_impulse_pips,

            average_true_range_pips=
                atr_pips
        )


    def analyze(
        self,
        candles: Sequence[CandleBar]
    ) -> MomentumSnapshot:

        result = (
            self.analyze_detailed(
                candles
            )
        )

        return result.state
        
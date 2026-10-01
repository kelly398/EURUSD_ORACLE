from math import (
    cos,
    pi,
    sin
)

from backend.agents.contracts import (
    AgentContext
)

from backend.agents.microstructure_features import (
    MicrostructureFeatureEngine
)

from backend.market_state.schemas import (
    MarketDirection,
    MomentumState,
    SpreadState,
    VolatilityRegime
)

from backend.market_state.session_engine import (
    SessionEngine
)

from backend.models.feature_contracts import (
    ModelFeatureVector
)


class MLFeatureBuilder:

    FEATURE_VERSION = "eurusd_m5_v1"


    FEATURE_NAMES = (
        "return_1_pips",
        "return_3_pips",
        "return_6_pips",
        "return_12_pips",
        "latest_range_pips",
        "latest_body_pips",
        "trend_score",
        "trend_slope_pips_per_bar",
        "momentum_score",
        "atr_pips",
        "realized_volatility",
        "volatility_regime_code",
        "structure_score",
        "average_spread_pips",
        "spread_state_code",
        "tick_update_imbalance",
        "tick_activity_ratio",
        "tick_spread_ratio",
        "tick_net_mid_move_pips",
        "london_active",
        "new_york_active",
        "tokyo_active",
        "session_overlap",
        "hour_sin",
        "hour_cos",
        "weekday_sin",
        "weekday_cos"
    )


    def __init__(self):

        self.microstructure_engine = (
            MicrostructureFeatureEngine()
        )

        self.session_engine = (
            SessionEngine()
        )


    @staticmethod
    def _return_pips(
        closes,
        bars
    ):

        return (

            closes[-1]
            - closes[
                -1 - bars
            ]

        ) * 10000.0


    @staticmethod
    def _trend_score(
        trend
    ):

        if trend is None:

            return 0.0


        if (
            trend.direction
            == MarketDirection.BULLISH
        ):

            return trend.strength


        if (
            trend.direction
            == MarketDirection.BEARISH
        ):

            return -trend.strength


        return 0.0


    @staticmethod
    def _momentum_score(
        momentum
    ):

        if momentum is None:

            return 0.0


        if (
            momentum.state
            == MomentumState.POSITIVE
        ):

            return momentum.strength


        if (
            momentum.state
            == MomentumState.NEGATIVE
        ):

            return -momentum.strength


        return 0.0


    @staticmethod
    def _structure_score(
        structure
    ):

        if structure is None:

            return 0.0


        mapping = {

            "higher_highs_higher_lows":
                1.0,

            "bullish_transition":
                0.5,

            "lower_highs_lower_lows":
                -1.0,

            "bearish_transition":
                -0.5
        }


        base = mapping.get(
            structure.label,
            0.0
        )


        return (

            base
            * structure.confidence
        )


    @staticmethod
    def _volatility_code(
        volatility
    ):

        if volatility is None:

            return 0.0


        mapping = {

            VolatilityRegime.LOW:
                0.0,

            VolatilityRegime.NORMAL:
                1.0,

            VolatilityRegime.HIGH:
                2.0,

            VolatilityRegime.EXTREME:
                3.0
        }


        return mapping[
            volatility.regime
        ]


    @staticmethod
    def _spread_code(
        liquidity
    ):

        if liquidity is None:

            return 0.0


        mapping = {

            SpreadState.TIGHT:
                -1.0,

            SpreadState.NORMAL:
                0.0,

            SpreadState.WIDE:
                1.0,

            SpreadState.EXTREME:
                2.0
        }


        return mapping[
            liquidity.spread_state
        ]


    def build(
        self,
        context: AgentContext
    ) -> ModelFeatureVector:

        if not isinstance(
            context,
            AgentContext
        ):

            raise TypeError(
                "MLFeatureBuilder requires "
                "AgentContext."
            )


        if len(context.candles) < 13:

            raise ValueError(
                "MLFeatureBuilder requires "
                "at least 13 candles."
            )


        if not context.ticks:

            raise ValueError(
                "MLFeatureBuilder requires "
                "snapshot-locked raw ticks."
            )


        candles = list(
            context.candles
        )


        closes = [

            candle.close

            for candle in candles
        ]


        latest = (
            candles[-1]
        )


        market_state = (
            context.market_state
        )


        tick_features = (
            self.microstructure_engine
            .analyze(

                ticks=
                    context.ticks,

                as_of=
                    context.as_of
            )
        )


        session = (
            self.session_engine
            .analyze(
                context.as_of
            )
        )


        trend = (
            market_state.trend
        )


        momentum = (
            market_state.momentum
        )


        volatility = (
            market_state.volatility
        )


        structure = (
            market_state.structure
        )


        liquidity = (
            market_state.liquidity
        )


        trend_slope = (

            trend.slope_pips_per_bar

            if (
                trend is not None
                and
                trend.slope_pips_per_bar
                is not None
            )

            else 0.0
        )


        atr_pips = (

            volatility.atr_pips

            if (
                volatility is not None
                and
                volatility.atr_pips
                is not None
            )

            else 0.0
        )


        realized_volatility = (

            volatility.realized_volatility

            if (
                volatility is not None
                and
                volatility.realized_volatility
                is not None
            )

            else 0.0
        )


        average_spread = (

            liquidity.average_spread_pips

            if (
                liquidity is not None
                and
                liquidity.average_spread_pips
                is not None
            )

            else 0.0
        )


        utc_hour = (

            context.as_of.hour

            + context.as_of.minute
            / 60.0
        )


        hour_angle = (

            2.0
            * pi
            * utc_hour
            / 24.0
        )


        weekday_angle = (

            2.0
            * pi
            * context.as_of.weekday()
            / 7.0
        )


        values = (

            self._return_pips(
                closes,
                1
            ),

            self._return_pips(
                closes,
                3
            ),

            self._return_pips(
                closes,
                6
            ),

            self._return_pips(
                closes,
                12
            ),

            (
                latest.high
                - latest.low
            ) * 10000.0,

            (
                latest.close
                - latest.open
            ) * 10000.0,

            self._trend_score(
                trend
            ),

            float(
                trend_slope
            ),

            self._momentum_score(
                momentum
            ),

            float(
                atr_pips
            ),

            float(
                realized_volatility
            ),

            self._volatility_code(
                volatility
            ),

            self._structure_score(
                structure
            ),

            float(
                average_spread
            ),

            self._spread_code(
                liquidity
            ),

            tick_features
            .update_imbalance,

            tick_features
            .tick_activity_ratio,

            tick_features
            .spread_ratio,

            tick_features
            .net_mid_move_pips,

            float(
                "london"
                in session.active_sessions
            ),

            float(
                "new_york"
                in session.active_sessions
            ),

            float(
                "tokyo"
                in session.active_sessions
            ),

            float(
                session.overlap
            ),

            sin(
                hour_angle
            ),

            cos(
                hour_angle
            ),

            sin(
                weekday_angle
            ),

            cos(
                weekday_angle
            )
        )


        return ModelFeatureVector(

            symbol=
                context.symbol,

            timeframe=
                context.timeframe,

            as_of=
                context.as_of,

            feature_version=
                self.FEATURE_VERSION,

            feature_names=
                self.FEATURE_NAMES,

            values=tuple(
                float(value)
                for value in values
            )
        )
        
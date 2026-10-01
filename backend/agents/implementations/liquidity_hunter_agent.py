from statistics import mean

from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory
)

from backend.agents.microstructure_features import (
    MicrostructureFeatureEngine
)


class LiquidityHunterAgent(
    BaseAgent
):

    agent_id = "liquidity_hunter"

    agent_name = "Liquidity Hunter"

    category = (
        AgentCategory.MICROSTRUCTURE
    )

    version = "1.0.0"


    def __init__(
        self,
        sweep_lookback=20
    ):

        self.sweep_lookback = (
            sweep_lookback
        )

        self.feature_engine = (
            MicrostructureFeatureEngine()
        )

        super().__init__()


    def analyze(
        self,
        context
    ):

        if not context.ticks:

            raise RuntimeError(
                "Liquidity Hunter requires "
                "raw tick data."
            )


        if (
            len(context.candles)
            < self.sweep_lookback + 1
        ):

            raise RuntimeError(
                "Insufficient candles for "
                "Liquidity Hunter."
            )


        features = (
            self.feature_engine.analyze(

                ticks=
                    context.ticks,

                as_of=
                    context.as_of
            )
        )


        latest = (
            context.candles[-1]
        )


        history = (

            context.candles[
                -(
                    self.sweep_lookback
                    + 1
                ):
                -1
            ]
        )


        prior_high = max(

            candle.high

            for candle in history
        )


        prior_low = min(

            candle.low

            for candle in history
        )


        average_range = mean(

            candle.high
            - candle.low

            for candle in history
        )


        average_range = max(
            average_range,
            0.000001
        )


        # ==================================
        # LIQUIDITY-SWEEP PROXIES
        # ==================================

        bullish_sweep = (

            latest.low
            < prior_low

            and

            latest.close
            > prior_low
        )


        bearish_sweep = (

            latest.high
            > prior_high

            and

            latest.close
            < prior_high
        )


        if (
            bullish_sweep
            and bearish_sweep
        ):

            score = 0.0

            sweep_type = (
                "two_sided_sweep"
            )

            base_strength = 0.0


        elif bullish_sweep:

            sweep_depth = (

                prior_low
                - latest.low
            )


            base_strength = min(

                1.0,

                0.35

                + 0.65
                * (
                    sweep_depth
                    / average_range
                )
            )


            score = (
                base_strength
            )


            sweep_type = (
                "sell_side_sweep"
            )


        elif bearish_sweep:

            sweep_depth = (

                latest.high
                - prior_high
            )


            base_strength = min(

                1.0,

                0.35

                + 0.65
                * (
                    sweep_depth
                    / average_range
                )
            )


            score = -(
                base_strength
            )


            sweep_type = (
                "buy_side_sweep"
            )


        else:

            score = 0.0

            base_strength = 0.0

            sweep_type = "none"


        # ==================================
        # CONFIDENCE QUALITY
        # ==================================

        activity_quality = min(
            1.0,
            features.tick_activity_ratio
        )


        if (
            features.spread_ratio
            <= 1.0
        ):

            spread_quality = 1.0

        else:

            spread_quality = min(

                1.0,

                1.0
                / features.spread_ratio
            )


        if score > 0:

            quote_alignment = (

                0.5

                + 0.5
                * max(
                    0.0,
                    features.update_imbalance
                )
            )


        elif score < 0:

            quote_alignment = (

                0.5

                + 0.5
                * max(
                    0.0,
                    -features.update_imbalance
                )
            )


        else:

            quote_alignment = 0.0


        confidence = min(

            1.0,

            base_strength
            * activity_quality
            * spread_quality
            * quote_alignment
        )


        return self.build_opinion(

            context=context,

            score=score,

            confidence=confidence,

            rationale=(
                "Liquidity Hunter searched for "
                "price sweeps beyond recent "
                "extremes followed by rejection. "
                f"Detected: {sweep_type}."
            ),

            evidence={

                "sweep_type":
                    sweep_type,

                "prior_high":
                    prior_high,

                "prior_low":
                    prior_low,

                "latest_high":
                    latest.high,

                "latest_low":
                    latest.low,

                "latest_close":
                    latest.close,

                "update_imbalance":
                    features.update_imbalance,

                "activity_ratio":
                    features.tick_activity_ratio,

                "spread_ratio":
                    features.spread_ratio,

                "quote_flow_proxy":
                    True
            }
        )
        
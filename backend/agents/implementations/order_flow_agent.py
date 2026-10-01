from math import (
    sqrt,
    tanh
)

from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory
)

from backend.agents.microstructure_features import (
    MicrostructureFeatureEngine
)


class OrderFlowAgent(
    BaseAgent
):

    agent_id = "order_flow"

    agent_name = "Order Flow"

    category = (
        AgentCategory.MICROSTRUCTURE
    )

    version = "1.0.0"


    def __init__(self):

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
                "Order Flow agent requires "
                "raw quote ticks."
            )


        features = (
            self.feature_engine.analyze(

                ticks=
                    context.ticks,

                as_of=
                    context.as_of
            )
        )


        if (
            features.directional_updates
            < 10
        ):

            score = 0.0

            move_score = 0.0


        else:

            random_walk_scale = max(

                0.25,

                features
                .mean_absolute_mid_move_pips

                * sqrt(
                    features
                    .directional_updates
                )
            )


            normalized_move = (

                features.net_mid_move_pips

                / random_walk_scale
            )


            move_score = tanh(
                normalized_move
            )


            score = (

                0.60
                * features.update_imbalance

                + 0.40
                * move_score
            )


            score = max(
                -1.0,
                min(
                    1.0,
                    score
                )
            )


        sample_quality = min(

            1.0,

            features.directional_updates
            / 100.0
        )


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


        confidence = (

            abs(score)

            * sample_quality

            * activity_quality

            * spread_quality
        )


        confidence = min(
            1.0,
            confidence
        )


        return self.build_opinion(

            context=context,

            score=score,

            confidence=confidence,

            rationale=(
                "Broker quote-flow proxy combined "
                "directional mid-price updates "
                "with the net recent mid-price "
                "movement."
            ),

            evidence={

                "update_imbalance":
                    features.update_imbalance,

                "net_mid_move_pips":
                    features.net_mid_move_pips,

                "move_score":
                    move_score,

                "up_updates":
                    features.up_updates,

                "down_updates":
                    features.down_updates,

                "directional_updates":
                    features.directional_updates,

                "activity_ratio":
                    features.tick_activity_ratio,

                "spread_ratio":
                    features.spread_ratio,

                "centralized_order_flow":
                    False,

                "quote_flow_proxy":
                    True
            }
        )
        
from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory
)

from backend.market_state.schemas import (
    MarketDirection
)


class TrendMasterAgent(BaseAgent):

    agent_id = "trend_master"

    agent_name = "Trend Master"

    category = AgentCategory.TECHNICAL

    version = "1.0.0"


    def analyze(
        self,
        context
    ):

        trend = (
            context.market_state.trend
        )


        if trend is None:

            raise RuntimeError(
                "Trend state is unavailable."
            )


        if (
            trend.direction
            == MarketDirection.BULLISH
        ):

            score = (
                trend.strength
            )


        elif (
            trend.direction
            == MarketDirection.BEARISH
        ):

            score = -(
                trend.strength
            )


        else:

            score = 0.0


        confidence = min(
            1.0,
            max(
                0.0,
                trend.strength
            )
        )


        rationale = (

            f"Trend engine classifies "
            f"EUR/USD as "
            f"{trend.direction.value} "
            f"with strength "
            f"{trend.strength:.4f}."

        )


        return self.build_opinion(

            context=context,

            score=score,

            confidence=confidence,

            rationale=rationale,

            evidence={

                "trend_direction":
                    trend.direction.value,

                "trend_strength":
                    trend.strength,

                "slope_pips_per_bar":
                    trend.slope_pips_per_bar
            }
        )
        
from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory
)

from backend.market_state.schemas import (
    MomentumState
)


class MomentumAgent(BaseAgent):

    agent_id = "momentum"

    agent_name = "Momentum"

    category = AgentCategory.TECHNICAL

    version = "1.0.0"


    def analyze(
        self,
        context
    ):

        momentum = (
            context.market_state.momentum
        )


        if momentum is None:

            raise RuntimeError(
                "Momentum state is unavailable."
            )


        if (
            momentum.state
            == MomentumState.POSITIVE
        ):

            score = (
                momentum.strength
            )


        elif (
            momentum.state
            == MomentumState.NEGATIVE
        ):

            score = -(
                momentum.strength
            )


        else:

            score = 0.0


        confidence = min(
            1.0,
            max(
                0.0,
                momentum.strength
            )
        )


        rationale = (

            f"Momentum engine classifies "
            f"current EUR/USD momentum as "
            f"{momentum.state.value} "
            f"with strength "
            f"{momentum.strength:.4f}."

        )


        return self.build_opinion(

            context=context,

            score=score,

            confidence=confidence,

            rationale=rationale,

            evidence={

                "momentum_state":
                    momentum.state.value,

                "momentum_strength":
                    momentum.strength
            }
        )
        
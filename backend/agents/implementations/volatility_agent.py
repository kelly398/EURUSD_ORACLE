from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory,
    AgentSignalType
)


class VolatilityAgent(BaseAgent):

    agent_id = "volatility"

    agent_name = "Volatility"

    category = AgentCategory.TECHNICAL

    signal_type = (
        AgentSignalType.CONTEXTUAL
    )

    version = "1.0.0"


    def analyze(
        self,
        context
    ):

        volatility = (
            context.market_state.volatility
        )


        if volatility is None:

            raise RuntimeError(
                "Volatility state is unavailable."
            )


        # ==================================
        # IMPORTANT DESIGN RULE
        # ==================================
        #
        # Volatility is not inherently
        # bullish or bearish.
        #
        # We therefore refuse to manufacture
        # directional information from a
        # non-directional variable.
        #
        # This agent reports NEUTRAL direction
        # while preserving volatility regime
        # information for later consensus and
        # risk processing.
        # ==================================

        score = 0.0


        rationale = (

            f"EUR/USD volatility regime is "
            f"{volatility.regime.value}. "
            f"ATR is "
            f"{volatility.atr_pips:.4f} pips. "
            "Volatility alone does not provide "
            "directional evidence."

        )


        return self.build_opinion(

            context=context,

            score=score,

            confidence=1.0,

            rationale=rationale,

            evidence={

                "volatility_regime":
                    volatility.regime.value,

                "atr_pips":
                    volatility.atr_pips,

                "realized_volatility":
                    volatility.realized_volatility,

                "directional_signal":
                    False
            }
        )
        
from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory
)


class MarketStructureAgent(
    BaseAgent
):

    agent_id = "market_structure"

    agent_name = "Market Structure"

    category = AgentCategory.TECHNICAL

    version = "1.0.0"


    def analyze(
        self,
        context
    ):

        structure = (
            context.market_state.structure
        )


        if structure is None:

            raise RuntimeError(
                "Market structure state "
                "is unavailable."
            )


        label = (
            structure.label
        )


        confidence = min(
            1.0,
            max(
                0.0,
                structure.confidence
            )
        )


        # ==================================
        # STRONG BULLISH STRUCTURE
        # ==================================

        if (
            label
            == "higher_highs_higher_lows"
        ):

            score = (
                confidence
            )


        # ==================================
        # STRONG BEARISH STRUCTURE
        # ==================================

        elif (
            label
            == "lower_highs_lower_lows"
        ):

            score = -(
                confidence
            )


        # ==================================
        # PARTIAL BULLISH TRANSITION
        # ==================================

        elif (
            label
            == "bullish_transition"
        ):

            score = (
                0.5
                * confidence
            )


        # ==================================
        # PARTIAL BEARISH TRANSITION
        # ==================================

        elif (
            label
            == "bearish_transition"
        ):

            score = -(
                0.5
                * confidence
            )


        # ==================================
        # NON-DIRECTIONAL STRUCTURES
        # ==================================

        else:

            score = 0.0


        rationale = (

            f"Confirmed EUR/USD structure "
            f"is {label} with confidence "
            f"{confidence:.4f}."

        )


        return self.build_opinion(

            context=context,

            score=score,

            confidence=confidence,

            rationale=rationale,

            evidence={

                "structure_label":
                    label,

                "structure_confidence":
                    confidence
            }
        )
        
from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory,
    AgentSignalType
)

from backend.institutional.cot_feature_builder import (
    COTFeatureBuilder
)

from backend.institutional.cot_research_protocol import (
    MINIMUM_COT_HISTORY_REPORTS
)


class COTPositioningAgent(
    BaseAgent
):

    agent_id = (
        "cot_positioning"
    )

    agent_name = (
        "COT / Institutional Positioning"
    )

    category = (
        AgentCategory.INSTITUTIONAL
    )

    signal_type = (
        AgentSignalType.CONTEXTUAL
    )

    version = "1.0.0"


    def __init__(
        self,
        feature_builder=None
    ):

        self.feature_builder = (
            feature_builder
            if feature_builder is not None
            else COTFeatureBuilder()
        )


        super().__init__()


    def _insufficient_history_opinion(
        self,
        context
    ):

        report_count = len(
            context.cot_data
        )


        if report_count:

            latest = (
                context.cot_data[-1]
            )

            latest_report_date = (
                latest.report_date
                .isoformat()
            )

            latest_available_at = (
                latest.available_at
                .isoformat()
            )

        else:

            latest_report_date = None

            latest_available_at = None


        return self.build_opinion(

            context=context,

            score=0.0,

            confidence=0.0,

            rationale=(
                "Causal CFTC TFF positioning "
                "history is insufficient for "
                "the frozen Seat 22 feature "
                "contract. No directional "
                "positioning inference was "
                "produced."
            ),

            evidence={

                "cot_status":
                    "insufficient_history",

                "research_role":
                    "contextual",

                "directional_mapping_authorized":
                    False,

                "report_count":
                    report_count,

                "minimum_required_reports":
                    MINIMUM_COT_HISTORY_REPORTS,

                "latest_report_date":
                    latest_report_date,

                "latest_available_at":
                    latest_available_at
            }
        )


    def analyze(
        self,
        context
    ):

        # ==================================
        # MINIMUM CAUSAL HISTORY
        # ==================================

        if (
            len(
                context.cot_data
            )
            < MINIMUM_COT_HISTORY_REPORTS
        ):

            return (
                self
                ._insufficient_history_opinion(
                    context
                )
            )


        # ==================================
        # FROZEN COT FEATURE CONTRACT
        # ==================================

        feature_vector = (
            self.feature_builder.build(
                context.cot_data
            )
        )


        features = (
            feature_vector.as_dict()
        )


        evidence = {

            "cot_status":
                "feature_ready",

            "research_role":
                "contextual",

            "directional_mapping_authorized":
                False,

            "report_count":
                len(
                    context.cot_data
                ),

            "minimum_required_reports":
                MINIMUM_COT_HISTORY_REPORTS,

            "feature_version":
                feature_vector
                .feature_version,

            "feature_count":
                len(
                    feature_vector
                    .feature_names
                ),

            "current_report_date":
                feature_vector
                .report_date
                .isoformat(),

            "current_available_at":
                feature_vector
                .available_at
                .isoformat()
        }


        for (
            feature_name,
            feature_value
        ) in features.items():

            evidence[
                feature_name
            ] = float(
                feature_value
            )


        # ==================================
        # CONTEXTUAL PRODUCTION ROLE
        # ==================================
        #
        # Seat 22 currently has no validated
        # directional mapping.
        #
        # Therefore:
        #
        # score      = 0
        # direction  = NEUTRAL
        # confidence = 0
        #
        # The institutional features remain
        # available in evidence for downstream
        # contextual reasoning.
        # ==================================

        return self.build_opinion(

            context=context,

            score=0.0,

            confidence=0.0,

            rationale=(
                "Snapshot-locked CFTC TFF "
                "institutional positioning "
                "features were computed from "
                "causal COT history. Seat 22 "
                "remains contextual under the "
                "frozen research protocol and "
                "does not emit a directional "
                "Council vote."
            ),

            evidence=
                evidence
        )
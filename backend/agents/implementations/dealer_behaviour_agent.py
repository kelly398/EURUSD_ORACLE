from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory,
    AgentSignalType
)

from backend.agents.dealer_behaviour_features import (
    DealerBehaviourFeatureEngine
)


class DealerBehaviourAgent(
    BaseAgent
):

    agent_id = (
        "dealer_behaviour"
    )

    agent_name = (
        "Dealer Behaviour"
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
        feature_engine=None
    ):

        self.feature_engine = (
            feature_engine
            if feature_engine is not None
            else DealerBehaviourFeatureEngine()
        )


        super().__init__()


    def analyze(
        self,
        context
    ):

        if not context.ticks:

            raise RuntimeError(
                "Dealer Behaviour agent requires "
                "raw broker quote ticks."
            )


        features = (
            self.feature_engine
            .analyze(

                ticks=
                    context.ticks,

                as_of=
                    context.as_of
            )
        )


        evidence = {

            # ==================================
            # PROVENANCE / INTERPRETATION
            # ==================================

            "dealer_inventory_observed":
                False,

            "dealer_identity_observed":
                False,

            "dealer_trades_observed":
                False,

            "centralized_order_book":
                False,

            "broker_quote_proxy":
                True,

            "directional_mapping_authorized":
                False,


            # ==================================
            # SAMPLE / BASELINE QUALITY
            # ==================================

            "recent_tick_count":
                features.recent_tick_count,

            "baseline_tick_count":
                features.baseline_tick_count,

            "tick_activity_ratio":
                features.tick_activity_ratio,

            "average_spread_pips":
                features.average_spread_pips,

            "baseline_spread_pips":
                features.baseline_spread_pips,

            "spread_ratio":
                features.spread_ratio,


            # ==================================
            # BID-SIDE QUOTE MANAGEMENT
            # ==================================

            "bid_up_updates":
                features.bid_up_updates,

            "bid_down_updates":
                features.bid_down_updates,

            "bid_directional_updates":
                features.bid_directional_updates,

            "bid_update_imbalance":
                features.bid_update_imbalance,


            # ==================================
            # ASK-SIDE QUOTE MANAGEMENT
            # ==================================

            "ask_up_updates":
                features.ask_up_updates,

            "ask_down_updates":
                features.ask_down_updates,

            "ask_directional_updates":
                features.ask_directional_updates,

            "ask_update_imbalance":
                features.ask_update_imbalance,


            # ==================================
            # CROSS-SIDE QUOTE STRUCTURE
            # ==================================

            "side_update_asymmetry":
                features.side_update_asymmetry,

            "quote_change_updates":
                features.quote_change_updates,

            "one_sided_updates":
                features.one_sided_updates,

            "synchronized_updates":
                features.synchronized_updates,

            "opposed_updates":
                features.opposed_updates,

            "one_sided_update_ratio":
                features.one_sided_update_ratio,

            "synchronized_update_ratio":
                features.synchronized_update_ratio,

            "opposed_update_ratio":
                features.opposed_update_ratio,


            # ==================================
            # SPREAD MANAGEMENT
            # ==================================

            "spread_widen_updates":
                features.spread_widen_updates,

            "spread_narrow_updates":
                features.spread_narrow_updates,

            "spread_directional_updates":
                features.spread_directional_updates,

            "spread_update_imbalance":
                features.spread_update_imbalance,

            "net_spread_change_pips":
                features.net_spread_change_pips,

            "mean_absolute_spread_change_pips":
                (
                    features
                    .mean_absolute_spread_change_pips
                )
        }


        # ==================================
        # CONTEXTUAL-ONLY V1
        # ==================================
        #
        # These observables characterize
        # broker quote-management behavior.
        #
        # They do NOT identify actual dealers,
        # inventory, trades, or a centralized
        # limit-order book.
        #
        # No empirically validated mapping to
        # EUR/USD direction currently exists.
        #
        # Therefore Seat 23 cannot emit a
        # directional Council vote in v1.
        # ==================================

        return self.build_opinion(

            context=context,

            score=0.0,

            confidence=0.0,

            rationale=(
                "Broker bid/ask quote-management "
                "behavior was characterized from "
                "causal raw quote ticks. The data "
                "does not observe dealer identity, "
                "dealer inventory, dealer trades, "
                "or a centralized order book, so "
                "Seat 23 remains contextual and "
                "does not emit a directional vote."
            ),

            evidence=
                evidence
        )
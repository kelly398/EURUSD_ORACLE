from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory,
    AgentSignalType
)

from backend.macro.ecb_calendar_protocol import (
    ECB_CALENDAR_SOURCE,
    ECB_POLICY_EVENT_CURRENCY,
    ECB_POLICY_EVENT_JURISDICTION,
    ECB_POLICY_EVENT_NAME
)


class ECBAgent(
    BaseAgent
):

    agent_id = (
        "ecb"
    )

    agent_name = (
        "ECB"
    )

    category = (
        AgentCategory.MACRO
    )

    signal_type = (
        AgentSignalType.CONTEXTUAL
    )

    version = "1.0.0"


    REQUIRED_RATE_NAMES = (

        "deposit_facility_rate",

        "main_refinancing_operations_rate",

        "marginal_lending_facility_rate"
    )


    def _latest_rate_state(
        self,
        ecb_data
    ):

        latest_by_rate = {}


        for item in ecb_data:

            current = (
                latest_by_rate.get(
                    item.rate_name
                )
            )


            if current is None:

                latest_by_rate[
                    item.rate_name
                ] = item

                continue


            current_key = (

                current.reference_date,

                current.available_at,

                current.source_record_id
            )


            candidate_key = (

                item.reference_date,

                item.available_at,

                item.source_record_id
            )


            if (
                candidate_key
                > current_key
            ):

                latest_by_rate[
                    item.rate_name
                ] = item


        return latest_by_rate


    def _policy_events(
        self,
        economic_calendar
    ):

        return tuple(

            item

            for item
            in economic_calendar

            if (

                item.source
                == ECB_CALENDAR_SOURCE

                and

                item.event_name
                == ECB_POLICY_EVENT_NAME

                and

                item.currency
                == ECB_POLICY_EVENT_CURRENCY

                and

                item.jurisdiction
                == ECB_POLICY_EVENT_JURISDICTION
            )
        )


    def _event_window(
        self,
        seconds_until_event
    ):

        if (
            seconds_until_event
            is None
        ):

            return "none_scheduled"


        if (
            seconds_until_event
            <= 3600.0
        ):

            return "within_1h"


        if (
            seconds_until_event
            <= 6.0 * 3600.0
        ):

            return "within_6h"


        if (
            seconds_until_event
            <= 24.0 * 3600.0
        ):

            return "within_24h"


        return "beyond_24h"


    def analyze(
        self,
        context
    ):

        # ==================================
        # REQUIRED CAUSAL ECB DATA
        # ==================================

        if not context.ecb_data:

            raise RuntimeError(
                "ECB agent requires causal "
                "ECB policy-rate data."
            )


        if not context.economic_calendar:

            raise RuntimeError(
                "ECB agent requires causal "
                "economic-calendar data."
            )


        # ==================================
        # CURRENT ECB RATE STATE
        # ==================================

        latest_by_rate = (
            self._latest_rate_state(
                context.ecb_data
            )
        )


        present_required_rates = tuple(

            rate_name

            for rate_name
            in self.REQUIRED_RATE_NAMES

            if (
                rate_name
                in latest_by_rate
            )
        )


        rate_state_complete = (

            len(
                present_required_rates
            )

            ==

            len(
                self.REQUIRED_RATE_NAMES
            )
        )


        completeness_ratio = (

            len(
                present_required_rates
            )

            /

            len(
                self.REQUIRED_RATE_NAMES
            )
        )


        # ==================================
        # ECB POLICY CALENDAR
        # ==================================

        policy_events = (
            self._policy_events(
                context.economic_calendar
            )
        )


        if not policy_events:

            raise RuntimeError(
                "ECB agent requires at least "
                "one causal ECB monetary-policy "
                "calendar event."
            )


        next_policy_event = None

        previous_policy_event = None


        for item in policy_events:

            if (
                item.scheduled_at
                >= context.as_of
            ):

                if (
                    next_policy_event
                    is None

                    or

                    item.scheduled_at
                    < (
                        next_policy_event
                        .scheduled_at
                    )
                ):

                    next_policy_event = item


            if (
                item.scheduled_at
                < context.as_of
            ):

                if (
                    previous_policy_event
                    is None

                    or

                    item.scheduled_at
                    > (
                        previous_policy_event
                        .scheduled_at
                    )
                ):

                    previous_policy_event = item


        if (
            next_policy_event
            is None
        ):

            seconds_until_next_event = None

        else:

            seconds_until_next_event = (

                next_policy_event
                .scheduled_at

                - context.as_of

            ).total_seconds()


        event_window = (
            self._event_window(
                seconds_until_next_event
            )
        )


        policy_event_imminent = (

            seconds_until_next_event
            is not None

            and

            seconds_until_next_event
            <= (
                6.0
                * 3600.0
            )
        )


        # ==================================
        # CURRENT RATE VALUES
        # ==================================

        deposit = (
            latest_by_rate.get(
                "deposit_facility_rate"
            )
        )


        main_refinancing = (
            latest_by_rate.get(
                "main_refinancing_operations_rate"
            )
        )


        marginal_lending = (
            latest_by_rate.get(
                "marginal_lending_facility_rate"
            )
        )


        if (
            deposit is not None

            and

            main_refinancing is not None
        ):

            deposit_to_mro_spread_bps = (

                (
                    main_refinancing
                    .value_percent

                    - deposit
                    .value_percent
                )

                * 100.0
            )

        else:

            deposit_to_mro_spread_bps = (
                None
            )


        if (
            main_refinancing is not None

            and

            marginal_lending is not None
        ):

            mro_to_marginal_spread_bps = (

                (
                    marginal_lending
                    .value_percent

                    - main_refinancing
                    .value_percent
                )

                * 100.0
            )

        else:

            mro_to_marginal_spread_bps = (
                None
            )


        # ==================================
        # CONTEXT CONFIDENCE
        # ==================================
        #
        # This is confidence in the completeness
        # of the ECB context, NOT confidence in
        # a directional EUR/USD forecast.
        #
        # Calendar provenance contributes the
        # fixed 20% component because at least
        # one valid ECB policy event is required.
        #
        # The remaining 80% is proportional to
        # key-rate completeness.
        # ==================================

        confidence = (

            0.20

            +

            0.80
            * completeness_ratio
        )


        confidence = min(
            1.0,
            max(
                0.0,
                confidence
            )
        )


        # ==================================
        # EVIDENCE
        # ==================================

        evidence = {

            "directional_mapping_authorized":
                False,

            "relative_rate_differential_available":
                False,

            "ecb_rate_snapshot_count":
                len(
                    context.ecb_data
                ),

            "ecb_policy_event_count":
                len(
                    policy_events
                ),

            "rate_state_complete":
                rate_state_complete,

            "required_rate_count":
                len(
                    self.REQUIRED_RATE_NAMES
                ),

            "present_rate_count":
                len(
                    present_required_rates
                ),


            "deposit_facility_rate_percent":
                (
                    None
                    if deposit is None
                    else float(
                        deposit.value_percent
                    )
                ),

            "deposit_facility_reference_date":
                (
                    None
                    if deposit is None
                    else (
                        deposit
                        .reference_date
                        .isoformat()
                    )
                ),

            "deposit_facility_available_at":
                (
                    None
                    if deposit is None
                    else (
                        deposit
                        .available_at
                        .isoformat()
                    )
                ),


            "main_refinancing_rate_percent":
                (
                    None
                    if main_refinancing is None
                    else float(
                        main_refinancing
                        .value_percent
                    )
                ),

            "main_refinancing_reference_date":
                (
                    None
                    if main_refinancing is None
                    else (
                        main_refinancing
                        .reference_date
                        .isoformat()
                    )
                ),

            "main_refinancing_available_at":
                (
                    None
                    if main_refinancing is None
                    else (
                        main_refinancing
                        .available_at
                        .isoformat()
                    )
                ),


            "marginal_lending_rate_percent":
                (
                    None
                    if marginal_lending is None
                    else float(
                        marginal_lending
                        .value_percent
                    )
                ),

            "marginal_lending_reference_date":
                (
                    None
                    if marginal_lending is None
                    else (
                        marginal_lending
                        .reference_date
                        .isoformat()
                    )
                ),

            "marginal_lending_available_at":
                (
                    None
                    if marginal_lending is None
                    else (
                        marginal_lending
                        .available_at
                        .isoformat()
                    )
                ),


            "deposit_to_mro_spread_bps":
                deposit_to_mro_spread_bps,

            "mro_to_marginal_spread_bps":
                mro_to_marginal_spread_bps,


            "previous_policy_event_id":
                (
                    None
                    if previous_policy_event
                    is None
                    else (
                        previous_policy_event
                        .source_event_id
                    )
                ),

            "previous_policy_event_at":
                (
                    None
                    if previous_policy_event
                    is None
                    else (
                        previous_policy_event
                        .scheduled_at
                        .isoformat()
                    )
                ),


            "next_policy_event_id":
                (
                    None
                    if next_policy_event
                    is None
                    else (
                        next_policy_event
                        .source_event_id
                    )
                ),

            "next_policy_event_at":
                (
                    None
                    if next_policy_event
                    is None
                    else (
                        next_policy_event
                        .scheduled_at
                        .isoformat()
                    )
                ),

            "seconds_until_next_policy_event":
                seconds_until_next_event,

            "policy_event_window":
                event_window,

            "policy_event_imminent":
                policy_event_imminent
        }


        # ==================================
        # CONTEXTUAL-ONLY SEAT 24
        # ==================================
        #
        # ECB data alone does not establish a
        # EUR/USD directional trade.
        #
        # Relative EUR-vs-USD monetary policy
        # requires the Federal Reserve and rate
        # differential layers.
        # ==================================

        return self.build_opinion(

            context=context,

            score=0.0,

            confidence=
                confidence,

            rationale=(
                "Causal ECB key-policy-rate state "
                "and known ECB monetary-policy "
                "event timing were summarized. "
                "Seat 24 is contextual-only in v1 "
                "and does not convert ECB data "
                "alone into an EUR/USD directional "
                "vote; relative monetary-policy "
                "interpretation belongs to the "
                "Fed and rate-differential layers."
            ),

            evidence=
                evidence
        )
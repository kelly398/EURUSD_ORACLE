from datetime import (
    timedelta
)

from backend.agents.contracts import (
    AgentContext
)

from backend.market_state.candle_repository import (
    CandleRepository
)

from backend.market_state.tick_repository import (
    TickRepository
)

from backend.macro.ecb_rate_protocol import (
    ECB_RATE_SOURCE
)

from backend.macro.ecb_calendar_protocol import (
    ECB_CALENDAR_SOURCE,
    ECB_POLICY_EVENT_CURRENCY,
    ECB_POLICY_EVENT_JURISDICTION
)

from backend.macro.macro_repository import (
    CentralBankRateRepository,
    EconomicCalendarRepository
)


class AgentContextFactory:

    def __init__(
        self,
        candle_limit=200,
        tick_lookback_minutes=65,
        require_ticks=True,
        ecb_history_limit=256,
        macro_calendar_lookback_days=370,
        macro_calendar_lookahead_days=370,
        ecb_rate_repository=None,
        economic_calendar_repository=None
    ):

        if candle_limit <= 0:

            raise ValueError(
                "candle_limit must be "
                "greater than zero."
            )


        if tick_lookback_minutes <= 0:

            raise ValueError(
                "tick_lookback_minutes must "
                "be greater than zero."
            )


        if ecb_history_limit <= 0:

            raise ValueError(
                "ecb_history_limit must be "
                "greater than zero."
            )


        if macro_calendar_lookback_days <= 0:

            raise ValueError(
                "macro_calendar_lookback_days "
                "must be greater than zero."
            )


        if macro_calendar_lookahead_days <= 0:

            raise ValueError(
                "macro_calendar_lookahead_days "
                "must be greater than zero."
            )


        self.candle_limit = (
            candle_limit
        )

        self.tick_lookback_minutes = (
            tick_lookback_minutes
        )

        self.require_ticks = (
            require_ticks
        )

        self.ecb_history_limit = (
            ecb_history_limit
        )

        self.macro_calendar_lookback_days = (
            macro_calendar_lookback_days
        )

        self.macro_calendar_lookahead_days = (
            macro_calendar_lookahead_days
        )


        self.candle_repository = (
            CandleRepository(
                source="MT5"
            )
        )


        self.tick_repository = (
            TickRepository()
        )


        self.ecb_rate_repository = (

            ecb_rate_repository

            if (
                ecb_rate_repository
                is not None
            )

            else CentralBankRateRepository(

                source=
                    ECB_RATE_SOURCE,

                central_bank="ECB"
            )
        )


        self.economic_calendar_repository = (

            economic_calendar_repository

            if (
                economic_calendar_repository
                is not None
            )

            else EconomicCalendarRepository(

                source=
                    ECB_CALENDAR_SOURCE,

                currency=
                    ECB_POLICY_EVENT_CURRENCY,

                jurisdiction=
                    ECB_POLICY_EVENT_JURISDICTION
            )
        )


    def _load_macro_context(
        self,
        as_of
    ):

        # ==================================
        # CAUSAL ECB RATE HISTORY
        # ==================================

        ecb_data = tuple(

            self.ecb_rate_repository
            .get_history_as_of(

                as_of=
                    as_of,

                limit=
                    self.ecb_history_limit
            )
        )


        # ==================================
        # CAUSAL ECONOMIC CALENDAR
        # ==================================
        #
        # Future scheduled events remain
        # eligible when their exact record
        # version was already public by as_of.
        # ==================================

        scheduled_start = (

            as_of

            - timedelta(
                days=
                    self.macro_calendar_lookback_days
            )
        )


        scheduled_end = (

            as_of

            + timedelta(
                days=
                    self.macro_calendar_lookahead_days
            )
        )


        economic_calendar = tuple(

            self.economic_calendar_repository
            .get_window_as_of(

                as_of=
                    as_of,

                scheduled_start=
                    scheduled_start,

                scheduled_end=
                    scheduled_end
            )
        )


        return (
            ecb_data,
            economic_calendar
        )


    def build(
        self,
        unified_result
    ):

        snapshot = (
            unified_result.snapshot
        )


        anchor = (
            unified_result
            .anchor_candle_timestamp
        )


        # ==================================
        # SNAPSHOT-LOCKED CANDLES
        # ==================================

        candles = (

            self.candle_repository
            .get_latest_as_of(

                latest_timestamp=
                    anchor,

                symbol=
                    snapshot.symbol,

                timeframe=
                    snapshot.timeframe,

                limit=
                    self.candle_limit
            )
        )


        if not candles:

            raise RuntimeError(
                "AgentContextFactory received "
                "no canonical candles."
            )


        if (
            candles[-1].timestamp
            != anchor
        ):

            raise RuntimeError(
                "Agent candle history is not "
                "aligned to the unified "
                "snapshot anchor."
            )


        # ==================================
        # SNAPSHOT-LOCKED RAW TICKS
        # ==================================

        tick_start = (

            snapshot.as_of

            - timedelta(
                minutes=
                    self.tick_lookback_minutes
            )
        )


        ticks = (

            self.tick_repository
            .get_between(

                start=
                    tick_start,

                end=
                    snapshot.as_of,

                symbol=
                    snapshot.symbol
            )
        )


        if (
            self.require_ticks
            and not ticks
        ):

            raise RuntimeError(
                "AgentContextFactory received "
                "no raw ticks."
            )


        # ==================================
        # SNAPSHOT-LOCKED MACRO CONTEXT
        # ==================================

        (
            ecb_data,
            economic_calendar
        ) = self._load_macro_context(
            snapshot.as_of
        )


        # ==================================
        # IMMUTABLE AGENT CONTEXT
        # ==================================

        return AgentContext(

            symbol=
                snapshot.symbol,

            timeframe=
                snapshot.timeframe,

            as_of=
                snapshot.as_of,

            market_state=
                snapshot,

            candles=
                tuple(candles),

            ticks=
                tuple(ticks),

            ecb_data=
                ecb_data,

            economic_calendar=
                economic_calendar
        )
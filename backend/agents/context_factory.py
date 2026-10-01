from datetime import timedelta

from backend.agents.contracts import (
    AgentContext
)

from backend.market_state.candle_repository import (
    CandleRepository
)

from backend.market_state.tick_repository import (
    TickRepository
)


class AgentContextFactory:

    def __init__(
        self,
        candle_limit=200,
        tick_lookback_minutes=65,
        require_ticks=True
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


        self.candle_limit = (
            candle_limit
        )

        self.tick_lookback_minutes = (
            tick_lookback_minutes
        )

        self.require_ticks = (
            require_ticks
        )


        self.candle_repository = (
            CandleRepository(
                source="MT5"
            )
        )


        self.tick_repository = (
            TickRepository()
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
                tuple(ticks)
        )
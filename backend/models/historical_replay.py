from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta
)

from backend.agents.contracts import (
    AgentContext
)

from backend.market_state.schemas import (
    DataQualityStatus,
    MarketStateSnapshot
)

from backend.market_state.trend_engine import (
    TrendEngine
)

from backend.market_state.volatility_engine import (
    VolatilityEngine
)

from backend.market_state.momentum_engine import (
    MomentumEngine
)

from backend.market_state.structure_engine import (
    MarketStructureEngine
)

from backend.market_state.liquidity_engine import (
    LiquidityEngine
)

from backend.market_state.session_engine import (
    SessionEngine
)


@dataclass(frozen=True)
class HistoricalReplayResult:

    anchor_timestamp: datetime

    as_of: datetime

    context: AgentContext

    candle_count: int

    tick_count: int


class HistoricalReplayBuilder:

    def __init__(
        self,
        symbol="EURUSD",
        timeframe="M5",
        timeframe_minutes=5,
        candle_limit=200,
        tick_lookback_minutes=65
    ):

        self.symbol = symbol

        self.timeframe = timeframe

        self.timeframe_minutes = (
            timeframe_minutes
        )

        self.candle_limit = (
            candle_limit
        )

        self.tick_lookback_minutes = (
            tick_lookback_minutes
        )


        self.trend_engine = (
            TrendEngine()
        )


        self.volatility_engine = (
            VolatilityEngine()
        )


        self.momentum_engine = (
            MomentumEngine()
        )


        self.structure_engine = (
            MarketStructureEngine()
        )


        self.liquidity_engine = (
            LiquidityEngine()
        )


        self.session_engine = (
            SessionEngine()
        )


    def build(
        self,
        candles,
        ticks,
        anchor_timestamp
    ):

        candles = list(
            candles
        )

        ticks = list(
            ticks
        )


        if not candles:

            raise ValueError(
                "Historical replay requires candles."
            )


        if (
            anchor_timestamp.tzinfo
            is None
        ):

            raise ValueError(
                "anchor_timestamp must "
                "be timezone-aware."
            )


        # ==================================
        # CANDLE VALIDATION
        # ==================================

        for previous, current in zip(
            candles,
            candles[1:]
        ):

            if (
                current.timestamp
                <= previous.timestamp
            ):

                raise ValueError(
                    "Historical candles must "
                    "be strictly chronological."
                )


        for candle in candles:

            if (
                candle.symbol
                != self.symbol
            ):

                raise ValueError(
                    "Historical replay contains "
                    "mixed candle symbols."
                )


            if (
                candle.timeframe
                != self.timeframe
            ):

                raise ValueError(
                    "Historical replay contains "
                    "mixed candle timeframes."
                )


        # Absolutely no candle after the
        # requested historical anchor may
        # enter an intelligence engine.
        history = [

            candle

            for candle in candles

            if (
                candle.timestamp
                <= anchor_timestamp
            )
        ]


        if not history:

            raise ValueError(
                "No candles exist at or before "
                "historical anchor."
            )


        if (
            history[-1].timestamp
            != anchor_timestamp
        ):

            raise ValueError(
                "Historical anchor candle "
                "is missing."
            )


        history = (
            history[
                -self.candle_limit:
            ]
        )


        # Volatility currently has the
        # largest minimum requirement.
        if len(history) < 140:

            raise ValueError(
                "Historical replay requires "
                "at least 140 candles."
            )


        as_of = (

            anchor_timestamp

            + timedelta(
                minutes=
                    self.timeframe_minutes
            )
        )


        # ==================================
        # TICK VALIDATION + LOCK
        # ==================================

        tick_start = (

            as_of

            - timedelta(
                minutes=
                    self.tick_lookback_minutes
            )
        )


        tick_window = [

            tick

            for tick in ticks

            if (
                tick_start
                <= tick.timestamp
                < as_of
            )
        ]


        if not tick_window:

            raise ValueError(
                "No historical ticks available "
                "for replay window."
            )


        for tick in tick_window:

            if (
                tick.symbol
                != self.symbol
            ):

                raise ValueError(
                    "Historical replay contains "
                    "mixed tick symbols."
                )


        tick_window.sort(
            key=lambda tick: (
                tick.timestamp,
                tick.source_time_msc
            )
        )


        # ==================================
        # REPLAY MARKET INTELLIGENCE
        # ==================================

        trend = (
            self.trend_engine
            .analyze_detailed(
                history
            )
        )


        volatility = (
            self.volatility_engine
            .analyze_detailed(
                history
            )
        )


        momentum = (
            self.momentum_engine
            .analyze_detailed(
                history
            )
        )


        structure = (
            self.structure_engine
            .analyze_detailed(
                history
            )
        )


        liquidity = (
            self.liquidity_engine
            .analyze_detailed(

                ticks=
                    tick_window,

                as_of=
                    as_of
            )
        )


        session = (
            self.session_engine
            .analyze(
                as_of
            )
        )


        snapshot = (
            MarketStateSnapshot(

                symbol=
                    self.symbol,

                timeframe=
                    self.timeframe,

                as_of=
                    as_of,

                data_quality=
                    DataQualityStatus.PASS,

                candle_count=
                    len(history),

                trend=
                    trend.state,

                volatility=
                    volatility.state,

                momentum=
                    momentum.state,

                structure=
                    structure.state,

                liquidity=
                    liquidity.state,

                session=
                    session.session
            )
        )


        context = AgentContext(

            symbol=
                self.symbol,

            timeframe=
                self.timeframe,

            as_of=
                as_of,

            market_state=
                snapshot,

            candles=
                tuple(history),

            ticks=
                tuple(tick_window)
        )


        return HistoricalReplayResult(

            anchor_timestamp=
                anchor_timestamp,

            as_of=
                as_of,

            context=
                context,

            candle_count=
                len(history),

            tick_count=
                len(tick_window)
        )
        
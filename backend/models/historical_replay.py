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


DEBUG_REPLAY = False


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

            if DEBUG_REPLAY:
                print(
                    "\nREPLAY FAILURE:"
                )
                print(
                    "Reason: No candles supplied"
                )

            raise ValueError(
                "Historical replay requires candles."
            )



        if (
            anchor_timestamp.tzinfo
            is None
        ):

            if DEBUG_REPLAY:
                print(
                    "\nREPLAY FAILURE:"
                )
                print(
                    "Reason: Anchor timestamp has no timezone"
                )

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

                if DEBUG_REPLAY:
                    print(
                        "\nREPLAY FAILURE:"
                    )
                    print(
                        "Reason: candles not chronological"
                    )

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



        history = [

            candle

            for candle in candles

            if (
                candle.timestamp
                <= anchor_timestamp
            )

        ]



        if not history:

            if DEBUG_REPLAY:
                print(
                    "\nREPLAY FAILURE:"
                )
                print(
                    "Reason: No candles before anchor"
                )
                print(
                    "Anchor:",
                    anchor_timestamp
                )

            raise ValueError(
                "No candles exist at or before "
                "historical anchor."
            )



        if (
            history[-1].timestamp
            != anchor_timestamp
        ):

            if DEBUG_REPLAY:

                print(
                    "\nREPLAY FAILURE:"
                )

                print(
                    "Reason: Anchor candle missing"
                )

                print(
                    "Requested:",
                    anchor_timestamp
                )

                print(
                    "Latest candle:",
                    history[-1].timestamp
                )


            raise ValueError(
                "Historical anchor candle "
                "is missing."
            )



        history = (
            history[
                -self.candle_limit:
            ]
        )



        if len(history) < 140:


            if DEBUG_REPLAY:

                print(
                    "\nREPLAY FAILURE:"
                )

                print(
                    "Reason: insufficient candle history"
                )

                print(
                    "Available:",
                    len(history)
                )

                print(
                    "Required:",
                    140
                )


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


            if DEBUG_REPLAY:

                print(
                    "\nREPLAY FAILURE:"
                )

                print(
                    "Reason: No ticks available"
                )

                print(
                    "Tick start:",
                    tick_start
                )

                print(
                    "Tick end:",
                    as_of
                )


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


        try:

            trend = (

                self.trend_engine

                .analyze_detailed(

                    history

                )

            )

        except Exception as e:

            print("\n========== TREND ENGINE FAILURE ==========")
            print("Anchor:", anchor_timestamp)
            print("Candles:", len(history))
            print("Error:", repr(e))
            print("===========================================\n")
            raise



        try:

            volatility = (

                self.volatility_engine

                .analyze_detailed(

                    history

                )

            )

        except Exception as e:

            print("\n====== VOLATILITY ENGINE FAILURE ======")
            print("Anchor:", anchor_timestamp)
            print("Candles:", len(history))
            print("Error:", repr(e))
            print("========================================\n")
            raise



        try:

            momentum = (

                self.momentum_engine

                .analyze_detailed(

                    history

                )

            )

        except Exception as e:

            print("\n======= MOMENTUM ENGINE FAILURE =======")
            print("Anchor:", anchor_timestamp)
            print("Candles:", len(history))
            print("Error:", repr(e))
            print("========================================\n")
            raise



        try:

            structure = (

                self.structure_engine

                .analyze_detailed(

                    history

                )

            )

        except Exception as e:

            print("\n===== STRUCTURE ENGINE FAILURE =====")
            print("Anchor:", anchor_timestamp)
            print("Candles:", len(history))
            print("Error:", repr(e))
            print("====================================\n")
            raise



        try:

            liquidity = (

                self.liquidity_engine

                .analyze_detailed(

                    ticks=tick_window,

                    as_of=as_of

                )

            )

        except Exception as e:

            print("\n===== LIQUIDITY ENGINE FAILURE =====")
            print("Anchor:", anchor_timestamp)
            print("Ticks:", len(tick_window))
            print("Error:", repr(e))
            print("====================================\n")
            raise



        try:

            session = (

                self.session_engine

                .analyze(

                    as_of

                )
            )

        except Exception as e:

            print("\n===== SESSION ENGINE FAILURE =====")
            print("Anchor:", anchor_timestamp)
            print("Error:", repr(e))
            print("==================================\n")
            raise



        snapshot = MarketStateSnapshot(
            symbol=self.symbol,
            timeframe=self.timeframe,
            as_of=as_of,
            data_quality=DataQualityStatus.PASS,
            candle_count=len(history),
            trend=trend.state,
            volatility=volatility.state,
            momentum=momentum.state,
            structure=structure.state,
            liquidity=liquidity.state,
            session=session.session,
        )

        context = AgentContext(

            symbol=self.symbol,

            timeframe=self.timeframe,

            as_of=as_of,

            market_state=snapshot,

            candles=tuple(history),

            ticks=tuple(tick_window)

            )

        if DEBUG_REPLAY:

            print(
                "\nREPLAY SUCCESS:"
            )

            print(
                "Anchor:",
                anchor_timestamp
            )

            print(
                "Candles:",
                len(history)
            )

            print(
                "Ticks:",
                len(tick_window)
            )

            print(
                "ABOUT TO RETURN HISTORICAL REPLAY RESULT"
            )


        return HistoricalReplayResult(

            anchor_timestamp=anchor_timestamp,

            as_of=as_of,

            context=context,

            candle_count=len(history),

            tick_count=len(tick_window)

        )

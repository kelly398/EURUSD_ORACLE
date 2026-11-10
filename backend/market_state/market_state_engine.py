from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta
)

from backend.data_quality.quality_engine import (
    DataQualityEngine
)

from backend.market_state.schemas import (
    DataQualityStatus,
    MarketStateSnapshot
)

from backend.market_state.candle_repository import (
    CandleRepository
)

from backend.market_state.tick_repository import (
    TickRepository
)

from backend.market_state.trend_engine import (
    TrendEngine,
    TrendEngineResult
)

from backend.market_state.volatility_engine import (
    VolatilityEngine,
    VolatilityEngineResult
)

from backend.market_state.momentum_engine import (
    MomentumEngine,
    MomentumEngineResult
)

from backend.market_state.structure_engine import (
    MarketStructureEngine,
    StructureEngineResult
)

from backend.market_state.liquidity_engine import (
    LiquidityEngine,
    LiquidityEngineResult
)

from backend.market_state.session_engine import (
    SessionEngine,
    SessionEngineResult
)
from backend.data_engine.feed_identity import require_feed_id


@dataclass(frozen=True)
class UnifiedMarketStateResult:

    snapshot: MarketStateSnapshot

    anchor_candle_timestamp: datetime

    quality_report: dict

    trend: TrendEngineResult

    volatility: VolatilityEngineResult

    momentum: MomentumEngineResult

    structure: StructureEngineResult

    liquidity: LiquidityEngineResult

    session: SessionEngineResult

    tick_window_start: datetime

    tick_window_end: datetime

    tick_count: int


class MarketStateEngine:

    def __init__(
        self,
        symbol="EURUSD",
        timeframe="M5",
        timeframe_minutes=5,
        candle_limit=200,
        feed_id=None,
        broker_profile=None,
        capture_epoch=None,
    ):

        self.symbol = symbol

        self.timeframe = timeframe

        self.timeframe_minutes = (
            timeframe_minutes
        )

        self.candle_limit = (
            candle_limit
        )
        if feed_id is not None:
            require_feed_id(feed_id)
        self.feed_id = feed_id


        # ==================================
        # DATA GATE
        # ==================================

        self.data_quality_engine = (
            DataQualityEngine(

                symbol=symbol,

                timeframe=timeframe,

                timeframe_minutes=
                    timeframe_minutes,

                lookback_hours=6,
                feed_id=feed_id,
                broker_profile=broker_profile,
                capture_epoch=capture_epoch,
            )
        )


        # ==================================
        # REPOSITORIES
        # ==================================

        self.candle_repository = (
            CandleRepository(
                source="MT5",
                feed_id=feed_id,
            )
        )


        self.tick_repository = (
            TickRepository(feed_id=feed_id)
        )


        # ==================================
        # INTELLIGENCE ENGINES
        # ==================================

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


    def build_snapshot(
        self
    ) -> UnifiedMarketStateResult:

        # ==================================
        # 1. DATA QUALITY GATE
        # ==================================

        quality_report = (
            self.data_quality_engine.run()
        )


        quality_status = (
            quality_report[
                "status"
            ]
        )


        # For ORACLE's live intelligence,
        # WARNING is not sufficient.
        #
        # Prediction engines should only
        # receive fully healthy market
        # data.
        if (
            quality_status
            != DataQualityStatus.PASS.value
        ):

            raise RuntimeError(
                "Market State Engine blocked "
                "by data quality gate. "
                f"Status: {quality_status}. "
                f"Issues: "
                f"{quality_report['issues']}"
            )


        # ==================================
        # 2. LOCK THE SNAPSHOT TIMESTAMP
        # ==================================

        anchor_timestamp = (
            quality_report[
                "latest_broker_candle"
            ]
        )


        expected_timestamp = (
            quality_report[
                "expected_latest_candle"
            ]
        )


        if anchor_timestamp is None:

            raise RuntimeError(
                "No canonical broker candle "
                "available for market state."
            )


        if expected_timestamp is None:

            raise RuntimeError(
                "Unable to determine the "
                "expected completed M5 candle."
            )


        if (
            anchor_timestamp
            != expected_timestamp
        ):

            raise RuntimeError(
                "Canonical candle timestamp "
                "does not match expected "
                "completed M5 timestamp."
            )


        # broker_candles.timestamp
        # represents candle OPEN time.
        #
        # Therefore the state becomes valid
        # at the candle CLOSE time.
        as_of = (

            anchor_timestamp

            + timedelta(
                minutes=
                self.timeframe_minutes
            )
        )


        # ==================================
        # 3. LOAD LOCKED CANDLE HISTORY
        # ==================================

        candles = (

            self.candle_repository
            .get_latest_as_of(

                latest_timestamp=
                    anchor_timestamp,

                symbol=self.symbol,

                timeframe=self.timeframe,

                limit=
                    self.candle_limit
            )
        )


        if not candles:

            raise RuntimeError(
                "No canonical candles "
                "were returned."
            )


        if (
            candles[-1].timestamp
            != anchor_timestamp
        ):

            raise RuntimeError(
                "Candle repository failed "
                "snapshot timestamp lock."
            )


        # ==================================
        # 4. TREND
        # ==================================

        trend_result = (
            self.trend_engine
            .analyze_detailed(
                candles
            )
        )


        # ==================================
        # 5. VOLATILITY
        # ==================================

        volatility_result = (
            self.volatility_engine
            .analyze_detailed(
                candles
            )
        )


        # ==================================
        # 6. MOMENTUM
        # ==================================

        momentum_result = (
            self.momentum_engine
            .analyze_detailed(
                candles
            )
        )


        # ==================================
        # 7. MARKET STRUCTURE
        # ==================================

        structure_result = (
            self.structure_engine
            .analyze_detailed(
                candles
            )
        )


        # ==================================
        # 8. LIQUIDITY WINDOW
        # ==================================

        total_liquidity_minutes = (

            self.liquidity_engine
            .recent_minutes

            + self.liquidity_engine
            .baseline_minutes
        )


        tick_window_start = (

            as_of

            - timedelta(
                minutes=
                    total_liquidity_minutes
            )
        )


        tick_window_end = (
            as_of
        )


        ticks = (

            self.tick_repository
            .get_between(

                start=
                    tick_window_start,

                end=
                    tick_window_end,

                symbol=
                    self.symbol
            )
        )


        if not ticks:

            raise RuntimeError(
                "No raw ticks available "
                "for liquidity analysis."
            )


        liquidity_result = (
            self.liquidity_engine
            .analyze_detailed(

                ticks=ticks,

                as_of=as_of
            )
        )


        # ==================================
        # 9. TRADING SESSION
        # ==================================

        session_result = (
            self.session_engine
            .analyze(
                as_of
            )
        )


        # ==================================
        # 10. BUILD UNIFIED SNAPSHOT
        # ==================================

        snapshot = (
            MarketStateSnapshot(

                symbol=
                    self.symbol,

                feed_id=
                    self.feed_id,

                timeframe=
                    self.timeframe,

                as_of=
                    as_of,

                data_quality=
                    DataQualityStatus.PASS,

                candle_count=
                    len(candles),

                trend=
                    trend_result.state,

                volatility=
                    volatility_result.state,

                momentum=
                    momentum_result.state,

                structure=
                    structure_result.state,

                liquidity=
                    liquidity_result.state,

                session=
                    session_result.session
            )
        )


        return UnifiedMarketStateResult(

            snapshot=
                snapshot,

            anchor_candle_timestamp=
                anchor_timestamp,

            quality_report=
                quality_report,

            trend=
                trend_result,

            volatility=
                volatility_result,

            momentum=
                momentum_result,

            structure=
                structure_result,

            liquidity=
                liquidity_result,

            session=
                session_result,

            tick_window_start=
                tick_window_start,

            tick_window_end=
                tick_window_end,

            tick_count=
                len(ticks)
        )


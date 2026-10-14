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

from backend.institutional.cot_schemas import (
    COTPositioningSnapshot
)

from backend.macro.macro_schemas import (
    CentralBankRateSnapshot,
    EconomicCalendarEventSnapshot
)

from backend.macro.fed_replay_lock import (
    lock_fed_data_as_of
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

    def has_sufficient_tick_context(
        self,
        ticks,
        as_of
    ):
        tick_start = (
            as_of
            - timedelta(
                minutes=self.tick_lookback_minutes
            )
        )

        window = [
            tick
            for tick in ticks
            if (
                tick_start
                <= tick.timestamp
                < as_of
            )
        ]

        if len(window) < 1:
            return False

        recent_start = (
            as_of
            - timedelta(
                minutes=self.liquidity_engine.recent_minutes
            )
        )

        baseline_start = (
            recent_start
            - timedelta(
                minutes=self.liquidity_engine.baseline_minutes
            )
        )

        baseline = [
            tick
            for tick in window
            if (
                baseline_start
                <= tick.timestamp
                < recent_start
            )
        ]
        recent = [
            tick
            for tick in window
            if (
                recent_start
                <= tick.timestamp
                < as_of
            )
        ]

        return (
            len(recent)
            >= self.liquidity_engine.minimum_recent_ticks
            and
            len(baseline)
            >= self.liquidity_engine.minimum_baseline_ticks
        )

    def _lock_cot_data_as_of(
        self,
        cot_data,
        as_of
    ):

        """
        Build the exact COT information set that
        was legally available at `as_of`.

        Rules:

        1. Future/unreleased reports are excluded.
        2. Future corrections are excluded.
        3. For each report_date, use the newest
           revision available by `as_of`.
        4. Output is strictly chronological by
           report_date.
        """

        cot_data = tuple(
            cot_data
        )


        if not cot_data:

            return tuple()


        for item in cot_data:

            if not isinstance(
                item,
                COTPositioningSnapshot
            ):

                raise TypeError(
                    "Historical COT input must "
                    "contain "
                    "COTPositioningSnapshot."
                )


        eligible = tuple(

            item

            for item
            in cot_data

            if (
                item.available_at
                <= as_of
            )
        )


        if not eligible:

            return tuple()


        latest_by_report_date = {}


        for item in eligible:

            existing = (
                latest_by_report_date
                .get(
                    item.report_date
                )
            )


            if existing is None:

                latest_by_report_date[
                    item.report_date
                ] = item

                continue


            if (
                item.available_at
                > existing.available_at
            ):

                latest_by_report_date[
                    item.report_date
                ] = item

                continue


            if (
                item.available_at
                == existing.available_at

                and

                item.content_hash
                != existing.content_hash
            ):

                raise ValueError(
                    "Ambiguous COT revisions "
                    "share the same report_date "
                    "and available_at timestamp."
                )


        locked = tuple(

            sorted(

                latest_by_report_date.values(),

                key=lambda item:
                    item.report_date
            )
        )


        return locked

    def _lock_ecb_data_as_of(
        self,
        ecb_data,
        as_of
    ):

        """
        Build the exact ECB rate information set
        legally available at `as_of`.

        Revision identity is source_record_id.
        """

        ecb_data = tuple(
            ecb_data
        )


        if not ecb_data:

            return tuple()


        for item in ecb_data:

            if not isinstance(
                item,
                CentralBankRateSnapshot
            ):

                raise TypeError(
                    "Historical ECB input must "
                    "contain "
                    "CentralBankRateSnapshot."
                )


            if (
                item.central_bank
                != "ECB"
            ):

                raise ValueError(
                    "Historical ecb_data "
                    "contains a non-ECB "
                    "central-bank snapshot."
                )


        eligible = tuple(

            item

            for item
            in ecb_data

            if (
                item.available_at
                <= as_of
            )
        )


        if not eligible:

            return tuple()


        latest_by_source_record = {}


        for item in eligible:

            existing = (
                latest_by_source_record
                .get(
                    item.source_record_id
                )
            )


            if existing is None:

                latest_by_source_record[
                    item.source_record_id
                ] = item

                continue


            if (
                item.available_at
                > existing.available_at
            ):

                latest_by_source_record[
                    item.source_record_id
                ] = item

                continue


            if (
                item.available_at
                == existing.available_at

                and

                item.content_hash
                != existing.content_hash
            ):

                raise ValueError(
                    "Ambiguous ECB revisions "
                    "share the same "
                    "source_record_id and "
                    "available_at timestamp."
                )


        return tuple(

            sorted(

                latest_by_source_record
                .values(),

                key=lambda item: (
                    item.reference_date,
                    item.rate_name,
                    item.source_record_id
                )
            )
        )

    def _lock_fed_data_as_of(
        self,
        fed_data,
        as_of
    ):
        return lock_fed_data_as_of(
            fed_data=fed_data,
            as_of=as_of
        )

    def _lock_economic_calendar_as_of(
        self,
        economic_calendar,
        as_of
    ):

        """
        Build the exact economic-calendar
        information set legally available at
        `as_of`.

        A scheduled event is retained even when
        scheduled_at is in the future, provided
        its exact record version was already
        public by as_of.

        Revision identity is source_event_id.
        """

        economic_calendar = tuple(
            economic_calendar
        )


        if not economic_calendar:

            return tuple()


        for item in economic_calendar:

            if not isinstance(
                item,
                EconomicCalendarEventSnapshot
            ):

                raise TypeError(
                    "Historical economic-calendar "
                    "input must contain "
                    "EconomicCalendarEventSnapshot."
                )


        eligible = tuple(

            item

            for item
            in economic_calendar

            if (
                item.available_at
                <= as_of
            )
        )


        if not eligible:

            return tuple()


        latest_by_event = {}


        for item in eligible:

            existing = (
                latest_by_event
                .get(
                    item.source_event_id
                )
            )


            if existing is None:

                latest_by_event[
                    item.source_event_id
                ] = item

                continue


            if (
                item.available_at
                > existing.available_at
            ):

                latest_by_event[
                    item.source_event_id
                ] = item

                continue


            if (
                item.available_at
                == existing.available_at

                and

                item.content_hash
                != existing.content_hash
            ):

                raise ValueError(
                    "Ambiguous economic-calendar "
                    "revisions share the same "
                    "source_event_id and "
                    "available_at timestamp."
                )


        return tuple(

            sorted(

                latest_by_event
                .values(),

                key=lambda item: (
                    item.scheduled_at,
                    item.source_event_id
                )
            )
        )

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
        anchor_timestamp,
        ticks_prebounded=False,
        cot_data=tuple(),
        ecb_data=tuple(),
        economic_calendar=tuple(),
        fed_data=tuple()
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
        # FED SNAPSHOT LOCK
        # HISTORICAL ADMISSION DISABLED
        # ==================================
        # An economic content hash and an
        # ingestion timestamp do not prove
        # independent historical authenticity.
        #
        # Until independently attested source
        # versions exist, historical replay
        # must not admit Fed snapshots.
        # ==================================

        if fed_data is None:
            raise TypeError(
                "Historical Fed input cannot be None."
            )

        fed_input = tuple(fed_data)

        if fed_input:
            raise ValueError(
                "Historical Fed replay admission is disabled "
                "pending independent source-version certification."
            )

        fed_window = self._lock_fed_data_as_of(
            fed_data=fed_input,
            as_of=as_of
        )

        # Defense in depth: even if the lock is
        # subsequently changed, no Fed context
        # may be admitted through this checkpoint.
        if fed_window:
            raise ValueError(
                "Historical Fed replay admission is disabled "
                "pending independent source-version certification."
            )


        # ==================================
        # COT SNAPSHOT LOCK
        # ==================================

        cot_window = (
            self._lock_cot_data_as_of(
                cot_data=cot_data,
                as_of=as_of
            )
        )
        # ==================================
        # ECB SNAPSHOT LOCK
        # ==================================

        ecb_window = (
            self._lock_ecb_data_as_of(

                ecb_data=
                    ecb_data,

                as_of=
                    as_of
            )
        )


        # ==================================
        # ECONOMIC CALENDAR SNAPSHOT LOCK
        # ==================================

        economic_calendar_window = (
            self._lock_economic_calendar_as_of(

                economic_calendar=
                    economic_calendar,

                as_of=
                    as_of
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



        if ticks_prebounded:

            tick_window = ticks

            if tick_window:

                if (
                    tick_window[0].timestamp < tick_start
                    or tick_window[-1].timestamp >= as_of
                ):
                    raise ValueError(
                        "Pre-bounded ticks fall outside "
                        "the replay window."
                    )

        else:

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



        if not ticks_prebounded:

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

            ticks=tuple(tick_window),

            cot_data=cot_window,

            ecb_data=ecb_window,

            fed_data=fed_window,

            economic_calendar=(
                economic_calendar_window
            )
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

from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta
)
from statistics import mean
from typing import Sequence

from backend.market_state.schemas import (
    LiquidityState,
    SpreadState,
    TickSample
)


@dataclass(frozen=True)
class LiquidityEngineResult:

    state: LiquidityState

    spread_ratio: float

    tick_activity_ratio: float

    recent_average_spread_pips: float

    baseline_average_spread_pips: float

    recent_ticks_per_minute: float

    baseline_ticks_per_minute: float

    recent_tick_count: int

    baseline_tick_count: int


class LiquidityEngine:

    def __init__(
        self,
        recent_minutes=5,
        baseline_minutes=60,
        minimum_recent_ticks=5,
        minimum_baseline_ticks=20
    ):

        self.recent_minutes = (
            recent_minutes
        )

        self.baseline_minutes = (
            baseline_minutes
        )

        self.minimum_recent_ticks = (
            minimum_recent_ticks
        )

        self.minimum_baseline_ticks = (
            minimum_baseline_ticks
        )


    def _validate(
        self,
        ticks
    ):

        if not ticks:

            raise ValueError(
                "LiquidityEngine received "
                "no tick data."
            )


        symbol = (
            ticks[0].symbol
        )


        for tick in ticks:

            if tick.symbol != symbol:

                raise ValueError(
                    "Mixed symbols supplied "
                    "to LiquidityEngine."
                )


        for previous, current in zip(
            ticks,
            ticks[1:]
        ):

            # Equal timestamps are allowed.
            # MT5 can contain multiple
            # legitimate ticks with the
            # same timestamp.
            if (
                current.timestamp
                < previous.timestamp
            ):

                raise ValueError(
                    "Ticks must be in "
                    "chronological order."
                )


    @staticmethod
    def _spread_state(
        ratio
    ):

        if ratio <= 0.75:

            return SpreadState.TIGHT


        if ratio < 1.50:

            return SpreadState.NORMAL


        if ratio < 2.50:

            return SpreadState.WIDE


        return SpreadState.EXTREME


    @staticmethod
    def _activity_state(
        ratio
    ):

        if ratio < 0.50:

            return "low"


        if ratio < 1.50:

            return "normal"


        if ratio < 2.50:

            return "high"


        return "extreme"


    def analyze_detailed(
        self,
        ticks: Sequence[TickSample],
        as_of: datetime
    ) -> LiquidityEngineResult:

        ticks = list(
            ticks
        )

        if ticks:

            if (
                ticks[-1].timestamp
                > as_of
            ):

                raise ValueError(
                    "LiquidityEngine received "
                    "ticks with timestamps "
                    "after the as_of time."
                )

        self._validate(
            ticks
        )


        recent_start = (
            as_of
            - timedelta(
                minutes=
                self.recent_minutes
            )
        )


        baseline_start = (
            recent_start
            - timedelta(
                minutes=
                self.baseline_minutes
            )
        )


        # ==================================
        # SPLIT BASELINE AND CURRENT WINDOW
        # ==================================

        baseline_ticks = [

            tick

            for tick in ticks

            if (
                baseline_start
                <= tick.timestamp
                < recent_start
            )
        ]


        recent_ticks = [

            tick

            for tick in ticks

            if (
                recent_start
                <= tick.timestamp
                < as_of
            )
        ]


        if (
            len(recent_ticks)
            < self.minimum_recent_ticks
        ):

            raise ValueError(
                "Insufficient recent ticks "
                "for liquidity analysis. "
                f"Found {len(recent_ticks)}, "
                f"need at least "
                f"{self.minimum_recent_ticks}."
            )


        if (
            len(baseline_ticks)
            < self.minimum_baseline_ticks
        ):

            raise ValueError(
                "Insufficient baseline ticks "
                "for liquidity analysis. "
                f"Found {len(baseline_ticks)}, "
                f"need at least "
                f"{self.minimum_baseline_ticks}."
            )


        # ==================================
        # SPREAD CONDITION
        # ==================================

        recent_average_spread = mean(

            tick.spread_pips

            for tick in recent_ticks
        )


        baseline_average_spread = mean(

            tick.spread_pips

            for tick in baseline_ticks
        )


        if baseline_average_spread <= 0:

            spread_ratio = 1.0

        else:

            spread_ratio = (

                recent_average_spread
                / baseline_average_spread
            )


        spread_state = (
            self._spread_state(
                spread_ratio
            )
        )


        # ==================================
        # TICK ACTIVITY
        # ==================================

        recent_ticks_per_minute = (

            len(recent_ticks)
            / self.recent_minutes
        )


        baseline_ticks_per_minute = (

            len(baseline_ticks)
            / self.baseline_minutes
        )


        if baseline_ticks_per_minute <= 0:

            tick_activity_ratio = 1.0

        else:

            tick_activity_ratio = (

                recent_ticks_per_minute
                / baseline_ticks_per_minute
            )


        tick_activity = (
            self._activity_state(
                tick_activity_ratio
            )
        )


        # ==================================
        # PUBLIC MARKET STATE
        # ==================================

        state = LiquidityState(

            spread_state=
                spread_state,

            average_spread_pips=
                recent_average_spread,

            tick_activity=
                tick_activity
        )


        return LiquidityEngineResult(

            state=state,

            spread_ratio=
                spread_ratio,

            tick_activity_ratio=
                tick_activity_ratio,

            recent_average_spread_pips=
                recent_average_spread,

            baseline_average_spread_pips=
                baseline_average_spread,

            recent_ticks_per_minute=
                recent_ticks_per_minute,

            baseline_ticks_per_minute=
                baseline_ticks_per_minute,

            recent_tick_count=
                len(recent_ticks),

            baseline_tick_count=
                len(baseline_ticks)
        )


    def analyze(
        self,
        ticks: Sequence[TickSample],
        as_of: datetime
    ) -> LiquidityState:

        result = (
            self.analyze_detailed(
                ticks,
                as_of
            )
        )

        return result.state
        
from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta
)
from statistics import mean

from backend.market_state.schemas import (
    TickSample
)


@dataclass(frozen=True)
class MicrostructureFeatures:

    as_of: datetime

    recent_tick_count: int

    baseline_tick_count: int

    recent_ticks_per_minute: float

    baseline_ticks_per_minute: float

    tick_activity_ratio: float

    average_spread_pips: float

    baseline_spread_pips: float

    spread_ratio: float

    up_updates: int

    down_updates: int

    flat_updates: int

    directional_updates: int

    update_imbalance: float

    net_mid_move_pips: float

    mean_absolute_mid_move_pips: float


class MicrostructureFeatureEngine:

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


    @staticmethod
    def _mid(
        tick
    ):

        return (
            tick.bid
            + tick.ask
        ) / 2.0


    def analyze(
        self,
        ticks: tuple[
            TickSample,
            ...
        ],
        as_of: datetime
    ) -> MicrostructureFeatures:

        ticks = list(
            ticks
        )


        if not ticks:

            raise ValueError(
                "Microstructure analysis "
                "requires raw ticks."
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
                "for microstructure analysis."
            )


        if (
            len(baseline_ticks)
            < self.minimum_baseline_ticks
        ):

            raise ValueError(
                "Insufficient baseline ticks "
                "for microstructure analysis."
            )


        recent_spread = mean(

            tick.spread_pips

            for tick in recent_ticks
        )


        baseline_spread = mean(

            tick.spread_pips

            for tick in baseline_ticks
        )


        if baseline_spread <= 0:

            spread_ratio = 1.0

        else:

            spread_ratio = (

                recent_spread
                / baseline_spread
            )


        recent_ticks_per_minute = (

            len(recent_ticks)

            / self.recent_minutes
        )


        baseline_ticks_per_minute = (

            len(baseline_ticks)

            / self.baseline_minutes
        )


        if (
            baseline_ticks_per_minute
            <= 0
        ):

            tick_activity_ratio = 1.0

        else:

            tick_activity_ratio = (

                recent_ticks_per_minute

                / baseline_ticks_per_minute
            )


        mids = [

            self._mid(
                tick
            )

            for tick in recent_ticks
        ]


        changes = [

            current
            - previous

            for previous, current
            in zip(
                mids,
                mids[1:]
            )
        ]


        up_updates = sum(

            1

            for change in changes

            if change > 0
        )


        down_updates = sum(

            1

            for change in changes

            if change < 0
        )


        flat_updates = sum(

            1

            for change in changes

            if change == 0
        )


        directional_updates = (

            up_updates
            + down_updates
        )


        if directional_updates == 0:

            update_imbalance = 0.0

        else:

            update_imbalance = (

                up_updates
                - down_updates

            ) / directional_updates


        if len(mids) >= 2:

            net_mid_move_pips = (

                mids[-1]
                - mids[0]

            ) * 10000.0

        else:

            net_mid_move_pips = 0.0


        if changes:

            mean_absolute_mid_move_pips = (

                mean(
                    abs(change)
                    for change in changes
                )

                * 10000.0
            )

        else:

            mean_absolute_mid_move_pips = 0.0


        return MicrostructureFeatures(

            as_of=
                as_of,

            recent_tick_count=
                len(recent_ticks),

            baseline_tick_count=
                len(baseline_ticks),

            recent_ticks_per_minute=
                recent_ticks_per_minute,

            baseline_ticks_per_minute=
                baseline_ticks_per_minute,

            tick_activity_ratio=
                tick_activity_ratio,

            average_spread_pips=
                recent_spread,

            baseline_spread_pips=
                baseline_spread,

            spread_ratio=
                spread_ratio,

            up_updates=
                up_updates,

            down_updates=
                down_updates,

            flat_updates=
                flat_updates,

            directional_updates=
                directional_updates,

            update_imbalance=
                update_imbalance,

            net_mid_move_pips=
                net_mid_move_pips,

            mean_absolute_mid_move_pips=
                mean_absolute_mid_move_pips
        )
        
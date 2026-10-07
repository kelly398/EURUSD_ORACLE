from dataclasses import (
    dataclass
)

from datetime import (
    datetime,
    timedelta
)

from statistics import (
    mean
)

from backend.agents.microstructure_features import (
    MicrostructureFeatureEngine
)

from backend.market_state.schemas import (
    TickSample
)


@dataclass(
    frozen=True
)
class DealerBehaviourFeatures:

    as_of: datetime

    recent_tick_count: int

    baseline_tick_count: int

    tick_activity_ratio: float

    average_spread_pips: float

    baseline_spread_pips: float

    spread_ratio: float

    bid_up_updates: int

    bid_down_updates: int

    bid_directional_updates: int

    bid_update_imbalance: float

    ask_up_updates: int

    ask_down_updates: int

    ask_directional_updates: int

    ask_update_imbalance: float

    side_update_asymmetry: float

    quote_change_updates: int

    one_sided_updates: int

    synchronized_updates: int

    opposed_updates: int

    one_sided_update_ratio: float

    synchronized_update_ratio: float

    opposed_update_ratio: float

    spread_widen_updates: int

    spread_narrow_updates: int

    spread_directional_updates: int

    spread_update_imbalance: float

    net_spread_change_pips: float

    mean_absolute_spread_change_pips: float


class DealerBehaviourFeatureEngine:

    PRICE_EPSILON = 1e-12


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


        self.microstructure_engine = (
            MicrostructureFeatureEngine(

                recent_minutes=
                    recent_minutes,

                baseline_minutes=
                    baseline_minutes,

                minimum_recent_ticks=
                    minimum_recent_ticks,

                minimum_baseline_ticks=
                    minimum_baseline_ticks
            )
        )


    @classmethod
    def _sign(
        cls,
        value
    ):

        if (
            value
            > cls.PRICE_EPSILON
        ):

            return 1


        if (
            value
            < -cls.PRICE_EPSILON
        ):

            return -1


        return 0


    @staticmethod
    def _imbalance(
        positive,
        negative
    ):

        directional = (
            positive
            + negative
        )


        if (
            directional
            == 0
        ):

            return 0.0


        return (

            float(
                positive
                - negative
            )

            / float(
                directional
            )
        )


    @staticmethod
    def _ratio(
        numerator,
        denominator
    ):

        if (
            denominator
            == 0
        ):

            return 0.0


        return (

            float(
                numerator
            )

            / float(
                denominator
            )
        )


    def analyze(
        self,
        ticks: tuple[
            TickSample,
            ...
        ],
        as_of: datetime
    ) -> DealerBehaviourFeatures:

        if not isinstance(
            as_of,
            datetime
        ):

            raise TypeError(
                "Dealer behaviour as_of "
                "must be datetime."
            )


        if (
            as_of.tzinfo
            is None
        ):

            raise ValueError(
                "Dealer behaviour as_of "
                "must be timezone-aware."
            )


        ticks = tuple(
            ticks
        )


        if not ticks:

            raise ValueError(
                "Dealer behaviour analysis "
                "requires raw ticks."
            )


        for tick in ticks:

            if not isinstance(
                tick,
                TickSample
            ):

                raise TypeError(
                    "Dealer behaviour analysis "
                    "requires TickSample input."
                )


            if (
                tick.timestamp
                >= as_of
            ):

                raise ValueError(
                    "Dealer behaviour analysis "
                    "received non-causal tick "
                    "at or after as_of."
                )


        symbols = {

            tick.symbol

            for tick
            in ticks
        }


        if (
            len(
                symbols
            )
            != 1
        ):

            raise ValueError(
                "Dealer behaviour analysis "
                "cannot mix symbols."
            )


        ordered_ticks = tuple(

            sorted(

                ticks,

                key=lambda tick: (
                    tick.timestamp,
                    tick.source_time_msc
                )
            )
        )


        base_features = (
            self.microstructure_engine
            .analyze(

                ticks=
                    ordered_ticks,

                as_of=
                    as_of
            )
        )


        recent_start = (

            as_of

            - timedelta(
                minutes=
                    self.recent_minutes
            )
        )


        recent_ticks = tuple(

            tick

            for tick
            in ordered_ticks

            if (
                recent_start
                <= tick.timestamp
                < as_of
            )
        )


        if (
            len(
                recent_ticks
            )
            < self.minimum_recent_ticks
        ):

            raise ValueError(
                "Insufficient recent ticks "
                "for dealer behaviour analysis."
            )


        bid_up_updates = 0
        bid_down_updates = 0

        ask_up_updates = 0
        ask_down_updates = 0

        quote_change_updates = 0

        one_sided_updates = 0

        synchronized_updates = 0

        opposed_updates = 0

        spread_widen_updates = 0

        spread_narrow_updates = 0

        spread_changes = []


        for (
            previous,
            current
        ) in zip(
            recent_ticks,
            recent_ticks[1:]
        ):

            bid_change = (
                current.bid
                - previous.bid
            )

            ask_change = (
                current.ask
                - previous.ask
            )


            bid_sign = (
                self._sign(
                    bid_change
                )
            )

            ask_sign = (
                self._sign(
                    ask_change
                )
            )


            if (
                bid_sign
                > 0
            ):

                bid_up_updates += 1

            elif (
                bid_sign
                < 0
            ):

                bid_down_updates += 1


            if (
                ask_sign
                > 0
            ):

                ask_up_updates += 1

            elif (
                ask_sign
                < 0
            ):

                ask_down_updates += 1


            if (
                bid_sign != 0
                or ask_sign != 0
            ):

                quote_change_updates += 1


                if (
                    (
                        bid_sign == 0
                    )
                    != (
                        ask_sign == 0
                    )
                ):

                    one_sided_updates += 1


                elif (
                    bid_sign
                    == ask_sign
                ):

                    synchronized_updates += 1


                else:

                    opposed_updates += 1


            spread_change = (

                float(
                    current.spread_pips
                )

                - float(
                    previous.spread_pips
                )
            )


            spread_changes.append(
                spread_change
            )


            spread_sign = (
                self._sign(
                    spread_change
                )
            )


            if (
                spread_sign
                > 0
            ):

                spread_widen_updates += 1

            elif (
                spread_sign
                < 0
            ):

                spread_narrow_updates += 1


        bid_directional_updates = (

            bid_up_updates
            + bid_down_updates
        )


        ask_directional_updates = (

            ask_up_updates
            + ask_down_updates
        )


        bid_update_imbalance = (
            self._imbalance(
                bid_up_updates,
                bid_down_updates
            )
        )


        ask_update_imbalance = (
            self._imbalance(
                ask_up_updates,
                ask_down_updates
            )
        )


        total_side_updates = (

            bid_directional_updates
            + ask_directional_updates
        )


        side_update_asymmetry = (

            0.0

            if (
                total_side_updates
                == 0
            )

            else (

                float(
                    bid_directional_updates
                    - ask_directional_updates
                )

                / float(
                    total_side_updates
                )
            )
        )


        one_sided_update_ratio = (
            self._ratio(
                one_sided_updates,
                quote_change_updates
            )
        )


        synchronized_update_ratio = (
            self._ratio(
                synchronized_updates,
                quote_change_updates
            )
        )


        opposed_update_ratio = (
            self._ratio(
                opposed_updates,
                quote_change_updates
            )
        )


        spread_directional_updates = (

            spread_widen_updates
            + spread_narrow_updates
        )


        spread_update_imbalance = (
            self._imbalance(
                spread_widen_updates,
                spread_narrow_updates
            )
        )


        net_spread_change_pips = (

            float(
                recent_ticks[-1]
                .spread_pips
            )

            - float(
                recent_ticks[0]
                .spread_pips
            )
        )


        if spread_changes:

            mean_absolute_spread_change_pips = (
                mean(

                    abs(
                        value
                    )

                    for value
                    in spread_changes
                )
            )

        else:

            mean_absolute_spread_change_pips = (
                0.0
            )


        return DealerBehaviourFeatures(

            as_of=
                as_of,

            recent_tick_count=
                base_features
                .recent_tick_count,

            baseline_tick_count=
                base_features
                .baseline_tick_count,

            tick_activity_ratio=
                base_features
                .tick_activity_ratio,

            average_spread_pips=
                base_features
                .average_spread_pips,

            baseline_spread_pips=
                base_features
                .baseline_spread_pips,

            spread_ratio=
                base_features
                .spread_ratio,

            bid_up_updates=
                bid_up_updates,

            bid_down_updates=
                bid_down_updates,

            bid_directional_updates=
                bid_directional_updates,

            bid_update_imbalance=
                bid_update_imbalance,

            ask_up_updates=
                ask_up_updates,

            ask_down_updates=
                ask_down_updates,

            ask_directional_updates=
                ask_directional_updates,

            ask_update_imbalance=
                ask_update_imbalance,

            side_update_asymmetry=
                side_update_asymmetry,

            quote_change_updates=
                quote_change_updates,

            one_sided_updates=
                one_sided_updates,

            synchronized_updates=
                synchronized_updates,

            opposed_updates=
                opposed_updates,

            one_sided_update_ratio=
                one_sided_update_ratio,

            synchronized_update_ratio=
                synchronized_update_ratio,

            opposed_update_ratio=
                opposed_update_ratio,

            spread_widen_updates=
                spread_widen_updates,

            spread_narrow_updates=
                spread_narrow_updates,

            spread_directional_updates=
                spread_directional_updates,

            spread_update_imbalance=
                spread_update_imbalance,

            net_spread_change_pips=
                net_spread_change_pips,

            mean_absolute_spread_change_pips=
                mean_absolute_spread_change_pips
        )
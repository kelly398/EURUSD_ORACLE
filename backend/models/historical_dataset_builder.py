from bisect import bisect_left
from dataclasses import dataclass
from datetime import timedelta

from backend.models.feature_builder import (
    MLFeatureBuilder
)

from backend.models.historical_replay import (
    HistoricalReplayBuilder
)

from backend.models.labeling import (
    DirectionLabeler
)

from backend.models.training_contracts import (
    LabelSpecification,
    TrainingSample
)


@dataclass(frozen=True)
class HistoricalDatasetResult:

    samples: tuple[
        TrainingSample,
        ...
    ]

    candidate_count: int

    created_count: int

    skipped_target_gap: int

    skipped_replay_error: int

    skipped_tick_context: int


class HistoricalDatasetBuilder:





    def __init__(
        self,
        label_specification:
            LabelSpecification,
        candle_window=200,
        timeframe_minutes=5
    ):

        if candle_window < 140:
            raise ValueError("candle_window must be at least 140.")
        if timeframe_minutes != 5 or label_specification.timeframe_minutes != 5:
            raise ValueError("Historical dataset features currently require M5 candles.")

        self.label_specification = (
            label_specification
        )

        self.candle_window = (
            candle_window
        )

        self.timeframe_minutes = (
            timeframe_minutes
        )


        self.replay_builder = (
            HistoricalReplayBuilder(

                symbol="EURUSD",

                timeframe="M5",

                timeframe_minutes=
                    timeframe_minutes,

                candle_limit=
                    candle_window,

                tick_lookback_minutes=65
            )
        )


        self.feature_builder = (
            MLFeatureBuilder()
        )


        self.labeler = (
            DirectionLabeler(
                label_specification
            )
        )


    def _target_is_contiguous(
        self,
        candles,
        anchor_index
    ):

        horizon = (
            self.label_specification
            .horizon_bars
        )


        anchor_time = (
            candles[
                anchor_index
            ].timestamp
        )


        for offset in range(
            1,
            horizon + 1
        ):

            expected = (

                anchor_time

                + timedelta(
                    minutes=
                        self.timeframe_minutes
                        * offset
                )
            )


            actual = (
                candles[
                    anchor_index
                    + offset
                ].timestamp
            )


            if actual != expected:

                return False


        return True


    def _has_tick_context(
        self,
        ticks,
        as_of,
    ):
        tick_start = (
            as_of
            - timedelta(
                minutes=self.replay_builder.tick_lookback_minutes
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

        if not window:
            return False

        recent_start = (
            as_of
            - timedelta(
                minutes=self.replay_builder.liquidity_engine.recent_minutes
            )
        )

        baseline_start = (
            recent_start
            - timedelta(
                minutes=self.replay_builder.liquidity_engine.baseline_minutes
            )
        )

        recent = [
            tick
            for tick in window
            if (
                recent_start
                <= tick.timestamp
                < as_of
            )
        ]

        baseline = [
            tick
            for tick in window
            if (
                baseline_start
                <= tick.timestamp
                < recent_start
            )
        ]

        return (
            len(recent)
            >= self.replay_builder.liquidity_engine.minimum_recent_ticks
            and
            len(baseline)
            >= self.replay_builder.liquidity_engine.minimum_baseline_ticks
        )


    def build(
        self,
        candles,
        ticks
    ) -> HistoricalDatasetResult:

        candles = list(
            candles
        )

        ticks = list(
            ticks
        )


        if not candles:

            raise ValueError(
                "Dataset builder requires candles."
            )


        for previous, current in zip(
            candles,
            candles[1:]
        ):

            if (
                current.timestamp
                <= previous.timestamp
            ):

                raise ValueError(
                    "Dataset candles must be "
                    "strictly chronological."
                )


        ticks.sort(
            key=lambda tick: (
                tick.timestamp,
                tick.source_time_msc
            )
        )

        for tick in ticks:
            if tick.symbol != "EURUSD":
                raise ValueError(
                    "Dataset ticks must contain only EURUSD."
                )

        tick_times = [tick.timestamp for tick in ticks]


        samples = []

        candidate_count = 0

        skipped_target_gap = 0

        skipped_replay_error = 0

        skipped_tick_context = 0


        horizon = (
            self.label_specification
            .horizon_bars
        )


        first_anchor_index = (
            self.candle_window - 1
        )

        if tick_times:
            earliest_tick_ready_time = (
                tick_times[0]
                + timedelta(
                    minutes=self.replay_builder.tick_lookback_minutes
                )
            )

            while (
                first_anchor_index < len(candles)
                and candles[first_anchor_index].timestamp
                < earliest_tick_ready_time
            ):
                first_anchor_index += 1


        last_anchor_index = (

            len(candles)

            - horizon
            - 1
        )


        if (
            last_anchor_index
            < first_anchor_index
        ):

            return HistoricalDatasetResult(

                samples=tuple(),

                candidate_count=0,

                created_count=0,

                skipped_target_gap=0,

                skipped_replay_error=0,

                skipped_tick_context=0
            )


        for anchor_index in range(
            first_anchor_index,
            last_anchor_index + 1
        ):

            as_of = candles[anchor_index].timestamp

            if not self._has_tick_context(
                ticks,
                as_of,
            ):
                skipped_replay_error += 1
                continue

            candidate_count += 1

            if not self._target_is_contiguous(
                candles,
                anchor_index
            ):

                skipped_target_gap += 1

                continue


            anchor = (
                candles[
                    anchor_index
                ]
            )


            future = (
                candles[
                    anchor_index
                    + horizon
                ]
            )


            # ==================================
            # IMPORTANT ANTI-LEAKAGE BOUNDARY
            # ==================================
            #
            # Only candles up to the anchor are
            # passed into historical replay.
            #
            # The future candle remains outside
            # feature construction entirely.
            # ==================================

            history_start = max(

                0,

                anchor_index
                - self.candle_window
                + 1
            )


            historical_candles = (

                candles[
                    history_start:
                    anchor_index + 1
                ]
            )


            as_of = (

                anchor.timestamp

                + timedelta(
                    minutes=
                        self.timeframe_minutes
                )
            )


            tick_start = (

                as_of

                - timedelta(
                    minutes=65
                )
            )


            historical_ticks = ticks[
                bisect_left(tick_times, tick_start):
                bisect_left(tick_times, as_of)
            ]

            if not self.replay_builder.has_sufficient_tick_context(
                historical_ticks,
                as_of
            ):
                skipped_replay_error += 1
                continue

            try:

                replay = (
                    self.replay_builder.build(

                        candles=
                            historical_candles,

                        ticks=
                            historical_ticks,

                        anchor_timestamp=
                            anchor.timestamp,

                        ticks_prebounded=True
                    )
                )

                if replay is None:
                    raise RuntimeError(
                        "HistoricalReplayBuilder returned None"
                    )


                features = (
                    self.feature_builder.build(
                        replay.context
                    )
                )


                sample = (
                    self.labeler.create_sample(

                        features=
                            features,

                        current_close=
                            anchor.close,

                        future_close=
                            future.close
                    )
                )


                samples.append(
                    sample
                )


            except (
                ValueError,
                RuntimeError
            ) as e:

                print("\n========== REPLAY FAILURE ==========")
                print("Timestamp:", anchor.timestamp)
                print("Reason:", str(e))
                print("====================================\n")

                skipped_replay_error += 1

        return HistoricalDatasetResult(

            samples=
                tuple(samples),

            candidate_count=
                candidate_count,

            created_count=
                len(samples),

            skipped_target_gap=
                skipped_target_gap,   

            skipped_replay_error=
                skipped_replay_error,

            skipped_tick_context=
                skipped_tick_context
        )

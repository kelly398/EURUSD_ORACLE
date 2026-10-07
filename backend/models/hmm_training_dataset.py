from dataclasses import (
    dataclass,
)

from datetime import (
    timedelta,
)

import numpy as np

from sqlalchemy import (
    select,
)

from backend.database.connection import (
    SessionLocal,
)

from backend.models.dataset_models import (
    MLTrainingSample,
)

from backend.market_state.candle_repository import (
    CandleRepository,
)

from backend.models.hmm_observation_builder import (
    HMMObservationBuilder,
)


DATASET_VERSION = (
    "eurusd_m5_full_20251002_20261001_v1"
)

SOURCE_FEATURE_VERSION = (
    "eurusd_m5_v1"
)

HMM_FEATURE_VERSION = (
    "eurusd_m5_hmm_obs_v1"
)

LABEL_VERSION = (
    "eurusd_m5_direction_v1"
)

SYMBOL = (
    "EURUSD"
)

TIMEFRAME = (
    "M5"
)


DEVELOPMENT_SPLITS = (
    "train",
    "validation",
)


EXPECTED_SOURCE_COUNTS = {

    "train":
        50462,

    "validation":
        11084,
}


LABEL_TO_ID = {

    "short":
        0,

    "neutral":
        1,

    "long":
        2,
}


@dataclass(
    frozen=True
)
class HMMAlignedSplit:

    name: str

    feature_names: tuple

    timestamps: tuple

    target_timestamps: tuple

    labels: tuple

    values: np.ndarray

    label_ids: np.ndarray

    target_move_pips: np.ndarray

    segment_lengths: tuple

    source_row_count: int

    dropped_unaligned_count: int


    @property
    def observation_count(
        self
    ):

        return int(
            self.values.shape[0]
        )


    @property
    def feature_count(
        self
    ):

        return int(
            self.values.shape[1]
        )


    @property
    def first_timestamp(
        self
    ):

        return (
            self.timestamps[
                0
            ]
        )


    @property
    def last_timestamp(
        self
    ):

        return (
            self.timestamps[
                -1
            ]
        )


@dataclass(
    frozen=True
)
class HMMDevelopmentDataset:

    dataset_version: str

    source_feature_version: str

    hmm_feature_version: str

    label_version: str

    symbol: str

    timeframe: str

    feature_names: tuple

    train: HMMAlignedSplit

    validation: HMMAlignedSplit

    test_loaded: bool


class HMMDevelopmentDatasetLoader:

    def __init__(
        self,
        enforce_frozen_counts=True,
    ):

        self.enforce_frozen_counts = (
            enforce_frozen_counts
        )


        self.observation_builder = (
            HMMObservationBuilder(

                symbol=
                    SYMBOL,

                timeframe=
                    TIMEFRAME,

                timeframe_minutes=
                    5,
            )
        )


        self.candle_repository = (
            CandleRepository(
                source="MT5"
            )
        )


    def _load_development_rows(
        self
    ):

        session = (
            SessionLocal()
        )


        try:

            statement = (

                select(
                    MLTrainingSample
                )

                .where(

                    MLTrainingSample.symbol
                    == SYMBOL,

                    MLTrainingSample.timeframe
                    == TIMEFRAME,

                    MLTrainingSample.feature_version
                    == SOURCE_FEATURE_VERSION,

                    MLTrainingSample.label_version
                    == LABEL_VERSION,

                    MLTrainingSample.split.in_(
                        DEVELOPMENT_SPLITS
                    ),
                )

                .order_by(
                    MLTrainingSample
                    .feature_timestamp
                    .asc()
                )
            )


            rows = (

                session.execute(
                    statement
                )

                .scalars()

                .all()
            )


            return tuple(
                rows
            )


        finally:

            session.close()


    def _validate_source_rows(
        self,
        rows
    ):

        if not rows:

            raise RuntimeError(
                "Frozen HMM development "
                "source rows were not found."
            )


        counts = {

            split_name:
                0

            for split_name
            in DEVELOPMENT_SPLITS
        }


        seen_timestamps = {

            split_name:
                set()

            for split_name
            in DEVELOPMENT_SPLITS
        }


        previous_timestamp = None


        for row in rows:

            if (
                row.split
                not in DEVELOPMENT_SPLITS
            ):

                raise RuntimeError(
                    "HMM development loader "
                    "received a non-development "
                    "split."
                )


            counts[
                row.split
            ] += 1


            if (
                row.feature_timestamp
                in seen_timestamps[
                    row.split
                ]
            ):

                raise RuntimeError(
                    "Frozen ML dataset contains "
                    "duplicate feature timestamps "
                    "within a split."
                )


            seen_timestamps[
                row.split
            ].add(
                row.feature_timestamp
            )


            if (
                previous_timestamp
                is not None

                and

                row.feature_timestamp
                < previous_timestamp
            ):

                raise RuntimeError(
                    "Frozen ML development rows "
                    "are not chronological."
                )


            previous_timestamp = (
                row.feature_timestamp
            )


            if (
                row.target_timestamp
                <= row.feature_timestamp
            ):

                raise RuntimeError(
                    "Frozen ML sample target "
                    "timestamp is not strictly "
                    "after the feature timestamp."
                )


            if (
                row.target_label
                not in LABEL_TO_ID
            ):

                raise RuntimeError(

                    "Unexpected frozen ML "
                    "target label: "

                    f"{row.target_label}"
                )


        if (
            self.enforce_frozen_counts
        ):

            for split_name in (
                DEVELOPMENT_SPLITS
            ):

                expected = (
                    EXPECTED_SOURCE_COUNTS[
                        split_name
                    ]
                )


                actual = (
                    counts[
                        split_name
                    ]
                )


                if (
                    actual
                    != expected
                ):

                    raise RuntimeError(

                        "Frozen ML dataset "
                        "row-count drift for "

                        f"{split_name}. "

                        f"Expected {expected}, "
                        f"received {actual}."
                    )


        return counts


    def _load_candles_for_rows(
        self,
        rows
    ):

        first_timestamp = (
            rows[
                0
            ].feature_timestamp
        )


        last_timestamp = (
            rows[
                -1
            ].feature_timestamp
        )


        # Give the observation builder
        # ample causal warm-up before the
        # first frozen feature timestamp.
        #
        # HMMObservationBuilder will still
        # reset across any gap > 5 minutes,
        # including weekends.
        start = (

            first_timestamp

            - timedelta(
                days=2
            )
        )


        # feature_timestamp represents the
        # as-of time after a completed M5
        # candle. get_between() has an
        # exclusive end boundary, so an end
        # slightly after the last as-of time
        # safely includes its source candle.
        end = (

            last_timestamp

            + timedelta(
                minutes=1
            )
        )


        candles = (
            self.candle_repository
            .get_between(

                start=
                    start,

                end=
                    end,

                symbol=
                    SYMBOL,

                timeframe=
                    TIMEFRAME,
            )
        )


        if not candles:

            raise RuntimeError(
                "No historical candles were "
                "available for HMM development."
            )


        return tuple(
            candles
        )


    @staticmethod
    def _observation_lookup(
        sequence
    ):

        lookup = {}


        offset = 0


        for segment_id, length in enumerate(
            sequence.segment_lengths
        ):

            for local_index in range(
                length
            ):

                absolute_index = (

                    offset

                    + local_index
                )


                timestamp = (
                    sequence
                    .as_of_timestamps[
                        absolute_index
                    ]
                )


                if (
                    timestamp
                    in lookup
                ):

                    raise RuntimeError(
                        "HMM observation sequence "
                        "contains a duplicate "
                        "as-of timestamp."
                    )


                lookup[
                    timestamp
                ] = (

                    sequence.values[
                        absolute_index
                    ],

                    segment_id,
                )


            offset += (
                length
            )


        if (
            offset
            != sequence.observation_count
        ):

            raise RuntimeError(
                "HMM source segment mapping "
                "does not cover all rows."
            )


        return lookup


    @staticmethod
    def _build_segment_lengths(
        timestamps,
        source_segment_ids,
    ):

        if not timestamps:

            return tuple()


        expected_delta = (
            timedelta(
                minutes=5
            )
        )


        lengths = []

        current_length = (
            1
        )


        for index in range(
            1,
            len(
                timestamps
            ),
        ):

            same_source_segment = (

                source_segment_ids[
                    index
                ]

                == source_segment_ids[
                    index - 1
                ]
            )


            contiguous_time = (

                timestamps[
                    index
                ]

                - timestamps[
                    index - 1
                ]

                == expected_delta
            )


            if (
                same_source_segment
                and
                contiguous_time
            ):

                current_length += (
                    1
                )

                continue


            lengths.append(
                current_length
            )


            current_length = (
                1
            )


        lengths.append(
            current_length
        )


        return tuple(
            lengths
        )


    def _align_split(
        self,
        split_name,
        rows,
        observation_lookup,
        feature_names,
    ):

        split_rows = [

            row

            for row
            in rows

            if (
                row.split
                == split_name
            )
        ]


        source_row_count = len(
            split_rows
        )


        timestamps = []

        target_timestamps = []

        labels = []

        values = []

        label_ids = []

        target_move_pips = []

        source_segment_ids = []


        dropped = (
            0
        )


        for row in split_rows:

            observation = (
                observation_lookup.get(
                    row.feature_timestamp
                )
            )


            if (
                observation
                is None
            ):

                dropped += (
                    1
                )

                continue


            (
                observation_values,
                source_segment_id,
            ) = observation


            timestamps.append(
                row.feature_timestamp
            )


            target_timestamps.append(
                row.target_timestamp
            )


            labels.append(
                row.target_label
            )


            values.append(
                observation_values
            )


            label_ids.append(

                LABEL_TO_ID[
                    row.target_label
                ]
            )


            target_move_pips.append(
                float(
                    row.target_move_pips
                )
            )


            source_segment_ids.append(
                source_segment_id
            )


        if not values:

            raise RuntimeError(

                "No HMM observations aligned "
                "to frozen split: "

                f"{split_name}"
            )


        segment_lengths = (
            self._build_segment_lengths(

                timestamps=
                    timestamps,

                source_segment_ids=
                    source_segment_ids,
            )
        )


        matrix = np.asarray(
            values,
            dtype=np.float64,
        )


        labels_array = np.asarray(
            label_ids,
            dtype=np.int64,
        )


        moves_array = np.asarray(
            target_move_pips,
            dtype=np.float64,
        )


        if (
            matrix.ndim
            != 2
        ):

            raise RuntimeError(
                "Aligned HMM matrix must "
                "be two-dimensional."
            )


        if (
            matrix.shape[
                1
            ]
            != len(
                feature_names
            )
        ):

            raise RuntimeError(
                "Aligned HMM feature width "
                "does not match the observation "
                "contract."
            )


        if not np.isfinite(
            matrix
        ).all():

            raise RuntimeError(
                "Aligned HMM matrix contains "
                "NaN or infinity."
            )


        if not np.isfinite(
            moves_array
        ).all():

            raise RuntimeError(
                "Aligned HMM target moves "
                "contain NaN or infinity."
            )


        if (
            sum(
                segment_lengths
            )
            != matrix.shape[
                0
            ]
        ):

            raise RuntimeError(
                "Aligned HMM segment lengths "
                "do not cover all observations."
            )


        # Treat returned arrays as frozen
        # dataset artifacts inside the
        # training process.
        matrix.setflags(
            write=False
        )


        labels_array.setflags(
            write=False
        )


        moves_array.setflags(
            write=False
        )


        return HMMAlignedSplit(

            name=
                split_name,

            feature_names=
                tuple(
                    feature_names
                ),

            timestamps=
                tuple(
                    timestamps
                ),

            target_timestamps=
                tuple(
                    target_timestamps
                ),

            labels=
                tuple(
                    labels
                ),

            values=
                matrix,

            label_ids=
                labels_array,

            target_move_pips=
                moves_array,

            segment_lengths=
                segment_lengths,

            source_row_count=
                source_row_count,

            dropped_unaligned_count=
                dropped,
        )


    def load(
        self
    ) -> HMMDevelopmentDataset:

        rows = (
            self._load_development_rows()
        )


        self._validate_source_rows(
            rows
        )


        candles = (
            self._load_candles_for_rows(
                rows
            )
        )


        sequence = (
            self.observation_builder
            .build(
                candles
            )
        )


        if (
            sequence.feature_version
            != HMM_FEATURE_VERSION
        ):

            raise RuntimeError(
                "Unexpected HMM observation "
                "feature version."
            )


        observation_lookup = (
            self._observation_lookup(
                sequence
            )
        )


        train = (
            self._align_split(

                split_name=
                    "train",

                rows=
                    rows,

                observation_lookup=
                    observation_lookup,

                feature_names=
                    sequence.feature_names,
            )
        )


        validation = (
            self._align_split(

                split_name=
                    "validation",

                rows=
                    rows,

                observation_lookup=
                    observation_lookup,

                feature_names=
                    sequence.feature_names,
            )
        )


        if (
            train.last_timestamp
            >= validation.first_timestamp
        ):

            raise RuntimeError(
                "HMM train and validation "
                "chronology overlaps."
            )


        return HMMDevelopmentDataset(

            dataset_version=
                DATASET_VERSION,

            source_feature_version=
                SOURCE_FEATURE_VERSION,

            hmm_feature_version=
                HMM_FEATURE_VERSION,

            label_version=
                LABEL_VERSION,

            symbol=
                SYMBOL,

            timeframe=
                TIMEFRAME,

            feature_names=
                tuple(
                    sequence.feature_names
                ),

            train=
                train,

            validation=
                validation,

            test_loaded=
                False,
        )
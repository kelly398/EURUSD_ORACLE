from bisect import bisect_right

from dataclasses import (
    dataclass
)

from datetime import (
    date,
    datetime,
    timedelta
)

from sqlalchemy import (
    func,
    select
)

from backend.database.connection import (
    SessionLocal
)

from backend.database.models import (
    BrokerCandle,
    COTPositioning
)

from backend.institutional.cot_feature_builder import (
    COTFeatureBuilder,
    COTFeatureVector
)

from backend.institutional.cot_repository import (
    COTPositioningRepository
)

from backend.institutional.cot_research_protocol import (
    DEVELOPMENT_SPLITS,
    PRIMARY_HORIZON_BARS,
    SECONDARY_HORIZON_BARS
)

from backend.models.dataset_models import (
    MLTrainingSample
)


ML_FEATURE_VERSION = (
    "eurusd_m5_v1"
)

ML_LABEL_VERSION = (
    "eurusd_m5_direction_v1"
)

SYMBOL = "EURUSD"

TIMEFRAME = "M5"

CANDLE_SOURCE = "MT5"

TIMEFRAME_MINUTES = 5

PIP_SIZE = 0.0001


@dataclass(
    frozen=True
)
class DevelopmentSplitWindow:

    name: str

    start: datetime

    end: datetime


@dataclass(
    frozen=True
)
class COTDevelopmentEvent:

    split: str

    report_date: date

    available_at: datetime

    entry_timestamp: datetime

    entry_open: float

    feature_vector: COTFeatureVector

    target_1d_timestamp: datetime

    target_3d_timestamp: datetime

    target_4d_timestamp: datetime

    return_1d_pips: float

    return_3d_pips: float

    return_4d_pips: float


@dataclass(
    frozen=True
)
class COTDevelopmentDataset:

    events: tuple[
        COTDevelopmentEvent,
        ...
    ]

    train_candidates: int

    validation_candidates: int

    train_events: int

    validation_events: int

    train_boundary_skips: int

    validation_boundary_skips: int


class COTDevelopmentDatasetBuilder:

    def __init__(
        self
    ):

        self.cot_repository = (
            COTPositioningRepository()
        )

        self.feature_builder = (
            COTFeatureBuilder()
        )


    def _load_split_windows(
        self
    ):

        session = SessionLocal()

        try:

            rows = (
                session.execute(

                    select(
                        MLTrainingSample.split,
                        func.min(
                            MLTrainingSample
                            .feature_timestamp
                        ),
                        func.max(
                            MLTrainingSample
                            .feature_timestamp
                        )
                    )

                    .where(

                        MLTrainingSample.symbol
                        == SYMBOL,

                        MLTrainingSample.timeframe
                        == TIMEFRAME,

                        MLTrainingSample
                        .feature_version
                        == ML_FEATURE_VERSION,

                        MLTrainingSample
                        .label_version
                        == ML_LABEL_VERSION,

                        MLTrainingSample.split
                        .in_(
                            DEVELOPMENT_SPLITS
                        )
                    )

                    .group_by(
                        MLTrainingSample.split
                    )
                )

                .all()
            )

        finally:

            session.close()


        windows = {

            row[0]:
                DevelopmentSplitWindow(
                    name=row[0],
                    start=row[1],
                    end=row[2]
                )

            for row
            in rows
        }


        for split_name in (
            DEVELOPMENT_SPLITS
        ):

            if split_name not in windows:

                raise RuntimeError(
                    "Frozen ML development "
                    f"split missing: {split_name}"
                )


        return windows


    def _load_candles(
        self,
        start,
        end
    ):

        session = SessionLocal()

        try:

            rows = (

                session.execute(

                    select(
                        BrokerCandle
                    )

                    .where(

                        BrokerCandle.symbol
                        == SYMBOL,

                        BrokerCandle.timeframe
                        == TIMEFRAME,

                        BrokerCandle.source
                        == CANDLE_SOURCE,

                        BrokerCandle.is_complete
                        .is_(
                            True
                        ),

                        BrokerCandle.timestamp
                        >= start,

                        BrokerCandle.timestamp
                        <= end
                    )

                    .order_by(
                        BrokerCandle.timestamp
                        .asc()
                    )
                )

                .scalars()

                .all()
            )

        finally:

            session.close()


        if not rows:

            raise RuntimeError(
                "No completed EUR/USD M5 "
                "candles available for "
                "COT development dataset."
            )


        for previous, current in zip(
            rows,
            rows[1:]
        ):

            if (
                current.timestamp
                <= previous.timestamp
            ):

                raise RuntimeError(
                    "Development candles are "
                    "not strictly chronological."
                )


        return tuple(
            rows
        )


    def _validate_no_development_revisions(
        self,
        development_end
    ):

        session = SessionLocal()

        try:

            rows = (
                session.execute(

                    select(
                        COTPositioning.report_date,
                        func.count(
                            COTPositioning.id
                        )
                    )

                    .where(

                        COTPositioning.source
                        == "CFTC",

                        COTPositioning.report_type
                        == "TFF_FUTURES_ONLY",

                        COTPositioning
                        .cftc_contract_market_code
                        == "099741",

                        COTPositioning.available_at
                        <= development_end
                    )

                    .group_by(
                        COTPositioning.report_date
                    )

                    .having(
                        func.count(
                            COTPositioning.id
                        )
                        > 1
                    )
                )

                .all()
            )

        finally:

            session.close()


        if rows:

            raise RuntimeError(
                "Development COT history "
                "contains multiple causal "
                "versions for at least one "
                "report_date. Research "
                "protocol must explicitly "
                "define correction-event "
                "handling before continuing."
            )


    def _split_for_available_at(
        self,
        available_at,
        windows
    ):

        for split_name in (
            DEVELOPMENT_SPLITS
        ):

            window = (
                windows[
                    split_name
                ]
            )


            if (
                window.start
                <= available_at
                <= window.end
            ):

                return window


        return None


    def _target_index(
        self,
        entry_index,
        horizon_bars
    ):

        return (
            entry_index
            + horizon_bars
            - 1
        )


    def _forward_return_pips(
        self,
        entry_open,
        target_close
    ):

        return (
            float(
                target_close
                - entry_open
            )
            / PIP_SIZE
        )


    def _build_event(
        self,
        *,
        cot_item,
        cot_history,
        candles,
        candle_timestamps,
        split_window
    ):

        # ==================================
        # STRICT POST-PUBLICATION ENTRY
        # ==================================
        #
        # If COT becomes available at
        # 19:30:08, the 19:30 M5 candle has
        # already begun and is forbidden.
        #
        # bisect_right chooses the first
        # candle timestamp strictly greater
        # than available_at.
        # ==================================

        entry_index = bisect_right(
            candle_timestamps,
            cot_item.available_at
        )


        if (
            entry_index
            >= len(
                candles
            )
        ):

            return None


        entry_bar = (
            candles[
                entry_index
            ]
        )


        if (
            entry_bar.timestamp
            > split_window.end
        ):

            return None


        horizons = (
            SECONDARY_HORIZON_BARS
            + (
                PRIMARY_HORIZON_BARS,
            )
        )


        target_indices = {

            horizon:
                self._target_index(
                    entry_index,
                    horizon
                )

            for horizon
            in horizons
        }


        primary_index = (
            target_indices[
                PRIMARY_HORIZON_BARS
            ]
        )


        if (
            primary_index
            >= len(
                candles
            )
        ):

            return None


        primary_bar = (
            candles[
                primary_index
            ]
        )


        primary_close_timestamp = (

            primary_bar.timestamp

            + timedelta(
                minutes=
                    TIMEFRAME_MINUTES
            )
        )


        # ==================================
        # SPLIT PURITY
        # ==================================
        #
        # The entire primary outcome must be
        # realized within the same frozen ML
        # split as the COT release.
        # ==================================

        if (
            primary_close_timestamp
            > split_window.end
        ):

            return None


        feature_vector = (
            self.feature_builder.build(
                cot_history
            )
        )


        if (
            feature_vector.report_date
            != cot_item.report_date
        ):

            raise RuntimeError(
                "COT feature history current "
                "report does not match the "
                "research event."
            )


        if (
            feature_vector.available_at
            != cot_item.available_at
        ):

            raise RuntimeError(
                "COT feature timestamp does "
                "not match research event "
                "availability."
            )


        target_bars = {

            horizon:
                candles[
                    target_index
                ]

            for horizon, target_index
            in target_indices.items()
        }


        target_close_times = {

            horizon:
                (
                    target_bar.timestamp
                    + timedelta(
                        minutes=
                            TIMEFRAME_MINUTES
                    )
                )

            for horizon, target_bar
            in target_bars.items()
        }


        entry_open = float(
            entry_bar.open
        )


        returns = {

            horizon:
                self._forward_return_pips(

                    entry_open=
                        entry_open,

                    target_close=
                        float(
                            target_bar.close
                        )
                )

            for horizon, target_bar
            in target_bars.items()
        }


        return COTDevelopmentEvent(

            split=
                split_window.name,

            report_date=
                cot_item.report_date,

            available_at=
                cot_item.available_at,

            entry_timestamp=
                entry_bar.timestamp,

            entry_open=
                entry_open,

            feature_vector=
                feature_vector,

            target_1d_timestamp=
                target_close_times[
                    288
                ],

            target_3d_timestamp=
                target_close_times[
                    864
                ],

            target_4d_timestamp=
                target_close_times[
                    1152
                ],

            return_1d_pips=
                returns[
                    288
                ],

            return_3d_pips=
                returns[
                    864
                ],

            return_4d_pips=
                returns[
                    1152
                ]
        )


    def build(
        self
    ):

        windows = (
            self._load_split_windows()
        )


        train_window = (
            windows[
                "train"
            ]
        )

        validation_window = (
            windows[
                "validation"
            ]
        )


        self._validate_no_development_revisions(
            development_end=
                validation_window.end
        )


        candles = (
            self._load_candles(

                start=
                    train_window.start,

                end=
                    validation_window.end
            )
        )


        candle_timestamps = tuple(

            row.timestamp

            for row
            in candles
        )


        # Snapshot the COT information set at
        # the END of development only.
        #
        # Test-period COT reports are never
        # requested by this builder.
        development_cot = (
            self.cot_repository
            .get_history_as_of(

                as_of=
                    validation_window.end,

                limit=5000
            )
        )


        candidate_counts = {
            "train": 0,
            "validation": 0
        }


        boundary_skips = {
            "train": 0,
            "validation": 0
        }


        events = []


        for cot_item in development_cot:

            split_window = (
                self._split_for_available_at(
                    cot_item.available_at,
                    windows
                )
            )


            if (
                split_window
                is None
            ):

                continue


            candidate_counts[
                split_window.name
            ] += 1


            # Reconstruct the exact COT
            # information set available at
            # this event timestamp.
            cot_history = (
                self.cot_repository
                .get_history_as_of(

                    as_of=
                        cot_item.available_at,

                    limit=5000
                )
            )


            event = (
                self._build_event(

                    cot_item=
                        cot_item,

                    cot_history=
                        cot_history,

                    candles=
                        candles,

                    candle_timestamps=
                        candle_timestamps,

                    split_window=
                        split_window
                )
            )


            if event is None:

                boundary_skips[
                    split_window.name
                ] += 1

                continue


            events.append(
                event
            )


        events = tuple(
            events
        )


        train_events = sum(

            1

            for event
            in events

            if event.split
            == "train"
        )


        validation_events = sum(

            1

            for event
            in events

            if event.split
            == "validation"
        )


        return COTDevelopmentDataset(

            events=
                events,

            train_candidates=
                candidate_counts[
                    "train"
                ],

            validation_candidates=
                candidate_counts[
                    "validation"
                ],

            train_events=
                train_events,

            validation_events=
                validation_events,

            train_boundary_skips=
                boundary_skips[
                    "train"
                ],

            validation_boundary_skips=
                boundary_skips[
                    "validation"
                ]
        )


def main():

    dataset = (
        COTDevelopmentDatasetBuilder()
        .build()
    )


    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "SEAT 22 DEVELOPMENT DATASET AUDIT"
    )

    print(
        "=" * 70
    )


    print()
    print(
        "Train candidates:",
        dataset.train_candidates
    )

    print(
        "Train usable events:",
        dataset.train_events
    )

    print(
        "Train boundary skips:",
        dataset.train_boundary_skips
    )


    print()
    print(
        "Validation candidates:",
        dataset.validation_candidates
    )

    print(
        "Validation usable events:",
        dataset.validation_events
    )

    print(
        "Validation boundary skips:",
        dataset.validation_boundary_skips
    )


    if dataset.events:

        print()
        print(
            "First development report:",
            dataset.events[0].report_date
        )

        print(
            "Last development report:",
            dataset.events[-1].report_date
        )


    print()
    print(
        "Test outcomes evaluated: NO"
    )


    print(
        "=" * 70
    )

    print(
        "SEAT 22 DEVELOPMENT "
        "DATASET AUDIT: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
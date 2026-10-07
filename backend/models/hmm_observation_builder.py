from datetime import (
    datetime,
    timedelta,
)

from math import (
    isfinite,
    log,
)

from statistics import (
    pstdev,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from backend.market_state.schemas import (
    CandleBar,
)


class HMMObservationSequence(
    BaseModel
):

    model_config = ConfigDict(
        frozen=True
    )

    symbol: str

    timeframe: str

    timeframe_minutes: int = Field(
        gt=0
    )

    feature_version: str

    feature_names: tuple[
        str,
        ...
    ]

    as_of_timestamps: tuple[
        datetime,
        ...
    ]

    values: tuple[
        tuple[
            float,
            ...
        ],
        ...
    ]

    segment_lengths: tuple[
        int,
        ...
    ]


    @model_validator(
        mode="after"
    )
    def validate_sequence(
        self
    ):

        if not self.feature_names:

            raise ValueError(
                "HMM observation feature "
                "names cannot be empty."
            )


        if (
            len(
                set(
                    self.feature_names
                )
            )
            != len(
                self.feature_names
            )
        ):

            raise ValueError(
                "HMM observation feature "
                "names must be unique."
            )


        if not self.values:

            raise ValueError(
                "HMM observation sequence "
                "cannot be empty."
            )


        if (
            len(
                self.as_of_timestamps
            )
            != len(
                self.values
            )
        ):

            raise ValueError(
                "HMM observation timestamps "
                "and rows must have equal "
                "length."
            )


        expected_width = len(
            self.feature_names
        )


        for row in self.values:

            if (
                len(row)
                != expected_width
            ):

                raise ValueError(
                    "HMM observation row width "
                    "does not match feature "
                    "names."
                )


            for value in row:

                if not isfinite(
                    value
                ):

                    raise ValueError(
                        "HMM observation sequence "
                        "contains NaN or infinity."
                    )


        for timestamp in (
            self.as_of_timestamps
        ):

            if (
                timestamp.tzinfo
                is None
            ):

                raise ValueError(
                    "HMM observation timestamps "
                    "must be timezone-aware."
                )


        for previous, current in zip(
            self.as_of_timestamps,
            self.as_of_timestamps[1:],
        ):

            if (
                current
                <= previous
            ):

                raise ValueError(
                    "HMM observation timestamps "
                    "must be strictly "
                    "chronological."
                )


        if not self.segment_lengths:

            raise ValueError(
                "HMM observation sequence "
                "requires segment lengths."
            )


        if any(
            length <= 0
            for length
            in self.segment_lengths
        ):

            raise ValueError(
                "HMM segment lengths must "
                "be positive."
            )


        if (
            sum(
                self.segment_lengths
            )
            != len(
                self.values
            )
        ):

            raise ValueError(
                "HMM segment lengths do not "
                "cover the observation rows."
            )


        return self


    @property
    def observation_count(
        self
    ):

        return len(
            self.values
        )


    @property
    def latest_as_of(
        self
    ):

        return (
            self.as_of_timestamps[
                -1
            ]
        )


class HMMObservationBuilder:

    FEATURE_VERSION = (
        "eurusd_m5_hmm_obs_v1"
    )


    FEATURE_NAMES = (

        "return_1_pips",

        "return_3_pips",

        "body_pips",

        "range_pips",

        "close_location",

        "realized_volatility_12_pips",

        "trend_slope_12_pips_per_bar",

        "log_tick_volume_ratio_12",
    )


    MINIMUM_CONTIGUOUS_CANDLES = (
        13
    )


    def __init__(
        self,
        symbol="EURUSD",
        timeframe="M5",
        timeframe_minutes=5,
    ):

        if timeframe_minutes <= 0:

            raise ValueError(
                "timeframe_minutes must "
                "be positive."
            )


        self.symbol = symbol

        self.timeframe = timeframe

        self.timeframe_minutes = (
            timeframe_minutes
        )


        self.expected_delta = (
            timedelta(
                minutes=
                    timeframe_minutes
            )
        )


    def _validate_candles(
        self,
        candles
    ):

        if not candles:

            raise ValueError(
                "HMMObservationBuilder "
                "requires candles."
            )


        for candle in candles:

            if not isinstance(
                candle,
                CandleBar
            ):

                raise TypeError(
                    "HMMObservationBuilder "
                    "requires CandleBar "
                    "instances."
                )


            if (
                candle.symbol
                != self.symbol
            ):

                raise ValueError(
                    "HMM observation sequence "
                    "contains an unexpected "
                    "symbol."
                )


            if (
                candle.timeframe
                != self.timeframe
            ):

                raise ValueError(
                    "HMM observation sequence "
                    "contains an unexpected "
                    "timeframe."
                )


            if (
                candle.timestamp.tzinfo
                is None
            ):

                raise ValueError(
                    "HMM source candle "
                    "timestamps must be "
                    "timezone-aware."
                )


        for previous, current in zip(
            candles,
            candles[1:],
        ):

            if (
                current.timestamp
                <= previous.timestamp
            ):

                raise ValueError(
                    "HMM source candles must "
                    "be strictly chronological."
                )


            delta = (

                current.timestamp

                - previous.timestamp
            )


            if (
                delta
                < self.expected_delta
            ):

                raise ValueError(
                    "HMM source candle cadence "
                    "is faster than the "
                    "configured timeframe."
                )


    def _split_contiguous_segments(
        self,
        candles
    ):

        segments = []

        start_index = 0


        for index in range(
            1,
            len(candles)
        ):

            delta = (

                candles[
                    index
                ].timestamp

                - candles[
                    index - 1
                ].timestamp
            )


            if (
                delta
                > self.expected_delta
            ):

                segments.append(

                    candles[
                        start_index:
                        index
                    ]
                )


                start_index = (
                    index
                )


        segments.append(
            candles[
                start_index:
            ]
        )


        return tuple(
            tuple(segment)
            for segment
            in segments
        )


    @staticmethod
    def _return_pips(
        closes,
        index,
        bars
    ):

        return (

            closes[
                index
            ]

            - closes[
                index - bars
            ]

        ) * 10000.0


    @staticmethod
    def _close_location(
        candle
    ):

        candle_range = (

            candle.high
            - candle.low
        )


        if (
            candle_range
            <= 0.0
        ):

            return 0.0


        location = (

            (
                candle.close
                - candle.low
            )

            / candle_range
        )


        return (

            2.0
            * location

            - 1.0
        )


    @staticmethod
    def _realized_volatility_12(
        closes,
        index
    ):

        returns = [

            (
                closes[
                    return_index
                ]

                - closes[
                    return_index - 1
                ]
            )
            * 10000.0

            for return_index
            in range(
                index - 11,
                index + 1,
            )
        ]


        return float(
            pstdev(
                returns
            )
        )


    @staticmethod
    def _regression_slope(
        values
    ):

        count = len(
            values
        )


        if count < 2:

            return 0.0


        x_mean = (

            count
            - 1

        ) / 2.0


        y_mean = (

            sum(
                values
            )

            / count
        )


        numerator = sum(

            (
                index
                - x_mean
            )

            * (
                value
                - y_mean
            )

            for index, value
            in enumerate(
                values
            )
        )


        denominator = sum(

            (
                index
                - x_mean
            )
            ** 2

            for index
            in range(
                count
            )
        )


        if denominator <= 0:

            return 0.0


        return (

            numerator
            / denominator
        )


    @classmethod
    def _trend_slope_12(
        cls,
        closes,
        index
    ):

        window = (

            closes[
                index - 11:
                index + 1
            ]
        )


        return (

            cls._regression_slope(
                window
            )

            * 10000.0
        )


    @staticmethod
    def _log_tick_volume_ratio_12(
        candles,
        index
    ):

        previous_volumes = [

            float(
                candle.tick_volume
            )

            for candle
            in candles[
                index - 12:
                index
            ]
        ]


        baseline = (

            sum(
                previous_volumes
            )

            / len(
                previous_volumes
            )
        )


        current = float(
            candles[
                index
            ].tick_volume
        )


        return log(

            (
                current
                + 1.0
            )

            / (
                baseline
                + 1.0
            )
        )


    def _build_segment(
        self,
        segment
    ):

        if (
            len(segment)
            < self.MINIMUM_CONTIGUOUS_CANDLES
        ):

            return (
                tuple(),
                tuple(),
            )


        closes = [

            candle.close

            for candle
            in segment
        ]


        timestamps = []

        rows = []


        for index in range(

            self.MINIMUM_CONTIGUOUS_CANDLES
            - 1,

            len(
                segment
            ),
        ):

            candle = (
                segment[
                    index
                ]
            )


            row = (

                self._return_pips(
                    closes,
                    index,
                    1,
                ),

                self._return_pips(
                    closes,
                    index,
                    3,
                ),

                (
                    candle.close
                    - candle.open
                )
                * 10000.0,

                (
                    candle.high
                    - candle.low
                )
                * 10000.0,

                self._close_location(
                    candle
                ),

                self._realized_volatility_12(
                    closes,
                    index,
                ),

                self._trend_slope_12(
                    closes,
                    index,
                ),

                self._log_tick_volume_ratio_12(
                    segment,
                    index,
                ),
            )


            rows.append(

                tuple(
                    float(value)
                    for value
                    in row
                )
            )


            # Candle timestamps represent
            # the start of the completed
            # M5 bar.
            #
            # The observation becomes
            # available only when that bar
            # has completed.
            timestamps.append(

                candle.timestamp

                + self.expected_delta
            )


        return (
            tuple(
                timestamps
            ),
            tuple(
                rows
            ),
        )


    def build(
        self,
        candles
    ) -> HMMObservationSequence:

        candles = tuple(
            candles
        )


        self._validate_candles(
            candles
        )


        segments = (
            self._split_contiguous_segments(
                candles
            )
        )


        all_timestamps = []

        all_rows = []

        segment_lengths = []


        for segment in segments:

            (
                timestamps,
                rows,
            ) = self._build_segment(
                segment
            )


            if not rows:

                continue


            all_timestamps.extend(
                timestamps
            )

            all_rows.extend(
                rows
            )

            segment_lengths.append(
                len(
                    rows
                )
            )


        if not all_rows:

            raise ValueError(

                "HMMObservationBuilder "
                "requires at least one "

                "contiguous segment of "

                f"{self.MINIMUM_CONTIGUOUS_CANDLES} "
                "M5 candles."
            )


        return HMMObservationSequence(

            symbol=
                self.symbol,

            timeframe=
                self.timeframe,

            timeframe_minutes=
                self.timeframe_minutes,

            feature_version=
                self.FEATURE_VERSION,

            feature_names=
                self.FEATURE_NAMES,

            as_of_timestamps=
                tuple(
                    all_timestamps
                ),

            values=
                tuple(
                    all_rows
                ),

            segment_lengths=
                tuple(
                    segment_lengths
                ),
        )
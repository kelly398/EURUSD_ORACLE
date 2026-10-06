from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterator, Literal


DatasetSplit = Literal["train", "validation", "test"]


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")


@dataclass(frozen=True)
class ChronologicalSplitPlan:
    start: datetime
    train_end: datetime
    validation_end: datetime
    end: datetime

    @classmethod
    def from_range(
        cls,
        start: datetime,
        end: datetime,
        train_fraction: float = 0.70,
        validation_fraction: float = 0.15,
    ) -> "ChronologicalSplitPlan":
        _require_aware(start, "start")
        _require_aware(end, "end")

        if end <= start:
            raise ValueError("end must be after start.")
        if not 0.0 < train_fraction < 1.0:
            raise ValueError("train_fraction must be between 0 and 1.")
        if not 0.0 < validation_fraction < 1.0:
            raise ValueError("validation_fraction must be between 0 and 1.")
        if train_fraction + validation_fraction >= 1.0:
            raise ValueError("train + validation fractions must leave a test partition.")

        duration = end - start
        train_end = start + duration * train_fraction
        validation_end = start + duration * (
            train_fraction + validation_fraction
        )

        return cls(
            start=start,
            train_end=train_end,
            validation_end=validation_end,
            end=end,
        )

    def assign(
        self,
        feature_timestamp: datetime,
        target_timestamp: datetime,
    ) -> DatasetSplit | None:
        """Assign a sample without allowing its label horizon to cross a split.

        Returning None means the sample must be purged because it is outside the
        requested dataset range or because its target becomes available after the
        end of the partition containing its feature timestamp.
        """
        _require_aware(feature_timestamp, "feature_timestamp")
        _require_aware(target_timestamp, "target_timestamp")

        if target_timestamp <= feature_timestamp:
            raise ValueError("target_timestamp must be after feature_timestamp.")

        if not self.start <= feature_timestamp < self.end:
            return None

        if feature_timestamp < self.train_end:
            return "train" if target_timestamp <= self.train_end else None

        if feature_timestamp < self.validation_end:
            return (
                "validation"
                if target_timestamp <= self.validation_end
                else None
            )

        return "test" if target_timestamp <= self.end else None


@dataclass(frozen=True)
class FeatureBatch:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _require_aware(self.start, "batch start")
        _require_aware(self.end, "batch end")
        if self.end <= self.start:
            raise ValueError("batch end must be after batch start.")

    def anchor_start(self, timeframe_minutes: int) -> datetime:
        return self.start - timedelta(minutes=timeframe_minutes)

    def tick_load_start(self, lookback_minutes: int) -> datetime:
        return self.start - timedelta(minutes=lookback_minutes)

    def candle_load_end(
        self,
        horizon_bars: int,
        timeframe_minutes: int,
    ) -> datetime:
        # Feature time is one completed candle after the anchor timestamp.
        # To label the last feature strictly before batch.end, we need the
        # horizon candle whose timestamp can extend (horizon_bars - 2) bars
        # beyond batch.end. The exclusive SQL boundary therefore extends one
        # additional bar: (horizon_bars - 1) * timeframe.
        extra_bars = max(0, horizon_bars - 1)
        return self.end + timedelta(
            minutes=extra_bars * timeframe_minutes
        )


def iter_feature_batches(
    start: datetime,
    end: datetime,
    batch_days: int,
) -> Iterator[FeatureBatch]:
    _require_aware(start, "start")
    _require_aware(end, "end")

    if end <= start:
        raise ValueError("end must be after start.")
    if batch_days <= 0:
        raise ValueError("batch_days must be positive.")

    cursor = start
    step = timedelta(days=batch_days)

    while cursor < end:
        batch_end = min(cursor + step, end)
        yield FeatureBatch(start=cursor, end=batch_end)
        cursor = batch_end

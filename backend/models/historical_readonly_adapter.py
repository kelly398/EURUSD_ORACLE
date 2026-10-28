
"""Read-only PostgreSQL adapter for historical EURUSD M5 candles.

Research-only. Never modifies historical candles, authorizes trades,
or certifies broker-wall-clock timestamps as true UTC.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

from sqlalchemy import text

from backend.models.historical_utc_reconstruction import (
    HistoricalReconstructionError,
    LegacyM5Bar,
    ReconstructionBatch,
    reconstruct_historical_m5_bars,
)

UTC = timezone.utc


_FIRST_PAGE = text("""
SELECT id, timestamp AS broker_label_time,
       open, high, low, close,
       tick_volume, spread_points, real_volume,
       symbol, timeframe, source, is_complete
FROM broker_candles
WHERE symbol = 'EURUSD'
  AND timeframe = 'M5'
  AND source = 'MT5'
  AND is_complete IS TRUE
  AND timestamp >= :start_label
  AND timestamp < :end_label
ORDER BY timestamp ASC, id ASC
LIMIT :page_size
""")

_NEXT_PAGE = text("""
SELECT id, timestamp AS broker_label_time,
       open, high, low, close,
       tick_volume, spread_points, real_volume,
       symbol, timeframe, source, is_complete
FROM broker_candles
WHERE symbol = 'EURUSD'
  AND timeframe = 'M5'
  AND source = 'MT5'
  AND is_complete IS TRUE
  AND timestamp >= :start_label
  AND timestamp < :end_label
  AND (timestamp, id) > (:last_label, :last_id)
ORDER BY timestamp ASC, id ASC
LIMIT :page_size
""")


class HistoricalArchiveReadError(RuntimeError):
    """Historical extraction failed and must not be admitted."""


def _validate_label(value: datetime, name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise HistoricalArchiveReadError(
            f"{name} must be a UTC-tagged broker-wall label."
        )

    value = value.astimezone(UTC)

    if (
        value.year < 2000
        or value.year > 2100
        or value.second != 0
        or value.microsecond != 0
        or value.minute % 5 != 0
    ):
        raise HistoricalArchiveReadError(
            f"{name} must be a valid M5-aligned label."
        )

    return value


@dataclass(frozen=True, slots=True)
class HistoricalArchiveWindow:
    """Half-open range of stored broker labels, not certified UTC."""

    start_label: datetime
    end_label: datetime
    page_size: int = 500
    max_rows: int = 100_000

    def __post_init__(self):
        start = _validate_label(self.start_label, "start_label")
        end = _validate_label(self.end_label, "end_label")

        if not start < end <= start + timedelta(days=366):
            raise HistoricalArchiveReadError(
                "Archive window must be positive and at most 366 days."
            )

        if (
            isinstance(self.page_size, bool)
            or not isinstance(self.page_size, int)
            or not 1 <= self.page_size <= 5000
        ):
            raise HistoricalArchiveReadError(
                "page_size must be between 1 and 5000."
            )

        if (
            isinstance(self.max_rows, bool)
            or not isinstance(self.max_rows, int)
            or not 1 <= self.max_rows <= 500_000
        ):
            raise HistoricalArchiveReadError(
                "max_rows must be between 1 and 500000."
            )


_REQUIRED_FIELDS = frozenset({
    "id",
    "broker_label_time",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "spread_points",
    "real_volume",
    "symbol",
    "timeframe",
    "source",
    "is_complete",
})


def _decode_row(row) -> LegacyM5Bar:
    """Validate database output using the existing candle contract."""

    try:
        if _REQUIRED_FIELDS.difference(row.keys()):
            raise HistoricalArchiveReadError(
                "Archive query returned an incomplete schema."
            )

        return LegacyM5Bar(
            row_id=row["id"],
            broker_label_time=row["broker_label_time"],
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            tick_volume=row["tick_volume"],
            spread_points=row["spread_points"],
            real_volume=row["real_volume"],
            symbol=row["symbol"],
            timeframe=row["timeframe"],
            source=row["source"],
            is_complete=row["is_complete"],
        )

    except (
        KeyError,
        TypeError,
        ValueError,
        HistoricalReconstructionError,
    ) as error:
        raise HistoricalArchiveReadError(
            "Invalid historical candle. Extraction halted."
        ) from error


def _require_postgres(engine):
    """Require PostgreSQL transaction semantics."""

    dialect = getattr(engine, "dialect", None)

    if getattr(dialect, "name", None) != "postgresql":
        raise HistoricalArchiveReadError(
            "A PostgreSQL engine is required."
        )


def iter_reconstructed_archive_batches(
    engine,
    window: HistoricalArchiveWindow,
) -> Iterator[ReconstructionBatch]:
    """Read and reconstruct bounded historical research batches.

    Uses one repeatable-read, read-only PostgreSQL transaction.
    All returned timestamps remain research-only UTC candidates.

    Exhaust or close the generator to release the transaction.
    If extraction fails, previously yielded batches are incomplete
    and must not be treated as a validated dataset.
    """

    if not isinstance(window, HistoricalArchiveWindow):
        raise HistoricalArchiveReadError(
            "window must be HistoricalArchiveWindow."
        )

    window.__post_init__()
    _require_postgres(engine)

    total = 0
    last_label = None
    last_id = None
    last_candidate = None
    seen_ids = set()

    with engine.connect() as connection:
        with connection.begin():

            # Database-level protection against writes.
            connection.execute(
                text(
                    "SET TRANSACTION ISOLATION LEVEL "
                    "REPEATABLE READ READ ONLY"
                )
            )

            # Legacy timestamps were stored as UTC-looking labels.
            connection.execute(
                text("SET LOCAL TIME ZONE 'UTC'")
            )

            connection.execute(
                text("SET LOCAL statement_timeout = '30s'")
            )

            while True:
                params = {
                    "start_label": window.start_label,
                    "end_label": window.end_label,
                    "page_size": window.page_size,
                }

                if last_label is None:
                    statement = _FIRST_PAGE
                else:
                    statement = _NEXT_PAGE

                    params.update({
                        "last_label": last_label,
                        "last_id": last_id,
                    })

                result = connection.execute(
                    statement,
                    params,
                )

                rows = result.mappings().all()

                if not rows:
                    break

                if len(rows) > window.page_size:
                    raise HistoricalArchiveReadError(
                        "Archive page exceeded requested size."
                    )

                if total + len(rows) > window.max_rows:
                    raise HistoricalArchiveReadError(
                        "Archive row limit exceeded."
                    )

                records = tuple(
                    _decode_row(row)
                    for row in rows
                )

                for bar in records:

                    if bar.row_id in seen_ids:
                        raise HistoricalArchiveReadError(
                            "Duplicate source row ID."
                        )

                    seen_ids.add(bar.row_id)

                    if not (
                        window.start_label
                        <= bar.broker_label_time
                        < window.end_label
                    ):
                        raise HistoricalArchiveReadError(
                            "Row outside requested archive range."
                        )

                    if (
                        last_label is not None
                        and bar.broker_label_time <= last_label
                    ):
                        raise HistoricalArchiveReadError(
                            "Duplicate or unordered broker labels."
                        )

                    last_label = bar.broker_label_time
                    last_id = bar.row_id

                try:
                    converted = reconstruct_historical_m5_bars(
                        records
                    )

                except HistoricalReconstructionError as error:
                    raise HistoricalArchiveReadError(
                        "Historical UTC reconstruction rejected "
                        "this archive batch."
                    ) from error

                if last_candidate is not None:
                    first_candidate = (
                        converted.records[0].candidate_utc_open
                    )

                    if first_candidate <= last_candidate:
                        raise HistoricalArchiveReadError(
                            "Nonmonotonic UTC reconstruction."
                        )

                last_candidate = (
                    converted.records[-1].candidate_utc_open
                )

                total += len(records)

                yield converted

    if total == 0:
        raise HistoricalArchiveReadError(
            "No completed EURUSD M5 candles found."
        )

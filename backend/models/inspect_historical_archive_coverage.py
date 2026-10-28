
"""Milestone 11: read-only coverage inventory for legacy EURUSD broker labels.

This is NOT a history collector or clock converter. Stored timestamptz
values are broker-wall labels encoded as if UTC, not certified UTC events.
The inventory counts rows; it does NOT validate gaps or approve trading.
"""

import argparse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Sequence

from sqlalchemy import text

UTC = timezone.utc
MAX_DAYS = 90

# Static SQL. Range values are bound parameters, never interpolated.
_COVERAGE_SQL = text("""
SELECT
    (timestamp AT TIME ZONE 'UTC')::date AS broker_day,
    COUNT(*) AS stored_rows,
    COUNT(*) FILTER (WHERE is_complete IS TRUE) AS complete_rows,
    COUNT(*) FILTER (WHERE is_complete IS NOT TRUE) AS incomplete_rows,
    COUNT(DISTINCT timestamp) FILTER (WHERE is_complete IS TRUE)
        AS distinct_complete_labels,
    COUNT(*) FILTER (
        WHERE is_complete IS TRUE AND tick_volume <= 0
    ) AS nonpositive_tick_volume,
    COUNT(*) FILTER (
        WHERE is_complete IS TRUE AND spread_points <= 0
    ) AS nonpositive_spread,
    MIN(timestamp) FILTER (WHERE is_complete IS TRUE) AS first_complete_label,
    MAX(timestamp) FILTER (WHERE is_complete IS TRUE) AS last_complete_label
FROM broker_candles
WHERE symbol = 'EURUSD'
  AND timeframe = 'M5'
  AND source = 'MT5'
  AND timestamp >= :start_label
  AND timestamp < :end_label
GROUP BY (timestamp AT TIME ZONE 'UTC')::date
ORDER BY broker_day
""")


class CoverageInspectionError(RuntimeError):
    """Fail closed on invalid parameters or inconsistent database output."""


def _day(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("Date must be YYYY-MM-DD.") from exc

    if parsed.isoformat() != value or not 2000 <= parsed.year <= 2100:
        raise argparse.ArgumentTypeError(
            "Date must be YYYY-MM-DD, 2000-2100."
        )

    return parsed


def _count(value, field: str) -> int:
    if type(value) is not int or value < 0:
        raise CoverageInspectionError(
            f"Invalid database count: {field}."
        )
    return value


@dataclass(frozen=True)
class DailyCoverage:
    day: date
    stored: int
    complete: int
    incomplete: int
    nonpositive_volume: int
    nonpositive_spread: int
    first_label: datetime | None
    last_label: datetime | None

    @property
    def label(self) -> str:
        if self.stored == 0:
            return "NO_STORED_ROWS"
        if self.complete == 0:
            return "NO_COMPLETED_CANDLES"
        if self.complete == 288 and self.incomplete == 0:
            return "288_COMPLETE_LABELS"
        return "PARTIAL_OR_SESSION_LIMITED"


@dataclass(frozen=True)
class CoverageReport:
    start_date: date
    end_date: date
    days: tuple[DailyCoverage, ...]

    @property
    def total_complete(self) -> int:
        return sum(d.complete for d in self.days)

    @property
    def observed_days(self) -> int:
        return sum(d.complete > 0 for d in self.days)


def inspect_coverage(
    engine,
    start_date: date,
    end_date: date,
) -> CoverageReport:
    """Run one PostgreSQL read-only snapshot and return a daily inventory.

    The end date is exclusive. A row-free day may be a market closure,
    holiday, ingestion outage, or simply outside the archive's range.
    """

    if type(start_date) is not date or type(end_date) is not date:
        raise CoverageInspectionError(
            "Use date objects for calendar bounds."
        )

    if (
        not 2000 <= start_date.year <= 2100
        or not 2000 <= end_date.year <= 2100
    ):
        raise CoverageInspectionError(
            "Dates must be within 2000-2100."
        )

    count_days = (end_date - start_date).days

    if not 1 <= count_days <= MAX_DAYS:
        raise CoverageInspectionError(
            "Choose 1 to 90 calendar days."
        )

    if (
        getattr(
            getattr(engine, "dialect", None),
            "name",
            None,
        ) != "postgresql"
    ):
        raise CoverageInspectionError(
            "A PostgreSQL engine is required."
        )

    start = datetime.combine(start_date, time.min, UTC)
    end = datetime.combine(end_date, time.min, UTC)

    observed = {}

    try:
        with engine.connect() as conn:
            with conn.begin():

                # Must be the first SQL in this transaction.
                conn.execute(
                    text(
                        "SET TRANSACTION ISOLATION LEVEL "
                        "REPEATABLE READ READ ONLY"
                    )
                )

                conn.execute(
                    text("SET LOCAL statement_timeout = '30s'")
                )

                result = conn.execute(
                    _COVERAGE_SQL,
                    {
                        "start_label": start,
                        "end_label": end,
                    },
                )

                for row in result.mappings():

                    day = row["broker_day"]

                    if (
                        type(day) is not date
                        or not start_date <= day < end_date
                    ):
                        raise CoverageInspectionError(
                            "Unexpected database day."
                        )

                    if day in observed:
                        raise CoverageInspectionError(
                            "Repeated database day."
                        )

                    stored = _count(
                        row["stored_rows"],
                        "stored_rows",
                    )

                    complete = _count(
                        row["complete_rows"],
                        "complete_rows",
                    )

                    incomplete = _count(
                        row["incomplete_rows"],
                        "incomplete_rows",
                    )

                    distinct = _count(
                        row["distinct_complete_labels"],
                        "distinct_complete_labels",
                    )

                    volume = _count(
                        row["nonpositive_tick_volume"],
                        "nonpositive_tick_volume",
                    )

                    spread = _count(
                        row["nonpositive_spread"],
                        "nonpositive_spread",
                    )

                    if (
                        stored != complete + incomplete
                        or complete > 288
                        or distinct != complete
                        or volume > complete
                        or spread > complete
                    ):
                        raise CoverageInspectionError(
                            "Inconsistent/duplicate M5 candle counts."
                        )

                    first = row["first_complete_label"]
                    last = row["last_complete_label"]

                    if complete == 0:
                        if first is not None or last is not None:
                            raise CoverageInspectionError(
                                "Invalid empty-day bounds."
                            )

                    else:
                        if (
                            not isinstance(first, datetime)
                            or not isinstance(last, datetime)
                            or first.tzinfo is None
                            or last.tzinfo is None
                            or first > last
                            or first.astimezone(UTC).date() != day
                            or last.astimezone(UTC).date() != day
                        ):
                            raise CoverageInspectionError(
                                "Invalid day timestamp bounds."
                            )

                    observed[day] = DailyCoverage(
                        day,
                        stored,
                        complete,
                        incomplete,
                        volume,
                        spread,
                        first,
                        last,
                    )

    except CoverageInspectionError:
        raise

    except Exception as exc:
        # Do not expose credentials or connection parameters.
        raise CoverageInspectionError(
            "Read-only coverage query failed "
            f"({type(exc).__name__})."
        ) from None

    days = []

    for offset in range(count_days):
        day = start_date + timedelta(days=offset)

        days.append(
            observed.get(
                day,
                DailyCoverage(
                    day, 0, 0, 0, 0, 0, None, None
                ),
            )
        )

    return CoverageReport(
        start_date,
        end_date,
        tuple(days),
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    engine=None,
) -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Read-only inventory of stored EURUSD M5 "
            "broker-label days."
        )
    )

    parser.add_argument(
        "--start-date",
        required=True,
        type=_day,
    )

    parser.add_argument(
        "--end-date",
        required=True,
        type=_day,
    )

    parser.add_argument(
        "--research-only",
        required=True,
        action="store_true",
    )

    args = parser.parse_args(argv)

    try:
        if engine is None:
            from backend.database.connection import (
                engine as configured_engine,
            )
            engine = configured_engine

        report = inspect_coverage(
            engine,
            args.start_date,
            args.end_date,
        )

    except CoverageInspectionError as exc:
        print(
            f"COVERAGE INSPECTION BLOCKED: {exc}"
        )
        return 1

    except Exception as exc:
        print(
            "COVERAGE INSPECTION BLOCKED: "
            f"{type(exc).__name__}"
        )
        return 1

    print(
        "EURUSD ORACLE | Legacy M5 archive inventory (read-only)"
    )

    print(
        "Stored broker labels, not certified event times."
    )

    for day in report.days:
        print(
            f"{day.day.isoformat()} | "
            f"complete={day.complete:3d} "
            f"incomplete={day.incomplete:3d} "
            f"nonpositive_volume={day.nonpositive_volume:3d} "
            f"nonpositive_spread={day.nonpositive_spread:3d} "
            f"| {day.label}"
        )

    print(
        f"SUMMARY: {report.total_complete} completed candles "
        f"across {report.observed_days}/{len(report.days)} days."
    )

    print(
        "No files written | UTC uncertified | "
        "TRAINING/EXECUTION BLOCKED"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

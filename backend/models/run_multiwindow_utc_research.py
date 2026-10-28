
"""Milestone 10: bounded multi-window coordinator for legacy EURUSD research.

Reuses Milestone 05 for every window; creates no new collector, SQL, clock
conversion, or trading path. All bounds are broker-wall labels tagged UTC,
not certified market-event timestamps.

Plan only by default. --execute reads PostgreSQL, and --export additionally
publishes per-window research artifacts. Each window uses its own read-only
snapshot; the entire range is NOT a single atomic database snapshot.
"""

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import re
from typing import Callable, Sequence

from backend.models.historical_readonly_adapter import (
    HistoricalArchiveWindow,
    HistoricalArchiveReadError,
)
from backend.models.run_historical_utc_research import (
    run as run_single_window,
)

UTC = timezone.utc
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
MAX_DAYS = 366
MAX_WINDOWS = 64


class WindowPlanError(ValueError):
    """A historical research window plan is invalid."""


def _date(value: str) -> date:
    if not _DATE.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "Use YYYY-MM-DD for broker-label dates."
        )

    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Invalid broker-label date."
        ) from exc

    if not 2000 <= parsed.year <= 2100:
        raise argparse.ArgumentTypeError(
            "Date outside supported years."
        )

    return parsed


@dataclass(frozen=True)
class PlannedWindow:
    index: int
    start_label: datetime
    end_label: datetime

    def as_args(
        self,
        *,
        page_size: int,
        max_rows: int,
        export: bool,
    ) -> list[str]:

        args = [
            "--start-label",
            self.start_label.isoformat(),
            "--end-label",
            self.end_label.isoformat(),
            "--page-size",
            str(page_size),
            "--max-rows",
            str(max_rows),
            "--research-only",
        ]

        if export:
            args.append("--export")

        return args


def plan_windows(
    start_date: date,
    end_date: date,
    *,
    window_days: int = 30,
    page_size: int = 500,
    max_rows: int = 100_000,
) -> tuple[PlannedWindow, ...]:
    """Make disjoint half-open broker-label windows, with no data access."""

    if (
        type(start_date) is not date
        or type(end_date) is not date
    ):
        raise WindowPlanError(
            "Use calendar dates, not datetime objects."
        )

    if not (
        2000 <= start_date.year <= 2100
        and 2000 <= end_date.year <= 2100
    ):
        raise WindowPlanError(
            "Date outside supported years."
        )

    total_days = (end_date - start_date).days

    if not 1 <= total_days <= MAX_DAYS:
        raise WindowPlanError(
            "Date range must cover 1 to 366 days."
        )

    if (
        type(window_days) is not int
        or not 1 <= window_days <= 90
    ):
        raise WindowPlanError(
            "window_days must be from 1 to 90."
        )

    if (
        type(page_size) is not int
        or not 1 <= page_size <= 5000
    ):
        raise WindowPlanError(
            "page_size must be from 1 to 5000."
        )

    if (
        type(max_rows) is not int
        or not 1 <= max_rows <= 500_000
    ):
        raise WindowPlanError(
            "max_rows must be from 1 to 500000."
        )

    count = (
        total_days + window_days - 1
    ) // window_days

    if count > MAX_WINDOWS:
        raise WindowPlanError(
            "Too many windows; increase --window-days."
        )

    windows: list[PlannedWindow] = []
    cursor = start_date

    for index in range(1, count + 1):
        finish = min(
            cursor + timedelta(days=window_days),
            end_date,
        )

        start_label = datetime.combine(
            cursor,
            datetime.min.time(),
            UTC,
        )

        end_label = datetime.combine(
            finish,
            datetime.min.time(),
            UTC,
        )

        # Use the existing database adapter's constraints.
        try:
            HistoricalArchiveWindow(
                start_label=start_label,
                end_label=end_label,
                page_size=page_size,
                max_rows=max_rows,
            )

        except HistoricalArchiveReadError as exc:
            raise WindowPlanError(
                "Adapter rejected proposed window."
            ) from exc

        windows.append(
            PlannedWindow(
                index,
                start_label,
                end_label,
            )
        )

        cursor = finish

    if cursor != end_date or len(windows) != count:
        raise WindowPlanError(
            "Incomplete window plan."
        )

    return tuple(windows)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Plan or execute research-only, "
            "non-overlapping broker-label windows."
        )
    )

    parser.add_argument(
        "--start-date",
        type=_date,
        required=True,
    )

    parser.add_argument(
        "--end-date",
        type=_date,
        required=True,
        help="Exclusive broker-label calendar date",
    )

    parser.add_argument(
        "--window-days",
        type=int,
        default=30,
    )

    parser.add_argument(
        "--page-size",
        type=int,
        default=500,
    )

    parser.add_argument(
        "--max-rows",
        type=int,
        default=100_000,
    )

    parser.add_argument(
        "--research-only",
        action="store_true",
        required=True,
    )

    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Open read-only PostgreSQL snapshots "
            "using Milestone 05"
        ),
    )

    parser.add_argument(
        "--export",
        action="store_true",
        help=(
            "Requires --execute; publish isolated "
            "research-only exports"
        ),
    )

    return parser


def run(
    argv: Sequence[str] | None = None,
    *,
    single_window_runner: Callable[..., int] | None = None,
) -> int:

    args = _parser().parse_args(argv)

    if args.export and not args.execute:
        print(
            "REJECTED: --export requires --execute."
        )
        return 2

    try:
        windows = plan_windows(
            args.start_date,
            args.end_date,
            window_days=args.window_days,
            page_size=args.page_size,
            max_rows=args.max_rows,
        )

    except WindowPlanError as exc:
        print(f"REJECTED: {exc}")
        return 2

    print(
        "EURUSD ORACLE | Multi-window "
        "UTC-candidate RESEARCH ONLY"
    )

    print(
        "All dates are stored broker labels, "
        "not certified UTC event times."
    )

    print(
        "Each executed window gets its OWN "
        "read-only PostgreSQL snapshot."
    )

    print(
        f"Windows planned: {len(windows)} | "
        f"Window-days: {args.window_days}"
    )

    for window in windows:
        print(
            f"Window {window.index}/{len(windows)}: "
            f"[{window.start_label.date()}, "
            f"{window.end_label.date()})"
        )

    if not args.execute:
        print(
            "PLAN ONLY: no database access "
            "and no files written."
        )
        return 0

    runner = (
        single_window_runner or run_single_window
    )

    completed = 0

    for window in windows:
        print(
            f"RUNNING WINDOW "
            f"{window.index}/{len(windows)}"
        )

        options = window.as_args(
            page_size=args.page_size,
            max_rows=args.max_rows,
            export=args.export,
        )

        try:
            status = runner(options)

        except KeyboardInterrupt:
            print(
                "STOPPED: interrupted; previously "
                "completed exports may remain."
            )
            return 130

        except Exception as exc:
            # Do not disclose database credentials.
            print(
                f"STOPPED: {type(exc).__name__}; "
                "no later windows attempted."
            )

            if args.export:
                print(
                    "Previously published window "
                    "exports, if any, remain on disk."
                )

            return 1

        if status != 0:
            print(
                f"STOPPED at window "
                f"{window.index}/{len(windows)}; "
                f"single-window exit code: {status}."
            )

            if args.export:
                print(
                    "Earlier window exports may exist; "
                    "this run is NOT fully complete."
                )

            return 130 if status == 130 else 1

        completed += 1

    print(
        f"COMPLETED: {completed}/{len(windows)} "
        "independent research windows."
    )

    print(
        "No consolidated, atomic research "
        "dataset was created."
    )

    print(
        "UTC CERTIFICATION: NO | "
        "TRAINING: BLOCKED | "
        "EXECUTION: BLOCKED"
    )

    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()

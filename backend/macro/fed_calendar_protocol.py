
"""F4.5.4a: Offline, diagnostic FOMC regular-meeting schedule protocol.

The 2026 dates and planned 2 p.m. Eastern statement time originate from
Federal Reserve press release monetary20240809a (August 9, 2024).

This module does NOT acquire, authenticate, or attest original Fed HTML.
A published release-time LABEL and a supplied observation timestamp do not
prove that the current website bytes existed at that historical instant.
Therefore all calendar admission/persistence/replay permissions remain False.
No Council context, economic-calendar snapshots, database, or HTTP calls.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
import json
from zoneinfo import ZoneInfo


FED_FOMC_SCHEDULE_SOURCE = "FED_FOMC_SCHEDULE_RELEASE"

FED_2026_SCHEDULE_URL = (
    "https://www.federalreserve.gov/newsevents/pressreleases/"
    "monetary20240809a.htm"
)

FED_EASTERN_TIMEZONE = "America/New_York"

FED_2026_SCHEDULE_RELEASE_LABEL_UTC = datetime(
    2024, 8, 9, 17, 30, tzinfo=timezone.utc
)

FED_REGULAR_STATEMENT_LOCAL_TIME = time(14, 0)


class FedCalendarProtocolError(ValueError):
    """A regular-meeting schedule candidate violates the local contract."""


@dataclass(frozen=True)
class FedScheduledMeetingSpec:
    year: int
    ordinal: int
    meeting_start: date
    meeting_end: date

    def __post_init__(self) -> None:
        if type(self.year) is not int or self.year != 2026:
            raise FedCalendarProtocolError(
                "Only the audited 2026 schedule is supported."
            )

        if type(self.ordinal) is not int or not 1 <= self.ordinal <= 8:
            raise FedCalendarProtocolError(
                "Invalid FOMC 2026 meeting ordinal."
            )

        if (
            type(self.meeting_start) is not date
            or type(self.meeting_end) is not date
        ):
            raise FedCalendarProtocolError(
                "Meeting start/end must be dates."
            )

        if (
            self.meeting_start.year != 2026
            or self.meeting_end.year != 2026
        ):
            raise FedCalendarProtocolError(
                "Meeting dates must be within 2026."
            )

        if self.meeting_end - self.meeting_start != timedelta(days=1):
            raise FedCalendarProtocolError(
                "Regular 2026 meetings span two days."
            )


FED_2026_SCHEDULED_MEETINGS = (
    FedScheduledMeetingSpec(
        2026, 1, date(2026, 1, 27), date(2026, 1, 28)
    ),
    FedScheduledMeetingSpec(
        2026, 2, date(2026, 3, 17), date(2026, 3, 18)
    ),
    FedScheduledMeetingSpec(
        2026, 3, date(2026, 4, 28), date(2026, 4, 29)
    ),
    FedScheduledMeetingSpec(
        2026, 4, date(2026, 6, 16), date(2026, 6, 17)
    ),
    FedScheduledMeetingSpec(
        2026, 5, date(2026, 7, 28), date(2026, 7, 29)
    ),
    FedScheduledMeetingSpec(
        2026, 6, date(2026, 9, 15), date(2026, 9, 16)
    ),
    FedScheduledMeetingSpec(
        2026, 7, date(2026, 10, 27), date(2026, 10, 28)
    ),
    FedScheduledMeetingSpec(
        2026, 8, date(2026, 12, 8), date(2026, 12, 9)
    ),
)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise FedCalendarProtocolError(
            f"{field_name} must be timezone-aware."
        )

    return value.astimezone(timezone.utc)


def build_fed_schedule_event_id(year: int, ordinal: int) -> str:
    if type(year) is not int or year != 2026:
        raise FedCalendarProtocolError(
            "Only 2026 FOMC schedule IDs are supported."
        )

    if type(ordinal) is not int or not 1 <= ordinal <= 8:
        raise FedCalendarProtocolError(
            "Invalid FOMC schedule ordinal."
        )

    # Stable across a changed meeting date in a later schedule revision.
    return f"FED|FOMC_REGULAR_MEETING|{year}|{ordinal:02d}"


def planned_fed_statement_at(
    spec: FedScheduledMeetingSpec,
) -> datetime:
    """Return nominal regular-meeting 2 p.m. ET, NOT actual release time."""

    if not isinstance(spec, FedScheduledMeetingSpec):
        raise TypeError("Expected FedScheduledMeetingSpec.")

    local = datetime.combine(
        spec.meeting_end,
        FED_REGULAR_STATEMENT_LOCAL_TIME,
        tzinfo=ZoneInfo(FED_EASTERN_TIMEZONE),
    )

    return local.astimezone(timezone.utc)


@dataclass(frozen=True)
class FedScheduleCandidate:
    event_id: str
    meeting_start: date
    meeting_end: date
    planned_statement_at: datetime
    claimed_schedule_release_at: datetime
    first_observed_at: datetime
    source: str
    source_url: str
    schedule_content_sha256: str

    @property
    def historical_version_attested(self) -> bool:
        return False

    @property
    def council_calendar_admission_authorized(self) -> bool:
        return False

    @property
    def historical_replay_authorized(self) -> bool:
        return False

    @property
    def persistence_authorized(self) -> bool:
        return False

    def was_observed_by(self, as_of: datetime) -> bool:
        """Observation-clock check only; never independent source attestation."""

        return self.first_observed_at <= _aware_utc(as_of, "as_of")


def build_fed_schedule_content_hash(
    spec: FedScheduledMeetingSpec,
) -> str:
    """Pure economic schedule hash; does not authenticate a source version."""

    if not isinstance(spec, FedScheduledMeetingSpec):
        raise TypeError("Expected FedScheduledMeetingSpec.")

    payload = {
        "contract": "fed_regular_schedule_candidate_v1",
        "event_id": build_fed_schedule_event_id(
            spec.year, spec.ordinal
        ),
        "meeting_start": spec.meeting_start.isoformat(),
        "meeting_end": spec.meeting_end.isoformat(),
        "planned_statement_at_utc": (
            planned_fed_statement_at(spec).isoformat()
        ),
        "source": FED_FOMC_SCHEDULE_SOURCE,
        "source_url": FED_2026_SCHEDULE_URL,
    }

    return sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def make_fed_schedule_candidate(
    spec: FedScheduledMeetingSpec,
    *,
    first_observed_at: datetime,
) -> FedScheduleCandidate:
    """Construct a quarantined schedule candidate without source-version approval.

    first_observed_at must come from a separately controlled acquisition ledger.
    This function does not verify the caller's clock or captured bytes.
    """

    if not isinstance(spec, FedScheduledMeetingSpec):
        raise TypeError("Expected FedScheduledMeetingSpec.")

    # This 2024 release supports ONLY its exact eight published 2026 dates.
    # A rescheduled meeting needs an independently reviewed new source version.
    if spec != FED_2026_SCHEDULED_MEETINGS[spec.ordinal - 1]:
        raise FedCalendarProtocolError(
            "Meeting is not in the audited 2024 schedule release."
        )

    observed_utc = _aware_utc(
        first_observed_at, "first_observed_at"
    )

    if observed_utc < FED_2026_SCHEDULE_RELEASE_LABEL_UTC:
        raise FedCalendarProtocolError(
            "Observation precedes the claimed official schedule release."
        )

    event_id = build_fed_schedule_event_id(
        spec.year, spec.ordinal
    )

    planned_at = planned_fed_statement_at(spec)

    content_hash = build_fed_schedule_content_hash(spec)

    return FedScheduleCandidate(
        event_id=event_id,
        meeting_start=spec.meeting_start,
        meeting_end=spec.meeting_end,
        planned_statement_at=planned_at,
        claimed_schedule_release_at=FED_2026_SCHEDULE_RELEASE_LABEL_UTC,
        first_observed_at=observed_utc,
        source=FED_FOMC_SCHEDULE_SOURCE,
        source_url=FED_2026_SCHEDULE_URL,
        schedule_content_sha256=content_hash,
    )


"""F4.5.6: Pure, fail-closed extraction of one Fed schedule acquisition.

Reconciles the visible meeting list of monetary20240809a against the pinned
2026 schedule protocol. This is NOT authentication of 2024 source bytes,
historical availability certification, calendar admission, or persistence.

No HTTP, database, Council, broker or HMM calls.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import re

from backend.macro.fed_calendar_client import (
    FedCalendarAcquisitionRecord,
    MAX_FED_CALENDAR_HTML_BYTES,
)
from backend.macro.fed_calendar_protocol import (
    FED_2026_SCHEDULED_MEETINGS,
    FED_2026_SCHEDULE_RELEASE_LABEL_UTC,
    FED_2026_SCHEDULE_URL,
    FedScheduleCandidate,
    make_fed_schedule_candidate,
)


class FedCalendarExtractionError(ValueError):
    """The acquired page does not unambiguously match the pinned schedule."""


_MONTHS = {
    name: number
    for number, name in enumerate(
        ("January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"),
        start=1,
    )
}

_WEEKDAYS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
)

_DAY_ROW = re.compile(
    r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"(\d{1,2}),\s*and\s*"
    r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"(\d{1,2})(?:,\s*(\d{4}))?$",
    re.IGNORECASE,
)

_POLICY_RULE = re.compile(
    r"^The Committee releases a policy statement at 2\s*p\.?\s*m\.?\s*"
    r"Eastern Time on the second day of each regularly scheduled meeting,\s*"
    r"and the Chair holds a news conference at 2:30\s*p\.?\s*m\.?\s*"
    r"Eastern Time the same day\.$",
    re.IGNORECASE,
)

_RELEASE_LABEL = re.compile(
    r"^For release at 1:30\s*p\.?\s*m\.?\s*EDT$",
    re.IGNORECASE,
)

_TITLE = (
    "Federal Open Market Committee announces its tentative meeting "
    "schedule for 2025 and 2026"
)

_BLOCKS = {
    "p", "li", "ul", "ol", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "tr", "td", "th",
}

_INVISIBLE = {
    "head", "script", "style", "nav", "footer", "aside", "template", "noscript"
}


class _VisibleLines(HTMLParser):
    """Collect visible block lines, ignoring metadata and chrome."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._pieces: list[str] = []
        self._suppress_counts: dict[str, int] = {}
        self._body_active = False
        self.body_seen = False

    def _flush(self) -> None:
        if self._pieces:
            line = re.sub(
                r"\s+", " ", " ".join(self._pieces)
            ).strip()

            if line:
                self.lines.append(line)

            self._pieces.clear()

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self._body_active = True
            self.body_seen = True

        if tag in _INVISIBLE:
            self._suppress_counts[tag] = (
                self._suppress_counts.get(tag, 0) + 1
            )

        if tag in _BLOCKS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in _BLOCKS:
            self._flush()

        if tag in _INVISIBLE and self._suppress_counts.get(tag, 0):
            self._suppress_counts[tag] -= 1

        if tag == "body":
            self._flush()
            self._body_active = False

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCKS:
            self._flush()

    def handle_data(self, data):
        if (
            self._body_active
            and not any(self._suppress_counts.values())
        ):
            self._pieces.append(data)


def _utc(value: datetime, name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise FedCalendarExtractionError(
            f"{name} must be timezone-aware."
        )

    return value.astimezone(timezone.utc)


def _parse_row(
    text: str,
    *,
    year: int,
) -> tuple[date, date, int | None]:

    match = _DAY_ROW.fullmatch(text)

    if match is None:
        raise FedCalendarExtractionError(
            "Unexpected FOMC schedule meeting row."
        )

    (
        wd_start,
        month_start,
        day_start,
        wd_end,
        month_end,
        day_end,
        explicit_year,
    ) = match.groups()

    declared_year = (
        int(explicit_year)
        if explicit_year
        else None
    )

    row_year = (
        declared_year
        if declared_year is not None
        else year
    )

    try:
        start = date(
            row_year,
            _MONTHS[month_start.capitalize()],
            int(day_start),
        )

        end = date(
            row_year,
            _MONTHS[month_end.capitalize()],
            int(day_end),
        )

    except ValueError as exc:
        raise FedCalendarExtractionError(
            "Invalid FOMC schedule calendar date."
        ) from exc

    if (
        _WEEKDAYS[start.weekday()].casefold() != wd_start.casefold()
        or _WEEKDAYS[end.weekday()].casefold() != wd_end.casefold()
    ):
        raise FedCalendarExtractionError(
            "FOMC schedule weekday/date mismatch."
        )

    if (end - start).days != 1:
        raise FedCalendarExtractionError(
            "FOMC schedule meeting must span two days."
        )

    return start, end, declared_year


@dataclass(frozen=True)
class FedCalendarScheduleExtraction:
    """A reconciled content observation. Never a historical attestation."""

    source_url: str
    observed_at: datetime
    entity_body_sha256: str
    transport_mode: str
    candidates: tuple[FedScheduleCandidate, ...]
    rollover_2027_start: date
    rollover_2027_end: date

    @property
    def historical_source_version_attested(self) -> bool:
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


def extract_fed_calendar_schedule(
    record: FedCalendarAcquisitionRecord,
) -> FedCalendarScheduleExtraction:
    """Reconcile exact eight 2026 meetings plus the 2027 rollover item.

    Validates claimed acquisition metadata against the retained entity bytes.
    A matching schedule does not show when the source version first existed.
    """

    if type(record) is not FedCalendarAcquisitionRecord:
        raise TypeError(
            "Expected FedCalendarAcquisitionRecord."
        )

    if (
        record.source_url != FED_2026_SCHEDULE_URL
        or record.response_url != FED_2026_SCHEDULE_URL
        or type(record.http_status) is not int
        or record.http_status != 200
    ):
        raise FedCalendarExtractionError(
            "FOMC schedule source identity mismatch."
        )

    if (
        type(record.content_type) is not str
        or record.content_type.split(";", 1)[0].strip().lower()
        != "text/html"
    ):
        raise FedCalendarExtractionError(
            "FOMC schedule content-type mismatch."
        )

    if record.transport_mode not in (
        "DEFAULT_VERIFIED_TLS_CONFIGURATION",
        "INJECTED_TRANSPORT_UNVERIFIED",
    ):
        raise FedCalendarExtractionError(
            "Unknown FOMC acquisition transport label."
        )

    started = _utc(
        record.request_started_at,
        "request_started_at",
    )

    observed = _utc(
        record.response_completed_at,
        "response_completed_at",
    )

    if (
        observed < started
        or observed < FED_2026_SCHEDULE_RELEASE_LABEL_UTC
    ):
        raise FedCalendarExtractionError(
            "Invalid FOMC schedule observation chronology."
        )

    body = record.entity_body

    if (
        type(body) is not bytes
        or not (0 < len(body) <= MAX_FED_CALENDAR_HTML_BYTES)
    ):
        raise FedCalendarExtractionError(
            "FOMC schedule entity body is invalid."
        )

    if (
        type(record.entity_body_sha256) is not str
        or sha256(body).hexdigest() != record.entity_body_sha256
    ):
        raise FedCalendarExtractionError(
            "FOMC schedule source byte SHA-256 mismatch."
        )

    if (
        b"<html" not in body[:8192].lower()
        or b"</html>" not in body.lower()
    ):
        raise FedCalendarExtractionError(
            "FOMC schedule HTML envelope is incomplete."
        )

    try:
        html_text = body.decode(
            "utf-8",
            errors="strict",
        )

    except UnicodeDecodeError as exc:
        raise FedCalendarExtractionError(
            "FOMC schedule HTML must be UTF-8."
        ) from exc

    parser = _VisibleLines()

    try:
        parser.feed(html_text)
        parser.close()

    except (ValueError, AssertionError) as exc:
        raise FedCalendarExtractionError(
            "FOMC schedule HTML parsing failed."
        ) from exc

    if not parser.body_seen:
        raise FedCalendarExtractionError(
            "FOMC schedule HTML requires body element."
        )

    lines = parser.lines

    title_positions = [
        i
        for i, line in enumerate(lines)
        if line.casefold() == _TITLE.casefold()
    ]

    date_positions = [
        i
        for i, line in enumerate(lines)
        if line == "August 09, 2024"
    ]

    release_positions = [
        i
        for i, line in enumerate(lines)
        if _RELEASE_LABEL.fullmatch(line)
    ]

    if len(title_positions) != 1:
        raise FedCalendarExtractionError(
            "FOMC schedule document title mismatch."
        )

    if len(date_positions) != 1:
        raise FedCalendarExtractionError(
            "FOMC schedule document date mismatch."
        )

    if len(release_positions) != 1:
        raise FedCalendarExtractionError(
            "FOMC schedule release label mismatch."
        )

    year_2025 = [
        i
        for i, line in enumerate(lines)
        if line.casefold() == "for 2025:"
    ]

    year_2026 = [
        i
        for i, line in enumerate(lines)
        if line.casefold() == "for 2026:"
    ]

    policy = [
        i
        for i, line in enumerate(lines)
        if _POLICY_RULE.fullmatch(line)
    ]

    if (
        len(year_2025) != 1
        or len(year_2026) != 1
        or len(policy) != 1
        or not (
            date_positions[0]
            < title_positions[0]
            < release_positions[0]
            < year_2025[0]
            < year_2026[0]
            < policy[0]
        )
    ):
        raise FedCalendarExtractionError(
            "FOMC 2026 schedule section is ambiguous."
        )

    section_lines = lines[
        year_2026[0] + 1 : policy[0]
    ]

    if len(section_lines) != 9:
        raise FedCalendarExtractionError(
            "Expected eight 2026 meetings and one 2027 rollover meeting."
        )

    candidates = []

    for spec, row in zip(
        FED_2026_SCHEDULED_MEETINGS,
        section_lines[:8],
        strict=True,
    ):
        start, end, explicit_year = _parse_row(
            row,
            year=2026,
        )

        if explicit_year not in (None, 2026):
            raise FedCalendarExtractionError(
                "2026 meeting has incorrect year label."
            )

        if (
            start,
            end,
        ) != (
            spec.meeting_start,
            spec.meeting_end,
        ):
            raise FedCalendarExtractionError(
                "FOMC 2026 meeting differs from pinned schedule protocol."
            )

        candidates.append(
            make_fed_schedule_candidate(
                spec,
                first_observed_at=observed,
            )
        )

    (
        rollover_start,
        rollover_end,
        rollover_year,
    ) = _parse_row(
        section_lines[8],
        year=2026,
    )

    if (
        rollover_year != 2027
        or rollover_start != date(2027, 1, 26)
        or rollover_end != date(2027, 1, 27)
    ):
        raise FedCalendarExtractionError(
            "Unexpected 2027 rollover meeting."
        )

    return FedCalendarScheduleExtraction(
        source_url=record.source_url,
        observed_at=observed,
        entity_body_sha256=record.entity_body_sha256,
        transport_mode=record.transport_mode,
        candidates=tuple(candidates),
        rollover_2027_start=rollover_start,
        rollover_2027_end=rollover_end,
    )

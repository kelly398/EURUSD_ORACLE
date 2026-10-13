
"""F2.4: fail-closed extraction of original FOMC policy-rate evidence.

This module never fetches documents, writes to a database, or manufactures an
availability timestamp. It extracts separately attested facts from the three
official HTML documents returned by FedRateClient, then delegates final
cross-document validation to the frozen F1 Fed rate protocol.

V1 supports audited FOMC statement/Desk-directive/meeting-page wording only.
Unrecognized layouts fail closed and require a new, tested extraction variant.
It does not ingest corrections or prove historical HTML version availability.
"""

from datetime import date, datetime
from html.parser import HTMLParser
import hashlib
import re
from urllib.parse import urljoin

from backend.macro.fed_rate_client import (
    FedSourceBundle,
    FedSourceDocument,
)

from backend.macro.fed_rate_protocol import (
    FED_RATE_MEETING_PATH,
    FED_RATE_SOURCE_PATH,
    FedMeetingIndexStamp,
    FedOriginalRateEvidence,
    FedRateProtocolError,
    FedReleaseStamp,
    build_fed_statement_id,
    validate_fed_original_evidence,
)


class FedEvidenceExtractionError(FedRateProtocolError):
    """Official HTML cannot be unambiguously mapped to the F1 contract."""


_MONTHS = (
    "January|February|March|April|May|June|July|August|"
    "September|October|November|December"
)

_DATE = rf"(?:{_MONTHS})\s+\d{{1,2}},\s+20\d{{2}}"

_CLOCK = (
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})"
    r"\s*(?P<ampm>a\.m\.|p\.m\.)"
)

_RATE = (
    r"(?:\d{1,3}-\d{1,2}/\d{1,2}|"
    r"\d{1,2}/\d{1,2}|"
    r"\d{1,3}(?:\.\d{1,6})?)"
)


# The original statement must independently attest its release time.
_STATEMENT_HEADER = re.compile(
    rf"(?P<release_date>{_DATE})\s+"
    r"Federal Reserve issues FOMC statement\s+"
    r"For release at\s+"
    + _CLOCK
    + r"\s+(?P<zone>EST|EDT)\b",
    re.IGNORECASE,
)


# Extract the Committee's adopted target, not a dissenting alternative.
_STATEMENT_RATE = re.compile(
    r"\bThe Committee decided to\s+"
    r"(?:raise|lower|maintain)\s+"
    r"the target range for the federal funds rate\b"
    r"[^.]{0,180}?\b(?:to|at)\s+"
    rf"(?P<lower>{_RATE})\s+to\s+"
    rf"(?P<upper>{_RATE})\s+percent\b",
    re.IGNORECASE,
)


_IMPL_TITLE = re.compile(
    rf"\bImplementation Note issued\s+"
    rf"(?P<issue_date>{_DATE})\b",
    re.IGNORECASE,
)


# Only the FOMC Desk directive establishes the target and its effective date.
# Do not mistake reserve-balance, repo, or primary-credit rates for this target.
_IMPL_DIRECTIVE = re.compile(
    rf"\bEffective\s+(?P<effective_date>{_DATE}),\s+"
    r"the Federal Open Market Committee directs the Desk to\s*:?\s*"
    r"undertake open market operations as necessary to maintain the "
    r"federal funds rate in a target range of\s+"
    rf"(?P<lower>{_RATE})\s+to\s+"
    rf"(?P<upper>{_RATE})\s+percent\b",
    re.IGNORECASE,
)


_MEETING_STATEMENT = re.compile(
    rf"\bFOMC Meeting Statement:\s+PDF\s*\|\s*HTML\s*"
    rf"\(Released\s+(?P<release_date>{_DATE})\s+at\s+"
    + _CLOCK
    + r"\)",
    re.IGNORECASE,
)


_MEETING_IMPLEMENTATION = re.compile(
    rf"\bImplementation Note\s*\(Released\s+"
    rf"(?P<release_date>{_DATE})\s+at\s+"
    + _CLOCK
    + r"\)",
    re.IGNORECASE,
)


class _VisibleHTML(HTMLParser):
    """Inert HTML reader; does not execute scripts or fetch linked resources."""

    _IGNORED = frozenset(
        ("head", "script", "style", "noscript", "template")
    )

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._parts = []
        self.links = set()

    def handle_starttag(self, tag, attrs):
        if tag in self._IGNORED:
            self._ignored_depth += 1

        if tag == "a" and self._ignored_depth == 0:
            self.links.update(
                value
                for key, value in attrs
                if key == "href" and isinstance(value, str)
            )

    def handle_endtag(self, tag):
        if tag in self._IGNORED:
            self._ignored_depth = max(
                0, self._ignored_depth - 1
            )

    def handle_data(self, data):
        if self._ignored_depth == 0:
            self._parts.append(data)

    @property
    def visible_text(self):
        return re.sub(
            r"\s+", " ", " ".join(self._parts)
        ).strip()


def _read_official_html(
    doc: FedSourceDocument,
    role: str,
):
    if not isinstance(doc, FedSourceDocument) or doc.role != role:
        raise FedEvidenceExtractionError(
            f"Expected {role} source document."
        )

    if not isinstance(doc.raw_bytes, bytes) or not doc.raw_bytes:
        raise FedEvidenceExtractionError(
            f"Missing {role} raw source bytes."
        )

    expected_sha = hashlib.sha256(
        doc.raw_bytes
    ).hexdigest()

    if doc.content_sha256 != expected_sha:
        raise FedEvidenceExtractionError(
            f"Changed {role} source fingerprint."
        )

    try:
        decoded = doc.raw_bytes.decode(
            "utf-8-sig", errors="strict"
        )
    except UnicodeDecodeError as exc:
        raise FedEvidenceExtractionError(
            f"Invalid {role} UTF-8 bytes."
        ) from exc

    if doc.html != decoded:
        raise FedEvidenceExtractionError(
            f"{role} text differs from source bytes."
        )

    if (
        not isinstance(doc.fetched_at, datetime)
        or doc.fetched_at.tzinfo is None
    ):
        raise FedEvidenceExtractionError(
            f"{role} fetch time must be aware."
        )

    if doc.fetched_at.utcoffset() is None:
        raise FedEvidenceExtractionError(
            f"{role} fetch time has no UTC offset."
        )

    parser = _VisibleHTML()
    parser.feed(doc.html)
    parser.close()

    return parser.visible_text, parser.links


def _exact_match(
    pattern: re.Pattern,
    text: str,
    label: str,
):
    matches = list(pattern.finditer(text))

    if len(matches) != 1:
        raise FedEvidenceExtractionError(
            f"Expected exactly one {label}; "
            f"observed {len(matches)}."
        )

    return matches[0]


def _official_date(value: str, label: str) -> date:
    try:
        return datetime.strptime(
            value, "%B %d, %Y"
        ).date()
    except ValueError as exc:
        raise FedEvidenceExtractionError(
            f"Invalid {label} date."
        ) from exc


def _clock(match, label: str) -> tuple[int, int]:
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))

    if not (
        1 <= hour <= 12
        and 0 <= minute <= 59
    ):
        raise FedEvidenceExtractionError(
            f"Invalid {label} local clock."
        )

    ampm = match.group("ampm").lower()

    hour_24 = (
        hour % 12
        + (12 if ampm == "p.m." else 0)
    )

    return hour_24, minute


def extract_fed_original_rate_evidence(
    bundle: FedSourceBundle,
) -> FedOriginalRateEvidence:
    """Extract and validate one original FOMC policy decision.

    No live HTTP or database access.

    Retrieval timestamps are not historical public-release timestamps.
    Corrections require a separate causally versioned protocol.
    """

    if not isinstance(bundle, FedSourceBundle):
        raise FedEvidenceExtractionError(
            "Expected complete FedSourceBundle."
        )

    statement_text, _ = _read_official_html(
        bundle.statement, "statement"
    )

    implementation_text, _ = _read_official_html(
        bundle.implementation, "implementation"
    )

    meeting_text, meeting_links = _read_official_html(
        bundle.meeting, "meeting"
    )

    # Validate document identities before interpreting their contents.
    statement_id = build_fed_statement_id(
        bundle.statement.url
    )

    token = statement_id[8:16]

    try:
        statement_day = date(
            int(token[:4]),
            int(token[4:6]),
            int(token[6:8]),
        )
    except ValueError as exc:
        raise FedEvidenceExtractionError(
            "Statement URL has invalid date."
        ) from exc

    if bundle.implementation.url != (
        FED_RATE_SOURCE_PATH
        + statement_id
        + "1.htm"
    ):
        raise FedEvidenceExtractionError(
            "Implementation URL identity mismatch."
        )

    if bundle.meeting.url != (
        FED_RATE_MEETING_PATH
        + "fomcpresconf"
        + token
        + ".htm"
    ):
        raise FedEvidenceExtractionError(
            "Meeting URL identity mismatch."
        )

    # The meeting index must link to both documents being examined.
    links = {
        urljoin(bundle.meeting.url, href)
        for href in meeting_links
    }

    for required_url in (
        bundle.statement.url,
        bundle.implementation.url,
    ):
        if required_url not in links:
            raise FedEvidenceExtractionError(
                "Meeting page does not link to "
                "required official source documents."
            )

    # Independently extract statement release information.
    statement_header = _exact_match(
        _STATEMENT_HEADER,
        statement_text,
        "statement release header",
    )

    statement_date = _official_date(
        statement_header.group("release_date"),
        "statement release",
    )

    if statement_date != statement_day:
        raise FedEvidenceExtractionError(
            "Statement page date conflicts with URL."
        )

    header_hour, header_minute = _clock(
        statement_header, "statement"
    )

    header_stamp = FedReleaseStamp(
        release_date=statement_date,
        local_hour=header_hour,
        local_minute=header_minute,
        timezone_label=(
            statement_header.group("zone").upper()
        ),
    )

    # Find the Committee's adopted target range.
    rate_statement = _exact_match(
        _STATEMENT_RATE,
        statement_text,
        "adopted Committee target range",
    )

    # Validate the separate implementation-note heading.
    issue_match = _exact_match(
        _IMPL_TITLE,
        implementation_text,
        "implementation note heading",
    )

    issue_date = _official_date(
        issue_match.group("issue_date"),
        "implementation issuance",
    )

    if issue_date != statement_day:
        raise FedEvidenceExtractionError(
            "Implementation publication date "
            "differs from statement date."
        )

    # Extract effective date and policy target from the Desk directive.
    directive = _exact_match(
        _IMPL_DIRECTIVE,
        implementation_text,
        "FOMC domestic policy directive",
    )

    effective_date = _official_date(
        directive.group("effective_date"),
        "directive effective",
    )

    # Read statement and implementation releases independently.
    meeting_stamps = []

    for pattern, label in (
        (
            _MEETING_STATEMENT,
            "meeting statement release",
        ),
        (
            _MEETING_IMPLEMENTATION,
            "meeting implementation release",
        ),
    ):
        match = _exact_match(
            pattern, meeting_text, label
        )

        local_hour, local_minute = _clock(
            match, label
        )

        meeting_stamps.append(
            FedMeetingIndexStamp(
                release_date=_official_date(
                    match.group("release_date"),
                    label,
                ),
                local_hour=local_hour,
                local_minute=local_minute,
            )
        )

    # Assemble the already frozen F1 evidence contract.
    evidence = FedOriginalRateEvidence(
        statement_url=bundle.statement.url,
        implementation_url=bundle.implementation.url,
        meeting_url=bundle.meeting.url,
        statement_header_release=header_stamp,
        meeting_statement_release=meeting_stamps[0],
        meeting_implementation_release=meeting_stamps[1],
        statement_lower=rate_statement.group("lower"),
        statement_upper=rate_statement.group("upper"),
        implementation_lower=directive.group("lower"),
        implementation_upper=directive.group("upper"),
        effective_date=effective_date,
    )

    # Reuse frozen F1 checks: source agreement, rates, dates, and UTC.
    validate_fed_original_evidence(evidence)

    return evidence

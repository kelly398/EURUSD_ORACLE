
"""F1: fail-closed, original-release FOMC target-range protocol.

Pure, deterministic normalization. This module neither downloads source documents
nor writes a database. F2 must independently extract/attest source facts before
passing them here. An amended/corrected decision is deliberately NOT ingestible
through this original-release path without a separate versioned correction protocol.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import re
from zoneinfo import ZoneInfo

from backend.macro.macro_schemas import CentralBankRateSnapshot


FED_RATE_SOURCE = "FED_FOMC_RELEASE"
FED_CENTRAL_BANK = "FED"
FED_RATE_TIMEZONE = "America/New_York"
FED_RATE_POLICY_VERSION = "fomc_target_range_v1"

FED_RATE_NAMES = (
    "federal_funds_target_lower",
    "federal_funds_target_upper",
)

FED_RATE_BOUNDS = (Decimal("0"), Decimal("30"))
FED_RATE_TLS_VERIFICATION_REQUIRED = True
FED_RATE_UNVERIFIED_TLS_ALLOWED = False

FED_RATE_ALLOWED_HOST = "www.federalreserve.gov"
FED_RATE_SOURCE_PATH = "https://www.federalreserve.gov/newsevents/pressreleases/"
FED_RATE_MEETING_PATH = "https://www.federalreserve.gov/monetarypolicy/"

FED_RATE_RECORD_ID_RULE = "FOMC:{statement_id}:{lower|upper}"

FED_RATE_HASH_FIELDS = (
    "central_bank",
    "source",
    "policy_version",
    "statement_id",
    "rate_name",
    "reference_date",
    "target_lower_percent",
    "target_upper_percent",
)

FED_RATE_HASH_EXCLUDED_FIELDS = (
    "available_at",
    "ingested_at",
    "http_date",
    "http_last_modified",
    "http_etag",
)

_STATEMENT_RE = re.compile(r"monetary(20[0-9]{6})a")
_DECIMAL_RE = re.compile(r"(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,6})?")

_FRACTION_RE = re.compile(
    r"(?:(0|[1-9][0-9]{0,2})-)?([1-9][0-9]?)/([1-9][0-9]?)"
)

_ALLOWED_FRACTION_DENOMINATORS = frozenset((2, 4, 8, 16, 32, 64))
_RATE_PRECISION = Decimal("0.000001")

_UTC = timezone.utc
_ET = ZoneInfo(FED_RATE_TIMEZONE)


class FedRateProtocolError(ValueError):
    """A release fails contractual source/provenance/economic validation."""


def _require_date(value: date, name: str) -> None:
    if type(value) is not date:
        raise FedRateProtocolError(
            f"{name} must be a date, not datetime/text."
        )


def _require_aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise FedRateProtocolError(
            f"{name} must be a timezone-aware datetime."
        )

    try:
        if value.utcoffset() is None:
            raise ValueError("invalid offset")

        return value.astimezone(_UTC)

    except (ValueError, OverflowError) as exc:
        raise FedRateProtocolError(
            f"{name} cannot be normalized to UTC."
        ) from exc


def build_fed_release_at_utc(
    release_date: date,
    local_hour: int,
    local_minute: int,
    timezone_label: str,
) -> datetime:
    """Convert a specifically attested EST/EDT release; never assume 14:00.

    Reject non-existent New York wall times and time-zone-label mismatches.
    Ambiguous fall-back times may be accepted only when the supplied EST/EDT
    label selects exactly one UTC instant.
    """

    _require_date(release_date, "release_date")

    if (
        type(local_hour) is not int
        or not 0 <= local_hour <= 23
        or type(local_minute) is not int
        or not 0 <= local_minute <= 59
    ):
        raise FedRateProtocolError(
            "Release hour/minute must be valid integers."
        )

    if timezone_label not in ("EST", "EDT"):
        raise FedRateProtocolError(
            "An explicit EST or EDT release label is required."
        )

    wall = datetime.combine(
        release_date,
        time(local_hour, local_minute),
    )

    valid_instants = set()

    for fold in (0, 1):
        candidate = wall.replace(tzinfo=_ET, fold=fold)
        utc = candidate.astimezone(_UTC)
        back = utc.astimezone(_ET)

        if (
            back.replace(tzinfo=None) == wall
            and back.tzname() == timezone_label
        ):
            valid_instants.add(utc)

    if len(valid_instants) != 1:
        raise FedRateProtocolError(
            "Release local time/EST-EDT label is inconsistent."
        )

    return valid_instants.pop()


def conservative_fed_date_only_available_at(
    publication_date: date,
) -> datetime:
    """Earliest safe UTC boundary for a PROVEN date-only official correction.

    This is NOT permission to infer revisions from mutable source page metadata.
    """

    _require_date(publication_date, "publication_date")

    next_midnight = datetime.combine(
        publication_date + timedelta(days=1),
        time.min,
        tzinfo=_ET,
    )

    return next_midnight.astimezone(_UTC)


def parse_fed_rate_percent(token: str) -> Decimal:
    """Normalize a rate token, not unstructured FOMC prose.

    Supports exact decimals, ordinary fractions, and mixed fractions such as
    '3-3/4'; an explicitly audited non-breaking-hyphen variant is normalized.
    No guessing from text containing multiple percentages.
    """

    if not isinstance(token, str):
        raise FedRateProtocolError(
            "Rate must be a source text token."
        )

    cleaned = (
        token.strip()
        .replace("\u2010", "-")
        .replace("\u2011", "-")
    )

    if _DECIMAL_RE.fullmatch(cleaned):
        value = Decimal(cleaned)

    else:
        match = _FRACTION_RE.fullmatch(cleaned)

        if match is None:
            raise FedRateProtocolError(
                f"Unrecognized rate token: {token!r}"
            )

        whole, numerator, denominator = match.groups()
        n, d = int(numerator), int(denominator)

        if (
            d not in _ALLOWED_FRACTION_DENOMINATORS
            or n >= d
        ):
            raise FedRateProtocolError(
                "Unsupported fraction or improper rate fraction."
            )

        value = Decimal(whole or "0") + Decimal(n) / Decimal(d)

    if not FED_RATE_BOUNDS[0] <= value <= FED_RATE_BOUNDS[1]:
        raise FedRateProtocolError(
            "Rate outside frozen plausible policy bounds."
        )

    if value != value.quantize(_RATE_PRECISION):
        raise FedRateProtocolError(
            "Rate precision exceeds frozen protocol."
        )

    return value


def canonical_fed_percent(token: str) -> str:
    return format(
        parse_fed_rate_percent(token).quantize(_RATE_PRECISION),
        "f",
    )


def build_fed_statement_id(statement_url: str) -> str:
    if (
        not isinstance(statement_url, str)
        or not statement_url.startswith(FED_RATE_SOURCE_PATH)
    ):
        raise FedRateProtocolError(
            "Statement must use the official HTTPS source path."
        )

    leaf = statement_url[len(FED_RATE_SOURCE_PATH):]

    if not leaf.endswith(".htm"):
        raise FedRateProtocolError(
            "Unsupported statement file extension."
        )

    statement_id = leaf[:-4]

    if _STATEMENT_RE.fullmatch(statement_id) is None:
        raise FedRateProtocolError(
            "Unsupported or ambiguous FOMC statement identity."
        )

    return statement_id


def build_fed_record_id(
    statement_id: str,
    bound: str,
) -> str:
    if (
        not isinstance(statement_id, str)
        or _STATEMENT_RE.fullmatch(statement_id) is None
    ):
        raise FedRateProtocolError(
            "Invalid canonical FOMC statement ID."
        )

    if bound not in ("lower", "upper"):
        raise FedRateProtocolError(
            "Bound must be lower or upper."
        )

    return f"FOMC:{statement_id}:{bound}"


def _date_from_statement_id(statement_id: str) -> date:
    assert _STATEMENT_RE.fullmatch(statement_id) is not None

    try:
        return date.fromisoformat(
            f"{statement_id[8:12]}-"
            f"{statement_id[12:14]}-"
            f"{statement_id[14:16]}"
        )

    except ValueError as exc:
        raise FedRateProtocolError(
            "Invalid date in official statement identity."
        ) from exc


@dataclass(frozen=True)
class FedReleaseStamp:
    release_date: date
    local_hour: int
    local_minute: int
    timezone_label: str

    def as_utc(self) -> datetime:
        return build_fed_release_at_utc(
            self.release_date,
            self.local_hour,
            self.local_minute,
            self.timezone_label,
        )


@dataclass(frozen=True)
class FedMeetingIndexStamp:
    """A date and Eastern-local clock from the meeting index.

    Unlike FOMC statement headers, the meeting index generally does not label
    the clock explicitly EST/EDT. Compare it to the header's converted local
    clock instead of inventing a missing independent timezone attestation.
    """

    release_date: date
    local_hour: int
    local_minute: int

    def validate(self) -> None:
        _require_date(
            self.release_date,
            "meeting_index_release_date",
        )

        if (
            type(self.local_hour) is not int
            or not 0 <= self.local_hour <= 23
            or type(self.local_minute) is not int
            or not 0 <= self.local_minute <= 59
        ):
            raise FedRateProtocolError(
                "Invalid meeting index release clock."
            )


@dataclass(frozen=True)
class FedOriginalRateEvidence:
    """Verified structured facts for ONE original, paired FOMC release.

    F2 must attest every field from its exact named official document.
    Website publication date and latest HTTP timestamps are NOT attestations.
    This type does not support later corrections or a single-source fallback.
    """

    statement_url: str
    implementation_url: str
    meeting_url: str

    statement_header_release: FedReleaseStamp
    meeting_statement_release: FedMeetingIndexStamp
    meeting_implementation_release: FedMeetingIndexStamp

    statement_lower: str
    statement_upper: str
    implementation_lower: str
    implementation_upper: str

    effective_date: date


def validate_fed_original_evidence(
    evidence: FedOriginalRateEvidence,
) -> tuple[str, datetime, Decimal, Decimal]:
    if not isinstance(evidence, FedOriginalRateEvidence):
        raise FedRateProtocolError(
            "Expected FedOriginalRateEvidence."
        )

    statement_id = build_fed_statement_id(
        evidence.statement_url
    )

    stamp_date = _date_from_statement_id(statement_id)

    if evidence.implementation_url != (
        FED_RATE_SOURCE_PATH + statement_id + "1.htm"
    ):
        raise FedRateProtocolError(
            "Implementation note does not match statement."
        )

    if evidence.meeting_url != (
        FED_RATE_MEETING_PATH
        + "fomcpresconf"
        + stamp_date.strftime("%Y%m%d")
        + ".htm"
    ):
        raise FedRateProtocolError(
            "FOMC meeting page does not match statement."
        )

    _require_date(evidence.effective_date, "effective_date")

    if evidence.effective_date < stamp_date:
        raise FedRateProtocolError(
            "Original rate effective date predates its release."
        )

    if not isinstance(
        evidence.statement_header_release,
        FedReleaseStamp,
    ):
        raise FedRateProtocolError(
            "Original statement header needs timed evidence."
        )

    if evidence.statement_header_release.release_date != stamp_date:
        raise FedRateProtocolError(
            "Statement header date conflicts with URL."
        )

    available_at = evidence.statement_header_release.as_utc()
    header_local = available_at.astimezone(_ET)

    for index_stamp in (
        evidence.meeting_statement_release,
        evidence.meeting_implementation_release,
    ):
        if not isinstance(index_stamp, FedMeetingIndexStamp):
            raise FedRateProtocolError(
                "Each release needs independent meeting-index evidence."
            )

        index_stamp.validate()

        if index_stamp.release_date != stamp_date:
            raise FedRateProtocolError(
                "Release-date conflict among official pages."
            )

        if (
            index_stamp.local_hour != header_local.hour
            or index_stamp.local_minute != header_local.minute
        ):
            raise FedRateProtocolError(
                "Statement/implementation release times disagree."
            )

    statement_lower = parse_fed_rate_percent(
        evidence.statement_lower
    )
    statement_upper = parse_fed_rate_percent(
        evidence.statement_upper
    )

    implementation_lower = parse_fed_rate_percent(
        evidence.implementation_lower
    )
    implementation_upper = parse_fed_rate_percent(
        evidence.implementation_upper
    )

    if statement_lower > statement_upper:
        raise FedRateProtocolError(
            "Statement lower rate exceeds upper rate."
        )

    if implementation_lower > implementation_upper:
        raise FedRateProtocolError(
            "Implementation lower rate exceeds upper rate."
        )

    if (
        statement_lower,
        statement_upper,
    ) != (
        implementation_lower,
        implementation_upper,
    ):
        raise FedRateProtocolError(
            "Official statement and implementation rates disagree."
        )

    return (
        statement_id,
        available_at,
        statement_lower,
        statement_upper,
    )


def build_fed_content_hash(
    statement_id: str,
    rate_name: str,
    reference_date: date,
    target_lower: str,
    target_upper: str,
) -> str:
    """Hash paired normalized economic content; excludes ingestion time.

    A change to either bound changes BOTH record hashes, avoiding an apparently
    half-revised decision after version-preserving writes.
    """

    if (
        not isinstance(statement_id, str)
        or _STATEMENT_RE.fullmatch(statement_id) is None
    ):
        raise FedRateProtocolError(
            "Invalid statement ID for rate content hash."
        )

    if rate_name not in FED_RATE_NAMES:
        raise FedRateProtocolError(
            "Unknown Fed rate name."
        )

    _require_date(reference_date, "reference_date")

    low = parse_fed_rate_percent(target_lower)
    high = parse_fed_rate_percent(target_upper)

    if low > high:
        raise FedRateProtocolError(
            "Lower target exceeds upper target."
        )

    payload = {
        "central_bank": FED_CENTRAL_BANK,
        "source": FED_RATE_SOURCE,
        "policy_version": FED_RATE_POLICY_VERSION,
        "statement_id": statement_id,
        "rate_name": rate_name,
        "reference_date": reference_date.isoformat(),
        "target_lower_percent": format(
            low.quantize(_RATE_PRECISION),
            "f",
        ),
        "target_upper_percent": format(
            high.quantize(_RATE_PRECISION),
            "f",
        ),
    }

    if tuple(payload.keys()) != FED_RATE_HASH_FIELDS:
        raise FedRateProtocolError(
            "F1 hash payload must match frozen field contract."
        )

    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def build_fed_original_rate_snapshots(
    evidence: FedOriginalRateEvidence,
    ingested_at: datetime,
) -> tuple[CentralBankRateSnapshot, CentralBankRateSnapshot]:
    """Build an indivisible two-rate original decision, no network or DB access."""

    observed_at = _require_aware(
        ingested_at,
        "ingested_at",
    )

    decision_id, available_at, lower, upper = (
        validate_fed_original_evidence(evidence)
    )

    if observed_at < available_at:
        raise FedRateProtocolError(
            "An original decision cannot be ingested before release."
        )

    lower_text = format(
        lower.quantize(_RATE_PRECISION),
        "f",
    )

    upper_text = format(
        upper.quantize(_RATE_PRECISION),
        "f",
    )

    records = []

    for bound, rate_name, value in zip(
        ("lower", "upper"),
        FED_RATE_NAMES,
        (lower, upper),
        strict=True,
    ):
        records.append(
            CentralBankRateSnapshot(
                central_bank=FED_CENTRAL_BANK,
                rate_name=rate_name,
                reference_date=evidence.effective_date,
                available_at=available_at,
                value_percent=float(value),
                source_record_id=build_fed_record_id(
                    decision_id,
                    bound,
                ),
                content_hash=build_fed_content_hash(
                    decision_id,
                    rate_name,
                    evidence.effective_date,
                    lower_text,
                    upper_text,
                ),
                source=FED_RATE_SOURCE,
                ingested_at=observed_at,
            )
        )

    return records[0], records[1]

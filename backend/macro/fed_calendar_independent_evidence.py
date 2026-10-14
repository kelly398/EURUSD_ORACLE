
"""F4.5.9: Fail-closed catalog of claimed independent FOMC calendar evidence.

An external reference is a *claim*, not verified historical provenance.
Recomputes the entire F4.5.6-8 capture/extraction/audit/gap chain.

Does not dereference external URLs, verify signatures, validate Memento archives,
create calendar context, admit history, persist records or register Council seats.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from hashlib import sha256
from itertools import islice
import json
import re
from urllib.parse import urlsplit

from backend.macro.fed_calendar_client import FedCalendarAcquisitionRecord
from backend.macro.fed_calendar_evidence_audit import FedCalendarEvidenceAudit
from backend.macro.fed_calendar_extractor import FedCalendarScheduleExtraction
from backend.macro.fed_calendar_protocol import (
    FED_2026_SCHEDULE_RELEASE_LABEL_UTC,
    FED_2026_SCHEDULE_URL,
)
from backend.macro.fed_calendar_provenance_gaps import (
    FedCalendarProvenanceGapError,
    FedCalendarProvenanceGapReport,
    assess_fed_calendar_provenance_gaps,
)


MAX_EXTERNAL_EVIDENCE_CLAIMS = 32


class FedCalendarIndependentEvidenceError(ValueError):
    """An external claim or upstream chain cannot be cataloged safely."""


class FedCalendarIndependentEvidenceNotReady(RuntimeError):
    """External evidence has not passed independent verification/admission."""


class FedExternalEvidenceKind(str, Enum):
    WEB_ARCHIVE_MEMENTO = "WEB_ARCHIVE_MEMENTO"
    SIGNED_TIMESTAMP_TOKEN = "SIGNED_TIMESTAMP_TOKEN"
    PUBLISHER_ARCHIVE = "PUBLISHER_ARCHIVE"


class FedClaimedTimePrecision(str, Enum):
    INSTANT = "INSTANT"
    UTC_DATE_ONLY = "UTC_DATE_ONLY"


class FedClaimedTimeState(str, Enum):
    CLAIMED_NOT_AFTER_AS_OF = "CLAIMED_NOT_AFTER_AS_OF"
    CLAIMED_AFTER_AS_OF = "CLAIMED_AFTER_AS_OF"
    DATE_ONLY_INTRADAY_AMBIGUOUS = "DATE_ONLY_INTRADAY_AMBIGUOUS"
    RELEASE_DAY_TIME_AMBIGUOUS = "RELEASE_DAY_TIME_AMBIGUOUS"
    CLAIMED_BEFORE_SOURCE_RELEASE = "CLAIMED_BEFORE_SOURCE_RELEASE"


# Requirements are NOT proof-completion flags or activation criteria.
REQUIRED_EXTERNAL_VERIFICATION_STEPS = (
    "RETRIEVE_EXTERNAL_EVIDENCE_ARTIFACT_WITH_PINNED_ORIGIN",
    "VERIFY_EXTERNAL_ARTIFACT_BYTES_AND_DOCUMENT_BINDING",
    "VERIFY_PUBLISHER_IDENTITY_AND_ARCHIVE_OR_TSA_INDEPENDENCE",
    "VERIFY_HISTORICAL_SOURCE_CONTENT_AND_VERSION_CHRONOLOGY",
    "VERIFY_CAPTURE_TIME_WITH_INDEPENDENT_RESOLUTION",
    "ENFORCE_SEPARATE_REVIEWED_COUNCIL_REPLAY_STORAGE_ADMISSION",
)


_ALWAYS_BLOCKED = (
    "FED_CALENDAR_EXTERNAL_ARTIFACTS_NOT_RETRIEVED",
    "FED_CALENDAR_EXTERNAL_ORIGIN_AND_INDEPENDENCE_NOT_VERIFIED",
    "FED_CALENDAR_EXTERNAL_BYTES_AND_SIGNATURES_NOT_VERIFIED",
    "FED_CALENDAR_EXTERNAL_CAPTURE_TIMES_NOT_VERIFIED",
    "FED_CALENDAR_EXTERNAL_SOURCE_VERSIONS_NOT_ADMITTED",
)


def _utc(value: datetime, field: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise FedCalendarIndependentEvidenceError(
            f"{field} must be a timezone-aware datetime."
        )
    return value.astimezone(timezone.utc)


def _validate_reference(claim: "FedCalendarExternalEvidenceClaim") -> None:
    if type(claim) is not FedCalendarExternalEvidenceClaim:
        raise FedCalendarIndependentEvidenceError(
            "External evidence reference requires exact claim type."
        )

    for field in ("provider_id", "claim_id"):
        value = getattr(claim, field)
        if type(value) is not str or re.fullmatch(
            r"[A-Z0-9][A-Z0-9._-]{0,63}", value
        ) is None:
            raise FedCalendarIndependentEvidenceError(
                f"Invalid external evidence {field}."
            )

    if type(claim.kind) is not FedExternalEvidenceKind:
        raise FedCalendarIndependentEvidenceError(
            "Invalid external evidence kind."
        )

    if type(claim.claimed_time_precision) is not FedClaimedTimePrecision:
        raise FedCalendarIndependentEvidenceError(
            "Invalid external evidence time precision."
        )

    if claim.original_source_url != FED_2026_SCHEDULE_URL:
        raise FedCalendarIndependentEvidenceError(
            "External claim must identify the exact pinned Fed source."
        )

    address = claim.external_artifact_url
    if (
        type(address) is not str
        or len(address) > 2048
        or any(ch.isspace() or ord(ch) < 32 for ch in address)
    ):
        raise FedCalendarIndependentEvidenceError(
            "External evidence artifact URL is invalid."
        )
    try:
        parsed = urlsplit(address)
        valid_url = (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and parsed.port in (None, 443)
        )
    except ValueError:
        valid_url = False

    if not valid_url:
        raise FedCalendarIndependentEvidenceError(
            "External evidence artifact requires a valid HTTPS URL."
        )

    if (
        type(claim.claimed_document_body_sha256) is not str
        or re.fullmatch(
            r"[0-9a-f]{64}", claim.claimed_document_body_sha256
        ) is None
    ):
        raise FedCalendarIndependentEvidenceError(
            "External evidence claimed document SHA-256 must be lowercase hex."
        )

    if claim.claimed_time_precision is FedClaimedTimePrecision.INSTANT:
        _utc(claim.claimed_evidence_time, "claimed_evidence_time")
    elif type(claim.claimed_evidence_time) is not date:
        raise FedCalendarIndependentEvidenceError(
            "UTC_DATE_ONLY requires a date, not datetime."
        )

    if (
        claim.kind is FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN
        and claim.claimed_time_precision is not FedClaimedTimePrecision.INSTANT
    ):
        raise FedCalendarIndependentEvidenceError(
            "Signed timestamp token claims require instant precision."
        )


@dataclass(frozen=True)
class FedCalendarExternalEvidenceClaim:
    """Untrusted locator/metadata; it is never dereferenced here."""

    provider_id: str
    claim_id: str
    kind: FedExternalEvidenceKind
    external_artifact_url: str
    original_source_url: str
    claimed_document_body_sha256: str
    claimed_time_precision: FedClaimedTimePrecision
    claimed_evidence_time: datetime | date

    def __post_init__(self) -> None:
        _validate_reference(self)


@dataclass(frozen=True)
class FedCalendarExternalClaimAssessment:
    provider_id: str
    claim_id: str
    kind: FedExternalEvidenceKind
    claimed_time_state: FedClaimedTimeState
    claimed_same_bytes_as_current_capture: bool


@dataclass(frozen=True)
class FedCalendarExternalEvidenceReview:
    """Reproducible, non-admitting claim catalog with explicit proof steps."""

    as_of: datetime
    canonical_provenance_sha256: str
    source_url: str
    external_claim_count: int
    claimed_provider_count: int
    claimed_same_bytes_count: int
    claim_assessments: tuple[FedCalendarExternalClaimAssessment, ...]
    required_independent_checks: tuple[str, ...]
    blockers: tuple[str, ...]
    diagnostic_manifest_sha256: str

    @property
    def independent_historical_source_attested(self) -> bool:
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

    def require_council_calendar_delivery(self) -> None:
        raise FedCalendarIndependentEvidenceNotReady(
            "Fed calendar external references have not been independently "
            "authenticated or admitted for Council delivery."
        )

    def require_historical_replay(self) -> None:
        raise FedCalendarIndependentEvidenceNotReady(
            "Fed calendar historical source-version/time evidence and "
            "separate replay admission remain unverified."
        )


def _claim_time_state(
    claim: FedCalendarExternalEvidenceClaim,
    *,
    as_of: datetime,
) -> FedClaimedTimeState:
    release = FED_2026_SCHEDULE_RELEASE_LABEL_UTC

    if claim.claimed_time_precision is FedClaimedTimePrecision.INSTANT:
        instant = _utc(claim.claimed_evidence_time, "claimed_evidence_time")
        if instant < release:
            return FedClaimedTimeState.CLAIMED_BEFORE_SOURCE_RELEASE
        if instant > as_of:
            return FedClaimedTimeState.CLAIMED_AFTER_AS_OF
        return FedClaimedTimeState.CLAIMED_NOT_AFTER_AS_OF

    claimed_date = claim.claimed_evidence_time
    if claimed_date < release.date():
        return FedClaimedTimeState.CLAIMED_BEFORE_SOURCE_RELEASE
    if claimed_date == release.date():
        # Even a time on the release date could precede its 17:30 UTC label.
        start_of_day = datetime.combine(
            claimed_date, time.min, tzinfo=timezone.utc
        )
        if start_of_day > as_of:
            return FedClaimedTimeState.CLAIMED_AFTER_AS_OF
        return FedClaimedTimeState.RELEASE_DAY_TIME_AMBIGUOUS

    start = datetime.combine(claimed_date, time.min, tzinfo=timezone.utc)
    next_day = start + timedelta(days=1)
    if start > as_of:
        return FedClaimedTimeState.CLAIMED_AFTER_AS_OF
    if next_day > as_of:
        return FedClaimedTimeState.DATE_ONLY_INTRADAY_AMBIGUOUS
    return FedClaimedTimeState.CLAIMED_NOT_AFTER_AS_OF


def catalog_fed_calendar_independent_evidence(
    acquisition: FedCalendarAcquisitionRecord,
    extraction: FedCalendarScheduleExtraction,
    audit: FedCalendarEvidenceAudit,
    gaps: FedCalendarProvenanceGapReport,
    claims,
    *,
    as_of: datetime,
) -> FedCalendarExternalEvidenceReview:
    """Audit upstream chain again and inventory claimed external evidence.

    Claimed time status is a *logic check on the claim*, not proof of time.
    A claimed digest match is not a verified byte match. No claim authorizes.
    """
    _utc(as_of, "as_of")
    if type(gaps) is not FedCalendarProvenanceGapReport:
        raise TypeError("Expected FedCalendarProvenanceGapReport.")

    try:
        canonical = assess_fed_calendar_provenance_gaps(
            acquisition, extraction, audit, as_of=as_of
        )
    except (FedCalendarProvenanceGapError, TypeError) as exc:
        raise FedCalendarIndependentEvidenceError(
            "Fed calendar provenance cannot be revalidated."
        ) from exc

    if gaps != canonical:
        raise FedCalendarIndependentEvidenceError(
            "Fed calendar provenance report differs from canonical chain."
        )

    if claims is None:
        raise TypeError("External evidence claim collection cannot be None.")
    try:
        selected = tuple(islice(claims, MAX_EXTERNAL_EVIDENCE_CLAIMS + 1))
    except (TypeError, ValueError) as exc:
        raise TypeError("External evidence claims must be iterable.") from exc

    if len(selected) > MAX_EXTERNAL_EVIDENCE_CLAIMS:
        raise FedCalendarIndependentEvidenceError(
            "Fed calendar external evidence claim limit exceeded."
        )

    seen = set()
    assessments = []
    same_bytes_count = 0
    for claim in selected:
        _validate_reference(claim)  # Includes model mutation bypass check.
        key = (claim.provider_id, claim.claim_id)
        if key in seen:
            raise FedCalendarIndependentEvidenceError(
                "Duplicate external evidence reference identity."
            )
        seen.add(key)

        byte_claim_matches = (
            claim.claimed_document_body_sha256 == canonical.entity_body_sha256
        )
        same_bytes_count += int(byte_claim_matches)
        assessments.append(
            FedCalendarExternalClaimAssessment(
                provider_id=claim.provider_id,
                claim_id=claim.claim_id,
                kind=claim.kind,
                claimed_time_state=_claim_time_state(claim, as_of=canonical.as_of),
                claimed_same_bytes_as_current_capture=byte_claim_matches,
            )
        )

    # These are claims that have NOT been retrieved or verified. A collection
    # of multiple provider names does not establish witness independence.
    blockers = list(dict.fromkeys((*canonical.blockers, *_ALWAYS_BLOCKED)))
    if not selected:
        blockers.append("FED_CALENDAR_EXTERNAL_EVIDENCE_REFERENCES_ABSENT")
    if any(
        a.claimed_time_state in (
            FedClaimedTimeState.CLAIMED_AFTER_AS_OF,
            FedClaimedTimeState.CLAIMED_BEFORE_SOURCE_RELEASE,
        )
        for a in assessments
    ):
        blockers.append("FED_CALENDAR_EXTERNAL_TIME_CLAIM_CONFLICT")
    if any(
        a.claimed_time_state in (
            FedClaimedTimeState.DATE_ONLY_INTRADAY_AMBIGUOUS,
            FedClaimedTimeState.RELEASE_DAY_TIME_AMBIGUOUS,
        )
        for a in assessments
    ):
        blockers.append("FED_CALENDAR_EXTERNAL_DATE_PRECISION_AMBIGUOUS")

    manifest = {
        "contract": "fed_calendar_independent_evidence_claim_catalog_v1",
        "as_of_utc": canonical.as_of.isoformat(timespec="microseconds"),
        "upstream_provenance_sha256": canonical.diagnostic_manifest_sha256,
        "source_url": canonical.source_url,
        "required_independent_checks": REQUIRED_EXTERNAL_VERIFICATION_STEPS,
        "references": [
            {
                "provider_id": c.provider_id,
                "claim_id": c.claim_id,
                "kind": c.kind.value,
                "external_artifact_url": c.external_artifact_url,
                "original_source_url": c.original_source_url,
                "claimed_document_sha256": c.claimed_document_body_sha256,
                "claimed_time_precision": c.claimed_time_precision.value,
                "claimed_evidence_time": (
                    _utc(c.claimed_evidence_time, "claimed_evidence_time")
                    .isoformat(timespec="microseconds")
                    if c.claimed_time_precision is FedClaimedTimePrecision.INSTANT
                    else c.claimed_evidence_time.isoformat()
                ),
                "time_assessment": a.claimed_time_state.value,
            }
            for c, a in zip(selected, assessments, strict=True)
        ],
        "blockers": blockers,
    }
    digest = sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")
    ).hexdigest()

    return FedCalendarExternalEvidenceReview(
        as_of=canonical.as_of,
        canonical_provenance_sha256=canonical.diagnostic_manifest_sha256,
        source_url=canonical.source_url,
        external_claim_count=len(selected),
        claimed_provider_count=len({c.provider_id for c in selected}),
        claimed_same_bytes_count=same_bytes_count,
        claim_assessments=tuple(assessments),
        required_independent_checks=REQUIRED_EXTERNAL_VERIFICATION_STEPS,
        blockers=tuple(blockers),
        diagnostic_manifest_sha256=digest,
    )

"""F4.5.10: Offline, fail-closed intake of supplied FOMC evidence artifacts.

Revalidates the F4.5.6--F4.5.9 diagnostic chain and checks locally supplied
artifact bytes and acquisition metadata. A caller-supplied HTTP record, an
RFC 7089 Memento-Datetime header, or opaque RFC 3161 token bytes do NOT prove
publisher origin, archival custody, historical availability, or signature.

No HTTP/IO, archive lookup, signature verification, persistence, Council
registration, or historical replay. The only result is a diagnostic receipt.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from enum import Enum
from hashlib import sha256
from itertools import islice
import json
import re

from backend.macro.fed_calendar_client import FedCalendarAcquisitionRecord
from backend.macro.fed_calendar_evidence_audit import FedCalendarEvidenceAudit
from backend.macro.fed_calendar_extractor import FedCalendarScheduleExtraction
from backend.macro.fed_calendar_independent_evidence import (
    FedCalendarExternalEvidenceClaim,
    FedCalendarExternalEvidenceReview,
    FedCalendarIndependentEvidenceError,
    FedClaimedTimePrecision,
    FedExternalEvidenceKind,
    MAX_EXTERNAL_EVIDENCE_CLAIMS,
    catalog_fed_calendar_independent_evidence,
)
from backend.macro.fed_calendar_protocol import FED_2026_SCHEDULE_URL
from backend.macro.fed_calendar_provenance_gaps import FedCalendarProvenanceGapReport

MAX_EXTERNAL_ARTIFACT_BYTES = 1024 * 1024


class FedCalendarArtifactIntakeError(ValueError):
    """A local artifact/claim chain is malformed or internally inconsistent."""


class FedCalendarArtifactNotReady(RuntimeError):
    """Local byte inspection does not authorize any Fed calendar use."""


class FedLocalArtifactBinding(str, Enum):
    MATCHES_CLAIMED_DOCUMENT_BYTES = "MATCHES_CLAIMED_DOCUMENT_BYTES"
    DIFFERS_FROM_CLAIMED_DOCUMENT_BYTES = "DIFFERS_FROM_CLAIMED_DOCUMENT_BYTES"
    OPAQUE_TOKEN_DOCUMENT_BINDING_UNVERIFIED = "OPAQUE_TOKEN_DOCUMENT_BINDING_UNVERIFIED"


REQUIRED_ARTIFACT_PROOFS = (
    "INDEPENDENTLY_VERIFY_ARTIFACT_FETCH_ORIGIN_AND_CHAIN_OF_CUSTODY",
    "INDEPENDENTLY_VERIFY_ARCHIVE_OR_PUBLISHER_ORIGINAL_RESOURCE_LINK",
    "INDEPENDENTLY_VERIFY_ARCHIVED_DOCUMENT_CONTENT_AND_VERSION_TIME",
    "CRYPTOGRAPHICALLY_VERIFY_RFC3161_TSA_SIGNATURE_CHAIN_AND_MESSAGE_IMPRINT",
    "INDEPENDENTLY_PROVE_SOURCE_AVAILABILITY_AT_REPLAY_AS_OF",
    "SEPARATELY_APPROVE_COUNCIL_REPLAY_AND_STORAGE_ADMISSION",
)

_ALWAYS_DENIED = (
    "FED_EXTERNAL_ARTIFACT_ORIGIN_UNVERIFIED",
    "FED_EXTERNAL_ARCHIVE_CUSTODY_UNVERIFIED",
    "FED_EXTERNAL_TIMESTAMP_SIGNATURE_AND_IMPRINT_UNVERIFIED",
    "FED_EXTERNAL_HISTORICAL_AVAILABILITY_UNVERIFIED",
    "FED_EXTERNAL_COUNCIL_REPLAY_STORAGE_NOT_AUTHORIZED",
)


def _utc(value: datetime, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise FedCalendarArtifactIntakeError(f"{name} must be timezone aware.")
    return value.astimezone(timezone.utc)


def _short_header(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if (type(value) is not str or not value or len(value) > 2048
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise FedCalendarArtifactIntakeError(f"Invalid {name} metadata.")
    return value


def _memento_instant(header: str) -> datetime:
    """Parse one *reported* RFC 1123 GMT header; never trust its origin."""
    _short_header(header, "Memento-Datetime")
    try:
        parsed = datetime.strptime(header, "%a, %d %b %Y %H:%M:%S GMT")
        result = parsed.replace(tzinfo=timezone.utc)
        if format_datetime(result, usegmt=True) != header:
            raise ValueError("Noncanonical RFC 1123 date or wrong weekday")
    except (TypeError, ValueError) as exc:
        raise FedCalendarArtifactIntakeError(
            "Reported Memento-Datetime must be canonical RFC 1123 GMT."
        ) from exc
    return result


@dataclass(frozen=True)
class FedCalendarExternalArtifactCapture:
    """Untrusted caller-supplied HTTP response envelope and retained raw bytes."""

    provider_id: str
    claim_id: str
    requested_url: str
    response_url: str
    http_status: int
    request_started_at: datetime
    response_completed_at: datetime
    content_type: str
    entity_body: bytes
    entity_body_sha256: str
    reported_memento_datetime: str | None = None
    reported_original_resource_url: str | None = None

    def __post_init__(self) -> None:
        _validate_capture(self)


def _validate_capture(capture: FedCalendarExternalArtifactCapture) -> None:
    if type(capture) is not FedCalendarExternalArtifactCapture:
        raise FedCalendarArtifactIntakeError("Exact artifact capture type required.")
    for name in ("provider_id", "claim_id"):
        value = getattr(capture, name)
        if (type(value) is not str or re.fullmatch(
                r"[A-Z0-9][A-Z0-9._-]{0,63}", value) is None):
            raise FedCalendarArtifactIntakeError(f"Invalid artifact {name}.")
    for name in ("requested_url", "response_url"):
        value = getattr(capture, name)
        if (type(value) is not str or not value or len(value) > 2048
                or any(ch.isspace() or ord(ch) < 32 for ch in value)):
            raise FedCalendarArtifactIntakeError(f"Invalid artifact {name}.")
    if type(capture.http_status) is not int or capture.http_status != 200:
        raise FedCalendarArtifactIntakeError("Artifact response must claim HTTP 200.")
    start = _utc(capture.request_started_at, "request_started_at")
    finish = _utc(capture.response_completed_at, "response_completed_at")
    if finish < start:
        raise FedCalendarArtifactIntakeError("Artifact response completed before start.")
    if _short_header(capture.content_type, "Content-Type") is None:
        raise FedCalendarArtifactIntakeError("Artifact Content-Type is required.")
    _short_header(capture.reported_memento_datetime, "Memento-Datetime")
    _short_header(capture.reported_original_resource_url, "original resource URI")
    if (type(capture.entity_body) is not bytes or not capture.entity_body
            or len(capture.entity_body) > MAX_EXTERNAL_ARTIFACT_BYTES):
        raise FedCalendarArtifactIntakeError("Artifact body must be bounded bytes.")
    if (type(capture.entity_body_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", capture.entity_body_sha256) is None
            or sha256(capture.entity_body).hexdigest() != capture.entity_body_sha256):
        raise FedCalendarArtifactIntakeError("Artifact body SHA-256 mismatch.")


@dataclass(frozen=True)
class FedCalendarArtifactIntakeFinding:
    provider_id: str
    claim_id: str
    kind: FedExternalEvidenceKind
    artifact_entity_sha256: str
    artifact_byte_count: int
    local_document_binding: FedLocalArtifactBinding
    local_artifact_matches_current_fed_capture: bool | None
    claimed_memento_state_at: datetime | None
    claimed_capture_completed_at: datetime
    claimed_capture_not_after_as_of: bool


@dataclass(frozen=True)
class FedCalendarArtifactIntakeReport:
    """No raw artifacts, trusted archive times, signatures or live snapshots."""

    as_of: datetime
    source_url: str
    canonical_external_review_sha256: str
    expected_claim_count: int
    received_artifact_count: int
    findings: tuple[FedCalendarArtifactIntakeFinding, ...]
    required_independent_proofs: tuple[str, ...]
    blockers: tuple[str, ...]
    diagnostic_manifest_sha256: str

    @property
    def independent_source_version_verified(self) -> bool:
        return False

    @property
    def timestamp_signature_verified(self) -> bool:
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
        raise FedCalendarArtifactNotReady(
            "Fed calendar artifact bytes do not establish Council admission."
        )

    def require_historical_replay(self) -> None:
        raise FedCalendarArtifactNotReady(
            "Fed calendar historical artifact version/time not independently proven."
        )

    def require_persistence(self) -> None:
        raise FedCalendarArtifactNotReady(
            "Fed calendar artifact intake does not authorize persistence."
        )


def inspect_fed_calendar_external_artifacts(
    acquisition: FedCalendarAcquisitionRecord,
    extraction: FedCalendarScheduleExtraction,
    audit: FedCalendarEvidenceAudit,
    gaps: FedCalendarProvenanceGapReport,
    claims,
    review: FedCalendarExternalEvidenceReview,
    captures,
    *,
    as_of: datetime,
) -> FedCalendarArtifactIntakeReport:
    """Check supplied bytes against revalidated claims; never certify provenance.

    An actual RFC 7089 response may have link and datetime metadata. Here
    those headers are caller-supplied fields, NOT a validated HTTP transcript.
    For RFC 3161 tokens only the opaque byte hash is computed; verifying the
    signature, imprint, TSA certificate and trusted time is out of scope.
    """
    checked_as_of = _utc(as_of, "as_of")
    if type(review) is not FedCalendarExternalEvidenceReview:
        raise TypeError("Expected FedCalendarExternalEvidenceReview.")
    if claims is None or captures is None:
        raise TypeError("Claims and captures must be iterable, not None.")
    try:
        refs = tuple(islice(claims, MAX_EXTERNAL_EVIDENCE_CLAIMS + 1))
        supplied = tuple(islice(captures, MAX_EXTERNAL_EVIDENCE_CLAIMS + 1))
    except (TypeError, ValueError) as exc:
        raise TypeError("Claims and captures must be iterable.") from exc
    if (len(refs) > MAX_EXTERNAL_EVIDENCE_CLAIMS
            or len(supplied) > MAX_EXTERNAL_EVIDENCE_CLAIMS):
        raise FedCalendarArtifactIntakeError("External claim/artifact limit exceeded.")

    try:
        canonical = catalog_fed_calendar_independent_evidence(
            acquisition, extraction, audit, gaps, refs, as_of=checked_as_of
        )
    except (FedCalendarIndependentEvidenceError, TypeError) as exc:
        raise FedCalendarArtifactIntakeError(
            "External claim/provenance chain cannot be revalidated."
        ) from exc
    if review != canonical:
        raise FedCalendarArtifactIntakeError(
            "External evidence review differs from canonical claim chain."
        )

    by_key: dict[tuple[str, str], FedCalendarExternalArtifactCapture] = {}
    for capture in supplied:
        _validate_capture(capture)  # Detect dataclass mutation bypass.
        key = (capture.provider_id, capture.claim_id)
        if key in by_key:
            raise FedCalendarArtifactIntakeError("Duplicate artifact capture identity.")
        by_key[key] = capture

    ref_keys = {(c.provider_id, c.claim_id) for c in refs}
    if not by_key.keys() <= ref_keys:
        raise FedCalendarArtifactIntakeError("Artifact has no matching evidence claim.")

    findings = []
    for claim in refs:
        capture = by_key.get((claim.provider_id, claim.claim_id))
        if capture is None:
            continue
        if (capture.requested_url != claim.external_artifact_url
                or capture.response_url != claim.external_artifact_url):
            raise FedCalendarArtifactIntakeError(
                "Artifact locator differs from exact claimed URL or redirected."
            )

        mime = capture.content_type.split(";", 1)[0].strip().lower()
        memento_at = None
        if claim.kind is FedExternalEvidenceKind.WEB_ARCHIVE_MEMENTO:
            if mime != "text/html":
                raise FedCalendarArtifactIntakeError("Memento body must be HTML.")
            if capture.reported_original_resource_url != FED_2026_SCHEDULE_URL:
                raise FedCalendarArtifactIntakeError(
                    "Memento original URI must identify pinned Fed source."
                )
            if capture.reported_memento_datetime is None:
                raise FedCalendarArtifactIntakeError(
                    "Memento requires reported Memento-Datetime."
                )
            memento_at = _memento_instant(capture.reported_memento_datetime)
            if claim.claimed_time_precision is FedClaimedTimePrecision.INSTANT:
                matches = memento_at == _utc(
                    claim.claimed_evidence_time, "claimed_evidence_time"
                )
            else:
                matches = memento_at.date() == claim.claimed_evidence_time
            if not matches:
                raise FedCalendarArtifactIntakeError(
                    "Memento-Datetime conflicts with external evidence claim."
                )
        else:
            if (capture.reported_memento_datetime is not None
                    or capture.reported_original_resource_url is not None):
                raise FedCalendarArtifactIntakeError(
                    "Non-Memento artifact cannot supply Memento metadata."
                )
            if claim.kind is FedExternalEvidenceKind.PUBLISHER_ARCHIVE:
                if mime != "text/html":
                    raise FedCalendarArtifactIntakeError("Publisher artifact must be HTML.")
            elif claim.kind is FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN:
                if mime not in (
                    "application/timestamp-reply",
                    "application/timestamp-token",
                    "application/octet-stream",
                ):
                    raise FedCalendarArtifactIntakeError(
                        "Timestamp artifact must have binary content type."
                    )
            else:
                raise FedCalendarArtifactIntakeError("Unknown external evidence kind.")

        if claim.kind is FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN:
            binding = FedLocalArtifactBinding.OPAQUE_TOKEN_DOCUMENT_BINDING_UNVERIFIED
            same_as_current = None
        else:
            matches_claim = (
                capture.entity_body_sha256 == claim.claimed_document_body_sha256
            )
            binding = (
                FedLocalArtifactBinding.MATCHES_CLAIMED_DOCUMENT_BYTES
                if matches_claim
                else FedLocalArtifactBinding.DIFFERS_FROM_CLAIMED_DOCUMENT_BYTES
            )
            same_as_current = (
                capture.entity_body_sha256 == acquisition.entity_body_sha256
            )

        findings.append(FedCalendarArtifactIntakeFinding(
            provider_id=claim.provider_id,
            claim_id=claim.claim_id,
            kind=claim.kind,
            artifact_entity_sha256=capture.entity_body_sha256,
            artifact_byte_count=len(capture.entity_body),
            local_document_binding=binding,
            local_artifact_matches_current_fed_capture=same_as_current,
            claimed_memento_state_at=memento_at,
            claimed_capture_completed_at=_utc(
                capture.response_completed_at, "response_completed_at"
            ),
            claimed_capture_not_after_as_of=(
                _utc(capture.response_completed_at, "response_completed_at")
                <= checked_as_of
            ),
        ))

    blockers = list(_ALWAYS_DENIED)
    if not findings:
        blockers.append("FED_EXTERNAL_ARTIFACT_BYTES_NOT_SUPPLIED")
    if len(findings) < len(refs):
        blockers.append("FED_EXTERNAL_ARTIFACT_CLAIMS_NOT_ALL_CAPTURED")
    if any(not f.claimed_capture_not_after_as_of for f in findings):
        blockers.append("FED_EXTERNAL_ARTIFACT_CAPTURE_POSTDATES_AS_OF")
    if any(f.local_document_binding is FedLocalArtifactBinding.DIFFERS_FROM_CLAIMED_DOCUMENT_BYTES
           for f in findings):
        blockers.append("FED_EXTERNAL_DOCUMENT_BYTES_DIFFER_FROM_CLAIM")
    if any(c.claimed_time_precision is FedClaimedTimePrecision.UTC_DATE_ONLY
           and (c.provider_id, c.claim_id) in by_key for c in refs):
        blockers.append("FED_EXTERNAL_MEMENTO_CLAIM_TIME_PRECISION_AMBIGUOUS")

    manifest = {
        "contract": "fed_calendar_external_artifact_intake_v1",
        "as_of": checked_as_of.isoformat(timespec="microseconds"),
        "upstream_review_sha256": canonical.diagnostic_manifest_sha256,
        "source_url": canonical.source_url,
        "expected_claim_count": len(refs),
        "required_proofs": REQUIRED_ARTIFACT_PROOFS,
        "captures": [
            {
                "provider": f.provider_id, "claim": f.claim_id,
                "kind": f.kind.value, "artifact_sha256": f.artifact_entity_sha256,
                "artifact_size": f.artifact_byte_count,
                "binding": f.local_document_binding.value,
                "same_as_current_fed": f.local_artifact_matches_current_fed_capture,
                "memento_claimed_time": (
                    f.claimed_memento_state_at.isoformat(timespec="microseconds")
                    if f.claimed_memento_state_at else None
                ),
                "captured_at": f.claimed_capture_completed_at.isoformat(
                    timespec="microseconds"
                ),
            } for f in findings
        ],
        "blockers": blockers,
    }
    digest = sha256(json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")).hexdigest()

    return FedCalendarArtifactIntakeReport(
        as_of=checked_as_of,
        source_url=canonical.source_url,
        canonical_external_review_sha256=canonical.diagnostic_manifest_sha256,
        expected_claim_count=len(refs),
        received_artifact_count=len(findings),
        findings=tuple(findings),
        required_independent_proofs=REQUIRED_ARTIFACT_PROOFS,
        blockers=tuple(blockers),
        diagnostic_manifest_sha256=digest,
    )

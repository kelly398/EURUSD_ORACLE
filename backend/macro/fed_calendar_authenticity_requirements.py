"""F4.5.11: Offline Fed calendar artifact authenticity verification requirements.

Reconstructs the entire F4.5.6-10 diagnostic chain from retained raw bytes.
Produces per-artifact *verification work items*, NOT certificates or attestations.

SHA-256 of captured bytes is a local integrity check. A Memento-Datetime,
original-resource URI, archive URL, or opaque RFC 3161 TimeStampToken is NOT
independent proof. In particular no CMS/PKIX cryptographic verification is
attempted here. No network, IO, database, Council, replay, or broker actions.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from itertools import islice
import json

from backend.macro.fed_calendar_artifact_intake import (
    FedCalendarArtifactIntakeError,
    FedCalendarArtifactIntakeReport,
    FedCalendarExternalArtifactCapture,
    FedLocalArtifactBinding,
    inspect_fed_calendar_external_artifacts,
)
from backend.macro.fed_calendar_client import FedCalendarAcquisitionRecord
from backend.macro.fed_calendar_evidence_audit import FedCalendarEvidenceAudit
from backend.macro.fed_calendar_extractor import FedCalendarScheduleExtraction
from backend.macro.fed_calendar_independent_evidence import (
    FedCalendarExternalEvidenceClaim,
    FedCalendarExternalEvidenceReview,
    FedClaimedTimeState,
    FedExternalEvidenceKind,
    MAX_EXTERNAL_EVIDENCE_CLAIMS,
)
from backend.macro.fed_calendar_provenance_gaps import FedCalendarProvenanceGapReport


class FedCalendarAuthenticityRequirementsError(ValueError):
    """The evidence chain cannot support a reproducible verification plan."""


class FedCalendarAuthenticityNotReady(RuntimeError):
    """The verification plan never grants source authenticity or admission."""


class FedVerificationCheckState(str, Enum):
    LOCAL_BYTES_RECONCILED = "LOCAL_BYTES_RECONCILED"
    CLAIM_ONLY = "CLAIM_ONLY"
    INDEPENDENT_VERIFICATION_REQUIRED = "INDEPENDENT_VERIFICATION_REQUIRED"
    ARTIFACT_MISSING = "ARTIFACT_MISSING"
    LOCAL_CONFLICT = "LOCAL_CONFLICT"


# Every work item names something a future, separately reviewed verifier must
# actually establish. These strings are not user-configurable trust assertions.
COMMON_CHECKS = (
    "CAPTURED_ARTIFACT_BYTE_INTEGRITY",
    "ARTIFACT_ORIGIN_AND_CUSTODY",
    "CLAIMED_TIME_AND_REPLAY_AS_OF",
    "SOURCE_VERSION_CONTENT_AND_CHRONOLOGY",
    "SEPARATE_COUNCIL_REPLAY_STORAGE_POLICY",
)
MEMENTO_CHECKS = (
    "RFC7089_AUTHENTIC_RESPONSE_AND_ORIGINAL_LINK",
    "RFC7089_INDEPENDENT_ARCHIVE_CAPTURE_TIME",
    "ARCHIVED_FOMC_SCHEDULE_CONTENT_REVIEW",
)
PUBLISHER_CHECKS = (
    "FED_PUBLISHER_ORIGIN_AND_AUTHORIZED_ARCHIVE",
    "PUBLISHER_VERSION_HISTORY_AND_PUBLICATION_TIME",
    "ARCHIVED_FOMC_SCHEDULE_CONTENT_REVIEW",
)
TIMESTAMP_CHECKS = (
    "RFC3161_CMS_SIGNATURE_VALIDATION",
    "RFC3161_TSA_CERTIFICATE_CHAIN_EKU_REVOCATION",
    "RFC3161_TSTINFO_MESSAGE_IMPRINT_ALGORITHM_AND_DIGEST",
    "RFC3161_GEN_TIME_POLICY_AND_ACCURACY",
    "RFC3161_NONCE_OR_INDEPENDENT_TRUSTED_TIME",
    "RFC3161_DOCUMENT_BYTES_FOR_IMPRINT",
)

ALWAYS_UNVERIFIED = (
    "FED_AUTHENTICITY_ORIGIN_AND_CUSTODY_UNVERIFIED",
    "FED_AUTHENTICITY_ARCHIVE_HISTORICAL_VERSION_UNVERIFIED",
    "FED_AUTHENTICITY_TIMESTAMP_CRYPTOGRAPHY_UNVERIFIED",
    "FED_AUTHENTICITY_INDEPENDENT_TIME_UNVERIFIED",
    "FED_AUTHENTICITY_COUNCIL_REPLAY_STORAGE_NOT_AUTHORIZED",
)


@dataclass(frozen=True)
class FedArtifactVerificationCheck:
    check_code: str
    state: FedVerificationCheckState


@dataclass(frozen=True)
class FedArtifactVerificationTarget:
    provider_id: str
    claim_id: str
    kind: FedExternalEvidenceKind
    claimed_document_sha256: str
    artifact_entity_sha256: str | None
    claimed_time_state: FedClaimedTimeState
    checks: tuple[FedArtifactVerificationCheck, ...]


@dataclass(frozen=True)
class FedCalendarAuthenticityRequirementsReport:
    """Diagnostic-only requirements; never emits source bytes or credentials."""

    as_of: datetime
    source_url: str
    canonical_intake_sha256: str
    expected_claim_count: int
    supplied_artifact_count: int
    verification_targets: tuple[FedArtifactVerificationTarget, ...]
    blockers: tuple[str, ...]
    diagnostic_manifest_sha256: str

    @property
    def publisher_origin_authenticated(self) -> bool:
        return False

    @property
    def archival_custody_authenticated(self) -> bool:
        return False

    @property
    def rfc3161_signature_verified(self) -> bool:
        return False

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

    def require_council_calendar_delivery(self) -> None:
        raise FedCalendarAuthenticityNotReady(
            "External FOMC artifact authenticity and Council admission unverified."
        )

    def require_historical_replay(self) -> None:
        raise FedCalendarAuthenticityNotReady(
            "External FOMC historical source version and time are unverified."
        )

    def require_persistence(self) -> None:
        raise FedCalendarAuthenticityNotReady(
            "Artifact authenticity requirements do not authorize Fed storage."
        )


def _as_utc(value: datetime) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise FedCalendarAuthenticityRequirementsError(
            "as_of must be a timezone-aware datetime."
        )
    return value.astimezone(timezone.utc)


def build_fed_calendar_authenticity_requirements(
    acquisition: FedCalendarAcquisitionRecord,
    extraction: FedCalendarScheduleExtraction,
    audit: FedCalendarEvidenceAudit,
    gaps: FedCalendarProvenanceGapReport,
    claims: tuple[FedCalendarExternalEvidenceClaim, ...],
    review: FedCalendarExternalEvidenceReview,
    captures: tuple[FedCalendarExternalArtifactCapture, ...],
    intake: FedCalendarArtifactIntakeReport,
    *,
    as_of: datetime,
) -> FedCalendarAuthenticityRequirementsReport:
    """Recheck every retained artifact; enumerate independent verification gaps.

    Even a complete local match cannot prove who archived the source, when
    the state first existed, or whether a timestamp token signs its digest.
    A token's claimed document digest is NOT the digest of the token itself.
    """
    checked_as_of = _as_utc(as_of)
    if type(intake) is not FedCalendarArtifactIntakeReport:
        raise TypeError("Expected FedCalendarArtifactIntakeReport.")
    if claims is None or captures is None:
        raise TypeError("Claims and artifact captures cannot be None.")
    try:
        refs = tuple(islice(claims, MAX_EXTERNAL_EVIDENCE_CLAIMS + 1))
        supplied = tuple(islice(captures, MAX_EXTERNAL_EVIDENCE_CLAIMS + 1))
    except (TypeError, ValueError) as exc:
        raise TypeError("Claims and captures must be iterable.") from exc
    if (len(refs) > MAX_EXTERNAL_EVIDENCE_CLAIMS
            or len(supplied) > MAX_EXTERNAL_EVIDENCE_CLAIMS):
        raise FedCalendarAuthenticityRequirementsError(
            "External claim/artifact limit exceeded."
        )

    # The earlier intake limits/revalidates all iterables, identities, raw
    # bytes, headers, capture clocks and its entire provenance chain.
    # Re-running it prevents a forged upstream receipt from granting status.
    try:
        canonical = inspect_fed_calendar_external_artifacts(
            acquisition, extraction, audit, gaps, refs, review,
            supplied, as_of=checked_as_of,
        )
    except (FedCalendarArtifactIntakeError, TypeError) as exc:
        raise FedCalendarAuthenticityRequirementsError(
            "Fed calendar artifact intake chain cannot be revalidated."
        ) from exc
    if intake != canonical:
        raise FedCalendarAuthenticityRequirementsError(
            "Artifact intake report differs from canonical retained evidence."
        )

    captures_by_id = {(c.provider_id, c.claim_id): c for c in supplied}
    findings_by_id = {(f.provider_id, f.claim_id): f for f in canonical.findings}
    assessed_by_id = {
        (f.provider_id, f.claim_id): f for f in review.claim_assessments
    }

    # Locally available HTML body bytes can serve as *candidate* documents
    # for RFC 3161 message-imprint verification. This does not verify imprint.
    local_document_hashes = {
        f.artifact_entity_sha256
        for f in canonical.findings
        if (
            f.kind is not FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN
            and f.local_document_binding
            is FedLocalArtifactBinding.MATCHES_CLAIMED_DOCUMENT_BYTES
        )
    }

    targets = []
    blockers = list(dict.fromkeys((*canonical.blockers, *ALWAYS_UNVERIFIED)))

    for ref in refs:
        key = (ref.provider_id, ref.claim_id)
        capture = captures_by_id.get(key)
        finding = findings_by_id.get(key)
        assessment = assessed_by_id[key]
        available = capture is not None

        def step(code: str, state: FedVerificationCheckState) -> FedArtifactVerificationCheck:
            return FedArtifactVerificationCheck(code, state)

        pending = (
            FedVerificationCheckState.INDEPENDENT_VERIFICATION_REQUIRED
            if available else FedVerificationCheckState.ARTIFACT_MISSING
        )
        checks = [
            step(
                COMMON_CHECKS[0],
                FedVerificationCheckState.LOCAL_BYTES_RECONCILED
                if available else FedVerificationCheckState.ARTIFACT_MISSING,
            ),
            step(COMMON_CHECKS[1], pending),
        ]

        conflicted_time = assessment.claimed_time_state in (
            FedClaimedTimeState.CLAIMED_AFTER_AS_OF,
            FedClaimedTimeState.CLAIMED_BEFORE_SOURCE_RELEASE,
        )
        if finding is not None and (
            not finding.claimed_capture_not_after_as_of
            or (
                finding.claimed_memento_state_at is not None
                and finding.claimed_memento_state_at > checked_as_of
            )
        ):
            conflicted_time = True
        time_state = (
            FedVerificationCheckState.LOCAL_CONFLICT
            if conflicted_time else (
                FedVerificationCheckState.CLAIM_ONLY
                if available else FedVerificationCheckState.ARTIFACT_MISSING
            )
        )
        checks.append(step(COMMON_CHECKS[2], time_state))
        if conflicted_time:
            blockers.append("FED_AUTHENTICITY_TEMPORAL_CLAIM_CONFLICT")

        content_conflict = (
            finding is not None
            and finding.local_document_binding
            is FedLocalArtifactBinding.DIFFERS_FROM_CLAIMED_DOCUMENT_BYTES
        )
        checks.append(step(
            COMMON_CHECKS[3],
            FedVerificationCheckState.LOCAL_CONFLICT if content_conflict else pending,
        ))
        if content_conflict:
            blockers.append("FED_AUTHENTICITY_LOCAL_DOCUMENT_DIGEST_CONFLICT")

        # Separate admission policy can never be satisfied by this plan.
        checks.append(step(
            COMMON_CHECKS[4],
            FedVerificationCheckState.INDEPENDENT_VERIFICATION_REQUIRED,
        ))

        if ref.kind is FedExternalEvidenceKind.WEB_ARCHIVE_MEMENTO:
            checks.extend(step(code, pending) for code in MEMENTO_CHECKS)
        elif ref.kind is FedExternalEvidenceKind.PUBLISHER_ARCHIVE:
            checks.extend(step(code, pending) for code in PUBLISHER_CHECKS)
        elif ref.kind is FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN:
            checks.extend(step(code, pending) for code in TIMESTAMP_CHECKS[:-1])
            document_ready = ref.claimed_document_body_sha256 in local_document_hashes
            checks.append(step(
                TIMESTAMP_CHECKS[-1],
                FedVerificationCheckState.LOCAL_BYTES_RECONCILED
                if document_ready
                else FedVerificationCheckState.ARTIFACT_MISSING,
            ))
            if not document_ready:
                blockers.append("FED_AUTHENTICITY_TOKEN_DOCUMENT_BYTES_NOT_BOUND")
        else:
            raise FedCalendarAuthenticityRequirementsError(
                "Unsupported external artifact type."
            )

        if not available:
            blockers.append("FED_AUTHENTICITY_REFERENCED_ARTIFACT_NOT_SUPPLIED")
        targets.append(FedArtifactVerificationTarget(
            provider_id=ref.provider_id,
            claim_id=ref.claim_id,
            kind=ref.kind,
            claimed_document_sha256=ref.claimed_document_body_sha256,
            artifact_entity_sha256=(capture.entity_body_sha256 if available else None),
            claimed_time_state=assessment.claimed_time_state,
            checks=tuple(checks),
        ))

    blockers = tuple(dict.fromkeys(blockers))
    manifest = {
        "contract": "fed_calendar_artifact_authenticity_requirements_v1",
        "as_of_utc": checked_as_of.isoformat(timespec="microseconds"),
        "canonical_intake_sha256": canonical.diagnostic_manifest_sha256,
        "source_url": canonical.source_url,
        "targets": [
            {
                "provider_id": t.provider_id,
                "claim_id": t.claim_id,
                "kind": t.kind.value,
                "claimed_document_sha256": t.claimed_document_sha256,
                "artifact_sha256": t.artifact_entity_sha256,
                "claimed_time_state": t.claimed_time_state.value,
                "checks": [(c.check_code, c.state.value) for c in t.checks],
            }
            for t in targets
        ],
        "blockers": blockers,
    }
    digest = sha256(json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")).hexdigest()
    return FedCalendarAuthenticityRequirementsReport(
        as_of=checked_as_of,
        source_url=canonical.source_url,
        canonical_intake_sha256=canonical.diagnostic_manifest_sha256,
        expected_claim_count=canonical.expected_claim_count,
        supplied_artifact_count=canonical.received_artifact_count,
        verification_targets=tuple(targets),
        blockers=blockers,
        diagnostic_manifest_sha256=digest,
    )

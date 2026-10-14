
"""F4.5.8: Read-only FOMC historical provenance gap assessment.

Recomputes the F4.5.7 evidence audit and identifies which independent
proofs remain absent. An internally reconciled HTTP body, a pinned URL,
TLS configuration, and an HTTP Last-Modified header are NOT historical
source-version or timestamp attestations.

Never emits Council calendar events, historical replay authorization,
persistence approval, or an activation token. No IO or network calls.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256
import json

from backend.macro.fed_calendar_client import FedCalendarAcquisitionRecord
from backend.macro.fed_calendar_evidence_audit import (
    FedCalendarEvidenceAudit,
    FedCalendarEvidenceAuditError,
    audit_fed_calendar_source_version,
)
from backend.macro.fed_calendar_extractor import FedCalendarScheduleExtraction
from backend.macro.fed_calendar_protocol import (
    FED_2026_SCHEDULE_RELEASE_LABEL_UTC,
)


class FedCalendarProvenanceGapError(ValueError):
    """The supplied audit chain is not internally reproducible."""


class FedCalendarProvenanceNotReady(RuntimeError):
    """Independent provenance and separate admission approval are missing."""


class ProvenanceEvidenceState(str, Enum):
    INTERNALLY_RECONCILED = "INTERNALLY_RECONCILED"
    CLAIM_ONLY = "CLAIM_ONLY"
    PROOF_MISSING = "PROOF_MISSING"
    TEMPORAL_CONFLICT = "TEMPORAL_CONFLICT"


@dataclass(frozen=True)
class FedCalendarProvenanceRequirement:
    code: str
    state: ProvenanceEvidenceState
    evidence_needed: str


# Descriptions are requirements, not representations that proof is present.
_PROOF_DESCRIPTIONS = {
    "FED_CALENDAR_BODY_RECONCILIATION": (
        "A reproducible local SHA-256 and exact eight-meeting extraction; "
        "this is internal integrity only."
    ),
    "FED_CALENDAR_PUBLISHER_ORIGIN": (
        "Independently verifiable publisher-origin evidence binding the "
        "capture to the Federal Reserve source, not a self-reported "
        "transport label or URL."
    ),
    "FED_CALENDAR_HISTORICAL_VERSION": (
        "An independently preserved, dated source version containing the "
        "same relevant schedule content and provably available by the "
        "requested historical as-of; a current page or 2024 release label "
        "cannot establish this."
    ),
    "FED_CALENDAR_CAPTURE_TIME": (
        "An independently witnessed, tamper-evident timestamp bound to "
        "the captured bytes or a verified source-version artifact."
    ),
    "FED_CALENDAR_AS_OF_VISIBILITY": (
        "Independent evidence that the relevant schedule version was "
        "available no later than the intended as-of, with no "
        "retroactive introduction of later observations."
    ),
    "FED_CALENDAR_ADMISSION_POLICY": (
        "A separate, reviewed calendar admission policy and enforced "
        "Council/replay/storage gates after provenance is independently "
        "established; this diagnostic grants none."
    ),
}


@dataclass(frozen=True)
class FedCalendarProvenanceGapReport:
    """Read-only chain audit and actionable gaps, without raw source bytes."""

    as_of: datetime
    claimed_observed_at: datetime
    schedule_release_label_at: datetime
    source_url: str
    entity_body_sha256: str
    upstream_audit_sha256: str
    diagnostic_manifest_sha256: str
    reconciled_2026_meeting_count: int
    requirements: tuple[FedCalendarProvenanceRequirement, ...]
    blockers: tuple[str, ...]

    @property
    def independently_historical_attested(self) -> bool:
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
        raise FedCalendarProvenanceNotReady(
            "FOMC calendar Council delivery blocked: independent provenance "
            "and separate admission approval are absent."
        )

    def require_historical_replay(self) -> None:
        raise FedCalendarProvenanceNotReady(
            "FOMC calendar historical replay blocked: independent "
            "as-of source-version evidence is absent."
        )


def assess_fed_calendar_provenance_gaps(
    acquisition: FedCalendarAcquisitionRecord,
    extraction: FedCalendarScheduleExtraction,
    audit: FedCalendarEvidenceAudit,
    *,
    as_of: datetime,
) -> FedCalendarProvenanceGapReport:
    """Re-audit the original capture and classify unresolved proof requirements.

    A caller-supplied audit or transport label cannot independently attest
    the source or timestamp. All admission controls are unconditional denials.
    """

    if type(acquisition) is not FedCalendarAcquisitionRecord:
        raise TypeError("Expected FedCalendarAcquisitionRecord.")
    if type(extraction) is not FedCalendarScheduleExtraction:
        raise TypeError("Expected FedCalendarScheduleExtraction.")
    if type(audit) is not FedCalendarEvidenceAudit:
        raise TypeError("Expected FedCalendarEvidenceAudit.")

    try:
        canonical = audit_fed_calendar_source_version(
            acquisition, extraction, as_of=as_of
        )
    except FedCalendarEvidenceAuditError as exc:
        raise FedCalendarProvenanceGapError(
            "Fed calendar source evidence cannot be re-audited."
        ) from exc

    if canonical != audit:
        raise FedCalendarProvenanceGapError(
            "Fed calendar evidence audit differs from canonical source chain."
        )

    observed_by_as_of = canonical.claimed_observation_not_after_as_of

    requirements = (
        FedCalendarProvenanceRequirement(
            "FED_CALENDAR_BODY_RECONCILIATION",
            ProvenanceEvidenceState.INTERNALLY_RECONCILED,
            _PROOF_DESCRIPTIONS["FED_CALENDAR_BODY_RECONCILIATION"],
        ),
        FedCalendarProvenanceRequirement(
            "FED_CALENDAR_PUBLISHER_ORIGIN",
            (
                ProvenanceEvidenceState.CLAIM_ONLY
                if acquisition.transport_mode == "DEFAULT_VERIFIED_TLS_CONFIGURATION"
                else ProvenanceEvidenceState.PROOF_MISSING
            ),
            _PROOF_DESCRIPTIONS["FED_CALENDAR_PUBLISHER_ORIGIN"],
        ),
        FedCalendarProvenanceRequirement(
            "FED_CALENDAR_HISTORICAL_VERSION",
            ProvenanceEvidenceState.PROOF_MISSING,
            _PROOF_DESCRIPTIONS["FED_CALENDAR_HISTORICAL_VERSION"],
        ),
        FedCalendarProvenanceRequirement(
            "FED_CALENDAR_CAPTURE_TIME",
            ProvenanceEvidenceState.PROOF_MISSING,
            _PROOF_DESCRIPTIONS["FED_CALENDAR_CAPTURE_TIME"],
        ),
        FedCalendarProvenanceRequirement(
            "FED_CALENDAR_AS_OF_VISIBILITY",
            (
                ProvenanceEvidenceState.CLAIM_ONLY
                if observed_by_as_of
                else ProvenanceEvidenceState.TEMPORAL_CONFLICT
            ),
            _PROOF_DESCRIPTIONS["FED_CALENDAR_AS_OF_VISIBILITY"],
        ),
        FedCalendarProvenanceRequirement(
            "FED_CALENDAR_ADMISSION_POLICY",
            ProvenanceEvidenceState.PROOF_MISSING,
            _PROOF_DESCRIPTIONS["FED_CALENDAR_ADMISSION_POLICY"],
        ),
    )

    # These are analysis findings. A nonempty blocker set is never treated
    # as the sole admission gate; authorization methods always refuse.
    blockers = canonical.blockers + tuple(
        item.code
        for item in requirements
        if item.state != ProvenanceEvidenceState.INTERNALLY_RECONCILED
    )

    manifest = {
        "contract": "fed_calendar_provenance_gaps_v1",
        "as_of_utc": canonical.as_of.isoformat(timespec="microseconds"),
        "claimed_observed_at_utc": canonical.claimed_first_observed_at.isoformat(
            timespec="microseconds"
        ),
        "source_release_label_at_utc": (
            FED_2026_SCHEDULE_RELEASE_LABEL_UTC.isoformat(timespec="microseconds")
        ),
        "source_url": canonical.source_url,
        "entity_body_sha256": canonical.acquisition_entity_body_sha256,
        "upstream_audit_sha256": canonical.diagnostic_manifest_sha256,
        "requirements": [
            (item.code, item.state.value) for item in requirements
        ],
        "blockers": blockers,
    }

    digest = sha256(
        json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()

    return FedCalendarProvenanceGapReport(
        as_of=canonical.as_of,
        claimed_observed_at=canonical.claimed_first_observed_at,
        schedule_release_label_at=FED_2026_SCHEDULE_RELEASE_LABEL_UTC,
        source_url=canonical.source_url,
        entity_body_sha256=canonical.acquisition_entity_body_sha256,
        upstream_audit_sha256=canonical.diagnostic_manifest_sha256,
        diagnostic_manifest_sha256=digest,
        reconciled_2026_meeting_count=canonical.reconciled_2026_meeting_count,
        requirements=requirements,
        blockers=blockers,
    )

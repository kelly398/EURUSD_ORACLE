
"""F4.5.7: Pure, fail-closed FOMC schedule source-version evidence audit.

Re-extracts the retained acquisition entity bytes and requires exact equality
with the supplied extraction result. The resulting fingerprint documents
internal consistency only; it is NOT independent source-version attestation.

No network, database, broker, Council, or historical replay side effects.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json

from backend.macro.fed_calendar_client import FedCalendarAcquisitionRecord
from backend.macro.fed_calendar_extractor import (
    FedCalendarExtractionError,
    FedCalendarScheduleExtraction,
    extract_fed_calendar_schedule,
)


class FedCalendarEvidenceAuditError(ValueError):
    """An acquisition/extraction audit input cannot be trusted internally."""


class FedCalendarEvidenceNotReady(RuntimeError):
    """Quarantined calendar evidence cannot be used as trusted data."""


REQUIRED_ATTESTATION_BLOCKERS = (
    "FED_CALENDAR_HISTORICAL_SOURCE_VERSION_NOT_ATTESTED",
    "FED_CALENDAR_OBSERVATION_CLOCK_NOT_INDEPENDENTLY_ATTESTED",
    "FED_CALENDAR_COUNCIL_DELIVERY_NOT_AUTHORIZED",
    "FED_CALENDAR_HISTORICAL_REPLAY_NOT_AUTHORIZED",
    "FED_CALENDAR_PERSISTENCE_NOT_AUTHORIZED",
)


def _utc(value: datetime, field: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise FedCalendarEvidenceAuditError(
            f"{field} must be a timezone-aware datetime."
        )
    return value.astimezone(timezone.utc)


def _header_claim(value: str | None, field: str) -> str | None:
    if value is None:
        return None

    if (
        type(value) is not str
        or not value
        or len(value) > 1024
        or "\r" in value
        or "\n" in value
    ):
        raise FedCalendarEvidenceAuditError(
            f"Invalid untrusted HTTP metadata: {field}."
        )

    return value


@dataclass(frozen=True)
class FedCalendarEvidenceAudit:
    """Immutable diagnostic receipt. Never contains HTML or Council events."""

    as_of: datetime
    claimed_first_observed_at: datetime
    source_url: str
    acquisition_entity_body_sha256: str
    acquisition_transport_claim: str
    reconciled_2026_meeting_count: int
    diagnostic_manifest_sha256: str
    claimed_observation_not_after_as_of: bool
    blockers: tuple[str, ...]

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
        raise FedCalendarEvidenceNotReady(
            "FOMC calendar delivery is blocked: "
            + "; ".join(REQUIRED_ATTESTATION_BLOCKERS)
        )

    def require_historical_replay(self) -> None:
        raise FedCalendarEvidenceNotReady(
            "FOMC calendar historical replay is blocked: "
            + "; ".join(REQUIRED_ATTESTATION_BLOCKERS)
        )


def audit_fed_calendar_source_version(
    acquisition: FedCalendarAcquisitionRecord,
    extraction: FedCalendarScheduleExtraction,
    *,
    as_of: datetime,
) -> FedCalendarEvidenceAudit:
    """Reconcile a capture and its extraction without approving historical use.

    The exact response bytes are re-parsed, and every extracted candidate is
    compared to the canonical result. Timestamps, TLS labels, and HTTP headers
    remain self-reported metadata; none can independently prove 2024 bytes.
    """

    if type(acquisition) is not FedCalendarAcquisitionRecord:
        raise TypeError("Expected FedCalendarAcquisitionRecord.")

    if type(extraction) is not FedCalendarScheduleExtraction:
        raise TypeError("Expected FedCalendarScheduleExtraction.")

    checked_as_of = _utc(as_of, "as_of")

    # Dataclasses can be created directly or altered with replace(). The
    # extraction must be derived afresh from the actual acquisition body.
    try:
        canonical = extract_fed_calendar_schedule(acquisition)
    except FedCalendarExtractionError as exc:
        raise FedCalendarEvidenceAuditError(
            "FOMC calendar acquisition failed canonical extraction."
        ) from exc

    if extraction != canonical:
        raise FedCalendarEvidenceAuditError(
            "FOMC extraction does not match retained acquisition bytes."
        )

    started = _utc(acquisition.request_started_at, "request_started_at")
    observed = _utc(acquisition.response_completed_at, "response_completed_at")

    etag = _header_claim(acquisition.content_etag, "ETag")
    last_modified = _header_claim(
        acquisition.content_last_modified, "Last-Modified"
    )

    observed_by_as_of = observed <= checked_as_of

    blockers = list(REQUIRED_ATTESTATION_BLOCKERS)

    if not observed_by_as_of:
        blockers.append("FED_CALENDAR_CAPTURE_POSTDATES_AS_OF")

    if acquisition.transport_mode == "INJECTED_TRANSPORT_UNVERIFIED":
        blockers.append("FED_CALENDAR_INJECTED_TRANSPORT_UNVERIFIED")

    # This is a diagnostic manifest, NOT a cryptographic signature of an
    # independent timestamping service or a source-version certificate.
    manifest = {
        "contract": "fed_calendar_source_evidence_audit_v1",
        "as_of_utc": checked_as_of.isoformat(timespec="microseconds"),
        "source_url": acquisition.source_url,
        "response_url": acquisition.response_url,
        "response_status": acquisition.http_status,
        "content_type": acquisition.content_type,
        "source_entity_body_sha256": acquisition.entity_body_sha256,
        "acquisition_started_utc": started.isoformat(timespec="microseconds"),
        "acquisition_completed_utc": observed.isoformat(timespec="microseconds"),
        "transport_claim": acquisition.transport_mode,
        "untrusted_etag": etag,
        "untrusted_last_modified": last_modified,
        "schedule_candidates": [
            {
                "event_id": item.event_id,
                "economic_content_sha256": item.schedule_content_sha256,
            }
            for item in canonical.candidates
        ],
        "rollover_2027_start": canonical.rollover_2027_start.isoformat(),
        "rollover_2027_end": canonical.rollover_2027_end.isoformat(),
    }

    serialized = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")

    return FedCalendarEvidenceAudit(
        as_of=checked_as_of,
        claimed_first_observed_at=observed,
        source_url=acquisition.source_url,
        acquisition_entity_body_sha256=acquisition.entity_body_sha256,
        acquisition_transport_claim=acquisition.transport_mode,
        reconciled_2026_meeting_count=len(canonical.candidates),
        diagnostic_manifest_sha256=sha256(serialized).hexdigest(),
        claimed_observation_not_after_as_of=observed_by_as_of,
        blockers=tuple(blockers),
    )

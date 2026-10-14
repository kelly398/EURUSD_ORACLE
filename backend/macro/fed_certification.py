"""
F3.7: Federal Reserve evidence certification model.

This module defines the admission decision boundary between:

    collected evidence
            |
            v
    certified evidence
            |
            v
    persistence/replay eligibility

No network access.
No database access.
No mutable state.

The certification object is intentionally stricter than economic agreement.
Matching numbers alone do not prove historical availability.
"""

from dataclasses import dataclass
from enum import Enum
from datetime import date, datetime, timezone
import hashlib
import json


class FedCertificationError(ValueError):
    """Invalid Federal Reserve certification state."""


class FedEvidenceResult(Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class FedAvailabilityLevel(Enum):
    """
    How strongly historical availability is established.
    """

    UNKNOWN = "unknown"

    DATE_BOUND = "date_bound"

    INTRADAY_CERTIFIED = "intraday_certified"


class FedCertificationLevel(Enum):
    """
    Overall admission state.
    """

    REJECTED = "rejected"

    CORROBORATED = "corroborated"

    CAUSALLY_AVAILABLE = "causally_available"

    PERSISTENCE_APPROVED = "persistence_approved"


_CERTIFICATION_VERSION = "fed_certification_v1"


@dataclass(frozen=True)
class FedEvidenceCertification:
    """
    Immutable evidence admission record.

    This object does not claim the truth of an economic decision.
    It records the strength of evidence supporting that decision.
    """

    statement_id: str

    economic_consistency: FedEvidenceResult

    source_integrity: FedEvidenceResult

    historical_availability: FedAvailabilityLevel

    certification_level: FedCertificationLevel

    release_timestamp: datetime

    effective_date: date

    fed_document_hashes: tuple[str, ...]

    alfred_response_hashes: tuple[str, ...]

    reason: str

    certification_version: str = (
        _CERTIFICATION_VERSION
    )

    def __post_init__(self):

        if not self.statement_id:
            raise FedCertificationError(
                "statement_id is required."
            )

        if (
            not isinstance(
                self.release_timestamp,
                datetime,
            )
            or self.release_timestamp.tzinfo is None
            or self.release_timestamp.utcoffset()
            is None
        ):
            raise FedCertificationError(
                "release_timestamp must be UTC-aware."
            )

        if type(self.effective_date) is not date:
            raise FedCertificationError(
                "effective_date must be date."
            )

        if not self.reason.strip():
            raise FedCertificationError(
                "Certification requires a reason."
            )

        self._validate_hashes(
            self.fed_document_hashes,
            "Fed document",
        )

        self._validate_hashes(
            self.alfred_response_hashes,
            "ALFRED response",
        )

        self._validate_state()

    @staticmethod
    def _validate_hashes(values, label):

        if not isinstance(values, tuple):
            raise FedCertificationError(
                f"{label} hashes must be tuple."
            )

        for value in values:

            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(
                    c not in "0123456789abcdef"
                    for c in value
                )
            ):
                raise FedCertificationError(
                    f"Invalid {label} SHA256 hash."
                )

    def _validate_state(self):

        level = self.certification_level

        economic = self.economic_consistency

        source = self.source_integrity

        availability = self.historical_availability


        if level == FedCertificationLevel.REJECTED:

            if economic == FedEvidenceResult.PASS:

                raise FedCertificationError(
                    "Rejected evidence cannot have "
                    "passing economic consistency."
                )


        if level in (
            FedCertificationLevel.CORROBORATED,
            FedCertificationLevel.CAUSALLY_AVAILABLE,
            FedCertificationLevel.PERSISTENCE_APPROVED,
        ):

            if economic != FedEvidenceResult.PASS:

                raise FedCertificationError(
                    "Positive certification requires "
                    "economic consistency."
                )


        if level in (
            FedCertificationLevel.CAUSALLY_AVAILABLE,
            FedCertificationLevel.PERSISTENCE_APPROVED,
        ):

            if source != FedEvidenceResult.PASS:

                raise FedCertificationError(
                    "Causal certification requires "
                    "source integrity."
                )


        if level == FedCertificationLevel.PERSISTENCE_APPROVED:

            if availability != (
                FedAvailabilityLevel.INTRADAY_CERTIFIED
            ):

                raise FedCertificationError(
                    "Persistence approval requires "
                    "intraday historical certification."
                )

            if source != FedEvidenceResult.PASS:

                raise FedCertificationError(
                    "Persistence approval requires "
                    "verified sources."
                )


    @property
    def persistence_authorized(self) -> bool:
        return (
            self.certification_level
            == FedCertificationLevel.PERSISTENCE_APPROVED
        )


    @property
    def causally_available(self) -> bool:
        return self.certification_level in (
            FedCertificationLevel.CAUSALLY_AVAILABLE,
            FedCertificationLevel.PERSISTENCE_APPROVED,
        )


    def content_hash(self) -> str:
        """
        Deterministic certification fingerprint.

        Excludes no economic fields.
        Excludes no evidence fields.
        """

        payload = {
            "version": self.certification_version,
            "statement_id": self.statement_id,
            "economic_consistency":
                self.economic_consistency.value,
            "source_integrity":
                self.source_integrity.value,
            "historical_availability":
                self.historical_availability.value,
            "certification_level":
                self.certification_level.value,
            "release_timestamp":
                self.release_timestamp.astimezone(
                    timezone.utc
                ).isoformat(),
            "effective_date":
                self.effective_date.isoformat(),
            "fed_document_hashes":
                self.fed_document_hashes,
            "alfred_response_hashes":
                self.alfred_response_hashes,
            "reason":
                self.reason,
        }

        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        return hashlib.sha256(
            encoded
        ).hexdigest()
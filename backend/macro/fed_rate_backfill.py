
"""F3.2: read-only Federal Reserve backfill planning and provenance preflight.

An official web page retrieved today is not proof of what its HTML contained
at its historical announcement time.

This planner creates candidate snapshots, NOT database-ready historical
observations. No storage class is imported, and there is deliberately no
persistence method in this module.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import ClassVar
import hashlib

from backend.macro.fed_rate_client import (
    FedRateClient,
    FedSourceBundle,
    FedSourceDocument,
)

from backend.macro.fed_rate_extractor import (
    extract_fed_original_rate_evidence,
)

from backend.macro.fed_rate_protocol import (
    FED_CENTRAL_BANK,
    FED_RATE_NAMES,
    FED_RATE_SOURCE,
    FedOriginalRateEvidence,
    build_fed_original_rate_snapshots,
    build_fed_record_id,
    build_fed_statement_id,
    validate_fed_original_evidence,
)

from backend.macro.macro_schemas import (
    CentralBankRateSnapshot,
)


class FedBackfillPreflightError(ValueError):
    """Candidate Fed evidence has inconsistent provenance."""


@dataclass(frozen=True)
class FedSourceFingerprint:
    role: str
    url: str
    sha256: str
    fetched_at: datetime


@dataclass(frozen=True)
class FedRateBackfillPlan:
    statement_id: str
    announced_at: datetime
    effective_date: date
    ingested_at: datetime

    source_fingerprints: tuple[FedSourceFingerprint, ...]

    candidate_snapshots: tuple[
        CentralBankRateSnapshot,
        CentralBankRateSnapshot,
    ]

    provenance_status: ClassVar[str] = (
        "CURRENT_HTML_NOT_HISTORICALLY_ATTESTED"
    )

    @property
    def persistence_permitted(self) -> bool:
        """A current website fetch is not an attested historical version."""
        return False


def _utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise FedBackfillPreflightError(
            f"{label} must be an aware datetime."
        )

    try:
        if value.utcoffset() is None:
            raise ValueError("Missing UTC offset.")

        return value.astimezone(timezone.utc)

    except (ValueError, OverflowError) as exc:
        raise FedBackfillPreflightError(
            f"{label} has invalid timezone data."
        ) from exc


def _fingerprint(
    document: FedSourceDocument,
    role: str,
) -> FedSourceFingerprint:
    if (
        not isinstance(document, FedSourceDocument)
        or document.role != role
    ):
        raise FedBackfillPreflightError(
            f"Expected {role} Fed source document."
        )

    if (
        not isinstance(document.raw_bytes, bytes)
        or not document.raw_bytes
    ):
        raise FedBackfillPreflightError(
            f"Missing {role} source bytes."
        )

    digest = hashlib.sha256(
        document.raw_bytes
    ).hexdigest()

    if document.content_sha256 != digest:
        raise FedBackfillPreflightError(
            f"{role} source fingerprint mismatch."
        )

    return FedSourceFingerprint(
        role=role,
        url=document.url,
        sha256=digest,
        fetched_at=_utc(
            document.fetched_at,
            f"{role}.fetched_at",
        ),
    )


def _validate_pair(
    snapshots: tuple[
        CentralBankRateSnapshot,
        CentralBankRateSnapshot,
    ],
    statement_id: str,
    available_at: datetime,
    effective_date: date,
    ingested_at: datetime,
) -> None:
    if len(snapshots) != 2:
        raise FedBackfillPreflightError(
            "A Fed decision requires both rate bounds."
        )

    for bound, name, snapshot in zip(
        ("lower", "upper"),
        FED_RATE_NAMES,
        snapshots,
        strict=True,
    ):
        if not isinstance(
            snapshot,
            CentralBankRateSnapshot,
        ):
            raise FedBackfillPreflightError(
                "Unexpected rate snapshot schema."
            )

        if (
            snapshot.central_bank != FED_CENTRAL_BANK
            or snapshot.source != FED_RATE_SOURCE
            or snapshot.rate_name != name
            or snapshot.source_record_id != build_fed_record_id(
                statement_id,
                bound,
            )
            or snapshot.reference_date != effective_date
            or _utc(
                snapshot.available_at,
                "snapshot.available_at",
            ) != available_at
            or _utc(
                snapshot.ingested_at,
                "snapshot.ingested_at",
            ) != ingested_at
        ):
            raise FedBackfillPreflightError(
                "Inconsistent Fed decision pair."
            )

    if snapshots[0].value_percent > snapshots[1].value_percent:
        raise FedBackfillPreflightError(
            "Lower target exceeds upper target."
        )

    if snapshots[0].content_hash == snapshots[1].content_hash:
        raise FedBackfillPreflightError(
            "Fed rate bounds need distinct content hashes."
        )


class FedRateBackfillPlanner:
    """Prepare and audit one original release without database writes."""

    def __init__(
        self,
        client=None,
        extractor=extract_fed_original_rate_evidence,
    ):
        self.client = (
            client
            if client is not None
            else FedRateClient()
        )

        if not callable(
            getattr(
                self.client,
                "fetch_original_documents",
                None,
            )
        ):
            raise TypeError(
                "Client must implement fetch_original_documents(url)."
            )

        if not callable(extractor):
            raise TypeError(
                "Extractor must be callable."
            )

        self.extractor = extractor

    def prepare(
        self,
        statement_url: str,
        *,
        ingested_at: datetime | None = None,
    ) -> FedRateBackfillPlan:
        """Produce non-persistable candidates and source fingerprints.

        ingested_at is the proposed preparation/ingestion time, not proof
        of historical publication or permission for database persistence.
        """

        expected_id = build_fed_statement_id(
            statement_url
        )

        bundle = self.client.fetch_original_documents(
            statement_url
        )

        if not isinstance(bundle, FedSourceBundle):
            raise FedBackfillPreflightError(
                "Client did not return a complete bundle."
            )

        fingerprints = tuple(
            _fingerprint(
                getattr(bundle, role),
                role,
            )
            for role in (
                "statement",
                "implementation",
                "meeting",
            )
        )

        if fingerprints[0].url != statement_url:
            raise FedBackfillPreflightError(
                "Statement URL changed during retrieval."
            )

        if not (
            fingerprints[0].fetched_at
            <= fingerprints[1].fetched_at
            <= fingerprints[2].fetched_at
        ):
            raise FedBackfillPreflightError(
                "Document fetch clocks are out of order."
            )

        evidence = self.extractor(bundle)

        if not isinstance(
            evidence,
            FedOriginalRateEvidence,
        ):
            raise FedBackfillPreflightError(
                "Extractor returned invalid evidence."
            )

        (
            statement_id,
            announced_at,
            _lower,
            _upper,
        ) = validate_fed_original_evidence(
            evidence
        )

        if statement_id != expected_id:
            raise FedBackfillPreflightError(
                "Extracted decision identity changed."
            )

        if fingerprints[0].fetched_at < announced_at:
            raise FedBackfillPreflightError(
                "Documents fetched before public release."
            )

        last_fetch = fingerprints[-1].fetched_at

        prepared_at = (
            last_fetch
            if ingested_at is None
            else _utc(
                ingested_at,
                "ingested_at",
            )
        )

        if prepared_at < last_fetch:
            raise FedBackfillPreflightError(
                "Ingestion timestamp predates final source retrieval."
            )

        snapshots = build_fed_original_rate_snapshots(
            evidence,
            prepared_at,
        )

        _validate_pair(
            snapshots,
            statement_id,
            announced_at,
            evidence.effective_date,
            prepared_at,
        )

        return FedRateBackfillPlan(
            statement_id=statement_id,
            announced_at=announced_at,
            effective_date=evidence.effective_date,
            ingested_at=prepared_at,
            source_fingerprints=fingerprints,
            candidate_snapshots=snapshots,
        )

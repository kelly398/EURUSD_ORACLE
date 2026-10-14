"""F5.1: read-only, causal Federal Reserve rate *candidate* repository.

These are UNTRUSTED candidate observations, even when structurally valid.
Neither a database row nor its economic SHA-256 demonstrates historical
publisher custody, independent version attestation, or Council admission.

No writes, network fetches, historical replay, or Council registration.
The production storage hard deny for FED is unchanged.
"""

from datetime import datetime

from sqlalchemy import select

from backend.database.connection import SessionLocal
from backend.database.models import CentralBankRate
from backend.macro.fed_rate_protocol import (
    FED_CENTRAL_BANK,
    FED_RATE_SOURCE,
)
from backend.macro.fed_replay_lock import (
    FedReplayLockError,
    lock_fed_data_as_of,
)
from backend.macro.macro_schemas import CentralBankRateSnapshot

MAX_FED_SCAN_ROWS = 4096
MAX_FED_CANDIDATE_SNAPSHOTS = 256


class FedCandidateRepositoryError(ValueError):
    """Fed candidate retrieval or causal/economic consistency failed."""


class FedRateCandidateRepository:
    """Query an as-of Fed source window for diagnostics, NEVER admission."""

    source = FED_RATE_SOURCE
    central_bank = FED_CENTRAL_BANK

    @staticmethod
    def _validate_request(as_of, limit):
        if (
            not isinstance(as_of, datetime)
            or as_of.tzinfo is None
            or as_of.utcoffset() is None
        ):
            raise FedCandidateRepositoryError(
                "Fed candidate as_of must be timezone-aware."
            )
        if (
            type(limit) is not int
            or limit < 2
            or limit > MAX_FED_CANDIDATE_SNAPSHOTS
            or limit % 2
        ):
            raise FedCandidateRepositoryError(
                "Fed candidate limit must be an even integer from 2 to 256."
            )

    @staticmethod
    def _schema(row):
        return CentralBankRateSnapshot(
            central_bank=row.central_bank,
            source=row.source,
            rate_name=row.rate_name,
            source_record_id=row.source_record_id,
            reference_date=row.reference_date,
            available_at=row.available_at,
            ingested_at=row.ingested_at,
            value_percent=row.value_percent,
            content_hash=row.content_hash,
        )

    def get_history_as_of(
        self,
        *,
        as_of: datetime,
        limit: int = MAX_FED_CANDIDATE_SNAPSHOTS,
    ) -> tuple[CentralBankRateSnapshot, ...]:
        """Return canonical paired candidates; do not issue source approval.

        Filter BOTH version availability and physical ingestion at SQL level.
        Reject oversized scans rather than silently omit conflicting history.
        Resolve revisions atomically with the F1 replay lock, then select the
        newest complete decisions without splitting lower/upper pairs.
        """
        self._validate_request(as_of, limit)

        statement = (
            select(CentralBankRate)
            .where(
                CentralBankRate.source == self.source,
                CentralBankRate.central_bank == self.central_bank,
                CentralBankRate.available_at <= as_of,
                CentralBankRate.ingested_at <= as_of,
            )
            .order_by(
                CentralBankRate.reference_date.asc(),
                CentralBankRate.available_at.asc(),
                CentralBankRate.ingested_at.asc(),
                CentralBankRate.id.asc(),
            )
            .limit(MAX_FED_SCAN_ROWS + 1)
        )

        session = SessionLocal()
        try:
            rows = session.execute(statement).scalars().all()
            if len(rows) > MAX_FED_SCAN_ROWS:
                raise FedCandidateRepositoryError(
                    "Fed candidate scan budget exceeded."
                )
            snapshots = tuple(self._schema(row) for row in rows)
        finally:
            session.close()

        # A mocked/misconfigured DB query must not cause silent omission.
        for item in snapshots:
            if item.available_at > as_of or item.ingested_at > as_of:
                raise FedCandidateRepositoryError(
                    "Fed repository returned a future source version."
                )

        try:
            canonical = lock_fed_data_as_of(snapshots, as_of)
        except (FedReplayLockError, ValueError, TypeError) as exc:
            raise FedCandidateRepositoryError(
                "Fed repository contains invalid paired evidence."
            ) from exc

        if len(canonical) <= limit:
            return canonical

        # Select the most recent complete decisions by their canonical
        # statement identity. Do not use a raw row slice (half-pair risk).
        groups = {}
        for item in canonical:
            statement_id = item.source_record_id.rsplit(":", 1)[0]
            groups.setdefault(statement_id, []).append(item)

        ordered = sorted(
            groups.items(),
            key=lambda group: (
                max(item.reference_date for item in group[1]),
                max(item.available_at for item in group[1]),
                group[0],
            ),
        )
        included = {
            key for key, _ in ordered[-(limit // 2):]
        }
        result = tuple(
            item for item in canonical
            if item.source_record_id.rsplit(":", 1)[0] in included
        )
        if len(result) > limit or lock_fed_data_as_of(result, as_of) != result:
            raise FedCandidateRepositoryError(
                "Fed candidate window is not a complete canonical history."
            )
        return result
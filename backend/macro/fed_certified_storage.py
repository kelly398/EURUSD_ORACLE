"""
F3.7.3a Federal Reserve certified persistence wrapper.

This module provides the admission boundary between:

    certified Fed evidence
            |
            v
    existing CentralBankRateStorage

It does not bypass storage.
It does not write directly to PostgreSQL.
It does not modify existing ECB behavior.

Fed persistence requires:
    - PERSISTENCE_APPROVED certification
    - matching Fed snapshot identity
    - matching certification evidence
"""

from dataclasses import dataclass

from backend.macro.fed_certification import (
    FedCertificationLevel,
    FedEvidenceCertification,
)


from backend.macro.macro_schemas import (
    CentralBankRateSnapshot,
)


from backend.macro.macro_storage import (
    CentralBankRateStorage,
)

from backend.macro.fed_replay_lock import (
    FedReplayLockError,
    lock_fed_data_as_of,
)


class FedCertifiedStorageError(PermissionError):
    """
    Fed evidence failed admission checks.
    """


@dataclass(frozen=True)
class FedCertifiedWriteResult:
    """
    Result of a certified Fed persistence attempt.
    """

    certification_hash: str
    snapshot_count: int
    storage_result: object



def _validate_hash(value: str):
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(
            c not in "0123456789abcdef"
            for c in value
        )
    ):
        raise FedCertifiedStorageError(
            "Invalid certification hash."
        )



class FedCertifiedRateStorage:
    """
    Admission-controlled Federal Reserve storage.

    All actual database behavior remains owned by
    CentralBankRateStorage.
    """

    def __init__(
        self,
        storage=None,
    ):

        self.storage = (
            storage
            if storage is not None
            else CentralBankRateStorage()
        )


    @staticmethod
    def _validate_certification(
        certification,
    ):

        if not isinstance(
            certification,
            FedEvidenceCertification,
        ):
            raise FedCertifiedStorageError(
                "Fed writes require FedEvidenceCertification."
            )


        if (
            certification.certification_level
            != FedCertificationLevel.PERSISTENCE_APPROVED
        ):
            raise FedCertifiedStorageError(
                "Fed evidence is not persistence approved."
            )


        if not certification.persistence_authorized:
            raise FedCertifiedStorageError(
                "Certification does not authorize persistence."
            )


    @staticmethod
    def _validate_snapshots(
        snapshots,
        certification,
    ):
        """Require one canonical Fed target-range pair bound to its claim.

        This is an internal economic/temporal consistency check only.
        A caller-provided PERSISTENCE_APPROVED certificate is not proof
        of independently authenticated historical publication. The
        underlying CentralBankRateStorage still refuses every Fed write.
        """
        if snapshots is None:
            raise FedCertifiedStorageError(
                "Fed snapshots cannot be None."
            )

        try:
            iterator = iter(snapshots)
        except TypeError as exc:
            raise FedCertifiedStorageError(
                "Fed snapshots must be iterable."
            ) from exc

        # Cap consumption before attempting any database operation.
        from itertools import islice
        supplied = tuple(islice(iterator, 3))

        if len(supplied) != 2:
            raise FedCertifiedStorageError(
                "Fed target range requires exactly two snapshots."
            )

        for bound, item in zip(("lower", "upper"), supplied, strict=True):
            if not isinstance(item, CentralBankRateSnapshot):
                raise FedCertifiedStorageError(
                    "Invalid Fed snapshot type."
                )
            if (
                item.central_bank != "FED"
                or item.source != "FED_FOMC_RELEASE"
                or item.rate_name != f"federal_funds_target_{bound}"
                or item.source_record_id
                != f"FOMC:{certification.statement_id}:{bound}"
            ):
                raise FedCertifiedStorageError(
                    "Fed snapshot identity does not match certification."
                )
            if (
                item.reference_date != certification.effective_date
                or item.available_at != certification.release_timestamp
            ):
                raise FedCertifiedStorageError(
                    "Fed snapshot chronology does not match certification."
                )

        if not certification.fed_document_hashes:
            raise FedCertifiedStorageError(
                "Missing Fed evidence hashes."
            )

        # The F1 replay lock rechecks paired economic hashes, ordering,
        # ingestion chronology, source IDs, and both rate bounds.
        try:
            locked = lock_fed_data_as_of(
                supplied,
                as_of=max(item.ingested_at for item in supplied),
            )
        except (FedReplayLockError, ValueError, TypeError) as exc:
            raise FedCertifiedStorageError(
                "Fed snapshot economic or temporal validation failed."
            ) from exc

        if locked != supplied:
            raise FedCertifiedStorageError(
                "Fed snapshot pair is not canonical."
            )

        return supplied

    def store_certified_fed_many(
        self,
        snapshots,
        certification,
    ):

        self._validate_certification(
            certification
        )


        snapshots = self._validate_snapshots(
            snapshots,
            certification,
        )


        certification_hash = (
            certification.content_hash()
        )

        _validate_hash(
            certification_hash
        )


        result = self.storage.store_many(
            snapshots
        )


        return FedCertifiedWriteResult(
            certification_hash=certification_hash,
            snapshot_count=len(snapshots),
            storage_result=result,
        )
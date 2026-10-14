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

        if not isinstance(
            snapshots,
            tuple,
        ):
            snapshots = tuple(snapshots)


        if len(snapshots) != 2:

            raise FedCertifiedStorageError(
                "Fed target range requires exactly two snapshots."
            )


        for snapshot in snapshots:

            if not isinstance(
                snapshot,
                CentralBankRateSnapshot,
            ):
                raise FedCertifiedStorageError(
                    "Invalid Fed snapshot type."
                )


            if (
                snapshot.central_bank != "FED"
                or snapshot.source != "FED_FOMC_RELEASE"
            ):
                raise FedCertifiedStorageError(
                    "Snapshot is not a Fed FOMC record."
                )


            if (
                not snapshot.source_record_id.startswith(
                    "FOMC:"
                )
            ):
                raise FedCertifiedStorageError(
                    "Invalid Fed source identity."
                )


        hashes = tuple(
            snapshot.content_hash
            for snapshot in snapshots
        )

        certification_hashes = (
            certification.fed_document_hashes
        )

        if not certification_hashes:

            raise FedCertifiedStorageError(
                "Missing Fed evidence hashes."
            )


        return snapshots



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
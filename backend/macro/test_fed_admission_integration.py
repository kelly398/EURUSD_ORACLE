"""
F3.7.5 Full Fed admission integration tests.

Offline only.

Validates:

Fed evidence
    ->
Certification
    ->
Certified storage admission

No PostgreSQL.
No network.
No broker.
"""

from datetime import date, datetime, timezone
from unittest import TestCase
from unittest.mock import Mock

from backend.macro.fed_certification import (
    FedAvailabilityLevel,
    FedCertificationLevel,
    FedEvidenceCertification,
    FedEvidenceResult,
)

from backend.macro.fed_certified_storage import (
    FedCertifiedRateStorage,
    FedCertifiedStorageError,
)

from backend.macro.macro_schemas import (
    CentralBankRateSnapshot,
)

from backend.macro.fed_rate_protocol import build_fed_content_hash


UTC = timezone.utc


def fed_snapshots():

    lower = CentralBankRateSnapshot(
        central_bank="FED",
        source="FED_FOMC_RELEASE",
        rate_name="federal_funds_target_lower",
        reference_date=date(
            2026,
            9,
            17,
        ),
        available_at=datetime(
            2026,
            9,
            16,
            18,
            0,
            tzinfo=UTC,
        ),
        value_percent=3.75,
        source_record_id=
            "FOMC:monetary20260916a:lower",
        content_hash=build_fed_content_hash(
            "monetary20260916a",
            "federal_funds_target_lower",
            date(2026, 9, 17),
            "3.75", "4.0",
        ),
        ingested_at=datetime(
            2026,
            10,
            13,
            18,
            0,
            tzinfo=UTC,
        ),
    )


    upper = lower.model_copy(
        update={
            "rate_name":
                "federal_funds_target_upper",

            "value_percent":
                4.0,

            "source_record_id":
                "FOMC:monetary20260916a:upper",

            "content_hash":build_fed_content_hash(
                "monetary20260916a",
                "federal_funds_target_upper",
                date(2026, 9, 17),
                "3.75", "4.0",
            ),
        }
    )

    return (
        lower,
        upper,
    )



def cert(
    level,
    availability=
        FedAvailabilityLevel.DATE_BOUND,
):

    return FedEvidenceCertification(

        statement_id=
            "monetary20260916a",

        economic_consistency=
            FedEvidenceResult.PASS,

        source_integrity=
            FedEvidenceResult.PASS,

        historical_availability=
            availability,

        certification_level=
            level,

        release_timestamp=datetime(
            2026,
            9,
            16,
            18,
            0,
            tzinfo=UTC,
        ),

        effective_date=date(
            2026,
            9,
            17,
        ),

        fed_document_hashes=(
            "c"*64,
        ),

        alfred_response_hashes=(
            "d"*64,
        ),

        reason=
            "integration test certification",
    )



class FedAdmissionIntegrationTests(TestCase):


    def test_01_corrobated_evidence_never_reaches_storage(self):

        storage = Mock()

        wrapper = FedCertifiedRateStorage(
            storage=storage
        )


        with self.assertRaises(
            FedCertifiedStorageError
        ):

            wrapper.store_certified_fed_many(
                fed_snapshots(),
                cert(
                    FedCertificationLevel.CORROBORATED
                ),
            )


        storage.store_many.assert_not_called()



    def test_02_persistence_approved_evidence_reaches_storage(self):

        storage = Mock()

        storage.store_many.return_value = (
            "stored"
        )


        wrapper = FedCertifiedRateStorage(
            storage=storage
        )


        result = (
            wrapper.store_certified_fed_many(
                fed_snapshots(),
                cert(
                    FedCertificationLevel.PERSISTENCE_APPROVED,
                    FedAvailabilityLevel.INTRADAY_CERTIFIED,
                ),
            )
        )


        storage.store_many.assert_called_once()


        self.assertEqual(
            result.snapshot_count,
            2,
        )


        self.assertEqual(
            len(result.certification_hash),
            64,
        )



    def test_03_tampered_certification_cannot_exist(self):

        with self.assertRaises(
            Exception
        ):

            FedEvidenceCertification(
                **{
                    **cert(
                        FedCertificationLevel.PERSISTENCE_APPROVED,
                        FedAvailabilityLevel.INTRADAY_CERTIFIED,
                    ).__dict__,

                    "source_integrity":
                        FedEvidenceResult.FAIL,
                }
            )
    def test_04_date_bound_can_never_become_persistence(self):

        with self.assertRaises(
            Exception
        ):

            cert(
                FedCertificationLevel.PERSISTENCE_APPROVED,
                FedAvailabilityLevel.DATE_BOUND,
            )



if __name__ == "__main__":
    import unittest

    unittest.main(
        verbosity=2
    )
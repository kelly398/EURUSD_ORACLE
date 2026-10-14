"""
F3.7.3b Certified Fed storage admission tests.

Offline only.
No PostgreSQL.
No network.
No broker.
"""

from datetime import date, datetime, timezone
import unittest
from unittest.mock import Mock

from backend.macro.fed_certified_storage import (
    FedCertifiedRateStorage,
    FedCertifiedStorageError,
)

from backend.macro.fed_certification import (
    FedAvailabilityLevel,
    FedCertificationLevel,
    FedEvidenceCertification,
    FedEvidenceResult,
)

from backend.macro.macro_schemas import (
    CentralBankRateSnapshot,
)


UTC = timezone.utc


def certification(
    level=FedCertificationLevel.PERSISTENCE_APPROVED,
):

    return FedEvidenceCertification(
        statement_id="monetary20260916a",

        economic_consistency=
            FedEvidenceResult.PASS,

        source_integrity=
            FedEvidenceResult.PASS,

        historical_availability=
            FedAvailabilityLevel.INTRADAY_CERTIFIED,

        certification_level=level,

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
            "a"*64,
        ),

        alfred_response_hashes=(
            "b"*64,
        ),

        reason="approved test evidence",
    )


def snapshot(
    bank="FED",
    source="FED_FOMC_RELEASE",
):

    return CentralBankRateSnapshot(
        central_bank=bank,
        source=source,

        rate_name=
            "federal_funds_target_lower",

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

        content_hash=
            "c"*64,

        ingested_at=datetime(
            2026,
            10,
            13,
            18,
            0,
            tzinfo=UTC,
        ),
    )


class FedCertifiedStorageTests(unittest.TestCase):


    def test_01_approved_certification_reaches_storage(self):

        storage = Mock()

        storage.store_many.return_value = (
            "stored"
        )

        wrapper = FedCertifiedRateStorage(
            storage=storage
        )

        result = (
            wrapper.store_certified_fed_many(
                (
                    snapshot(),
                    snapshot().model_copy(
                        update={
                            "rate_name":
                            "federal_funds_target_upper",

                            "source_record_id":
                            "FOMC:monetary20260916a:upper",

                            "content_hash":
                            "d"*64,

                            "value_percent":
                            4.0,
                        }
                    ),
                ),
                certification(),
            )
        )

        storage.store_many.assert_called_once()

        self.assertEqual(
            result.snapshot_count,
            2,
        )


    def test_02_corroborated_is_blocked(self):

        with self.assertRaises(
            FedCertifiedStorageError
        ):

            FedCertifiedRateStorage().store_certified_fed_many(
                (snapshot(), snapshot()),
                certification(
                    FedCertificationLevel.CORROBORATED
                ),
            )


    def test_03_causally_available_is_blocked(self):

        with self.assertRaises(
            FedCertifiedStorageError
        ):

            FedCertifiedRateStorage().store_certified_fed_many(
                (snapshot(), snapshot()),
                certification(
                    FedCertificationLevel.CAUSALLY_AVAILABLE
                ),
            )


    def test_04_ecb_snapshot_cannot_use_fed_certification(self):

        with self.assertRaises(
            FedCertifiedStorageError
        ):

            FedCertifiedRateStorage().store_certified_fed_many(
                (
                    snapshot(
                        bank="ECB",
                        source="ECB_SDMX_FM",
                    ),
                    snapshot(
                        bank="ECB",
                        source="ECB_SDMX_FM",
                    ),
                ),
                certification(),
            )


    def test_05_missing_certification_is_blocked(self):

        with self.assertRaises(
            FedCertifiedStorageError
        ):

            FedCertifiedRateStorage().store_certified_fed_many(
                (
                    snapshot(),
                    snapshot(),
                ),
                None,
            )


    def test_06_wrong_snapshot_count_is_blocked(self):

        with self.assertRaises(
            FedCertifiedStorageError
        ):

            FedCertifiedRateStorage().store_certified_fed_many(
                (
                    snapshot(),
                ),
                certification(),
            )


    def test_07_invalid_snapshot_type_is_blocked(self):

        with self.assertRaises(
            FedCertifiedStorageError
        ):

            FedCertifiedRateStorage().store_certified_fed_many(
                (
                    object(),
                    snapshot(),
                ),
                certification(),
            )


    def test_08_storage_failure_is_not_hidden(self):

        storage = Mock()

        storage.store_many.side_effect = (
            RuntimeError("storage failed")
        )

        wrapper = FedCertifiedRateStorage(
            storage=storage
        )

        with self.assertRaises(
            RuntimeError
        ):

            wrapper.store_certified_fed_many(
                (
                    snapshot(),
                    snapshot(),
                ),
                certification(),
            )


    def test_09_certification_hash_returned(self):

        storage = Mock()

        wrapper = FedCertifiedRateStorage(
            storage=storage
        )

        result = (
            wrapper.store_certified_fed_many(
                (
                    snapshot(),
                    snapshot(),
                ),
                certification(),
            )
        )

        self.assertEqual(
            len(result.certification_hash),
            64,
        )


if __name__ == "__main__":
    unittest.main(
        verbosity=2
    )
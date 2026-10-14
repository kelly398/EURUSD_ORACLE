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

from backend.macro.fed_rate_protocol import build_fed_content_hash


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
    *,
    bound="lower",
):

    return CentralBankRateSnapshot(
        central_bank=bank,
        source=source,

        rate_name=
            f"federal_funds_target_{bound}",

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

        value_percent=3.75 if bound == "lower" else 4.0,

        source_record_id=
            f"FOMC:monetary20260916a:{bound}",

        content_hash=build_fed_content_hash(
            "monetary20260916a",
            f"federal_funds_target_{bound}",
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
                    snapshot(bound="upper"),
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
                    snapshot(bound="upper"),
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
                    snapshot(bound="upper"),
                ),
                certification(),
            )
        )

        self.assertEqual(
            len(result.certification_hash),
            64,
        )


    def test_10_wrong_statement_certification_is_rejected(self):
        from dataclasses import replace
        storage = Mock()
        mismatch = replace(certification(), statement_id="monetary20261216a")
        with self.assertRaises(FedCertifiedStorageError):
            FedCertifiedRateStorage(storage=storage).store_certified_fed_many(
                (snapshot(), snapshot(bound="upper")), mismatch,
            )
        storage.store_many.assert_not_called()

    def test_11_wrong_effective_date_is_rejected(self):
        from dataclasses import replace
        storage = Mock()
        mismatch = replace(certification(), effective_date=date(2026, 9, 18))
        with self.assertRaises(FedCertifiedStorageError):
            FedCertifiedRateStorage(storage=storage).store_certified_fed_many(
                (snapshot(), snapshot(bound="upper")), mismatch,
            )
        storage.store_many.assert_not_called()

    def test_12_wrong_release_time_is_rejected(self):
        from dataclasses import replace
        storage = Mock()
        mismatch = replace(
            certification(),
            release_timestamp=datetime(2026, 9, 16, 19, tzinfo=UTC),
        )
        with self.assertRaises(FedCertifiedStorageError):
            FedCertifiedRateStorage(storage=storage).store_certified_fed_many(
                (snapshot(), snapshot(bound="upper")), mismatch,
            )
        storage.store_many.assert_not_called()

    def test_13_forged_economic_hash_is_rejected(self):
        storage = Mock()
        invalid = snapshot().model_copy(update={"content_hash": "c" * 64})
        with self.assertRaises(FedCertifiedStorageError):
            FedCertifiedRateStorage(storage=storage).store_certified_fed_many(
                (invalid, snapshot(bound="upper")), certification(),
            )
        storage.store_many.assert_not_called()

    def test_14_duplicate_bound_is_rejected(self):
        storage = Mock()
        with self.assertRaises(FedCertifiedStorageError):
            FedCertifiedRateStorage(storage=storage).store_certified_fed_many(
                (snapshot(), snapshot()), certification(),
            )
        storage.store_many.assert_not_called()

    def test_15_out_of_order_bounds_are_rejected(self):
        storage = Mock()
        with self.assertRaises(FedCertifiedStorageError):
            FedCertifiedRateStorage(storage=storage).store_certified_fed_many(
                (snapshot(bound="upper"), snapshot()), certification(),
            )
        storage.store_many.assert_not_called()


if __name__ == "__main__":
    unittest.main(
        verbosity=2
    )
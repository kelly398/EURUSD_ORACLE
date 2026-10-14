
"""F3.6.1: offline, fail-closed Federal Reserve storage gate tests.

Run:
    python -m backend.macro.test_fed_rate_storage_gate

No PostgreSQL connections, network requests, broker actions,
or reserved HMM evaluation.
"""

from datetime import date, datetime, timezone
import unittest
from unittest.mock import Mock, patch

from backend.macro.macro_schemas import CentralBankRateSnapshot
from backend.macro.macro_storage import (
    CentralBankRateStorage,
    FedRatePersistenceBlocked,
    MacroStorageResult,
)


WHEN = datetime(2026, 10, 13, 18, 0, tzinfo=timezone.utc)


def snapshot(bank="FED", source="FED_FOMC_RELEASE"):
    return CentralBankRateSnapshot(
        central_bank=bank,
        source=source,
        rate_name="federal_funds_target_lower",
        reference_date=date(2026, 9, 17),
        available_at=datetime(
            2026, 9, 16, 18, 0, tzinfo=timezone.utc
        ),
        value_percent=3.75,
        source_record_id="FOMC:monetary20260916a:lower",
        content_hash="a" * 64,
        ingested_at=WHEN,
    )


class FedRateStorageGateTests(unittest.TestCase):

    def test_01_fed_lower_blocked_before_session_creation(self):
        with patch(
            "backend.macro.macro_storage.SessionLocal"
        ) as factory:
            with self.assertRaises(FedRatePersistenceBlocked):
                CentralBankRateStorage().store_many(
                    (snapshot(),)
                )
            factory.assert_not_called()

    def test_02_fed_upper_blocked_before_session_creation(self):
        upper = snapshot().model_copy(update={
            "rate_name": "federal_funds_target_upper",
            "source_record_id": "FOMC:monetary20260916a:upper",
            "content_hash": "b" * 64,
            "value_percent": 4.0,
        })

        with patch(
            "backend.macro.macro_storage.SessionLocal"
        ) as factory:
            with self.assertRaises(FedRatePersistenceBlocked):
                CentralBankRateStorage().store_many((upper,))
            factory.assert_not_called()

    def test_03_mixed_batch_blocks_before_any_database_action(self):
        ecb = snapshot("ECB", "ECB_SDMX_FM")

        with patch(
            "backend.macro.macro_storage.SessionLocal"
        ) as factory:
            with self.assertRaises(FedRatePersistenceBlocked):
                CentralBankRateStorage().store_many(
                    (ecb, snapshot())
                )
            factory.assert_not_called()

    def test_04_fed_bank_cannot_hide_under_ecb_source(self):
        with patch(
            "backend.macro.macro_storage.SessionLocal"
        ) as factory:
            with self.assertRaises(FedRatePersistenceBlocked):
                CentralBankRateStorage().store_many(
                    (snapshot("FED", "ECB_SDMX_FM"),)
                )
            factory.assert_not_called()

    def test_05_fed_source_cannot_hide_under_ecb_bank(self):
        with patch(
            "backend.macro.macro_storage.SessionLocal"
        ) as factory:
            with self.assertRaises(FedRatePersistenceBlocked):
                CentralBankRateStorage().store_many(
                    (snapshot("ECB", "FED_FOMC_RELEASE"),)
                )
            factory.assert_not_called()

    def test_06_case_and_padding_do_not_bypass_guard(self):
        pairs = (
            (" fed ", "ECB_SDMX_FM"),
            ("ECB", " fed_fomc_release "),
        )

        for bank, source in pairs:
            with self.subTest(bank=bank, source=source):
                with patch(
                    "backend.macro.macro_storage.SessionLocal"
                ) as factory:
                    with self.assertRaises(FedRatePersistenceBlocked):
                        CentralBankRateStorage().store_many(
                            (snapshot(bank, source),)
                        )
                    factory.assert_not_called()

    def test_07_internal_insert_helper_rejects_fed(self):
        session = Mock()

        with self.assertRaises(FedRatePersistenceBlocked):
            CentralBankRateStorage()._insert_snapshot(
                session,
                snapshot(),
            )

        session.execute.assert_not_called()

    def test_08_ecb_batch_remains_transactional(self):
        ecb = snapshot("ECB", "ECB_SDMX_FM")
        writer = CentralBankRateStorage()

        with (
            patch(
                "backend.macro.macro_storage.SessionLocal"
            ) as factory,
            patch.object(
                writer,
                "_insert_snapshot",
                side_effect=[True, False],
            ) as insert,
        ):
            result = writer.store_many((ecb, ecb))

            self.assertEqual(
                result,
                MacroStorageResult(2, 1, 1),
            )
            self.assertEqual(insert.call_count, 2)
            factory.assert_called_once_with()
            factory.return_value.commit.assert_called_once_with()
            factory.return_value.close.assert_called_once_with()
            factory.return_value.rollback.assert_not_called()

    def test_09_invalid_snapshot_rejected_before_session(self):
        with patch(
            "backend.macro.macro_storage.SessionLocal"
        ) as factory:
            with self.assertRaises(TypeError):
                CentralBankRateStorage().store_many(
                    (object(),)
                )
            factory.assert_not_called()

    def test_10_empty_batch_remains_noop(self):
        with patch(
            "backend.macro.macro_storage.SessionLocal"
        ) as factory:
            self.assertEqual(
                CentralBankRateStorage().store_many(()),
                MacroStorageResult(0, 0, 0),
            )
            factory.assert_not_called()

    def test_11_internal_helper_rejects_wrong_type(self):
        session = Mock()

        with self.assertRaises(TypeError):
            CentralBankRateStorage()._insert_snapshot(
                session,
                object(),
            )

        session.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)

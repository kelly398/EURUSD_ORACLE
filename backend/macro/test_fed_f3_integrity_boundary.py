"""Seat 25 F3.8: offline Fed certification truthfulness and write-boundary tests.

Synthetic Fed/ALFRED observations only. No network, PostgreSQL, broker,
Council activation, real historical attestation, or reserved HMM execution.
"""

from datetime import date, datetime, timezone
from unittest import TestCase
from unittest.mock import Mock, patch

from backend.macro.fed_certification import (
    FedAvailabilityLevel,
    FedCertificationLevel,
    FedEvidenceResult,
)
from backend.macro.fed_certified_backfill import FedCertifiedBackfill, FedCertifiedBackfillError
from backend.macro.fed_certified_storage import FedCertifiedRateStorage, FedCertifiedStorageError
from backend.macro.fed_rate_alfred_client import FedAlfredClient
from backend.macro.fed_rate_backfill import FedRateBackfillPlanner
from backend.macro.macro_storage import CentralBankRateStorage, FedRatePersistenceBlocked
from backend.macro.test_fed_rate_alfred_client import EFFECTIVE, FETCHED, KEY, FakeTransport
from backend.macro.test_fed_rate_backfill import FakeClient
from backend.macro.test_fed_rate_extractor import STATEMENT_URL
from backend.macro.test_fed_certified_storage import certification, snapshot


class FedF3IntegrityBoundaryTests(TestCase):
    def _backfill(self, storage=None):
        planner = FedRateBackfillPlanner(client=FakeClient())
        alfred = FedAlfredClient(
            api_key=KEY,
            opener=FakeTransport(),
            clock=lambda: FETCHED,
        )
        return FedCertifiedBackfill(planner=planner, alfred=alfred, storage=storage)

    def test_01_real_synthetic_source_pair_never_mints_integrity_pass(self):
        plan, pair, result = self._backfill().certify_candidate(
            STATEMENT_URL, EFFECTIVE,
        )
        self.assertEqual(len(plan.candidate_snapshots), 2)
        self.assertEqual(len(pair.observations), 2)
        self.assertEqual(result.certification_level, FedCertificationLevel.CORROBORATED)
        self.assertEqual(result.economic_consistency, FedEvidenceResult.PASS)
        self.assertEqual(result.source_integrity, FedEvidenceResult.UNKNOWN)
        self.assertEqual(result.historical_availability, FedAvailabilityLevel.DATE_BOUND)
        self.assertFalse(result.persistence_authorized)
        self.assertFalse(result.causally_available)
        self.assertIn("unverified", result.reason)

    def test_02_corroborated_source_does_not_forward_to_injected_storage(self):
        storage = Mock()
        backfill = self._backfill(storage=storage)
        with self.assertRaises(FedCertifiedBackfillError):
            backfill.persist_certified(STATEMENT_URL, EFFECTIVE)
        storage.store_certified_fed_many.assert_not_called()

    def test_03_fabricated_approved_object_cannot_enter_actual_db_writer(self):
        # A self-asserted approval can be constructed; it MUST NOT bypass
        # the separate hard deny in the true production storage layer.
        pair = (snapshot(), snapshot(bound="upper"))
        with patch("backend.macro.macro_storage.SessionLocal") as session:
            with self.assertRaises(FedRatePersistenceBlocked):
                FedCertifiedRateStorage(storage=CentralBankRateStorage()).store_certified_fed_many(
                    pair, certification(),
                )
            session.assert_not_called()

    def test_04_injected_storage_cannot_borrow_unrelated_statement(self):
        from dataclasses import replace
        fake_storage = Mock()
        with self.assertRaises(FedCertifiedStorageError):
            FedCertifiedRateStorage(storage=fake_storage).store_certified_fed_many(
                (snapshot(), snapshot(bound="upper")),
                replace(certification(), statement_id="monetary20261216a"),
            )
        fake_storage.store_many.assert_not_called()

    def test_05_certification_does_not_change_context_or_calendar_authority(self):
        _, _, cert = self._backfill().certify_candidate(STATEMENT_URL, EFFECTIVE)
        self.assertFalse(cert.causally_available)
        self.assertFalse(cert.persistence_authorized)
        self.assertEqual(cert.source_integrity, FedEvidenceResult.UNKNOWN)

    def test_06_planner_pair_reconciles_with_current_certification(self):
        plan, _, cert = self._backfill().certify_candidate(STATEMENT_URL, EFFECTIVE)
        self.assertEqual(
            FedCertifiedRateStorage._validate_snapshots(
                plan.candidate_snapshots, cert,
            ),
            plan.candidate_snapshots,
        )
        self.assertFalse(cert.persistence_authorized)


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
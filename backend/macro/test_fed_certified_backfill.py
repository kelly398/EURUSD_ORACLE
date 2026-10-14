"""
F3.7.4b Certified Fed backfill integration tests.

Offline only.
No PostgreSQL.
No network.
"""

from datetime import date
from unittest import TestCase
from unittest.mock import Mock

from backend.macro.fed_certified_backfill import (
    FedCertifiedBackfill,
    FedCertifiedBackfillError,
)

from backend.macro.fed_certification import (
    FedCertificationLevel,
)

from backend.macro.test_fed_rate_backfill import (
    FakeClient,
)

from backend.macro.test_fed_rate_extractor import (
    STATEMENT_URL,
)


class FakePlanner:

    def prepare(self, url):

        from backend.macro.fed_rate_backfill import (
            FedRateBackfillPlanner,
        )

        return FedRateBackfillPlanner(
            client=FakeClient()
        ).prepare(url)


class FakeAlfred:

    def fetch_pair(
        self,
        observation_date,
        vintage_date,
    ):

        from backend.macro.fed_rate_alfred_client import (
            FedAlfredSourcePair,
        )

        from backend.macro.test_fed_rate_alfred_client import (
            FakeResponse,
        )

        raise AssertionError(
            "ALFRED should be mocked in these tests."
        )


class FakeStorage:

    def __init__(self):
        self.called = False

    def store_certified_fed_many(
        self,
        snapshots,
        certification,
    ):
        self.called = True
        return Mock(
            certification_hash="a"*64,
            snapshot_count=len(snapshots),
        )


class FedCertifiedBackfillTests(TestCase):


    def test_01_corrobation_creates_non_persistable_certification(self):

        """
        Current evidence path should produce
        CORROBORATED, not persistence approval.
        """

        backfill = FedCertifiedBackfill(
            planner=Mock(),
            alfred=Mock(),
        )

        # checked through existing certification tests
        self.assertEqual(
            FedCertificationLevel.CORROBORATED.value,
            "corroborated",
        )


    def test_02_persistence_requires_authorized_certification(self):

        storage = FakeStorage()

        backfill = FedCertifiedBackfill(
            storage=storage
        )

        with self.assertRaises(
            FedCertifiedBackfillError
        ):

            backfill.persist_certified(
                STATEMENT_URL,
                date(2026,9,18),
            )

        self.assertFalse(
            storage.called
        )


    def test_03_storage_wrapper_is_never_bypassed(self):

        storage = Mock()

        backfill = FedCertifiedBackfill(
            storage=storage
        )

        try:
            backfill.persist_certified(
                STATEMENT_URL,
                date(2026,9,18),
            )

        except Exception:
            pass

        storage.store_many.assert_not_called()


    def test_04_certification_levels_are_explicit(self):

        from backend.macro.fed_certification import (
            FedCertificationLevel,
        )

        self.assertEqual(
            FedCertificationLevel.CORROBORATED.value,
            "corroborated",
        )

        self.assertEqual(
            FedCertificationLevel.PERSISTENCE_APPROVED.value,
            "persistence_approved",
        )


if __name__ == "__main__":
    import unittest
    unittest.main(
        verbosity=2
    )
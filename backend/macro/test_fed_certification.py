"""
F3.7.2 Federal Reserve certification tests.

Offline only.
No database.
No network.
No replay.
"""

from datetime import date, datetime, timezone
import unittest

from backend.macro.fed_certification import (
    FedCertificationError,
    FedCertificationLevel,
    FedEvidenceCertification,
    FedEvidenceResult,
    FedAvailabilityLevel,
)


UTC = timezone.utc


def certification(
    level,
    economic=FedEvidenceResult.PASS,
    source=FedEvidenceResult.PASS,
    availability=FedAvailabilityLevel.DATE_BOUND,
):
    return FedEvidenceCertification(
        statement_id="monetary20260916a",
        economic_consistency=economic,
        source_integrity=source,
        historical_availability=availability,
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
            "a" * 64,
            "b" * 64,
            "c" * 64,
        ),
        alfred_response_hashes=(
            "d" * 64,
            "e" * 64,
        ),
        reason="deterministic test evidence",
    )


class FedCertificationTests(unittest.TestCase):

    def test_01_corroborated_is_valid(self):

        item = certification(
            FedCertificationLevel.CORROBORATED
        )

        self.assertFalse(
            item.persistence_authorized
        )

        self.assertFalse(
            item.causally_available
        )

        self.assertEqual(
            len(item.content_hash()),
            64,
        )


    def test_02_causally_available_is_valid(self):

        item = certification(
            FedCertificationLevel.CAUSALLY_AVAILABLE,
            availability=
                FedAvailabilityLevel.DATE_BOUND,
        )

        self.assertTrue(
            item.causally_available
        )

        self.assertFalse(
            item.persistence_authorized
        )


    def test_03_intraday_certified_can_support_persistence(self):

        item = certification(
            FedCertificationLevel.PERSISTENCE_APPROVED,
            availability=
                FedAvailabilityLevel.INTRADAY_CERTIFIED,
        )

        self.assertTrue(
            item.persistence_authorized
        )

        self.assertTrue(
            item.causally_available
        )


    def test_04_persistence_requires_intraday(self):

        with self.assertRaises(
            FedCertificationError
        ):

            certification(
                FedCertificationLevel.PERSISTENCE_APPROVED,
                availability=
                    FedAvailabilityLevel.DATE_BOUND,
            )


    def test_05_causal_requires_source_integrity(self):

        with self.assertRaises(
            FedCertificationError
        ):

            certification(
                FedCertificationLevel.CAUSALLY_AVAILABLE,
                source=FedEvidenceResult.FAIL,
            )


    def test_06_positive_states_require_economic_pass(self):

        for level in (
            FedCertificationLevel.CORROBORATED,
            FedCertificationLevel.CAUSALLY_AVAILABLE,
            FedCertificationLevel.PERSISTENCE_APPROVED,
        ):

            with self.subTest(level=level):

                with self.assertRaises(
                    FedCertificationError
                ):

                    certification(
                        level,
                        economic=
                            FedEvidenceResult.FAIL,
                        availability=
                            FedAvailabilityLevel.INTRADAY_CERTIFIED,
                    )


    def test_07_rejected_cannot_have_economic_pass(self):

        with self.assertRaises(
            FedCertificationError
        ):

            certification(
                FedCertificationLevel.REJECTED,
                economic=
                    FedEvidenceResult.PASS,
            )


    def test_08_hash_is_deterministic(self):

        a = certification(
            FedCertificationLevel.CORROBORATED
        )

        b = certification(
            FedCertificationLevel.CORROBORATED
        )

        self.assertEqual(
            a.content_hash(),
            b.content_hash(),
        )


    def test_09_hash_changes_with_reason(self):

        a = certification(
            FedCertificationLevel.CORROBORATED
        )

        b = FedEvidenceCertification(
            **{
                **a.__dict__,
                "reason": "different evidence",
            }
        )

        self.assertNotEqual(
            a.content_hash(),
            b.content_hash(),
        )


    def test_10_invalid_hash_length_rejected(self):

        with self.assertRaises(
            FedCertificationError
        ):

            FedEvidenceCertification(
                statement_id="test",
                economic_consistency=
                    FedEvidenceResult.PASS,
                source_integrity=
                    FedEvidenceResult.PASS,
                historical_availability=
                    FedAvailabilityLevel.DATE_BOUND,
                certification_level=
                    FedCertificationLevel.CORROBORATED,
                release_timestamp=datetime.now(
                    UTC
                ),
                effective_date=date.today(),
                fed_document_hashes=("bad",),
                alfred_response_hashes=(),
                reason="bad",
            )


if __name__ == "__main__":
    unittest.main(
        verbosity=2
    )
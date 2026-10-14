
"""F4.5.9 fully offline external FOMC evidence-claim protocol tests.

Run: python -m backend.macro.test_fed_calendar_independent_evidence
No live HTTP, DB, Council, broker or reserved HMM run.
"""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from itertools import repeat
import unittest

from backend.macro.fed_calendar_evidence_audit import audit_fed_calendar_source_version
from backend.macro.fed_calendar_extractor import extract_fed_calendar_schedule
from backend.macro.fed_calendar_independent_evidence import (
    FedCalendarExternalEvidenceClaim,
    FedCalendarIndependentEvidenceError,
    FedCalendarIndependentEvidenceNotReady,
    FedClaimedTimePrecision,
    FedClaimedTimeState,
    FedExternalEvidenceKind,
    MAX_EXTERNAL_EVIDENCE_CLAIMS,
    REQUIRED_EXTERNAL_VERIFICATION_STEPS,
    catalog_fed_calendar_independent_evidence,
)
from backend.macro.fed_calendar_protocol import (
    FED_2026_SCHEDULE_RELEASE_LABEL_UTC,
    FED_2026_SCHEDULE_URL,
)
from backend.macro.fed_calendar_provenance_gaps import (
    assess_fed_calendar_provenance_gaps,
)
from backend.macro.test_fed_calendar_extractor import make_record


AS_OF = datetime(2026, 10, 14, 13, tzinfo=timezone.utc)
CAPTURE = datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc)


def evidence_chain(*, as_of=AS_OF):
    acquisition = make_record()
    extraction = extract_fed_calendar_schedule(acquisition)
    audit = audit_fed_calendar_source_version(
        acquisition, extraction, as_of=as_of
    )
    gaps = assess_fed_calendar_provenance_gaps(
        acquisition, extraction, audit, as_of=as_of
    )
    return acquisition, extraction, audit, gaps


def claim(**changes):
    base = dict(
        provider_id="ARCHIVE_A",
        claim_id="CAPTURE_001",
        kind=FedExternalEvidenceKind.WEB_ARCHIVE_MEMENTO,
        external_artifact_url="https://archive.example.org/memento/20261014",
        original_source_url=FED_2026_SCHEDULE_URL,
        claimed_document_body_sha256=make_record().entity_body_sha256,
        claimed_time_precision=FedClaimedTimePrecision.INSTANT,
        claimed_evidence_time=CAPTURE,
    )
    base.update(changes)
    return FedCalendarExternalEvidenceClaim(**base)


def review(refs=(), *, chain=None, as_of=AS_OF):
    if chain is None:
        chain = evidence_chain(as_of=as_of)
    return catalog_fed_calendar_independent_evidence(
        *chain, refs, as_of=as_of
    )


class FedCalendarIndependentEvidenceTests(unittest.TestCase):

    def test_01_no_external_references_remains_blocked(self):
        item = review()
        self.assertEqual(item.external_claim_count, 0)
        self.assertIn(
            "FED_CALENDAR_EXTERNAL_EVIDENCE_REFERENCES_ABSENT",
            item.blockers,
        )
        self.assertFalse(item.independent_historical_source_attested)

    def test_02_structurally_valid_claim_is_not_proof(self):
        item = review((claim(),))
        self.assertEqual(item.external_claim_count, 1)
        self.assertEqual(item.claimed_provider_count, 1)
        self.assertFalse(item.council_calendar_admission_authorized)
        self.assertFalse(item.historical_replay_authorized)
        self.assertFalse(item.persistence_authorized)

    def test_03_claimed_same_bytes_not_authenticated(self):
        item = review((claim(),))
        self.assertEqual(item.claimed_same_bytes_count, 1)
        self.assertTrue(
            item.claim_assessments[0].claimed_same_bytes_as_current_capture
        )
        self.assertIn(
            "FED_CALENDAR_EXTERNAL_BYTES_AND_SIGNATURES_NOT_VERIFIED",
            item.blockers,
        )

    def test_04_different_historical_bytes_allowed_as_unverified_claim(self):
        item = review((claim(claimed_document_body_sha256="f" * 64),))
        self.assertEqual(item.claimed_same_bytes_count, 0)
        self.assertFalse(item.historical_replay_authorized)

    def test_05_instant_claimed_before_asof_not_accepted(self):
        item = review((claim(),))
        self.assertEqual(
            item.claim_assessments[0].claimed_time_state,
            FedClaimedTimeState.CLAIMED_NOT_AFTER_AS_OF,
        )
        self.assertFalse(item.independent_historical_source_attested)

    def test_06_future_instant_has_conflict(self):
        item = review((claim(claimed_evidence_time=AS_OF + timedelta(seconds=1)),))
        self.assertEqual(
            item.claim_assessments[0].claimed_time_state,
            FedClaimedTimeState.CLAIMED_AFTER_AS_OF,
        )
        self.assertIn("FED_CALENDAR_EXTERNAL_TIME_CLAIM_CONFLICT", item.blockers)

    def test_07_equal_asof_still_not_verified(self):
        item = review((claim(claimed_evidence_time=AS_OF),))
        self.assertEqual(
            item.claim_assessments[0].claimed_time_state,
            FedClaimedTimeState.CLAIMED_NOT_AFTER_AS_OF,
        )
        self.assertFalse(item.historical_replay_authorized)

    def test_08_pre_2024_label_is_an_invalid_temporal_claim(self):
        ref = claim(
            claimed_evidence_time=FED_2026_SCHEDULE_RELEASE_LABEL_UTC
            - timedelta(seconds=1)
        )
        item = review((ref,))
        self.assertEqual(
            item.claim_assessments[0].claimed_time_state,
            FedClaimedTimeState.CLAIMED_BEFORE_SOURCE_RELEASE,
        )
        self.assertIn("FED_CALENDAR_EXTERNAL_TIME_CLAIM_CONFLICT", item.blockers)

    def test_09_exact_release_label_is_only_claim(self):
        ref = claim(claimed_evidence_time=FED_2026_SCHEDULE_RELEASE_LABEL_UTC)
        item = review((ref,))
        self.assertEqual(
            item.claim_assessments[0].claimed_time_state,
            FedClaimedTimeState.CLAIMED_NOT_AFTER_AS_OF,
        )
        self.assertFalse(item.independent_historical_source_attested)

    def test_10_date_only_previous_day_claim_is_not_verification(self):
        ref = claim(
            claimed_time_precision=FedClaimedTimePrecision.UTC_DATE_ONLY,
            claimed_evidence_time=date(2026, 10, 13),
        )
        item = review((ref,))
        self.assertEqual(
            item.claim_assessments[0].claimed_time_state,
            FedClaimedTimeState.CLAIMED_NOT_AFTER_AS_OF,
        )
        self.assertFalse(item.council_calendar_admission_authorized)

    def test_11_date_only_same_day_is_intraday_ambiguous(self):
        ref = claim(
            claimed_time_precision=FedClaimedTimePrecision.UTC_DATE_ONLY,
            claimed_evidence_time=date(2026, 10, 14),
        )
        item = review((ref,))
        self.assertEqual(
            item.claim_assessments[0].claimed_time_state,
            FedClaimedTimeState.DATE_ONLY_INTRADAY_AMBIGUOUS,
        )
        self.assertIn(
            "FED_CALENDAR_EXTERNAL_DATE_PRECISION_AMBIGUOUS", item.blockers
        )

    def test_12_date_only_future_day_is_conflict(self):
        ref = claim(
            claimed_time_precision=FedClaimedTimePrecision.UTC_DATE_ONLY,
            claimed_evidence_time=date(2026, 10, 15),
        )
        item = review((ref,))
        self.assertEqual(
            item.claim_assessments[0].claimed_time_state,
            FedClaimedTimeState.CLAIMED_AFTER_AS_OF,
        )

    def test_13_2024_release_day_precision_is_insufficient(self):
        ref = claim(
            claimed_time_precision=FedClaimedTimePrecision.UTC_DATE_ONLY,
            claimed_evidence_time=date(2024, 8, 9),
        )
        item = review((ref,))
        self.assertEqual(
            item.claim_assessments[0].claimed_time_state,
            FedClaimedTimeState.RELEASE_DAY_TIME_AMBIGUOUS,
        )

    def test_14_date_only_prior_to_release_is_conflict(self):
        ref = claim(
            claimed_time_precision=FedClaimedTimePrecision.UTC_DATE_ONLY,
            claimed_evidence_time=date(2024, 8, 8),
        )
        item = review((ref,))
        self.assertEqual(
            item.claim_assessments[0].claimed_time_state,
            FedClaimedTimeState.CLAIMED_BEFORE_SOURCE_RELEASE,
        )

    def test_15_timestamp_token_requires_instant_precision(self):
        with self.assertRaisesRegex(
            FedCalendarIndependentEvidenceError, "instant precision"
        ):
            claim(
                kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN,
                claimed_time_precision=FedClaimedTimePrecision.UTC_DATE_ONLY,
                claimed_evidence_time=date(2026, 10, 14),
            )

    def test_16_signed_timestamp_token_label_cannot_authorize(self):
        item = review((claim(kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN),))
        self.assertFalse(item.independent_historical_source_attested)

    def test_17_publisher_archive_not_independent_by_name(self):
        item = review((claim(kind=FedExternalEvidenceKind.PUBLISHER_ARCHIVE),))
        self.assertFalse(item.historical_replay_authorized)
        self.assertIn(
            "FED_CALENDAR_EXTERNAL_ORIGIN_AND_INDEPENDENCE_NOT_VERIFIED",
            item.blockers,
        )

    def test_18_two_claimed_providers_do_not_prove_independence(self):
        refs = (
            claim(),
            claim(provider_id="ARCHIVE_B", claim_id="CAPTURE_002"),
        )
        item = review(refs)
        self.assertEqual(item.claimed_provider_count, 2)
        self.assertFalse(item.independent_historical_source_attested)

    def test_19_duplicate_provider_record_identity_rejected(self):
        with self.assertRaisesRegex(
            FedCalendarIndependentEvidenceError, "Duplicate"
        ):
            review((claim(), claim()))

    def test_20_same_provider_distinct_records_not_independent(self):
        item = review((claim(), claim(claim_id="CAPTURE_002")))
        self.assertEqual(item.external_claim_count, 2)
        self.assertEqual(item.claimed_provider_count, 1)
        self.assertFalse(item.historical_replay_authorized)

    def test_21_provider_id_validation(self):
        for bad in ("", "lower", "A B", "x" * 65, 42):
            with self.subTest(bad=bad):
                with self.assertRaises(FedCalendarIndependentEvidenceError):
                    claim(provider_id=bad)

    def test_22_original_source_must_be_exactly_pinned(self):
        with self.assertRaisesRegex(
            FedCalendarIndependentEvidenceError, "pinned Fed source"
        ):
            claim(original_source_url="https://evil.invalid/")

    def test_23_artifact_url_must_use_safe_https_structure(self):
        for bad in (
            "http://archive.example.org/item",
            "https://u:p@archive.example.org/item",
            "https://archive.example.org/item#fragment",
            "https://archive.example.org:444/item",
            "https://archive.example.org/has space",
            "https://archive.example.org:bogus/item",
            "https:///missing-host",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(FedCalendarIndependentEvidenceError):
                    claim(external_artifact_url=bad)

    def test_24_invalid_or_uppercase_content_hash_rejected(self):
        for bad in ("g" * 64, "A" * 64, "0" * 63, None):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(
                    FedCalendarIndependentEvidenceError, "SHA-256"
                ):
                    claim(claimed_document_body_sha256=bad)

    def test_25_time_precision_requires_correct_python_type(self):
        with self.assertRaisesRegex(
            FedCalendarIndependentEvidenceError, "timezone-aware"
        ):
            claim(claimed_evidence_time=CAPTURE.replace(tzinfo=None))
        with self.assertRaisesRegex(
            FedCalendarIndependentEvidenceError, "requires a date"
        ):
            claim(
                claimed_time_precision=FedClaimedTimePrecision.UTC_DATE_ONLY,
                claimed_evidence_time=CAPTURE,
            )
        with self.assertRaises(FedCalendarIndependentEvidenceError):
            claim(claimed_time_precision="INSTANT")

    def test_26_tz_equivalent_claim_produces_same_digest(self):
        local = CAPTURE.astimezone(timezone(timedelta(hours=-5)))
        self.assertEqual(
            review((claim(),)).diagnostic_manifest_sha256,
            review((claim(claimed_evidence_time=local),)).diagnostic_manifest_sha256,
        )

    def test_27_claim_and_artifact_changes_change_manifest(self):
        a = review((claim(),))
        b = review((claim(claim_id="CAPTURE_002"),))
        c = review((claim(external_artifact_url="https://archive.example.org/other"),))
        self.assertNotEqual(a.diagnostic_manifest_sha256, b.diagnostic_manifest_sha256)
        self.assertNotEqual(a.diagnostic_manifest_sha256, c.diagnostic_manifest_sha256)

    def test_28_repeatability_and_upstream_binding(self):
        a = review((claim(),))
        b = review((claim(),))
        self.assertEqual(a.diagnostic_manifest_sha256, b.diagnostic_manifest_sha256)
        self.assertEqual(
            a.canonical_provenance_sha256,
            evidence_chain()[3].diagnostic_manifest_sha256,
        )
        self.assertRegex(a.diagnostic_manifest_sha256, r"^[0-9a-f]{64}$")

    def test_29_forged_provenance_gap_report_rejected(self):
        chain = evidence_chain()
        forged = replace(chain[3], blockers=())
        with self.assertRaisesRegex(
            FedCalendarIndependentEvidenceError, "differs"
        ):
            review((claim(),), chain=(*chain[:3], forged))

    def test_30_modified_acquisition_after_audit_rejected(self):
        chain = evidence_chain()
        forged = replace(chain[0], entity_body_sha256="f" * 64)
        with self.assertRaisesRegex(
            FedCalendarIndependentEvidenceError, "revalidated"
        ):
            review((claim(),), chain=(forged, *chain[1:]))

    def test_31_tampered_upstream_audit_rejected(self):
        chain = evidence_chain()
        forged = replace(chain[2], blockers=())
        with self.assertRaisesRegex(
            FedCalendarIndependentEvidenceError, "revalidated"
        ):
            review((claim(),), chain=(*chain[:2], forged, chain[3]))

    def test_32_different_asof_requires_recomputed_provenance(self):
        chain = evidence_chain()
        with self.assertRaisesRegex(
            FedCalendarIndependentEvidenceError, "revalidated"
        ):
            review((claim(),), chain=chain, as_of=AS_OF + timedelta(seconds=1))

    def test_33_invalid_asof_rejected(self):
        chain = evidence_chain()
        for bad in (None, "2026-10-14", AS_OF.replace(tzinfo=None)):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(
                    FedCalendarIndependentEvidenceError, "timezone-aware"
                ):
                    review((claim(),), chain=chain, as_of=bad)

    def test_34_oversized_or_none_or_wrong_type_claims_rejected(self):
        with self.assertRaisesRegex(
            FedCalendarIndependentEvidenceError, "limit exceeded"
        ):
            review(repeat(claim(), MAX_EXTERNAL_EVIDENCE_CLAIMS + 1))
        with self.assertRaises(TypeError):
            review(None)
        with self.assertRaisesRegex(
            FedCalendarIndependentEvidenceError, "exact claim type"
        ):
            review((object(),))

    def test_35_mutated_frozen_claim_fails_again_on_review(self):
        ref = claim()
        object.__setattr__(ref, "kind", "WEB_ARCHIVE_MEMENTO")
        with self.assertRaisesRegex(
            FedCalendarIndependentEvidenceError, "kind"
        ):
            review((ref,))

    def test_36_receipt_does_not_embed_external_urls_or_calendar_data(self):
        item = review((claim(),))
        for name in (
            "entity_body", "candidates", "context", "economic_calendar",
            "external_artifact_url", "source_snapshots", "raw_evidence",
        ):
            self.assertFalse(hasattr(item, name))
        self.assertEqual(
            item.required_independent_checks,
            REQUIRED_EXTERNAL_VERIFICATION_STEPS,
        )
        self.assertEqual(len(item.required_independent_checks), 6)

    def test_37_delivery_and_replay_require_methods_always_refuse(self):
        item = review((claim(),))
        for operation in (
            item.require_council_calendar_delivery,
            item.require_historical_replay,
        ):
            with self.assertRaises(FedCalendarIndependentEvidenceNotReady):
                operation()

    def test_38_forged_empty_blockers_cannot_authorize(self):
        item = replace(
            review((claim(),)),
            blockers=(),
            required_independent_checks=(),
        )
        self.assertFalse(item.independent_historical_source_attested)
        self.assertFalse(item.council_calendar_admission_authorized)
        self.assertFalse(item.historical_replay_authorized)
        self.assertFalse(item.persistence_authorized)
        with self.assertRaises(FedCalendarIndependentEvidenceNotReady):
            item.require_historical_replay()

    def test_39_frozen_report(self):
        with self.assertRaises(FrozenInstanceError):
            review().source_url = "changed"


if __name__ == "__main__":
    unittest.main(verbosity=2)

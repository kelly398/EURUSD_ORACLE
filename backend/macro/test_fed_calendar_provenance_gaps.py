
"""F4.5.8: Fully offline historical FOMC calendar provenance gap tests.

Run: python -m backend.macro.test_fed_calendar_provenance_gaps
No HTTP, database access, Council activation, broker, or HMM execution.
"""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from backend.macro.fed_calendar_evidence_audit import (
    audit_fed_calendar_source_version,
)
from backend.macro.fed_calendar_extractor import (
    extract_fed_calendar_schedule,
)
from backend.macro.fed_calendar_provenance_gaps import (
    FedCalendarProvenanceGapError,
    FedCalendarProvenanceNotReady,
    ProvenanceEvidenceState,
    assess_fed_calendar_provenance_gaps,
)
from backend.macro.test_fed_calendar_extractor import (
    END,
    START,
    html_document,
    make_record,
)


AS_OF = datetime(
    2026, 10, 14, 13,
    tzinfo=timezone.utc,
)


def inputs(record=None, *, as_of=AS_OF):
    acquired = make_record() if record is None else record

    extracted = extract_fed_calendar_schedule(acquired)

    audited = audit_fed_calendar_source_version(
        acquired,
        extracted,
        as_of=as_of,
    )

    return acquired, extracted, audited


def report(record=None, *, as_of=AS_OF):
    acquired, extracted, audited = inputs(
        record, as_of=as_of
    )

    return assess_fed_calendar_provenance_gaps(
        acquired,
        extracted,
        audited,
        as_of=as_of,
    )


class FedCalendarProvenanceGapTests(unittest.TestCase):

    def test_01_receipt_reports_eight_reconciled_meetings(self):
        item = report()

        self.assertEqual(
            item.requirements[0].state,
            ProvenanceEvidenceState.INTERNALLY_RECONCILED,
        )
        self.assertEqual(
            item.reconciled_2026_meeting_count, 8
        )
        self.assertEqual(item.claimed_observed_at, END)

    def test_02_body_digest_bound_to_prior_audit(self):
        item = report()
        acquired, _, audited = inputs()

        self.assertEqual(
            item.entity_body_sha256,
            sha256(acquired.entity_body).hexdigest(),
        )
        self.assertEqual(
            item.upstream_audit_sha256,
            audited.diagnostic_manifest_sha256,
        )
        self.assertRegex(
            item.diagnostic_manifest_sha256,
            r"^[a-f0-9]{64}$",
        )

    def test_03_original_release_label_not_historical_proof(self):
        item = report()

        self.assertEqual(
            item.schedule_release_label_at.year, 2024
        )
        self.assertEqual(
            item.claimed_observed_at.year, 2026
        )
        self.assertFalse(
            item.independently_historical_attested
        )

    def test_04_all_six_requirements_have_unique_codes_and_proof_text(self):
        findings = report().requirements

        self.assertEqual(len(findings), 6)
        self.assertEqual(
            len({r.code for r in findings}), 6
        )
        self.assertTrue(
            all(r.evidence_needed for r in findings)
        )

    def test_05_internal_integrity_is_not_admission(self):
        item = report()

        self.assertEqual(
            item.requirements[0].state,
            ProvenanceEvidenceState.INTERNALLY_RECONCILED,
        )
        self.assertFalse(
            item.historical_replay_authorized
        )
        self.assertFalse(
            item.council_calendar_admission_authorized
        )

    def test_06_injected_transport_does_not_prove_publisher_origin(self):
        item = report()

        self.assertEqual(
            item.requirements[1].state,
            ProvenanceEvidenceState.PROOF_MISSING,
        )
        self.assertIn(
            "FED_CALENDAR_INJECTED_TRANSPORT_UNVERIFIED",
            item.blockers,
        )

    def test_07_default_tls_label_is_only_a_claim(self):
        modified = replace(
            make_record(),
            transport_mode="DEFAULT_VERIFIED_TLS_CONFIGURATION",
        )

        item = report(modified)

        self.assertEqual(
            item.requirements[1].state,
            ProvenanceEvidenceState.CLAIM_ONLY,
        )
        self.assertFalse(
            item.independently_historical_attested
        )

    def test_08_historical_version_and_capture_clock_are_missing(self):
        item = report()

        self.assertEqual(
            item.requirements[2].state,
            ProvenanceEvidenceState.PROOF_MISSING,
        )
        self.assertEqual(
            item.requirements[3].state,
            ProvenanceEvidenceState.PROOF_MISSING,
        )
        self.assertIn(
            "FED_CALENDAR_HISTORICAL_SOURCE_VERSION_NOT_ATTESTED",
            item.blockers,
        )
        self.assertIn(
            "FED_CALENDAR_OBSERVATION_CLOCK_NOT_INDEPENDENTLY_ATTESTED",
            item.blockers,
        )

    def test_09_capture_after_asof_is_temporal_conflict(self):
        item = report(as_of=START)

        self.assertEqual(
            item.requirements[4].state,
            ProvenanceEvidenceState.TEMPORAL_CONFLICT,
        )
        self.assertIn(
            "FED_CALENDAR_CAPTURE_POSTDATES_AS_OF",
            item.blockers,
        )

    def test_10_capture_equal_asof_is_still_only_claimed(self):
        item = report(as_of=END)

        self.assertEqual(
            item.requirements[4].state,
            ProvenanceEvidenceState.CLAIM_ONLY,
        )
        self.assertNotIn(
            "FED_CALENDAR_CAPTURE_POSTDATES_AS_OF",
            item.blockers,
        )
        self.assertFalse(
            item.historical_replay_authorized
        )

    def test_11_capture_prior_asof_still_not_proven(self):
        item = report(
            as_of=AS_OF + timedelta(days=1)
        )

        self.assertEqual(
            item.requirements[4].state,
            ProvenanceEvidenceState.CLAIM_ONLY,
        )
        self.assertFalse(
            item.council_calendar_admission_authorized
        )

    def test_12_2025_replay_cannot_backdate_2026_capture(self):
        replay_as_of = datetime(
            2025, 9, 17,
            tzinfo=timezone.utc,
        )

        item = report(as_of=replay_as_of)

        self.assertLess(
            item.schedule_release_label_at,
            replay_as_of,
        )
        self.assertGreater(
            item.claimed_observed_at,
            replay_as_of,
        )
        self.assertEqual(
            item.requirements[4].state,
            ProvenanceEvidenceState.TEMPORAL_CONFLICT,
        )
        self.assertFalse(
            item.historical_replay_authorized
        )

    def test_13_separate_policy_admission_is_always_missing(self):
        item = report()

        self.assertEqual(
            item.requirements[5].state,
            ProvenanceEvidenceState.PROOF_MISSING,
        )
        self.assertIn(
            "FED_CALENDAR_ADMISSION_POLICY",
            item.blockers,
        )
        self.assertFalse(
            item.persistence_authorized
        )

    def test_14_repeatability(self):
        self.assertEqual(
            report().diagnostic_manifest_sha256,
            report().diagnostic_manifest_sha256,
        )

    def test_15_timezone_equivalence_preserves_digest(self):
        local = AS_OF.astimezone(
            timezone(timedelta(hours=-5))
        )

        self.assertEqual(
            report(as_of=local).diagnostic_manifest_sha256,
            report().diagnostic_manifest_sha256,
        )

    def test_16_changed_asof_changes_digest(self):
        self.assertNotEqual(
            report().diagnostic_manifest_sha256,
            report(
                as_of=AS_OF + timedelta(seconds=1)
            ).diagnostic_manifest_sha256,
        )

    def test_17_changed_source_bytes_changes_digest(self):
        a = report()

        b = report(
            make_record(
                body=html_document(
                    extra_html="<p>Change</p>"
                )
            )
        )

        self.assertNotEqual(
            a.entity_body_sha256,
            b.entity_body_sha256,
        )
        self.assertNotEqual(
            a.diagnostic_manifest_sha256,
            b.diagnostic_manifest_sha256,
        )

    def test_18_untrusted_header_changes_receipt_not_grade(self):
        modified = replace(
            make_record(),
            content_last_modified=(
                "Fri, 09 Aug 2023 17:30:00 GMT"
            ),
        )

        a = report()
        b = report(modified)

        self.assertEqual(
            a.entity_body_sha256,
            b.entity_body_sha256,
        )
        self.assertNotEqual(
            a.diagnostic_manifest_sha256,
            b.diagnostic_manifest_sha256,
        )
        self.assertFalse(
            b.independently_historical_attested
        )

    def test_19_forged_upstream_blockers_cannot_pass(self):
        acquired, extracted, audited = inputs()
        forged = replace(audited, blockers=())

        with self.assertRaisesRegex(
            FedCalendarProvenanceGapError, "differs"
        ):
            assess_fed_calendar_provenance_gaps(
                acquired,
                extracted,
                forged,
                as_of=AS_OF,
            )

    def test_20_forged_audit_asof_cannot_pass(self):
        acquired, extracted, audited = inputs()

        forged = replace(
            audited,
            as_of=AS_OF + timedelta(seconds=1),
        )

        with self.assertRaisesRegex(
            FedCalendarProvenanceGapError, "differs"
        ):
            assess_fed_calendar_provenance_gaps(
                acquired,
                extracted,
                forged,
                as_of=AS_OF,
            )

    def test_21_forged_upstream_observation_flag_cannot_pass(self):
        acquired, extracted, audited = inputs()

        forged = replace(
            audited,
            claimed_observation_not_after_as_of=False,
        )

        with self.assertRaisesRegex(
            FedCalendarProvenanceGapError, "differs"
        ):
            assess_fed_calendar_provenance_gaps(
                acquired,
                extracted,
                forged,
                as_of=AS_OF,
            )

    def test_22_forged_audit_digest_cannot_pass(self):
        acquired, extracted, audited = inputs()

        forged = replace(
            audited,
            diagnostic_manifest_sha256="f" * 64,
        )

        with self.assertRaisesRegex(
            FedCalendarProvenanceGapError, "differs"
        ):
            assess_fed_calendar_provenance_gaps(
                acquired,
                extracted,
                forged,
                as_of=AS_OF,
            )

    def test_23_stale_extraction_cannot_pass(self):
        acquired, extracted, audited = inputs()

        modified = make_record(
            body=html_document(
                extra_html="<p>Updated footer</p>"
            )
        )

        with self.assertRaisesRegex(
            FedCalendarProvenanceGapError, "re-audited"
        ):
            assess_fed_calendar_provenance_gaps(
                modified,
                extracted,
                audited,
                as_of=AS_OF,
            )

    def test_24_bad_original_sha256_cannot_pass(self):
        acquired, extracted, audited = inputs()

        modified = replace(
            acquired,
            entity_body_sha256="0" * 64,
        )

        with self.assertRaisesRegex(
            FedCalendarProvenanceGapError, "re-audited"
        ):
            assess_fed_calendar_provenance_gaps(
                modified,
                extracted,
                audited,
                as_of=AS_OF,
            )

    def test_25_bad_original_document_url_cannot_pass(self):
        acquired, extracted, audited = inputs()

        modified = replace(
            acquired,
            source_url="https://untrusted.invalid/",
        )

        with self.assertRaisesRegex(
            FedCalendarProvenanceGapError, "re-audited"
        ):
            assess_fed_calendar_provenance_gaps(
                modified,
                extracted,
                audited,
                as_of=AS_OF,
            )

    def test_26_invalid_asof_is_rejected(self):
        acquired, extracted, audited = inputs()

        for bad in (
            None,
            "2026-10-14",
            AS_OF.replace(tzinfo=None),
        ):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(
                    FedCalendarProvenanceGapError,
                    "re-audited",
                ):
                    assess_fed_calendar_provenance_gaps(
                        acquired,
                        extracted,
                        audited,
                        as_of=bad,
                    )

    def test_27_invalid_types_rejected(self):
        acquired, extracted, audited = inputs()

        for a, e, r in (
            (object(), extracted, audited),
            (acquired, object(), audited),
            (acquired, extracted, object()),
        ):
            with self.subTest(
                a=type(a),
                e=type(e),
                r=type(r),
            ):
                with self.assertRaises(TypeError):
                    assess_fed_calendar_provenance_gaps(
                        a, e, r, as_of=AS_OF
                    )

    def test_28_receipt_carries_no_html_or_calendar_context(self):
        item = report()

        for name in (
            "entity_body",
            "candidates",
            "calendar_snapshots",
            "context",
            "economic_calendar",
        ):
            self.assertFalse(
                hasattr(item, name)
            )

    def test_29_council_delivery_always_refuses(self):
        with self.assertRaises(
            FedCalendarProvenanceNotReady
        ):
            report().require_council_calendar_delivery()

    def test_30_historical_replay_always_refuses(self):
        with self.assertRaises(
            FedCalendarProvenanceNotReady
        ):
            report().require_historical_replay()

    def test_31_forged_empty_blockers_cannot_authorize(self):
        forged = replace(
            report(),
            blockers=(),
            requirements=(),
        )

        self.assertFalse(
            forged.council_calendar_admission_authorized
        )
        self.assertFalse(
            forged.historical_replay_authorized
        )
        self.assertFalse(
            forged.persistence_authorized
        )

        with self.assertRaises(
            FedCalendarProvenanceNotReady
        ):
            forged.require_historical_replay()

    def test_32_frozen_diagnostic(self):
        with self.assertRaises(FrozenInstanceError):
            report().source_url = "changed"


if __name__ == "__main__":
    unittest.main(verbosity=2)

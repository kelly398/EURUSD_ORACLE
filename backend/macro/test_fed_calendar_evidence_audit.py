
"""F4.5.7: Offline FOMC acquisition-to-extraction source evidence audit.

Run: python -m backend.macro.test_fed_calendar_evidence_audit
No HTTP, database, broker, Council registration, or HMM.
"""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from backend.macro.fed_calendar_client import FedCalendarClient
from backend.macro.fed_calendar_evidence_audit import (
    FedCalendarEvidenceAudit,
    FedCalendarEvidenceAuditError,
    FedCalendarEvidenceNotReady,
    REQUIRED_ATTESTATION_BLOCKERS,
    audit_fed_calendar_source_version,
)
from backend.macro.fed_calendar_extractor import extract_fed_calendar_schedule
from backend.macro.test_fed_calendar_client import FakeOpener, FakeResponse
from backend.macro.test_fed_calendar_extractor import (
    END,
    START,
    html_document,
    make_record,
)


AS_OF = datetime(2026, 10, 14, 13, tzinfo=timezone.utc)


def pipeline(record=None, *, as_of=AS_OF):
    candidate = make_record() if record is None else record
    extracted = extract_fed_calendar_schedule(candidate)
    return audit_fed_calendar_source_version(
        candidate, extracted, as_of=as_of
    )


class FedCalendarEvidenceAuditTests(unittest.TestCase):

    def test_01_valid_diagnostic_reconciles_eight_meetings(self):
        result = pipeline()

        self.assertIsInstance(result, FedCalendarEvidenceAudit)
        self.assertEqual(result.reconciled_2026_meeting_count, 8)
        self.assertEqual(result.claimed_first_observed_at, END)
        self.assertEqual(result.source_url, make_record().source_url)

    def test_02_captured_bytes_digest_is_retained(self):
        record = make_record()
        result = pipeline(record)

        self.assertEqual(
            result.acquisition_entity_body_sha256,
            sha256(record.entity_body).hexdigest(),
        )
        self.assertRegex(
            result.diagnostic_manifest_sha256, r"^[0-9a-f]{64}$"
        )

    def test_03_no_independent_history_or_admission(self):
        result = pipeline()

        self.assertFalse(result.historical_source_version_attested)
        self.assertFalse(result.council_calendar_admission_authorized)
        self.assertFalse(result.historical_replay_authorized)
        self.assertFalse(result.persistence_authorized)

    def test_04_required_blockers_are_nonempty(self):
        result = pipeline()

        self.assertEqual(
            result.blockers[:len(REQUIRED_ATTESTATION_BLOCKERS)],
            REQUIRED_ATTESTATION_BLOCKERS,
        )
        self.assertIn(
            "FED_CALENDAR_OBSERVATION_CLOCK_NOT_INDEPENDENTLY_ATTESTED",
            result.blockers,
        )

    def test_05_preobservation_asof_adds_blocker(self):
        result = pipeline(as_of=START)

        self.assertFalse(result.claimed_observation_not_after_as_of)
        self.assertIn(
            "FED_CALENDAR_CAPTURE_POSTDATES_AS_OF", result.blockers
        )
        self.assertFalse(result.historical_replay_authorized)

    def test_06_equal_observation_asof_still_not_admitted(self):
        result = pipeline(as_of=END)

        self.assertTrue(result.claimed_observation_not_after_as_of)
        self.assertNotIn(
            "FED_CALENDAR_CAPTURE_POSTDATES_AS_OF", result.blockers
        )
        self.assertFalse(result.historical_replay_authorized)

    def test_07_postobservation_asof_still_not_admitted(self):
        result = pipeline(as_of=END + timedelta(days=1))

        self.assertTrue(result.claimed_observation_not_after_as_of)
        self.assertFalse(result.council_calendar_admission_authorized)

    def test_08_injected_transport_is_explicitly_diagnostic(self):
        result = pipeline()

        self.assertEqual(
            result.acquisition_transport_claim,
            "INJECTED_TRANSPORT_UNVERIFIED",
        )
        self.assertIn(
            "FED_CALENDAR_INJECTED_TRANSPORT_UNVERIFIED",
            result.blockers,
        )

    def test_09_forged_default_transport_label_does_not_authorize(self):
        record = replace(
            make_record(),
            transport_mode="DEFAULT_VERIFIED_TLS_CONFIGURATION",
        )
        result = pipeline(record)

        self.assertNotIn(
            "FED_CALENDAR_INJECTED_TRANSPORT_UNVERIFIED",
            result.blockers,
        )
        self.assertFalse(result.historical_source_version_attested)
        self.assertIn(
            "FED_CALENDAR_OBSERVATION_CLOCK_NOT_INDEPENDENTLY_ATTESTED",
            result.blockers,
        )
        self.assertFalse(result.council_calendar_admission_authorized)

    def test_10_repeatability(self):
        self.assertEqual(
            pipeline().diagnostic_manifest_sha256,
            pipeline().diagnostic_manifest_sha256,
        )

    def test_11_equivalent_timezone_asof_stable(self):
        earlier_offset = timezone(timedelta(hours=-5))
        local = AS_OF.astimezone(earlier_offset)

        self.assertEqual(
            pipeline(as_of=local).diagnostic_manifest_sha256,
            pipeline().diagnostic_manifest_sha256,
        )

    def test_12_different_asof_changes_manifest_not_source(self):
        a = pipeline()
        b = pipeline(as_of=AS_OF + timedelta(seconds=1))

        self.assertNotEqual(
            a.diagnostic_manifest_sha256,
            b.diagnostic_manifest_sha256,
        )
        self.assertEqual(
            a.acquisition_entity_body_sha256,
            b.acquisition_entity_body_sha256,
        )

    def test_13_untrusted_etag_change_affects_only_manifest(self):
        base = make_record()
        revised = replace(base, content_etag='"new-etag"')

        a, b = pipeline(base), pipeline(revised)

        self.assertNotEqual(
            a.diagnostic_manifest_sha256, b.diagnostic_manifest_sha256
        )
        self.assertEqual(
            a.acquisition_entity_body_sha256,
            b.acquisition_entity_body_sha256,
        )
        self.assertFalse(b.historical_replay_authorized)

    def test_14_last_modified_2024_is_not_historical_attestation(self):
        result = pipeline()

        self.assertFalse(result.historical_source_version_attested)
        self.assertIn(
            "FED_CALENDAR_HISTORICAL_SOURCE_VERSION_NOT_ATTESTED",
            result.blockers,
        )

    def test_15_nonsemantic_source_byte_change_changes_receipt(self):
        a = pipeline()
        revised = make_record(
            body=html_document(extra_html="<p>Unrelated footer</p>")
        )
        b = pipeline(revised)

        self.assertNotEqual(
            a.acquisition_entity_body_sha256,
            b.acquisition_entity_body_sha256,
        )
        self.assertNotEqual(
            a.diagnostic_manifest_sha256,
            b.diagnostic_manifest_sha256,
        )

    def test_16_modified_record_after_extraction_rejected(self):
        original = make_record()
        extraction = extract_fed_calendar_schedule(original)
        revised = make_record(
            body=html_document(extra_html="<p>Changed footer</p>")
        )

        with self.assertRaisesRegex(
            FedCalendarEvidenceAuditError, "does not match"
        ):
            audit_fed_calendar_source_version(
                revised, extraction, as_of=AS_OF
            )

    def test_17_modified_extraction_digest_rejected(self):
        record = make_record()
        original = extract_fed_calendar_schedule(record)
        forged = replace(
            original, entity_body_sha256="f" * 64
        )

        with self.assertRaisesRegex(
            FedCalendarEvidenceAuditError, "does not match"
        ):
            audit_fed_calendar_source_version(
                record, forged, as_of=AS_OF
            )

    def test_18_modified_extraction_observation_rejected(self):
        record = make_record()
        original = extract_fed_calendar_schedule(record)
        forged = replace(
            original,
            observed_at=END + timedelta(seconds=1),
        )

        with self.assertRaisesRegex(
            FedCalendarEvidenceAuditError, "does not match"
        ):
            audit_fed_calendar_source_version(
                record, forged, as_of=AS_OF
            )

    def test_19_modified_extraction_transport_rejected(self):
        record = make_record()
        original = extract_fed_calendar_schedule(record)
        forged = replace(
            original,
            transport_mode="DEFAULT_VERIFIED_TLS_CONFIGURATION",
        )

        with self.assertRaisesRegex(
            FedCalendarEvidenceAuditError, "does not match"
        ):
            audit_fed_calendar_source_version(
                record, forged, as_of=AS_OF
            )

    def test_20_forged_meeting_content_hash_rejected(self):
        record = make_record()
        original = extract_fed_calendar_schedule(record)

        forged_candidate = replace(
            original.candidates[0],
            schedule_content_sha256="f" * 64,
        )
        forged = replace(
            original,
            candidates=(forged_candidate,) + original.candidates[1:],
        )

        with self.assertRaisesRegex(
            FedCalendarEvidenceAuditError, "does not match"
        ):
            audit_fed_calendar_source_version(
                record, forged, as_of=AS_OF
            )

    def test_21_forged_candidate_observation_rejected(self):
        record = make_record()
        original = extract_fed_calendar_schedule(record)

        forged_candidate = replace(
            original.candidates[0],
            first_observed_at=END + timedelta(seconds=1),
        )
        forged = replace(
            original,
            candidates=(forged_candidate,) + original.candidates[1:],
        )

        with self.assertRaisesRegex(
            FedCalendarEvidenceAuditError, "does not match"
        ):
            audit_fed_calendar_source_version(
                record, forged, as_of=AS_OF
            )

    def test_22_missing_candidate_rejected(self):
        record = make_record()
        original = extract_fed_calendar_schedule(record)
        forged = replace(
            original, candidates=original.candidates[:-1]
        )

        with self.assertRaisesRegex(
            FedCalendarEvidenceAuditError, "does not match"
        ):
            audit_fed_calendar_source_version(
                record, forged, as_of=AS_OF
            )

    def test_23_forged_rollover_rejected(self):
        record = make_record()
        original = extract_fed_calendar_schedule(record)
        forged = replace(
            original,
            rollover_2027_end=(
                original.rollover_2027_end + timedelta(days=1)
            ),
        )

        with self.assertRaisesRegex(
            FedCalendarEvidenceAuditError, "does not match"
        ):
            audit_fed_calendar_source_version(
                record, forged, as_of=AS_OF
            )

    def test_24_mutated_acquisition_body_and_digest_rejected(self):
        record = make_record()
        original = extract_fed_calendar_schedule(record)
        wrong_body = html_document().replace(
            b"September 15", b"September 22"
        )
        modified = replace(
            record,
            entity_body=wrong_body,
            entity_body_sha256=sha256(wrong_body).hexdigest(),
        )

        with self.assertRaisesRegex(
            FedCalendarEvidenceAuditError, "canonical extraction"
        ):
            audit_fed_calendar_source_version(
                modified, original, as_of=AS_OF
            )

    def test_25_mismatched_original_body_sha256_rejected(self):
        record = make_record()
        original = extract_fed_calendar_schedule(record)
        forged = replace(
            record, entity_body_sha256="0" * 64
        )

        with self.assertRaisesRegex(
            FedCalendarEvidenceAuditError, "canonical extraction"
        ):
            audit_fed_calendar_source_version(
                forged, original, as_of=AS_OF
            )

    def test_26_invalid_asof_values_rejected(self):
        record = make_record()
        original = extract_fed_calendar_schedule(record)

        for bad in (
            None,
            "2026-10-14",
            AS_OF.replace(tzinfo=None),
        ):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(
                    FedCalendarEvidenceAuditError, "timezone-aware"
                ):
                    audit_fed_calendar_source_version(
                        record, original, as_of=bad
                    )

    def test_27_invalid_argument_types_rejected(self):
        record = make_record()
        original = extract_fed_calendar_schedule(record)

        with self.assertRaises(TypeError):
            audit_fed_calendar_source_version(
                object(), original, as_of=AS_OF
            )

        with self.assertRaises(TypeError):
            audit_fed_calendar_source_version(
                record, object(), as_of=AS_OF
            )

    def test_28_invalid_untrusted_header_type_rejected(self):
        record = make_record()
        forged = replace(record, content_etag=123)
        original = extract_fed_calendar_schedule(forged)

        with self.assertRaisesRegex(
            FedCalendarEvidenceAuditError, "HTTP metadata"
        ):
            audit_fed_calendar_source_version(
                forged, original, as_of=AS_OF
            )

    def test_29_injected_header_newline_rejected(self):
        record = make_record()
        forged = replace(
            record,
            content_last_modified="2024\nX-Claim: Certified",
        )
        original = extract_fed_calendar_schedule(forged)

        with self.assertRaisesRegex(
            FedCalendarEvidenceAuditError, "HTTP metadata"
        ):
            audit_fed_calendar_source_version(
                forged, original, as_of=AS_OF
            )

    def test_30_malformed_source_metadata_fails_closed(self):
        record = make_record()
        forged = replace(
            record, source_url="https://untrusted.invalid"
        )
        original = extract_fed_calendar_schedule(record)

        with self.assertRaisesRegex(
            FedCalendarEvidenceAuditError, "canonical extraction"
        ):
            audit_fed_calendar_source_version(
                forged, original, as_of=AS_OF
            )

    def test_31_receipt_does_not_expose_candidate_data_or_html(self):
        result = pipeline()

        for name in (
            "entity_body",
            "candidates",
            "calendar_snapshots",
            "economic_calendar",
            "context",
        ):
            self.assertFalse(hasattr(result, name))

    def test_32_require_delivery_and_replay_always_refuse(self):
        result = pipeline()

        for action in (
            result.require_council_calendar_delivery,
            result.require_historical_replay,
        ):
            with self.assertRaises(FedCalendarEvidenceNotReady):
                action()

    def test_33_forged_empty_blockers_cannot_grant_access(self):
        forged = replace(
            pipeline(),
            blockers=(),
            claimed_observation_not_after_as_of=True,
        )

        self.assertFalse(
            forged.council_calendar_admission_authorized
        )
        self.assertFalse(forged.historical_replay_authorized)

        with self.assertRaises(FedCalendarEvidenceNotReady):
            forged.require_council_calendar_delivery()

    def test_34_receipt_fields_are_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            pipeline().source_url = "changed"

    def test_35_in_memory_client_extractor_audit_integration(self):
        response = FakeResponse(body=html_document())
        times = iter((START, END))

        client = FedCalendarClient(
            opener=FakeOpener(response),
            clock=lambda: next(times),
        )

        acquired = client.fetch()
        extracted = extract_fed_calendar_schedule(acquired)

        report = audit_fed_calendar_source_version(
            acquired, extracted, as_of=AS_OF
        )

        self.assertEqual(
            report.reconciled_2026_meeting_count, 8
        )
        self.assertFalse(
            report.historical_source_version_attested
        )
        self.assertFalse(
            report.council_calendar_admission_authorized
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""F4.5.10: Offline, non-admitting Fed external artifact intake tests.

Run: python -m backend.macro.test_fed_calendar_artifact_intake
No live HTTP, TLS requests, DB, broker, Council registration, or HMM.
"""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from hashlib import sha256
import unittest

from backend.macro.fed_calendar_artifact_intake import (
    FedCalendarArtifactIntakeError,
    FedCalendarArtifactNotReady,
    FedCalendarExternalArtifactCapture,
    FedLocalArtifactBinding,
    MAX_EXTERNAL_ARTIFACT_BYTES,
    REQUIRED_ARTIFACT_PROOFS,
    inspect_fed_calendar_external_artifacts,
)
from backend.macro.fed_calendar_independent_evidence import (
    FedClaimedTimePrecision,
    FedExternalEvidenceKind,
    catalog_fed_calendar_independent_evidence,
)
from backend.macro.fed_calendar_protocol import FED_2026_SCHEDULE_URL
from backend.macro.test_fed_calendar_extractor import make_record
from backend.macro.test_fed_calendar_independent_evidence import (
    AS_OF,
    CAPTURE,
    claim,
    evidence_chain,
)


CAPTURE_START = datetime(2026, 10, 14, 12, 40, tzinfo=timezone.utc)
CAPTURE_END = CAPTURE_START + timedelta(seconds=1)


def artifact(ref=None, *, body=None, **changes):
    item = claim() if ref is None else ref
    actual = make_record().entity_body if body is None else body
    fields = dict(
        provider_id=item.provider_id,
        claim_id=item.claim_id,
        requested_url=item.external_artifact_url,
        response_url=item.external_artifact_url,
        http_status=200,
        request_started_at=CAPTURE_START,
        response_completed_at=CAPTURE_END,
        content_type=(
            "application/timestamp-reply"
            if item.kind is FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN
            else "text/html; charset=utf-8"
        ),
        entity_body=actual,
        entity_body_sha256=sha256(actual).hexdigest(),
        reported_memento_datetime=(
            format_datetime(CAPTURE, usegmt=True)
            if item.kind is FedExternalEvidenceKind.WEB_ARCHIVE_MEMENTO
            else None
        ),
        reported_original_resource_url=(
            FED_2026_SCHEDULE_URL
            if item.kind is FedExternalEvidenceKind.WEB_ARCHIVE_MEMENTO
            else None
        ),
    )
    fields.update(changes)
    return FedCalendarExternalArtifactCapture(**fields)


def source_inputs(refs=None, *, as_of=AS_OF):
    selected = (claim(),) if refs is None else tuple(refs)
    chain = evidence_chain(as_of=as_of)
    reviewed = catalog_fed_calendar_independent_evidence(
        *chain, selected, as_of=as_of
    )
    return (*chain, selected, reviewed)


def intake(refs=None, receipts=None, *, as_of=AS_OF, chain=None):
    if chain is None:
        chain = source_inputs(refs, as_of=as_of)
    selected = chain[4]
    present = (artifact(selected[0]),) if receipts is None and selected else (
        () if receipts is None else receipts
    )
    return inspect_fed_calendar_external_artifacts(
        *chain, present, as_of=as_of
    )


class FedCalendarArtifactIntakeTests(unittest.TestCase):

    def test_01_no_artifacts_is_still_a_blocked_report(self):
        result = intake(receipts=())
        self.assertEqual(result.expected_claim_count, 1)
        self.assertEqual(result.received_artifact_count, 0)
        self.assertIn("FED_EXTERNAL_ARTIFACT_BYTES_NOT_SUPPLIED", result.blockers)
        self.assertFalse(result.independent_source_version_verified)

    def test_02_memento_bytes_match_claim_only_locally(self):
        result = intake()
        finding = result.findings[0]
        self.assertEqual(result.received_artifact_count, 1)
        self.assertEqual(
            finding.local_document_binding,
            FedLocalArtifactBinding.MATCHES_CLAIMED_DOCUMENT_BYTES,
        )
        self.assertTrue(finding.local_artifact_matches_current_fed_capture)
        self.assertFalse(result.independent_source_version_verified)
        self.assertFalse(result.historical_replay_authorized)

    def test_03_opaque_timestamp_token_not_verified(self):
        ref = claim(kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN)
        result = intake((ref,), (artifact(ref, body=b"\x30\x03\x02\x01\x00"),))
        finding = result.findings[0]
        self.assertEqual(
            finding.local_document_binding,
            FedLocalArtifactBinding.OPAQUE_TOKEN_DOCUMENT_BINDING_UNVERIFIED,
        )
        self.assertIsNone(finding.local_artifact_matches_current_fed_capture)
        self.assertFalse(result.timestamp_signature_verified)

    def test_04_publisher_html_is_only_a_local_byte_comparison(self):
        ref = claim(kind=FedExternalEvidenceKind.PUBLISHER_ARCHIVE)
        result = intake((ref,), (artifact(ref),))
        self.assertEqual(result.received_artifact_count, 1)
        self.assertFalse(result.council_calendar_admission_authorized)

    def test_05_digest_binds_to_current_claim_catalog(self):
        chain = source_inputs()
        result = intake(chain=chain)
        self.assertEqual(
            result.canonical_external_review_sha256,
            chain[5].diagnostic_manifest_sha256,
        )
        self.assertRegex(result.diagnostic_manifest_sha256, r"^[0-9a-f]{64}$")

    def test_06_repeated_receipts_are_deterministic(self):
        self.assertEqual(
            intake().diagnostic_manifest_sha256,
            intake().diagnostic_manifest_sha256,
        )

    def test_07_equivalent_asof_timezone_preserves_manifest(self):
        other = AS_OF.astimezone(timezone(timedelta(hours=-5)))
        self.assertEqual(
            intake().diagnostic_manifest_sha256,
            intake(as_of=other).diagnostic_manifest_sha256,
        )

    def test_08_distinct_asof_changes_manifest(self):
        self.assertNotEqual(
            intake().diagnostic_manifest_sha256,
            intake(as_of=AS_OF + timedelta(seconds=1)).diagnostic_manifest_sha256,
        )

    def test_09_artifact_captured_after_asof_is_reported_not_admitted(self):
        earlier = CAPTURE_END - timedelta(seconds=1)
        result = intake(as_of=earlier)
        self.assertFalse(result.findings[0].claimed_capture_not_after_as_of)
        self.assertIn("FED_EXTERNAL_ARTIFACT_CAPTURE_POSTDATES_AS_OF", result.blockers)
        self.assertFalse(result.historical_replay_authorized)

    def test_10_old_replay_cannot_use_later_artifact(self):
        as_of = datetime(2025, 9, 17, tzinfo=timezone.utc)
        result = intake(as_of=as_of)
        self.assertIn("FED_EXTERNAL_ARTIFACT_CAPTURE_POSTDATES_AS_OF", result.blockers)
        self.assertFalse(result.council_calendar_admission_authorized)

    def test_11_artifact_matching_exact_observation_still_not_proven(self):
        result = intake(as_of=CAPTURE_END)
        self.assertTrue(result.findings[0].claimed_capture_not_after_as_of)
        self.assertFalse(result.timestamp_signature_verified)
        self.assertFalse(result.persistence_authorized)

    def test_12_different_archive_bytes_are_explicitly_reported(self):
        ref = claim()
        result = intake((ref,), (artifact(ref, body=b"<html>Archive rewrite</html>"),))
        self.assertEqual(
            result.findings[0].local_document_binding,
            FedLocalArtifactBinding.DIFFERS_FROM_CLAIMED_DOCUMENT_BYTES,
        )
        self.assertIn("FED_EXTERNAL_DOCUMENT_BYTES_DIFFER_FROM_CLAIM", result.blockers)
        self.assertFalse(result.independent_source_version_verified)

    def test_13_rehashing_changed_bytes_changes_receipt(self):
        ref = claim()
        before = intake((ref,), (artifact(ref),))
        after = intake((ref,), (artifact(ref, body=b"<html>different</html>"),))
        self.assertNotEqual(before.diagnostic_manifest_sha256, after.diagnostic_manifest_sha256)

    def test_14_date_only_claim_retains_intraday_ambiguity(self):
        ref = claim(
            claimed_time_precision=FedClaimedTimePrecision.UTC_DATE_ONLY,
            claimed_evidence_time=date(2026, 10, 14),
        )
        result = intake((ref,), (artifact(ref),))
        self.assertIn(
            "FED_EXTERNAL_MEMENTO_CLAIM_TIME_PRECISION_AMBIGUOUS", result.blockers
        )
        self.assertFalse(result.historical_replay_authorized)

    def test_15_memento_header_time_must_match_claimed_instant(self):
        ref = claim()
        response = artifact(
            ref,
            reported_memento_datetime="Wed, 14 Oct 2026 12:31:00 GMT",
        )
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "conflicts"):
            intake((ref,), (response,))

    def test_16_date_only_claim_header_mismatch_rejected(self):
        ref = claim(
            claimed_time_precision=FedClaimedTimePrecision.UTC_DATE_ONLY,
            claimed_evidence_time=date(2026, 10, 15),
        )
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "conflicts"):
            intake((ref,), (artifact(ref),))

    def test_17_bad_memento_http_date_rejected(self):
        ref = claim()
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "RFC 1123"):
            intake((ref,), (artifact(ref, reported_memento_datetime="2026-10-14"),))

    def test_18_wrong_rfc1123_weekday_rejected(self):
        ref = claim()
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "RFC 1123"):
            intake((ref,), (artifact(
                ref, reported_memento_datetime="Thu, 14 Oct 2026 12:30:00 GMT"
            ),))

    def test_19_memento_requires_original_fed_uri(self):
        ref = claim()
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "original URI"):
            intake((ref,), (artifact(ref, reported_original_resource_url="https://other.invalid"),))

    def test_20_memento_without_timestamp_rejected(self):
        ref = claim()
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "Memento-Datetime"):
            intake((ref,), (artifact(ref, reported_memento_datetime=None),))

    def test_21_redirected_response_cannot_claim_exact_reference(self):
        ref = claim()
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "redirected"):
            intake((ref,), (artifact(ref, response_url="https://other.invalid"),))

    def test_22_changed_request_url_rejected(self):
        ref = claim()
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "claimed URL"):
            intake((ref,), (artifact(ref, requested_url="https://other.invalid"),))

    def test_23_non200_response_rejected(self):
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "HTTP 200"):
            artifact(http_status=302)

    def test_24_memento_binary_content_type_rejected(self):
        ref = claim()
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "must be HTML"):
            intake((ref,), (artifact(ref, content_type="application/octet-stream"),))

    def test_25_publisher_archive_binary_content_type_rejected(self):
        ref = claim(kind=FedExternalEvidenceKind.PUBLISHER_ARCHIVE)
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "must be HTML"):
            intake((ref,), (artifact(ref, content_type="application/octet-stream"),))

    def test_26_timestamp_token_text_content_type_rejected(self):
        ref = claim(kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN)
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "binary content type"):
            intake((ref,), (artifact(ref, content_type="text/html"),))

    def test_27_timestamp_token_cannot_impersonate_memento_metadata(self):
        ref = claim(kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN)
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "Non-Memento"):
            intake((ref,), (artifact(ref, reported_memento_datetime="Wed, 14 Oct 2026 12:30:00 GMT"),))

    def test_28_publisher_archive_cannot_impersonate_memento_metadata(self):
        ref = claim(kind=FedExternalEvidenceKind.PUBLISHER_ARCHIVE)
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "Non-Memento"):
            intake((ref,), (artifact(ref, reported_original_resource_url=FED_2026_SCHEDULE_URL),))

    def test_29_unknown_artifact_identity_rejected(self):
        ref = claim()
        bogus = artifact(ref, claim_id="UNKNOWN")
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "no matching"):
            intake((ref,), (bogus,))

    def test_30_duplicate_artifact_identity_rejected(self):
        ref = claim()
        record = artifact(ref)
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "Duplicate"):
            intake((ref,), (record, record))

    def test_31_claim_review_replacement_rejected(self):
        chain = source_inputs()
        forged = replace(chain[5], blockers=())
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "differs"):
            intake(chain=(*chain[:5], forged))

    def test_32_tampered_fed_acquisition_rejected(self):
        chain = source_inputs()
        forged = replace(chain[0], entity_body_sha256="f" * 64)
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "revalidated"):
            intake(chain=(forged, *chain[1:]))

    def test_33_wrong_asof_for_review_rejected(self):
        chain = source_inputs()
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "revalidated"):
            intake(chain=chain, as_of=AS_OF + timedelta(seconds=1))

    def test_34_receipt_contains_no_html_raw_token_or_live_events(self):
        result = intake()
        for name in (
            "entity_body", "captured_bytes", "raw_artifact", "candidates",
            "context", "economic_calendar", "signature", "live_snapshots",
        ):
            self.assertFalse(hasattr(result, name))
            self.assertFalse(hasattr(result.findings[0], name))

    def test_35_all_delivery_replay_and_write_methods_refuse(self):
        result = intake()
        for fn in (
            result.require_council_calendar_delivery,
            result.require_historical_replay,
            result.require_persistence,
        ):
            with self.assertRaises(FedCalendarArtifactNotReady):
                fn()

    def test_36_forged_empty_blockers_cannot_grant_admission(self):
        forged = replace(intake(), blockers=(), required_independent_proofs=())
        self.assertFalse(forged.independent_source_version_verified)
        self.assertFalse(forged.timestamp_signature_verified)
        self.assertFalse(forged.council_calendar_admission_authorized)
        self.assertFalse(forged.historical_replay_authorized)
        self.assertFalse(forged.persistence_authorized)
        with self.assertRaises(FedCalendarArtifactNotReady):
            forged.require_historical_replay()

    def test_37_receipt_is_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            intake().source_url = "changed"

    def test_38_mutated_frozen_capture_revalidated_on_intake(self):
        rec = artifact()
        object.__setattr__(rec, "entity_body_sha256", "f" * 64)
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "SHA-256"):
            intake(receipts=(rec,))

    def test_39_null_and_wrong_type_capture_collection_rejected(self):
        chain = source_inputs()
        with self.assertRaises(TypeError):
            inspect_fed_calendar_external_artifacts(*chain, None, as_of=AS_OF)
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "Exact artifact"):
            intake(receipts=(object(),))

    def test_40_oversized_or_empty_artifact_rejected(self):
        for bad in (b"", b"x" * (MAX_EXTERNAL_ARTIFACT_BYTES + 1)):
            with self.subTest(size=len(bad)):
                with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "bounded bytes"):
                    artifact(body=bad)

    def test_41_naive_or_reversed_response_clock_rejected(self):
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "timezone aware"):
            artifact(response_completed_at=CAPTURE_END.replace(tzinfo=None))
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "before start"):
            artifact(response_completed_at=CAPTURE_START - timedelta(seconds=1))

    def test_42_forged_body_hash_rejected_before_receipt(self):
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "SHA-256"):
            artifact(entity_body_sha256="f" * 64)

    def test_43_bad_http_header_values_rejected(self):
        for bad in (None, "", "text/html\nX-Original: forged"):
            with self.subTest(value=bad):
                with self.assertRaises(FedCalendarArtifactIntakeError):
                    artifact(content_type=bad)

    def test_44_captures_reordering_does_not_change_claim_order_receipt(self):
        a = claim()
        b = claim(provider_id="ARCHIVE_B", claim_id="CAPTURE_002")
        x, y = artifact(a), artifact(b)
        first = intake((a, b), (x, y))
        second = intake((a, b), (y, x))
        self.assertEqual(first.diagnostic_manifest_sha256, second.diagnostic_manifest_sha256)
        self.assertEqual([i.provider_id for i in first.findings], ["ARCHIVE_A", "ARCHIVE_B"])

    def test_45_partial_artifact_collection_remains_blocked(self):
        a = claim()
        b = claim(provider_id="ARCHIVE_B", claim_id="CAPTURE_002")
        result = intake((a, b), (artifact(a),))
        self.assertEqual(result.expected_claim_count, 2)
        self.assertEqual(result.received_artifact_count, 1)
        self.assertIn("FED_EXTERNAL_ARTIFACT_CLAIMS_NOT_ALL_CAPTURED", result.blockers)

    def test_46_multiple_providers_do_not_prove_witness_independence(self):
        a = claim()
        b = claim(provider_id="ARCHIVE_B", claim_id="CAPTURE_002")
        result = intake((a, b), (artifact(a), artifact(b)))
        self.assertEqual(result.received_artifact_count, 2)
        self.assertFalse(result.independent_source_version_verified)
        self.assertIn("FED_EXTERNAL_ARCHIVE_CUSTODY_UNVERIFIED", result.blockers)

    def test_47_review_cannot_be_replaced_by_wrong_type(self):
        chain = source_inputs()
        with self.assertRaises(TypeError):
            intake(chain=(*chain[:5], object()))

    def test_48_required_external_proofs_still_all_present(self):
        result = intake()
        self.assertEqual(result.required_independent_proofs, REQUIRED_ARTIFACT_PROOFS)
        self.assertEqual(len(result.required_independent_proofs), 6)
        self.assertIn("FED_EXTERNAL_TIMESTAMP_SIGNATURE_AND_IMPRINT_UNVERIFIED", result.blockers)

    def test_49_artifact_limit_enforced(self):
        ref = claim()
        many = [artifact(ref)] * 33
        with self.assertRaisesRegex(FedCalendarArtifactIntakeError, "limit exceeded"):
            intake((ref,), many)

    def test_50_new_metadata_cannot_be_claimed_crypto_certificate(self):
        result = intake()
        self.assertFalse(result.timestamp_signature_verified)
        self.assertFalse(result.independent_source_version_verified)
        self.assertEqual(result.findings[0].claimed_memento_state_at, CAPTURE)


if __name__ == "__main__":
    unittest.main(verbosity=2)

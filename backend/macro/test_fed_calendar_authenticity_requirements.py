"""F4.5.11 deterministic, offline Fed artifact-authenticity requirements tests.

Run: python -m backend.macro.test_fed_calendar_authenticity_requirements
No network, cryptographic trust assertion, Council registration, DB, HMM or broker.
"""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from itertools import repeat
import unittest

from backend.macro.fed_calendar_artifact_intake import (
    FedCalendarExternalArtifactCapture,
    inspect_fed_calendar_external_artifacts,
)
from backend.macro.fed_calendar_authenticity_requirements import (
    ALWAYS_UNVERIFIED,
    COMMON_CHECKS,
    MEMENTO_CHECKS,
    PUBLISHER_CHECKS,
    TIMESTAMP_CHECKS,
    FedCalendarAuthenticityNotReady,
    FedCalendarAuthenticityRequirementsError,
    FedVerificationCheckState,
    build_fed_calendar_authenticity_requirements,
)
from backend.macro.fed_calendar_independent_evidence import (
    FedClaimedTimePrecision,
    FedExternalEvidenceKind,
    MAX_EXTERNAL_EVIDENCE_CLAIMS,
)
from backend.macro.test_fed_calendar_artifact_intake import (
    AS_OF,
    CAPTURE_END,
    artifact,
    source_inputs,
)
from backend.macro.test_fed_calendar_independent_evidence import claim


def setup(refs=None, captures=None, *, as_of=AS_OF):
    selected = (claim(),) if refs is None else tuple(refs)
    chain = source_inputs(selected, as_of=as_of)
    received = (
        (artifact(selected[0]),)
        if captures is None and selected
        else (() if captures is None else tuple(captures))
    )
    inspected = inspect_fed_calendar_external_artifacts(
        *chain, received, as_of=as_of
    )
    return chain, received, inspected


def plan(refs=None, captures=None, *, as_of=AS_OF, prepared=None):
    if prepared is None:
        prepared = setup(refs, captures, as_of=as_of)
    chain, received, inspected = prepared
    return build_fed_calendar_authenticity_requirements(
        *chain, received, inspected, as_of=as_of
    )


def check(target, code):
    return next(item.state for item in target.checks if item.check_code == code)


class FedCalendarAuthenticityRequirementsTests(unittest.TestCase):

    def test_01_memento_bytes_locally_reconciled(self):
        result = plan()
        self.assertEqual(result.supplied_artifact_count, 1)
        self.assertEqual(
            check(result.verification_targets[0], COMMON_CHECKS[0]),
            FedVerificationCheckState.LOCAL_BYTES_RECONCILED,
        )

    def test_02_memento_origin_and_custody_require_independent_proof(self):
        result = plan()
        self.assertEqual(
            check(result.verification_targets[0], COMMON_CHECKS[1]),
            FedVerificationCheckState.INDEPENDENT_VERIFICATION_REQUIRED,
        )
        self.assertFalse(result.archival_custody_authenticated)

    def test_03_reported_memento_link_is_not_authenticated(self):
        result = plan()
        self.assertEqual(
            check(result.verification_targets[0], MEMENTO_CHECKS[0]),
            FedVerificationCheckState.INDEPENDENT_VERIFICATION_REQUIRED,
        )

    def test_04_reported_memento_datetime_is_not_authenticated(self):
        result = plan()
        self.assertEqual(
            check(result.verification_targets[0], MEMENTO_CHECKS[1]),
            FedVerificationCheckState.INDEPENDENT_VERIFICATION_REQUIRED,
        )

    def test_05_historical_schedule_requires_separate_review(self):
        result = plan()
        self.assertEqual(
            check(result.verification_targets[0], MEMENTO_CHECKS[2]),
            FedVerificationCheckState.INDEPENDENT_VERIFICATION_REQUIRED,
        )

    def test_06_claimed_time_before_asof_is_not_proof(self):
        result = plan()
        self.assertEqual(
            check(result.verification_targets[0], COMMON_CHECKS[2]),
            FedVerificationCheckState.CLAIM_ONLY,
        )
        self.assertFalse(result.historical_source_version_attested)

    def test_07_after_asof_capture_is_temporal_conflict(self):
        result = plan(as_of=CAPTURE_END - timedelta(seconds=1))
        self.assertEqual(
            check(result.verification_targets[0], COMMON_CHECKS[2]),
            FedVerificationCheckState.LOCAL_CONFLICT,
        )
        self.assertIn("FED_AUTHENTICITY_TEMPORAL_CLAIM_CONFLICT", result.blockers)

    def test_08_past_replay_cannot_rely_on_later_capture(self):
        result = plan(as_of=datetime(2025, 9, 17, tzinfo=timezone.utc))
        self.assertEqual(
            check(result.verification_targets[0], COMMON_CHECKS[2]),
            FedVerificationCheckState.LOCAL_CONFLICT,
        )
        self.assertFalse(result.historical_replay_authorized)

    def test_09_future_claim_is_temporal_conflict_even_with_early_capture(self):
        ref = claim(claimed_evidence_time=AS_OF + timedelta(hours=1))
        result = plan((ref,), (artifact(ref, reported_memento_datetime="Wed, 14 Oct 2026 14:00:00 GMT"),))
        self.assertEqual(
            check(result.verification_targets[0], COMMON_CHECKS[2]),
            FedVerificationCheckState.LOCAL_CONFLICT,
        )

    def test_10_date_only_claim_is_still_not_independently_precise(self):
        ref = claim(
            claimed_time_precision=FedClaimedTimePrecision.UTC_DATE_ONLY,
            claimed_evidence_time=date(2026, 10, 14),
        )
        result = plan((ref,), (artifact(ref),))
        self.assertEqual(
            check(result.verification_targets[0], COMMON_CHECKS[2]),
            FedVerificationCheckState.CLAIM_ONLY,
        )
        self.assertIn("FED_EXTERNAL_MEMENTO_CLAIM_TIME_PRECISION_AMBIGUOUS", result.blockers)

    def test_11_changed_archive_bytes_are_local_conflict(self):
        ref = claim()
        result = plan((ref,), (artifact(ref, body=b"<html>different</html>"),))
        self.assertEqual(
            check(result.verification_targets[0], COMMON_CHECKS[3]),
            FedVerificationCheckState.LOCAL_CONFLICT,
        )
        self.assertIn("FED_AUTHENTICITY_LOCAL_DOCUMENT_DIGEST_CONFLICT", result.blockers)

    def test_12_locally_matching_schedule_is_still_not_historical_proof(self):
        result = plan()
        self.assertEqual(
            check(result.verification_targets[0], COMMON_CHECKS[3]),
            FedVerificationCheckState.INDEPENDENT_VERIFICATION_REQUIRED,
        )
        self.assertFalse(result.historical_source_version_attested)

    def test_13_no_claims_returns_blocked_empty_report(self):
        result = plan((), ())
        self.assertEqual(result.expected_claim_count, 0)
        self.assertEqual(result.verification_targets, ())
        self.assertFalse(result.council_calendar_admission_authorized)
        for code in ALWAYS_UNVERIFIED:
            self.assertIn(code, result.blockers)

    def test_14_reference_with_no_artifact_has_missing_byte_check(self):
        result = plan(captures=())
        self.assertEqual(
            check(result.verification_targets[0], COMMON_CHECKS[0]),
            FedVerificationCheckState.ARTIFACT_MISSING,
        )
        self.assertIsNone(result.verification_targets[0].artifact_entity_sha256)
        self.assertIn("FED_AUTHENTICITY_REFERENCED_ARTIFACT_NOT_SUPPLIED", result.blockers)

    def test_15_reference_without_artifact_does_not_prove_link(self):
        result = plan(captures=())
        self.assertEqual(
            check(result.verification_targets[0], MEMENTO_CHECKS[0]),
            FedVerificationCheckState.ARTIFACT_MISSING,
        )

    def test_16_publisher_evidence_requires_origin_and_version_proof(self):
        ref = claim(kind=FedExternalEvidenceKind.PUBLISHER_ARCHIVE)
        result = plan((ref,), (artifact(ref),))
        self.assertEqual(
            [c.check_code for c in result.verification_targets[0].checks][-3:],
            list(PUBLISHER_CHECKS),
        )
        self.assertFalse(result.publisher_origin_authenticated)

    def test_17_publisher_archive_same_body_not_historical_certification(self):
        ref = claim(kind=FedExternalEvidenceKind.PUBLISHER_ARCHIVE)
        result = plan((ref,), (artifact(ref),))
        self.assertEqual(
            check(result.verification_targets[0], COMMON_CHECKS[0]),
            FedVerificationCheckState.LOCAL_BYTES_RECONCILED,
        )
        self.assertFalse(result.historical_source_version_attested)

    def test_18_timestamp_token_does_not_self_prove_message_imprint(self):
        ref = claim(kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN)
        result = plan((ref,), (artifact(ref, body=b"\x30\x03\x02\x01\x00"),))
        self.assertEqual(
            check(result.verification_targets[0], TIMESTAMP_CHECKS[2]),
            FedVerificationCheckState.INDEPENDENT_VERIFICATION_REQUIRED,
        )
        self.assertFalse(result.rfc3161_signature_verified)

    def test_19_timestamp_crypto_and_tsa_checks_all_pending(self):
        ref = claim(kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN)
        result = plan((ref,), (artifact(ref, body=b"opaque CMS"),))
        target = result.verification_targets[0]
        for code in TIMESTAMP_CHECKS[:-1]:
            self.assertEqual(
                check(target, code),
                FedVerificationCheckState.INDEPENDENT_VERIFICATION_REQUIRED,
            )

    def test_20_timestamp_requires_separately_supplied_document_bytes(self):
        ref = claim(kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN)
        result = plan((ref,), (artifact(ref, body=b"opaque"),))
        self.assertEqual(
            check(result.verification_targets[0], TIMESTAMP_CHECKS[-1]),
            FedVerificationCheckState.ARTIFACT_MISSING,
        )
        self.assertIn("FED_AUTHENTICITY_TOKEN_DOCUMENT_BYTES_NOT_BOUND", result.blockers)

    def test_21_token_document_candidate_from_distinct_memento_bytes(self):
        web = claim()
        token = claim(
            provider_id="TSA_A", claim_id="TS_001",
            kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN,
        )
        result = plan((web, token), (artifact(web), artifact(token, body=b"opaque")))
        self.assertEqual(
            check(result.verification_targets[1], TIMESTAMP_CHECKS[-1]),
            FedVerificationCheckState.LOCAL_BYTES_RECONCILED,
        )
        self.assertFalse(result.rfc3161_signature_verified)

    def test_22_token_document_candidate_from_distinct_publisher_archive(self):
        pub = claim(kind=FedExternalEvidenceKind.PUBLISHER_ARCHIVE)
        token = claim(
            provider_id="TSA_B", claim_id="TS_002",
            kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN,
        )
        result = plan((pub, token), (artifact(pub), artifact(token, body=b"token")))
        self.assertEqual(
            check(result.verification_targets[1], TIMESTAMP_CHECKS[-1]),
            FedVerificationCheckState.LOCAL_BYTES_RECONCILED,
        )

    def test_23_bad_archive_document_cannot_support_token_candidate(self):
        web = claim()
        token = claim(
            provider_id="TSA_A", claim_id="TS_003",
            kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN,
        )
        result = plan((web, token), (
            artifact(web, body=b"<html>mutated</html>"),
            artifact(token, body=b"token"),
        ))
        self.assertEqual(
            check(result.verification_targets[1], TIMESTAMP_CHECKS[-1]),
            FedVerificationCheckState.ARTIFACT_MISSING,
        )

    def test_24_unrelated_document_digest_cannot_support_token(self):
        web = claim()
        token = claim(
            provider_id="TSA_A", claim_id="TS_004",
            kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN,
            claimed_document_body_sha256="f" * 64,
        )
        result = plan((web, token), (artifact(web), artifact(token, body=b"token")))
        self.assertEqual(
            check(result.verification_targets[1], TIMESTAMP_CHECKS[-1]),
            FedVerificationCheckState.ARTIFACT_MISSING,
        )

    def test_25_token_body_digest_does_not_count_as_document_digest(self):
        raw = b"opaque token"
        token = claim(
            kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN,
            claimed_document_body_sha256=sha256(raw).hexdigest(),
        )
        result = plan((token,), (artifact(token, body=raw),))
        self.assertEqual(
            check(result.verification_targets[0], TIMESTAMP_CHECKS[-1]),
            FedVerificationCheckState.ARTIFACT_MISSING,
        )

    def test_26_absent_token_with_document_still_not_crypto_verified(self):
        web = claim()
        token = claim(
            provider_id="TSA_A", claim_id="TS_005",
            kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN,
        )
        result = plan((web, token), (artifact(web),))
        self.assertEqual(
            check(result.verification_targets[1], TIMESTAMP_CHECKS[0]),
            FedVerificationCheckState.ARTIFACT_MISSING,
        )
        self.assertFalse(result.rfc3161_signature_verified)

    def test_27_same_provider_claims_do_not_prove_independence(self):
        a = claim()
        b = claim(claim_id="CAPTURE_002")
        result = plan((a, b), (artifact(a), artifact(b)))
        self.assertEqual(result.expected_claim_count, 2)
        self.assertFalse(result.archival_custody_authenticated)

    def test_28_multi_provider_claims_do_not_prove_independence(self):
        a = claim()
        b = claim(provider_id="ARCHIVE_B", claim_id="CAPTURE_002")
        result = plan((a, b), (artifact(a), artifact(b)))
        self.assertFalse(result.publisher_origin_authenticated)
        self.assertFalse(result.historical_source_version_attested)

    def test_29_separate_admission_is_never_satisfied(self):
        result = plan()
        self.assertEqual(
            check(result.verification_targets[0], COMMON_CHECKS[4]),
            FedVerificationCheckState.INDEPENDENT_VERIFICATION_REQUIRED,
        )
        self.assertFalse(result.persistence_authorized)

    def test_30_receipt_binds_to_canonical_intake_hash(self):
        prepared = setup()
        result = plan(prepared=prepared)
        self.assertEqual(
            result.canonical_intake_sha256,
            prepared[2].diagnostic_manifest_sha256,
        )
        self.assertRegex(result.diagnostic_manifest_sha256, r"^[0-9a-f]{64}$")

    def test_31_repeatability(self):
        self.assertEqual(
            plan().diagnostic_manifest_sha256,
            plan().diagnostic_manifest_sha256,
        )

    def test_32_equivalent_timezone_asof_is_stable(self):
        local = AS_OF.astimezone(timezone(timedelta(hours=-5)))
        self.assertEqual(
            plan().diagnostic_manifest_sha256,
            plan(as_of=local).diagnostic_manifest_sha256,
        )

    def test_33_different_asof_changes_manifest(self):
        self.assertNotEqual(
            plan().diagnostic_manifest_sha256,
            plan(as_of=AS_OF + timedelta(seconds=1)).diagnostic_manifest_sha256,
        )

    def test_34_changed_artifact_bytes_change_manifest(self):
        ref = claim()
        a = plan((ref,), (artifact(ref),))
        b = plan((ref,), (artifact(ref, body=b"<html>modified</html>"),))
        self.assertNotEqual(a.diagnostic_manifest_sha256, b.diagnostic_manifest_sha256)

    def test_35_capture_order_independent_of_input_order(self):
        a = claim()
        b = claim(provider_id="ARCHIVE_B", claim_id="CAPTURE_002")
        first = plan((a, b), (artifact(a), artifact(b)))
        second = plan((a, b), (artifact(b), artifact(a)))
        self.assertEqual(first.diagnostic_manifest_sha256, second.diagnostic_manifest_sha256)

    def test_36_claim_order_is_manifest_order(self):
        a = claim()
        b = claim(provider_id="ARCHIVE_B", claim_id="CAPTURE_002")
        first = plan((a, b), (artifact(a), artifact(b)))
        second = plan((b, a), (artifact(a), artifact(b)))
        self.assertNotEqual(first.diagnostic_manifest_sha256, second.diagnostic_manifest_sha256)

    def test_37_forged_intake_blockers_are_rejected(self):
        chain, captures, inspected = setup()
        forged = replace(inspected, blockers=())
        with self.assertRaisesRegex(
            FedCalendarAuthenticityRequirementsError, "differs"
        ):
            plan(prepared=(chain, captures, forged))

    def test_38_forged_intake_digest_is_rejected(self):
        chain, captures, inspected = setup()
        forged = replace(inspected, diagnostic_manifest_sha256="0" * 64)
        with self.assertRaisesRegex(
            FedCalendarAuthenticityRequirementsError, "differs"
        ):
            plan(prepared=(chain, captures, forged))

    def test_39_forged_external_review_is_rejected(self):
        chain, captures, inspected = setup()
        forged_review = replace(chain[5], blockers=())
        with self.assertRaisesRegex(
            FedCalendarAuthenticityRequirementsError, "revalidated"
        ):
            plan(prepared=((*chain[:5], forged_review), captures, inspected))

    def test_40_forged_capture_digest_is_rejected(self):
        chain, captures, inspected = setup()
        forged_capture = captures[0]
        object.__setattr__(forged_capture, "entity_body_sha256", "f" * 64)
        with self.assertRaisesRegex(
            FedCalendarAuthenticityRequirementsError, "revalidated"
        ):
            plan(prepared=(chain, (forged_capture,), inspected))

    def test_41_tampered_original_fed_body_is_rejected(self):
        chain, captures, inspected = setup()
        forged_acquisition = replace(chain[0], entity_body_sha256="0" * 64)
        with self.assertRaisesRegex(
            FedCalendarAuthenticityRequirementsError, "revalidated"
        ):
            plan(prepared=((forged_acquisition, *chain[1:]), captures, inspected))

    def test_42_wrong_asof_requires_fresh_upstream_chain(self):
        prepared = setup()
        with self.assertRaisesRegex(
            FedCalendarAuthenticityRequirementsError, "revalidated"
        ):
            plan(as_of=AS_OF + timedelta(seconds=1), prepared=prepared)

    def test_43_invalid_asof_rejected(self):
        prepared = setup()
        for bad in (None, "2026-10-14", AS_OF.replace(tzinfo=None)):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(
                    FedCalendarAuthenticityRequirementsError, "timezone-aware"
                ):
                    plan(as_of=bad, prepared=prepared)

    def test_44_bad_report_type_rejected(self):
        chain, captures, inspected = setup()
        with self.assertRaises(TypeError):
            plan(prepared=(chain, captures, object()))

    def test_45_none_claims_or_captures_rejected(self):
        chain, captures, inspected = setup()
        with self.assertRaises(TypeError):
            plan(prepared=((chain[:4] + (None, chain[5])), captures, inspected))
        with self.assertRaises(TypeError):
            plan(prepared=(chain, None, inspected))

    def test_46_oversized_claim_collection_rejected(self):
        chain, captures, inspected = setup()
        with self.assertRaisesRegex(
            FedCalendarAuthenticityRequirementsError, "limit exceeded"
        ):
            plan(prepared=(
                (*chain[:4], repeat(claim(), MAX_EXTERNAL_EVIDENCE_CLAIMS + 1), chain[5]),
                captures,
                inspected,
            ))

    def test_47_generator_inputs_are_materialized_once(self):
        chain, captures, inspected = setup()
        reconstituted = (
            *chain[:4], iter(chain[4]), chain[5]
        )
        result = plan(prepared=(reconstituted, iter(captures), inspected))
        self.assertEqual(result.expected_claim_count, 1)
        self.assertEqual(result.supplied_artifact_count, 1)

    def test_48_no_exposure_of_html_tokens_or_urls(self):
        result = plan()
        for field in (
            "entity_body", "raw_token", "external_artifact_url",
            "calendar_snapshots", "context", "source_certificates",
        ):
            self.assertFalse(hasattr(result, field))
            self.assertFalse(hasattr(result.verification_targets[0], field))

    def test_49_check_codes_are_unique_and_kind_specific(self):
        memento = plan().verification_targets[0]
        codes = [item.check_code for item in memento.checks]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual(codes[:5], list(COMMON_CHECKS))
        self.assertEqual(codes[5:], list(MEMENTO_CHECKS))

    def test_50_all_production_delivery_methods_refuse(self):
        result = plan()
        for method in (
            result.require_council_calendar_delivery,
            result.require_historical_replay,
            result.require_persistence,
        ):
            with self.assertRaises(FedCalendarAuthenticityNotReady):
                method()

    def test_51_forged_empty_blockers_cannot_enable_admission(self):
        forged = replace(plan(), blockers=(), verification_targets=())
        for flag in (
            forged.publisher_origin_authenticated,
            forged.archival_custody_authenticated,
            forged.rfc3161_signature_verified,
            forged.historical_source_version_attested,
            forged.council_calendar_admission_authorized,
            forged.historical_replay_authorized,
            forged.persistence_authorized,
        ):
            self.assertFalse(flag)
        with self.assertRaises(FedCalendarAuthenticityNotReady):
            forged.require_historical_replay()

    def test_52_report_is_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            plan().source_url = "forged"


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""F4.5.12 deterministic offline Fed calendar RFC 3161 prerequisite tests.

Run: python -m backend.macro.test_fed_calendar_crypto_prerequisites
No ASN.1/CMS verification, HTTP, DB, Council, broker, or reserved HMM.
"""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from itertools import repeat
import unittest

from backend.macro.fed_calendar_artifact_intake import inspect_fed_calendar_external_artifacts
from backend.macro.fed_calendar_authenticity_requirements import (
    build_fed_calendar_authenticity_requirements,
)
from backend.macro.fed_calendar_crypto_prerequisites import (
    FedCalendarCryptoPrerequisitesReport,
    FedCryptoCheckState,
    FedCryptoPrerequisitesError,
    FedCryptoPrerequisitesNotReady,
    FedRfc3161VerifierInputClaim,
    MAX_VERIFIER_INPUTS,
    RFC3161_PREREQUISITE_CHECKS,
    SHA256_OID,
    TIMESTAMPING_EKU_OID,
    TSTINFO_CONTENT_TYPE_OID,
    assess_fed_calendar_crypto_prerequisites,
)
from backend.macro.fed_calendar_independent_evidence import FedExternalEvidenceKind
from backend.macro.test_fed_calendar_artifact_intake import (
    AS_OF, CAPTURE_END, artifact, source_inputs,
)
from backend.macro.test_fed_calendar_independent_evidence import claim


ROOT = "a" * 64
REQUEST = "b" * 64
REVOCATION = "c" * 64
POLICY = "1.2.840.113549.1.9.16.1.4"
TOKEN_BYTES = b"\x30\x03\x02\x01\x00"  # Opaque, deliberately NOT a valid signed token.


def references():
    web = claim()
    token = claim(
        provider_id="TSA_A", claim_id="TOKEN_001",
        kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN,
    )
    return web, token


def verifier_input(token=None, document=None, **changes):
    web, default_token = references()
    token = default_token if token is None else token
    document = web if document is None else document
    fields = dict(
        provider_id=token.provider_id,
        claim_id=token.claim_id,
        document_provider_id=document.provider_id,
        document_claim_id=document.claim_id,
        claimed_tsa_trust_anchor_sha256=ROOT,
        expected_tsa_policy_oid=POLICY,
        claimed_request_nonce=123456789,
        claimed_request_der_sha256=REQUEST,
        claimed_revocation_evidence_sha256=REVOCATION,
    )
    fields.update(changes)
    return FedRfc3161VerifierInputClaim(**fields)


def build_chain(refs=None, captures=None, *, as_of=AS_OF):
    refs = references() if refs is None else tuple(refs)
    chain = source_inputs(refs, as_of=as_of)
    if captures is None:
        captures = tuple(
            artifact(ref, body=TOKEN_BYTES)
            if ref.kind is FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN
            else artifact(ref)
            for ref in refs
        )
    captures = tuple(captures)
    intake = inspect_fed_calendar_external_artifacts(
        *chain, captures, as_of=as_of,
    )
    authenticity = build_fed_calendar_authenticity_requirements(
        *chain, captures, intake, as_of=as_of,
    )
    return chain, captures, intake, authenticity


def assess(inputs=None, *, refs=None, captures=None, as_of=AS_OF, prepared=None):
    if prepared is None:
        prepared = build_chain(refs, captures, as_of=as_of)
    chain, supplied, intake, authentic = prepared
    if inputs is None:
        inputs = (verifier_input(),)
    return assess_fed_calendar_crypto_prerequisites(
        *chain, supplied, intake, authentic, inputs, as_of=as_of,
    )


def check(target, code):
    return next(value.state for value in target.checks if value.code == code)


class FedCalendarCryptoPrerequisitesTests(unittest.TestCase):

    def test_01_one_token_target_and_upstream_binding(self):
        prepared = build_chain()
        result = assess(prepared=prepared)
        self.assertIsInstance(result, FedCalendarCryptoPrerequisitesReport)
        self.assertEqual(result.timestamp_claim_count, 1)
        self.assertEqual(result.supplied_verifier_input_count, 1)
        self.assertEqual(
            result.canonical_authenticity_sha256,
            prepared[3].diagnostic_manifest_sha256,
        )

    def test_02_all_council_and_historical_authorizations_false(self):
        result = assess()
        self.assertFalse(result.rfc3161_signature_verified)
        self.assertFalse(result.tsa_certificate_chain_verified)
        self.assertFalse(result.historical_capture_time_attested)
        self.assertFalse(result.historical_source_version_attested)
        self.assertFalse(result.council_calendar_admission_authorized)
        self.assertFalse(result.historical_replay_authorized)
        self.assertFalse(result.persistence_authorized)

    def test_03_exact_eleven_unique_check_codes(self):
        codes = [step.code for step in assess().targets[0].checks]
        self.assertEqual(tuple(codes), RFC3161_PREREQUISITE_CHECKS)
        self.assertEqual(len(codes), len(set(codes)))

    def test_04_captured_token_body_is_only_candidate(self):
        target = assess().targets[0]
        self.assertEqual(target.token_body_sha256, sha256(TOKEN_BYTES).hexdigest())
        self.assertEqual(check(target, RFC3161_PREREQUISITE_CHECKS[0]),
                         FedCryptoCheckState.LOCAL_CANDIDATE_ONLY)

    def test_05_distinct_document_sha_is_a_candidate_not_verified_imprint(self):
        target = assess().targets[0]
        self.assertEqual(target.locally_bound_document_sha256,
                         references()[0].claimed_document_body_sha256)
        self.assertEqual(check(target, RFC3161_PREREQUISITE_CHECKS[1]),
                         FedCryptoCheckState.LOCAL_CANDIDATE_ONLY)
        self.assertEqual(check(target, RFC3161_PREREQUISITE_CHECKS[8]),
                         FedCryptoCheckState.INDEPENDENT_VERIFICATION_REQUIRED)

    def test_06_complete_claimed_material_does_not_verify_signature(self):
        result = assess()
        for code in RFC3161_PREREQUISITE_CHECKS[6:10]:
            self.assertEqual(check(result.targets[0], code),
                             FedCryptoCheckState.INDEPENDENT_VERIFICATION_REQUIRED)
        self.assertIn("FED_CRYPTO_RFC3161_CMS_SIGNATURE_NOT_VERIFIED", result.blockers)

    def test_07_anchor_hash_is_only_untrusted_claim(self):
        self.assertEqual(check(assess().targets[0], RFC3161_PREREQUISITE_CHECKS[2]),
                         FedCryptoCheckState.CLAIM_ONLY)

    def test_08_missing_anchor_reference_is_reported(self):
        item = replace(verifier_input(), claimed_tsa_trust_anchor_sha256=None)
        result = assess((item,))
        self.assertEqual(check(result.targets[0], RFC3161_PREREQUISITE_CHECKS[2]),
                         FedCryptoCheckState.MISSING)
        self.assertIn("FED_CRYPTO_TSA_TRUST_ANCHOR_REFERENCE_MISSING", result.blockers)

    def test_09_claimed_tsa_policy_is_not_signed_policy_verification(self):
        target = assess().targets[0]
        self.assertEqual(check(target, RFC3161_PREREQUISITE_CHECKS[3]),
                         FedCryptoCheckState.CLAIM_ONLY)
        self.assertFalse(assess().rfc3161_signature_verified)

    def test_10_missing_expected_policy_is_reported(self):
        result = assess((replace(verifier_input(), expected_tsa_policy_oid=None),))
        self.assertEqual(check(result.targets[0], RFC3161_PREREQUISITE_CHECKS[3]),
                         FedCryptoCheckState.MISSING)
        self.assertIn("FED_CRYPTO_TSA_POLICY_REFERENCE_MISSING", result.blockers)

    def test_11_nonce_and_request_digest_are_unverified_claims(self):
        self.assertEqual(check(assess().targets[0], RFC3161_PREREQUISITE_CHECKS[4]),
                         FedCryptoCheckState.CLAIM_ONLY)

    def test_12_nonce_without_request_digest_insufficient(self):
        result = assess((replace(verifier_input(), claimed_request_der_sha256=None),))
        self.assertEqual(check(result.targets[0], RFC3161_PREREQUISITE_CHECKS[4]),
                         FedCryptoCheckState.MISSING)

    def test_13_request_digest_without_nonce_insufficient(self):
        result = assess((replace(verifier_input(), claimed_request_nonce=None),))
        self.assertEqual(check(result.targets[0], RFC3161_PREREQUISITE_CHECKS[4]),
                         FedCryptoCheckState.MISSING)

    def test_14_claimed_reference_time_without_nonce_still_unverified(self):
        value = replace(verifier_input(), claimed_request_nonce=None,
                        claimed_request_der_sha256=None,
                        claimed_external_reference_at=AS_OF - timedelta(days=1))
        result = assess((value,))
        self.assertEqual(check(result.targets[0], RFC3161_PREREQUISITE_CHECKS[4]),
                         FedCryptoCheckState.CLAIM_ONLY)
        self.assertFalse(result.historical_capture_time_attested)

    def test_15_future_reference_clock_claim_is_conflict(self):
        ref = replace(verifier_input(),
                      claimed_external_reference_at=AS_OF + timedelta(seconds=1))
        result = assess((ref,))
        self.assertEqual(check(result.targets[0], RFC3161_PREREQUISITE_CHECKS[4]),
                         FedCryptoCheckState.CONFLICT)
        self.assertIn("FED_CRYPTO_FUTURE_REFERENCE_TIME_CLAIM", result.blockers)

    def test_16_archived_capture_after_asof_is_conflict(self):
        clock = CAPTURE_END - timedelta(seconds=1)
        result = assess(as_of=clock)
        self.assertEqual(check(result.targets[0], RFC3161_PREREQUISITE_CHECKS[9]),
                         FedCryptoCheckState.CONFLICT)
        self.assertFalse(result.historical_replay_authorized)

    def test_17_revocation_reference_is_not_validated_revocation(self):
        self.assertEqual(check(assess().targets[0], RFC3161_PREREQUISITE_CHECKS[5]),
                         FedCryptoCheckState.CLAIM_ONLY)
        self.assertFalse(assess().tsa_certificate_chain_verified)

    def test_18_missing_revocation_reference_is_reported(self):
        result = assess((replace(verifier_input(),
                                 claimed_revocation_evidence_sha256=None),))
        self.assertEqual(check(result.targets[0], RFC3161_PREREQUISITE_CHECKS[5]),
                         FedCryptoCheckState.MISSING)
        self.assertIn("FED_CRYPTO_REVOCATION_EVIDENCE_REFERENCE_MISSING", result.blockers)

    def test_19_missing_token_body_does_not_parse_or_approve(self):
        web, token = references()
        result = assess(refs=(web, token), captures=(artifact(web),))
        self.assertEqual(check(result.targets[0], RFC3161_PREREQUISITE_CHECKS[0]),
                         FedCryptoCheckState.MISSING)
        self.assertIn("FED_CRYPTO_TIMESTAMP_TOKEN_BYTES_MISSING", result.blockers)

    def test_20_missing_document_body_is_not_candidate(self):
        web, token = references()
        result = assess(refs=(web, token),
                        captures=(artifact(token, body=TOKEN_BYTES),))
        self.assertEqual(check(result.targets[0], RFC3161_PREREQUISITE_CHECKS[1]),
                         FedCryptoCheckState.MISSING)

    def test_21_mismatched_archive_bytes_are_conflict(self):
        web, token = references()
        result = assess(refs=(web, token),
                        captures=(artifact(web, body=b"<html>changed</html>"),
                                  artifact(token, body=TOKEN_BYTES)))
        self.assertEqual(check(result.targets[0], RFC3161_PREREQUISITE_CHECKS[1]),
                         FedCryptoCheckState.CONFLICT)
        self.assertIn("FED_CRYPTO_LOCAL_DOCUMENT_DIGEST_CONFLICT", result.blockers)

    def test_22_wrong_token_claimed_document_digest_is_conflict(self):
        web, token = references()
        changed_token = replace(token, claimed_document_body_sha256="f" * 64)
        binding = verifier_input(token=changed_token, document=web)
        result = assess((binding,), refs=(web, changed_token),
                        captures=(artifact(web), artifact(changed_token, body=TOKEN_BYTES)))
        self.assertEqual(check(result.targets[0], RFC3161_PREREQUISITE_CHECKS[1]),
                         FedCryptoCheckState.CONFLICT)

    def test_23_different_provider_document_is_not_proof_of_independence(self):
        web, token = references()
        result = assess((verifier_input(token=token, document=web),))
        self.assertFalse(result.historical_source_version_attested)
        self.assertEqual(result.targets[0].document_provider_id, web.provider_id)

    def test_24_token_cannot_link_to_itself(self):
        _, token = references()
        with self.assertRaisesRegex(FedCryptoPrerequisitesError, "own document"):
            verifier_input(token=token, document=token)

    def test_25_unknown_catalog_document_rejected(self):
        value = replace(verifier_input(), document_claim_id="UNKNOWN")
        with self.assertRaisesRegex(FedCryptoPrerequisitesError, "distinct catalogued"):
            assess((value,))

    def test_26_token_as_linked_document_rejected(self):
        _, token = references()
        another = claim(provider_id="TSA_B", claim_id="TOKEN_002",
                        kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN)
        value = verifier_input(token=token, document=another)
        with self.assertRaisesRegex(FedCryptoPrerequisitesError, "distinct catalogued"):
            assess((value,), refs=(token, another))

    def test_27_non_token_claim_cannot_have_verifier_input(self):
        web, _ = references()
        item = replace(verifier_input(), provider_id=web.provider_id,
                       claim_id=web.claim_id, document_provider_id=None,
                       document_claim_id=None)
        with self.assertRaisesRegex(FedCryptoPrerequisitesError, "token claim"):
            assess((item,))

    def test_28_duplicate_verifier_input_identity_rejected(self):
        with self.assertRaisesRegex(FedCryptoPrerequisitesError, "Duplicate"):
            assess((verifier_input(), verifier_input()))

    def test_29_non_model_verifier_input_rejected(self):
        with self.assertRaisesRegex(FedCryptoPrerequisitesError, "Exact"):
            assess((object(),))

    def test_30_verifier_input_cap_enforced(self):
        with self.assertRaisesRegex(FedCryptoPrerequisitesError, "limit exceeded"):
            assess(repeat(verifier_input(), MAX_VERIFIER_INPUTS + 1))

    def test_31_provider_or_claim_id_invalid(self):
        for bad in ("", "lower", "A B", "A" * 65, 1):
            with self.subTest(bad=bad):
                with self.assertRaises(FedCryptoPrerequisitesError):
                    verifier_input(provider_id=bad)

    def test_32_partial_document_identity_rejected(self):
        for override in (
            dict(document_provider_id=None), dict(document_claim_id=None)
        ):
            with self.subTest(override=override):
                with self.assertRaisesRegex(FedCryptoPrerequisitesError, "together"):
                    verifier_input(**override)

    def test_33_invalid_trust_request_or_revocation_hash(self):
        for key in (
            "claimed_tsa_trust_anchor_sha256",
            "claimed_request_der_sha256",
            "claimed_revocation_evidence_sha256",
        ):
            for bad in ("A" * 64, "g" * 64, "a" * 63, 123):
                with self.subTest(key=key, bad=bad):
                    with self.assertRaisesRegex(FedCryptoPrerequisitesError, "SHA-256"):
                        verifier_input(**{key: bad})

    def test_34_oid_must_have_valid_decimal_arcs(self):
        for bad in ("", "1.2", "3.3.3", "1.40.4", "01.2.3", "1..2", "x.y.z", "1.2.3.", 12):
            with self.subTest(oid=bad):
                with self.assertRaisesRegex(FedCryptoPrerequisitesError, "OID"):
                    verifier_input(expected_tsa_policy_oid=bad)

    def test_35_nonce_rejects_boolean_and_nonpositive_or_huge(self):
        for bad in (True, 0, -1, 2 ** 256, "123", 1.5):
            with self.subTest(nonce=bad):
                with self.assertRaisesRegex(FedCryptoPrerequisitesError, "nonce"):
                    verifier_input(claimed_request_nonce=bad)

    def test_36_naive_or_wrong_type_external_clock_rejected(self):
        for bad in ("2026-10-14", AS_OF.replace(tzinfo=None), 3):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(FedCryptoPrerequisitesError, "timezone-aware"):
                    verifier_input(claimed_external_reference_at=bad)

    def test_37_timezone_equivalence_preserves_receipt(self):
        equivalent = AS_OF.astimezone(timezone(timedelta(hours=-5)))
        one = assess((replace(verifier_input(), claimed_external_reference_at=AS_OF),))
        two = assess((replace(verifier_input(), claimed_external_reference_at=equivalent),))
        self.assertEqual(one.diagnostic_manifest_sha256, two.diagnostic_manifest_sha256)

    def test_38_verifier_input_order_has_stable_digest(self):
        web, token = references()
        other = claim(provider_id="TSA_B", claim_id="TOKEN_002",
                      kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN)
        refs = (web, token, other)
        captures = (artifact(web), artifact(token, body=TOKEN_BYTES),
                    artifact(other, body=b"opaque 2"))
        a = verifier_input(token=token, document=web)
        b = verifier_input(token=other, document=web)
        first = assess((a, b), refs=refs, captures=captures)
        second = assess((b, a), refs=refs, captures=captures)
        self.assertEqual(first.diagnostic_manifest_sha256, second.diagnostic_manifest_sha256)

    def test_39_changed_claimed_anchor_changes_manifest_not_authority(self):
        initial = assess()
        altered = assess((replace(verifier_input(),
                                  claimed_tsa_trust_anchor_sha256="d" * 64),))
        self.assertNotEqual(initial.diagnostic_manifest_sha256,
                            altered.diagnostic_manifest_sha256)
        self.assertFalse(altered.tsa_certificate_chain_verified)

    def test_40_forged_authenticity_blockers_rejected(self):
        chain, captures, intake, authentic = build_chain()
        forged = replace(authentic, blockers=())
        with self.assertRaisesRegex(FedCryptoPrerequisitesError, "differs"):
            assess(prepared=(chain, captures, intake, forged))

    def test_41_forged_intake_digest_rejected(self):
        chain, captures, intake, authentic = build_chain()
        forged = replace(intake, diagnostic_manifest_sha256="f" * 64)
        with self.assertRaisesRegex(FedCryptoPrerequisitesError, "recomputed"):
            assess(prepared=(chain, captures, forged, authentic))

    def test_42_tampered_original_fed_body_rejected(self):
        chain, captures, intake, authentic = build_chain()
        altered_acquisition = replace(chain[0], entity_body_sha256="f" * 64)
        with self.assertRaisesRegex(FedCryptoPrerequisitesError, "recomputed"):
            assess(prepared=((altered_acquisition, *chain[1:]), captures, intake, authentic))

    def test_43_asof_change_requires_recomputed_chain(self):
        prepared = build_chain()
        with self.assertRaisesRegex(FedCryptoPrerequisitesError, "recomputed"):
            assess(prepared=prepared, as_of=AS_OF + timedelta(seconds=1))

    def test_44_invalid_asof_rejected(self):
        prepared = build_chain()
        for bad in (None, "2026-10-14", AS_OF.replace(tzinfo=None)):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(FedCryptoPrerequisitesError, "timezone-aware"):
                    assess(prepared=prepared, as_of=bad)

    def test_45_none_input_collection_rejected(self):
        chain, captures, intake, authentic = build_chain()
        with self.assertRaises(TypeError):
            assess_fed_calendar_crypto_prerequisites(
                *chain, captures, intake, authentic, None, as_of=AS_OF
            )

    def test_46_generator_materialized_once(self):
        result = assess(iter((verifier_input(),)))
        self.assertEqual(result.supplied_verifier_input_count, 1)

    def test_47_receipt_does_not_embed_certificates_tokens_or_urls(self):
        result = assess()
        for name in (
            "entity_body", "cms_token", "trust_anchor_certificate", "request_nonce",
            "external_artifact_url", "calendar_snapshots", "context", "live_events",
        ):
            self.assertFalse(hasattr(result, name))
            self.assertFalse(hasattr(result.targets[0], name))
        self.assertRegex(result.diagnostic_manifest_sha256, r"^[a-f0-9]{64}$")

    def test_48_all_delivery_replay_and_persistence_methods_refuse(self):
        result = assess()
        for operation in (
            result.require_council_calendar_delivery,
            result.require_historical_replay,
            result.require_persistence,
        ):
            with self.assertRaises(FedCryptoPrerequisitesNotReady):
                operation()

    def test_49_forged_empty_blockers_and_targets_cannot_grant(self):
        forged = replace(assess(), blockers=(), targets=())
        self.assertFalse(forged.historical_replay_authorized)
        self.assertFalse(forged.council_calendar_admission_authorized)
        with self.assertRaises(FedCryptoPrerequisitesNotReady):
            forged.require_historical_replay()

    def test_50_frozen_report(self):
        with self.assertRaises(FrozenInstanceError):
            assess().source_url = "forged"

    def test_51_no_token_claims_still_blocked(self):
        web = references()[0]
        result = assess((), refs=(web,), captures=(artifact(web),))
        self.assertEqual(result.timestamp_claim_count, 0)
        self.assertEqual(result.targets, ())
        self.assertFalse(result.historical_source_version_attested)
        self.assertIn("FED_CRYPTO_COUNCIL_REPLAY_STORAGE_NOT_AUTHORIZED",
                      result.blockers)

    def test_52_missing_verifier_claim_yields_missing_work_items(self):
        result = assess(())
        self.assertEqual(result.supplied_verifier_input_count, 0)
        self.assertEqual(check(result.targets[0], RFC3161_PREREQUISITE_CHECKS[1]),
                         FedCryptoCheckState.MISSING)
        self.assertIn("FED_CRYPTO_VERIFIER_INPUT_CLAIM_MISSING", result.blockers)

    def test_53_opaque_token_cannot_self_bind_document_even_matching_hash(self):
        web, token = references()
        altered_token = replace(token, claimed_document_body_sha256=sha256(TOKEN_BYTES).hexdigest())
        result = assess((verifier_input(token=altered_token,
                                        document_provider_id=None,
                                        document_claim_id=None),),
                        refs=(altered_token,),
                        captures=(artifact(altered_token, body=TOKEN_BYTES),))
        self.assertEqual(check(result.targets[0], RFC3161_PREREQUISITE_CHECKS[1]),
                         FedCryptoCheckState.MISSING)

    def test_54_known_rfc3161_oids_are_named_not_automatically_trusted(self):
        self.assertEqual(SHA256_OID, "2.16.840.1.101.3.4.2.1")
        self.assertEqual(TIMESTAMPING_EKU_OID, "1.3.6.1.5.5.7.3.8")
        self.assertEqual(TSTINFO_CONTENT_TYPE_OID, "1.2.840.113549.1.9.16.1.4")
        self.assertFalse(assess().rfc3161_signature_verified)


if __name__ == "__main__":
    unittest.main(verbosity=2)

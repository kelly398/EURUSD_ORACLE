"""F4.5.14 deterministic, offline cryptographic adapter preflight tests.

Run: python -m backend.macro.test_fed_calendar_crypto_adapter
No CMS signature verification, untrusted callbacks, network, broker, DB or HMM.
"""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta, timezone
from hashlib import sha256
from itertools import repeat
import inspect
import unittest

from backend.macro.fed_calendar_crypto_adapter import (
    CMS_SIGNED_DATA_OID_DER,
    FedAdapterHandoffState,
    FedCryptoAdapterError,
    FedCryptoAdapterPlan,
    FedCryptoAdapterUnavailable,
    FedTokenOuterEncoding,
    TRUSTED_ENGINE_REQUIRED_STEPS,
    prepare_fed_calendar_crypto_adapter,
)
from backend.macro.fed_calendar_independent_evidence import FedExternalEvidenceKind
from backend.macro.fed_calendar_verifier_boundary import (
    FedVerifierBoundaryError,
    FedWorkOrderState,
    build_fed_calendar_verifier_boundary,
)
from backend.macro.test_fed_calendar_artifact_intake import artifact
from backend.macro.test_fed_calendar_crypto_prerequisites import (
    AS_OF, ROOT, TOKEN_BYTES, references,
)
from backend.macro.test_fed_calendar_verifier_boundary import (
    POLICY, POLICY2, configured_input, policy, prepared_chain,
)
from backend.macro.test_fed_calendar_independent_evidence import claim


def der(tag: int, data: bytes) -> bytes:
    if len(data) < 128:
        size = bytes((len(data),))
    else:
        length = (len(data).bit_length() + 7) // 8
        size = bytes((0x80 + length,)) + len(data).to_bytes(length, 'big')
    return bytes((tag,)) + size + data


# A deliberately incomplete CMS SignedData skeleton: no signer or signature.
# Passing this preflight must NEVER suggest RFC 3161 correctness.
FAKE_CMS = der(0x30, CMS_SIGNED_DATA_OID_DER + der(0xA0, der(0x30, der(0x02, b'\x01'))))
FAKE_RESPONSE = der(0x30, der(0x30, der(0x02, b'\x00')) + FAKE_CMS)
FAKE_MODIFIED_RESPONSE = der(0x30, der(0x30, der(0x02, b'\x01')) + FAKE_CMS)


def package(*, refs=None, captures=None, inputs=None, policies=None, as_of=AS_OF):
    refs = references() if refs is None else tuple(refs)
    if captures is None:
        captures = tuple(
            artifact(ref, body=FAKE_CMS)
            if ref.kind is FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN
            else artifact(ref)
            for ref in refs
        )
    inputs = (configured_input(),) if inputs is None else tuple(inputs)
    policies = (policy(),) if policies is None else tuple(policies)
    prepared = prepared_chain(refs=refs, captures=captures, inputs=inputs, as_of=as_of)
    chain, retained, intake, authenticity, verifier_inputs, prerequisites = prepared
    boundary = build_fed_calendar_verifier_boundary(
        *chain, retained, intake, authenticity,
        verifier_inputs, prerequisites, policies, as_of=as_of,
    )
    return (*chain, retained, intake, authenticity,
            verifier_inputs, prerequisites, policies, boundary)


def run(*, packed=None, as_of=AS_OF, **options):
    if packed is None:
        packed = package(as_of=as_of, **options)
    return prepare_fed_calendar_crypto_adapter(*packed, as_of=as_of)


class FedCalendarCryptoAdapterTests(unittest.TestCase):

    def test_01_result_is_sealed_report(self):
        self.assertIsInstance(run(), FedCryptoAdapterPlan)

    def test_02_canonical_boundary_hash_is_bound(self):
        values = package()
        self.assertEqual(run(packed=values).canonical_boundary_sha256,
                         values[-1].diagnostic_manifest_sha256)

    def test_03_exactly_one_timestamp_target(self):
        result = run()
        self.assertEqual(len(result.targets), 1)
        self.assertEqual((result.targets[0].provider_id, result.targets[0].claim_id),
                         ('TSA_A', 'TOKEN_001'))

    def test_04_separate_document_identity_is_retained(self):
        target = run().targets[0]
        self.assertEqual((target.document_provider_id, target.document_claim_id),
                         ('ARCHIVE_A', 'CAPTURE_001'))

    def test_05_local_token_bytes_are_rehashed(self):
        target = run().targets[0]
        self.assertEqual(target.token_body_sha256, sha256(FAKE_CMS).hexdigest())

    def test_06_local_document_bytes_are_rehashed(self):
        values = package()
        target = run(packed=values).targets[0]
        self.assertEqual(target.document_body_sha256,
                         sha256(values[6][0].entity_body).hexdigest())

    def test_07_cms_outer_header_is_only_candidate(self):
        target = run().targets[0]
        self.assertEqual(target.outer_encoding,
                         FedTokenOuterEncoding.CMS_SIGNED_DATA_OUTER_ONLY)
        self.assertEqual(target.state, FedAdapterHandoffState.STRUCTURAL_CANDIDATE_ONLY)

    def test_08_candidate_not_crypto_success(self):
        report = run()
        self.assertEqual(report.candidate_count, 1)
        self.assertFalse(report.cryptographic_verifier_executed)
        self.assertFalse(report.rfc3161_signature_verified)

    def test_09_critical_crypto_checks_remain_unexecuted(self):
        report = run()
        self.assertEqual(report.required_engine_steps, TRUSTED_ENGINE_REQUIRED_STEPS)
        self.assertEqual(len(set(report.required_engine_steps)),
                         len(TRUSTED_ENGINE_REQUIRED_STEPS))
        self.assertIn('FED_CRYPTO_ADAPTER_NO_TRUSTED_ENGINE_ATTACHED', report.blockers)

    def test_10_no_admission_or_historical_verification(self):
        report = run()
        for name in ('tsa_certificate_chain_verified', 'historical_capture_time_attested',
                     'historical_source_version_attested',
                     'council_calendar_admission_authorized',
                     'historical_replay_authorized', 'persistence_authorized'):
            self.assertIs(getattr(report, name), False, name)

    def test_11_all_execution_delivery_methods_refuse(self):
        report = run()
        for method in (report.require_trusted_verifier_execution,
                       report.require_council_calendar_delivery,
                       report.require_historical_replay, report.require_persistence):
            with self.assertRaises(FedCryptoAdapterUnavailable):
                method()

    def test_12_caller_supplied_result_never_accepted(self):
        report = run()
        for alleged in (True, False, {'signature_valid': True}, {'verified': True}, None):
            with self.subTest(alleged=repr(alleged)):
                with self.assertRaises(FedCryptoAdapterUnavailable):
                    report.accept_external_verifier_result(alleged)

    def test_13_forged_report_can_never_enable_admission(self):
        forged = replace(run(), blockers=(), targets=(), candidate_count=7)
        self.assertFalse(forged.rfc3161_signature_verified)
        self.assertFalse(forged.historical_replay_authorized)
        with self.assertRaises(FedCryptoAdapterUnavailable):
            forged.require_trusted_verifier_execution()

    def test_14_report_and_targets_are_frozen(self):
        report = run()
        with self.assertRaises(FrozenInstanceError):
            report.source_url = 'other'
        with self.assertRaises(FrozenInstanceError):
            report.targets[0].token_body_sha256 = 'f' * 64

    def test_15_timestamp_response_outer_structure_candidate(self):
        refs = references()
        caps = (artifact(refs[0]), artifact(refs[1], body=FAKE_RESPONSE))
        target = run(captures=caps).targets[0]
        self.assertEqual(target.outer_encoding,
                         FedTokenOuterEncoding.RFC3161_RESPONSE_WITH_CMS_OUTER_ONLY)
        self.assertEqual(target.state, FedAdapterHandoffState.STRUCTURAL_CANDIDATE_ONLY)

    def test_16_modified_timestamp_response_outer_structure_candidate(self):
        refs = references()
        caps = (artifact(refs[0]), artifact(refs[1], body=FAKE_MODIFIED_RESPONSE))
        target = run(captures=caps).targets[0]
        self.assertEqual(target.outer_encoding,
                         FedTokenOuterEncoding.RFC3161_RESPONSE_WITH_CMS_OUTER_ONLY)

    def test_17_old_fixture_token_not_misread_as_cms(self):
        refs = references()
        target = run(captures=(artifact(refs[0]), artifact(refs[1], body=TOKEN_BYTES))).targets[0]
        self.assertEqual(target.outer_encoding,
                         FedTokenOuterEncoding.MALFORMED_OR_UNSUPPORTED)
        self.assertEqual(target.state, FedAdapterHandoffState.BLOCKED_UNSUPPORTED_ENCODING)

    def test_18_trailing_bytes_rejected(self):
        refs = references()
        target = run(captures=(artifact(refs[0]), artifact(refs[1], body=FAKE_CMS+b'\x00'))).targets[0]
        self.assertEqual(target.outer_encoding, FedTokenOuterEncoding.MALFORMED_OR_UNSUPPORTED)

    def test_19_indefinite_ber_length_rejected(self):
        refs = references()
        body = b'\x30\x80' + FAKE_CMS[2:] + b'\x00\x00'
        target = run(captures=(artifact(refs[0]), artifact(refs[1], body=body))).targets[0]
        self.assertEqual(target.outer_encoding, FedTokenOuterEncoding.MALFORMED_OR_UNSUPPORTED)

    def test_20_nonminimal_long_form_rejected(self):
        refs = references()
        body = b'\x30\x81'+bytes((len(FAKE_CMS)-2,))+FAKE_CMS[2:]
        target = run(captures=(artifact(refs[0]), artifact(refs[1], body=body))).targets[0]
        self.assertEqual(target.outer_encoding, FedTokenOuterEncoding.MALFORMED_OR_UNSUPPORTED)

    def test_21_high_tag_number_rejected(self):
        refs = references()
        body = b'\x3f'+FAKE_CMS[1:]
        target = run(captures=(artifact(refs[0]), artifact(refs[1], body=body))).targets[0]
        self.assertEqual(target.outer_encoding, FedTokenOuterEncoding.MALFORMED_OR_UNSUPPORTED)

    def test_22_wrong_cms_oid_rejected(self):
        refs = references()
        oid = bytes.fromhex('06092a864886f70d010701')
        body = der(0x30, oid + der(0xA0, der(0x30, b'')))
        target = run(captures=(artifact(refs[0]), artifact(refs[1], body=body))).targets[0]
        self.assertEqual(target.outer_encoding, FedTokenOuterEncoding.MALFORMED_OR_UNSUPPORTED)

    def test_23_missing_explicit_wrapper_rejected(self):
        refs = references()
        body = der(0x30, CMS_SIGNED_DATA_OID_DER + der(0x30, b''))
        target = run(captures=(artifact(refs[0]), artifact(refs[1], body=body))).targets[0]
        self.assertEqual(target.outer_encoding, FedTokenOuterEncoding.MALFORMED_OR_UNSUPPORTED)

    def test_24_extra_top_level_child_rejected(self):
        refs = references()
        body = der(0x30, FAKE_CMS[2:] + der(0x05, b''))
        target = run(captures=(artifact(refs[0]), artifact(refs[1], body=body))).targets[0]
        self.assertEqual(target.outer_encoding, FedTokenOuterEncoding.MALFORMED_OR_UNSUPPORTED)

    def test_25_corrupt_nested_length_rejected(self):
        refs = references()
        body = bytearray(FAKE_CMS)
        body[-4] = 0xff
        target = run(captures=(artifact(refs[0]), artifact(refs[1], body=bytes(body)))).targets[0]
        self.assertEqual(target.outer_encoding, FedTokenOuterEncoding.MALFORMED_OR_UNSUPPORTED)

    def test_26_truncated_response_rejected(self):
        refs = references()
        target = run(captures=(artifact(refs[0]), artifact(refs[1], body=FAKE_RESPONSE[:-2]))).targets[0]
        self.assertEqual(target.outer_encoding, FedTokenOuterEncoding.MALFORMED_OR_UNSUPPORTED)

    def test_27_rejected_pkistatus_is_not_candidate(self):
        refs = references()
        body = der(0x30, der(0x30, der(0x02, b'\x02')) + FAKE_CMS)
        target = run(captures=(artifact(refs[0]), artifact(refs[1], body=body))).targets[0]
        self.assertEqual(target.outer_encoding, FedTokenOuterEncoding.MALFORMED_OR_UNSUPPORTED)

    def test_28_missing_timestamp_token_response_rejected(self):
        refs = references()
        body = der(0x30, der(0x30, der(0x02, b'\x00')))
        target = run(captures=(artifact(refs[0]), artifact(refs[1], body=body))).targets[0]
        self.assertEqual(target.outer_encoding, FedTokenOuterEncoding.MALFORMED_OR_UNSUPPORTED)

    def test_29_status_with_wrong_integer_encoding_rejected(self):
        refs = references()
        body = der(0x30, der(0x30, der(0x02, b'\x00\x00')) + FAKE_CMS)
        target = run(captures=(artifact(refs[0]), artifact(refs[1], body=body))).targets[0]
        self.assertEqual(target.outer_encoding, FedTokenOuterEncoding.MALFORMED_OR_UNSUPPORTED)

    def test_30_changed_raw_token_changes_digest(self):
        refs = references()
        a = run()
        b = run(captures=(artifact(refs[0]), artifact(refs[1], body=FAKE_RESPONSE)))
        self.assertNotEqual(a.diagnostic_manifest_sha256, b.diagnostic_manifest_sha256)

    def test_31_byte_equal_envelope_receipts_are_deterministic(self):
        self.assertEqual(run().diagnostic_manifest_sha256, run().diagnostic_manifest_sha256)

    def test_32_utc_timezone_equivalence(self):
        local = AS_OF.astimezone(timezone(timedelta(hours=-5)))
        # Must compare against a freshly canonicalized chain.
        self.assertEqual(run().diagnostic_manifest_sha256,
                         run(as_of=local).diagnostic_manifest_sha256)

    def test_33_different_asof_changes_manifest(self):
        future = AS_OF + timedelta(minutes=1)
        self.assertNotEqual(run().diagnostic_manifest_sha256,
                            run(as_of=future).diagnostic_manifest_sha256)

    def test_34_stale_upstream_boundary_rejected(self):
        with self.assertRaises(FedCryptoAdapterError):
            run(packed=package(), as_of=AS_OF + timedelta(minutes=1))

    def test_35_clearing_boundary_blockers_is_forgery(self):
        args = package()
        forged = replace(args[-1], blockers=())
        with self.assertRaisesRegex(FedCryptoAdapterError, 'differs'):
            run(packed=(*args[:-1], forged))

    def test_36_forged_boundary_receipt_digest_rejected(self):
        args = package()
        forged = replace(args[-1], diagnostic_manifest_sha256='f' * 64)
        with self.assertRaises(FedCryptoAdapterError):
            run(packed=(*args[:-1], forged))

    def test_37_forged_boundary_success_is_rejected(self):
        args = package()
        forged = replace(args[-1], work_orders=())
        with self.assertRaises(FedCryptoAdapterError):
            run(packed=(*args[:-1], forged))

    def test_38_forged_upstream_prerequisite_rejected(self):
        args = package()
        forged = replace(args[10], blockers=())
        with self.assertRaisesRegex(FedCryptoAdapterError, 'revalidated'):
            run(packed=(*args[:10], forged, *args[11:]))

    def test_39_modified_source_record_rejected(self):
        args = package()
        forged = replace(args[0], entity_body_sha256='f' * 64)
        with self.assertRaises(FedCryptoAdapterError):
            run(packed=(forged, *args[1:]))

    def test_40_wrong_policy_reference_rejected(self):
        args = package()
        mismatched = (policy(allowed_tsa_policy_oids=(POLICY2,)),)
        with self.assertRaises(FedCryptoAdapterError):
            run(packed=(*args[:11], mismatched, args[12]))

    def test_41_changed_policy_revision_recomputes_receipt(self):
        a = run()
        b = run(policies=(policy(policy_revision_sha256='e' * 64),))
        self.assertNotEqual(a.diagnostic_manifest_sha256, b.diagnostic_manifest_sha256)
        self.assertFalse(b.cryptographic_verifier_executed)

    def test_42_missing_policy_is_still_incomplete(self):
        target = run(policies=()).targets[0]
        self.assertEqual(target.state, FedAdapterHandoffState.BLOCKED_MISSING_BYTES_OR_POLICY)

    def test_43_missing_verifier_input_is_incomplete(self):
        target = run(inputs=()).targets[0]
        self.assertEqual(target.state, FedAdapterHandoffState.BLOCKED_MISSING_BYTES_OR_POLICY)

    def test_44_missing_token_capture_has_explicit_missing_encoding(self):
        refs = references()
        target = run(captures=(artifact(refs[0]),)).targets[0]
        self.assertEqual(target.outer_encoding, FedTokenOuterEncoding.MISSING)
        self.assertEqual(target.state, FedAdapterHandoffState.BLOCKED_MISSING_BYTES_OR_POLICY)

    def test_45_missing_document_capture_is_incomplete(self):
        refs = references()
        target = run(captures=(artifact(refs[1], body=FAKE_CMS),)).targets[0]
        self.assertIsNone(target.document_body_sha256)
        self.assertEqual(target.state, FedAdapterHandoffState.BLOCKED_MISSING_BYTES_OR_POLICY)

    def test_46_policy_conflict_precedes_encoding_candidate(self):
        target = run(policies=(policy(pinned_tsa_anchor_certificate_sha256='f'*64),)).targets[0]
        self.assertEqual(target.state, FedAdapterHandoffState.BLOCKED_CONFLICT)

    def test_47_no_timestamp_claims_reports_no_candidates(self):
        result = run(refs=(claim(),), inputs=(), policies=())
        self.assertEqual(result.targets, ())
        self.assertEqual(result.candidate_count, 0)
        self.assertIn('FED_CRYPTO_ADAPTER_NO_TIMESTAMP_WORK_ORDERS', result.blockers)

    def test_48_no_claims_at_all_remains_blocked(self):
        report = run(refs=(), captures=(), inputs=(), policies=())
        self.assertEqual(report.targets, ())
        self.assertFalse(report.historical_replay_authorized)

    def test_49_invalid_asof_rejected(self):
        for invalid in (None, '2026-10-14', AS_OF.replace(tzinfo=None)):
            with self.subTest(value=str(invalid)):
                with self.assertRaises(FedCryptoAdapterError):
                    run(packed=package(), as_of=invalid)

    def test_50_none_or_bad_collection_rejected(self):
        args = package()
        for idx in (4, 6, 9, 11):
            with self.subTest(idx=idx):
                mutable = list(args)
                mutable[idx] = None
                with self.assertRaises(TypeError):
                    run(packed=tuple(mutable))

    def test_51_external_claim_limit_is_enforced(self):
        args = package()
        with self.assertRaises(FedCryptoAdapterError):
            run(packed=(*args[:4], repeat(claim(), 33), *args[5:]))

    def test_52_generators_consumed_once(self):
        args = package()
        prepared = list(args)
        for idx in (4, 6, 9, 11):
            prepared[idx] = (item for item in args[idx])
        self.assertEqual(run(packed=tuple(prepared)).diagnostic_manifest_sha256,
                         run(packed=args).diagnostic_manifest_sha256)

    def test_53_wrong_boundary_type_rejected(self):
        args = package()
        with self.assertRaises(TypeError):
            run(packed=(*args[:-1], object()))

    def test_54_output_does_not_embed_raw_bytes_or_urls(self):
        report = run()
        for name in ('entity_body', 'token_bytes', 'document_bytes', 'trust_anchor',
                     'verifier_callback', 'output', 'html', 'url', 'certificate_der'):
            self.assertFalse(hasattr(report, name))
        for target in report.targets:
            for name in ('entity_body', 'token_bytes', 'document_bytes',
                         'trust_anchor', 'verifier_callback', 'artifact_url'):
                self.assertFalse(hasattr(target, name))

    def test_55_no_dynamic_engine_callback_supported(self):
        with self.assertRaises(TypeError):
            prepare_fed_calendar_crypto_adapter(*package(), as_of=AS_OF,
                                                verifier=lambda *_: True)

    def test_56_no_network_subprocess_db_or_council_imports(self):
        import backend.macro.fed_calendar_crypto_adapter as module
        source = inspect.getsource(module)
        for token in ('import subprocess', 'import socket', 'import requests',
                      'from backend.agents', 'from backend.db', 'from sqlalchemy',
                      'os.getenv', 'urllib.request'):
            self.assertNotIn(token, source)

    def test_57_fake_cms_is_obviously_not_signed_but_cannot_be_approved(self):
        self.assertLess(len(FAKE_CMS), 40)
        report = run()
        self.assertEqual(report.targets[0].state,
                         FedAdapterHandoffState.STRUCTURAL_CANDIDATE_ONLY)
        self.assertFalse(report.rfc3161_signature_verified)
        self.assertFalse(report.tsa_certificate_chain_verified)

    def test_58_reordered_captures_preserve_manifest(self):
        refs = references()
        captures = (artifact(refs[0]), artifact(refs[1], body=FAKE_CMS))
        self.assertEqual(run(captures=captures).diagnostic_manifest_sha256,
                         run(captures=tuple(reversed(captures))).diagnostic_manifest_sha256)

    def test_59_minimal_claimed_time_does_not_attest(self):
        report = run()
        self.assertFalse(report.historical_capture_time_attested)
        self.assertFalse(report.historical_source_version_attested)

    def test_61_claimed_document_digest_conflict_returns_blocked_report(self):
        from dataclasses import replace as change
        original, token = references()
        forged = change(original, claimed_document_body_sha256='e' * 64)
        result = run(refs=(forged, token), captures=(
            artifact(forged), artifact(token, body=FAKE_CMS),
        ))
        target = result.targets[0]
        self.assertEqual(target.state, FedAdapterHandoffState.BLOCKED_CONFLICT)
        self.assertIsNone(target.document_body_sha256)
        self.assertFalse(result.historical_replay_authorized)

    def test_62_malformed_token_does_not_hide_document_binding(self):
        refs = references()
        result = run(captures=(artifact(refs[0]),
                               artifact(refs[1], body=TOKEN_BYTES)))
        self.assertEqual(result.targets[0].state,
                         FedAdapterHandoffState.BLOCKED_UNSUPPORTED_ENCODING)
        self.assertIsNotNone(result.targets[0].document_body_sha256)

    def test_60_future_document_capture_is_upstream_temporal_conflict(self):
        refs = references()
        later = AS_OF + timedelta(minutes=2)
        later_web = artifact(refs[0], response_completed_at=later,
                             request_started_at=later - timedelta(seconds=1))
        target = run(captures=(later_web, artifact(refs[1], body=FAKE_CMS))).targets[0]
        self.assertEqual(target.state, FedAdapterHandoffState.BLOCKED_CONFLICT)


if __name__ == '__main__':
    unittest.main(verbosity=2)

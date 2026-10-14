"""F4.5.15 deterministic, offline RFC 3161 engine prerequisite inventory tests.

Run: python -m backend.macro.test_fed_calendar_rfc3161_engine_prerequisites
The upstream F4.5.14 function is mocked to test the new sealed boundary's
behaviour; complete integration is also covered by re-running F4.5.6--14 on
Windows. No cryptographic proof, engine loading, IO, DB, broker, or HMM.
"""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from itertools import repeat
from types import SimpleNamespace
from unittest.mock import patch
import ast
import inspect
import unittest

from backend.macro.fed_calendar_crypto_adapter import (
    FedAdapterHandoffState,
    FedCryptoAdapterError,
    FedCryptoAdapterHandoffTarget,
    FedCryptoAdapterPlan,
    FedTokenOuterEncoding,
)
from backend.macro.fed_calendar_verifier_boundary import FedVerifierPolicyReference
from backend.macro.fed_calendar_rfc3161_engine_prerequisites import (
    ENGINE_PREREQUISITE_CHECKS,
    MAX_ENGINE_PROVISIONING_REFERENCES,
    REQUIRED_ENGINE_CAPABILITIES,
    REQUIRED_ENGINE_PROFILE,
    FedEngineReferenceCheckState as S,
    FedEngineWorkState as W,
    FedRfc3161EngineMaterialReference,
    FedRfc3161EnginePrerequisiteError,
    FedRfc3161EnginePrerequisitesReport,
    FedRfc3161EngineUnavailable,
    assess_fed_calendar_rfc3161_engine_prerequisites,
)

AS_OF = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
SOURCE = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20240809a.htm"
TOKEN = "a" * 64
DOCUMENT = "b" * 64
POLICY = "c" * 64
BUILD = "d" * 64
LOCK = "e" * 64
TRUST_STORE = "f" * 64
ANCHOR = "0" * 64
CHAIN = "1" * 64
REVOCATION = "2" * 64
REQUEST = "3" * 64
TRUSTED_CLOCK = "4" * 64


def target(*, claim_id="TOKEN_001", state=FedAdapterHandoffState.STRUCTURAL_CANDIDATE_ONLY,
           token_digest=TOKEN, document_digest=DOCUMENT, policy_digest=POLICY):
    return FedCryptoAdapterHandoffTarget(
        provider_id="TSA_A", claim_id=claim_id,
        document_provider_id="ARCHIVE_A", document_claim_id="CAPTURE_001",
        token_body_sha256=token_digest, document_body_sha256=document_digest,
        policy_revision_reference_sha256=policy_digest,
        outer_encoding=FedTokenOuterEncoding.CMS_SIGNED_DATA_OUTER_ONLY, state=state,
    )


def adapter(*, as_of=AS_OF, targets=None, digest="5" * 64):
    values = (target(),) if targets is None else tuple(targets)
    return FedCryptoAdapterPlan(
        as_of=as_of, source_url=SOURCE, canonical_boundary_sha256="6" * 64,
        candidate_count=sum(x.state is FedAdapterHandoffState.STRUCTURAL_CANDIDATE_ONLY
                            for x in values),
        targets=values, required_engine_steps=(), blockers=("UPSTREAM_DENIED",),
        diagnostic_manifest_sha256=digest,
    )


def material(*, claim_id="TOKEN_001", **kwargs):
    values = dict(
        provider_id="TSA_A", claim_id=claim_id,
        claimed_token_sha256=TOKEN,
        claimed_document_sha256=DOCUMENT,
        claimed_policy_revision_sha256=POLICY,
        claimed_engine_build_sha256=BUILD,
        claimed_dependency_lock_sha256=LOCK,
        claimed_trust_store_sha256=TRUST_STORE,
        claimed_tsa_trust_anchor_sha256=ANCHOR,
        claimed_tsa_signer_chain_sha256=CHAIN,
        claimed_revocation_evidence_sha256=REVOCATION,
        claimed_request_der_sha256=REQUEST,
        claimed_material_observed_at=AS_OF-timedelta(hours=1),
        claimed_capabilities=REQUIRED_ENGINE_CAPABILITIES,
    )
    values.update(kwargs)
    return FedRfc3161EngineMaterialReference(**values)


def _verifier_input(*, claim_id="TOKEN_001", **kwargs):
    values = dict(
        provider_id="TSA_A", claim_id=claim_id,
        claimed_tsa_trust_anchor_sha256=ANCHOR,
        claimed_revocation_evidence_sha256=REVOCATION,
        claimed_request_der_sha256=REQUEST,
        claimed_request_nonce=1234567,
        claimed_external_reference_at=None,
    )
    values.update(kwargs)
    return SimpleNamespace(**values)


def _policy(*, claim_id="TOKEN_001", **kwargs):
    values = dict(
        provider_id="TSA_A", claim_id=claim_id,
        policy_revision_sha256=POLICY,
        pinned_tsa_anchor_certificate_sha256=ANCHOR,
        allowed_tsa_policy_oids=("1.3.6.1.4.1.55555.3161.1",),
    )
    values.update(kwargs)
    return FedVerifierPolicyReference(**values)


def inputs(*, base=None, mats=None, verify_inputs=None, policies=None,
           claims=(), captures=(), as_of=AS_OF):
    base = adapter(as_of=as_of) if base is None else base
    mats = (material(),) if mats is None else mats
    verify_inputs = (_verifier_input(),) if verify_inputs is None else verify_inputs
    policies = (_policy(),) if policies is None else policies
    # Canonical adapter factory arguments in F4.5.14 order. Other opaque
    # placeholders are ignored by the stubbed factory, never trusted in prod.
    args = (
        object(), object(), object(), object(), claims, object(), captures,
        object(), object(), verify_inputs, object(), policies, object(), base, mats,
    )
    return args


def run(*, canonical=None, as_of=AS_OF, arguments=None, **updates):
    if arguments is None:
        arguments = inputs(as_of=as_of, **updates)
    expected = arguments[-2] if canonical is None else canonical
    with patch(
        "backend.macro.fed_calendar_rfc3161_engine_prerequisites.prepare_fed_calendar_crypto_adapter",
        return_value=expected,
    ) as patched:
        result = assess_fed_calendar_rfc3161_engine_prerequisites(
            *arguments, as_of=as_of,
        )
        patched.assert_called_once()
    return result


def check(report, index=0, target_idx=0):
    return report.targets[target_idx].checks[index].state


class FedCalendarRfc3161EnginePrerequisitesTests(unittest.TestCase):

    def test_01_frozen_report_type(self):
        self.assertIsInstance(run(), FedRfc3161EnginePrerequisitesReport)

    def test_02_upstream_manifest_binding(self):
        self.assertEqual(run().canonical_adapter_sha256, adapter().diagnostic_manifest_sha256)

    def test_03_one_target_one_reference(self):
        r = run()
        self.assertEqual((r.target_count, r.reference_count), (1, 1))
        self.assertEqual((r.targets[0].provider_id, r.targets[0].claim_id), ("TSA_A", "TOKEN_001"))

    def test_04_unique_exact_check_codes(self):
        r = run()
        self.assertEqual(tuple(c.code for c in r.targets[0].checks), ENGINE_PREREQUISITE_CHECKS)
        self.assertEqual(len(ENGINE_PREREQUISITE_CHECKS), len(set(ENGINE_PREREQUISITE_CHECKS)))

    def test_05_capabilities_strict_complete_and_unique(self):
        r = run()
        self.assertEqual(r.required_engine_capabilities, REQUIRED_ENGINE_CAPABILITIES)
        self.assertEqual(REQUIRED_ENGINE_CAPABILITIES, tuple(sorted(set(REQUIRED_ENGINE_CAPABILITIES))))
        self.assertEqual(len(REQUIRED_ENGINE_CAPABILITIES), 10)

    def test_06_fully_declared_is_only_reference_candidate(self):
        r = run()
        self.assertEqual(r.review_package_candidate_count, 1)
        self.assertEqual(r.targets[0].state, W.REFERENCE_PACKAGE_CANDIDATE_ONLY)

    def test_07_every_optional_approval_flag_false(self):
        r = run()
        for name in (
            "trusted_engine_installed", "cryptographic_verifier_executed",
            "rfc3161_signature_verified", "tsa_certificate_chain_verified",
            "document_imprint_verified", "trusted_timestamp_verified",
            "historical_capture_time_attested", "historical_source_version_attested",
            "council_calendar_admission_authorized", "historical_replay_authorized",
            "persistence_authorized",
        ):
            self.assertIs(getattr(r, name), False, name)

    def test_08_delivery_execution_replay_storage_always_refuse(self):
        r = run()
        for method in (r.require_trusted_verifier_execution,
                       r.require_council_calendar_delivery,
                       r.require_historical_replay, r.require_persistence):
            with self.assertRaises(FedRfc3161EngineUnavailable):
                method()

    def test_09_caller_supplied_verified_true_refused(self):
        r = run()
        for thing in (True, False, {"verified": True}, {"signature": "valid"}, None):
            with self.subTest(thing=thing):
                with self.assertRaises(FedRfc3161EngineUnavailable):
                    r.accept_external_verification_assertion(thing)

    def test_10_no_engine_references_remains_blocked(self):
        r = run(mats=())
        self.assertEqual(r.reference_count, 0)
        self.assertEqual(r.review_package_candidate_count, 0)
        self.assertEqual(r.targets[0].state, W.BLOCKED_INCOMPLETE_REFERENCE_PACKAGE)

    def test_11_raw_token_only_local_match(self):
        self.assertEqual(check(run(), 1), S.UNTRUSTED_REFERENCE_ONLY)

    def test_12_document_only_local_match(self):
        self.assertEqual(check(run(), 2), S.UNTRUSTED_REFERENCE_ONLY)

    def test_13_policy_only_local_match(self):
        self.assertEqual(check(run(), 3), S.UNTRUSTED_REFERENCE_ONLY)

    def test_14_candidate_outer_envelope_not_verified(self):
        self.assertEqual(check(run(), 0), S.LOCAL_CANDIDATE_ONLY)
        self.assertEqual(check(run(), 13), S.INDEPENDENT_EXECUTION_REQUIRED)

    def test_15_missing_engine_build_blocks(self):
        r = run(mats=(material(claimed_engine_build_sha256=None),))
        self.assertEqual(check(r, 4), S.MISSING)
        self.assertEqual(r.targets[0].state, W.BLOCKED_INCOMPLETE_REFERENCE_PACKAGE)

    def test_16_missing_dependency_lock_blocks(self):
        self.assertEqual(check(run(mats=(material(claimed_dependency_lock_sha256=None),)), 4), S.MISSING)

    def test_17_parser_missing_capability_blocks(self):
        caps = tuple(x for x in REQUIRED_ENGINE_CAPABILITIES
                     if x != "COMPLETE_BOUNDED_DER_CMS_TSTINFO_DECODER")
        self.assertEqual(check(run(mats=(material(claimed_capabilities=caps),)), 5), S.MISSING)

    def test_18_signature_missing_capability_blocks(self):
        caps = tuple(x for x in REQUIRED_ENGINE_CAPABILITIES
                     if x != "VERIFY_CMS_SINGLE_SIGNER_SIGNATURE_AND_SIGNED_ATTRIBUTES")
        self.assertEqual(check(run(mats=(material(claimed_capabilities=caps),)), 5), S.MISSING)

    def test_19_ess_certid_v2_missing_capability_blocks(self):
        caps = tuple(x for x in REQUIRED_ENGINE_CAPABILITIES
                     if x != "VERIFY_ESS_CERTID_V2_TSA_SIGNER_CERTIFICATE_BINDING")
        self.assertEqual(check(run(mats=(material(claimed_capabilities=caps),)), 5), S.MISSING)

    def test_20_pkix_path_missing_capability_blocks(self):
        caps = tuple(x for x in REQUIRED_ENGINE_CAPABILITIES
                     if x != "VERIFY_RFC5280_CERTIFICATE_PATH_AT_GEN_TIME")
        self.assertEqual(check(run(mats=(material(claimed_capabilities=caps),)), 6), S.MISSING)

    def test_21_timestamping_eku_missing_capability_blocks(self):
        caps = tuple(x for x in REQUIRED_ENGINE_CAPABILITIES
                     if x != "VERIFY_EXCLUSIVE_CRITICAL_TIMESTAMPING_EKU")
        self.assertEqual(check(run(mats=(material(claimed_capabilities=caps),)), 6), S.MISSING)

    def test_22_imprint_missing_capability_blocks(self):
        caps = tuple(x for x in REQUIRED_ENGINE_CAPABILITIES
                     if x != "VERIFY_SIGNED_SHA256_IMPRINT_AGAINST_DISTINCT_DOCUMENT")
        self.assertEqual(check(run(mats=(material(claimed_capabilities=caps),)), 7), S.MISSING)

    def test_23_signed_policy_missing_capability_blocks(self):
        caps = tuple(x for x in REQUIRED_ENGINE_CAPABILITIES
                     if x != "VERIFY_SIGNED_TSA_POLICY_AGAINST_OPERATOR_ALLOWLIST")
        self.assertEqual(check(run(mats=(material(claimed_capabilities=caps),)), 7), S.MISSING)

    def test_24_nonce_or_time_missing_capability_blocks(self):
        caps = tuple(x for x in REQUIRED_ENGINE_CAPABILITIES
                     if x != "VERIFY_REQUEST_NONCE_OR_INDEPENDENT_TRUSTED_TIME")
        self.assertEqual(check(run(mats=(material(claimed_capabilities=caps),)), 7), S.MISSING)

    def test_25_signed_gen_time_missing_capability_blocks(self):
        caps = tuple(x for x in REQUIRED_ENGINE_CAPABILITIES
                     if x != "VERIFY_SIGNED_GEN_TIME_AND_HISTORICAL_AS_OF_VISIBILITY")
        self.assertEqual(check(run(mats=(material(claimed_capabilities=caps),)), 7), S.MISSING)

    def test_26_missing_token_reference_blocks(self):
        r = run(mats=(material(claimed_token_sha256=None),))
        self.assertEqual(check(r, 1), S.MISSING)

    def test_27_mismatching_token_hash_conflicts(self):
        r = run(mats=(material(claimed_token_sha256="9"*64),))
        self.assertEqual(check(r, 1), S.CONFLICT)
        self.assertEqual(r.targets[0].state, W.BLOCKED_UPSTREAM_OR_LOCAL_CONFLICT)

    def test_28_missing_document_reference_blocks(self):
        self.assertEqual(check(run(mats=(material(claimed_document_sha256=None),)), 2), S.MISSING)

    def test_29_mismatching_document_hash_conflicts(self):
        self.assertEqual(check(run(mats=(material(claimed_document_sha256="9"*64),)), 2), S.CONFLICT)

    def test_30_missing_policy_revision_blocks(self):
        self.assertEqual(check(run(mats=(material(claimed_policy_revision_sha256=None),)), 3), S.MISSING)

    def test_31_mismatching_policy_revision_conflicts(self):
        self.assertEqual(check(run(mats=(material(claimed_policy_revision_sha256="9"*64),)), 3), S.CONFLICT)

    def test_32_trust_store_missing_blocks(self):
        self.assertEqual(check(run(mats=(material(claimed_trust_store_sha256=None),)), 8), S.MISSING)

    def test_33_trust_anchor_missing_blocks(self):
        self.assertEqual(check(run(mats=(material(claimed_tsa_trust_anchor_sha256=None),)), 8), S.MISSING)

    def test_34_trust_anchor_mismatch_upstream_policy_conflicts(self):
        r = run(mats=(material(claimed_tsa_trust_anchor_sha256="9"*64),))
        self.assertEqual(check(r, 8), S.CONFLICT)

    def test_35_trust_anchor_mismatch_upstream_claim_conflicts(self):
        v = _verifier_input(claimed_tsa_trust_anchor_sha256="9"*64)
        r = run(verify_inputs=(v,))
        self.assertEqual(check(r, 8), S.CONFLICT)

    def test_36_signer_chain_missing_blocks(self):
        self.assertEqual(check(run(mats=(material(claimed_tsa_signer_chain_sha256=None),)), 9), S.MISSING)

    def test_37_revocation_missing_blocks(self):
        self.assertEqual(check(run(mats=(material(claimed_revocation_evidence_sha256=None),)), 10), S.MISSING)

    def test_38_revocation_mismatch_conflicts(self):
        self.assertEqual(check(run(mats=(material(claimed_revocation_evidence_sha256="9"*64),)), 10), S.CONFLICT)

    def test_39_revocation_capability_missing_blocks(self):
        caps = tuple(x for x in REQUIRED_ENGINE_CAPABILITIES
                     if x != "VERIFY_REVOCATION_AT_GEN_TIME_FROM_APPROVED_EVIDENCE")
        self.assertEqual(check(run(mats=(material(claimed_capabilities=caps),)), 10), S.MISSING)

    def test_40_request_with_nonce_is_only_claim(self):
        self.assertEqual(check(run(), 11), S.UNTRUSTED_REFERENCE_ONLY)

    def test_41_request_digest_mismatch_conflicts(self):
        self.assertEqual(check(run(mats=(material(claimed_request_der_sha256="9"*64),)), 11), S.CONFLICT)

    def test_42_request_digest_without_nonce_blocks(self):
        self.assertEqual(check(run(verify_inputs=(_verifier_input(claimed_request_nonce=None),)), 11), S.MISSING)

    def test_43_signed_clock_claim_not_crypto_proof(self):
        m = material(claimed_request_der_sha256=None,
                     claimed_independent_time_evidence_sha256=TRUSTED_CLOCK)
        v = _verifier_input(claimed_request_nonce=None, claimed_request_der_sha256=None,
                            claimed_external_reference_at=AS_OF-timedelta(hours=2))
        r = run(mats=(m,), verify_inputs=(v,))
        self.assertEqual(check(r, 11), S.UNTRUSTED_REFERENCE_ONLY)
        self.assertFalse(r.trusted_timestamp_verified)

    def test_44_clock_claim_without_upstream_external_clock_blocks(self):
        m = material(claimed_request_der_sha256=None,
                     claimed_independent_time_evidence_sha256=TRUSTED_CLOCK)
        self.assertEqual(check(run(mats=(m,)), 11), S.MISSING)

    def test_45_missing_request_and_time_blocks(self):
        m = material(claimed_request_der_sha256=None,
                     claimed_independent_time_evidence_sha256=None)
        self.assertEqual(check(run(mats=(m,)), 11), S.MISSING)

    def test_46_observed_after_asof_conflicts(self):
        m = material(claimed_material_observed_at=AS_OF+timedelta(seconds=1))
        r = run(mats=(m,))
        self.assertEqual(check(r, 12), S.CONFLICT)
        self.assertEqual(r.targets[0].state, W.BLOCKED_UPSTREAM_OR_LOCAL_CONFLICT)

    def test_47_observed_equal_asof_remains_untrusted(self):
        self.assertEqual(check(run(mats=(material(claimed_material_observed_at=AS_OF),)), 12),
                         S.UNTRUSTED_REFERENCE_ONLY)

    def test_48_missing_observation_is_incomplete(self):
        self.assertEqual(check(run(mats=(material(claimed_material_observed_at=None),)), 12), S.MISSING)

    def test_49_utc_equivalent_asof_same_receipt(self):
        tz = timezone(timedelta(hours=3))
        r1 = run()
        r2 = run(as_of=AS_OF.astimezone(tz))
        self.assertEqual(r1.diagnostic_manifest_sha256, r2.diagnostic_manifest_sha256)

    def test_50_observed_timezone_equivalent_same_receipt(self):
        claimed = AS_OF-timedelta(hours=1)
        r1 = run(mats=(material(claimed_material_observed_at=claimed),))
        r2 = run(mats=(material(claimed_material_observed_at=claimed.astimezone(timezone(timedelta(hours=-5)))),))
        self.assertEqual(r1.diagnostic_manifest_sha256, r2.diagnostic_manifest_sha256)

    def test_51_changed_asof_changes_receipt(self):
        r1 = run()
        r2 = run(as_of=AS_OF+timedelta(minutes=1),
                 base=adapter(as_of=AS_OF+timedelta(minutes=1)))
        self.assertNotEqual(r1.diagnostic_manifest_sha256, r2.diagnostic_manifest_sha256)

    def test_52_stale_upstream_asof_cannot_pass(self):
        arguments = inputs(as_of=AS_OF)
        with patch("backend.macro.fed_calendar_rfc3161_engine_prerequisites.prepare_fed_calendar_crypto_adapter",
                   return_value=adapter(as_of=AS_OF+timedelta(hours=1))):
            with self.assertRaises(FedRfc3161EnginePrerequisiteError):
                assess_fed_calendar_rfc3161_engine_prerequisites(*arguments, as_of=AS_OF)

    def test_53_upstream_forged_digest_rejected(self):
        original = adapter()
        values = inputs(base=replace(original, diagnostic_manifest_sha256="9"*64))
        with patch("backend.macro.fed_calendar_rfc3161_engine_prerequisites.prepare_fed_calendar_crypto_adapter",
                   return_value=original):
            with self.assertRaises(FedRfc3161EnginePrerequisiteError):
                assess_fed_calendar_rfc3161_engine_prerequisites(*values, as_of=AS_OF)

    def test_54_upstream_forged_blockers_rejected(self):
        original = adapter()
        values = inputs(base=replace(original, blockers=()))
        with patch("backend.macro.fed_calendar_rfc3161_engine_prerequisites.prepare_fed_calendar_crypto_adapter",
                   return_value=original):
            with self.assertRaises(FedRfc3161EnginePrerequisiteError):
                assess_fed_calendar_rfc3161_engine_prerequisites(*values, as_of=AS_OF)

    def test_55_upstream_failure_is_wrapped(self):
        values = inputs()
        with patch("backend.macro.fed_calendar_rfc3161_engine_prerequisites.prepare_fed_calendar_crypto_adapter",
                   side_effect=FedCryptoAdapterError("upstream forged")):
            with self.assertRaises(FedRfc3161EnginePrerequisiteError):
                assess_fed_calendar_rfc3161_engine_prerequisites(*values, as_of=AS_OF)

    def test_56_bad_adapter_type_rejected(self):
        values = inputs()
        values = (*values[:-2], object(), values[-1])
        with self.assertRaises(TypeError):
            assess_fed_calendar_rfc3161_engine_prerequisites(*values, as_of=AS_OF)

    def test_57_reference_order_independent_and_two_targets(self):
        t1, t2 = target(), target(claim_id="TOKEN_002")
        base = adapter(targets=(t1, t2))
        ms = (material(), material(claim_id="TOKEN_002"))
        vs = (_verifier_input(), _verifier_input(claim_id="TOKEN_002"))
        ps = (_policy(), _policy(claim_id="TOKEN_002"))
        r1 = run(base=base, mats=ms, verify_inputs=vs, policies=ps)
        r2 = run(base=base, mats=reversed(ms), verify_inputs=vs, policies=ps)
        self.assertEqual(r1.diagnostic_manifest_sha256, r2.diagnostic_manifest_sha256)
        self.assertEqual(r1.target_count, 2)

    def test_58_missing_second_reference_does_not_borrow_first(self):
        base = adapter(targets=(target(), target(claim_id="TOKEN_002")))
        r = run(base=base)
        self.assertEqual(r.targets[1].state, W.BLOCKED_INCOMPLETE_REFERENCE_PACKAGE)

    def test_59_duplicate_reference_identity_rejected(self):
        with self.assertRaises(FedRfc3161EnginePrerequisiteError):
            run(mats=(material(), material()))

    def test_60_unknown_token_work_order_rejected(self):
        with self.assertRaises(FedRfc3161EnginePrerequisiteError):
            run(mats=(material(claim_id="UNKNOWN"),))

    def test_61_wrong_reference_model_rejected(self):
        with self.assertRaises(FedRfc3161EnginePrerequisiteError):
            run(mats=({"provider_id": "TSA_A"},))

    def test_62_reference_cap_enforced(self):
        with self.assertRaises(FedRfc3161EnginePrerequisiteError):
            run(mats=repeat(material(), MAX_ENGINE_PROVISIONING_REFERENCES+1))

    def test_63_reference_generator_consumed_once(self):
        r = run(mats=(x for x in (material(),)))
        self.assertEqual(r.review_package_candidate_count, 1)

    def test_64_all_upstream_iterables_consumed_once(self):
        values = inputs(
            claims=(x for x in ()), captures=(x for x in ()),
            verify_inputs=(x for x in (_verifier_input(),)),
            policies=(x for x in (_policy(),)),
        )
        self.assertEqual(run(arguments=values).review_package_candidate_count, 1)

    def test_65_mutated_frozen_reference_revalidated(self):
        v = material()
        object.__setattr__(v, "claimed_capabilities", ("SIGNATURE_VERIFIED",))
        with self.assertRaises(FedRfc3161EnginePrerequisiteError):
            run(mats=(v,))

    def test_66_invalid_reference_id_rejected(self):
        for values in ({"provider_id": "bad space"}, {"claim_id": ""},
                       {"provider_id": "a"*65}):
            with self.subTest(values=values):
                with self.assertRaises(FedRfc3161EnginePrerequisiteError):
                    material(**values)

    def test_67_invalid_sha256_reference_rejected(self):
        for values in ({"claimed_engine_build_sha256": "ABC"},
                       {"claimed_document_sha256": "A"*64},
                       {"claimed_trust_store_sha256": True},
                       {"claimed_request_der_sha256": 123}):
            with self.subTest(values=values):
                with self.assertRaises(FedRfc3161EnginePrerequisiteError):
                    material(**values)

    def test_68_weakened_profile_rejected(self):
        with self.assertRaises(FedRfc3161EnginePrerequisiteError):
            material(claimed_profile="SHA1_WEAKENED")

    def test_69_unknown_success_capability_rejected(self):
        with self.assertRaises(FedRfc3161EnginePrerequisiteError):
            material(claimed_capabilities=("SIGNATURE_VERIFIED",))

    def test_70_duplicate_out_of_order_capabilities_rejected(self):
        for caps in ((REQUIRED_ENGINE_CAPABILITIES[0],)*2,
                     tuple(reversed(REQUIRED_ENGINE_CAPABILITIES)),
                     list(REQUIRED_ENGINE_CAPABILITIES),
                     (True,)):
            with self.subTest(caps=str(caps)[:70]):
                with self.assertRaises(FedRfc3161EnginePrerequisiteError):
                    material(claimed_capabilities=caps)

    def test_71_naive_or_wrong_type_claimed_observation_rejected(self):
        for value in (datetime(2026, 10, 14), "2026-10-14", True):
            with self.subTest(value=value):
                with self.assertRaises(FedRfc3161EnginePrerequisiteError):
                    material(claimed_material_observed_at=value)

    def test_72_invalid_asof_rejected(self):
        values = inputs()
        for value in (datetime(2026, 10, 14), None, "2026-10-14"):
            with self.subTest(value=value):
                with self.assertRaises(FedRfc3161EnginePrerequisiteError):
                    assess_fed_calendar_rfc3161_engine_prerequisites(*values, as_of=value)

    def test_73_none_collections_rejected(self):
        values = inputs()
        for index in (4, 6, 9, 11, 14):
            parts = list(values)
            parts[index] = None
            with self.subTest(index=index):
                with self.assertRaises(TypeError):
                    assess_fed_calendar_rfc3161_engine_prerequisites(*parts, as_of=AS_OF)

    def test_74_missing_upstream_token_or_document_never_admitted(self):
        base = adapter(targets=(target(token_digest=None, document_digest=None),))
        r = run(base=base)
        self.assertEqual(r.targets[0].state, W.BLOCKED_INCOMPLETE_REFERENCE_PACKAGE)
        self.assertFalse(r.document_imprint_verified)

    def test_75_conflicting_upstream_work_order_never_admitted(self):
        base = adapter(targets=(target(state=FedAdapterHandoffState.BLOCKED_CONFLICT),))
        self.assertEqual(run(base=base).targets[0].state, W.BLOCKED_UPSTREAM_OR_LOCAL_CONFLICT)

    def test_76_unsupported_upstream_token_never_admitted(self):
        base = adapter(targets=(target(state=FedAdapterHandoffState.BLOCKED_UNSUPPORTED_ENCODING),))
        self.assertEqual(run(base=base).targets[0].state, W.BLOCKED_INCOMPLETE_REFERENCE_PACKAGE)

    def test_77_no_token_claims_still_blocked(self):
        base = adapter(targets=())
        r = run(base=base, mats=())
        self.assertEqual((r.target_count, r.review_package_candidate_count), (0, 0))
        self.assertIn("FED_RFC3161_ENGINE_NO_TIMESTAMP_WORK_ITEMS", r.blockers)
        self.assertFalse(r.rfc3161_signature_verified)

    def test_78_changed_material_claim_changes_manifest_not_authority(self):
        r1 = run()
        r2 = run(mats=(material(claimed_engine_build_sha256="9"*64),))
        self.assertNotEqual(r1.diagnostic_manifest_sha256, r2.diagnostic_manifest_sha256)
        self.assertEqual(r1.targets[0].state, r2.targets[0].state)
        self.assertFalse(r2.trusted_engine_installed)

    def test_79_report_does_not_include_evidence_urls_or_bytes(self):
        r = run()
        values = repr(r)
        self.assertNotIn("archive.example", values)
        self.assertNotIn("BEGIN CERTIFICATE", values)
        self.assertNotIn("FAKE_CMS", values)
        self.assertNotIn("\\x30\\x82", values)

    def test_80_no_dynamic_subprocess_network_database_imports(self):
        import backend.macro.fed_calendar_rfc3161_engine_prerequisites as module
        tree = ast.parse(inspect.getsource(module))
        imports = [alias.name for node in ast.walk(tree)
                   if isinstance(node, ast.Import) for alias in node.names]
        self.assertFalse(set(imports) & {"socket", "urllib", "subprocess", "ctypes",
                                         "importlib", "os", "psycopg2", "requests"})

    def test_81_receipts_are_deterministic(self):
        self.assertEqual(run().diagnostic_manifest_sha256, run().diagnostic_manifest_sha256)

    def test_82_frozen_outputs_and_requirements(self):
        r = run()
        with self.assertRaises(FrozenInstanceError):
            r.diagnostic_manifest_sha256 = "9"*64
        with self.assertRaises(FrozenInstanceError):
            r.targets[0].state = W.BLOCKED_INCOMPLETE_REFERENCE_PACKAGE
        with self.assertRaises(FrozenInstanceError):
            r.targets[0].checks[0].state = S.CONFLICT

    def test_83_forged_empty_blockers_cannot_authorize(self):
        forged = replace(run(), blockers=(), targets=(), review_package_candidate_count=99)
        self.assertFalse(forged.council_calendar_admission_authorized)
        self.assertFalse(forged.rfc3161_signature_verified)
        with self.assertRaises(FedRfc3161EngineUnavailable):
            forged.require_trusted_verifier_execution()

    def test_84_no_caller_controlled_signature_verified_keyword(self):
        with self.assertRaises(TypeError):
            material(signature_verified=True)

    def test_85_source_url_preserved_from_canonical_adapter(self):
        self.assertEqual(run().source_url, SOURCE)

    def test_86_refers_to_rfc5816_not_sha1_exclusive_essid(self):
        names = set(REQUIRED_ENGINE_CAPABILITIES)
        self.assertIn("VERIFY_ESS_CERTID_V2_TSA_SIGNER_CERTIFICATE_BINDING", names)
        self.assertIn("VERIFY_SIGNED_SHA256_IMPRINT_AGAINST_DISTINCT_DOCUMENT", names)
        self.assertFalse(any("SHA1_ONLY" in x for x in names))


    def test_87_real_f4514_fixture_round_trip_is_still_non_authorizing(self):
        """Exercise the actual previous-milestone chain when installed."""
        from backend.macro.test_fed_calendar_crypto_adapter import (
            AS_OF as FIXTURE_AS_OF, package as previous_package,
        )
        from backend.macro.fed_calendar_crypto_adapter import prepare_fed_calendar_crypto_adapter
        packed = previous_package(as_of=FIXTURE_AS_OF)
        handoff = prepare_fed_calendar_crypto_adapter(*packed, as_of=FIXTURE_AS_OF)
        self.assertEqual(len(handoff.targets), 1)
        token_target = handoff.targets[0]
        candidate_material = material(
            claimed_token_sha256=token_target.token_body_sha256,
            claimed_document_sha256=token_target.document_body_sha256,
            claimed_policy_revision_sha256=token_target.policy_revision_reference_sha256,
            claimed_tsa_trust_anchor_sha256=(
                packed[11][0].pinned_tsa_anchor_certificate_sha256
            ),
            claimed_revocation_evidence_sha256=(
                packed[9][0].claimed_revocation_evidence_sha256
            ),
            claimed_request_der_sha256=(packed[9][0].claimed_request_der_sha256),
            claimed_material_observed_at=FIXTURE_AS_OF - timedelta(minutes=1),
        )
        result = assess_fed_calendar_rfc3161_engine_prerequisites(
            *packed, handoff, (candidate_material,), as_of=FIXTURE_AS_OF,
        )
        self.assertEqual(result.canonical_adapter_sha256, handoff.diagnostic_manifest_sha256)
        self.assertEqual(result.review_package_candidate_count, 1)
        self.assertFalse(result.trusted_engine_installed)
        self.assertFalse(result.rfc3161_signature_verified)

    def test_88_real_f4514_fixture_rejects_forged_adapter_report(self):
        from backend.macro.test_fed_calendar_crypto_adapter import (
            AS_OF as FIXTURE_AS_OF, package as previous_package,
        )
        from backend.macro.fed_calendar_crypto_adapter import prepare_fed_calendar_crypto_adapter
        packed = previous_package(as_of=FIXTURE_AS_OF)
        handoff = prepare_fed_calendar_crypto_adapter(*packed, as_of=FIXTURE_AS_OF)
        forgery = replace(handoff, diagnostic_manifest_sha256="f" * 64)
        with self.assertRaises(FedRfc3161EnginePrerequisiteError):
            assess_fed_calendar_rfc3161_engine_prerequisites(
                *packed, forgery, (), as_of=FIXTURE_AS_OF,
            )

    def test_89_real_f4514_fixture_rejects_stale_asof(self):
        from backend.macro.test_fed_calendar_crypto_adapter import (
            AS_OF as FIXTURE_AS_OF, package as previous_package,
        )
        from backend.macro.fed_calendar_crypto_adapter import prepare_fed_calendar_crypto_adapter
        packed = previous_package(as_of=FIXTURE_AS_OF)
        handoff = prepare_fed_calendar_crypto_adapter(*packed, as_of=FIXTURE_AS_OF)
        with self.assertRaises(FedRfc3161EnginePrerequisiteError):
            assess_fed_calendar_rfc3161_engine_prerequisites(
                *packed, handoff, (), as_of=FIXTURE_AS_OF+timedelta(hours=1),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

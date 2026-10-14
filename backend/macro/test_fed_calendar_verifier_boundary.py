"""F4.5.13 deterministic, offline sealed verifier execution-boundary tests.

Run: python -m backend.macro.test_fed_calendar_verifier_boundary
No network, ASN.1/CMS calls, verifier callbacks, DB, Council, broker or HMM.
"""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from itertools import repeat
import unittest

from backend.macro.fed_calendar_crypto_prerequisites import (
    FedCryptoPrerequisitesError,
    assess_fed_calendar_crypto_prerequisites,
    SHA256_OID,
    TIMESTAMPING_EKU_OID,
    TSTINFO_CONTENT_TYPE_OID,
)
from backend.macro.fed_calendar_independent_evidence import (
    FedExternalEvidenceKind, FedClaimedTimePrecision,
)
from backend.macro.fed_calendar_verifier_boundary import (
    FedBoundaryCheckState,
    FedTrustedVerificationUnavailable,
    FedTrustedVerifierBoundaryReport,
    FedVerifierBoundaryError,
    FedVerifierPolicyReference,
    FedWorkOrderState,
    MAX_TRUST_POLICY_REFERENCES,
    NON_POLICY_OIDS,
    STRICT_VERIFIER_CONTROLS,
    VERIFIER_BOUNDARY_CHECKS,
    build_fed_calendar_verifier_boundary,
)
from backend.macro.test_fed_calendar_crypto_prerequisites import (
    ROOT, AS_OF, CAPTURE_END, build_chain, references,
    verifier_input,
)
from backend.macro.test_fed_calendar_artifact_intake import artifact
from backend.macro.test_fed_calendar_independent_evidence import claim


POLICY = "1.3.6.1.4.1.55555.3161.1"  # Synthetic claimed policy, NOT approved TSA.
POLICY2 = "1.3.6.1.4.1.55555.3161.2"
REVISION = "d" * 64


def configured_input(**changes):
    values = {"expected_tsa_policy_oid": POLICY}
    values.update(changes)
    return replace(verifier_input(), **values)


def policy(**changes):
    values = dict(
        provider_id="TSA_A",
        claim_id="TOKEN_001",
        policy_revision_sha256=REVISION,
        pinned_tsa_anchor_certificate_sha256=ROOT,
        allowed_tsa_policy_oids=(POLICY,),
    )
    values.update(changes)
    return FedVerifierPolicyReference(**values)


def prepared_chain(*, refs=None, captures=None, inputs=None, as_of=AS_OF):
    chain, retained, intake, authenticity = build_chain(refs, captures, as_of=as_of)
    inputs = (configured_input(),) if inputs is None else tuple(inputs)
    upstream = assess_fed_calendar_crypto_prerequisites(
        *chain, retained, intake, authenticity, inputs, as_of=as_of,
    )
    return chain, retained, intake, authenticity, inputs, upstream


def assess(*, policies=None, prepared=None, as_of=AS_OF):
    if prepared is None:
        prepared = prepared_chain(as_of=as_of)
    chain, captures, intake, authenticity, inputs, prior = prepared
    policies = (policy(),) if policies is None else policies
    return build_fed_calendar_verifier_boundary(
        *chain, captures, intake, authenticity, inputs, prior,
        policies, as_of=as_of,
    )


def check(item, index):
    return item.checks[index].state


class FedCalendarVerifierBoundaryTests(unittest.TestCase):

    def test_01_canonical_upstream_binding(self):
        prepared = prepared_chain()
        result = assess(prepared=prepared)
        self.assertIsInstance(result, FedTrustedVerifierBoundaryReport)
        self.assertEqual(result.canonical_crypto_prerequisites_sha256,
                         prepared[-1].diagnostic_manifest_sha256)

    def test_02_one_token_one_policy_work_order(self):
        result = assess()
        self.assertEqual(result.token_claim_count, 1)
        self.assertEqual(result.policy_reference_count, 1)
        self.assertEqual([(w.provider_id, w.claim_id) for w in result.work_orders],
                         [("TSA_A", "TOKEN_001")])

    def test_03_unique_check_codes(self):
        codes = tuple(c.code for c in assess().work_orders[0].checks)
        self.assertEqual(codes, VERIFIER_BOUNDARY_CHECKS)
        self.assertEqual(len(codes), len(set(codes)))

    def test_04_strict_controls_cannot_be_omitted(self):
        controls = assess().strict_verifier_controls
        self.assertEqual(controls, STRICT_VERIFIER_CONTROLS)
        self.assertEqual(len(controls), len(set(controls)))
        self.assertEqual(len(controls), 8)

    def test_05_token_bytes_are_only_local_candidate(self):
        self.assertEqual(check(assess().work_orders[0], 0),
                         FedBoundaryCheckState.LOCAL_CANDIDATE_ONLY)

    def test_06_document_bytes_are_only_local_candidate(self):
        result = assess().work_orders[0]
        self.assertIsNotNone(result.document_body_sha256)
        self.assertEqual(check(result, 1), FedBoundaryCheckState.LOCAL_CANDIDATE_ONLY)

    def test_07_policy_configuration_is_not_proven_trust(self):
        self.assertEqual(check(assess().work_orders[0], 2),
                         FedBoundaryCheckState.UNTRUSTED_REFERENCE_ONLY)

    def test_08_anchor_agreement_is_untrusted(self):
        self.assertEqual(check(assess().work_orders[0], 3),
                         FedBoundaryCheckState.UNTRUSTED_REFERENCE_ONLY)
        self.assertFalse(assess().tsa_certificate_chain_verified)

    def test_09_tsa_policy_agreement_is_untrusted(self):
        self.assertEqual(check(assess().work_orders[0], 4),
                         FedBoundaryCheckState.UNTRUSTED_REFERENCE_ONLY)

    def test_10_nonce_request_are_untrusted(self):
        self.assertEqual(check(assess().work_orders[0], 5),
                         FedBoundaryCheckState.UNTRUSTED_REFERENCE_ONLY)

    def test_11_revocation_digest_only_untrusted(self):
        self.assertEqual(check(assess().work_orders[0], 6),
                         FedBoundaryCheckState.UNTRUSTED_REFERENCE_ONLY)

    def test_12_historical_asof_only_consistent_locally(self):
        self.assertEqual(check(assess().work_orders[0], 7),
                         FedBoundaryCheckState.UNTRUSTED_REFERENCE_ONLY)
        self.assertFalse(assess().historical_capture_time_attested)

    def test_13_all_real_crypto_and_admission_steps_pending(self):
        order = assess().work_orders[0]
        for idx in range(8, len(VERIFIER_BOUNDARY_CHECKS)):
            self.assertEqual(check(order, idx),
                             FedBoundaryCheckState.TRUSTED_EXECUTION_REQUIRED)

    def test_14_complete_input_is_still_only_candidate(self):
        order = assess().work_orders[0]
        self.assertEqual(order.state, FedWorkOrderState.INPUT_CANDIDATE_ONLY)
        self.assertIn("FED_VERIFIER_CANDIDATE_AWAITING_INDEPENDENT_ENGINE",
                      assess().blockers)

    def test_15_all_verification_and_admission_flags_false(self):
        result = assess()
        for name in (
            "trusted_verification_engine_installed", "rfc3161_signature_verified",
            "tsa_certificate_chain_verified", "historical_capture_time_attested",
            "historical_source_version_attested", "council_calendar_admission_authorized",
            "historical_replay_authorized", "persistence_authorized",
        ):
            self.assertIs(getattr(result, name), False, name)

    def test_16_all_execution_and_delivery_methods_refuse(self):
        result = assess()
        for fn in (
            result.require_trusted_verification_execution,
            result.require_council_calendar_delivery,
            result.require_historical_replay,
            result.require_persistence,
        ):
            with self.assertRaises(FedTrustedVerificationUnavailable):
                fn()

    def test_17_caller_claimed_verifier_success_never_accepted(self):
        for alleged in (True, {"verified": True}, object(), None):
            with self.subTest(alleged=str(alleged)):
                with self.assertRaises(FedTrustedVerificationUnavailable):
                    assess().accept_external_verification_assertion(alleged)

    def test_18_cleared_blockers_cannot_enable_execution(self):
        forged = replace(assess(), blockers=(), work_orders=())
        self.assertFalse(forged.rfc3161_signature_verified)
        self.assertFalse(forged.historical_replay_authorized)
        with self.assertRaises(FedTrustedVerificationUnavailable):
            forged.require_trusted_verification_execution()

    def test_19_missing_policy_is_incomplete_not_implicitly_defaulted(self):
        order = assess(policies=()).work_orders[0]
        self.assertEqual(order.state, FedWorkOrderState.BLOCKED_INCOMPLETE)
        for idx in (2, 3, 4):
            self.assertEqual(check(order, idx), FedBoundaryCheckState.MISSING)

    def test_20_missing_verifier_input_is_incomplete(self):
        order = assess(prepared=prepared_chain(inputs=())).work_orders[0]
        self.assertEqual(order.state, FedWorkOrderState.BLOCKED_INCOMPLETE)
        self.assertEqual(check(order, 3), FedBoundaryCheckState.MISSING)
        self.assertEqual(check(order, 5), FedBoundaryCheckState.MISSING)

    def test_21_missing_token_capture_is_incomplete(self):
        chain, captures, _, _ = build_chain()
        prepared = prepared_chain(captures=(captures[0],))
        order = assess(prepared=prepared).work_orders[0]
        self.assertEqual(check(order, 0), FedBoundaryCheckState.MISSING)
        self.assertEqual(order.state, FedWorkOrderState.BLOCKED_INCOMPLETE)

    def test_22_missing_html_archive_is_incomplete(self):
        _, captures, _, _ = build_chain()
        prepared = prepared_chain(captures=(captures[1],))
        order = assess(prepared=prepared).work_orders[0]
        self.assertEqual(check(order, 1), FedBoundaryCheckState.MISSING)
        self.assertEqual(order.state, FedWorkOrderState.BLOCKED_INCOMPLETE)

    def test_23_document_digest_conflict_blocks_execution(self):
        inputs = (configured_input(),)
        web, token = references()
        conflict_ref = replace(web, claimed_document_body_sha256="e" * 64)
        p = prepared_chain(refs=(conflict_ref, token), inputs=inputs)
        order = assess(prepared=p).work_orders[0]
        self.assertEqual(check(order, 1), FedBoundaryCheckState.CONFLICT)
        self.assertEqual(order.state, FedWorkOrderState.BLOCKED_CONFLICT)

    def test_24_anchor_mismatch_blocks_execution(self):
        order = assess(policies=(policy(pinned_tsa_anchor_certificate_sha256="e" * 64),)).work_orders[0]
        self.assertEqual(check(order, 3), FedBoundaryCheckState.CONFLICT)
        self.assertEqual(order.state, FedWorkOrderState.BLOCKED_CONFLICT)

    def test_25_disallowed_tsa_policy_blocks_execution(self):
        order = assess(policies=(policy(allowed_tsa_policy_oids=(POLICY2,)),)).work_orders[0]
        self.assertEqual(check(order, 4), FedBoundaryCheckState.CONFLICT)
        self.assertEqual(order.state, FedWorkOrderState.BLOCKED_CONFLICT)

    def test_26_multiple_allowed_policies_do_not_imply_approval(self):
        order = assess(policies=(policy(allowed_tsa_policy_oids=(POLICY, POLICY2)),)).work_orders[0]
        self.assertEqual(order.state, FedWorkOrderState.INPUT_CANDIDATE_ONLY)
        self.assertFalse(assess(policies=(policy(allowed_tsa_policy_oids=(POLICY, POLICY2)),)).rfc3161_signature_verified)

    def test_27_claimed_untrusted_structure_oid_does_not_pass_policy(self):
        p = prepared_chain(inputs=(verifier_input(),))
        order = assess(prepared=p).work_orders[0]
        self.assertEqual(check(order, 4), FedBoundaryCheckState.CONFLICT)

    def test_28_no_claimed_anchor_makes_incomplete(self):
        p = prepared_chain(inputs=(configured_input(claimed_tsa_trust_anchor_sha256=None),))
        self.assertEqual(check(assess(prepared=p).work_orders[0], 3),
                         FedBoundaryCheckState.MISSING)

    def test_29_no_claimed_policy_makes_incomplete(self):
        p = prepared_chain(inputs=(configured_input(expected_tsa_policy_oid=None),))
        self.assertEqual(check(assess(prepared=p).work_orders[0], 4),
                         FedBoundaryCheckState.MISSING)

    def test_30_missing_nonce_and_trusted_clock_is_incomplete(self):
        p = prepared_chain(inputs=(configured_input(
            claimed_request_nonce=None, claimed_request_der_sha256=None,
            claimed_external_reference_at=None),))
        self.assertEqual(check(assess(prepared=p).work_orders[0], 5),
                         FedBoundaryCheckState.MISSING)

    def test_31_only_reported_trusted_clock_is_still_untrusted(self):
        p = prepared_chain(inputs=(configured_input(
            claimed_request_nonce=None, claimed_request_der_sha256=None,
            claimed_external_reference_at=AS_OF - timedelta(hours=2)),))
        self.assertEqual(check(assess(prepared=p).work_orders[0], 5),
                         FedBoundaryCheckState.UNTRUSTED_REFERENCE_ONLY)
        self.assertFalse(assess(prepared=p).historical_capture_time_attested)

    def test_32_no_revocation_reference_is_incomplete(self):
        p = prepared_chain(inputs=(configured_input(claimed_revocation_evidence_sha256=None),))
        self.assertEqual(check(assess(prepared=p).work_orders[0], 6),
                         FedBoundaryCheckState.MISSING)

    def test_33_future_claimed_reference_clock_is_conflict(self):
        p = prepared_chain(inputs=(configured_input(
            claimed_external_reference_at=AS_OF + timedelta(seconds=1)),))
        order = assess(prepared=p).work_orders[0]
        self.assertEqual(check(order, 7), FedBoundaryCheckState.CONFLICT)
        self.assertEqual(order.state, FedWorkOrderState.BLOCKED_CONFLICT)

    def test_34_capture_after_asof_is_conflict(self):
        as_of = CAPTURE_END - timedelta(seconds=1)
        p = prepared_chain(as_of=as_of)
        order = assess(prepared=p, as_of=as_of).work_orders[0]
        self.assertEqual(check(order, 7), FedBoundaryCheckState.CONFLICT)

    def test_67_linked_archive_captured_after_asof_blocks_token_work(self):
        _, captures, _, _ = build_chain()
        later = replace(
            captures[0],
            request_started_at=AS_OF + timedelta(seconds=1),
            response_completed_at=AS_OF + timedelta(seconds=2),
        )
        p = prepared_chain(captures=(later, captures[1]))
        order = assess(prepared=p).work_orders[0]
        self.assertEqual(check(order, 7), FedBoundaryCheckState.CONFLICT)
        self.assertEqual(order.state, FedWorkOrderState.BLOCKED_CONFLICT)

    def test_68_linked_archive_date_only_intraday_is_incomplete(self):
        web, token = references()
        web = replace(
            web,
            claimed_time_precision=FedClaimedTimePrecision.UTC_DATE_ONLY,
            claimed_evidence_time=date(2026, 10, 14),
        )
        p = prepared_chain(refs=(web, token))
        order = assess(prepared=p).work_orders[0]
        self.assertEqual(check(order, 7), FedBoundaryCheckState.MISSING)
        self.assertEqual(order.state, FedWorkOrderState.BLOCKED_INCOMPLETE)

    def test_35_no_timestamp_claims_is_still_blocked(self):
        p = prepared_chain(refs=(references()[0],), inputs=())
        result = assess(prepared=p, policies=())
        self.assertEqual(result.work_orders, ())
        self.assertIn("FED_VERIFIER_NO_TIMESTAMP_TOKEN_WORK_ITEMS", result.blockers)
        self.assertFalse(result.historical_replay_authorized)

    def test_36_two_token_claims_remain_independent_work_orders(self):
        web, token = references()
        token2 = claim(provider_id="TSA_B", claim_id="TOKEN_002",
                       kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN)
        second = replace(configured_input(), provider_id="TSA_B", claim_id="TOKEN_002")
        p = prepared_chain(refs=(web, token, token2), inputs=(configured_input(), second))
        policy2 = policy(provider_id="TSA_B", claim_id="TOKEN_002")
        result = assess(prepared=p, policies=(policy(), policy2))
        self.assertEqual(result.token_claim_count, 2)
        self.assertEqual(result.policy_reference_count, 2)
        self.assertEqual(len(result.work_orders), 2)
        self.assertFalse(result.tsa_certificate_chain_verified)

    def test_37_missing_second_policy_does_not_borrow_first(self):
        web, token = references()
        token2 = claim(provider_id="TSA_B", claim_id="TOKEN_002",
                       kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN)
        second = replace(configured_input(), provider_id="TSA_B", claim_id="TOKEN_002")
        p = prepared_chain(refs=(web, token, token2), inputs=(configured_input(), second))
        result = assess(prepared=p)
        self.assertEqual(result.work_orders[0].state, FedWorkOrderState.INPUT_CANDIDATE_ONLY)
        self.assertEqual(result.work_orders[1].state, FedWorkOrderState.BLOCKED_INCOMPLETE)

    def test_38_policy_order_canonicalized_for_two_tokens(self):
        web, token = references()
        token2 = claim(provider_id="TSA_B", claim_id="TOKEN_002",
                       kind=FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN)
        second = replace(configured_input(), provider_id="TSA_B", claim_id="TOKEN_002")
        p = prepared_chain(refs=(web, token, token2), inputs=(configured_input(), second))
        a, b = policy(), policy(provider_id="TSA_B", claim_id="TOKEN_002")
        one = assess(prepared=p, policies=(a, b))
        two = assess(prepared=p, policies=(b, a))
        self.assertEqual(one.diagnostic_manifest_sha256, two.diagnostic_manifest_sha256)

    def test_39_artifact_order_changes_no_work_order_digest(self):
        _, captures, _, _ = build_chain()
        a = assess(prepared=prepared_chain(captures=captures))
        b = assess(prepared=prepared_chain(captures=tuple(reversed(captures))))
        self.assertEqual(a.diagnostic_manifest_sha256, b.diagnostic_manifest_sha256)

    def test_40_timezone_equivalent_asof_stable(self):
        other = AS_OF.astimezone(timezone(timedelta(hours=-4)))
        a = assess()
        b = assess(prepared=prepared_chain(as_of=other), as_of=other)
        self.assertEqual(a.diagnostic_manifest_sha256, b.diagnostic_manifest_sha256)

    def test_41_changed_asof_changes_receipt_for_new_chain(self):
        later = AS_OF + timedelta(seconds=5)
        a = assess()
        b = assess(prepared=prepared_chain(as_of=later), as_of=later)
        self.assertNotEqual(a.diagnostic_manifest_sha256, b.diagnostic_manifest_sha256)

    def test_42_changed_asof_rejects_stale_upstream(self):
        with self.assertRaisesRegex(FedVerifierBoundaryError, "revalidated"):
            assess(prepared=prepared_chain(), as_of=AS_OF + timedelta(seconds=5))

    def test_43_policy_revision_changes_receipt_not_authority(self):
        before = assess()
        after = assess(policies=(policy(policy_revision_sha256="e" * 64),))
        self.assertNotEqual(before.diagnostic_manifest_sha256, after.diagnostic_manifest_sha256)
        self.assertFalse(after.rfc3161_signature_verified)

    def test_44_anchor_reference_changes_policy_manifest(self):
        before = assess()
        after = assess(policies=(policy(pinned_tsa_anchor_certificate_sha256="e" * 64),))
        self.assertNotEqual(before.policy_reference_manifest_sha256,
                            after.policy_reference_manifest_sha256)
        self.assertFalse(after.tsa_certificate_chain_verified)

    def test_45_duplicate_policy_identity_rejected(self):
        with self.assertRaisesRegex(FedVerifierBoundaryError, "Duplicate"):
            assess(policies=(policy(), policy()))

    def test_46_policy_referencing_unknown_token_rejected(self):
        with self.assertRaisesRegex(FedVerifierBoundaryError, "catalogued"):
            assess(policies=(policy(claim_id="NOT_A_TOKEN"),))

    def test_47_non_model_policy_rejected(self):
        with self.assertRaisesRegex(FedVerifierBoundaryError, "Exact"):
            assess(policies=(object(),))

    def test_48_mutated_frozen_policy_revalidated(self):
        p = policy()
        object.__setattr__(p, "require_certificate_revocation", False)
        with self.assertRaisesRegex(FedVerifierBoundaryError, "cannot disable"):
            assess(policies=(p,))

    def test_49_policy_count_limit_enforced_before_duplicate_check(self):
        with self.assertRaisesRegex(FedVerifierBoundaryError, "count exceeded"):
            assess(policies=repeat(policy(), MAX_TRUST_POLICY_REFERENCES + 1))

    def test_50_invalid_asof_rejected(self):
        for bad in (None, "2026-10-14", AS_OF.replace(tzinfo=None)):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(FedVerifierBoundaryError, "timezone-aware"):
                    assess(as_of=bad, prepared=prepared_chain())

    def test_51_none_claims_captures_verifier_inputs_or_policies_rejected(self):
        p = prepared_chain()
        chain, captures, intake, authenticity, inputs, prior = p
        for idx in (4, 6, 9, 11):
            args = [*chain, captures, intake, authenticity, inputs, prior, (policy(),)]
            args[idx] = None
            with self.subTest(index=idx):
                with self.assertRaises(TypeError):
                    build_fed_calendar_verifier_boundary(*args, as_of=AS_OF)

    def test_52_input_generators_materialized_once(self):
        chain, captures, intake, authenticity, inputs, prior = prepared_chain()
        result = build_fed_calendar_verifier_boundary(
            *chain, iter(captures), intake, authenticity, iter(inputs), prior,
            iter((policy(),)), as_of=AS_OF,
        )
        self.assertEqual(result.work_orders[0].state,
                         FedWorkOrderState.INPUT_CANDIDATE_ONLY)

    def test_53_forged_prerequisites_blockers_rejected(self):
        p = prepared_chain()
        corrupted = (*p[:-1], replace(p[-1], blockers=()))
        with self.assertRaisesRegex(FedVerifierBoundaryError, "differs"):
            assess(prepared=corrupted)

    def test_54_forged_prerequisites_digest_rejected(self):
        p = prepared_chain()
        corrupted = (*p[:-1], replace(p[-1], diagnostic_manifest_sha256="f" * 64))
        with self.assertRaisesRegex(FedVerifierBoundaryError, "differs"):
            assess(prepared=corrupted)

    def test_55_modified_source_acquisition_rejected(self):
        p = prepared_chain()
        chain, captures, intake, authentic, inputs, prior = p
        source = replace(chain[0], entity_body_sha256="f" * 64)
        with self.assertRaisesRegex(FedVerifierBoundaryError, "revalidated"):
            assess(prepared=((source, *chain[1:]), captures, intake,
                             authentic, inputs, prior))

    def test_56_receipt_excludes_raw_data_urls_and_certificates(self):
        order = assess().work_orders[0]
        report = assess()
        for name in (
            "entity_body", "raw_token", "token_bytes", "certificates",
            "request_der", "trust_anchor_der", "external_artifact_url",
            "source_snapshots", "calendar_events", "context",
        ):
            self.assertFalse(hasattr(report, name), name)
            self.assertFalse(hasattr(order, name), name)

    def test_57_output_is_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            assess().blockers = ()

    def test_58_tstinfo_content_type_not_allowed_as_tsa_policy(self):
        self.assertIn(TSTINFO_CONTENT_TYPE_OID, NON_POLICY_OIDS)
        with self.assertRaisesRegex(FedVerifierBoundaryError, "not a TSA policy"):
            policy(allowed_tsa_policy_oids=(TSTINFO_CONTENT_TYPE_OID,))

    def test_59_algorithm_and_eku_oid_not_accepted_as_tsa_policy(self):
        for bad in (SHA256_OID, TIMESTAMPING_EKU_OID, "1.2.840.113549.1.7.2"):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(FedVerifierBoundaryError, "not a TSA policy"):
                    policy(allowed_tsa_policy_oids=(bad,))

    def test_60_invalid_policy_oid_syntax_rejected(self):
        for bad in ("", "2.99", "1.40.12", "1.02.3", "3.1.1", "1.2.bad", ".1.2"):
            with self.subTest(bad=bad):
                with self.assertRaises(FedVerifierBoundaryError):
                    policy(allowed_tsa_policy_oids=(bad,))

    def test_61_policy_oids_sorted_unique_and_nonempty(self):
        for bad in ((), (POLICY2, POLICY), (POLICY, POLICY), [POLICY], (POLICY,) * 17):
            with self.subTest(bad=bad):
                with self.assertRaises(FedVerifierBoundaryError):
                    policy(allowed_tsa_policy_oids=bad)

    def test_62_mandatory_controls_cannot_be_disabled(self):
        for name in (
            "require_sha256_imprint", "require_critical_tsa_eku",
            "require_certificate_revocation", "require_nonce_or_trusted_time",
        ):
            for bad in (False, None, 1, "true"):
                with self.subTest(name=name, bad=bad):
                    with self.assertRaisesRegex(FedVerifierBoundaryError, "cannot disable"):
                        policy(**{name: bad})

    def test_63_invalid_policy_digests_and_identities(self):
        for changes in (
            dict(policy_revision_sha256="A" * 64),
            dict(pinned_tsa_anchor_certificate_sha256="0" * 63),
            dict(provider_id="lower"),
            dict(claim_id="wrong space"),
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(FedVerifierBoundaryError):
                    policy(**changes)

    def test_64_no_injected_success_keyword_supported(self):
        p = prepared_chain()
        chain, captures, intake, authenticity, inputs, prior = p
        with self.assertRaises(TypeError):
            build_fed_calendar_verifier_boundary(
                *chain, captures, intake, authenticity, inputs,
                prior, (policy(),), as_of=AS_OF, verified=True,
            )

    def test_65_stable_manifest_hashes(self):
        a = assess()
        b = assess()
        self.assertEqual(a.diagnostic_manifest_sha256, b.diagnostic_manifest_sha256)
        self.assertRegex(a.diagnostic_manifest_sha256, "^[0-9a-f]{64}$")
        self.assertRegex(a.policy_reference_manifest_sha256, "^[0-9a-f]{64}$")

    def test_66_no_council_or_db_dependencies_imported(self):
        import backend.macro.fed_calendar_verifier_boundary as module
        import inspect
        source = inspect.getsource(module)
        self.assertNotIn("from backend.agents", source)
        self.assertNotIn("import psycopg", source)
        self.assertNotIn("import requests", source)
        self.assertNotIn("import subprocess", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

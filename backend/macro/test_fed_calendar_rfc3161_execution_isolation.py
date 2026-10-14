"""F4.5.16: offline fail-closed verification isolation design tests.

Run: python -m backend.macro.test_fed_calendar_rfc3161_execution_isolation
Unit tests mock the already-reviewed F4.5.15 revalidator at the handoff;
final integration tests exercise F4.5.14 and F4.5.15 when installed.
No verifier binary, sandbox process, network, trust store, or trade is used.
"""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from itertools import repeat
from unittest.mock import patch
import ast
import inspect
import unittest

from backend.macro import fed_calendar_rfc3161_execution_isolation as impl
from backend.macro.fed_calendar_rfc3161_execution_isolation import (
    ISOLATION_CHECK_CODES,
    ISOLATION_PROFILE,
    MAX_CPU_BUDGET_MS,
    MAX_DOCUMENT_INPUT_BYTES,
    MAX_ISOLATION_CLAIMS,
    MAX_MEMORY_BUDGET_BYTES,
    MAX_TOKEN_INPUT_BYTES,
    REQUIRED_ISOLATION_CONTROLS,
    FedRfc3161ExecutionIsolationReport,
    FedRfc3161IsolationError,
    FedRfc3161IsolationReference,
    FedRfc3161IsolationUnavailable,
    IsolationCheckState as S,
    IsolationPlanState as W,
    assess_fed_calendar_rfc3161_execution_isolation,
)
from backend.macro.fed_calendar_rfc3161_engine_prerequisites import (
    FedRfc3161EnginePrerequisiteError,
    FedEngineWorkState,
    assess_fed_calendar_rfc3161_engine_prerequisites,
)
from backend.macro.test_fed_calendar_rfc3161_engine_prerequisites import (
    AS_OF,
    BUILD,
    LOCK,
    ANCHOR,
    TRUST_STORE,
    adapter as f4514_adapter,
    material,
    target,
    inputs as earlier_inputs,
    run as f4515_run,
    _verifier_input,
    _policy,
)

RUNTIME = "5" * 64
ISOLATION_POLICY = "6" * 64
SCHEMA = "7" * 64


def isolation(upstream=None, *, material_ref=None, claim_id="TOKEN_001", **overrides):
    base = dict(
        provider_id="TSA_A", claim_id=claim_id,
        claimed_adapter_receipt_sha256=upstream.canonical_adapter_sha256 if upstream else "5" * 64,
        claimed_engine_prerequisites_sha256=upstream.diagnostic_manifest_sha256 if upstream else "8" * 64,
        claimed_engine_build_sha256=(material_ref.claimed_engine_build_sha256
                                     if material_ref else BUILD),
        claimed_dependency_lock_sha256=(material_ref.claimed_dependency_lock_sha256
                                        if material_ref else LOCK),
        claimed_trust_store_sha256=(material_ref.claimed_trust_store_sha256
                                    if material_ref else TRUST_STORE),
        claimed_tsa_anchor_sha256=(material_ref.claimed_tsa_trust_anchor_sha256
                                   if material_ref else ANCHOR),
        claimed_isolation_runtime_sha256=RUNTIME,
        claimed_isolation_policy_sha256=ISOLATION_POLICY,
        claimed_result_schema_sha256=SCHEMA,
        claimed_material_observed_at=AS_OF - timedelta(minutes=10),
        claimed_controls=REQUIRED_ISOLATION_CONTROLS,
    )
    base.update(overrides)
    return FedRfc3161IsolationReference(**base)


def assemble(*, as_of=AS_OF, materials=None, base=None, verify_inputs=None,
             policies=None, iso_refs=None, claims=(), captures=(),
             old_args=None, upstream=None):
    if old_args is None:
        old_args = earlier_inputs(
            mats=(material(),) if materials is None else materials,
            base=base, verify_inputs=verify_inputs, policies=policies,
            claims=claims, captures=captures, as_of=as_of,
        )
    previous = f4515_run(arguments=old_args, as_of=as_of) if upstream is None else upstream
    refs = (isolation(previous, material_ref=old_args[-1][0]),) if iso_refs is None and old_args[-1] else (
        () if iso_refs is None else iso_refs
    )
    return old_args, previous, refs


def run(*, as_of=AS_OF, iso_refs=None, canonical=None, provided=None,
        upstream_error=None, **kwargs):
    args, previous, refs = assemble(as_of=as_of, iso_refs=iso_refs, **kwargs)
    provided = previous if provided is None else provided
    if callable(iso_refs):
        refs = iso_refs(previous, args[-1])
    with patch.object(impl, "assess_fed_calendar_rfc3161_engine_prerequisites",
                      side_effect=upstream_error if upstream_error is not None else None,
                      return_value=previous if canonical is None else canonical) as validated:
        result = assess_fed_calendar_rfc3161_execution_isolation(
            *args, provided, refs, as_of=as_of,
        )
        validated.assert_called_once()
    return result


def check(result, index=0, target_index=0):
    return result.targets[target_index].checks[index].state


def altered(field, value):
    return lambda prev, mats: (isolation(prev, material_ref=mats[0], **{field: value}),)


class FedCalendarRfc3161ExecutionIsolationTests(unittest.TestCase):
    def test_01_output_is_sealed_report(self):
        self.assertIsInstance(run(), FedRfc3161ExecutionIsolationReport)

    def test_02_binding_to_canonical_f4515_digest(self):
        r = run()
        self.assertEqual(r.canonical_engine_prerequisites_sha256, f4515_run().diagnostic_manifest_sha256)

    def test_03_binding_to_original_f4514_receipt(self):
        self.assertEqual(run().canonical_adapter_sha256, f4514_adapter().diagnostic_manifest_sha256)

    def test_04_one_target_and_one_isolation_reference(self):
        r = run()
        self.assertEqual((r.target_count, r.reference_count, r.plan_candidate_count), (1, 1, 1))

    def test_05_check_codes_unique_and_ordered(self):
        self.assertEqual(tuple(c.code for c in run().targets[0].checks), ISOLATION_CHECK_CODES)
        self.assertEqual(len(ISOLATION_CHECK_CODES), len(set(ISOLATION_CHECK_CODES)))

    def test_06_controls_have_unambiguous_sorted_unique_names(self):
        self.assertEqual(REQUIRED_ISOLATION_CONTROLS,
                         tuple(sorted(set(REQUIRED_ISOLATION_CONTROLS))))
        self.assertGreaterEqual(len(REQUIRED_ISOLATION_CONTROLS), 10)

    def test_07_strict_profile_is_nonexecuting(self):
        self.assertIn("NOEXEC", ISOLATION_PROFILE)
        self.assertEqual(isolation().claimed_profile, ISOLATION_PROFILE)

    def test_08_fully_matching_claims_still_not_executable(self):
        r = run()
        self.assertEqual(r.targets[0].state, W.NONEXECUTING_ISOLATION_PLAN_CANDIDATE_ONLY)
        self.assertGreater(len(r.blockers), 4)

    def test_09_all_authorization_flags_always_false(self):
        r = run()
        for field in (
            "trusted_engine_installed", "independently_provisioned_trust_store",
            "operating_system_sandbox_enforced", "verifier_executed",
            "rfc3161_signature_verified", "tsa_certificate_chain_verified",
            "signed_document_imprint_verified", "signed_timestamp_time_verified",
            "verified_output_admitted", "historical_source_version_attested",
            "council_admission_authorized", "historical_replay_authorized",
            "persistence_authorized",
        ):
            with self.subTest(field=field):
                self.assertIs(getattr(r, field), False)
        self.assertEqual(r.directional_trading_score, 0)

    def test_10_all_execution_delivery_methods_refuse(self):
        r = run()
        for method in (
            r.require_trusted_verifier_execution,
            r.require_authenticated_verifier_output,
            r.require_council_calendar_delivery,
            r.require_historical_replay,
            r.require_persistence,
        ):
            with self.subTest(method=method.__name__):
                with self.assertRaises(FedRfc3161IsolationUnavailable):
                    method()

    def test_11_untrusted_verifier_success_never_promoted(self):
        r = run()
        for claim in (True, False, {"signature_verified": True}, object(), None):
            with self.assertRaises(FedRfc3161IsolationUnavailable):
                r.accept_external_verification_assertion(claim)

    def test_12_upstream_state_only_reference_candidate(self):
        self.assertEqual(check(run(), 0), S.UPSTREAM_REFERENCE_ONLY)

    def test_13_matching_adapter_digest_is_only_claim(self):
        self.assertEqual(check(run(), 1), S.UNTRUSTED_CLAIM_MATCH_ONLY)

    def test_14_matching_engine_receipt_digest_only_claim(self):
        self.assertEqual(check(run(), 2), S.UNTRUSTED_CLAIM_MATCH_ONLY)

    def test_15_engine_build_hash_match_only_claim(self):
        self.assertEqual(check(run(), 3), S.UNTRUSTED_CLAIM_MATCH_ONLY)

    def test_16_dependency_lock_hash_match_only_claim(self):
        self.assertEqual(check(run(), 4), S.UNTRUSTED_CLAIM_MATCH_ONLY)

    def test_17_trust_store_and_anchor_match_only_claim(self):
        self.assertEqual(check(run(), 5), S.UNTRUSTED_CLAIM_MATCH_ONLY)

    def test_18_runtime_and_isolation_policy_artifact_present_only(self):
        self.assertEqual(check(run(), 6), S.UNTRUSTED_CLAIM_MATCH_ONLY)

    def test_19_result_schema_reference_is_not_verification(self):
        self.assertEqual(check(run(), 7), S.UNTRUSTED_CLAIM_MATCH_ONLY)

    def test_20_isolation_profile_is_not_sandbox(self):
        self.assertEqual(check(run(), 8), S.UNTRUSTED_CLAIM_MATCH_ONLY)
        self.assertFalse(run().operating_system_sandbox_enforced)

    def test_21_declared_denial_controls_are_not_enforced(self):
        self.assertEqual(check(run(), 9), S.UNTRUSTED_CLAIM_MATCH_ONLY)

    def test_22_resource_budgets_are_not_actual_limits(self):
        self.assertEqual(check(run(), 10), S.UNTRUSTED_CLAIM_MATCH_ONLY)
        self.assertFalse(run().verifier_executed)

    def test_23_claimed_observation_is_not_trusted_clock(self):
        self.assertEqual(check(run(), 11), S.UNTRUSTED_CLAIM_MATCH_ONLY)

    def test_24_all_remaining_checks_require_real_execution(self):
        r = run()
        self.assertTrue(all(check(r, x) is S.INDEPENDENT_EXECUTION_REQUIRED
                            for x in range(12, len(ISOLATION_CHECK_CODES))))

    def test_25_no_references_remains_blocked(self):
        r = run(iso_refs=())
        self.assertEqual(r.reference_count, 0)
        self.assertEqual(r.plan_candidate_count, 0)
        self.assertEqual(r.targets[0].state, W.BLOCKED_INCOMPLETE_ISOLATION_PACKAGE)

    def test_26_missing_adapter_binding_blocks(self):
        self.assertEqual(check(run(iso_refs=altered("claimed_adapter_receipt_sha256", None)), 1), S.MISSING)

    def test_27_wrong_adapter_binding_conflicts(self):
        self.assertEqual(check(run(iso_refs=altered("claimed_adapter_receipt_sha256", "f"*64)), 1), S.CONFLICT)

    def test_28_missing_engine_receipt_binding_blocks(self):
        self.assertEqual(check(run(iso_refs=altered("claimed_engine_prerequisites_sha256", None)), 2), S.MISSING)

    def test_29_wrong_engine_receipt_binding_conflicts(self):
        self.assertEqual(check(run(iso_refs=altered("claimed_engine_prerequisites_sha256", "f"*64)), 2), S.CONFLICT)

    def test_30_missing_engine_build_blocks(self):
        self.assertEqual(check(run(iso_refs=altered("claimed_engine_build_sha256", None)), 3), S.MISSING)

    def test_31_wrong_engine_build_conflicts(self):
        self.assertEqual(check(run(iso_refs=altered("claimed_engine_build_sha256", "f"*64)), 3), S.CONFLICT)

    def test_32_missing_dependency_lock_blocks(self):
        self.assertEqual(check(run(iso_refs=altered("claimed_dependency_lock_sha256", None)), 4), S.MISSING)

    def test_33_wrong_dependency_lock_conflicts(self):
        self.assertEqual(check(run(iso_refs=altered("claimed_dependency_lock_sha256", "f"*64)), 4), S.CONFLICT)

    def test_34_missing_trust_store_blocks(self):
        self.assertEqual(check(run(iso_refs=altered("claimed_trust_store_sha256", None)), 5), S.MISSING)

    def test_35_wrong_trust_store_conflicts(self):
        self.assertEqual(check(run(iso_refs=altered("claimed_trust_store_sha256", "a"*64)), 5), S.CONFLICT)

    def test_36_missing_tsa_anchor_blocks(self):
        self.assertEqual(check(run(iso_refs=altered("claimed_tsa_anchor_sha256", None)), 5), S.MISSING)

    def test_37_wrong_tsa_anchor_conflicts(self):
        self.assertEqual(check(run(iso_refs=altered("claimed_tsa_anchor_sha256", "a"*64)), 5), S.CONFLICT)

    def test_38_missing_isolation_runtime_reference_blocks(self):
        self.assertEqual(check(run(iso_refs=altered("claimed_isolation_runtime_sha256", None)), 6), S.MISSING)

    def test_39_missing_isolation_policy_reference_blocks(self):
        self.assertEqual(check(run(iso_refs=altered("claimed_isolation_policy_sha256", None)), 6), S.MISSING)

    def test_40_missing_verifier_output_schema_reference_blocks(self):
        self.assertEqual(check(run(iso_refs=altered("claimed_result_schema_sha256", None)), 7), S.MISSING)

    def test_41_missing_one_mandatory_denial_blocks(self):
        control_subset = REQUIRED_ISOLATION_CONTROLS[:-1]
        self.assertEqual(check(run(iso_refs=altered("claimed_controls", control_subset)), 9), S.MISSING)

    def test_42_duplicate_isolation_controls_rejected(self):
        with self.assertRaises(FedRfc3161IsolationError):
            isolation(claimed_controls=REQUIRED_ISOLATION_CONTROLS+REQUIRED_ISOLATION_CONTROLS[-1:])

    def test_43_unsorted_isolation_controls_rejected(self):
        with self.assertRaises(FedRfc3161IsolationError):
            isolation(claimed_controls=tuple(reversed(REQUIRED_ISOLATION_CONTROLS)))

    def test_44_unknown_privilege_grant_control_rejected(self):
        with self.assertRaises(FedRfc3161IsolationError):
            isolation(claimed_controls=("ALLOW_PROCESS_EXECUTION",))

    def test_45_weakened_nonexecuting_profile_rejected(self):
        with self.assertRaises(FedRfc3161IsolationError):
            isolation(claimed_profile="RFC3161_EXECUTE_ANYTHING_V1")

    def test_46_future_claimed_observation_is_conflict(self):
        r = run(iso_refs=altered("claimed_material_observed_at", AS_OF+timedelta(seconds=1)))
        self.assertEqual(check(r, 11), S.CONFLICT)
        self.assertEqual(r.targets[0].state, W.BLOCKED_UPSTREAM_OR_LOCAL_CONFLICT)

    def test_47_missing_observation_is_incomplete(self):
        self.assertEqual(check(run(iso_refs=altered("claimed_material_observed_at", None)), 11), S.MISSING)

    def test_48_equal_asof_claimed_observation_still_untrusted(self):
        self.assertEqual(check(run(iso_refs=altered("claimed_material_observed_at", AS_OF)), 11),
                         S.UNTRUSTED_CLAIM_MATCH_ONLY)

    def test_49_timezone_equivalence_is_deterministic(self):
        other = timezone(timedelta(hours=3))
        equivalent = AS_OF.astimezone(other)-timedelta(minutes=10)
        r = run(iso_refs=altered("claimed_material_observed_at", equivalent))
        self.assertEqual(r.diagnostic_manifest_sha256, run().diagnostic_manifest_sha256)

    def test_50_wrong_asof_for_same_previous_report_rejected(self):
        args, prev, refs = assemble()
        with patch.object(impl, "assess_fed_calendar_rfc3161_engine_prerequisites",
                          return_value=prev):
            with self.assertRaises(FedRfc3161IsolationError):
                assess_fed_calendar_rfc3161_execution_isolation(
                    *args, prev, refs, as_of=AS_OF+timedelta(seconds=1),
                )

    def test_51_stale_or_forged_upstream_digest_rejected(self):
        args, prev, refs = assemble()
        fake = replace(prev, diagnostic_manifest_sha256="f"*64)
        with patch.object(impl, "assess_fed_calendar_rfc3161_engine_prerequisites",
                          return_value=prev):
            with self.assertRaises(FedRfc3161IsolationError):
                assess_fed_calendar_rfc3161_execution_isolation(*args, fake, refs, as_of=AS_OF)

    def test_52_upstream_empty_blockers_forgery_rejected(self):
        args, prev, refs = assemble()
        fake = replace(prev, blockers=())
        with patch.object(impl, "assess_fed_calendar_rfc3161_engine_prerequisites",
                          return_value=prev):
            with self.assertRaises(FedRfc3161IsolationError):
                assess_fed_calendar_rfc3161_execution_isolation(*args, fake, refs, as_of=AS_OF)

    def test_53_tampered_upstream_plan_candidate_count_rejected(self):
        args, prev, refs = assemble()
        fake = replace(prev, review_package_candidate_count=100)
        with patch.object(impl, "assess_fed_calendar_rfc3161_engine_prerequisites",
                          return_value=prev):
            with self.assertRaises(FedRfc3161IsolationError):
                assess_fed_calendar_rfc3161_execution_isolation(*args, fake, refs, as_of=AS_OF)

    def test_54_upstream_chain_verification_failure_is_wrapped(self):
        with self.assertRaises(FedRfc3161IsolationError):
            run(upstream_error=FedRfc3161EnginePrerequisiteError("bad upstream"))

    def test_55_no_engine_material_is_upstream_incomplete(self):
        r = run(materials=())
        self.assertEqual(check(r, 0), S.MISSING)
        self.assertEqual(r.targets[0].state, W.BLOCKED_INCOMPLETE_ISOLATION_PACKAGE)

    def test_56_upstream_material_mismatch_is_conflict(self):
        r = run(materials=(material(claimed_token_sha256="0"*64),))
        self.assertEqual(check(r, 0), S.CONFLICT)
        self.assertEqual(r.targets[0].state, W.BLOCKED_UPSTREAM_OR_LOCAL_CONFLICT)

    def test_57_upstream_missing_one_critical_engine_capability(self):
        m = material(claimed_capabilities=())
        r = run(materials=(m,))
        self.assertEqual(check(r, 0), S.MISSING)

    def test_58_invalid_reference_identity_rejected(self):
        with self.assertRaises(FedRfc3161IsolationError):
            isolation(provider_id="bad/url")

    def test_59_invalid_sha256_rejected(self):
        for value in ("X"*64, "a"*63, "a"*65, b"a"*64, 0, False):
            with self.subTest(value=value):
                with self.assertRaises(FedRfc3161IsolationError):
                    isolation(claimed_engine_build_sha256=value)

    def test_60_naive_and_wrong_type_observation_rejected(self):
        for value in (datetime(2026, 10, 14), "2026-10-14", 123, False):
            with self.subTest(value=value):
                with self.assertRaises(FedRfc3161IsolationError):
                    isolation(claimed_material_observed_at=value)

    def test_61_bool_and_zero_cpu_budgets_rejected(self):
        for val in (0, -1, True, False, 1.0, MAX_CPU_BUDGET_MS+1):
            with self.subTest(val=val):
                with self.assertRaises(FedRfc3161IsolationError):
                    isolation(claimed_cpu_budget_ms=val)

    def test_62_token_budget_cannot_exceed_cap(self):
        with self.assertRaises(FedRfc3161IsolationError):
            isolation(claimed_max_token_bytes=MAX_TOKEN_INPUT_BYTES+1)

    def test_63_document_budget_cannot_exceed_cap(self):
        with self.assertRaises(FedRfc3161IsolationError):
            isolation(claimed_max_document_bytes=MAX_DOCUMENT_INPUT_BYTES+1)

    def test_64_memory_budget_cannot_exceed_cap(self):
        with self.assertRaises(FedRfc3161IsolationError):
            isolation(claimed_memory_budget_bytes=MAX_MEMORY_BUDGET_BYTES+1)

    def test_65_bounded_smaller_budgets_are_still_only_claims(self):
        r = run(iso_refs=lambda p, ms: (isolation(
            p, material_ref=ms[0], claimed_max_token_bytes=1024,
            claimed_max_document_bytes=1024, claimed_cpu_budget_ms=100,
            claimed_memory_budget_bytes=1_048_576,
        ),))
        self.assertEqual(check(r, 10), S.UNTRUSTED_CLAIM_MATCH_ONLY)
        self.assertFalse(r.operating_system_sandbox_enforced)

    def test_66_unknown_token_claim_rejected(self):
        args, prev, refs = assemble()
        bad = isolation(prev, material_ref=args[-1][0], claim_id="UNKNOWN")
        with patch.object(impl, "assess_fed_calendar_rfc3161_engine_prerequisites",
                          return_value=prev):
            with self.assertRaises(FedRfc3161IsolationError):
                assess_fed_calendar_rfc3161_execution_isolation(*args, prev, (bad,), as_of=AS_OF)

    def test_67_duplicate_isolation_claim_identity_rejected(self):
        args, prev, refs = assemble()
        with patch.object(impl, "assess_fed_calendar_rfc3161_engine_prerequisites",
                          return_value=prev):
            with self.assertRaises(FedRfc3161IsolationError):
                assess_fed_calendar_rfc3161_execution_isolation(*args, prev, refs*2, as_of=AS_OF)

    def test_68_nonmodel_isolation_reference_rejected(self):
        args, prev, refs = assemble()
        with patch.object(impl, "assess_fed_calendar_rfc3161_engine_prerequisites",
                          return_value=prev):
            with self.assertRaises(FedRfc3161IsolationError):
                assess_fed_calendar_rfc3161_execution_isolation(*args, prev, (object(),), as_of=AS_OF)

    def test_69_null_isolation_claims_rejected(self):
        args, prev, _ = assemble()
        with self.assertRaises(TypeError):
            assess_fed_calendar_rfc3161_execution_isolation(*args, prev, None, as_of=AS_OF)

    def test_70_bounded_isolation_claims_cap(self):
        args, prev, refs = assemble()
        with self.assertRaises(FedRfc3161IsolationError):
            assess_fed_calendar_rfc3161_execution_isolation(
                *args, prev, repeat(refs[0], MAX_ISOLATION_CLAIMS + 1), as_of=AS_OF,
            )

    def test_71_generator_materialized_once(self):
        args, prev, refs = assemble()
        with patch.object(impl, "assess_fed_calendar_rfc3161_engine_prerequisites",
                          return_value=prev):
            r = assess_fed_calendar_rfc3161_execution_isolation(
                *args, prev, (x for x in refs), as_of=AS_OF,
            )
        self.assertEqual(r.reference_count, 1)

    def test_72_mutated_frozen_control_reference_revalidated(self):
        args, prev, refs = assemble()
        object.__setattr__(refs[0], "claimed_controls", ("ALLOW_NETWORK",))
        with patch.object(impl, "assess_fed_calendar_rfc3161_engine_prerequisites",
                          return_value=prev):
            with self.assertRaises(FedRfc3161IsolationError):
                assess_fed_calendar_rfc3161_execution_isolation(*args, prev, refs, as_of=AS_OF)

    def test_73_mutated_frozen_resource_reference_revalidated(self):
        args, prev, refs = assemble()
        object.__setattr__(refs[0], "claimed_cpu_budget_ms", -1)
        with patch.object(impl, "assess_fed_calendar_rfc3161_engine_prerequisites",
                          return_value=prev):
            with self.assertRaises(FedRfc3161IsolationError):
                assess_fed_calendar_rfc3161_execution_isolation(*args, prev, refs, as_of=AS_OF)

    def test_74_isolation_reference_order_is_canonical(self):
        base = f4514_adapter(targets=(target(), target(claim_id="TOKEN_002")))
        mats = (material(), material(claim_id="TOKEN_002"))
        values = earlier_inputs(
            base=base, mats=mats,
            verify_inputs=(_verifier_input(), _verifier_input(claim_id="TOKEN_002")),
            policies=(_policy(), _policy(claim_id="TOKEN_002")),
        )
        upstream = f4515_run(arguments=values)
        first = isolation(upstream, material_ref=mats[0])
        second = isolation(upstream, material_ref=mats[1], claim_id="TOKEN_002")
        with patch.object(impl, "assess_fed_calendar_rfc3161_engine_prerequisites",
                          return_value=upstream):
            a = assess_fed_calendar_rfc3161_execution_isolation(
                *values, upstream, (first, second), as_of=AS_OF,
            )
            b = assess_fed_calendar_rfc3161_execution_isolation(
                *values, upstream, (second, first), as_of=AS_OF,
            )
        self.assertEqual(a.diagnostic_manifest_sha256, b.diagnostic_manifest_sha256)
        self.assertEqual(a.plan_candidate_count, 2)

    def test_75_second_missing_claim_cannot_borrow_first(self):
        base = f4514_adapter(targets=(target(), target(claim_id="TOKEN_002")))
        mats = (material(), material(claim_id="TOKEN_002"))
        values = earlier_inputs(
            base=base, mats=mats,
            verify_inputs=(_verifier_input(), _verifier_input(claim_id="TOKEN_002")),
            policies=(_policy(), _policy(claim_id="TOKEN_002")),
        )
        upstream = f4515_run(arguments=values)
        first = isolation(upstream, material_ref=mats[0])
        with patch.object(impl, "assess_fed_calendar_rfc3161_engine_prerequisites",
                          return_value=upstream):
            r = assess_fed_calendar_rfc3161_execution_isolation(
                *values, upstream, (first,), as_of=AS_OF,
            )
        self.assertEqual(r.targets[1].state, W.BLOCKED_INCOMPLETE_ISOLATION_PACKAGE)

    def test_76_unchanged_inputs_reproduce_receipt(self):
        self.assertEqual(run().diagnostic_manifest_sha256, run().diagnostic_manifest_sha256)

    def test_77_changed_isolation_policy_changes_receipt_not_authority(self):
        r = run(iso_refs=altered("claimed_isolation_policy_sha256", "9"*64))
        self.assertNotEqual(r.diagnostic_manifest_sha256, run().diagnostic_manifest_sha256)
        self.assertFalse(r.verifier_executed)

    def test_78_changed_resource_budget_changes_receipt_not_authority(self):
        r = run(iso_refs=altered("claimed_memory_budget_bytes", 32_000_000))
        self.assertNotEqual(r.isolation_references_sha256, run().isolation_references_sha256)
        self.assertFalse(r.verified_output_admitted)

    def test_79_output_and_nested_checks_are_immutable(self):
        r = run()
        with self.assertRaises(FrozenInstanceError):
            r.blockers = ()
        with self.assertRaises(FrozenInstanceError):
            r.targets[0].state = W.NONEXECUTING_ISOLATION_PLAN_CANDIDATE_ONLY
        with self.assertRaises(FrozenInstanceError):
            r.targets[0].checks[0].state = S.CONFLICT

    def test_80_forged_empty_blockers_never_grant_authority(self):
        forgery = replace(run(), blockers=(), targets=(), plan_candidate_count=100)
        self.assertFalse(forgery.verifier_executed)
        self.assertFalse(forgery.council_admission_authorized)
        self.assertEqual(forgery.directional_trading_score, 0)
        with self.assertRaises(FedRfc3161IsolationUnavailable):
            forgery.require_trusted_verifier_execution()

    def test_81_no_caller_supplied_execution_or_result_keywords(self):
        for kwarg in ("execute", "subprocess", "trust_anchor_verified",
                      "verified", "signature_valid", "use_network", "command"):
            with self.subTest(kwarg=kwarg):
                with self.assertRaises(TypeError):
                    isolation(**{kwarg: True})

    def test_82_report_contains_no_private_raw_data(self):
        r = run()
        rendered = repr(r)
        for disallowed in ("BEGIN CERTIFICATE", "TSA PRIVATE KEY", "<html>",
                           "subprocess.Popen", "https://untrusted.example/"):
            self.assertNotIn(disallowed, rendered)

    def test_83_module_never_imports_io_network_or_dynamic_code(self):
        tree = ast.parse(inspect.getsource(impl))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(n.name.partition(".")[0] for n in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module.partition(".")[0])
        self.assertFalse(set(imports) & {
            "os", "socket", "subprocess", "requests", "urllib", "ctypes",
            "importlib", "pathlib", "shutil", "psycopg2", "sqlite3", "ssl",
            "cryptography", "tempfile", "threading", "multiprocessing",
        })

    def test_84_invalid_asof_rejected(self):
        args, prev, refs = assemble()
        for when in (None, datetime(2026, 10, 14), "2026-10-14", 123):
            with self.subTest(when=when):
                with self.assertRaises(FedRfc3161IsolationError):
                    assess_fed_calendar_rfc3161_execution_isolation(
                        *args, prev, refs, as_of=when,
                    )

    def test_85_no_timestamp_targets_still_refuses_all_access(self):
        base = f4514_adapter(targets=())
        r = run(base=base, materials=())
        self.assertEqual(r.target_count, 0)
        self.assertEqual(r.plan_candidate_count, 0)
        self.assertFalse(r.council_admission_authorized)

    def test_86_bad_upstream_engine_report_type_refused(self):
        args, prev, refs = assemble()
        with self.assertRaises(TypeError):
            assess_fed_calendar_rfc3161_execution_isolation(*args, object(), refs, as_of=AS_OF)

    def test_87_real_f4515_fixture_round_trip_nonexecuting(self):
        from backend.macro.test_fed_calendar_crypto_adapter import package, AS_OF as FIXTURE_AS_OF
        from backend.macro.fed_calendar_crypto_adapter import prepare_fed_calendar_crypto_adapter
        packed = package(as_of=FIXTURE_AS_OF)
        handoff = prepare_fed_calendar_crypto_adapter(*packed, as_of=FIXTURE_AS_OF)
        original = handoff.targets[0]
        m = material(
            claimed_token_sha256=original.token_body_sha256,
            claimed_document_sha256=original.document_body_sha256,
            claimed_policy_revision_sha256=original.policy_revision_reference_sha256,
            claimed_tsa_trust_anchor_sha256=packed[11][0].pinned_tsa_anchor_certificate_sha256,
            claimed_revocation_evidence_sha256=packed[9][0].claimed_revocation_evidence_sha256,
            claimed_request_der_sha256=packed[9][0].claimed_request_der_sha256,
            claimed_material_observed_at=FIXTURE_AS_OF-timedelta(minutes=1),
        )
        eng = assess_fed_calendar_rfc3161_engine_prerequisites(
            *packed, handoff, (m,), as_of=FIXTURE_AS_OF,
        )
        ref = isolation(eng, material_ref=m)
        report = assess_fed_calendar_rfc3161_execution_isolation(
            *packed, handoff, (m,), eng, (ref,), as_of=FIXTURE_AS_OF,
        )
        self.assertEqual(report.canonical_engine_prerequisites_sha256, eng.diagnostic_manifest_sha256)
        self.assertEqual(report.plan_candidate_count, 1)
        self.assertFalse(report.operating_system_sandbox_enforced)
        self.assertFalse(report.rfc3161_signature_verified)

    def test_88_real_f4515_fixture_rejects_forged_receipt(self):
        from backend.macro.test_fed_calendar_crypto_adapter import package, AS_OF as FIXTURE_AS_OF
        from backend.macro.fed_calendar_crypto_adapter import prepare_fed_calendar_crypto_adapter
        packed = package(as_of=FIXTURE_AS_OF)
        handoff = prepare_fed_calendar_crypto_adapter(*packed, as_of=FIXTURE_AS_OF)
        m = material(
            claimed_token_sha256=handoff.targets[0].token_body_sha256,
            claimed_document_sha256=handoff.targets[0].document_body_sha256,
            claimed_policy_revision_sha256=handoff.targets[0].policy_revision_reference_sha256,
            claimed_tsa_trust_anchor_sha256=packed[11][0].pinned_tsa_anchor_certificate_sha256,
            claimed_revocation_evidence_sha256=packed[9][0].claimed_revocation_evidence_sha256,
            claimed_request_der_sha256=packed[9][0].claimed_request_der_sha256,
            claimed_material_observed_at=FIXTURE_AS_OF-timedelta(minutes=1),
        )
        eng = assess_fed_calendar_rfc3161_engine_prerequisites(
            *packed, handoff, (m,), as_of=FIXTURE_AS_OF,
        )
        fake = replace(eng, blockers=())
        ref = isolation(eng, material_ref=m)
        with self.assertRaises(FedRfc3161IsolationError):
            assess_fed_calendar_rfc3161_execution_isolation(
                *packed, handoff, (m,), fake, (ref,), as_of=FIXTURE_AS_OF,
            )

    def test_89_real_f4515_fixture_rejects_stale_asof(self):
        from backend.macro.test_fed_calendar_crypto_adapter import package, AS_OF as FIXTURE_AS_OF
        from backend.macro.fed_calendar_crypto_adapter import prepare_fed_calendar_crypto_adapter
        packed = package(as_of=FIXTURE_AS_OF)
        handoff = prepare_fed_calendar_crypto_adapter(*packed, as_of=FIXTURE_AS_OF)
        m = material(
            claimed_token_sha256=handoff.targets[0].token_body_sha256,
            claimed_document_sha256=handoff.targets[0].document_body_sha256,
            claimed_policy_revision_sha256=handoff.targets[0].policy_revision_reference_sha256,
            claimed_tsa_trust_anchor_sha256=packed[11][0].pinned_tsa_anchor_certificate_sha256,
            claimed_revocation_evidence_sha256=packed[9][0].claimed_revocation_evidence_sha256,
            claimed_request_der_sha256=packed[9][0].claimed_request_der_sha256,
            claimed_material_observed_at=FIXTURE_AS_OF-timedelta(minutes=1),
        )
        eng = assess_fed_calendar_rfc3161_engine_prerequisites(
            *packed, handoff, (m,), as_of=FIXTURE_AS_OF,
        )
        ref = isolation(eng, material_ref=m)
        with self.assertRaises(FedRfc3161IsolationError):
            assess_fed_calendar_rfc3161_execution_isolation(
                *packed, handoff, (m,), eng, (ref,), as_of=FIXTURE_AS_OF+timedelta(seconds=1),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""F4.5.17: fail-closed, deterministic offline runtime-attestation inventory tests.

Run: python -m backend.macro.test_fed_calendar_rfc3161_runtime_attestation
The majority of tests isolate the F4.5.16 revalidator at the exact handoff;
three final integration checks run the real previous milestone chain when
available. No sandbox, attestor, verifier, database, broker, or network.
"""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from itertools import repeat
from unittest.mock import patch
import ast
import inspect
import unittest

from backend.macro import fed_calendar_rfc3161_runtime_attestation as impl
from backend.macro.fed_calendar_rfc3161_runtime_attestation import (
    MAX_RUNTIME_ATTESTATION_REFERENCES,
    REQUIRED_RUNTIME_ATTESTATION_CONTROLS, RUNTIME_ATTESTATION_PROFILE,
    RUNTIME_ATTESTATION_CHECK_CODES,
    FedRfc3161RuntimeAttestationReference, FedRfc3161RuntimeAttestationReport,
    FedRfc3161RuntimeAttestationError, FedRfc3161RuntimeAttestationUnavailable,
    RuntimeAttestationCheckState as S, RuntimeAttestationPlanState as W,
    assess_fed_calendar_rfc3161_runtime_attestation,
)
from backend.macro import fed_calendar_rfc3161_execution_isolation as f4516_impl
from backend.macro.fed_calendar_rfc3161_execution_isolation import (
    FedRfc3161IsolationError,
    IsolationPlanState,
    assess_fed_calendar_rfc3161_execution_isolation,
)
from backend.macro.test_fed_calendar_rfc3161_execution_isolation import (
    AS_OF, RUNTIME, ISOLATION_POLICY, SCHEMA,
    assemble as f4516_assemble,
    isolation as f4516_reference,
)
from backend.macro.test_fed_calendar_rfc3161_engine_prerequisites import (
    BUILD, LOCK, TRUST_STORE, ANCHOR, material, target, _verifier_input, _policy,
    adapter as f4514_adapter, inputs as f4515_inputs,
)
# Full-chain integration captures occur after the synthetic F4.5.15 AS_OF.
# Use the F4.5.14 fixture clock so the 12:40 UTC capture is causally visible.
from backend.macro.test_fed_calendar_crypto_adapter import AS_OF as REAL_CHAIN_AS_OF
from backend.macro.fed_calendar_rfc3161_engine_prerequisites import (
    assess_fed_calendar_rfc3161_engine_prerequisites,
)

MEASURED_PROCESS = "1" * 64
MEASURED_LIBRARIES = "2" * 64
OPERATOR_POLICY = "3" * 64
OS_MEASUREMENT_POLICY = "4" * 64
SANDBOX_EVIDENCE = "5" * 64
ATTESTOR_IDENTITY = "6" * 64
SIGNED_ENVELOPE = "7" * 64
CHALLENGE = "8" * 64


def attestation(upstream=None, *, isolation_ref=None, material_ref=None,
                claim_id="TOKEN_001", **overrides):
    data = dict(
        provider_id="TSA_A", claim_id=claim_id,
        claimed_isolation_receipt_sha256=(upstream.diagnostic_manifest_sha256 if upstream else "a"*64),
        claimed_adapter_receipt_sha256=(upstream.canonical_adapter_sha256 if upstream else "b"*64),
        claimed_engine_prerequisites_sha256=(
            upstream.canonical_engine_prerequisites_sha256 if upstream else "c"*64
        ),
        claimed_engine_build_sha256=(material_ref.claimed_engine_build_sha256 if material_ref else BUILD),
        claimed_dependency_lock_sha256=(material_ref.claimed_dependency_lock_sha256 if material_ref else LOCK),
        claimed_trust_store_sha256=(material_ref.claimed_trust_store_sha256 if material_ref else TRUST_STORE),
        claimed_tsa_anchor_sha256=(material_ref.claimed_tsa_trust_anchor_sha256 if material_ref else ANCHOR),
        claimed_isolation_runtime_sha256=(isolation_ref.claimed_isolation_runtime_sha256 if isolation_ref else RUNTIME),
        claimed_isolation_policy_sha256=(isolation_ref.claimed_isolation_policy_sha256 if isolation_ref else ISOLATION_POLICY),
        claimed_result_schema_sha256=(isolation_ref.claimed_result_schema_sha256 if isolation_ref else SCHEMA),
        claimed_process_image_measurement_sha256=MEASURED_PROCESS,
        claimed_loaded_dependencies_measurement_sha256=MEASURED_LIBRARIES,
        claimed_operator_approval_policy_sha256=OPERATOR_POLICY,
        claimed_os_measurement_policy_sha256=OS_MEASUREMENT_POLICY,
        claimed_sandbox_enforcement_evidence_sha256=SANDBOX_EVIDENCE,
        claimed_attestor_identity_sha256=ATTESTOR_IDENTITY,
        claimed_attestation_signature_envelope_sha256=SIGNED_ENVELOPE,
        claimed_operator_challenge_sha256=CHALLENGE,
        claimed_attested_challenge_sha256=CHALLENGE,
        claimed_runtime_started_at=AS_OF - timedelta(minutes=5),
        claimed_attestation_observed_at=AS_OF - timedelta(minutes=1),
        claimed_controls=REQUIRED_RUNTIME_ATTESTATION_CONTROLS,
    )
    data.update(overrides)
    return FedRfc3161RuntimeAttestationReference(**data)


def assembled(*, as_of=AS_OF, old_args=None, engine=None, iso_refs=None, material_refs=None,
              base=None, verifier_inputs=None, policies=None):
    if old_args is None:
        old_args, previous_engine, previous_isolation = f4516_assemble(
            as_of=as_of, materials=material_refs, base=base,
            verify_inputs=verifier_inputs, policies=policies,
        )
    else:
        previous_engine = engine
        previous_isolation = iso_refs
    if engine is None:
        engine = previous_engine
    if iso_refs is None:
        iso_refs = previous_isolation
    with patch.object(f4516_impl, "assess_fed_calendar_rfc3161_engine_prerequisites",
                      return_value=engine):
        isolation_report = assess_fed_calendar_rfc3161_execution_isolation(
            *old_args, engine, iso_refs, as_of=as_of,
        )
    attestation_refs = tuple(attestation(
        isolation_report, isolation_ref=i,
        material_ref=next((m for m in old_args[-1] if
                           (m.provider_id, m.claim_id) == (i.provider_id, i.claim_id)), None),
        claim_id=i.claim_id,
    ) for i in iso_refs)
    return old_args, engine, iso_refs, isolation_report, attestation_refs


def run(*, as_of=AS_OF, att_refs=None, canonical=None, supplied=None,
        previous_error=None, **kwargs):
    args, engine, iso_refs, actual, refs = assembled(as_of=as_of, **kwargs)
    if callable(att_refs):
        refs = att_refs(actual, iso_refs, args[-1])
    elif att_refs is not None:
        refs = att_refs
    supplied = actual if supplied is None else supplied
    with patch.object(impl, "assess_fed_calendar_rfc3161_execution_isolation",
                      side_effect=previous_error if previous_error is not None else None,
                      return_value=actual if canonical is None else canonical) as validated:
        result = assess_fed_calendar_rfc3161_runtime_attestation(
            *args, engine, iso_refs, supplied, refs, as_of=as_of,
        )
        validated.assert_called_once()
    return result


def check(result, index, target=0):
    return result.targets[target].checks[index].state


def altered(field, value):
    return lambda prev, iso, mats: (attestation(
        prev, isolation_ref=iso[0], material_ref=mats[0], **{field: value}
    ),)


class FedCalendarRfc3161RuntimeAttestationTests(unittest.TestCase):
    def test_01_frozen_report_model(self):
        self.assertIsInstance(run(), FedRfc3161RuntimeAttestationReport)

    def test_02_isolation_receipt_bound(self):
        args, eng, iso, previous, refs = assembled()
        self.assertEqual(run().canonical_isolation_sha256, previous.diagnostic_manifest_sha256)

    def test_03_adapter_receipt_bound(self):
        self.assertEqual(run().canonical_adapter_sha256, f4514_adapter().diagnostic_manifest_sha256)

    def test_04_engine_receipt_bound(self):
        args, eng, iso, previous, refs = assembled()
        self.assertEqual(run().canonical_engine_prerequisites_sha256, eng.diagnostic_manifest_sha256)

    def test_05_one_target_one_reference(self):
        r = run()
        self.assertEqual((r.target_count, r.reference_count, r.reference_candidate_count), (1, 1, 1))

    def test_06_stable_unique_check_codes(self):
        self.assertEqual(tuple(c.code for c in run().targets[0].checks), RUNTIME_ATTESTATION_CHECK_CODES)
        self.assertEqual(len(set(RUNTIME_ATTESTATION_CHECK_CODES)), len(RUNTIME_ATTESTATION_CHECK_CODES))

    def test_07_nonexecuting_profile(self):
        self.assertIn("NOEXEC", RUNTIME_ATTESTATION_PROFILE)
        self.assertEqual(attestation().claimed_profile, RUNTIME_ATTESTATION_PROFILE)

    def test_08_required_controls_sorted_unique(self):
        self.assertEqual(REQUIRED_RUNTIME_ATTESTATION_CONTROLS,
                         tuple(sorted(set(REQUIRED_RUNTIME_ATTESTATION_CONTROLS))))
        self.assertGreaterEqual(len(REQUIRED_RUNTIME_ATTESTATION_CONTROLS), 12)

    def test_09_complete_claim_only_unverified_candidate(self):
        r = run()
        self.assertEqual(r.targets[0].state, W.UNVERIFIED_ATTESTATION_PACKAGE_CANDIDATE_ONLY)
        self.assertTrue(r.blockers)

    def test_10_every_authorization_flag_is_false(self):
        r = run()
        for field in (
            "verifier_runtime_measured", "attestation_signature_verified",
            "independently_provisioned_attestor_trusted", "trusted_operator_policy_authenticated",
            "runtime_challenge_freshness_proven", "os_sandbox_enforced",
            "independent_tsa_anchor_authenticated", "trusted_verifier_executed",
            "rfc3161_cryptographically_verified", "historical_source_version_attested",
            "council_admission_authorized", "historical_replay_authorized",
            "persistence_authorized",
        ):
            with self.subTest(field=field):
                self.assertIs(getattr(r, field), False)
        self.assertEqual(r.directional_trading_score, 0)

    def test_11_execution_and_delivery_refuse(self):
        r = run()
        for f in (r.require_trusted_runtime_attestation,
                  r.require_trusted_verifier_execution, r.require_council_calendar_delivery,
                  r.require_historical_replay, r.require_persistence):
            with self.subTest(method=f.__name__):
                with self.assertRaises(FedRfc3161RuntimeAttestationUnavailable):
                    f()

    def test_12_caller_controlled_verdict_never_trusted(self):
        for verdict in (True, False, None, {"signed": True}, object()):
            with self.assertRaises(FedRfc3161RuntimeAttestationUnavailable):
                run().accept_external_attestation_verdict(verdict)

    def test_13_upstream_plan_only(self):
        self.assertEqual(check(run(), 0), S.UPSTREAM_PLAN_ONLY)

    def test_14_matching_isolation_hash_only_claim(self):
        self.assertEqual(check(run(), 1), S.UNTRUSTED_CLAIM_MATCH_ONLY)

    def test_15_matching_adapter_hash_only_claim(self):
        self.assertEqual(check(run(), 2), S.UNTRUSTED_CLAIM_MATCH_ONLY)

    def test_16_matching_engine_hash_only_claim(self):
        self.assertEqual(check(run(), 3), S.UNTRUSTED_CLAIM_MATCH_ONLY)

    def test_17_matching_engine_and_lock_only_claim(self):
        self.assertEqual(check(run(), 4), S.UNTRUSTED_CLAIM_MATCH_ONLY)

    def test_18_matching_store_and_anchor_only_claim(self):
        self.assertEqual(check(run(), 5), S.UNTRUSTED_CLAIM_MATCH_ONLY)

    def test_19_matching_runtime_policy_schema_only_claim(self):
        self.assertEqual(check(run(), 6), S.UNTRUSTED_CLAIM_MATCH_ONLY)

    def test_20_measured_image_claim_only_presence(self):
        self.assertEqual(check(run(), 7), S.UNTRUSTED_PRESENCE_ONLY)

    def test_21_policy_references_only_presence(self):
        self.assertEqual(check(run(), 8), S.UNTRUSTED_PRESENCE_ONLY)

    def test_22_sandbox_claim_only_presence(self):
        self.assertEqual(check(run(), 9), S.UNTRUSTED_PRESENCE_ONLY)

    def test_23_attestor_identity_and_envelope_only_presence(self):
        self.assertEqual(check(run(), 10), S.UNTRUSTED_PRESENCE_ONLY)

    def test_24_equal_challenges_are_untrusted(self):
        self.assertEqual(check(run(), 11), S.UNTRUSTED_CLAIM_MATCH_ONLY)
        self.assertFalse(run().runtime_challenge_freshness_proven)

    def test_25_observation_clock_only_local_consistency(self):
        self.assertEqual(check(run(), 12), S.UNTRUSTED_CLAIM_MATCH_ONLY)

    def test_26_controls_are_declared_not_enforced(self):
        self.assertEqual(check(run(), 13), S.UNTRUSTED_PRESENCE_ONLY)
        self.assertFalse(run().os_sandbox_enforced)

    def test_27_real_attestation_always_required(self):
        r = run()
        for i in range(14, len(RUNTIME_ATTESTATION_CHECK_CODES)):
            self.assertIs(check(r, i), S.INDEPENDENT_ATTESTATION_REQUIRED)

    def test_28_no_references_never_admitted(self):
        r = run(att_refs=())
        self.assertEqual(r.reference_count, 0)
        self.assertEqual(r.reference_candidate_count, 0)
        self.assertEqual(r.targets[0].state, W.BLOCKED_INCOMPLETE_ATTESTATION_REFERENCES)
        self.assertFalse(r.trusted_verifier_executed)

    def test_29_missing_isolation_receipt(self):
        self.assertEqual(check(run(att_refs=altered("claimed_isolation_receipt_sha256", None)), 1), S.MISSING)

    def test_30_isolation_receipt_mismatch(self):
        self.assertEqual(check(run(att_refs=altered("claimed_isolation_receipt_sha256", "f"*64)), 1), S.CONFLICT)

    def test_31_missing_adapter_receipt(self):
        self.assertEqual(check(run(att_refs=altered("claimed_adapter_receipt_sha256", None)), 2), S.MISSING)

    def test_32_wrong_adapter_receipt(self):
        self.assertEqual(check(run(att_refs=altered("claimed_adapter_receipt_sha256", "f"*64)), 2), S.CONFLICT)

    def test_33_missing_engine_receipt(self):
        self.assertEqual(check(run(att_refs=altered("claimed_engine_prerequisites_sha256", None)), 3), S.MISSING)

    def test_34_wrong_engine_receipt(self):
        self.assertEqual(check(run(att_refs=altered("claimed_engine_prerequisites_sha256", "f"*64)), 3), S.CONFLICT)

    def test_35_missing_verifier_build(self):
        self.assertEqual(check(run(att_refs=altered("claimed_engine_build_sha256", None)), 4), S.MISSING)

    def test_36_mismatched_dependency_lock(self):
        self.assertEqual(check(run(att_refs=altered("claimed_dependency_lock_sha256", "f"*64)), 4), S.CONFLICT)

    def test_37_missing_trust_store(self):
        self.assertEqual(check(run(att_refs=altered("claimed_trust_store_sha256", None)), 5), S.MISSING)

    def test_38_mismatched_anchor(self):
        self.assertEqual(check(run(att_refs=altered("claimed_tsa_anchor_sha256", "f"*64)), 5), S.CONFLICT)

    def test_39_missing_runtime_identity(self):
        self.assertEqual(check(run(att_refs=altered("claimed_isolation_runtime_sha256", None)), 6), S.MISSING)

    def test_40_mismatched_isolation_policy(self):
        self.assertEqual(check(run(att_refs=altered("claimed_isolation_policy_sha256", "f"*64)), 6), S.CONFLICT)

    def test_41_missing_result_schema(self):
        self.assertEqual(check(run(att_refs=altered("claimed_result_schema_sha256", None)), 6), S.MISSING)

    def test_42_missing_process_measurement(self):
        self.assertEqual(check(run(att_refs=altered("claimed_process_image_measurement_sha256", None)), 7), S.MISSING)

    def test_43_missing_dependency_measurement(self):
        self.assertEqual(check(run(att_refs=altered("claimed_loaded_dependencies_measurement_sha256", None)), 7), S.MISSING)

    def test_44_missing_operator_policy(self):
        self.assertEqual(check(run(att_refs=altered("claimed_operator_approval_policy_sha256", None)), 8), S.MISSING)

    def test_45_missing_os_measurement_policy(self):
        self.assertEqual(check(run(att_refs=altered("claimed_os_measurement_policy_sha256", None)), 8), S.MISSING)

    def test_46_missing_sandbox_enforcement_claim(self):
        self.assertEqual(check(run(att_refs=altered("claimed_sandbox_enforcement_evidence_sha256", None)), 9), S.MISSING)

    def test_47_missing_attestor_identity(self):
        self.assertEqual(check(run(att_refs=altered("claimed_attestor_identity_sha256", None)), 10), S.MISSING)

    def test_48_missing_signature_envelope_claim(self):
        self.assertEqual(check(run(att_refs=altered("claimed_attestation_signature_envelope_sha256", None)), 10), S.MISSING)

    def test_49_missing_challenge(self):
        self.assertEqual(check(run(att_refs=altered("claimed_operator_challenge_sha256", None)), 11), S.MISSING)

    def test_50_inconsistent_challenge_is_conflict(self):
        self.assertEqual(check(run(att_refs=altered("claimed_attested_challenge_sha256", "f"*64)), 11), S.CONFLICT)

    def test_51_missing_launch_clock(self):
        self.assertEqual(check(run(att_refs=altered("claimed_runtime_started_at", None)), 12), S.MISSING)

    def test_52_missing_attestation_clock(self):
        self.assertEqual(check(run(att_refs=altered("claimed_attestation_observed_at", None)), 12), S.MISSING)

    def test_53_observation_in_future_is_conflict(self):
        self.assertEqual(check(run(att_refs=altered("claimed_attestation_observed_at", AS_OF+timedelta(seconds=1))), 12), S.CONFLICT)

    def test_54_runtime_started_after_attestation_conflicts(self):
        self.assertEqual(check(run(att_refs=altered("claimed_runtime_started_at", AS_OF)), 12), S.CONFLICT)

    def test_55_runtime_started_before_isolation_material_conflicts(self):
        self.assertEqual(check(run(att_refs=altered("claimed_runtime_started_at", AS_OF-timedelta(hours=1))), 12), S.CONFLICT)

    def test_56_same_asof_clock_still_untrusted(self):
        self.assertEqual(check(run(att_refs=altered("claimed_attestation_observed_at", AS_OF)), 12), S.UNTRUSTED_CLAIM_MATCH_ONLY)

    def test_57_timezone_normalized_attestation_receipt(self):
        tz = timezone(timedelta(hours=3))
        r = run(att_refs=altered("claimed_runtime_started_at", (AS_OF-timedelta(minutes=5)).astimezone(tz)))
        self.assertEqual(r.diagnostic_manifest_sha256, run().diagnostic_manifest_sha256)

    def test_58_at_least_one_missing_mandatory_control_blocks(self):
        r = run(att_refs=altered("claimed_controls", REQUIRED_RUNTIME_ATTESTATION_CONTROLS[:-1]))
        self.assertEqual(check(r, 13), S.MISSING)
        self.assertEqual(r.reference_candidate_count, 0)

    def test_59_unsorted_controls_rejected(self):
        with self.assertRaises(FedRfc3161RuntimeAttestationError):
            attestation(claimed_controls=tuple(reversed(REQUIRED_RUNTIME_ATTESTATION_CONTROLS)))

    def test_60_duplicate_controls_rejected(self):
        with self.assertRaises(FedRfc3161RuntimeAttestationError):
            attestation(claimed_controls=REQUIRED_RUNTIME_ATTESTATION_CONTROLS + REQUIRED_RUNTIME_ATTESTATION_CONTROLS[-1:])

    def test_61_unknown_privilege_escalation_control_rejected(self):
        with self.assertRaises(FedRfc3161RuntimeAttestationError):
            attestation(claimed_controls=("ALLOW_UNRESTRICTED_PROCESS_EXECUTION",))

    def test_62_weakened_profile_rejected(self):
        with self.assertRaises(FedRfc3161RuntimeAttestationError):
            attestation(claimed_profile="RFC3161_UNRESTRICTED_V1")

    def test_63_bad_identity_rejected(self):
        for identity in ("bad/url", "x"*65, "", b"TSA_A", 77):
            with self.subTest(identity=identity):
                with self.assertRaises(FedRfc3161RuntimeAttestationError):
                    attestation(provider_id=identity)

    def test_64_invalid_sha256_rejected(self):
        for value in ("G"*64, "a"*63, "a"*65, b"a"*64, False, 17):
            with self.subTest(value=value):
                with self.assertRaises(FedRfc3161RuntimeAttestationError):
                    attestation(claimed_attestor_identity_sha256=value)

    def test_65_naive_clock_rejected(self):
        for when in (datetime(2026, 10, 14), 123, None, "2026-10-14"):
            if when is None:
                continue  # absence is a local blocker, not malformed schema
            with self.subTest(when=when):
                with self.assertRaises(FedRfc3161RuntimeAttestationError):
                    attestation(claimed_runtime_started_at=when)

    def test_66_unknown_caller_execution_success_keywords(self):
        for keyword in ("verified", "signature_valid", "trusted_attestor", "execute",
                        "allow_council", "command", "run_subprocess", "use_network"):
            with self.subTest(keyword=keyword):
                with self.assertRaises(TypeError):
                    attestation(**{keyword: True})

    def test_67_deterministic_receipt(self):
        self.assertEqual(run().diagnostic_manifest_sha256, run().diagnostic_manifest_sha256)

    def test_68_changed_measurement_changes_receipt_not_trust(self):
        r = run(att_refs=altered("claimed_process_image_measurement_sha256", "9"*64))
        self.assertNotEqual(r.reference_manifest_sha256, run().reference_manifest_sha256)
        self.assertFalse(r.verifier_runtime_measured)

    def test_69_changed_attestor_identity_changes_receipt_not_trust(self):
        r = run(att_refs=altered("claimed_attestor_identity_sha256", "9"*64))
        self.assertNotEqual(r.diagnostic_manifest_sha256, run().diagnostic_manifest_sha256)
        self.assertFalse(r.independently_provisioned_attestor_trusted)

    def test_70_changed_claimed_os_measurement_policy_changes_receipt(self):
        r = run(att_refs=altered("claimed_os_measurement_policy_sha256", "9"*64))
        self.assertNotEqual(r.diagnostic_manifest_sha256, run().diagnostic_manifest_sha256)

    def test_71_changed_challenge_changes_receipt_not_freshness(self):
        r = run(att_refs=lambda prev, iso, mats: (attestation(
            prev, isolation_ref=iso[0], material_ref=mats[0],
            claimed_operator_challenge_sha256="9"*64,
            claimed_attested_challenge_sha256="9"*64,
        ),))
        self.assertNotEqual(r.reference_manifest_sha256, run().reference_manifest_sha256)
        self.assertFalse(r.runtime_challenge_freshness_proven)

    def test_72_equivalent_asof_normalized(self):
        tz = timezone(timedelta(hours=2))
        self.assertEqual(run(as_of=AS_OF.astimezone(tz)).diagnostic_manifest_sha256,
                         run().diagnostic_manifest_sha256)

    def test_73_unknown_token_identity_rejected(self):
        args, engine, iso, previous, refs = assembled()
        forged = attestation(previous, isolation_ref=iso[0], material_ref=args[-1][0], claim_id="TOKEN_MISSING")
        with patch.object(impl, "assess_fed_calendar_rfc3161_execution_isolation", return_value=previous):
            with self.assertRaises(FedRfc3161RuntimeAttestationError):
                assess_fed_calendar_rfc3161_runtime_attestation(
                    *args, engine, iso, previous, (forged,), as_of=AS_OF,
                )

    def test_74_duplicate_attestation_id_rejected(self):
        args, engine, iso, previous, refs = assembled()
        with patch.object(impl, "assess_fed_calendar_rfc3161_execution_isolation", return_value=previous):
            with self.assertRaises(FedRfc3161RuntimeAttestationError):
                assess_fed_calendar_rfc3161_runtime_attestation(
                    *args, engine, iso, previous, refs * 2, as_of=AS_OF,
                )

    def test_75_non_model_reference_rejected(self):
        args, engine, iso, previous, refs = assembled()
        with patch.object(impl, "assess_fed_calendar_rfc3161_execution_isolation", return_value=previous):
            with self.assertRaises(FedRfc3161RuntimeAttestationError):
                assess_fed_calendar_rfc3161_runtime_attestation(
                    *args, engine, iso, previous, (object(),), as_of=AS_OF,
                )

    def test_76_none_reference_collection_rejected(self):
        args, engine, iso, previous, refs = assembled()
        with self.assertRaises(TypeError):
            assess_fed_calendar_rfc3161_runtime_attestation(
                *args, engine, iso, previous, None, as_of=AS_OF,
            )

    def test_77_bounded_reference_limit(self):
        args, engine, iso, previous, refs = assembled()
        with self.assertRaises(FedRfc3161RuntimeAttestationError):
            assess_fed_calendar_rfc3161_runtime_attestation(
                *args, engine, iso, previous,
                repeat(refs[0], MAX_RUNTIME_ATTESTATION_REFERENCES+1), as_of=AS_OF,
            )

    def test_78_reference_generator_materialized_once(self):
        args, engine, iso, previous, refs = assembled()
        with patch.object(impl, "assess_fed_calendar_rfc3161_execution_isolation", return_value=previous):
            r = assess_fed_calendar_rfc3161_runtime_attestation(
                *args, engine, iso, previous, (x for x in refs), as_of=AS_OF,
            )
        self.assertEqual(r.reference_count, 1)

    def test_79_none_upstream_collection_rejected(self):
        args, engine, iso, previous, refs = assembled()
        array = list(args)
        array[4] = None
        with self.assertRaises(TypeError):
            assess_fed_calendar_rfc3161_runtime_attestation(
                *array, engine, iso, previous, refs, as_of=AS_OF,
            )

    def test_80_upstream_generator_materialized_once(self):
        args, engine, iso, previous, refs = assembled()
        args = list(args)
        for index in (4, 6, 9, 11, 14):
            args[index] = (x for x in args[index])
        with patch.object(impl, "assess_fed_calendar_rfc3161_execution_isolation", return_value=previous):
            r = assess_fed_calendar_rfc3161_runtime_attestation(
                *args, engine, (x for x in iso), previous, (x for x in refs), as_of=AS_OF,
            )
        self.assertEqual(r.reference_candidate_count, 1)

    def test_81_mutated_frozen_reference_revalidated(self):
        args, engine, iso, previous, refs = assembled()
        object.__setattr__(refs[0], "claimed_controls", ("ALLOW_UNTRUSTED_ATTESTOR",))
        with patch.object(impl, "assess_fed_calendar_rfc3161_execution_isolation", return_value=previous):
            with self.assertRaises(FedRfc3161RuntimeAttestationError):
                assess_fed_calendar_rfc3161_runtime_attestation(
                    *args, engine, iso, previous, refs, as_of=AS_OF,
                )

    def test_82_forged_empty_blockers_never_authorize(self):
        forgery = replace(run(), blockers=(), targets=(), reference_candidate_count=100)
        self.assertFalse(forgery.council_admission_authorized)
        self.assertFalse(forgery.attestation_signature_verified)
        with self.assertRaises(FedRfc3161RuntimeAttestationUnavailable):
            forgery.require_trusted_runtime_attestation()

    def test_83_immutable_report_target_checks(self):
        r = run()
        with self.assertRaises(FrozenInstanceError):
            r.blockers = ()
        with self.assertRaises(FrozenInstanceError):
            r.targets[0].state = W.UNVERIFIED_ATTESTATION_PACKAGE_CANDIDATE_ONLY
        with self.assertRaises(FrozenInstanceError):
            r.targets[0].checks[0].state = S.MISSING

    def test_84_forged_upstream_report_digest_rejected(self):
        args, engine, iso, previous, refs = assembled()
        bad = replace(previous, diagnostic_manifest_sha256="0"*64)
        with patch.object(impl, "assess_fed_calendar_rfc3161_execution_isolation", return_value=previous):
            with self.assertRaises(FedRfc3161RuntimeAttestationError):
                assess_fed_calendar_rfc3161_runtime_attestation(
                    *args, engine, iso, bad, refs, as_of=AS_OF,
                )

    def test_85_forged_upstream_clear_blockers_rejected(self):
        args, engine, iso, previous, refs = assembled()
        bad = replace(previous, blockers=())
        with patch.object(impl, "assess_fed_calendar_rfc3161_execution_isolation", return_value=previous):
            with self.assertRaises(FedRfc3161RuntimeAttestationError):
                assess_fed_calendar_rfc3161_runtime_attestation(
                    *args, engine, iso, bad, refs, as_of=AS_OF,
                )

    def test_86_forged_upstream_plan_count_rejected(self):
        args, engine, iso, previous, refs = assembled()
        bad = replace(previous, plan_candidate_count=999)
        with patch.object(impl, "assess_fed_calendar_rfc3161_execution_isolation", return_value=previous):
            with self.assertRaises(FedRfc3161RuntimeAttestationError):
                assess_fed_calendar_rfc3161_runtime_attestation(
                    *args, engine, iso, bad, refs, as_of=AS_OF,
                )

    def test_87_untrusted_upstream_revalidator_exception_wrapped(self):
        with self.assertRaises(FedRfc3161RuntimeAttestationError):
            run(previous_error=FedRfc3161IsolationError("bad evidence"))

    def test_88_wrong_upstream_type_rejected(self):
        args, engine, iso, previous, refs = assembled()
        with self.assertRaises(TypeError):
            assess_fed_calendar_rfc3161_runtime_attestation(
                *args, engine, iso, object(), refs, as_of=AS_OF,
            )

    def test_89_stale_asof_rejected(self):
        args, engine, iso, previous, refs = assembled()
        with patch.object(impl, "assess_fed_calendar_rfc3161_execution_isolation", return_value=previous):
            with self.assertRaises(FedRfc3161RuntimeAttestationError):
                assess_fed_calendar_rfc3161_runtime_attestation(
                    *args, engine, iso, previous, refs,
                    as_of=AS_OF+timedelta(seconds=1),
                )

    def test_90_invalid_asof_rejected(self):
        args, engine, iso, previous, refs = assembled()
        for value in (None, datetime(2026, 10, 14), "2026-10-14", True, 123):
            with self.subTest(value=value):
                with self.assertRaises(FedRfc3161RuntimeAttestationError):
                    assess_fed_calendar_rfc3161_runtime_attestation(
                        *args, engine, iso, previous, refs, as_of=value,
                    )

    def test_91_no_timestamp_targets_still_denies_all(self):
        base = f4514_adapter(targets=())
        r = run(base=base, material_refs=())
        self.assertEqual(r.target_count, 0)
        self.assertEqual(r.reference_candidate_count, 0)
        self.assertFalse(r.council_admission_authorized)

    def test_92_conflicting_upstream_isolation_stays_blocked(self):
        r = run(iso_refs=(f4516_reference(claimed_adapter_receipt_sha256="f"*64),),
                att_refs=())
        self.assertEqual(check(r, 0), S.CONFLICT)
        self.assertEqual(r.targets[0].state, W.BLOCKED_UPSTREAM_OR_REFERENCE_CONFLICT)

    def test_93_missing_upstream_isolation_never_borrows_another(self):
        r = run(iso_refs=(), att_refs=())
        self.assertEqual(check(r, 0), S.MISSING)
        self.assertEqual(r.targets[0].state, W.BLOCKED_INCOMPLETE_ATTESTATION_REFERENCES)

    def test_94_two_targets_order_independent(self):
        base = f4514_adapter(targets=(target(), target(claim_id="TOKEN_002")))
        mats = (material(), material(claim_id="TOKEN_002"))
        vinputs = (_verifier_input(), _verifier_input(claim_id="TOKEN_002"))
        policies = (_policy(), _policy(claim_id="TOKEN_002"))
        args, eng, _ = f4516_assemble(
            materials=mats, base=base, verify_inputs=vinputs, policies=policies,
        )
        iso = (f4516_reference(eng, material_ref=mats[0]),
               f4516_reference(eng, material_ref=mats[1], claim_id="TOKEN_002"))
        with patch.object(f4516_impl, "assess_fed_calendar_rfc3161_engine_prerequisites",
                          return_value=eng):
            prev = assess_fed_calendar_rfc3161_execution_isolation(
                *args, eng, iso, as_of=AS_OF,
            )
        refs = tuple(attestation(prev, isolation_ref=iso[i], material_ref=mats[i],
                                 claim_id=iso[i].claim_id) for i in range(2))
        # Second upstream isolation reference must be explicit, not borrowed.
        self.assertEqual(prev.target_count, 2)
        self.assertEqual(len(iso), 2)
        second = replace(refs[1], claimed_attestor_identity_sha256="9"*64)
        with patch.object(impl, "assess_fed_calendar_rfc3161_execution_isolation", return_value=prev):
            a = assess_fed_calendar_rfc3161_runtime_attestation(
                *args, eng, iso, prev, (refs[0], second), as_of=AS_OF,
            )
            b = assess_fed_calendar_rfc3161_runtime_attestation(
                *args, eng, iso, prev, (second, refs[0]), as_of=AS_OF,
            )
        self.assertEqual(a.diagnostic_manifest_sha256, b.diagnostic_manifest_sha256)
        self.assertEqual(a.reference_candidate_count, 2)

    def test_95_second_missing_reference_cannot_borrow_first(self):
        base = f4514_adapter(targets=(target(), target(claim_id="TOKEN_002")))
        mats = (material(), material(claim_id="TOKEN_002"))
        vinputs = (_verifier_input(), _verifier_input(claim_id="TOKEN_002"))
        policies = (_policy(), _policy(claim_id="TOKEN_002"))
        args, eng, _ = f4516_assemble(
            materials=mats, base=base, verify_inputs=vinputs, policies=policies,
        )
        iso = (f4516_reference(eng, material_ref=mats[0]),
               f4516_reference(eng, material_ref=mats[1], claim_id="TOKEN_002"))
        with patch.object(f4516_impl, "assess_fed_calendar_rfc3161_engine_prerequisites",
                          return_value=eng):
            prev = assess_fed_calendar_rfc3161_execution_isolation(
                *args, eng, iso, as_of=AS_OF,
            )
        first = attestation(prev, isolation_ref=iso[0], material_ref=mats[0])
        with patch.object(impl, "assess_fed_calendar_rfc3161_execution_isolation", return_value=prev):
            r = assess_fed_calendar_rfc3161_runtime_attestation(
                *args, eng, iso, prev, (first,), as_of=AS_OF,
            )
        self.assertEqual(r.reference_candidate_count, 1)
        self.assertEqual(r.targets[1].state, W.BLOCKED_INCOMPLETE_ATTESTATION_REFERENCES)

    def test_96_no_network_execution_filesystem_db_imports(self):
        tree = ast.parse(inspect.getsource(impl))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(n.name.partition(".")[0] for n in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.partition(".")[0])
        self.assertFalse(roots & {
            "os", "subprocess", "ctypes", "socket", "requests", "urllib", "ssl",
            "pathlib", "shutil", "psycopg2", "sqlite3", "cryptography",
            "importlib", "tempfile", "threading", "multiprocessing",
        })

    def test_97_no_raw_attestation_proof_or_urls_in_receipt(self):
        rendered = repr(run())
        for forbidden in ("BEGIN CERTIFICATE", "ATTESTOR PRIVATE KEY", "https://untrusted.example",
                          "<html>", "subprocess.Popen"):
            self.assertNotIn(forbidden, rendered)

    def test_98_complete_attestation_is_not_signed_attestation(self):
        r = run()
        self.assertEqual(r.reference_candidate_count, 1)
        self.assertFalse(r.attestation_signature_verified)
        self.assertFalse(r.trusted_operator_policy_authenticated)

    def test_99_empty_candidate_list_preserves_permanent_blockers(self):
        r = run(att_refs=())
        self.assertTrue(any("NO_RFC3161_CRYPTOGRAPHIC_VERDICT" in b for b in r.blockers))
        self.assertFalse(r.historical_replay_authorized)

    def test_100_f4517_genuine_previous_fixture_round_trip_non_authorizing(self):
        args = _real_inputs()
        r = assess_fed_calendar_rfc3161_runtime_attestation(*args, as_of=REAL_CHAIN_AS_OF)
        self.assertEqual(r.reference_candidate_count, 1)
        self.assertFalse(r.os_sandbox_enforced)
        self.assertFalse(r.rfc3161_cryptographically_verified)

    def test_101_f4517_genuine_previous_fixture_rejects_forged_report(self):
        args = list(_real_inputs())
        args[-2] = replace(args[-2], blockers=())
        with self.assertRaises(FedRfc3161RuntimeAttestationError):
            assess_fed_calendar_rfc3161_runtime_attestation(*args, as_of=REAL_CHAIN_AS_OF)

    def test_102_f4517_genuine_previous_fixture_rejects_stale_asof(self):
        args = _real_inputs()
        with self.assertRaises(FedRfc3161RuntimeAttestationError):
            assess_fed_calendar_rfc3161_runtime_attestation(
                *args, as_of=REAL_CHAIN_AS_OF + timedelta(seconds=1),
            )


def _real_inputs():
    """Real F4.5.14/15/16 call chain, not a patched F4.5.17 revalidator."""
    from backend.macro.test_fed_calendar_crypto_adapter import package
    from backend.macro.fed_calendar_crypto_adapter import prepare_fed_calendar_crypto_adapter
    packed = package(as_of=REAL_CHAIN_AS_OF)
    adapter = prepare_fed_calendar_crypto_adapter(*packed, as_of=REAL_CHAIN_AS_OF)
    handed = adapter.targets[0]
    m = material(
        claimed_token_sha256=handed.token_body_sha256,
        claimed_document_sha256=handed.document_body_sha256,
        claimed_policy_revision_sha256=handed.policy_revision_reference_sha256,
        claimed_tsa_trust_anchor_sha256=packed[11][0].pinned_tsa_anchor_certificate_sha256,
        claimed_revocation_evidence_sha256=packed[9][0].claimed_revocation_evidence_sha256,
        claimed_request_der_sha256=packed[9][0].claimed_request_der_sha256,
        claimed_material_observed_at=REAL_CHAIN_AS_OF-timedelta(minutes=15),
    )
    engine = assess_fed_calendar_rfc3161_engine_prerequisites(
        *packed, adapter, (m,), as_of=REAL_CHAIN_AS_OF,
    )
    iso = f4516_reference(
        engine, material_ref=m,
        claimed_material_observed_at=REAL_CHAIN_AS_OF-timedelta(minutes=10),
    )
    isolate = assess_fed_calendar_rfc3161_execution_isolation(
        *packed, adapter, (m,), engine, (iso,), as_of=REAL_CHAIN_AS_OF,
    )
    ref = attestation(
        isolate, isolation_ref=iso, material_ref=m,
        claimed_runtime_started_at=REAL_CHAIN_AS_OF-timedelta(minutes=5),
        claimed_attestation_observed_at=REAL_CHAIN_AS_OF-timedelta(minutes=1),
    )
    return (*packed, adapter, (m,), engine, (iso,), isolate, (ref,))


if __name__ == "__main__":
    unittest.main(verbosity=2)

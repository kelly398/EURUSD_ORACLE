"""F4.5.17: offline, fail-closed RFC 3161 runtime-attestation prerequisites.

This module records *untrusted reference claims* for future independently
measured verifier runtime attestation. It DOES NOT execute a process, measure
files, verify a signed attestation, provision a trust anchor, read a certificate,
query an OS/TPM/TEE, or grant admission. SHA-256 labels, apparently signed
attestation envelopes, matching runtime nonces and declared enforcement controls
never constitute verified runtime trust.

Recomputes F4.5.16 and the F4.5.6--15 source chain using original inputs.
A future reviewed verifier must independently authenticate operator policy,
process/dependency measurements, the challenge, attestor identity and actual
OS-enforced isolation, then separately verify RFC 3161 cryptography. Historical
Fed source provenance and Council authorization remain independent gates.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from itertools import islice
import json
import re

from backend.macro.fed_calendar_crypto_adapter import FedCryptoAdapterPlan
from backend.macro.fed_calendar_crypto_prerequisites import MAX_VERIFIER_INPUTS
from backend.macro.fed_calendar_independent_evidence import MAX_EXTERNAL_EVIDENCE_CLAIMS
from backend.macro.fed_calendar_rfc3161_engine_prerequisites import (
    FedRfc3161EngineMaterialReference, FedRfc3161EnginePrerequisitesReport,
    MAX_ENGINE_PROVISIONING_REFERENCES,
)
from backend.macro.fed_calendar_rfc3161_execution_isolation import (
    FedRfc3161IsolationReference, FedRfc3161ExecutionIsolationReport,
    FedRfc3161IsolationError, IsolationPlanState, MAX_ISOLATION_CLAIMS,
    assess_fed_calendar_rfc3161_execution_isolation,
)
from backend.macro.fed_calendar_verifier_boundary import MAX_TRUST_POLICY_REFERENCES

RUNTIME_ATTESTATION_PROFILE = "RFC3161_RUNTIME_ATTESTATION_NOEXEC_QUARANTINE_V1"
MAX_RUNTIME_ATTESTATION_REFERENCES = MAX_ISOLATION_CLAIMS

# These are future independent proof requirements, never proof that an OS
# kernel, trusted attestor, or trusted verifier has enforced any of them.
REQUIRED_RUNTIME_ATTESTATION_CONTROLS = tuple(sorted((
    "REQUIRE_OPERATOR_PINNED_VERIFIER_IMAGE_MEASUREMENT",
    "REQUIRE_OPERATOR_PINNED_DEPENDENCY_MEASUREMENT",
    "REQUIRE_INDEPENDENTLY_APPROVED_ATTESTOR_IDENTITY",
    "REQUIRE_INDEPENDENT_OPERATOR_CHALLENGE_BINDING",
    "REQUIRE_VERIFIED_ATTESTATION_ENVELOPE_SIGNATURE",
    "REQUIRE_VERIFIED_SANDBOX_ENFORCEMENT_MEASUREMENT",
    "REQUIRE_MEASURED_TRUST_STORE_AND_TSA_ANCHOR",
    "REQUIRE_FRESH_PROTECTED_RUNTIME_INSTANCE_IDENTITY",
    "REQUIRE_BOUNDED_REPLAY_SAFE_OBSERVATION_WINDOW",
    "REQUIRE_SEPARATE_RFC3161_CRYPTOGRAPHIC_VERDICT",
    "DENY_CALLER_CONTROLLED_ATTESTATION_SUCCESS",
    "DENY_VERIFIER_NETWORK_BROKER_DATABASE_COUNCIL_HMM_ACCESS",
    "DENY_HISTORICAL_REPLAY_AND_AUTOMATIC_ADMISSION",
)))

RUNTIME_ATTESTATION_CHECK_CODES = (
    "CANONICAL_F4516_ISOLATION_REPORT_REVALIDATED",
    "CLAIMED_F4516_ISOLATION_RECEIPT_RECONCILED",
    "CLAIMED_F4514_ADAPTER_RECEIPT_RECONCILED",
    "CLAIMED_F4515_ENGINE_RECEIPT_RECONCILED",
    "CLAIMED_VERIFIER_IMAGE_AND_DEPENDENCY_LOCK_RECONCILED",
    "CLAIMED_TSA_TRUST_STORE_AND_ANCHOR_RECONCILED",
    "CLAIMED_RUNTIME_ISOLATION_POLICY_AND_RESULT_SCHEMA_RECONCILED",
    "CLAIMED_PROCESS_IMAGE_AND_LOADED_DEPENDENCY_MEASUREMENTS_PRESENT",
    "CLAIMED_OPERATOR_APPROVAL_AND_OS_MEASUREMENT_POLICIES_PRESENT",
    "CLAIMED_SANDBOX_ENFORCEMENT_EVIDENCE_PRESENT",
    "CLAIMED_ATTESTOR_IDENTITY_AND_SIGNATURE_ENVELOPE_PRESENT",
    "CLAIMED_CHALLENGE_ECHO_RECONCILED_NOT_PROVEN_FRESH",
    "CLAIMED_RUNTIME_AND_ATTESTATION_CLOCKS_LOCALLY_CONSISTENT",
    "STRICT_NONEXECUTING_ATTESTATION_REQUIREMENTS_DECLARED",
    "MEASURED_BINARY_AND_DEPENDENCIES_NOT_INDEPENDENTLY_VERIFIED",
    "OPERATOR_POLICY_AND_ATTESTOR_TRUST_NOT_INDEPENDENTLY_VERIFIED",
    "ATTESTATION_SIGNATURE_CHALLENGE_AND_FRESHNESS_UNVERIFIED",
    "OS_ENFORCED_ISOLATION_AND_RUNTIME_IDENTITY_UNATTESTED",
    "RFC3161_CMS_PKIX_IMPRINT_AND_SIGNED_TIME_UNVERIFIED",
    "INDEPENDENT_HISTORICAL_SOURCE_PROVENANCE_NOT_ATTESTED",
    "COUNCIL_REPLAY_PERSISTENCE_BROKER_AND_TRADE_DENIED",
)

_ALWAYS_BLOCKERS = (
    "FED_F4517_NO_INDEPENDENT_VERIFIER_RUNTIME_MEASUREMENT",
    "FED_F4517_NO_TRUSTED_ATTESTOR_OR_SIGNED_POLICY_VERIFICATION",
    "FED_F4517_NO_PROVEN_OS_SANDBOX_ISOLATION",
    "FED_F4517_NO_AUTHENTICATED_NONCE_OR_ATTESTATION_FRESHNESS",
    "FED_F4517_NO_RFC3161_CRYPTOGRAPHIC_VERDICT",
    "FED_F4517_NO_INDEPENDENT_HISTORICAL_SOURCE_ATTESTATION",
    "FED_F4517_COUNCIL_REPLAY_PERSISTENCE_TRADE_DENIED",
)


class FedRfc3161RuntimeAttestationError(ValueError):
    """Malformed claims or an inconsistent/forged upstream report."""


class FedRfc3161RuntimeAttestationUnavailable(RuntimeError):
    """No trusted runtime, attestation, or Council-admission authority exists."""


class RuntimeAttestationCheckState(str, Enum):
    UPSTREAM_PLAN_ONLY = "UPSTREAM_PLAN_ONLY"
    UNTRUSTED_CLAIM_MATCH_ONLY = "UNTRUSTED_CLAIM_MATCH_ONLY"
    UNTRUSTED_PRESENCE_ONLY = "UNTRUSTED_PRESENCE_ONLY"
    MISSING = "MISSING"
    CONFLICT = "CONFLICT"
    INDEPENDENT_ATTESTATION_REQUIRED = "INDEPENDENT_ATTESTATION_REQUIRED"


class RuntimeAttestationPlanState(str, Enum):
    BLOCKED_UPSTREAM_OR_REFERENCE_CONFLICT = "BLOCKED_UPSTREAM_OR_REFERENCE_CONFLICT"
    BLOCKED_INCOMPLETE_ATTESTATION_REFERENCES = "BLOCKED_INCOMPLETE_ATTESTATION_REFERENCES"
    UNVERIFIED_ATTESTATION_PACKAGE_CANDIDATE_ONLY = "UNVERIFIED_ATTESTATION_PACKAGE_CANDIDATE_ONLY"


def _utc(value: datetime, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise FedRfc3161RuntimeAttestationError(f"{name} must be a timezone-aware datetime.")
    return value.astimezone(timezone.utc)


def _identifier(value: str, name: str) -> str:
    if type(value) is not str or re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,63}", value) is None:
        raise FedRfc3161RuntimeAttestationError(f"Invalid {name} identity.")
    return value


def _sha(value: str | None, name: str) -> str | None:
    if value is not None and (type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None):
        raise FedRfc3161RuntimeAttestationError(f"Invalid {name} SHA-256 reference.")
    return value


def _take(items, maximum: int, name: str) -> tuple:
    if items is None:
        raise TypeError(f"{name} cannot be None.")
    try:
        values = tuple(islice(items, maximum + 1))
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be iterable.") from exc
    if len(values) > maximum:
        raise FedRfc3161RuntimeAttestationError(f"{name} count limit exceeded.")
    return values


def _digest(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _iso(value: datetime | None) -> str | None:
    return _utc(value, "claimed runtime timestamp").isoformat(timespec="microseconds") if value else None


@dataclass(frozen=True)
class FedRfc3161RuntimeAttestationReference:
    """UNTRUSTED, non-executable reference manifest. No raw proof, URLs or I/O.

    Matches to F4.5.16 and even a self-consistent signed-envelope *label*
    mean only that strings coincide. An attestor must be independently trusted;
    a digest or an operator identity asserted by this caller is not sufficient.
    """

    provider_id: str
    claim_id: str
    claimed_profile: str = RUNTIME_ATTESTATION_PROFILE
    claimed_isolation_receipt_sha256: str | None = None
    claimed_adapter_receipt_sha256: str | None = None
    claimed_engine_prerequisites_sha256: str | None = None
    claimed_engine_build_sha256: str | None = None
    claimed_dependency_lock_sha256: str | None = None
    claimed_trust_store_sha256: str | None = None
    claimed_tsa_anchor_sha256: str | None = None
    claimed_isolation_runtime_sha256: str | None = None
    claimed_isolation_policy_sha256: str | None = None
    claimed_result_schema_sha256: str | None = None
    claimed_process_image_measurement_sha256: str | None = None
    claimed_loaded_dependencies_measurement_sha256: str | None = None
    claimed_operator_approval_policy_sha256: str | None = None
    claimed_os_measurement_policy_sha256: str | None = None
    claimed_sandbox_enforcement_evidence_sha256: str | None = None
    claimed_attestor_identity_sha256: str | None = None
    claimed_attestation_signature_envelope_sha256: str | None = None
    claimed_operator_challenge_sha256: str | None = None
    claimed_attested_challenge_sha256: str | None = None
    claimed_runtime_started_at: datetime | None = None
    claimed_attestation_observed_at: datetime | None = None
    claimed_controls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_reference(self)


_REFERENCE_SHA_FIELDS = (
    "claimed_isolation_receipt_sha256", "claimed_adapter_receipt_sha256",
    "claimed_engine_prerequisites_sha256", "claimed_engine_build_sha256",
    "claimed_dependency_lock_sha256", "claimed_trust_store_sha256",
    "claimed_tsa_anchor_sha256", "claimed_isolation_runtime_sha256",
    "claimed_isolation_policy_sha256", "claimed_result_schema_sha256",
    "claimed_process_image_measurement_sha256", "claimed_loaded_dependencies_measurement_sha256",
    "claimed_operator_approval_policy_sha256", "claimed_os_measurement_policy_sha256",
    "claimed_sandbox_enforcement_evidence_sha256", "claimed_attestor_identity_sha256",
    "claimed_attestation_signature_envelope_sha256", "claimed_operator_challenge_sha256",
    "claimed_attested_challenge_sha256",
)


def _validate_reference(ref: FedRfc3161RuntimeAttestationReference) -> None:
    # Frozen dataclass can be maliciously mutated using object.__setattr__.
    if type(ref) is not FedRfc3161RuntimeAttestationReference:
        raise FedRfc3161RuntimeAttestationError("Exact runtime reference model required.")
    _identifier(ref.provider_id, "provider_id")
    _identifier(ref.claim_id, "claim_id")
    if type(ref.claimed_profile) is not str or ref.claimed_profile != RUNTIME_ATTESTATION_PROFILE:
        raise FedRfc3161RuntimeAttestationError("Non-executing runtime attestation profile required.")
    for name in _REFERENCE_SHA_FIELDS:
        _sha(getattr(ref, name), name)
    for name in ("claimed_runtime_started_at", "claimed_attestation_observed_at"):
        if getattr(ref, name) is not None:
            _utc(getattr(ref, name), name)
    controls = ref.claimed_controls
    if (type(controls) is not tuple or any(type(c) is not str for c in controls)
            or controls != tuple(sorted(set(controls)))
            or not set(controls).issubset(REQUIRED_RUNTIME_ATTESTATION_CONTROLS)):
        raise FedRfc3161RuntimeAttestationError(
            "Controls must be a strict sorted unique subset of non-executing requirements."
        )


@dataclass(frozen=True)
class FedRuntimeAttestationCheck:
    code: str
    state: RuntimeAttestationCheckState


@dataclass(frozen=True)
class FedRuntimeAttestationTarget:
    provider_id: str
    claim_id: str
    token_sha256: str | None
    document_sha256: str | None
    claimed_process_image_measurement_sha256: str | None
    state: RuntimeAttestationPlanState
    checks: tuple[FedRuntimeAttestationCheck, ...]


@dataclass(frozen=True)
class FedRfc3161RuntimeAttestationReport:
    """Sealed diagnostic receipt, never a trusted measurement or authorization."""

    as_of: datetime
    source_url: str
    canonical_adapter_sha256: str
    canonical_engine_prerequisites_sha256: str
    canonical_isolation_sha256: str
    reference_manifest_sha256: str
    target_count: int
    reference_count: int
    reference_candidate_count: int
    targets: tuple[FedRuntimeAttestationTarget, ...]
    required_controls: tuple[str, ...]
    blockers: tuple[str, ...]
    diagnostic_manifest_sha256: str

    @property
    def verifier_runtime_measured(self) -> bool:
        return False

    @property
    def attestation_signature_verified(self) -> bool:
        return False

    @property
    def independently_provisioned_attestor_trusted(self) -> bool:
        return False

    @property
    def trusted_operator_policy_authenticated(self) -> bool:
        return False

    @property
    def runtime_challenge_freshness_proven(self) -> bool:
        return False

    @property
    def os_sandbox_enforced(self) -> bool:
        return False

    @property
    def independent_tsa_anchor_authenticated(self) -> bool:
        return False

    @property
    def trusted_verifier_executed(self) -> bool:
        return False

    @property
    def rfc3161_cryptographically_verified(self) -> bool:
        return False

    @property
    def historical_source_version_attested(self) -> bool:
        return False

    @property
    def council_admission_authorized(self) -> bool:
        return False

    @property
    def historical_replay_authorized(self) -> bool:
        return False

    @property
    def persistence_authorized(self) -> bool:
        return False

    @property
    def directional_trading_score(self) -> int:
        return 0

    def require_trusted_runtime_attestation(self) -> None:
        raise FedRfc3161RuntimeAttestationUnavailable("No independently verified runtime attestation exists.")

    def require_trusted_verifier_execution(self) -> None:
        raise FedRfc3161RuntimeAttestationUnavailable("No independently verified verifier has executed.")

    def require_council_calendar_delivery(self) -> None:
        raise FedRfc3161RuntimeAttestationUnavailable("Council Seat 25 calendar admission remains blocked.")

    def require_historical_replay(self) -> None:
        raise FedRfc3161RuntimeAttestationUnavailable("Historical replay requires independently authenticated source versions.")

    def require_persistence(self) -> None:
        raise FedRfc3161RuntimeAttestationUnavailable("Fed persistence remains prohibited.")

    def accept_external_attestation_verdict(self, claimed_verdict: object) -> None:
        raise FedRfc3161RuntimeAttestationUnavailable("Caller-controlled runtime success assertions are forbidden.")


def _match(expected: str | None, claimed: str | None) -> RuntimeAttestationCheckState:
    if expected is None or claimed is None:
        return RuntimeAttestationCheckState.MISSING
    if expected != claimed:
        return RuntimeAttestationCheckState.CONFLICT
    return RuntimeAttestationCheckState.UNTRUSTED_CLAIM_MATCH_ONLY


def _presence(*values: str | None) -> RuntimeAttestationCheckState:
    return (RuntimeAttestationCheckState.UNTRUSTED_PRESENCE_ONLY
            if all(x is not None for x in values) else RuntimeAttestationCheckState.MISSING)


def _combine(*states: RuntimeAttestationCheckState) -> RuntimeAttestationCheckState:
    if RuntimeAttestationCheckState.CONFLICT in states:
        return RuntimeAttestationCheckState.CONFLICT
    if RuntimeAttestationCheckState.MISSING in states:
        return RuntimeAttestationCheckState.MISSING
    return RuntimeAttestationCheckState.UNTRUSTED_CLAIM_MATCH_ONLY


def assess_fed_calendar_rfc3161_runtime_attestation(
    acquisition,
    extraction,
    audit,
    gaps,
    claims,
    review,
    captures,
    intake,
    authenticity,
    verifier_inputs,
    prerequisites,
    policy_references,
    boundary,
    adapter: FedCryptoAdapterPlan,
    engine_references,
    engine_report: FedRfc3161EnginePrerequisitesReport,
    isolation_references,
    isolation_report: FedRfc3161ExecutionIsolationReport,
    attestation_references,
    *,
    as_of: datetime,
) -> FedRfc3161RuntimeAttestationReport:
    """Construct an untrusted attestation inventory after full F4.5.16 revalidation.

    The supplied F4.5.16 result is only a comparison target, not authority.
    Never executes, reads a file, imports an execution engine or verifies a
    claimed signature. Claims cannot change the immutable authorization flags.
    """
    now = _utc(as_of, "as_of")
    if type(adapter) is not FedCryptoAdapterPlan:
        raise TypeError("Exact F4.5.14 adapter model required.")
    if type(engine_report) is not FedRfc3161EnginePrerequisitesReport:
        raise TypeError("Exact F4.5.15 engine report model required.")
    if type(isolation_report) is not FedRfc3161ExecutionIsolationReport:
        raise TypeError("Exact F4.5.16 isolation report model required.")

    held_claims = _take(claims, MAX_EXTERNAL_EVIDENCE_CLAIMS, "claims")
    held_captures = _take(captures, MAX_EXTERNAL_EVIDENCE_CLAIMS, "captures")
    held_inputs = _take(verifier_inputs, MAX_VERIFIER_INPUTS, "verifier_inputs")
    held_policies = _take(policy_references, MAX_TRUST_POLICY_REFERENCES, "policy_references")
    held_materials = _take(engine_references, MAX_ENGINE_PROVISIONING_REFERENCES, "engine_references")
    held_isolation = _take(isolation_references, MAX_ISOLATION_CLAIMS, "isolation_references")
    held_references = _take(attestation_references, MAX_RUNTIME_ATTESTATION_REFERENCES,
                            "attestation_references")

    try:
        canonical = assess_fed_calendar_rfc3161_execution_isolation(
            acquisition, extraction, audit, gaps, held_claims, review,
            held_captures, intake, authenticity, held_inputs, prerequisites,
            held_policies, boundary, adapter, held_materials, engine_report,
            held_isolation, as_of=now,
        )
    except (FedRfc3161IsolationError, TypeError, ValueError) as exc:
        raise FedRfc3161RuntimeAttestationError(
            "Cannot independently recompute F4.5.16 and original evidence chain."
        ) from exc
    if canonical != isolation_report or canonical.as_of != now:
        raise FedRfc3161RuntimeAttestationError("Forged, stale or inconsistent F4.5.16 report.")
    if canonical.canonical_engine_prerequisites_sha256 != engine_report.diagnostic_manifest_sha256:
        raise FedRfc3161RuntimeAttestationError("Inconsistent F4.5.15 handoff.")
    if canonical.canonical_adapter_sha256 != adapter.diagnostic_manifest_sha256:
        raise FedRfc3161RuntimeAttestationError("Inconsistent F4.5.14 handoff.")

    by_isolation = {}
    for ref in held_isolation:
        if type(ref) is not FedRfc3161IsolationReference:
            raise FedRfc3161RuntimeAttestationError("Exact F4.5.16 isolation reference required.")
        key = (ref.provider_id, ref.claim_id)
        if key in by_isolation:
            raise FedRfc3161RuntimeAttestationError("Duplicate upstream isolation reference.")
        by_isolation[key] = ref
    by_material = {}
    for ref in held_materials:
        if type(ref) is not FedRfc3161EngineMaterialReference:
            raise FedRfc3161RuntimeAttestationError("Exact F4.5.15 engine reference required.")
        key = (ref.provider_id, ref.claim_id)
        if key in by_material:
            raise FedRfc3161RuntimeAttestationError("Duplicate upstream engine material reference.")
        by_material[key] = ref
    targets_by_key = {(t.provider_id, t.claim_id): t for t in canonical.targets}
    references_by_key = {}
    for ref in held_references:
        _validate_reference(ref)
        key = (ref.provider_id, ref.claim_id)
        if key not in targets_by_key:
            raise FedRfc3161RuntimeAttestationError("Attestation claim refers to unknown timestamp token.")
        if key in references_by_key:
            raise FedRfc3161RuntimeAttestationError("Duplicate attestation reference identity.")
        references_by_key[key] = ref

    canonical_references = sorted(references_by_key.values(), key=lambda r: (r.provider_id, r.claim_id))
    reference_manifest = [{
        "id": (r.provider_id, r.claim_id),
        "profile": r.claimed_profile,
        "sha256_references": [(name, getattr(r, name)) for name in _REFERENCE_SHA_FIELDS],
        "runtime_started": _iso(r.claimed_runtime_started_at),
        "attestation_observed": _iso(r.claimed_attestation_observed_at),
        "controls": r.claimed_controls,
    } for r in canonical_references]
    refs_digest = _digest(reference_manifest)

    blockers = list(dict.fromkeys((*canonical.blockers, *_ALWAYS_BLOCKERS)))
    targets = []
    for t in canonical.targets:
        key = (t.provider_id, t.claim_id)
        claim = references_by_key.get(key)
        iso_ref = by_isolation.get(key)
        eng_ref = by_material.get(key)
        checks = []

        def check(index: int, state: RuntimeAttestationCheckState) -> None:
            checks.append(FedRuntimeAttestationCheck(RUNTIME_ATTESTATION_CHECK_CODES[index], state))

        if t.state is IsolationPlanState.NONEXECUTING_ISOLATION_PLAN_CANDIDATE_ONLY:
            check(0, RuntimeAttestationCheckState.UPSTREAM_PLAN_ONLY)
        elif t.state is IsolationPlanState.BLOCKED_UPSTREAM_OR_LOCAL_CONFLICT:
            check(0, RuntimeAttestationCheckState.CONFLICT)
        else:
            check(0, RuntimeAttestationCheckState.MISSING)
        check(1, _match(canonical.diagnostic_manifest_sha256,
                        claim.claimed_isolation_receipt_sha256 if claim else None))
        check(2, _match(canonical.canonical_adapter_sha256,
                        claim.claimed_adapter_receipt_sha256 if claim else None))
        check(3, _match(canonical.canonical_engine_prerequisites_sha256,
                        claim.claimed_engine_prerequisites_sha256 if claim else None))
        check(4, _combine(
            _match(eng_ref.claimed_engine_build_sha256 if eng_ref else None,
                   claim.claimed_engine_build_sha256 if claim else None),
            _match(eng_ref.claimed_dependency_lock_sha256 if eng_ref else None,
                   claim.claimed_dependency_lock_sha256 if claim else None),
        ))
        check(5, _combine(
            _match(eng_ref.claimed_trust_store_sha256 if eng_ref else None,
                   claim.claimed_trust_store_sha256 if claim else None),
            _match(eng_ref.claimed_tsa_trust_anchor_sha256 if eng_ref else None,
                   claim.claimed_tsa_anchor_sha256 if claim else None),
        ))
        check(6, _combine(
            _match(iso_ref.claimed_isolation_runtime_sha256 if iso_ref else None,
                   claim.claimed_isolation_runtime_sha256 if claim else None),
            _match(iso_ref.claimed_isolation_policy_sha256 if iso_ref else None,
                   claim.claimed_isolation_policy_sha256 if claim else None),
            _match(iso_ref.claimed_result_schema_sha256 if iso_ref else None,
                   claim.claimed_result_schema_sha256 if claim else None),
        ))
        check(7, _presence(
            claim.claimed_process_image_measurement_sha256 if claim else None,
            claim.claimed_loaded_dependencies_measurement_sha256 if claim else None,
        ))
        check(8, _presence(
            claim.claimed_operator_approval_policy_sha256 if claim else None,
            claim.claimed_os_measurement_policy_sha256 if claim else None,
        ))
        check(9, _presence(claim.claimed_sandbox_enforcement_evidence_sha256 if claim else None))
        check(10, _presence(
            claim.claimed_attestor_identity_sha256 if claim else None,
            claim.claimed_attestation_signature_envelope_sha256 if claim else None,
        ))
        check(11, _match(
            claim.claimed_operator_challenge_sha256 if claim else None,
            claim.claimed_attested_challenge_sha256 if claim else None,
        ))
        if claim is None or claim.claimed_runtime_started_at is None or claim.claimed_attestation_observed_at is None:
            check(12, RuntimeAttestationCheckState.MISSING)
        else:
            started = _utc(claim.claimed_runtime_started_at, "claimed_runtime_started_at")
            observed = _utc(claim.claimed_attestation_observed_at, "claimed_attestation_observed_at")
            isolation_observed = (
                _utc(iso_ref.claimed_material_observed_at, "isolation material observation")
                if iso_ref is not None and iso_ref.claimed_material_observed_at is not None else None
            )
            if started > observed or observed > now or (isolation_observed is not None and started < isolation_observed):
                check(12, RuntimeAttestationCheckState.CONFLICT)
            elif isolation_observed is None:
                check(12, RuntimeAttestationCheckState.MISSING)
            else:
                check(12, RuntimeAttestationCheckState.UNTRUSTED_CLAIM_MATCH_ONLY)
        check(13, RuntimeAttestationCheckState.UNTRUSTED_PRESENCE_ONLY
              if claim is not None and claim.claimed_controls == REQUIRED_RUNTIME_ATTESTATION_CONTROLS
              else RuntimeAttestationCheckState.MISSING)
        for i in range(14, len(RUNTIME_ATTESTATION_CHECK_CODES)):
            check(i, RuntimeAttestationCheckState.INDEPENDENT_ATTESTATION_REQUIRED)
        initial_states = tuple(c.state for c in checks[:14])
        if RuntimeAttestationCheckState.CONFLICT in initial_states:
            state = RuntimeAttestationPlanState.BLOCKED_UPSTREAM_OR_REFERENCE_CONFLICT
            blockers.append("FED_F4517_UPSTREAM_OR_ATTESTATION_REFERENCE_CONFLICT")
        elif RuntimeAttestationCheckState.MISSING in initial_states:
            state = RuntimeAttestationPlanState.BLOCKED_INCOMPLETE_ATTESTATION_REFERENCES
            blockers.append("FED_F4517_ATTESTATION_REFERENCE_PACKAGE_INCOMPLETE")
        else:
            state = RuntimeAttestationPlanState.UNVERIFIED_ATTESTATION_PACKAGE_CANDIDATE_ONLY
            blockers.append("FED_F4517_ATTESTATION_CLAIMS_ONLY_NOT_CRYPTO_PROOF")
        targets.append(FedRuntimeAttestationTarget(
            provider_id=t.provider_id, claim_id=t.claim_id,
            token_sha256=t.token_sha256, document_sha256=t.document_sha256,
            claimed_process_image_measurement_sha256=(
                claim.claimed_process_image_measurement_sha256 if claim else None
            ),
            state=state, checks=tuple(checks),
        ))
    if not targets:
        blockers.append("FED_F4517_NO_TIMESTAMP_TARGETS")
    blockers = tuple(dict.fromkeys(blockers))
    receipt = {
        "contract": "fed_calendar_rfc3161_runtime_attestation_v1",
        "as_of": now.isoformat(timespec="microseconds"),
        "source_url": canonical.source_url,
        "adapter_digest": canonical.canonical_adapter_sha256,
        "engine_prerequisites_digest": canonical.canonical_engine_prerequisites_sha256,
        "isolation_digest": canonical.diagnostic_manifest_sha256,
        "reference_digest": refs_digest,
        "required_controls": REQUIRED_RUNTIME_ATTESTATION_CONTROLS,
        "targets": [{
            "id": (t.provider_id, t.claim_id),
            "token_sha256": t.token_sha256,
            "document_sha256": t.document_sha256,
            "process_measurement_sha256": t.claimed_process_image_measurement_sha256,
            "state": t.state.value,
            "checks": [(c.code, c.state.value) for c in t.checks],
        } for t in targets],
        "blockers": blockers,
    }
    return FedRfc3161RuntimeAttestationReport(
        as_of=now, source_url=canonical.source_url,
        canonical_adapter_sha256=canonical.canonical_adapter_sha256,
        canonical_engine_prerequisites_sha256=canonical.canonical_engine_prerequisites_sha256,
        canonical_isolation_sha256=canonical.diagnostic_manifest_sha256,
        reference_manifest_sha256=refs_digest,
        target_count=len(targets), reference_count=len(canonical_references),
        reference_candidate_count=sum(
            t.state is RuntimeAttestationPlanState.UNVERIFIED_ATTESTATION_PACKAGE_CANDIDATE_ONLY
            for t in targets
        ),
        targets=tuple(targets), required_controls=REQUIRED_RUNTIME_ATTESTATION_CONTROLS,
        blockers=blockers, diagnostic_manifest_sha256=_digest(receipt),
    )

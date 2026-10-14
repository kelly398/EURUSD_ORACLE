"""F4.5.16: fail-closed, offline RFC 3161 execution-isolation DESIGN boundary.

Produces deterministic, immutable *diagnostic plans* from fully revalidated
F4.5.6--15 evidence and claimed isolation materials. It DOES NOT provision or
execute a sandbox, locate/load/run a verifier, read local files, access a
network, trust an operator identity, accept a result, or authenticate a token.

Caller-provided hashes, control lists and resource budgets are untrusted
reference claims, even when locally consistent. A proposed work order NEVER
establishes an independent TSA anchor, a CMS signature, ESSCertIDv2, PKIX,
revocation, an RFC 3161 imprint/nonce/time, historical publisher custody,
or any Council/replay/persistence admission. A future independently reviewed
engine must enforce all controls at a process/OS boundary before any work.
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
    FedEngineWorkState,
    FedRfc3161EngineMaterialReference,
    FedRfc3161EnginePrerequisiteError,
    FedRfc3161EnginePrerequisitesReport,
    MAX_ENGINE_PROVISIONING_REFERENCES,
    assess_fed_calendar_rfc3161_engine_prerequisites,
)
from backend.macro.fed_calendar_verifier_boundary import MAX_TRUST_POLICY_REFERENCES

ISOLATION_PROFILE = "RFC3161_OFFLINE_NOEXEC_QUARANTINE_V1"
MAX_ISOLATION_CLAIMS = MAX_ENGINE_PROVISIONING_REFERENCES

# Upper bounds on *proposed* future sandbox resources. These values are not
# enforced by this module. Never interpret them as proof of kernel isolation.
MAX_TOKEN_INPUT_BYTES = 1_048_576
MAX_DOCUMENT_INPUT_BYTES = 8_388_608
MAX_CPU_BUDGET_MS = 2_000
MAX_MEMORY_BUDGET_BYTES = 134_217_728

REQUIRED_ISOLATION_CONTROLS = tuple(sorted((
    "DENY_NETWORK_AND_DNS",
    "DENY_SUBPROCESS_AND_SHELL",
    "DENY_DYNAMIC_CODE_AND_UNPINNED_PLUGINS",
    "DENY_FILE_WRITES_AND_UNDECLARED_READS",
    "DENY_DATABASE_BROKER_COUNCIL_AND_HMM_ACCESS",
    "DENY_AMBIENT_CREDENTIALS_AND_ENVIRONMENT_SECRETS",
    "DENY_REMOTE_CLOCK_AND_UNPINNED_TIME_SOURCES",
    "DENY_UNVERIFIED_VERIFIER_OUTPUT_AND_AUTO_ADMISSION",
    "REQUIRE_BOUNDED_TOKEN_DOCUMENT_MEMORY_AND_CPU",
    "REQUIRE_IMMUTABLE_OPERATOR_PROVISIONED_BINARY_AND_TRUST",
    "REQUIRE_SEPARATE_VERIFIER_AND_HISTORICAL_ADMISSION_POLICIES",
)))

ISOLATION_CHECK_CODES = (
    "CANONICAL_F4515_REFERENCE_PACKAGE_REVALIDATED",
    "PLAN_BINDS_TO_CANONICAL_F4514_ADAPTER_RECEIPT",
    "PLAN_BINDS_TO_CANONICAL_F4515_ENGINE_RECEIPT",
    "CLAIMED_VERIFIER_BUILD_REFERENCE_RECONCILED",
    "CLAIMED_DEPENDENCY_LOCK_REFERENCE_RECONCILED",
    "CLAIMED_TRUST_STORE_AND_TSA_ANCHOR_REFERENCES_RECONCILED",
    "CLAIMED_RUNTIME_AND_ISOLATION_POLICY_ARTIFACTS_PRESENT",
    "CLAIMED_OUTPUT_CONTRACT_ARTIFACT_PRESENT",
    "STRICT_NONEXECUTING_QUARANTINE_PROFILE_DECLARED",
    "REQUIRED_SANDBOX_DENIALS_DECLARED",
    "BOUNDED_RESOURCE_BUDGETS_ONLY_CLAIMED",
    "CLAIMED_ISOLATION_MATERIAL_OBSERVED_NO_LATER_THAN_ASOF",
    "INDEPENDENT_OPERATOR_PROVISIONING_NOT_ATTESTED",
    "ACTUAL_PROCESS_AND_KERNEL_ISOLATION_NOT_EXECUTED",
    "RFC3161_SIGNATURE_PATH_IMPRINT_POLICY_AND_TIME_UNVERIFIED",
    "HISTORICAL_PROVENANCE_COUNCIL_REPLAY_AND_STORAGE_NOT_ADMITTED",
)

_PERMANENT_BLOCKERS = (
    "FED_F4516_NO_INDEPENDENT_OPERATOR_PROVISIONING",
    "FED_F4516_NO_OS_ENFORCED_ISOLATED_VERIFIER_EXECUTION",
    "FED_F4516_NO_TRUSTED_RFC3161_CRYPTOGRAPHIC_VERDICT",
    "FED_F4516_NO_INDEPENDENT_HISTORICAL_SOURCE_ATTESTATION",
    "FED_F4516_COUNCIL_REPLAY_PERSISTENCE_AND_TRADE_DENIED",
)


class FedRfc3161IsolationError(ValueError):
    """Invalid local claims or forged/mismatched earlier-stage evidence."""


class FedRfc3161IsolationUnavailable(RuntimeError):
    """No trusted engine, isolation enforcement, or data-admission path exists."""


class IsolationCheckState(str, Enum):
    UPSTREAM_REFERENCE_ONLY = "UPSTREAM_REFERENCE_ONLY"
    UNTRUSTED_CLAIM_MATCH_ONLY = "UNTRUSTED_CLAIM_MATCH_ONLY"
    MISSING = "MISSING"
    CONFLICT = "CONFLICT"
    INDEPENDENT_EXECUTION_REQUIRED = "INDEPENDENT_EXECUTION_REQUIRED"


class IsolationPlanState(str, Enum):
    BLOCKED_UPSTREAM_OR_LOCAL_CONFLICT = "BLOCKED_UPSTREAM_OR_LOCAL_CONFLICT"
    BLOCKED_INCOMPLETE_ISOLATION_PACKAGE = "BLOCKED_INCOMPLETE_ISOLATION_PACKAGE"
    NONEXECUTING_ISOLATION_PLAN_CANDIDATE_ONLY = "NONEXECUTING_ISOLATION_PLAN_CANDIDATE_ONLY"


def _utc(value: datetime, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise FedRfc3161IsolationError(f"{name} must be timezone-aware datetime.")
    return value.astimezone(timezone.utc)


def _identifier(value: str, name: str) -> str:
    if type(value) is not str or re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,63}", value) is None:
        raise FedRfc3161IsolationError(f"Invalid {name} identifier.")
    return value


def _sha(value: str | None, name: str) -> str | None:
    if value is not None and (
        type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None
    ):
        raise FedRfc3161IsolationError(f"Invalid {name} SHA-256 reference.")
    return value


def _take(items, maximum: int, name: str) -> tuple:
    if items is None:
        raise TypeError(f"{name} cannot be None.")
    try:
        values = tuple(islice(items, maximum + 1))
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be iterable.") from exc
    if len(values) > maximum:
        raise FedRfc3161IsolationError(f"{name} count limit exceeded.")
    return values


def _positive_bounded(value: int, limit: int, name: str) -> int:
    if type(value) is not int or not 1 <= value <= limit:
        raise FedRfc3161IsolationError(f"Invalid claimed {name} resource bound.")
    return value


@dataclass(frozen=True)
class FedRfc3161IsolationReference:
    """Untrusted, data-only request to *design* a future isolated work order.

    No paths, commands, callbacks, raw bytes, execution handles, success flags,
    or runtime permissions may be supplied through this schema.
    """

    provider_id: str
    claim_id: str
    claimed_profile: str = ISOLATION_PROFILE
    claimed_adapter_receipt_sha256: str | None = None
    claimed_engine_prerequisites_sha256: str | None = None
    claimed_engine_build_sha256: str | None = None
    claimed_dependency_lock_sha256: str | None = None
    claimed_trust_store_sha256: str | None = None
    claimed_tsa_anchor_sha256: str | None = None
    claimed_isolation_runtime_sha256: str | None = None
    claimed_isolation_policy_sha256: str | None = None
    claimed_result_schema_sha256: str | None = None
    claimed_material_observed_at: datetime | None = None
    claimed_controls: tuple[str, ...] = ()
    claimed_max_token_bytes: int = MAX_TOKEN_INPUT_BYTES
    claimed_max_document_bytes: int = MAX_DOCUMENT_INPUT_BYTES
    claimed_cpu_budget_ms: int = MAX_CPU_BUDGET_MS
    claimed_memory_budget_bytes: int = MAX_MEMORY_BUDGET_BYTES

    def __post_init__(self) -> None:
        _validate_reference(self)


def _validate_reference(ref: FedRfc3161IsolationReference) -> None:
    # Revalidation is mandatory, including after object.__setattr__ mutation.
    if type(ref) is not FedRfc3161IsolationReference:
        raise FedRfc3161IsolationError("Exact isolation reference model required.")
    _identifier(ref.provider_id, "provider_id")
    _identifier(ref.claim_id, "claim_id")
    if type(ref.claimed_profile) is not str or ref.claimed_profile != ISOLATION_PROFILE:
        raise FedRfc3161IsolationError("Non-executing profile cannot be weakened or replaced.")
    for field in (
        "claimed_adapter_receipt_sha256", "claimed_engine_prerequisites_sha256",
        "claimed_engine_build_sha256", "claimed_dependency_lock_sha256",
        "claimed_trust_store_sha256", "claimed_tsa_anchor_sha256",
        "claimed_isolation_runtime_sha256", "claimed_isolation_policy_sha256",
        "claimed_result_schema_sha256",
    ):
        _sha(getattr(ref, field), field)
    if ref.claimed_material_observed_at is not None:
        _utc(ref.claimed_material_observed_at, "claimed_material_observed_at")
    cs = ref.claimed_controls
    if (type(cs) is not tuple or any(type(x) is not str for x in cs)
            or tuple(sorted(set(cs))) != cs
            or not set(cs).issubset(REQUIRED_ISOLATION_CONTROLS)):
        raise FedRfc3161IsolationError(
            "Controls must be an exact-name sorted unique subset of the deny profile."
        )
    for field, upper in (
        ("claimed_max_token_bytes", MAX_TOKEN_INPUT_BYTES),
        ("claimed_max_document_bytes", MAX_DOCUMENT_INPUT_BYTES),
        ("claimed_cpu_budget_ms", MAX_CPU_BUDGET_MS),
        ("claimed_memory_budget_bytes", MAX_MEMORY_BUDGET_BYTES),
    ):
        _positive_bounded(getattr(ref, field), upper, field)


@dataclass(frozen=True)
class FedIsolationCheck:
    code: str
    state: IsolationCheckState


@dataclass(frozen=True)
class FedIsolationTarget:
    provider_id: str
    claim_id: str
    token_sha256: str | None
    document_sha256: str | None
    claimed_runtime_sha256: str | None
    state: IsolationPlanState
    checks: tuple[FedIsolationCheck, ...]


@dataclass(frozen=True)
class FedRfc3161ExecutionIsolationReport:
    """Immutable diagnostic receipt. A review-plan candidate is never runnable."""

    as_of: datetime
    source_url: str
    canonical_adapter_sha256: str
    canonical_engine_prerequisites_sha256: str
    isolation_references_sha256: str
    target_count: int
    reference_count: int
    plan_candidate_count: int
    targets: tuple[FedIsolationTarget, ...]
    required_isolation_controls: tuple[str, ...]
    blockers: tuple[str, ...]
    diagnostic_manifest_sha256: str

    @property
    def trusted_engine_installed(self) -> bool:
        return False

    @property
    def independently_provisioned_trust_store(self) -> bool:
        return False

    @property
    def operating_system_sandbox_enforced(self) -> bool:
        return False

    @property
    def verifier_executed(self) -> bool:
        return False

    @property
    def rfc3161_signature_verified(self) -> bool:
        return False

    @property
    def tsa_certificate_chain_verified(self) -> bool:
        return False

    @property
    def signed_document_imprint_verified(self) -> bool:
        return False

    @property
    def signed_timestamp_time_verified(self) -> bool:
        return False

    @property
    def verified_output_admitted(self) -> bool:
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

    def require_trusted_verifier_execution(self) -> None:
        raise FedRfc3161IsolationUnavailable("No operating-system isolated verifier is executable.")

    def require_authenticated_verifier_output(self) -> None:
        raise FedRfc3161IsolationUnavailable("No authenticated verifier output exists.")

    def require_council_calendar_delivery(self) -> None:
        raise FedRfc3161IsolationUnavailable("Council Seat 25 admission remains blocked.")

    def require_historical_replay(self) -> None:
        raise FedRfc3161IsolationUnavailable("Historical source provenance is not attested.")

    def require_persistence(self) -> None:
        raise FedRfc3161IsolationUnavailable("Fed persistence remains prohibited.")

    def accept_external_verification_assertion(self, assertion: object) -> None:
        raise FedRfc3161IsolationUnavailable("Caller-controlled verifier output is forbidden.")


def _match(expected: str | None, claimed: str | None) -> IsolationCheckState:
    if expected is None or claimed is None:
        return IsolationCheckState.MISSING
    if expected != claimed:
        return IsolationCheckState.CONFLICT
    return IsolationCheckState.UNTRUSTED_CLAIM_MATCH_ONLY


def _present(*references: str | None) -> IsolationCheckState:
    if any(item is None for item in references):
        return IsolationCheckState.MISSING
    return IsolationCheckState.UNTRUSTED_CLAIM_MATCH_ONLY


def _utc_iso(value: datetime | None) -> str | None:
    return _utc(value, "claimed_material_observed_at").isoformat(timespec="microseconds") if value is not None else None


def _digest(value: object) -> str:
    return sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")).hexdigest()


def assess_fed_calendar_rfc3161_execution_isolation(
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
    *,
    as_of: datetime,
) -> FedRfc3161ExecutionIsolationReport:
    """Produce a non-executable work-order DESIGN after full upstream validation.

    Neither `engine_report` nor caller-owned `isolation_references` is a trust
    grant. Recomputing F4.5.15 also recomputes F4.5.14 from original inputs.
    """
    now = _utc(as_of, "as_of")
    if type(adapter) is not FedCryptoAdapterPlan:
        raise TypeError("Exact canonical F4.5.14 adapter type required.")
    if type(engine_report) is not FedRfc3161EnginePrerequisitesReport:
        raise TypeError("Exact canonical F4.5.15 engine report type required.")
    held_claims = _take(claims, MAX_EXTERNAL_EVIDENCE_CLAIMS, "claims")
    held_captures = _take(captures, MAX_EXTERNAL_EVIDENCE_CLAIMS, "captures")
    held_inputs = _take(verifier_inputs, MAX_VERIFIER_INPUTS, "verifier_inputs")
    held_policies = _take(policy_references, MAX_TRUST_POLICY_REFERENCES, "policy_references")
    held_materials = _take(
        engine_references, MAX_ENGINE_PROVISIONING_REFERENCES, "engine_references"
    )
    isolation = _take(isolation_references, MAX_ISOLATION_CLAIMS, "isolation_references")

    try:
        canonical = assess_fed_calendar_rfc3161_engine_prerequisites(
            acquisition, extraction, audit, gaps, held_claims, review,
            held_captures, intake, authenticity, held_inputs, prerequisites,
            held_policies, boundary, adapter, held_materials, as_of=now,
        )
    except (FedRfc3161EnginePrerequisiteError, TypeError, ValueError) as exc:
        raise FedRfc3161IsolationError(
            "Cannot independently reconstruct F4.5.15 and preceding evidence chain."
        ) from exc
    if canonical != engine_report or canonical.as_of != now:
        raise FedRfc3161IsolationError("Forged, stale or inconsistent F4.5.15 receipt.")
    if canonical.canonical_adapter_sha256 != adapter.diagnostic_manifest_sha256:
        raise FedRfc3161IsolationError("Inconsistent canonical F4.5.14 handoff digest.")

    target_map = {(x.provider_id, x.claim_id): x for x in canonical.targets}
    by_material = {}
    for m in held_materials:
        if type(m) is not FedRfc3161EngineMaterialReference:
            raise FedRfc3161IsolationError("Exact F4.5.15 reference model required.")
        key = (m.provider_id, m.claim_id)
        if key in by_material:
            raise FedRfc3161IsolationError("Duplicate F4.5.15 reference identity.")
        by_material[key] = m
    by_isolation = {}
    for ref in isolation:
        _validate_reference(ref)
        key = (ref.provider_id, ref.claim_id)
        if key not in target_map:
            raise FedRfc3161IsolationError("Isolation claim refers to unknown token work order.")
        if key in by_isolation:
            raise FedRfc3161IsolationError("Duplicate isolation reference identity.")
        by_isolation[key] = ref

    canon_isolation = sorted(by_isolation.values(), key=lambda r: (r.provider_id, r.claim_id))
    references_digest = _digest([{
        "id": (r.provider_id, r.claim_id),
        "profile": r.claimed_profile,
        "adapter": r.claimed_adapter_receipt_sha256,
        "engine_report": r.claimed_engine_prerequisites_sha256,
        "build": r.claimed_engine_build_sha256,
        "lock": r.claimed_dependency_lock_sha256,
        "trust_store": r.claimed_trust_store_sha256,
        "tsa_anchor": r.claimed_tsa_anchor_sha256,
        "runtime": r.claimed_isolation_runtime_sha256,
        "isolation_policy": r.claimed_isolation_policy_sha256,
        "result_schema": r.claimed_result_schema_sha256,
        "observed_at": _utc_iso(r.claimed_material_observed_at),
        "controls": r.claimed_controls,
        "token_limit": r.claimed_max_token_bytes,
        "document_limit": r.claimed_max_document_bytes,
        "cpu_limit": r.claimed_cpu_budget_ms,
        "memory_limit": r.claimed_memory_budget_bytes,
    } for r in canon_isolation])

    blockers = list(dict.fromkeys((*canonical.blockers, *_PERMANENT_BLOCKERS)))
    targets = []
    for t in canonical.targets:
        key = (t.provider_id, t.claim_id)
        r = by_isolation.get(key)
        material = by_material.get(key)
        checks = []

        def check(index: int, state: IsolationCheckState) -> None:
            checks.append(FedIsolationCheck(ISOLATION_CHECK_CODES[index], state))

        if t.state is FedEngineWorkState.REFERENCE_PACKAGE_CANDIDATE_ONLY:
            check(0, IsolationCheckState.UPSTREAM_REFERENCE_ONLY)
        elif t.state is FedEngineWorkState.BLOCKED_UPSTREAM_OR_LOCAL_CONFLICT:
            check(0, IsolationCheckState.CONFLICT)
        else:
            check(0, IsolationCheckState.MISSING)
        check(1, _match(canonical.canonical_adapter_sha256,
                        r.claimed_adapter_receipt_sha256 if r else None))
        check(2, _match(canonical.diagnostic_manifest_sha256,
                        r.claimed_engine_prerequisites_sha256 if r else None))
        check(3, _match(material.claimed_engine_build_sha256 if material else None,
                        r.claimed_engine_build_sha256 if r else None))
        check(4, _match(material.claimed_dependency_lock_sha256 if material else None,
                        r.claimed_dependency_lock_sha256 if r else None))
        trust_store = _match(material.claimed_trust_store_sha256 if material else None,
                             r.claimed_trust_store_sha256 if r else None)
        anchor = _match(material.claimed_tsa_trust_anchor_sha256 if material else None,
                        r.claimed_tsa_anchor_sha256 if r else None)
        check(5, (IsolationCheckState.CONFLICT if IsolationCheckState.CONFLICT in (trust_store, anchor)
                  else IsolationCheckState.MISSING if IsolationCheckState.MISSING in (trust_store, anchor)
                  else IsolationCheckState.UNTRUSTED_CLAIM_MATCH_ONLY))
        check(6, _present(r.claimed_isolation_runtime_sha256 if r else None,
                          r.claimed_isolation_policy_sha256 if r else None))
        check(7, _present(r.claimed_result_schema_sha256 if r else None))
        check(8, IsolationCheckState.UNTRUSTED_CLAIM_MATCH_ONLY if r is not None
              else IsolationCheckState.MISSING)
        check(9, IsolationCheckState.UNTRUSTED_CLAIM_MATCH_ONLY
              if r is not None and r.claimed_controls == REQUIRED_ISOLATION_CONTROLS
              else IsolationCheckState.MISSING)
        check(10, IsolationCheckState.UNTRUSTED_CLAIM_MATCH_ONLY
              if r is not None else IsolationCheckState.MISSING)
        check(11, (IsolationCheckState.MISSING
                   if r is None or r.claimed_material_observed_at is None
                   else IsolationCheckState.CONFLICT
                   if _utc(r.claimed_material_observed_at, "claimed_material_observed_at") > now
                   else IsolationCheckState.UNTRUSTED_CLAIM_MATCH_ONLY))
        for i in range(12, len(ISOLATION_CHECK_CODES)):
            check(i, IsolationCheckState.INDEPENDENT_EXECUTION_REQUIRED)
        states = tuple(c.state for c in checks[:12])
        if IsolationCheckState.CONFLICT in states:
            status = IsolationPlanState.BLOCKED_UPSTREAM_OR_LOCAL_CONFLICT
            blockers.append("FED_F4516_CONFLICTING_UPSTREAM_OR_ISOLATION_CLAIMS")
        elif IsolationCheckState.MISSING in states:
            status = IsolationPlanState.BLOCKED_INCOMPLETE_ISOLATION_PACKAGE
            blockers.append("FED_F4516_ISOLATION_PACKAGE_INCOMPLETE")
        else:
            status = IsolationPlanState.NONEXECUTING_ISOLATION_PLAN_CANDIDATE_ONLY
            blockers.append("FED_F4516_ISOLATION_PACKAGE_ONLY_A_LOCAL_PLAN")
        targets.append(FedIsolationTarget(
            provider_id=t.provider_id, claim_id=t.claim_id,
            token_sha256=t.token_body_sha256,
            document_sha256=t.document_body_sha256,
            claimed_runtime_sha256=r.claimed_isolation_runtime_sha256 if r else None,
            state=status, checks=tuple(checks),
        ))
    if not targets:
        blockers.append("FED_F4516_NO_TIMESTAMP_WORK_ITEMS")
    unique_blockers = tuple(dict.fromkeys(blockers))
    report_manifest = {
        "contract": "fed_calendar_rfc3161_execution_isolation_v1",
        "as_of_utc": now.isoformat(timespec="microseconds"),
        "source_url": canonical.source_url,
        "adapter_digest": canonical.canonical_adapter_sha256,
        "engine_prerequisites_digest": canonical.diagnostic_manifest_sha256,
        "isolation_references_digest": references_digest,
        "required_controls": REQUIRED_ISOLATION_CONTROLS,
        "targets": [{
            "id": (t.provider_id, t.claim_id),
            "token_sha256": t.token_sha256,
            "document_sha256": t.document_sha256,
            "claimed_runtime_sha256": t.claimed_runtime_sha256,
            "state": t.state.value,
            "checks": [(c.code, c.state.value) for c in t.checks],
        } for t in targets],
        "blockers": unique_blockers,
    }
    return FedRfc3161ExecutionIsolationReport(
        as_of=now, source_url=canonical.source_url,
        canonical_adapter_sha256=canonical.canonical_adapter_sha256,
        canonical_engine_prerequisites_sha256=canonical.diagnostic_manifest_sha256,
        isolation_references_sha256=references_digest,
        target_count=len(targets), reference_count=len(isolation),
        plan_candidate_count=sum(t.state is IsolationPlanState.NONEXECUTING_ISOLATION_PLAN_CANDIDATE_ONLY
                                 for t in targets),
        targets=tuple(targets), required_isolation_controls=REQUIRED_ISOLATION_CONTROLS,
        blockers=unique_blockers, diagnostic_manifest_sha256=_digest(report_manifest),
    )

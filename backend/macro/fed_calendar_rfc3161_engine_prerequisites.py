"""F4.5.15: sealed, OFFLINE RFC 3161 engine provisioning/prerequisite inventory.

This module does NOT install, locate, import, run, or approve a cryptographic
engine. It revalidates the canonical F4.5.6--14 Fed calendar evidence chain,
then records *untrusted claims* about future RFC 3161/CMS/ESSv2/PKIX verifier
capabilities and material. Even an entirely complete reference package is NOT
trusted verification, historical provenance, Council admission, replay, or
persistence authorization.

Reference standards: RFC 3161 (timestamp protocol), RFC 5816 (ESSCertIDv2),
RFC 5652 (CMS), RFC 5280 (PKIX). All signature, path, revocation and trusted
clock verification remains for a separately designed and reviewed engine.
No network, subprocess, dynamic verifier callback, filesystem, database,
broker, Council, or HMM dependencies or side effects.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from itertools import islice
import json
import re

from backend.macro.fed_calendar_crypto_adapter import (
    FedAdapterHandoffState,
    FedCryptoAdapterError,
    FedCryptoAdapterPlan,
    prepare_fed_calendar_crypto_adapter,
)
from backend.macro.fed_calendar_crypto_prerequisites import MAX_VERIFIER_INPUTS
from backend.macro.fed_calendar_independent_evidence import MAX_EXTERNAL_EVIDENCE_CLAIMS
from backend.macro.fed_calendar_verifier_boundary import (
    FedVerifierPolicyReference,
    MAX_TRUST_POLICY_REFERENCES,
)

MAX_ENGINE_PROVISIONING_REFERENCES = MAX_VERIFIER_INPUTS

# A restrictive *review profile name*, NOT evidence that these capabilities
# are installed, successfully exercised, or securely configured.
REQUIRED_ENGINE_PROFILE = "RFC3161_SHA256_ESSV2_PKIX_OFFLINE_V1"

REQUIRED_ENGINE_CAPABILITIES = tuple(sorted((
    "COMPLETE_BOUNDED_DER_CMS_TSTINFO_DECODER",
    "VERIFY_CMS_SINGLE_SIGNER_SIGNATURE_AND_SIGNED_ATTRIBUTES",
    "VERIFY_ESS_CERTID_V2_TSA_SIGNER_CERTIFICATE_BINDING",
    "VERIFY_RFC5280_CERTIFICATE_PATH_AT_GEN_TIME",
    "VERIFY_EXCLUSIVE_CRITICAL_TIMESTAMPING_EKU",
    "VERIFY_REVOCATION_AT_GEN_TIME_FROM_APPROVED_EVIDENCE",
    "VERIFY_SIGNED_SHA256_IMPRINT_AGAINST_DISTINCT_DOCUMENT",
    "VERIFY_SIGNED_TSA_POLICY_AGAINST_OPERATOR_ALLOWLIST",
    "VERIFY_REQUEST_NONCE_OR_INDEPENDENT_TRUSTED_TIME",
    "VERIFY_SIGNED_GEN_TIME_AND_HISTORICAL_AS_OF_VISIBILITY",
)))

ENGINE_PREREQUISITE_CHECKS = (
    "UPSTREAM_HANDOFF_STRUCTURE_ONLY",
    "CLAIMED_TOKEN_DIGEST_MATCHES_LOCAL_HANDOFF",
    "CLAIMED_DOCUMENT_DIGEST_MATCHES_LOCAL_HANDOFF",
    "CLAIMED_POLICY_REVISION_MATCHES_LOCAL_HANDOFF",
    "VERIFIER_BUILD_AND_DEPENDENCY_LOCK_REFERENCED",
    "DER_CMS_SIGNED_ATTRIBUTES_AND_ESSV2_CAPABILITIES_CLAIMED",
    "PKIX_CERT_PATH_AND_EXCLUSIVE_TSA_EKU_CAPABILITIES_CLAIMED",
    "SIGNED_IMPRINT_POLICY_NONCE_AND_TIME_CAPABILITIES_CLAIMED",
    "OPERATOR_TRUST_STORE_AND_ANCHOR_REFERENCES_RECONCILED",
    "TSA_SIGNER_CERTIFICATE_CHAIN_REFERENCE_CLAIMED",
    "REVOCATION_REFERENCE_RECONCILED_AND_CAPABILITY_CLAIMED",
    "REQUEST_NONCE_OR_INDEPENDENT_TIME_REFERENCE_RECONCILED",
    "PROVISIONING_REFERENCE_AS_OF_NOT_CONTRADICTED",
    "CMS_ESSV2_PKIX_RFC3161_CRYPTOGRAPHY_NOT_EXECUTED",
    "INDEPENDENT_HISTORICAL_SOURCE_ORIGIN_NOT_ATTESTED",
    "COUNCIL_REPLAY_STORAGE_POLICY_ADMISSION_NOT_GRANTED",
)

_ALWAYS_BLOCKERS = (
    "FED_RFC3161_ENGINE_REFERENCE_NOT_A_TRUSTED_INSTALLATION",
    "FED_RFC3161_ENGINE_CMS_ESSV2_SIGNATURE_NOT_VERIFIED",
    "FED_RFC3161_ENGINE_TSA_PKIX_EKU_REVOCATION_NOT_VERIFIED",
    "FED_RFC3161_ENGINE_SIGNED_IMPRINT_NONCE_AND_TIME_NOT_VERIFIED",
    "FED_RFC3161_ENGINE_HISTORICAL_SOURCE_CUSTODY_UNVERIFIED",
    "FED_RFC3161_ENGINE_COUNCIL_REPLAY_STORAGE_DENIED",
)


class FedRfc3161EnginePrerequisiteError(ValueError):
    """Malformed reference, local inconsistency or forged upstream chain."""


class FedRfc3161EngineUnavailable(RuntimeError):
    """This milestone never executes or authorizes a trusted verifier."""


class FedEngineReferenceCheckState(str, Enum):
    LOCAL_CANDIDATE_ONLY = "LOCAL_CANDIDATE_ONLY"
    UNTRUSTED_REFERENCE_ONLY = "UNTRUSTED_REFERENCE_ONLY"
    MISSING = "MISSING"
    CONFLICT = "CONFLICT"
    INDEPENDENT_EXECUTION_REQUIRED = "INDEPENDENT_EXECUTION_REQUIRED"


class FedEngineWorkState(str, Enum):
    BLOCKED_UPSTREAM_OR_LOCAL_CONFLICT = "BLOCKED_UPSTREAM_OR_LOCAL_CONFLICT"
    BLOCKED_INCOMPLETE_REFERENCE_PACKAGE = "BLOCKED_INCOMPLETE_REFERENCE_PACKAGE"
    REFERENCE_PACKAGE_CANDIDATE_ONLY = "REFERENCE_PACKAGE_CANDIDATE_ONLY"


def _utc(value: datetime, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise FedRfc3161EnginePrerequisiteError(f"{name} must be a timezone-aware datetime.")
    return value.astimezone(timezone.utc)


def _identifier(value: str, name: str) -> str:
    if type(value) is not str or re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,63}", value) is None:
        raise FedRfc3161EnginePrerequisiteError(f"Invalid {name} identifier.")
    return value


def _optional_sha(value: str | None, name: str) -> str | None:
    if value is not None and (
        type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None
    ):
        raise FedRfc3161EnginePrerequisiteError(f"Invalid {name} SHA-256 reference.")
    return value


def _take(items, maximum: int, name: str) -> tuple:
    if items is None:
        raise TypeError(f"{name} cannot be None.")
    try:
        values = tuple(islice(items, maximum + 1))
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be iterable.") from exc
    if len(values) > maximum:
        raise FedRfc3161EnginePrerequisiteError(f"{name} count limit exceeded.")
    return values


@dataclass(frozen=True)
class FedRfc3161EngineMaterialReference:
    """Untrusted descriptions of possible future verifier material.

    No input here can create a cryptographic verdict, provision a trust store,
    choose a runtime library, override a security requirement, or cause IO.
    SHA-256 strings label artifacts; they do not authenticate their origin.
    """

    provider_id: str
    claim_id: str
    claimed_profile: str = REQUIRED_ENGINE_PROFILE
    claimed_token_sha256: str | None = None
    claimed_document_sha256: str | None = None
    claimed_policy_revision_sha256: str | None = None
    claimed_engine_build_sha256: str | None = None
    claimed_dependency_lock_sha256: str | None = None
    claimed_trust_store_sha256: str | None = None
    claimed_tsa_trust_anchor_sha256: str | None = None
    claimed_tsa_signer_chain_sha256: str | None = None
    claimed_revocation_evidence_sha256: str | None = None
    claimed_request_der_sha256: str | None = None
    claimed_independent_time_evidence_sha256: str | None = None
    claimed_material_observed_at: datetime | None = None
    claimed_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_reference(self)


def _validate_reference(ref: FedRfc3161EngineMaterialReference) -> None:
    # A malicious caller can mutate frozen objects via object.__setattr__.
    if type(ref) is not FedRfc3161EngineMaterialReference:
        raise FedRfc3161EnginePrerequisiteError("Exact engine reference type required.")
    _identifier(ref.provider_id, "provider_id")
    _identifier(ref.claim_id, "claim_id")
    if ref.claimed_profile != REQUIRED_ENGINE_PROFILE or type(ref.claimed_profile) is not str:
        raise FedRfc3161EnginePrerequisiteError("Engine profile cannot be weakened or replaced.")
    for name in (
        "claimed_token_sha256", "claimed_document_sha256", "claimed_policy_revision_sha256",
        "claimed_engine_build_sha256", "claimed_dependency_lock_sha256",
        "claimed_trust_store_sha256", "claimed_tsa_trust_anchor_sha256",
        "claimed_tsa_signer_chain_sha256", "claimed_revocation_evidence_sha256",
        "claimed_request_der_sha256", "claimed_independent_time_evidence_sha256",
    ):
        _optional_sha(getattr(ref, name), name)
    if ref.claimed_material_observed_at is not None:
        _utc(ref.claimed_material_observed_at, "claimed_material_observed_at")
    caps = ref.claimed_capabilities
    if (type(caps) is not tuple or any(type(x) is not str for x in caps)
            or tuple(sorted(set(caps))) != caps
            or not set(caps).issubset(REQUIRED_ENGINE_CAPABILITIES)):
        raise FedRfc3161EnginePrerequisiteError(
            "Capabilities must be a sorted, unique subset of strict profile requirements."
        )


@dataclass(frozen=True)
class FedEnginePrerequisiteCheck:
    code: str
    state: FedEngineReferenceCheckState


@dataclass(frozen=True)
class FedRfc3161EngineTarget:
    provider_id: str
    claim_id: str
    token_body_sha256: str | None
    document_body_sha256: str | None
    claimed_engine_build_sha256: str | None
    state: FedEngineWorkState
    checks: tuple[FedEnginePrerequisiteCheck, ...]


@dataclass(frozen=True)
class FedRfc3161EnginePrerequisitesReport:
    """Immutable, non-executable evidence inventory and diagnostic receipt."""

    as_of: datetime
    source_url: str
    canonical_adapter_sha256: str
    engine_reference_manifest_sha256: str
    target_count: int
    reference_count: int
    review_package_candidate_count: int
    targets: tuple[FedRfc3161EngineTarget, ...]
    required_engine_capabilities: tuple[str, ...]
    blockers: tuple[str, ...]
    diagnostic_manifest_sha256: str

    @property
    def trusted_engine_installed(self) -> bool:
        return False

    @property
    def cryptographic_verifier_executed(self) -> bool:
        return False

    @property
    def rfc3161_signature_verified(self) -> bool:
        return False

    @property
    def tsa_certificate_chain_verified(self) -> bool:
        return False

    @property
    def document_imprint_verified(self) -> bool:
        return False

    @property
    def trusted_timestamp_verified(self) -> bool:
        return False

    @property
    def historical_capture_time_attested(self) -> bool:
        return False

    @property
    def historical_source_version_attested(self) -> bool:
        return False

    @property
    def council_calendar_admission_authorized(self) -> bool:
        return False

    @property
    def historical_replay_authorized(self) -> bool:
        return False

    @property
    def persistence_authorized(self) -> bool:
        return False

    def require_trusted_verifier_execution(self) -> None:
        raise FedRfc3161EngineUnavailable("No independently installed RFC 3161 verifier exists here.")

    def require_council_calendar_delivery(self) -> None:
        raise FedRfc3161EngineUnavailable("Engine prerequisite references never authorize Council delivery.")

    def require_historical_replay(self) -> None:
        raise FedRfc3161EngineUnavailable("Historical replay requires independently attested source history.")

    def require_persistence(self) -> None:
        raise FedRfc3161EngineUnavailable("Engine prerequisite references never authorize storage.")

    def accept_external_verification_assertion(self, assertion: object) -> None:
        raise FedRfc3161EngineUnavailable("Caller-supplied success assertions are forbidden.")


def _state_equal(expected: str | None, provided: str | None) -> FedEngineReferenceCheckState:
    if expected is None or provided is None:
        return FedEngineReferenceCheckState.MISSING
    if expected != provided:
        return FedEngineReferenceCheckState.CONFLICT
    return FedEngineReferenceCheckState.UNTRUSTED_REFERENCE_ONLY


def _state_present(*values: str | None) -> FedEngineReferenceCheckState:
    return (FedEngineReferenceCheckState.UNTRUSTED_REFERENCE_ONLY
            if all(v is not None for v in values)
            else FedEngineReferenceCheckState.MISSING)


def _state_capabilities(ref, names: tuple[str, ...]) -> FedEngineReferenceCheckState:
    if ref is None or not set(names).issubset(ref.claimed_capabilities):
        return FedEngineReferenceCheckState.MISSING
    return FedEngineReferenceCheckState.UNTRUSTED_REFERENCE_ONLY


def assess_fed_calendar_rfc3161_engine_prerequisites(
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
    *,
    as_of: datetime,
) -> FedRfc3161EnginePrerequisitesReport:
    """Revalidate upstream and inventory strictly untrusted future-engine claims.

    `engine_references` is NOT an engine installation, trust store, or crypto
    verification callback. Even all-present claims remain untrusted.
    """
    now = _utc(as_of, "as_of")
    if type(adapter) is not FedCryptoAdapterPlan:
        raise TypeError("Exact FedCryptoAdapterPlan required.")
    refs = _take(claims, MAX_EXTERNAL_EVIDENCE_CLAIMS, "claims")
    held = _take(captures, MAX_EXTERNAL_EVIDENCE_CLAIMS, "captures")
    inputs = _take(verifier_inputs, MAX_VERIFIER_INPUTS, "verifier_inputs")
    policies = _take(policy_references, MAX_TRUST_POLICY_REFERENCES, "policy_references")
    engine_materials = _take(
        engine_references, MAX_ENGINE_PROVISIONING_REFERENCES, "engine_references"
    )
    try:
        canonical = prepare_fed_calendar_crypto_adapter(
            acquisition, extraction, audit, gaps, refs, review, held, intake,
            authenticity, inputs, prerequisites, policies, boundary, as_of=now,
        )
    except (FedCryptoAdapterError, TypeError, ValueError) as exc:
        raise FedRfc3161EnginePrerequisiteError(
            "Cannot revalidate canonical F4.5.14 adapter and upstream evidence chain."
        ) from exc
    if canonical != adapter:
        raise FedRfc3161EnginePrerequisiteError("Adapter receipt differs from canonical evidence chain.")
    if canonical.as_of != now:
        raise FedRfc3161EnginePrerequisiteError("Adapter as_of is inconsistent with current request.")

    target_map = {(t.provider_id, t.claim_id): t for t in canonical.targets}
    input_map = {(i.provider_id, i.claim_id): i for i in inputs}
    policy_map = {}
    for p in policies:
        if type(p) is not FedVerifierPolicyReference:
            raise FedRfc3161EnginePrerequisiteError("Exact policy reference type required.")
        policy_map[(p.provider_id, p.claim_id)] = p
    by_material = {}
    for material in engine_materials:
        _validate_reference(material)
        key = (material.provider_id, material.claim_id)
        if key not in target_map:
            raise FedRfc3161EnginePrerequisiteError("Engine material references an unknown timestamp work order.")
        if key in by_material:
            raise FedRfc3161EnginePrerequisiteError("Duplicate engine material reference identity.")
        by_material[key] = material

    ordered_materials = sorted(by_material.values(), key=lambda x: (x.provider_id, x.claim_id))
    material_manifest = [{
        "id": (m.provider_id, m.claim_id),
        "claimed_profile": m.claimed_profile,
        "token_sha256": m.claimed_token_sha256,
        "document_sha256": m.claimed_document_sha256,
        "policy_sha256": m.claimed_policy_revision_sha256,
        "engine_build_sha256": m.claimed_engine_build_sha256,
        "dependency_lock_sha256": m.claimed_dependency_lock_sha256,
        "trust_store_sha256": m.claimed_trust_store_sha256,
        "trust_anchor_sha256": m.claimed_tsa_trust_anchor_sha256,
        "signer_chain_sha256": m.claimed_tsa_signer_chain_sha256,
        "revocation_sha256": m.claimed_revocation_evidence_sha256,
        "request_sha256": m.claimed_request_der_sha256,
        "time_reference_sha256": m.claimed_independent_time_evidence_sha256,
        "observed_at": (_utc(m.claimed_material_observed_at, "claimed_material_observed_at")
                         .isoformat(timespec="microseconds")
                         if m.claimed_material_observed_at is not None else None),
        "claimed_capabilities": m.claimed_capabilities,
    } for m in ordered_materials]
    reference_digest = sha256(json.dumps(
        material_manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")).hexdigest()

    blockers = list(dict.fromkeys((*canonical.blockers, *_ALWAYS_BLOCKERS)))
    result_targets = []
    for target in canonical.targets:
        key = (target.provider_id, target.claim_id)
        m = by_material.get(key)
        upstream_input = input_map.get(key)
        upstream_policy = policy_map.get(key)
        checks = []

        def append(index: int, state: FedEngineReferenceCheckState) -> None:
            checks.append(FedEnginePrerequisiteCheck(ENGINE_PREREQUISITE_CHECKS[index], state))

        structural = target.state is FedAdapterHandoffState.STRUCTURAL_CANDIDATE_ONLY
        append(0, (FedEngineReferenceCheckState.LOCAL_CANDIDATE_ONLY if structural
                   else FedEngineReferenceCheckState.CONFLICT
                   if target.state is FedAdapterHandoffState.BLOCKED_CONFLICT
                   else FedEngineReferenceCheckState.MISSING))
        append(1, _state_equal(target.token_body_sha256, m.claimed_token_sha256 if m else None))
        append(2, _state_equal(target.document_body_sha256, m.claimed_document_sha256 if m else None))
        append(3, _state_equal(target.policy_revision_reference_sha256,
                               m.claimed_policy_revision_sha256 if m else None))
        append(4, _state_present(m.claimed_engine_build_sha256 if m else None,
                                 m.claimed_dependency_lock_sha256 if m else None))
        append(5, _state_capabilities(m, (
            "COMPLETE_BOUNDED_DER_CMS_TSTINFO_DECODER",
            "VERIFY_CMS_SINGLE_SIGNER_SIGNATURE_AND_SIGNED_ATTRIBUTES",
            "VERIFY_ESS_CERTID_V2_TSA_SIGNER_CERTIFICATE_BINDING",
        )))
        append(6, _state_capabilities(m, (
            "VERIFY_RFC5280_CERTIFICATE_PATH_AT_GEN_TIME",
            "VERIFY_EXCLUSIVE_CRITICAL_TIMESTAMPING_EKU",
        )))
        append(7, _state_capabilities(m, (
            "VERIFY_SIGNED_SHA256_IMPRINT_AGAINST_DISTINCT_DOCUMENT",
            "VERIFY_SIGNED_TSA_POLICY_AGAINST_OPERATOR_ALLOWLIST",
            "VERIFY_REQUEST_NONCE_OR_INDEPENDENT_TRUSTED_TIME",
            "VERIFY_SIGNED_GEN_TIME_AND_HISTORICAL_AS_OF_VISIBILITY",
        )))
        trusted_claimed_anchor = (upstream_policy.pinned_tsa_anchor_certificate_sha256
                                  if upstream_policy is not None else None)
        local_anchor = m.claimed_tsa_trust_anchor_sha256 if m else None
        trust_state = _state_present(m.claimed_trust_store_sha256 if m else None, local_anchor)
        if trust_state is not FedEngineReferenceCheckState.MISSING:
            trust_state = _state_equal(trusted_claimed_anchor, local_anchor)
            if (trust_state is FedEngineReferenceCheckState.UNTRUSTED_REFERENCE_ONLY
                    and upstream_input is not None):
                trust_state = _state_equal(upstream_input.claimed_tsa_trust_anchor_sha256,
                                           local_anchor)
        append(8, trust_state)
        append(9, _state_present(m.claimed_tsa_signer_chain_sha256 if m else None))
        rev_state = _state_present(m.claimed_revocation_evidence_sha256 if m else None)
        if rev_state is not FedEngineReferenceCheckState.MISSING:
            rev_state = _state_equal(
                upstream_input.claimed_revocation_evidence_sha256 if upstream_input else None,
                m.claimed_revocation_evidence_sha256,
            )
            if (rev_state is FedEngineReferenceCheckState.UNTRUSTED_REFERENCE_ONLY
                    and "VERIFY_REVOCATION_AT_GEN_TIME_FROM_APPROVED_EVIDENCE"
                    not in m.claimed_capabilities):
                rev_state = FedEngineReferenceCheckState.MISSING
        append(10, rev_state)
        request_state = FedEngineReferenceCheckState.MISSING
        if m is not None:
            if m.claimed_request_der_sha256 is not None:
                upstream_request = (upstream_input.claimed_request_der_sha256
                                    if upstream_input is not None else None)
                request_state = _state_equal(upstream_request, m.claimed_request_der_sha256)
                if (request_state is FedEngineReferenceCheckState.UNTRUSTED_REFERENCE_ONLY
                        and (upstream_input is None or upstream_input.claimed_request_nonce is None)):
                    request_state = FedEngineReferenceCheckState.MISSING
            elif m.claimed_independent_time_evidence_sha256 is not None:
                if (upstream_input is not None
                        and upstream_input.claimed_external_reference_at is not None):
                    request_state = FedEngineReferenceCheckState.UNTRUSTED_REFERENCE_ONLY
        append(11, request_state)
        observed = m.claimed_material_observed_at if m else None
        append(12, (FedEngineReferenceCheckState.MISSING if observed is None
                    else FedEngineReferenceCheckState.CONFLICT if _utc(observed, "claimed_material_observed_at") > now
                    else FedEngineReferenceCheckState.UNTRUSTED_REFERENCE_ONLY))
        for index in range(13, len(ENGINE_PREREQUISITE_CHECKS)):
            append(index, FedEngineReferenceCheckState.INDEPENDENT_EXECUTION_REQUIRED)

        local = tuple(c.state for c in checks[:13])
        if FedEngineReferenceCheckState.CONFLICT in local:
            state = FedEngineWorkState.BLOCKED_UPSTREAM_OR_LOCAL_CONFLICT
            blockers.append("FED_RFC3161_ENGINE_LOCAL_REFERENCE_CONFLICT")
        elif FedEngineReferenceCheckState.MISSING in local:
            state = FedEngineWorkState.BLOCKED_INCOMPLETE_REFERENCE_PACKAGE
            blockers.append("FED_RFC3161_ENGINE_REFERENCE_PACKAGE_INCOMPLETE")
        else:
            state = FedEngineWorkState.REFERENCE_PACKAGE_CANDIDATE_ONLY
            blockers.append("FED_RFC3161_ENGINE_REFERENCE_PACKAGE_NOT_CRYPTOGRAPHIC_PROOF")
        result_targets.append(FedRfc3161EngineTarget(
            provider_id=target.provider_id, claim_id=target.claim_id,
            token_body_sha256=target.token_body_sha256,
            document_body_sha256=target.document_body_sha256,
            claimed_engine_build_sha256=m.claimed_engine_build_sha256 if m else None,
            state=state, checks=tuple(checks),
        ))
    if not result_targets:
        blockers.append("FED_RFC3161_ENGINE_NO_TIMESTAMP_WORK_ITEMS")
    unique_blockers = tuple(dict.fromkeys(blockers))
    manifest = {
        "contract": "fed_calendar_rfc3161_engine_prerequisites_v1",
        "as_of_utc": now.isoformat(timespec="microseconds"),
        "source_url": canonical.source_url,
        "canonical_adapter_sha256": canonical.diagnostic_manifest_sha256,
        "reference_manifest_sha256": reference_digest,
        "requirements": REQUIRED_ENGINE_CAPABILITIES,
        "targets": [{
            "id": (t.provider_id, t.claim_id),
            "token_sha256": t.token_body_sha256,
            "document_sha256": t.document_body_sha256,
            "engine_build_sha256": t.claimed_engine_build_sha256,
            "state": t.state.value,
            "checks": [(c.code, c.state.value) for c in t.checks],
        } for t in result_targets],
        "blockers": unique_blockers,
    }
    receipt_digest = sha256(json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")).hexdigest()
    return FedRfc3161EnginePrerequisitesReport(
        as_of=now, source_url=canonical.source_url,
        canonical_adapter_sha256=canonical.diagnostic_manifest_sha256,
        engine_reference_manifest_sha256=reference_digest,
        target_count=len(result_targets), reference_count=len(engine_materials),
        review_package_candidate_count=sum(
            x.state is FedEngineWorkState.REFERENCE_PACKAGE_CANDIDATE_ONLY
            for x in result_targets
        ),
        targets=tuple(result_targets), required_engine_capabilities=REQUIRED_ENGINE_CAPABILITIES,
        blockers=unique_blockers, diagnostic_manifest_sha256=receipt_digest,
    )

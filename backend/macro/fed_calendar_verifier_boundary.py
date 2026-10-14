"""F4.5.13: Sealed RFC 3161 verification *execution boundary* for Fed calendar.

Recomputes the F4.5.6--12 provenance/intake/authenticity/prerequisite chain,
then builds immutable per-token *work orders* against operator-supplied policy
references. References are not a provisioned/verifiably approved trust store.
No caller-controlled result, callback, or claim can act as a trusted verifier.

This stage does NOT parse ASN.1/CMS, execute a verifier, validate PKIX chains,
fetch archives, attest source history, enable Council, replay or persistence.
A future separately reviewed verifier implementation must use a trusted store
outside the evidence-claim path, validate cryptography itself, and receive a
separate authorization review. No external effects occur here.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from itertools import islice
import json
import re

from backend.macro.fed_calendar_artifact_intake import (
    FedCalendarArtifactIntakeReport,
    FedCalendarExternalArtifactCapture,
)
from backend.macro.fed_calendar_authenticity_requirements import (
    FedCalendarAuthenticityRequirementsReport,
    FedVerificationCheckState,
    COMMON_CHECKS,
)
from backend.macro.fed_calendar_client import FedCalendarAcquisitionRecord
from backend.macro.fed_calendar_crypto_prerequisites import (
    FedCalendarCryptoPrerequisitesReport,
    FedCryptoCheckState,
    FedCryptoPrerequisitesError,
    FedRfc3161VerifierInputClaim,
    MAX_VERIFIER_INPUTS,
    SHA256_OID,
    TIMESTAMPING_EKU_OID,
    TSTINFO_CONTENT_TYPE_OID,
    RFC3161_PREREQUISITE_CHECKS,
    assess_fed_calendar_crypto_prerequisites,
)
from backend.macro.fed_calendar_evidence_audit import FedCalendarEvidenceAudit
from backend.macro.fed_calendar_extractor import FedCalendarScheduleExtraction
from backend.macro.fed_calendar_independent_evidence import (
    FedCalendarExternalEvidenceClaim,
    FedCalendarExternalEvidenceReview,
    FedClaimedTimeState,
    MAX_EXTERNAL_EVIDENCE_CLAIMS,
)
from backend.macro.fed_calendar_provenance_gaps import FedCalendarProvenanceGapReport


MAX_TRUST_POLICY_REFERENCES = MAX_VERIFIER_INPUTS

# These OIDs name *structures or algorithms*, not independent TSA policies.
NON_POLICY_OIDS = frozenset((
    SHA256_OID,
    TIMESTAMPING_EKU_OID,
    TSTINFO_CONTENT_TYPE_OID,
    "1.2.840.113549.1.7.2",  # CMS signedData
))

STRICT_VERIFIER_CONTROLS = (
    "PIN_SHA256_MESSAGE_IMPRINT",
    "REQUIRE_CRITICAL_EXCLUSIVE_TIMESTAMPING_EKU",
    "REQUIRE_CERTIFICATE_PATH_AND_REVOCATION_VALIDATION",
    "REQUIRE_REQUEST_NONCE_OR_INDEPENDENT_TRUSTED_TIME",
    "REQUIRE_SIGNED_POLICY_AND_TSA_CERT_BINDING",
    "REQUIRE_VERIFIED_GEN_TIME_AND_REPLAY_AS_OF",
    "REQUIRE_INDEPENDENT_ARCHIVE_CUSTODY_REVIEW",
    "REQUIRE_SEPARATE_COUNCIL_REPLAY_STORAGE_ADMISSION",
)

VERIFIER_BOUNDARY_CHECKS = (
    "TOKEN_BYTES_LOCALLY_RECONCILED",
    "SEPARATE_DOCUMENT_BYTES_LOCALLY_RECONCILED",
    "OPERATOR_TRUST_POLICY_REFERENCE_PRESENT",
    "CLAIMED_TSA_ANCHOR_MATCHES_POLICY_REFERENCE",
    "CLAIMED_TSA_POLICY_ALLOWED_BY_POLICY_REFERENCE",
    "REQUEST_NONCE_OR_TRUSTED_TIME_REFERENCE_CLAIMED",
    "REVOCATION_EVIDENCE_REFERENCE_CLAIMED",
    "PREREQUISITE_HISTORICAL_ASOF_NOT_CONTRADICTED",
    "RFC3161_CMS_ESS_SIGNER_SIGNATURE_NOT_EXECUTED",
    "RFC5280_CERT_CHAIN_CRITICAL_EKU_REVOCATION_NOT_EXECUTED",
    "RFC3161_IMPRINT_POLICY_NONCE_GENTIME_NOT_EXECUTED",
    "INDEPENDENT_ARCHIVE_CUSTODY_NOT_VERIFIED",
    "COUNCIL_REPLAY_AND_STORAGE_ADMISSION_NOT_AUTHORIZED",
)

_ALWAYS_BLOCKERS = (
    "FED_VERIFIER_EXECUTION_ENGINE_NOT_INSTALLED",
    "FED_VERIFIER_TRUST_ANCHORS_NOT_INDEPENDENTLY_PROVISIONED",
    "FED_VERIFIER_CMS_PKIX_RFC3161_CRYPTOGRAPHY_NOT_EXECUTED",
    "FED_VERIFIER_HISTORICAL_ARCHIVE_CUSTODY_NOT_ATTESTED",
    "FED_VERIFIER_COUNCIL_REPLAY_STORAGE_NOT_AUTHORIZED",
)


class FedVerifierBoundaryError(ValueError):
    """Malformed policy/input or a forged upstream verification report."""


class FedTrustedVerificationUnavailable(RuntimeError):
    """A separately reviewed verifier/trust store has not been installed."""


class FedBoundaryCheckState(str, Enum):
    LOCAL_CANDIDATE_ONLY = "LOCAL_CANDIDATE_ONLY"
    UNTRUSTED_REFERENCE_ONLY = "UNTRUSTED_REFERENCE_ONLY"
    MISSING = "MISSING"
    CONFLICT = "CONFLICT"
    TRUSTED_EXECUTION_REQUIRED = "TRUSTED_EXECUTION_REQUIRED"


class FedWorkOrderState(str, Enum):
    BLOCKED_CONFLICT = "BLOCKED_CONFLICT"
    BLOCKED_INCOMPLETE = "BLOCKED_INCOMPLETE"
    INPUT_CANDIDATE_ONLY = "INPUT_CANDIDATE_ONLY"


def _utc(value: datetime, field: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise FedVerifierBoundaryError(f"{field} must be a timezone-aware datetime.")
    return value.astimezone(timezone.utc)


def _id(value: str, name: str) -> str:
    if type(value) is not str or re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,63}", value) is None:
        raise FedVerifierBoundaryError(f"Invalid {name} identity.")
    return value


def _sha(value: str, name: str) -> str:
    if type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise FedVerifierBoundaryError(f"Invalid {name} SHA-256 reference.")
    return value


def _oid(value: str) -> str:
    if type(value) is not str or len(value) > 128:
        raise FedVerifierBoundaryError("Invalid permitted TSA policy OID.")
    arcs = value.split(".")
    if not 3 <= len(arcs) <= 32 or any(
        re.fullmatch(r"0|[1-9][0-9]{0,9}", arc) is None for arc in arcs
    ):
        raise FedVerifierBoundaryError("Invalid permitted TSA policy OID.")
    numbers = [int(a) for a in arcs]
    if numbers[0] > 2 or (numbers[0] < 2 and numbers[1] > 39):
        raise FedVerifierBoundaryError("Invalid permitted TSA policy OID.")
    if value in NON_POLICY_OIDS:
        raise FedVerifierBoundaryError("Algorithm/content/EKU OID is not a TSA policy.")
    return value


@dataclass(frozen=True)
class FedVerifierPolicyReference:
    """Untrusted *description* of intended operator trust configuration.

    These hashes/OIDs are references only. This object does not contain a
    trust store or prove operator approval, a certificate, or a signature.
    All security controls must remain mandatory in the proposed policy.
    """

    provider_id: str
    claim_id: str
    policy_revision_sha256: str
    pinned_tsa_anchor_certificate_sha256: str
    allowed_tsa_policy_oids: tuple[str, ...]
    require_sha256_imprint: bool = True
    require_critical_tsa_eku: bool = True
    require_certificate_revocation: bool = True
    require_nonce_or_trusted_time: bool = True

    def __post_init__(self) -> None:
        _validate_policy(self)


def _validate_policy(value: FedVerifierPolicyReference) -> None:
    if type(value) is not FedVerifierPolicyReference:
        raise FedVerifierBoundaryError("Exact verifier policy reference type required.")
    _id(value.provider_id, "provider_id")
    _id(value.claim_id, "claim_id")
    _sha(value.policy_revision_sha256, "policy revision")
    _sha(value.pinned_tsa_anchor_certificate_sha256, "pinned TSA anchor")
    policies = value.allowed_tsa_policy_oids
    if (type(policies) is not tuple or not 1 <= len(policies) <= 16
            or not all(type(item) is str for item in policies)):
        raise FedVerifierBoundaryError("Allowed policy OIDs require a bounded tuple.")
    for policy in policies:
        _oid(policy)
    if tuple(sorted(set(policies))) != policies:
        raise FedVerifierBoundaryError("Allowed TSA policy OIDs must be sorted and unique.")
    for name in (
        "require_sha256_imprint", "require_critical_tsa_eku",
        "require_certificate_revocation", "require_nonce_or_trusted_time",
    ):
        if getattr(value, name) is not True:
            raise FedVerifierBoundaryError(f"Policy cannot disable {name}.")


@dataclass(frozen=True)
class FedBoundaryRequirement:
    code: str
    state: FedBoundaryCheckState


@dataclass(frozen=True)
class FedTrustedVerifierWorkOrder:
    provider_id: str
    claim_id: str
    token_body_sha256: str | None
    document_body_sha256: str | None
    candidate_policy_revision_sha256: str | None
    state: FedWorkOrderState
    checks: tuple[FedBoundaryRequirement, ...]


@dataclass(frozen=True)
class FedTrustedVerifierBoundaryReport:
    """Sealed work-order receipt. It is not a cryptographic verdict."""

    as_of: datetime
    source_url: str
    canonical_crypto_prerequisites_sha256: str
    policy_reference_manifest_sha256: str
    token_claim_count: int
    policy_reference_count: int
    work_orders: tuple[FedTrustedVerifierWorkOrder, ...]
    strict_verifier_controls: tuple[str, ...]
    blockers: tuple[str, ...]
    diagnostic_manifest_sha256: str

    @property
    def trusted_verification_engine_installed(self) -> bool:
        return False

    @property
    def rfc3161_signature_verified(self) -> bool:
        return False

    @property
    def tsa_certificate_chain_verified(self) -> bool:
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

    def require_trusted_verification_execution(self) -> None:
        raise FedTrustedVerificationUnavailable(
            "No independently provisioned, reviewed RFC 3161 cryptographic verifier "
            "is attached to this execution boundary."
        )

    def require_council_calendar_delivery(self) -> None:
        raise FedTrustedVerificationUnavailable(
            "A work order and policy reference cannot authorize Fed Council delivery."
        )

    def require_historical_replay(self) -> None:
        raise FedTrustedVerificationUnavailable(
            "Historic source custody/time and separate replay approval are missing."
        )

    def require_persistence(self) -> None:
        raise FedTrustedVerificationUnavailable(
            "Trusted verification work orders cannot authorize Fed storage."
        )

    def accept_external_verification_assertion(self, assertion: object) -> None:
        """Explicitly reject a fake verifier result (including `True`)."""
        raise FedTrustedVerificationUnavailable(
            "A caller-supplied verification result has no trusted execution authority."
        )


def build_fed_calendar_verifier_boundary(
    acquisition: FedCalendarAcquisitionRecord,
    extraction: FedCalendarScheduleExtraction,
    audit: FedCalendarEvidenceAudit,
    gaps: FedCalendarProvenanceGapReport,
    claims: tuple[FedCalendarExternalEvidenceClaim, ...],
    review: FedCalendarExternalEvidenceReview,
    captures: tuple[FedCalendarExternalArtifactCapture, ...],
    intake: FedCalendarArtifactIntakeReport,
    authenticity: FedCalendarAuthenticityRequirementsReport,
    verifier_inputs,
    prerequisites: FedCalendarCryptoPrerequisitesReport,
    policy_references,
    *,
    as_of: datetime,
) -> FedTrustedVerifierBoundaryReport:
    """Recompute upstream and construct non-executing, non-authorizing work.

    `policy_references` are untrusted *assertions* about intended policy,
    not a live trust store. Agreement with other supplied claims is only a
    local consistency observation. No callback or claimed success is run.
    """
    now = _utc(as_of, "as_of")
    if type(prerequisites) is not FedCalendarCryptoPrerequisitesReport:
        raise TypeError("Expected FedCalendarCryptoPrerequisitesReport.")
    if (claims is None or captures is None or verifier_inputs is None
            or policy_references is None):
        raise TypeError("Claims, captures, verifier inputs and policies cannot be None.")
    try:
        refs = tuple(islice(claims, MAX_EXTERNAL_EVIDENCE_CLAIMS + 1))
        retained = tuple(islice(captures, MAX_EXTERNAL_EVIDENCE_CLAIMS + 1))
        inputs = tuple(islice(verifier_inputs, MAX_VERIFIER_INPUTS + 1))
        policies = tuple(islice(policy_references, MAX_TRUST_POLICY_REFERENCES + 1))
    except (TypeError, ValueError) as exc:
        raise TypeError("Claims, captures, verifier inputs and policies must be iterable.") from exc
    if (len(refs) > MAX_EXTERNAL_EVIDENCE_CLAIMS
            or len(retained) > MAX_EXTERNAL_EVIDENCE_CLAIMS
            or len(inputs) > MAX_VERIFIER_INPUTS
            or len(policies) > MAX_TRUST_POLICY_REFERENCES):
        raise FedVerifierBoundaryError("Verifier boundary input count exceeded.")
    try:
        canonical = assess_fed_calendar_crypto_prerequisites(
            acquisition, extraction, audit, gaps, refs, review,
            retained, intake, authenticity, inputs, as_of=now,
        )
    except (FedCryptoPrerequisitesError, ValueError, TypeError) as exc:
        raise FedVerifierBoundaryError(
            "Fed crypto prerequisites cannot be revalidated against the input chain."
        ) from exc
    if canonical != prerequisites:
        raise FedVerifierBoundaryError(
            "Crypto prerequisites report differs from the canonical evidence chain."
        )

    tokens = {(t.provider_id, t.claim_id): t for t in canonical.targets}
    authenticity_by_id = {
        (t.provider_id, t.claim_id): t for t in authenticity.verification_targets
    }
    claims_by_id = {(c.provider_id, c.claim_id): c for c in inputs}
    policy_by_id = {}
    for policy in policies:
        _validate_policy(policy)  # Also defeats frozen-instance mutation bypass.
        key = (policy.provider_id, policy.claim_id)
        if key in policy_by_id:
            raise FedVerifierBoundaryError("Duplicate verifier policy reference identity.")
        if key not in tokens:
            raise FedVerifierBoundaryError("Verifier policy must target a catalogued timestamp token.")
        policy_by_id[key] = policy

    policy_data = [
        {
            "id": (p.provider_id, p.claim_id),
            "revision": p.policy_revision_sha256,
            "anchor": p.pinned_tsa_anchor_certificate_sha256,
            "policies": p.allowed_tsa_policy_oids,
            "mandatory_controls": STRICT_VERIFIER_CONTROLS,
        }
        for p in sorted(policies, key=lambda i: (i.provider_id, i.claim_id))
    ]
    policy_digest = sha256(json.dumps(
        policy_data, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")).hexdigest()

    blockers = list(dict.fromkeys((*canonical.blockers, *_ALWAYS_BLOCKERS)))
    results = []
    for item in canonical.targets:
        key = (item.provider_id, item.claim_id)
        candidate = policy_by_id.get(key)
        claim = claims_by_id.get(key)
        prior = {c.code: c.state for c in item.checks}
        states = []

        def step(index: int, state: FedBoundaryCheckState) -> None:
            states.append(FedBoundaryRequirement(VERIFIER_BOUNDARY_CHECKS[index], state))

        token_ok = item.token_body_sha256 is not None
        document_ok = item.locally_bound_document_sha256 is not None
        step(0, FedBoundaryCheckState.LOCAL_CANDIDATE_ONLY if token_ok
             else FedBoundaryCheckState.MISSING)
        step(1, FedBoundaryCheckState.LOCAL_CANDIDATE_ONLY if document_ok else (
            FedBoundaryCheckState.CONFLICT if prior[RFC3161_PREREQUISITE_CHECKS[1]]
            is FedCryptoCheckState.CONFLICT else FedBoundaryCheckState.MISSING
        ))
        step(2, FedBoundaryCheckState.UNTRUSTED_REFERENCE_ONLY if candidate is not None
             else FedBoundaryCheckState.MISSING)

        if candidate is None or claim is None or claim.claimed_tsa_trust_anchor_sha256 is None:
            anchor_state = FedBoundaryCheckState.MISSING
        elif candidate.pinned_tsa_anchor_certificate_sha256 != claim.claimed_tsa_trust_anchor_sha256:
            anchor_state = FedBoundaryCheckState.CONFLICT
        else:
            anchor_state = FedBoundaryCheckState.UNTRUSTED_REFERENCE_ONLY
        step(3, anchor_state)

        if candidate is None or claim is None or claim.expected_tsa_policy_oid is None:
            policy_state = FedBoundaryCheckState.MISSING
        elif claim.expected_tsa_policy_oid not in candidate.allowed_tsa_policy_oids:
            policy_state = FedBoundaryCheckState.CONFLICT
        else:
            policy_state = FedBoundaryCheckState.UNTRUSTED_REFERENCE_ONLY
        step(4, policy_state)

        basis = prior[RFC3161_PREREQUISITE_CHECKS[4]]
        step(5, (FedBoundaryCheckState.CONFLICT if basis is FedCryptoCheckState.CONFLICT
                 else FedBoundaryCheckState.UNTRUSTED_REFERENCE_ONLY
                 if basis is FedCryptoCheckState.CLAIM_ONLY else FedBoundaryCheckState.MISSING))
        rev = prior[RFC3161_PREREQUISITE_CHECKS[5]]
        step(6, (FedBoundaryCheckState.UNTRUSTED_REFERENCE_ONLY
                 if rev is FedCryptoCheckState.CLAIM_ONLY else FedBoundaryCheckState.MISSING))
        # Token time is insufficient on its own. The *linked document*
        # may have been first captured later than the historical replay.
        # Preserve its independent temporal/content conflicts too.
        token_history = authenticity_by_id[key]
        document_history = authenticity_by_id.get(
            (item.document_provider_id, item.document_claim_id)
        ) if item.document_provider_id is not None else None
        associated_history = (token_history,) + (
            (document_history,) if document_history is not None else ()
        )
        historical_conflict = (
            prior[RFC3161_PREREQUISITE_CHECKS[9]] is FedCryptoCheckState.CONFLICT
            or prior[RFC3161_PREREQUISITE_CHECKS[1]] is FedCryptoCheckState.CONFLICT
            or any(
                c.check_code in (COMMON_CHECKS[2], COMMON_CHECKS[3])
                and c.state is FedVerificationCheckState.LOCAL_CONFLICT
                for t in associated_history for c in t.checks
            )
        )
        historical_ambiguous = any(
            t.claimed_time_state in (
                FedClaimedTimeState.DATE_ONLY_INTRADAY_AMBIGUOUS,
                FedClaimedTimeState.RELEASE_DAY_TIME_AMBIGUOUS,
            ) for t in associated_history
        )
        step(7, FedBoundaryCheckState.CONFLICT if historical_conflict
             else FedBoundaryCheckState.MISSING if historical_ambiguous
             else FedBoundaryCheckState.UNTRUSTED_REFERENCE_ONLY)
        for index in range(8, len(VERIFIER_BOUNDARY_CHECKS)):
            step(index, FedBoundaryCheckState.TRUSTED_EXECUTION_REQUIRED)

        early = [c.state for c in states[:8]]
        if FedBoundaryCheckState.CONFLICT in early:
            readiness = FedWorkOrderState.BLOCKED_CONFLICT
            blockers.append("FED_VERIFIER_LOCAL_POLICY_OR_TEMPORAL_CONFLICT")
        elif FedBoundaryCheckState.MISSING in early:
            readiness = FedWorkOrderState.BLOCKED_INCOMPLETE
            blockers.append("FED_VERIFIER_LOCAL_WORK_INPUTS_INCOMPLETE")
        else:
            readiness = FedWorkOrderState.INPUT_CANDIDATE_ONLY
            blockers.append("FED_VERIFIER_CANDIDATE_AWAITING_INDEPENDENT_ENGINE")

        results.append(FedTrustedVerifierWorkOrder(
            provider_id=item.provider_id,
            claim_id=item.claim_id,
            token_body_sha256=item.token_body_sha256,
            document_body_sha256=item.locally_bound_document_sha256,
            candidate_policy_revision_sha256=(
                candidate.policy_revision_sha256 if candidate is not None else None
            ),
            state=readiness,
            checks=tuple(states),
        ))
    if not tokens:
        blockers.append("FED_VERIFIER_NO_TIMESTAMP_TOKEN_WORK_ITEMS")

    blockers = tuple(dict.fromkeys(blockers))
    manifest = {
        "contract": "fed_calendar_trusted_verifier_execution_boundary_v1",
        "as_of_utc": now.isoformat(timespec="microseconds"),
        "upstream_crypto_prerequisites_sha256": canonical.diagnostic_manifest_sha256,
        "policy_reference_manifest_sha256": policy_digest,
        "work_orders": [
            {
                "id": (w.provider_id, w.claim_id),
                "token_sha256": w.token_body_sha256,
                "document_sha256": w.document_body_sha256,
                "candidate_policy_revision_sha256": w.candidate_policy_revision_sha256,
                "state": w.state.value,
                "requirements": [(c.code, c.state.value) for c in w.checks],
            }
            for w in results
        ],
        "strict_verifier_controls": STRICT_VERIFIER_CONTROLS,
        "blockers": blockers,
    }
    receipt_hash = sha256(json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")).hexdigest()
    return FedTrustedVerifierBoundaryReport(
        as_of=now,
        source_url=canonical.source_url,
        canonical_crypto_prerequisites_sha256=canonical.diagnostic_manifest_sha256,
        policy_reference_manifest_sha256=policy_digest,
        token_claim_count=len(tokens),
        policy_reference_count=len(policies),
        work_orders=tuple(results),
        strict_verifier_controls=STRICT_VERIFIER_CONTROLS,
        blockers=blockers,
        diagnostic_manifest_sha256=receipt_hash,
    )

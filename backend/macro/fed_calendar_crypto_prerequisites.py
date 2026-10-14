"""F4.5.12: Read-only prerequisites for trustworthy FOMC timestamp verification.

Recomputes the F4.5.6--11 chain before cataloguing *untrusted* inputs for a
future independent RFC 3161 verifier. Local document SHA-256 matching is not
TSTInfo.messageImprint verification. A claimed trust-anchor fingerprint,
policy OID, nonce or reference time is not a certificate, signature, trusted
clock, or verified request transcript.

No ASN.1/CMS parsing, PKIX verification, external IO, database writes,
Council registration, calendar delivery, historical replay, or broker use.
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
    FedLocalArtifactBinding,
)
from backend.macro.fed_calendar_authenticity_requirements import (
    FedCalendarAuthenticityRequirementsError,
    FedCalendarAuthenticityRequirementsReport,
    FedVerificationCheckState,
    COMMON_CHECKS,
    build_fed_calendar_authenticity_requirements,
)
from backend.macro.fed_calendar_client import FedCalendarAcquisitionRecord
from backend.macro.fed_calendar_evidence_audit import FedCalendarEvidenceAudit
from backend.macro.fed_calendar_extractor import FedCalendarScheduleExtraction
from backend.macro.fed_calendar_independent_evidence import (
    FedCalendarExternalEvidenceClaim,
    FedCalendarExternalEvidenceReview,
    FedExternalEvidenceKind,
    MAX_EXTERNAL_EVIDENCE_CLAIMS,
)
from backend.macro.fed_calendar_provenance_gaps import FedCalendarProvenanceGapReport


SHA256_OID = "2.16.840.1.101.3.4.2.1"
TIMESTAMPING_EKU_OID = "1.3.6.1.5.5.7.3.8"
TSTINFO_CONTENT_TYPE_OID = "1.2.840.113549.1.9.16.1.4"
MAX_VERIFIER_INPUTS = MAX_EXTERNAL_EVIDENCE_CLAIMS

RFC3161_PREREQUISITE_CHECKS = (
    "OPAQUE_RFC3161_RESPONSE_BYTES_RETAINED",
    "SEPARATE_DOCUMENT_BYTES_FOR_MESSAGE_IMPRINT_CANDIDATE",
    "TSA_TRUST_ANCHOR_REFERENCE_NOT_AUTHENTICATED",
    "ALLOWED_TSA_POLICY_REFERENCE_NOT_AUTHENTICATED",
    "REQUEST_NONCE_OR_INDEPENDENT_TIME_REFERENCE_NOT_VERIFIED",
    "TSA_REVOCATION_EVIDENCE_REFERENCE_NOT_AUTHENTICATED",
    "RFC3161_CMS_SIGNATURE_AND_SIGNER_ESS_CERT_ID_NOT_VERIFIED",
    "RFC3161_PKIX_CHAIN_EKU_AND_REVOCATION_NOT_VERIFIED",
    "RFC3161_TSTINFO_IMPRINT_SHA256_NOT_VERIFIED",
    "RFC3161_GEN_TIME_POLICY_NONCE_AND_ASOF_NOT_VERIFIED",
    "ARCHIVED_SOURCE_CUSTODY_AND_SEPARATE_ADMISSION_NOT_VERIFIED",
)

_ALWAYS_BLOCKERS = (
    "FED_CRYPTO_RFC3161_CMS_SIGNATURE_NOT_VERIFIED",
    "FED_CRYPTO_TSA_CHAIN_EKU_AND_REVOCATION_NOT_VERIFIED",
    "FED_CRYPTO_TSTINFO_IMPRINT_AND_GEN_TIME_NOT_VERIFIED",
    "FED_CRYPTO_INDEPENDENT_ARCHIVE_CUSTODY_UNVERIFIED",
    "FED_CRYPTO_COUNCIL_REPLAY_STORAGE_NOT_AUTHORIZED",
)


class FedCryptoPrerequisitesError(ValueError):
    """Inputs or their re-audited upstream evidence cannot be trusted."""


class FedCryptoPrerequisitesNotReady(RuntimeError):
    """Verification prerequisites do not grant authentication or admission."""


class FedCryptoCheckState(str, Enum):
    LOCAL_CANDIDATE_ONLY = "LOCAL_CANDIDATE_ONLY"
    CLAIM_ONLY = "CLAIM_ONLY"
    MISSING = "MISSING"
    CONFLICT = "CONFLICT"
    INDEPENDENT_VERIFICATION_REQUIRED = "INDEPENDENT_VERIFICATION_REQUIRED"


def _utc(value: datetime, field: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise FedCryptoPrerequisitesError(
            f"{field} must be a timezone-aware datetime."
        )
    return value.astimezone(timezone.utc)


def _identifier(value: str, field: str) -> str:
    if type(value) is not str or re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,63}", value) is None:
        raise FedCryptoPrerequisitesError(f"Invalid {field} reference identifier.")
    return value


def _optional_hash(value: str | None, field: str) -> str | None:
    if value is not None and (
        type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None
    ):
        raise FedCryptoPrerequisitesError(f"Invalid {field} SHA-256 reference.")
    return value


def _optional_oid(value: str | None) -> str | None:
    if value is None:
        return None
    if type(value) is not str or len(value) > 128:
        raise FedCryptoPrerequisitesError("Invalid expected TSA policy OID.")
    parts = value.split(".")
    if len(parts) < 3 or len(parts) > 32 or any(
        not re.fullmatch(r"0|[1-9][0-9]{0,9}", p) for p in parts
    ):
        raise FedCryptoPrerequisitesError("Invalid expected TSA policy OID.")
    arcs = [int(p) for p in parts]
    if arcs[0] > 2 or (arcs[0] < 2 and arcs[1] > 39):
        raise FedCryptoPrerequisitesError("Invalid expected TSA policy OID.")
    return value


@dataclass(frozen=True)
class FedRfc3161VerifierInputClaim:
    """Untrusted operator-supplied references, NOT verified TSA materials.

    Trust anchor and revocation values are only digest *references*; the
    corresponding certificate/CRL/OCSP bytes and trust-store decisions are
    not supplied or cryptographically checked by this protocol.
    """

    provider_id: str
    claim_id: str
    document_provider_id: str | None = None
    document_claim_id: str | None = None
    claimed_tsa_trust_anchor_sha256: str | None = None
    expected_tsa_policy_oid: str | None = None
    claimed_request_nonce: int | None = None
    claimed_request_der_sha256: str | None = None
    claimed_external_reference_at: datetime | None = None
    claimed_revocation_evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        _validate_verifier_input(self)


def _validate_verifier_input(item: FedRfc3161VerifierInputClaim) -> None:
    if type(item) is not FedRfc3161VerifierInputClaim:
        raise FedCryptoPrerequisitesError("Exact RFC 3161 input claim type required.")
    _identifier(item.provider_id, "provider_id")
    _identifier(item.claim_id, "claim_id")
    if (item.document_provider_id is None) != (item.document_claim_id is None):
        raise FedCryptoPrerequisitesError(
            "Document provider and claim identifiers must appear together."
        )
    if item.document_provider_id is not None:
        _identifier(item.document_provider_id, "document_provider_id")
        _identifier(item.document_claim_id, "document_claim_id")
        if (item.provider_id, item.claim_id) == (
            item.document_provider_id, item.document_claim_id
        ):
            raise FedCryptoPrerequisitesError(
                "A timestamp token cannot serve as its own document."
            )
    _optional_hash(item.claimed_tsa_trust_anchor_sha256, "TSA trust anchor")
    _optional_hash(item.claimed_request_der_sha256, "request DER")
    _optional_hash(item.claimed_revocation_evidence_sha256, "revocation evidence")
    _optional_oid(item.expected_tsa_policy_oid)
    if item.claimed_request_nonce is not None and (
        type(item.claimed_request_nonce) is not int
        or not (0 < item.claimed_request_nonce < (1 << 256))
    ):
        raise FedCryptoPrerequisitesError("Invalid claimed RFC 3161 request nonce.")
    if item.claimed_external_reference_at is not None:
        _utc(item.claimed_external_reference_at, "claimed_external_reference_at")


@dataclass(frozen=True)
class FedCryptoPrerequisiteCheck:
    code: str
    state: FedCryptoCheckState


@dataclass(frozen=True)
class FedRfc3161PrerequisiteTarget:
    provider_id: str
    claim_id: str
    token_body_sha256: str | None
    document_provider_id: str | None
    document_claim_id: str | None
    locally_bound_document_sha256: str | None
    checks: tuple[FedCryptoPrerequisiteCheck, ...]


@dataclass(frozen=True)
class FedCalendarCryptoPrerequisitesReport:
    """Read-only verification *planning* receipt; never a trust certificate."""

    as_of: datetime
    source_url: str
    canonical_authenticity_sha256: str
    timestamp_claim_count: int
    supplied_verifier_input_count: int
    targets: tuple[FedRfc3161PrerequisiteTarget, ...]
    blockers: tuple[str, ...]
    diagnostic_manifest_sha256: str

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

    def require_council_calendar_delivery(self) -> None:
        raise FedCryptoPrerequisitesNotReady(
            "Trusted Fed source custody and independent Council admission are absent."
        )

    def require_historical_replay(self) -> None:
        raise FedCryptoPrerequisitesNotReady(
            "RFC 3161 proof and historical source availability are not verified."
        )

    def require_persistence(self) -> None:
        raise FedCryptoPrerequisitesNotReady(
            "Cryptographic verification prerequisites do not authorize Fed writes."
        )


def assess_fed_calendar_crypto_prerequisites(
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
    *,
    as_of: datetime,
) -> FedCalendarCryptoPrerequisitesReport:
    """Recompute F4.5.11 before cataloguing untrusted verifier input claims.

    A supplied TSA trust-anchor fingerprint is not obtained from a trust
    store, a nonce has not been matched against a signed/request transcript,
    and an alleged trusted reference clock has not been independently proven.
    This function never parses a token or flips any authorization flag.
    """
    checked_as_of = _utc(as_of, "as_of")
    if type(authenticity) is not FedCalendarAuthenticityRequirementsReport:
        raise TypeError("Expected FedCalendarAuthenticityRequirementsReport.")
    if claims is None or captures is None or verifier_inputs is None:
        raise TypeError("Claims, captures and verifier inputs cannot be None.")
    try:
        refs = tuple(islice(claims, MAX_EXTERNAL_EVIDENCE_CLAIMS + 1))
        retained = tuple(islice(captures, MAX_EXTERNAL_EVIDENCE_CLAIMS + 1))
        inputs = tuple(islice(verifier_inputs, MAX_VERIFIER_INPUTS + 1))
    except (TypeError, ValueError) as exc:
        raise TypeError("Claims, captures and verifier inputs must be iterable.") from exc
    if (len(refs) > MAX_EXTERNAL_EVIDENCE_CLAIMS
            or len(retained) > MAX_EXTERNAL_EVIDENCE_CLAIMS
            or len(inputs) > MAX_VERIFIER_INPUTS):
        raise FedCryptoPrerequisitesError("Verifier material claim limit exceeded.")

    try:
        canonical = build_fed_calendar_authenticity_requirements(
            acquisition, extraction, audit, gaps, refs, review,
            retained, intake, as_of=checked_as_of,
        )
    except (FedCalendarAuthenticityRequirementsError, TypeError) as exc:
        raise FedCryptoPrerequisitesError(
            "Fed calendar authenticity plan cannot be independently recomputed."
        ) from exc
    if authenticity != canonical:
        raise FedCryptoPrerequisitesError(
            "Fed calendar authenticity plan differs from canonical evidence chain."
        )

    refs_by_key = {(r.provider_id, r.claim_id): r for r in refs}
    captures_by_key = {(c.provider_id, c.claim_id): c for c in retained}
    local_by_key = {(f.provider_id, f.claim_id): f for f in intake.findings}
    requirement_by_key = {(t.provider_id, t.claim_id): t for t in canonical.verification_targets}

    bound = {}
    for value in inputs:
        _validate_verifier_input(value)  # Recheck object.__setattr__ bypass.
        key = (value.provider_id, value.claim_id)
        if key in bound:
            raise FedCryptoPrerequisitesError("Duplicate RFC 3161 verifier input identity.")
        ref = refs_by_key.get(key)
        if ref is None or ref.kind is not FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN:
            raise FedCryptoPrerequisitesError(
                "Verifier input must target a catalogued RFC 3161 token claim."
            )
        if value.document_provider_id is not None:
            doc_key = (value.document_provider_id, value.document_claim_id)
            source = refs_by_key.get(doc_key)
            if source is None or source.kind is FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN:
                raise FedCryptoPrerequisitesError(
                    "Linked document must be a distinct catalogued HTML archive claim."
                )
        bound[key] = value

    blockers = list(dict.fromkeys((*canonical.blockers, *_ALWAYS_BLOCKERS)))
    targets = []
    for ref in refs:
        if ref.kind is not FedExternalEvidenceKind.SIGNED_TIMESTAMP_TOKEN:
            continue
        key = (ref.provider_id, ref.claim_id)
        data = bound.get(key)
        token = captures_by_key.get(key)
        expected = requirement_by_key[key]
        report = []

        def step(code: str, state: FedCryptoCheckState) -> None:
            report.append(FedCryptoPrerequisiteCheck(code, state))

        step(RFC3161_PREREQUISITE_CHECKS[0], (
            FedCryptoCheckState.LOCAL_CANDIDATE_ONLY if token is not None
            else FedCryptoCheckState.MISSING
        ))
        if token is None:
            blockers.append("FED_CRYPTO_TIMESTAMP_TOKEN_BYTES_MISSING")

        doc_key = None if data is None or data.document_provider_id is None else (
            data.document_provider_id, data.document_claim_id
        )
        doc_sha = None
        document_state = FedCryptoCheckState.MISSING
        if doc_key is not None:
            doc_ref = refs_by_key[doc_key]
            document = local_by_key.get(doc_key)
            if document is not None:
                if (document.local_document_binding
                        is FedLocalArtifactBinding.DIFFERS_FROM_CLAIMED_DOCUMENT_BYTES
                        or doc_ref.claimed_document_body_sha256
                        != ref.claimed_document_body_sha256):
                    document_state = FedCryptoCheckState.CONFLICT
                elif document.local_document_binding is FedLocalArtifactBinding.MATCHES_CLAIMED_DOCUMENT_BYTES:
                    doc_sha = document.artifact_entity_sha256
                    document_state = FedCryptoCheckState.LOCAL_CANDIDATE_ONLY
        step(RFC3161_PREREQUISITE_CHECKS[1], document_state)
        if document_state is FedCryptoCheckState.CONFLICT:
            blockers.append("FED_CRYPTO_LOCAL_DOCUMENT_DIGEST_CONFLICT")
        elif document_state is FedCryptoCheckState.MISSING:
            blockers.append("FED_CRYPTO_RFC3161_DOCUMENT_BYTES_MISSING")

        for idx, provided in (
            (2, data is not None and data.claimed_tsa_trust_anchor_sha256 is not None),
            (3, data is not None and data.expected_tsa_policy_oid is not None),
        ):
            step(RFC3161_PREREQUISITE_CHECKS[idx], (
                FedCryptoCheckState.CLAIM_ONLY if provided else FedCryptoCheckState.MISSING
            ))
        if data is None or data.claimed_tsa_trust_anchor_sha256 is None:
            blockers.append("FED_CRYPTO_TSA_TRUST_ANCHOR_REFERENCE_MISSING")
        if data is None or data.expected_tsa_policy_oid is None:
            blockers.append("FED_CRYPTO_TSA_POLICY_REFERENCE_MISSING")

        nonce_request_claimed = (
            data is not None and data.claimed_request_nonce is not None
            and data.claimed_request_der_sha256 is not None
        )
        reference_claimed = (
            data is not None and data.claimed_external_reference_at is not None
        )
        future_reference = (
            reference_claimed
            and _utc(data.claimed_external_reference_at, "claimed_external_reference_at")
            > checked_as_of
        )
        clock_state = (
            FedCryptoCheckState.CONFLICT if future_reference else (
                FedCryptoCheckState.CLAIM_ONLY if nonce_request_claimed or reference_claimed
                else FedCryptoCheckState.MISSING
            )
        )
        step(RFC3161_PREREQUISITE_CHECKS[4], clock_state)
        if future_reference:
            blockers.append("FED_CRYPTO_FUTURE_REFERENCE_TIME_CLAIM")
        if clock_state is FedCryptoCheckState.MISSING:
            blockers.append("FED_CRYPTO_RFC3161_TIMELINESS_BASIS_MISSING")

        step(RFC3161_PREREQUISITE_CHECKS[5], (
            FedCryptoCheckState.CLAIM_ONLY if (
                data is not None and data.claimed_revocation_evidence_sha256 is not None
            ) else FedCryptoCheckState.MISSING
        ))
        if data is None or data.claimed_revocation_evidence_sha256 is None:
            blockers.append("FED_CRYPTO_REVOCATION_EVIDENCE_REFERENCE_MISSING")

        step(RFC3161_PREREQUISITE_CHECKS[6], (
            FedCryptoCheckState.INDEPENDENT_VERIFICATION_REQUIRED if token is not None
            else FedCryptoCheckState.MISSING
        ))
        step(RFC3161_PREREQUISITE_CHECKS[7], (
            FedCryptoCheckState.INDEPENDENT_VERIFICATION_REQUIRED if token is not None
            else FedCryptoCheckState.MISSING
        ))
        step(RFC3161_PREREQUISITE_CHECKS[8], (
            FedCryptoCheckState.INDEPENDENT_VERIFICATION_REQUIRED
            if token is not None and document_state is FedCryptoCheckState.LOCAL_CANDIDATE_ONLY
            else document_state if document_state is FedCryptoCheckState.CONFLICT
            else FedCryptoCheckState.MISSING
        ))
        time_conflict = any(
            c.check_code == COMMON_CHECKS[2] and c.state is FedVerificationCheckState.LOCAL_CONFLICT
            for c in expected.checks
        )
        step(RFC3161_PREREQUISITE_CHECKS[9], (
            FedCryptoCheckState.CONFLICT if time_conflict or future_reference else (
                FedCryptoCheckState.INDEPENDENT_VERIFICATION_REQUIRED
                if token is not None else FedCryptoCheckState.MISSING
            )
        ))
        if time_conflict:
            blockers.append("FED_CRYPTO_HISTORICAL_ASOF_CONFLICT")
        step(RFC3161_PREREQUISITE_CHECKS[10], FedCryptoCheckState.INDEPENDENT_VERIFICATION_REQUIRED)

        targets.append(FedRfc3161PrerequisiteTarget(
            provider_id=ref.provider_id,
            claim_id=ref.claim_id,
            token_body_sha256=(token.entity_body_sha256 if token is not None else None),
            document_provider_id=(doc_key[0] if doc_key else None),
            document_claim_id=(doc_key[1] if doc_key else None),
            locally_bound_document_sha256=doc_sha,
            checks=tuple(report),
        ))
        if data is None:
            blockers.append("FED_CRYPTO_VERIFIER_INPUT_CLAIM_MISSING")

    manifest = {
        "contract": "fed_calendar_rfc3161_crypto_verification_prerequisites_v1",
        "as_of_utc": checked_as_of.isoformat(timespec="microseconds"),
        "source_url": canonical.source_url,
        "canonical_authenticity_sha256": canonical.diagnostic_manifest_sha256,
        "supplied_verifier_inputs": [
            {
                "id": (v.provider_id, v.claim_id),
                "document": (v.document_provider_id, v.document_claim_id),
                "claimed_anchor_hash": v.claimed_tsa_trust_anchor_sha256,
                "expected_policy_oid": v.expected_tsa_policy_oid,
                "claimed_nonce": str(v.claimed_request_nonce) if v.claimed_request_nonce is not None else None,
                "claimed_request_der_hash": v.claimed_request_der_sha256,
                "claimed_reference_time": (
                    _utc(v.claimed_external_reference_at, "claimed_external_reference_at")
                    .isoformat(timespec="microseconds")
                    if v.claimed_external_reference_at is not None else None
                ),
                "claimed_revocation_hash": v.claimed_revocation_evidence_sha256,
            }
            for v in sorted(inputs, key=lambda i: (i.provider_id, i.claim_id))
        ],
        "targets": [
            {
                "id": (t.provider_id, t.claim_id),
                "token_sha256": t.token_body_sha256,
                "document_id": (t.document_provider_id, t.document_claim_id),
                "document_sha256": t.locally_bound_document_sha256,
                "checks": [(s.code, s.state.value) for s in t.checks],
            }
            for t in targets
        ],
        "blockers": tuple(dict.fromkeys(blockers)),
    }
    digest = sha256(json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")).hexdigest()
    return FedCalendarCryptoPrerequisitesReport(
        as_of=checked_as_of,
        source_url=canonical.source_url,
        canonical_authenticity_sha256=canonical.diagnostic_manifest_sha256,
        timestamp_claim_count=len(targets),
        supplied_verifier_input_count=len(inputs),
        targets=tuple(targets),
        blockers=tuple(dict.fromkeys(blockers)),
        diagnostic_manifest_sha256=digest,
    )

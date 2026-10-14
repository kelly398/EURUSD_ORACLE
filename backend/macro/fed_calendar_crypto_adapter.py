"""F4.5.14: non-executing RFC 3161 crypto-adapter handoff preflight.

Revalidates the F4.5.6--13 Fed provenance/cryptographic work-order chain,
locally rehashes separately supplied document and token bytes, and recognizes
ONLY a small, strict DER *outer envelope* subset for a CMS ContentInfo token
or an RFC 3161 TimeStampResp carrying that ContentInfo.

This is NOT a CMS, ASN.1 TSTInfo, X.509 or RFC 3161 verifier. No signature,
TSA identity, policy, nonce, signed imprint, trusted time, historical custody
or issuer certificate path is authenticated. No caller-supplied verifier
callback, success assertion, trust-store path, or cryptographic output is
executed or accepted. No IO, network, broker, database, Council or HMM use.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from itertools import islice
import json

from backend.macro.fed_calendar_artifact_intake import (
    FedCalendarArtifactIntakeReport,
    FedCalendarExternalArtifactCapture,
)
from backend.macro.fed_calendar_authenticity_requirements import (
    FedCalendarAuthenticityRequirementsReport,
)
from backend.macro.fed_calendar_client import FedCalendarAcquisitionRecord
from backend.macro.fed_calendar_crypto_prerequisites import (
    FedCalendarCryptoPrerequisitesReport,
    FedRfc3161VerifierInputClaim,
    MAX_VERIFIER_INPUTS,
)
from backend.macro.fed_calendar_evidence_audit import FedCalendarEvidenceAudit
from backend.macro.fed_calendar_extractor import FedCalendarScheduleExtraction
from backend.macro.fed_calendar_independent_evidence import (
    FedCalendarExternalEvidenceClaim,
    FedCalendarExternalEvidenceReview,
    MAX_EXTERNAL_EVIDENCE_CLAIMS,
)
from backend.macro.fed_calendar_provenance_gaps import FedCalendarProvenanceGapReport
from backend.macro.fed_calendar_verifier_boundary import (
    FedTrustedVerifierBoundaryReport,
    FedVerifierBoundaryError,
    FedVerifierPolicyReference,
    FedWorkOrderState,
    MAX_TRUST_POLICY_REFERENCES,
    STRICT_VERIFIER_CONTROLS,
    build_fed_calendar_verifier_boundary,
)


# id-signedData; checking this OID in an untrusted byte stream is NOT CMS verification.
CMS_SIGNED_DATA_OID_DER = bytes.fromhex("06092a864886f70d010702")

TRUSTED_ENGINE_REQUIRED_STEPS = (
    "PARSE_ALL_RFC3161_TSTINFO_AND_CMS_SIGNED_ATTRIBUTES",
    "VALIDATE_CMS_TSA_SIGNATURE_AND_ESS_CERTIFICATE_BINDING",
    "VERIFY_OPERATOR_PROVISIONED_TSA_TRUST_ANCHOR_AND_RFC5280_PATH",
    "VERIFY_EXCLUSIVE_CRITICAL_TIMESTAMPING_EKU_AND_REVOCATION",
    "VERIFY_SIGNED_SHA256_MESSAGE_IMPRINT_AGAINST_DOCUMENT_BYTES",
    "VERIFY_SIGNED_TSA_POLICY_AND_REQUEST_NONCE_OR_TRUSTED_TIME",
    "VERIFY_GEN_TIME_CERT_VALIDITY_AND_REPLAY_AS_OF_VISIBILITY",
    "INDEPENDENTLY_VERIFY_ARCHIVAL_ORIGIN_AND_HISTORICAL_SOURCE_VERSION",
    "REQUIRE_SEPARATE_COUNCIL_REPLAY_STORAGE_POLICY_ADMISSION",
)

_ALWAYS_BLOCKERS = (
    "FED_CRYPTO_ADAPTER_NO_TRUSTED_ENGINE_ATTACHED",
    "FED_CRYPTO_ADAPTER_SIGNATURE_CHAIN_IMPRINT_TIME_NOT_VERIFIED",
    "FED_CRYPTO_ADAPTER_ARCHIVE_CUSTODY_NOT_AUTHENTICATED",
    "FED_CRYPTO_ADAPTER_COUNCIL_REPLAY_STORAGE_DENIED",
)


class FedCryptoAdapterError(ValueError):
    """Malformed adapter inputs, byte mismatch or forged upstream report."""


class FedCryptoAdapterUnavailable(RuntimeError):
    """No separately reviewed, independently configured verifier exists here."""


class FedTokenOuterEncoding(str, Enum):
    MISSING = "MISSING"
    MALFORMED_OR_UNSUPPORTED = "MALFORMED_OR_UNSUPPORTED"
    CMS_SIGNED_DATA_OUTER_ONLY = "CMS_SIGNED_DATA_OUTER_ONLY"
    RFC3161_RESPONSE_WITH_CMS_OUTER_ONLY = "RFC3161_RESPONSE_WITH_CMS_OUTER_ONLY"


class FedAdapterHandoffState(str, Enum):
    BLOCKED_CONFLICT = "BLOCKED_CONFLICT"
    BLOCKED_MISSING_BYTES_OR_POLICY = "BLOCKED_MISSING_BYTES_OR_POLICY"
    BLOCKED_UNSUPPORTED_ENCODING = "BLOCKED_UNSUPPORTED_ENCODING"
    STRUCTURAL_CANDIDATE_ONLY = "STRUCTURAL_CANDIDATE_ONLY"


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise FedCryptoAdapterError("as_of must be a timezone-aware datetime.")
    return value.astimezone(timezone.utc)


def _take(items, maximum: int, name: str) -> tuple:
    if items is None:
        raise TypeError(f"{name} cannot be None.")
    try:
        result = tuple(islice(items, maximum + 1))
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be iterable.") from exc
    if len(result) > maximum:
        raise FedCryptoAdapterError(f"{name} count limit exceeded.")
    return result


def _der_tlv(data: bytes, pos: int, limit: int) -> tuple[int, int, int]:
    """Parse just one strictly definite-length TLV; no recursive ASN.1 claim."""
    if pos + 2 > limit:
        raise ValueError("Incomplete DER header")
    tag = data[pos]
    if tag == 0 or tag & 31 == 31:
        raise ValueError("Unsupported DER tag")
    first = data[pos + 1]
    if first < 128:
        length, head = first, pos + 2
    else:
        number = first & 127
        if number == 0 or number > 3 or pos + 2 + number > limit:
            raise ValueError("Indefinite, overlong or truncated length")
        encoded = data[pos + 2: pos + 2 + number]
        length = int.from_bytes(encoded, "big")
        if encoded[0] == 0 or length < 128 or length < 1 << (8 * (number - 1)):
            raise ValueError("Noncanonical DER length")
        head = pos + 2 + number
    end = head + length
    if end > limit:
        raise ValueError("DER content exceeds envelope")
    return tag, head, end


def _is_cms_contentinfo(data: bytes, start: int, end: int) -> bool:
    """Match only ContentInfo outer SEQUENCE, signedData OID and [0] wrapper.

    The nested SignedData SEQUENCE is not parsed. In particular, a fabricated
    SignedData prefix passes this structural preflight, never verification.
    """
    tag, body_start, body_end = _der_tlv(data, start, end)
    if tag != 0x30 or body_end != end:
        return False
    oid_tag, oid_content, oid_end = _der_tlv(data, body_start, end)
    if oid_tag != 0x06 or data[body_start:oid_end] != CMS_SIGNED_DATA_OID_DER:
        return False
    wrapped_tag, wrapped_start, wrapped_end = _der_tlv(data, oid_end, end)
    if wrapped_tag != 0xA0 or wrapped_end != end:
        return False
    nested_tag, _, nested_end = _der_tlv(data, wrapped_start, wrapped_end)
    return nested_tag == 0x30 and nested_end == wrapped_end and oid_content < oid_end


def _outer_encoding(token: bytes) -> FedTokenOuterEncoding:
    """Conservative local structural hint; NOT a verifier or general decoder."""
    if not token:
        return FedTokenOuterEncoding.MISSING
    try:
        if _is_cms_contentinfo(token, 0, len(token)):
            return FedTokenOuterEncoding.CMS_SIGNED_DATA_OUTER_ONLY
        outer_tag, inner_start, inner_end = _der_tlv(token, 0, len(token))
        if outer_tag != 0x30 or inner_end != len(token):
            return FedTokenOuterEncoding.MALFORMED_OR_UNSUPPORTED
        status_tag, status_begin, status_end = _der_tlv(token, inner_start, inner_end)
        if status_tag != 0x30:
            return FedTokenOuterEncoding.MALFORMED_OR_UNSUPPORTED
        integer_tag, integer_start, integer_end = _der_tlv(token, status_begin, status_end)
        if (integer_tag != 0x02 or integer_end != status_end
                or token[integer_start:integer_end] not in (b"\x00", b"\x01")):
            return FedTokenOuterEncoding.MALFORMED_OR_UNSUPPORTED
        if status_end < inner_end and _is_cms_contentinfo(token, status_end, inner_end):
            return FedTokenOuterEncoding.RFC3161_RESPONSE_WITH_CMS_OUTER_ONLY
    except ValueError:
        pass
    return FedTokenOuterEncoding.MALFORMED_OR_UNSUPPORTED


@dataclass(frozen=True)
class FedCryptoAdapterHandoffTarget:
    provider_id: str
    claim_id: str
    document_provider_id: str | None
    document_claim_id: str | None
    token_body_sha256: str | None
    document_body_sha256: str | None
    policy_revision_reference_sha256: str | None
    outer_encoding: FedTokenOuterEncoding
    state: FedAdapterHandoffState


@dataclass(frozen=True)
class FedCryptoAdapterPlan:
    """Audit receipt only; contains neither raw evidence nor execution handles."""

    as_of: datetime
    source_url: str
    canonical_boundary_sha256: str
    candidate_count: int
    targets: tuple[FedCryptoAdapterHandoffTarget, ...]
    required_engine_steps: tuple[str, ...]
    blockers: tuple[str, ...]
    diagnostic_manifest_sha256: str

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
        raise FedCryptoAdapterUnavailable("No reviewed verifier/trust store is attached.")

    def require_council_calendar_delivery(self) -> None:
        raise FedCryptoAdapterUnavailable("Adapter preflight does not authorize Council delivery.")

    def require_historical_replay(self) -> None:
        raise FedCryptoAdapterUnavailable("Historical verification/admission is incomplete.")

    def require_persistence(self) -> None:
        raise FedCryptoAdapterUnavailable("Adapter preflight does not authorize persistence.")

    def accept_external_verifier_result(self, assertion: object) -> None:
        raise FedCryptoAdapterUnavailable("Caller-supplied verifier results are never trusted.")


def prepare_fed_calendar_crypto_adapter(
    acquisition: FedCalendarAcquisitionRecord,
    extraction: FedCalendarScheduleExtraction,
    audit: FedCalendarEvidenceAudit,
    gaps: FedCalendarProvenanceGapReport,
    claims,
    review: FedCalendarExternalEvidenceReview,
    captures,
    intake: FedCalendarArtifactIntakeReport,
    authenticity: FedCalendarAuthenticityRequirementsReport,
    verifier_inputs,
    prerequisites: FedCalendarCryptoPrerequisitesReport,
    policy_references,
    boundary: FedTrustedVerifierBoundaryReport,
    *,
    as_of: datetime,
) -> FedCryptoAdapterPlan:
    """Rehash supplied bytes and draft work to be done by a FUTURE trusted engine.

    Does not import/execute a verifier. Any positive state means only that a
    limited DER outer-envelope pattern appeared in untrusted captured bytes.
    """
    now = _utc(as_of)
    if type(boundary) is not FedTrustedVerifierBoundaryReport:
        raise TypeError("Expected exact FedTrustedVerifierBoundaryReport.")
    refs = _take(claims, MAX_EXTERNAL_EVIDENCE_CLAIMS, "claims")
    retained = _take(captures, MAX_EXTERNAL_EVIDENCE_CLAIMS, "captures")
    inputs = _take(verifier_inputs, MAX_VERIFIER_INPUTS, "verifier_inputs")
    policies = _take(policy_references, MAX_TRUST_POLICY_REFERENCES, "policy_references")
    try:
        canonical = build_fed_calendar_verifier_boundary(
            acquisition, extraction, audit, gaps, refs, review,
            retained, intake, authenticity, inputs, prerequisites,
            policies, as_of=now,
        )
    except (FedVerifierBoundaryError, TypeError, ValueError) as exc:
        raise FedCryptoAdapterError("Verifier boundary cannot be canonically revalidated.") from exc
    if canonical != boundary:
        raise FedCryptoAdapterError("Verifier boundary report differs from canonical chain.")
    if canonical.strict_verifier_controls != STRICT_VERIFIER_CONTROLS:
        raise FedCryptoAdapterError("Strict controls differ from sealed boundary.")

    by_capture = {(c.provider_id, c.claim_id): c for c in retained}
    by_input = {(i.provider_id, i.claim_id): i for i in inputs}
    blockers = list(dict.fromkeys((*canonical.blockers, *_ALWAYS_BLOCKERS)))
    handoffs = []
    for order in canonical.work_orders:
        key = (order.provider_id, order.claim_id)
        info = by_input.get(key)
        token = by_capture.get(key)
        document_key = (
            (info.document_provider_id, info.document_claim_id)
            if info is not None and info.document_provider_id is not None
            else None
        )
        document = by_capture.get(document_key) if document_key else None
        token_digest = sha256(token.entity_body).hexdigest() if token is not None else None
        doc_digest = sha256(document.entity_body).hexdigest() if document is not None else None
        # The upstream chain has already revalidated every captured byte, but
        # this adapter independently checks its immediate handoff bindings.
        if token_digest != order.token_body_sha256 or (
            order.document_body_sha256 is not None
            and doc_digest != order.document_body_sha256
        ):
            raise FedCryptoAdapterError("Cryptographic adapter byte bindings disagree with work order.")
        # If the upstream work order declined to bind conflicting document
        # bytes, do not present their hash as a verified handoff document.
        bound_document_digest = order.document_body_sha256
        encoding = _outer_encoding(token.entity_body) if token is not None else FedTokenOuterEncoding.MISSING
        if order.state is FedWorkOrderState.BLOCKED_CONFLICT:
            state = FedAdapterHandoffState.BLOCKED_CONFLICT
            blockers.append("FED_CRYPTO_ADAPTER_UPSTREAM_WORK_ORDER_CONFLICT")
        elif order.state is FedWorkOrderState.BLOCKED_INCOMPLETE:
            state = FedAdapterHandoffState.BLOCKED_MISSING_BYTES_OR_POLICY
            blockers.append("FED_CRYPTO_ADAPTER_WORK_ORDER_INCOMPLETE")
        elif encoding not in (
            FedTokenOuterEncoding.CMS_SIGNED_DATA_OUTER_ONLY,
            FedTokenOuterEncoding.RFC3161_RESPONSE_WITH_CMS_OUTER_ONLY,
        ):
            state = FedAdapterHandoffState.BLOCKED_UNSUPPORTED_ENCODING
            blockers.append("FED_CRYPTO_ADAPTER_UNSUPPORTED_TOKEN_ENCODING")
        else:
            state = FedAdapterHandoffState.STRUCTURAL_CANDIDATE_ONLY
            blockers.append("FED_CRYPTO_ADAPTER_OUTER_ENVELOPE_NEEDS_CRYPTOGRAPHIC_VERIFIER")
        handoffs.append(FedCryptoAdapterHandoffTarget(
            provider_id=order.provider_id,
            claim_id=order.claim_id,
            document_provider_id=document_key[0] if document_key else None,
            document_claim_id=document_key[1] if document_key else None,
            token_body_sha256=token_digest,
            document_body_sha256=bound_document_digest,
            policy_revision_reference_sha256=order.candidate_policy_revision_sha256,
            outer_encoding=encoding,
            state=state,
        ))
    if not handoffs:
        blockers.append("FED_CRYPTO_ADAPTER_NO_TIMESTAMP_WORK_ORDERS")

    blockers = tuple(dict.fromkeys(blockers))
    manifest = {
        "contract": "fed_calendar_crypto_adapter_preflight_v1",
        "as_of_utc": now.isoformat(timespec="microseconds"),
        "source_url": canonical.source_url,
        "canonical_boundary_sha256": canonical.diagnostic_manifest_sha256,
        "targets": [
            {
                "id": (t.provider_id, t.claim_id),
                "document_id": (t.document_provider_id, t.document_claim_id),
                "token_sha256": t.token_body_sha256,
                "document_sha256": t.document_body_sha256,
                "policy_revision_reference_sha256": t.policy_revision_reference_sha256,
                "outer_encoding": t.outer_encoding.value,
                "handoff_state": t.state.value,
            }
            for t in handoffs
        ],
        "future_engine_steps": TRUSTED_ENGINE_REQUIRED_STEPS,
        "blockers": blockers,
    }
    digest = sha256(json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")).hexdigest()
    return FedCryptoAdapterPlan(
        as_of=now,
        source_url=canonical.source_url,
        canonical_boundary_sha256=canonical.diagnostic_manifest_sha256,
        candidate_count=sum(t.state is FedAdapterHandoffState.STRUCTURAL_CANDIDATE_ONLY
                            for t in handoffs),
        targets=tuple(handoffs),
        required_engine_steps=TRUSTED_ENGINE_REQUIRED_STEPS,
        blockers=blockers,
        diagnostic_manifest_sha256=digest,
    )

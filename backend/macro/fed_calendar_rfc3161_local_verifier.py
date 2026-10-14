"""F4.5.18: OPT-IN executable RFC 3161 verification, quarantined from admission.

This is the first *actual cryptographic* check in the Fed calendar evidence work:
OpenSSL independently verifies a DER TimeStampResp / CMS timestamp token against
(1) the ORIGINAL SHA-256 timestamp request, including its nonce and TSA policy,
and (2) the distinct raw document bytes. OpenSSL verifies the CMS signer and
certificate path against an explicitly SHA-256-pinned, supplied CA bundle.

NO claim of trusted OS isolation, independent operator trust provisioning,
certificate revocation at genTime, historical Fed publisher authenticity,
Council registration, replay, database persistence, or trade authorization.
The F4.5.14--17 modules and their nonexecuting contracts stay unchanged.

Operator-supplied pins are *consistency checks*, not proof of operator identity.
A successful local crypto check can NEVER be treated as calendar admission.
The OpenSSL executable runs as an external process, NOT an OS-level sandbox.
Do not call on untrusted inputs in an untrusted host without a reviewed sandbox.

See https://docs.openssl.org/master/man1/openssl-ts/ for OpenSSL ts semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import subprocess
import tempfile

# Keep these limits identical to F4.5.16. This standalone executor deliberately
# does NOT import F4.5.16 or F4.5.17: that would make nonexecuting diagnostics
# dependent on an executable implementation and risk accidental capability flow.
MAX_TOKEN_BYTES = 1_048_576
MAX_DOCUMENT_BYTES = 8_388_608
MAX_REQUEST_BYTES = 65_536
MAX_CA_BUNDLE_BYTES = 262_144
MAX_UNTRUSTED_CHAIN_BYTES = 262_144
MAX_TIMEOUT_SECONDS = 10

_SHA256_OID_DER = bytes.fromhex("0609608648016503040201")
_IDENT = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,63}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_OID = re.compile(r"(?:0|1|2)\.(?:0|[1-9][0-9]{0,8})(?:\.(?:0|[1-9][0-9]{0,8})){1,14}\Z")

PERMANENT_BLOCKERS = (
    "FED_F4518_RUNTIME_AND_OS_ISOLATION_NOT_ATTESTED",
    "FED_F4518_OPERATOR_TRUST_ANCHOR_ORIGIN_NOT_AUTHENTICATED",
    "FED_F4518_TSA_REVOCATION_AT_SIGNED_TIME_NOT_VERIFIED",
    "FED_F4518_HISTORICAL_SOURCE_VERSION_AND_CUSTODY_NOT_ATTESTED",
    "FED_F4518_COUNCIL_REPLAY_PERSISTENCE_AND_TRADE_DENIED",
)


class LocalVerifierError(ValueError):
    """Malformed user input, unsafe configuration or noncanonical request."""


class LocalVerifierUnavailable(RuntimeError):
    """No F4.5.18 result can authorize Fed historical or Council admission."""


class LocalVerificationState(str, Enum):
    BLOCKED_OBSERVATION_AFTER_AS_OF = "BLOCKED_OBSERVATION_AFTER_AS_OF"
    BLOCKED_ENGINE_UNAVAILABLE = "BLOCKED_ENGINE_UNAVAILABLE"
    BLOCKED_ENGINE_PIN_MISMATCH = "BLOCKED_ENGINE_PIN_MISMATCH"
    BLOCKED_TRUST_BUNDLE_PIN_MISMATCH = "BLOCKED_TRUST_BUNDLE_PIN_MISMATCH"
    BLOCKED_REQUEST_DOCUMENT_OR_POLICY_MISMATCH = "BLOCKED_REQUEST_DOCUMENT_OR_POLICY_MISMATCH"
    BLOCKED_EXECUTION_ERROR = "BLOCKED_EXECUTION_ERROR"
    BLOCKED_QUERY_SIGNATURE_CHAIN_NONCE_OR_POLICY = "BLOCKED_QUERY_SIGNATURE_CHAIN_NONCE_OR_POLICY"
    BLOCKED_DOCUMENT_SIGNATURE_CHAIN_OR_IMPRINT = "BLOCKED_DOCUMENT_SIGNATURE_CHAIN_OR_IMPRINT"
    BLOCKED_ENGINE_CHANGED_DURING_CHECK = "BLOCKED_ENGINE_CHANGED_DURING_CHECK"
    LOCAL_CRYPTO_CHECKS_PASSED_NOT_ADMITTED = "LOCAL_CRYPTO_CHECKS_PASSED_NOT_ADMITTED"


def _sha_hex(value: str, name: str) -> None:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise LocalVerifierError(f"{name} requires a lowercase 64-character SHA-256 digest")


def _utc(value: datetime, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise LocalVerifierError(f"{name} requires a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _oid_der(oid: str) -> bytes:
    if type(oid) is not str or _OID.fullmatch(oid) is None:
        raise LocalVerifierError("Expected a canonical dotted-decimal TSA policy OID")
    arcs = [int(part) for part in oid.split(".")]
    if arcs[0] not in (0, 1, 2) or (arcs[0] in (0, 1) and arcs[1] > 39):
        raise LocalVerifierError("Invalid initial TSA policy OID arcs")
    encoded = bytearray()
    for number in (40 * arcs[0] + arcs[1], *arcs[2:]):
        octets = [number & 0x7F]
        number >>= 7
        while number:
            octets.append(0x80 | (number & 0x7F))
            number >>= 7
        encoded.extend(reversed(octets))
    if len(encoded) >= 128:
        raise LocalVerifierError("TSA policy OID is too large")
    return bytes((0x06, len(encoded))) + bytes(encoded)


def _bounded_bytes(value: bytes, name: str, maximum: int, allow_empty: bool = False) -> None:
    if type(value) is not bytes or len(value) > maximum or (not allow_empty and not value):
        raise LocalVerifierError(f"{name} must contain bounded immutable bytes")


def _tlv(blob: bytes, offset: int, upper: int) -> tuple[int, bytes, int]:
    """Minimal *strict DER* TLV reader for RFC 3161 request preflight only.

    This DOES NOT parse or authenticate CMS/TSTInfo. OpenSSL performs the actual
    signature and timestamp-token verification. The small parser prevents a
    request with no nonce, non-SHA-256 imprint or wrong policy being accepted.
    """
    if offset + 2 > upper:
        raise LocalVerifierError("Truncated DER timestamp request")
    tag = blob[offset]
    first = blob[offset + 1]
    pos = offset + 2
    if first < 128:
        length = first
    else:
        width = first & 0x7F
        if not 1 <= width <= 3 or pos + width > upper:
            raise LocalVerifierError("Indefinite or excessive DER length")
        if blob[pos] == 0:
            raise LocalVerifierError("Nonminimal DER long length")
        length = int.from_bytes(blob[pos:pos + width], "big")
        pos += width
        if length < 128:
            raise LocalVerifierError("Nonminimal DER length")
    end = pos + length
    if end > upper:
        raise LocalVerifierError("DER field exceeds timestamp request")
    return tag, blob[pos:end], end


def _read(blob: bytes, offset: int, expected_tag: int) -> tuple[bytes, int]:
    tag, content, end = _tlv(blob, offset, len(blob))
    if tag != expected_tag:
        raise LocalVerifierError(f"Wrong RFC 3161 request tag: expected {expected_tag:#x}")
    return content, end


def _positive_der_integer(value: bytes) -> None:
    if not value or len(value) > 21 or value[0] & 0x80 or all(b == 0 for b in value):
        raise LocalVerifierError("Missing or invalid positive timestamp request nonce")
    if len(value) > 1 and value[0] == 0 and not value[1] & 0x80:
        raise LocalVerifierError("Nonminimal timestamp request nonce")


def _check_original_request(query_der: bytes, document: bytes, expected_policy_oid: str) -> None:
    """Require DER TimeStampReq v1, SHA-256 imprint, a policy and a nonce.

    The parsed request remains UNTRUSTED until OpenSSL verifies the signed
    response against it. No regex of an OpenSSL human-readable dump is used.
    """
    seq, end = _read(query_der, 0, 0x30)
    if end != len(query_der):
        raise LocalVerifierError("Trailing bytes outside TimeStampReq")
    pos = 0
    version, pos = _read(seq, pos, 0x02)
    if version != b"\x01":
        raise LocalVerifierError("Only RFC 3161 TimeStampReq version 1 supported")
    imprint, pos = _read(seq, pos, 0x30)
    alg, ap = _read(imprint, 0, 0x30)
    oid, op = _read(alg, 0, 0x06)
    if b"\x06" + bytes((len(oid),)) + oid != _SHA256_OID_DER:
        raise LocalVerifierError("Timestamp request must use SHA-256")
    if op < len(alg):
        params, op = _read(alg, op, 0x05)
        if params:
            raise LocalVerifierError("Invalid SHA-256 AlgorithmIdentifier parameters")
    if op != len(alg):
        raise LocalVerifierError("Unexpected SHA-256 AlgorithmIdentifier fields")
    hashed, hp = _read(imprint, ap, 0x04)
    if hp != len(imprint) or len(hashed) != 32 or hashed != sha256(document).digest():
        raise LocalVerifierError("Original request imprint does not match document bytes")
    policy, pos = _read(seq, pos, 0x06)
    encoded_policy = b"\x06" + bytes((len(policy),)) + policy
    if encoded_policy != _oid_der(expected_policy_oid):
        raise LocalVerifierError("Original request TSA policy differs from operator allowlist")
    nonce, pos = _read(seq, pos, 0x02)
    _positive_der_integer(nonce)
    if pos < len(seq):
        certificate_required, pos = _read(seq, pos, 0x01)
        if certificate_required != b"\xff":
            raise LocalVerifierError("certReq must be DER TRUE when present")
    if pos != len(seq):
        raise LocalVerifierError("Unsupported extra fields in original timestamp request")


def _file_sha256(path: Path) -> str:
    # Bound the executable size to reject devices/absurd inputs. Runtime hash
    # checking is not an OS-backed binary measurement or anti-TOCTOU guarantee.
    if path.stat().st_size > 64 * 1024 * 1024:
        raise OSError("OpenSSL executable unexpectedly large")
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


@dataclass(frozen=True)
class FedRfc3161VerifierPolicy:
    """Operator-provided, but not independently authenticated, trust policy."""

    openssl_executable: str
    expected_openssl_sha256: str
    expected_ca_bundle_sha256: str
    allowed_tsa_policy_oid: str
    timeout_seconds: int = 5

    def __post_init__(self) -> None:
        _validate_policy(self)


def _validate_policy(value: FedRfc3161VerifierPolicy) -> None:
    if type(value) is not FedRfc3161VerifierPolicy:
        raise LocalVerifierError("Exact verifier policy type required")
    if (type(value.openssl_executable) is not str
            or not Path(value.openssl_executable).is_absolute()
            or "\x00" in value.openssl_executable):
        raise LocalVerifierError("OpenSSL executable must be an explicit absolute path")
    _sha_hex(value.expected_openssl_sha256, "OpenSSL binary pin")
    _sha_hex(value.expected_ca_bundle_sha256, "Trust bundle pin")
    _oid_der(value.allowed_tsa_policy_oid)
    if type(value.timeout_seconds) is not int or not 1 <= value.timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise LocalVerifierError("Timeout must be a bounded integer (1..10 seconds)")


@dataclass(frozen=True)
class FedRfc3161VerifierEvidence:
    """Distinct source bytes and ORIGINAL request; caller provenance untrusted."""

    provider_id: str
    claim_id: str
    document_bytes: bytes
    token_or_response_der: bytes
    original_request_der: bytes
    ca_bundle_pem: bytes
    untrusted_signer_chain_pem: bytes
    captured_at: datetime
    token_format: str = "RESPONSE"  # "RESPONSE" or "TOKEN" (CMS ContentInfo)

    def __post_init__(self) -> None:
        _validate_evidence(self)


def _validate_evidence(value: FedRfc3161VerifierEvidence) -> None:
    if type(value) is not FedRfc3161VerifierEvidence:
        raise LocalVerifierError("Exact evidence model required")
    for name in ("provider_id", "claim_id"):
        label = getattr(value, name)
        if type(label) is not str or _IDENT.fullmatch(label) is None:
            raise LocalVerifierError(f"Invalid {name}")
    _bounded_bytes(value.document_bytes, "Document", MAX_DOCUMENT_BYTES)
    _bounded_bytes(value.token_or_response_der, "Timestamp token", MAX_TOKEN_BYTES)
    _bounded_bytes(value.original_request_der, "Original request", MAX_REQUEST_BYTES)
    _bounded_bytes(value.ca_bundle_pem, "Explicit trust bundle", MAX_CA_BUNDLE_BYTES)
    _bounded_bytes(value.untrusted_signer_chain_pem, "Untrusted chain", MAX_UNTRUSTED_CHAIN_BYTES, allow_empty=True)
    for name in ("ca_bundle_pem", "untrusted_signer_chain_pem"):
        material = getattr(value, name)
        if b"PRIVATE KEY" in material or (material and b"-----BEGIN CERTIFICATE-----" not in material):
            raise LocalVerifierError(f"{name} must contain PEM certificates only")
    _utc(value.captured_at, "captured_at")
    if type(value.token_format) is not str or value.token_format not in ("RESPONSE", "TOKEN"):
        raise LocalVerifierError("Explicit DER token format must be RESPONSE or TOKEN")


@dataclass(frozen=True)
class FedRfc3161LocalVerificationReport:
    """Diagnostic receipt for local OpenSSL checks; no downstream authority."""

    as_of: datetime
    provider_id: str
    claim_id: str
    state: LocalVerificationState
    token_sha256: str
    document_sha256: str
    request_sha256: str
    trust_bundle_sha256: str
    untrusted_chain_sha256: str
    configured_binary_sha256: str
    tsa_policy_oid: str
    captured_at: datetime
    token_format: str
    completed_crypto_checks: tuple[str, ...]
    blockers: tuple[str, ...]
    diagnostic_manifest_sha256: str

    @property
    def local_cryptographic_checks_passed(self) -> bool:
        return self.state is LocalVerificationState.LOCAL_CRYPTO_CHECKS_PASSED_NOT_ADMITTED

    @property
    def trusted_verifier_runtime_attested(self) -> bool:
        return False

    @property
    def trusted_tsa_anchor_provenance_authenticated(self) -> bool:
        return False

    @property
    def revocation_at_signed_time_verified(self) -> bool:
        return False

    @property
    def independent_historical_source_attested(self) -> bool:
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

    def require_historical_admission(self) -> None:
        raise LocalVerifierUnavailable("Cryptography alone cannot establish historical Fed provenance")

    def require_council_calendar_delivery(self) -> None:
        raise LocalVerifierUnavailable("Seat 25 calendar delivery remains prohibited")

    def require_historical_replay(self) -> None:
        raise LocalVerifierUnavailable("Fed historical replay remains prohibited")

    def require_persistence(self) -> None:
        raise LocalVerifierUnavailable("Fed database writes remain prohibited")


def _report(
    as_of: datetime,
    evidence: FedRfc3161VerifierEvidence,
    policy: FedRfc3161VerifierPolicy,
    state: LocalVerificationState,
    checks: tuple[str, ...] = (),
) -> FedRfc3161LocalVerificationReport:
    blockers = PERMANENT_BLOCKERS + (() if state is LocalVerificationState.LOCAL_CRYPTO_CHECKS_PASSED_NOT_ADMITTED else (state.value,))
    content = {
        "as_of": as_of.isoformat(timespec="microseconds"),
        "id": [evidence.provider_id, evidence.claim_id],
        "state": state.value,
        "token_sha256": sha256(evidence.token_or_response_der).hexdigest(),
        "document_sha256": sha256(evidence.document_bytes).hexdigest(),
        "request_sha256": sha256(evidence.original_request_der).hexdigest(),
        "trust_bundle_sha256": sha256(evidence.ca_bundle_pem).hexdigest(),
        "untrusted_chain_sha256": sha256(evidence.untrusted_signer_chain_pem).hexdigest(),
        "captured_at": _utc(evidence.captured_at, "captured_at").isoformat(timespec="microseconds"),
        "token_format": evidence.token_format,
        "expected_engine_sha256": policy.expected_openssl_sha256,
        "tsa_policy_oid": policy.allowed_tsa_policy_oid,
        "completed_crypto_checks": checks,
        "blockers": blockers,
    }
    return FedRfc3161LocalVerificationReport(
        as_of=as_of,
        provider_id=evidence.provider_id,
        claim_id=evidence.claim_id,
        state=state,
        token_sha256=content["token_sha256"],
        document_sha256=content["document_sha256"],
        request_sha256=content["request_sha256"],
        trust_bundle_sha256=content["trust_bundle_sha256"],
        untrusted_chain_sha256=content["untrusted_chain_sha256"],
        captured_at=_utc(evidence.captured_at, "captured_at"),
        token_format=evidence.token_format,
        configured_binary_sha256=content["expected_engine_sha256"],
        tsa_policy_oid=policy.allowed_tsa_policy_oid,
        completed_crypto_checks=checks,
        blockers=blockers,
        diagnostic_manifest_sha256=_stable_digest(content),
    )


def verify_fed_calendar_rfc3161_locally(
    evidence: FedRfc3161VerifierEvidence,
    policy: FedRfc3161VerifierPolicy,
    *,
    as_of: datetime,
) -> FedRfc3161LocalVerificationReport:
    """Explicit opt-in local OpenSSL RFC 3161 verification, never admission.

    Fails closed on unsigned/mismatched request, unpinned engine/roots,
    unavailable OpenSSL, failed signer/nonce/policy or imprint checks, timeout,
    or an executable changed while this module was running.

    This function *does* launch OpenSSL and writes bounded raw evidence to a
    private temporary directory. It does not fetch data, call the DB, touch
    Council/agent/HMM code, or claim that the host enforces OS sandboxing.
    """
    _validate_evidence(evidence)  # recheck mutated frozen inputs
    _validate_policy(policy)
    instant = _utc(as_of, "as_of")
    if _utc(evidence.captured_at, "captured_at") > instant:
        return _report(instant, evidence, policy, LocalVerificationState.BLOCKED_OBSERVATION_AFTER_AS_OF)
    actual_ca_pin = sha256(evidence.ca_bundle_pem).hexdigest()
    if actual_ca_pin != policy.expected_ca_bundle_sha256:
        return _report(instant, evidence, policy, LocalVerificationState.BLOCKED_TRUST_BUNDLE_PIN_MISMATCH)
    try:
        _check_original_request(evidence.original_request_der, evidence.document_bytes, policy.allowed_tsa_policy_oid)
    except LocalVerifierError:
        return _report(instant, evidence, policy, LocalVerificationState.BLOCKED_REQUEST_DOCUMENT_OR_POLICY_MISMATCH)

    binary = Path(policy.openssl_executable)
    try:
        if not binary.is_file():
            return _report(instant, evidence, policy, LocalVerificationState.BLOCKED_ENGINE_UNAVAILABLE)
        if _file_sha256(binary) != policy.expected_openssl_sha256:
            return _report(instant, evidence, policy, LocalVerificationState.BLOCKED_ENGINE_PIN_MISMATCH)
    except OSError:
        return _report(instant, evidence, policy, LocalVerificationState.BLOCKED_ENGINE_UNAVAILABLE)

    completed: list[str] = []
    state = LocalVerificationState.BLOCKED_EXECUTION_ERROR
    try:
        with tempfile.TemporaryDirectory(prefix="f4518_rfc3161_") as work:
            directory = Path(work)
            for name, data in (
                ("document.bin", evidence.document_bytes),
                ("timestamp.der", evidence.token_or_response_der),
                ("original_request.tsq", evidence.original_request_der),
                ("roots.pem", evidence.ca_bundle_pem),
                ("untrusted.pem", evidence.untrusted_signer_chain_pem),
            ):
                with (directory / name).open("xb") as dst:
                    dst.write(data)
            # Prevent OpenSSL from loading arbitrary environment-provided
            # openssl.cnf content. OS sandboxing is STILL NOT established.
            (directory / "blank_openssl.cnf").write_bytes(b"\n")
            common = [
                str(binary), "ts", "-verify", "-in", str(directory / "timestamp.der"),
                "-CAfile", str(directory / "roots.pem"), "-x509_strict",
            ]
            if evidence.token_format == "TOKEN":
                common.append("-token_in")
            if evidence.untrusted_signer_chain_pem:
                common.extend(["-untrusted", str(directory / "untrusted.pem")])
            environment = {
                "OPENSSL_CONF": str(directory / "blank_openssl.cnf"),
                "HOME": work,
                "TMP": work,
                "TEMP": work,
                "TMPDIR": work,
            }
            if os.name == "nt":
                # Windows requires SystemRoot for some side-by-side assemblies.
                # Only these OS loader hints are passed through; no user PATH,
                # OpenSSL settings, proxy variables or application secrets.
                for loader_key in ("SystemRoot", "WINDIR"):
                    if os.environ.get(loader_key):
                        environment[loader_key] = os.environ[loader_key]
            # No shell, no inherited environment/secrets, no stdin, no logs of
            # raw bytes, and no unrestricted process runtime. Windows and Linux
            # behave differently; do NOT claim OS-enforced sandbox isolation.
            for argument, label, failure in (
                (["-queryfile", str(directory / "original_request.tsq")],
                 "QUERY_SIGNATURE_CHAIN_NONCE_AND_POLICY", LocalVerificationState.BLOCKED_QUERY_SIGNATURE_CHAIN_NONCE_OR_POLICY),
                (["-data", str(directory / "document.bin")],
                 "DOCUMENT_SIGNATURE_CHAIN_AND_IMPRINT", LocalVerificationState.BLOCKED_DOCUMENT_SIGNATURE_CHAIN_OR_IMPRINT),
            ):
                output = subprocess.run(
                    common + argument,
                    cwd=work,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=policy.timeout_seconds,
                    check=False,
                    shell=False,
                )
                if output.returncode != 0:
                    state = failure
                    break
                completed.append(label)
            else:
                state = LocalVerificationState.LOCAL_CRYPTO_CHECKS_PASSED_NOT_ADMITTED
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        state = LocalVerificationState.BLOCKED_EXECUTION_ERROR

    try:
        if _file_sha256(binary) != policy.expected_openssl_sha256:
            state = LocalVerificationState.BLOCKED_ENGINE_CHANGED_DURING_CHECK
            completed = []
    except OSError:
        state = LocalVerificationState.BLOCKED_ENGINE_CHANGED_DURING_CHECK
        completed = []
    return _report(instant, evidence, policy, state, tuple(completed))

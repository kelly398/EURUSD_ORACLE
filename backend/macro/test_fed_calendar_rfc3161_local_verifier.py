"""F4.5.18 offline tests: actual OpenSSL RFC 3161 verification plus fail-closed gates.

The real cryptographic fixtures use a NEW, LOCAL, SYNTHETIC CA/TSA. They make
no Federal Reserve provenance or real-world TSA trust assertions. OpenSSL test
cases are skipped (never faked) if a compatible local executable is missing.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from shutil import which
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backend.macro.fed_calendar_rfc3161_local_verifier import (
    MAX_CA_BUNDLE_BYTES,
    MAX_DOCUMENT_BYTES,
    MAX_REQUEST_BYTES,
    MAX_TIMEOUT_SECONDS,
    MAX_TOKEN_BYTES,
    PERMANENT_BLOCKERS,
    FedRfc3161LocalVerificationReport,
    FedRfc3161VerifierEvidence,
    FedRfc3161VerifierPolicy,
    LocalVerifierError,
    LocalVerifierUnavailable,
    LocalVerificationState,
    _check_original_request,
    _file_sha256,
    _oid_der,
    _tlv,
    verify_fed_calendar_rfc3161_locally,
)

NOW = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
TOKEN = b"test token"
DOCUMENT = b"Synthetic document only - not a Federal Reserve release."
ROOT = b"-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----\n"
CHAIN = ROOT
FAKE_EXE = "/nonexistent/f4518_openssl.exe" if os.name != "nt" else "C:\\nonexistent\\f4518_openssl.exe"
SHA = lambda contents: sha256(contents).hexdigest()


def synthetic_evidence(**changes):
    values = {
        "provider_id": "LOCAL_FIXTURE",
        "claim_id": "TS01",
        "document_bytes": DOCUMENT,
        "token_or_response_der": TOKEN,
        "original_request_der": b"\x30\x00",
        "ca_bundle_pem": ROOT,
        "untrusted_signer_chain_pem": CHAIN,
        "captured_at": NOW - timedelta(seconds=30),
        "token_format": "RESPONSE",
    }
    values.update(changes)
    return FedRfc3161VerifierEvidence(**values)


def synthetic_policy(**changes):
    values = {
        "openssl_executable": FAKE_EXE,
        "expected_openssl_sha256": "f" * 64,
        "expected_ca_bundle_sha256": SHA(ROOT),
        "allowed_tsa_policy_oid": "1.2.3.4.5.6",
        "timeout_seconds": 3,
    }
    values.update(changes)
    return FedRfc3161VerifierPolicy(**values)


class FedRfc3161LocalVerifierSafetyTests(unittest.TestCase):
    def test_01_boundaries_mirror_f4516(self):
        self.assertEqual(MAX_TOKEN_BYTES, 1_048_576)
        self.assertEqual(MAX_DOCUMENT_BYTES, 8_388_608)
        self.assertLess(MAX_REQUEST_BYTES, MAX_TOKEN_BYTES)

    def test_02_binary_must_be_absolute_path(self):
        with self.assertRaises(LocalVerifierError):
            synthetic_policy(openssl_executable="openssl")

    def test_03_missing_executable_cannot_be_approved(self):
        # Valid DER is tested separately; no execution occurs on malformed request.
        result = verify_fed_calendar_rfc3161_locally(synthetic_evidence(), synthetic_policy(), as_of=NOW)
        self.assertEqual(result.state, LocalVerificationState.BLOCKED_REQUEST_DOCUMENT_OR_POLICY_MISMATCH)
        self.assertFalse(result.local_cryptographic_checks_passed)

    def test_04_missing_trust_hash_rejected(self):
        with self.assertRaises(LocalVerifierError):
            synthetic_policy(expected_ca_bundle_sha256="0")

    def test_05_uppercase_hash_rejected(self):
        with self.assertRaises(LocalVerifierError):
            synthetic_policy(expected_openssl_sha256="A" * 64)

    def test_06_empty_trust_store_rejected(self):
        with self.assertRaises(LocalVerifierError):
            synthetic_evidence(ca_bundle_pem=b"")

    def test_07_private_key_in_trust_store_rejected(self):
        with self.assertRaises(LocalVerifierError):
            synthetic_evidence(ca_bundle_pem=ROOT + b"PRIVATE KEY")

    def test_08_private_key_in_untrusted_chain_rejected(self):
        with self.assertRaises(LocalVerifierError):
            synthetic_evidence(untrusted_signer_chain_pem=ROOT + b"PRIVATE KEY")

    def test_09_empty_untrusted_chain_allowed(self):
        self.assertEqual(synthetic_evidence(untrusted_signer_chain_pem=b"").untrusted_signer_chain_pem, b"")

    def test_10_oversize_document_rejected(self):
        with self.assertRaises(LocalVerifierError):
            synthetic_evidence(document_bytes=b"x" * (MAX_DOCUMENT_BYTES + 1))

    def test_11_oversize_token_rejected(self):
        with self.assertRaises(LocalVerifierError):
            synthetic_evidence(token_or_response_der=b"x" * (MAX_TOKEN_BYTES + 1))

    def test_12_oversize_original_query_rejected(self):
        with self.assertRaises(LocalVerifierError):
            synthetic_evidence(original_request_der=b"x" * (MAX_REQUEST_BYTES + 1))

    def test_13_oversize_root_rejected(self):
        with self.assertRaises(LocalVerifierError):
            synthetic_evidence(ca_bundle_pem=b"x" * (MAX_CA_BUNDLE_BYTES + 1))

    def test_14_wrong_types_rejected(self):
        with self.assertRaises(LocalVerifierError):
            synthetic_evidence(document_bytes=bytearray(DOCUMENT))
        with self.assertRaises(LocalVerifierError):
            synthetic_evidence(provider_id="bad-id")
        with self.assertRaises(LocalVerifierError):
            synthetic_evidence(token_format="PKCS7")

    def test_15_naive_clock_rejected(self):
        with self.assertRaises(LocalVerifierError):
            synthetic_evidence(captured_at=NOW.replace(tzinfo=None))
        with self.assertRaises(LocalVerifierError):
            verify_fed_calendar_rfc3161_locally(synthetic_evidence(), synthetic_policy(),
                                                as_of=NOW.replace(tzinfo=None))

    def test_16_captured_after_asof_blocked_without_process(self):
        e = synthetic_evidence(captured_at=NOW + timedelta(seconds=1))
        with patch("backend.macro.fed_calendar_rfc3161_local_verifier.subprocess.run") as run:
            r = verify_fed_calendar_rfc3161_locally(e, synthetic_policy(), as_of=NOW)
            run.assert_not_called()
        self.assertEqual(r.state, LocalVerificationState.BLOCKED_OBSERVATION_AFTER_AS_OF)

    def test_17_changed_roots_block_even_before_der_parse(self):
        with patch("backend.macro.fed_calendar_rfc3161_local_verifier.subprocess.run") as run:
            r = verify_fed_calendar_rfc3161_locally(synthetic_evidence(),
                synthetic_policy(expected_ca_bundle_sha256="0" * 64), as_of=NOW)
            run.assert_not_called()
        self.assertEqual(r.state, LocalVerificationState.BLOCKED_TRUST_BUNDLE_PIN_MISMATCH)

    def test_18_oid_parser_rejects_invalid_first_arcs(self):
        for value in ("3.2.4", "1.40.3", "1.2", "1.2.-3", "1.2.03", "1.2.3.", "1.2.1234567890"):
            with self.subTest(oid=value), self.assertRaises(LocalVerifierError):
                _oid_der(value)

    def test_19_oid_parser_encodes_expected_policy(self):
        self.assertEqual(_oid_der("1.2.3.4.5.6"), bytes.fromhex("06052a03040506"))

    def test_20_der_reader_rejects_indefinite_length(self):
        with self.assertRaises(LocalVerifierError):
            _tlv(b"\x30\x80", 0, 2)

    def test_21_der_reader_rejects_nonminimal_long_length(self):
        with self.assertRaises(LocalVerifierError):
            _tlv(b"\x30\x81\x00", 0, 3)

    def test_22_empty_sequence_not_valid_timestamp_request(self):
        with self.assertRaises(LocalVerifierError):
            _check_original_request(b"\x30\x00", DOCUMENT, "1.2.3.4.5.6")

    def test_23_der_reader_rejects_truncated_body(self):
        with self.assertRaises(LocalVerifierError):
            _tlv(b"\x30\x05\x01", 0, 3)

    def test_24_permanent_blockers_in_every_report(self):
        r = verify_fed_calendar_rfc3161_locally(synthetic_evidence(), synthetic_policy(), as_of=NOW)
        self.assertTrue(set(PERMANENT_BLOCKERS).issubset(r.blockers))
        self.assertFalse(r.council_admission_authorized)
        self.assertFalse(r.persistence_authorized)

    def test_25_all_authorization_flags_false(self):
        r = verify_fed_calendar_rfc3161_locally(synthetic_evidence(), synthetic_policy(), as_of=NOW)
        for name in (
            "trusted_verifier_runtime_attested", "trusted_tsa_anchor_provenance_authenticated",
            "revocation_at_signed_time_verified", "independent_historical_source_attested",
            "council_admission_authorized", "historical_replay_authorized", "persistence_authorized",
        ):
            self.assertFalse(getattr(r, name), name)
        self.assertEqual(r.directional_trading_score, 0)

    def test_26_delivery_storage_and_replay_all_refuse(self):
        r = verify_fed_calendar_rfc3161_locally(synthetic_evidence(), synthetic_policy(), as_of=NOW)
        for name in ("require_historical_admission", "require_council_calendar_delivery",
                     "require_historical_replay", "require_persistence"):
            with self.assertRaises(LocalVerifierUnavailable):
                getattr(r, name)()

    def test_27_receipt_is_immutable(self):
        r = verify_fed_calendar_rfc3161_locally(synthetic_evidence(), synthetic_policy(), as_of=NOW)
        with self.assertRaises(FrozenInstanceError):
            r.state = LocalVerificationState.LOCAL_CRYPTO_CHECKS_PASSED_NOT_ADMITTED

    def test_28_diagnostic_digest_is_stable_for_identical_inputs(self):
        left = verify_fed_calendar_rfc3161_locally(synthetic_evidence(), synthetic_policy(), as_of=NOW)
        right = verify_fed_calendar_rfc3161_locally(synthetic_evidence(), synthetic_policy(), as_of=NOW)
        self.assertEqual(left, right)

    def test_29_receipt_never_embeds_raw_bytes(self):
        r = verify_fed_calendar_rfc3161_locally(synthetic_evidence(), synthetic_policy(), as_of=NOW)
        self.assertNotIn(DOCUMENT.decode(), repr(r))
        self.assertNotIn(ROOT.decode().strip(), repr(r))
        self.assertNotIn("/nonexistent/f4518_openssl", repr(r))

    def test_30_revalidate_frozen_evidence_mutation(self):
        e = synthetic_evidence()
        object.__setattr__(e, "document_bytes", [])
        with self.assertRaises(LocalVerifierError):
            verify_fed_calendar_rfc3161_locally(e, synthetic_policy(), as_of=NOW)

    def test_31_revalidate_frozen_policy_mutation(self):
        p = synthetic_policy()
        object.__setattr__(p, "timeout_seconds", True)
        with self.assertRaises(LocalVerifierError):
            verify_fed_calendar_rfc3161_locally(synthetic_evidence(), p, as_of=NOW)

    def test_32_wrong_report_class_not_accepted_as_evidence(self):
        with self.assertRaises(LocalVerifierError):
            verify_fed_calendar_rfc3161_locally(synthetic_policy(), synthetic_policy(), as_of=NOW)

    def test_33_no_overly_long_timeout(self):
        for value in (0, -1, MAX_TIMEOUT_SECONDS + 1, True, 2.0):
            with self.subTest(timeout=value), self.assertRaises(LocalVerifierError):
                synthetic_policy(timeout_seconds=value)

    def test_34_boolean_cannot_be_utc_datetime(self):
        with self.assertRaises(LocalVerifierError):
            verify_fed_calendar_rfc3161_locally(synthetic_evidence(), synthetic_policy(), as_of=True)

    def test_35_empty_data_rejected(self):
        with self.assertRaises(LocalVerifierError):
            synthetic_evidence(document_bytes=b"")
        with self.assertRaises(LocalVerifierError):
            synthetic_evidence(token_or_response_der=b"")

    def test_36_trust_root_must_be_cert_pem(self):
        with self.assertRaises(LocalVerifierError):
            synthetic_evidence(ca_bundle_pem=b"just a hash not a CA")

    def test_37_request_missing_nonce_does_not_execute(self):
        e = synthetic_evidence(original_request_der=b"\x30\x05\x02\x01\x01\x30\x00")
        with patch("backend.macro.fed_calendar_rfc3161_local_verifier.subprocess.run") as run:
            r = verify_fed_calendar_rfc3161_locally(e, synthetic_policy(), as_of=NOW)
            run.assert_not_called()
        self.assertEqual(r.state, LocalVerificationState.BLOCKED_REQUEST_DOCUMENT_OR_POLICY_MISMATCH)

    def test_38_status_enum_explicit_and_finite(self):
        self.assertEqual(len(LocalVerificationState), 10)
        self.assertEqual(len(set(LocalVerificationState)), len(LocalVerificationState))


class FedRfc3161LocalVerifierActualCryptoTests(unittest.TestCase):
    """Uses an actual synthetic signature; no external Fed data or network."""

    @classmethod
    def setUpClass(cls):
        detected = which("openssl")
        if detected is None:
            raise unittest.SkipTest("OpenSSL not found on PATH; actual crypto tests NOT executed")
        cls.openssl = str(Path(detected).resolve())
        cls.tmp = tempfile.TemporaryDirectory(prefix="f4518_test_signing_")
        cls.dir = Path(cls.tmp.name)
        cls.doc = b"F4.5.18 synthetic RFC 3161 fixture, no claim of Fed authenticity."
        (cls.dir / "document.bin").write_bytes(cls.doc)
        (cls.dir / "tsa.ext").write_text(
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature\n"
            "extendedKeyUsage=critical,timeStamping\n"
            "subjectKeyIdentifier=hash\n"
            "authorityKeyIdentifier=keyid,issuer\n", encoding="ascii")
        (cls.dir / "tsa.conf").write_text(
            "[tsa]\n"
            "default_tsa=test_tsa\n"
            "[test_tsa]\n"
            "serial=serial\n"
            "default_policy=1.2.3.4.5.6\n"
            "signer_cert=tsa.pem\n"
            "certs=root.pem\n"
            "signer_key=tsa.key\n"
            "signer_digest=sha256\n"
            "digests=sha256\n"
            "accuracy=secs:1\n"
            "ordering=yes\n"
            "tsa_name=yes\n"
            "ess_cert_id_chain=yes\n", encoding="ascii")
        (cls.dir / "serial").write_text("01\n", encoding="ascii")

        def call(*args):
            run = subprocess.run([cls.openssl, *args], cwd=str(cls.dir),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=15, check=False)
            if run.returncode:
                raise unittest.SkipTest("OpenSSL synthetic fixture cannot be constructed: "
                                        + run.stderr.decode("utf-8", "replace")[-300:])

        try:
            call("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "root.key",
                 "-out", "root.pem", "-days", "2", "-subj", "/CN=F4518 Synthetic Root",
                 "-addext", "basicConstraints=critical,CA:TRUE",
                 "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                 "-addext", "subjectKeyIdentifier=hash")
            call("req", "-newkey", "rsa:2048", "-nodes", "-keyout", "tsa.key",
                 "-out", "tsa.csr", "-subj", "/CN=F4518 Synthetic TSA Signer")
            call("x509", "-req", "-in", "tsa.csr", "-CA", "root.pem", "-CAkey", "root.key",
                 "-CAcreateserial", "-out", "tsa.pem", "-days", "2", "-extfile", "tsa.ext")
            call("ts", "-query", "-data", "document.bin", "-sha256",
                 "-tspolicy", "1.2.3.4.5.6", "-cert", "-out", "request.tsq")
            call("ts", "-reply", "-queryfile", "request.tsq", "-config", "tsa.conf",
                 "-out", "response.tsr")
            call("ts", "-reply", "-in", "response.tsr", "-token_out", "-out", "token.der")
        except Exception:
            cls.tmp.cleanup()
            raise
        cls.policy = FedRfc3161VerifierPolicy(
            openssl_executable=cls.openssl,
            expected_openssl_sha256=_file_sha256(Path(cls.openssl)),
            expected_ca_bundle_sha256=SHA((cls.dir / "root.pem").read_bytes()),
            allowed_tsa_policy_oid="1.2.3.4.5.6",
            timeout_seconds=5,
        )
        cls.evidence = FedRfc3161VerifierEvidence(
            provider_id="SYNTHETIC_TSA", claim_id="SIGNED_T1",
            document_bytes=cls.doc,
            token_or_response_der=(cls.dir / "response.tsr").read_bytes(),
            original_request_der=(cls.dir / "request.tsq").read_bytes(),
            ca_bundle_pem=(cls.dir / "root.pem").read_bytes(),
            untrusted_signer_chain_pem=(cls.dir / "tsa.pem").read_bytes(),
            captured_at=NOW - timedelta(minutes=5),
            token_format="RESPONSE",
        )

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "tmp"):
            cls.tmp.cleanup()

    def verify(self, evidence=None, policy=None, as_of=None):
        return verify_fed_calendar_rfc3161_locally(
            evidence if evidence is not None else self.evidence,
            policy if policy is not None else self.policy,
            as_of=as_of if as_of is not None else NOW,
        )

    def test_39_valid_real_signed_response_passes_local_checks(self):
        r = self.verify()
        self.assertIsInstance(r, FedRfc3161LocalVerificationReport)
        self.assertEqual(r.state, LocalVerificationState.LOCAL_CRYPTO_CHECKS_PASSED_NOT_ADMITTED)
        self.assertTrue(r.local_cryptographic_checks_passed)
        self.assertEqual(len(r.completed_crypto_checks), 2)

    def test_40_valid_cms_timestamp_token_passes_local_checks(self):
        e = replace(self.evidence, token_or_response_der=(self.dir / "token.der").read_bytes(), token_format="TOKEN")
        self.assertTrue(self.verify(e).local_cryptographic_checks_passed)

    def test_41_all_authorization_flags_stay_false_even_when_crypto_passes(self):
        r = self.verify()
        self.assertFalse(r.trusted_verifier_runtime_attested)
        self.assertFalse(r.trusted_tsa_anchor_provenance_authenticated)
        self.assertFalse(r.revocation_at_signed_time_verified)
        self.assertFalse(r.independent_historical_source_attested)
        self.assertFalse(r.council_admission_authorized)
        self.assertFalse(r.historical_replay_authorized)
        self.assertFalse(r.persistence_authorized)
        self.assertEqual(r.directional_trading_score, 0)

    def test_42_valid_crypto_never_grants_council_or_replay(self):
        r = self.verify()
        for name in ("require_historical_admission", "require_council_calendar_delivery",
                     "require_historical_replay", "require_persistence"):
            with self.subTest(name=name), self.assertRaises(LocalVerifierUnavailable):
                getattr(r, name)()

    def test_43_forged_timestamp_bytes_rejected(self):
        raw = bytearray(self.evidence.token_or_response_der)
        raw[-10] ^= 1
        r = self.verify(replace(self.evidence, token_or_response_der=bytes(raw)))
        self.assertEqual(r.state, LocalVerificationState.BLOCKED_QUERY_SIGNATURE_CHAIN_NONCE_OR_POLICY)

    def test_44_token_data_is_not_request_bytes(self):
        r = self.verify(replace(self.evidence, original_request_der=self.evidence.token_or_response_der))
        self.assertEqual(r.state, LocalVerificationState.BLOCKED_REQUEST_DOCUMENT_OR_POLICY_MISMATCH)

    def test_45_tampered_document_fails_preflight(self):
        r = self.verify(replace(self.evidence, document_bytes=self.doc + b"tampered"))
        self.assertEqual(r.state, LocalVerificationState.BLOCKED_REQUEST_DOCUMENT_OR_POLICY_MISMATCH)

    def test_46_wrong_policy_oid_refused(self):
        p = replace(self.policy, allowed_tsa_policy_oid="1.2.3.4.5.7")
        r = self.verify(policy=p)
        self.assertEqual(r.state, LocalVerificationState.BLOCKED_REQUEST_DOCUMENT_OR_POLICY_MISMATCH)

    def test_47_no_nonce_request_is_refused(self):
        # DER generation using a legitimate, but intentionally nonce-less request.
        out = self.dir / "no_nonce.tsq"
        subprocess.run([self.openssl, "ts", "-query", "-data", "document.bin",
                        "-sha256", "-tspolicy", "1.2.3.4.5.6", "-no_nonce", "-out", str(out)],
                       cwd=str(self.dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=True, timeout=10)
        r = self.verify(replace(self.evidence, original_request_der=out.read_bytes()))
        self.assertEqual(r.state, LocalVerificationState.BLOCKED_REQUEST_DOCUMENT_OR_POLICY_MISMATCH)

    def test_48_wrong_binary_pin_rejected_before_execution(self):
        p = replace(self.policy, expected_openssl_sha256="0" * 64)
        with patch("backend.macro.fed_calendar_rfc3161_local_verifier.subprocess.run") as run:
            r = self.verify(policy=p)
            run.assert_not_called()
        self.assertEqual(r.state, LocalVerificationState.BLOCKED_ENGINE_PIN_MISMATCH)

    def test_49_wrong_trust_anchor_certificate_rejected(self):
        # Signer has no path to this different operator-pinned root.
        other = self.dir / "other_root.pem"
        if not other.exists():
            run = subprocess.run([self.openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", "other_root.key", "-out", "other_root.pem", "-days", "2",
                "-subj", "/CN=F4518 Other Synthetic Root"], cwd=str(self.dir),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=15)
            self.assertEqual(run.returncode, 0)
        bad = other.read_bytes()
        e = replace(self.evidence, ca_bundle_pem=bad)
        p = replace(self.policy, expected_ca_bundle_sha256=SHA(bad))
        r = self.verify(e, p)
        self.assertEqual(r.state, LocalVerificationState.BLOCKED_QUERY_SIGNATURE_CHAIN_NONCE_OR_POLICY)

    def test_50_token_label_mismatch_is_not_automatically_detected_by_format(self):
        # OpenSSL rejects the response interpreted as a raw CMS token.
        e = replace(self.evidence, token_format="TOKEN")
        r = self.verify(e)
        self.assertEqual(r.state, LocalVerificationState.BLOCKED_QUERY_SIGNATURE_CHAIN_NONCE_OR_POLICY)

    def test_51_original_request_changed_nonce_rejected_cryptographically(self):
        # Regenerate an otherwise identical request with a fresh nonce.
        out = self.dir / "second_nonce.tsq"
        subprocess.run([self.openssl, "ts", "-query", "-data", "document.bin",
                        "-sha256", "-tspolicy", "1.2.3.4.5.6", "-cert", "-out", str(out)],
                       cwd=str(self.dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=True, timeout=10)
        self.assertNotEqual(out.read_bytes(), self.evidence.original_request_der)
        r = self.verify(replace(self.evidence, original_request_der=out.read_bytes()))
        self.assertEqual(r.state, LocalVerificationState.BLOCKED_QUERY_SIGNATURE_CHAIN_NONCE_OR_POLICY)

    def test_52_raw_request_nonce_and_document_pinning(self):
        _check_original_request(self.evidence.original_request_der, self.doc, self.policy.allowed_tsa_policy_oid)
        with self.assertRaises(LocalVerifierError):
            _check_original_request(self.evidence.original_request_der, self.doc + b"x", self.policy.allowed_tsa_policy_oid)

    def test_53_changed_asof_changes_receipt_not_authority(self):
        a = self.verify(as_of=NOW)
        b = self.verify(as_of=NOW + timedelta(seconds=1))
        self.assertNotEqual(a.diagnostic_manifest_sha256, b.diagnostic_manifest_sha256)
        self.assertFalse(a.council_admission_authorized)
        self.assertFalse(b.council_admission_authorized)

    def test_54_equivalent_timezone_asof_is_deterministic(self):
        tz = timezone(timedelta(hours=3))
        alternate = NOW.astimezone(tz)
        self.assertEqual(self.verify(as_of=NOW).diagnostic_manifest_sha256,
                         self.verify(as_of=alternate).diagnostic_manifest_sha256)

    def test_55_success_receipt_never_embeds_private_payload(self):
        r = self.verify()
        self.assertNotIn(self.doc.decode(), repr(r))
        self.assertNotIn("BEGIN CERTIFICATE", repr(r))
        self.assertNotIn("BEGIN PRIVATE KEY", repr(r))

    def test_56_success_report_contains_permanent_blockers(self):
        r = self.verify()
        self.assertTrue(set(PERMANENT_BLOCKERS).issubset(r.blockers))
        self.assertEqual(len(r.blockers), len(PERMANENT_BLOCKERS))

    def test_57_timeout_fails_closed(self):
        with patch("backend.macro.fed_calendar_rfc3161_local_verifier.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="openssl", timeout=1)):
            r = self.verify()
        self.assertEqual(r.state, LocalVerificationState.BLOCKED_EXECUTION_ERROR)
        self.assertFalse(r.local_cryptographic_checks_passed)

    def test_58_second_command_failed_does_not_count_success(self):
        fake = [subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 2)]
        with patch("backend.macro.fed_calendar_rfc3161_local_verifier.subprocess.run", side_effect=fake) as call:
            r = self.verify()
        self.assertEqual(call.call_count, 2)
        self.assertEqual(r.state, LocalVerificationState.BLOCKED_DOCUMENT_SIGNATURE_CHAIN_OR_IMPRINT)
        self.assertEqual(r.completed_crypto_checks, ("QUERY_SIGNATURE_CHAIN_NONCE_AND_POLICY",))

    def test_60_receipt_binds_to_signer_chain_and_token_format(self):
        a = self.verify()
        # The input bytes, their format, and the untrusted chain must be
        # auditable, even if a different chain happens to give the same verdict.
        token = replace(self.evidence, token_or_response_der=(self.dir / "token.der").read_bytes(), token_format="TOKEN")
        b = self.verify(token)
        self.assertNotEqual(a.diagnostic_manifest_sha256, b.diagnostic_manifest_sha256)
        self.assertEqual(a.token_format, "RESPONSE")
        self.assertEqual(b.token_format, "TOKEN")
        self.assertEqual(a.untrusted_chain_sha256, SHA(self.evidence.untrusted_signer_chain_pem))
        self.assertEqual(a.captured_at, self.evidence.captured_at)

    def test_61_success_report_changed_claim_id_changes_manifest(self):
        a = self.verify()
        b = self.verify(replace(self.evidence, claim_id="SIGNED_T2"))
        self.assertNotEqual(a.diagnostic_manifest_sha256, b.diagnostic_manifest_sha256)
        self.assertFalse(b.council_admission_authorized)

    def test_62_binary_changed_during_verification_fails_closed(self):
        with patch("backend.macro.fed_calendar_rfc3161_local_verifier._file_sha256",
                   side_effect=[self.policy.expected_openssl_sha256, "0" * 64]):
            with patch("backend.macro.fed_calendar_rfc3161_local_verifier.subprocess.run",
                       side_effect=[subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0)]):
                r = self.verify()
        self.assertEqual(r.state, LocalVerificationState.BLOCKED_ENGINE_CHANGED_DURING_CHECK)
        self.assertFalse(r.local_cryptographic_checks_passed)

    def test_63_actual_signed_response_without_untrusted_chain(self):
        # -cert signs a response that embeds the TSA certificate. OpenSSL may
        # recover it from the response without external signer-chain hints.
        r = self.verify(replace(self.evidence, untrusted_signer_chain_pem=b""))
        self.assertTrue(r.local_cryptographic_checks_passed)

    def test_64_a_changed_attested_capture_reference_alters_manifest(self):
        a = self.verify()
        b = self.verify(replace(self.evidence, captured_at=self.evidence.captured_at + timedelta(seconds=1)))
        self.assertNotEqual(a.diagnostic_manifest_sha256, b.diagnostic_manifest_sha256)
        self.assertFalse(b.council_admission_authorized)

    def test_59_invocations_use_no_shell_no_network_fetch(self):
        with patch("backend.macro.fed_calendar_rfc3161_local_verifier.subprocess.run",
                   side_effect=[subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0)]) as run:
            r = self.verify()
        self.assertTrue(r.local_cryptographic_checks_passed)
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            self.assertFalse(call.kwargs["shell"])
            self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(call.kwargs["stdout"], subprocess.DEVNULL)
            self.assertNotIn("PATH", call.kwargs["env"])
            self.assertEqual(call.args[0][1:3], ["ts", "-verify"])
            self.assertIn("-CAfile", call.args[0])
            self.assertIn("-x509_strict", call.args[0])
            self.assertNotIn("-crl_download", call.args[0])
        self.assertIn("-queryfile", run.call_args_list[0].args[0])
        self.assertIn("-data", run.call_args_list[1].args[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)

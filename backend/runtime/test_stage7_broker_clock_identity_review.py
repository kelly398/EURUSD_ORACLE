
"""P7-05..08: synthetic broker identity, clock-domain and HOLD safety tests.

Every evidence record is a disposable local fixture, never a real provider
attestation. No existing project history or external service is touched.
"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime.stage7_broker_clock_identity_review import (
    EPOCH, POLICY_SHA256, BrokerClockReviewError, _digest, _utc,
    _time_wall, assess_stage7_broker_clock, run,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "docs/clock_profiles/hfm_forward_20261027.json"
AS_OF = "2026-10-30T12:00:00Z"


def save(path, data):
    path.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def identity():
    return {
        "record_id": "broker_claim_01", "broker_name": "HFM Research Feed",
        "server_identifier": "Server-DEMO-01", "source_channel": "MT5_TERMINAL",
        "account_environment": "DEMO", "issuer_claim": "BrokerIssuer",
        "third_party_issuer_claim": "ClaimedExternalAuditor",
        "third_party_confirmation_ref": "REF001",
        "claimed_issued_at": "2026-10-30T10:00:00Z",
        "claimed_received_at": "2026-10-30T10:05:00Z",
        "relative_path": "broker_claim_01.bin", "file_sha256": "a" * 64,
    }


def reference():
    return {
        "record_id": "utc_ref_01", "source_issuer_claim": "TimeIssuerA",
        "method": "NTP_SERVER", "receipt_id_claim": "RECEIPT_0001",
        "reference_utc_at": "2026-10-30T11:01:00Z",
        "device_utc_at": "2026-10-30T11:01:00.100000Z",
        "first_available_at": "2026-10-30T11:01:01Z",
        "claimed_received_at": "2026-10-30T11:01:02Z",
        "stated_uncertainty_ms": "20", "round_trip_ms": "10",
        "relative_path": "utc_ref_01.bin", "file_sha256": "a" * 64,
    }


def sample():
    return {
        "record_id": "capture_01", "reference_record_id": "utc_ref_01",
        "broker_identity_record_id": "broker_claim_01",
        "broker_timestamp_raw": "2026-10-30 13:01:58.500",
        "broker_timezone_label_claim": "UTC+2_UNVERIFIED",
        "broker_utc_offset_claim_minutes": 120,
        "broker_utc_at_claim": "2026-10-30T11:01:58.500Z",
        "feed_utc_at_claim": "2026-10-30T11:01:58.500Z",
        "device_utc_at_claim": "2026-10-30T11:01:59Z",
        "capture_arrival_utc_claim": "2026-10-30T11:01:59Z",
        "decision_cutoff_at": "2026-10-30T11:02:00Z",
        "first_available_at": "2026-10-30T11:01:59.100Z",
        "claimed_received_at": "2026-10-30T11:01:59.500Z",
        "relative_path": "capture_01.bin", "file_sha256": "a" * 64,
    }


def fixture(tmp_path, *, identities=1, references=1, samples=1):
    root = tmp_path / "untrusted_evidence"
    root.mkdir()
    doc = {
        "schema_version": 1, "epoch_id": EPOCH,
        "packet_id": "LOCAL_FIXTURE_NEVER_AUTHENTICATED",
        "as_of": AS_OF, "identity_claims": [], "utc_references": [], "clock_samples": [],
    }
    for key, template, n in (
        ("identity_claims", identity, identities),
        ("utc_references", reference, references),
        ("clock_samples", sample, samples),
    ):
        for i in range(n):
            entry = template()
            if i:
                entry["record_id"] += "_%02d" % i
                entry["relative_path"] = entry["record_id"] + ".bin"
                if key == "identity_claims":
                    entry["third_party_confirmation_ref"] += "_%02d" % i
                if key == "utc_references":
                    entry["receipt_id_claim"] += "_%02d" % i
                    ref_time = datetime(2026, 10, 30, 11, 1, i, tzinfo=timezone.utc)
                    entry["reference_utc_at"] = ref_time.isoformat()
                    entry["device_utc_at"] = (ref_time + timedelta(milliseconds=100)).isoformat()
                    entry["first_available_at"] = (ref_time + timedelta(milliseconds=200)).isoformat()
                    entry["claimed_received_at"] = (ref_time + timedelta(milliseconds=300)).isoformat()
                if key == "clock_samples":
                    entry["reference_record_id"] = "utc_ref_01"
                    if not identities:
                        entry["broker_identity_record_id"] = None
                    # Shift every time field so sample clock sequence is ascending.
                    for field in (
                        "broker_utc_at_claim", "feed_utc_at_claim", "device_utc_at_claim",
                        "capture_arrival_utc_claim", "decision_cutoff_at", "first_available_at", "claimed_received_at",
                    ):
                        original = _utc(entry[field])
                        entry[field] = (original + timedelta(seconds=i)).isoformat()
                    raw = datetime.fromisoformat(entry["broker_timestamp_raw"])
                    entry["broker_timestamp_raw"] = str(raw + timedelta(seconds=i))
            if key == "clock_samples" and not identities:
                entry["broker_identity_record_id"] = None
            payload = b"SYNTHETIC_UNTRUSTED_" + entry["record_id"].encode()
            (root / entry["relative_path"]).write_bytes(payload)
            entry["file_sha256"] = sha256(payload).hexdigest()
            doc[key].append(entry)
    index = tmp_path / "local_broker_clock_index.json"
    save(index, doc)
    return root, index, doc


def assess(index=None, root=None, *, as_of=AS_OF, policy=POLICY):
    return assess_stage7_broker_clock(
        epoch_id=EPOCH, as_of=as_of, policy_file=policy,
        packet_index=index, evidence_root=root,
    )


def edit(index, doc, collection, row, **changes):
    doc[collection][row].update(changes)
    save(index, doc)


# P7-05. Broker identity contracts and privacy.

def test_p705_no_identity_packet_is_not_verified():
    report = assess()
    assert report["candidate_packet"]["status"] == "NOT_SUPPLIED"
    assert report["broker_identity"]["status"] == "NOT_SUPPLIED"
    assert report["G01_independently_verified"] is False
    assert report["final_action"] == "HOLD"


def test_p705_single_claim_keeps_broker_unattested(tmp_path):
    root, index, _ = fixture(tmp_path, references=0, samples=0)
    result = assess(index, root)
    assert result["broker_identity"]["claims"] == 1
    assert result["broker_identity"]["status"] == "LOCAL_IDENTITY_CLAIM_UNATTESTED"
    assert result["broker_identity"]["third_party_claims_distinct_from_issuer"] == 1
    assert result["G01_independently_verified"] is False


def test_p705_two_matching_claims_still_unattested(tmp_path):
    root, index, doc = fixture(tmp_path, identities=2, references=0, samples=0)
    result = assess(index, root)
    assert result["broker_identity"]["claims"] == 2
    assert result["broker_identity"]["status"] == "LOCAL_IDENTITY_CLAIM_UNATTESTED"


def test_p705_conflicting_server_quarantined(tmp_path):
    root, index, doc = fixture(tmp_path, identities=2, references=0, samples=0)
    edit(index, doc, "identity_claims", 1, server_identifier="Other-Server-99")
    result = assess(index, root)
    assert result["broker_identity"]["conflict_found"]
    assert "BROKER_SERVER_CHANNEL_OR_ENVIRONMENT_CONFLICT" in result["broker_identity"]["findings"]
    assert "LOCAL_CLAIMS_REQUIRE_QUARANTINE_AND_REVIEW" in result["blockers"]


def test_p705_conflicting_live_environment_quarantined(tmp_path):
    root, index, doc = fixture(tmp_path, identities=2, references=0, samples=0)
    edit(index, doc, "identity_claims", 1, account_environment="LIVE")
    assert assess(index, root)["broker_identity"]["conflict_found"]


def test_p705_self_attestation_detected(tmp_path):
    root, index, doc = fixture(tmp_path, references=0, samples=0)
    edit(index, doc, "identity_claims", 0, third_party_issuer_claim="brokerissuer")
    result = assess(index, root)
    assert "SELF_ASSERTED_THIRD_PARTY_CONFIRMATION" in result["broker_identity"]["findings"]


@pytest.mark.parametrize("field,value", [
    ("account_environment", "REAL"), ("source_channel", "BROKER_WRITE_ORDER"),
    ("server_identifier", ""), ("broker_name", "broker@private.example"),
    ("issuer_claim", "password=secret"),
])
def test_p705_invalid_identity_claim_rejected(tmp_path, field, value):
    root, index, doc = fixture(tmp_path, references=0, samples=0)
    edit(index, doc, "identity_claims", 0, **{field: value})
    with pytest.raises(BrokerClockReviewError):
        assess(index, root)


def test_p705_private_account_identifier_field_rejected(tmp_path):
    root, index, doc = fixture(tmp_path, references=0, samples=0)
    edit(index, doc, "identity_claims", 0, account_number="123456789")
    with pytest.raises(BrokerClockReviewError, match="Strict schema"):
        assess(index, root)


def test_p705_public_export_contains_no_server_or_issuer_identifiers(tmp_path):
    root, index, doc = fixture(tmp_path)
    edit(index, doc, "identity_claims", 0, server_identifier="UNIQUE_PRIVATE_SERVER")
    report_json = json.dumps(assess(index, root), sort_keys=True)
    for forbidden in ("UNIQUE_PRIVATE_SERVER", "ClaimedExternalAuditor", "Server-DEMO-01", "REF001"):
        assert forbidden not in report_json
    assert '"personal_account_identifiers_exported": false' in report_json


def test_p705_identity_receipt_chronology_rejected(tmp_path):
    root, index, doc = fixture(tmp_path, references=0, samples=0)
    edit(index, doc, "identity_claims", 0, claimed_issued_at="2026-10-30T11:00:00Z")
    with pytest.raises(BrokerClockReviewError, match="chronology"):
        assess(index, root)


# P7-06. External reference claims and explicit time handling.

def test_p706_one_reference_is_not_independent_proof(tmp_path):
    root, index, _ = fixture(tmp_path, identities=0, samples=0)
    ref = assess(index, root)["utc_reference_claims"]
    assert ref["reference_count"] == 1
    assert ref["status"] == "LOCAL_TIME_REFERENCE_CLAIMS_UNATTESTED"
    assert ref["offset_range_ms_claimed"] == {"minimum": "100.000", "maximum": "100.000"}
    assert ref["independently_authenticated_time_sources"] == 0


def test_p706_distinct_reference_issuers_not_automatically_trusted(tmp_path):
    root, index, doc = fixture(tmp_path, identities=0, references=2, samples=0)
    edit(index, doc, "utc_references", 1, source_issuer_claim="TimeIssuerB")
    result = assess(index, root)
    assert result["utc_reference_claims"]["distinct_claimed_issuers"] == 2
    assert not result["G02_independently_verified"]


def test_p706_same_reference_issuer_receipt_reuse_refused(tmp_path):
    root, index, doc = fixture(tmp_path, identities=0, references=2, samples=0)
    edit(index, doc, "utc_references", 1, receipt_id_claim="RECEIPT_0001")
    with pytest.raises(BrokerClockReviewError, match="Duplicate claimed reference receipt"):
        assess(index, root)


def test_p706_future_reference_availability_rejected(tmp_path):
    root, index, doc = fixture(tmp_path, identities=0, samples=0)
    edit(index, doc, "utc_references", 0, first_available_at="2026-10-30T11:00:00Z")
    with pytest.raises(BrokerClockReviewError, match="available before sample"):
        assess(index, root)


def test_p706_reference_receipt_before_available_rejected(tmp_path):
    root, index, doc = fixture(tmp_path, identities=0, samples=0)
    edit(index, doc, "utc_references", 0, claimed_received_at="2026-10-30T11:00:00Z")
    with pytest.raises(BrokerClockReviewError, match="chronology"):
        assess(index, root)


@pytest.mark.parametrize("field,value", [
    ("method", "UNAUTHENTICATED_DESKTOP_TIME"),
    ("stated_uncertainty_ms", "-1"),
    ("stated_uncertainty_ms", "10001"),
    ("round_trip_ms", "60001"),
    ("round_trip_ms", 5),
    ("device_utc_at", "2026-10-30T11:01:00+03:00"),
])
def test_p706_reference_contract_rejects_invalid(tmp_path, field, value):
    root, index, doc = fixture(tmp_path, identities=0, samples=0)
    edit(index, doc, "utc_references", 0, **{field: value})
    with pytest.raises(BrokerClockReviewError):
        assess(index, root)


def test_p706_reference_offset_conflict_quarantines_samples(tmp_path):
    root, index, doc = fixture(tmp_path, identities=0, references=2)
    edit(index, doc, "utc_references", 1,
         source_issuer_claim="IndependentClaimB",
         device_utc_at="2026-10-30T11:01:02.500Z")
    report = assess(index, root)
    assert "REFERENCE_OFFSET_CONFLICT" in report["utc_reference_claims"]["findings"]
    assert report["clock_domain_review"]["local_quarantined_samples"] == 1
    assert "REFERENCE_OFFSET_CONFLICT" in report["clock_domain_review"]["findings"]


def test_p706_nonmonotonic_reference_samples_flagged(tmp_path):
    root, index, doc = fixture(tmp_path, identities=0, references=2)
    edit(index, doc, "utc_references", 1,
         reference_utc_at="2026-10-30T11:00:59Z")
    report = assess(index, root)
    assert "NON_MONOTONIC_REFERENCE_SAMPLE_SEQUENCE" in report["utc_reference_claims"]["findings"]


def test_p706_reused_reference_record_id_refused(tmp_path):
    root, index, doc = fixture(tmp_path, identities=0, references=2, samples=0)
    edit(index, doc, "utc_references", 1, record_id="utc_ref_01")
    with pytest.raises(BrokerClockReviewError, match="Duplicate evidence record ID"):
        assess(index, root)


# P7-07. Cross-domain source safety and uncertainty propagation.

def test_p707_clean_claims_remain_unattested(tmp_path):
    root, index, _ = fixture(tmp_path)
    data = assess(index, root)
    assert data["clock_domain_review"]["status"] == "LOCAL_CLOCK_SAMPLES_UNATTESTED"
    assert data["clock_domain_review"]["local_quarantined_samples"] == 0
    assert data["clock_domain_review"]["sample_results"][0]["effective_uncertainty_ms_claimed"] == "28.000"
    assert data["clock_domain_review"]["sample_results"][0]["eligible_for_council"] is False


@pytest.mark.parametrize("changes,finding", [
    ({"broker_utc_offset_claim_minutes": None}, "BROKER_UTC_OFFSET_NOT_ESTABLISHED_BY_LABEL"),
    ({"broker_utc_at_claim": None}, "BROKER_UTC_CLAIM_NOT_SUPPLIED"),
    ({"broker_utc_at_claim": "2026-10-30T11:01:57Z"}, "BROKER_WALL_OFFSET_CLAIM_CONFLICT"),
    ({"feed_utc_at_claim": None}, "FEED_TIMESTAMP_NOT_SUPPLIED"),
    ({"feed_utc_at_claim": "2026-10-30T11:02:05Z"}, "FUTURE_FEED_TIMESTAMP"),
    ({"feed_utc_at_claim": "2026-10-30T11:01:40Z"}, "CAPTURE_DELAY_EXCEEDS_POLICY"),
    ({"capture_arrival_utc_claim": "2026-10-30T11:02:01Z"}, "POST_DECISION_CAPTURE"),
    ({"first_available_at": "2026-10-30T11:02:01Z", "claimed_received_at": "2026-10-30T11:02:02Z"}, "POST_DECISION_EVIDENCE_AVAILABILITY"),
    ({"reference_record_id": None}, "REFERENCE_OBSERVATION_NOT_LINKED"),
    ({"device_utc_at_claim": "2026-10-30T11:02:00Z"}, "DEVICE_TIME_AHEAD_OF_CAPTURE"),
])
def test_p707_adversarial_clock_sample_quarantined(tmp_path, changes, finding):
    root, index, doc = fixture(tmp_path)
    edit(index, doc, "clock_samples", 0, **changes)
    report = assess(index, root)
    assert finding in report["clock_domain_review"]["findings"]
    assert report["clock_domain_review"]["local_quarantined_samples"] == 1
    assert not report["council_context_admitted"]


def test_p707_reference_staleness_quarantined(tmp_path):
    root, index, doc = fixture(tmp_path)
    edit(index, doc, "clock_samples", 0,
         decision_cutoff_at="2026-10-30T11:07:00Z")
    assert "REFERENCE_STALE_FOR_DECISION" in assess(index, root)["clock_domain_review"]["findings"]


def test_p707_reference_from_future_quarantined(tmp_path):
    root, index, doc = fixture(tmp_path)
    edit(index, doc, "clock_samples", 0,
         decision_cutoff_at="2026-10-30T11:00:00Z")
    assert "REFERENCE_FROM_FUTURE" in assess(index, root)["clock_domain_review"]["findings"]


def test_p707_reference_unavailable_at_decision_quarantined(tmp_path):
    root, index, doc = fixture(tmp_path)
    edit(index, doc, "utc_references", 0,
         first_available_at="2026-10-30T11:02:10Z",
         claimed_received_at="2026-10-30T11:02:20Z")
    assert "REFERENCE_UNAVAILABLE_AT_DECISION" in assess(index, root)["clock_domain_review"]["findings"]


def test_p707_large_uncertainty_quarantined(tmp_path):
    root, index, doc = fixture(tmp_path)
    edit(index, doc, "utc_references", 0, stated_uncertainty_ms="1500")
    assert "CLOCK_UNCERTAINTY_EXCEEDS_POLICY" in assess(index, root)["clock_domain_review"]["findings"]


def test_p707_missing_reference_id_rejected(tmp_path):
    root, index, doc = fixture(tmp_path)
    edit(index, doc, "clock_samples", 0, reference_record_id="does_not_exist")
    with pytest.raises(BrokerClockReviewError, match="unknown evidence record"):
        assess(index, root)


def test_p707_nonmonotonic_capture_quarantined(tmp_path):
    root, index, doc = fixture(tmp_path, samples=2)
    edit(index, doc, "clock_samples", 1,
         capture_arrival_utc_claim="2026-10-30T11:01:57Z")
    report = assess(index, root)
    assert "NON_MONOTONIC_CAPTURE_SEQUENCE" in report["clock_domain_review"]["findings"]


def test_p707_explicit_offset_not_inferred_from_timezone_label(tmp_path):
    root, index, doc = fixture(tmp_path)
    edit(index, doc, "clock_samples", 0,
         broker_timezone_label_claim="UTC+2", broker_utc_offset_claim_minutes=None)
    assert "BROKER_UTC_OFFSET_NOT_ESTABLISHED_BY_LABEL" in assess(index, root)["clock_domain_review"]["findings"]


@pytest.mark.parametrize("bad", ["2026-10-30 11:01:60", "2026-10-30T11:01:01Z", "2026-11-31 11:00:00", "11:01:00"])
def test_p707_broker_naive_leap_or_timezone_rejected(bad):
    with pytest.raises(BrokerClockReviewError):
        _time_wall(bad)


def test_p707_broker_offset_boolean_rejected(tmp_path):
    root, index, doc = fixture(tmp_path)
    edit(index, doc, "clock_samples", 0, broker_utc_offset_claim_minutes=True)
    with pytest.raises(BrokerClockReviewError, match="integer minutes"):
        assess(index, root)


def test_p707_broker_offset_excessive_rejected(tmp_path):
    root, index, doc = fixture(tmp_path)
    edit(index, doc, "clock_samples", 0, broker_utc_offset_claim_minutes=900)
    with pytest.raises(BrokerClockReviewError, match="integer minutes"):
        assess(index, root)


# P7-08. Strict evidence custody and independent approval separation.

def test_p708_reviewer_packet_explicit_g01_g02(tmp_path):
    root, index, _ = fixture(tmp_path)
    report = assess(index, root)
    packet = report["external_reviewer_requirements"]
    assert set(packet) == {"G01", "G02"}
    assert packet["G01"]["external_reviewer_signature"] is None
    assert packet["G02"]["external_reviewer_signature"] is None
    assert packet["G01"]["operator_decision"] == "NOT_PROVIDED"
    assert packet["G02"]["approved"] is False
    assert report["external_gates_independently_verified"] == 0


def test_p708_local_rehash_cannot_grant_approval(tmp_path):
    root, index, doc = fixture(tmp_path)
    edit(index, doc, "identity_claims", 0, independently_verified=True)
    with pytest.raises(BrokerClockReviewError, match="Strict schema"):
        assess(index, root)


def test_p708_tampered_artifact_refused(tmp_path):
    root, index, doc = fixture(tmp_path)
    (root / doc["utc_references"][0]["relative_path"]).write_bytes(b"TAMPERED")
    with pytest.raises(BrokerClockReviewError, match="SHA-256 mismatch"):
        assess(index, root)


def test_p708_duplicate_artifact_path_refused(tmp_path):
    root, index, doc = fixture(tmp_path)
    edit(index, doc, "clock_samples", 0,
         relative_path=doc["utc_references"][0]["relative_path"])
    with pytest.raises(BrokerClockReviewError, match="Repeated local artifact path"):
        assess(index, root)


@pytest.mark.parametrize("path", ["../private", "/etc/shadow", "C:/temp/account.txt", "a//b", "a/../b", "./x", "folder\\x", "account_number_777.bin"])
def test_p708_unsafe_path_refused(tmp_path, path):
    root, index, doc = fixture(tmp_path)
    edit(index, doc, "identity_claims", 0, relative_path=path)
    with pytest.raises(BrokerClockReviewError, match="Unsafe or sensitive evidence path"):
        assess(index, root)


def test_p708_symlinked_evidence_refused(tmp_path):
    root, index, doc = fixture(tmp_path)
    link = root / "pointer.bin"
    try:
        link.symlink_to(root / doc["identity_claims"][0]["relative_path"])
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation unavailable on Windows")
    edit(index, doc, "identity_claims", 0, relative_path=link.name)
    with pytest.raises(BrokerClockReviewError, match="Symlink"):
        assess(index, root)


def test_p708_duplicate_json_key_refused(tmp_path):
    root, index, _ = fixture(tmp_path)
    index.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(BrokerClockReviewError, match="Duplicate JSON"):
        assess(index, root)


def test_p708_wrong_historical_epoch_refused(tmp_path):
    root, index, doc = fixture(tmp_path)
    doc["epoch_id"] = "f" * 64
    save(index, doc)
    with pytest.raises(BrokerClockReviewError, match="immutable epoch"):
        assess(index, root)


def test_p708_original_policy_tamper_refused(tmp_path):
    bad = tmp_path / "altered_policy.json"
    bad.write_bytes(POLICY.read_bytes() + b" ")
    with pytest.raises(BrokerClockReviewError, match="byte pin mismatch"):
        assess(policy=bad)


def test_p708_no_index_with_root_refused(tmp_path):
    with pytest.raises(BrokerClockReviewError, match="without index"):
        assess(root=tmp_path)


def test_p708_index_requires_evidence_root(tmp_path):
    root, index, _ = fixture(tmp_path)
    with pytest.raises(BrokerClockReviewError, match="root required"):
        assess(index=index)


def test_p708_same_source_same_cutoff_deterministic(tmp_path):
    root, index, _ = fixture(tmp_path)
    one = assess(index, root)
    two = assess(index, root)
    assert one == two
    assert one["report_sha256"] == _digest({k: v for k, v in one.items() if k != "report_sha256"})


def test_p708_no_execution_or_external_approval(tmp_path):
    root, index, _ = fixture(tmp_path)
    report = assess(index, root)
    for name in (
        "files_written", "broker_connected", "network_accessed", "collector_started",
        "council_invoked", "training_authorized", "settlement_invoked",
        "paper_orders_authorized", "live_orders_authorized", "forward_epoch_created",
        "operator_approval_granted", "G01_independently_verified", "G02_independently_verified",
        "council_context_admitted",
    ):
        assert report[name] is False
    assert report["read_only"] is True
    assert report["final_action"] == "HOLD"


def test_p708_cli_needs_readonly_flags():
    with pytest.raises(SystemExit) as exc:
        run(["--epoch-id", EPOCH, "--as-of", AS_OF])
    assert exc.value.code == 2


def test_p708_cli_json_without_evidence(capsys):
    result = run([
        "--read-only-source", "--read-only-evidence", "--epoch-id", EPOCH,
        "--as-of", AS_OF, "--policy-file", str(POLICY), "--json",
    ])
    assert result == 0
    report = json.loads(capsys.readouterr().out)
    assert report["candidate_packet"]["status"] == "NOT_SUPPLIED"
    assert report["external_gates_independently_verified"] == 0
    assert report["final_action"] == "HOLD"


def test_p708_cli_rejects_future_cutoff(capsys):
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    code = run(["--read-only-source", "--read-only-evidence",
                "--epoch-id", EPOCH, "--as-of", future,
                "--policy-file", str(POLICY)])
    assert code == 1
    assert "BLOCKED" in capsys.readouterr().out


@pytest.mark.parametrize("stamp", ["bad", "2026-10-30T12:00:00", "2026-10-30T15:00:00+03:00", "2026-12-31T23:59:60Z"])
def test_p708_explicit_utc_only(stamp):
    with pytest.raises(BrokerClockReviewError):
        _utc(stamp)


def test_p708_sample_index_future_asof_rejected(tmp_path):
    root, index, doc = fixture(tmp_path)
    doc["as_of"] = "2026-12-31T12:00:00Z"
    save(index, doc)
    with pytest.raises(BrokerClockReviewError, match="future review"):
        assess(index, root)


def test_p708_sample_before_reference_unavailable_rejected_only_for_readiness(tmp_path):
    root, index, doc = fixture(tmp_path)
    edit(index, doc, "clock_samples", 0, reference_record_id=None)
    report = assess(index, root)
    assert report["clock_domain_review"]["local_quarantined_samples"] == 1
    assert report["G02_independently_verified"] is False


def test_p706_same_source_offset_jump_is_quarantined(tmp_path):
    root, index, doc = fixture(tmp_path, identities=0, references=2)
    edit(index, doc, "utc_references", 1,
         device_utc_at="2026-10-30T11:01:03.500Z")
    report = assess(index, root)
    assert "INTRA_SOURCE_REFERENCE_OFFSET_JUMP" in report["utc_reference_claims"]["findings"]
    assert "INTRA_SOURCE_REFERENCE_OFFSET_JUMP" in report["clock_domain_review"]["findings"]


def test_p707_reference_receipt_after_decision_is_quarantined(tmp_path):
    root, index, doc = fixture(tmp_path)
    edit(index, doc, "utc_references", 0,
         claimed_received_at="2026-10-30T11:02:01Z")
    report = assess(index, root)
    assert "REFERENCE_RECEIPT_AFTER_DECISION" in report["clock_domain_review"]["findings"]


def test_p707_device_capture_large_gap_quarantined(tmp_path):
    root, index, doc = fixture(tmp_path)
    edit(index, doc, "clock_samples", 0,
         device_utc_at_claim="2026-10-30T11:01:50Z")
    assert "DEVICE_CAPTURE_DOMAIN_DISAGREEMENT" in assess(index, root)["clock_domain_review"]["findings"]


def test_p705_sample_requires_existing_identity_reference(tmp_path):
    root, index, doc = fixture(tmp_path)
    edit(index, doc, "clock_samples", 0, broker_identity_record_id="missing_identity")
    with pytest.raises(BrokerClockReviewError, match="unknown broker identity"):
        assess(index, root)


def test_p705_missing_identity_reference_quarantined(tmp_path):
    root, index, doc = fixture(tmp_path)
    edit(index, doc, "clock_samples", 0, broker_identity_record_id=None)
    assert "BROKER_IDENTITY_CLAIM_NOT_LINKED" in assess(index, root)["clock_domain_review"]["findings"]


def test_p705_conflicting_broker_identity_quarantines_clock_sample(tmp_path):
    root, index, doc = fixture(tmp_path, identities=2)
    edit(index, doc, "identity_claims", 1, server_identifier="A_Different_Server")
    assert "BROKER_IDENTITY_CLAIMS_CONFLICT" in assess(index, root)["clock_domain_review"]["findings"]


def test_p706_broker_issuer_cannot_masquerade_as_external_reference(tmp_path):
    root, index, doc = fixture(tmp_path)
    edit(index, doc, "utc_references", 0, source_issuer_claim="brokerissuer")
    report = assess(index, root)
    assert "REFERENCE_ISSUER_NOT_DISTINCT_FROM_BROKER_CLAIM" in report["utc_reference_claims"]["findings"]
    assert "REFERENCE_ISSUER_NOT_DISTINCT_FROM_BROKER_CLAIM" in report["clock_domain_review"]["findings"]
    assert not report["G02_independently_verified"]

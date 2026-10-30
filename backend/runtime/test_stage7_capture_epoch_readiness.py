
"""P7-09..12: synthetic historical capture, causal chain, future proposal, HOLD.

Tests create only disposable local artifacts in tmp_path. They never invoke
real broker, collector, Council, network, settlement or trading services.
"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime.stage7_capture_epoch_readiness import (
    EPOCH, POLICY_SHA256, CaptureEpochError, FAULTS, GATES, PACKAGES, ROLES,
    STOPS, _digest, _utc, assess_stage7_capture_epoch,
    inspect_capture_custody, inspect_future_epoch_charter,
    offline_recovery_matrix, run,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "docs/clock_profiles/hfm_forward_20261027.json"
AS_OF = "2026-10-30T12:00:00Z"
INDEX_AS_OF = "2026-10-30T11:00:00Z"
BASE = "2026-10-27T10:00:00Z"


def save(path, obj):
    path.write_text(json.dumps(obj, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def source(record_id, kind, parents=(), sequence=1):
    return {
        "record_id": record_id,
        "kind": kind,
        "provider_claim": "UNATTESTED_PROVIDER_A",
        "source_sequence": sequence,
        "raw_timestamp_claim": "2026-10-27 12:00:00 UTC+2 unverified",
        "observed_at_claim": BASE,
        "first_available_at_claim": "2026-10-27T10:00:01Z",
        "captured_at_claim": "2026-10-27T10:00:02Z",
        "claimed_receipt_at": "2026-10-27T10:00:03Z",
        "relative_path": "originals/" + record_id + ".bin",
        "file_sha256": "a" * 64,
        "transform_code_path": None,
        "transform_code_sha256": None,
        "upstream_ids": list(parents),
        "report_parent_sha256": None,
    }


def fixture_capture(tmp_path):
    root = tmp_path / "UNTRUSTED_CAPTURE_ROOT"
    (root / "originals").mkdir(parents=True)
    (root / "code").mkdir()
    source_code = b"def transform(data):\n    return data\n"
    (root / "code/transform.py").write_bytes(source_code)
    rows = [
        source("payload_01", "PROVIDER_PAYLOAD"),
        source("tick_01", "CAPTURED_TICK", ["payload_01"]),
        source("m5_01", "M5_CANDLE", ["tick_01"]),
        source("stored_01", "STORAGE_RECORD", ["m5_01"]),
        source("context_01", "DERIVED_CONTEXT", ["stored_01"]),
        source("receipt_01", "CAPTURE_RECEIPT", ["tick_01"]),
        source("archive_01", "ARCHIVED_REPORT", ["receipt_01"]),
    ]
    for row in rows:
        payload = ("UNTRUSTED_ARTIFACT_" + row["record_id"]).encode("utf-8")
        (root / row["relative_path"]).write_bytes(payload)
        row["file_sha256"] = sha256(payload).hexdigest()
        if row["kind"] in ("M5_CANDLE", "STORAGE_RECORD", "DERIVED_CONTEXT"):
            row["transform_code_path"] = "code/transform.py"
            row["transform_code_sha256"] = sha256(source_code).hexdigest()
    doc = {
        "schema_version": 1, "historical_epoch_id": EPOCH,
        "packet_id": "HISTORICAL_OCT27_UNATTESTED", "as_of": INDEX_AS_OF,
        "records": rows,
    }
    index = tmp_path / "capture_index.json"
    save(index, doc)
    return root, index, doc


def mutate(index, doc, row, **changes):
    doc["records"][row].update(changes)
    save(index, doc)


def append_artifact(root, index, doc, record_id, kind, parents=(), sequence=2):
    item = source(record_id, kind, parents, sequence)
    content = b"UNTRUSTED_ARTIFACT_" + record_id.encode("utf-8")
    (root / item["relative_path"]).write_bytes(content)
    item["file_sha256"] = sha256(content).hexdigest()
    doc["records"].append(item)
    save(index, doc)
    return item


def fixture_charter(tmp_path):
    doc = {
        "schema_version": 1,
        "proposal_id": "FUTURE_RESEARCH_PROPOSAL_ONLY",
        "historical_epoch_id": EPOCH,
        "proposed_epoch_id": "a" * 64,
        "created_at": "2026-10-30T10:00:00Z",
        "proposed_start_at": "2026-11-02T09:00:00Z",
        "proposed_end_at": "2026-11-02T10:00:00Z",
        "research_scope": "RESEARCH_PREPARATION_NO_EXECUTION",
        "isolation_mode": "NO_BROKER_CONNECTIONS_NO_COLLECTOR_START",
        "provider_claims": ["PROVIDER_UNVERIFIED_A"],
        "max_clock_uncertainty_ms": "500",
        "maximum_capture_gap_seconds": 30,
        "retention_days": 365,
        "required_stop_conditions": list(STOPS),
        "required_external_gates": list(GATES),
        "required_approval_roles": list(ROLES),
        "expected_evidence_packages": list(PACKAGES),
        "approval_signature_claims": [],
    }
    path = tmp_path / "prospective_charter.json"
    save(path, doc)
    return path, doc


def assess(*, capture_index=None, capture_root=None, previous_index=None,
           previous_root=None, charter_file=None, policy_file=POLICY, as_of=AS_OF):
    return assess_stage7_capture_epoch(
        epoch_id=EPOCH, as_of=as_of, policy_file=policy_file,
        capture_index=capture_index, capture_root=capture_root,
        previous_index=previous_index, previous_root=previous_root,
        charter_file=charter_file,
    )


# P7-09: historical forensics and first-owned raw byte continuity.

def test_p709_no_historical_index_does_not_invent_capture():
    report = assess()
    inv = report["capture_inventory"]
    assert inv["status"] == "NOT_SUPPLIED"
    assert inv["record_count"] == 0
    assert inv["historical_broker_utc_attested"] is False
    assert report["final_action"] == "HOLD"


def test_p709_original_bytes_and_seven_kinds_locally_hashed(tmp_path):
    root, index, _ = fixture_capture(tmp_path)
    inv, chain, digest = inspect_capture_custody(index_file=index, root=root, as_of=AS_OF)
    assert inv["local_hash_matched"] == 7
    assert inv["record_count"] == 7
    assert inv["record_kinds"]["CAPTURED_TICK"] == 1
    assert inv["record_kinds"]["M5_CANDLE"] == 1
    assert inv["record_kinds"]["ARCHIVED_REPORT"] == 1
    assert inv["transformation_files_hash_matched"] == 1
    assert chain["status"] == "LOCAL_CHAIN_CONSISTENT_UNATTESTED"
    assert len(digest) == 64


def test_p709_original_oct27_policy_unchanged(tmp_path):
    root, index, _ = fixture_capture(tmp_path)
    report = assess(capture_index=index, capture_root=root)
    assert report["original_historical_policy"]["sha256"] == POLICY_SHA256
    assert report["original_historical_policy"]["historical_epoch_rebound"] is False
    assert report["original_historical_policy"]["broker_utc_independently_verified"] is False


def test_p709_raw_timestamp_not_translated_or_exported(tmp_path):
    root, index, _ = fixture_capture(tmp_path)
    body = json.dumps(assess(capture_index=index, capture_root=root))
    assert "2026-10-27 12:00:00" not in body
    assert "UNATTESTED_PROVIDER_A" not in body
    assert "originals/tick_01.bin" not in body


@pytest.mark.parametrize("field,value,finding", [
    ("observed_at_claim", "2026-10-27T10:00:04Z", "OBSERVED_AFTER_CAPTURE_CLAIM"),
    ("first_available_at_claim", "2026-10-27T10:00:03Z", "FIRST_AVAILABLE_AFTER_CAPTURE_CLAIM"),
    ("captured_at_claim", "2026-10-27T10:00:05Z", "CAPTURE_AFTER_CLAIMED_RECEIPT"),
    ("claimed_receipt_at", "2026-10-27T10:00:00Z", "CAPTURE_AFTER_CLAIMED_RECEIPT"),
])
def test_p709_timestamp_conflict_flagged_without_repair(tmp_path, field, value, finding):
    root, index, doc = fixture_capture(tmp_path)
    mutate(index, doc, 1, **{field: value})
    result = assess(capture_index=index, capture_root=root)
    discrepancies = result["capture_inventory"]["timestamp_discrepancies"]
    assert any(x["record_id"] == "tick_01" and x["finding"] == finding for x in discrepancies)
    assert result["capture_inventory"]["status"] == "LOCAL_CAPTURE_FINDINGS_QUARANTINED"
    assert result["final_action"] == "HOLD"


def test_p709_capture_gap_disclosed(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    append_artifact(root, index, doc, "tick_04", "CAPTURED_TICK", ["payload_01"], sequence=4)
    records = assess(capture_index=index, capture_root=root)["capture_inventory"]["sequence_findings"]
    assert {"record_id": "tick_04", "finding": "SOURCE_SEQUENCE_GAP", "missing_count": 2} in records


def test_p709_duplicate_sequence_disclosed(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    append_artifact(root, index, doc, "tick_dupe", "CAPTURED_TICK", ["payload_01"], sequence=1)
    findings = assess(capture_index=index, capture_root=root)["capture_inventory"]["sequence_findings"]
    assert any(x["finding"] == "DUPLICATE_SOURCE_SEQUENCE" for x in findings)


def test_p709_regressed_sequence_disclosed(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    mutate(index, doc, 1, source_sequence=4)
    append_artifact(root, index, doc, "tick_regress", "CAPTURED_TICK", ["payload_01"], sequence=2)
    assert any(x["finding"] == "REGRESSED_SOURCE_SEQUENCE" for x in assess(capture_index=index, capture_root=root)["capture_inventory"]["sequence_findings"])


def test_p709_duplicate_observation_bytes_disclosed(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    other = append_artifact(root, index, doc, "tick_copy", "CAPTURED_TICK", ["payload_01"], sequence=2)
    old = (root / "originals/tick_01.bin").read_bytes()
    (root / other["relative_path"]).write_bytes(old)
    other["file_sha256"] = sha256(old).hexdigest()
    save(index, doc)
    findings = assess(capture_index=index, capture_root=root)["capture_inventory"]["sequence_findings"]
    assert any(x["finding"] == "DUPLICATED_OBSERVATION_BYTES" for x in findings)


def test_p709_modified_original_without_rehash_rejected(tmp_path):
    root, index, _ = fixture_capture(tmp_path)
    (root / "originals/tick_01.bin").write_bytes(b"ALTERED")
    with pytest.raises(CaptureEpochError, match="byte hash mismatch"):
        assess(capture_index=index, capture_root=root)


def test_p709_rewritten_capture_snapshot_detected(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    old = tmp_path / "prior.json"
    save(old, doc)
    mutate(index, doc, 2, raw_timestamp_claim="2026-10-27 15:12:00")
    with pytest.raises(CaptureEpochError, match="prefix removed or rewritten"):
        assess(capture_index=index, capture_root=root,
               previous_index=old, previous_root=root)


def test_p709_append_only_capture_snapshot_local_only(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    old = tmp_path / "prior.json"
    save(old, doc)
    append_artifact(root, index, doc, "payload_02", "PROVIDER_PAYLOAD", sequence=2)
    report = assess(capture_index=index, capture_root=root,
                    previous_index=old, previous_root=root)
    assert report["capture_inventory"]["append_only_prefix"] == "LOCAL_APPEND_ONLY_PREFIX_MATCH_UNATTESTED"
    assert not report["G03_independently_verified"]


def test_p709_future_index_cutoff_rejected(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    doc["as_of"] = "2026-11-03T00:00:00Z"
    save(index, doc)
    with pytest.raises(CaptureEpochError, match="future availability"):
        assess(capture_index=index, capture_root=root)


def test_p709_wrong_historical_epoch_rejected(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    doc["historical_epoch_id"] = "f" * 64
    save(index, doc)
    with pytest.raises(CaptureEpochError, match="Historical epoch"):
        assess(capture_index=index, capture_root=root)


# P7-10: provider-payload -> capture -> candle -> storage -> context.

def test_p710_complete_local_lineage_still_unattested(tmp_path):
    root, index, _ = fixture_capture(tmp_path)
    chain = assess(capture_index=index, capture_root=root)["source_chain_reconciliation"]
    assert chain["checked_links"] == 6
    assert chain["status"] == "LOCAL_CHAIN_CONSISTENT_UNATTESTED"
    assert chain["independently_verified_custody_links"] == 0
    assert not chain["council_source_chain_admitted"]


def test_p710_missing_upstream_link_quarantined(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    mutate(index, doc, 1, upstream_ids=[])
    findings = assess(capture_index=index, capture_root=root)["source_chain_reconciliation"]["findings"]
    assert any(x["finding"] == "MISSING_UPSTREAM_CUSTODY_LINK" for x in findings)


def test_p710_unresolved_upstream_link_quarantined(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    mutate(index, doc, 1, upstream_ids=["nonexistent_payload"])
    findings = assess(capture_index=index, capture_root=root)["source_chain_reconciliation"]["findings"]
    assert any(x["finding"] == "UNRESOLVED_UPSTREAM_RECORD" for x in findings)


def test_p710_forward_upstream_link_quarantined(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    doc["records"][0], doc["records"][1] = doc["records"][1], doc["records"][0]
    save(index, doc)
    findings = assess(capture_index=index, capture_root=root)["source_chain_reconciliation"]["findings"]
    assert any(x["finding"] == "FORWARD_OR_NONCAUSAL_RECORD_LINK" for x in findings)


def test_p710_incompatible_lineage_kind_quarantined(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    mutate(index, doc, 4, upstream_ids=["payload_01"])
    findings = assess(capture_index=index, capture_root=root)["source_chain_reconciliation"]["findings"]
    assert any(x["finding"] == "UNSUPPORTED_SOURCE_CHAIN_EDGE" for x in findings)


def test_p710_noncausal_first_availability_quarantined(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    mutate(index, doc, 1, first_available_at_claim="2026-10-27T10:00:00Z")
    mutate(index, doc, 0, first_available_at_claim="2026-10-27T10:00:02Z")
    findings = assess(capture_index=index, capture_root=root)["source_chain_reconciliation"]["findings"]
    assert any(x["finding"] == "PARENT_AVAILABLE_AFTER_DERIVED_CONTEXT" for x in findings)


def test_p710_transformation_code_absent_is_flagged(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    mutate(index, doc, 2, transform_code_path=None, transform_code_sha256=None)
    findings = assess(capture_index=index, capture_root=root)["source_chain_reconciliation"]["findings"]
    assert any(x["finding"] == "TRANSFORMATION_CODE_NOT_SUPPLIED" for x in findings)


def test_p710_modified_transform_code_is_rejected(tmp_path):
    root, index, _ = fixture_capture(tmp_path)
    (root / "code/transform.py").write_bytes(b"print('new version')\n")
    with pytest.raises(CaptureEpochError, match="code byte hash mismatch"):
        assess(capture_index=index, capture_root=root)


def test_p710_invalid_transform_code_ast_rejected(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    (root / "code/transform.py").write_bytes(b"def function(: bad\n")
    changed = sha256(b"def function(: bad\n").hexdigest()
    for item in doc["records"]:
        if item["transform_code_path"]:
            item["transform_code_sha256"] = changed
    save(index, doc)
    with pytest.raises(CaptureEpochError, match="Python AST invalid"):
        assess(capture_index=index, capture_root=root)


def test_p710_archived_report_ancestry_unresolved(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    append_artifact(root, index, doc, "archive_02", "ARCHIVED_REPORT", ["receipt_01"])
    findings = assess(capture_index=index, capture_root=root)["source_chain_reconciliation"]["findings"]
    assert any(x["finding"] == "ARCHIVED_REPORT_PARENT_NOT_DECLARED" for x in findings)


def test_p710_archived_report_parent_hash_consistent(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    second = append_artifact(root, index, doc, "archive_02", "ARCHIVED_REPORT", ["receipt_01"])
    second["report_parent_sha256"] = doc["records"][6]["file_sha256"]
    save(index, doc)
    findings = assess(capture_index=index, capture_root=root)["source_chain_reconciliation"]["findings"]
    assert not any(x["finding"].startswith("ARCHIVED_REPORT_PARENT") for x in findings)


def test_p710_archived_report_parent_rewrite_detected(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    second = append_artifact(root, index, doc, "archive_02", "ARCHIVED_REPORT", ["receipt_01"])
    second["report_parent_sha256"] = "0" * 64
    save(index, doc)
    findings = assess(capture_index=index, capture_root=root)["source_chain_reconciliation"]["findings"]
    assert any(x["finding"] == "ARCHIVED_REPORT_PARENT_HASH_NOT_FOUND" for x in findings)


def test_p710_duplicate_record_id_rejected(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    mutate(index, doc, 1, record_id="payload_01")
    with pytest.raises(CaptureEpochError, match="Duplicate capture"):
        assess(capture_index=index, capture_root=root)


def test_p710_duplicate_local_path_rejected(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    mutate(index, doc, 1, relative_path="originals/payload_01.bin")
    with pytest.raises(CaptureEpochError, match="path reused"):
        assess(capture_index=index, capture_root=root)


def test_p710_record_extension_cannot_assert_independent_verification(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    mutate(index, doc, 1, independently_approved=True)
    with pytest.raises(CaptureEpochError, match="Strict schema"):
        assess(capture_index=index, capture_root=root)


def test_p710_duplicate_json_key_rejected(tmp_path):
    root, index, _ = fixture_capture(tmp_path)
    index.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(CaptureEpochError, match="Duplicate JSON"):
        assess(capture_index=index, capture_root=root)


@pytest.mark.parametrize("unsafe", [
    "../traverse.bin", "/tmp/secret.bin", "C:/sensitive.bin", "a//b.bin",
    "a/../b.bin", "./x.bin", "folder\\file.bin", "account_number_123.bin",
])
def test_p710_unsafe_artifact_paths_rejected(tmp_path, unsafe):
    root, index, doc = fixture_capture(tmp_path)
    mutate(index, doc, 1, relative_path=unsafe)
    with pytest.raises(CaptureEpochError, match="Unsafe or sensitive"):
        assess(capture_index=index, capture_root=root)


def test_p710_symlink_evidence_rejected_if_supported(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    link = root / "originals" / "pointing.bin"
    try:
        link.symlink_to(root / "originals" / "tick_01.bin")
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation unavailable")
    mutate(index, doc, 1, relative_path="originals/pointing.bin")
    with pytest.raises(CaptureEpochError, match="Symlink"):
        assess(capture_index=index, capture_root=root)


def test_p710_root_without_capture_index_rejected(tmp_path):
    with pytest.raises(CaptureEpochError, match="requires current index"):
        assess(capture_root=tmp_path)


def test_p710_capture_index_without_root_rejected(tmp_path):
    root, index, _ = fixture_capture(tmp_path)
    with pytest.raises(CaptureEpochError, match="root required"):
        assess(capture_index=index)


# P7-11: proposed future epoch, fully distinct and never authorized.

def test_p711_no_charter_cannot_create_epoch():
    charter = assess()["future_epoch_charter"]
    assert charter["status"] == "NOT_SUPPLIED"
    assert not charter["collector_start_permitted"]


def test_p711_valid_future_charter_is_still_unapproved(tmp_path):
    file, _ = fixture_charter(tmp_path)
    report = assess(charter_file=file)
    charter = report["future_epoch_charter"]
    assert charter["status"] == "LOCAL_FUTURE_EPOCH_PROPOSAL_UNAPPROVED"
    assert charter["future_epoch_distinct"] is True
    assert charter["required_independent_gates"] == list(GATES)
    assert charter["required_stop_conditions"] == list(STOPS)
    assert not charter["operational_approval"]
    assert report["final_action"] == "HOLD"
    assert not report["forward_epoch_created"]


def test_p711_future_charter_deterministic_digest(tmp_path):
    file, _ = fixture_charter(tmp_path)
    first = inspect_future_epoch_charter(charter_file=file, as_of=AS_OF)
    second = inspect_future_epoch_charter(charter_file=file, as_of=AS_OF)
    assert first == second
    assert len(first["proposal_digest"]) == 64


@pytest.mark.parametrize("field,value", [
    ("proposed_epoch_id", EPOCH),
    ("proposed_epoch_id", "0" * 64),
    ("historical_epoch_id", "f" * 64),
    ("proposed_start_at", "2026-10-30T10:00:00Z"),
    ("proposed_end_at", "2026-11-30T09:00:00Z"),
    ("research_scope", "LIVE_EXECUTION"),
    ("isolation_mode", "ALLOW_COLLECTOR_RESTART"),
    ("max_clock_uncertainty_ms", "1001"),
    ("max_clock_uncertainty_ms", "-1"),
    ("maximum_capture_gap_seconds", True),
    ("maximum_capture_gap_seconds", 1000),
    ("retention_days", 1),
    ("retention_days", 99999),
    ("approval_signature_claims", ["APPROVED"]),
    ("required_stop_conditions", ["OPERATOR_STOP"]),
    ("required_approval_roles", ["SYSTEM_OPERATOR"]),
    ("required_external_gates", ["G01", "G02"]),
    ("expected_evidence_packages", []),
    ("provider_claims", ["account_number_1"]),
    ("provider_claims", ["same", "same"]),
])
def test_p711_unsafe_or_insufficient_charter_refused(tmp_path, field, value):
    file, doc = fixture_charter(tmp_path)
    doc[field] = value
    save(file, doc)
    with pytest.raises(CaptureEpochError):
        assess(charter_file=file)


def test_p711_charter_extra_approval_field_rejected(tmp_path):
    file, doc = fixture_charter(tmp_path)
    doc["approved"] = True
    save(file, doc)
    with pytest.raises(CaptureEpochError, match="Strict schema"):
        assess(charter_file=file)


def test_p711_charter_with_changed_policy_rejected(tmp_path):
    file, _ = fixture_charter(tmp_path)
    broken = tmp_path / "not_original_policy.json"
    broken.write_bytes(POLICY.read_bytes() + b" ")
    with pytest.raises(CaptureEpochError, match="SHA-256 mismatch"):
        assess(charter_file=file, policy_file=broken)


# P7-12: synthetic-only fail-closed recovery and report integrity.

def test_p712_recovery_scenarios_all_hold():
    scenarios = offline_recovery_matrix()
    assert {s["fault"] for s in scenarios} == set(FAULTS)
    assert len(scenarios) == 6
    assert all(s["expected_response"] == "QUARANTINE_AND_HOLD" for s in scenarios)
    assert all(s["simulated_only"] and not s["collector_restart_authorized"] for s in scenarios)


def test_p712_recovery_not_verified_by_simulated_matrix():
    recovery = assess()["offline_recovery_preflight"]
    assert recovery["status"] == "DESIGN_ONLY_NOT_TESTED_ON_COLLECTOR"
    assert not recovery["actual_collector_recovery_verified"]
    assert not recovery["atomic_write_behavior_verified"]
    assert not recovery["external_storage_recovery_verified"]
    assert not recovery["prospective_session_authorized"]


def test_p712_record_and_charter_cannot_promote_proof(tmp_path):
    root, index, _ = fixture_capture(tmp_path)
    file, _ = fixture_charter(tmp_path)
    report = assess(capture_index=index, capture_root=root, charter_file=file)
    assert report["external_gates_independently_verified"] == 0
    assert report["source_families_independently_verified"] == 0
    assert not report["G03_independently_verified"]
    assert not report["G04_independently_verified"]
    assert report["research_operational_readiness"] == "BLOCKED_EXTERNAL_EVIDENCE"
    assert report["final_action"] == "HOLD"


def test_p712_no_operational_side_effects(tmp_path):
    root, index, _ = fixture_capture(tmp_path)
    report = assess(capture_index=index, capture_root=root)
    for key in (
        "files_written", "historical_files_modified", "remote_queried", "network_accessed",
        "broker_connected", "collector_started", "council_invoked", "training_authorized",
        "settlement_invoked", "paper_orders_authorized", "live_orders_authorized",
        "forward_epoch_created", "operator_approval_granted",
    ):
        assert report[key] is False
    assert report["read_only"] is True


def test_p712_deterministic_report_for_same_evidence_and_cutoff(tmp_path):
    root, index, _ = fixture_capture(tmp_path)
    file, _ = fixture_charter(tmp_path)
    first = assess(capture_index=index, capture_root=root, charter_file=file)
    second = assess(capture_index=index, capture_root=root, charter_file=file)
    assert first == second
    assert first["report_sha256"] == _digest({k: v for k, v in first.items() if k != "report_sha256"})


def test_p712_cli_readonly_no_evidence_json(capsys):
    code = run(["--read-only-capture", "--read-only-proposal", "--epoch-id", EPOCH,
                "--as-of", AS_OF, "--policy-file", str(POLICY), "--json"])
    assert code == 0
    obj = json.loads(capsys.readouterr().out)
    assert obj["capture_inventory"]["status"] == "NOT_SUPPLIED"
    assert obj["future_epoch_charter"]["status"] == "NOT_SUPPLIED"
    assert obj["final_action"] == "HOLD"


def test_p712_cli_readonly_without_json(capsys):
    code = run(["--read-only-capture", "--read-only-proposal", "--epoch-id", EPOCH,
                "--as-of", AS_OF, "--policy-file", str(POLICY)])
    assert code == 0
    output = capsys.readouterr().out
    assert "NO REBIND" in output and "FINAL=HOLD" in output


def test_p712_cli_readonly_flags_required():
    with pytest.raises(SystemExit) as exc:
        run(["--epoch-id", EPOCH, "--as-of", AS_OF])
    assert exc.value.code == 2


def test_p712_cli_future_cutoff_rejected(capsys):
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    result = run(["--read-only-capture", "--read-only-proposal", "--epoch-id", EPOCH,
                  "--as-of", future, "--policy-file", str(POLICY)])
    assert result == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_p712_wrong_epoch_rebind_rejected():
    with pytest.raises(CaptureEpochError, match="rebinding refused"):
        assess_stage7_capture_epoch(epoch_id="f" * 64, as_of=AS_OF, policy_file=POLICY)


@pytest.mark.parametrize("time", [
    "2026-10-30T12:00:00", "2026-10-30T15:00:00+03:00", "2026-10-30T12:00:60Z",
    "not-a-date", "2026-11-31T12:00:00Z",
])
def test_p712_utc_requires_valid_zero_offset(time):
    with pytest.raises(CaptureEpochError):
        _utc(time)


def test_p712_previous_snapshot_without_root_refused(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    old = tmp_path / "old.json"
    save(old, doc)
    with pytest.raises(CaptureEpochError, match="requires its evidence root"):
        assess(capture_index=index, capture_root=root, previous_index=old)


def test_p712_empty_capture_snapshot_allowed_but_not_trusted(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    doc["records"] = []
    save(index, doc)
    report = assess(capture_index=index, capture_root=root)
    assert report["capture_inventory"]["record_count"] == 0
    assert report["final_action"] == "HOLD"
    assert report["G03_independently_verified"] is False


def test_p710_capture_record_invalid_kind_type_is_rejected(tmp_path):
    root, index, doc = fixture_capture(tmp_path)
    mutate(index, doc, 1, kind=["CAPTURED_TICK"])
    with pytest.raises(CaptureEpochError, match="Unsupported capture artifact kind"):
        assess(capture_index=index, capture_root=root)


def test_p711_charter_unhashable_provider_claim_rejected(tmp_path):
    file, doc = fixture_charter(tmp_path)
    doc["provider_claims"] = [{"spoof": "broker"}]
    save(file, doc)
    with pytest.raises(CaptureEpochError, match="provider claims"):
        assess(charter_file=file)


def test_p711_charter_unhashable_stop_contract_rejected(tmp_path):
    file, doc = fixture_charter(tmp_path)
    doc["required_stop_conditions"][0] = {"fake": "approval"}
    save(file, doc)
    with pytest.raises(CaptureEpochError, match="safety contract"):
        assess(charter_file=file)

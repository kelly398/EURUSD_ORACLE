
"""P7-21..24 synthetic gate board, risk/recovery and no-promotion handover.

Only pytest tmp_path files. No broker, network, collector, Council, model,
training, settlement or market orders. No historical policy mutation.
"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

import backend.runtime.stage7_evidence_board_handover as mod
from backend.runtime.stage7_evidence_board_handover import (
    EPOCH, POLICY_SHA256, BRANCH, GATES, FUTURE_GATES, STOPS, FAULTS,
    WINDOWS_TEST_ALLOWLIST, PRIOR_REPORT_DIGESTS,
    EvidenceBoardError, _digest, _utc, _git_review, _git_run,
    assess_stage7_handover, run,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "docs/clock_profiles/hfm_forward_20261027.json"
ASOF = "2026-10-30T12:00:00Z"
ISSUED = "2026-10-30T10:00:00Z"
AVAILABLE = "2026-10-30T10:01:00Z"
REVIEWED = "2026-10-30T10:02:00Z"
REVOKED = "2026-10-30T10:03:00Z"


def save(path, document):
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def claim(gate="G01", evidence="gate_01", state="APPROVED"):
    return {
        "gate_id": gate, "evidence_id": evidence,
        "issuer_claim": "UNTRUSTED_PROVIDER_A",
        "reviewer_claim": "UNTRUSTED_REVIEWER_B",
        "scope_claim": GATES[gate][1], "claim_state": state,
        "issued_at": ISSUED, "first_available_at": AVAILABLE,
        "reviewed_at_claim": REVIEWED if state in ("APPROVED", "FAILED", "REVOKED") else None,
        "revoked_at_claim": REVOKED if state == "REVOKED" else None,
        "signature_sha256_claim": "a" * 64,
        "independent_receipt_claim": "LOCAL_UNVERIFIED_RECEIPT_A",
        "artifact_relative_path": "claims/" + evidence + ".dat",
        "artifact_sha256": "0" * 64,
    }


def fixture_gates(tmp_path, *records):
    root = tmp_path / "UNATTESTED_GATE_EVIDENCE"
    (root / "claims").mkdir(parents=True)
    doc = {
        "schema_version": 1, "historical_epoch_id": EPOCH,
        "packet_id": "SYNTHETIC_P7_GATE_INDEX", "as_of": ASOF,
        "records": list(records),
    }
    for entry in records:
        body = ("FAKE_LOCAL_BYTES_" + entry["evidence_id"]).encode("ascii")
        (root / entry["artifact_relative_path"]).write_bytes(body)
        entry["artifact_sha256"] = sha256(body).hexdigest()
    index = tmp_path / "gate_index.json"
    save(index, doc)
    return root, index, doc


def fixture_risk(tmp_path):
    plan = {
        "schema_version": 1, "historical_epoch_id": EPOCH,
        "plan_id": "SYNTHETIC_RISK_PLAN", "as_of": ASOF,
        "risk_policy_version_claim": "UNAPPROVED_POLICY_01",
        "clock_uncertainty_limit_ms": "500",
        "max_source_gap_seconds": 30,
        "max_quote_age_seconds": 120,
        "retention_days": 365,
        "data_isolation": {key: False for key in mod.ISOLATION_FIELDS},
        "stop_conditions": list(STOPS),
        "recovery_scenarios": [
            {"fault": name, "expected_action": "QUARANTINE_HOLD_ESCALATE",
             "simulation_result": "DESIGN_ONLY",
             "escalation_role": "RISK_OWNER",
             "restart_authorized": False}
            for name in FAULTS
        ],
        "restart_policy": {
            key: key != "automatic_restart" for key in mod.RESTART_FIELDS
        },
        "operator_approval_claim": False,
        "independent_risk_signoff_claim": False,
    }
    path = tmp_path / "risk_plan.json"
    save(path, plan)
    return path, plan


def fixture_ledger(tmp_path):
    doc = {
        "schema_version": 1, "historical_epoch_id": EPOCH,
        "package_id": "SYNTHETIC_HANDOVER_LEDGER",
        "as_of": ASOF, "branch": BRANCH,
        "batch_commits": [
            {"batch_id": batch, "commit_id": prefix}
            for batch, prefix, _ in mod.STAGE7_BATCHES[:5]
        ],
        "windows_suite": {
            "reported_at": "2026-10-30T11:00:00Z",
            "passed": 407, "skipped": 5,
            "allowlist": list(WINDOWS_TEST_ALLOWLIST[:5]),
            "operator_reported": True,
        },
        "report_receipts": [
            {"batch_id": batch, "report_sha256": digest,
             "recorded_at": "2026-10-30T11:00:00Z"}
            for batch, digest in PRIOR_REPORT_DIGESTS.items()
        ],
        "evidence_receipts": [
            {"gate_id": "G01", "evidence_id": "unverified_01", "artifact_sha256": "a" * 64}
        ],
        "atlas_append_only_ticket": "PENDING_ATLAS_APPEND",
        "operator_signoff_claim": False,
        "stage8_authorization_claim": False,
    }
    path = tmp_path / "proposed_ledger.json"
    save(path, doc)
    return path, doc


def assess(*, gate_index=None, evidence_root=None, risk_plan=None,
           handover_ledger=None, git_root=None, policy_file=POLICY, as_of=ASOF):
    return assess_stage7_handover(
        epoch_id=EPOCH, as_of=as_of, policy_file=policy_file,
        gate_index=gate_index, evidence_root=evidence_root,
        risk_plan=risk_plan, handover_ledger=handover_ledger, git_root=git_root,
    )


def status(report, gate):
    return next(g for g in report["independent_gate_board"]["gates"] if g["gate_id"] == gate)


def change(path, obj, row, **changes):
    obj["records"][row].update(changes)
    save(path, obj)


# P7-21: evidence IDs, issuers, reviewers, scope, conflicts, revocation.

def test_p721_empty_board_seven_gates_and_no_approvals():
    report = assess()
    board = report["independent_gate_board"]
    assert len(board["gates"]) == 7
    assert [x["gate_id"] for x in board["gates"]] == list(GATES)
    assert all(x["local_status"] == "NOT_SUPPLIED" for x in board["gates"])
    assert all(x["independently_verified"] is False for x in board["gates"])
    assert board["independently_verified_gates_G01_G11"] == 0
    assert set(board["future_gates_G08_G11"]) == set(FUTURE_GATES)


def test_p721_original_policy_pin_not_broker_attestation():
    r = assess()["historical_clock_policy"]
    assert r["sha256"] == POLICY_SHA256
    assert r["original_raw_bytes_matched"]
    assert not r["broker_utc_independently_attested"]
    assert not r["historical_epoch_rebound"]


def test_p721_signed_local_approved_claim_never_passes_gate(tmp_path):
    root, index, _ = fixture_gates(tmp_path, claim())
    entry = status(assess(gate_index=index, evidence_root=root), "G01")
    assert entry["local_status"] == "LOCAL_APPROVAL_CLAIM_UNATTESTED"
    assert entry["evidence_ids"] == ["gate_01"]
    assert entry["claimed_review_dates"] == ["2026-10-30T10:02:00+00:00"]
    assert entry["issuers_claimed"] == ["UNTRUSTED_PROVIDER_A"]
    assert entry["reviewers_claimed"] == ["UNTRUSTED_REVIEWER_B"]
    assert entry["independently_verified"] is False
    assert not entry["authorized_to_admit_sources"]


@pytest.mark.parametrize("gate", list(GATES))
def test_p721_any_gate_self_approval_cannot_promote(tmp_path, gate):
    root, index, _ = fixture_gates(tmp_path, claim(gate, "proof_" + gate))
    report = assess(gate_index=index, evidence_root=root)
    assert status(report, gate)["local_status"] == "LOCAL_APPROVAL_CLAIM_UNATTESTED"
    assert report["independent_gates_verified"] == 0
    assert report["source_readiness_decision"]["source_ingestion_allowed"] is False


@pytest.mark.parametrize("state,expected", [
    ("PENDING", "PENDING_LOCAL_REVIEW_CLAIM"),
    ("FAILED", "LOCAL_FAILED_CLAIM"),
    ("REVOKED", "LOCAL_REVOCATION_CLAIM_QUARANTINED"),
])
def test_p721_pending_failed_revoked_kept_distinct(tmp_path, state, expected):
    root, index, _ = fixture_gates(tmp_path, claim(state=state))
    report = assess(gate_index=index, evidence_root=root)
    assert status(report, "G01")["local_status"] == expected
    assert not status(report, "G01")["independently_verified"]


def test_p721_revocation_claim_persists(tmp_path):
    root, index, _ = fixture_gates(tmp_path, claim(state="REVOKED"))
    g = status(assess(gate_index=index, evidence_root=root), "G01")
    assert g["revocation_claim_present"] is True
    assert not g["authorized_to_admit_sources"]


def test_p721_conflicting_evidence_quarantined(tmp_path):
    a = claim(evidence="evidence_a", state="APPROVED")
    b = claim(evidence="evidence_b", state="FAILED")
    root, index, _ = fixture_gates(tmp_path, a, b)
    g = status(assess(gate_index=index, evidence_root=root), "G01")
    assert g["local_status"] == "QUARANTINED_LOCAL_CLAIM"
    assert "CONFLICTING_LOCAL_GATE_CLAIMS" in g["findings"]


def test_p721_same_issuer_reviewer_quarantined(tmp_path):
    root, index, doc = fixture_gates(tmp_path, claim())
    change(index, doc, 0, reviewer_claim="UNTRUSTED_PROVIDER_A")
    g = status(assess(gate_index=index, evidence_root=root), "G01")
    assert "ISSUER_AND_REVIEWER_NOT_INDEPENDENT" in g["findings"]


def test_p721_wrong_scope_quarantined(tmp_path):
    root, index, doc = fixture_gates(tmp_path, claim())
    change(index, doc, 0, scope_claim="OTHER_EPOCH")
    assert "GATE_SCOPE_MISMATCH" in status(
        assess(gate_index=index, evidence_root=root), "G01"
    )["findings"]


def test_p721_missing_signature_claim_cannot_pass(tmp_path):
    root, index, doc = fixture_gates(tmp_path, claim())
    change(index, doc, 0, signature_sha256_claim=None)
    assert "APPROVAL_CLAIM_LACKS_SIGNED_RECEIPT_METADATA" in status(
        assess(gate_index=index, evidence_root=root), "G01"
    )["findings"]


def test_p721_missing_reviewer_quarantined(tmp_path):
    root, index, doc = fixture_gates(tmp_path, claim())
    change(index, doc, 0, reviewer_claim=None)
    assert "REVIEWER_NOT_DECLARED" in status(
        assess(gate_index=index, evidence_root=root), "G01"
    )["findings"]


def test_p721_duplicate_evidence_id_rejected(tmp_path):
    a, b = claim(evidence="same"), claim("G02", "same")
    root, index, _ = fixture_gates(tmp_path, a, b)
    with pytest.raises(EvidenceBoardError, match="Duplicate gate evidence"):
        assess(gate_index=index, evidence_root=root)


def test_p721_artifact_tamper_rejected(tmp_path):
    root, index, _ = fixture_gates(tmp_path, claim())
    (root / "claims/gate_01.dat").write_bytes(b"CHANGED")
    with pytest.raises(EvidenceBoardError, match="SHA-256 mismatch"):
        assess(gate_index=index, evidence_root=root)


def test_p721_rehashed_tamper_still_not_independent(tmp_path):
    root, index, doc = fixture_gates(tmp_path, claim())
    (root / "claims/gate_01.dat").write_bytes(b"CHANGED")
    change(index, doc, 0, artifact_sha256=sha256(b"CHANGED").hexdigest())
    assert status(assess(gate_index=index, evidence_root=root), "G01")[
        "independently_verified"
    ] is False


def test_p721_gate_outside_stage7_scope_rejected(tmp_path):
    root, index, doc = fixture_gates(tmp_path, claim())
    change(index, doc, 0, gate_id="G08")
    with pytest.raises(EvidenceBoardError, match="G01-G07"):
        assess(gate_index=index, evidence_root=root)


@pytest.mark.parametrize("field,value", [
    ("reviewed_at_claim", "2026-10-30T10:00:00Z"),
    ("first_available_at", "2026-10-30T09:00:00Z"),
    ("issued_at", "2026-11-01T00:00:00Z"),
])
def test_p721_invalid_gate_claim_chronology_refused(tmp_path, field, value):
    root, index, doc = fixture_gates(tmp_path, claim())
    change(index, doc, 0, **{field: value})
    with pytest.raises(EvidenceBoardError):
        assess(gate_index=index, evidence_root=root)


def test_p721_revocation_before_review_quarantined(tmp_path):
    root, index, doc = fixture_gates(tmp_path, claim(state="REVOKED"))
    change(index, doc, 0, revoked_at_claim="2026-10-30T10:01:30Z")
    assert "REVOCATION_PRECEDES_REVIEW" in status(
        assess(gate_index=index, evidence_root=root), "G01"
    )["findings"]


@pytest.mark.parametrize("unsafe", [
    "../outside.bin", "/tmp/secret.bin", "C:/private.bin", "a//b", "a/../b",
    "./x.bin", "folder\\file", "account_number_7.bin", "user@example.com",
])
def test_p721_unsafe_evidence_path_refused(tmp_path, unsafe):
    root, index, doc = fixture_gates(tmp_path, claim())
    change(index, doc, 0, artifact_relative_path=unsafe)
    with pytest.raises(EvidenceBoardError, match="Unsafe or sensitive"):
        assess(gate_index=index, evidence_root=root)


def test_p721_symlink_evidence_rejected_if_supported(tmp_path):
    root, index, doc = fixture_gates(tmp_path, claim())
    link = root / "claims" / "symlink.dat"
    try:
        link.symlink_to(root / "claims/gate_01.dat")
    except (OSError, NotImplementedError):
        pytest.skip("Windows symlink privileges unavailable")
    change(index, doc, 0, artifact_relative_path="claims/symlink.dat")
    with pytest.raises(EvidenceBoardError, match="Symlink"):
        assess(gate_index=index, evidence_root=root)


def test_p721_claim_index_wrong_epoch_refused(tmp_path):
    root, index, doc = fixture_gates(tmp_path, claim())
    doc["historical_epoch_id"] = "f" * 64
    save(index, doc)
    with pytest.raises(EvidenceBoardError, match="historical epoch"):
        assess(gate_index=index, evidence_root=root)


# P7-22: safe-stop, risk limits, isolation, recovery, restart and escalation.

def test_p722_absent_risk_plan_not_accepted():
    risk = assess()["risk_and_recovery_preflight"]
    assert risk["status"] == "NOT_SUPPLIED"
    assert not risk["operator_approval"]
    assert not risk["restart_authorized"]


def test_p722_complete_local_design_not_real_recovery_proof(tmp_path):
    path, doc = fixture_risk(tmp_path)
    risk = assess(risk_plan=path)["risk_and_recovery_preflight"]
    assert risk["status"] == "LOCAL_RISK_PLAN_CANDIDATE_UNATTESTED"
    assert risk["design_scenarios_reported"] == 8
    assert risk["actual_recovery_tests_verified"] == 0
    assert risk["safe_stop_conditions_proposed"] == sorted(STOPS)
    assert not risk["operator_approval"]
    assert not risk["restart_authorized"]


@pytest.mark.parametrize("field,value,reason", [
    ("clock_uncertainty_limit_ms", "1500", "PROPOSED_CLOCK_UNCERTAINTY_EXCEEDS_DESIGN_CEILING"),
    ("max_source_gap_seconds", 400, "PROPOSED_RISK_OR_RETENTION_LIMITS_OUTSIDE_DESIGN_BOUNDS"),
    ("max_quote_age_seconds", 400, "PROPOSED_RISK_OR_RETENTION_LIMITS_OUTSIDE_DESIGN_BOUNDS"),
    ("retention_days", 1, "PROPOSED_RISK_OR_RETENTION_LIMITS_OUTSIDE_DESIGN_BOUNDS"),
    ("stop_conditions", ["OPERATOR_STOP"], "MANDATORY_SAFE_STOP_CONTRACT_INCOMPLETE"),
    ("recovery_scenarios", [], "RECOVERY_FAULT_MATRIX_INCOMPLETE"),
    ("operator_approval_claim", True, "SELF_ASSERTED_OPERATOR_OR_RISK_APPROVAL_NOT_ADMISSIBLE"),
    ("independent_risk_signoff_claim", True, "SELF_ASSERTED_OPERATOR_OR_RISK_APPROVAL_NOT_ADMISSIBLE"),
])
def test_p722_unsafe_plan_quarantined(tmp_path, field, value, reason):
    path, doc = fixture_risk(tmp_path)
    doc[field] = value
    save(path, doc)
    risk = assess(risk_plan=path)["risk_and_recovery_preflight"]
    assert risk["status"] == "LOCAL_RISK_PLAN_QUARANTINED"
    assert reason in risk["issues"]


@pytest.mark.parametrize("isolation_field", mod.ISOLATION_FIELDS)
def test_p722_isolation_break_is_quarantined(tmp_path, isolation_field):
    path, doc = fixture_risk(tmp_path)
    doc["data_isolation"][isolation_field] = True
    save(path, doc)
    assert "NONISOLATED_RESEARCH_PLAN_REJECTED" in assess(risk_plan=path)[
        "risk_and_recovery_preflight"
    ]["issues"]


@pytest.mark.parametrize("restart_field", mod.RESTART_FIELDS)
def test_p722_restart_guard_change_is_quarantined(tmp_path, restart_field):
    path, doc = fixture_risk(tmp_path)
    doc["restart_policy"][restart_field] = restart_field == "automatic_restart"
    save(path, doc)
    assert "UNSAFE_AUTOMATIC_OR_UNAPPROVED_RESTART_PATH" in assess(risk_plan=path)[
        "risk_and_recovery_preflight"
    ]["issues"]


def test_p722_fault_requires_quarantine_hold_and_escalation(tmp_path):
    path, doc = fixture_risk(tmp_path)
    doc["recovery_scenarios"][0]["expected_action"] = "AUTO_RESTART"
    save(path, doc)
    assert "RECOVERY_FAULT_NOT_FAIL_CLOSED" in assess(risk_plan=path)[
        "risk_and_recovery_preflight"
    ]["issues"]


def test_p722_simulated_failure_not_passing(tmp_path):
    path, doc = fixture_risk(tmp_path)
    doc["recovery_scenarios"][0]["simulation_result"] = "SIMULATED_FAIL"
    save(path, doc)
    assert "SIMULATED_RECOVERY_FAULT_FAILED" in assess(risk_plan=path)[
        "risk_and_recovery_preflight"
    ]["issues"]


def test_p722_duplicate_fault_refused(tmp_path):
    path, doc = fixture_risk(tmp_path)
    doc["recovery_scenarios"][1]["fault"] = FAULTS[0]
    save(path, doc)
    with pytest.raises(EvidenceBoardError, match="Duplicate recovery"):
        assess(risk_plan=path)


def test_p722_unchecked_safe_stop_boolean_refused(tmp_path):
    path, doc = fixture_risk(tmp_path)
    doc["data_isolation"]["broker_connected"] = "false"
    save(path, doc)
    with pytest.raises(EvidenceBoardError, match="boolean"):
        assess(risk_plan=path)


def test_p722_plan_epoch_rebinding_refused(tmp_path):
    path, doc = fixture_risk(tmp_path)
    doc["historical_epoch_id"] = "f" * 64
    save(path, doc)
    with pytest.raises(EvidenceBoardError, match="historical epoch"):
        assess(risk_plan=path)


# P7-23: separate engineering code integrity, evidence, families, model, operator.

def test_p723_readiness_sections_separate_and_all_hold():
    result = assess()["source_readiness_decision"]
    assert result["independent_evidence"] == "BLOCKED_NOT_ATTESTED"
    assert result["clock_confidence"] == "BLOCKED_NO_INDEPENDENT_UTC_PROOF"
    assert result["source_family_coverage"]["contract_count"] == 13
    assert result["source_family_coverage"]["independently_admitted"] == 0
    assert result["model_and_feature_ancestry"] == "UNATTESTED_NO_COUNCIL_MODEL_ADMISSION"
    assert result["operator_approval"] == "NOT_GRANTED"
    assert result["final_action"] == "HOLD"


def test_p723_all_seven_local_approved_claims_still_blocked(tmp_path):
    rows = [claim(g, "local_" + g) for g in GATES]
    root, index, _ = fixture_gates(tmp_path, *rows)
    result = assess(gate_index=index, evidence_root=root)
    assert all(g["local_status"] == "LOCAL_APPROVAL_CLAIM_UNATTESTED"
               for g in result["independent_gate_board"]["gates"])
    assert result["independent_gates_verified"] == 0
    assert result["independent_source_families_verified"] == 0
    assert result["source_readiness_decision"]["stage8_external_readiness"] == "BLOCKED"
    assert result["final_action"] == "HOLD"


def test_p723_g07_risk_plan_and_self_approval_cannot_clear(tmp_path):
    risk, _ = fixture_risk(tmp_path)
    root, index, _ = fixture_gates(tmp_path, claim("G07", "gate07"))
    result = assess(gate_index=index, evidence_root=root, risk_plan=risk)
    assert status(result, "G07")["independently_verified"] is False
    assert result["source_readiness_decision"]["research_council_dispatch_allowed"] is False


def test_p723_policy_tamper_refused(tmp_path):
    wrong = tmp_path / "changed_policy.json"
    wrong.write_bytes(POLICY.read_bytes() + b" ")
    with pytest.raises(EvidenceBoardError, match="policy SHA-256 mismatch"):
        assess(policy_file=wrong)


def test_p723_wrong_epoch_refused():
    with pytest.raises(EvidenceBoardError, match="rebinding refused"):
        assess_stage7_handover(epoch_id="f" * 64, as_of=ASOF, policy_file=POLICY)


def test_p723_no_side_effects_with_claims(tmp_path):
    root, index, _ = fixture_gates(tmp_path, claim())
    plan, _ = fixture_risk(tmp_path)
    ledger, _ = fixture_ledger(tmp_path)
    result = assess(gate_index=index, evidence_root=root, risk_plan=plan, handover_ledger=ledger)
    for key in (
        "remote_proof_queried", "files_written", "historical_files_modified",
        "network_accessed", "broker_connected", "collector_started", "council_invoked",
        "model_loaded", "training_authorized", "settlement_invoked", "paper_orders_authorized",
        "live_orders_authorized", "forward_epoch_created", "operator_approval_granted",
    ):
        assert result[key] is False
    assert result["read_only"]
    assert result["research_operational_readiness"] == "BLOCKED_EXTERNAL_EVIDENCE"


# P7-24: append-only Atlas input, Windows allowlist, Git continuity candidates.

def test_p724_handover_lists_six_batches_and_twelve_paths():
    items = assess()["stage7_handover"]["batch_file_allowlist"]
    assert len(items) == 6
    assert {x["batch_id"] for x in items} == {"B1", "B2", "B3", "B4", "B5", "B6"}
    assert len({x["source_path"] for x in items}) == 6
    assert len({x["test_path"] for x in items}) == 6
    assert len(assess()["stage7_handover"]["windows_test_allowlist"]) == 6


def test_p724_progress_only_twenty_committed_until_batch6_git():
    handover = assess()["stage7_handover"]
    assert handover["operator_reported_prior_committed"] == 20
    assert handover["batch6_engineering_checkpoints_pending"] == [
        "P7-21", "P7-22", "P7-23", "P7-24"
    ]
    assert handover["atlas_written_by_this_tool"] is False
    assert handover["stage8_authorization"] is False


def test_p724_prior_operator_report_digests_preserved():
    handover = assess()["stage7_handover"]
    assert handover["previously_reported_snapshot_digests"] == PRIOR_REPORT_DIGESTS
    assert handover["prior_operator_reported_windows_regression"]["passed"] == 407
    assert handover["prior_operator_reported_windows_regression"]["skipped"] == 5


def test_p724_proposed_ledger_is_not_written_or_approved(tmp_path):
    path, _ = fixture_ledger(tmp_path)
    report = assess(handover_ledger=path)
    ledger = report["stage7_handover"]["optional_local_atlas_ledger"]
    assert ledger["status"] == "LOCAL_ATLAS_UPDATE_CANDIDATE_NOT_WRITTEN"
    assert len(ledger["batch_commit_claims"]) == 5
    assert len(ledger["report_receipts"]) == len(PRIOR_REPORT_DIGESTS)
    assert ledger["windows_test_claim"]["status"] == "OPERATOR_REPORTED_NOT_INDEPENDENTLY_REPRODUCED"
    assert not ledger["atlas_updated"]
    assert not ledger["stage8_authorized"]


@pytest.mark.parametrize("name", ["operator_signoff_claim", "stage8_authorization_claim"])
def test_p724_unsigned_ledger_cannot_assert_approval(tmp_path, name):
    path, doc = fixture_ledger(tmp_path)
    doc[name] = True
    save(path, doc)
    with pytest.raises(EvidenceBoardError, match="cannot assert operational approval"):
        assess(handover_ledger=path)


def test_p724_ledger_wrong_anchor_refused(tmp_path):
    path, doc = fixture_ledger(tmp_path)
    doc["batch_commits"][0]["commit_id"] = "f" * 40
    save(path, doc)
    with pytest.raises(EvidenceBoardError, match="contradicts prior Windows anchor"):
        assess(handover_ledger=path)


def test_p724_windows_allowlist_must_be_canonical(tmp_path):
    path, doc = fixture_ledger(tmp_path)
    doc["windows_suite"]["allowlist"] = ["backend/runtime/test_invented.py"]
    save(path, doc)
    with pytest.raises(EvidenceBoardError, match="allowlist mismatch"):
        assess(handover_ledger=path)


def test_p724_duplicate_batch_ledger_refused(tmp_path):
    path, doc = fixture_ledger(tmp_path)
    doc["batch_commits"].append(dict(doc["batch_commits"][0]))
    save(path, doc)
    with pytest.raises(EvidenceBoardError, match="duplicate batch"):
        assess(handover_ledger=path)


def test_p724_local_git_not_requested_does_not_claim_verified():
    review = assess()["stage7_handover"]["local_git_review"]
    assert review["status"] == "NOT_REQUESTED"
    assert review["remote_server_independently_queried"] is False
    assert review["remote_custody_attested"] is False


def fake_git(monkeypatch, *, batch6=False, dirty=False, remote_mismatch=False):
    prefixes = {p: (p + "0" * (40 - len(p))) for _, p, _ in mod.STAGE7_BATCHES[:5]}
    head = ("d" * 40) if batch6 else prefixes["f6327c7"]
    seen = []

    def fake(root, *args, allowed_exit=(0,)):
        seen.append(args)
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return BRANCH, 0
        if args == ("rev-parse", "HEAD"):
            return head, 0
        if args == ("rev-parse", "refs/remotes/origin/" + BRANCH):
            return ("e" * 40 if remote_mismatch else head), 0
        if args == ("status", "--porcelain", "--untracked-files=no"):
            return (" M tracked.py" if dirty else ""), 0
        if args[:2] == ("rev-parse", "--verify"):
            short = args[2].split("^")[0]
            return prefixes[short], 0
        if args[:2] == ("merge-base", "--is-ancestor"):
            return "", 0
        if args[:3] == ("show", "--format=", "--name-only"):
            commit = args[-1]
            if commit == head and batch6:
                name = mod.STAGE7_BATCHES[-1][2]
            else:
                short = next(p for p, full in prefixes.items() if full == commit)
                name = next(n for _, p, n in mod.STAGE7_BATCHES[:5] if p == short)
            return ("backend/runtime/" + name + ".py\nbackend/runtime/test_" + name + ".py"), 0
        raise AssertionError("Unexpected local Git subprocess " + repr(args))

    monkeypatch.setattr(mod, "_git_run", fake)
    return seen


def test_p724_local_five_commits_are_not_stage7_complete(monkeypatch, tmp_path):
    seen = fake_git(monkeypatch)
    git = _git_review(tmp_path)
    assert git["verified_batch_prefixes"] == ["B1", "B2", "B3", "B4", "B5"]
    assert git["status"] == "LOCAL_GIT_HANDOVER_NOT_COMPLETE"
    assert not git["batch6_commit_present"]
    assert not git["remote_server_independently_queried"]
    assert not any("fetch" in args or "push" in args for args in seen)


def test_p724_local_six_git_batches_still_not_external_attestation(monkeypatch, tmp_path):
    fake_git(monkeypatch, batch6=True)
    result = assess(git_root=tmp_path)
    review = result["stage7_handover"]["local_git_review"]
    assert review["status"] == "SIX_BATCHES_LOCALLY_PRESENT_UNATTESTED"
    assert review["batch6_commit_present"]
    assert result["source_readiness_decision"]["stage7_engineering_completion"] == (
        "LOCAL_SIX_COMMIT_CANDIDATE_PENDING_WINDOWS_CONFIRMATION"
    )
    assert result["independent_gates_verified"] == 0
    assert result["final_action"] == "HOLD"


@pytest.mark.parametrize("dirty,remote_mismatch", [(True, False), (False, True)])
def test_p724_git_dirty_or_tracking_drift_not_ready(monkeypatch, tmp_path, dirty, remote_mismatch):
    fake_git(monkeypatch, batch6=True, dirty=dirty, remote_mismatch=remote_mismatch)
    assert _git_review(tmp_path)["status"] != "SIX_BATCHES_LOCALLY_PRESENT_UNATTESTED"


def test_p724_actual_git_missing_repo_fail_closed(tmp_path):
    with pytest.raises(EvidenceBoardError, match="Local Git object inspection failed"):
        _git_review(tmp_path)


def test_p724_no_bundle_or_report_bytes_mutated_by_assessment(tmp_path):
    path, _ = fixture_ledger(tmp_path)
    baseline = sha256(path.read_bytes()).hexdigest()
    one = assess(handover_ledger=path)
    two = assess(handover_ledger=path)
    assert one == two
    assert sha256(path.read_bytes()).hexdigest() == baseline
    assert one["report_sha256"] == _digest({
        k: v for k, v in one.items() if k != "report_sha256"
    })


# CLI and strict parser behavior.

def test_p724_cli_no_packet_final_hold(capsys):
    exit_code = run(["--read-only-gates", "--read-only-handover", "--epoch-id", EPOCH,
                     "--as-of", ASOF, "--policy-file", str(POLICY)])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "G01-G07 independent verified: 0/7" in output
    assert "Stage 8: NOT_AUTHORIZED" in output
    assert "FINAL=HOLD" in output


def test_p724_cli_json_report(capsys):
    exit_code = run(["--read-only-gates", "--read-only-handover", "--epoch-id", EPOCH,
                     "--as-of", ASOF, "--policy-file", str(POLICY), "--json"])
    assert exit_code == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["historical_clock_policy"]["sha256"] == POLICY_SHA256
    assert len(doc["independent_gate_board"]["gates"]) == 7
    assert doc["stage7_handover"]["atlas_written_by_this_tool"] is False
    assert doc["final_action"] == "HOLD"


def test_p724_cli_requires_two_readonly_flags():
    with pytest.raises(SystemExit) as error:
        run(["--epoch-id", EPOCH, "--as-of", ASOF])
    assert error.value.code == 2


def test_p724_cli_future_cutoff_blocked(capsys):
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    code = run(["--read-only-gates", "--read-only-handover", "--epoch-id", EPOCH,
                "--as-of", tomorrow, "--policy-file", str(POLICY)])
    assert code == 1
    assert "BLOCKED" in capsys.readouterr().out


@pytest.mark.parametrize("value", [
    "2026-10-30T12:00:00", "2026-10-30T15:00:00+03:00",
    "2026-10-30T12:00:60Z", "2026-11-31T12:00:00Z", "not_utc",
])
def test_p724_strict_utc_zero_offset(value):
    with pytest.raises(EvidenceBoardError):
        _utc(value)


def test_p724_duplicate_json_key_rejected(tmp_path):
    root, index, _ = fixture_gates(tmp_path, claim())
    index.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(EvidenceBoardError, match="Duplicate JSON"):
        assess(gate_index=index, evidence_root=root)


def test_p724_gate_root_without_index_refused(tmp_path):
    with pytest.raises(EvidenceBoardError, match="root without index"):
        assess(evidence_root=tmp_path)


def test_p724_gate_index_without_root_refused(tmp_path):
    root, index, _ = fixture_gates(tmp_path, claim())
    with pytest.raises(EvidenceBoardError, match="root required"):
        assess(gate_index=index)


def test_p724_strict_no_extra_self_verification_field(tmp_path):
    root, index, doc = fixture_gates(tmp_path, claim())
    change(index, doc, 0, independent_verified=True)
    with pytest.raises(EvidenceBoardError, match="Strict schema"):
        assess(gate_index=index, evidence_root=root)


def test_p724_no_raw_gate_artifact_leak_to_report(tmp_path):
    root, index, _ = fixture_gates(tmp_path, claim())
    output = json.dumps(assess(gate_index=index, evidence_root=root))
    assert "FAKE_LOCAL_BYTES" not in output
    assert "claims/gate_01.dat" not in output
    assert "signature_sha256_claim" not in output

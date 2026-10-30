
"""P6-13..16 synthetic, offline-only prospective custody regression tests."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime.stage6_prospective_ledger_readiness import (
    HISTORICAL_EPOCH, KINDS, ProspectiveLedgerError, _digest, _utc,
    assess_stage6_prospective_ledger, inspect_candidate_ledger,
    inspect_learning_source_contracts, run,
)

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "backend/learning/contracts.py"
SETTLEMENT = ROOT / "backend/learning/settle_outcomes.py"
DECISION = "2026-10-27T15:03:00Z"
ASOF = "2026-10-29T10:00:00Z"
PID = "b" * 64
SCOPE = "synthetic_holdout_scope"
DUMMY_SHA = "a" * 64


def write(path, obj):
    path.write_text(json.dumps(obj, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def base_record(record_id, kind, received, **extras):
    return {
        "record_id": record_id, "kind": kind, "epoch_id": HISTORICAL_EPOCH,
        "claimed_issuer": "SYNTHETIC_NOT_AUTHENTICATED",
        "claimed_receipt_id": "unverified_" + record_id,
        "claimed_receipt_at": received, **extras,
    }


def holdout():
    return base_record(
        "holdout_1", "holdout_freeze", "2026-10-27T14:01:00Z",
        scope_id=SCOPE, freeze_at="2026-10-27T14:00:00Z",
        partition_sha256=DUMMY_SHA, evaluation_plan_sha256=DUMMY_SHA,
        model_selection_sha256=DUMMY_SHA,
    )


def prediction():
    return base_record(
        "registration_1", "prediction_registration", "2026-10-27T14:05:00Z",
        prediction_id=PID, scope_id=SCOPE, symbol="EURUSD",
        created_at="2026-10-27T14:02:00Z",
        registered_at="2026-10-27T14:04:00Z", decision_at=DECISION,
        horizon_seconds=300, horizon_end_at="2026-10-27T15:08:00Z",
        max_quote_age_seconds=60,
        forecast_specification_sha256=DUMMY_SHA,
        model_artifact_sha256=DUMMY_SHA,
        context_snapshot_sha256=DUMMY_SHA,
        feature_snapshot_sha256=DUMMY_SHA,
    )


def outcome():
    return base_record(
        "outcome_1", "outcome_quote", "2026-10-27T15:08:07Z",
        prediction_id=PID, symbol="EURUSD",
        quote_at="2026-10-27T15:08:05Z", available_at="2026-10-27T15:08:06Z",
        bid="1.08230", ask="1.08242", source_record_id="synthetic_quote_1",
    )


def cost():
    return base_record(
        "cost_1", "cost_receipt", "2026-10-27T15:08:08Z",
        prediction_id=PID, symbol="EURUSD",
        filled_at="2026-10-27T15:03:30Z",
        broker_fill_record_id="synthetic_fill_1",
        spread_pips="1.2", commission_pips="0.6", slippage_pips="0.2",
        financing_pips="0",
    )


def no_trade():
    return base_record(
        "no_trade_1", "no_trade_scope", "2026-10-27T15:09:02Z",
        prediction_id=PID, scope_account_sha256=DUMMY_SHA,
        period_start_at="2026-10-27T15:02:00Z",
        period_end_at="2026-10-27T15:09:00Z", trade_count=0,
    )


def make_fixture(tmp_path, *, choice="cost", include_outcome=True,
                 include_prediction=True, include_holdout=True):
    root = tmp_path / "evidence"
    root.mkdir()
    values = []
    if include_holdout:
        values.append(holdout())
    if include_prediction:
        values.append(prediction())
    if include_outcome:
        values.append(outcome())
    if choice == "cost":
        values.append(cost())
    elif choice == "no_trade":
        values.append(no_trade())
    doc = {
        "schema_version": 1, "epoch_id": HISTORICAL_EPOCH,
        "decision_at": DECISION, "as_of": ASOF, "entries": [],
    }
    for record in values:
        path = root / (record["record_id"] + ".json")
        write(path, record)
        doc["entries"].append({
            "record_id": record["record_id"], "kind": record["kind"],
            "relative_path": path.name,
            "file_sha256": sha256(path.read_bytes()).hexdigest(),
        })
    index = tmp_path / "index.json"
    write(index, doc)
    return root, index, doc


def edit(root, index, doc, record_id, changes, *, update_hash=True):
    entry = next(x for x in doc["entries"] if x["record_id"] == record_id)
    path = root / entry["relative_path"]
    current = json.loads(path.read_text())
    current.update(changes)
    write(path, current)
    if update_hash:
        entry["file_sha256"] = sha256(path.read_bytes()).hexdigest()
        write(index, doc)


def read(root=None, index=None, *, previous_index=None, previous_root=None):
    return inspect_candidate_ledger(
        index_file=index, evidence_root=root, epoch_id=HISTORICAL_EPOCH,
        decision_at=DECISION, as_of=ASOF,
        previous_index=previous_index, previous_root=previous_root,
    )


def assess(**extras):
    return assess_stage6_prospective_ledger(
        contracts_file=CONTRACTS, settlement_file=SETTLEMENT,
        epoch_id=HISTORICAL_EPOCH, decision_at=DECISION, as_of=ASOF,
        **extras,
    )


def test_p613_static_learning_contracts_present_without_import():
    r = inspect_learning_source_contracts(
        contracts_file=CONTRACTS, settlement_file=SETTLEMENT,
    )
    assert r["status"] == "LOCAL_STATIC_LEARNING_CONTRACTS_PRESENT"
    assert r["source_files_executed_or_imported"] is False
    assert r["forecast_specification_present"]
    assert r["settlement_request_contract_present"]
    assert not r["settlement_invoked"]


def test_p613_static_source_digests_repeatable():
    a = inspect_learning_source_contracts(contracts_file=CONTRACTS, settlement_file=SETTLEMENT)
    b = inspect_learning_source_contracts(contracts_file=CONTRACTS, settlement_file=SETTLEMENT)
    assert a == b
    assert len(a["contracts_sha256"]) == 64
    assert len(a["settlement_sha256"]) == 64


def test_p613_missing_contract_file_fails_closed(tmp_path):
    with pytest.raises(ProspectiveLedgerError, match="regular"):
        inspect_learning_source_contracts(
            contracts_file=tmp_path / "missing.py", settlement_file=SETTLEMENT,
        )


def test_p613_missing_settlement_class_fails_closed(tmp_path):
    bad = tmp_path / "settlement.py"
    bad.write_text("class Unrelated:\n    pass\n", encoding="utf-8")
    with pytest.raises(ProspectiveLedgerError, match="SettlementRequest"):
        inspect_learning_source_contracts(contracts_file=CONTRACTS, settlement_file=bad)


def test_p613_holdout_prediction_chain_is_local_only(tmp_path):
    root, index, _ = make_fixture(tmp_path)
    result = read(root, index)
    assert result["candidate_counts"]["holdout_freeze"] == 1
    assert result["candidate_counts"]["prediction_registration"] == 1
    assert result["prediction_ids_locally_reconciled"] == 1
    assert result["complete_local_claimed_chains"] == 1
    assert not result["locally_matched_chain_is_independent_proof"]


def test_p613_future_registration_claim_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "registration_1", {
        "registered_at": "2026-10-27T15:04:00Z",
        "claimed_receipt_at": "2026-10-27T15:04:05Z",
    })
    with pytest.raises(ProspectiveLedgerError, match="predecision"):
        read(root, index)


def test_p613_created_after_registration_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "registration_1", {
        "created_at": "2026-10-27T14:04:10Z",
    })
    with pytest.raises(ProspectiveLedgerError, match="predecision"):
        read(root, index)


def test_p613_registration_receipt_earlier_than_registration_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "registration_1", {
        "registered_at": "2026-10-27T14:06:00Z",
    })
    with pytest.raises(ProspectiveLedgerError, match="predecision"):
        read(root, index)


def test_p613_horizon_inconsistent_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "registration_1", {
        "horizon_end_at": "2026-10-27T15:07:00Z",
    })
    with pytest.raises(ProspectiveLedgerError, match="horizon"):
        read(root, index)


@pytest.mark.parametrize("bad", [0, 86401, True, "300"])
def test_p613_invalid_horizon_refused(tmp_path, bad):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "registration_1", {"horizon_seconds": bad})
    with pytest.raises(ProspectiveLedgerError, match="horizon"):
        read(root, index)


def test_p613_missing_holdout_refused(tmp_path):
    root, index, _ = make_fixture(tmp_path, include_holdout=False)
    with pytest.raises(ProspectiveLedgerError, match="frozen before registration"):
        read(root, index)


def test_p613_holdout_frozen_too_late_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "holdout_1", {
        "freeze_at": "2026-10-27T15:05:00Z",
    })
    with pytest.raises(ProspectiveLedgerError, match="Holdout freeze"):
        read(root, index)


def test_p613_holdout_receipt_after_registration_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "holdout_1", {
        "claimed_receipt_at": "2026-10-27T14:05:00Z",
    })
    with pytest.raises(ProspectiveLedgerError, match="frozen before registration"):
        read(root, index)


def test_p613_duplicate_prediction_id_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    duplicate = prediction()
    duplicate["record_id"] = "registration_2"
    duplicate["claimed_receipt_id"] = "unverified_registration_2"
    duplicate["claimed_receipt_at"] = "2026-10-27T14:06:00Z"
    # All registrations must appear before post-horizon events.
    path = root / "registration_2.json"
    write(path, duplicate)
    doc["entries"].insert(2, {
        "record_id": duplicate["record_id"], "kind": duplicate["kind"],
        "relative_path": path.name, "file_sha256": sha256(path.read_bytes()).hexdigest(),
    })
    write(index, doc)
    with pytest.raises(ProspectiveLedgerError, match="Duplicate prospective prediction"):
        read(root, index)


def test_p613_no_predictions_does_not_imply_matured_results(tmp_path):
    root, index, _ = make_fixture(tmp_path, choice="none", include_outcome=False,
                                   include_prediction=False)
    result = read(root, index)
    assert result["prediction_ids_locally_reconciled"] == 0
    assert result["complete_local_claimed_chains"] == 0


def test_p614_missing_outcome_is_not_a_completed_chain(tmp_path):
    root, index, _ = make_fixture(tmp_path, include_outcome=False)
    result = read(root, index)
    assert result["complete_local_claimed_chains"] == 0
    assert result["predictions_without_post_horizon_outcome"] == 1


def test_p614_outcome_before_horizon_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "outcome_1", {
        "quote_at": "2026-10-27T15:07:55Z",
    })
    with pytest.raises(ProspectiveLedgerError, match="outside declared horizon"):
        read(root, index)


def test_p614_outcome_stale_after_horizon_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "outcome_1", {
        "quote_at": "2026-10-27T15:10:01Z",
        "available_at": "2026-10-27T15:10:02Z",
        "claimed_receipt_at": "2026-10-27T15:10:03Z",
    })
    # Must preserve chronological manifest receipt ordering for this case.
    edit(root, index, doc, "cost_1", {"claimed_receipt_at": "2026-10-27T15:10:04Z"})
    with pytest.raises(ProspectiveLedgerError, match="outside declared horizon"):
        read(root, index)


def test_p614_quote_available_after_claimed_receipt_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "outcome_1", {
        "available_at": "2026-10-27T15:08:09Z",
    })
    with pytest.raises(ProspectiveLedgerError, match="Outcome unavailable"):
        read(root, index)


def test_p614_crossed_bid_ask_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "outcome_1", {"ask": "1.08000"})
    with pytest.raises(ProspectiveLedgerError, match="Crossed"):
        read(root, index)


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-1.1", "0", "1e2", 1.08])
def test_p614_invalid_quote_price_refused(tmp_path, price):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "outcome_1", {"bid": price})
    with pytest.raises(ProspectiveLedgerError, match="decimal|Nonpositive"):
        read(root, index)


def test_p614_orphan_outcome_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "outcome_1", {"prediction_id": "f" * 64})
    with pytest.raises(ProspectiveLedgerError, match="Orphan"):
        read(root, index)


def test_p614_wrong_outcome_symbol_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "outcome_1", {"symbol": "GBPUSD"})
    with pytest.raises(ProspectiveLedgerError, match="Outcome symbol"):
        read(root, index)


def test_p614_fabricated_independent_approval_field_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "registration_1", {"independently_verified": True})
    with pytest.raises(ProspectiveLedgerError, match="payload schema"):
        read(root, index)


def test_p614_payload_tampered_without_updated_index_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "outcome_1", {"bid": "1.20000"}, update_hash=False)
    with pytest.raises(ProspectiveLedgerError, match="SHA-256 mismatch"):
        read(root, index)


def test_p614_wrong_epoch_in_payload_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "registration_1", {"epoch_id": "f" * 64})
    with pytest.raises(ProspectiveLedgerError, match="epoch mismatch"):
        read(root, index)


def test_p614_duplicate_json_key_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    index.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(ProspectiveLedgerError, match="Duplicate"):
        read(root, index)


def test_p614_claimed_external_receipt_reused_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "registration_1", {
        "claimed_receipt_id": "unverified_holdout_1",
    })
    with pytest.raises(ProspectiveLedgerError, match="identifier reused"):
        read(root, index)


@pytest.mark.parametrize("path", ["../secret", "/absolute", "C:/secret", "a//b", "./file", "a/../b", "dir\\file"])
def test_p614_unsafe_payload_paths_refused(tmp_path, path):
    root, index, doc = make_fixture(tmp_path)
    doc["entries"][1]["relative_path"] = path
    write(index, doc)
    with pytest.raises(ProspectiveLedgerError, match="Unsafe relative"):
        read(root, index)


def test_p614_symlinked_payload_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    link = root / "link.json"
    try:
        link.symlink_to(root / "registration_1.json")
    except (OSError, NotImplementedError):
        pytest.skip("Windows symlink creation unavailable")
    doc["entries"][1]["relative_path"] = link.name
    write(index, doc)
    with pytest.raises(ProspectiveLedgerError, match="Symlinked"):
        read(root, index)


def test_p614_claim_after_index_cutoff_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "cost_1", {
        "claimed_receipt_at": "2026-10-30T10:00:00Z",
    })
    with pytest.raises(ProspectiveLedgerError, match="after cutoff"):
        read(root, index)


def test_p615_nonnegative_claimed_cost_fields_valid(tmp_path):
    root, index, _ = make_fixture(tmp_path)
    r = read(root, index)
    assert r["candidate_counts"]["cost_receipt"] == 1
    assert r["complete_local_claimed_chains"] == 1
    assert r["independently_authenticated_cost_or_no_trade_scopes"] == 0


def test_p615_independent_no_trade_scope_not_inferred(tmp_path):
    root, index, _ = make_fixture(tmp_path, choice="no_trade")
    r = read(root, index)
    assert r["candidate_counts"]["no_trade_scope"] == 1
    assert r["complete_local_claimed_chains"] == 1
    assert r["independently_authenticated_cost_or_no_trade_scopes"] == 0


def test_p615_zero_cost_is_not_verified_profit(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "cost_1", {field: "0" for field in (
        "spread_pips", "commission_pips", "slippage_pips", "financing_pips",
    )})
    result = assess(index_file=index, evidence_root=root)
    assert not result["prospective_profitability_established"]
    assert result["final_action"] == "HOLD"


@pytest.mark.parametrize("value", ["-0.1", "NaN", "Infinity", "1e1", 0.5])
def test_p615_invalid_cost_refused(tmp_path, value):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "cost_1", {"slippage_pips": value})
    with pytest.raises(ProspectiveLedgerError, match="decimal"):
        read(root, index)


def test_p615_cost_fill_before_decision_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    edit(root, index, doc, "cost_1", {"filled_at": "2026-10-27T15:02:59Z"})
    with pytest.raises(ProspectiveLedgerError, match="fill time"):
        read(root, index)


def test_p615_no_trade_count_must_be_integer_zero(tmp_path):
    root, index, doc = make_fixture(tmp_path, choice="no_trade")
    edit(root, index, doc, "no_trade_1", {"trade_count": True})
    with pytest.raises(ProspectiveLedgerError, match="zero trades"):
        read(root, index)


def test_p615_no_trade_period_must_cover_horizon(tmp_path):
    root, index, doc = make_fixture(tmp_path, choice="no_trade")
    edit(root, index, doc, "no_trade_1", {
        "period_end_at": "2026-10-27T15:07:00Z",
    })
    with pytest.raises(ProspectiveLedgerError, match="cover prediction horizon"):
        read(root, index)


def test_p615_no_trade_received_after_period(tmp_path):
    root, index, doc = make_fixture(tmp_path, choice="no_trade")
    edit(root, index, doc, "no_trade_1", {
        "claimed_receipt_at": "2026-10-27T15:08:59Z",
    })
    with pytest.raises(ProspectiveLedgerError, match="cover prediction horizon"):
        read(root, index)


def test_p615_cost_and_no_trade_mutually_exclusive(tmp_path):
    root, index, doc = make_fixture(tmp_path, choice="cost")
    r = no_trade()
    path = root / "no_trade_1.json"
    write(path, r)
    doc["entries"].append({
        "record_id": "no_trade_1", "kind": "no_trade_scope",
        "relative_path": path.name, "file_sha256": sha256(path.read_bytes()).hexdigest(),
    })
    write(index, doc)
    with pytest.raises(ProspectiveLedgerError, match="Contradictory"):
        read(root, index)


def test_p615_no_cost_or_no_trade_scope_is_incomplete(tmp_path):
    root, index, _ = make_fixture(tmp_path, choice="none")
    r = read(root, index)
    assert r["complete_local_claimed_chains"] == 0
    assert r["predictions_without_cost_or_no_trade_scope"] == 1


def test_p616_no_index_is_not_claim_of_nonexistence():
    r = read()
    assert r["status"] == "NOT_SUPPLIED"
    assert r["complete_local_claimed_chains"] == 0
    assert r["local_append_only_prefix_consistent"] is None


def test_p616_previous_snapshot_prefix_verifiable_locally(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    old_index = tmp_path / "previous.json"
    old_doc = {**doc, "as_of": "2026-10-27T15:03:00Z",
               "entries": doc["entries"][:2]}
    write(old_index, old_doc)
    r = read(root, index, previous_index=old_index, previous_root=root)
    assert r["local_append_only_prefix_consistent"] is True
    assert r["previous_snapshot"] == "LOCAL_PREFIX_CHECKED_UNATTESTED"
    assert not r["locally_matched_chain_is_independent_proof"]


def test_p616_predecision_freeze_snapshot_is_accepted_locally(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    old_index = tmp_path / "predecision_freeze_snapshot.json"
    old_doc = {**doc, "as_of": "2026-10-27T14:01:00Z",
               "entries": doc["entries"][:1]}
    write(old_index, old_doc)
    result = read(root, index, previous_index=old_index, previous_root=root)
    assert result["local_append_only_prefix_consistent"] is True
    assert result["independently_authenticated_holdout_freezes"] == 0


def test_p616_current_assessment_before_decision_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    with pytest.raises(ProspectiveLedgerError, match="cutoff precedes decision"):
        inspect_candidate_ledger(
            index_file=index, evidence_root=root, epoch_id=HISTORICAL_EPOCH,
            decision_at=DECISION, as_of="2026-10-27T15:02:00Z",
        )


def test_p616_previous_index_mutated_prefix_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    old_index = tmp_path / "previous.json"
    old_doc = {**doc, "as_of": "2026-10-27T15:03:00Z",
               "entries": doc["entries"][:2]}
    old_doc["entries"] = [dict(x) for x in old_doc["entries"]]
    old_doc["entries"][1]["file_sha256"] = "f" * 64
    write(old_index, old_doc)
    with pytest.raises(ProspectiveLedgerError, match="SHA-256 mismatch"):
        read(root, index, previous_index=old_index, previous_root=root)


def test_p616_previously_committed_entry_deletion_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    old = tmp_path / "previous.json"
    write(old, doc)
    doc["entries"] = doc["entries"][:-1]
    write(index, doc)
    with pytest.raises(ProspectiveLedgerError, match="modified, reordered or deleted"):
        read(root, index, previous_index=old, previous_root=root)


def test_p616_previous_entry_reordered_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    old = tmp_path / "previous.json"
    write(old, doc)
    doc["entries"] = [*doc["entries"][:1], *doc["entries"][2:3],
                      *doc["entries"][1:2], *doc["entries"][3:]]
    write(index, doc)
    with pytest.raises(ProspectiveLedgerError, match="Receipt.*order|modified, reordered"):
        read(root, index, previous_index=old, previous_root=root)


def test_p616_previous_snapshot_with_no_current_refused(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    with pytest.raises(ProspectiveLedgerError, match="without current index"):
        read(previous_index=index, previous_root=root)


def test_p616_previous_index_requires_root(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    with pytest.raises(ProspectiveLedgerError, match="requires previous root"):
        read(root, index, previous_index=index)


def test_p616_index_root_required(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    with pytest.raises(ProspectiveLedgerError, match="root required"):
        read(index=index)


def test_p616_previous_snapshot_unprovided_is_explicit(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    r = read(root, index)
    assert r["previous_snapshot"] == "NOT_SUPPLIED"
    assert r["local_append_only_prefix_consistent"] is None


def test_p616_strict_no_rebind():
    with pytest.raises(ProspectiveLedgerError, match="epoch mismatch"):
        assess_stage6_prospective_ledger(
            contracts_file=CONTRACTS, settlement_file=SETTLEMENT,
            epoch_id="f" * 64, decision_at=DECISION, as_of=ASOF,
        )


def test_p616_assessment_is_deterministic():
    r1, r2 = assess(), assess()
    assert r1 == r2
    assert r1["report_sha256"] == _digest({k: v for k, v in r1.items()
                                             if k != "report_sha256"})


def test_p616_complete_local_chain_does_not_admit_execution(tmp_path):
    root, index, doc = make_fixture(tmp_path)
    r = assess(index_file=index, evidence_root=root)
    assert r["candidate_ledger"]["complete_local_claimed_chains"] == 1
    assert r["external_prospective_gates_verified"] == 0
    assert not r["independent_external_timestamp_service_verified"]
    assert not r["independent_outcome_source_verified"]
    assert not r["independent_holdout_freeze_verified"]
    assert not r["broker_trade_costs_or_no_trade_scope_verified"]
    assert not r["prospective_profitability_established"]
    assert r["final_action"] == "HOLD"


def test_p616_no_side_effects_or_operations():
    r = assess()
    for field in (
        "files_written", "database_accessed", "network_accessed",
        "broker_connected", "collector_started", "historical_epoch_rebound",
        "council_invoked", "settlement_invoked", "training_authorized",
        "paper_execution_authorized", "live_execution_authorized",
    ):
        assert r[field] is False
    assert r["read_only"] is True


def test_p616_cli_requires_readonly_flags():
    with pytest.raises(SystemExit) as error:
        run(["--epoch-id", HISTORICAL_EPOCH,
             "--decision-at", DECISION, "--as-of", ASOF])
    assert error.value.code == 2


def test_p616_cli_json_report_hold(capsys):
    result = run([
        "--read-only-source", "--read-only-evidence",
        "--contracts-file", str(CONTRACTS), "--settlement-file", str(SETTLEMENT),
        "--epoch-id", HISTORICAL_EPOCH, "--decision-at", DECISION,
        "--as-of", ASOF, "--json",
    ])
    assert result == 0
    report = json.loads(capsys.readouterr().out)
    assert report["external_prospective_gates_verified"] == 0
    assert report["final_action"] == "HOLD"


def test_p616_cli_wrong_epoch_blocks(capsys):
    code = run([
        "--read-only-source", "--read-only-evidence",
        "--contracts-file", str(CONTRACTS), "--settlement-file", str(SETTLEMENT),
        "--epoch-id", "bad", "--decision-at", DECISION, "--as-of", ASOF,
    ])
    assert code == 1
    assert "BLOCKED" in capsys.readouterr().out


@pytest.mark.parametrize("value", [
    "bad", "2026-10-27T15:03:00", "2026-10-27T18:03:00+03:00",
])
def test_p616_utc_must_be_explicit_utc(value):
    with pytest.raises(ProspectiveLedgerError):
        _utc(value)

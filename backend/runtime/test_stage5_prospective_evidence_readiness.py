
"""S5-17..20: synthetic-only candidate and fail-closed research evaluations."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime.stage5_prospective_evidence_readiness import (
    Stage5ProspectiveError, _digest, _utc, assess_stage5_prospective,
    inspect_candidate_index, inspect_memory_contracts, run,
)

ROOT = Path(__file__).parents[2]
MEMORY = ROOT / "backend/learning/contracts.py"
SETTLE = ROOT / "backend/learning/settle_outcomes.py"
EPOCH = "7" * 64
PRED = "8" * 64
DECISION = datetime(2026, 10, 27, 15, 1, 40, tzinfo=timezone.utc)
ASOF = datetime(2026, 10, 29, 19, 10, tzinfo=timezone.utc)
CREATED = DECISION - timedelta(minutes=2)
HORIZON = 300
PARTITION = "SYNTHETIC-HOLDOUT"


def ts(x):
    return x.isoformat()


def report_args(**kw):
    return dict(memory_contracts_file=MEMORY, settlement_file=SETTLE,
                epoch_id=EPOCH, as_of=ts(ASOF), **kw)


def write(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")


def fixture_bundle(tmp_path, *, kinds=("prediction", "outcome", "cost", "holdout"), trade=True):
    root = tmp_path / "evidence"
    root.mkdir()
    artifacts = {
        "prediction": {
            "schema_version": 1, "epoch_id": EPOCH, "prediction_id": PRED,
            "symbol": "EURUSD", "direction": "BUY", "created_at": ts(CREATED),
            "decision_at": ts(DECISION), "horizon_seconds": HORIZON,
            "partition_id": PARTITION, "model_sha256": "a" * 64,
            "inputs_sha256": "b" * 64,
            "execution_scope": "TRADE_CLAIMED" if trade else "NO_TRADE_DECLARED",
        },
        "outcome": {
            "schema_version": 1, "epoch_id": EPOCH, "prediction_id": PRED,
            "observed_at": ts(DECISION + timedelta(seconds=HORIZON + 2)),
            "available_at": ts(DECISION + timedelta(seconds=HORIZON + 3)),
            "bid": 1.08970, "ask": 1.08985, "source_record_id": "SYNTHETIC-QUOTE",
        },
        "cost": {
            "schema_version": 1, "epoch_id": EPOCH, "prediction_id": PRED,
            "executed_at": ts(DECISION + timedelta(seconds=1)),
            "fill_receipt_id": "SYNTHETIC-FILL", "spread_pips": 1.1,
            "commission_pips": 0.5, "slippage_pips": 0.3,
            "financing_pips": 0.0,
        },
        "holdout": {
            "schema_version": 1, "epoch_id": EPOCH,
            "partition_id": PARTITION, "frozen_at": ts(CREATED - timedelta(hours=1)),
            "partition_sha256": "c" * 64, "evaluation_plan_sha256": "d" * 64,
        },
    }
    windows = {
        "prediction": (CREATED + timedelta(seconds=10), CREATED + timedelta(seconds=20)),
        "outcome": (DECISION + timedelta(seconds=HORIZON + 5), DECISION + timedelta(seconds=HORIZON + 6)),
        "cost": (DECISION + timedelta(seconds=70), DECISION + timedelta(seconds=71)),
        "holdout": (CREATED - timedelta(minutes=55), CREATED - timedelta(minutes=54)),
    }
    entries = []
    for kind in kinds:
        path = root / (kind + ".json")
        write(path, artifacts[kind])
        issued, received = windows[kind]
        entries.append({
            "kind": kind, "record_id": "fixture-" + kind,
            "relative_path": path.name, "file_sha256": sha256(path.read_bytes()).hexdigest(),
            "claimed_issuer": "SYNTHETIC-NOT-A-PROVIDER",
            "claimed_issued_at": ts(issued),
            "claimed_received_at": ts(received),
        })
    index = {"schema_version": 1, "epoch_id": EPOCH, "as_of": ts(ASOF),
             "entries": entries}
    index_path = tmp_path / "index.json"
    write(index_path, index)
    return root, index_path, index, artifacts


def refresh(root, index_path, index, artifacts):
    for kind, item in artifacts.items():
        path = root / (kind + ".json")
        if path.exists():
            write(path, item)
            for entry in index["entries"]:
                if entry["kind"] == kind:
                    entry["file_sha256"] = sha256(path.read_bytes()).hexdigest()
    write(index_path, index)


def review(root, index_path):
    return inspect_candidate_index(index_path, root=root, epoch_id=EPOCH, as_of=ts(ASOF))


def test_s517_original_memory_contracts_are_static_only():
    result = inspect_memory_contracts(MEMORY, SETTLE)
    assert result["forecast_specification_present"]
    assert result["outcome_quote_contract_present"]
    assert result["settlement_request_contract_present"]
    assert result["source_files_executed"] is False
    assert result["external_prediction_registration_verified"] is False


def test_s517_memory_contract_removed_refused(tmp_path):
    bad = tmp_path / "contracts.py"
    bad.write_text("class SomethingElse: pass\n")
    with pytest.raises(Stage5ProspectiveError, match="memory contracts"):
        inspect_memory_contracts(bad, SETTLE)


def test_s517_settlement_contract_removed_refused(tmp_path):
    bad = tmp_path / "settle.py"
    bad.write_text("class NotASettlement: pass\n")
    with pytest.raises(Stage5ProspectiveError, match="SettlementRequest"):
        inspect_memory_contracts(MEMORY, bad)


def test_s517_no_index_means_no_attestation():
    r = inspect_candidate_index(None, root=None, epoch_id=EPOCH, as_of=ts(ASOF))
    assert r["status"] == "NOT_SUPPLIED"
    assert r["locally_hash_matched_files"] == 0
    assert r["externally_authenticated_registrations"] == 0


def test_s517_hash_matched_prediction_stays_unattested(tmp_path):
    root, idx, _, _ = fixture_bundle(tmp_path, kinds=("prediction",))
    r = review(root, idx)
    assert r["candidate_counts"]["prediction"] == 1
    assert r["externally_authenticated_registrations"] == 0
    assert r["performance_calculated"] is False


def test_s517_prediction_registered_after_decision_refused(tmp_path):
    root, idx, index, _ = fixture_bundle(tmp_path)
    index["entries"][0]["claimed_received_at"] = ts(DECISION + timedelta(seconds=1))
    write(idx, index)
    with pytest.raises(Stage5ProspectiveError, match="before decision"):
        review(root, idx)


def test_s517_prediction_created_after_issued_refused(tmp_path):
    root, idx, index, artifacts = fixture_bundle(tmp_path)
    artifacts["prediction"]["created_at"] = ts(DECISION - timedelta(seconds=1))
    refresh(root, idx, index, artifacts)
    with pytest.raises(Stage5ProspectiveError, match="before decision"):
        review(root, idx)


def test_s517_prediction_horizon_boolean_refused(tmp_path):
    root, idx, index, artifacts = fixture_bundle(tmp_path)
    artifacts["prediction"]["horizon_seconds"] = True
    refresh(root, idx, index, artifacts)
    with pytest.raises(Stage5ProspectiveError, match="Prediction contract"):
        review(root, idx)


def test_s517_unsupported_symbol_refused(tmp_path):
    root, idx, index, artifacts = fixture_bundle(tmp_path)
    artifacts["prediction"]["symbol"] = "GBPUSD"
    refresh(root, idx, index, artifacts)
    with pytest.raises(Stage5ProspectiveError, match="Prediction contract"):
        review(root, idx)


def test_s517_missing_prediction_link_refused(tmp_path):
    root, idx, _, _ = fixture_bundle(tmp_path, kinds=("outcome",))
    with pytest.raises(Stage5ProspectiveError, match="lacks registered prediction"):
        review(root, idx)


def test_s518_post_horizon_outcome_is_locally_linked(tmp_path):
    root, idx, _, _ = fixture_bundle(tmp_path)
    r = review(root, idx)
    assert r["complete_local_prediction_outcome_pairs"] == 1
    assert r["post_horizon_quotes_locally_reconciled"] == 1
    assert r["independently_verified_outcomes"] == 0


def test_s518_outcome_before_horizon_refused(tmp_path):
    root, idx, index, artifacts = fixture_bundle(tmp_path)
    artifacts["outcome"]["observed_at"] = ts(DECISION + timedelta(seconds=HORIZON - 1))
    refresh(root, idx, index, artifacts)
    with pytest.raises(Stage5ProspectiveError, match="post-horizon"):
        review(root, idx)


def test_s518_outcome_late_quote_refused(tmp_path):
    root, idx, index, artifacts = fixture_bundle(tmp_path)
    at = DECISION + timedelta(seconds=HORIZON + 301)
    artifacts["outcome"]["observed_at"] = ts(at)
    artifacts["outcome"]["available_at"] = ts(at + timedelta(seconds=1))
    evidence = next(e for e in index["entries"] if e["kind"] == "outcome")
    evidence["claimed_issued_at"] = ts(at + timedelta(seconds=2))
    evidence["claimed_received_at"] = ts(at + timedelta(seconds=3))
    refresh(root, idx, index, artifacts)
    with pytest.raises(Stage5ProspectiveError, match="post-horizon"):
        review(root, idx)


def test_s518_late_outcome_availability_refused(tmp_path):
    root, idx, index, artifacts = fixture_bundle(tmp_path)
    artifacts["outcome"]["available_at"] = ts(ASOF + timedelta(seconds=1))
    refresh(root, idx, index, artifacts)
    with pytest.raises(Stage5ProspectiveError, match="Outcome availability"):
        review(root, idx)


def test_s518_crossed_quotes_refused(tmp_path):
    root, idx, index, artifacts = fixture_bundle(tmp_path)
    artifacts["outcome"]["ask"] = 1.01
    refresh(root, idx, index, artifacts)
    with pytest.raises(Stage5ProspectiveError, match="Outcome quote"):
        review(root, idx)


def test_s518_nonfinite_quote_refused(tmp_path):
    root, idx, index, artifacts = fixture_bundle(tmp_path)
    artifacts["outcome"]["bid"] = float("nan")
    refresh(root, idx, index, artifacts)
    with pytest.raises(Stage5ProspectiveError, match="Nonfinite"):
        review(root, idx)


def test_s518_outcome_bad_prediction_reference_refused(tmp_path):
    root, idx, index, artifacts = fixture_bundle(tmp_path)
    artifacts["outcome"]["prediction_id"] = "f" * 64
    refresh(root, idx, index, artifacts)
    with pytest.raises(Stage5ProspectiveError, match="lacks registered prediction"):
        review(root, idx)


def test_s519_trade_claimed_cost_remains_not_broker_confirmed(tmp_path):
    root, idx, _, _ = fixture_bundle(tmp_path)
    r = review(root, idx)
    assert r["claimed_trade_predictions"] == 1
    assert r["candidate_counts"]["cost"] == 1
    assert r["broker_confirmed_costs"] == 0
    assert r["complete_local_evaluation_chains"] == 1


def test_s519_no_trade_scope_requires_no_cost_candidate(tmp_path):
    root, idx, _, _ = fixture_bundle(tmp_path, trade=False, kinds=("prediction", "outcome", "holdout"))
    r = review(root, idx)
    assert r["declared_no_trade_predictions"] == 1
    assert r["complete_local_evaluation_chains"] == 1
    assert r["broker_confirmed_costs"] == 0


def test_s519_no_trade_claim_cannot_have_cost(tmp_path):
    root, idx, _, _ = fixture_bundle(tmp_path, trade=False)
    with pytest.raises(Stage5ProspectiveError, match="no-trade"):
        review(root, idx)


def test_s519_cost_fill_before_decision_refused(tmp_path):
    root, idx, index, artifacts = fixture_bundle(tmp_path)
    artifacts["cost"]["executed_at"] = ts(DECISION - timedelta(seconds=1))
    refresh(root, idx, index, artifacts)
    with pytest.raises(Stage5ProspectiveError, match="predates"):
        review(root, idx)


def test_s519_cost_negative_refused(tmp_path):
    root, idx, index, artifacts = fixture_bundle(tmp_path)
    artifacts["cost"]["slippage_pips"] = -1
    refresh(root, idx, index, artifacts)
    with pytest.raises(Stage5ProspectiveError, match="cost contract"):
        review(root, idx)


def test_s519_cost_boolean_refused(tmp_path):
    root, idx, index, artifacts = fixture_bundle(tmp_path)
    artifacts["cost"]["commission_pips"] = False
    refresh(root, idx, index, artifacts)
    with pytest.raises(Stage5ProspectiveError, match="cost contract"):
        review(root, idx)


def test_s519_claimed_trade_without_cost_is_incomplete(tmp_path):
    root, idx, _, _ = fixture_bundle(tmp_path, kinds=("prediction", "outcome", "holdout"))
    r = review(root, idx)
    assert r["complete_local_prediction_outcome_pairs"] == 1
    assert r["complete_local_evaluation_chains"] == 0


def test_s520_freeze_precommitted_locally_not_attested(tmp_path):
    root, idx, _, _ = fixture_bundle(tmp_path)
    r = review(root, idx)
    assert r["holdout_freezes_locally_precommitted"] == 1
    assert r["independently_verified_holdout_freezes"] == 0


def test_s520_freeze_after_prediction_creation_refused(tmp_path):
    root, idx, index, _ = fixture_bundle(tmp_path)
    freeze = next(e for e in index["entries"] if e["kind"] == "holdout")
    freeze["claimed_issued_at"] = ts(CREATED + timedelta(seconds=1))
    freeze["claimed_received_at"] = ts(CREATED + timedelta(seconds=2))
    write(idx, index)
    with pytest.raises(Stage5ProspectiveError, match="freeze received after"):
        review(root, idx)


def test_s520_missing_holdout_is_incomplete(tmp_path):
    root, idx, _, _ = fixture_bundle(tmp_path, kinds=("prediction", "outcome", "cost"))
    assert review(root, idx)["complete_local_evaluation_chains"] == 0


def test_s520_freeze_only_does_not_establish_performance(tmp_path):
    root, idx, _, _ = fixture_bundle(tmp_path, kinds=("holdout",))
    assert review(root, idx)["complete_local_evaluation_chains"] == 0


def test_s520_all_candidates_still_hold(tmp_path):
    root, idx, _, _ = fixture_bundle(tmp_path)
    r = assess_stage5_prospective(**report_args(index_file=idx, evidence_root=root))
    assert r["candidate_evidence"]["locally_hash_matched_files"] == 4
    assert r["external_prospective_gates_independently_verified"] == 0
    assert r["net_of_costs_performance_verified"] is False
    assert r["research_ready"] is False
    assert r["final_action"] == "HOLD"


def test_s520_no_index_assessment_all_gates_blocked():
    r = assess_stage5_prospective(**report_args())
    assert r["candidate_evidence"]["status"] == "NOT_SUPPLIED"
    assert r["external_prospective_gates_independently_verified"] == 0
    assert r["final_action"] == "HOLD"


def test_s520_hash_is_deterministic():
    a = assess_stage5_prospective(**report_args())
    b = assess_stage5_prospective(**report_args())
    assert a == b
    assert a["report_sha256"] == _digest({k: v for k, v in a.items() if k != "report_sha256"})


def test_s520_no_stateful_actions():
    r = assess_stage5_prospective(**report_args())
    for field in ("files_written", "database_accessed", "network_accessed", "broker_connected",
                  "collector_started", "council_invoked", "training_authorized",
                  "execution_authorized", "research_admission_authorized"):
        assert r[field] is False


def test_s520_duplicate_index_id_refused(tmp_path):
    root, idx, index, _ = fixture_bundle(tmp_path)
    index["entries"][1]["record_id"] = index["entries"][0]["record_id"]
    write(idx, index)
    with pytest.raises(Stage5ProspectiveError, match="duplicate"):
        review(root, idx)


def test_s520_tampered_artifact_refused(tmp_path):
    root, idx, _, _ = fixture_bundle(tmp_path)
    (root / "prediction.json").write_text("tampered", encoding="utf-8")
    with pytest.raises(Stage5ProspectiveError, match="SHA-256"):
        review(root, idx)


def test_s520_duplicate_json_fields_refused(tmp_path):
    root, idx, _, _ = fixture_bundle(tmp_path)
    idx.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(Stage5ProspectiveError, match="Duplicate"):
        review(root, idx)


@pytest.mark.parametrize("relative", ["../escape", "folder/../x", "/root", "C:/x", "foo\\bar", "a//b", "./x"])
def test_s520_unsafe_paths_refused(tmp_path, relative):
    root, idx, index, _ = fixture_bundle(tmp_path)
    index["entries"][0]["relative_path"] = relative
    write(idx, index)
    with pytest.raises(Stage5ProspectiveError, match="path|traversal"):
        review(root, idx)


def test_s520_symlinked_evidence_refused(tmp_path):
    root, idx, index, _ = fixture_bundle(tmp_path)
    link = root / "link.json"
    try:
        link.symlink_to(root / "prediction.json")
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable")
    index["entries"][0]["relative_path"] = link.name
    write(idx, index)
    with pytest.raises(Stage5ProspectiveError, match="Symlink"):
        review(root, idx)


def test_s520_cli_requires_readonly_flags():
    with pytest.raises(SystemExit) as exc:
        run(["--epoch-id", EPOCH, "--as-of", ts(ASOF)])
    assert exc.value.code == 2


def test_s520_cli_json_hold(capsys):
    assert run(["--read-only-source", "--read-only-evidence", "--epoch-id", EPOCH,
                "--as-of", ts(ASOF), "--memory-contracts-file", str(MEMORY),
                "--settlement-file", str(SETTLE), "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["final_action"] == "HOLD"
    assert r["candidate_evidence"]["status"] == "NOT_SUPPLIED"


@pytest.mark.parametrize("value", ["2026-10-27T15:01:40", "invalid", "2026-10-27T18:01:40+03:00"])
def test_s520_utc_only(value):
    with pytest.raises(Stage5ProspectiveError):
        _utc(value)

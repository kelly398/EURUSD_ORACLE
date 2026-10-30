
"""P6-17..20: isolated synthetic evaluation, anti-leakage and HOLD tests."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime.stage6_research_evaluation_readiness import (
    BENCHMARKS, EPOCH, REGIMES, ResearchEvaluationError, _digest,
    _utc, assess_stage6_research_evaluation, run,
)
from backend.runtime.stage6_council_context_readiness import inspect_static_council

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "backend/agents/contracts.py"
MANIFEST = ROOT / "backend/agents/council_manifest.py"
CUTOFF = "2026-10-30T12:00:00Z"
STUDY = "SYNTHETIC_EVAL_ONLY"
DUMMY_SHA = "a" * 64
BASE = datetime(2026, 10, 27, 15, 3, tzinfo=timezone.utc)


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def write(file, obj):
    file.write_text(json.dumps(obj, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def static():
    return inspect_static_council(contracts_file=CONTRACTS, manifest_file=MANIFEST)


def protocol():
    return {
        "schema_version": 1, "epoch_id": EPOCH, "experiment_id": STUDY,
        "council_manifest_sha256": static()["manifest_sha256"],
        "claimed_freeze_at": "2026-10-27T13:59:00Z",
        "claimed_freeze_receipt_at": "2026-10-27T14:00:00Z",
        "evaluation_start_at": "2026-10-27T15:00:00Z",
        "evaluation_end_at": "2026-10-27T17:00:00Z",
        "holdout_partition_sha256": DUMMY_SHA,
        "evaluation_plan_sha256": DUMMY_SHA,
        "horizon_seconds": 300, "min_samples": 4,
        "neutral_band_pips": "0.1", "assumed_roundtrip_cost_pips": "1.5",
        "stress_extra_cost_pips": ["0", "1", "2.5"],
        "benchmarks": list(BENCHMARKS), "regimes": list(REGIMES),
    }


def record(i):
    decision = BASE + timedelta(minutes=12 * i)
    sign = "LONG" if i % 2 == 0 else "SHORT"
    final = "HOLD" if i == 3 else sign
    changes = ["1.08105", "1.08096", "1.08100", "1.08108", "1.08093", "1.08101"]
    seats = [(s["seat"], s["agent_id"]) for s in static()["seats"] if s["stage"] == "PRIMARY"]
    votes = []
    for pos, agent in seats:
        direction = sign if pos <= 10 else ("SHORT" if sign == "LONG" else "LONG") if pos <= 20 else "ABSTAIN"
        votes.append({"seat": pos, "agent_id": agent, "direction": direction,
                      "confidence": "0.7" if direction != "ABSTAIN" else "0"})
    return {
        "record_id": "fixture_%02d" % i,
        "epoch_id": EPOCH, "experiment_id": STUDY,
        "prediction_id": "%064x" % (i + 1),
        "decision_at": iso(decision),
        "claimed_registered_at": iso(decision - timedelta(minutes=2)),
        "claimed_registration_receipt_at": iso(decision - timedelta(minutes=1)),
        "horizon_end_at": iso(decision + timedelta(minutes=5)),
        "entry_quote_at": iso(decision - timedelta(seconds=30)),
        "entry_quote_available_at": iso(decision - timedelta(seconds=25)),
        "entry_mid": "1.08100",
        "outcome_quote_at": iso(decision + timedelta(minutes=5)),
        "outcome_quote_available_at": iso(decision + timedelta(minutes=5, seconds=1)),
        "claimed_outcome_receipt_at": iso(decision + timedelta(minutes=5, seconds=2)),
        "outcome_mid": changes[i],
        "prior_signal_available_at": iso(decision - timedelta(minutes=3)),
        "prior_move_pips": "1" if i % 2 == 0 else "-1",
        "regime": REGIMES[i % len(REGIMES)],
        "consensus_direction": sign,
        "consensus_probability_up": "0.8" if sign == "LONG" else "0.2",
        "primary_votes": votes,
        "devils_advocate_challenge": i == 2,
        "risk_veto": i == 3,
        "final_research_action": final,
        "claimed_registration_receipt_sha256": DUMMY_SHA,
        "claimed_outcome_receipt_sha256": DUMMY_SHA,
        "claimed_cost_basis_sha256": DUMMY_SHA,
        "research_only_no_orders": True,
    }


def fixture(tmp_path, *, count=5):
    folder = tmp_path / "study_evidence"
    folder.mkdir()
    proto = tmp_path / "study_protocol.json"
    spec = protocol()
    write(proto, spec)
    protocol_sha = sha256(proto.read_bytes()).hexdigest()
    index = {"schema_version": 1, "epoch_id": EPOCH, "experiment_id": STUDY,
             "as_of": CUTOFF, "protocol_sha256": protocol_sha, "entries": []}
    for i in range(count):
        entry = record(i)
        name = entry["record_id"] + ".json"
        path = folder / name
        write(path, entry)
        index["entries"].append({"record_id": entry["record_id"],
                                  "relative_path": name, "file_sha256": sha256(path.read_bytes()).hexdigest()})
    idx = tmp_path / "study_index.json"
    write(idx, index)
    return proto, idx, folder, spec, index


def revise(root, index, doc, i, changes, *, rehash=True):
    entry = doc["entries"][i]
    p = root / entry["relative_path"]
    obj = json.loads(p.read_text(encoding="utf-8"))
    obj.update(changes)
    write(p, obj)
    if rehash:
        entry["file_sha256"] = sha256(p.read_bytes()).hexdigest()
        write(index, doc)


def revise_protocol(proto, idx, spec, doc, changes, *, rehash=True):
    spec.update(changes)
    write(proto, spec)
    if rehash:
        doc["protocol_sha256"] = sha256(proto.read_bytes()).hexdigest()
        write(idx, doc)


def assess(proto=None, index=None, root=None, as_of=CUTOFF):
    return assess_stage6_research_evaluation(
        contracts_file=CONTRACTS, manifest_file=MANIFEST,
        epoch_id=EPOCH, as_of=as_of,
        protocol_file=proto, sample_index=index, evidence_root=root,
    )


def scores(tmp_path, count=5):
    proto, idx, root, _, _ = fixture(tmp_path, count=count)
    return assess(proto, idx, root)


# P6-17: registered definitions and predeclared benchmarks

def test_p617_baseline_procedure_is_fixed():
    r = assess()
    assert [x["name"] for x in r["benchmark_definitions"]] == list(BENCHMARKS)
    assert r["static_council"]["seats"] == 31
    assert r["static_council"]["primary_seats_evaluated_by_contract"] == 28
    assert r["static_council"]["source_families"] == 13


def test_p617_no_candidate_is_not_performance():
    r = assess()
    assert r["protocol_candidate"]["status"] == "NOT_SUPPLIED"
    assert r["candidate_sample"]["status"] == "NOT_SUPPLIED"
    assert r["exploratory_local_metrics"] is None
    assert r["final_action"] == "HOLD"


def test_p617_valid_local_predeclared_protocol_and_sample(tmp_path):
    r = scores(tmp_path)
    assert r["protocol_candidate"]["status"] == "LOCAL_PREDECLARED_PROTOCOL_CLAIM_UNATTESTED"
    assert r["candidate_sample"]["locally_hash_matched_samples"] == 5
    assert r["candidate_sample"]["locally_scoreable_samples"] == 5
    assert r["exploratory_local_metrics"]["score_status"] == "LOCAL_HYPOTHETICAL_EXPLORATORY_ONLY"


def test_p617_protocol_hash_and_manifest_bound(tmp_path):
    proto, idx, root, spec, data = fixture(tmp_path)
    revise_protocol(proto, idx, spec, data, {"council_manifest_sha256": "f" * 64})
    with pytest.raises(ResearchEvaluationError, match="manifest differs"):
        assess(proto, idx, root)


def test_p617_protocol_freeze_must_precede_evaluation(tmp_path):
    proto, idx, root, spec, data = fixture(tmp_path)
    revise_protocol(proto, idx, spec, data, {"claimed_freeze_receipt_at": "2026-10-27T15:01:00Z"})
    with pytest.raises(ResearchEvaluationError, match="freeze/receipt"):
        assess(proto, idx, root)


def test_p617_future_freeze_claim_invalid(tmp_path):
    proto, idx, root, spec, data = fixture(tmp_path)
    revise_protocol(proto, idx, spec, data, {"claimed_freeze_at": "2026-11-10T10:00:00Z"})
    with pytest.raises(ResearchEvaluationError, match="freeze/receipt"):
        assess(proto, idx, root)


def test_p617_duplicate_benchmarks_refused(tmp_path):
    proto, idx, root, spec, data = fixture(tmp_path)
    revise_protocol(proto, idx, spec, data, {"benchmarks": ["NO_TRADE", "ALWAYS_LONG"]})
    with pytest.raises(ResearchEvaluationError, match="benchmarks/regimes"):
        assess(proto, idx, root)


def test_p617_negative_cost_assumption_refused(tmp_path):
    proto, idx, root, spec, data = fixture(tmp_path)
    revise_protocol(proto, idx, spec, data, {"assumed_roundtrip_cost_pips": "-1"})
    with pytest.raises(ResearchEvaluationError, match="Out-of-range"):
        assess(proto, idx, root)


def test_p617_duplicate_stress_grid_refused(tmp_path):
    proto, idx, root, spec, data = fixture(tmp_path)
    revise_protocol(proto, idx, spec, data, {"stress_extra_cost_pips": ["1", "1"]})
    with pytest.raises(ResearchEvaluationError, match="stress grid"):
        assess(proto, idx, root)


@pytest.mark.parametrize("value", [0, 1, 257, True, "4"])
def test_p617_min_sample_count_strict(tmp_path, value):
    proto, idx, root, spec, data = fixture(tmp_path)
    revise_protocol(proto, idx, spec, data, {"min_samples": value})
    with pytest.raises(ResearchEvaluationError, match="minimum sample"):
        assess(proto, idx, root)


def test_p617_not_enough_samples_no_numeric_score(tmp_path):
    r = scores(tmp_path, count=3)
    assert r["candidate_sample"]["study_minimum_samples_met"] is False
    assert r["exploratory_local_metrics"] is None
    assert "LOCAL_SAMPLE_BELOW_PREDECLARED_MINIMUM" in r["blockers"]


def test_p617_uncompleted_window_disallows_score(tmp_path):
    proto, idx, root, spec, data = fixture(tmp_path)
    revise_protocol(proto, idx, spec, data, {"evaluation_end_at": "2026-11-02T17:00:00Z"})
    result = assess(proto, idx, root)
    assert result["exploratory_local_metrics"] is None
    assert "EVALUATION_WINDOW_NOT_COMPLETED" in result["blockers"]


def test_p617_sample_without_protocol_refused(tmp_path):
    proto, idx, root, _, _ = fixture(tmp_path)
    with pytest.raises(ResearchEvaluationError, match="without study protocol"):
        assess(index=idx, root=root)


def test_p617_protocol_without_index_makes_no_scores(tmp_path):
    proto, idx, root, _, _ = fixture(tmp_path)
    r = assess(proto=proto)
    assert r["protocol_candidate"]["status"].startswith("LOCAL_")
    assert r["exploratory_local_metrics"] is None


def test_p617_protocol_index_sha_mismatch_rejected(tmp_path):
    proto, idx, root, spec, doc = fixture(tmp_path)
    doc["protocol_sha256"] = "f" * 64
    write(idx, doc)
    with pytest.raises(ResearchEvaluationError, match="index/protocol"):
        assess(proto, idx, root)


def test_p617_unexpected_protocol_fields_refused(tmp_path):
    proto, idx, root, spec, doc = fixture(tmp_path)
    revise_protocol(proto, idx, spec, doc, {"certified_profitable": True})
    with pytest.raises(ResearchEvaluationError, match="schema"):
        assess(proto, idx, root)


def test_p617_wrong_epoch_refused():
    with pytest.raises(ResearchEvaluationError, match="epoch mismatch"):
        assess_stage6_research_evaluation(
            contracts_file=CONTRACTS, manifest_file=MANIFEST,
            epoch_id="f" * 64, as_of=CUTOFF,
        )


# P6-18: vote identities, correlation, disagreement, risk veto and challenges

def test_p618_primary_disagreement_and_correlation_reported(tmp_path):
    m = scores(tmp_path)["exploratory_local_metrics"]
    assert m["mean_primary_directional_disagreement"] is not None
    assert m["pairwise_correlations_with_variation"] > 0
    assert m["high_absolute_correlation_pair_count"] > 0
    assert m["risk_veto_count"] == 1
    assert m["devils_advocate_challenge_count"] == 1


def test_p618_veto_inconsistent_strategy_refused(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    revise(root, idx, doc, 3, {"final_research_action": "SHORT"})
    with pytest.raises(ResearchEvaluationError, match="Risk veto"):
        assess(proto, idx, root)


def test_p618_governance_vote_not_counted_as_primary(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    row = json.loads((root / "fixture_00.json").read_text())
    votes = row["primary_votes"]
    votes.append({"seat": 29, "agent_id": "devils_advocate", "direction": "ABSTAIN", "confidence": "0"})
    revise(root, idx, doc, 0, {"primary_votes": votes})
    with pytest.raises(ResearchEvaluationError, match="Exactly 28"):
        assess(proto, idx, root)


def test_p618_wrong_primary_seat_identity_refused(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    row = json.loads((root / "fixture_00.json").read_text())
    row["primary_votes"][0]["agent_id"] = "not_trend_master"
    revise(root, idx, doc, 0, {"primary_votes": row["primary_votes"]})
    with pytest.raises(ResearchEvaluationError, match="vote identity"):
        assess(proto, idx, root)


def test_p618_duplicate_primary_seat_refused(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    row = json.loads((root / "fixture_00.json").read_text())
    row["primary_votes"][1]["seat"] = 1
    revise(root, idx, doc, 0, {"primary_votes": row["primary_votes"]})
    with pytest.raises(ResearchEvaluationError, match="vote identity"):
        assess(proto, idx, root)


def test_p618_vote_confidence_bounded(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    row = json.loads((root / "fixture_00.json").read_text())
    row["primary_votes"][0]["confidence"] = "1.1"
    revise(root, idx, doc, 0, {"primary_votes": row["primary_votes"]})
    with pytest.raises(ResearchEvaluationError, match="confidence outside"):
        assess(proto, idx, root)


def test_p618_governance_flags_must_be_booleans(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    revise(root, idx, doc, 0, {"risk_veto": 1})
    with pytest.raises(ResearchEvaluationError, match="governance flags"):
        assess(proto, idx, root)


def test_p618_consensus_action_conflict_refused(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    revise(root, idx, doc, 0, {"final_research_action": "SHORT"})
    with pytest.raises(ResearchEvaluationError, match="conflicts with consensus"):
        assess(proto, idx, root)


def test_p618_unadmitted_orders_claim_refused(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    revise(root, idx, doc, 0, {"research_only_no_orders": False})
    with pytest.raises(ResearchEvaluationError, match="governance flags"):
        assess(proto, idx, root)


def test_p618_no_operational_council_invocation(tmp_path):
    r = scores(tmp_path)
    assert r["static_council"]["governance_stages_executed"] is False
    assert r["council_invoked"] is False
    assert r["external_source_families_independently_verified"] == 0


# P6-19: released/outcome chronology, heldout and calibration

def test_p619_brier_calibration_and_regimes_scored_locally(tmp_path):
    m = scores(tmp_path)["exploratory_local_metrics"]
    assert m["brier_probability_up"] is not None
    assert sum(x["sample_count"] for x in m["calibration_bins"]) == 4
    assert len(m["calibration_bins"]) == 5
    assert len(m["strategies"]["COUNCIL_FINAL"]["per_regime"]) == 4
    assert m["neutral_label_count"] == 1
    assert m["temporal_calibration_drift_exploratory"] is not None
    assert m["temporal_calibration_drift_exploratory"]["drift_statistically_established"] is False
    assert m["strategies"]["COUNCIL_FINAL"]["directional_accuracy_wilson_95_local"] is not None


def test_p619_future_registration_receipt_refused(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    revise(root, idx, doc, 0, {"claimed_registration_receipt_at": "2026-10-27T15:03:01Z"})
    with pytest.raises(ResearchEvaluationError, match="registered before"):
        assess(proto, idx, root)


def test_p619_horizon_cannot_change_after_freeze(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    revise(root, idx, doc, 0, {"horizon_end_at": "2026-10-27T15:09:00Z"})
    with pytest.raises(ResearchEvaluationError, match="horizon differs"):
        assess(proto, idx, root)


def test_p619_released_actual_future_leak_refused(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    revise(root, idx, doc, 0, {"outcome_quote_at": "2026-10-27T15:07:59Z"})
    with pytest.raises(ResearchEvaluationError, match="Outcome horizon"):
        assess(proto, idx, root)


def test_p619_outcome_unavailable_before_receipt_refused(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    revise(root, idx, doc, 0, {"outcome_quote_available_at": "2026-10-27T15:09:00Z"})
    with pytest.raises(ResearchEvaluationError, match="Outcome horizon"):
        assess(proto, idx, root)


def test_p619_future_entry_quote_refused(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    revise(root, idx, doc, 0, {"entry_quote_available_at": "2026-10-27T15:04:00Z"})
    with pytest.raises(ResearchEvaluationError, match="future information"):
        assess(proto, idx, root)


def test_p619_prior_momentum_baseline_must_be_predecision(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    revise(root, idx, doc, 0, {"prior_signal_available_at": "2026-10-27T15:03:01Z"})
    with pytest.raises(ResearchEvaluationError, match="future information"):
        assess(proto, idx, root)


def test_p619_probability_must_be_one_or_less(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    revise(root, idx, doc, 0, {"consensus_probability_up": "1.01"})
    with pytest.raises(ResearchEvaluationError, match="probability outside"):
        assess(proto, idx, root)


def test_p619_wilson_interval_is_descriptive_not_evidence(tmp_path):
    m = scores(tmp_path)["exploratory_local_metrics"]
    c = m["strategies"]["ALWAYS_LONG"]["directional_accuracy_wilson_95_local"]
    assert 0 <= float(c["lower"]) <= float(c["upper"]) <= 1
    assert c["interval_assumption"] == "iid_binomial_EXPLORATORY_ONLY"


def test_p619_uncertainty_sample_limited_no_claim(tmp_path):
    r = scores(tmp_path)
    assert not r["independent_holdout_certified"]
    assert not r["independent_outcomes_certified"]
    assert not r["statistical_superiority_or_net_profit_established"]
    assert not r["exploratory_local_metrics"]["heldout_externally_verified"]


def test_p619_unknown_regime_refused(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    revise(root, idx, doc, 0, {"regime": "CRASH"})
    with pytest.raises(ResearchEvaluationError, match="Unrecognized regime"):
        assess(proto, idx, root)


# P6-20: hypothetical cost stress, integrity, determinism, safety

def test_p620_no_trade_baseline_zero_return(tmp_path):
    m = scores(tmp_path)["exploratory_local_metrics"]["strategies"]["NO_TRADE"]
    assert m["hypothetical_actions"] == 0
    assert m["mean_hypothetical_gross_pips"] == "0.000000"
    assert m["mean_hypothetical_net_assumed_cost_pips"] == "0.000000"
    assert m["directional_accuracy"] is None


def test_p620_cost_stress_worsens_non_hold_scores(tmp_path):
    m = scores(tmp_path)["exploratory_local_metrics"]["strategies"]["COUNCIL_FINAL"]
    costs = [float(x["average_hypothetical_net_pips"]) for x in m["net_sensitivity"]]
    assert costs[0] > costs[1] > costs[2]
    assert m["hypothetical_actions"] == 4
    assert m["hold_actions"] == 1


def test_p620_stress_only_local_estimates(tmp_path):
    m = scores(tmp_path)["exploratory_local_metrics"]
    assert m["net_of_actual_broker_costs_verified"] is False
    assert m["profitable_or_statistically_superior_strategy_established"] is False
    assert m["compared_to_baselines_externally_validated"] is False


def test_p620_tampered_payload_without_rehash_rejected(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    revise(root, idx, doc, 0, {"entry_mid": "1.09"}, rehash=False)
    with pytest.raises(ResearchEvaluationError, match="SHA-256 mismatch"):
        assess(proto, idx, root)


def test_p620_index_wrong_epoch_refused(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    doc["epoch_id"] = "f" * 64
    write(idx, doc)
    with pytest.raises(ResearchEvaluationError, match="index/protocol"):
        assess(proto, idx, root)


def test_p620_duplicate_prediction_refused(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    revise(root, idx, doc, 1, {"prediction_id": "%064x" % 1})
    with pytest.raises(ResearchEvaluationError, match="Duplicate prediction"):
        assess(proto, idx, root)


def test_p620_out_of_order_samples_refused(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    doc["entries"][0], doc["entries"][1] = doc["entries"][1], doc["entries"][0]
    write(idx, doc)
    with pytest.raises(ResearchEvaluationError, match="out of order"):
        assess(proto, idx, root)


@pytest.mark.parametrize("unsafe", ["../secret", "C:/secret", "/etc/passwd", "a//b", "./x", "a/../b", "dir\\x"])
def test_p620_unsafe_paths_refused(tmp_path, unsafe):
    proto, idx, root, _, doc = fixture(tmp_path)
    doc["entries"][0]["relative_path"] = unsafe
    write(idx, doc)
    with pytest.raises(ResearchEvaluationError, match="Unsafe relative path"):
        assess(proto, idx, root)


def test_p620_symlinked_sample_refused(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    link = root / "symlink.json"
    try:
        link.symlink_to(root / "fixture_00.json")
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation unavailable on Windows")
    doc["entries"][0]["relative_path"] = link.name
    write(idx, doc)
    with pytest.raises(ResearchEvaluationError, match="Symlinked"):
        assess(proto, idx, root)


def test_p620_malformed_json_duplicate_keys_refused(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    idx.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(ResearchEvaluationError, match="Duplicate JSON"):
        assess(proto, idx, root)


def test_p620_self_declared_approval_field_refused(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    revise(root, idx, doc, 0, {"independently_certified_profitable": True})
    with pytest.raises(ResearchEvaluationError, match="sample schema"):
        assess(proto, idx, root)


def test_p620_same_source_bytes_and_cutoff_stable_digest(tmp_path):
    proto, idx, root, _, doc = fixture(tmp_path)
    a = assess(proto, idx, root)
    b = assess(proto, idx, root)
    assert a == b
    assert a["report_sha256"] == _digest({k: v for k, v in a.items() if k != "report_sha256"})


def test_p620_no_side_effects():
    a = assess()
    for field in (
        "files_written", "network_accessed", "database_accessed", "broker_connected",
        "collector_started", "council_invoked", "model_training_authorized",
        "settlement_invoked", "historical_epoch_rebound", "paper_orders_authorized",
        "live_orders_authorized",
    ):
        assert a[field] is False
    assert a["read_only"] is True and a["final_action"] == "HOLD"


def test_p620_cli_required_flags():
    with pytest.raises(SystemExit) as err:
        run(["--epoch-id", EPOCH, "--as-of", CUTOFF])
    assert err.value.code == 2


def test_p620_cli_bad_epoch_blocks(capsys):
    rc = run(["--read-only-source", "--read-only-evidence", "--epoch-id", "bad", "--as-of", CUTOFF])
    assert rc == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_p620_cli_json_reports_hold(capsys):
    rc = run(["--read-only-source", "--read-only-evidence",
              "--epoch-id", EPOCH, "--as-of", CUTOFF,
              "--contracts-file", str(CONTRACTS), "--manifest-file", str(MANIFEST), "--json"])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["final_action"] == "HOLD"
    assert result["external_evaluation_gates_verified"] == 0
    assert result["exploratory_local_metrics"] is None


@pytest.mark.parametrize("stamp", ["invalid", "2026-10-30T12:00:00", "2026-10-30T15:00:00+03:00"])
def test_p620_explicit_utc_only(stamp):
    with pytest.raises(ResearchEvaluationError):
        _utc(stamp)

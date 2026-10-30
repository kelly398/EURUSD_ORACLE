
"""Synthetic-only read-only checks for Stage 6 Council context P6-09..12."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

import backend.runtime.stage6_council_context_readiness as ctx
from backend.runtime.stage6_council_context_readiness import (
    CouncilContextError, EPOCH, FAMILIES, _digest, _utc,
    inspect_static_council, inspect_point_in_time_index,
    inspect_saved_envelope, assess_stage6_council_context, run,
)

ASOF = "2026-10-30T11:00:00Z"
DECISION = "2026-10-27T15:03:00Z"
ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "backend/agents/contracts.py"
MANIFEST = ROOT / "backend/agents/council_manifest.py"


def assess(**kwargs):
    return assess_stage6_council_context(
        contracts_file=CONTRACTS, manifest_file=MANIFEST,
        epoch_id=EPOCH, decision_at=DECISION, as_of=ASOF, **kwargs)


def static(**kwargs):
    return inspect_static_council(
        contracts_file=kwargs.get("contracts_file", CONTRACTS),
        manifest_file=kwargs.get("manifest_file", MANIFEST),
    )


def write_json(path, obj):
    path.write_text(json.dumps(obj, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def doc_fixture(tmp_path, *, family="ticks", kind="OBSERVED", observed="2026-10-27T15:01:00Z"):
    root = tmp_path / "artifacts"
    root.mkdir()
    raw = b"SYNTHETIC_FIXTURE_ONLY_DOES_NOT_AUTHENTICATE_ANY_PROVIDER"
    (root / "fixture.bin").write_bytes(raw)
    item = {
        "record_id": "tick_v1", "family": family,
        "evidence_kind": kind, "relative_path": "fixture.bin",
        "file_sha256": sha256(raw).hexdigest(),
        "observed_at": observed,
        "first_available_at": "2026-10-27T15:02:00Z",
        "captured_at": "2026-10-27T15:02:01Z",
        "supersedes": None, "dependencies": [],
    }
    doc = {"schema_version": 1, "epoch_id": EPOCH,
           "decision_at": DECISION, "entries": [item]}
    index = tmp_path / "index.json"
    write_json(index, doc)
    return root, index, doc


def append_record(root, doc, *, record_id, family, kind, observed,
                  first_available="2026-10-27T15:02:00Z", captured="2026-10-27T15:02:01Z",
                  supersedes=None, dependencies=()):
    raw = ("SYNTHETIC_ARTIFACT_" + record_id).encode()
    path = record_id + ".bin"
    (root / path).write_bytes(raw)
    entry = dict(
        record_id=record_id, family=family, evidence_kind=kind,
        relative_path=path, file_sha256=sha256(raw).hexdigest(),
        observed_at=observed, first_available_at=first_available,
        captured_at=captured, supersedes=supersedes,
        dependencies=list(dependencies),
    )
    doc["entries"].append(entry)
    return entry


def review(root, index):
    return inspect_point_in_time_index(
        index, evidence_root=root, epoch_id=EPOCH, decision_at=DECISION,
    )


def reported_envelope(tmp_path):
    obj = {
        "assessment_version": "SYNTHETIC_STAGE6_ENVELOPE_REPORT",
        "epoch_id": EPOCH, "decision_at": DECISION,
        "as_of": ASOF, "final_action": "HOLD", "read_only": True,
        "independent_source_families_verified": 0,
        "external_provenance_gates_verified": 0,
        "candidate_evidence": {"status": "NOT_SUPPLIED"},
    }
    obj["report_sha256"] = _digest(obj)
    file = tmp_path / "report.json"
    write_json(file, obj)
    return file, obj


def test_p609_31_seats_13_source_families():
    record = static()
    assert record["static_seats"] == 31
    assert record["static_source_families"] == 13
    assert record["families"] == list(FAMILIES)
    assert [x["seat"] for x in record["seats"]] == list(range(1, 32))
    assert [x["stage"] for x in record["seats"]] == ["PRIMARY"] * 28 + ["GOVERNANCE"] * 2 + ["SYNTHESIS"]
    assert not record["source_executed_or_imported"]


def test_p609_direct_and_derived_source_contracts():
    by = {x["agent_id"]: x for x in static()["seats"]}
    assert by["trend_master"]["required_source_families"] == ["candles", "market_state"]
    assert by["order_flow"]["required_source_families"] == ["market_state", "ticks"]
    assert by["rate_differential"]["required_source_families"] == ["ecb_data", "fed_data"]
    assert by["ml_tree_ensemble"]["needs_model_artifact_lineage"]
    assert "feature_vector" in by["neural_network"]["derived_labels"]
    assert by["risk_officer"]["required_source_families"] == ["market_state", "risk_limits", "risk_state"]
    assert by["chief_strategist"]["required_source_families"] == ["market_state", "reliability", "synthesis_policy"]


def test_p609_governance_stages_not_external_source_families():
    by = {x["agent_id"]: x for x in static()["seats"]}
    assert by["devils_advocate"]["needs_internal_stage_opinions"] == ["primary_agent_opinions"]
    assert by["chief_strategist"]["needs_internal_stage_opinions"] == ["governance_opinions", "primary_agent_opinions"]
    assert "model_artifact" not in FAMILIES
    assert "primary_agent_opinions" not in FAMILIES


def test_p609_deterministic_static_hashes():
    assert static() == static()
    assert len(static()["contracts_sha256"]) == 64
    assert len(static()["manifest_sha256"]) == 64


def test_p609_static_file_cannot_be_missing(tmp_path):
    with pytest.raises(CouncilContextError, match="ordinary"):
        static(manifest_file=tmp_path / "missing.py")


def test_p609_changed_31_seat_manifest_fails(tmp_path):
    p = tmp_path / "manifest.py"
    p.write_bytes(MANIFEST.read_bytes().replace(b"seat_number=31,", b"seat_number=32,"))
    with pytest.raises(CouncilContextError, match="ordering/stage"):
        static(manifest_file=p)


def test_p609_unknown_derived_label_refused(tmp_path):
    p = tmp_path / "manifest.py"
    p.write_bytes(MANIFEST.read_bytes().replace(b'"candles",', b'"unknown_data_source",', 1))
    with pytest.raises(CouncilContextError, match="seat fields"):
        static(manifest_file=p)


def test_p609_missing_agentcontext_family_refused(tmp_path):
    p = tmp_path / "contracts.py"
    p.write_bytes(CONTRACTS.read_bytes().replace(b"    synthesis_policy: ", b"    renamed_policy: ", 1))
    with pytest.raises(CouncilContextError, match="Required 13"):
        static(contracts_file=p)


def test_p610_absent_index_is_not_local_proof():
    r = assess()
    assert r["point_in_time_candidate_context"]["status"] == "NOT_SUPPLIED"
    assert r["locally_complete_source_family_count"] == 0
    assert r["source_families_independently_verified"] == 0


def test_p610_local_tick_hash_match_not_attestation(tmp_path):
    root, index, _ = doc_fixture(tmp_path)
    r = assess(context_index=index, evidence_root=root)
    assert r["locally_complete_source_family_count"] == 1
    assert r["point_in_time_candidate_context"]["records_locally_hash_matched"] == 1
    assert r["source_families_independently_verified"] == 0
    assert all(not x["dispatch_allowed"] for x in r["seat_readiness"])


def test_p610_absent_index_with_root_rejected(tmp_path):
    with pytest.raises(CouncilContextError, match="root without index"):
        inspect_point_in_time_index(None, evidence_root=tmp_path, epoch_id=EPOCH, decision_at=DECISION)


def test_p610_index_without_root_rejected(tmp_path):
    _, index, _ = doc_fixture(tmp_path)
    with pytest.raises(CouncilContextError, match="root required"):
        inspect_point_in_time_index(index, evidence_root=None, epoch_id=EPOCH, decision_at=DECISION)


def test_p610_unknown_epoch_refused():
    with pytest.raises(CouncilContextError, match="epoch mismatch"):
        assess_stage6_council_context(contracts_file=CONTRACTS, manifest_file=MANIFEST,
                                      epoch_id="a"*64, decision_at=DECISION, as_of=ASOF)


def test_p610_future_decision_refused():
    with pytest.raises(CouncilContextError, match="after review"):
        assess_stage6_council_context(contracts_file=CONTRACTS, manifest_file=MANIFEST,
                                      epoch_id=EPOCH, decision_at=ASOF, as_of=DECISION)


def test_p610_future_capture_refused(tmp_path):
    root, index, doc = doc_fixture(tmp_path)
    doc["entries"][0]["captured_at"] = "2026-10-27T15:05:00Z"
    write_json(index, doc)
    with pytest.raises(CouncilContextError, match="Future"):
        review(root, index)


def test_p610_future_observed_tick_refused(tmp_path):
    root, index, doc = doc_fixture(tmp_path, observed="2026-10-28T10:00:00Z")
    with pytest.raises(CouncilContextError, match="Future observed"):
        review(root, index)


def test_p610_announced_future_schedule_permitted(tmp_path):
    root, index, doc = doc_fixture(tmp_path, family="economic_calendar", kind="SCHEDULED", observed="2026-11-01T13:00:00Z")
    assert review(root, index)["local_families_with_visible_candidate"] == 1


def test_p610_announced_future_effective_policy_permitted(tmp_path):
    root, index, doc = doc_fixture(tmp_path, family="fed_data", kind="EFFECTIVE", observed="2026-11-01T13:00:00Z")
    assert review(root, index)["local_families_with_visible_candidate"] == 1


def test_p610_future_actual_forbidden(tmp_path):
    root, index, doc = doc_fixture(tmp_path, family="economic_calendar", kind="OBSERVED", observed="2026-11-01T13:00:00Z")
    with pytest.raises(CouncilContextError, match="Future observed"):
        review(root, index)


def test_p610_wrong_family_kind_refused(tmp_path):
    root, index, doc = doc_fixture(tmp_path, family="ticks", kind="EFFECTIVE")
    with pytest.raises(CouncilContextError, match="incompatible kind"):
        review(root, index)


def test_p610_observed_timestamp_required(tmp_path):
    root, index, doc = doc_fixture(tmp_path, observed=None)
    with pytest.raises(CouncilContextError, match="timestamp missing"):
        review(root, index)


def test_p610_availability_after_capture_rejected(tmp_path):
    root, index, doc = doc_fixture(tmp_path)
    doc["entries"][0]["first_available_at"] = "2026-10-27T15:02:03Z"
    write_json(index, doc)
    with pytest.raises(CouncilContextError, match="retroactive"):
        review(root, index)


def test_p610_evidence_content_tamper_refused(tmp_path):
    root, index, doc = doc_fixture(tmp_path)
    (root / "fixture.bin").write_bytes(b"TAMPERED")
    with pytest.raises(CouncilContextError, match="SHA-256"):
        review(root, index)


@pytest.mark.parametrize("path", ["../private", "C:/system", "/absolute", "./file", "a//b", "a/../b", "dir\\file"])
def test_p610_path_traversal_refused(tmp_path, path):
    root, index, doc = doc_fixture(tmp_path)
    doc["entries"][0]["relative_path"] = path
    write_json(index, doc)
    with pytest.raises(CouncilContextError, match="Unsafe relative"):
        review(root, index)


def test_p610_symlink_rejected(tmp_path):
    root, index, doc = doc_fixture(tmp_path)
    link = root / "link.bin"
    try:
        link.symlink_to(root / "fixture.bin")
    except (OSError, NotImplementedError):
        pytest.skip("Windows symlink permission unavailable")
    doc["entries"][0]["relative_path"] = link.name
    write_json(index, doc)
    with pytest.raises(CouncilContextError, match="Symlinked"):
        review(root, index)


def test_p610_duplicate_id_refused(tmp_path):
    root, index, doc = doc_fixture(tmp_path)
    doc["entries"].append(dict(doc["entries"][0]))
    write_json(index, doc)
    with pytest.raises(CouncilContextError, match="Duplicate record"):
        review(root, index)


def test_p610_unhashable_family_refused(tmp_path):
    root, index, doc = doc_fixture(tmp_path)
    doc["entries"][0]["family"] = ["ticks"]
    write_json(index, doc)
    with pytest.raises(CouncilContextError, match="unrecognized family"):
        review(root, index)


def test_p610_unknown_index_version_refused(tmp_path):
    root, index, doc = doc_fixture(tmp_path)
    doc["schema_version"] = True
    write_json(index, doc)
    with pytest.raises(CouncilContextError, match="schema"):
        review(root, index)


def test_p611_derived_dep_chain_valid(tmp_path):
    root, index, doc = doc_fixture(tmp_path)
    append_record(root, doc, record_id="bar", family="candles", kind="OBSERVED",
                  observed="2026-10-27T15:00:00Z")
    append_record(root, doc, record_id="state", family="market_state", kind="DERIVED", observed=None,
                  first_available="2026-10-27T15:02:02Z", captured="2026-10-27T15:02:03Z",
                  dependencies=("tick_v1", "bar"))
    write_json(index, doc)
    result = review(root, index)
    assert result["dependency_edges_checked"] == 2
    assert result["local_families_with_visible_candidate"] == 3
    assert result["independently_verified_source_families"] == 0


def test_p611_derived_without_parent_refused(tmp_path):
    root, index, doc = doc_fixture(tmp_path, family="market_state", kind="DERIVED", observed=None)
    with pytest.raises(CouncilContextError, match="declare dependencies"):
        review(root, index)


def test_p611_missing_derived_parent_refused(tmp_path):
    root, index, doc = doc_fixture(tmp_path)
    append_record(root, doc, record_id="state", family="market_state", kind="DERIVED", observed=None,
                  dependencies=("MISSING",))
    write_json(index, doc)
    with pytest.raises(CouncilContextError, match="Unresolved dependency"):
        review(root, index)


def test_p611_derived_availability_before_parent_refused(tmp_path):
    root, index, doc = doc_fixture(tmp_path)
    append_record(root, doc, record_id="state", family="market_state", kind="DERIVED", observed=None,
                  first_available="2026-10-27T15:01:00Z", captured="2026-10-27T15:02:01Z",
                  dependencies=("tick_v1",))
    write_json(index, doc)
    with pytest.raises(CouncilContextError, match="precedes its dependency"):
        review(root, index)


def test_p611_self_dependency_refused(tmp_path):
    root, index, doc = doc_fixture(tmp_path)
    doc["entries"][0]["dependencies"] = ["tick_v1"]
    write_json(index, doc)
    with pytest.raises(CouncilContextError, match="Invalid lineage"):
        review(root, index)


def test_p611_dependency_cycle_refused(tmp_path):
    root, index, doc = doc_fixture(tmp_path)
    append_record(root, doc, record_id="risk", family="risk_state", kind="DERIVED", observed=None,
                  dependencies=("tick_v1",))
    doc["entries"][0]["dependencies"] = ["risk"]
    write_json(index, doc)
    with pytest.raises(CouncilContextError, match="cycle"):
        review(root, index)


def test_p611_strict_revision_chain(tmp_path):
    root, index, doc = doc_fixture(tmp_path)
    append_record(root, doc, record_id="tick_v2", family="ticks", kind="OBSERVED",
                  observed="2026-10-27T15:01:00Z",
                  first_available="2026-10-27T15:02:02Z", captured="2026-10-27T15:02:04Z",
                  supersedes="tick_v1")
    write_json(index, doc)
    result = review(root, index)
    assert result["family_latest_visible_record_ids"]["ticks"] == "tick_v2"
    assert result["family_candidate_counts"]["ticks"] == 2


def test_p611_multiple_unlinked_versions_refused(tmp_path):
    root, index, doc = doc_fixture(tmp_path)
    append_record(root, doc, record_id="second", family="ticks", kind="OBSERVED",
                  observed="2026-10-27T15:01:00Z")
    write_json(index, doc)
    with pytest.raises(CouncilContextError, match="revision roots"):
        review(root, index)


def test_p611_revision_time_reversal_refused(tmp_path):
    root, index, doc = doc_fixture(tmp_path)
    append_record(root, doc, record_id="tick_v2", family="ticks", kind="OBSERVED",
                  observed="2026-10-27T15:01:00Z", supersedes="tick_v1")
    write_json(index, doc)
    with pytest.raises(CouncilContextError, match="must increase"):
        review(root, index)


def test_p611_cross_family_revision_refused(tmp_path):
    root, index, doc = doc_fixture(tmp_path)
    append_record(root, doc, record_id="bar", family="candles", kind="OBSERVED",
                  observed="2026-10-27T15:00:00Z", supersedes="tick_v1")
    write_json(index, doc)
    with pytest.raises(CouncilContextError, match="another family"):
        review(root, index)


def test_p611_forked_revision_refused(tmp_path):
    root, index, doc = doc_fixture(tmp_path)
    for name in ("v2a", "v2b"):
        append_record(root, doc, record_id=name, family="ticks", kind="OBSERVED",
                      observed="2026-10-27T15:01:00Z",
                      first_available="2026-10-27T15:02:02Z", captured="2026-10-27T15:02:04Z",
                      supersedes="tick_v1")
    write_json(index, doc)
    with pytest.raises(CouncilContextError, match="Forked"):
        review(root, index)


def test_p612_optional_existing_source_report_hash_check(tmp_path):
    file, _ = reported_envelope(tmp_path)
    r = assess(envelope_report=file)
    assert r["previous_stage6_source_envelope"]["status"] == "LOCAL_SAVED_REPORT_DIGEST_MATCHES_UNATTESTED"
    assert r["previous_stage6_source_envelope"]["independently_attested"] is False


def test_p612_saved_source_report_tamper_refused(tmp_path):
    file, data = reported_envelope(tmp_path)
    data["final_action"] = "BUY"
    write_json(file, data)
    with pytest.raises(CouncilContextError, match="HOLD/local contract"):
        assess(envelope_report=file)


def test_p612_saved_source_report_self_promote_refused(tmp_path):
    file, data = reported_envelope(tmp_path)
    data["independent_source_families_verified"] = 13
    data["report_sha256"] = _digest({k: v for k, v in data.items() if k != "report_sha256"})
    write_json(file, data)
    with pytest.raises(CouncilContextError, match="HOLD/local contract"):
        assess(envelope_report=file)


def test_p612_performance_and_execution_always_blocked():
    r = assess()
    assert r["final_action"] == "HOLD"
    assert r["council_seats_operationally_admitted"] == 0
    assert r["fed_production_source_admitted"] is False
    assert r["risk_veto_executed"] is False
    assert r["council_replay_executed"] is False
    assert r["point_in_time_snapshot_created"] is False
    assert r["performance_verified"] is False
    assert r["research_ready"] is False
    assert all(not seat["external_admission_approved"] for seat in r["seat_readiness"])


def test_p612_no_mutation_or_io():
    before = (CONTRACTS.read_bytes(), MANIFEST.read_bytes())
    r = assess()
    assert before == (CONTRACTS.read_bytes(), MANIFEST.read_bytes())
    for name in ("files_written", "network_accessed", "database_accessed",
                 "broker_connected", "collector_started", "historical_epoch_rebound",
                 "council_invoked", "training_authorized", "paper_execution_authorized",
                 "live_execution_authorized", "model_artifacts_loaded"):
        assert r[name] is False
    assert r["read_only"]


def test_p612_report_is_deterministic():
    a = assess()
    b = assess()
    assert a == b
    assert a["report_sha256"] == _digest({k: v for k, v in a.items() if k != "report_sha256"})


def test_p612_cli_requires_two_readonly_flags():
    with pytest.raises(SystemExit) as e:
        run(["--epoch-id", EPOCH, "--decision-at", DECISION, "--as-of", ASOF])
    assert e.value.code == 2


def test_p612_cli_json_prints_honest_hold(capsys):
    assert run(["--read-only-source", "--read-only-evidence",
                "--contracts-file", str(CONTRACTS), "--manifest-file", str(MANIFEST),
                "--epoch-id", EPOCH, "--decision-at", DECISION,
                "--as-of", ASOF, "--json"]) == 0
    obj = json.loads(capsys.readouterr().out)
    assert obj["static_seat_count"] == 31
    assert obj["static_input_family_count"] == 13
    assert obj["final_action"] == "HOLD"


def test_p612_cli_invalid_epoch_fails_closed(capsys):
    assert run(["--read-only-source", "--read-only-evidence",
                "--epoch-id", "bad", "--decision-at", DECISION,
                "--as-of", ASOF]) == 1
    assert "BLOCKED" in capsys.readouterr().out


@pytest.mark.parametrize("stamp", ["bad", "2026-10-27T15:03:00", "2026-10-27T18:03:00+03:00"])
def test_p612_strict_utc(stamp):
    with pytest.raises(CouncilContextError):
        _utc(stamp)

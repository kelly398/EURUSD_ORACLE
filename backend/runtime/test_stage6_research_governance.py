
"""P6-01..04: offline-only governance and evidence-claim safety tests."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime.stage6_research_governance import (
    ENTRY_FIELDS, EXPECTED_POLICY, GATES, HISTORICAL_EPOCH,
    HISTORICAL_POLICY_SHA256, ResearchGovernanceError,
    TRUST_CLASSES, _hash, _utc, assess_stage6_governance,
    inspect_historical_anchor, inspect_local_claims, run,
)

ROOT = Path(__file__).parents[2]
POLICY = ROOT / "docs/clock_profiles/hfm_forward_20261027.json"
ASOF = datetime(2026, 10, 30, 10, 0, tzinfo=timezone.utc)


def opts(**kw):
    return dict(policy_file=POLICY, epoch_id=HISTORICAL_EPOCH,
                as_of=ASOF.isoformat(), **kw)


def assess(**kw):
    return assess_stage6_governance(**opts(**kw))


def write(p, obj):
    p.write_text(json.dumps(obj), encoding="utf-8")


def fixture_claim(tmp_path, *, trust="LOCAL_ONLY", gate="G01", count=1):
    root = tmp_path / "evidence"
    root.mkdir()
    entries = []
    for n in range(count):
        path = root / ("raw-%d.bin" % n)
        path.write_bytes(b"SYNTHETIC LOCAL DATA NOT ORIGINAL EVIDENCE " + bytes([n]))
        entries.append({
            "gate_id": gate, "record_id": "fixture-%d" % n,
            "relative_path": path.name, "file_sha256": sha256(path.read_bytes()).hexdigest(),
            "claimed_issuer": "SYNTHETIC_TEST_ONLY",
            "claimed_received_at": (ASOF - timedelta(minutes=2)).isoformat(),
            "trust_class": trust,
        })
    index = tmp_path / "index.json"
    data = {"schema_version": 1, "epoch_id": HISTORICAL_EPOCH,
            "as_of": ASOF.isoformat(), "entries": entries}
    write(index, data)
    return root, index, data


def review(root, index):
    return inspect_local_claims(index, evidence_root=root, as_of=ASOF.isoformat())


def mutate(index, data):
    write(index, data)


def test_p601_policy_bytes_original_pin():
    r = inspect_historical_anchor(POLICY)
    assert r["status"] == "LOCAL_BYTES_MATCH_PINNED_HISTORICAL_POLICY"
    assert r["policy_sha256"] == HISTORICAL_POLICY_SHA256
    assert r["historical_admission"] is False
    assert r["epoch_id_derived_from_policy_json"] is False


def test_p601_policy_is_archived_pinned_object():
    assert json.loads(POLICY.read_text()) == EXPECTED_POLICY
    assert "epoch_id" not in EXPECTED_POLICY


def test_p601_policy_digest_tampering_rejected(tmp_path):
    p = tmp_path / "tampered.json"
    p.write_bytes(POLICY.read_bytes() + b" ")
    with pytest.raises(ResearchGovernanceError, match="SHA-256 mismatch"):
        inspect_historical_anchor(p)


def test_p601_missing_policy_fails_closed(tmp_path):
    with pytest.raises(ResearchGovernanceError, match="regular"):
        inspect_historical_anchor(tmp_path / "missing.json")


def test_p601_wrong_epoch_fails_closed():
    with pytest.raises(ResearchGovernanceError, match="Historical epoch mismatch"):
        assess_stage6_governance(policy_file=POLICY, epoch_id="a" * 64,
                                 as_of=ASOF.isoformat())


def test_p601_authority_denial_defaults():
    r = assess()["policy_contract"]
    assert r["mode"] == "RESEARCH_OFFLINE_ONLY"
    assert r["operational_action"] == "HOLD"
    for key in (
        "historical_epoch_rebinding_allowed", "collector_restart_allowed",
        "council_dispatch_allowed", "model_training_allowed", "paper_orders_allowed",
        "live_orders_allowed", "policy_self_authorizes_operational_mode_changes",
        "stage5_commit_independently_verified_in_this_assessment",
    ):
        assert r[key] is False


def test_p601_policy_code_pinned_not_current_verified():
    r = inspect_historical_anchor(POLICY)
    assert len(r["pinned_clock_implementation_sha256"]) == 64
    assert not r["independent_broker_clock_attestation"]


def test_p602_all_local_trust_classes_are_unattested():
    assert TRUST_CLASSES == ("LOCAL_ONLY", "EXTERNAL_CLAIM_UNVERIFIED", "QUARANTINED")
    assert "INDEPENDENTLY_ATTESTED" not in TRUST_CLASSES


@pytest.mark.parametrize("trust", TRUST_CLASSES)
def test_p602_claims_cannot_promote_trust(tmp_path, trust):
    root, index, _ = fixture_claim(tmp_path, trust=trust)
    r = assess(index_file=index, evidence_root=root)
    assert r["candidate_evidence"]["locally_hash_matched_documents"] == 1
    assert r["candidate_evidence"]["trust_class_counts"][trust] == 1
    assert r["external_gates_verified"] == 0
    assert all(not x["approved"] for x in r["acceptance_matrix"])


def test_p602_missing_index_no_evidence_inferred():
    r = inspect_local_claims(None, evidence_root=None, as_of=ASOF.isoformat())
    assert r["status"] == "NOT_SUPPLIED"
    assert r["independently_verified_documents"] == 0


def test_p602_root_without_index_rejected(tmp_path):
    with pytest.raises(ResearchGovernanceError, match="without index"):
        inspect_local_claims(None, evidence_root=tmp_path, as_of=ASOF.isoformat())


def test_p602_index_without_root_rejected(tmp_path):
    _, index, _ = fixture_claim(tmp_path)
    with pytest.raises(ResearchGovernanceError, match="requires evidence root"):
        inspect_local_claims(index, evidence_root=None, as_of=ASOF.isoformat())


def test_p602_self_certifying_trust_refused(tmp_path):
    root, index, data = fixture_claim(tmp_path)
    data["entries"][0]["trust_class"] = "INDEPENDENTLY_ATTESTED"
    mutate(index, data)
    with pytest.raises(ResearchGovernanceError, match="Self-assigned"):
        review(root, index)


def test_p602_additional_approved_field_refused(tmp_path):
    root, index, data = fixture_claim(tmp_path)
    data["entries"][0]["approved"] = True
    mutate(index, data)
    with pytest.raises(ResearchGovernanceError, match="contract"):
        review(root, index)


def test_p602_original_local_file_digest_tamper_refused(tmp_path):
    root, index, _ = fixture_claim(tmp_path)
    (root / "raw-0.bin").write_bytes(b"CHANGED")
    with pytest.raises(ResearchGovernanceError, match="SHA-256 mismatch"):
        review(root, index)


def test_p602_index_scope_epoch_mismatch(tmp_path):
    root, index, data = fixture_claim(tmp_path)
    data["epoch_id"] = "b" * 64
    mutate(index, data)
    with pytest.raises(ResearchGovernanceError, match="schema/epoch"):
        review(root, index)


def test_p602_duplicate_claim_record_rejected(tmp_path):
    root, index, data = fixture_claim(tmp_path)
    data["entries"].append(dict(data["entries"][0]))
    mutate(index, data)
    with pytest.raises(ResearchGovernanceError, match="duplicate"):
        review(root, index)


def test_p602_unknown_gate_refused(tmp_path):
    root, index, data = fixture_claim(tmp_path)
    data["entries"][0]["gate_id"] = "G99"
    mutate(index, data)
    with pytest.raises(ResearchGovernanceError, match="Unknown gate"):
        review(root, index)


def test_p602_unhashable_gate_fails_closed(tmp_path):
    root, index, data = fixture_claim(tmp_path)
    data["entries"][0]["gate_id"] = ["G01"]
    mutate(index, data)
    with pytest.raises(ResearchGovernanceError, match="Unknown gate"):
        review(root, index)


def test_p602_claim_after_cutoff_refused(tmp_path):
    root, index, data = fixture_claim(tmp_path)
    data["entries"][0]["claimed_received_at"] = (ASOF + timedelta(seconds=1)).isoformat()
    mutate(index, data)
    with pytest.raises(ResearchGovernanceError, match="after review cutoff"):
        review(root, index)


def test_p602_non_utc_claim_refused(tmp_path):
    root, index, data = fixture_claim(tmp_path)
    data["entries"][0]["claimed_received_at"] = "2026-10-30T13:00:00+03:00"
    mutate(index, data)
    with pytest.raises(ResearchGovernanceError, match="zero-offset"):
        review(root, index)


def test_p602_bad_claim_schema_version_refused(tmp_path):
    root, index, data = fixture_claim(tmp_path)
    data["schema_version"] = True
    mutate(index, data)
    with pytest.raises(ResearchGovernanceError, match="schema/epoch"):
        review(root, index)


def test_p602_duplicate_json_fields_rejected(tmp_path):
    root, index, _ = fixture_claim(tmp_path)
    index.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(ResearchGovernanceError, match="Duplicate"):
        review(root, index)


def test_p602_nonfinite_json_rejected(tmp_path):
    root, index, _ = fixture_claim(tmp_path)
    index.write_text('{"foo":NaN}', encoding="utf-8")
    with pytest.raises(ResearchGovernanceError, match="Nonfinite"):
        review(root, index)


@pytest.mark.parametrize("relative", ["../secret", "C:/secret", "/absolute", "a/../b", "sub\\file", "a//b", "./file"])
def test_p602_unsafe_relative_paths_refused(tmp_path, relative):
    root, index, data = fixture_claim(tmp_path)
    data["entries"][0]["relative_path"] = relative
    mutate(index, data)
    with pytest.raises(ResearchGovernanceError, match="path"):
        review(root, index)


def test_p602_symlinked_source_refused(tmp_path):
    root, index, data = fixture_claim(tmp_path)
    link = root / "link.bin"
    try:
        link.symlink_to(root / "raw-0.bin")
    except (OSError, NotImplementedError):
        pytest.skip("Symlink unavailable on this Windows host")
    data["entries"][0]["relative_path"] = link.name
    mutate(index, data)
    with pytest.raises(ResearchGovernanceError, match="Symlinked"):
        review(root, index)


def test_p602_no_missing_artifact_generated(tmp_path):
    root, index, _ = fixture_claim(tmp_path)
    (root / "raw-0.bin").unlink()
    with pytest.raises(ResearchGovernanceError, match="regular"):
        review(root, index)
    assert not (root / "raw-0.bin").exists()


def test_p603_registry_is_exactly_eleven_unique_gates():
    assert len(GATES) == 11
    assert [gate_id for gate_id, *_ in GATES] == ["G%02d" % n for n in range(1, 12)]
    assert len({name for _, name, *_ in GATES}) == 11


def test_p603_matrix_provides_independent_role_and_proof():
    matrix = assess()["acceptance_matrix"]
    assert len(matrix) == 11
    assert all(x["required_review_role"] and x["minimum_independent_proof"] for x in matrix)
    assert all(x["status"] == "NOT_INDEPENDENTLY_VERIFIED" for x in matrix)


def test_p603_multiple_local_claims_still_not_approved(tmp_path):
    root, index, _ = fixture_claim(tmp_path, gate="G07", count=3)
    matrix = assess(index_file=index, evidence_root=root)["acceptance_matrix"]
    assert next(x for x in matrix if x["gate_id"] == "G07")["locally_hash_matched_candidate_count"] == 3
    assert all(not x["approved"] for x in matrix)


def test_p603_all_13_families_still_unverified():
    r = assess()
    assert r["independent_source_families_verified"] == 0
    assert r["research_operational_readiness"] == "BLOCKED_EXTERNAL_EVIDENCE"


def test_p603_unverified_gates_are_one_to_one_blockers():
    r = assess()
    assert len([x for x in r["blockers"] if x.startswith("EXTERNAL_GATE_")]) == 11
    assert r["external_gates_total"] == 11
    assert r["external_gates_verified"] == 0


def test_p604_report_hash_stable():
    a = assess()
    b = assess()
    assert a == b
    assert a["report_sha256"] == _hash({k: v for k, v in a.items() if k != "report_sha256"})


def test_p604_no_side_effects():
    r = assess()
    for field in (
        "files_written", "git_mutated", "database_accessed", "network_accessed",
        "broker_connected", "collector_started", "historical_epoch_rebound",
        "council_invoked", "training_authorized", "paper_execution_authorized",
        "live_execution_authorized", "research_admission_authorized",
    ):
        assert r[field] is False
    assert r["read_only"] is True and r["final_action"] == "HOLD"


def test_p604_reads_do_not_mutate_historical_policy():
    before = POLICY.read_bytes()
    assess()
    assert POLICY.read_bytes() == before


def test_p604_cli_requires_two_readonly_flags():
    with pytest.raises(SystemExit) as exc:
        run(["--epoch-id", HISTORICAL_EPOCH, "--as-of", ASOF.isoformat()])
    assert exc.value.code == 2


def test_p604_cli_bad_epoch_fails_closed(capsys):
    code = run([
        "--read-only-source", "--read-only-evidence", "--epoch-id", "BAD",
        "--policy-file", str(POLICY), "--as-of", ASOF.isoformat(),
    ])
    assert code == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_p604_cli_json_uses_only_hold(capsys):
    result = run([
        "--read-only-source", "--read-only-evidence", "--epoch-id", HISTORICAL_EPOCH,
        "--policy-file", str(POLICY), "--as-of", ASOF.isoformat(), "--json",
    ])
    assert result == 0
    obj = json.loads(capsys.readouterr().out)
    assert obj["external_gates_verified"] == 0
    assert obj["final_action"] == "HOLD"


@pytest.mark.parametrize("value", ["invalid", "2026-10-30T10:00:00", "2026-10-30T13:00:00+03:00"])
def test_p604_utc_only(value):
    with pytest.raises(ResearchGovernanceError):
        _utc(value)

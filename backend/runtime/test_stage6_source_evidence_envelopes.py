
"""P6-05..08: synthetic file-backed market/macro source integrity tests."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime.stage6_research_governance import HISTORICAL_EPOCH
from backend.runtime.stage6_source_evidence_envelopes import (
    KINDS, SourceEnvelopeError, _empty, _hash,
    assess_stage6_source_envelopes, inspect_existing_source_index, run,
)

ROOT = Path(__file__).parents[2]
POLICY = ROOT / "docs/clock_profiles/hfm_forward_20261027.json"
DECISION = datetime(2026, 10, 27, 15, 3, tzinfo=timezone.utc)
ASOF = datetime(2026, 10, 30, 10, tzinfo=timezone.utc)
BEFORE = DECISION - timedelta(seconds=30)


def stamp(value):
    return value.isoformat()


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def options(**kwargs):
    return dict(policy_file=POLICY, epoch_id=HISTORICAL_EPOCH,
                decision_at=stamp(DECISION), as_of=stamp(ASOF), **kwargs)


def candidate(root, *, rid="a", kind="tick", source_id=None,
              revision=1, predecessor=None, event=None, published=None,
              available=None, received=None, stream="EURUSD", sequence=1,
              uncertainty=150, reference="LOCAL_UTC_CLAIM", receipt=False):
    event = event if event is not None else BEFORE - timedelta(seconds=5)
    published = published if published is not None else BEFORE
    available = available if available is not None else published + timedelta(seconds=1)
    received = received if received is not None else available + timedelta(seconds=1)
    market = kind in {"tick", "m5_bar", "cross_asset_quote"}
    if not market:
        sequence = None
    name = rid + ".bin"
    p = root / name
    p.write_bytes(("SYNTHETIC PAYLOAD " + rid).encode())
    record = {
        "record_id": rid,
        "source_kind": kind,
        "provider_id": "TEST_PROVIDER",
        "source_record_id": source_id or rid,
        "stream_id": stream,
        "revision": revision,
        "revision_of": predecessor,
        "event_at": stamp(event),
        "source_published_at": stamp(published),
        "source_available_at": stamp(available),
        "local_received_at": stamp(received),
        "native_timestamp": "2026.10.27 17:02:25 TEST_WALL",
        "clock_reference": reference,
        "clock_uncertainty_ms": uncertainty,
        "sequence": sequence,
        "interval_seconds": 300 if kind == "m5_bar" else None,
        "relative_path": name,
        "file_sha256": sha256(p.read_bytes()).hexdigest(),
        "custody_receipt_path": None,
        "custody_receipt_sha256": None,
        "trust_class": "LOCAL_ONLY",
    }
    if receipt:
        rp = root / (rid + "-claimed-receipt.bin")
        rp.write_bytes(("SYNTHETIC UNVERIFIED RECEIPT " + rid).encode())
        record["custody_receipt_path"] = rp.name
        record["custody_receipt_sha256"] = sha256(rp.read_bytes()).hexdigest()
    return record


def bundle(tmp_path, *records):
    root = tmp_path / "sources"
    root.mkdir()
    if not records:
        records = (candidate(root),)
    idx = tmp_path / "index.json"
    document = {"schema_version": 1, "epoch_id": HISTORICAL_EPOCH,
                "decision_at": stamp(DECISION), "as_of": stamp(ASOF),
                "records": list(records)}
    write(idx, document)
    return root, idx, document


def one(tmp_path, **kwargs):
    root = tmp_path / "sources"
    root.mkdir()
    row = candidate(root, **kwargs)
    idx = tmp_path / "index.json"
    doc = {"schema_version": 1, "epoch_id": HISTORICAL_EPOCH,
           "decision_at": stamp(DECISION), "as_of": stamp(ASOF),
           "records": [row]}
    write(idx, doc)
    return root, idx, doc


def inspect(root, idx):
    return inspect_existing_source_index(
        idx, evidence_root=root, epoch_id=HISTORICAL_EPOCH,
        decision_at=stamp(DECISION), as_of=stamp(ASOF))


def update(idx, doc):
    write(idx, doc)


def multi(tmp_path, definitions):
    root = tmp_path / "sources"
    root.mkdir()
    rows = [candidate(root, **x) for x in definitions]
    idx = tmp_path / "index.json"
    doc = {"schema_version": 1, "epoch_id": HISTORICAL_EPOCH,
           "decision_at": stamp(DECISION), "as_of": stamp(ASOF),
           "records": rows}
    write(idx, doc)
    return root, idx, doc


def test_p605_eight_kinds_and_trust_unattested():
    assert set(KINDS) == {"tick", "m5_bar", "cross_asset_quote", "cot_release",
                          "ecb_release", "fed_release", "economic_schedule", "economic_actual"}
    assert _empty()["provider_origins_independently_verified"] == 0


@pytest.mark.parametrize("kind", list(KINDS))
def test_p605_every_source_kind_local_only(tmp_path, kind):
    extra = {}
    if kind in {"ecb_release", "fed_release", "economic_schedule"}:
        extra["event"] = DECISION + timedelta(days=1)
    root, idx, _ = one(tmp_path, kind=kind, **extra)
    result = inspect(root, idx)
    assert result["locally_hash_matched_payloads"] == 1
    assert result["record_counts_by_kind"][kind] == 1
    assert result["provider_origins_independently_verified"] == 0
    assert result["provider_payloads_semantically_decoded"] is False


def test_p605_m5_requires_300_seconds(tmp_path):
    root, idx, doc = one(tmp_path, kind="m5_bar")
    doc["records"][0]["interval_seconds"] = 60
    update(idx, doc)
    with pytest.raises(SourceEnvelopeError, match="M5 interval"):
        inspect(root, idx)


def test_p605_market_sequence_is_mandatory(tmp_path):
    root, idx, doc = one(tmp_path)
    doc["records"][0]["sequence"] = None
    update(idx, doc)
    with pytest.raises(SourceEnvelopeError, match="sequence"):
        inspect(root, idx)


def test_p605_unrecognized_kind_refused(tmp_path):
    root, idx, doc = one(tmp_path)
    doc["records"][0]["source_kind"] = "invented"
    update(idx, doc)
    with pytest.raises(SourceEnvelopeError, match="unknown source"):
        inspect(root, idx)


def test_p605_wrong_epoch_refused(tmp_path):
    root, idx, doc = one(tmp_path)
    doc["epoch_id"] = "a" * 64
    update(idx, doc)
    with pytest.raises(SourceEnvelopeError, match="schema/epoch"):
        inspect(root, idx)


def test_p605_source_file_tamper_refused(tmp_path):
    root, idx, doc = one(tmp_path)
    (root / doc["records"][0]["relative_path"]).write_bytes(b"TAMPER")
    with pytest.raises(SourceEnvelopeError, match="payload SHA-256 mismatch"):
        inspect(root, idx)


def test_p605_duplicate_record_identity_refused(tmp_path):
    root, idx, doc = one(tmp_path)
    doc["records"].append(dict(doc["records"][0]))
    update(idx, doc)
    with pytest.raises(SourceEnvelopeError, match="Duplicate record"):
        inspect(root, idx)


def test_p606_future_calendar_schedule_is_not_future_actual(tmp_path):
    root, idx, _ = one(tmp_path, kind="economic_schedule", event=DECISION + timedelta(days=7))
    assert inspect(root, idx)["locally_hash_matched_payloads"] == 1


def test_p606_future_effective_ecb_fed_allowed_only_if_known_before_decision(tmp_path):
    root, idx, _ = one(tmp_path, kind="fed_release", event=DECISION + timedelta(days=30))
    assert inspect(root, idx)["locally_hash_matched_payloads"] == 1


@pytest.mark.parametrize("kind", ["economic_actual", "cot_release", "tick", "m5_bar", "cross_asset_quote"])
def test_p606_future_observation_refused(tmp_path, kind):
    root, idx, _ = one(tmp_path, kind=kind, event=DECISION + timedelta(seconds=1))
    with pytest.raises(SourceEnvelopeError, match="Unreleased observation"):
        inspect(root, idx)


def test_p606_publication_after_decision_refused(tmp_path):
    root, idx, _ = one(tmp_path, published=DECISION + timedelta(seconds=1),
                        available=DECISION + timedelta(seconds=2), received=DECISION + timedelta(seconds=3))
    with pytest.raises(SourceEnvelopeError, match="not available"):
        inspect(root, idx)


def test_p606_availability_before_publication_refused(tmp_path):
    root, idx, doc = one(tmp_path)
    doc["records"][0]["source_available_at"] = stamp(BEFORE - timedelta(seconds=1))
    update(idx, doc)
    with pytest.raises(SourceEnvelopeError, match="not available"):
        inspect(root, idx)


def test_p606_non_utc_publication_refused(tmp_path):
    root, idx, doc = one(tmp_path)
    doc["records"][0]["source_published_at"] = "2026-10-27T18:02:30+03:00"
    update(idx, doc)
    with pytest.raises(ValueError, match="zero-offset"):
        inspect(root, idx)


def test_p606_good_revision_chain(tmp_path):
    root, idx, _ = multi(tmp_path, [
        {"rid": "revision1", "source_id": "rate-event", "kind": "fed_release", "event": BEFORE},
        {"rid": "revision2", "source_id": "rate-event", "kind": "fed_release", "event": BEFORE,
         "revision": 2, "predecessor": "revision1", "published": BEFORE + timedelta(seconds=4),
         "available": BEFORE + timedelta(seconds=5), "received": BEFORE + timedelta(seconds=6)},
    ])
    assert inspect(root, idx)["revision_links_checked"] == 1


def test_p606_missing_revision_predecessor_refused(tmp_path):
    root, idx, _ = one(tmp_path, revision=2)
    with pytest.raises(SourceEnvelopeError, match="exact predecessor"):
        inspect(root, idx)


def test_p606_revision_backdating_refused(tmp_path):
    root, idx, _ = multi(tmp_path, [
        {"rid": "v1", "source_id": "r", "kind": "fed_release", "event": BEFORE},
        {"rid": "v2", "source_id": "r", "kind": "fed_release", "event": BEFORE,
         "revision": 2, "predecessor": "v1"},
    ])
    with pytest.raises(SourceEnvelopeError, match="backdated"):
        inspect(root, idx)


def test_p606_revision_identity_changed_refused(tmp_path):
    root, idx, _ = multi(tmp_path, [
        {"rid": "v1", "source_id": "r", "kind": "fed_release", "event": BEFORE},
        {"rid": "v2", "source_id": "r", "kind": "fed_release", "event": BEFORE + timedelta(seconds=1),
         "revision": 2, "predecessor": "v1", "published": BEFORE + timedelta(seconds=4),
         "available": BEFORE + timedelta(seconds=5), "received": BEFORE + timedelta(seconds=6)},
    ])
    with pytest.raises(SourceEnvelopeError, match="changed event"):
        inspect(root, idx)


def test_p606_duplicate_provider_source_version_refused(tmp_path):
    root, idx, _ = multi(tmp_path, [
        {"rid": "v1", "source_id": "r", "kind": "fed_release", "event": BEFORE},
        {"rid": "same", "source_id": "r", "kind": "fed_release", "event": BEFORE},
    ])
    with pytest.raises(SourceEnvelopeError, match="Duplicate provider source"):
        inspect(root, idx)


def test_p607_market_sequence_gap_reported_without_synthetic_fill(tmp_path):
    root, idx, _ = multi(tmp_path, [
        {"rid": "t1", "event": BEFORE - timedelta(seconds=2), "sequence": 1},
        {"rid": "t2", "event": BEFORE - timedelta(seconds=1), "sequence": 3},
    ])
    result = inspect(root, idx)
    assert result["sequence_gaps_detected"] == 1
    assert result["locally_hash_matched_payloads"] == 2
    assert result["quality_clear_for_independent_admission"] is False


def test_p607_m5_interval_gap_reported(tmp_path):
    root, idx, _ = multi(tmp_path, [
        {"rid": "b1", "kind": "m5_bar", "event": BEFORE - timedelta(seconds=600), "sequence": 5},
        {"rid": "b2", "kind": "m5_bar", "event": BEFORE, "sequence": 6},
    ])
    assert inspect(root, idx)["m5_time_gaps_detected"] == 1


def test_p607_stale_tick_is_warning_not_automatically_filled(tmp_path):
    root, idx, _ = one(tmp_path, event=BEFORE - timedelta(hours=1))
    r = inspect(root, idx)
    assert r["stale_market_observations_detected"] == 1
    assert any(x["code"] == "MARKET_OBSERVATION_STALE" for x in r["quality_findings"])


def test_p607_uncertainty_and_unknown_clock_are_visible(tmp_path):
    root, idx, _ = one(tmp_path, uncertainty=6000, reference="UNKNOWN")
    r = inspect(root, idx)
    assert r["clock_claims_above_2000ms"] == 1
    assert r["clock_reference_unknown"] == 1
    assert r["clock_offsets_independently_verified"] == 0


def test_p607_negative_uncertainty_refused(tmp_path):
    root, idx, doc = one(tmp_path)
    doc["records"][0]["clock_uncertainty_ms"] = -1
    update(idx, doc)
    with pytest.raises(SourceEnvelopeError, match="uncertainty"):
        inspect(root, idx)


def test_p607_boolean_uncertainty_refused(tmp_path):
    root, idx, doc = one(tmp_path)
    doc["records"][0]["clock_uncertainty_ms"] = False
    update(idx, doc)
    with pytest.raises(SourceEnvelopeError, match="uncertainty"):
        inspect(root, idx)


def test_p607_fake_clock_independent_claim_refused(tmp_path):
    root, idx, doc = one(tmp_path)
    doc["records"][0]["clock_reference"] = "INDEPENDENTLY_ATTESTED"
    update(idx, doc)
    with pytest.raises(SourceEnvelopeError, match="Self-certified clock"):
        inspect(root, idx)


def test_p608_local_custody_file_is_not_independent_proof(tmp_path):
    root, idx, _ = one(tmp_path, receipt=True)
    r = inspect(root, idx)
    assert r["locally_hash_matched_custody_claims"] == 1
    assert r["custody_signatures_cryptographically_verified"] == 0


def test_p608_custody_tamper_refused(tmp_path):
    root, idx, doc = one(tmp_path, receipt=True)
    (root / doc["records"][0]["custody_receipt_path"]).write_bytes(b"TAMPER")
    with pytest.raises(SourceEnvelopeError, match="custody receipt"):
        inspect(root, idx)


def test_p608_custody_path_without_sha_refused(tmp_path):
    root, idx, doc = one(tmp_path, receipt=True)
    doc["records"][0]["custody_receipt_sha256"] = None
    update(idx, doc)
    with pytest.raises(SourceEnvelopeError, match="travel together"):
        inspect(root, idx)


def test_p608_self_certification_forbidden(tmp_path):
    root, idx, doc = one(tmp_path)
    doc["records"][0]["trust_class"] = "INDEPENDENTLY_ATTESTED"
    update(idx, doc)
    with pytest.raises(SourceEnvelopeError, match="Self-certifying"):
        inspect(root, idx)


@pytest.mark.parametrize("relative", ["../x", "C:/x", "/x", "a/../b", "a\\b", "a//b", "./x"])
def test_p608_unsafe_paths_refused(tmp_path, relative):
    root, idx, doc = one(tmp_path)
    doc["records"][0]["relative_path"] = relative
    update(idx, doc)
    with pytest.raises(ValueError, match="path"):
        inspect(root, idx)


def test_p608_symlink_refused(tmp_path):
    root, idx, doc = one(tmp_path)
    link = root / "link.bin"
    try:
        link.symlink_to(root / "a.bin")
    except (OSError, NotImplementedError):
        pytest.skip("Host does not permit symlink creation")
    doc["records"][0]["relative_path"] = link.name
    update(idx, doc)
    with pytest.raises(ValueError, match="Symlinked"):
        inspect(root, idx)


def test_p608_index_not_supplied_means_unknown_not_verified():
    r = assess_stage6_source_envelopes(**options())
    assert r["candidate_evidence"]["status"] == "NOT_SUPPLIED"
    assert r["independent_source_families_verified"] == 0
    assert r["research_ready"] is False
    assert r["final_action"] == "HOLD"


def test_p608_existing_index_still_hold(tmp_path):
    root, idx, _ = one(tmp_path, receipt=True)
    r = assess_stage6_source_envelopes(**options(index_file=idx, evidence_root=root))
    assert r["candidate_evidence"]["locally_hash_matched_payloads"] == 1
    assert r["external_provenance_gates_verified"] == 0
    assert r["final_action"] == "HOLD"


def test_p608_deterministic_hash():
    a = assess_stage6_source_envelopes(**options())
    b = assess_stage6_source_envelopes(**options())
    assert a == b
    assert a["report_sha256"] == _hash({k: v for k, v in a.items() if k != "report_sha256"})


def test_p608_policy_unchanged_after_read_only():
    before = POLICY.read_bytes()
    assess_stage6_source_envelopes(**options())
    assert POLICY.read_bytes() == before


def test_p608_no_operational_side_effects():
    r = assess_stage6_source_envelopes(**options())
    for k in ("files_written", "network_accessed", "database_accessed", "broker_connected",
              "collector_started", "council_invoked", "training_authorized",
              "paper_execution_authorized", "live_execution_authorized", "historical_epoch_rebound"):
        assert r[k] is False


def test_p608_cli_requires_read_only_flags():
    with pytest.raises(SystemExit) as e:
        run(["--epoch-id", HISTORICAL_EPOCH, "--decision-at", stamp(DECISION), "--as-of", stamp(ASOF)])
    assert e.value.code == 2


def test_p608_cli_json_hold(capsys):
    assert run(["--read-only-source", "--read-only-evidence", "--epoch-id", HISTORICAL_EPOCH,
                "--decision-at", stamp(DECISION), "--as-of", stamp(ASOF),
                "--policy-file", str(POLICY), "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["final_action"] == "HOLD"


def test_p608_cli_bad_epoch_fails_closed(capsys):
    assert run(["--read-only-source", "--read-only-evidence", "--epoch-id", "BAD",
                "--decision-at", stamp(DECISION), "--as-of", stamp(ASOF),
                "--policy-file", str(POLICY)]) == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_p608_empty_index_not_evidence(tmp_path):
    root, idx, doc = one(tmp_path)
    doc["records"] = []
    update(idx, doc)
    r = inspect(root, idx)
    assert r["status"] == "LOCAL_EMPTY_SOURCE_INDEX_UNATTESTED"
    assert r["locally_hash_matched_payloads"] == 0


def test_p608_missing_policy_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="regular"):
        assess_stage6_source_envelopes(**{**options(), "policy_file": tmp_path / "missing"})


def test_p608_index_without_root_refused(tmp_path):
    _, idx, _ = one(tmp_path)
    with pytest.raises(SourceEnvelopeError, match="requires root"):
        inspect_existing_source_index(idx, evidence_root=None, epoch_id=HISTORICAL_EPOCH,
                                      decision_at=stamp(DECISION), as_of=stamp(ASOF))

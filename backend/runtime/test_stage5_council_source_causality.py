
"""S5-13..16: synthetic-only source publication, revision and lineage tests."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime.stage5_council_source_causality import (
    FAMILIES, Stage5CouncilEvidenceError, _hash, _utc,
    assess_stage5_council_sources, inspect_council_contracts,
    inspect_point_in_time_index, run,
)

ROOT = Path(__file__).parents[2]
CONTRACTS = ROOT / "backend/agents/contracts.py"
MANIFEST = ROOT / "backend/agents/council_manifest.py"
EPOCH = "7" * 64
DECISION = datetime(
    2026, 10, 27, 15, 1, 43, tzinfo=timezone.utc
)
ASOF = datetime(
    2026, 10, 29, 19, 10, tzinfo=timezone.utc
)
BEFORE = DECISION - timedelta(minutes=20)


def utc(value):
    return value.isoformat()


def config(**extra):
    return dict(
        contracts_file=CONTRACTS,
        manifest_file=MANIFEST,
        epoch_id=EPOCH,
        decision_at=utc(DECISION),
        as_of=utc(ASOF),
        **extra,
    )


def _row(
    folder, *, family="ecb_data", kind="policy_rate",
    rid="source-1", source_id="ECB-OBS", version=1,
    previous=None, dependencies=None, published=None,
    available=None, ingested=None, event_at=None
):
    path = folder / (rid + ".txt")
    path.write_bytes(
        ("SYNTHETIC NO EXTERNAL ORIGIN " + rid).encode()
    )

    published = published or BEFORE
    available = available or published + timedelta(seconds=1)
    ingested = ingested or available + timedelta(seconds=1)

    if event_at is None:
        if kind in {"calendar_schedule", "policy_rate"}:
            event_at = DECISION + timedelta(days=1)
        else:
            event_at = BEFORE - timedelta(days=1)

    return {
        "family": family,
        "record_id": rid,
        "source_kind": kind,
        "source_record_id": source_id,
        "version": version,
        "revision_of": previous,
        "claimed_issuer": "SYNTHETIC-TEST",
        "claimed_published_at": utc(published),
        "claimed_available_at": utc(available),
        "claimed_ingested_at": utc(ingested),
        "economic_event_at": utc(event_at),
        "dependencies": list(dependencies or []),
        "relative_path": path.name,
        "file_sha256": sha256(path.read_bytes()).hexdigest(),
    }


def _index(tmp_path, records):
    idx = tmp_path / "candidate-index.json"
    _save(idx, {
        "schema_version": 1,
        "epoch_id": EPOCH,
        "decision_at": utc(DECISION),
        "as_of": utc(ASOF),
        "records": records,
    })
    return idx


def _save(path, data):
    path.write_text(
        json.dumps(data), encoding="utf-8"
    )


def _case(tmp_path, **kwargs):
    root = tmp_path / "evidence"
    root.mkdir()
    record = _row(root, **kwargs)
    idx = _index(tmp_path, [record])
    return root, idx, record


def _review(root, idx):
    return inspect_point_in_time_index(
        idx,
        evidence_root=root,
        epoch_id=EPOCH,
        decision_at=utc(DECISION),
        as_of=utc(ASOF),
    )


def test_s513_exact_13_known_families_and_31_seats():
    result = inspect_council_contracts(
        CONTRACTS, MANIFEST
    )
    assert len(FAMILIES) == 13
    assert result["expected_source_family_count"] == 13
    assert result["static_council_seat_count"] == 31
    assert result["data_families_independently_attested"] == 0
    assert result["source_files_executed"] is False


def test_s513_missing_agent_context_contract_refused(tmp_path):
    p = tmp_path / "contracts.py"
    p.write_text("class NotAgentContext: pass\n")

    with pytest.raises(
        Stage5CouncilEvidenceError, match="AgentContext"
    ):
        inspect_council_contracts(p, MANIFEST)


def test_s513_missing_family_refused(tmp_path):
    p = tmp_path / "contracts.py"
    p.write_text(
        CONTRACTS.read_text().replace(
            "    fed_data: tuple[",
            "    fed_missing: tuple[",
            1,
        )
    )

    with pytest.raises(
        Stage5CouncilEvidenceError, match="families"
    ):
        inspect_council_contracts(p, MANIFEST)


def test_s513_wrong_manifest_seat_refused(tmp_path):
    p = tmp_path / "manifest.py"
    p.write_text(
        MANIFEST.read_text().replace(
            "seat_number=1,", "seat_number=2,", 1
        )
    )

    with pytest.raises(
        Stage5CouncilEvidenceError, match="31 Council"
    ):
        inspect_council_contracts(CONTRACTS, p)


def test_s513_malformed_source_refused(tmp_path):
    p = tmp_path / "contracts.py"
    p.write_text("this is not python @@")

    with pytest.raises(
        Stage5CouncilEvidenceError, match="parsed"
    ):
        inspect_council_contracts(p, MANIFEST)


def test_s513_no_index_no_external_attestation():
    r = inspect_point_in_time_index(
        None,
        evidence_root=None,
        epoch_id=EPOCH,
        decision_at=utc(DECISION),
        as_of=utc(ASOF),
    )
    assert r["status"] == "NOT_SUPPLIED"
    assert r["families_with_candidates"] == 0
    assert r["independently_attested_source_families"] == 0


def test_s514_existing_hash_matched_economic_candidate_unattested(
    tmp_path,
):
    root, idx, _ = _case(tmp_path)
    r = _review(root, idx)

    assert r["records_locally_hash_matched"] == 1
    assert r["family_candidate_counts"]["ecb_data"] == 1
    assert (
        r["historical_publication_times_independently_verified"]
        is False
    )


def test_s514_future_effective_policy_rate_not_future_release(
    tmp_path,
):
    root, idx, _ = _case(
        tmp_path,
        event_at=DECISION + timedelta(days=10),
    )
    assert _review(root, idx)["records_locally_hash_matched"] == 1


def test_s514_preannounced_calendar_schedule_allowed(tmp_path):
    root, idx, _ = _case(
        tmp_path,
        family="economic_calendar",
        kind="calendar_schedule",
        rid="schedule",
    )
    assert _review(root, idx)["calendar_schedule_records"] == 1


def test_s514_future_calendar_actual_refused(tmp_path):
    root, idx, _ = _case(
        tmp_path,
        family="economic_calendar",
        kind="calendar_actual",
        rid="actual",
        event_at=DECISION + timedelta(seconds=1),
    )
    with pytest.raises(
        Stage5CouncilEvidenceError,
        match="Future economic actual",
    ):
        _review(root, idx)


def test_s514_released_actual_with_past_event_allowed(tmp_path):
    root, idx, _ = _case(
        tmp_path,
        family="economic_calendar",
        kind="calendar_actual",
        event_at=BEFORE - timedelta(seconds=5),
    )
    assert _review(root, idx)["calendar_actual_records"] == 1


def test_s514_calendar_actual_before_event_refused(tmp_path):
    root, idx, _ = _case(
        tmp_path,
        family="economic_calendar",
        kind="calendar_actual",
        event_at=BEFORE + timedelta(seconds=1),
    )
    with pytest.raises(
        Stage5CouncilEvidenceError,
        match="Future economic actual",
    ):
        _review(root, idx)


def test_s514_later_revision_ingested_after_decision_refused(
    tmp_path,
):
    root, idx, r = _case(tmp_path)
    r["claimed_ingested_at"] = utc(
        DECISION + timedelta(seconds=1)
    )
    _save(idx, {
        "schema_version": 1,
        "epoch_id": EPOCH,
        "decision_at": utc(DECISION),
        "as_of": utc(ASOF),
        "records": [r],
    })
    with pytest.raises(
        Stage5CouncilEvidenceError,
        match="unavailable at decision",
    ):
        _review(root, idx)


def test_s514_fabricated_early_available_late_publication_refused(
    tmp_path,
):
    root, idx, r = _case(tmp_path)
    r["claimed_available_at"] = utc(
        BEFORE - timedelta(seconds=1)
    )
    _save(idx, {
        "schema_version": 1,
        "epoch_id": EPOCH,
        "decision_at": utc(DECISION),
        "as_of": utc(ASOF),
        "records": [r],
    })
    with pytest.raises(
        Stage5CouncilEvidenceError,
        match="unavailable at decision",
    ):
        _review(root, idx)


def test_s514_cross_asset_observation_after_published_refused(
    tmp_path,
):
    root, idx, _ = _case(
        tmp_path,
        family="cross_asset_data",
        kind="cross_asset",
        event_at=DECISION - timedelta(minutes=1),
    )
    with pytest.raises(
        Stage5CouncilEvidenceError,
        match="Observation timestamp",
    ):
        _review(root, idx)


def test_s514_cot_observation_after_release_refused(tmp_path):
    root, idx, _ = _case(
        tmp_path,
        family="cot_data",
        kind="cot",
        event_at=BEFORE + timedelta(seconds=30),
    )
    with pytest.raises(
        Stage5CouncilEvidenceError,
        match="Observation timestamp",
    ):
        _review(root, idx)


def test_s514_bad_family_kind_pair_refused(tmp_path):
    root, idx, _ = _case(
        tmp_path,
        family="fed_data",
        kind="calendar_schedule",
    )
    with pytest.raises(
        Stage5CouncilEvidenceError,
        match="Source kind",
    ):
        _review(root, idx)


def test_s514_missing_required_event_timestamp_refused(tmp_path):
    root, idx, r = _case(tmp_path)
    r["economic_event_at"] = None
    _save(idx, {
        "schema_version": 1,
        "epoch_id": EPOCH,
        "decision_at": utc(DECISION),
        "as_of": utc(ASOF),
        "records": [r],
    })
    with pytest.raises(
        Stage5CouncilEvidenceError,
        match="event/effective",
    ):
        _review(root, idx)


def test_s514_revision_chain_reconciled_locally(tmp_path):
    root = tmp_path / "records"
    root.mkdir()

    a = _row(root, rid="rate-v1")
    b = _row(
        root, rid="rate-v2", version=2,
        previous="rate-v1",
        published=BEFORE + timedelta(minutes=1),
    )
    idx = _index(tmp_path, [a, b])
    r = _review(root, idx)

    assert r["release_revisions_linked"] == 1
    assert r["records_locally_hash_matched"] == 2


def test_s514_revision_without_predecessor_link_refused(
    tmp_path,
):
    root = tmp_path / "records"
    root.mkdir()

    a = _row(root, rid="rate-v1")
    b = _row(
        root, rid="rate-v2", version=2,
        previous=None,
        published=BEFORE + timedelta(minutes=1),
    )
    idx = _index(tmp_path, [a, b])

    with pytest.raises(
        Stage5CouncilEvidenceError, match="omitted"
    ):
        _review(root, idx)


def test_s514_revision_wrong_source_refused(tmp_path):
    root = tmp_path / "records"
    root.mkdir()

    a = _row(root, rid="rate-v1")
    b = _row(
        root,
        rid="rate-v2",
        source_id="OTHER",
        version=2,
        previous="rate-v1",
        published=BEFORE + timedelta(minutes=1),
    )
    idx = _index(tmp_path, [a, b])

    with pytest.raises(
        Stage5CouncilEvidenceError, match="lacks exact"
    ):
        _review(root, idx)


def test_s514_revision_same_release_time_refused(tmp_path):
    root = tmp_path / "records"
    root.mkdir()

    a = _row(root, rid="rate-v1")
    b = _row(
        root,
        rid="rate-v2",
        version=2,
        previous="rate-v1",
    )
    idx = _index(tmp_path, [a, b])

    with pytest.raises(
        Stage5CouncilEvidenceError,
        match="later publication",
    ):
        _review(root, idx)


def test_s515_derived_feature_requires_lineage(tmp_path):
    root, idx, _ = _case(
        tmp_path,
        family="market_state",
        kind="derived_feature",
    )
    with pytest.raises(
        Stage5CouncilEvidenceError,
        match="requires evidence",
    ):
        _review(root, idx)


def test_s515_lineage_dependency_closed_and_ordered(tmp_path):
    root = tmp_path / "records"
    root.mkdir()

    a = _row(
        root,
        family="ticks",
        kind="market",
        rid="tick-record",
        source_id="TICK",
    )
    b = _row(
        root,
        family="market_state",
        kind="derived_feature",
        rid="feature-record",
        source_id="FEATURE",
        dependencies=["tick-record"],
        published=BEFORE + timedelta(seconds=5),
    )
    idx = _index(tmp_path, [a, b])
    r = _review(root, idx)

    assert r["derived_lineage_edges_checked"] == 1
    assert r["families_with_candidates"] == 2
    assert (
        r["model_and_feature_origin_independently_verified"]
        is False
    )


def test_s515_model_artifact_lineage_closed(tmp_path):
    root = tmp_path / "records"
    root.mkdir()

    a = _row(
        root, family="ticks", kind="market",
        rid="tick-record", source_id="TICK",
    )
    b = _row(
        root,
        family="reliability",
        kind="model_artifact",
        rid="model-record",
        source_id="MODEL",
        dependencies=["tick-record"],
        published=BEFORE + timedelta(seconds=5),
    )
    assert _review(
        root, _index(tmp_path, [a, b])
    )["derived_lineage_edges_checked"] == 1


def test_s515_unresolved_dependency_refused(tmp_path):
    root, idx, _ = _case(
        tmp_path,
        family="market_state",
        kind="derived_feature",
        dependencies=["missing-record"],
    )
    with pytest.raises(
        Stage5CouncilEvidenceError, match="Missing derived"
    ):
        _review(root, idx)


def test_s515_dependency_with_later_receipt_refused(tmp_path):
    root = tmp_path / "records"
    root.mkdir()

    a = _row(
        root,
        family="ticks",
        kind="market",
        rid="tick-record",
        source_id="TICK",
        published=BEFORE + timedelta(minutes=5),
    )
    b = _row(
        root,
        family="market_state",
        kind="derived_feature",
        rid="feature-record",
        source_id="FEATURE",
        dependencies=["tick-record"],
    )
    with pytest.raises(
        Stage5CouncilEvidenceError, match="depends on later"
    ):
        _review(root, _index(tmp_path, [a, b]))


def test_s515_cyclic_feature_lineage_refused(tmp_path):
    root = tmp_path / "records"
    root.mkdir()

    a = _row(
        root,
        family="market_state",
        kind="derived_feature",
        rid="a",
        source_id="STATE-A",
        dependencies=["b"],
    )
    b = _row(
        root,
        family="market_state",
        kind="derived_feature",
        rid="b",
        source_id="STATE-B",
        dependencies=["a"],
    )
    with pytest.raises(
        Stage5CouncilEvidenceError, match="Cyclic"
    ):
        _review(root, _index(tmp_path, [a, b]))


def test_s515_duplicate_dependency_refused(tmp_path):
    root, idx, _ = _case(
        tmp_path,
        family="market_state",
        kind="derived_feature",
        dependencies=["x", "x"],
    )
    with pytest.raises(
        Stage5CouncilEvidenceError,
        match="dependencies invalid",
    ):
        _review(root, idx)


def test_s515_self_dependency_refused(tmp_path):
    root, idx, _ = _case(
        tmp_path,
        family="market_state",
        kind="derived_feature",
        dependencies=["source-1"],
    )
    with pytest.raises(
        Stage5CouncilEvidenceError,
        match="dependencies invalid",
    ):
        _review(root, idx)


def test_s515_duplicate_record_id_refused(tmp_path):
    root, idx, r = _case(tmp_path)
    _save(idx, {
        "schema_version": 1,
        "epoch_id": EPOCH,
        "decision_at": utc(DECISION),
        "as_of": utc(ASOF),
        "records": [r, r],
    })
    with pytest.raises(
        Stage5CouncilEvidenceError, match="Duplicate evidence"
    ):
        _review(root, idx)


def test_s515_bad_sha_detected(tmp_path):
    root, idx, r = _case(tmp_path)
    (root / r["relative_path"]).write_bytes(b"tampered")

    with pytest.raises(
        Stage5CouncilEvidenceError, match="SHA-256 mismatch"
    ):
        _review(root, idx)


def test_s515_duplicate_json_key_detected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    idx = tmp_path / "idx.json"
    idx.write_text(
        '{"schema_version":1,"schema_version":1}'
    )
    with pytest.raises(
        Stage5CouncilEvidenceError, match="Duplicate"
    ):
        _review(root, idx)


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "/root",
        "c:/win",
        "sub\\file",
        "a//b",
        "./x",
        "nested/../x",
    ],
)
def test_s515_path_escape_refused(tmp_path, path):
    root, idx, r = _case(tmp_path)
    r["relative_path"] = path
    _save(idx, {
        "schema_version": 1,
        "epoch_id": EPOCH,
        "decision_at": utc(DECISION),
        "as_of": utc(ASOF),
        "records": [r],
    })
    with pytest.raises(
        Stage5CouncilEvidenceError,
        match="path|traversal",
    ):
        _review(root, idx)


def test_s515_symlinked_document_refused(tmp_path):
    root, idx, r = _case(tmp_path)
    link = root / "shortcut.txt"

    try:
        link.symlink_to(root / r["relative_path"])
    except (OSError, NotImplementedError):
        pytest.skip("Symlink unavailable on host")

    r["relative_path"] = link.name
    _save(idx, {
        "schema_version": 1,
        "epoch_id": EPOCH,
        "decision_at": utc(DECISION),
        "as_of": utc(ASOF),
        "records": [r],
    })
    with pytest.raises(
        Stage5CouncilEvidenceError, match="Symlinked"
    ):
        _review(root, idx)


def test_s516_no_documents_claimed_as_real_source():
    r = assess_stage5_council_sources(**config())

    assert r["static_council_inventory"]["static_council_seat_count"] == 31
    assert r["source_families_independently_verified"] == 0
    assert r["fed_source_admitted_to_live_context"] is False
    assert r["real_council_research_admission"] is False
    assert r["final_action"] == "HOLD"

    assert len([
        x for x in r["external_evidence_blockers"]
        if x.startswith("FAMILY_EXTERNAL_")
    ]) == 13


def test_s516_local_evidence_does_not_promote_source(tmp_path):
    root, idx, _ = _case(tmp_path)
    r = assess_stage5_council_sources(
        **config(
            index_file=idx,
            evidence_root=root,
        )
    )
    assert (
        r["point_in_time_candidate_evidence"][
            "records_locally_hash_matched"
        ] == 1
    )
    assert r["source_families_independently_verified"] == 0
    assert (
        r["calendar_release_times_independently_verified"]
        is False
    )


def test_s516_deterministic_hash():
    first = assess_stage5_council_sources(**config())
    second = assess_stage5_council_sources(**config())

    assert first == second
    assert first["report_sha256"] == _hash({
        k: v for k, v in first.items()
        if k != "report_sha256"
    })


def test_s516_no_stateful_actions():
    r = assess_stage5_council_sources(**config())

    for field in (
        "source_files_executed",
        "files_written",
        "database_accessed",
        "network_accessed",
        "broker_connected",
        "collector_started",
        "council_invoked",
        "training_authorized",
        "execution_authorized",
    ):
        assert r[field] is False


def test_s516_cli_requires_readonly_optins():
    with pytest.raises(SystemExit) as exc:
        run([
            "--epoch-id",
            EPOCH,
            "--decision-at",
            utc(DECISION),
            "--as-of",
            utc(ASOF),
        ])
    assert exc.value.code == 2


def test_s516_cli_bad_epoch_fails_closed(capsys):
    assert run([
        "--read-only-source",
        "--read-only-evidence",
        "--epoch-id",
        "BAD",
        "--decision-at",
        utc(DECISION),
        "--as-of",
        utc(ASOF),
    ]) == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_s516_cli_json_preserves_hold(capsys):
    assert run([
        "--read-only-source",
        "--read-only-evidence",
        "--epoch-id",
        EPOCH,
        "--decision-at",
        utc(DECISION),
        "--as-of",
        utc(ASOF),
        "--contracts-file",
        str(CONTRACTS),
        "--manifest-file",
        str(MANIFEST),
        "--json",
    ]) == 0

    r = json.loads(capsys.readouterr().out)
    assert (
        r["static_council_inventory"][
            "static_council_seat_count"
        ]
    ) == 31
    assert r["final_action"] == "HOLD"


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-27T15:01:43",
        "invalid",
        "2026-10-27T18:01:43+03:00",
    ],
)
def test_s516_utc_only(value):
    with pytest.raises(Stage5CouncilEvidenceError):
        _utc(value)

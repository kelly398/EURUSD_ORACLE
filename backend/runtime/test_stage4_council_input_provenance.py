
"""S4-13..16: static provenance regressions; all records here are synthetic."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

from backend.runtime.stage4_council_input_provenance import (
    CouncilProvenanceError,
    FAMILY_SPECS,
    CORE_PATHS,
    _hashed,
    inspect_local_index,
    inspect_stage4_council_inputs,
    run,
)

BASE = Path(__file__).parents[2]
ASOF = datetime(
    2026, 10, 29, 20, 45, 12,
    tzinfo=timezone.utc,
)


@pytest.fixture
def repo(tmp_path):
    target = tmp_path / "repo"

    paths = {
        p
        for _, _, ps, _ in FAMILY_SPECS
        for p in ps
    } | set(CORE_PATHS)

    for path in paths:
        dest = target / path
        dest.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        shutil.copy2(BASE / path, dest)

    return target


def audit(repo, **kw):
    return inspect_stage4_council_inputs(
        repo=repo,
        as_of=ASOF,
        **kw,
    )


def row(family="fed_data", source="SYNTHETIC-FIXTURE"):
    data = {
        "family": family,
        "source_id": source,
        "content_sha256": sha256(
            b"synthetic-only"
        ).hexdigest(),
        "claimed_published_at": (
            ASOF - timedelta(hours=2)
        ).isoformat(),
        "claimed_received_at": (
            ASOF - timedelta(hours=1)
        ).isoformat(),
    }
    data["record_sha256"] = _hashed(data)
    return data


def index_file(tmp_path, rows=None, *, cutoff=ASOF):
    path = tmp_path / "declared-index.json"
    path.write_text(
        json.dumps({
            "version": 1,
            "as_of": cutoff.isoformat(),
            "records": rows or [],
        }),
        encoding="utf-8",
    )
    return path


def test_s413_thirteen_family_contracts_in_source(repo):
    r = audit(repo)
    assert r["families_total"] == 13
    assert r["typed_context_family_fields_found"] == 13
    assert r["local_code_inventory_status"] == "COMPLETE"
    assert {x["family"] for x in r["families"]} == {
        s[0] for s in FAMILY_SPECS
    }


def test_s413_market_source_paths_are_local_not_broker_attestation(repo):
    r = audit(repo)

    for name in ("market_state", "candles", "ticks"):
        f = next(
            f for f in r["families"]
            if f["family"] == name
        )
        assert f["group"] == "market"
        assert f["local_code_paths_parseable"] is True
        assert f["independent_source_origin_verified"] is False
        assert all(
            len(p["local_source_sha256"]) == 64
            for p in f["source_files"]
        )


def test_s414_macro_family_coverage(repo):
    r = audit(repo)
    assert {
        x["family"]
        for x in r["families"]
        if x["group"] == "macro"
    } == {
        "cot_data",
        "ecb_data",
        "fed_data",
        "economic_calendar",
        "calendar_coverage",
    }


def test_s415_risk_and_governance_group_coverage(repo):
    r = audit(repo)

    assert {
        x["family"]
        for x in r["families"]
        if x["group"] == "risk"
    } == {
        "cross_asset_data",
        "risk_state",
        "risk_limits",
    }

    assert {
        x["family"]
        for x in r["families"]
        if x["group"] == "governance"
    } == {
        "reliability",
        "synthesis_policy",
    }


def test_s416_31_seat_manifest_only_static(repo):
    r = audit(repo)
    assert r["manifest_31_seats_statically_verified"] is True
    assert r["manifest_seats_found"] == 31
    assert r["council_seats_run"] == 0


def test_s416_direct_requirements_distinguished_from_optional_fields(repo):
    r = audit(repo)
    seats = {
        f["family"]: f["seat_numbers_with_direct_requirement"]
        for f in r["families"]
    }

    assert 25 in seats["fed_data"]
    assert 24 in seats["ecb_data"]
    assert 22 in seats["cot_data"]
    assert 30 in seats["risk_state"]
    assert seats["risk_limits"] == []
    assert seats["calendar_coverage"] == []


def test_s416_manifest_other_derived_inputs_not_mislabeled(repo):
    r = audit(repo)
    assert (
        "model_artifact"
        in r["manifest_dependencies_outside_13_families"]
    )
    assert (
        "tick_features"
        in r["manifest_dependencies_outside_13_families"]
    )
    assert (
        "primary_agent_opinions"
        in r["manifest_dependencies_outside_13_families"]
    )
    assert (
        "rates_data"
        in r["manifest_dependencies_outside_13_families"]
    )


def test_s414_static_fed_guards_present_not_certification(repo):
    r = audit(repo)
    assert all(r["static_fed_safeguards"].values())

    assert next(
        f for f in r["families"]
        if f["family"] == "fed_data"
    )["admitted_to_real_council"] is False


def test_s416_all_independent_evidence_gates_fail_closed(repo):
    r = audit(repo)

    assert r["independently_verified_family_count"] == 0
    assert len(r["unverified_families"]) == 13
    assert len(r["required_independent_evidence_actions"]) == 13

    for f in r["families"]:
        assert (
            f["independent_publication_or_receipt_time_verified"]
            is False
        )
        assert f["admitted_to_real_council"] is False


def test_s416_no_execution_or_resource_access(repo):
    r = audit(repo)

    assert r["network_accessed"] is False
    assert r["database_accessed"] is False
    assert r["raw_inputs_read_or_council_context_built"] is False
    assert r["council_invoked"] is False
    assert r["real_forward_council_admission"] is False
    assert r["training_authorized"] is False
    assert r["execution_authorized"] is False
    assert r["final_action"] == "HOLD"


def test_s416_deterministic_report_at_fixed_cutoff(repo):
    a, b = audit(repo), audit(repo)

    assert a == b
    assert a["report_sha256"] == _hashed({
        k: v
        for k, v in a.items()
        if k != "report_sha256"
    })


def test_s413_source_hash_changes_report_but_not_approval(repo):
    before = audit(repo)
    path = repo / "backend/market_state/candle_repository.py"

    path.write_bytes(
        path.read_bytes() + b"\n# synthetic local change\n"
    )

    after = audit(repo)
    assert before["report_sha256"] != after["report_sha256"]
    assert after["independently_verified_family_count"] == 0


def test_s413_missing_market_source_blocks_local_inventory(repo):
    (
        repo / "backend/market_state/tick_repository.py"
    ).unlink()

    r = audit(repo)
    assert r["local_code_inventory_status"] == "INCOMPLETE"
    assert (
        "SOURCE_FILES_MISSING_OR_UNPARSEABLE"
        in r["local_code_inventory_issues"]
    )
    assert r["final_action"] == "HOLD"


def test_s413_invalid_source_syntax_marks_incomplete(repo):
    (
        repo / "backend/market_state/schemas.py"
    ).write_text("def broken(\n")

    assert audit(repo)["local_code_inventory_status"] == "INCOMPLETE"


def test_s413_missing_typed_field_detected(repo):
    path = repo / "backend/agents/contracts.py"
    content = path.read_text()

    assert "market_state: MarketStateSnapshot" in content

    path.write_text(
        content.replace(
            "market_state: MarketStateSnapshot",
            "missing_state: MarketStateSnapshot",
            1,
        )
    )

    r = audit(repo)
    assert r["typed_context_family_fields_found"] == 12
    assert (
        "COUNCIL_CONTEXT_FIELDS_INCOMPLETE"
        in r["local_code_inventory_issues"]
    )


def test_s416_manifest_missing_blocks_static_claim(repo):
    (
        repo / "backend/agents/council_manifest.py"
    ).unlink()

    r = audit(repo)
    assert r["manifest_31_seats_statically_verified"] is False
    assert r["manifest_seats_found"] == 0


def test_s416_manifest_duplicate_seat_blocks_static_claim(repo):
    path = repo / "backend/agents/council_manifest.py"
    raw = path.read_text()

    assert "seat_number=2" in raw

    path.write_text(
        raw.replace(
            "seat_number=2",
            "seat_number=1",
            1,
        )
    )

    r = audit(repo)
    assert r["manifest_31_seats_statically_verified"] is False
    assert (
        "MANIFEST_31_SEATS_NOT_VERIFIED"
        in r["local_code_inventory_issues"]
    )


def test_s414_missing_fed_readiness_gate_detected(repo):
    path = repo / "backend/agents/fed_input_readiness.py"
    raw = path.read_text()

    assert "FED_SOURCE_VERSION_ATTESTATION_MISSING" in raw

    path.write_text(
        raw.replace(
            "FED_SOURCE_VERSION_ATTESTATION_MISSING",
            "REPLACED_MISSING_GATE",
        )
    )

    assert (
        "STATIC_FED_GUARD_NOT_FOUND"
        in audit(repo)["local_code_inventory_issues"]
    )


def test_s414_missing_factory_fed_guard_detected(repo):
    path = repo / "backend/agents/context_factory.py"
    raw = path.read_text()

    assert "raise FedInputNotReady(" in raw

    path.write_text(
        raw.replace(
            "raise FedInputNotReady(",
            "raise RuntimeError(",
        )
    )

    assert audit(repo)["static_fed_safeguards"][
        "production_fed_injection_guard_found_in_factory"
    ] is False


def test_s416_absent_index_does_not_invent_records(repo):
    r = audit(repo)

    assert r["optional_evidence_index"]["status"] == "NOT_SUPPLIED"
    assert r["optional_evidence_index"]["records_total"] == 0
    assert all(
        f["claimed_local_evidence_records"] == 0
        for f in r["families"]
    )


def test_s416_valid_local_index_still_not_attested(repo, tmp_path):
    p = index_file(
        tmp_path,
        [row("fed_data")],
    )
    r = audit(repo, evidence_index=p)

    assert r["optional_evidence_index"]["records_total"] == 1
    assert r["optional_evidence_index"]["independent_records_verified"] == 0

    assert next(
        f for f in r["families"]
        if f["family"] == "fed_data"
    )["local_evidence_status"] == "LOCAL_DECLARATION_UNATTESTED"

    assert r["independently_verified_family_count"] == 0


def test_s416_multi_family_index_counts_only_supplied_scope(repo, tmp_path):
    p = index_file(
        tmp_path,
        [
            row("fed_data"),
            row("ticks", "TEST-TICK"),
        ],
    )
    r = audit(repo, evidence_index=p)

    assert (
        r["optional_evidence_index"]["claimed_records"]["ticks"]
        == 1
    )
    assert (
        r["optional_evidence_index"]["claimed_records"]["fed_data"]
        == 1
    )
    assert (
        r["optional_evidence_index"]["claimed_records"]["risk_state"]
        == 0
    )


def test_s414_index_future_received_at_rejected(tmp_path):
    item = row()
    item["claimed_received_at"] = (
        ASOF + timedelta(seconds=1)
    ).isoformat()

    item["record_sha256"] = _hashed({
        k: v
        for k, v in item.items()
        if k != "record_sha256"
    })

    with pytest.raises(
        CouncilProvenanceError,
        match="ordering",
    ):
        inspect_local_index(
            index_file(tmp_path, [item]),
            as_of=ASOF,
        )


def test_s414_index_publication_after_receipt_rejected(tmp_path):
    item = row()
    item["claimed_published_at"] = ASOF.isoformat()

    item["record_sha256"] = _hashed({
        k: v
        for k, v in item.items()
        if k != "record_sha256"
    })

    with pytest.raises(
        CouncilProvenanceError,
        match="ordering",
    ):
        inspect_local_index(
            index_file(tmp_path, [item]),
            as_of=ASOF,
        )


def test_s416_index_cutoff_mismatch_rejected(tmp_path):
    with pytest.raises(
        CouncilProvenanceError,
        match="cutoff",
    ):
        inspect_local_index(
            index_file(
                tmp_path,
                cutoff=ASOF - timedelta(seconds=1),
            ),
            as_of=ASOF,
        )


def test_s416_duplicate_source_rejected(tmp_path):
    item = row()

    with pytest.raises(
        CouncilProvenanceError,
        match="Duplicate family",
    ):
        inspect_local_index(
            index_file(tmp_path, [item, item]),
            as_of=ASOF,
        )


def test_s416_self_hash_tamper_rejected(tmp_path):
    item = row()
    item["claimed_published_at"] = (
        ASOF - timedelta(minutes=5)
    ).isoformat()

    with pytest.raises(
        CouncilProvenanceError,
        match="hash mismatch",
    ):
        inspect_local_index(
            index_file(tmp_path, [item]),
            as_of=ASOF,
        )


def test_s416_unknown_family_rejected(tmp_path):
    with pytest.raises(
        CouncilProvenanceError,
        match="family/source",
    ):
        inspect_local_index(
            index_file(
                tmp_path,
                [row("wrong_family")],
            ),
            as_of=ASOF,
        )


def test_s416_unhashable_family_rejected(tmp_path):
    item = row()
    item["family"] = ["fed_data"]

    with pytest.raises(
        CouncilProvenanceError,
        match="family/source",
    ):
        inspect_local_index(
            index_file(tmp_path, [item]),
            as_of=ASOF,
        )


def test_s416_invalid_content_hash_rejected(tmp_path):
    item = row()
    item["content_sha256"] = "not-a-digest"

    with pytest.raises(
        CouncilProvenanceError,
        match="content digest",
    ):
        inspect_local_index(
            index_file(tmp_path, [item]),
            as_of=ASOF,
        )


def test_s416_bad_source_id_rejected(tmp_path):
    with pytest.raises(
        CouncilProvenanceError,
        match="family/source",
    ):
        inspect_local_index(
            index_file(
                tmp_path,
                [row(source="../../escape")],
            ),
            as_of=ASOF,
        )


def test_s416_duplicate_json_keys_refused(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(
        '{"version":1,"version":1,"as_of":"2026-10-29T20:45:12+00:00","records":[]}'
    )

    with pytest.raises(
        CouncilProvenanceError,
        match="Duplicate JSON",
    ):
        inspect_local_index(p, as_of=ASOF)


def test_s416_oversized_index_refused(tmp_path):
    p = tmp_path / "large.json"
    p.write_bytes(b"x" * 262145)

    with pytest.raises(
        CouncilProvenanceError,
        match="limit",
    ):
        inspect_local_index(p, as_of=ASOF)


def test_s416_symlink_index_refused(tmp_path):
    p = index_file(tmp_path)
    sym = tmp_path / "symlink.json"

    try:
        sym.symlink_to(p)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation unavailable")

    with pytest.raises(
        CouncilProvenanceError,
        match="regular",
    ):
        inspect_local_index(
            sym,
            as_of=ASOF,
        )


def test_s416_index_record_cap_enforced(tmp_path):
    p = index_file(
        tmp_path,
        [row()],
    )

    with pytest.raises(
        CouncilProvenanceError,
        match="limit",
    ):
        inspect_local_index(
            p,
            as_of=ASOF,
            max_records=0,
        )


def test_s416_cli_must_opt_into_readonly(capsys):
    with pytest.raises(SystemExit) as error:
        run([
            "--as-of",
            ASOF.isoformat(),
        ])
    assert error.value.code == 2


def test_s416_cli_bad_time_rejected(capsys):
    rc = run([
        "--read-only-sources",
        "--read-only-evidence",
        "--as-of",
        "2026-10-29T20:45:12",
        "--repo",
        str(BASE),
    ])

    assert rc == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_s416_cli_readonly_json_matches_hash(repo, capsys):
    rc = run([
        "--read-only-sources",
        "--read-only-evidence",
        "--as-of",
        ASOF.isoformat(),
        "--repo",
        str(repo),
        "--json",
    ])

    assert rc == 0
    data = json.loads(
        capsys.readouterr().out
    )

    assert data["report_sha256"] == _hashed({
        k: v
        for k, v in data.items()
        if k != "report_sha256"
    })
    assert data["final_action"] == "HOLD"


def test_s416_no_code_or_index_files_are_mutated(repo, tmp_path):
    p = index_file(
        tmp_path,
        [row()],
    )

    paths = [
        repo / "backend/agents/contracts.py",
        repo / "backend/agents/council_manifest.py",
        p,
    ]

    before = [
        x.read_bytes()
        for x in paths
    ]

    audit(
        repo,
        evidence_index=p,
    )

    assert [
        x.read_bytes()
        for x in paths
    ] == before

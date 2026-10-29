
"""P3-17..20: bounded synthetic evidence contract, never actual predictions."""

from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import event

from backend.data_engine.forward_store import hashed
from backend.runtime.forward_market_input_lineage import inspect_market_input_lineage
from backend.runtime.forward_prospective_evaluation import (
    ProspectiveEvidenceError,
    _safe_json,
    inspect_evaluation_contract,
    run,
    _timestamp,
)
from backend.runtime.test_forward_source_provenance import _fixture


@pytest.fixture
def source(tmp_path):
    engine, policy, latest = _fixture(count=2)
    policy_file = tmp_path / "original.json"
    policy_file.write_text(
        policy.model_dump_json(), encoding="utf-8"
    )
    decision = latest + timedelta(seconds=3)
    cutoff = latest + timedelta(seconds=130)

    try:
        report = inspect_market_input_lineage(
            engine,
            epoch_id=policy.epoch_id,
            as_of=cutoff,
            decision_at=decision,
            policy_file=policy_file,
        )
        yield report, engine, policy
    finally:
        engine.dispose()


def mark(row):
    row["record_sha256"] = hashed({
        k: v for k, v in row.items()
        if k != "record_sha256"
    })
    return row


def evidence(lineage):
    decision = _timestamp(lineage["decision_at"])
    tick = lineage["first_owned_receipts"]["latest_tick"]
    bar = lineage["first_owned_receipts"]["latest_closed_m5"]

    registration = mark({
        "epoch_id": lineage["epoch_id"],
        "decision_at": decision.isoformat(),
        "registered_at": (
            decision - timedelta(seconds=1)
        ).isoformat(),
        "source_report_sha256": lineage["report_sha256"],
        "tick_source_id": tick["source_id"],
        "bar_source_id": bar["source_id"],
        "direction": "UP",
        "horizon_seconds": 60,
        "previous_sha256": "0" * 64,
    })

    horizon = decision + timedelta(seconds=60)

    outcome = mark({
        "registration_sha256": registration["record_sha256"],
        "horizon_end": horizon.isoformat(),
        "source_first_available_at": (
            horizon + timedelta(seconds=1)
        ).isoformat(),
        "observed_at": (
            horizon + timedelta(seconds=2)
        ).isoformat(),
        "source_capture_id": "e" * 64,
        "bid": 1.1100,
        "ask": 1.1102,
    })

    cost = mark({
        "registration_sha256": registration["record_sha256"],
        "observed_at": (
            horizon + timedelta(seconds=3)
        ).isoformat(),
        "entry_bid": 1.1,
        "entry_ask": 1.1002,
        "exit_bid": 1.1100,
        "exit_ask": 1.1102,
        "commission_round_trip_pips": 0.6,
        "slippage_pips": 0.2,
        "financing_pips": 0.0,
        "broker_fill_reference": "SYNTHETIC-TEST",
    })

    split = {
        "frozen_at": (
            decision - timedelta(hours=1)
        ).isoformat(),
        "evaluation_start": (
            decision - timedelta(minutes=1)
        ).isoformat(),
        "evaluation_end": (
            decision + timedelta(seconds=100)
        ).isoformat(),
        "split_sha256": "a" * 64,
    }

    split["split_sha256"] = hashed({
        k: v for k, v in split.items()
        if k != "split_sha256"
    })

    return {
        "version": 1,
        "epoch_id": lineage["epoch_id"],
        "registrations": [registration],
        "outcomes": [outcome],
        "costs": [cost],
        "holdout": split,
    }


def review(lineage, bundle=None):
    return inspect_evaluation_contract(
        lineage,
        bundle=bundle,
        file_sha256=(
            "f" * 64 if bundle is not None else None
        ),
    )


def test_p317_absent_evidence_is_not_invented(source):
    result = review(source[0])
    assert result["registration_count_in_scope"] == 0
    assert result["post_horizon_outcomes_in_scope"] == 0
    assert result["cost_records_in_scope"] == 0
    assert result["heldout_local_declaration"] == "NOT_SUPPLIED"
    assert (
        "NO_REGISTRATIONS_IN_SUPPLIED_SCOPE"
        in result["local_evidence_blockers"]
    )


def test_p317_chained_local_registration_never_independent_attestation(source):
    result = review(source[0], evidence(source[0]))
    assert result["registration_count_in_scope"] == 1
    assert (
        result["registration_contract"]
        == "HASH_CHAIN_AND_LOCAL_TIMESTAMP_ORDER_ONLY"
    )
    assert result["independent_prediction_registration_attested"] is False
    assert result["prospective_prediction_registered_by_inspector"] is False


def test_p318_linked_outcome_still_not_independently_verified(source):
    r = review(source[0], evidence(source[0]))
    assert r["post_horizon_outcomes_in_scope"] == 1
    assert r["independent_outcome_source_attested"] is False
    assert r["performance_measured"] is False


def test_p319_cost_fields_not_real_executions(source):
    r = review(source[0], evidence(source[0]))
    assert (
        r["cost_records_in_scope"]
        == r["fully_linked_triplets_in_scope"]
        == 1
    )
    assert r["broker_fill_and_costs_attested"] is False
    assert r["transaction_costs_measured"] is False


def test_p320_local_split_frozen_claim_not_heldout_certification(source):
    r = review(source[0], evidence(source[0]))
    assert (
        r["heldout_local_declaration"]
        == "LOCAL_SPLIT_DECLARATION_UNATTESTED"
    )
    assert r["heldout_split_independently_frozen"] is False
    assert len(r["independent_evidence_gaps"]) == 4
    assert r["out_of_sample_trades_measured"] is None


def test_p320_every_path_remains_hold(source):
    for bundle in (None, evidence(source[0])):
        r = review(source[0], bundle)
        assert r["final_action"] == "HOLD"
        assert r["execution_authorized"] is False
        assert r["training_authorized"] is False
        assert r["real_forward_council_admission"] is False
        assert r["council_seats_run"] == 0
        assert r["files_created"] is False
        assert r["database_modified"] is False


def test_p317_registration_not_before_first_local_tick_availability(source):
    b = evidence(source[0])
    b["registrations"][0]["registered_at"] = (
        source[0]["first_owned_receipts"][
            "latest_tick"
        ]["first_local_availability"]
    )
    mark(b["registrations"][0])
    with pytest.raises(
        ProspectiveEvidenceError, match="causal"
    ):
        review(source[0], b)


def test_p317_registration_after_decision_rejected(source):
    b = evidence(source[0])
    b["registrations"][0]["registered_at"] = (
        _timestamp(source[0]["decision_at"])
        + timedelta(seconds=1)
    ).isoformat()
    mark(b["registrations"][0])
    with pytest.raises(
        ProspectiveEvidenceError, match="causal"
    ):
        review(source[0], b)


def test_p317_wrong_source_id_rejected(source):
    b = evidence(source[0])
    b["registrations"][0]["tick_source_id"] = "d" * 64
    mark(b["registrations"][0])
    with pytest.raises(
        ProspectiveEvidenceError, match="causal"
    ):
        review(source[0], b)


def test_p317_hash_tampering_rejected(source):
    b = evidence(source[0])
    b["registrations"][0]["direction"] = "DOWN"
    with pytest.raises(
        ProspectiveEvidenceError, match="integrity"
    ):
        review(source[0], b)


def test_p317_chain_reordering_detected(source):
    b = evidence(source[0])
    copy = dict(b["registrations"][0])
    copy["previous_sha256"] = "f" * 64
    mark(copy)
    b["registrations"].append(copy)
    with pytest.raises(
        ProspectiveEvidenceError, match="causal"
    ):
        review(source[0], b)


def test_p318_outcome_before_horizon_rejected(source):
    b = evidence(source[0])
    b["outcomes"][0]["source_first_available_at"] = (
        source[0]["decision_at"]
    )
    mark(b["outcomes"][0])
    with pytest.raises(
        ProspectiveEvidenceError, match="horizon"
    ):
        review(source[0], b)


def test_p318_outcome_wrong_registration_rejected(source):
    b = evidence(source[0])
    b["outcomes"][0]["registration_sha256"] = "a" * 64
    mark(b["outcomes"][0])
    with pytest.raises(
        ProspectiveEvidenceError, match="registration"
    ):
        review(source[0], b)


def test_p318_future_outcome_after_inspection_rejected(source):
    b = evidence(source[0])
    b["outcomes"][0]["observed_at"] = (
        _timestamp(source[0]["as_of"])
        + timedelta(seconds=1)
    ).isoformat()
    mark(b["outcomes"][0])
    with pytest.raises(
        ProspectiveEvidenceError, match="horizon"
    ):
        review(source[0], b)


def test_p319_cost_without_outcome_rejected(source):
    b = evidence(source[0])
    b["outcomes"] = []
    with pytest.raises(
        ProspectiveEvidenceError, match="outcome"
    ):
        review(source[0], b)


@pytest.mark.parametrize(
    "field,amount",
    [
        ("commission_round_trip_pips", -1),
        ("slippage_pips", -0.3),
        ("financing_pips", float("nan")),
        ("entry_bid", 0),
        ("exit_ask", True),
    ],
)
def test_p319_invalid_costs_rejected(source, field, amount):
    b = evidence(source[0])
    b["costs"][0][field] = amount

    if not (
        isinstance(amount, float)
        and math_is_nan(amount)
    ):
        mark(b["costs"][0])
        with pytest.raises(
            ProspectiveEvidenceError, match="Cost evidence"
        ):
            review(source[0], b)
    else:
        with pytest.raises(ValueError):
            mark(b["costs"][0])


def math_is_nan(value):
    return value != value


def test_p320_holdout_freeze_after_start_rejected(source):
    b = evidence(source[0])
    b["holdout"]["frozen_at"] = (
        b["holdout"]["evaluation_start"]
    )
    with pytest.raises(
        ProspectiveEvidenceError, match="Held-out freeze"
    ):
        review(source[0], b)


def test_p320_split_date_tampering_rejected(source):
    b = evidence(source[0])
    b["holdout"]["evaluation_end"] = (
        _timestamp(source[0]["as_of"])
        - timedelta(seconds=1)
    ).isoformat()
    with pytest.raises(
        ProspectiveEvidenceError, match="content hash"
    ):
        review(source[0], b)


def test_p320_missing_heldout_keeps_blocker(source):
    b = evidence(source[0])
    b["holdout"] = None
    r = review(source[0], b)
    assert (
        "NO_HELDOUT_SPLIT_IN_SUPPLIED_SCOPE"
        in r["local_evidence_blockers"]
    )


def test_p320_source_digest_tampering_rejected(source):
    lineage = dict(source[0])
    lineage["decision_at"] = "2026-10-27T14:00:00Z"
    with pytest.raises(
        ProspectiveEvidenceError, match="integrity"
    ):
        review(lineage)


def test_p320_source_fake_training_flag_rejected(source):
    lineage = dict(source[0])
    lineage["training_authorized"] = True
    lineage["report_sha256"] = hashed({
        k: v for k, v in lineage.items()
        if k != "report_sha256"
    })
    with pytest.raises(
        ProspectiveEvidenceError, match="safety"
    ):
        review(lineage)


def test_p320_deterministic_with_same_inputs(source):
    b = evidence(source[0])
    a = review(source[0], b)
    c = review(source[0], b)
    assert a == c
    assert a["report_sha256"] == hashed({
        k: v for k, v in a.items()
        if k != "report_sha256"
    })


def test_p320_evidence_file_only_read(tmp_path, source):
    path = tmp_path / "existing.json"
    path.write_text(json.dumps(evidence(source[0])))
    before = path.read_bytes()

    b, digest = _safe_json(path)
    assert digest
    assert b == evidence(source[0])

    inspect_evaluation_contract(
        source[0], bundle=b, file_sha256=digest
    )
    assert path.read_bytes() == before


def test_p320_duplicate_json_keys_blocked(tmp_path):
    path = tmp_path / "dup.json"
    path.write_text('{"version":1,"version":2}')

    with pytest.raises(
        ProspectiveEvidenceError, match="unreadable"
    ):
        _safe_json(path)


def test_p320_oversized_evidence_file_blocked(tmp_path):
    path = tmp_path / "large.json"
    path.write_bytes(b"x" * 1048577)
    with pytest.raises(
        ProspectiveEvidenceError, match="unreadable"
    ):
        _safe_json(path)


def test_p320_sql_read_only_during_source_inspection(source):
    engine, policy = source[1], source[2]
    events = []

    def track(
        _conn, _cursor, sql,
        _params, _ctx, _many
    ):
        events.append(sql.strip().upper())

    event.listen(
        engine, "before_cursor_execute", track
    )

    try:
        inspect_market_input_lineage(
            engine,
            epoch_id=policy.epoch_id,
            decision_at=_timestamp(
                source[0]["decision_at"]
            ),
            as_of=_timestamp(
                source[0]["as_of"]
            ),
        )
    finally:
        event.remove(
            engine, "before_cursor_execute", track
        )

    assert events
    assert all(
        q.startswith("SELECT")
        for q in events
    )


def test_p320_cli_requires_both_read_only_flags():
    with pytest.raises(SystemExit) as exc:
        run([
            "--epoch-id", "a" * 64,
            "--as-of", "2026-10-27T15:01:43Z",
            "--decision-at", "2026-10-27T15:01:43Z",
        ])
    assert exc.value.code == 2


def test_p320_cli_bad_epoch_fails_before_db(capsys):
    result = run([
        "--read-only-db",
        "--read-only-evidence",
        "--epoch-id", "bad",
        "--as-of", "2026-10-27T15:01:43Z",
        "--decision-at", "2026-10-27T15:01:43Z",
    ])
    assert result == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_p320_invalid_record_cap_rejected(source):
    for cap in (0, 5001, True, "100"):
        with pytest.raises(
            ProspectiveEvidenceError, match="max_records"
        ):
            inspect_evaluation_contract(
                source[0], max_records=cap
            )

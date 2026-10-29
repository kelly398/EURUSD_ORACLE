
"""D4+E1-E3 evidence inventory: synthetic feed and temporary local Git only."""

from datetime import timedelta

import pytest
from sqlalchemy import event

from backend.data_engine.forward_cycle_readiness import (
    audit_bound_clock_git_history, evaluate_forward_cycle_gate,
)
from backend.data_engine.forward_research_evaluation import (
    ForwardEvaluationError, _digest, evaluate_forward_research,
    inspect_forward_research, run,
)
from backend.data_engine.forward_source_lineage import inspect_forward_source_lineage
from backend.data_engine.test_forward_cycle_readiness import gitrepo
from backend.data_engine.test_forward_evidence_health import make_fixture


@pytest.fixture
def demo():
    engine, policy, now = make_fixture()
    try:
        yield engine, policy, now
    finally:
        engine.dispose()


def _inputs(demo, gitrepo):
    engine, policy, now = demo
    lineage = inspect_forward_source_lineage(
        engine, epoch_id=policy.epoch_id, as_of=now
    )
    info = lineage["clock_lineage"]
    clock = audit_bound_clock_git_history(
        info["bound_implementation_sha256"],
        info["local_implementation_sha256"],
        repo=gitrepo[0],
    )
    gate = evaluate_forward_cycle_gate(lineage, clock)
    return lineage, clock, gate


def _run(demo, gitrepo, **kwargs):
    engine, policy, now = demo
    return inspect_forward_research(
        engine, epoch_id=policy.epoch_id, as_of=now,
        repo=gitrepo[0], **kwargs,
    )


def test_d4_exact_clock_blob_location_does_not_certify_history(demo, gitrepo):
    result = _run(demo, gitrepo)
    assert result["clock_original_candidate"]["match_type"] in (
        "EXACT_GIT_BLOB_BYTES",
        "CRLF_CHECKOUT_EQUIVALENT",
    )
    assert result["clock_candidate_status"] in (
        "EXACT_LOCAL_GIT_BYTES_UNATTESTED",
        "LINE_ENDING_EQUIVALENCE_ONLY_UNATTESTED",
    )
    assert len(result["clock_original_candidate"]["revision"]) == 40
    assert result["historical_utc_certified"] is False


def test_d4_clock_not_in_git_is_explicitly_unmatched(demo, gitrepo):
    lineage, clock, gate = _inputs(demo, gitrepo)
    clock["matches"] = []
    clock["local_history_status"] = "NOT_FOUND_IN_BOUNDED_LOCAL_GIT_HISTORY"
    clock["report_sha256"] = _digest({
        k: v for k, v in clock.items()
        if k != "report_sha256"
    })
    gate = evaluate_forward_cycle_gate(lineage, clock)
    result = evaluate_forward_research(lineage, clock, gate)
    assert result["clock_original_candidate"] is None
    assert (
        "EPOCH_CLOCK_SOURCE_BYTES_NOT_EXACTLY_LOCATED"
        in result["readiness_blockers"]
    )


def test_d4_crlf_match_is_not_byte_exact(demo, gitrepo):
    lineage, clock, gate = _inputs(demo, gitrepo)
    clock["matches"][0]["match_type"] = "CRLF_CHECKOUT_EQUIVALENT"
    clock["report_sha256"] = _digest({
        k: v for k, v in clock.items()
        if k != "report_sha256"
    })
    gate = evaluate_forward_cycle_gate(lineage, clock)
    result = evaluate_forward_research(lineage, clock, gate)
    assert (
        result["clock_candidate_status"]
        == "LINE_ENDING_EQUIVALENCE_ONLY_UNATTESTED"
    )
    assert (
        "EPOCH_CLOCK_SOURCE_BYTES_NOT_EXACTLY_LOCATED"
        in result["readiness_blockers"]
    )


def test_d4_missing_candidate_metadata_fails_closed(demo, gitrepo):
    lineage, clock, _ = _inputs(demo, gitrepo)
    clock["matches"] = []
    clock["report_sha256"] = _digest({
        k: v for k, v in clock.items()
        if k != "report_sha256"
    })
    gate = evaluate_forward_cycle_gate(lineage, clock)
    with pytest.raises(ForwardEvaluationError, match="candidate is missing"):
        evaluate_forward_research(lineage, clock, gate)


def test_e1_bounded_baseline_is_reproducible(demo, gitrepo):
    first = _run(demo, gitrepo)
    second = _run(demo, gitrepo)
    assert first == second
    assert first["report_sha256"] == _digest({
        k: v for k, v in first.items()
        if k != "report_sha256"
    })
    assert first["coverage"]["bounded_forward_capture_count"] == 2
    assert first["coverage"]["sample_truncated"] is False


def test_e1_stale_cutoff_remains_blocked(demo, gitrepo):
    engine, policy, now = demo
    result = inspect_forward_research(
        engine, epoch_id=policy.epoch_id,
        as_of=now + timedelta(minutes=20), repo=gitrepo[0],
    )
    assert (
        "CAPTURE_INGESTION_NOT_CURRENT"
        in result["readiness_blockers"]
    )
    assert (
        result["performance_conclusion"]
        == "NOT_MEASURABLE_FROM_THIS_EVIDENCE_SCOPE"
    )


def test_e1_capture_window_limit_exposes_unverified_coverage(demo, gitrepo):
    result = _run(demo, gitrepo, max_captures=1)
    assert result["coverage"]["sample_truncated"] is True
    assert (
        "CAPTURE_SAMPLE_INCOMPLETE"
        in result["readiness_blockers"]
    )
    assert (
        result["coverage"]["full_epoch_coverage_certified"]
        is False
    )


def test_e2_no_fabricated_predictions_or_performance(demo, gitrepo):
    result = _run(demo, gitrepo)
    by_name = {
        item["dimension"]: item
        for item in result["prospective_evaluation_evidence"]
    }
    assert (
        by_name["prospectively_registered_predictions"]["status"]
        == "NOT_INSPECTED"
    )
    assert (
        by_name["independent_post_horizon_outcomes"]["evaluated_count"]
        is None
    )
    assert result["measured_out_of_sample_trades"] is None
    assert result["measured_predictive_performance"] is None


def test_e2_costs_and_fills_not_inferred_from_bars(demo, gitrepo):
    result = _run(demo, gitrepo)
    assert result["measured_transaction_costs"] is None
    costs = {
        item["dimension"]: item["status"]
        for item in result["prospective_evaluation_evidence"]
    }
    assert (
        costs["matched_bid_ask_commission_and_slippage"]
        == "NOT_MEASURED"
    )
    assert (
        costs["broker_fill_and_realized_execution_cost"]
        == "NOT_INSPECTED"
    )
    assert (
        "NO_VERIFIED_ALL_IN_TRANSACTION_COST_MODEL"
        in result["readiness_blockers"]
    )


def test_e3_thirteen_inputs_all_unverified_and_zero_seats(demo, gitrepo):
    result = _run(demo, gitrepo)
    assert result["unverified_council_input_count"] == 13
    assert len(result["unverified_council_families"]) == 13
    assert result["council_seats_run"] == 0
    assert result["council_invoked"] is False
    assert result["final_action"] == "HOLD"
    assert result["training_authorized"] is False
    assert result["execution_authorized"] is False


def test_e3_lineage_digest_mismatch_rejected(demo, gitrepo):
    lineage, clock, gate = _inputs(demo, gitrepo)
    lineage["as_of"] = "2026-10-27T10:00:00+00:00"
    with pytest.raises(
        ForwardEvaluationError, match="lineage report digest"
    ):
        evaluate_forward_research(lineage, clock, gate)


def test_e3_mixed_gate_and_history_rejected(demo, gitrepo):
    lineage, clock, gate = _inputs(demo, gitrepo)
    gate["git_clock_audit_sha256"] = "0" * 64
    gate["report_sha256"] = _digest({
        k: v for k, v in gate.items()
        if k != "report_sha256"
    })
    with pytest.raises(ForwardEvaluationError, match="Git audit"):
        evaluate_forward_research(lineage, clock, gate)


def test_e3_upstream_claimed_dispatch_rejected(demo, gitrepo):
    lineage, clock, gate = _inputs(demo, gitrepo)
    gate["council_seats_run"] = 1
    gate["report_sha256"] = _digest({
        k: v for k, v in gate.items()
        if k != "report_sha256"
    })
    with pytest.raises(
        ForwardEvaluationError, match="dispatched Council"
    ):
        evaluate_forward_research(lineage, clock, gate)


def test_e3_source_family_claimed_certification_rejected(demo, gitrepo):
    lineage, clock, gate = _inputs(demo, gitrepo)
    lineage["council_input_families"][0]["real_first_receipt_attested"] = True
    lineage["report_sha256"] = _digest({
        k: v for k, v in lineage.items()
        if k != "report_sha256"
    })
    gate["source_lineage_sha256"] = lineage["report_sha256"]
    gate["report_sha256"] = _digest({
        k: v for k, v in gate.items()
        if k != "report_sha256"
    })
    with pytest.raises(
        ForwardEvaluationError, match="certification claim"
    ):
        evaluate_forward_research(lineage, clock, gate)


def test_e3_db_reads_are_select_only(demo, gitrepo):
    engine, _, _ = demo
    statements = []

    def listen(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement.strip().upper())

    event.listen(engine, "before_cursor_execute", listen)
    try:
        _run(demo, gitrepo)
    finally:
        event.remove(engine, "before_cursor_execute", listen)

    assert statements
    assert all(
        statement.startswith("SELECT")
        for statement in statements
    )


def test_e3_cli_requires_both_read_only_flags():
    for args in ([], ["--read-only-db"], ["--git-read-only"]):
        with pytest.raises(SystemExit) as exc:
            run(args)
        assert exc.value.code == 2


def test_e3_cli_rejects_bad_epoch_without_database(capsys):
    assert run([
        "--read-only-db", "--git-read-only",
        "--epoch-id", "bad",
        "--as-of", "2026-10-27T14:00:00Z",
    ]) == 1
    assert "EVALUATION BLOCKED" in capsys.readouterr().out

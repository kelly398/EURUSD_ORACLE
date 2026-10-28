
"""Offline integration tests for M29-32: the original real 31-seat Council."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch
import json

import pytest

from backend.agents.agent_registry import AgentRegistry
from backend.agents.contracts import AgentContext
from backend.agents.council_inputs import ExecutionMode
from backend.agents.register_core_agents import register_core_agents
from backend.agents.test_full_research_council import synthetic_full_context
from backend.agents.synthetic_council_shadow import (
    ShadowCouncilError,
    _prepare_context,
    _validate_registry,
    run_shadow_council,
    run,
)


@pytest.fixture(scope="module")
def original():
    return synthetic_full_context()


def new_registry():
    return register_core_agents(AgentRegistry())


def no_supplemental(original):
    payload = original.model_dump()
    payload.update(
        ecb_data=(),
        fed_data=(),
        economic_calendar=(),
        calendar_coverage=None,
        risk_limits=None,
        risk_state=None,
        reliability=(),
        synthesis_policy=None,
    )
    return AgentContext.model_validate(payload)


def test_m29_manifest_has_canonical_stages_and_seats():
    specs = _validate_registry(new_registry())

    assert len(specs) == 31
    assert [s.seat_number for s in specs] == list(range(1, 32))

    assert [
        sum(s.stage.value == stage for s in specs)
        for stage in ("primary", "governance", "synthesis")
    ] == [28, 2, 1]


def test_m29_eligibility_does_not_equal_registration(original):
    registry = new_registry()
    bad = no_supplemental(original)

    assert registry.registered_count == 31
    check = registry.eligibility(
        "fed",
        bad,
        mode=ExecutionMode.RESEARCH,
    )
    assert not check.allowed
    assert "INPUT_MISSING:fed_data" in check.blockers


def test_m30_real_31_agents_on_synthetic_only_are_complete_and_hold(original):
    report = run_shadow_council(original)

    assert report["council_seat_count"] == 31
    assert report["stage_counts"] == {
        "primary": {
            "expected_seats": 28,
            "success": 28,
            "blocked": 0,
            "error": 0,
        },
        "governance": {
            "expected_seats": 2,
            "success": 2,
            "blocked": 0,
            "error": 0,
        },
        "synthesis": {
            "expected_seats": 1,
            "success": 1,
            "blocked": 0,
            "error": 0,
        },
    }
    assert report["council_run_complete"] is True
    assert report["canonical_council_invoked"] is True
    assert report["final_action"] == "HOLD"
    assert report["execution_authorized"] is False
    assert report["historical_admission"] is False
    assert report["training_authorized"] is False
    assert (
        report["source_provenance_independently_verified"]
        is False
    )
    assert len(report["seats"]) == 31
    assert [
        s["seat"] for s in report["seats"]
    ] == list(range(1, 32))


def test_m31_real_primary_disagreement_and_governance(original):
    report = run_shadow_council(original)
    votes = report["primary_directional_opinions"]

    assert votes["long"] > 0
    assert votes["short"] > 0
    assert (
        report["primary_long_short_disagreement_observed"]
        is True
    )
    assert set(report["governance"]) == {
        "devils_advocate",
        "risk_officer",
    }
    assert all(
        value["status"] == "success"
        for value in report["governance"].values()
    )
    assert report["final_action"] == "HOLD"


def test_m31_missing_sources_block_real_seats(original):
    report = run_shadow_council(
        no_supplemental(original)
    )

    assert report["council_run_complete"] is False
    assert report["stage_counts"]["primary"]["blocked"] >= 4

    blocked = {
        x["agent_id"]: x
        for x in report["seats"]
        if x["status"] == "blocked"
    }

    assert "fed" in blocked
    assert "risk_officer" in blocked
    assert "chief_strategist" in blocked
    assert (
        "INPUT_MISSING:fed_data"
        in blocked["fed"]["blockers"]
    )
    assert report["final_action"] == "HOLD"


def test_m31_error_remains_visible_but_no_execution(original):
    registry = new_registry()

    with patch.object(
        registry.get_agent("trend_master"),
        "run",
        side_effect=RuntimeError(
            "synthetic injected failure"
        ),
    ):
        report = run_shadow_council(
            original,
            registry=registry,
        )

    seat = next(
        x for x in report["seats"]
        if x["agent_id"] == "trend_master"
    )

    assert seat["status"] == "error"
    assert seat["error_type"] == "RuntimeError"
    assert report["stage_counts"]["primary"]["error"] == 1
    assert report["council_run_complete"] is False
    assert report["final_action"] == "HOLD"


def test_m32_deterministic_receipt_ignores_wall_clock_runtime(original):
    first = run_shadow_council(original)
    second = run_shadow_council(original)

    assert first == second
    assert len(first["report_sha256"]) == 64


def test_m32_never_exposes_production_candidate_as_action(original):
    from backend.agents.council_runner import CouncilRunner

    original_run = CouncilRunner.run

    def forged(self, context, **kwargs):
        result = original_run(
            self,
            context,
            **kwargs,
        )
        last = result.synthesis[0]

        opinion = last.opinion.model_copy(
            update={
                "evidence": {
                    "decision": "research_long_candidate"
                }
            }
        )

        return replace(
            result,
            synthesis=(
                replace(last, opinion=opinion),
            ),
        )

    with patch.object(
        CouncilRunner,
        "run",
        forged,
    ):
        report = run_shadow_council(original)

    assert (
        report["council_internal_research_decision"]
        == "research_long_candidate"
    )
    assert report["final_action"] == "HOLD"
    assert report["execution_authorized"] is False


def test_forged_mode_is_rejected_before_dispatch(original):
    values = original.model_dump()
    values["execution_mode"] = "production"
    context = AgentContext.model_validate(values)

    with pytest.raises(
        ShadowCouncilError,
        match="research inputs",
    ):
        run_shadow_council(context)


def test_forged_source_time_is_rejected(original):
    changed = original.cross_asset_data[0].model_copy(
        update={
            "ingested_at": (
                original.as_of + timedelta(seconds=1)
            )
        }
    )

    forged = original.model_copy(
        update={
            "cross_asset_data": (
                changed,
                *original.cross_asset_data[1:],
            )
        }
    )

    with pytest.raises(
        ShadowCouncilError,
        match="revalidation",
    ):
        run_shadow_council(forged)


def test_precomputed_stage_opinions_are_rejected(original):
    from backend.agents.test_remaining_council_seats import (
        primary_opinions,
    )

    altered = original.model_copy(
        update={
            "primary_agent_opinions": (
                primary_opinions()[0],
            )
        }
    )

    with pytest.raises(ShadowCouncilError):
        run_shadow_council(altered)


@pytest.mark.parametrize(
    "value",
    [0, -1, True, 9, "4", 0.3],
)
def test_bad_workers_rejected_before_council(value, original):
    with pytest.raises(
        ShadowCouncilError,
        match="max_workers",
    ):
        run_shadow_council(
            original,
            max_workers=value,
        )


def test_false_synthetic_origin_rejected(original):
    with pytest.raises(
        ShadowCouncilError,
        match="origin",
    ):
        run_shadow_council(
            original,
            declared_origin="historical_research",
        )


def test_partial_registry_rejected_before_dispatch(original):
    with pytest.raises(
        ShadowCouncilError,
        match="incomplete",
    ):
        run_shadow_council(
            original,
            registry=AgentRegistry(),
        )


def test_bad_context_type_rejected():
    with pytest.raises(
        ShadowCouncilError,
        match="typed",
    ):
        _prepare_context({
            "execution_mode": "research"
        })


def test_cli_runs_two_real_scenarios_with_final_hold(capsys):
    assert run([
        "--synthetic-only",
        "--scenario",
        "both",
    ]) == 0

    output = capsys.readouterr().out

    assert (
        "SCENARIO complete | seats=31 | primary=28/28"
        in output
    )
    assert (
        "SCENARIO missing_inputs | seats=31"
        in output
    )
    assert "fed: blocked" in output
    assert output.count("FINAL=HOLD") == 2
    assert "NO FILES WRITTEN" in output


def test_cli_json_never_authorizes_execution(capsys):
    assert run([
        "--synthetic-only",
        "--scenario",
        "complete",
        "--json",
    ]) == 0

    values = json.loads(
        capsys.readouterr().out
    )

    assert len(values) == 1

    result = values[0]["result"]

    assert result["final_action"] == "HOLD"
    assert result["execution_authorized"] is False
    assert result["council_seat_count"] == 31


def test_cli_requires_explicit_synthetic_only():
    with pytest.raises(SystemExit) as error:
        run([
            "--scenario",
            "complete",
        ])

    assert error.value.code == 2

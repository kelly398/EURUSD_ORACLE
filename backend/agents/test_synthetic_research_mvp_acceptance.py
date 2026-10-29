
"""Milestones 57-60: real synthetic integration acceptance, failures and gates."""

from dataclasses import replace
import json

import pytest

import backend.agents.synthetic_research_mvp_acceptance as acceptance
from backend.agents.synthetic_research_mvp_acceptance import (
    ResearchAcceptanceError,
    SCENARIOS,
    UNRESOLVED_GATES,
    _hash,
    _one_pass,
    _verify_result,
    assess_research_mvp,
    run,
)


@pytest.fixture(scope="module")
def accepted():
    return assess_research_mvp(synthetic_only=True)


def test_m57_full_matrix_runs_real_council_only_on_two_valid_scenarios(accepted):
    assert accepted["scenario_count"] == 13
    assert accepted["positive_scenarios"] == 2
    assert accepted["negative_scenarios"] == 11
    assert sum(x["council_seats"] == 31 for x in accepted["scenarios"]) == 2
    assert sum(x["council_seats"] == 0 for x in accepted["scenarios"]) == 11
    assert all(x["final_action"] == "HOLD" for x in accepted["scenarios"])


@pytest.mark.parametrize("name", [s.name for s in SCENARIOS])
def test_m57_every_named_scenario_has_explicit_contract(name, accepted):
    spec = next(s for s in SCENARIOS if s.name == name)
    row = next(r for r in accepted["scenarios"] if r["scenario"] == name)
    assert row["capture_count"] == spec.capture_count
    assert row["repeated_identically"] is True
    if spec.expected_reason:
        assert row["observed_status"] == "BLOCKED"
        assert spec.expected_reason in row["blockers"]
        assert row["council_invoked"] is False
        assert row["council_seats"] == 0
    else:
        assert row["observed_status"] == "SYNTHETIC_INPUT_READINESS_SHADOW_HOLD"
        assert row["blockers"] == []
        assert row["council_invoked"] is True
        assert row["council_seats"] == 31


def test_m58_every_case_runs_twice_and_checks_determinism(accepted):
    assert accepted["determinism_repeated_runs"] == 26
    assert all(row["repeated_identically"] is True for row in accepted["scenarios"])
    assert len({r["integrated_report_sha256"] for r in accepted["scenarios"]}) == 13


def test_m58_independent_replay_is_bitwise_repeatable(accepted):
    again = assess_research_mvp(synthetic_only=True)
    assert accepted == again
    assert accepted["acceptance_sha256"] == _hash({
        k: v for k, v in accepted.items() if k != "acceptance_sha256"
    })


def test_m59_explicit_external_and_execution_blockers(accepted):
    assert accepted["synthetic_research_mvp_status"] == "ACCEPTED_WITH_EXTERNAL_BLOCKERS"
    assert accepted["acceptance_scope"] == "SYNTHETIC_RESEARCH_INTEGRATION_ONLY"
    assert accepted["operational_readiness"] == "BLOCKED_UNVERIFIED_EXTERNAL_EVIDENCE"
    assert accepted["all_external_gates_met"] is False
    assert set(accepted["unresolved_external_gates"]) == set(UNRESOLVED_GATES)
    assert len(accepted["unresolved_external_gates"]) == 9
    assert accepted["historical_utc_certified"] is False
    for key in ("real_forward_admission", "historical_admission",
                "training_authorized", "execution_authorized", "broker_connected"):
        assert accepted[key] is False
    assert accepted["final_action"] == "HOLD"


def test_m59_missing_candle_and_overlap_are_distinguishable(accepted):
    by_name = {r["scenario"]: r for r in accepted["scenarios"]}
    first = by_name["missing_candle"]
    second = by_name["overlapping_capture"]
    assert first["integrated_report_sha256"] != second["integrated_report_sha256"]
    assert first["blockers"] != second["blockers"]
    assert any("FIRST_CAPTURE_BAR_MEMBERSHIP_INVALID" in x for x in first["blockers"])
    assert any("TICK_INTERVAL_OVERLAP" in x for x in second["blockers"])


def test_m60_no_subset_can_be_declared_complete(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Source should not run with a partial matrix")
    monkeypatch.setattr(acceptance, "_one_pass", forbidden)
    with pytest.raises(ResearchAcceptanceError, match="Full immutable"):
        assess_research_mvp(synthetic_only=True, scenarios=SCENARIOS[:-1])


def test_m60_order_cannot_be_changed(monkeypatch):
    monkeypatch.setattr(acceptance, "_one_pass", lambda *_: None)
    with pytest.raises(ResearchAcceptanceError, match="Full immutable"):
        assess_research_mvp(synthetic_only=True, scenarios=SCENARIOS[::-1])


def test_m60_explicit_synthetic_optin_is_required_before_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No fixture should be created")
    monkeypatch.setattr(acceptance, "_one_pass", forbidden)
    with pytest.raises(ResearchAcceptanceError, match="opt-in"):
        assess_research_mvp()


def test_m60_positive_result_cannot_mask_a_negative_scenario():
    good_spec = SCENARIOS[0]
    bad_spec = next(x for x in SCENARIOS if x.name == "missing_fed")
    good = _one_pass(good_spec)
    with pytest.raises(ResearchAcceptanceError, match="did not block"):
        _verify_result(bad_spec, good)


def test_m60_fabricated_council_success_is_rejected():
    spec = SCENARIOS[0]
    real = _one_pass(spec)
    fake = dict(real)
    fake["council_seats"] = 0
    fake.pop("report_sha256")
    fake["report_sha256"] = _hash(fake)
    with pytest.raises(ResearchAcceptanceError, match="whole Council"):
        _verify_result(spec, fake)


def test_m60_attempted_production_authorization_is_rejected():
    spec = SCENARIOS[0]
    real = _one_pass(spec)
    forged = dict(real)
    forged["execution_authorized"] = True
    forged["report_sha256"] = _hash({
        k: v for k, v in forged.items() if k != "report_sha256"
    })
    with pytest.raises(ResearchAcceptanceError, match="Safety"):
        _verify_result(spec, forged)


def test_m60_incorrect_report_digest_is_rejected():
    spec = SCENARIOS[0]
    forged = dict(_one_pass(spec))
    forged["report_sha256"] = "0" * 64
    with pytest.raises(ResearchAcceptanceError, match="hash invalid"):
        _verify_result(spec, forged)


def test_m60_partial_payload_is_rejected():
    with pytest.raises(ResearchAcceptanceError, match="missing required"):
        _verify_result(SCENARIOS[0], {"status": "SYNTHETIC_INPUT_READINESS_SHADOW_HOLD"})


def test_m60_acceptance_aborts_on_nondeterministic_case(monkeypatch):
    original = acceptance._one_pass
    calls = {}

    def unstable(spec):
        real = original(spec)
        count = calls.get(spec.name, 0)
        calls[spec.name] = count + 1
        if spec.name == "complete_5" and count == 1:
            real = dict(real)
            real["report_sha256"] = "0" * 64
        return real

    monkeypatch.setattr(acceptance, "_one_pass", unstable)
    with pytest.raises(ResearchAcceptanceError, match="Non-deterministic"):
        assess_research_mvp(synthetic_only=True)


def test_m60_cli_json_is_complete_and_never_authorizes(capsys):
    assert run(["--synthetic-only", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["scenario_count"] == 13
    assert report["synthetic_research_mvp_status"] == "ACCEPTED_WITH_EXTERNAL_BLOCKERS"
    assert report["operational_readiness"].startswith("BLOCKED")
    assert report["execution_authorized"] is False
    assert report["training_authorized"] is False
    assert report["final_action"] == "HOLD"


def test_m60_cli_human_summary_disambiguates_mvp_and_live(capsys):
    assert run(["--synthetic-only"]) == 0
    output = capsys.readouterr().out
    assert "SYNTHETIC RESEARCH MVP: ACCEPTED_WITH_EXTERNAL_BLOCKERS" in output
    assert "OPERATIONAL READINESS: BLOCKED_UNVERIFIED_EXTERNAL_EVIDENCE" in output
    assert output.count("SCENARIO ") == 13
    assert output.count("FINAL=HOLD") == 13
    assert "NO REAL ADMISSION" in output


def test_m60_cli_requires_synthetic_flag():
    with pytest.raises(SystemExit) as exc:
        run([])
    assert exc.value.code == 2


def test_m60_runtime_error_returns_failure_not_false_success(monkeypatch, capsys):
    def broken(*args, **kwargs):
        raise ResearchAcceptanceError("fault injected")
    monkeypatch.setattr(acceptance, "assess_research_mvp", broken)
    assert run(["--synthetic-only"]) == 1
    output = capsys.readouterr().out
    assert "ACCEPTANCE BLOCKED" in output
    assert "ACCEPTED" not in output

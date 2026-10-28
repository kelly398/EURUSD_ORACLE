
"""Milestones 29-32: isolated, non-executing shadow review of the real 31-seat Council.

M29: verify canonical manifest, typed input and mode gates.
M30: exercise the real registered Council on caller-declared synthetic context.
M31: preserve primary disagreement, seat blockers and governance veto evidence.
M32: return a deterministic, bounded shadow report with unconditional HOLD.

No historical reconstruction, production admission, model fitting, DB, journals,
network or broker calls. The CLI uses an existing test-only synthetic fixture;
its reported source is a declaration, NOT independently verified provenance.
"""

import argparse
from collections import Counter
from datetime import timedelta
from hashlib import sha256
import json

from backend.agents.agent_registry import AgentRegistry
from backend.agents.contracts import AgentContext, AgentStage
from backend.agents.council_inputs import ExecutionMode
from backend.agents.council_manifest import COUNCIL_MANIFEST
from backend.agents.council_runner import AgentRunStatus, CouncilRunner
from backend.agents.register_core_agents import register_core_agents

VERSION = "EURUSD_SYNTHETIC_CANONICAL_COUNCIL_SHADOW_V1"
STAGES = (
    (AgentStage.PRIMARY, 28),
    (AgentStage.GOVERNANCE, 2),
    (AgentStage.SYNTHESIS, 1),
)
DIRECTIONS = ("long", "short", "neutral")


class ShadowCouncilError(RuntimeError):
    """Fail closed when a shadow-Council invariant cannot be established."""


def _prepare_context(context):
    if not isinstance(context, AgentContext):
        raise ShadowCouncilError("A typed AgentContext is required.")
    try:
        context = AgentContext.model_validate(context.model_dump())
    except (TypeError, ValueError) as exc:
        raise ShadowCouncilError("Council context failed revalidation.") from exc

    if (
        context.execution_mode is not ExecutionMode.RESEARCH
        or context.symbol != "EURUSD"
        or context.timeframe != "M5"
        or context.as_of.utcoffset() != timedelta(0)
        or context.primary_agent_opinions
        or context.governance_opinions
        or context.execution_authorized is not False
    ):
        raise ShadowCouncilError(
            "Only empty-stage EURUSD M5 UTC research inputs qualify."
        )
    return context


def _validate_registry(registry):
    if not isinstance(registry, AgentRegistry):
        raise ShadowCouncilError("Canonical AgentRegistry required.")

    canonical = tuple(COUNCIL_MANIFEST)

    if (
        len(canonical) != 31
        or tuple(registry.specs) != canonical
        or len(registry.registered_agent_ids) != 31
        or registry.missing_agent_ids
        or any(
            spec.seat_number != i
            for i, spec in enumerate(canonical, 1)
        )
        or tuple(
            sum(spec.stage == stage for spec in canonical)
            for stage, _ in STAGES
        ) != (28, 2, 1)
    ):
        raise ShadowCouncilError(
            "Canonical 31-seat catalogue is incomplete or altered."
        )

    return canonical


def _receipt(run, manifest):
    """Inspect actual CouncilRunner output; never substitute synthetic votes."""
    executions = run.executions

    if len(executions) != 31:
        raise ShadowCouncilError(
            "Council did not dispatch all canonical seats."
        )

    for seat, (spec, item) in enumerate(
        zip(manifest, executions), 1
    ):
        if (
            (
                item.seat_number,
                item.agent_id,
                item.agent_name,
                item.stage,
            ) != (
                seat,
                spec.agent_id,
                spec.agent_name,
                spec.stage,
            )
        ):
            raise ShadowCouncilError(
                "Dispatched Council seat identity changed."
            )

        if item.status is AgentRunStatus.SUCCESS:
            if (
                item.opinion is None
                or item.error is not None
                or item.blockers
            ):
                raise ShadowCouncilError(
                    "Successful seat has inconsistent evidence."
                )

            if (
                (
                    item.opinion.agent_id,
                    item.opinion.agent_name,
                    item.opinion.category,
                    item.opinion.as_of,
                ) != (
                    spec.agent_id,
                    spec.agent_name,
                    spec.category,
                    run.primary.as_of,
                )
            ):
                raise ShadowCouncilError(
                    "Opinion identity or timestamp changed."
                )

        elif item.status is AgentRunStatus.BLOCKED:
            if item.opinion is not None or not item.blockers:
                raise ShadowCouncilError(
                    "Blocked seat lacks explicit blockers."
                )

        elif item.status is AgentRunStatus.ERROR:
            if item.opinion is not None or not item.error:
                raise ShadowCouncilError(
                    "Failed seat lacks error evidence."
                )

        else:
            raise ShadowCouncilError(
                "Unrecognized Council execution status."
            )

    return executions


def run_shadow_council(
    context,
    *,
    declared_origin="SYNTHETIC_TEST_FIXTURE",
    registry=None,
    max_workers=4,
):
    """Run real Council implementations while forbidding any executable result.

    Caller declaration is not source attestation. This is ONLY a synthetic
    integration diagnostic; no live or historical data admission is performed.
    """
    if declared_origin != "SYNTHETIC_TEST_FIXTURE":
        raise ShadowCouncilError(
            "A synthetic-only origin declaration is required."
        )

    if (
        type(max_workers) is not int
        or not 1 <= max_workers <= 8
    ):
        raise ShadowCouncilError(
            "max_workers must be an integer 1..8."
        )

    context = _prepare_context(context)
    registry = (
        register_core_agents(AgentRegistry())
        if registry is None
        else registry
    )
    manifest = _validate_registry(registry)

    try:
        runner = CouncilRunner(
            registry,
            max_workers=max_workers,
            mode=ExecutionMode.RESEARCH,
        )
        result = runner.run(
            context,
            require_all_registered=True,
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        raise ShadowCouncilError(
            "Canonical Council dispatch failed."
        ) from exc

    if (
        result.mode is not ExecutionMode.RESEARCH
        or result.execution_authorized is not False
        or result.primary.expected_agent_count != 28
        or result.missing_agent_ids
    ):
        raise ShadowCouncilError(
            "Council result violated the research-only contract."
        )

    executions = _receipt(result, manifest)
    stage_details = {}

    for stage, count in STAGES:
        items = tuple(
            item for item in executions
            if item.stage is stage
        )

        if len(items) != count:
            raise ShadowCouncilError(
                "Council stage count mismatch."
            )

        statuses = Counter(
            item.status.value for item in items
        )
        stage_details[stage.value] = {
            "expected_seats": count,
            "success": statuses[AgentRunStatus.SUCCESS.value],
            "blocked": statuses[AgentRunStatus.BLOCKED.value],
            "error": statuses[AgentRunStatus.ERROR.value],
        }

    directional = Counter(
        item.opinion.direction.value
        for item in result.primary.executions
        if (
            item.opinion is not None
            and item.opinion.signal_type.value == "directional"
        )
    )

    if set(directional) - set(DIRECTIONS):
        raise ShadowCouncilError(
            "Unexpected primary directional evidence."
        )

    disagreement = (
        directional["long"] > 0
        and directional["short"] > 0
    )

    governance = {}

    for item in result.governance:
        if item.opinion is None:
            governance[item.agent_id] = {
                "status": item.status.value,
                "approved": False,
            }
            continue

        field = (
            "review_passed"
            if item.agent_id == "devils_advocate"
            else "risk_checks_passed"
            if item.agent_id == "risk_officer"
            else None
        )

        if field is None:
            raise ShadowCouncilError(
                "Unexpected governance identity."
            )

        raw = item.opinion.evidence.get(field)
        governance[item.agent_id] = {
            "status": item.status.value,
            "approved": raw is True,
            "evidence_is_explicit": type(raw) is bool,
        }

    synthesis = result.final_opinion
    raw_decision = result.decision

    if not isinstance(raw_decision, str):
        raise ShadowCouncilError(
            "Invalid research decision."
        )

    # Remove runtime durations and raw agent errors from the receipt.
    # This keeps repeated reports reproducible without exposing details.
    seats = [
        {
            "seat": item.seat_number,
            "agent_id": item.agent_id,
            "stage": item.stage.value,
            "status": item.status.value,
            "blockers": list(item.blockers),
            "direction": (
                item.opinion.direction.value
                if item.opinion else None
            ),
            "signal_type": (
                item.opinion.signal_type.value
                if item.opinion else None
            ),
            "error_type": (
                item.error.split(":", 1)[0]
                if item.error else None
            ),
        }
        for item in executions
    ]

    report = {
        "assessment_version": VERSION,
        "scenario_origin": declared_origin,
        "source_provenance_independently_verified": False,
        "context_sha256": result.context_sha256,
        "council_seat_count": 31,
        "stage_counts": stage_details,
        "council_run_complete": result.is_complete,
        "primary_directional_opinions": {
            key: directional[key] for key in DIRECTIONS
        },
        "primary_long_short_disagreement_observed": disagreement,
        "governance": governance,
        "council_internal_research_decision": raw_decision,
        "synthesis_present": synthesis is not None,
        "seats": seats,
        "final_action": "HOLD",
        "execution_authorized": False,
        "historical_admission": False,
        "training_authorized": False,
        "synthetic_only": True,
        "canonical_council_invoked": True,
        "broker_connected": False,
        "persistence_enabled": False,
    }

    encoded = json.dumps(
        report,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    report["report_sha256"] = sha256(encoded).hexdigest()
    return report


def _demo_context():
    # Explicitly test-only. Never a live or historical data connector.
    from backend.agents.test_full_research_council import (
        synthetic_full_context,
    )
    return synthetic_full_context()


def run(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Offline canonical 31-agent synthetic research shadow test"
        )
    )
    parser.add_argument(
        "--synthetic-only",
        action="store_true",
        required=True,
    )
    parser.add_argument(
        "--scenario",
        choices=("complete", "missing_inputs", "both"),
        default="both",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
    )
    parser.add_argument(
        "--json",
        action="store_true",
    )
    args = parser.parse_args(argv)

    try:
        base = _demo_context()
        scenarios = (
            ("complete", "missing_inputs")
            if args.scenario == "both"
            else (args.scenario,)
        )
        results = []

        for name in scenarios:
            context = base

            if name == "missing_inputs":
                payload = base.model_dump()
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
                context = AgentContext.model_validate(payload)

            result = run_shadow_council(
                context,
                max_workers=args.workers,
            )
            results.append({
                "scenario": name,
                "result": result,
            })

    except (
        ShadowCouncilError,
        ValueError,
        TypeError,
        ImportError,
    ) as exc:
        print(
            "SYNTHETIC SHADOW BLOCKED: "
            + type(exc).__name__
        )
        return 1

    if args.json:
        print(
            json.dumps(
                results,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
        )
    else:
        print(
            "EURUSD ORACLE | Canonical Council synthetic shadow M29-32"
        )

        for item in results:
            result = item["result"]
            stage = result["stage_counts"]

            print(
                f"SCENARIO {item['scenario']} | seats=31 | "
                f"primary={stage['primary']['success']}/28 | "
                f"governance={stage['governance']['success']}/2 | "
                f"synthesis={stage['synthesis']['success']}/1"
            )

            print(
                f"  disagreements="
                f"{result['primary_long_short_disagreement_observed']} "
                f"internal={result['council_internal_research_decision']} | "
                f"FINAL=HOLD | digest={result['report_sha256']}"
            )

            for seat in result["seats"]:
                if seat["status"] != "success":
                    print(
                        f"  {seat['agent_id']}: {seat['status']} | "
                        f"{','.join(seat['blockers']) or seat['error_type']}"
                    )

        print(
            "TEST-DECLARED SYNTHETIC ONLY | NO FILES WRITTEN | "
            "NO BROKER | TRAINING/EXECUTION BLOCKED"
        )

    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()

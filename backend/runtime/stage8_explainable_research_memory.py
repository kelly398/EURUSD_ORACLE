
"""P8-13..16: offline explanation, diversity, frozen forecast proposal and journal.

Reads and independently recomputes the committed Stage 8 Batch 3 declarative
Council rehearsal from its original point-in-time context and seat schedule.
No agents/models are run. A local journal prefix is NOT a receipt or independent
append-only custody. A proposal is NOT a registered prediction. FINAL=HOLD.
"""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re

from backend.runtime.stage6_council_context_readiness import FAMILIES, DERIVED_INPUTS
from backend.runtime.stage8_m5_offline_heartbeat import EPOCH, POLICY_PATH, POLICY_SHA256
from backend.runtime.stage8_offline_council_sandbox import (
    CouncilSandboxError, _read, _decode, _digest, _utc, _sha, _id,
    assess_stage8_council,
)

VERSION = "EURUSD_STAGE8_EXPLAINABLE_MEMORY_P8_13_16_V1"
EXPLANATION_VERSION = "SYNTHETIC_SOURCE_OVERLAP_COUNCIL_EXPLANATION_V1"
FORECAST_VERSION = "UNREGISTERED_RESEARCH_PROPOSAL_V1"
JOURNAL_VERSION = "LOCAL_UNPERSISTED_JOURNAL_PREVIEW_V1"
COUNCIL_SCOPE = "P8_09_TO_P8_12_SYNTHETIC_COUNCIL_SANDBOX"
CONTEXT_SCOPE = "P8_05_TO_P8_08_POINT_IN_TIME_MARKET_STATE"
MAX_COUNCIL_BYTES = 524288
MAX_CONTEXT_BYTES = 524288
MAX_SCHEDULE_BYTES = 131072
MAX_FORECAST_BYTES = 16384
MAX_JOURNAL_BYTES = 131072
MAX_JOURNAL_EVENTS = 4
SUPPORTED_HORIZONS = (300, 900, 3600, 14400, 86400)
PROBABILITY = re.compile(r"^(?:0(?:\.[0-9]{1,3})?|1(?:\.0{1,3})?)$")
FORECAST_KEYS = {
    "schema_version", "historical_epoch_id", "packet_id", "mode",
    "decision_key", "context_sha256", "council_report_sha256",
    "proposed_at", "horizon_seconds", "horizon_definition", "direction",
    "probability_up", "model_context_sha256",
}
JOURNAL_KEYS = {
    "schema_version", "historical_epoch_id", "packet_id", "mode", "as_of",
    "decision_key", "context_sha256", "council_report_sha256", "events",
}
EVENT_KEYS = {
    "sequence", "event_type", "effective_at", "previous_sha256",
    "payload_sha256", "event_sha256", "terminal_state",
}
EVENT_KINDS = (
    "CONTEXT_FROZEN", "COUNCIL_REHEARSAL_HELD",
    "EXPLANATION_FROZEN", "FORECAST_PROPOSAL_HELD",
)
ZERO_SHA = "0" * 64
UTC = timezone.utc


class ResearchMemoryError(ValueError):
    """Unsafe, changed or noncausal local research envelope."""


def _strict(doc, keys, name):
    if not isinstance(doc, dict) or set(doc) != keys:
        raise ResearchMemoryError("Strict schema mismatch: " + name)


def _read_json(path, maximum):
    try:
        raw = _read(path, maximum)
        return _decode(raw), sha256(raw).hexdigest()
    except (CouncilSandboxError, OSError, ValueError, TypeError):
        raise ResearchMemoryError("Unsafe or malformed local JSON evidence.") from None


def _time(text):
    try:
        return _utc(text)
    except (CouncilSandboxError, ValueError, TypeError):
        raise ResearchMemoryError("Strict UTC time required.") from None


def _hash(value, label):
    try:
        return _sha(value, label)
    except CouncilSandboxError:
        raise ResearchMemoryError("SHA-256 required: " + label) from None


def _identifier(value, label):
    try:
        return _id(value, label)
    except CouncilSandboxError:
        raise ResearchMemoryError("Safe identifier required: " + label) from None


def _bundle(council_path, context_path, schedule_path, cutoff,
            policy_file, contracts_file, manifest_file):
    supplied = [x is not None for x in (council_path, context_path, schedule_path)]
    if not any(supplied):
        return None
    if not all(supplied):
        raise ResearchMemoryError(
            "Council report, original context and original schedule must be supplied together."
        )
    council, council_file_sha = _read_json(council_path, MAX_COUNCIL_BYTES)
    context, context_file_sha = _read_json(context_path, MAX_CONTEXT_BYTES)
    schedule, schedule_file_sha = _read_json(schedule_path, MAX_SCHEDULE_BYTES)
    if (council.get("scope") != COUNCIL_SCOPE
            or council.get("historical_epoch_id") != EPOCH
            or council.get("final_action") != "HOLD"
            or council.get("read_only") is not True
            or council.get("council_invoked") is not False
            or council.get("prediction_registered") is not False
            or council.get("cycle_result") != "HELD_NO_DISPATCH"
            or type(council.get("council_seats_dispatched")) is not int
            or council["council_seats_dispatched"] != 0
            or council.get("independent_external_gates_verified") != 0
            or council.get("independent_source_families_verified") != 0):
        raise ResearchMemoryError("Council report has unsupported scope or authority claim.")
    if (context.get("scope") != CONTEXT_SCOPE
            or context.get("historical_epoch_id") != EPOCH
            or context.get("final_action") != "HOLD"
            or context.get("read_only") is not True):
        raise ResearchMemoryError("Point-in-time context report is not a held offline report.")
    council_cutoff = _time(council.get("as_of"))
    if council_cutoff > cutoff or _time(context.get("as_of")) > council_cutoff:
        raise ResearchMemoryError("Council/context report exceeds review cutoff.")
    _hash(council.get("report_sha256"), "council_report_sha256")
    _hash(context.get("report_sha256"), "context_report_sha256")
    if _digest({k: v for k, v in council.items() if k != "report_sha256"}) != council["report_sha256"]:
        raise ResearchMemoryError("Council report SHA-256 does not match bytes.")
    if _digest({k: v for k, v in context.items() if k != "report_sha256"}) != context["report_sha256"]:
        raise ResearchMemoryError("Context report SHA-256 does not match bytes.")
    try:
        reproduced = assess_stage8_council(
            epoch_id=EPOCH,
            as_of=council["as_of"],
            policy_file=policy_file,
            context_report=context_path,
            seat_schedule=schedule_path,
            contracts_file=contracts_file,
            manifest_file=manifest_file,
        )
    except (CouncilSandboxError, OSError, ValueError, TypeError):
        raise ResearchMemoryError(
            "Earlier Council report cannot be reproduced from frozen inputs."
        ) from None
    if council != reproduced:
        raise ResearchMemoryError("Earlier Council report changed on causal replay.")
    ctx = context["context"]
    if (ctx.get("context_sha256") != council["context"]["context_sha256"]
            or ctx.get("decision_key") != council["context"]["decision_key"]
            or council["context"]["context_file_sha256"] != context_file_sha
            or council["synthetic_schedule"]["index_sha256"] != schedule_file_sha
            or schedule.get("decision_key") != ctx.get("decision_key")
            or schedule.get("context_sha256") != ctx.get("context_sha256")):
        raise ResearchMemoryError("Council/source/decision identity mismatch.")
    if council["synthetic_schedule"]["model_mock_digest"] is not None:
        _hash(council["synthetic_schedule"]["model_mock_digest"], "synthetic_model_mock_sha256")
    return {
        "council": council, "context": context, "schedule": schedule,
        "council_file_sha256": council_file_sha,
        "context_file_sha256": context_file_sha,
        "schedule_file_sha256": schedule_file_sha,
        "council_cutoff": council_cutoff,
    }


def _explanation(bundle):
    if bundle is None:
        return {
            "status": "NOT_SUPPLIED", "explanation_sha256": None,
            "contributing_seats": [], "contrary_seats": [], "blocked_seats": [],
            "governance": None, "consensus": None,
            "source_ancestry_record_ids": [], "context_sha256": None,
            "decision_key": None, "reason_codes": ["NO_REPRODUCIBLE_COUNCIL_PACKET"],
        }
    council = bundle["council"]
    context = bundle["context"]["context"]
    schedule = bundle["schedule"]
    preflight = council["rehearsal"]
    rows = {x["seat_number"]: x for x in schedule["seat_results"]}
    states = {x["seat_number"]: x for x in preflight["seat_records"]}
    fields = {x["label"]: x for x in context["context_fields"]}
    accepted = []
    contributors = []
    contrary = []
    blocked = []
    all_sources = set()
    majority = preflight["primary_majority_direction"]
    for num in range(1, 29):
        state = states[num]
        raw = rows.get(num)
        if raw is not None and state["outcome_digest"] != _digest(raw):
            raise ResearchMemoryError("Council seat outcome digest changed.")
        summary = {
            "seat_number": num, "seat_id": state["seat_id"],
            "state": state["status"], "outcome_digest": state["outcome_digest"],
            "direction": None, "confidence_claim": None,
            "cited_source_family_labels": [], "cited_derived_labels": [],
            "source_ancestry_record_ids": [], "reliability_versions": [],
            "reason_code": None, "view": "BLOCKED_OR_NOT_SUBMITTED",
        }
        if raw is not None and raw["outcome"] in ("RESULT", "ABSTAIN"):
            payload = raw["payload"]
            if isinstance(payload, dict):
                summary["reason_code"] = payload.get("reason_code")
        if state["status"] == "LOCAL_SYNTHETIC_RESULT_UNATTESTED":
            payload = raw["payload"]
            if payload["direction"] not in ("BUY", "SELL"):
                raise ResearchMemoryError("Valid primary seat has no directional vote.")
            confidence = Decimal(payload["confidence"])
            ancestry = set()
            reliability = set()
            sources = []
            derived = []
            for label in payload["cited_labels"]:
                item = fields[label]
                if item["status"] != "LOCAL_CANDIDATE_UNATTESTED":
                    raise ResearchMemoryError("Accepted vote cited unavailable input.")
                if label in FAMILIES:
                    sources.append(label)
                    ancestry.add(item["selected_record_id"])
                elif label in DERIVED_INPUTS:
                    derived.append(label)
                    ancestry.update(item["source_ancestry"])
                else:
                    raise ResearchMemoryError("Unrecognized cited label in accepted seat.")
                if label == "reliability":
                    reliability.add(item["selected_vintage_id"])
            summary.update({
                "direction": payload["direction"],
                "confidence_claim": str(confidence),
                "cited_source_family_labels": sorted(sources),
                "cited_derived_labels": sorted(derived),
                "source_ancestry_record_ids": sorted(ancestry),
                "reliability_versions": sorted(reliability),
                "view": "SUPPORTS_PRIMARY_MAJORITY" if payload["direction"] == majority
                        else "CONTRARY_TO_PRIMARY_MAJORITY",
            })
            accepted.append({
                "seat_number": num, "seat_id": state["seat_id"],
                "direction": payload["direction"], "confidence": confidence,
                "ancestry": ancestry,
            })
            all_sources.update(ancestry)
            (contributors if summary["view"] == "SUPPORTS_PRIMARY_MAJORITY" else contrary).append(summary)
        else:
            blocked.append(summary)
    n = len(accepted)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    # Conservative: all votes lacking source ancestry form ONE unknown cluster.
    for i in range(n):
        for j in range(i):
            if (not accepted[i]["ancestry"] and not accepted[j]["ancestry"]
                    or accepted[i]["ancestry"] & accepted[j]["ancestry"]):
                union(i, j)
    groups = {}
    for i, item in enumerate(accepted):
        groups.setdefault(find(i), []).append(item)
    clusters = []
    for members in groups.values():
        directions = {x["direction"] for x in members}
        lineage = sorted(set().union(*(x["ancestry"] for x in members)))
        clusters.append({
            "seat_numbers": sorted(x["seat_number"] for x in members),
            "shared_source_ancestry_record_ids": lineage,
            "cluster_direction": next(iter(directions)) if len(directions) == 1 else "MIXED",
            "structural_overlap_only": True,
        })
    clusters.sort(key=lambda x: x["seat_numbers"][0])
    vote_count = len(accepted)
    confidence_values = [x["confidence"] for x in accepted]
    mean = (sum(confidence_values, Decimal(0)) / vote_count) if vote_count else None
    buys = sum(x["direction"] == "BUY" for x in accepted)
    sells = vote_count - buys
    unanimous_clusters = sum(x["cluster_direction"] != "MIXED" for x in clusters)
    consensus = {
        "primary_directional_votes": vote_count,
        "primary_buy_votes": buys, "primary_sell_votes": sells,
        "primary_majority_direction": majority,
        "primary_majority_share_claim": preflight["primary_majority_share"],
        "mean_declared_confidence": None if mean is None else str(mean.quantize(Decimal("0.001"))),
        "declared_confidence_not_calibrated": True,
        "source_overlap_clusters": clusters,
        "structural_effective_diversity_upper_bound": len(clusters),
        "single_source_clusters": len(clusters) == 1 and vote_count > 0,
        "unanimous_direction_clusters": unanimous_clusters,
        "observed_error_correlation_measured": False,
        "independent_evidence_or_vote_confirmed": False,
        "metric_version": EXPLANATION_VERSION,
    }
    governance = {}
    for name, num in (("devils_advocate", 29), ("risk_officer", 30), ("chief_strategist", 31)):
        r = rows.get(num)
        state = states[num]
        governance[name] = {
            "seat_status": state["status"],
            "outcome_digest": state["outcome_digest"],
            "reason_code": r["payload"].get("reason_code") if (
                r is not None and state["status"] == "LOCAL_SYNTHETIC_RESULT_UNATTESTED"
            ) else None,
        }
    governance["risk_veto"] = preflight["declared_risk_veto"]
    governance["challenge"] = preflight["devils_advocate_challenge"]
    governance["chief_direction"] = preflight["declared_strategist_direction"]
    governance["council_ruleset_version"] = council["dispatch_contract"]["policy_version"]
    governance["risk_limits_vintage_id"] = fields["risk_limits"]["selected_vintage_id"]
    governance["risk_state_vintage_id"] = fields["risk_state"]["selected_vintage_id"]
    governance["reliability_vintage_id"] = fields["reliability"]["selected_vintage_id"]
    governance["locally_consistent_synthetic_governance"] = preflight[
        "locally_consistent_synthetic_governance"
    ]
    governance["synthetic_proposed_direction"] = preflight[
        "synthetic_only_proposed_direction"
    ]
    governance["risk_rules_independently_approved"] = False
    material = {
        "historical_epoch_id": EPOCH,
        "decision_key": context["decision_key"],
        "context_sha256": context["context_sha256"],
        "council_report_sha256": council["report_sha256"],
        "council_manifest_sha256": council["dispatch_contract"]["manifest_sha256"],
        "policy_sha256": POLICY_SHA256,
        "version": EXPLANATION_VERSION,
        "contributing_seats": contributors,
        "contrary_seats": contrary,
        "blocked_seats": blocked,
        "governance": governance,
        "consensus": consensus,
        "source_ancestry_record_ids": sorted(all_sources),
        "reason_codes": preflight["reason_codes"],
    }
    explanation = {
        "status": "LOCAL_SYNTHETIC_EXPLANATION_UNATTESTED",
        **material,
        "explanation_sha256": _digest(material),
        "agents_executed": 0,
        "forecast_registered": False,
    }
    return explanation


def _proposal(path, bundle, cutoff):
    if path is None:
        return {
            "status": "NOT_SUPPLIED", "prediction_id": None,
            "forecast_envelope_sha256": None, "horizon_seconds": None,
            "target_at": None, "direction": None, "probability_up": None,
            "registration_status": "NO_FORECAST_REGISTERED",
            "independent_predecision_receipt": False,
        }
    if bundle is None:
        raise ResearchMemoryError("Forecast proposal requires fully replayed Council inputs.")
    candidate, candidate_file_sha = _read_json(path, MAX_FORECAST_BYTES)
    _strict(candidate, FORECAST_KEYS, "unregistered_forecast_proposal")
    council = bundle["council"]
    context = bundle["context"]["context"]
    _identifier(candidate["packet_id"], "forecast_packet_id")
    if (type(candidate["schema_version"]) is not int or candidate["schema_version"] != 1
            or candidate["historical_epoch_id"] != EPOCH
            or candidate["mode"] != "SYNTHETIC_OFFLINE_UNREGISTERED"
            or candidate["horizon_definition"] != "DECISION_AT_PLUS_SECONDS"
            or candidate["decision_key"] != context["decision_key"]
            or candidate["context_sha256"] != context["context_sha256"]
            or candidate["council_report_sha256"] != council["report_sha256"]):
        raise ResearchMemoryError("Synthetic forecast identity or unregistered scope mismatch.")
    if type(candidate["horizon_seconds"]) is not int or candidate["horizon_seconds"] not in SUPPORTED_HORIZONS:
        raise ResearchMemoryError("Unsupported fixed synthetic forecast horizon.")
    direction = candidate["direction"]
    if direction not in ("BUY", "SELL", "HOLD"):
        raise ResearchMemoryError("Forecast direction is not canonical.")
    if direction != council["rehearsal"]["synthetic_only_proposed_direction"]:
        raise ResearchMemoryError("Forecast attempts to override held Council proposal.")
    mock = council["synthetic_schedule"]["model_mock_digest"]
    if candidate["model_context_sha256"] != mock:
        raise ResearchMemoryError("Forecast model mock ancestry does not match Council.")
    if mock is None and direction != "HOLD":
        raise ResearchMemoryError("Directional proposal has no model mock lineage.")
    probability = candidate["probability_up"]
    if direction == "HOLD":
        if probability is not None:
            raise ResearchMemoryError("HOLD cannot masquerade as a probability forecast.")
    else:
        if not isinstance(probability, str) or not PROBABILITY.fullmatch(probability):
            raise ResearchMemoryError("Bounded decimal probability string required.")
        p = Decimal(probability)
        if (direction == "BUY" and p < Decimal("0.5")) or (
                direction == "SELL" and p > Decimal("0.5")):
            raise ResearchMemoryError("Direction contradicts declared synthetic probability.")
    decision_at = _time(context["decision_at"])
    target = decision_at + timedelta(seconds=candidate["horizon_seconds"])
    proposed = _time(candidate["proposed_at"])
    if not (decision_at <= bundle["council_cutoff"] <= proposed <= cutoff and proposed < target):
        raise ResearchMemoryError("Synthetic forecast fails pre-target chronology gate.")
    # Forecast ID is deterministic on content, but never a real registration ID.
    identity = {
        "namespace": "SYNTHETIC_NOT_REGISTERED",
        "historical_epoch_id": EPOCH,
        "decision_key": context["decision_key"],
        "context_sha256": context["context_sha256"],
        "council_report_sha256": council["report_sha256"],
        "horizon_seconds": candidate["horizon_seconds"],
        "direction": direction, "probability_up": probability,
        "model_context_sha256": mock, "proposed_at": proposed.isoformat(),
        "contract_version": FORECAST_VERSION,
    }
    return {
        "status": "LOCAL_SYNTHETIC_FORECAST_PROPOSAL_UNREGISTERED",
        "prediction_id": _digest(identity),
        "prediction_id_is_real_registration": False,
        "forecast_envelope_sha256": _digest(identity),
        "candidate_file_sha256": candidate_file_sha,
        "horizon_seconds": candidate["horizon_seconds"],
        "target_at": target.isoformat(),
        "proposed_at": proposed.isoformat(),
        "direction": direction, "probability_up": probability,
        "model_context_sha256": mock,
        "forecast_specification_version": FORECAST_VERSION,
        "registration_status": "UNREGISTERED_NO_INDEPENDENT_PREDECISION_RECEIPT",
        "independent_predecision_receipt": False,
        "pre_target_claim_locally_consistent": True,
        "observed_outcome_used": False,
        "forecast_outcome_registered": False,
    }


def _event(sequence, kind, effective_at, previous, payload):
    material = {
        "sequence": sequence, "event_type": kind,
        "effective_at": effective_at, "previous_sha256": previous,
        "payload_sha256": _digest(payload), "terminal_state": "HELD",
    }
    return {**material, "event_sha256": _digest(material)}


def _journal(index_path, bundle, explanation, proposal, cutoff):
    if bundle is None:
        if index_path is not None:
            raise ResearchMemoryError("Journal requires the original Council/context bundle.")
        return {
            "status": "NOT_SUPPLIED", "existing_entries": 0,
            "proposed_append_events": [], "preview_tip_sha256": None,
            "existing_local_index_sha256": None,
            "prefix_locally_verified": False,
            "independent_append_only_custody_verified": False,
            "journal_persisted": False, "events_written": 0,
        }
    council = bundle["council"]
    ctx = bundle["context"]["context"]
    event_time = bundle["council_cutoff"].isoformat()
    expected = []
    causes = (
        ("CONTEXT_FROZEN", {"decision_key": ctx["decision_key"],
                            "context_sha256": ctx["context_sha256"]}),
        ("COUNCIL_REHEARSAL_HELD", {"council_report_sha256": council["report_sha256"],
                                    "cycle_result": "HELD_NO_DISPATCH"}),
        ("EXPLANATION_FROZEN", {"explanation_sha256": explanation["explanation_sha256"],
                                "final_action": "HOLD"}),
    )
    for kind, payload in causes:
        expected.append(_event(len(expected) + 1, kind, event_time,
                               ZERO_SHA if not expected else expected[-1]["event_sha256"], payload))
    if proposal["prediction_id"] is not None:
        expected.append(_event(4, "FORECAST_PROPOSAL_HELD", proposal["proposed_at"],
                               expected[-1]["event_sha256"], {
            "synthetic_prediction_id": proposal["prediction_id"],
            "envelope_sha256": proposal["forecast_envelope_sha256"],
            "registration_status": proposal["registration_status"],
        }))
    prior_sha = None
    existing = []
    if index_path is not None:
        doc, prior_sha = _read_json(index_path, MAX_JOURNAL_BYTES)
        _strict(doc, JOURNAL_KEYS, "prior_local_journal_prefix")
        _identifier(doc["packet_id"], "journal_packet_id")
        if (type(doc["schema_version"]) is not int or doc["schema_version"] != 1
                or doc["historical_epoch_id"] != EPOCH
                or doc["mode"] != "LOCAL_UNATTESTED_APPEND_ONLY_CLAIM"
                or doc["decision_key"] != ctx["decision_key"]
                or doc["context_sha256"] != ctx["context_sha256"]
                or doc["council_report_sha256"] != council["report_sha256"]
                or _time(doc["as_of"]) > cutoff):
            raise ResearchMemoryError("Prior local journal epoch or context binding mismatch.")
        existing = doc["events"]
        if not isinstance(existing, list) or len(existing) > MAX_JOURNAL_EVENTS:
            raise ResearchMemoryError("Journal entry count invalid.")
        previous = ZERO_SHA
        previous_at = None
        for pos, item in enumerate(existing):
            _strict(item, EVENT_KEYS, "prior_journal_entry")
            if type(item["sequence"]) is not int or item["sequence"] != pos + 1:
                raise ResearchMemoryError("Noncontiguous or duplicate journal sequence.")
            if item["event_type"] not in EVENT_KINDS or item["event_type"] != EVENT_KINDS[pos]:
                raise ResearchMemoryError("Journal event ordering or kind mismatch.")
            when = _time(item["effective_at"])
            if (when > cutoff or (previous_at is not None and when < previous_at)):
                raise ResearchMemoryError("Journal event time outside causal cutoff/order.")
            previous_at = when
            if item["previous_sha256"] != previous:
                raise ResearchMemoryError("Journal hash chain parent mismatch.")
            _hash(item["payload_sha256"], "journal_payload_sha256")
            _hash(item["event_sha256"], "journal_event_sha256")
            if item["terminal_state"] != "HELD" or _digest({
                k: v for k, v in item.items() if k != "event_sha256"
            }) != item["event_sha256"]:
                raise ResearchMemoryError("Prior local journal event content changed.")
            previous = item["event_sha256"]
        if len(existing) > len(expected):
            raise ResearchMemoryError("Journal contains unauthorized or future-stage events.")
        for pos, item in enumerate(existing):
            if item != expected[pos]:
                raise ResearchMemoryError("Journal prior event edit/rewrite refused.")
    preview = expected[len(existing):]
    return {
        "status": "LOCAL_APPEND_ONLY_DESIGN_UNATTESTED",
        "existing_entries": len(existing),
        "proposed_append_events": preview,
        "preview_tip_sha256": expected[-1]["event_sha256"],
        "existing_local_index_sha256": prior_sha,
        "prefix_locally_verified": index_path is not None,
        "independent_append_only_custody_verified": False,
        "journal_persisted": False,
        "events_written": 0,
        "lifecycle_terminal_state": "HELD",
        "lifecycle_reason_codes": sorted(set(council["rehearsal"]["reason_codes"] + [
            "NO_EXTERNAL_GATE_ACCEPTANCE_OR_PREDECISION_FORECAST_RECEIPT"
        ])),
        "later_outcome_or_label_used": False,
        "journal_contract_version": JOURNAL_VERSION,
    }


def assess_stage8_explainable_memory(*, epoch_id, as_of,
                                      policy_file=POLICY_PATH,
                                      council_report=None, context_report=None,
                                      seat_schedule=None, forecast_proposal=None,
                                      prior_journal=None,
                                      contracts_file="backend/agents/contracts.py",
                                      manifest_file="backend/agents/council_manifest.py"):
    if epoch_id != EPOCH:
        raise ResearchMemoryError("Historical epoch mismatch; rebinding refused.")
    cutoff = _time(as_of)
    if cutoff > datetime.now(UTC):
        raise ResearchMemoryError("Future assessment cutoff refused.")
    try:
        policy_sha = sha256(_read(policy_file, 16384)).hexdigest()
    except (CouncilSandboxError, OSError):
        raise ResearchMemoryError("Historical policy file unreadable or unsafe.") from None
    if policy_sha != POLICY_SHA256:
        raise ResearchMemoryError("Immutable historical policy SHA-256 mismatch.")
    bundle = _bundle(council_report, context_report, seat_schedule, cutoff,
                     policy_file, contracts_file, manifest_file)
    explanation = _explanation(bundle)
    forecast = _proposal(forecast_proposal, bundle, cutoff)
    journal = _journal(prior_journal, bundle, explanation, forecast, cutoff)
    blockers = [
        "STAGE7_INDEPENDENT_GATES_NOT_VERIFIED",
        "THIRTEEN_SOURCE_FAMILIES_NOT_INDEPENDENTLY_ADMITTED",
        "G06_MODEL_AND_FEATURE_ANCESTRY_UNATTESTED",
        "RISK_AND_OPERATOR_SCOPE_NOT_APPROVED",
        "NO_INDEPENDENT_PREDECISION_PREDICTION_RECEIPT",
        "LOCAL_JOURNAL_PREVIEW_NOT_PERSISTENCE_OR_WITNESS",
        "NO_ACTUAL_COUNCIL_EXECUTION_OR_FORECAST_REGISTRATION",
    ]
    if bundle is None:
        blockers.append("NO_REPRODUCIBLE_COUNCIL_CONTEXT_AND_SCHEDULE_SUPPLIED")
    if forecast["prediction_id"] is None:
        blockers.append("NO_SYNTHETIC_FORECAST_PROPOSAL_SUPPLIED")
    if prior_journal is None:
        blockers.append("NO_LOCAL_PRIOR_JOURNAL_INDEX_SUPPLIED")
    report = {
        "version": VERSION,
        "scope": "P8_13_TO_P8_16_EXPLAINABLE_RESEARCH_MEMORY",
        "historical_epoch_id": EPOCH,
        "as_of": cutoff.isoformat(),
        "historical_policy_sha256": policy_sha,
        "mode": "READ_ONLY_SYNTHETIC_EXPLANATION_NO_REGISTRATION",
        "council_input": {
            "status": "NOT_SUPPLIED" if bundle is None else "REPLAYED_LOCAL_SYNTHETIC_UNATTESTED",
            "council_report_sha256": None if bundle is None else bundle["council"]["report_sha256"],
            "context_sha256": None if bundle is None else bundle["context"]["context"]["context_sha256"],
            "schedule_file_sha256": None if bundle is None else bundle["schedule_file_sha256"],
            "council_replay_matches_supplied": bundle is not None,
            "independently_attested": False,
        },
        "explanation": explanation,
        "forecast_envelope": forecast,
        "decision_journal": journal,
        "council_seats_executed": 0,
        "prediction_registered": False,
        "independent_predecision_receipts_verified": 0,
        "independent_external_gates_verified": 0,
        "independent_source_families_verified": 0,
        "read_only": True,
        "files_written": False,
        "network_accessed": False,
        "broker_connected": False,
        "collector_started": False,
        "council_invoked": False,
        "model_loaded": False,
        "training_authorized": False,
        "settlement_invoked": False,
        "paper_orders_authorized": False,
        "live_orders_authorized": False,
        "historical_epoch_rebound": False,
        "future_epoch_created": False,
        "operator_approval_granted": False,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(blockers),
        "final_action": "HOLD",
    }
    report["report_sha256"] = _digest(report)
    return report


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-memory", action="store_true", required=True)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--policy-file", default=POLICY_PATH)
    parser.add_argument("--council-report")
    parser.add_argument("--context-report")
    parser.add_argument("--seat-schedule")
    parser.add_argument("--forecast-proposal")
    parser.add_argument("--prior-journal")
    parser.add_argument("--contracts-file", default="backend/agents/contracts.py")
    parser.add_argument("--manifest-file", default="backend/agents/council_manifest.py")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = assess_stage8_explainable_memory(
            epoch_id=args.epoch_id, as_of=args.as_of,
            policy_file=args.policy_file,
            council_report=args.council_report,
            context_report=args.context_report, seat_schedule=args.seat_schedule,
            forecast_proposal=args.forecast_proposal, prior_journal=args.prior_journal,
            contracts_file=args.contracts_file, manifest_file=args.manifest_file,
        )
    except (ResearchMemoryError, CouncilSandboxError, ValueError, OSError, TypeError):
        print("STAGE 8 RESEARCH MEMORY BLOCKED: Unsafe, changed or noncausal offline input.")
        return 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    else:
        exp = report["explanation"]
        forecast = report["forecast_envelope"]
        print("EURUSD ORACLE | Stage 8 P8-13..16 offline explainable memory")
        print("Historical clock policy: ORIGINAL_BYTES_MATCH_PIN")
        print("Council replay: " + report["council_input"]["status"])
        print("Explanation: " + exp["status"])
        if exp["consensus"] is not None:
            print("Primary votes: %d | Structural diversity upper bound: %d" % (
                exp["consensus"]["primary_directional_votes"],
                exp["consensus"]["structural_effective_diversity_upper_bound"],
            ))
        print("Forecast: " + forecast["registration_status"])
        print("Journal: " + report["decision_journal"]["status"])
        print("Actual Council seats executed: 0 | Forecasts registered: 0")
        print("Independent gates/families: 0/11 | 0/13")
        for blocker in report["blockers"]:
            print("  BLOCKER " + blocker)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | LOCAL UNATTESTED | NO REGISTER | NO BROKER | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()

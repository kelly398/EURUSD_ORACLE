
"""P9-21..24: read-only Stage 9 challenger, selection and evidence handover.

All challenge attempts and reviewer packets are LOCAL UNATTESTED CLAIMS.
Blind aliases do not establish independent blinding; hashes do not prove
preoutcome registration or external custody. The existing synthetic scorecard
is recomputed from its original inputs, not accepted as self-attested evidence.

No test holdout opening, model inference, retraining, Council invocation,
forecast registration, network access, disk writes or trading. FINAL=HOLD.
"""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from backend.runtime.stage8_m5_offline_heartbeat import (
    EPOCH, POLICY_PATH, POLICY_SHA256, OfflineHeartbeatError, _read, _decode,
    _digest, _ident,
)
from backend.runtime.stage9_predeclared_experiment_protocol import (
    ExperimentProtocolError, BASELINE_ORDER, _time, assess_stage9_experiment_protocol,
)
from backend.runtime.stage9_offline_costed_robustness_scorecard import (
    OfflineScorecardError, assess_stage9_offline_scorecard,
)

VERSION = "EURUSD_STAGE9_INDEPENDENT_CHALLENGE_DECISION_P9_21_24_V1"
CHALLENGE_VERSION = "LOCAL_UNATTESTED_SIX_WAY_BLIND_CHALLENGE_V1"
SELECTION_VERSION = "LOCAL_TRIAL_AND_MULTIPLICITY_AUDIT_V1"
REVIEW_VERSION = "INDEPENDENT_RESEARCH_REVIEW_REQUEST_PREVIEW_UNSENT_V1"
MEMO_VERSION = "STAGE9_RESEARCH_DECISION_MEMORANDUM_V1"
MAX_CHALLENGE_BYTES = 131072
MAX_REVIEW_BYTES = 32768
MAX_TRIALS = 64
MAX_REVIEWERS = 8
UTC = timezone.utc
ZERO = "0" * 64
CHALLENGERS = ("ORACLE",) + BASELINE_ORDER
PHASES_ALLOWED = ("TRAIN", "VALIDATION", "CALIBRATION")
TRIAL_KINDS = (
    "INITIAL", "CONFIG_CHANGE", "LOOKBACK_CHANGE", "ABANDONED", "EVALUATION_RESTART",
)
RESTART_REASONS = ("DATA_CORRECTION", "MISSING_INPUT", "CODE_CHANGE", "SPLIT_REWORK")
DECISIONS = (
    "NOT_ENOUGH_EVIDENCE", "NO_DEMONSTRATED_EDGE", "CONDITIONAL_CANDIDATE",
    "INDEPENDENTLY_SUPPORTED_FOR_RESEARCH_ONLY",
)
CHALLENGE_KEYS = {
    "schema_version", "historical_epoch_id", "packet_id", "mode", "contract_version",
    "experiment_id", "protocol_candidate_sha256", "custody_report_sha256",
    "outcome_report_sha256", "scorecard_report_sha256", "locked_at",
    "selection_window_start_at", "final_test_start_at", "final_test_opened",
    "all_candidate_test_results_unseen", "publication_policy", "completeness_claim",
    "blind_salt_sha256", "candidates", "trials", "declared_total_trials",
}
CANDIDATE_KEYS = {"algorithm_id", "blind_alias", "configuration_sha256"}
TRIAL_KEYS = {
    "ordinal", "attempt_id", "algorithm_id", "attempt_kind", "configuration_sha256",
    "lookback_m5_bars", "partition", "run_at", "result_digest_claim",
    "restart_reason", "previous_attempt_sha256", "attempt_sha256",
}
REVIEW_KEYS = {
    "schema_version", "historical_epoch_id", "packet_id", "mode", "contract_version",
    "experiment_id", "challenge_file_sha256", "scorecard_report_sha256",
    "independent_approval_claim", "request_transmitted", "reviewers",
}
REVIEWER_KEYS = {
    "reviewer_id", "reviewer_role", "reviewed_at", "local_review_status",
    "review_digest_claim", "conflict_disclosure", "signature_verified",
}
REVIEW_ROLES = (
    "DATA_CUSTODY_REVIEWER_CLAIM", "STATISTICAL_REVIEWER_CLAIM",
    "RISK_REVIEWER_CLAIM",
)
REVIEW_STATUSES = ("NOT_REVIEWED", "LOCAL_READ_ONLY_REVIEW_CLAIM", "LOCAL_BLOCKERS_IDENTIFIED")
CODE_INVENTORY = (
    ("stage8_handover", "stage8_recovery_observability_handover.py"),
    ("experiment_protocol", "stage9_predeclared_experiment_protocol.py"),
    ("forecast_custody", "stage9_forecast_custody_reconciliation.py"),
    ("outcome_maturation", "stage9_outcome_maturation_reconciliation.py"),
    ("offline_scorecard", "stage9_offline_costed_robustness_scorecard.py"),
    ("challenge_and_handover", "stage9_independent_challenge_decision.py"),
)
STAGE8_COMMIT = "7b07586448ccef6b9c0bfabd65e2e1e29c2a4329"
STAGE9_BASELINE_COMMIT = "9e35ad9d774059a0635bd23944a2a4bd7f6fe158"


class ChallengeDecisionError(ValueError):
    """Invalid or falsely authorizing local Stage 9 evaluation material."""


def _strict(obj, keys, label):
    if not isinstance(obj, dict) or set(obj) != keys:
        raise ChallengeDecisionError("Strict schema mismatch: " + label)


def _id(value, label):
    try:
        return _ident(value, label)
    except (OfflineHeartbeatError, TypeError, ValueError):
        raise ChallengeDecisionError("Unsafe identifier: " + label) from None


def _hex(value, label):
    if (not isinstance(value, str) or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)):
        raise ChallengeDecisionError("Lowercase SHA-256 required: " + label)
    return value


def _clock(value):
    try:
        return _time(value)
    except (ExperimentProtocolError, TypeError, ValueError):
        raise ChallengeDecisionError("Canonical UTC Z timestamp required.") from None


def _file(path, bound):
    try:
        raw = _read(path, bound)
        return _decode(raw), sha256(raw).hexdigest()
    except (OfflineHeartbeatError, OSError, TypeError, ValueError, UnicodeError):
        raise ChallengeDecisionError("Unsafe, oversized or invalid local JSON evidence.") from None


def _inventory():
    root = Path(__file__).resolve().parent
    records = []
    for name, filename in CODE_INVENTORY:
        try:
            code = _read(root / filename, 131072)
        except (OfflineHeartbeatError, OSError):
            raise ChallengeDecisionError("Local engineering module unavailable for review.") from None
        records.append({
            "component": name,
            "source_file": "backend/runtime/" + filename,
            "local_code_sha256": sha256(code).hexdigest(),
            "independently_reviewed": False,
        })
    return records


def _trial_audit(trials, windows, locked_at, max_configs, hypothesis_count, alpha):
    if not isinstance(trials, list) or len(trials) > MAX_TRIALS:
        raise ChallengeDecisionError("Too many local trial-history entries.")
    seen, last_time, previous = set(), None, ZERO
    previous_by_algorithm = {}
    last_kind_by_algorithm = {}
    event_counts = {name: 0 for name in TRIAL_KINDS}
    attempted = set()
    compact = []
    for offset, trial in enumerate(trials):
        _strict(trial, TRIAL_KEYS, "local_trial_history_record")
        if type(trial["ordinal"]) is not int or trial["ordinal"] != offset:
            raise ChallengeDecisionError("Trial ordinals must be contiguous and zero-based.")
        attempt_id = _id(trial["attempt_id"], "attempt_id")
        if attempt_id in seen:
            raise ChallengeDecisionError("Duplicate local attempt identity.")
        seen.add(attempt_id)
        algorithm = trial["algorithm_id"]
        kind = trial["attempt_kind"]
        phase = trial["partition"]
        if algorithm not in CHALLENGERS or kind not in TRIAL_KINDS or phase not in PHASES_ALLOWED:
            raise ChallengeDecisionError("Unrecognized algorithm, trial kind or data partition.")
        digest = _hex(trial["configuration_sha256"], "attempt_configuration_sha256")
        bars = trial["lookback_m5_bars"]
        if type(bars) is not int or not 1 <= bars <= 10000:
            raise ChallengeDecisionError("Bounded causal lookback in M5 bars required.")
        when = _clock(trial["run_at"])
        start, end = windows[phase]
        if not (start <= when < end and when <= locked_at):
            raise ChallengeDecisionError("Trial claim falls outside its preregistered data partition.")
        if last_time is not None and when < last_time:
            raise ChallengeDecisionError("Trial history is not timestamp ordered.")
        last_time = when
        if trial["result_digest_claim"] is not None:
            _hex(trial["result_digest_claim"], "local_validation_result_digest")
        restart = trial["restart_reason"]
        if (kind == "EVALUATION_RESTART") != (restart is not None):
            raise ChallengeDecisionError("Only evaluation restarts can carry restart reasons.")
        if restart is not None and restart not in RESTART_REASONS:
            raise ChallengeDecisionError("Unknown or post-hoc evaluation restart reason.")
        previous_state = previous_by_algorithm.get(algorithm)
        if kind == "INITIAL" and previous_state is not None:
            raise ChallengeDecisionError("Duplicate initial trial for existing algorithm.")
        if kind != "INITIAL" and previous_state is None:
            raise ChallengeDecisionError("Changed/abandoned trial requires existing algorithm history.")
        if kind == "LOOKBACK_CHANGE" and bars == previous_state["bars"]:
            raise ChallengeDecisionError("Lookback-change claim did not change lookback.")
        if kind == "CONFIG_CHANGE" and digest == previous_state["configuration"]:
            raise ChallengeDecisionError("Configuration-change claim did not change config digest.")
        if trial["previous_attempt_sha256"] != previous:
            raise ChallengeDecisionError("Trial history parent hash mismatch.")
        computed = _digest({k: v for k, v in trial.items() if k != "attempt_sha256"})
        if trial["attempt_sha256"] != computed:
            raise ChallengeDecisionError("Trial history event content hash mismatch.")
        previous = computed
        previous_by_algorithm[algorithm] = {"bars": bars, "configuration": digest}
        last_kind_by_algorithm[algorithm] = kind
        event_counts[kind] += 1
        attempted.add((algorithm, digest, bars))
        compact.append({
            "ordinal": offset, "attempt_id": attempt_id, "algorithm_id": algorithm,
            "attempt_kind": kind, "partition": phase,
            "local_claimed_at": when.isoformat(),
            "configuration_sha256": digest, "lookback_m5_bars": bars,
            "attempt_sha256": computed,
        })
    total_families = max(max_configs, hypothesis_count * len(BASELINE_ORDER), len(attempted))
    a = Decimal(alpha)
    critical = [format(a / Decimal(total_families - position), ".10f")
                for position in range(total_families)]
    return {
        "contract_version": SELECTION_VERSION,
        "claimed_trial_events": len(trials),
        "distinct_algorithm_config_lookback_combinations": len(attempted),
        "attempt_kind_counts": event_counts,
        "locally_claimed_abandoned_trials": event_counts["ABANDONED"],
        "locally_claimed_lookback_changes": event_counts["LOOKBACK_CHANGE"],
        "locally_claimed_evaluation_restarts": event_counts["EVALUATION_RESTART"],
        "local_prefix_tip_sha256": previous if trials else None,
        "trial_receipts": compact,
        "last_attempt_state_by_algorithm": {
            key: {**value, "kind": last_kind_by_algorithm[key]}
            for key, value in sorted(previous_by_algorithm.items())
        },
        "minimum_planned_family_size": total_families,
        "predeclared_familywise_alpha": alpha,
        "holm_bonferroni_critical_values_by_order": critical,
        "holm_corrected_p_values_computed": False,
        "candidate_comparisons_with_test_statistics": 0,
        "independent_complete_attempt_history_verified": False,
        "unreported_or_abandoned_attempts_ruled_out": False,
        "selection_bias_independently_excluded": False,
    }


def _challenge(path, protocol, scorecard, cutoff):
    if path is None:
        return None, None
    doc, raw_sha = _file(path, MAX_CHALLENGE_BYTES)
    _strict(doc, CHALLENGE_KEYS, "local_challenger_and_selection_packet")
    _id(doc["packet_id"], "challenge_packet_id")
    if (type(doc["schema_version"]) is not int or doc["schema_version"] != 1
            or doc["historical_epoch_id"] != EPOCH
            or doc["mode"] != "LOCAL_UNATTESTED_CHALLENGE_DESIGN_ONLY"
            or doc["contract_version"] != CHALLENGE_VERSION
            or doc["experiment_id"] != protocol["experiment_id"]
            or doc["protocol_candidate_sha256"] != scorecard["protocol_candidate_sha256"]
            or doc["custody_report_sha256"] != scorecard["custody_report_sha256"]
            or doc["outcome_report_sha256"] != scorecard["outcome_report_sha256"]
            or doc["scorecard_report_sha256"] != scorecard["report_sha256"]
            or doc["final_test_opened"] is not False
            or doc["all_candidate_test_results_unseen"] is not True
            or doc["publication_policy"] != "PUBLISH_ALL_BENCHMARKS_MISSINGNESS_AND_NEGATIVE_RESULTS"
            or doc["completeness_claim"] != "LOCAL_UNVERIFIED_ATTEMPT_INVENTORY"):
        raise ChallengeDecisionError("Challenge design changes the upstream frozen evidence boundary.")
    locked = _clock(doc["locked_at"])
    selected = next(w for w in protocol["holdout"]["windows"]
                    if w["phase"] == "CHALLENGER_SELECTION")
    final = next(w for w in protocol["holdout"]["windows"]
                 if w["phase"] == "FINAL_TEST")
    declared = _clock(protocol["declared_at"])
    if (not declared <= locked <= cutoff
            or locked >= _clock(selected["start_at"])
            or doc["selection_window_start_at"] != selected["start_at"]
            or doc["final_test_start_at"] != final["start_at"]):
        raise ChallengeDecisionError("Challenge not frozen before future selection and final test.")
    _hex(doc["blind_salt_sha256"], "local_blind_salt_sha256")
    candidates = doc["candidates"]
    if not isinstance(candidates, list) or len(candidates) != len(CHALLENGERS):
        raise ChallengeDecisionError("All six Council and baseline challengers are mandatory.")
    algorithms = []
    aliases = set()
    for candidate in candidates:
        _strict(candidate, CANDIDATE_KEYS, "blind_alias_candidate")
        algorithm = candidate["algorithm_id"]
        if algorithm not in CHALLENGERS:
            raise ChallengeDecisionError("Unknown challenger cannot be injected.")
        algorithms.append(algorithm)
        alias = _id(candidate["blind_alias"], "blind_alias")
        if not alias.startswith("BLIND_") or alias in aliases:
            raise ChallengeDecisionError("Blind aliases must be distinct and non-authorizing.")
        aliases.add(alias)
        _hex(candidate["configuration_sha256"], "challenger_config_digest")
    if algorithms != list(CHALLENGERS):
        raise ChallengeDecisionError("Council and five benchmark set must remain ordered and intact.")
    trials = doc["trials"]
    if type(doc["declared_total_trials"]) is not int or doc["declared_total_trials"] != len(trials):
        raise ChallengeDecisionError("Declared trial count differs from submitted history.")
    windows = {w["phase"]: (_clock(w["start_at"]), _clock(w["end_at"]))
               for w in protocol["holdout"]["windows"] if w["phase"] in PHASES_ALLOWED}
    audit = _trial_audit(trials, windows, locked,
                         protocol["decision_thresholds"]["max_challenger_configurations"],
                         len(protocol["hypotheses"]),
                         protocol["decision_thresholds"]["familywise_alpha"])
    final_state = audit["last_attempt_state_by_algorithm"]
    for entry in candidates:
        a = entry["algorithm_id"]
        if a in final_state:
            if (final_state[a]["configuration"] != entry["configuration_sha256"]
                    or final_state[a]["kind"] == "ABANDONED"):
                raise ChallengeDecisionError("Frozen challenger does not match nonabandoned last local trial.")
    return {
        "contract_version": CHALLENGE_VERSION,
        "status": "LOCAL_UNATTESTED_CHALLENGE_DESIGN_RECONCILED",
        "packet_file_sha256": raw_sha,
        "locally_locked_at": locked.isoformat(),
        "locked_algorithm_order": algorithms,
        "blind_aliases": [x["blind_alias"] for x in candidates],
        "local_blind_map_sha256": _digest(candidates),
        "independent_blind_assignment_or_holdout_custody_verified": False,
        "final_test_outcomes_opened": False,
        "all_five_baselines_included": True,
        "selection_history": audit,
    }, raw_sha


def _review(path, challenge, challenge_sha, scorecard, cutoff, experiment_id):
    if path is not None and challenge is None:
        raise ChallengeDecisionError("Local review claims require verified local challenger design.")
    if path is None:
        return {
            "status": "NOT_SUPPLIED", "local_review_file_sha256": None,
            "reviewer_claim_count": 0, "claimed_roles": [],
            "locally_claimed_blocker_reviews": 0,
            "independently_verified_reviewer_signatures": 0,
            "independent_reviewers_accepted": 0,
        }
    doc, raw_sha = _file(path, MAX_REVIEW_BYTES)
    _strict(doc, REVIEW_KEYS, "local_reviewer_claim_packet")
    _id(doc["packet_id"], "review_packet_id")
    if (type(doc["schema_version"]) is not int or doc["schema_version"] != 1
            or doc["historical_epoch_id"] != EPOCH
            or doc["mode"] != "LOCAL_UNATTESTED_REVIEW_CLAIMS_ONLY"
            or doc["contract_version"] != REVIEW_VERSION
            or doc["experiment_id"] != experiment_id
            or doc["challenge_file_sha256"] != challenge_sha
            or doc["scorecard_report_sha256"] != scorecard["report_sha256"]
            or doc["independent_approval_claim"] is not False
            or doc["request_transmitted"] is not False):
        raise ChallengeDecisionError("Untrusted reviewer packet claims authority or wrong chain.")
    reviewers = doc["reviewers"]
    if not isinstance(reviewers, list) or len(reviewers) > MAX_REVIEWERS:
        raise ChallengeDecisionError("Bounded reviewer claim list required.")
    ids, roles, blocked = set(), set(), 0
    for reviewer in reviewers:
        _strict(reviewer, REVIEWER_KEYS, "untrusted_reviewer_claim")
        rid = _id(reviewer["reviewer_id"], "reviewer_id")
        role = reviewer["reviewer_role"]
        status = reviewer["local_review_status"]
        if rid in ids or role not in REVIEW_ROLES or role in roles or status not in REVIEW_STATUSES:
            raise ChallengeDecisionError("Duplicate or unrecognized reviewer/role claim.")
        ids.add(rid)
        roles.add(role)
        when = _clock(reviewer["reviewed_at"])
        if when < _clock(challenge["locally_locked_at"].replace("+00:00", "Z")) or when > cutoff:
            raise ChallengeDecisionError("Local reviewer claim outside review clock.")
        if reviewer["conflict_disclosure"] not in (
                "NONE_CLAIMED_UNVERIFIED", "POTENTIAL_CONFLICT_DISCLOSED"):
            raise ChallengeDecisionError("Reviewer conflict disclosure cannot self-certify independence.")
        if reviewer["signature_verified"] is not False:
            raise ChallengeDecisionError("Local reviewer signature cannot self-certify verification.")
        if (reviewer["review_digest_claim"] is None) != (status == "NOT_REVIEWED"):
            raise ChallengeDecisionError("Reviewer digest and review status inconsistent.")
        if reviewer["review_digest_claim"] is not None:
            _hex(reviewer["review_digest_claim"], "local_review_digest_claim")
        blocked += int(status == "LOCAL_BLOCKERS_IDENTIFIED")
    return {
        "status": "LOCAL_UNTRUSTED_REVIEW_CLAIMS_ONLY",
        "local_review_file_sha256": raw_sha,
        "reviewer_claim_count": len(reviewers),
        "claimed_roles": sorted(roles),
        "locally_claimed_blocker_reviews": blocked,
        "independently_verified_reviewer_signatures": 0,
        "independent_reviewers_accepted": 0,
    }


def assess_stage9_challenge_decision(*, epoch_id, as_of, policy_file=POLICY_PATH,
                                       protocol_file=None, prior_protocol=None,
                                       ledger_file=None, prior_ledger=None,
                                       receipt_candidates=None, outcome_file=None,
                                       prior_outcome=None, scenario_file=None,
                                       prior_scenario=None, challenge_file=None,
                                       prior_challenge=None, reviewer_claims=None):
    if epoch_id != EPOCH:
        raise ChallengeDecisionError("Historical epoch rebinding refused.")
    cutoff = _clock(as_of)
    if cutoff > datetime.now(UTC):
        raise ChallengeDecisionError("Future review cutoff refused.")
    try:
        pin = sha256(_read(policy_file, 16384)).hexdigest()
    except (OfflineHeartbeatError, OSError):
        raise ChallengeDecisionError("Original clock policy file unsafe or missing.") from None
    if pin != POLICY_SHA256:
        raise ChallengeDecisionError("Original historical clock policy SHA-256 mismatch.")
    supplied = (protocol_file, ledger_file, outcome_file, scenario_file)
    if any(x is not None for x in supplied) and not all(x is not None for x in supplied):
        raise ChallengeDecisionError("Frozen protocol, ledger, outcome and scorecard inputs must be paired.")
    if not all(x is not None for x in supplied) and any(x is not None for x in (
            prior_protocol, prior_ledger, receipt_candidates, prior_outcome, prior_scenario)):
        raise ChallengeDecisionError("Auxiliary evidence requires the full original scorecard chain.")
    if challenge_file is not None and not all(x is not None for x in supplied):
        raise ChallengeDecisionError("Challenge requires the complete recomputed scorecard chain.")
    if prior_challenge is not None and challenge_file is None:
        raise ChallengeDecisionError("Previous local challenge cannot substitute for current packet.")
    try:
        scorecard = assess_stage9_offline_scorecard(
            epoch_id=EPOCH, as_of=as_of, policy_file=policy_file,
            protocol_file=protocol_file, prior_protocol=prior_protocol,
            ledger_file=ledger_file, prior_ledger=prior_ledger,
            receipt_candidates=receipt_candidates, outcome_file=outcome_file,
            prior_outcome=prior_outcome, scenario_file=scenario_file,
            prior_scenario=prior_scenario,
        )
    except (OfflineScorecardError, ExperimentProtocolError, OSError, ValueError,
            TypeError, KeyError, OverflowError):
        raise ChallengeDecisionError("Stage 9 scorecard chain failed read-only recomputation.") from None
    protocol = None
    if protocol_file is not None:
        protocol, _ = _file(protocol_file, 65536)
        # The scorecard already recomputed and validated this exact file.
    challenge, challenge_sha = _challenge(challenge_file, protocol, scorecard, cutoff)
    prior_sha = None
    if prior_challenge is not None:
        if Path(challenge_file).absolute() == Path(prior_challenge).absolute():
            raise ChallengeDecisionError("Distinct local previous challenge snapshot required.")
        _, prior_sha = _file(prior_challenge, MAX_CHALLENGE_BYTES)
        if prior_sha != challenge_sha:
            raise ChallengeDecisionError("Previously locked local challenge packet changed bytes.")
    reviewers = _review(
        reviewer_claims, challenge, challenge_sha, scorecard, cutoff,
        None if protocol is None else protocol["experiment_id"],
    )
    inventory = _inventory()
    review_material = {
        "contract_version": REVIEW_VERSION,
        "request_only": True,
        "transmitted": False,
        "independent_review_accepted": False,
        "reproducible_local_input_hashes": {
            "protocol_candidate_sha256": scorecard["protocol_candidate_sha256"],
            "custody_report_sha256": scorecard["custody_report_sha256"],
            "outcome_report_sha256": scorecard["outcome_report_sha256"],
            "scorecard_report_sha256": scorecard["report_sha256"],
            "challenge_file_sha256": challenge_sha,
            "prior_challenge_file_sha256": prior_sha,
        },
        "code_inventory": inventory,
        "local_code_inventory_unattested": True,
        "source_family_receipts": 0,
        "independently_registered_forecasts": 0,
        "independent_outcome_source_receipts": 0,
        "real_risk_veto_outcomes": 0,
        "independent_broker_cost_receipts": 0,
        "independent_reproducibility_reviews": 0,
        "hypothesis_count": len(scorecard["hypotheses"]),
        "five_baseline_ids": list(BASELINE_ORDER),
        "local_matured_toy_pairs": scorecard["local_matured_unattested_forecast_pairs"],
        "synthetic_full_m5_denominator": scorecard["full_m5_denominator"],
        "local_cost_stress_only": True,
        "negative_findings_must_be_published": True,
    }
    blockers = [
        "NO_INDEPENDENT_PROTOCOL_PREOUTCOME_REGISTRATION",
        "NO_INDEPENDENT_FORECAST_WITNESS_AND_OUTCOME_RECEIPTS",
        "NO_INDEPENDENT_SOURCE_FAMILY_ADMISSION_OR_STAGE7_GATES",
        "NO_INDEPENDENT_REVIEWER_IDENTITIES_SIGNATURES_OR_CUSTODY",
        "NO_UNTOUCHED_PROSPECTIVE_HOLDOUT_AND_SELECTION_AUDIT",
        "NO_INDEPENDENT_TIME_BLOCKED_STATISTICAL_COMPARISONS",
        "NO_ACTUAL_BROKER_COST_OR_REAL_TRADING_PROOF",
        "NO_APPROVED_RESEARCH_SHADOW_OR_OPERATIONAL_SCOPE",
    ]
    if challenge is None:
        blockers.append("NO_LOCAL_CHALLENGE_AND_SELECTION_HISTORY_PACKET")
    if scorecard["scenario_status"] == "NOT_SUPPLIED":
        blockers.append("NO_REPRODUCIBLE_LOCAL_SYNTHETIC_SCORECARD_CHAIN")
    if reviewer_claims is None:
        blockers.append("NO_LOCAL_REVIEWER_CLAIM_PACKET")
    audit = None if challenge is None else challenge["selection_history"]
    memo = {
        "contract_version": MEMO_VERSION,
        "decision_classes": list(DECISIONS),
        "research_decision": "NOT_ENOUGH_EVIDENCE",
        "decision_basis": "ZERO_INDEPENDENTLY_SCORABLE_PROSPECTIVE_FORECAST_OUTCOME_PAIRS",
        "no_demonstrated_edge_is_not_equivalent_to_no_evidence": True,
        "negative_result_disclosure_required": True,
        "false_positive_discovery_claim_allowed": False,
        "conditional_research_candidate_granted": False,
        "independent_research_support_granted": False,
        "stage9_engineering_contracts_locally_reviewable": True,
        "stage9_independent_research_exit_criteria_met": False,
        "stage10_reliability_training_or_selection_authorized": False,
        "stage10_conditional_planning_only": True,
        "shadow_or_trading_rights_granted": False,
        "next_decision_dependency": "EXTERNAL_CUSTODY_AND_PROSPECTIVE_TEST_BEFORE_RESEARCH_CONCLUSION",
        "final_action": "HOLD",
    }
    report = {
        "version": VERSION,
        "scope": "P9_21_TO_P9_24_LOCAL_INDEPENDENT_CHALLENGE_HANDOVER",
        "historical_epoch_id": EPOCH, "as_of": cutoff.isoformat(),
        "historical_policy_sha256": pin,
        "stage8_operator_reported_commit": STAGE8_COMMIT,
        "stage9_operator_reported_baseline_commit": STAGE9_BASELINE_COMMIT,
        "remote_git_commit_independently_verified": False,
        "mode": "READ_ONLY_LOCAL_UNATTESTED_RESEARCH_REVIEW",
        "challenge_status": "NOT_SUPPLIED" if challenge is None else challenge["status"],
        "challenge": challenge,
        "selection_audit": audit,
        "reviewer_claims": reviewers,
        "independent_review_request": {
            **review_material,
            "unsent_request_sha256": _digest(review_material),
        },
        "research_decision_memorandum": memo,
        "prospectively_scorable_pairs": 0,
        "eligible_final_test_outcomes_inspected": False,
        "independent_external_gates_verified": 0,
        "independent_source_families_verified": 0,
        "independent_protocol_receipts_verified": 0,
        "independent_forecast_receipts_verified": 0,
        "independent_outcome_receipts_verified": 0,
        "real_forecasts_registered": 0,
        "blind_challenger_comparison_executed": False,
        "multiple_testing_adjusted_significance_claim_made": False,
        "independent_research_review_completed": False,
        "predictive_edge_proven": False,
        "read_only": True, "files_written": False, "network_accessed": False,
        "broker_connected": False, "collector_started": False,
        "council_invoked": False, "model_loaded": False,
        "training_authorized": False, "settlement_invoked": False,
        "paper_orders_authorized": False, "live_orders_authorized": False,
        "operator_approval_granted": False, "historical_epoch_rebound": False,
        "future_epoch_created": False,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(blockers), "final_action": "HOLD",
    }
    report["report_sha256"] = _digest(report)
    return report


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-review", action="store_true", required=True)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--policy-file", default=POLICY_PATH)
    parser.add_argument("--protocol-file")
    parser.add_argument("--prior-protocol")
    parser.add_argument("--ledger-file")
    parser.add_argument("--prior-ledger")
    parser.add_argument("--receipt-candidates")
    parser.add_argument("--outcome-file")
    parser.add_argument("--prior-outcome")
    parser.add_argument("--scenario-file")
    parser.add_argument("--prior-scenario")
    parser.add_argument("--challenge-file")
    parser.add_argument("--prior-challenge")
    parser.add_argument("--reviewer-claims")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = assess_stage9_challenge_decision(
            epoch_id=args.epoch_id, as_of=args.as_of, policy_file=args.policy_file,
            protocol_file=args.protocol_file, prior_protocol=args.prior_protocol,
            ledger_file=args.ledger_file, prior_ledger=args.prior_ledger,
            receipt_candidates=args.receipt_candidates, outcome_file=args.outcome_file,
            prior_outcome=args.prior_outcome, scenario_file=args.scenario_file,
            prior_scenario=args.prior_scenario, challenge_file=args.challenge_file,
            prior_challenge=args.prior_challenge, reviewer_claims=args.reviewer_claims,
        )
    except (ChallengeDecisionError, ExperimentProtocolError, OfflineScorecardError,
            OfflineHeartbeatError, OSError, TypeError, ValueError, KeyError, OverflowError):
        print("STAGE 9 INDEPENDENT CHALLENGE BLOCKED: Invalid or self-authorizing local material.")
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Stage 9 P9-21..24 challenge and research decision")
        print("Historical clock policy: ORIGINAL_BYTES_MATCH_PIN")
        print("Challenge: " + result["challenge_status"])
        print("Local trials: %d | Independent challenger reviews: 0" % (
            0 if result["selection_audit"] is None else result["selection_audit"]["claimed_trial_events"]))
        print("Prospectively scorable pairs: 0 | Verified witnesses: 0")
        print("Decision: " + result["research_decision_memorandum"]["research_decision"])
        print("Independent gates/families: 0/11 | 0/13")
        for blocker in result["blockers"]:
            print("  BLOCKER " + blocker)
        print("SHA-256: " + result["report_sha256"])
        print("READ-ONLY | NO INDEPENDENT REVIEW | NO BROKER | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()

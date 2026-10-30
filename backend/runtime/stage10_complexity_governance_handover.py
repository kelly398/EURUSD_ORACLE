
"""P10-21..24: offline Council complexity, ablation and Stage 10 handover.

Recomputes the Stage 10 registry boundary, then checks a complete, bounded,
SYNTHETIC ablation/stress matrix. All input scores and resource measurements
are user-authored local claims, not measured benchmarks or certified results.
Every method uses the same declared universe within every stress condition.
No architectural promotion, Council dispatch, model execution or real scenario.

The Mission Control read-model preview explicitly labels every layer as
unattested or unavailable; no green dashboard or fake performance data.
FINAL=HOLD unconditionally. No filesystem writes, network or broker access.
"""

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re

from backend.runtime.stage8_m5_offline_heartbeat import (
    EPOCH, POLICY_PATH, POLICY_SHA256, OfflineHeartbeatError, _read, _decode,
    _digest, _ident,
)
from backend.runtime.stage9_predeclared_experiment_protocol import (
    ExperimentProtocolError, _time,
)
from backend.runtime.stage10_model_registry_promotion_design import (
    ModelRegistryError, assess_stage10_model_registry,
)

VERSION = 'EURUSD_STAGE10_COMPLEXITY_GOVERNANCE_HANDOVER_P10_21_24_V1'
AUDIT_VERSION = 'LOCAL_SYNTHETIC_FULL_ABLATION_AND_STRESS_MATRIX_V1'
READ_MODEL_VERSION = 'MISSION_CONTROL_READ_ONLY_STAGE10_CONTRACT_V1'
MAX_AUDIT_BYTES = 262144
MAX_SAMPLES = 25000
UTC = timezone.utc
SIGNED = re.compile(r'^-?(?:0|[1-9][0-9]{0,7})(?:\.[0-9]{1,4})?$')
METHODS = (
    'FULL_COUNCIL', 'SMALL_COUNCIL', 'SIMPLE_BASELINE',
    'CONSENSUS_NO_SCENARIOS', 'COUNCIL_NO_PATTERN_MEMORY',
    'CHALLENGER_SUBSTITUTION',
)
STRESSES = (
    'NORMAL', 'POLICY_SURPRISE', 'VOLATILITY_BREAK', 'SPREAD_WIDENING',
    'LIQUIDITY_GAP', 'SOURCE_OUTAGE',
)
AUDIT_KEYS = {
    'schema_version', 'historical_epoch_id', 'packet_id', 'mode', 'contract_version',
    'registry_report_sha256', 'declared_at', 'measurement_start_at',
    'measurement_end_at', 'final_test_opened', 'negative_results_publication',
    'sample_universe_sha256', 'sample_count', 'budget', 'matrix',
    'rejected_methods', 'source_treatment', 'risk_officer_veto_policy',
}
BUDGET_KEYS = {
    'latency_ms', 'compute_ms', 'peak_memory_mb', 'human_review_minutes',
    'source_family_count', 'failure_mode_count',
}
ROW_KEYS = {
    'method_id', 'stress_id', 'sample_universe_sha256', 'sample_count',
    'missing_count', 'costed_pips_sum_claim', 'worst_drawdown_pips_claim',
    'loss_tail_pips_claim', 'average_latency_ms', 'average_compute_ms',
    'peak_memory_mb', 'human_review_minutes', 'source_family_count',
    'distinct_failure_modes', 'veto_bypasses', 'source_class',
    'row_claim_sha256',
}
REJECT_KEYS = {'method_id', 'reason_code', 'local_result_status'}
REJECT_REASONS = (
    'NO_INDEPENDENT_IMPROVEMENT', 'COST_OVERRUN_LOCAL',
    'SOURCE_UNAVAILABLE', 'FROZEN_NEGATIVE_RESULT',
)
READ_MODELS = (
    'OVERVIEW_RESEARCH_GATE', 'COUNCIL_RELIABILITY',
    'CAUSAL_PATTERN_MEMORY', 'SCENARIO_STUDIO',
    'MODEL_REGISTRY', 'MODEL_COMPARISON',
    'COMPLEXITY_BUDGET', 'EVIDENCE_HANDOVER',
)
CODE_FILES = (
    'stage10_council_reliability_diversity_design.py',
    'stage10_causal_pattern_memory.py',
    'stage10_offline_scenario_laboratory.py',
    'stage10_model_registry_promotion_design.py',
    'stage10_complexity_governance_handover.py',
)
STAGE10_BASELINE_COMMIT_OPERATOR_REPORTED = '32c63e606eb7d73481827fc58092cce0e1850546'


class ComplexityAuditError(ValueError):
    """Unverifiable, cherry-picked, noncausal or falsely approving audit data."""


def _strict(obj, keys, name):
    if not isinstance(obj, dict) or set(obj) != keys:
        raise ComplexityAuditError('Strict schema mismatch: ' + name)


def _id(value, name):
    try:
        return _ident(value, name)
    except (OfflineHeartbeatError, ValueError, TypeError):
        raise ComplexityAuditError('Invalid identifier: ' + name) from None


def _sha(value, name):
    if (not isinstance(value, str) or len(value) != 64 or
            any(ch not in '0123456789abcdef' for ch in value)):
        raise ComplexityAuditError('Lowercase SHA-256 required: ' + name)
    return value


def _clock(value):
    try:
        return _time(value)
    except (ExperimentProtocolError, ValueError, TypeError):
        raise ComplexityAuditError('Strict canonical UTC Z timestamp required.') from None


def _file(path):
    try:
        raw = _read(path, MAX_AUDIT_BYTES)
        return _decode(raw), sha256(raw).hexdigest()
    except (OfflineHeartbeatError, OSError, UnicodeError, ValueError, TypeError):
        raise ComplexityAuditError('Unsafe, oversized or malformed local audit file.') from None


def _int(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ComplexityAuditError('Bounded integer required: ' + label)
    return value


def _decimal(value, label, *, nonnegative=False):
    if not isinstance(value, str) or SIGNED.fullmatch(value) is None:
        raise ComplexityAuditError('Bounded signed decimal required: ' + label)
    try:
        d = Decimal(value)
    except InvalidOperation:
        raise ComplexityAuditError('Nonfinite metric rejected: ' + label) from None
    if not d.is_finite() or (nonnegative and d < 0):
        raise ComplexityAuditError('Metric outside permitted domain: ' + label)
    return d


def _code_inventory():
    root = Path(__file__).resolve().parent
    indexed = []
    for filename in CODE_FILES:
        try:
            raw = _read(root / filename, 131072)
        except (OfflineHeartbeatError, OSError):
            raise ComplexityAuditError('Local Stage 10 read-only source inventory incomplete.') from None
        indexed.append({
            'component': filename[:-3], 'source_file': 'backend/runtime/' + filename,
            'local_code_sha256': sha256(raw).hexdigest(),
            'independently_reproduced': False,
        })
    return indexed


def _audit(doc, registry, cutoff):
    _strict(doc, AUDIT_KEYS, 'synthetic_complexity_audit')
    _id(doc['packet_id'], 'complexity_packet_id')
    if (type(doc['schema_version']) is not int or doc['schema_version'] != 1
            or doc['historical_epoch_id'] != EPOCH
            or doc['mode'] != 'LOCAL_SYNTHETIC_COMPLEXITY_AUDIT_ONLY'
            or doc['contract_version'] != AUDIT_VERSION
            or doc['registry_report_sha256'] != registry['report_sha256']
            or doc['final_test_opened'] is not False
            or doc['negative_results_publication'] != 'ALL_METHODS_ALL_STRESSES_AND_NEGATIVES'
            or doc['source_treatment'] != 'SYNTHETIC_UNATTESTED_NOT_MARKET_EVIDENCE'
            or doc['risk_officer_veto_policy'] != 'VETO_IS_IMMUTABLE_HOLD'):
        raise ComplexityAuditError('Local audit cannot assert independent evidence or bypass veto.')
    declared, start, end = (_clock(doc['declared_at']),
                            _clock(doc['measurement_start_at']),
                            _clock(doc['measurement_end_at']))
    if not declared <= start < end <= cutoff:
        raise ComplexityAuditError('Post-hoc or future measurement window refused.')
    universe = _sha(doc['sample_universe_sha256'], 'audit_sample_universe_sha256')
    count = _int(doc['sample_count'], 'sample_count', 1, MAX_SAMPLES)
    budget = doc['budget']
    _strict(budget, BUDGET_KEYS, 'predeclared_local_complexity_budget')
    maxima = {
        'latency_ms': 60000, 'compute_ms': 600000, 'peak_memory_mb': 16384,
        'human_review_minutes': 1440, 'source_family_count': 13,
        'failure_mode_count': 64,
    }
    for name in BUDGET_KEYS:
        _int(budget[name], name, 0, maxima[name])
    if budget['latency_ms'] == 0 or budget['peak_memory_mb'] == 0:
        raise ComplexityAuditError('Complexity budgets must be meaningful and finite.')
    rows = doc['matrix']
    if not isinstance(rows, list) or len(rows) != len(METHODS) * len(STRESSES):
        raise ComplexityAuditError('Complete six-method by six-stress matrix required.')
    expected = {(m, s) for m in METHODS for s in STRESSES}
    seen = set()
    examined = []
    stress_missing = {}
    method_overruns = {m: [] for m in METHODS}
    worst_costed = {m: None for m in METHODS}
    stress_coverage = {m: [] for m in METHODS}
    for item in rows:
        _strict(item, ROW_KEYS, 'complete_synthetic_ablation_row')
        pair = (item['method_id'], item['stress_id'])
        if pair not in expected or pair in seen:
            raise ComplexityAuditError('Missing, duplicate or invented method/stress cell.')
        seen.add(pair)
        if item['source_class'] != 'LOCAL_SYNTHETIC_UNVERIFIED':
            raise ComplexityAuditError('Ablation cannot claim verified market data.')
        if (item['sample_universe_sha256'] != universe
                or _int(item['sample_count'], 'cell_denominator', 1, MAX_SAMPLES) != count):
            raise ComplexityAuditError('Every method/stress must keep the identical sample universe.')
        missing = _int(item['missing_count'], 'missing_count', 0, count)
        if pair[1] in stress_missing and stress_missing[pair[1]] != missing:
            raise ComplexityAuditError('Missingness cannot vary by method within a stress.')
        stress_missing[pair[1]] = missing
        if pair[1] == 'SOURCE_OUTAGE' and missing == 0:
            raise ComplexityAuditError('A declared source outage cannot hide unavailable inputs.')
        gain = _decimal(item['costed_pips_sum_claim'], 'synthetic_costed_pips_sum')
        drawdown = _decimal(item['worst_drawdown_pips_claim'], 'synthetic_drawdown', nonnegative=True)
        tail = _decimal(item['loss_tail_pips_claim'], 'synthetic_loss_tail', nonnegative=True)
        if missing == count and (gain != 0 or drawdown != 0 or tail != 0):
            raise ComplexityAuditError('Completely missing sample cannot have a P&L value.')
        for name, ceiling in (
            ('average_latency_ms', 60000), ('average_compute_ms', 600000),
            ('peak_memory_mb', 16384), ('human_review_minutes', 1440),
            ('source_family_count', 13), ('distinct_failure_modes', 64),
        ):
            _int(item[name], name, 0, ceiling)
        if item['veto_bypasses'] != 0 or type(item['veto_bypasses']) is not int:
            raise ComplexityAuditError('Risk Officer veto can never be bypassed.')
        original = _digest({k: v for k, v in item.items() if k != 'row_claim_sha256'})
        if original != item['row_claim_sha256']:
            raise ComplexityAuditError('Local ablation row was modified after claim.')
        resources = {
            'average_latency_ms': 'latency_ms', 'average_compute_ms': 'compute_ms',
            'peak_memory_mb': 'peak_memory_mb', 'human_review_minutes': 'human_review_minutes',
            'source_family_count': 'source_family_count',
            'distinct_failure_modes': 'failure_mode_count',
        }
        over = sorted(name for name, bound in resources.items()
                      if item[name] > budget[bound])
        method_overruns[pair[0]].extend(over)
        stress_coverage[pair[0]].append(pair[1])
        prior = worst_costed[pair[0]]
        if prior is None or gain < prior:
            worst_costed[pair[0]] = gain
        examined.append({
            'method_id': pair[0], 'stress_id': pair[1], 'sample_universe_sha256': universe,
            'synthetic_sample_count': count, 'synthetic_missing_count': missing,
            'synthetic_costed_pips_sum_claim': item['costed_pips_sum_claim'],
            'synthetic_worst_drawdown_pips_claim': item['worst_drawdown_pips_claim'],
            'synthetic_loss_tail_pips_claim': item['loss_tail_pips_claim'],
            'local_budget_overrun_fields': over,
            'row_claim_sha256': original,
            'independent_performance_evidence': False,
        })
    if seen != expected:
        raise ComplexityAuditError('Incomplete six-by-six synthetic comparison matrix.')
    rejected = doc['rejected_methods']
    if not isinstance(rejected, list) or len(rejected) != len(METHODS):
        raise ComplexityAuditError('All methods, including negative results, need a rejection row.')
    rejection_map = {}
    for claim in rejected:
        _strict(claim, REJECT_KEYS, 'locally_rejected_or_unproven_method')
        name = claim['method_id']
        if name not in METHODS or name in rejection_map or claim['reason_code'] not in REJECT_REASONS:
            raise ComplexityAuditError('Invalid/duplicate method outcome disclosure.')
        if claim['local_result_status'] != 'UNVERIFIED_NOT_PROMOTED':
            raise ComplexityAuditError('Negative/abandoned local claims cannot self-certify promotion.')
        rejection_map[name] = claim['reason_code']
    method_summaries = []
    for name in METHODS:
        method_summaries.append({
            'method_id': name, 'stresses_present': sorted(stress_coverage[name]),
            'local_worst_stress_costed_pips_claim': str(worst_costed[name]),
            'local_resource_budget_overrun_fields': sorted(set(method_overruns[name])),
            'local_rejection_or_no_evidence_reason': rejection_map[name],
            'independent_complexity_benefit_proven': False,
            'eligible_for_council_integration': False,
        })
    return {
        'status': 'LOCAL_COMPLETE_SYNTHETIC_DESIGN_MATRIX',
        'sample_count': count, 'sample_universe_sha256': universe,
        'matrix_rows': examined, 'method_summaries': method_summaries,
        'stress_condition_missingness': {
            name: stress_missing[name] for name in STRESSES},
        'budget': budget, 'negative_results_disclosed_locally': len(rejected),
        'prospectively_eligible_scorable_rows': 0,
    }


def assess_stage10_complexity_handover(*, epoch_id, as_of, policy_file=POLICY_PATH,
                                         history_file=None, prior_history=None,
                                         pattern_file=None, prior_pattern=None,
                                         scenario_file=None, prior_scenario=None,
                                         registry_file=None, prior_registry=None,
                                         audit_file=None, prior_audit=None,
                                         contracts_file='backend/agents/contracts.py',
                                         manifest_file='backend/agents/council_manifest.py'):
    if epoch_id != EPOCH:
        raise ComplexityAuditError('Historical epoch rebinding refused.')
    cutoff = _clock(as_of)
    if cutoff > datetime.now(UTC):
        raise ComplexityAuditError('Future review cutoff refused.')
    if audit_file is not None and registry_file is None:
        raise ComplexityAuditError('Complexity audit requires original local registry report.')
    if prior_audit is not None and audit_file is None:
        raise ComplexityAuditError('Previous local audit cannot replace present audit.')
    try:
        policy = sha256(_read(policy_file, 16384)).hexdigest()
        if policy != POLICY_SHA256:
            raise ComplexityAuditError('Historical clock policy SHA-256 mismatch.')
        registry = assess_stage10_model_registry(
            epoch_id=EPOCH, as_of=as_of, policy_file=policy_file,
            history_file=history_file, prior_history=prior_history,
            pattern_file=pattern_file, prior_pattern=prior_pattern,
            scenario_file=scenario_file, prior_scenario=prior_scenario,
            registry_file=registry_file, prior_registry=prior_registry,
            contracts_file=contracts_file, manifest_file=manifest_file,
        )
    except (ModelRegistryError, OfflineHeartbeatError, ValueError, OSError,
            TypeError, KeyError, OverflowError):
        raise ComplexityAuditError('Registry or prior Stage 10 evidence failed recomputation.') from None
    if registry['final_action'] != 'HOLD' or registry['stage10_adaptive_model_selection_authorized']:
        raise ComplexityAuditError('Unexpected upstream permissions; fail closed.')
    packet_sha, previous_sha, material = None, None, None
    if audit_file is not None:
        doc, packet_sha = _file(audit_file)
        material = _audit(doc, registry, cutoff)
        if prior_audit is not None:
            if Path(audit_file).absolute() == Path(prior_audit).absolute():
                raise ComplexityAuditError('Distinct previous local audit snapshot required.')
            _, previous_sha = _file(prior_audit)
            if previous_sha != packet_sha:
                raise ComplexityAuditError('Local audit bytes changed after snapshot.')
    inventory = _code_inventory()
    read_model = []
    for name in READ_MODELS:
        read_model.append({
            'read_model_id': name, 'contract_version': READ_MODEL_VERSION,
            'display_status': 'NOT_INDEPENDENTLY_VERIFIED',
            'primary_local_dependency_sha256': registry['report_sha256'],
            'real_market_data_admitted': False,
            'verified_independent_performance_available': False,
            'must_show_hold_banner': True,
            'can_submit_orders_or_change_weights': False,
        })
    blockers = [
        'STAGE9_INDEPENDENT_RESEARCH_EXIT_NOT_MET',
        'NO_INDEPENDENT_SOURCED_MODEL_OR_TRAINING_ARTIFACT',
        'NO_INDEPENDENT_PROSPECTIVE_ABLATION_AND_STRESS_PROOF',
        'NO_VERIFIED_PROMOTION_REVIEW_OR_ROLLBACK_PERMISSION',
        'NO_ACTUAL_MEASURED_COMPLEXITY_BENCHMARKS',
        'NO_REAL_RISK_VETO_OUTCOMES_OR_COUNCIL_SELECTION_AUTHORIZATION',
        'STAGE7_EXTERNAL_GATES_0_OF_11_AND_FAMILIES_0_OF_13',
        'NO_PROSPECTIVE_STAGE10_RESEARCH_EXIT_EVIDENCE',
    ]
    if registry_file is None:
        blockers.append('NO_LOCAL_REGISTRY_DESIGN_PACKET_SUPPLIED')
    if audit_file is None:
        blockers.append('NO_LOCAL_COMPLEXITY_ABLATION_PACKET_SUPPLIED')
    handover = {
        'classification': 'OFFLINE_ENGINEERING_CONTRACTS_ONLY',
        'stage10_engineering_contracts_locally_reviewable': True,
        'stage10_independent_research_exit_met': False,
        'research_decision': 'NOT_ENOUGH_EVIDENCE',
        'accepted_for_offline_diagnostic_design': [
            'CAUSAL_RELIABILITY_AND_SHARED_ANCESTRY_PREVIEWS',
            'CAUSAL_PATTERN_SEARCH_AND_SYNTHETIC_ABLATION',
            'DETERMINISTIC_SYNTHETIC_SCENARIO_LAB',
            'LOCAL_MODEL_METADATA_REGISTRY_WITH_NO_TRAINING',
            'UNSENT_PROMOTION_AND_STATIC_ROLLBACK_DESIGN',
            'COMPLETE_SYNTHETIC_COMPLEXITY_MATRIX_SCHEMA',
        ],
        'rejected_or_unproven_claims': [
            'ADAPTIVE_WEIGHT_PROMOTION', 'REAL_MODEL_TRAINING_COMPLETED',
            'INDEPENDENT_CHALLENGER_SUPERIORITY', 'REAL_EURUSD_SIMULATION_CALIBRATION',
            'INDEPENDENT_RESEARCH_EDGE', 'SHADOW_OR_LIVE_TRADING_PERMISSION',
        ],
        'simplification_policy': 'PREFER_STATIC_SIMPLE_METHOD_UNTIL_INDEPENDENT_GAIN_ESTABLISHED',
        'production_model_selection_authorized': False,
        'stage11_read_only_mission_control_design_can_be_PLANNED': True,
        'stage11_operational_or_trading_rights': False,
        'real_research_stage10_exit_fulfilled': False,
        'fixed_recovery_path_activated': False,
        'final_action': 'HOLD',
    }
    report = {
        'version': VERSION,
        'scope': 'P10_21_TO_P10_24_COMPLEXITY_GOVERNANCE_AND_HANDOVER',
        'historical_epoch_id': EPOCH, 'as_of': cutoff.isoformat(),
        'historical_policy_sha256': policy,
        'mode': 'READ_ONLY_LOCAL_COMPLEXITY_DESIGN_NO_OPERATIONAL_RIGHTS',
        'stage10_registry_report_sha256': registry['report_sha256'],
        'registry_status': registry['registry_status'],
        'audit_status': 'NOT_SUPPLIED' if material is None else material['status'],
        'audit_contract_version': AUDIT_VERSION,
        'audit_file_sha256': packet_sha,
        'prior_local_audit_file_sha256': previous_sha,
        'methods_compared_in_local_design': list(METHODS),
        'synthetic_stresses_predeclared': list(STRESSES),
        'complexity_audit': material,
        'stage10_code_inventory': inventory,
        'source_code_hashes_prove_external_review': False,
        'stage10_operator_reported_base_commit': STAGE10_BASELINE_COMMIT_OPERATOR_REPORTED,
        'remote_git_commit_independently_verified': False,
        'mission_control_read_model_previews': read_model,
        'research_handover': handover,
        'local_synthetic_complexity_improvement_claim_accepted': False,
        'independent_research_model_selection_granted': False,
        'training_executed': False, 'model_loaded': False,
        'real_forecasts_registered': 0, 'real_council_cycles_dispatched': 0,
        'real_trades_or_broker_receipts': 0,
        'read_only': True, 'files_written': False, 'network_accessed': False,
        'broker_connected': False, 'collector_started': False,
        'council_invoked': False, 'training_authorized': False,
        'settlement_invoked': False, 'paper_orders_authorized': False,
        'live_orders_authorized': False, 'operator_approval_granted': False,
        'historical_epoch_rebound': False, 'future_epoch_created': False,
        'independent_external_gates_verified': 0,
        'independent_source_families_verified': 0,
        'research_operational_readiness': 'BLOCKED_EXTERNAL_EVIDENCE',
        'blockers': sorted(blockers), 'final_action': 'HOLD',
    }
    report['report_sha256'] = _digest(report)
    return report


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--read-only-governance', action='store_true', required=True)
    parser.add_argument('--synthetic-only', action='store_true', required=True)
    parser.add_argument('--epoch-id', required=True)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--policy-file', default=POLICY_PATH)
    parser.add_argument('--contracts-file', default='backend/agents/contracts.py')
    parser.add_argument('--manifest-file', default='backend/agents/council_manifest.py')
    parser.add_argument('--history-file')
    parser.add_argument('--prior-history')
    parser.add_argument('--pattern-file')
    parser.add_argument('--prior-pattern')
    parser.add_argument('--scenario-file')
    parser.add_argument('--prior-scenario')
    parser.add_argument('--registry-file')
    parser.add_argument('--prior-registry')
    parser.add_argument('--audit-file')
    parser.add_argument('--prior-audit')
    parser.add_argument('--json', action='store_true')
    a = parser.parse_args(argv)
    try:
        report = assess_stage10_complexity_handover(
            epoch_id=a.epoch_id, as_of=a.as_of, policy_file=a.policy_file,
            history_file=a.history_file, prior_history=a.prior_history,
            pattern_file=a.pattern_file, prior_pattern=a.prior_pattern,
            scenario_file=a.scenario_file, prior_scenario=a.prior_scenario,
            registry_file=a.registry_file, prior_registry=a.prior_registry,
            audit_file=a.audit_file, prior_audit=a.prior_audit,
            contracts_file=a.contracts_file, manifest_file=a.manifest_file,
        )
    except (ComplexityAuditError, ModelRegistryError, OfflineHeartbeatError,
            ValueError, OSError, TypeError, KeyError, OverflowError):
        print('STAGE 10 HANDOVER BLOCKED: Unsafe, incomplete or self-authorizing local audit.')
        return 1
    if a.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        print('EURUSD ORACLE | Stage 10 P10-21..24 offline complexity handover')
        print('Historical clock policy: ORIGINAL_BYTES_MATCH_PIN')
        print('Registry: ' + report['registry_status'])
        print('Audit: ' + report['audit_status'])
        print('Methods: 6 | Stress conditions: 6 | Independent comparisons: 0')
        print('Model training: NONE | Promotions: NONE | Rollback: NOT ACTIVATED')
        print('Research decision: NOT_ENOUGH_EVIDENCE | Gates/families: 0/11 | 0/13')
        for code in report['blockers']:
            print('  BLOCKER ' + code)
        print('SHA-256: ' + report['report_sha256'])
        print('READ-ONLY | ENGINEERING HANDOVER ONLY | NO BROKER | FINAL=HOLD')
    return 0


def main():
    raise SystemExit(run())


if __name__ == '__main__':
    main()

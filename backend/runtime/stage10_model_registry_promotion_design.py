
"""P10-17..20: local, non-executable challenger registry and promotion *design*.

Only inspects bounded, synthetic JSON metadata and the earlier Stage 10 read-only
scenario boundary. No model artifact or training code is opened, deserialized,
imported, trained, inferred, promoted or executed. Hashes and approvals in the
input are untrusted local claims. The optional comparison packet is descriptive,
not shadow deployment and not an untouched prospective holdout.

A candidate registry is not a model registry of available binary artifacts.
Stage 9 exit remains NOT_ENOUGH_EVIDENCE; Stage 10 training, selection and
promotion remain forbidden. The fixed, immutable Council fallback is never
replaced. No writes, network, brokers, agents or operational actions. HOLD.
"""

import argparse
from datetime import datetime, timedelta, timezone
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
from backend.runtime.stage10_offline_scenario_laboratory import (
    ScenarioLabError, assess_stage10_scenario_lab,
)
from backend.runtime.stage6_council_context_readiness import (
    CouncilContextError, inspect_static_council,
)

VERSION = 'EURUSD_STAGE10_MODEL_REGISTRY_PROMOTION_DESIGN_P10_17_20_V1'
REGISTRY_VERSION = 'SYNTHETIC_CANDIDATE_METADATA_ONLY_V1'
COMPARISON_VERSION = 'SYNTHETIC_CHALLENGER_COMPARISON_CLAIMS_V1'
PROMOTION_VERSION = 'UNSENT_BLOCKED_RESEARCH_PROMOTION_REQUEST_V1'
MAX_BYTES = 262144
MAX_MODELS = 12
MAX_COMPARISONS = 12
MIN_EMBARGO_SECONDS = 86400
UTC = timezone.utc
SHA_ZERO = '0' * 64
FLOAT_TEXT = re.compile(r'^(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,4})?$')
MODES = ('LINEAR_LOGISTIC', 'SHALLOW_TREE', 'CONSTANT_PROBABILITY')
PURPOSES = ('PROBABILITY_UP_RESEARCH', 'DIRECTIONAL_RESEARCH', 'NO_TRADE_BASELINE')
FEATURE_COMPAT = ('COMPATIBLE_LOCAL_CLAIM', 'SCHEMA_MISMATCH', 'UNKNOWN')
DRIFT_STATUSES = ('NO_CAUSAL_DRIFT_BASELINE', 'LOCAL_DRIFT_CLAIM', 'UNAVAILABLE')
REGISTRY_KEYS = {
    'schema_version', 'historical_epoch_id', 'packet_id', 'contract_version',
    'mode', 'scenario_report_sha256', 'declared_at', 'final_test_opened',
    'training_enabled', 'negative_results_publication', 'candidates',
    'comparisons', 'promotion_request', 'fixed_rollback',
}
MODEL_KEYS = {
    'candidate_id', 'candidate_version', 'model_family_id', 'algorithm',
    'purpose', 'horizon_seconds', 'artifact_digest_claim', 'artifact_format',
    'training_code_digest_claim', 'training_data_digest_claim',
    'training_window_start_at', 'training_window_end_at',
    'training_data_last_available_at', 'feature_schema_sha256',
    'feature_snapshot_sha256', 'hyperparameters', 'created_at',
    'training_job_status', 'approver_id_claim', 'approval_status',
    'candidate_metadata_sha256',
}
HYPER_KEYS = {'learning_rate', 'regularization', 'max_depth', 'seed'}
COMPARISON_KEYS = {
    'comparison_id', 'candidate_id', 'incumbent_id', 'window_start_at',
    'window_end_at', 'reported_at', 'horizon_seconds', 'sample_universe_sha256',
    'synthetic_pairs_claimed', 'missing_pairs_claimed',
    'candidate_brier_claim', 'incumbent_brier_claim',
    'feature_psi_claim', 'calibration_shift_claim', 'feature_compatibility',
    'residual_drift_status', 'comparison_status', 'independent_outcomes_verified',
    'comparison_claim_sha256',
}
PROMOTION_KEYS = {
    'contract_version', 'request_transmitted', 'signature_verified',
    'independent_reviewer_accepted', 'operator_authorization_claim',
    'candidate_requested', 'request_status', 'requires_independent_holdout',
    'model_or_weight_switch_performed', 'rollback_pointer_changed',
}
ROLLBACK_KEYS = {
    'fallback_id', 'fallback_contracts_sha256', 'fallback_manifest_sha256',
    'artifact_availability_verified', 'recovery_activation_permitted',
    'fallback_sha256',
}


class ModelRegistryError(ValueError):
    """Invalid metadata, causal comparison or self-authorizing promotion."""


def _strict(value, expected, label):
    if not isinstance(value, dict) or set(value) != expected:
        raise ModelRegistryError('Strict schema mismatch: ' + label)


def _id(value, label):
    try:
        return _ident(value, label)
    except (OfflineHeartbeatError, ValueError, TypeError):
        raise ModelRegistryError('Invalid or sensitive identifier: ' + label) from None


def _sha(value, label):
    if (not isinstance(value, str) or len(value) != 64 or
            any(c not in '0123456789abcdef' for c in value)):
        raise ModelRegistryError('Lowercase SHA-256 required: ' + label)
    return value


def _clock(value):
    try:
        return _time(value)
    except (ExperimentProtocolError, ValueError, TypeError):
        raise ModelRegistryError('Canonical Z UTC timestamp required.') from None


def _file(path):
    try:
        raw = _read(path, MAX_BYTES)
        return _decode(raw), sha256(raw).hexdigest()
    except (OfflineHeartbeatError, OSError, UnicodeError, ValueError, TypeError):
        raise ModelRegistryError('Unsafe, oversized or malformed candidate metadata.') from None


def _number(value, label, upper=100, *, permit_zero=True):
    if not isinstance(value, str) or FLOAT_TEXT.fullmatch(value) is None:
        raise ModelRegistryError('Canonical bounded decimal required: ' + label)
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ModelRegistryError('Invalid decimal: ' + label) from None
    if (not result.is_finite() or result > Decimal(str(upper))
            or (not permit_zero and result <= 0)):
        raise ModelRegistryError('Decimal outside permitted range: ' + label)
    return result


def _safe_model(item, cutoff, locked):
    _strict(item, MODEL_KEYS, 'frozen_candidate_metadata')
    for field in ('candidate_id', 'candidate_version', 'model_family_id', 'approver_id_claim'):
        _id(item[field], field)
    if item['algorithm'] not in MODES or item['purpose'] not in PURPOSES:
        raise ModelRegistryError('Unrecognized bounded research algorithm or purpose.')
    if (item['artifact_format'] not in ('NONE', 'ONNX_CLAIM', 'JSON_CLAIM')
            or item['training_job_status'] != 'NOT_EXECUTED'
            or item['approval_status'] != 'NOT_APPROVED'
            or type(item['horizon_seconds']) is not int
            or item['horizon_seconds'] not in (900, 3600, 86400)):
        raise ModelRegistryError('Unexecutable candidate and valid forecast horizon required.')
    for name in ('artifact_digest_claim', 'training_code_digest_claim',
                 'training_data_digest_claim', 'feature_schema_sha256',
                 'feature_snapshot_sha256'):
        _sha(item[name], name)
    h = item['hyperparameters']
    _strict(h, HYPER_KEYS, 'bounded_research_hyperparameters')
    _number(h['learning_rate'], 'learning_rate', 1)
    _number(h['regularization'], 'regularization', 100)
    if (type(h['max_depth']) is not int or not 1 <= h['max_depth'] <= 6
            or type(h['seed']) is not int or not 0 <= h['seed'] <= 2147483647):
        raise ModelRegistryError('Bounded depth and deterministic integer seed required.')
    trained_from = _clock(item['training_window_start_at'])
    trained_until = _clock(item['training_window_end_at'])
    available = _clock(item['training_data_last_available_at'])
    created = _clock(item['created_at'])
    if not (locked <= trained_from < trained_until <= available <= created <= cutoff):
        raise ModelRegistryError('Data window/vintage/candidate chronology is noncausal.')
    digest = _digest({k: v for k, v in item.items() if k != 'candidate_metadata_sha256'})
    if item['candidate_metadata_sha256'] != digest:
        raise ModelRegistryError('Candidate metadata claim differs from original content.')
    return {
        'candidate_id': item['candidate_id'],
        'candidate_version': item['candidate_version'],
        'model_family_id': item['model_family_id'],
        'algorithm': item['algorithm'],
        'purpose': item['purpose'],
        'horizon_seconds': item['horizon_seconds'],
        'candidate_metadata_sha256': digest,
        'artifact_digest_claim': item['artifact_digest_claim'],
        'training_code_digest_claim': item['training_code_digest_claim'],
        'training_data_digest_claim': item['training_data_digest_claim'],
        'feature_schema_sha256': item['feature_schema_sha256'],
        'feature_snapshot_sha256': item['feature_snapshot_sha256'],
        'training_window_start_at': item['training_window_start_at'],
        'training_window_end_at': item['training_window_end_at'],
        'training_data_last_available_at': item['training_data_last_available_at'],
        'data_last_available_utc': available,
        'training_ended_utc': trained_until,
        'model_created_utc': created,
        'metadata_only_not_a_verified_artifact': True,
        'training_or_model_execution_permitted': False,
        'approver_claim_independently_verified': False,
    }


def _compare(item, models, cutoff):
    _strict(item, COMPARISON_KEYS, 'untrusted_shadow_comparison')
    _id(item['comparison_id'], 'comparison_id')
    candidate = models.get(item['candidate_id'])
    incumbent = models.get(item['incumbent_id'])
    if candidate is None or incumbent is None or item['candidate_id'] == item['incumbent_id']:
        raise ModelRegistryError('Challenger must refer to a distinct registered local candidate.')
    if (item['comparison_status'] != 'LOCAL_DESCRIPTIVE_NOT_SHADOW_DEPLOYMENT'
            or item['independent_outcomes_verified'] is not False
            or item['feature_compatibility'] not in FEATURE_COMPAT
            or item['residual_drift_status'] not in DRIFT_STATUSES
            or type(item['horizon_seconds']) is not int
            or item['horizon_seconds'] != candidate['horizon_seconds']
            or item['horizon_seconds'] != incumbent['horizon_seconds']):
        raise ModelRegistryError('Untrusted comparison must preserve horizon, mode and source state.')
    start, end, reported = (
        _clock(item['window_start_at']), _clock(item['window_end_at']),
        _clock(item['reported_at']),
    )
    earliest = max(candidate['data_last_available_utc'], incumbent['data_last_available_utc'],
                   candidate['model_created_utc'], incumbent['model_created_utc'])
    if (start < earliest + timedelta(seconds=MIN_EMBARGO_SECONDS)
            or not start < end <= reported <= cutoff):
        raise ModelRegistryError('Noncausal evaluation window or failed embargo.')
    if (type(item['synthetic_pairs_claimed']) is not int
            or type(item['missing_pairs_claimed']) is not int
            or item['synthetic_pairs_claimed'] < 0 or item['missing_pairs_claimed'] < 0
            or item['synthetic_pairs_claimed'] + item['missing_pairs_claimed'] > 100000):
        raise ModelRegistryError('Bounded comparison denominator and missingness required.')
    _sha(item['sample_universe_sha256'], 'sample_universe_sha256')
    metric_names = ('candidate_brier_claim', 'incumbent_brier_claim',
                    'feature_psi_claim', 'calibration_shift_claim')
    values = []
    for name in metric_names:
        value = item[name]
        values.append(None if value is None else _number(
            value, name, 25 if name == 'feature_psi_claim' else 1))
    if not item['synthetic_pairs_claimed'] and any(v is not None for v in values):
        raise ModelRegistryError('Missing comparisons must not manufacture metric values.')
    if item['synthetic_pairs_claimed'] and any(v is None for v in values):
        raise ModelRegistryError('Synthetic metric completeness must be explicit.')
    if item['feature_compatibility'] != 'COMPATIBLE_LOCAL_CLAIM' and item['synthetic_pairs_claimed']:
        raise ModelRegistryError('Incompatible features cannot masquerade as scored comparisons.')
    claimed_sha = _digest({k: v for k, v in item.items() if k != 'comparison_claim_sha256'})
    if item['comparison_claim_sha256'] != claimed_sha:
        raise ModelRegistryError('Comparison content changed since local hash claim.')
    improvement = None
    if item['synthetic_pairs_claimed']:
        improvement = format((values[1] - values[0]).quantize(Decimal('0.0001')), 'f')
    return {
        'comparison_id': item['comparison_id'],
        'candidate_id': item['candidate_id'],
        'incumbent_id': item['incumbent_id'],
        'comparison_claim_sha256': claimed_sha,
        'sample_universe_sha256': item['sample_universe_sha256'],
        'synthetic_pairs_claimed': item['synthetic_pairs_claimed'],
        'missing_pairs_claimed': item['missing_pairs_claimed'],
        'brier_improvement_local_claim_only': improvement,
        'feature_compatibility_claim': item['feature_compatibility'],
        'feature_psi_local_claim': item['feature_psi_claim'],
        'calibration_shift_local_claim': item['calibration_shift_claim'],
        'local_drift_status': item['residual_drift_status'],
        'independent_holdout_verified': False,
        'independently_scorable_pairs': 0,
        'shadow_cycle_dispatched': False,
        'improvement_established': False,
    }


def _validate(doc, scenario, council, cutoff):
    _strict(doc, REGISTRY_KEYS, 'local_registry_design')
    _id(doc['packet_id'], 'registry_packet_id')
    if (type(doc['schema_version']) is not int or doc['schema_version'] != 1
            or doc['historical_epoch_id'] != EPOCH
            or doc['mode'] != 'SYNTHETIC_MODEL_REGISTRY_DESIGN_ONLY'
            or doc['contract_version'] != REGISTRY_VERSION
            or doc['scenario_report_sha256'] != scenario['report_sha256']
            or doc['training_enabled'] is not False
            or doc['final_test_opened'] is not False
            or doc['negative_results_publication'] != 'PUBLISH_ALL_NEGATIVE_AND_ABANDONED'):
        raise ModelRegistryError('Registry tries to rebind upstream state or authorize training.')
    locked = _clock(doc['declared_at'])
    if locked > cutoff:
        raise ModelRegistryError('Future registry declaration refused.')
    supplied = doc['candidates']
    if not isinstance(supplied, list) or not 1 <= len(supplied) <= MAX_MODELS:
        raise ModelRegistryError('Bounded nonempty synthetic candidate design required.')
    models = {}
    for obj in supplied:
        entry = _safe_model(obj, cutoff, locked)
        if entry['candidate_id'] in models:
            raise ModelRegistryError('Duplicate candidate version identity.')
        models[entry['candidate_id']] = entry
    if len(set(x['candidate_metadata_sha256'] for x in models.values())) != len(models):
        raise ModelRegistryError('Duplicate candidate content under different identities.')
    comparisons = doc['comparisons']
    if not isinstance(comparisons, list) or len(comparisons) > MAX_COMPARISONS:
        raise ModelRegistryError('Bounded local comparison history required.')
    seen, reviewed = set(), []
    for comp in comparisons:
        entry = _compare(comp, models, cutoff)
        if entry['comparison_id'] in seen:
            raise ModelRegistryError('Duplicate local comparison identity.')
        seen.add(entry['comparison_id'])
        reviewed.append(entry)
    # No post-hoc candidate suppression, shifting incumbent, or unequal
    # synthetic denominators is allowed even for non-admissible comparisons.
    if reviewed:
        incumbents = {x['incumbent_id'] for x in reviewed}
        if len(incumbents) != 1:
            raise ModelRegistryError('One common incumbent required for comparisons.')
        incumbent_id = next(iter(incumbents))
        challenger_ids = {x['candidate_id'] for x in reviewed}
        if (challenger_ids != set(models) - {incumbent_id}
                or len(reviewed) != len(challenger_ids)):
            raise ModelRegistryError('Every registered challenger must be compared once.')
        sample_identifiers = {
            (x['sample_universe_sha256'], x['synthetic_pairs_claimed'],
             x['missing_pairs_claimed']) for x in reviewed
        }
        if len(sample_identifiers) != 1:
            raise ModelRegistryError('All challengers must share one synthetic sample universe.')
    _strict(doc['promotion_request'], PROMOTION_KEYS, 'unsent_local_promotion_preview')
    req = doc['promotion_request']
    if (req['contract_version'] != PROMOTION_VERSION
            or req['request_transmitted'] is not False
            or req['signature_verified'] is not False
            or req['independent_reviewer_accepted'] is not False
            or req['operator_authorization_claim'] is not False
            or req['request_status'] != 'BLOCKED_EXTERNAL_EVIDENCE'
            or req['requires_independent_holdout'] is not True
            or req['model_or_weight_switch_performed'] is not False
            or req['rollback_pointer_changed'] is not False):
        raise ModelRegistryError('Local promotion preview claims independent authority.')
    proposed = req['candidate_requested']
    if proposed is not None and proposed not in models:
        raise ModelRegistryError('Promotion preview refers to unknown candidate.')
    _strict(doc['fixed_rollback'], ROLLBACK_KEYS, 'nonactivated_immutable_rollback')
    fallback = doc['fixed_rollback']
    if (fallback['fallback_id'] != 'STATIC_COUNCIL_ROSTER_V1'
            or fallback['fallback_contracts_sha256'] != council['contracts_sha256']
            or fallback['fallback_manifest_sha256'] != council['manifest_sha256']
            or fallback['artifact_availability_verified'] is not False
            or fallback['recovery_activation_permitted'] is not False):
        raise ModelRegistryError('Rollback must bind unmodified Council, without execution.')
    computed = _digest({k: v for k, v in fallback.items() if k != 'fallback_sha256'})
    if fallback['fallback_sha256'] != computed:
        raise ModelRegistryError('Local rollback contract hash differs from original content.')
    return {
        'registry_status': 'LOCAL_SYNTHETIC_METADATA_ONLY',
        'models': [{k: v for k, v in models[x].items() if not k.endswith('_utc')}
                   for x in sorted(models)],
        'comparisons': reviewed,
        'promotion_request': {
            'requested_candidate': proposed, 'request_status': 'BLOCKED_EXTERNAL_EVIDENCE',
            'transmitted': False, 'trusted_signatures_verified': 0,
            'research_promotion_granted': False, 'execution_permission_granted': False,
        },
        'fixed_rollback_sha256': computed,
        'fallback_reference_id': fallback['fallback_id'],
        'rollback_artifact_available_proven': False,
    }


def assess_stage10_model_registry(*, epoch_id, as_of, policy_file=POLICY_PATH,
                                  history_file=None, prior_history=None,
                                  pattern_file=None, prior_pattern=None,
                                  scenario_file=None, prior_scenario=None,
                                  registry_file=None, prior_registry=None,
                                  contracts_file='backend/agents/contracts.py',
                                  manifest_file='backend/agents/council_manifest.py'):
    if epoch_id != EPOCH:
        raise ModelRegistryError('Historical epoch rebinding refused.')
    cutoff = _clock(as_of)
    if cutoff > datetime.now(UTC):
        raise ModelRegistryError('Future review cutoff refused.')
    if registry_file is not None and (history_file is None) != (pattern_file is None):
        raise ModelRegistryError('Prior Stage 10 history and pattern inputs must be paired.')
    if prior_registry is not None and registry_file is None:
        raise ModelRegistryError('Previous local registry cannot replace current registry.')
    try:
        policy = sha256(_read(policy_file, 16384)).hexdigest()
        if policy != POLICY_SHA256:
            raise ModelRegistryError('Pinned historical clock policy SHA-256 mismatch.')
        scenario = assess_stage10_scenario_lab(
            epoch_id=EPOCH, as_of=as_of, policy_file=policy_file,
            history_file=history_file, prior_history=prior_history,
            pattern_file=pattern_file, prior_pattern=prior_pattern,
            scenario_file=scenario_file, prior_scenario=prior_scenario,
            contracts_file=contracts_file, manifest_file=manifest_file,
        )
        council = inspect_static_council(contracts_file=contracts_file, manifest_file=manifest_file)
    except (ScenarioLabError, CouncilContextError, OfflineHeartbeatError,
            ValueError, OSError, TypeError, KeyError, OverflowError):
        raise ModelRegistryError('Stage 10 original scenario/Council boundary cannot replay.') from None
    if (scenario['final_action'] != 'HOLD'
            or scenario['independent_external_gates_verified'] != 0
            or scenario['independent_source_families_verified'] != 0
            or scenario['real_market_forecasts_issued'] != 0):
        raise ModelRegistryError('Unexpected upstream research permissions; fail closed.')
    material, packet_sha, previous_sha = None, None, None
    if registry_file is not None:
        doc, packet_sha = _file(registry_file)
        material = _validate(doc, scenario, council, cutoff)
        if prior_registry is not None:
            if Path(registry_file).absolute() == Path(prior_registry).absolute():
                raise ModelRegistryError('Distinct prior local registry snapshot required.')
            _, previous_sha = _file(prior_registry)
            if previous_sha != packet_sha:
                raise ModelRegistryError('Local registry changed after frozen snapshot.')
    blockers = [
        'STAGE9_INDEPENDENT_RESEARCH_EXIT_NOT_MET',
        'NO_INDEPENDENT_TRAINING_DATA_OR_MODEL_ARTIFACT_PROVENANCE',
        'NO_AUTHORIZED_ISOLATED_TRAINING_JOB',
        'NO_INDEPENDENT_PROSPECTIVE_SHADOW_COMPARISONS',
        'NO_INDEPENDENT_DRIFT_CALIBRATION_OR_SELECTION_METRICS',
        'NO_SIGNED_REVIEW_PACKET_OR_OPERATOR_PROMOTION_APPROVAL',
        'NO_VERIFIED_RECOVERY_ARTIFACT_OR_ROLLBACK_EXECUTION_RIGHT',
        'NO_REAL_COUNCIL_WEIGHT_UPDATE_AUTHORIZATION',
    ]
    if registry_file is None:
        blockers.append('NO_LOCAL_SYNTHETIC_REGISTRY_DESIGN_SUPPLIED')
    report = {
        'version': VERSION, 'scope': 'P10_17_TO_P10_20_MODEL_REGISTRY_PROMOTION_DESIGN',
        'historical_epoch_id': EPOCH, 'as_of': cutoff.isoformat(),
        'historical_policy_sha256': policy,
        'mode': 'READ_ONLY_SYNTHETIC_CANDIDATE_METADATA_AND_ROLLBACK_DESIGN',
        'stage10_scenario_report_sha256': scenario['report_sha256'],
        'stage10_scenario_status': scenario['scenario_status'],
        'council_manifest_sha256': council['manifest_sha256'],
        'council_contracts_sha256': council['contracts_sha256'],
        'registry_status': 'NOT_SUPPLIED' if material is None else material['registry_status'],
        'registry_contract_version': REGISTRY_VERSION,
        'registry_file_sha256': packet_sha, 'prior_registry_file_sha256': previous_sha,
        'candidate_metadata_count': 0 if material is None else len(material['models']),
        'candidate_metadata': [] if material is None else material['models'],
        'local_unverified_comparisons': [] if material is None else material['comparisons'],
        'comparison_count': 0 if material is None else len(material['comparisons']),
        'promotion_preview': None if material is None else material['promotion_request'],
        'fixed_rollback_contract_sha256': None if material is None else material['fixed_rollback_sha256'],
        'static_council_fallback_id': 'STATIC_COUNCIL_ROSTER_V1',
        'local_registry_claims_are_external_receipts': False,
        'model_artifact_files_read': 0, 'model_artifact_bytes_verified': 0,
        'training_job_started': False, 'training_executed': False,
        'trained_models_registered': 0, 'verified_holdout_comparisons': 0,
        'independent_drift_evaluations': 0, 'operational_weights_promoted': 0,
        'research_promotions_granted': 0, 'rollback_or_weight_activation': False,
        'stage9_research_decision': 'NOT_ENOUGH_EVIDENCE',
        'stage10_adaptive_model_selection_authorized': False,
        'stage10_engineering_design_reviewable': True,
        'read_only': True, 'files_written': False, 'network_accessed': False,
        'broker_connected': False, 'collector_started': False,
        'council_invoked': False, 'model_loaded': False,
        'training_authorized': False, 'settlement_invoked': False,
        'paper_orders_authorized': False, 'live_orders_authorized': False,
        'operator_approval_granted': False, 'historical_epoch_rebound': False,
        'future_epoch_created': False,
        'independent_external_gates_verified': 0,
        'independent_source_families_verified': 0,
        'research_operational_readiness': 'BLOCKED_EXTERNAL_EVIDENCE',
        'blockers': sorted(blockers), 'final_action': 'HOLD',
    }
    report['report_sha256'] = _digest(report)
    return report


def run(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--read-only-registry', required=True, action='store_true')
    p.add_argument('--synthetic-only', required=True, action='store_true')
    p.add_argument('--epoch-id', required=True)
    p.add_argument('--as-of', required=True)
    p.add_argument('--policy-file', default=POLICY_PATH)
    p.add_argument('--contracts-file', default='backend/agents/contracts.py')
    p.add_argument('--manifest-file', default='backend/agents/council_manifest.py')
    p.add_argument('--history-file')
    p.add_argument('--prior-history')
    p.add_argument('--pattern-file')
    p.add_argument('--prior-pattern')
    p.add_argument('--scenario-file')
    p.add_argument('--prior-scenario')
    p.add_argument('--registry-file')
    p.add_argument('--prior-registry')
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    try:
        report = assess_stage10_model_registry(
            epoch_id=a.epoch_id, as_of=a.as_of, policy_file=a.policy_file,
            history_file=a.history_file, prior_history=a.prior_history,
            pattern_file=a.pattern_file, prior_pattern=a.prior_pattern,
            scenario_file=a.scenario_file, prior_scenario=a.prior_scenario,
            registry_file=a.registry_file, prior_registry=a.prior_registry,
            contracts_file=a.contracts_file, manifest_file=a.manifest_file,
        )
    except (ModelRegistryError, ScenarioLabError, CouncilContextError,
            OfflineHeartbeatError, OSError, ValueError, TypeError,
            KeyError, OverflowError):
        print('STAGE 10 REGISTRY BLOCKED: Unsafe or self-authorizing model metadata.')
        return 1
    if a.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        print('EURUSD ORACLE | Stage 10 P10-17..20 model registry and promotion DESIGN')
        print('Historical clock policy: ORIGINAL_BYTES_MATCH_PIN')
        print('Registry: ' + report['registry_status'])
        print('Local candidate metadata: %d | Trained models: 0 | Promoted weights: 0' %
              report['candidate_metadata_count'])
        print('Local comparisons: %d | Independent holdouts: 0 | Promotions: 0' %
              report['comparison_count'])
        print('Stage9 exit: NOT_ENOUGH_EVIDENCE | Gates/families: 0/11 | 0/13')
        for code in report['blockers']:
            print('  BLOCKER ' + code)
        print('SHA-256: ' + report['report_sha256'])
        print('READ-ONLY | NO TRAINING | NO MODEL LOADING | NO BROKER | FINAL=HOLD')
    return 0


def main():
    raise SystemExit(run())


if __name__ == '__main__':
    main()

"""P10-09..12: bounded read-only causal *synthetic* historical memory.

Revalidates the immutable Stage 10 seat-history packet and independent research
HOLD boundary before examining any synthetic history. Historical cases may be
retrieved only after their horizon + explicit embargo has passed; independently
unattested outcomes remain labeled as local, never forward evidence.

No model/vector database, web, agent dispatch, predictor, training, disk write,
source admission or brokerage. A self-reported as-of time is not a witness.
FINAL=HOLD regardless of sample counts or apparent similarity.
"""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re

from backend.runtime.stage8_m5_offline_heartbeat import (
    EPOCH, POLICY_PATH, POLICY_SHA256, OfflineHeartbeatError,
    _read, _decode, _digest, _ident,
)
from backend.runtime.stage9_predeclared_experiment_protocol import (
    ExperimentProtocolError, _time,
)
from backend.runtime.stage10_council_reliability_diversity_design import (
    Stage10DesignError, assess_stage10_council_reliability,
)

VERSION = 'EURUSD_STAGE10_CAUSAL_PATTERN_MEMORY_P10_09_12_V1'
PATTERN_VERSION = 'TRANSPARENT_ASOF_SYNTHETIC_PATTERN_VECTOR_V1'
ABLATION_VERSION = 'UNSCORED_COMPLETE_CASE_PATTERN_ABLATION_V1'
MAX_BYTES = 262144
MAX_CASES = 256
MAX_NEIGHBORS = 20
UTC = timezone.utc
Q = Decimal('0.0001')
HEX = re.compile(r'^[0-9a-f]{64}$')
DECIMAL = re.compile(r'^(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,4})?$')
REGIMES = ('TREND', 'RANGE', 'HIGH_VOLATILITY', 'LIQUIDITY_STRESS', 'UNKNOWN')
EVENT_RISKS = ('NONE_KNOWN', 'PREDECLARED_EVENT', 'SOURCE_UNAVAILABLE')
ACTIONS = ('BUY', 'SELL', 'HOLD')
PACKET_FIELDS = {
    'schema_version', 'historical_epoch_id', 'packet_id', 'mode',
    'contract_version', 'seat_history_file_sha256', 'seat_reliability_report_sha256',
    'declared_at', 'feature_schema', 'max_neighbors', 'minimum_band_samples',
    'embargo_seconds', 'rows', 'ablation_plan',
}
FEATURE_FIELDS = {
    'decision_key', 'decision_at', 'event_id', 'horizon_seconds',
    'source_snapshot_sha256', 'source_family_contract_sha256',
    'trend_bps', 'structure', 'volatility_pips', 'spread_pips',
    'session', 'regime', 'event_risk', 'feature_first_available_at',
    'macro_vintage_sha256', 'macro_first_available_at', 'feature_claim_sha256',
}
ABLATION_FIELDS = {'contract_version', 'frozen_at', 'window_role', 'metric', 'actions'}
ACTION_FIELDS = {'decision_key', 'without_memory', 'with_memory'}


class PatternMemoryError(ValueError):
    """Untrusted packet, chronology, feature lineage or ablation violation."""


def _strict(obj, keys, label):
    if not isinstance(obj, dict) or set(obj) != keys:
        raise PatternMemoryError('Strict schema mismatch: ' + label)


def _sha(value, label):
    if not isinstance(value, str) or HEX.fullmatch(value) is None:
        raise PatternMemoryError('SHA-256 digest required: ' + label)
    return value


def _id(value, label):
    try:
        return _ident(value, label)
    except (OfflineHeartbeatError, ValueError, TypeError):
        raise PatternMemoryError('Unsafe case or feature identifier: ' + label) from None


def _clock(value):
    try:
        return _time(value)
    except (ExperimentProtocolError, TypeError, ValueError):
        raise PatternMemoryError('Canonical Z UTC required.') from None


def _file(path, limit):
    try:
        data = _read(path, limit)
        return _decode(data), sha256(data).hexdigest()
    except (OfflineHeartbeatError, ValueError, OSError, TypeError, UnicodeError):
        raise PatternMemoryError('Unsafe or oversized local pattern input.') from None


def _decimal(value, label, upper, *, strictly_positive=False):
    if not isinstance(value, str) or DECIMAL.fullmatch(value) is None:
        raise PatternMemoryError('Bounded decimal text required: ' + label)
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise PatternMemoryError('Nonfinite decimal refused: ' + label) from None
    if not number.is_finite() or number > Decimal(str(upper)) or (strictly_positive and number <= 0):
        raise PatternMemoryError('Decimal exceeds safe bound: ' + label)
    return number


def _feature(item, case, declared):
    _strict(item, FEATURE_FIELDS, 'asof_pattern_feature')
    bar = _clock(case['bar_close_at'])
    decision = _clock(case['decision_at'])
    if (item['decision_key'] != case['decision_key']
            or item['decision_at'] != case['decision_at']
            or item['event_id'] != case['event_id']
            or type(item['horizon_seconds']) is not int
            or item['horizon_seconds'] != case['horizon_seconds']
            or item['source_snapshot_sha256'] != case['source_snapshot_sha256']
            or item['session'] != case['session']
            or item['regime'] != case['regime']):
        raise PatternMemoryError('Pattern feature not bound to original M5 case.')
    _sha(item['source_family_contract_sha256'], 'source_family_contract_sha256')
    first = _clock(item['feature_first_available_at'])
    if not bar <= first <= decision or declared > bar:
        raise PatternMemoryError('Pattern feature declared or available after the decision.')
    if type(item['trend_bps']) is not int or not -500 <= item['trend_bps'] <= 500:
        raise PatternMemoryError('Bounded signed trend required.')
    if type(item['structure']) is not int or item['structure'] not in (-2, -1, 0, 1, 2):
        raise PatternMemoryError('Structure must use five fixed categories.')
    _decimal(item['volatility_pips'], 'volatility_pips', 200, strictly_positive=True)
    _decimal(item['spread_pips'], 'spread_pips', 50)
    if item['event_risk'] not in EVENT_RISKS:
        raise PatternMemoryError('Unknown event or lookahead label.')
    macro, available = item['macro_vintage_sha256'], item['macro_first_available_at']
    if (macro is None) != (available is None):
        raise PatternMemoryError('Macro vintage and availability must be paired.')
    if macro is not None:
        _sha(macro, 'macro_vintage_sha256')
        if _clock(available) > first:
            raise PatternMemoryError('Postdecision macro vintage cannot enter features.')
    expected = _digest({k: v for k, v in item.items() if k != 'feature_claim_sha256'})
    if item['feature_claim_sha256'] != expected:
        raise PatternMemoryError('Local feature content changed after digest claim.')
    return {**item, '_decision': decision, '_bar': bar}


def _distance(a, b):
    """Fixed explicit metric: not fitted and not a learned embedding."""
    numeric = (
        Decimal(abs(a['trend_bps'] - b['trend_bps'])) / 100
        + Decimal(abs(a['structure'] - b['structure'])) / 2
        + abs(Decimal(a['volatility_pips']) - Decimal(b['volatility_pips'])) / 20
        + abs(Decimal(a['spread_pips']) - Decimal(b['spread_pips'])) / 5
    )
    categorical = sum((
        a['session'] != b['session'],
        a['regime'] != b['regime'],
        a['event_risk'] != b['event_risk'],
    ))
    macro_penalty = (Decimal('0.5000') if a['macro_vintage_sha256'] != b['macro_vintage_sha256']
                     else Decimal('0'))
    return (numeric + Decimal(categorical) + macro_penalty).quantize(Q)


def _wilson(buys, n):
    """Only a descriptive IID illustration; never a dependence-adjusted CI."""
    if n == 0:
        return None
    z = Decimal('1.9600')
    zn = z * z
    count = Decimal(n)
    p = Decimal(buys) / count
    denominator = Decimal(1) + zn / count
    center = (p + zn / (Decimal(2) * count)) / denominator
    margin = z * ((p * (Decimal(1)-p) / count + zn / (Decimal(4)*count*count)).sqrt()) / denominator
    return [format(max(Decimal(0), center-margin).quantize(Q), 'f'),
            format(min(Decimal(1), center+margin).quantize(Q), 'f')]


def _search(features, cases, index, *, k, min_band, embargo):
    current = features[index]
    original = cases[index]
    eligible = []
    for older_feature, older in zip(features[:index], cases[:index]):
        if (older_feature['horizon_seconds'] != current['horizon_seconds']
                or older_feature['source_family_contract_sha256'] != current['source_family_contract_sha256']
                or _clock(older['target_at']) + timedelta(seconds=embargo) > current['_decision']
                or older['event_id'] == current['event_id']):
            continue
        available = older['outcome_available_at']
        if available is not None and _clock(available) > current['_decision']:
            continue
        eligible.append((_distance(current, older_feature), older_feature, older))
    eligible.sort(key=lambda x: (x[0], x[1]['_decision'], x[1]['decision_key']))
    neighbors = []
    seen_events = set()
    duplicates = 0
    for distance, feature, case in eligible:
        if case['event_id'] in seen_events:
            duplicates += 1
            continue
        seen_events.add(case['event_id'])
        if len(neighbors) < k:
            neighbors.append({
                'historical_decision_key': case['decision_key'],
                'historical_case_sha256': case['case_claim_sha256'],
                'local_feature_sha256': feature['feature_claim_sha256'],
                'local_similarity_distance': format(distance, 'f'),
                'local_historical_outcome': case['outcome_direction'],
                'outcome_status': case['outcome_status'],
                'session_mismatch': current['session'] != feature['session'],
                'regime_mismatch': current['regime'] != feature['regime'],
                'event_risk_mismatch': current['event_risk'] != feature['event_risk'],
                'externally_attested': False,
            })
    buys = sum(x['local_historical_outcome'] == 'BUY' for x in neighbors)
    sells = sum(x['local_historical_outcome'] == 'SELL' for x in neighbors)
    ties = sum(x['local_historical_outcome'] == 'TIE' for x in neighbors)
    missing = sum(x['local_historical_outcome'] is None for x in neighbors)
    observed = buys + sells
    unique_days = len({ _clock(c['decision_at']).date().isoformat() for c in cases[:index]
                        if any(x['historical_decision_key'] == c['decision_key'] for x in neighbors)
                        and c['outcome_direction'] in ('BUY', 'SELL')})
    has_band = observed >= min_band and unique_days >= min_band
    return {
        'decision_key': original['decision_key'],
        'bar_close_at': original['bar_close_at'],
        'horizon_seconds': original['horizon_seconds'],
        'source_family_contract_sha256': current['source_family_contract_sha256'],
        'local_feature_sha256': current['feature_claim_sha256'],
        'local_candidate_rows_before_event_dedup': len(eligible),
        'duplicate_event_candidates_suppressed': duplicates,
        'retrieved_neighbors': neighbors,
        'neighbors_returned': len(neighbors),
        'locally_labeled_buy': buys,
        'locally_labeled_sell': sells,
        'locally_labeled_tie': ties,
        'missing_historical_outcomes': missing,
        'distinct_directional_utc_days': unique_days,
        'local_buy_fraction_descriptive': (
            format((Decimal(buys)/Decimal(observed)).quantize(Q), 'f') if observed else None),
        'illustrative_iid_wilson_band': _wilson(buys, observed) if has_band else None,
        'independent_or_dependence_corrected_interval': None,
        'deterministic_directional_forecast': None,
        'independent_retrieval_value_established': False,
    }


def _ablation(plan, cases, matches, declared):
    _strict(plan, ABLATION_FIELDS, 'uns cored_pattern_ablation'.replace(' ', ''))
    if (plan['contract_version'] != ABLATION_VERSION
            or plan['window_role'] != 'SYNTHETIC_REHEARSAL_NOT_UNSEEN_TEST'
            or plan['metric'] != 'COMPLETE_CASE_ACTION_DISAGREEMENT_NO_OUTCOME_SCORING'):
        raise PatternMemoryError('Ablation cannot claim future evaluation, model training or edge.')
    frozen = _clock(plan['frozen_at'])
    if cases and (frozen > _clock(cases[0]['bar_close_at']) or frozen < declared):
        raise PatternMemoryError('Ablation frozen after case decisions or before local declaration.')
    actions = plan['actions']
    if not isinstance(actions, list) or len(actions) != len(cases):
        raise PatternMemoryError('Ablation must include every M5 history case.')
    changed = 0
    both_hold = 0
    with_hold = 0
    for source, result, action in zip(cases, matches, actions):
        _strict(action, ACTION_FIELDS, 'synthetic_ablation_case')
        if action['decision_key'] != source['decision_key'] or any(
            action[key] not in ACTIONS for key in ('without_memory', 'with_memory')
        ):
            raise PatternMemoryError('Ablation actions must match ordered case universe.')
        if (result['neighbors_returned'] == 0
                and action['without_memory'] != action['with_memory']):
            raise PatternMemoryError('No memory neighbor can alter Council action.')
        changed += int(action['without_memory'] != action['with_memory'])
        both_hold += int(action['without_memory'] == 'HOLD' and action['with_memory'] == 'HOLD')
        with_hold += int(action['with_memory'] == 'HOLD')
    return {
        'status': 'LOCAL_UNATTESTED_DESIGN_ONLY',
        'full_case_denominator': len(cases),
        'local_action_disagreements': changed,
        'both_hold_cases': both_hold,
        'with_memory_hold_cases': with_hold,
        'frozen_claim_is_independent_registration': False,
        'independently_evaluated_on_unseen_data': False,
        'calibration_improvement_established': False,
        'retrieval_promoted_to_council': False,
    }


def assess_stage10_pattern_memory(*, epoch_id, as_of, policy_file=POLICY_PATH,
                                   history_file=None, prior_history=None,
                                   pattern_file=None, prior_pattern=None,
                                   contracts_file='backend/agents/contracts.py',
                                   manifest_file='backend/agents/council_manifest.py'):
    if epoch_id != EPOCH:
        raise PatternMemoryError('Historical epoch rebinding refused.')
    cutoff = _clock(as_of)
    if cutoff > datetime.now(UTC):
        raise PatternMemoryError('Future as-of review refused.')
    if pattern_file is not None and history_file is None:
        raise PatternMemoryError('Pattern packet requires original seat history.')
    if prior_pattern is not None and pattern_file is None:
        raise PatternMemoryError('Previous pattern packet requires current packet.')
    if prior_history is not None and history_file is None:
        raise PatternMemoryError('Previous seat history requires original history.')
    try:
        pinned = sha256(_read(policy_file, 16384)).hexdigest()
        if pinned != POLICY_SHA256:
            raise PatternMemoryError('Original historical clock policy SHA-256 mismatch.')
        reliability = assess_stage10_council_reliability(
            epoch_id=EPOCH, as_of=as_of, policy_file=policy_file,
            history_file=history_file, prior_history=prior_history,
            contracts_file=contracts_file, manifest_file=manifest_file,
        )
    except (OfflineHeartbeatError, Stage10DesignError, OSError, ValueError, TypeError, KeyError):
        raise PatternMemoryError('Original Stage 10 reliability boundary failed replay.') from None
    cases, rows, packet_sha, prior_sha = [], [], None, None
    if pattern_file is not None:
        history, history_sha = _file(history_file, 8388608)
        case_records = history['cases']
        packet, packet_sha = _file(pattern_file, MAX_BYTES)
        _strict(packet, PACKET_FIELDS, 'synthetic_pattern_packet')
        _id(packet['packet_id'], 'pattern_packet_id')
        if (type(packet['schema_version']) is not int or packet['schema_version'] != 1
                or packet['historical_epoch_id'] != EPOCH
                or packet['mode'] != 'SYNTHETIC_LOCAL_PATTERN_MEMORY_ONLY'
                or packet['contract_version'] != PATTERN_VERSION
                or packet['feature_schema'] != 'FIXED_TRANSPARENT_EURUSD_M5_V1'
                or packet['seat_history_file_sha256'] != history_sha
                or packet['seat_reliability_report_sha256'] != reliability['report_sha256']):
            raise PatternMemoryError('Pattern packet does not bind original history or stage9 gates.')
        declared = _clock(packet['declared_at'])
        if declared > cutoff or (case_records and declared > _clock(case_records[0]['bar_close_at'])):
            raise PatternMemoryError('Pattern candidate not locally frozen before case history.')
        k = packet['max_neighbors']
        min_band = packet['minimum_band_samples']
        embargo = packet['embargo_seconds']
        if (type(k) is not int or not 1 <= k <= MAX_NEIGHBORS
                or type(min_band) is not int or not 5 <= min_band <= k
                or type(embargo) is not int or embargo != history['embargo_seconds']
                or len(case_records) > MAX_CASES):
            raise PatternMemoryError('Changed neighbor, embargo or minimum-sample policy.')
        supplied = packet['rows']
        if not isinstance(supplied, list) or len(supplied) != len(case_records):
            raise PatternMemoryError('Complete case-aligned feature universe required.')
        cases = case_records
        features = [_feature(item, case, declared) for item, case in zip(supplied, cases)]
        rows = [_search(features, cases, idx, k=k, min_band=min_band, embargo=embargo)
                for idx in range(len(cases))]
        ablation = _ablation(packet['ablation_plan'], cases, rows, declared)
        if prior_pattern is not None:
            if Path(pattern_file).absolute() == Path(prior_pattern).absolute():
                raise PatternMemoryError('Distinct local prior pattern snapshot required.')
            _, prior_sha = _file(prior_pattern, MAX_BYTES)
            if prior_sha != packet_sha:
                raise PatternMemoryError('Local pattern snapshot changed after freeze.')
    else:
        ablation = {
            'status': 'NOT_SUPPLIED', 'full_case_denominator': 0,
            'local_action_disagreements': 0, 'both_hold_cases': 0,
            'with_memory_hold_cases': 0, 'frozen_claim_is_independent_registration': False,
            'independently_evaluated_on_unseen_data': False,
            'calibration_improvement_established': False, 'retrieval_promoted_to_council': False,
        }
    blockers = [
        'STAGE9_PROSPECTIVE_INDEPENDENT_RESEARCH_EXIT_NOT_MET',
        'NO_INDEPENDENT_HISTORICAL_CASE_OR_FEATURE_VINTAGE_RECEIPTS',
        'NO_UNSEEN_PATTERNS_ABLATION_OR_CALIBRATION_EVIDENCE',
        'NO_AUTHORIZED_PATTERN_MEMORY_COUNCIL_INTEGRATION',
        'NO_INDEPENDENT_SOURCE_FAMILY_ADMISSIONS',
    ]
    if pattern_file is None:
        blockers.append('NO_LOCAL_SYNTHETIC_PATTERN_PACKET_SUPPLIED')
    report = {
        'version': VERSION,
        'scope': 'P10_09_TO_P10_12_CAUSAL_PATTERN_MEMORY_DESIGN',
        'historical_epoch_id': EPOCH, 'as_of': cutoff.isoformat(),
        'historical_policy_sha256': POLICY_SHA256,
        'stage10_reliability_report_sha256': reliability['report_sha256'],
        'stage9_research_decision': reliability['stage9_research_decision'],
        'pattern_status': 'NOT_SUPPLIED' if packet_sha is None else 'LOCAL_SYNTHETIC_UNATTESTED',
        'history_file_sha256': reliability['history_file_sha256'],
        'pattern_file_sha256': packet_sha,
        'prior_pattern_file_sha256': prior_sha,
        'feature_contract_version': PATTERN_VERSION,
        'cases_with_asof_features': len(cases),
        'neighbors_retrieved_total': sum(x['neighbors_returned'] for x in rows),
        'pattern_cases': rows,
        'ablation_preview': ablation,
        'historical_case_source_receipts_independently_verified': 0,
        'trusted_retrieval_labels': 0,
        'model_retrieval_or_pattern_learning_performed': False,
        'council_pattern_seat_dispatched': False,
        'real_predictions_registered': 0,
        'independent_edge_or_ablation_improvement_established': False,
        'read_only': True, 'files_written': False, 'network_accessed': False,
        'broker_connected': False, 'collector_started': False, 'council_invoked': False,
        'model_loaded': False, 'training_authorized': False,
        'settlement_invoked': False, 'paper_orders_authorized': False,
        'live_orders_authorized': False, 'operator_approval_granted': False,
        'historical_epoch_rebound': False, 'future_epoch_created': False,
        'independent_external_gates_verified': 0, 'independent_source_families_verified': 0,
        'research_operational_readiness': 'BLOCKED_EXTERNAL_EVIDENCE',
        'blockers': sorted(blockers), 'final_action': 'HOLD',
    }
    report['report_sha256'] = _digest(report)
    return report


def run(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--read-only-pattern', action='store_true', required=True)
    p.add_argument('--synthetic-only', action='store_true', required=True)
    p.add_argument('--epoch-id', required=True)
    p.add_argument('--as-of', required=True)
    p.add_argument('--policy-file', default=POLICY_PATH)
    p.add_argument('--contracts-file', default='backend/agents/contracts.py')
    p.add_argument('--manifest-file', default='backend/agents/council_manifest.py')
    p.add_argument('--history-file')
    p.add_argument('--prior-history')
    p.add_argument('--pattern-file')
    p.add_argument('--prior-pattern')
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    try:
        report = assess_stage10_pattern_memory(
            epoch_id=a.epoch_id, as_of=a.as_of, policy_file=a.policy_file,
            history_file=a.history_file, prior_history=a.prior_history,
            pattern_file=a.pattern_file, prior_pattern=a.prior_pattern,
            contracts_file=a.contracts_file, manifest_file=a.manifest_file,
        )
    except (PatternMemoryError, OfflineHeartbeatError, OSError, ValueError,
            TypeError, KeyError, OverflowError):
        print('STAGE 10 PATTERN BLOCKED: Unsafe, changed or noncausal offline evidence.')
        return 1
    if a.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    else:
        print('EURUSD ORACLE | Stage 10 P10-09..12 causal historical pattern memory')
        print('Historical clock policy: ORIGINAL_BYTES_MATCH_PIN')
        print('Pattern: ' + report['pattern_status'])
        print('Cases: %d | Locally retrieved analogues: %d | Independent labels: 0' % (
            report['cases_with_asof_features'], report['neighbors_retrieved_total']))
        print('Ablation improvement established: NO | Real predictions: 0')
        print('Independent gates/families: 0/11 | 0/13')
        for reason in report['blockers']:
            print('  BLOCKER ' + reason)
        print('SHA-256: ' + report['report_sha256'])
        print('READ-ONLY | UNATTESTED PATTERNS | NO AGENTS | NO BROKER | FINAL=HOLD')
    return 0


def main():
    raise SystemExit(run())


if __name__ == '__main__':
    main()

"""P10-13..16: deterministic, entirely local EURUSD scenario laboratory.

Input is a synthetic scenario packet bound to the validated P10 pattern report.
Simulated paths, gap shocks and stress prices are not forecasts, calibrated
probabilities, real quotes, trading costs or authorizations. The only supported
'MiroFish' option is a disabled, unvalidated adapter declaration. No imports
or network calls to that external system are made.

Uses fixed SHA-256 counter draws and Decimal arithmetic for replay stability.
Reads existing files only; no models, agents, collectors or broker. FINAL=HOLD.
"""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
from pathlib import Path

from backend.runtime.stage8_m5_offline_heartbeat import (
    EPOCH, POLICY_PATH, POLICY_SHA256, OfflineHeartbeatError, _read, _decode,
    _digest, _ident,
)
from backend.runtime.stage10_causal_pattern_memory import (
    PatternMemoryError, _clock as _pattern_clock, _decimal as _pattern_decimal,
    _file as _pattern_file, _sha as _pattern_sha,
    assess_stage10_pattern_memory,
)

VERSION = 'EURUSD_STAGE10_OFFLINE_SCENARIO_LAB_P10_13_16_V1'
SCENARIO_VERSION = 'SEEDED_SYNTHETIC_REGIME_GAP_AND_SHOCK_LAB_V1'
ADAPTER_VERSION = 'MIROFISH_OPTIONAL_UNVALIDATED_DISABLED_V1'
SHOCKS = (
    'SPREAD_WIDENING', 'POLICY_SURPRISE_UP', 'POLICY_SURPRISE_DOWN',
    'VOLATILITY_BREAK', 'LIQUIDITY_DROUGHT',
    'CORRELATED_ASSET_DIVERGENCE', 'SOURCE_OUTAGE',
)
REGIME_SCALES = {
    'TREND': Decimal('1.0000'), 'RANGE': Decimal('0.7000'),
    'HIGH_VOLATILITY': Decimal('2.0000'), 'LIQUIDITY_STRESS': Decimal('2.5000'),
    'UNKNOWN': Decimal('1.0000'),
}
PIP = Decimal('10000')
ONE = Decimal('1')
TWO = Decimal('2')
PRICE_Q = Decimal('0.000001')
PIPS_Q = Decimal('0.0001')
MAX_BYTES = 131072
MAX_PATHS = 64
MAX_STEPS = 96
MAX_EVENTS = 12
PRICE_TEXT = re.compile(r'^(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,6})?$')
SCENARIO_FIELDS = {
    'schema_version', 'historical_epoch_id', 'packet_id', 'mode',
    'contract_version', 'design_purpose', 'pattern_report_sha256',
    'pattern_packet_sha256', 'declared_at', 'origin_decision_key',
    'origin_feature_sha256', 'origin_source_contract_sha256',
    'start_at', 'start_bid', 'start_ask', 'quote_first_available_at',
    'source_class', 'regime', 'selected_vintage_sha256',
    'selected_vintage_first_available_at', 'seed', 'paths', 'steps',
    'step_seconds', 'horizon_seconds', 'drift_pips_per_step',
    'volatility_pips_per_step', 'gap_probability_basis_points',
    'gap_size_pips', 'events', 'shocks', 'mirofish_adapter',
    'final_test_opened',
}
EVENT_FIELDS = {'event_id', 'known_at', 'scheduled_for', 'severity'}
SHOCK_FIELDS = {'shock_id', 'at_step', 'strength', 'assumption_class'}
ADAPTER_FIELDS = {
    'adapter_name', 'adapter_version_contract', 'integration_status',
    'external_adapter_version', 'external_artifact_sha256', 'network_enabled',
    'security_reviewed', 'reproducibility_verified', 'benchmarked_against_baselines',
}
UTC = timezone.utc


class ScenarioLabError(ValueError):
    """Unbound, future-looking, non-deterministic or authorizing scenario."""


def _clock(value):
    try:
        return _pattern_clock(value)
    except PatternMemoryError:
        raise ScenarioLabError('Canonical Z UTC timestamp required.') from None


def _sha(value, label):
    try:
        return _pattern_sha(value, label)
    except PatternMemoryError:
        raise ScenarioLabError('Invalid original scenario SHA-256: ' + label) from None


def _decimal(value, label, upper, *, strictly_positive=False):
    try:
        return _pattern_decimal(value, label, upper, strictly_positive=strictly_positive)
    except PatternMemoryError:
        raise ScenarioLabError('Invalid bounded scenario decimal: ' + label) from None


def _strict(value, keys, label):
    if not isinstance(value, dict) or set(value) != keys:
        raise ScenarioLabError('Strict schema mismatch: ' + label)


def _id(value, label):
    try:
        return _ident(value, label)
    except (OfflineHeartbeatError, ValueError, TypeError):
        raise ScenarioLabError('Invalid identifier: ' + label) from None


def _file(path):
    try:
        raw = _read(path, MAX_BYTES)
        return _decode(raw), sha256(raw).hexdigest()
    except (OfflineHeartbeatError, OSError, UnicodeError, TypeError, ValueError):
        raise ScenarioLabError('Unsafe, oversized or malformed local scenario.') from None


def _string(number, places=PRICE_Q):
    return format(number.quantize(places), 'f')


def _price(value, label):
    if not isinstance(value, str) or PRICE_TEXT.fullmatch(value) is None:
        raise ScenarioLabError('Bounded six-decimal synthetic quote required: ' + label)
    number = Decimal(value)
    if not number.is_finite() or not Decimal('0.500000') <= number <= Decimal('2.500000'):
        raise ScenarioLabError('Synthetic quote outside permitted bounds: ' + label)
    return number


def _uniform(seed, path, step, draw):
    """Counter-based deterministic discrete uniform; no host PRNG state."""
    blob = ('EURUSD_STAGE10_SIM_V1|%d|%d|%d|%d' % (seed, path, step, draw)).encode('ascii')
    number = int.from_bytes(sha256(blob).digest()[:4], 'big')
    return Decimal(number) / Decimal(4294967296)


def _gaussian_like(seed, path, step):
    # Standardized Irwin-Hall(12), an illustrative bounded normal-like law.
    # This is NOT a fitted market-return distribution or a risk probability.
    return sum((_uniform(seed, path, step, draw) for draw in range(12)), Decimal(0)) - Decimal(6)


def _validate(doc, pattern, history, cutoff):
    _strict(doc, SCENARIO_FIELDS, 'read_only_synthetic_scenario')
    _id(doc['packet_id'], 'scenario_packet_id')
    if (type(doc['schema_version']) is not int or doc['schema_version'] != 1
            or doc['historical_epoch_id'] != EPOCH
            or doc['mode'] != 'SYNTHETIC_OFFLINE_SCENARIO_DESIGN_ONLY'
            or doc['contract_version'] != SCENARIO_VERSION
            or doc['design_purpose'] != 'REPRODUCIBLE_STRESS_REHEARSAL_NOT_A_FORECAST'
            or doc['pattern_report_sha256'] != pattern['report_sha256']
            or doc['pattern_packet_sha256'] != pattern['pattern_file_sha256']
            or doc['source_class'] != 'LOCAL_SYNTHETIC_UNVERIFIED'
            or doc['final_test_opened'] is not False):
        raise ScenarioLabError('Scenario not bound to the original causal research boundary.')
    if not pattern['pattern_cases'] or not history['cases']:
        raise ScenarioLabError('Scenario requires a real local synthetic origin case.')
    source = history['cases'][-1]
    feature = pattern['pattern_cases'][-1]
    start = _clock(doc['start_at'])
    declared = _clock(doc['declared_at'])
    quote_known = _clock(doc['quote_first_available_at'])
    vintage_known = _clock(doc['selected_vintage_first_available_at'])
    _sha(doc['selected_vintage_sha256'], 'local_synthetic_vintage_sha256')
    if (doc['origin_decision_key'] != source['decision_key']
            or doc['origin_decision_key'] != feature['decision_key']
            or doc['origin_feature_sha256'] != feature['local_feature_sha256']
            or doc['origin_source_contract_sha256'] != feature['source_family_contract_sha256']
            or doc['regime'] != source['regime']
            or start != _clock(source['decision_at'])
            or not _clock(source['bar_close_at']) <= quote_known <= start
            or vintage_known > start or declared > start or declared > cutoff):
        raise ScenarioLabError('Scenario origin, declared assumptions or vintage are noncausal.')
    bid = _price(doc['start_bid'], 'start_bid')
    ask = _price(doc['start_ask'], 'start_ask')
    if ask < bid or (ask - bid) * PIP > Decimal('30'):
        raise ScenarioLabError('Synthetic starting quote is crossed or illiquid.')
    steps, paths, seconds, horizon, seed = (
        doc['steps'], doc['paths'], doc['step_seconds'], doc['horizon_seconds'], doc['seed']
    )
    if (type(steps) is not int or not 1 <= steps <= MAX_STEPS
            or type(paths) is not int or not 1 <= paths <= MAX_PATHS
            or type(seconds) is not int or seconds != 300
            or type(horizon) is not int or horizon != steps * seconds
            or type(seed) is not int or not 0 <= seed <= 4294967295):
        raise ScenarioLabError('Seed, M5 horizon or bounded path count is invalid.')
    # Signed drift is deliberately bounded separately from unsigned costs.
    drift = doc['drift_pips_per_step']
    if not isinstance(drift, str) or not drift.startswith(('+', '-')):
        raise ScenarioLabError('Signed explicit drift assumption required.')
    drift_mag = _decimal(drift[1:], 'drift_pips_per_step', 10)
    drift_value = drift_mag if drift[0] == '+' else -drift_mag
    sigma = _decimal(doc['volatility_pips_per_step'], 'volatility_pips_per_step', 15,
                     strictly_positive=True)
    gap = _decimal(doc['gap_size_pips'], 'gap_size_pips', 50)
    chance = doc['gap_probability_basis_points']
    if type(chance) is not int or not 0 <= chance <= 1000:
        raise ScenarioLabError('Bounded integer gap basis-point chance required.')
    events = doc['events']
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ScenarioLabError('Event-calendar count exceeds safe bound.')
    event_ids = set()
    for item in events:
        _strict(item, EVENT_FIELDS, 'preknown_synthetic_event')
        event_id = _id(item['event_id'], 'scenario_event_id')
        if event_id in event_ids:
            raise ScenarioLabError('Duplicate synthetic event-calendar identity.')
        event_ids.add(event_id)
        known = _clock(item['known_at'])
        scheduled = _clock(item['scheduled_for'])
        if (known > start or not start < scheduled <= start + timedelta(seconds=horizon)
                or item['severity'] not in ('LOW', 'MEDIUM', 'HIGH')):
            raise ScenarioLabError('Event calendar was not known at starting decision.')
    shocks = doc['shocks']
    if not isinstance(shocks, list) or len(shocks) > len(SHOCKS):
        raise ScenarioLabError('Bounded shock library required.')
    by_shock = {}
    for shock in shocks:
        _strict(shock, SHOCK_FIELDS, 'versioned_shock_assumption')
        sid = shock['shock_id']
        st = shock['at_step']
        level = shock['strength']
        if (sid not in SHOCKS or sid in by_shock
                or type(st) is not int or not 1 <= st <= steps
                or type(level) is not int or not 1 <= level <= 5
                or shock['assumption_class'] != 'SYNTHETIC_CONDITIONAL_NOT_OBSERVED'):
            raise ScenarioLabError('Unknown, duplicate, out-of-bounds or self-attested shock.')
        by_shock[sid] = {'step': st, 'strength': level}
    adapter = doc['mirofish_adapter']
    _strict(adapter, ADAPTER_FIELDS, 'disabled_optional_mirofish_adapter')
    if (adapter != {
            'adapter_name': 'MIROFISH', 'adapter_version_contract': ADAPTER_VERSION,
            'integration_status': 'NOT_INTEGRATED_PENDING_VALIDATION',
            'external_adapter_version': None, 'external_artifact_sha256': None,
            'network_enabled': False, 'security_reviewed': False,
            'reproducibility_verified': False, 'benchmarked_against_baselines': False,
    }):
        raise ScenarioLabError('External MiroFish adapter cannot self-authorize integration.')
    return {
        'seed': seed, 'start': start, 'steps': steps, 'seconds': seconds, 'paths': paths,
        'initial_mid': (bid + ask) / TWO, 'initial_spread_pips': (ask - bid) * PIP,
        'drift': drift_value, 'sigma': sigma * REGIME_SCALES[doc['regime']],
        'gap': gap, 'chance': chance, 'shocks': by_shock,
        'events': events,
    }


def _simulate(config):
    all_paths = []
    preview = []
    terminals = []
    max_spread = Decimal(0)
    gap_count = 0
    unavailable_steps = 0
    for path in range(config['paths']):
        mid = config['initial_mid']
        spread = config['initial_spread_pips']
        volatility = config['sigma']
        source_available = True
        records = []
        for step in range(1, config['steps'] + 1):
            # Draws are indexed by path/step, not by prior random calls; changing
            # a shock cannot silently rewrite the base random stream.
            innovation = _gaussian_like(config['seed'], path, step)
            move = config['drift'] + volatility * innovation
            if _uniform(config['seed'], path, step, 12) * Decimal(10000) < config['chance']:
                sign = ONE if _uniform(config['seed'], path, step, 13) >= Decimal('0.5') else -ONE
                move += sign * config['gap']
                gap_count += 1
            for shock_id, shock in sorted(config['shocks'].items()):
                if shock['step'] != step:
                    continue
                level = Decimal(shock['strength'])
                if shock_id == 'POLICY_SURPRISE_UP':
                    move += Decimal(3) * level
                elif shock_id == 'POLICY_SURPRISE_DOWN':
                    move -= Decimal(3) * level
                elif shock_id == 'SPREAD_WIDENING':
                    spread += Decimal(2) * level
                elif shock_id == 'VOLATILITY_BREAK':
                    volatility *= (ONE + level / TWO)
                elif shock_id == 'LIQUIDITY_DROUGHT':
                    spread += Decimal(4) * level
                    volatility *= Decimal('1.5')
                elif shock_id == 'CORRELATED_ASSET_DIVERGENCE':
                    # Simplified conditional proxy shock, not a cross-asset model.
                    move += level if path % 2 else -level
                elif shock_id == 'SOURCE_OUTAGE':
                    source_available = False
            mid += move / PIP
            if mid <= 0:
                raise ScenarioLabError('Synthetic stress price crossed zero; scenario invalid.')
            bid, ask = mid - spread / (TWO * PIP), mid + spread / (TWO * PIP)
            if bid <= 0:
                raise ScenarioLabError('Synthetic quote crossed zero under gap stress.')
            max_spread = max(max_spread, spread)
            unavailable_steps += int(not source_available)
            records.append({
                'step': step,
                'synthetic_at': (config['start'] + timedelta(seconds=step * config['seconds'])).isoformat(),
                'synthetic_mid': _string(mid),
                'synthetic_bid': _string(bid), 'synthetic_ask': _string(ask),
                'source_available_in_assumed_scenario': source_available,
            })
        all_paths.append(records)
        terminals.append(mid)
        if path < 3:
            preview.append({'path_index': path, 'steps': records, 'simulated_only': True})
    ordered = sorted(terminals)
    def quantile(numerator):
        return _string(ordered[((len(ordered)-1)*numerator)//10])
    reference = config['initial_mid'] + Decimal(config['steps']) * config['drift'] / PIP
    return {
        'distribution': 'BOUNDED_CLT12_REGIME_GAP_NOT_FITTED_TO_MARKET',
        'deterministic_rng': 'SHA256_COUNTER_UNIFORMS_V1',
        'simulated_path_manifest_sha256': _digest(all_paths),
        'simulated_path_count': len(all_paths),
        'simulated_steps_per_path': config['steps'],
        'sample_path_previews': preview,
        'terminal_synthetic_mid_p10': quantile(1),
        'terminal_synthetic_mid_p50': quantile(5),
        'terminal_synthetic_mid_p90': quantile(9),
        'max_assumed_spread_pips': _string(max_spread, PIPS_Q),
        'synthetic_gap_occurrences': gap_count,
        'source_unavailable_synthetic_steps': unavailable_steps,
        'simple_drift_only_terminal_mid': _string(reference),
        'benchmark_comparison_type': 'ASSUMPTION_ONLY_NOT_PREDICTIVE_ACCURACY',
        'quantile_frequencies_are_market_probabilities': False,
        'probabilistic_calibration_executed': False,
        'actual_quote_data_or_forecasts_used': False,
    }


def assess_stage10_scenario_lab(*, epoch_id, as_of, policy_file=POLICY_PATH,
                                 history_file=None, prior_history=None,
                                 pattern_file=None, prior_pattern=None,
                                 scenario_file=None, prior_scenario=None,
                                 contracts_file='backend/agents/contracts.py',
                                 manifest_file='backend/agents/council_manifest.py'):
    if epoch_id != EPOCH:
        raise ScenarioLabError('Historical epoch rebinding refused.')
    cutoff = _clock(as_of)
    if cutoff > datetime.now(UTC):
        raise ScenarioLabError('Future review cutoff refused.')
    if scenario_file is not None and (history_file is None or pattern_file is None):
        raise ScenarioLabError('Scenario needs complete local history and pattern chain.')
    if prior_scenario is not None and scenario_file is None:
        raise ScenarioLabError('Prior scenario alone cannot become active research evidence.')
    try:
        clock_sha = sha256(_read(policy_file, 16384)).hexdigest()
        if clock_sha != POLICY_SHA256:
            raise ScenarioLabError('Original clock policy SHA-256 mismatch.')
        pattern = assess_stage10_pattern_memory(
            epoch_id=EPOCH, as_of=as_of, policy_file=policy_file,
            history_file=history_file, prior_history=prior_history,
            pattern_file=pattern_file, prior_pattern=prior_pattern,
            contracts_file=contracts_file, manifest_file=manifest_file,
        )
    except (OfflineHeartbeatError, PatternMemoryError, OSError, ValueError, TypeError, KeyError):
        raise ScenarioLabError('Pattern-memory original read-only boundary failed replay.') from None
    result, sha, prior_sha, params = None, None, None, None
    if scenario_file is not None:
        history, _ = _pattern_file(history_file, 8388608)
        doc, sha = _file(scenario_file)
        params = _validate(doc, pattern, history, cutoff)
        result = _simulate(params)
        if prior_scenario is not None:
            if Path(scenario_file).absolute() == Path(prior_scenario).absolute():
                raise ScenarioLabError('Separate prior local scenario file required.')
            _, prior_sha = _file(prior_scenario)
            if prior_sha != sha:
                raise ScenarioLabError('Locally frozen scenario bytes were edited.')
    blockers = [
        'STAGE9_INDEPENDENT_RESEARCH_EXIT_CRITERIA_NOT_MET',
        'NO_INDEPENDENT_CALIBRATED_EURUSD_SIMULATION_DISTRIBUTION',
        'NO_REAL_QUOTE_BROKER_OR_OUTCOME_WITNESS_RECEIPTS',
        'NO_INDEPENDENT_SCENARIO_BENCHMARK_OR_UNSEEN_PROSPECTIVE_CHECK',
        'MIROFISH_ADAPTER_TECHNICAL_SECURITY_COST_VALIDATION_NOT_DONE',
        'NO_REAL_SCENARIO_TO_COUNCIL_OR_TRADING_AUTHORIZATION',
    ]
    if scenario_file is None:
        blockers.append('NO_LOCAL_SYNTHETIC_SCENARIO_PACKET_SUPPLIED')
    report = {
        'version': VERSION,
        'scope': 'P10_13_TO_P10_16_OFFLINE_REPRODUCIBLE_SCENARIO_LAB',
        'historical_epoch_id': EPOCH, 'as_of': cutoff.isoformat(),
        'historical_policy_sha256': clock_sha,
        'pattern_report_sha256': pattern['report_sha256'],
        'pattern_status': pattern['pattern_status'],
        'scenario_status': 'NOT_SUPPLIED' if sha is None else 'LOCAL_SYNTHETIC_UNATTESTED',
        'scenario_file_sha256': sha, 'prior_scenario_file_sha256': prior_sha,
        'scenario_contract_version': SCENARIO_VERSION,
        'seed': None if params is None else params['seed'],
        'hypothetical_scenarios_generated': 0 if result is None else result['simulated_path_count'],
        'synthetic_simulation': result,
        'shock_library_available': list(SHOCKS),
        'mirofish_integration_decision': {
            'status': 'NOT_INTEGRATED_PENDING_VALIDATION',
            'adapter_version_contract': ADAPTER_VERSION,
            'network_contacted': False,
            'artifact_or_security_review_completed': False,
            'reproducibility_or_benchmark_accepted': False,
            'external_engine_forecast_advantage_demonstrated': False,
        },
        'real_market_forecasts_issued': 0,
        'simulated_paths_count_as_actual_predictions': False,
        'independent_scenario_research_admission': False,
        'models_or_council_weights_modified': False,
        'read_only': True, 'files_written': False, 'network_accessed': False,
        'broker_connected': False, 'collector_started': False,
        'council_invoked': False, 'model_loaded': False,
        'training_authorized': False, 'settlement_invoked': False,
        'paper_orders_authorized': False, 'live_orders_authorized': False,
        'operator_approval_granted': False,
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
    parser.add_argument('--read-only-scenarios', action='store_true', required=True)
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
    parser.add_argument('--json', action='store_true')
    a = parser.parse_args(argv)
    try:
        result = assess_stage10_scenario_lab(
            epoch_id=a.epoch_id, as_of=a.as_of, policy_file=a.policy_file,
            history_file=a.history_file, prior_history=a.prior_history,
            pattern_file=a.pattern_file, prior_pattern=a.prior_pattern,
            scenario_file=a.scenario_file, prior_scenario=a.prior_scenario,
            contracts_file=a.contracts_file, manifest_file=a.manifest_file,
        )
    except (ScenarioLabError, PatternMemoryError, OfflineHeartbeatError, OSError,
            ValueError, TypeError, KeyError, OverflowError):
        print('STAGE 10 SCENARIO BLOCKED: Unsafe, noncausal or self-authorizing assumptions.')
        return 1
    if a.json:
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    else:
        print('EURUSD ORACLE | Stage 10 P10-13..16 offline scenario laboratory')
        print('Historical clock policy: ORIGINAL_BYTES_MATCH_PIN')
        print('Scenario: ' + result['scenario_status'])
        print('Synthetic paths: %d | Real predictions: 0' % result['hypothetical_scenarios_generated'])
        print('MiroFish adapter: NOT_INTEGRATED_PENDING_VALIDATION')
        print('Stage9 research exit: NOT_MET | Independent gates/families: 0/11 | 0/13')
        for reason in result['blockers']:
            print('  BLOCKER ' + reason)
        print('SHA-256: ' + result['report_sha256'])
        print('READ-ONLY | SYNTHETIC PATHS ONLY | NO AGENTS | NO BROKER | FINAL=HOLD')
    return 0


def main():
    raise SystemExit(run())


if __name__ == '__main__':
    main()

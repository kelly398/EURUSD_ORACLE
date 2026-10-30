"""P10-13..16: bounded synthetic scenario path, shock and adapter adversarial tests."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from backend.runtime.stage8_m5_offline_heartbeat import EPOCH, _digest
from backend.runtime.test_stage10_council_reliability_diversity_design import (
    POLICY, CONTRACTS, MANIFEST, save, z, read_time,
)
from backend.runtime.test_stage10_causal_pattern_memory import (
    pattern_fixture,
)
from backend.runtime.stage10_causal_pattern_memory import assess_stage10_pattern_memory
from backend.runtime.stage10_offline_scenario_laboratory import (
    VERSION, SCENARIO_VERSION, ADAPTER_VERSION, SHOCKS, ScenarioLabError,
    _uniform, _gaussian_like, assess_stage10_scenario_lab, run,
)

UTC = timezone.utc


def scenario_fixture(tmp_path, *, shocks=(), events=True, paths=8, steps=12,
                     regime=None, seed=20261030):
    hp, history, pp, packet, at = pattern_fixture(tmp_path, n=7)
    pattern = assess_stage10_pattern_memory(
        epoch_id=EPOCH, as_of=at, policy_file=POLICY,
        history_file=hp, pattern_file=pp,
        contracts_file=CONTRACTS, manifest_file=MANIFEST,
    )
    origin = history['cases'][-1]
    bar = read_time(origin['bar_close_at'])
    start = read_time(origin['decision_at'])
    sd = {
        'schema_version': 1, 'historical_epoch_id': EPOCH,
        'packet_id': 'LOCAL_OFFLINE_SCENARIO_001',
        'mode': 'SYNTHETIC_OFFLINE_SCENARIO_DESIGN_ONLY',
        'contract_version': SCENARIO_VERSION,
        'design_purpose': 'REPRODUCIBLE_STRESS_REHEARSAL_NOT_A_FORECAST',
        'pattern_report_sha256': pattern['report_sha256'],
        'pattern_packet_sha256': pattern['pattern_file_sha256'],
        'declared_at': history['declared_at'],
        'origin_decision_key': origin['decision_key'],
        'origin_feature_sha256': packet['rows'][-1]['feature_claim_sha256'],
        'origin_source_contract_sha256': packet['rows'][-1]['source_family_contract_sha256'],
        'start_at': origin['decision_at'],
        'start_bid': '1.080000', 'start_ask': '1.080200',
        'quote_first_available_at': z(bar+timedelta(seconds=2)),
        'source_class': 'LOCAL_SYNTHETIC_UNVERIFIED',
        'regime': origin['regime'] if regime is None else regime,
        'selected_vintage_sha256': 'c'*64,
        'selected_vintage_first_available_at': z(bar+timedelta(seconds=1)),
        'seed': seed, 'paths': paths, 'steps': steps,
        'step_seconds': 300, 'horizon_seconds': steps*300,
        'drift_pips_per_step': '+0.1500',
        'volatility_pips_per_step': '2.0000',
        'gap_probability_basis_points': 100,
        'gap_size_pips': '10.0000',
        'events': ([{
            'event_id': 'SYNTHETIC_FED_CALENDAR', 'known_at': z(bar),
            'scheduled_for': z(start+timedelta(seconds=steps*300)),
            'severity': 'HIGH',
        }] if events else []),
        'shocks': [{
            'shock_id': shock, 'at_step': (i % steps)+1, 'strength': 2,
            'assumption_class': 'SYNTHETIC_CONDITIONAL_NOT_OBSERVED',
        } for i, shock in enumerate(shocks)],
        'mirofish_adapter': {
            'adapter_name': 'MIROFISH', 'adapter_version_contract': ADAPTER_VERSION,
            'integration_status': 'NOT_INTEGRATED_PENDING_VALIDATION',
            'external_adapter_version': None, 'external_artifact_sha256': None,
            'network_enabled': False, 'security_reviewed': False,
            'reproducibility_verified': False,
            'benchmarked_against_baselines': False,
        },
        'final_test_opened': False,
    }
    path = save(tmp_path/'synthetic_scenario_lab.json', sd)
    return hp, history, pp, packet, path, sd, at


def assess(hp=None, pp=None, sp=None, *, at=None, prior=None, original_prior=None,
           prior_pattern=None, policy=POLICY, epoch=EPOCH):
    return assess_stage10_scenario_lab(
        epoch_id=epoch,
        as_of=at or z(datetime.now(UTC).replace(microsecond=0)),
        policy_file=policy, history_file=hp, prior_history=original_prior,
        pattern_file=pp, prior_pattern=prior_pattern,
        scenario_file=sp, prior_scenario=prior,
        contracts_file=CONTRACTS, manifest_file=MANIFEST,
    )


def valid(tmp_path, **opts):
    hp, history, pp, pat, sp, doc, at = scenario_fixture(tmp_path, **opts)
    return assess(hp, pp, sp, at=at)


def changed(tmp_path, mutate, **opts):
    hp, history, pp, pat, sp, doc, at = scenario_fixture(tmp_path, **opts)
    mutate(doc)
    save(sp, doc)
    with pytest.raises(ScenarioLabError):
        assess(hp, pp, sp, at=at)


# P10-13: frozen as-of origin and contract.

def test_p1013_empty_cli_scenario_has_no_paths():
    r = assess()
    assert r['scenario_status'] == 'NOT_SUPPLIED'
    assert r['synthetic_simulation'] is None
    assert r['hypothetical_scenarios_generated'] == 0
    assert r['final_action'] == 'HOLD'


def test_p1013_valid_synthetic_origin_is_bound_to_pattern(tmp_path):
    r = valid(tmp_path)
    assert r['scenario_status'] == 'LOCAL_SYNTHETIC_UNATTESTED'
    assert r['pattern_status'] == 'LOCAL_SYNTHETIC_UNATTESTED'
    assert len(r['pattern_report_sha256']) == 64
    assert r['historical_epoch_id'] == EPOCH


def test_p1013_future_known_quote_refused(tmp_path):
    changed(tmp_path, lambda d: d.update(quote_first_available_at=z(
        read_time(d['start_at']) + timedelta(seconds=1))))


def test_p1013_future_known_macro_vintage_refused(tmp_path):
    changed(tmp_path, lambda d: d.update(selected_vintage_first_available_at=z(
        read_time(d['start_at']) + timedelta(seconds=1))))


def test_p1013_late_local_scenario_declaration_refused(tmp_path):
    changed(tmp_path, lambda d: d.update(declared_at=z(
        read_time(d['start_at']) + timedelta(seconds=1))))


def test_p1013_invalid_timestamp_has_scenario_specific_error(tmp_path):
    changed(tmp_path, lambda d: d.update(start_at='2026-10-30T00:00:00+03:00'))


@pytest.mark.parametrize('field, bad', [
    ('origin_decision_key', 'f'*64), ('origin_feature_sha256', 'f'*64),
    ('origin_source_contract_sha256', 'f'*64),
    ('pattern_report_sha256', 'f'*64), ('pattern_packet_sha256', 'f'*64),
    ('historical_epoch_id', 'f'*64),
    ('source_class', 'BROKER_VERIFIED'), ('regime', 'WINNING_ONLY'),
    ('design_purpose', 'AUTHORIZED_REAL_TRADES'), ('final_test_opened', True),
    ('mode', 'LIVE_TRADING'), ('contract_version', 'SHADOW_APPROVED'),
])
def test_p1013_origin_and_scope_change_refused(tmp_path, field, bad):
    changed(tmp_path, lambda d: d.update({field: bad}))


@pytest.mark.parametrize('field,bad', [
    ('start_bid', '1.200000'), ('start_bid', '-1'),
    ('start_ask', 1.08), ('start_ask', '0'),
    ('start_bid', '0.001000'), ('start_ask', '5.000000'),
    ('selected_vintage_sha256', 'notahash'),
])
def test_p1013_invalid_synthetic_quote_or_provenance(tmp_path, field, bad):
    changed(tmp_path, lambda d: d.update({field: bad}))


def test_p1013_calendar_first_known_must_precede_scenario(tmp_path):
    def modify(d):
        d['events'][0]['known_at'] = z(read_time(d['start_at']) + timedelta(seconds=1))
    changed(tmp_path, modify)


def test_p1013_calendar_event_must_fall_within_horizon(tmp_path):
    def modify(d):
        d['events'][0]['scheduled_for'] = z(read_time(d['start_at'])+timedelta(days=3))
    changed(tmp_path, modify)


def test_p1013_duplicate_event_id_rejected(tmp_path):
    def mutate(d):
        d['events'].append(deepcopy(d['events'][0]))
    changed(tmp_path, mutate)


def test_p1013_event_calendar_may_be_empty(tmp_path):
    r = valid(tmp_path, events=False)
    assert r['hypothetical_scenarios_generated'] == 8


# P10-14: deterministic bounded CLT12 + honest fixed drift reference.

def test_p1014_fixed_seed_replays_same_path_digest(tmp_path):
    hp, h, pp, p, sp, doc, at = scenario_fixture(tmp_path)
    a, b = assess(hp, pp, sp, at=at), assess(hp, pp, sp, at=at)
    assert a == b
    assert a['synthetic_simulation']['simulated_path_manifest_sha256'] == b['synthetic_simulation']['simulated_path_manifest_sha256']


def test_p1014_changing_seed_changes_output_but_not_authorization(tmp_path):
    hp, h, pp, p, sp, doc, at = scenario_fixture(tmp_path)
    first = assess(hp, pp, sp, at=at)
    doc['seed'] = 123456789
    save(sp, doc)
    second = assess(hp, pp, sp, at=at)
    assert first['synthetic_simulation']['simulated_path_manifest_sha256'] != second['synthetic_simulation']['simulated_path_manifest_sha256']
    assert second['final_action'] == 'HOLD'


def test_p1014_counter_rng_reproducible_and_bounded():
    x = _uniform(420, 1, 1, 2)
    assert x == _uniform(420, 1, 1, 2)
    assert Decimal(0) <= x < Decimal(1)
    assert x != _uniform(420, 1, 2, 2)
    assert Decimal(-6) < _gaussian_like(420, 1, 2) < Decimal(6)


def test_p1014_preview_contains_only_synthetic_labelled_paths(tmp_path):
    r = valid(tmp_path)['synthetic_simulation']
    assert r['simulated_path_count'] == 8
    assert r['simulated_steps_per_path'] == 12
    assert len(r['sample_path_previews']) == 3
    assert all(x['simulated_only'] is True for x in r['sample_path_previews'])
    assert all('synthetic_bid' in q and 'synthetic_ask' in q
               for p in r['sample_path_previews'] for q in p['steps'])


def test_p1014_quantiles_ordered_as_simulated_not_market_probabilities(tmp_path):
    r = valid(tmp_path)['synthetic_simulation']
    assert Decimal(r['terminal_synthetic_mid_p10']) <= Decimal(r['terminal_synthetic_mid_p50']) <= Decimal(r['terminal_synthetic_mid_p90'])
    assert r['quantile_frequencies_are_market_probabilities'] is False
    assert r['probabilistic_calibration_executed'] is False


def test_p1014_drift_only_transparent_benchmark_present(tmp_path):
    r = valid(tmp_path)['synthetic_simulation']
    assert Decimal(r['simple_drift_only_terminal_mid']) > Decimal(0)
    assert r['benchmark_comparison_type'] == 'ASSUMPTION_ONLY_NOT_PREDICTIVE_ACCURACY'


@pytest.mark.parametrize('field,bad', [
    ('seed', True), ('seed', -1), ('seed', 4294967296), ('seed', '100'),
    ('paths', 0), ('paths', True), ('paths', 65),
    ('steps', 0), ('steps', 97), ('steps', True),
    ('step_seconds', 60), ('horizon_seconds', 60),
    ('drift_pips_per_step', '0.25'), ('drift_pips_per_step', '+100'),
    ('volatility_pips_per_step', '0'), ('volatility_pips_per_step', '200'),
    ('gap_probability_basis_points', -1), ('gap_probability_basis_points', True),
    ('gap_probability_basis_points', 1001), ('gap_size_pips', '150'),
])
def test_p1014_random_seed_and_resource_bounds_enforced(tmp_path, field, bad):
    changed(tmp_path, lambda d: d.update({field: bad}))


def test_p1014_single_path_also_reproducible(tmp_path):
    r = valid(tmp_path, paths=1)['synthetic_simulation']
    assert r['simulated_path_count'] == 1
    assert len(r['sample_path_previews']) == 1


# P10-15: shocks are explicit, bounded, conditional and do not imply winning forecasts.

def test_p1015_all_seven_versioned_shocks_enumerated(tmp_path):
    r = valid(tmp_path)
    assert tuple(r['shock_library_available']) == SHOCKS


def test_p1015_pessimistic_spread_widening_increases_assumed_spread(tmp_path):
    plain = valid(tmp_path)['synthetic_simulation']['max_assumed_spread_pips']
    stressed = valid(tmp_path, shocks=('SPREAD_WIDENING',))['synthetic_simulation']['max_assumed_spread_pips']
    assert Decimal(stressed) > Decimal(plain)


def test_p1015_source_outage_never_becomes_available_label(tmp_path):
    r = valid(tmp_path, shocks=('SOURCE_OUTAGE',))['synthetic_simulation']
    assert r['source_unavailable_synthetic_steps'] > 0
    assert any(x['source_available_in_assumed_scenario'] is False
               for y in r['sample_path_previews'] for x in y['steps'])
    assert r['actual_quote_data_or_forecasts_used'] is False


def test_p1015_policy_up_down_generate_different_counterfactuals(tmp_path):
    rise = valid(tmp_path, shocks=('POLICY_SURPRISE_UP',))['synthetic_simulation']['terminal_synthetic_mid_p50']
    fall = valid(tmp_path, shocks=('POLICY_SURPRISE_DOWN',))['synthetic_simulation']['terminal_synthetic_mid_p50']
    assert Decimal(rise) > Decimal(fall)


def test_p1015_high_vol_regime_is_supported_but_not_relabelable(tmp_path):
    def modify(d):
        d['regime'] = 'HIGH_VOLATILITY'
    changed(tmp_path, modify)


def test_p1015_every_shock_is_synthetic_without_actual_receipt(tmp_path):
    r = valid(tmp_path, shocks=SHOCKS)
    assert r['simulated_paths_count_as_actual_predictions'] is False
    assert r['real_market_forecasts_issued'] == 0
    assert r['independent_scenario_research_admission'] is False


@pytest.mark.parametrize('field,bad', [
    ('shock_id', 'REAL_BROKER_ORDER'), ('at_step', 0), ('at_step', 1000),
    ('at_step', True), ('strength', True), ('strength', 0),
    ('strength', 6), ('assumption_class', 'ACTUAL_ECONOMIC_EVENT'),
])
def test_p1015_unbounded_or_real_shock_rejected(tmp_path, field, bad):
    def modify(d):
        d['shocks'][0][field] = bad
    changed(tmp_path, modify, shocks=('SOURCE_OUTAGE',))


def test_p1015_duplicate_shock_identity_rejected(tmp_path):
    def modify(d):
        d['shocks'].append(deepcopy(d['shocks'][0]))
    changed(tmp_path, modify, shocks=('SOURCE_OUTAGE',))


def test_p1015_shock_count_must_not_exceed_catalog(tmp_path):
    def modify(d):
        d['shocks'] = [deepcopy(d['shocks'][0]) for _ in range(8)]
    changed(tmp_path, modify, shocks=('SOURCE_OUTAGE',))


# P10-16: MiroFish remains disabled pending independently evaluated validation.

def test_p1016_adapter_report_explicitly_unintegrated(tmp_path):
    r = valid(tmp_path)['mirofish_integration_decision']
    assert r['status'] == 'NOT_INTEGRATED_PENDING_VALIDATION'
    assert r['network_contacted'] is False
    assert r['artifact_or_security_review_completed'] is False
    assert r['external_engine_forecast_advantage_demonstrated'] is False


@pytest.mark.parametrize('field,bad', [
    ('integration_status', 'INTEGRATED_AND_APPROVED'),
    ('network_enabled', True), ('security_reviewed', True),
    ('reproducibility_verified', True), ('benchmarked_against_baselines', True),
    ('external_adapter_version', 'UNVERIFIED_V1'),
    ('external_artifact_sha256', 'f'*64),
    ('adapter_name', 'BROKER_LIVE'), ('adapter_version_contract', 'NEW_TRADING_MODE'),
])
def test_p1016_adapter_self_attestation_rejected(tmp_path, field, bad):
    changed(tmp_path, lambda d: d['mirofish_adapter'].update({field: bad}))


# Global lineage, local snapshots, safety, CLI.

def test_global_scenario_without_pattern_and_history_refused(tmp_path):
    fake = save(tmp_path/'fake.json', {})
    with pytest.raises(ScenarioLabError, match='complete local history and pattern'):
        assess(sp=fake)


def test_global_scenario_with_history_but_no_pattern_refused(tmp_path):
    hp, h, pp, p, sp, d, at = scenario_fixture(tmp_path)
    with pytest.raises(ScenarioLabError):
        assess(hp, sp=sp, at=at)


def test_global_prior_scenario_without_current_refused(tmp_path):
    fake = save(tmp_path/'fake.json', {})
    with pytest.raises(ScenarioLabError, match='Prior scenario alone'):
        assess(prior=fake)


def test_global_same_path_prior_snapshot_refused(tmp_path):
    hp, h, pp, p, sp, d, at = scenario_fixture(tmp_path)
    with pytest.raises(ScenarioLabError, match='Separate prior'):
        assess(hp, pp, sp, prior=sp, at=at)


def test_global_unchanged_prior_local_scenario_still_no_witness(tmp_path):
    hp, h, pp, p, sp, d, at = scenario_fixture(tmp_path)
    prior = tmp_path/'copy_scenario.json'
    prior.write_bytes(sp.read_bytes())
    r = assess(hp, pp, sp, prior=prior, at=at)
    assert r['prior_scenario_file_sha256'] == sha256(sp.read_bytes()).hexdigest()
    assert r['independent_scenario_research_admission'] is False


def test_global_prior_copy_detects_rehashed_change(tmp_path):
    hp, h, pp, p, sp, d, at = scenario_fixture(tmp_path)
    prior = tmp_path/'prior.json'
    prior.write_bytes(sp.read_bytes())
    d['seed'] = 99
    save(sp, d)
    with pytest.raises(ScenarioLabError, match='bytes were edited'):
        assess(hp, pp, sp, prior=prior, at=at)


def test_global_pattern_mutation_invalidates_scenario(tmp_path):
    hp, h, pp, p, sp, d, at = scenario_fixture(tmp_path)
    p['rows'][-1]['trend_bps'] += 1
    from backend.runtime.test_stage10_causal_pattern_memory import frozen_feature
    frozen_feature(p['rows'][-1]); save(pp, p)
    with pytest.raises(ScenarioLabError):
        assess(hp, pp, sp, at=at)


def test_global_seat_history_mutation_invalidates_scenario(tmp_path):
    hp, h, pp, p, sp, d, at = scenario_fixture(tmp_path)
    h['packet_id'] = 'NEW_HISTORY_ID'
    save(hp, h)
    with pytest.raises(ScenarioLabError):
        assess(hp, pp, sp, at=at)


def test_global_pinned_policy_byte_edit_refused(tmp_path):
    bad = tmp_path/'modified_policy.json'
    bad.write_bytes(POLICY.read_bytes()+b' ')
    with pytest.raises(ScenarioLabError, match='boundary failed replay'):
        assess(policy=bad)


def test_global_historical_epoch_rebinding_refused():
    with pytest.raises(ScenarioLabError, match='rebinding'):
        assess(epoch='f'*64)


def test_global_future_as_of_refused():
    with pytest.raises(ScenarioLabError, match='Future review'):
        assess(at=z(datetime.now(UTC)+timedelta(days=1)))


def test_global_broker_and_shadow_flags_unchanged(tmp_path):
    r = valid(tmp_path, shocks=SHOCKS)
    for x in ('read_only','files_written','network_accessed','broker_connected',
              'collector_started','council_invoked','model_loaded','training_authorized',
              'paper_orders_authorized','live_orders_authorized',
              'historical_epoch_rebound','future_epoch_created'):
        assert r[x] is True if x == 'read_only' else r[x] is False
    assert r['final_action'] == 'HOLD'


def test_global_never_write_originals(tmp_path):
    hp, h, pp, p, sp, d, at = scenario_fixture(tmp_path)
    old = [sha256(x.read_bytes()).hexdigest() for x in (hp, pp, sp)]
    r = assess(hp, pp, sp, at=at)
    assert [sha256(x.read_bytes()).hexdigest() for x in (hp, pp, sp)] == old
    assert r['files_written'] is False


def test_global_sha_reproducible_for_same_asof_and_seed(tmp_path):
    hp, h, pp, p, sp, d, at = scenario_fixture(tmp_path)
    a, b = assess(hp, pp, sp, at=at), assess(hp, pp, sp, at=at)
    assert a == b
    assert a['report_sha256'] == _digest({k:v for k,v in a.items() if k!='report_sha256'})


def test_global_duplicate_json_key_refused(tmp_path):
    hp, h, pp, p, sp, d, at = scenario_fixture(tmp_path)
    sp.write_text('{"mode":"SYNTHETIC_OFFLINE_SCENARIO_DESIGN_ONLY","mode":"LIVE"}')
    with pytest.raises(ScenarioLabError, match='Unsafe'):
        assess(hp, pp, sp, at=at)


def test_global_symlink_scenario_refused_if_supported(tmp_path):
    hp, h, pp, p, sp, d, at = scenario_fixture(tmp_path)
    alias = tmp_path/'linked_scenario.json'
    try:
        alias.symlink_to(sp)
    except (OSError, NotImplementedError):
        pytest.skip('Symlink privilege unavailable')
    with pytest.raises(ScenarioLabError, match='Unsafe'):
        assess(hp, pp, alias, at=at)


def test_global_oversized_scenario_refused(tmp_path):
    hp, h, pp, p, sp, d, at = scenario_fixture(tmp_path)
    sp.write_bytes(b'x' * 131073)
    with pytest.raises(ScenarioLabError, match='Unsafe'):
        assess(hp, pp, sp, at=at)


def test_global_scenario_cannot_add_operator_permission(tmp_path):
    changed(tmp_path, lambda d: d.update(shadow_operator_approved=True))


def test_global_cli_no_inputs_prints_hold(capsys):
    code = run(['--read-only-scenarios', '--synthetic-only', '--epoch-id', EPOCH,
                '--as-of', z(datetime.now(UTC).replace(microsecond=0)),
                '--policy-file', str(POLICY), '--contracts-file', str(CONTRACTS),
                '--manifest-file', str(MANIFEST)])
    assert code == 0
    stdout = capsys.readouterr().out
    assert 'Synthetic paths: 0' in stdout
    assert 'FINAL=HOLD' in stdout


def test_global_cli_with_fixture_json_does_not_authorize(tmp_path, capsys):
    hp, h, pp, p, sp, d, at = scenario_fixture(tmp_path)
    code = run(['--read-only-scenarios', '--synthetic-only', '--epoch-id', EPOCH,
                '--as-of', at, '--policy-file', str(POLICY),
                '--contracts-file', str(CONTRACTS), '--manifest-file', str(MANIFEST),
                '--history-file', str(hp), '--pattern-file', str(pp),
                '--scenario-file', str(sp), '--json'])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report['hypothetical_scenarios_generated'] == 8
    assert report['real_market_forecasts_issued'] == 0
    assert report['final_action'] == 'HOLD'


def test_global_cli_requires_safety_flags():
    with pytest.raises(SystemExit) as e:
        run(['--epoch-id', EPOCH, '--as-of', z(datetime.now(UTC))])
    assert e.value.code == 2

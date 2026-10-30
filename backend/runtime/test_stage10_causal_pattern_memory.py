"""P10-09..12: synthetic-only causal pattern, nearest-neighbor, ablation tests."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest

from backend.runtime.stage8_m5_offline_heartbeat import EPOCH, _digest
from backend.runtime.test_stage10_council_reliability_diversity_design import (
    POLICY, CONTRACTS, MANIFEST, setup as original_history, save, z, read_time,
)
from backend.runtime.stage10_council_reliability_diversity_design import assess_stage10_council_reliability
from backend.runtime.stage10_causal_pattern_memory import (
    VERSION, PATTERN_VERSION, ABLATION_VERSION, PatternMemoryError,
    assess_stage10_pattern_memory, _distance, _wilson, run,
)

UTC = timezone.utc


def frozen_feature(row):
    row['feature_claim_sha256'] = _digest({k: v for k, v in row.items()
                                           if k != 'feature_claim_sha256'})


def pattern_fixture(tmp_path, *, n=8, step_days=4, missing=(), same_event=False,
                    source_switch=False, duplicate_event=False):
    hp, history, at = original_history(tmp_path, n=n, step_days=step_days,
                                       missing=missing, same_event=same_event)
    source = assess_stage10_council_reliability(
        epoch_id=EPOCH, as_of=at, policy_file=POLICY,
        history_file=hp, contracts_file=CONTRACTS, manifest_file=MANIFEST,
    )
    features = []
    for i, case in enumerate(history['cases']):
        bar = read_time(case['bar_close_at'])
        features.append({
            'decision_key': case['decision_key'], 'decision_at': case['decision_at'],
            'event_id': case['event_id'], 'horizon_seconds': case['horizon_seconds'],
            'source_snapshot_sha256': case['source_snapshot_sha256'],
            'source_family_contract_sha256': 'a'*64 if not source_switch or i < n//2 else 'c'*64,
            'trend_bps': 10+i, 'structure': 1, 'volatility_pips': '5.0000',
            'spread_pips': '0.9000', 'session': case['session'],
            'regime': case['regime'], 'event_risk': 'NONE_KNOWN',
            'feature_first_available_at': z(bar+timedelta(seconds=10)),
            'macro_vintage_sha256': 'd'*64,
            'macro_first_available_at': z(bar+timedelta(seconds=3)),
            'feature_claim_sha256': '0'*64,
        })
        frozen_feature(features[-1])
    first = read_time(history['cases'][0]['bar_close_at'])
    actions = []
    for i, case in enumerate(history['cases']):
        actions.append({
            'decision_key': case['decision_key'],
            'without_memory': 'HOLD',
            'with_memory': ('BUY' if i > 0 and not same_event else 'HOLD'),
        })
    packet = {
        'schema_version': 1, 'historical_epoch_id': EPOCH,
        'packet_id': 'LOCAL_SYNTHETIC_PATTERN_REHEARSAL_001',
        'mode': 'SYNTHETIC_LOCAL_PATTERN_MEMORY_ONLY',
        'contract_version': PATTERN_VERSION,
        'seat_history_file_sha256': sha256(hp.read_bytes()).hexdigest(),
        'seat_reliability_report_sha256': source['report_sha256'],
        'declared_at': history['declared_at'],
        'feature_schema': 'FIXED_TRANSPARENT_EURUSD_M5_V1',
        'max_neighbors': 6, 'minimum_band_samples': 5,
        'embargo_seconds': history['embargo_seconds'],
        'rows': features,
        'ablation_plan': {
            'contract_version': ABLATION_VERSION,
            'frozen_at': z(first-timedelta(days=1)),
            'window_role': 'SYNTHETIC_REHEARSAL_NOT_UNSEEN_TEST',
            'metric': 'COMPLETE_CASE_ACTION_DISAGREEMENT_NO_OUTCOME_SCORING',
            'actions': actions,
        },
    }
    # Avoid false retrieval if same-event or source change removes all neighbors.
    if source_switch:
        for i, action in enumerate(packet['ablation_plan']['actions']):
            if i in (0, n//2):
                action['with_memory'] = 'HOLD'
    pp = save(tmp_path/'local_pattern_packet.json', packet)
    return hp, history, pp, packet, at


def assess(hp=None, pp=None, *, at=None, prior=None, original_prior=None,
           policy=POLICY, epoch=EPOCH):
    review = at or z(datetime.now(UTC).replace(microsecond=0))
    return assess_stage10_pattern_memory(
        epoch_id=epoch, as_of=review, policy_file=policy,
        history_file=hp, prior_history=original_prior,
        pattern_file=pp, prior_pattern=prior,
        contracts_file=CONTRACTS, manifest_file=MANIFEST,
    )


def valid(tmp_path, **kwargs):
    hp, history, pp, packet, at = pattern_fixture(tmp_path, **kwargs)
    return assess(hp, pp, at=at)


def changed(tmp_path, mutation, *, rehash=True, n=8):
    hp, history, pp, packet, at = pattern_fixture(tmp_path, n=n)
    mutation(packet)
    if rehash:
        for item in packet.get('rows', []):
            if isinstance(item, dict) and 'feature_claim_sha256' in item:
                frozen_feature(item)
    save(pp, packet)
    with pytest.raises(PatternMemoryError):
        assess(hp, pp, at=at)


# Causal features and source-bound history.

def test_p1009_empty_history_reports_no_training_or_forecast():
    r = assess()
    assert r['pattern_status'] == 'NOT_SUPPLIED'
    assert r['cases_with_asof_features'] == 0
    assert r['stage9_research_decision'] == 'NOT_ENOUGH_EVIDENCE'
    assert r['final_action'] == 'HOLD'


def test_p1009_valid_case_features_are_explicitly_unattested(tmp_path):
    r = valid(tmp_path)
    assert r['cases_with_asof_features'] == 8
    assert r['historical_case_source_receipts_independently_verified'] == 0
    assert r['model_retrieval_or_pattern_learning_performed'] is False
    assert r['council_pattern_seat_dispatched'] is False


def test_p1009_source_case_sha_and_local_feature_sha_bound(tmp_path):
    hp, history, pp, packet, at = pattern_fixture(tmp_path)
    r = assess(hp, pp, at=at)
    assert r['pattern_cases'][0]['local_feature_sha256'] == packet['rows'][0]['feature_claim_sha256']
    assert r['pattern_cases'][0]['horizon_seconds'] == history['cases'][0]['horizon_seconds']


def test_p1009_feature_digest_catches_unhashed_edit(tmp_path):
    changed(tmp_path, lambda d: d['rows'][0].update(trend_bps=88), rehash=False)


@pytest.mark.parametrize('field,bad', [
    ('trend_bps', True), ('trend_bps', 501), ('trend_bps', '10'),
    ('structure', 3), ('structure', '1'), ('volatility_pips', '0'),
    ('volatility_pips', '2000'), ('spread_pips', '-1'),
    ('spread_pips', 9.5), ('event_risk', 'AFTER_EARNINGS_RELEASE'),
    ('session', 'AFTER_DECISION'), ('regime', 'FAVORABLE_REGIME'),
    ('source_family_contract_sha256', 'X'*64),
])
def test_p1009_invalid_feature_semantics_fails_closed(tmp_path, field, bad):
    changed(tmp_path, lambda d: d['rows'][0].update({field: bad}))


def test_p1009_macro_vintage_cannot_arrive_after_feature(tmp_path):
    changed(tmp_path, lambda d: d['rows'][0].update(
        macro_first_available_at=d['rows'][0]['decision_at']))


def test_p1009_macro_vintage_missing_timestamp_pair_refused(tmp_path):
    changed(tmp_path, lambda d: d['rows'][0].update(macro_vintage_sha256=None))


def test_p1009_future_feature_available_at_refused(tmp_path):
    def mutate(d):
        d['rows'][0]['feature_first_available_at'] = z(
            read_time(d['rows'][0]['decision_at']) + timedelta(seconds=1))
    changed(tmp_path, mutate)


def test_p1009_feature_reassignment_to_wrong_decision_rejected(tmp_path):
    changed(tmp_path, lambda d: d['rows'][0].update(decision_key=d['rows'][1]['decision_key']))


def test_p1009_feature_order_missing_or_duplicate_refused(tmp_path):
    changed(tmp_path, lambda d: d['rows'].pop())
    changed(tmp_path, lambda d: d['rows'].insert(0, deepcopy(d['rows'][0])))


def test_p1009_historical_history_edit_breaks_hash_chain(tmp_path):
    hp, history, pp, packet, at = pattern_fixture(tmp_path)
    history['cases'][1]['event_id'] = 'CHANGED_EVENT'
    save(hp, history)
    with pytest.raises(PatternMemoryError):
        assess(hp, pp, at=at)


def test_p1009_local_snapshot_does_not_count_as_source_attestation(tmp_path):
    hp, history, pp, packet, at = pattern_fixture(tmp_path)
    prior = tmp_path/'separate_pattern_copy.json'
    prior.write_bytes(pp.read_bytes())
    r = assess(hp, pp, prior=prior, at=at)
    assert r['prior_pattern_file_sha256'] == sha256(pp.read_bytes()).hexdigest()
    assert r['trusted_retrieval_labels'] == 0


def test_p1009_edited_local_prior_copy_fails(tmp_path):
    hp, history, pp, packet, at = pattern_fixture(tmp_path)
    prior = tmp_path/'prior_pattern.json'
    prior.write_bytes(pp.read_bytes())
    packet['rows'][0]['trend_bps'] = 60
    frozen_feature(packet['rows'][0])
    save(pp, packet)
    with pytest.raises(PatternMemoryError, match='changed after freeze'):
        assess(hp, pp, prior=prior, at=at)


# Nearest-neighbor causal eligibility and uncertainty labels.

def test_p1010_first_decision_has_no_historical_analogue(tmp_path):
    r = valid(tmp_path)
    assert r['pattern_cases'][0]['neighbors_returned'] == 0
    assert r['pattern_cases'][0]['deterministic_directional_forecast'] is None


def test_p1010_matched_cases_never_include_query_itself(tmp_path):
    r = valid(tmp_path)
    for case in r['pattern_cases']:
        assert case['decision_key'] not in {x['historical_decision_key'] for x in case['retrieved_neighbors']}


def test_p1010_bounded_six_nearest_history(tmp_path):
    r = valid(tmp_path, n=11)
    assert r['pattern_cases'][-1]['neighbors_returned'] == 6
    assert len(r['pattern_cases'][-1]['retrieved_neighbors']) == 6


def test_p1010_horizon_changed_case_cannot_be_neighbor(tmp_path):
    hp, history, pp, packet, at = pattern_fixture(tmp_path)
    c = history['cases'][0]
    c['horizon_seconds'] = 86400
    c['target_at'] = z(read_time(c['decision_at']) + timedelta(days=1))
    c['outcome_available_at'] = z(read_time(c['target_at']) + timedelta(seconds=300))
    from backend.runtime.test_stage10_council_reliability_diversity_design import freeze
    freeze(history)
    save(hp, history)
    packet['seat_history_file_sha256'] = sha256(hp.read_bytes()).hexdigest()
    latest = assess_stage10_council_reliability(
        epoch_id=EPOCH, as_of=at, policy_file=POLICY, history_file=hp,
        contracts_file=CONTRACTS, manifest_file=MANIFEST)
    packet['seat_reliability_report_sha256'] = latest['report_sha256']
    packet['rows'][0]['horizon_seconds'] = 86400
    packet['ablation_plan']['actions'][1]['with_memory'] = 'HOLD'
    frozen_feature(packet['rows'][0]); save(pp, packet)
    r = assess(hp, pp, at=at)
    assert all(history['cases'][0]['decision_key'] not in [x['historical_decision_key']
               for x in item['retrieved_neighbors']] for item in r['pattern_cases'][1:])


def test_p1010_seat_history_one_day_embargo_blocks_next_day(tmp_path):
    hp, history, pp, packet, at = pattern_fixture(tmp_path, n=4, step_days=1)
    packet['ablation_plan']['actions'][1]['with_memory'] = 'HOLD'
    save(pp, packet)
    r = assess(hp, pp, at=at)
    assert r['pattern_cases'][1]['neighbors_returned'] == 0
    assert r['pattern_cases'][2]['neighbors_returned'] == 1


def test_p1010_same_event_id_never_retrieved_as_analogue(tmp_path):
    r = valid(tmp_path, n=5, same_event=True)
    assert all(x['neighbors_returned'] == 0 for x in r['pattern_cases'])


def test_p1010_source_family_contract_change_filters_incompatible(tmp_path):
    r = valid(tmp_path, n=8, source_switch=True)
    assert r['pattern_cases'][4]['neighbors_returned'] == 0
    assert r['pattern_cases'][5]['neighbors_returned'] == 1


def test_p1010_duplicate_event_does_not_overweight_history(tmp_path):
    hp, history, pp, packet, at = pattern_fixture(tmp_path)
    # Two prior cases share an event, while query has a different event.
    history['cases'][1]['event_id'] = history['cases'][0]['event_id']
    from backend.runtime.test_stage10_council_reliability_diversity_design import freeze
    freeze(history); save(hp, history)
    packet['seat_history_file_sha256'] = sha256(hp.read_bytes()).hexdigest()
    revised = assess_stage10_council_reliability(
        epoch_id=EPOCH, as_of=at, policy_file=POLICY, history_file=hp,
        contracts_file=CONTRACTS, manifest_file=MANIFEST)
    packet['seat_reliability_report_sha256'] = revised['report_sha256']
    packet['rows'][1]['event_id'] = history['cases'][0]['event_id']
    packet['ablation_plan']['actions'][1]['with_memory'] = 'HOLD'
    frozen_feature(packet['rows'][1]); save(pp, packet)
    r = assess(hp, pp, at=at)
    assert r['pattern_cases'][-1]['duplicate_event_candidates_suppressed'] >= 1
    assert len({x['historical_decision_key'] for x in r['pattern_cases'][-1]['retrieved_neighbors']}) == r['pattern_cases'][-1]['neighbors_returned']


def test_p1010_cannot_treat_missing_as_a_buy(tmp_path):
    r = valid(tmp_path, n=8, missing=(1,))
    assert r['pattern_cases'][-1]['missing_historical_outcomes'] >= 1
    assert r['pattern_cases'][-1]['locally_labeled_buy'] <= r['pattern_cases'][-1]['neighbors_returned']-1


def test_p1010_no_prospective_forecast_emitted(tmp_path):
    r = valid(tmp_path)
    assert r['real_predictions_registered'] == 0
    assert all(x['deterministic_directional_forecast'] is None for x in r['pattern_cases'])


def test_p1011_fixed_distance_increases_for_regime_mismatch(tmp_path):
    hp, history, pp, packet, at = pattern_fixture(tmp_path)
    a = packet['rows'][0]
    b = deepcopy(a)
    assert _distance(a, b) == 0
    b['regime'] = 'RANGE'
    assert _distance(a, b) == 1


def test_p1011_distance_is_symmetric(tmp_path):
    hp, history, pp, packet, at = pattern_fixture(tmp_path)
    a = packet['rows'][0]
    b = deepcopy(packet['rows'][1]); b['structure'] = -1
    assert _distance(a, b) == _distance(b, a)


def test_p1011_wilson_interval_sanity_is_descriptive_only():
    assert _wilson(0, 5)[0] == '0.0000'
    assert _wilson(5, 5)[1] == '1.0000'
    lo, hi = _wilson(2, 6)
    assert 0 <= float(lo) < float(hi) <= 1


def test_p1011_small_sample_has_no_uncertainty_interval(tmp_path):
    r = valid(tmp_path, n=3)
    assert all(x['illustrative_iid_wilson_band'] is None for x in r['pattern_cases'])


def test_p1011_large_sample_labels_interval_as_nonindependent(tmp_path):
    r = valid(tmp_path, n=12)
    last = r['pattern_cases'][-1]
    assert last['illustrative_iid_wilson_band'] is not None
    assert last['independent_or_dependence_corrected_interval'] is None
    assert last['independent_retrieval_value_established'] is False


def test_p1011_regime_and_event_mismatch_visible(tmp_path):
    r = valid(tmp_path)
    assert all('regime_mismatch' in x and 'event_risk_mismatch' in x
               for item in r['pattern_cases'] for x in item['retrieved_neighbors'])


# Selection and ablation cannot be laundered into evidence.

def test_p1012_all_cases_in_ablation_denominator(tmp_path):
    r = valid(tmp_path)
    assert r['ablation_preview']['full_case_denominator'] == 8
    assert r['ablation_preview']['local_action_disagreements'] == 7
    assert r['ablation_preview']['independently_evaluated_on_unseen_data'] is False


def test_p1012_cherry_picked_ablation_rows_refused(tmp_path):
    changed(tmp_path, lambda d: d['ablation_plan']['actions'].pop())


def test_p1012_duplicated_ablation_decision_refused(tmp_path):
    changed(tmp_path, lambda d: d['ablation_plan']['actions'][1].update(
        decision_key=d['ablation_plan']['actions'][0]['decision_key']))


def test_p1012_no_neighbor_may_not_change_vote(tmp_path):
    changed(tmp_path, lambda d: d['ablation_plan']['actions'][0].update(with_memory='BUY'))


@pytest.mark.parametrize('field,bad', [
    ('window_role', 'FINAL_TEST'), ('metric', 'WINNING_RETURN'),
    ('contract_version', 'APPROVED_FOR_MODEL_TRAINING'),
])
def test_p1012_ablation_cannot_self_approve(tmp_path, field, bad):
    changed(tmp_path, lambda d: d['ablation_plan'].update({field: bad}))


def test_p1012_postdecision_ablation_freeze_refused(tmp_path):
    changed(tmp_path, lambda d: d['ablation_plan'].update(
        frozen_at=d['rows'][0]['decision_at']))


def test_p1012_local_ablation_never_retrains_model(tmp_path):
    r = valid(tmp_path)
    assert r['ablation_preview']['retrieval_promoted_to_council'] is False
    assert r['independent_edge_or_ablation_improvement_established'] is False
    assert r['training_authorized'] is False


# Path, schema, replay and CLI fail-closed.

def test_global_pattern_without_history_refused(tmp_path):
    fake = save(tmp_path/'fake.json', {})
    with pytest.raises(PatternMemoryError, match='requires original seat history'):
        assess(pp=fake)


def test_global_prior_pattern_without_current_refused(tmp_path):
    fake = save(tmp_path/'fake.json', {})
    with pytest.raises(PatternMemoryError, match='requires current packet'):
        assess(prior=fake)


def test_global_same_local_prior_path_refused(tmp_path):
    hp, hist, pp, packet, at = pattern_fixture(tmp_path)
    with pytest.raises(PatternMemoryError, match='Distinct'):
        assess(hp, pp, prior=pp, at=at)


def test_global_modified_policy_rejected(tmp_path):
    bad = tmp_path/'changed.json'
    bad.write_bytes(POLICY.read_bytes()+b' ')
    with pytest.raises(PatternMemoryError):
        assess(policy=bad)


def test_global_future_cutoff_refused():
    with pytest.raises(PatternMemoryError, match='Future'):
        assess(at=z(datetime.now(UTC)+timedelta(days=1)))


def test_global_historical_epoch_rebinding_refused():
    with pytest.raises(PatternMemoryError, match='rebinding'):
        assess(epoch='f'*64)


def test_global_no_extra_json_fields(tmp_path):
    changed(tmp_path, lambda d: d.update(real_edge_proven=True))


def test_global_no_operator_permission_claim(tmp_path):
    changed(tmp_path, lambda d: d.update(shadow_approved=True))


def test_global_duplicate_json_keys_refused(tmp_path):
    hp, hist, pp, packet, at = pattern_fixture(tmp_path)
    pp.write_text('{"mode":"SYNTHETIC_LOCAL_PATTERN_MEMORY_ONLY","mode":"LIVE"}')
    with pytest.raises(PatternMemoryError, match='Unsafe'):
        assess(hp, pp, at=at)


def test_global_symlink_refused_if_host_allows(tmp_path):
    hp, hist, pp, packet, at = pattern_fixture(tmp_path)
    alias = tmp_path/'linked_pattern.json'
    try:
        alias.symlink_to(pp)
    except (OSError, NotImplementedError):
        pytest.skip('Host does not permit symlinks')
    with pytest.raises(PatternMemoryError, match='Unsafe'):
        assess(hp, alias, at=at)


def test_global_oversized_pattern_packet_refused(tmp_path):
    hp, hist, pp, packet, at = pattern_fixture(tmp_path)
    pp.write_bytes(b'x'*262145)
    with pytest.raises(PatternMemoryError, match='Unsafe'):
        assess(hp, pp, at=at)


def test_global_history_and_packet_not_mutated(tmp_path):
    hp, hist, pp, packet, at = pattern_fixture(tmp_path)
    before = (sha256(hp.read_bytes()).hexdigest(), sha256(pp.read_bytes()).hexdigest())
    r = assess(hp, pp, at=at)
    after = (sha256(hp.read_bytes()).hexdigest(), sha256(pp.read_bytes()).hexdigest())
    assert before == after
    assert r['files_written'] is False


def test_global_fixed_asof_is_replay_deterministic(tmp_path):
    hp, hist, pp, packet, at = pattern_fixture(tmp_path)
    a = assess(hp, pp, at=at)
    b = assess(hp, pp, at=at)
    assert a == b
    assert a['report_sha256'] == _digest({k:v for k,v in a.items() if k!='report_sha256'})


def test_global_cli_readonly_no_inputs_reports_hold(capsys):
    r = run(['--read-only-pattern', '--synthetic-only', '--epoch-id', EPOCH,
             '--as-of', z(datetime.now(UTC).replace(microsecond=0)),
             '--policy-file', str(POLICY), '--contracts-file', str(CONTRACTS),
             '--manifest-file', str(MANIFEST)])
    assert r == 0
    output = capsys.readouterr().out
    assert 'FINAL=HOLD' in output
    assert 'Cases: 0' in output


def test_global_cli_json_with_packet_still_no_edge(tmp_path, capsys):
    hp, hist, pp, packet, at = pattern_fixture(tmp_path)
    code = run(['--read-only-pattern', '--synthetic-only', '--epoch-id', EPOCH,
                '--as-of', at, '--policy-file', str(POLICY),
                '--contracts-file', str(CONTRACTS), '--manifest-file', str(MANIFEST),
                '--history-file', str(hp), '--pattern-file', str(pp), '--json'])
    assert code == 0
    r = json.loads(capsys.readouterr().out)
    assert r['cases_with_asof_features'] == 8
    assert r['trusted_retrieval_labels'] == 0
    assert r['final_action'] == 'HOLD'


def test_global_cli_requires_both_safety_flags():
    with pytest.raises(SystemExit) as e:
        run(['--epoch-id', EPOCH, '--as-of', z(datetime.now(UTC))])
    assert e.value.code == 2


def test_global_no_models_or_network_connected(tmp_path):
    r = valid(tmp_path)
    for field in ('network_accessed','broker_connected','council_invoked','model_loaded',
                  'training_authorized','paper_orders_authorized','live_orders_authorized',
                  'historical_epoch_rebound','future_epoch_created'):
        assert r[field] is False
    assert r['final_action'] == 'HOLD'

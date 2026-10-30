
"""P10-21..24: fail-closed synthetic ablations, budgets and read-model tests."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest

from backend.runtime.stage8_m5_offline_heartbeat import EPOCH, _digest
from backend.runtime.test_stage10_council_reliability_diversity_design import (
    POLICY, CONTRACTS, MANIFEST, z, save,
)
from backend.runtime.test_stage10_model_registry_promotion_design import (
    registry_fixture, clock,
)
from backend.runtime.stage10_model_registry_promotion_design import assess_stage10_model_registry
from backend.runtime.stage10_complexity_governance_handover import (
    AUDIT_VERSION, READ_MODEL_VERSION, VERSION, METHODS, STRESSES, READ_MODELS,
    ComplexityAuditError, assess_stage10_complexity_handover, run,
)

UTC = timezone.utc


def audit_fixture(tmp_path):
    registry_path, rd, at = registry_fixture(tmp_path)
    registry = assess_stage10_model_registry(
        epoch_id=EPOCH, as_of=at, policy_file=POLICY,
        contracts_file=CONTRACTS, manifest_file=MANIFEST,
        registry_file=registry_path,
    )
    moment = datetime.fromisoformat(at.replace('Z', '+00:00'))
    universe = '7' * 64
    budget = {
        'latency_ms': 1000, 'compute_ms': 3000, 'peak_memory_mb': 1024,
        'human_review_minutes': 20, 'source_family_count': 13,
        'failure_mode_count': 12,
    }
    matrix = []
    for mi, method in enumerate(METHODS):
        for stress in STRESSES:
            row = {
                'method_id': method, 'stress_id': stress,
                'sample_universe_sha256': universe, 'sample_count': 10,
                'missing_count': 2 if stress == 'SOURCE_OUTAGE' else 0,
                'costed_pips_sum_claim': '-5.0000' if stress == 'LIQUIDITY_GAP' else '2.0000',
                'worst_drawdown_pips_claim': '10.0000',
                'loss_tail_pips_claim': '15.0000',
                'average_latency_ms': 500 + mi * 50,
                'average_compute_ms': 1000 + mi * 100,
                'peak_memory_mb': 400 + mi * 100,
                'human_review_minutes': 5 + mi,
                'source_family_count': min(13, 6 + mi),
                'distinct_failure_modes': mi + 1,
                'veto_bypasses': 0,
                'source_class': 'LOCAL_SYNTHETIC_UNVERIFIED',
            }
            row['row_claim_sha256'] = _digest(row)
            matrix.append(row)
    doc = {
        'schema_version': 1, 'historical_epoch_id': EPOCH,
        'packet_id': 'SYNTHETIC_COMPLEXITY_AUDIT_001',
        'mode': 'LOCAL_SYNTHETIC_COMPLEXITY_AUDIT_ONLY',
        'contract_version': AUDIT_VERSION,
        'registry_report_sha256': registry['report_sha256'],
        'declared_at': z(moment - timedelta(days=30)),
        'measurement_start_at': z(moment - timedelta(days=20)),
        'measurement_end_at': z(moment - timedelta(days=19)),
        'final_test_opened': False,
        'negative_results_publication': 'ALL_METHODS_ALL_STRESSES_AND_NEGATIVES',
        'sample_universe_sha256': universe, 'sample_count': 10,
        'budget': budget, 'matrix': matrix,
        'rejected_methods': [
            {'method_id': method, 'reason_code': 'NO_INDEPENDENT_IMPROVEMENT',
             'local_result_status': 'UNVERIFIED_NOT_PROMOTED'}
            for method in METHODS
        ],
        'source_treatment': 'SYNTHETIC_UNATTESTED_NOT_MARKET_EVIDENCE',
        'risk_officer_veto_policy': 'VETO_IS_IMMUTABLE_HOLD',
    }
    audit_path = save(tmp_path / 'local_synthetic_complexity_design.json', doc)
    return registry_path, rd, audit_path, doc, at


def row_rehash(r):
    r['row_claim_sha256'] = _digest({k: v for k,v in r.items() if k != 'row_claim_sha256'})


def assess(registry=None, audit=None, *, at=None, prior=None, policy=POLICY, epoch=EPOCH):
    return assess_stage10_complexity_handover(
        epoch_id=epoch, as_of=at or z(clock()), policy_file=policy,
        contracts_file=CONTRACTS, manifest_file=MANIFEST,
        registry_file=registry, audit_file=audit, prior_audit=prior,
    )


def valid(tmp_path):
    rp, registry, ap, audit, at = audit_fixture(tmp_path)
    return assess(rp, ap, at=at)


def mutation(tmp_path, fn, rehash=False):
    rp, registry, ap, doc, at = audit_fixture(tmp_path)
    fn(doc)
    if rehash:
        for row in doc['matrix']:
            row_rehash(row)
    save(ap, doc)
    with pytest.raises(ComplexityAuditError):
        assess(rp, ap, at=at)


def test_p1021_no_local_audit_still_hold():
    r = assess()
    assert r['registry_status'] == 'NOT_SUPPLIED'
    assert r['audit_status'] == 'NOT_SUPPLIED'
    assert r['independent_research_model_selection_granted'] is False
    assert r['final_action'] == 'HOLD'


def test_p1021_six_methods_six_stresses_complete(tmp_path):
    r = valid(tmp_path)
    assert r['methods_compared_in_local_design'] == list(METHODS)
    assert r['synthetic_stresses_predeclared'] == list(STRESSES)
    assert len(r['complexity_audit']['matrix_rows']) == 36
    assert len(r['complexity_audit']['method_summaries']) == 6
    assert r['complexity_audit']['prospectively_eligible_scorable_rows'] == 0


def test_p1021_all_methods_keep_same_universe_and_denominator(tmp_path):
    r = valid(tmp_path)
    cells = r['complexity_audit']['matrix_rows']
    assert {x['sample_universe_sha256'] for x in cells} == {'7' * 64}
    assert {x['synthetic_sample_count'] for x in cells} == {10}
    assert r['complexity_audit']['stress_condition_missingness']['SOURCE_OUTAGE'] == 2
    assert all(not x['independent_performance_evidence'] for x in cells)


def test_p1021_mandatory_negative_result_disclosure_for_all_methods(tmp_path):
    r = valid(tmp_path)
    assert r['complexity_audit']['negative_results_disclosed_locally'] == 6
    assert all(not m['eligible_for_council_integration']
               for m in r['complexity_audit']['method_summaries'])


def test_p1021_matrix_missing_cell_refused(tmp_path):
    mutation(tmp_path, lambda x: x['matrix'].pop())


def test_p1021_matrix_duplicate_cell_refused(tmp_path):
    mutation(tmp_path, lambda x: x['matrix'].__setitem__(1, deepcopy(x['matrix'][0])))


def test_p1021_duplicate_comparison_under_different_stress_refused(tmp_path):
    mutation(tmp_path, lambda x: x['matrix'][1].update(stress_id=x['matrix'][0]['stress_id']), rehash=True)


def test_p1021_changed_sample_universe_refused(tmp_path):
    mutation(tmp_path, lambda x: x['matrix'][0].update(sample_universe_sha256='f'*64), rehash=True)


def test_p1021_row_changed_without_rehash_refused(tmp_path):
    mutation(tmp_path, lambda x: x['matrix'][0].update(costed_pips_sum_claim='999.0'))


def test_p1021_missingness_cannot_change_by_method(tmp_path):
    mutation(tmp_path, lambda x: x['matrix'][6].update(missing_count=2), rehash=True)


def test_p1021_source_outage_cannot_hide_missingness(tmp_path):
    def changed(doc):
        for cell in doc['matrix']:
            if cell['stress_id'] == 'SOURCE_OUTAGE':
                cell['missing_count'] = 0
    mutation(tmp_path, changed, rehash=True)


def test_p1021_missing_all_cannot_have_profit(tmp_path):
    def changed(doc):
        for cell in doc['matrix']:
            if cell['stress_id'] == 'SOURCE_OUTAGE':
                cell['missing_count'] = 10
    mutation(tmp_path, changed, rehash=True)


def test_p1021_negative_metrics_cannot_be_hidden(tmp_path):
    mutation(tmp_path, lambda x: x['rejected_methods'].pop())


def test_p1021_duplicate_rejection_identity_refused(tmp_path):
    mutation(tmp_path, lambda x: x['rejected_methods'].__setitem__(1,
                                     deepcopy(x['rejected_methods'][0])))


def test_p1021_false_method_approval_refused(tmp_path):
    mutation(tmp_path, lambda x: x['rejected_methods'][0].update(
        local_result_status='PROVEN_PROMOTED'))


def test_p1022_hypothetical_tail_loss_and_worst_stress_are_only_claims(tmp_path):
    r = valid(tmp_path)
    summary = r['complexity_audit']['method_summaries'][0]
    assert summary['local_worst_stress_costed_pips_claim'] == '-5.0000'
    assert summary['independent_complexity_benefit_proven'] is False
    assert 'LOCAL' in r['audit_status']


def test_p1022_budgets_expose_overruns_without_promoting(tmp_path):
    r = valid(tmp_path)
    last = r['complexity_audit']['method_summaries'][-1]
    assert last['method_id'] == 'CHALLENGER_SUBSTITUTION'
    assert 'peak_memory_mb' not in last['local_resource_budget_overrun_fields']
    assert r['local_synthetic_complexity_improvement_claim_accepted'] is False


def test_p1022_synthetic_scores_cannot_become_real_returns(tmp_path):
    r = valid(tmp_path)
    assert r['real_trades_or_broker_receipts'] == 0
    assert r['real_forecasts_registered'] == 0
    assert r['training_executed'] is False


def test_p1022_risk_veto_bypass_forbidden(tmp_path):
    mutation(tmp_path, lambda x: x['matrix'][0].update(veto_bypasses=1), rehash=True)


@pytest.mark.parametrize('field,value', [
    ('source_class', 'BROKER_TRADES_VERIFIED'),
    ('missing_count', -1), ('sample_count', 100),
    ('sample_count', True), ('veto_bypasses', True),
    ('average_latency_ms', -1), ('average_compute_ms', 600001),
    ('peak_memory_mb', 16385), ('source_family_count', 14),
    ('distinct_failure_modes', 65),
    ('worst_drawdown_pips_claim', '-1.0000'),
    ('loss_tail_pips_claim', '-0.5000'),
    ('costed_pips_sum_claim', 'nan'),
    ('costed_pips_sum_claim', 0.1),
])
def test_p1022_invalid_resource_or_result_claim_rejected(tmp_path, field, value):
    mutation(tmp_path, lambda x: x['matrix'][0].update({field: value}), rehash=True)


@pytest.mark.parametrize('field,value', [
    ('mode', 'INDEPENDENTLY_EVALUATED'),
    ('historical_epoch_id', 'f'*64),
    ('contract_version', 'INDEPENDENT_COMPLEXITY_APPROVED'),
    ('registry_report_sha256', 'f'*64),
    ('final_test_opened', True),
    ('negative_results_publication', 'BEST_ONLY'),
    ('risk_officer_veto_policy', 'CAN_OVERRIDE_VETO'),
    ('source_treatment', 'REAL_MARKET_OBSERVED'),
])
def test_p1022_self_authorizing_audit_rejected(tmp_path, field, value):
    mutation(tmp_path, lambda x: x.update({field: value}))


def test_p1022_future_measurement_end_refused(tmp_path):
    mutation(tmp_path, lambda x: x.update(measurement_end_at=z(
        datetime.now(UTC) + timedelta(days=1))))


def test_p1022_post_hoc_audit_declaration_refused(tmp_path):
    mutation(tmp_path, lambda x: x.update(declared_at=x['measurement_end_at']))


def test_p1022_zero_latency_budget_refused(tmp_path):
    mutation(tmp_path, lambda x: x['budget'].update(latency_ms=0))


def test_p1022_invalid_budget_extra_authorization_refused(tmp_path):
    mutation(tmp_path, lambda x: x['budget'].update(order_execution_permitted=True))


def test_p1023_code_inventory_files_have_hashes_but_are_not_external_review(tmp_path):
    r = valid(tmp_path)
    items = r['stage10_code_inventory']
    assert len(items) == 5
    assert all(len(x['local_code_sha256']) == 64 for x in items)
    assert all(not x['independently_reproduced'] for x in items)
    assert r['source_code_hashes_prove_external_review'] is False


def test_p1023_complexity_preference_is_simplicity_until_evidence(tmp_path):
    r = valid(tmp_path)
    assert r['research_handover']['simplification_policy'] == (
        'PREFER_STATIC_SIMPLE_METHOD_UNTIL_INDEPENDENT_GAIN_ESTABLISHED')
    assert r['independent_research_model_selection_granted'] is False


def test_p1023_local_budget_remains_unverified_measurement(tmp_path):
    r = valid(tmp_path)
    assert r['complexity_audit']['budget']['latency_ms'] == 1000
    assert r['complexity_audit']['method_summaries'][0]['independent_complexity_benefit_proven'] is False
    assert r['research_operational_readiness'] == 'BLOCKED_EXTERNAL_EVIDENCE'


def test_p1024_mission_control_read_models_require_hold_banner(tmp_path):
    r = valid(tmp_path)
    assert [x['read_model_id'] for x in r['mission_control_read_model_previews']] == list(READ_MODELS)
    assert all(x['contract_version'] == READ_MODEL_VERSION for x in r['mission_control_read_model_previews'])
    assert all(x['display_status'] == 'NOT_INDEPENDENTLY_VERIFIED' for x in r['mission_control_read_model_previews'])
    assert all(x['must_show_hold_banner'] for x in r['mission_control_read_model_previews'])
    assert all(not x['can_submit_orders_or_change_weights'] for x in r['mission_control_read_model_previews'])


def test_p1024_stage10_handover_is_engineering_not_research_exit(tmp_path):
    r = valid(tmp_path)
    m = r['research_handover']
    assert m['stage10_engineering_contracts_locally_reviewable'] is True
    assert m['stage10_independent_research_exit_met'] is False
    assert m['research_decision'] == 'NOT_ENOUGH_EVIDENCE'
    assert m['production_model_selection_authorized'] is False
    assert m['stage11_operational_or_trading_rights'] is False
    assert m['final_action'] == 'HOLD'


def test_p1024_handover_rejects_unsupported_performance_claims(tmp_path):
    m = valid(tmp_path)['research_handover']
    assert 'INDEPENDENT_RESEARCH_EDGE' in m['rejected_or_unproven_claims']
    assert 'ADAPTIVE_WEIGHT_PROMOTION' in m['rejected_or_unproven_claims']
    assert 'SHADOW_OR_LIVE_TRADING_PERMISSION' in m['rejected_or_unproven_claims']


def test_p1024_registry_tamper_invalidates_audit_binding(tmp_path):
    rp, registry, ap, doc, at = audit_fixture(tmp_path)
    registry['packet_id'] = 'LOCAL_MODEL_METADATA_REGISTRY_002'
    save(rp, registry)
    with pytest.raises(ComplexityAuditError):
        assess(rp, ap, at=at)


def test_p1024_local_audit_snapshot_edit_detected(tmp_path):
    rp, registry, ap, doc, at = audit_fixture(tmp_path)
    old = tmp_path / 'frozen_complexity_audit.json'
    old.write_bytes(ap.read_bytes())
    doc['matrix'][0]['costed_pips_sum_claim'] = '99.0000'
    row_rehash(doc['matrix'][0])
    save(ap, doc)
    with pytest.raises(ComplexityAuditError, match='bytes changed'):
        assess(rp, ap, prior=old, at=at)


def test_p1024_same_local_snapshot_not_external_proof(tmp_path):
    rp, registry, ap, doc, at = audit_fixture(tmp_path)
    prior = tmp_path / 'frozen_complexity_audit.json'
    prior.write_bytes(ap.read_bytes())
    r = assess(rp, ap, prior=prior, at=at)
    assert r['prior_local_audit_file_sha256'] == sha256(ap.read_bytes()).hexdigest()
    assert r['independent_external_gates_verified'] == 0


def test_p1024_prior_same_file_rejected(tmp_path):
    rp, registry, ap, doc, at = audit_fixture(tmp_path)
    with pytest.raises(ComplexityAuditError, match='Distinct previous'):
        assess(rp, ap, prior=ap, at=at)


def test_p1024_audit_without_registry_refused(tmp_path):
    p = save(tmp_path / 'bad.json', {})
    with pytest.raises(ComplexityAuditError, match='requires original'):
        assess(audit=p)


def test_p1024_prior_without_current_refused(tmp_path):
    p = save(tmp_path / 'prior.json', {})
    with pytest.raises(ComplexityAuditError, match='Previous local'):
        assess(prior=p)


def test_p1024_wrong_epoch_refused():
    with pytest.raises(ComplexityAuditError, match='rebinding refused'):
        assess(epoch='f'*64)


def test_p1024_future_cutoff_refused():
    with pytest.raises(ComplexityAuditError, match='Future review'):
        assess(at=z(datetime.now(UTC)+timedelta(days=1)))


def test_p1024_tampered_clock_policy_refused(tmp_path):
    f = tmp_path / 'policy.json'
    f.write_bytes(POLICY.read_bytes()+b' ')
    with pytest.raises(ComplexityAuditError):
        assess(policy=f)


def test_p1024_duplicate_json_keys_refused(tmp_path):
    rp, registry, ap, doc, at = audit_fixture(tmp_path)
    ap.write_text('{"mode":"SAFE","mode":"ORDER_TRADING"}')
    with pytest.raises(ComplexityAuditError):
        assess(rp, ap, at=at)


def test_p1024_oversized_json_refused(tmp_path):
    rp, registry, ap, doc, at = audit_fixture(tmp_path)
    ap.write_bytes(b'X' * 262145)
    with pytest.raises(ComplexityAuditError):
        assess(rp, ap, at=at)


def test_p1024_symlink_refused_if_available(tmp_path):
    rp, registry, ap, doc, at = audit_fixture(tmp_path)
    link = tmp_path / 'link.json'
    try:
        link.symlink_to(ap)
    except (NotImplementedError, OSError):
        pytest.skip('Host symlink restrictions')
    with pytest.raises(ComplexityAuditError):
        assess(rp, link, at=at)


def test_p1024_report_is_reproducible_no_file_mutation(tmp_path):
    rp, registry, ap, doc, at = audit_fixture(tmp_path)
    before = (sha256(rp.read_bytes()).hexdigest(), sha256(ap.read_bytes()).hexdigest())
    a, b = assess(rp, ap, at=at), assess(rp, ap, at=at)
    assert a == b
    assert a['report_sha256'] == _digest({k:v for k,v in a.items() if k != 'report_sha256'})
    assert before == (sha256(rp.read_bytes()).hexdigest(), sha256(ap.read_bytes()).hexdigest())
    assert a['files_written'] is False and a['network_accessed'] is False
    assert a['broker_connected'] is False and a['final_action'] == 'HOLD'


def test_p1024_cli_without_inputs_holds(capsys):
    rc = run(['--read-only-governance', '--synthetic-only', '--epoch-id', EPOCH,
              '--as-of', z(clock()), '--policy-file', str(POLICY),
              '--contracts-file', str(CONTRACTS), '--manifest-file', str(MANIFEST)])
    assert rc == 0
    assert 'FINAL=HOLD' in capsys.readouterr().out


def test_p1024_cli_with_fixture_json_still_hold(tmp_path, capsys):
    rp, registry, ap, doc, at = audit_fixture(tmp_path)
    rc = run(['--read-only-governance', '--synthetic-only', '--epoch-id', EPOCH,
              '--as-of', at, '--policy-file', str(POLICY),
              '--contracts-file', str(CONTRACTS), '--manifest-file', str(MANIFEST),
              '--registry-file', str(rp), '--audit-file', str(ap), '--json'])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report['audit_status'] == 'LOCAL_COMPLETE_SYNTHETIC_DESIGN_MATRIX'
    assert report['independent_research_model_selection_granted'] is False
    assert report['final_action'] == 'HOLD'


def test_p1024_cli_flags_required():
    with pytest.raises(SystemExit) as x:
        run(['--epoch-id', EPOCH, '--as-of', z(clock())])
    assert x.value.code == 2


def test_p1024_cli_future_refused(capsys):
    assert run(['--read-only-governance', '--synthetic-only', '--epoch-id', EPOCH,
                '--as-of', z(datetime.now(UTC)+timedelta(days=1)),
                '--policy-file', str(POLICY)]) == 1
    assert 'BLOCKED' in capsys.readouterr().out

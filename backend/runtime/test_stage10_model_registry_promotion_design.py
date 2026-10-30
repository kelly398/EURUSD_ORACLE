
"""Adversarial P10-17..20 local metadata-only registry tests.

Fixtures may create temporary JSON files. The implementation never starts
training, opens model artifacts, approves a challenger or transmits a request.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest

from backend.runtime.stage8_m5_offline_heartbeat import EPOCH, _digest
from backend.runtime.stage6_council_context_readiness import inspect_static_council
from backend.runtime.stage10_offline_scenario_laboratory import assess_stage10_scenario_lab
from backend.runtime.test_stage10_council_reliability_diversity_design import (
    POLICY, MANIFEST, CONTRACTS, z, save,
)
from backend.runtime.stage10_model_registry_promotion_design import (
    REGISTRY_VERSION, PROMOTION_VERSION, VERSION, ModelRegistryError,
    assess_stage10_model_registry, run,
)

UTC = timezone.utc


def clock():
    return datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=5)


def metadata(now, index):
    start = now - timedelta(days=70)
    ending = now - timedelta(days=60)
    item = {
        'candidate_id': 'LOCAL_MODEL_A' if index == 0 else 'LOCAL_MODEL_B',
        'candidate_version': 'CANDIDATE_001',
        'model_family_id': 'LINEAR' if index == 0 else 'TREE',
        'algorithm': 'LINEAR_LOGISTIC' if index == 0 else 'SHALLOW_TREE',
        'purpose': 'PROBABILITY_UP_RESEARCH',
        'horizon_seconds': 3600,
        'artifact_digest_claim': 'a' * 64 if index == 0 else 'b' * 64,
        'artifact_format': 'NONE',
        'training_code_digest_claim': 'c' * 64,
        'training_data_digest_claim': 'd' * 64,
        'training_window_start_at': z(start),
        'training_window_end_at': z(ending),
        'training_data_last_available_at': z(ending + timedelta(hours=1)),
        'feature_schema_sha256': 'e' * 64,
        'feature_snapshot_sha256': 'f' * 64,
        'hyperparameters': {
            'learning_rate': '0.0100', 'regularization': '0.1000',
            'max_depth': 3, 'seed': 42,
        },
        'created_at': z(now - timedelta(days=55)),
        'training_job_status': 'NOT_EXECUTED',
        'approver_id_claim': 'REVIEWER_NOT_APPROVED',
        'approval_status': 'NOT_APPROVED',
    }
    item['candidate_metadata_sha256'] = _digest(item)
    return item


def refreeze(item):
    item['candidate_metadata_sha256'] = _digest({
        k: v for k, v in item.items() if k != 'candidate_metadata_sha256'
    })


def comparison(now):
    item = {
        'comparison_id': 'LOCAL_COMPARISON_001',
        'candidate_id': 'LOCAL_MODEL_B', 'incumbent_id': 'LOCAL_MODEL_A',
        'window_start_at': z(now - timedelta(days=50)),
        'window_end_at': z(now - timedelta(days=49)),
        'reported_at': z(now - timedelta(days=48)),
        'horizon_seconds': 3600,
        'sample_universe_sha256': '1' * 64,
        'synthetic_pairs_claimed': 100, 'missing_pairs_claimed': 10,
        'candidate_brier_claim': '0.2400', 'incumbent_brier_claim': '0.2500',
        'feature_psi_claim': '0.1000', 'calibration_shift_claim': '0.0200',
        'feature_compatibility': 'COMPATIBLE_LOCAL_CLAIM',
        'residual_drift_status': 'LOCAL_DRIFT_CLAIM',
        'comparison_status': 'LOCAL_DESCRIPTIVE_NOT_SHADOW_DEPLOYMENT',
        'independent_outcomes_verified': False,
    }
    item['comparison_claim_sha256'] = _digest(item)
    return item


def recompare(item):
    item['comparison_claim_sha256'] = _digest({
        k: v for k, v in item.items() if k != 'comparison_claim_sha256'
    })


def registry_fixture(tmp_path, *, comparisons=True, proposed=True):
    at = clock()
    as_of = z(at)
    scenario = assess_stage10_scenario_lab(
        epoch_id=EPOCH, as_of=as_of, policy_file=POLICY,
        contracts_file=CONTRACTS, manifest_file=MANIFEST,
    )
    council = inspect_static_council(contracts_file=CONTRACTS, manifest_file=MANIFEST)
    fallback = {
        'fallback_id': 'STATIC_COUNCIL_ROSTER_V1',
        'fallback_contracts_sha256': council['contracts_sha256'],
        'fallback_manifest_sha256': council['manifest_sha256'],
        'artifact_availability_verified': False,
        'recovery_activation_permitted': False,
    }
    fallback['fallback_sha256'] = _digest(fallback)
    doc = {
        'schema_version': 1, 'historical_epoch_id': EPOCH,
        'packet_id': 'LOCAL_MODEL_METADATA_REGISTRY_001',
        'contract_version': REGISTRY_VERSION,
        'mode': 'SYNTHETIC_MODEL_REGISTRY_DESIGN_ONLY',
        'scenario_report_sha256': scenario['report_sha256'],
        'declared_at': z(at - timedelta(days=80)),
        'final_test_opened': False, 'training_enabled': False,
        'negative_results_publication': 'PUBLISH_ALL_NEGATIVE_AND_ABANDONED',
        'candidates': [metadata(at, 0), metadata(at, 1)],
        'comparisons': [comparison(at)] if comparisons else [],
        'promotion_request': {
            'contract_version': PROMOTION_VERSION,
            'request_transmitted': False, 'signature_verified': False,
            'independent_reviewer_accepted': False,
            'operator_authorization_claim': False,
            'candidate_requested': 'LOCAL_MODEL_B' if proposed else None,
            'request_status': 'BLOCKED_EXTERNAL_EVIDENCE',
            'requires_independent_holdout': True,
            'model_or_weight_switch_performed': False,
            'rollback_pointer_changed': False,
        },
        'fixed_rollback': fallback,
    }
    path = save(tmp_path / 'local_model_registry_design.json', doc)
    return path, doc, as_of


def assess(registry=None, *, at=None, prior=None, epoch=EPOCH, policy=POLICY):
    return assess_stage10_model_registry(
        epoch_id=epoch, as_of=at or z(clock()), policy_file=policy,
        contracts_file=CONTRACTS, manifest_file=MANIFEST,
        registry_file=registry, prior_registry=prior,
    )


def valid(tmp_path, **options):
    fp, doc, at = registry_fixture(tmp_path, **options)
    return assess(fp, at=at)


def mutation(tmp_path, mutate, *, rehash=False):
    path, doc, at = registry_fixture(tmp_path)
    mutate(doc)
    if rehash:
        for model in doc['candidates']:
            refreeze(model)
        for item in doc['comparisons']:
            recompare(item)
        fallback = doc['fixed_rollback']
        fallback['fallback_sha256'] = _digest({k:v for k,v in fallback.items()
                                               if k != 'fallback_sha256'})
    save(path, doc)
    with pytest.raises(ModelRegistryError):
        assess(path, at=at)


def test_p1017_missing_registry_is_not_a_model_registry():
    r = assess()
    assert r['registry_status'] == 'NOT_SUPPLIED'
    assert r['candidate_metadata_count'] == 0
    assert r['trained_models_registered'] == 0
    assert r['final_action'] == 'HOLD'


def test_p1017_frozen_metadata_binds_code_data_features_approver(tmp_path):
    r = valid(tmp_path)
    assert r['registry_status'] == 'LOCAL_SYNTHETIC_METADATA_ONLY'
    assert r['candidate_metadata_count'] == 2
    assert len(r['candidate_metadata']) == 2
    m = r['candidate_metadata'][0]
    assert len(m['candidate_metadata_sha256']) == 64
    assert len(m['training_code_digest_claim']) == 64
    assert len(m['training_data_digest_claim']) == 64
    assert len(m['feature_schema_sha256']) == 64
    assert m['approver_claim_independently_verified'] is False
    assert m['metadata_only_not_a_verified_artifact'] is True


def test_p1017_no_artifacts_read_or_models_run(tmp_path):
    r = valid(tmp_path)
    assert r['model_artifact_files_read'] == 0
    assert r['model_artifact_bytes_verified'] == 0
    assert r['training_executed'] is False
    assert r['model_loaded'] is False
    assert r['operational_weights_promoted'] == 0


def test_p1017_duplicate_candidate_id_rejected(tmp_path):
    mutation(tmp_path, lambda d: d['candidates'][1].update(
        candidate_id=d['candidates'][0]['candidate_id']), rehash=True)


def test_p1017_duplicate_candidate_content_rejected(tmp_path):
    mutation(tmp_path, lambda d: d['candidates'].append(deepcopy(d['candidates'][0])))


def test_p1017_content_edit_without_rehash_rejected(tmp_path):
    mutation(tmp_path, lambda d: d['candidates'][0].update(
        artifact_digest_claim='3' * 64))


def test_p1017_rehashed_metadata_with_future_training_rejected(tmp_path):
    mutation(tmp_path, lambda d: d['candidates'][0].update(
        training_window_end_at=z(clock() + timedelta(days=1))), rehash=True)


def test_p1017_training_data_cannot_arrive_after_candidate_creation(tmp_path):
    mutation(tmp_path, lambda d: d['candidates'][0].update(
        training_data_last_available_at=z(clock())), rehash=True)


def test_p1017_candidate_must_follow_registry_declaration(tmp_path):
    mutation(tmp_path, lambda d: d.update(declared_at=z(clock())), rehash=True)


@pytest.mark.parametrize('field,value', [
    ('mode', 'LIVE_REGISTRY'), ('historical_epoch_id', 'f' * 64),
    ('training_enabled', True), ('final_test_opened', True),
    ('scenario_report_sha256', 'f' * 64),
    ('negative_results_publication', 'ONLY_BEST_CHALLENGER'),
    ('contract_version', 'SIGNED_APPROVED'),
])
def test_p1017_self_authorizing_registry_rejected(tmp_path, field, value):
    mutation(tmp_path, lambda d: d.update({field: value}))


@pytest.mark.parametrize('field,value', [
    ('training_job_status', 'TRAINING_COMPLETE'),
    ('approval_status', 'APPROVED'),
    ('artifact_format', 'PICKLE_EXECUTABLE'),
    ('horizon_seconds', 30), ('horizon_seconds', True),
    ('algorithm', 'UNBOUNDED_TRANSFORMER'),
    ('purpose', 'ORDER_EXECUTION'),
])
def test_p1018_training_or_promotion_claim_is_rejected(tmp_path, field, value):
    mutation(tmp_path, lambda d: d['candidates'][0].update({field: value}), rehash=True)


@pytest.mark.parametrize('field,value', [
    ('learning_rate', 'nan'), ('learning_rate', '-0.1'),
    ('regularization', '1000.0'), ('max_depth', 50),
    ('max_depth', True), ('seed', -1), ('seed', 3.5),
])
def test_p1018_unbounded_hyperparameter_rejected(tmp_path, field, value):
    mutation(tmp_path, lambda d: d['candidates'][0]['hyperparameters'].update(
        {field: value}), rehash=True)


def test_p1018_training_data_window_reversed_rejected(tmp_path):
    mutation(tmp_path, lambda d: d['candidates'][0].update(
        training_window_start_at=z(clock() - timedelta(days=50))), rehash=True)


def test_p1018_neither_binary_nor_training_code_is_loaded(tmp_path):
    r = valid(tmp_path)
    assert all(not m['training_or_model_execution_permitted'] for m in r['candidate_metadata'])
    assert r['training_job_started'] is False


def test_p1019_local_brier_difference_is_not_holdout_result(tmp_path):
    r = valid(tmp_path)
    c = r['local_unverified_comparisons'][0]
    assert c['brier_improvement_local_claim_only'] == '0.0100'
    assert c['improvement_established'] is False
    assert c['independently_scorable_pairs'] == 0
    assert c['shadow_cycle_dispatched'] is False


def test_p1019_no_comparisons_cannot_infer_drift(tmp_path):
    r = valid(tmp_path, comparisons=False)
    assert r['comparison_count'] == 0
    assert r['independent_drift_evaluations'] == 0


def test_p1019_comparison_metric_tamper_rejected(tmp_path):
    mutation(tmp_path, lambda d: d['comparisons'][0].update(candidate_brier_claim='0.0100'))


def test_p1019_comparison_before_training_embargo_rejected(tmp_path):
    mutation(tmp_path, lambda d: d['comparisons'][0].update(
        window_start_at=z(clock() - timedelta(days=61))), rehash=True)


def test_p1019_comparison_unknown_model_rejected(tmp_path):
    mutation(tmp_path, lambda d: d['comparisons'][0].update(candidate_id='FAKE_MODEL'), rehash=True)


def test_p1019_same_candidate_as_incumbent_rejected(tmp_path):
    mutation(tmp_path, lambda d: d['comparisons'][0].update(
        candidate_id='LOCAL_MODEL_A'), rehash=True)


def test_p1019_comparison_future_report_refused(tmp_path):
    mutation(tmp_path, lambda d: d['comparisons'][0].update(
        reported_at=z(clock() + timedelta(days=1))), rehash=True)


def test_p1019_evaluation_horizon_cannot_change(tmp_path):
    mutation(tmp_path, lambda d: d['comparisons'][0].update(horizon_seconds=900), rehash=True)


def test_p1019_incompatible_schema_cannot_score(tmp_path):
    mutation(tmp_path, lambda d: d['comparisons'][0].update(
        feature_compatibility='SCHEMA_MISMATCH'), rehash=True)


def test_p1019_missing_outcomes_cannot_be_zero_score(tmp_path):
    def change(d):
        comp = d['comparisons'][0]
        comp['synthetic_pairs_claimed'] = 0
    mutation(tmp_path, change, rehash=True)


def test_p1019_no_results_must_use_null_metrics(tmp_path):
    path, doc, at = registry_fixture(tmp_path)
    comp = doc['comparisons'][0]
    comp['synthetic_pairs_claimed'] = 0
    for field in ('candidate_brier_claim', 'incumbent_brier_claim',
                  'feature_psi_claim', 'calibration_shift_claim'):
        comp[field] = None
    recompare(comp)
    save(path, doc)
    r = assess(path, at=at)
    assert r['local_unverified_comparisons'][0]['brier_improvement_local_claim_only'] is None


def test_p1019_duplicate_comparison_id_refused(tmp_path):
    mutation(tmp_path, lambda d: d['comparisons'].append(deepcopy(d['comparisons'][0])))


def test_p1019_hidden_independent_status_refused(tmp_path):
    mutation(tmp_path, lambda d: d['comparisons'][0].update(
        independent_outcomes_verified=True), rehash=True)


@pytest.mark.parametrize('field,value', [
    ('request_transmitted', True), ('signature_verified', True),
    ('independent_reviewer_accepted', True), ('operator_authorization_claim', True),
    ('request_status', 'PROMOTED'), ('requires_independent_holdout', False),
    ('model_or_weight_switch_performed', True), ('rollback_pointer_changed', True),
    ('candidate_requested', 'INVENTED_MODEL'),
])
def test_p1020_self_promotion_or_unsafe_request_refused(tmp_path, field, value):
    mutation(tmp_path, lambda d: d['promotion_request'].update({field: value}))


@pytest.mark.parametrize('field,value', [
    ('fallback_id', 'NEW_UNVERIFIED_MODEL'),
    ('fallback_contracts_sha256', '9' * 64),
    ('fallback_manifest_sha256', '8' * 64),
    ('artifact_availability_verified', True),
    ('recovery_activation_permitted', True),
])
def test_p1020_fallback_rewrite_or_activation_rejected(tmp_path, field, value):
    mutation(tmp_path, lambda d: d['fixed_rollback'].update({field: value}), rehash=True)


def test_p1020_local_rollback_digest_edit_detected(tmp_path):
    mutation(tmp_path, lambda d: d['fixed_rollback'].update(fallback_sha256='f' * 64))


def test_p1020_valid_request_is_unsent_and_unapproved(tmp_path):
    r = valid(tmp_path)
    p = r['promotion_preview']
    assert p['requested_candidate'] == 'LOCAL_MODEL_B'
    assert p['transmitted'] is False
    assert p['research_promotion_granted'] is False
    assert p['execution_permission_granted'] is False
    assert r['fixed_rollback_contract_sha256']
    assert r['rollback_or_weight_activation'] is False


def test_global_prior_registry_snapshot_detects_rehashed_edits(tmp_path):
    p, doc, at = registry_fixture(tmp_path)
    old = tmp_path / 'prior_registry.json'
    old.write_bytes(p.read_bytes())
    doc['comparisons'][0]['candidate_brier_claim'] = '0.2500'
    recompare(doc['comparisons'][0])
    save(p, doc)
    with pytest.raises(ModelRegistryError, match='changed after frozen'):
        assess(p, at=at, prior=old)


def test_global_matching_previous_bytes_do_not_prove_custody(tmp_path):
    p, doc, at = registry_fixture(tmp_path)
    prior = tmp_path / 'prior_registry.json'
    prior.write_bytes(p.read_bytes())
    r = assess(p, at=at, prior=prior)
    assert r['prior_registry_file_sha256'] == sha256(p.read_bytes()).hexdigest()
    assert not r['local_registry_claims_are_external_receipts']


def test_global_prior_same_path_rejected(tmp_path):
    p, doc, at = registry_fixture(tmp_path)
    with pytest.raises(ModelRegistryError, match='Distinct prior'):
        assess(p, at=at, prior=p)


def test_global_prior_without_current_refused(tmp_path):
    p, doc, at = registry_fixture(tmp_path)
    with pytest.raises(ModelRegistryError, match='Previous local registry'):
        assess(prior=p, at=at)


def test_global_wrong_epoch_refused():
    with pytest.raises(ModelRegistryError, match='rebinding refused'):
        assess(epoch='f' * 64)


def test_global_future_cutoff_refused():
    with pytest.raises(ModelRegistryError, match='Future review'):
        assess(at=z(datetime.now(UTC) + timedelta(days=1)))


def test_global_tampered_historical_policy_refused(tmp_path):
    path = tmp_path / 'policy.json'
    path.write_bytes(POLICY.read_bytes() + b' ')
    with pytest.raises(ModelRegistryError):
        assess(policy=path)


def test_global_duplicate_json_key_rejected(tmp_path):
    p, doc, at = registry_fixture(tmp_path)
    p.write_text('{"mode":"SAFE","mode":"BROKER_TRADING"}')
    with pytest.raises(ModelRegistryError):
        assess(p, at=at)


def test_global_oversized_file_rejected(tmp_path):
    p, doc, at = registry_fixture(tmp_path)
    p.write_bytes(b'x' * 262145)
    with pytest.raises(ModelRegistryError):
        assess(p, at=at)


def test_global_symlink_refused_if_available(tmp_path):
    p, doc, at = registry_fixture(tmp_path)
    link = tmp_path / 'alias.json'
    try:
        link.symlink_to(p)
    except (OSError, NotImplementedError):
        pytest.skip('Windows cannot create symlink')
    with pytest.raises(ModelRegistryError):
        assess(link, at=at)


def test_global_files_unmodified_and_report_deterministic(tmp_path):
    p, doc, at = registry_fixture(tmp_path)
    original = sha256(p.read_bytes()).hexdigest()
    x, y = assess(p, at=at), assess(p, at=at)
    assert x == y
    assert x['report_sha256'] == _digest({k: v for k, v in x.items() if k != 'report_sha256'})
    assert original == sha256(p.read_bytes()).hexdigest()
    assert x['files_written'] is False
    assert x['network_accessed'] is False
    assert x['broker_connected'] is False
    assert x['final_action'] == 'HOLD'


def test_global_cli_no_registry_hold(capsys):
    assert run(['--read-only-registry', '--synthetic-only', '--epoch-id', EPOCH,
                '--as-of', z(clock()), '--policy-file', str(POLICY),
                '--contracts-file', str(CONTRACTS), '--manifest-file', str(MANIFEST)]) == 0
    assert 'FINAL=HOLD' in capsys.readouterr().out


def test_global_cli_with_registry_json(tmp_path, capsys):
    p, doc, at = registry_fixture(tmp_path)
    assert run(['--read-only-registry', '--synthetic-only', '--epoch-id', EPOCH,
                '--as-of', at, '--policy-file', str(POLICY),
                '--contracts-file', str(CONTRACTS), '--manifest-file', str(MANIFEST),
                '--registry-file', str(p), '--json']) == 0
    r = json.loads(capsys.readouterr().out)
    assert r['candidate_metadata_count'] == 2
    assert r['training_executed'] is False
    assert r['final_action'] == 'HOLD'


def test_global_cli_flags_required():
    with pytest.raises(SystemExit) as exc:
        run(['--epoch-id', EPOCH, '--as-of', z(clock())])
    assert exc.value.code == 2


def test_global_cli_future_cutoff_blocked(capsys):
    assert run(['--read-only-registry', '--synthetic-only', '--epoch-id', EPOCH,
                '--as-of', z(datetime.now(UTC) + timedelta(days=1)),
                '--policy-file', str(POLICY)]) == 1
    assert 'BLOCKED' in capsys.readouterr().out


def test_p1018_registry_declaration_after_training_start_refused(tmp_path):
    mutation(tmp_path, lambda d: d.update(declared_at=z(clock() - timedelta(days=65))))


def test_p1019_comparison_cannot_predate_candidate_metadata_creation(tmp_path):
    mutation(tmp_path, lambda d: d['candidates'][1].update(
        created_at=z(clock() - timedelta(days=49))), rehash=True)


def test_p1019_complete_challenger_matrix_required_no_selective_candidates(tmp_path):
    def add_candidate(doc):
        third = deepcopy(doc['candidates'][1])
        third['candidate_id'] = 'LOCAL_MODEL_C'
        third['model_family_id'] = 'ALTERNATE_TREE'
        refreeze(third)
        doc['candidates'].append(third)
    mutation(tmp_path, add_candidate)


def test_p1019_common_incumbent_required_for_every_challenger(tmp_path):
    def tamper(doc):
        third = deepcopy(doc['candidates'][1])
        third['candidate_id'] = 'LOCAL_MODEL_C'
        third['model_family_id'] = 'ALTERNATE_TREE'
        refreeze(third)
        doc['candidates'].append(third)
        comp = deepcopy(doc['comparisons'][0])
        comp['comparison_id'] = 'LOCAL_COMPARISON_002'
        comp['candidate_id'] = 'LOCAL_MODEL_C'
        comp['incumbent_id'] = 'LOCAL_MODEL_B'
        recompare(comp)
        doc['comparisons'].append(comp)
    mutation(tmp_path, tamper)


def test_p1019_all_challengers_identical_universe_mandatory(tmp_path):
    def tamper(doc):
        third = deepcopy(doc['candidates'][1])
        third['candidate_id'] = 'LOCAL_MODEL_C'
        third['model_family_id'] = 'ALTERNATE_TREE'
        refreeze(third)
        doc['candidates'].append(third)
        comp = deepcopy(doc['comparisons'][0])
        comp['comparison_id'] = 'LOCAL_COMPARISON_002'
        comp['candidate_id'] = 'LOCAL_MODEL_C'
        comp['sample_universe_sha256'] = '2'*64
        recompare(comp)
        doc['comparisons'].append(comp)
    mutation(tmp_path, tamper)


def test_p1019_all_challengers_same_denominator_mandatory(tmp_path):
    def tamper(doc):
        third = deepcopy(doc['candidates'][1])
        third['candidate_id'] = 'LOCAL_MODEL_C'
        third['model_family_id'] = 'ALTERNATE_TREE'
        refreeze(third)
        doc['candidates'].append(third)
        comp = deepcopy(doc['comparisons'][0])
        comp['comparison_id'] = 'LOCAL_COMPARISON_002'
        comp['candidate_id'] = 'LOCAL_MODEL_C'
        comp['missing_pairs_claimed'] = 100
        recompare(comp)
        doc['comparisons'].append(comp)
    mutation(tmp_path, tamper)


def test_p1019_two_complete_challengers_allowed_as_untrusted_local_claim(tmp_path):
    path, doc, at = registry_fixture(tmp_path)
    third = deepcopy(doc['candidates'][1])
    third['candidate_id'] = 'LOCAL_MODEL_C'
    third['model_family_id'] = 'ALTERNATE_TREE'
    refreeze(third)
    doc['candidates'].append(third)
    comp = deepcopy(doc['comparisons'][0])
    comp['comparison_id'] = 'LOCAL_COMPARISON_002'
    comp['candidate_id'] = 'LOCAL_MODEL_C'
    recompare(comp)
    doc['comparisons'].append(comp)
    save(path, doc)
    report = assess(path, at=at)
    assert report['candidate_metadata_count'] == 3
    assert report['comparison_count'] == 2
    assert not report['local_unverified_comparisons'][1]['improvement_established']


def test_global_registry_is_hash_bound_to_full_synthetic_scenario_chain(tmp_path):
    from backend.runtime.test_stage10_offline_scenario_laboratory import scenario_fixture
    hp, history, pp, pattern, sp, scenario, at = scenario_fixture(tmp_path, steps=3, paths=2)
    upstream = assess_stage10_scenario_lab(
        epoch_id=EPOCH, as_of=at, policy_file=POLICY,
        history_file=hp, pattern_file=pp, scenario_file=sp,
        contracts_file=CONTRACTS, manifest_file=MANIFEST,
    )
    rp, doc, unused_at = registry_fixture(tmp_path)
    doc['scenario_report_sha256'] = upstream['report_sha256']
    save(rp, doc)
    result = assess_stage10_model_registry(
        epoch_id=EPOCH, as_of=at, policy_file=POLICY,
        history_file=hp, pattern_file=pp, scenario_file=sp,
        registry_file=rp, contracts_file=CONTRACTS, manifest_file=MANIFEST,
    )
    assert result['stage10_scenario_status'] == 'LOCAL_SYNTHETIC_UNATTESTED'
    assert result['candidate_metadata_count'] == 2
    assert result['training_executed'] is False
    assert result['final_action'] == 'HOLD'


def test_global_changed_upstream_scenario_invalidates_registry(tmp_path):
    from backend.runtime.test_stage10_offline_scenario_laboratory import scenario_fixture
    hp, history, pp, pattern, sp, scenario, at = scenario_fixture(tmp_path, steps=3, paths=2)
    upstream = assess_stage10_scenario_lab(
        epoch_id=EPOCH, as_of=at, policy_file=POLICY,
        history_file=hp, pattern_file=pp, scenario_file=sp,
        contracts_file=CONTRACTS, manifest_file=MANIFEST,
    )
    rp, doc, unused_at = registry_fixture(tmp_path)
    doc['scenario_report_sha256'] = upstream['report_sha256']
    save(rp, doc)
    scenario['seed'] += 1
    save(sp, scenario)
    with pytest.raises(ModelRegistryError):
        assess_stage10_model_registry(
            epoch_id=EPOCH, as_of=at, policy_file=POLICY,
            history_file=hp, pattern_file=pp, scenario_file=sp,
            registry_file=rp, contracts_file=CONTRACTS, manifest_file=MANIFEST,
        )

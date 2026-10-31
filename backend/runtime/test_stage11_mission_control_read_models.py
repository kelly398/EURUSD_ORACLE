
"""P11-01..04: offline read-model contracts, provenance and safety tests.

Temporary files are synthetic fixtures. No real provider, Council or trading
component is imported, contacted, executed or treated as independently trusted.
"""

import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime.stage11_mission_control_read_models import (
    VERSION, SCHEMA_VERSION, PACKET_VERSION, EPOCH, POLICY_PATH,
    POLICY_SHA256, FAMILIES, GATES, ROLES, RECORD_TYPES,
    MissionControlReadModelError, _digest, assess_stage11_read_models, run,
)

UTC = timezone.utc
POLICY = Path(POLICY_PATH)
T = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=7)


def z(value):
    return value.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def save(path, doc):
    path.write_text(json.dumps(doc, sort_keys=True, separators=(',', ':')),
                    encoding='utf-8')
    return path


def payload(kind):
    common = {
        'EPOCH': {'epoch_state': 'HISTORICAL', 'operating_mode': 'OFFLINE_REPLAY'},
        'DECISION': {'cycle_status': 'HELD', 'research_direction': 'HOLD',
                     'risk_veto': None, 'm5_close_at_utc': z(T - timedelta(minutes=5))},
        'SOURCE_FAMILY': {'family_id': 'candles', 'provider_reference': 'LOCAL_PROVIDER_REF',
                          'first_available_at_utc': z(T - timedelta(minutes=3)),
                          'revision_status': 'NONE', 'clock_uncertainty_ms': '120'},
        'MARKET_SNAPSHOT': {'symbol': 'EURUSD', 'timeframe': 'M5',
                            'bar_close_at_utc': z(T - timedelta(minutes=5)),
                            'mid_price': '1.09250', 'source_family_id': 'candles'},
        'SEAT_VOTE': {'seat_id': 'technical_001', 'direction': 'BUY',
                      'input_complete': False, 'confidence': '0.7000',
                      'source_family_ids': ['candles', 'ticks']},
        'GOVERNANCE_VETO': {'seat_id': 'risk_officer', 'veto_state': 'VETOED',
                            'reason_code': 'MISSING_ATTESTATION'},
        'FORECAST': {'prediction_id': 'LOCAL_FORECAST_001',
                     'forecast_status': 'LOCAL_UNREGISTERED',
                     'horizon_seconds': 3600, 'probability_up': '0.6500'},
        'OUTCOME': {'prediction_id': 'LOCAL_FORECAST_001',
                    'maturity_status': 'MATURED_UNVERIFIED',
                    'hypothetical_costed_pips': '-2.3000'},
        'MODEL': {'candidate_id': 'LOCAL_MODEL_001', 'candidate_state': 'LOCAL_METADATA_ONLY',
                  'active_approved': False},
        'EVALUATION_PROTOCOL': {'experiment_id': 'LOCAL_EXPERIMENT_001',
                                'candidate_sha256': 'a' * 64,
                                'protocol_state': 'LOCAL_UNVERIFIED'},
        'GATE': {'gate_id': 'G01', 'gate_status': 'NOT_VERIFIED',
                 'receipt_digest_claim': 'b' * 64},
        'INCIDENT': {'severity': 'WARNING', 'reason_code': 'CLOCK_GAP',
                     'incident_state': 'OPEN'},
    }
    return deepcopy(common[kind])


def record(kind, *, ident=None, at=T, observed=None, content=None,
           evidence='HASH_MATCHED', parent_sha=None):
    obj = {
        'record_type': kind,
        'record_id': ident or kind + '_01',
        'as_of_utc': z(at),
        'observed_at_utc': z(at - timedelta(minutes=3) if observed is None else observed),
        'origin_report_sha256': parent_sha,
        'evidence_status': evidence,
        'payload': payload(kind) if content is None else content,
    }
    obj['record_sha256'] = _digest(obj)
    return obj


def refreeze_rec(obj):
    obj['record_sha256'] = _digest({k: v for k, v in obj.items()
                                  if k != 'record_sha256'})


def fixture(tmp_path, *, kinds=(), parent=None, now=T):
    rows = [record(kind, ident=kind + '_001', at=now,
                   parent_sha=parent['report_sha256'] if parent else None)
            for kind in kinds]
    doc = {
        'schema_version': 1,
        'contract_version': PACKET_VERSION,
        'historical_epoch_id': EPOCH,
        'packet_id': 'LOCAL_MISSION_CONTROL_EXPORT_001',
        'mode': 'LOCAL_UNATTESTED_OFFLINE_EXPORT_ONLY',
        'exported_at_utc': z(now + timedelta(seconds=5)),
        'policy_sha256': POLICY_SHA256,
        'parent_stage10_report_sha256': parent['report_sha256'] if parent else None,
        'independent_attestation_verified': False,
        'operational_permissions': [],
        'records': rows,
    }
    doc['packet_sha256'] = _digest(doc)
    return save(tmp_path / 'local_mission_control_packet.json', doc), doc


def refreeze(doc):
    doc['packet_sha256'] = _digest({k: v for k, v in doc.items()
                                   if k != 'packet_sha256'})


def fake_stage10(tmp_path, at=T):
    doc = {
        'version': 'EURUSD_STAGE10_COMPLEXITY_GOVERNANCE_HANDOVER_P10_21_24_V1',
        'scope': 'P10_21_TO_P10_24_COMPLEXITY_GOVERNANCE_AND_HANDOVER',
        'as_of': at.isoformat(),
        'historical_epoch_id': EPOCH,
        'historical_policy_sha256': POLICY_SHA256,
        'final_action': 'HOLD', 'read_only': True,
        'files_written': False, 'broker_connected': False,
        'network_accessed': False, 'training_executed': False,
        'model_loaded': False, 'council_invoked': False,
        'paper_orders_authorized': False, 'live_orders_authorized': False,
        'independent_research_model_selection_granted': False,
        'research_operational_readiness': 'BLOCKED_EXTERNAL_EVIDENCE',
        'independent_external_gates_verified': 0,
        'independent_source_families_verified': 0,
        'research_handover': {
            'research_decision': 'NOT_ENOUGH_EVIDENCE',
            'stage10_independent_research_exit_met': False,
        },
    }
    doc['report_sha256'] = _digest(doc)
    return save(tmp_path / 'local_stage10_handover.json', doc), doc


def assess(snapshot=None, *, at=None, prior=None, stage10=None,
           role='VIEWER', policy=POLICY, epoch=EPOCH):
    return assess_stage11_read_models(
        epoch_id=epoch, as_of=z(T + timedelta(minutes=2)) if at is None else at,
        policy_file=policy, snapshot_file=snapshot, prior_snapshot=prior,
        stage10_report_file=stage10, role_preview=role,
    )


def mutate(tmp_path, fn, *, kinds=('SOURCE_FAMILY',), inner=False):
    path, doc = fixture(tmp_path, kinds=kinds)
    fn(doc)
    if inner:
        for row in doc['records']:
            refreeze_rec(row)
    refreeze(doc)
    save(path, doc)
    with pytest.raises(MissionControlReadModelError):
        assess(path)


# P11-01: stable, semantically safe evidence DTO inventory.

def test_p1101_missing_report_has_twelve_empty_versioned_models():
    report = assess()
    assert report['version'] == VERSION
    assert report['snapshot_status'] == 'NOT_SUPPLIED'
    assert [x['record_type'] for x in report['read_model_inventory']] == list(RECORD_TYPES)
    assert len(report['read_model_inventory']) == 12
    assert all(x['local_records_present'] == 0 for x in report['read_model_inventory'])
    assert report['final_action'] == 'HOLD'


def test_p1101_all_twelve_types_validate_and_keep_causal_classification(tmp_path):
    path, doc = fixture(tmp_path, kinds=RECORD_TYPES)
    r = assess(path)
    assert r['record_count'] == 12
    assert {x['record_type'] for x in r['records']} == set(RECORD_TYPES)
    assert all(x['schema_version'] == SCHEMA_VERSION for x in r['records'])
    assert all(x['external_attestation_verified'] is False for x in r['records'])
    assert all(x['permission_to_trade'] is False for x in r['records'])


def test_p1101_unknown_values_are_null_not_numeric_zeros():
    r = assess()
    assert r['latest_verified_market_price'] is None
    assert r['latest_verified_m5_cycle'] is None
    assert r['verified_performance_metrics'] is None
    assert r['latest_research_direction_untrusted'] is None
    assert r['missing_numeric_values_are_null_not_zero'] is True
    assert r['independently_verified_external_gates'] == 0


def test_p1101_all_source_family_and_gate_slots_exist_unverified():
    r = assess()
    assert [x['identifier'] for x in r['source_family_board']] == list(FAMILIES)
    assert [x['identifier'] for x in r['evidence_gate_board']] == list(GATES)
    assert all(x['evidence_status'] == 'NOT_SUPPLIED' for x in r['source_family_board'])
    assert all(x['evidence_status'] == 'NOT_SUPPLIED' for x in r['evidence_gate_board'])


def test_p1101_local_hash_matches_but_not_external_attestation(tmp_path):
    path, doc = fixture(tmp_path, kinds=('SOURCE_FAMILY',))
    r = assess(path)
    source = r['source_family_board'][1]
    assert source['identifier'] == 'candles'
    assert source['evidence_status'] == 'HASH_MATCHED'
    assert source['independently_verified'] is False
    assert r['independently_verified_source_families'] == 0
    assert r['records'][0]['local_checksum_matches'] is True
    assert r['records'][0]['display_watermark'] == 'LOCAL UNVERIFIED'


def test_p1101_local_forecast_direction_not_tradable(tmp_path):
    path, doc = fixture(tmp_path, kinds=('DECISION', 'FORECAST'))
    doc['records'][0]['payload']['research_direction'] = 'BUY'
    refreeze_rec(doc['records'][0]); refreeze(doc); save(path, doc)
    r = assess(path)
    assert r['latest_research_direction_untrusted'] == 'BUY'
    assert r['final_action'] == 'HOLD'
    assert r['paper_orders_authorized'] is False
    assert r['live_orders_authorized'] is False
    assert r['prospectively_scorable_pairs'] == 0


def test_p1101_local_pnl_separate_from_verified_performance(tmp_path):
    p, _ = fixture(tmp_path, kinds=('OUTCOME',))
    r = assess(p)
    assert r['records'][0]['payload']['hypothetical_costed_pips'] == '-2.3000'
    assert r['verified_performance_metrics'] is None
    assert r['source_origin_independently_authenticated'] is False


def test_p1101_missing_outcome_with_pnl_refused(tmp_path):
    mutate(tmp_path, lambda d: d['records'][0]['payload'].update(
        maturity_status='OUTCOME_MISSING'), kinds=('OUTCOME',), inner=True)


def test_p1101_model_cannot_be_self_promoted(tmp_path):
    mutate(tmp_path, lambda d: d['records'][0]['payload'].update(active_approved=True),
           kinds=('MODEL',), inner=True)


def test_p1101_local_forecast_cannot_claim_external_registration(tmp_path):
    mutate(tmp_path, lambda d: d['records'][0]['payload'].update(
        forecast_status='EXTERNALLY_REGISTERED'), kinds=('FORECAST',), inner=True)


def test_p1101_gate_cannot_claim_verified_receipt(tmp_path):
    mutate(tmp_path, lambda d: d['records'][0]['payload'].update(
        gate_status='INDEPENDENTLY_VERIFIED'), kinds=('GATE',), inner=True)


def test_p1101_probability_must_be_bounded(tmp_path):
    mutate(tmp_path, lambda d: d['records'][0]['payload'].update(
        probability_up='1.0001'), kinds=('FORECAST',), inner=True)


def test_p1101_negative_price_refused(tmp_path):
    mutate(tmp_path, lambda d: d['records'][0]['payload'].update(
        mid_price='-1.0000'), kinds=('MARKET_SNAPSHOT',), inner=True)


def test_p1101_duplicate_gate_under_different_id_refused(tmp_path):
    p, doc = fixture(tmp_path, kinds=('GATE',))
    dupe = deepcopy(doc['records'][0]); dupe['record_id'] = 'GATE_002'
    refreeze_rec(dupe)
    doc['records'].append(dupe)
    refreeze(doc); save(p, doc)
    with pytest.raises(MissionControlReadModelError, match='Duplicate gate'):
        assess(p)


def test_p1101_duplicate_family_under_different_id_refused(tmp_path):
    p, doc = fixture(tmp_path, kinds=('SOURCE_FAMILY',))
    dupe = deepcopy(doc['records'][0]); dupe['record_id'] = 'SOURCE_FAMILY_002'
    refreeze_rec(dupe)
    doc['records'].append(dupe)
    refreeze(doc); save(p, doc)
    with pytest.raises(MissionControlReadModelError, match='Duplicate canonical'):
        assess(p)


def test_p1101_unknown_schema_version_refused(tmp_path):
    mutate(tmp_path, lambda d: d.update(schema_version=2))


def test_p1101_unexpected_record_type_refused(tmp_path):
    mutate(tmp_path, lambda d: d['records'][0].update(record_type='ORDER_TICKET'), inner=True)


def test_p1101_unexpected_payload_field_rejected(tmp_path):
    mutate(tmp_path, lambda d: d['records'][0]['payload'].update(
        broker_password='do_not_export'), inner=True)


# P11-02: immutable local-only adapter and original Stage 10 hash validation.

def test_p1102_snapshot_content_replay_is_deterministic(tmp_path):
    p, doc = fixture(tmp_path, kinds=('EPOCH', 'SOURCE_FAMILY', 'GATE'))
    a, b = assess(p), assess(p)
    assert a == b
    assert a['report_sha256'] == _digest({k: v for k, v in a.items()
                                          if k != 'report_sha256'})


def test_p1102_prior_snapshot_identical_bytes_not_attestation(tmp_path):
    p, doc = fixture(tmp_path, kinds=('SOURCE_FAMILY',))
    prior = tmp_path / 'prior_local_offline.json'
    prior.write_bytes(p.read_bytes())
    r = assess(p, prior=prior)
    assert r['prior_local_snapshot_file_sha256'] == sha256(p.read_bytes()).hexdigest()
    assert r['source_origin_independently_authenticated'] is False


def test_p1102_prior_snapshot_mutation_detected_even_rehashed(tmp_path):
    p, doc = fixture(tmp_path, kinds=('SOURCE_FAMILY',))
    prior = tmp_path / 'prior_local_offline.json'
    prior.write_bytes(p.read_bytes())
    doc['records'][0]['payload']['clock_uncertainty_ms'] = '200'
    refreeze_rec(doc['records'][0]); refreeze(doc); save(p, doc)
    with pytest.raises(MissionControlReadModelError, match='bytes changed'):
        assess(p, prior=prior)


def test_p1102_same_previous_path_not_independent_snapshot(tmp_path):
    p, doc = fixture(tmp_path, kinds=('EPOCH',))
    with pytest.raises(MissionControlReadModelError, match='Distinct local prior'):
        assess(p, prior=p)


def test_p1102_prior_without_current_refused(tmp_path):
    p, doc = fixture(tmp_path, kinds=('EPOCH',))
    with pytest.raises(MissionControlReadModelError, match='Previous snapshot'):
        assess(prior=p)


def test_p1102_stage10_export_local_checksum_checked(tmp_path):
    rp, doc = fake_stage10(tmp_path)
    report = assess(stage10=rp)
    s = report['stage10_report_provenance']
    assert s['local_checksum_matches'] is True
    assert s['claimed_report_sha256'] == doc['report_sha256']
    assert s['independently_authenticated'] is False
    assert report['snapshot_status'] == 'NOT_SUPPLIED'


def test_p1102_snapshot_binds_original_stage10_export_digest(tmp_path):
    rp, parent = fake_stage10(tmp_path)
    sp, doc = fixture(tmp_path, kinds=('EPOCH',), parent=parent)
    r = assess(sp, stage10=rp)
    assert r['records'][0]['origin_report_sha256'] == parent['report_sha256']
    assert r['snapshot_file_sha256'] == sha256(sp.read_bytes()).hexdigest()
    assert r['final_action'] == 'HOLD'


def test_p1102_changed_original_stage10_rejected(tmp_path):
    rp, parent = fake_stage10(tmp_path)
    sp, doc = fixture(tmp_path, kinds=('EPOCH',), parent=parent)
    parent['research_handover']['research_decision'] = 'NOW_APPROVED'
    parent['report_sha256'] = _digest({k: v for k, v in parent.items()
                                       if k != 'report_sha256'})
    save(rp, parent)
    with pytest.raises(MissionControlReadModelError):
        assess(sp, stage10=rp)


def test_p1102_fake_stage10_nonzero_evidence_denied(tmp_path):
    rp, parent = fake_stage10(tmp_path)
    parent['independent_external_gates_verified'] = 1
    parent['report_sha256'] = _digest({k: v for k, v in parent.items()
                                       if k != 'report_sha256'})
    save(rp, parent)
    with pytest.raises(MissionControlReadModelError):
        assess(stage10=rp)


def test_p1102_changed_stage10_sha_denied(tmp_path):
    rp, doc = fake_stage10(tmp_path)
    doc['report_sha256'] = 'f' * 64
    save(rp, doc)
    with pytest.raises(MissionControlReadModelError, match='hash mismatch'):
        assess(stage10=rp)


def test_p1102_external_binding_without_original_report_refused(tmp_path):
    rp, parent = fake_stage10(tmp_path)
    sp, doc = fixture(tmp_path, kinds=('EPOCH',), parent=parent)
    with pytest.raises(MissionControlReadModelError, match='not bound'):
        assess(sp)


def test_p1102_future_stage10_report_refused(tmp_path):
    rp, doc = fake_stage10(tmp_path, at=T + timedelta(days=1))
    with pytest.raises(MissionControlReadModelError, match='future'):
        assess(stage10=rp)


def test_p1102_stage10_scope_downgrade_denied(tmp_path):
    rp, doc = fake_stage10(tmp_path)
    doc['scope'] = 'P10_PERFORMANCE_APPROVED'
    doc['report_sha256'] = _digest({k: v for k, v in doc.items()
                                   if k != 'report_sha256'})
    save(rp, doc)
    with pytest.raises(MissionControlReadModelError):
        assess(stage10=rp)


def test_p1102_no_interactive_or_external_imports():
    path = Path(__file__).with_name('stage11_mission_control_read_models.py')
    ast_tree = ast.parse(path.read_text(encoding='utf-8'))
    imports = []
    for node in ast.walk(ast_tree):
        if isinstance(node, ast.Import):
            imports.extend(n.name.split('.')[0] for n in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module.split('.')[0])
    assert set(imports) <= {'argparse', 'datetime', 'decimal', 'hashlib', 'json',
                            'pathlib', 're'}


def test_p1102_input_files_are_not_mutated(tmp_path):
    rp, parent = fake_stage10(tmp_path)
    sp, doc = fixture(tmp_path, kinds=('GATE',), parent=parent)
    before = (sha256(rp.read_bytes()).hexdigest(), sha256(sp.read_bytes()).hexdigest())
    r = assess(sp, stage10=rp)
    assert before == (sha256(rp.read_bytes()).hexdigest(), sha256(sp.read_bytes()).hexdigest())
    assert r['files_written'] is False
    assert r['network_accessed'] is False
    assert r['broker_connected'] is False


# P11-03: role previews, untrusted identity and secrets-safe projections.

def test_p1103_all_roles_read_only_and_no_authentication():
    for role in ROLES:
        r = assess(role=role)
        assert r['role_preview'] == role
        assert r['role_is_authenticated'] is False
        assert r['role_can_grant_privileges'] is False
        assert all(x['permitted_actions'] == ['VIEW_REDACTED_OFFLINE_REPORTS']
                   for x in r['role_contracts'])
        assert all(not x['can_submit_orders'] for x in r['role_contracts'])


def test_p1103_operator_role_preview_cannot_promote_or_trade(tmp_path):
    p, doc = fixture(tmp_path, kinds=('SEAT_VOTE', 'GOVERNANCE_VETO'))
    r = assess(p, role='OPERATOR')
    assert r['operator_approval_granted'] is False
    assert r['training_executed'] is False
    assert r['weights_promoted'] is False
    assert r['council_invoked'] is False
    assert r['final_action'] == 'HOLD'


def test_p1103_all_role_previews_redact_provider_reference(tmp_path):
    p, _ = fixture(tmp_path, kinds=('SOURCE_FAMILY',))
    for role in ROLES:
        r = assess(p, role=role)
        assert r['records'][0]['payload']['provider_reference'] is None
        assert 'LOCAL_PROVIDER_REF' not in json.dumps(r)


def test_p1103_sensitive_identifier_rejected_even_under_auditor_role(tmp_path):
    mutate(tmp_path, lambda d: d['records'][0]['payload'].update(
        provider_reference='account_number_112233'), inner=True)


def test_p1103_html_injection_rejected_even_under_auditor_role(tmp_path):
    mutate(tmp_path, lambda d: d['records'][0]['payload'].update(
        provider_reference='<script>alert(1)</script>'), inner=True)


def test_p1103_unknown_role_rejected():
    with pytest.raises(MissionControlReadModelError, match='role'):
        assess(role='GOD_MODE')


def test_p1103_extra_operational_permissions_refused(tmp_path):
    mutate(tmp_path, lambda d: d.update(operational_permissions=['TRADE']))


def test_p1103_local_packet_cannot_self_attest(tmp_path):
    mutate(tmp_path, lambda d: d.update(independent_attestation_verified=True))


def test_p1103_local_record_cannot_self_attest(tmp_path):
    mutate(tmp_path, lambda d: d['records'][0].update(
        evidence_status='INDEPENDENTLY_ATTESTED'), inner=True)


def test_p1103_invalid_packet_id_sensitive_refused(tmp_path):
    mutate(tmp_path, lambda d: d.update(packet_id='secret_token'))


def test_p1103_source_artifact_content_not_exported(tmp_path):
    p, doc = fixture(tmp_path, kinds=('SOURCE_FAMILY',))
    output = json.dumps(assess(p))
    assert 'provider_password' not in output
    assert 'LOCAL_PROVIDER_REF' not in output
    assert 'credentials' not in output


# P11-04: source and clock issues, empty states and malicious payloads.

def test_p1104_risk_officer_veto_survives_positive_vote(tmp_path):
    p, doc = fixture(tmp_path, kinds=('SEAT_VOTE', 'GOVERNANCE_VETO'))
    r = assess(p)
    assert r['risk_veto_declared_locally'] is True
    assert 'RISK_OFFICER_VETO_OR_HOLD' in r['why_hold']
    assert r['final_action'] == 'HOLD'
    assert r['veto_independently_assessed'] is False


def test_p1104_revoked_family_never_counted_verified(tmp_path):
    p, doc = fixture(tmp_path, kinds=('SOURCE_FAMILY',))
    doc['records'][0]['evidence_status'] = 'REVOKED'
    doc['records'][0]['payload']['revision_status'] = 'REVOKED'
    refreeze_rec(doc['records'][0]); refreeze(doc); save(p, doc)
    r = assess(p)
    assert r['revoked_records'] == 1
    assert r['source_family_board'][1]['evidence_status'] == 'REVOKED'
    assert r['independently_verified_source_families'] == 0


def test_p1104_revocation_conflict_quarantined(tmp_path):
    p, doc = fixture(tmp_path, kinds=('SOURCE_FAMILY',))
    doc['records'][0]['payload']['revision_status'] = 'REVOKED'
    refreeze_rec(doc['records'][0]); refreeze(doc); save(p, doc)
    r = assess(p)
    assert r['quarantined_records'] == 1
    assert 'REVOKED_SOURCE_INCONSISTENT_CLAIM' in r['records'][0]['blocked_reasons']
    assert r['records'][0]['payload'] is None


def test_p1104_future_source_vintage_hidden_from_historical_view(tmp_path):
    p, doc = fixture(tmp_path, kinds=('SOURCE_FAMILY',))
    doc['records'][0]['payload']['first_available_at_utc'] = z(T + timedelta(minutes=2))
    refreeze_rec(doc['records'][0]); refreeze(doc); save(p, doc)
    r = assess(p)
    assert r['records'][0]['payload'] is None
    assert r['records'][0]['freshness_status'] == 'FUTURE_DATED'
    assert r['source_family_board'][1]['evidence_status'] == 'QUARANTINED'
    assert r['independently_verified_source_families'] == 0


def test_p1104_observed_after_original_asof_quarantined(tmp_path):
    p, doc = fixture(tmp_path, kinds=('MARKET_SNAPSHOT',))
    doc['records'][0]['observed_at_utc'] = z(T + timedelta(minutes=1))
    refreeze_rec(doc['records'][0]); refreeze(doc); save(p, doc)
    r = assess(p)
    assert r['quarantined_records'] == 1
    assert r['records'][0]['payload'] is None
    assert r['latest_verified_market_price'] is None


def test_p1104_source_conflicting_clocks_quarantined(tmp_path):
    p, doc = fixture(tmp_path, kinds=('SOURCE_FAMILY',))
    doc['records'][0]['payload']['first_available_at_utc'] = z(T - timedelta(minutes=6))
    refreeze_rec(doc['records'][0]); refreeze(doc); save(p, doc)
    r = assess(p)
    assert 'CONFLICTING_SOURCE_CLOCKS' in r['records'][0]['blocked_reasons']
    assert r['records'][0]['freshness_status'] == 'GAP_DETECTED'


def test_p1104_stale_source_separate_from_local_hash(tmp_path):
    p, doc = fixture(tmp_path, kinds=('SOURCE_FAMILY',))
    old = T - timedelta(hours=4)
    doc['records'][0]['observed_at_utc'] = z(old)
    doc['records'][0]['payload']['first_available_at_utc'] = z(old + timedelta(seconds=1))
    refreeze_rec(doc['records'][0]); refreeze(doc); save(p, doc)
    r = assess(p)
    assert r['source_family_board'][1]['evidence_status'] == 'HASH_MATCHED'
    assert r['source_family_board'][1]['freshness_status'] == 'STALE'
    assert r['stale_records'] == 1
    assert r['independently_verified_source_families'] == 0


def test_p1104_empty_observed_time_unknown_not_current(tmp_path):
    p, doc = fixture(tmp_path, kinds=('SOURCE_FAMILY',))
    doc['records'][0]['observed_at_utc'] = None
    refreeze_rec(doc['records'][0]); refreeze(doc); save(p, doc)
    r = assess(p)
    assert r['records'][0]['freshness_status'] == 'UNKNOWN'


def test_p1104_future_market_bar_hidden(tmp_path):
    p, doc = fixture(tmp_path, kinds=('MARKET_SNAPSHOT',))
    doc['records'][0]['payload']['bar_close_at_utc'] = z(T + timedelta(minutes=1))
    refreeze_rec(doc['records'][0]); refreeze(doc); save(p, doc)
    r = assess(p)
    assert r['records'][0]['payload'] is None
    assert 'FUTURE_MARKET_CANDLE' in r['records'][0]['blocked_reasons']


def test_p1104_future_decision_bar_hidden(tmp_path):
    p, doc = fixture(tmp_path, kinds=('DECISION',))
    doc['records'][0]['payload']['m5_close_at_utc'] = z(T + timedelta(minutes=1))
    refreeze_rec(doc['records'][0]); refreeze(doc); save(p, doc)
    r = assess(p)
    assert r['latest_research_direction_untrusted'] is None
    assert 'FUTURE_DECISION_BAR' in r['records'][0]['blocked_reasons']


def test_p1104_record_future_asof_refused(tmp_path):
    mutate(tmp_path, lambda d: d['records'][0].update(
        as_of_utc=z(T + timedelta(minutes=1))), inner=True)


def test_p1104_packet_future_export_refused(tmp_path):
    mutate(tmp_path, lambda d: d.update(exported_at_utc=z(T + timedelta(days=1))))


def test_p1104_utc_offset_in_local_record_refused(tmp_path):
    mutate(tmp_path, lambda d: d['records'][0].update(as_of_utc=z(T).replace('Z', '+00:00')),
           inner=True)


def test_p1104_tampered_row_hash_refused(tmp_path):
    p, doc = fixture(tmp_path, kinds=('SOURCE_FAMILY',))
    doc['records'][0]['payload']['revision_status'] = 'REVISED'
    refreeze(doc); save(p, doc)
    with pytest.raises(MissionControlReadModelError, match='DTO content hash'):
        assess(p)


def test_p1104_tampered_packet_hash_refused(tmp_path):
    p, doc = fixture(tmp_path, kinds=('SOURCE_FAMILY',))
    doc['packet_sha256'] = '0' * 64
    save(p, doc)
    with pytest.raises(MissionControlReadModelError, match='Snapshot local content hash'):
        assess(p)


def test_p1104_duplicate_json_key_refused(tmp_path):
    p = tmp_path / 'duplicate.json'
    p.write_text('{"mode":"OFFLINE","mode":"BROKER"}', encoding='utf-8')
    with pytest.raises(MissionControlReadModelError, match='Duplicate JSON'):
        assess(p)


def test_p1104_nonfinite_json_refused(tmp_path):
    p = tmp_path / 'nan.json'
    p.write_text('{"mode":NaN}', encoding='utf-8')
    with pytest.raises(MissionControlReadModelError, match='Nonfinite'):
        assess(p)


def test_p1104_non_object_json_refused(tmp_path):
    p = tmp_path / 'array.json'
    p.write_text('[]', encoding='utf-8')
    with pytest.raises(MissionControlReadModelError, match='root object'):
        assess(p)


def test_p1104_oversized_packet_refused(tmp_path):
    p = tmp_path / 'oversized.json'
    p.write_bytes(b'x' * 262145)
    with pytest.raises(MissionControlReadModelError, match='too large'):
        assess(p)


def test_p1104_wrong_epoch_refused():
    with pytest.raises(MissionControlReadModelError, match='rebinding'):
        assess(epoch='f' * 64)


def test_p1104_tampered_policy_refused(tmp_path):
    p = tmp_path / 'altered_policy.json'
    p.write_bytes(POLICY.read_bytes() + b' ')
    with pytest.raises(MissionControlReadModelError, match='policy bytes changed'):
        assess(policy=p)


def test_p1104_future_dashboard_cutoff_refused():
    with pytest.raises(MissionControlReadModelError, match='Future dashboard cutoff'):
        assess(at=z(datetime.now(UTC) + timedelta(days=1)))


def test_p1104_symlink_snapshot_refused_if_supported(tmp_path):
    p, doc = fixture(tmp_path, kinds=('SOURCE_FAMILY',))
    link = tmp_path / 'packet_symlink.json'
    try:
        link.symlink_to(p)
    except (NotImplementedError, OSError):
        pytest.skip('Windows symlink privilege unavailable')
    with pytest.raises(MissionControlReadModelError, match='Symlink'):
        assess(link)


def test_p1104_cli_missing_packet_holds(capsys):
    code = run(['--read-only-mission-control', '--offline-only',
                '--epoch-id', EPOCH, '--as-of', z(T + timedelta(minutes=2)),
                '--policy-file', str(POLICY)])
    assert code == 0
    text = capsys.readouterr().out
    assert 'Snapshot: NOT_SUPPLIED' in text
    assert 'Independent gates: 0/11 | Source families: 0/13' in text
    assert 'FINAL=HOLD' in text


def test_p1104_cli_with_json_fixture_still_hold(tmp_path, capsys):
    p, doc = fixture(tmp_path, kinds=('EPOCH', 'DECISION', 'SOURCE_FAMILY'))
    code = run(['--read-only-mission-control', '--offline-only', '--epoch-id', EPOCH,
                '--as-of', z(T + timedelta(minutes=2)), '--policy-file', str(POLICY),
                '--snapshot-file', str(p), '--role-preview', 'AUDITOR', '--json'])
    assert code == 0
    r = json.loads(capsys.readouterr().out)
    assert r['record_count'] == 3
    assert r['role_is_authenticated'] is False
    assert r['independently_verified_source_families'] == 0
    assert r['final_action'] == 'HOLD'


def test_p1104_cli_explicit_safe_flags_required():
    with pytest.raises(SystemExit) as exc:
        run(['--epoch-id', EPOCH, '--as-of', z(T)])
    assert exc.value.code == 2


def test_p1104_cli_malicious_report_blocked(capsys, tmp_path):
    p = tmp_path / 'malicious.json'
    p.write_text('{"live_orders_authorized": true}', encoding='utf-8')
    assert run(['--read-only-mission-control', '--offline-only', '--epoch-id', EPOCH,
                '--as-of', z(T + timedelta(minutes=2)), '--snapshot-file', str(p),
                '--policy-file', str(POLICY)]) == 1
    assert 'BLOCKED' in capsys.readouterr().out


def test_p1104_report_sha_deterministic_with_fixed_inputs(tmp_path):
    p, doc = fixture(tmp_path, kinds=('SEAT_VOTE', 'GATE'))
    a = assess(p)
    b = assess(p)
    assert a == b
    assert a['report_sha256'] == _digest({k: v for k, v in a.items()
                                          if k != 'report_sha256'})
    assert a['stage11_ui_delivered'] is False


@pytest.mark.parametrize('flag', [
    'network_accessed', 'training_executed', 'model_loaded', 'council_invoked',
    'paper_orders_authorized', 'live_orders_authorized',
    'independent_research_model_selection_granted',
])
def test_p1102_upstream_operational_self_authorization_refused(tmp_path, flag):
    rp, doc = fake_stage10(tmp_path)
    doc[flag] = True
    doc['report_sha256'] = _digest({k: v for k, v in doc.items()
                                   if k != 'report_sha256'})
    save(rp, doc)
    with pytest.raises(MissionControlReadModelError, match='inconsistent authority'):
        assess(stage10=rp)


def test_p1104_quarantined_gate_remains_on_evidence_board(tmp_path):
    p, doc = fixture(tmp_path, kinds=('GATE',))
    doc['records'][0]['observed_at_utc'] = z(T + timedelta(minutes=1))
    refreeze_rec(doc['records'][0]); refreeze(doc); save(p, doc)
    report = assess(p)
    assert report['evidence_gate_board'][0]['identifier'] == 'G01'
    assert report['evidence_gate_board'][0]['evidence_status'] == 'QUARANTINED'
    assert report['independently_verified_external_gates'] == 0


def test_p1104_unknown_first_source_availability_is_not_fresh(tmp_path):
    p, doc = fixture(tmp_path, kinds=('SOURCE_FAMILY',))
    doc['records'][0]['payload']['first_available_at_utc'] = None
    refreeze_rec(doc['records'][0]); refreeze(doc); save(p, doc)
    r = assess(p)
    assert r['source_family_board'][1]['freshness_status'] == 'UNKNOWN'
    assert r['source_family_board'][1]['independently_verified'] is False


def test_p1104_quarantined_duplicate_family_cannot_hide_collision(tmp_path):
    p, doc = fixture(tmp_path, kinds=('SOURCE_FAMILY',))
    duplicate = deepcopy(doc['records'][0])
    duplicate['record_id'] = 'SOURCE_FAMILY_OTHER'
    duplicate['payload']['first_available_at_utc'] = z(T + timedelta(minutes=1))
    refreeze_rec(duplicate)
    doc['records'].append(duplicate)
    refreeze(doc); save(p, doc)
    with pytest.raises(MissionControlReadModelError, match='Duplicate canonical'):
        assess(p)


def test_p1104_council_duplicate_vote_id_refused(tmp_path):
    p, doc = fixture(tmp_path, kinds=('SEAT_VOTE',))
    duplicate = deepcopy(doc['records'][0])
    duplicate['record_id'] = 'SEAT_VOTE_OTHER'
    refreeze_rec(duplicate)
    doc['records'].append(duplicate)
    refreeze(doc); save(p, doc)
    with pytest.raises(MissionControlReadModelError, match='Duplicate Council'):
        assess(p)

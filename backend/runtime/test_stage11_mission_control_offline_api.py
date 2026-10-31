
"""Stage 11 P11-05..08 offline API boundary and redaction tests.

Only synthetic, locally hash-consistent Stage 11 report fixtures are used.
No network request, HTTP listener, authentication or execution is performed.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest

from backend.runtime.stage11_mission_control_offline_api import (
    API_PREFIX, DTO_TYPES, EPOCH, GATE_SLOTS, MAX_REPORT_BYTES, ROUTES,
    SOURCE_SLOTS, SOURCE_SCOPE, SAFE_ROLES, VERSION,
    OfflineMissionControlAPIError, _digest, build_offline_api_response,
    read_offline_report, run,
)

UTC = timezone.utc


def now():
    return datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=8)


def z(at):
    return at.strftime('%Y-%m-%dT%H:%M:%SZ')


def synthetic_stage11_report(at=None):
    at = now() if at is None else at
    doc = {
        'scope': SOURCE_SCOPE,
        'historical_epoch_id': EPOCH,
        'as_of_utc': z(at),
        'final_action': 'HOLD',
        'snapshot_status': 'LOCAL_SYNTHETIC_UNATTESTED',
        'record_count': 3,
        'read_model_inventory': [
            {'record_type': 'TYPE_%02d' % i, 'raw_record': 'do not export'}
            for i in range(1, DTO_TYPES + 1)
        ],
        'source_family_board': [
            {'identifier': 'FAMILY_%02d' % i, 'provider_token': 'top-secret-provider-key'}
            for i in range(1, SOURCE_SLOTS + 1)
        ],
        'evidence_gate_board': [
            {'identifier': 'G%02d' % i, 'reviewer_email': 'private@example.test'}
            for i in range(1, GATE_SLOTS + 1)
        ],
        'independently_verified_source_families': 0,
        'independently_verified_external_gates': 0,
        'latest_verified_market_price': None,
        'verified_performance_metrics': None,
        'role_is_authenticated': False,
        'stage11_ui_delivered': False,
        'research_operational_readiness': 'BLOCKED_EXTERNAL_EVIDENCE',
        'read_only': True,
        'files_written': False,
        'network_accessed': False,
        'broker_connected': False,
        'collector_started': False,
        'council_invoked': False,
        'model_loaded': False,
        'training_executed': False,
        'training_authorized': False,
        'paper_orders_authorized': False,
        'live_orders_authorized': False,
        'operator_approval_granted': False,
    }
    doc['report_sha256'] = _digest(doc)
    return doc


def resign(doc):
    doc['report_sha256'] = _digest({k: v for k, v in doc.items()
                                    if k != 'report_sha256'})


def render(report=None, *, name='overview', method='GET', role='VIEWER', at=None, route=None):
    return build_offline_api_response(
        epoch_id=EPOCH, as_of=z(now() if at is None else at),
        route=route if route is not None else API_PREFIX + '/' + name,
        method=method, role=role, report=report,
    )


def given(tmp_path, report=None):
    doc = synthetic_stage11_report() if report is None else report
    path = tmp_path / 'exported_read_model_report.json'
    path.write_text(json.dumps(doc, separators=(',', ':'), sort_keys=True), encoding='utf-8')
    return path, doc


def denied(tmp_path, mutate, *, rehash=False):
    path, doc = given(tmp_path)
    mutate(doc)
    if rehash:
        resign(doc)
    path.write_text(json.dumps(doc, sort_keys=True), encoding='utf-8')
    with pytest.raises(OfflineMissionControlAPIError):
        render(read_offline_report(path), at=datetime.fromisoformat(doc['as_of_utc'].replace('Z', '+00:00'))
               if doc['as_of_utc'].endswith('Z') else now())


# P11-05: versioned read-only API surface, not an actual HTTP service.

def test_p1105_all_eight_declared_routes_exist_without_network():
    for name in ROUTES:
        r = render(name=name)
        assert r['http_status_preview'] == 200
        assert r['final_action'] == 'HOLD'
        assert r['network_accessed'] is False
        assert r['http_listener_created'] is False
        assert r['body']['route'] == API_PREFIX + '/' + name
        assert r['body']['can_execute_actions'] is False


def test_p1105_unbound_source_is_missing_not_zero_price():
    data = render()['body']
    assert data['source_classification'] == 'NO_STAGE11_REPORT_SUPPLIED'
    assert data['panel']['latest_verified_market_price'] is None
    assert data['panel']['local_record_count'] is None
    assert data['panel']['independently_verified_performance'] is None


def test_p1105_get_returns_deterministic_version_and_frozen_epoch():
    t = now()
    a = render(at=t)
    b = render(at=t)
    assert a == b
    assert a['body']['api_version'] == VERSION
    assert a['body']['historical_epoch_id'] == EPOCH


def test_p1105_head_emits_no_body_but_same_digest():
    t = now()
    get = render(at=t)
    head = render(at=t, method='HEAD')
    assert get['body'] is not None
    assert head['body'] is None
    assert head['body_emitted'] is False
    assert head['body_sha256'] == get['body_sha256']
    assert head['headers_preview']['ETag'] == get['headers_preview']['ETag']


@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'CONNECT'])
def test_p1105_write_and_nonread_methods_are_denied_without_processing(method):
    r = render(method=method, report={'unsafe': 'not inspected'})
    assert r['http_status_preview'] == 405
    assert r['body']['requested_action_performed'] is False
    assert r['final_action'] == 'HOLD'


@pytest.mark.parametrize('path', [
    '/api/v1/mission-control/orders',
    '/api/v1/mission-control/promotion',
    '/api/v1/mission-control/overview?trade=true',
    '/api/v1/mission-control/../admin',
    '/api/v1/mission-control//overview',
    '/api/v1/mission-control/%6Fverview',
    '/api/v1/mission-control/overview#hash',
    'http://localhost:8000/api/v1/mission-control/overview',
    '/', '',
])
def test_p1105_unknown_or_injected_route_does_not_read_source(path):
    r = render(route=path, report={'unsafe': 'not inspected'})
    assert r['http_status_preview'] == 404
    assert r['body']['final_action'] == 'HOLD'


def test_p1105_no_credentials_cookies_cors_or_cache_enabling():
    r = render()
    h = r['headers_preview']
    assert 'Access-Control-Allow-Origin' not in h
    assert h['Cache-Control'].startswith('no-store')
    assert 'none' in h['Content-Security-Policy']
    assert h['X-Content-Type-Options'] == 'nosniff'
    assert 'Set-Cookie' not in h
    assert 'Access-Control-Allow-Credentials' not in h
    assert r['role_authenticated'] is False


# P11-06: genuine known/missing separation and safe projection.

def test_p1106_local_hash_consistency_is_not_evidence():
    at = now()
    d = synthetic_stage11_report(at)
    r = render(d, at=at)
    assert r['body']['source_classification'] == 'LOCAL_STAGE11_REPORT_HASH_MATCH_UNATTESTED'
    assert r['body']['local_source_report_sha256'] == d['report_sha256']
    assert r['body']['verified_external_gate_count'] == 0
    assert r['body']['verified_source_family_count'] == 0


def test_p1106_source_board_has_thirteen_redacted_unverified_slots():
    t = now()
    d = synthetic_stage11_report(t)
    panel = render(d, at=t, name='sources')['body']['panel']
    assert len(panel['source_families']) == 13
    assert panel['independently_admitted_families'] == 0
    assert panel['true_last_receipt_time'] is None
    assert all(x['status'] == 'NOT_INDEPENDENTLY_VERIFIED' for x in panel['source_families'])
    assert all(x['raw_artifact_exposed'] is False for x in panel['source_families'])
    assert 'top-secret-provider-key' not in json.dumps(panel)


def test_p1106_gate_board_has_eleven_blocked_slots():
    t = now()
    d = synthetic_stage11_report(t)
    panel = render(d, at=t, name='gates')['body']['panel']
    assert len(panel['gates']) == 11
    assert panel['independently_verified_gates'] == 0
    assert panel['signed_operator_approval_present'] is False
    assert 'private@example.test' not in json.dumps(panel)


def test_p1106_design_council_is_not_an_execution_receipt():
    panel = render(name='council')['body']['panel']
    assert panel['design_seats'] == 31
    assert panel['primary_seats'] == 28
    assert panel['governance_seats'] == 2
    assert panel['synthesis_seats'] == 1
    assert panel['real_cycles_dispatched'] == 0
    assert panel['most_recent_verified_consensus'] is None
    assert panel['most_recent_verified_risk_veto'] is None
    assert panel['risk_veto_state'] == 'NOT_OBSERVED_DO_NOT_ASSUME_CLEAR'


def test_p1106_research_has_null_not_zero_performance_metrics():
    panel = render(name='research')['body']['panel']
    for k in ('independent_forecast_outcome_pairs', 'verified_costed_pnl',
              'verified_calibration', 'verified_robustness'):
        assert panel[k] is None
    assert panel['stage9_independent_exit_met'] is False
    assert panel['stage10_independent_exit_met'] is False


def test_p1106_health_is_unknown_not_healthy():
    panel = render(name='health')['body']['panel']
    assert panel['status'] == 'UNKNOWN_NOT_INSPECTED'
    assert panel['collector_process_running'] is None
    assert panel['real_council_latency_ms'] is None
    assert panel['risk_kill_switch_clear'] is None
    assert panel['clock_attestation_verified'] is False


def test_p1106_model_schemas_never_model_weights_or_trainings():
    panel = render(name='models')['body']['panel']
    assert len(panel['types']) == DTO_TYPES
    assert panel['model_training_executed'] is False
    assert panel['model_selection_or_promotion_allowed'] is False
    assert all(x['source_record_contents_exported'] is False for x in panel['types'])


def test_p1106_audit_never_confuses_local_hash_with_attestation():
    panel = render(name='audit')['body']['panel']
    assert panel['verified_audit_events'] == []
    assert panel['original_report_bytes_independently_attested'] is False
    assert panel['diagnostic_caveat'] == 'HASH_CONSISTENCY_IS_NOT_EXTERNAL_CUSTODY'


def test_p1106_inventory_defaults_are_design_not_certification():
    panel = render(name='models')['body']['panel']
    assert panel['types'][0]['read_model_schema_available'] is False
    assert panel['types'][0]['classification'] == 'READ_ONLY_DESIGN_NOT_INDEPENDENTLY_VERIFIED'


def test_p1106_local_dto_names_come_only_from_allowlisted_ids():
    t = now()
    d = synthetic_stage11_report(t)
    types = render(d, at=t, name='models')['body']['panel']['types']
    assert types[0]['dto_type'] == 'TYPE_01'
    assert all(x['read_model_schema_available'] is True for x in types)


def test_p1106_local_decision_count_is_a_count_not_a_signal():
    t = now()
    d = synthetic_stage11_report(t)
    panel = render(d, at=t)['body']['panel']
    assert panel['local_record_count'] == 3
    assert panel['latest_verified_market_price'] is None
    assert panel['real_forecasts_registered'] is None


def test_p1106_provider_paths_and_private_text_never_leak_even_auditor():
    t = now()
    d = synthetic_stage11_report(t)
    d['extra_secret'] = '/private/secret/account/password.txt'
    resign(d)
    for role in SAFE_ROLES:
        for route in ROUTES:
            output = json.dumps(render(d, at=t, role=role, name=route))
            assert 'password.txt' not in output
            assert 'private@example.test' not in output
            assert 'top-secret-provider-key' not in output
            assert output.find('FINAL=HOLD') == -1 or 'HOLD' in output


# P11-07: fail closed on forged, replayed or manipulated source artifacts.

def test_p1107_tampered_digest_denied():
    t = now()
    d = synthetic_stage11_report(t)
    d['record_count'] = 5
    with pytest.raises(OfflineMissionControlAPIError, match='local hash'):
        render(d, at=t)


def test_p1107_rehashed_forged_price_still_denied():
    t = now()
    d = synthetic_stage11_report(t)
    d['latest_verified_market_price'] = '1.07520'
    resign(d)
    with pytest.raises(OfflineMissionControlAPIError):
        render(d, at=t)


def test_p1107_rehashed_forged_performance_still_denied():
    t = now()
    d = synthetic_stage11_report(t)
    d['verified_performance_metrics'] = {'win_rate': 1.0}
    resign(d)
    with pytest.raises(OfflineMissionControlAPIError):
        render(d, at=t)


@pytest.mark.parametrize('key,value', [
    ('role_is_authenticated', True), ('stage11_ui_delivered', True),
    ('independently_verified_source_families', 1),
    ('independently_verified_external_gates', 1),
    ('final_action', 'TRADE'),
    ('research_operational_readiness', 'LIVE_APPROVED'),
    ('scope', 'STAGE11_API_PRODUCTION'),
    ('historical_epoch_id', 'f'*64),
    ('read_only', False), ('files_written', True),
    ('council_invoked', True), ('network_accessed', True),
    ('operator_approval_granted', True), ('training_executed', True),
    ('paper_orders_authorized', True), ('live_orders_authorized', True),
    ('role_is_authenticated', 1), ('read_only', 1),
])
def test_p1107_false_assurance_or_operation_denied_even_when_rehashed(key, value):
    t = now()
    d = synthetic_stage11_report(t)
    d[key] = value
    resign(d)
    with pytest.raises(OfflineMissionControlAPIError):
        render(d, at=t)


@pytest.mark.parametrize('key, value', [
    ('record_count', -1), ('record_count', True), ('record_count', 4097),
    ('read_model_inventory', []), ('source_family_board', []),
    ('evidence_gate_board', []), ('snapshot_status', '<script>evil</script>'),
    ('report_sha256', 'F'*64),
])
def test_p1107_unsafe_report_structure_denied(key, value):
    t = now()
    d = synthetic_stage11_report(t)
    d[key] = value
    if key != 'report_sha256':
        resign(d)
    with pytest.raises(OfflineMissionControlAPIError):
        render(d, at=t)


def test_p1107_future_asof_rejected():
    t = datetime.now(UTC) + timedelta(days=1)
    with pytest.raises(OfflineMissionControlAPIError, match='Future dashboard'):
        render(at=t)


def test_p1107_wrong_epoch_rejected():
    with pytest.raises(OfflineMissionControlAPIError):
        build_offline_api_response(epoch_id='0'*64, as_of=z(now()),
                                   route=API_PREFIX + '/overview')


@pytest.mark.parametrize('raw', [
    '2026-10-30 12:00:00',
    '2026-10-30T12:00:00+03:00',
    '2026-12-99T12:00:00Z',
])
def test_p1107_unsafe_local_time_rejected(raw):
    with pytest.raises(OfflineMissionControlAPIError):
        build_offline_api_response(epoch_id=EPOCH, as_of=raw,
                                   route=API_PREFIX + '/overview')


def test_p1107_prior_report_cannot_be_reused_at_other_cutoff():
    t = now()
    d = synthetic_stage11_report(t)
    with pytest.raises(OfflineMissionControlAPIError, match='cutoff'):
        render(d, at=t + timedelta(seconds=1))


def test_p1107_report_hash_is_not_external_confirmation():
    t = now()
    d = synthetic_stage11_report(t)
    audit = render(d, at=t, name='audit')['body']['panel']
    assert audit['local_source_report_sha256'] == d['report_sha256']
    assert audit['original_report_bytes_independently_attested'] is False


def test_p1107_duplicate_labels_disallowed_on_sanitized_board():
    t = now()
    d = synthetic_stage11_report(t)
    d['source_family_board'][1]['identifier'] = 'FAMILY_01'
    resign(d)
    with pytest.raises(OfflineMissionControlAPIError, match='duplicated'):
        render(d, at=t, name='sources')


def test_p1107_html_and_sensitive_identifiers_not_exposed_from_raw_board():
    t = now()
    d = synthetic_stage11_report(t)
    d['source_family_board'][0]['identifier'] = '<script>alert(1)</script>'
    resign(d)
    output = json.dumps(render(d, at=t, name='sources'))
    assert '<script>' not in output
    assert 'SOURCE_SLOT_01' in output


def test_p1107_input_report_not_mutated_by_projector():
    t = now()
    d = synthetic_stage11_report(t)
    before = deepcopy(d)
    for route in ROUTES:
        render(d, at=t, name=route)
    assert d == before


def test_p1107_unknown_role_cannot_authenticate():
    with pytest.raises(OfflineMissionControlAPIError):
        render(role='ADMIN')


@pytest.mark.parametrize('role', SAFE_ROLES)
def test_p1107_role_previews_all_have_identical_powers(role):
    t = now()
    d = synthetic_stage11_report(t)
    r = render(d, at=t, role=role)
    assert r['role_authenticated'] is False
    assert r['body']['can_execute_actions'] is False
    assert r['body']['role_authenticated'] is False


# P11-08: offline CLI, replay, no raw artifacts, file safety.

def test_p1108_read_only_report_file_round_trip(tmp_path):
    t = now()
    p, d = given(tmp_path, synthetic_stage11_report(t))
    before = sha256(p.read_bytes()).hexdigest()
    loaded = read_offline_report(p)
    out = render(loaded, at=t)
    assert out['body']['source_classification'].endswith('UNATTESTED')
    assert before == sha256(p.read_bytes()).hexdigest()
    assert out['network_accessed'] is False
    assert out['body']['files_written'] is False


def test_p1108_utf8_bom_from_windows_export_supported(tmp_path):
    t = now()
    p, d = given(tmp_path, synthetic_stage11_report(t))
    p.write_bytes(b'\xef\xbb\xbf' + p.read_bytes())
    assert render(read_offline_report(p), at=t)['final_action'] == 'HOLD'


def test_p1108_duplicate_json_field_rejected(tmp_path):
    p = tmp_path / 'forged.json'
    p.write_text('{"final_action":"HOLD","final_action":"BUY"}', encoding='utf-8')
    with pytest.raises(OfflineMissionControlAPIError, match='Duplicate'):
        read_offline_report(p)


def test_p1108_nonfinite_json_rejected(tmp_path):
    p = tmp_path / 'forged.json'
    p.write_text('{"value": NaN}', encoding='utf-8')
    with pytest.raises(OfflineMissionControlAPIError):
        read_offline_report(p)


def test_p1108_array_instead_of_report_object_rejected(tmp_path):
    p = tmp_path / 'forged.json'
    p.write_text('[]', encoding='utf-8')
    with pytest.raises(OfflineMissionControlAPIError):
        read_offline_report(p)


def test_p1108_oversized_file_rejected(tmp_path):
    p = tmp_path / 'huge.json'
    p.write_bytes(b'\x20' * (MAX_REPORT_BYTES + 1))
    with pytest.raises(OfflineMissionControlAPIError):
        read_offline_report(p)


def test_p1108_non_json_extension_rejected(tmp_path):
    p = tmp_path / 'export.txt'
    p.write_text('{}')
    with pytest.raises(OfflineMissionControlAPIError):
        read_offline_report(p)


def test_p1108_symlink_alias_rejected_if_supported(tmp_path):
    p, d = given(tmp_path)
    link = tmp_path / 'alias.json'
    try:
        link.symlink_to(p)
    except (OSError, NotImplementedError):
        pytest.skip('Windows symlink privilege not available')
    with pytest.raises(OfflineMissionControlAPIError):
        read_offline_report(link)


def test_p1108_cli_without_report_is_safe(capsys):
    result = run(['--read-only-api', '--offline-only', '--epoch-id', EPOCH,
                  '--as-of', z(now()), '--route', API_PREFIX+'/overview'])
    assert result == 0
    output = capsys.readouterr().out
    assert 'FINAL=HOLD' in output
    assert 'NO API SERVER' in output


def test_p1108_cli_json_response_has_no_server(capsys):
    result = run(['--read-only-api', '--offline-only', '--epoch-id', EPOCH,
                  '--as-of', z(now()), '--route', API_PREFIX+'/gates', '--json'])
    assert result == 0
    report = json.loads(capsys.readouterr().out)
    assert report['http_status_preview'] == 200
    assert report['body']['panel']['independently_verified_gates'] == 0
    assert report['http_listener_created'] is False
    assert report['network_accessed'] is False


def test_p1108_cli_imported_real_report_shape(tmp_path, capsys):
    t = now()
    p, d = given(tmp_path, synthetic_stage11_report(t))
    result = run(['--read-only-api', '--offline-only', '--epoch-id', EPOCH,
                  '--as-of', z(t), '--route', API_PREFIX+'/models',
                  '--report-file', str(p), '--json'])
    assert result == 0
    body = json.loads(capsys.readouterr().out)['body']
    assert body['source_classification'] == 'LOCAL_STAGE11_REPORT_HASH_MATCH_UNATTESTED'
    assert len(body['panel']['types']) == DTO_TYPES
    assert body['final_action'] == 'HOLD'


def test_p1108_cli_unsafe_report_blocked(tmp_path, capsys):
    p = tmp_path / 'bad.json'
    p.write_text('{"final_action":"BUY"}', encoding='utf-8')
    assert run(['--read-only-api', '--offline-only', '--epoch-id', EPOCH,
                '--as-of', z(now()), '--report-file', str(p)]) == 1
    assert 'BLOCKED' in capsys.readouterr().out


def test_p1108_cli_mandatory_flags():
    with pytest.raises(SystemExit) as e:
        run(['--epoch-id', EPOCH, '--as-of', z(now())])
    assert e.value.code == 2


def test_p1108_response_replay_same_report_and_cutoff_is_byte_stable():
    t = now()
    d = synthetic_stage11_report(t)
    a = render(d, at=t, name='research')
    b = render(d, at=t, name='research')
    assert a == b
    assert a['response_sha256'] == _digest({k: v for k,v in a.items() if k != 'response_sha256'})
    assert a['body_sha256'] == _digest(a['body'])


def test_p1108_same_source_different_route_produces_distinct_digest():
    t = now()
    d = synthetic_stage11_report(t)
    a = render(d, at=t, name='overview')
    b = render(d, at=t, name='audit')
    assert a['response_sha256'] != b['response_sha256']
    assert a['body_sha256'] != b['body_sha256']


def test_p1108_etag_matches_original_safe_body_hash():
    t = now()
    d = synthetic_stage11_report(t)
    r = render(d, at=t)
    assert r['headers_preview']['ETag'] == '"' + r['body_sha256'] + '"'
    assert r['response_sha256'] != r['body_sha256']


def test_p1108_no_runtime_socket_subprocess_or_import_side_effects():
    import inspect
    from backend.runtime import stage11_mission_control_offline_api as module
    text = inspect.getsource(module)
    for forbidden in ('import socket', 'subprocess.', 'requests.', 'urllib.request',
                      'http.server', 'ThreadingHTTPServer', 'os.system(',
                      'pickle.loads(', 'exec(', 'eval('):
        assert forbidden not in text


# P11-08: integration against the committed read-model CLI.

def test_p1108_original_asof_utc_required_not_legacy_asof():
    t = now()
    source = synthetic_stage11_report(t)
    source['as_of'] = source.pop('as_of_utc')
    resign(source)

    with pytest.raises(
        OfflineMissionControlAPIError,
        match='Missing required',
    ):
        render(source, at=t)


def test_p1108_original_identifier_and_record_type_are_preserved():
    t = now()
    source = synthetic_stage11_report(t)

    families = render(
        source, at=t, name='sources'
    )['body']['panel']['source_families']

    gates = render(
        source, at=t, name='gates'
    )['body']['panel']['gates']

    models = render(
        source, at=t, name='models'
    )['body']['panel']['types']

    assert families[0]['label'] == 'FAMILY_01'
    assert families[-1]['label'] == 'FAMILY_13'
    assert gates[0]['label'] == 'G01'
    assert models[0]['dto_type'] == 'TYPE_01'


def test_p1108_actual_stage11_read_model_export_integration(
    tmp_path, capsys
):
    """Exercise the committed read-model CLI and all API projections."""
    try:
        from backend.runtime.stage11_mission_control_read_models import (
            run as run_read_models,
        )
    except ModuleNotFoundError as exc:
        if exc.name == (
            'backend.runtime.stage11_mission_control_read_models'
        ):
            pytest.skip(
                'Committed Stage 11 read-model module unavailable'
            )
        raise

    from pathlib import Path

    policy = Path(__file__).resolve().parents[2] / (
        'docs/clock_profiles/hfm_forward_20261027.json'
    )

    t = now()

    result = run_read_models([
        '--read-only-mission-control',
        '--offline-only',
        '--epoch-id',
        EPOCH,
        '--as-of',
        z(t),
        '--policy-file',
        str(policy),
        '--json',
    ])

    output = capsys.readouterr().out

    assert result == 0

    report = json.loads(output)

    assert datetime.fromisoformat(
        report['as_of_utc'].replace('Z', '+00:00')
    ) == t

    assert report['scope'] == SOURCE_SCOPE
    assert report['final_action'] == 'HOLD'

    exported = tmp_path / 'actual_stage11_read_model_report.json'

    exported.write_text(
        json.dumps(report, sort_keys=True, indent=2),
        encoding='utf-8',
    )

    imported = read_offline_report(exported)

    for name in ROUTES:
        response = render(imported, at=t, name=name)

        assert response['http_status_preview'] == 200
        assert response['body']['source_classification'] == (
            'LOCAL_STAGE11_REPORT_HASH_MATCH_UNATTESTED'
        )
        assert response['body']['verified_source_family_count'] == 0
        assert response['body']['verified_external_gate_count'] == 0
        assert response['body']['final_action'] == 'HOLD'
        assert response['network_accessed'] is False
        assert response['http_listener_created'] is False

    families = render(
        imported, at=t, name='sources'
    )['body']['panel']['source_families']

    gates = render(
        imported, at=t, name='gates'
    )['body']['panel']['gates']

    models = render(
        imported, at=t, name='models'
    )['body']['panel']['types']

    assert len(families) == 13
    assert len(gates) == 11
    assert len(models) == 12

    for index, slot in enumerate(families):
        expected = report[
            'source_family_board'
        ][index].get('identifier')

        if (
            isinstance(expected, str)
            and expected
            and expected.isidentifier()
        ):
            assert slot['label'] == expected

    for index, slot in enumerate(gates):
        expected = report[
            'evidence_gate_board'
        ][index].get('identifier')

        if (
            isinstance(expected, str)
            and expected
            and expected.isidentifier()
        ):
            assert slot['label'] == expected

    assert all(
        not item['independent_attestation_present']
        for item in families
    )

    assert all(
        not item['independent_attestation_present']
        for item in gates
    )

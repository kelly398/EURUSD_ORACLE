
"""Integrated Stage 11 local alert, incident replay, recovery and HTML tests."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path

import pytest

from backend.runtime.stage11_mission_control_offline_api import (
    EPOCH, _digest, read_offline_report,
)
from backend.runtime.test_stage11_mission_control_offline_api import (
    synthetic_stage11_report, z,
)
from backend.runtime.stage11_offline_monitoring_recovery import (
    INCIDENT_VERSION, ZERO_SHA, MAX_EVENTS, VERSION,
    OfflineRecoveryError, _validate_packet, assess_offline_monitoring_recovery,
    render_monitoring_recovery_html, run,
)

UTC = timezone.utc


def at():
    return datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=15)


def event(eid, incident, kind, state, moment, previous):
    e = {
        'event_id': eid, 'incident_id': incident,
        'kind': kind, 'state': state,
        'component': 'RECOVERY' if kind in ('RESTART_REQUEST', 'RECOVERY_CHECKPOINT')
                     else 'MARKET_DATA',
        'event_at_utc': z(moment),
        'recorded_at_utc': z(moment + timedelta(seconds=10)),
        'previous_event_sha256': previous,
    }
    e['event_sha256'] = _digest(e)
    return e


def packet(report, t=None):
    t = at() if t is None else t
    events = []
    items = (
        ('EVT_A1', 'INCIDENT_A', 'DATA_STALE', 'OPEN', t - timedelta(minutes=45)),
        ('EVT_A2', 'INCIDENT_A', 'RECOVERY_CHECKPOINT', 'REPORTED_RESOLVED',
         t - timedelta(minutes=25)),
        ('EVT_B1', 'INCIDENT_B', 'RESTART_REQUEST', 'OPEN', t - timedelta(minutes=15)),
    )
    previous = ZERO_SHA
    for eid, group, kind, state, moment in items:
        e = event(eid, group, kind, state, moment, previous)
        events.append(e)
        previous = e['event_sha256']
    return {
        'schema_version': 1, 'packet_id': 'TEST_LOCAL_CHAIN_001',
        'historical_epoch_id': EPOCH, 'as_of_utc': z(t),
        'declared_at_utc': z(t - timedelta(days=1)),
        'source_report_sha256': report['report_sha256'],
        'source_class': 'LOCAL_UNATTESTED_CLAIMS',
        'mode': INCIDENT_VERSION,
        'events': events, 'head_sha256': previous, 'final_action': 'HOLD',
    }


def input_at(t=None):
    t = at() if t is None else t
    source = synthetic_stage11_report(t)
    return source, packet(source, t), t


def assess(source=None, log=None, prior=None, t=None, epoch=EPOCH):
    return assess_offline_monitoring_recovery(
        epoch_id=epoch, as_of=z(at() if t is None else t), report=source,
        incident_packet=log, prior_incident_packet=prior,
    )


def fail_edit(mutate, *, rehash=False):
    source, log, t = input_at()
    mutate(log)
    if rehash:
        chain = ZERO_SHA
        for item in log['events']:
            item['previous_event_sha256'] = chain
            item['event_sha256'] = _digest({k: v for k, v in item.items()
                                            if k != 'event_sha256'})
            chain = item['event_sha256']
        log['head_sha256'] = chain
    with pytest.raises(OfflineRecoveryError):
        assess(source, log, t=t)


# Monitoring does not invent a healthy process, approvals, sources or outcomes.

def test_no_source_and_no_incident_log_fail_closed():
    r = assess()
    assert r['incident_status'] == 'NOT_SUPPLIED'
    assert r['source_classification'] == 'NO_STAGE11_REPORT_SUPPLIED'
    assert r['alert_count'] == 6
    assert r['verified_external_gates'] == 0
    assert r['verified_source_families'] == 0
    assert r['final_action'] == 'HOLD'


def test_missing_incidents_are_unknown_not_proof_of_no_incidents():
    source, log, t = input_at()
    r = assess(source, t=t)
    assert r['incident_replay'] is None
    assert 'INCIDENT_LOG_NOT_SUPPLIED' in [a['code'] for a in r['alert_evaluations']]
    assert r['historical_evidence_independently_attested'] is False


def test_local_replay_groups_repeated_incident_without_self_certifying_resolution():
    source, log, t = input_at()
    r = assess(source, log, t=t)
    history = r['incident_replay']
    assert r['incident_status'] == 'LOCAL_HASH_CHAIN_REPLAY_UNATTESTED'
    assert history['event_count'] == 3
    assert history['incident_count'] == 2
    a = next(x for x in history['incident_summary'] if x['incident_id'] == 'INCIDENT_A')
    assert a['event_count'] == 2
    assert a['local_state'] == 'LOCAL_REPORTED_RESOLVED_UNVERIFIED'
    assert a['independently_closed'] is False
    assert a['restart_safe'] is False


def test_alert_evaluation_not_network_notification():
    source, log, t = input_at()
    r = assess(source, log, t=t)
    assert r['alert_count'] == 7
    assert r['alerts_delivered'] == 0
    assert r['automated_alert_dispatch_enabled'] is False
    assert any(a['code'] == 'LOCAL_INCIDENT_INCIDENT_B' and a['severity'] == 'CRITICAL'
               for a in r['alert_evaluations'])


def test_replay_timeline_carries_local_hashes_not_runtime_actions():
    source, log, t = input_at()
    events = assess(source, log, t=t)['incident_replay']['replay_timeline']
    assert len(events) == 3
    assert [x['ordinal'] for x in events] == [1, 2, 3]
    assert [x['kind'] for x in events] == [
        'DATA_STALE', 'RECOVERY_CHECKPOINT', 'RESTART_REQUEST']
    assert all(not x['independently_verified'] for x in events)
    assert all(not x['runtime_action_performed'] for x in events)
    assert events[-1]['event_sha256'] == log['head_sha256']


def test_recovery_ten_independent_gates_all_blocked():
    source, log, t = input_at()
    r = assess(source, log, t=t)['recovery_safety']
    assert r['required_checks_total'] == 10
    assert r['required_checks_verified'] == 0
    assert all(not x['independently_verified'] for x in r['required_independent_gates'])
    assert not r['collector_restart_permitted']
    assert not r['runtime_replay_permitted']
    assert not r['paper_trading_permitted']
    assert not r['rollback_activated']


def test_stage11_full_operational_acceptance_remains_outstanding():
    source, log, t = input_at()
    r = assess(source, log, t=t)['engineering_acceptance']
    assert r['offline_alert_rule_design_reviewable'] is True
    assert r['local_incident_hash_replay_checked'] is True
    assert r['stage11_full_operational_exit_met'] is False
    assert r['real_host_fault_drill_completed'] is False
    assert r['paper_order_lifecycle_verified'] is False
    assert r['final_action'] == 'HOLD'


def test_report_deterministic_and_digest_replays():
    source, log, t = input_at()
    a, b = assess(source, log, t=t), assess(source, log, t=t)
    assert a == b
    assert a['report_sha256'] == _digest({k: v for k, v in a.items()
                                           if k != 'report_sha256'})
    assert a['network_accessed'] is False
    assert a['evidence_records_modified'] is False


def test_empty_local_packet_does_not_certify_host_health():
    source, log, t = input_at()
    log['events'] = []
    log['head_sha256'] = ZERO_SHA
    r = assess(source, log, t=t)
    assert r['incident_replay']['event_count'] == 0
    assert r['incident_status'] == 'LOCAL_HASH_CHAIN_REPLAY_UNATTESTED'
    assert r['recovery_safety']['runtime_restart_permitted'] is False
    assert r['alerts_delivered'] == 0


# Chain integrity, causality, replay and append-only local prefix comparisons.

def test_altered_event_fails_even_when_packet_head_is_unchanged():
    fail_edit(lambda d: d['events'][0].update(kind='CLOCK_MISMATCH'))


def test_rehashing_all_event_hashes_does_not_prove_external_custody():
    source, log, t = input_at()
    log['events'][0]['kind'] = 'CLOCK_MISMATCH'
    chain = ZERO_SHA
    for e in log['events']:
        e['previous_event_sha256'] = chain
        e['event_sha256'] = _digest({k:v for k,v in e.items() if k != 'event_sha256'})
        chain = e['event_sha256']
    log['head_sha256'] = chain
    r = assess(source, log, t=t)
    assert r['incident_replay']['local_chain_proves_independent_custody'] is False
    assert r['recovery_safety']['runtime_restart_permitted'] is False


def test_broken_previous_link_fails():
    fail_edit(lambda d: d['events'][1].update(previous_event_sha256='f'*64))


def test_altered_head_refused():
    fail_edit(lambda d: d.update(head_sha256='f'*64))


def test_reordered_events_with_recomputed_hashes_refused():
    fail_edit(lambda d: d['events'].reverse(), rehash=True)


def test_duplicate_event_id_with_new_hashes_refused():
    fail_edit(lambda d: d['events'][2].update(event_id=d['events'][0]['event_id']), rehash=True)


def test_mislabelled_historical_epoch_refused():
    fail_edit(lambda d: d.update(historical_epoch_id='f'*64))


def test_incident_source_report_hash_mismatch_refused():
    fail_edit(lambda d: d.update(source_report_sha256='f'*64))


def test_future_event_recording_refused():
    fail_edit(lambda d: d['events'][2].update(
        recorded_at_utc=z(at() + timedelta(days=1))), rehash=True)


def test_event_recorded_before_it_occurred_refused():
    fail_edit(lambda d: d['events'][0].update(
        recorded_at_utc=z(at() - timedelta(days=5))), rehash=True)


def test_event_before_packet_declaration_refused():
    fail_edit(lambda d: d.update(declared_at_utc=z(at() - timedelta(minutes=1))))


def test_resolution_without_prior_open_refused():
    fail_edit(lambda d: d['events'][0].update(state='REPORTED_RESOLVED'), rehash=True)


def test_source_class_cannot_be_self_certified():
    fail_edit(lambda d: d.update(source_class='INDEPENDENT_BROKER_RECEIPT'))


def test_packet_cannot_enable_orders_or_restarts():
    fail_edit(lambda d: d.update(runtime_restart_permitted=True))


def test_packet_cannot_announce_trade_action():
    fail_edit(lambda d: d.update(final_action='BUY'))


def test_future_packet_cutoff_refused():
    fail_edit(lambda d: d.update(as_of_utc=z(at() + timedelta(days=1))))


def test_boolean_schema_version_refused():
    fail_edit(lambda d: d.update(schema_version=True))


def test_missing_required_field_refused():
    fail_edit(lambda d: d.pop('mode'))


@pytest.mark.parametrize('field,value', [
    ('kind', 'RUN_LIVE_ORDER'), ('state', 'VERIFIED_RESOLVED'),
    ('component', 'EXCHANGE_SERVER'), ('event_id', '../../escape'),
    ('incident_id', '<script>bad</script>'),
    ('event_at_utc', '2026-10-30T20:00:00+03:00'),
])
def test_invalid_event_detail_cannot_enter_timeline(field, value):
    fail_edit(lambda d: d['events'][0].update({field:value}), rehash=True)


def test_event_schema_cannot_carry_raw_secrets():
    fail_edit(lambda d: d['events'][0].update(raw_provider_token='password'))


def test_packet_event_limit_bounded():
    source, log, t = input_at()
    log['events'] = [{} for _ in range(MAX_EVENTS+1)]
    with pytest.raises(OfflineRecoveryError):
        assess(source, log, t=t)


def test_packet_requires_preexisting_source_report():
    source, log, t = input_at()
    with pytest.raises(OfflineRecoveryError, match='requires original'):
        assess(None, log, t=t)


def test_standalone_prior_packet_rejected():
    source, log, t = input_at()
    with pytest.raises(OfflineRecoveryError):
        assess(source, None, log, t=t)


def test_local_prior_prefix_comparison_detects_rewrite():
    source, log, t = input_at()
    previous = deepcopy(log)
    previous['events'] = previous['events'][:2]
    previous['head_sha256'] = previous['events'][-1]['event_sha256']
    current = deepcopy(log)
    current['events'][0]['kind'] = 'CLOCK_MISMATCH'
    chain = ZERO_SHA
    for e in current['events']:
        e['previous_event_sha256'] = chain
        e['event_sha256'] = _digest({k:v for k,v in e.items() if k != 'event_sha256'})
        chain = e['event_sha256']
    current['head_sha256'] = chain
    with pytest.raises(OfflineRecoveryError, match='rewritten'):
        assess(source, current, previous, t=t)


def test_local_unchanged_prior_prefix_is_reported_as_unattested():
    source, log, t = input_at()
    previous = deepcopy(log)
    previous['events'] = previous['events'][:2]
    previous['head_sha256'] = previous['events'][-1]['event_sha256']
    r = assess(source, log, previous, t=t)
    assert r['incident_replay']['prior_local_history_prefix_equal'] is True
    assert r['incident_replay']['local_chain_proves_independent_custody'] is False
    assert r['engineering_acceptance']['local_prior_history_append_only_checked'] is True


def test_local_prior_prefix_larger_than_current_refused():
    source, log, t = input_at()
    current = deepcopy(log)
    current['events'] = current['events'][:2]
    current['head_sha256'] = current['events'][-1]['event_sha256']
    with pytest.raises(OfflineRecoveryError):
        assess(source, current, log, t=t)


def test_local_prior_packet_must_share_asof_and_report_binding():
    source, log, t = input_at()
    prior = deepcopy(log)
    prior['as_of_utc'] = z(t - timedelta(seconds=1))
    with pytest.raises(OfflineRecoveryError, match='same review cutoff'):
        assess(source, log, prior, t=t)


def test_local_prior_packet_never_proves_immutability():
    source, log, t = input_at()
    result = assess(source, log, deepcopy(log), t=t)
    assert result['incident_replay']['prior_local_history_prefix_equal'] is True
    assert result['historical_evidence_independently_attested'] is False


# HTML extends, rather than replaces, the original 8 views.

class Sections(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sections = []
        self.links = []
        self.tags = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        self.tags.append(tag)
        if tag == 'section':
            self.sections.append(d.get('id'))
        if tag == 'a':
            self.links.append(d.get('href'))


def test_dashboard_gains_two_integrated_sections_not_another_server():
    source, log, t = input_at()
    html = render_monitoring_recovery_html(
        epoch_id=EPOCH, as_of=z(t), report=source, incident_packet=log)
    d = Sections(); d.feed(html)
    assert d.sections == [
        'overview', 'sources', 'gates', 'council', 'research',
        'health', 'models', 'audit', 'incidents', 'recovery']
    assert '#incidents' in d.links and '#recovery' in d.links
    assert all(x not in d.tags for x in ('script', 'form', 'iframe', 'button', 'input'))
    assert 'FINAL=HOLD' in html
    assert 'No trading authorization' in html
    assert 'INCIDENT_B' in html
    assert 'Restart permission' in html


def test_dashboard_no_events_is_marked_not_supplied():
    html = render_monitoring_recovery_html(epoch_id=EPOCH, as_of=z(at()))
    assert 'No Stage 11 report supplied' in html
    assert 'Monitoring &amp; incident replay' in html
    assert 'None supplied' in html
    assert 'FINAL=HOLD' in html


def test_dashboard_never_injects_market_prices_or_raw_credentials():
    source, log, t = input_at()
    source['secret_note'] = 'MY_PRIVATE_API_KEY'
    source['report_sha256'] = _digest({k:v for k,v in source.items() if k != 'report_sha256'})
    log['source_report_sha256'] = source['report_sha256']
    html = render_monitoring_recovery_html(
        epoch_id=EPOCH, as_of=z(t), report=source, incident_packet=log)
    assert 'MY_PRIVATE_API_KEY' not in html
    assert 'Not available' in html
    assert 'No' in html


def test_dashboard_cannot_render_future_cutoff():
    with pytest.raises(OfflineRecoveryError):
        render_monitoring_recovery_html(
            epoch_id=EPOCH, as_of=z(datetime.now(UTC)+timedelta(days=1)))


def test_dashboard_invalid_source_rejected_before_html():
    source, log, t = input_at()
    source['final_action'] = 'TRADE'
    source['report_sha256'] = _digest({k:v for k,v in source.items() if k!='report_sha256'})
    with pytest.raises(OfflineRecoveryError):
        render_monitoring_recovery_html(epoch_id=EPOCH, as_of=z(t), report=source)


def test_dashboard_same_cutoff_and_incidents_is_deterministic():
    source, log, t = input_at()
    a = render_monitoring_recovery_html(
        epoch_id=EPOCH, as_of=z(t), report=source, incident_packet=log)
    b = render_monitoring_recovery_html(
        epoch_id=EPOCH, as_of=z(t), report=source, incident_packet=log)
    assert a == b
    assert len(a.encode('utf-8')) < 256_000


# Offline CLI: explicit output creation only, no source/event file modification.

def write_inputs(tmp_path, *, report=None, packet_doc=None):
    t = at()
    report = synthetic_stage11_report(t) if report is None else report
    packet_doc = packet(report, t) if packet_doc is None else packet_doc
    r = tmp_path / 'source.json'
    e = tmp_path / 'incidents.json'
    r.write_text(json.dumps(report, sort_keys=True), encoding='utf-8')
    e.write_text(json.dumps(packet_doc, sort_keys=True), encoding='utf-8')
    return r, e, t


def args(t):
    return ['--read-only-monitor', '--offline-only', '--epoch-id', EPOCH,
            '--as-of', z(t)]


def test_cli_basic_inspection_no_file_creation(capsys):
    assert run(args(at())) == 0
    output = capsys.readouterr().out
    assert 'FINAL=HOLD' in output
    assert 'Alert evaluations: 6 | Alerts sent: 0' in output


def test_cli_json_snapshot_returns_strict_hold(capsys):
    assert run(args(at()) + ['--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['alert_count'] == 6
    assert report['recovery_safety']['required_checks_verified'] == 0
    assert report['final_action'] == 'HOLD'


def test_cli_valid_local_incident_packet_json(tmp_path, capsys):
    r, i, t = write_inputs(tmp_path)
    original = (sha256(r.read_bytes()).hexdigest(), sha256(i.read_bytes()).hexdigest())
    assert run(args(t) + ['--report-file', str(r), '--incident-file', str(i), '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['incident_replay']['event_count'] == 3
    assert result['final_action'] == 'HOLD'
    assert original == (sha256(r.read_bytes()).hexdigest(), sha256(i.read_bytes()).hexdigest())


def test_cli_html_export_is_new_local_snapshot(tmp_path, capsys):
    r, i, t = write_inputs(tmp_path)
    output = tmp_path / 'mission_monitor_recovery.html'
    assert run(args(t) + ['--report-file', str(r), '--incident-file', str(i),
                           '--output-html', str(output)]) == 0
    html = output.read_text(encoding='utf-8')
    assert 'Monitoring &amp; incident replay' in html
    assert 'Recovery &amp; phase handover' in html
    assert 'FINAL=HOLD' in html
    assert 'NO COLLECTOR RESTART' in capsys.readouterr().out


def test_cli_existing_export_never_overwritten(tmp_path, capsys):
    r, i, t = write_inputs(tmp_path)
    output = tmp_path / 'existing.html'
    output.write_text('original', encoding='utf-8')
    assert run(args(t) + ['--report-file', str(r), '--output-html', str(output)]) == 1
    assert output.read_text(encoding='utf-8') == 'original'
    assert 'BLOCKED' in capsys.readouterr().out


def test_cli_rejects_invalid_source_and_does_not_export(tmp_path, capsys):
    r, i, t = write_inputs(tmp_path)
    source = json.loads(r.read_text())
    source['final_action'] = 'BUY'
    r.write_text(json.dumps(source))
    output = tmp_path / 'blocked.html'
    assert run(args(t) + ['--report-file', str(r), '--output-html', str(output)]) == 1
    assert not output.exists()
    assert 'BLOCKED' in capsys.readouterr().out


def test_cli_rejects_oversized_log(tmp_path, capsys):
    r, i, t = write_inputs(tmp_path)
    i.write_bytes(b' ' * (196608 + 1))
    assert run(args(t) + ['--report-file', str(r), '--incident-file', str(i)]) == 1
    assert 'BLOCKED' in capsys.readouterr().out


def test_cli_rejects_json_duplicate_fields(tmp_path, capsys):
    r, i, t = write_inputs(tmp_path)
    i.write_text('{"events":[],"events":[]}', encoding='utf-8')
    assert run(args(t) + ['--report-file', str(r), '--incident-file', str(i)]) == 1
    assert 'BLOCKED' in capsys.readouterr().out


def test_cli_rejects_symlink_log_if_supported(tmp_path, capsys):
    r, i, t = write_inputs(tmp_path)
    link = tmp_path / 'alias.json'
    try:
        link.symlink_to(i)
    except (OSError, NotImplementedError):
        pytest.skip('Windows cannot create symlinks')
    assert run(args(t) + ['--report-file', str(r), '--incident-file', str(link)]) == 1
    assert 'BLOCKED' in capsys.readouterr().out


def test_cli_mandatory_flags(capsys):
    with pytest.raises(SystemExit) as ex:
        run(['--epoch-id', EPOCH, '--as-of', z(at())])
    assert ex.value.code == 2


def test_cli_cannot_request_json_and_html_simultaneously(tmp_path):
    with pytest.raises(SystemExit) as ex:
        run(args(at()) + ['--json', '--output-html', str(tmp_path/'report.html')])
    assert ex.value.code == 2


def test_integration_with_committed_stage11_read_model_if_installed(capsys):
    try:
        from backend.runtime.stage11_mission_control_read_models import run as original
    except ModuleNotFoundError as exc:
        if exc.name == 'backend.runtime.stage11_mission_control_read_models':
            pytest.skip('Original Windows Stage 11 read-model module absent locally')
        raise
    t = at()
    policy = Path(__file__).resolve().parents[2] / 'docs/clock_profiles/hfm_forward_20261027.json'
    assert original([
        '--read-only-mission-control', '--offline-only', '--epoch-id', EPOCH,
        '--as-of', z(t), '--policy-file', str(policy), '--json']) == 0
    source = json.loads(capsys.readouterr().out)
    response = assess(source, t=t)
    assert response['source_report_sha256'] == source['report_sha256']
    assert response['final_action'] == 'HOLD'
    html = render_monitoring_recovery_html(epoch_id=EPOCH, as_of=z(t), report=source)
    assert source['report_sha256'] in html
    assert 'Monitoring &amp; incident replay' in html
    assert 'FINAL=HOLD' in html

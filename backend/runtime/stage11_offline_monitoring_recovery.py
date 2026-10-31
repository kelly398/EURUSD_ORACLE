
"""EURUSD ORACLE Stage 11: offline monitoring, incident replay and recovery HOLD.

Reuses the committed Stage 11 API and HTML dashboard. Reads only bounded
untrusted local JSON. Alerting is diagnostic output, not a delivered alert.
Incident timestamps, hashes and source claims are NOT independent receipts.
Restart, replay into runtime, network, broker, paper orders and model promotion
are forbidden unconditionally. An optional HTML snapshot requires explicit
--output-html, creates a new file, and never overwrites a record. FINAL=HOLD.
"""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re

from backend.runtime.stage11_mission_control_offline_api import (
    EPOCH, API_PREFIX, OfflineMissionControlAPIError, _digest, _timestamp,
    build_offline_api_response, read_offline_report,
)
from backend.runtime.stage11_mission_control_static_dashboard import (
    OfflineDashboardError, MAX_HTML_BYTES, render_offline_dashboard,
    write_new_html_snapshot, _e, _table, _panel_pairs,
)

VERSION = 'EURUSD_STAGE11_OFFLINE_MONITORING_RECOVERY_HANDOVER_V1'
INCIDENT_VERSION = 'LOCAL_UNATTESTED_INCIDENT_CHAIN_V1'
MAX_EVENTS = 64
MAX_INCIDENT_BYTES = 196608
UTC = timezone.utc
ZERO_SHA = '0' * 64
ID = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
SHA = re.compile(r'^[0-9a-f]{64}$')
INCIDENT_KEYS = {
    'schema_version', 'packet_id', 'historical_epoch_id', 'as_of_utc',
    'declared_at_utc', 'source_report_sha256', 'source_class', 'mode',
    'events', 'head_sha256', 'final_action',
}
EVENT_KEYS = {
    'event_id', 'incident_id', 'kind', 'state', 'component',
    'event_at_utc', 'recorded_at_utc', 'previous_event_sha256',
    'event_sha256',
}
KINDS = {
    'COLLECTOR_STATE_UNKNOWN', 'DATA_STALE', 'CLOCK_MISMATCH',
    'SOURCE_OUTAGE', 'SOURCE_FAMILY_MISSING', 'DUPLICATE_CAPTURE',
    'COUNCIL_DEADLINE_MISS', 'RISK_VETO', 'RESTART_REQUEST',
    'RECOVERY_CHECKPOINT', 'MANUAL_REVIEW',
}
COMPONENTS = {
    'COLLECTOR', 'MARKET_DATA', 'CLOCK', 'SOURCE_PROVENANCE',
    'COUNCIL', 'RISK_OFFICER', 'RECOVERY', 'HOST',
}
STATES = {'OPEN', 'UNKNOWN', 'REPORTED_RESOLVED'}
CRITICAL = {'RISK_VETO', 'RESTART_REQUEST', 'CLOCK_MISMATCH'}
RECOVERY_GATES = (
    'INDEPENDENT_BROKER_AND_CLOCK_ATTESTATION',
    'ORIGINAL_CAPTURE_CUSTODY',
    'ALL_REQUIRED_SOURCE_FAMILIES_ADMITTED',
    'PROSPECTIVE_RESEARCH_EXIT_AND_FROZEN_HOLDOUT',
    'HOST_PROCESS_AND_LAUNCHER_STATE_ATTESTED',
    'QUIESCENCE_AND_CONCURRENT_WRITER_EXCLUSION',
    'DURABLE_CHECKPOINT_PROVENANCE_AND_BACKUP',
    'DUPLICATE_REPLAY_AND_IDEMPOTENCY_PROVEN',
    'RISK_OFFICER_VETO_CLEAR_INDEPENDENTLY',
    'SIGNED_OPERATOR_RECOVERY_AUTHORIZATION',
)


class OfflineRecoveryError(ValueError):
    """An incident packet or recovery claim does not meet the local contract."""


def _exact(obj, fields, label):
    if type(obj) is not dict or set(obj) != fields:
        raise OfflineRecoveryError('Strict schema mismatch: ' + label)


def _id(value, label):
    if type(value) is not str or ID.fullmatch(value) is None:
        raise OfflineRecoveryError('Unsafe ' + label)
    return value


def _sha(value, label):
    if type(value) is not str or SHA.fullmatch(value) is None:
        raise OfflineRecoveryError('Invalid lowercase digest: ' + label)
    return value


def _time(value):
    try:
        # Original Stage 11 parser supports canonical UTC Z or +00:00.
        return _timestamp(value)
    except (OfflineMissionControlAPIError, ValueError, TypeError):
        raise OfflineRecoveryError('UTC timestamp required.') from None


def _incident_file(path):
    p = Path(path)
    try:
        if p.stat().st_size > MAX_INCIDENT_BYTES:
            raise OfflineRecoveryError('Incident log exceeds bounded size.')
        return read_offline_report(p)
    except (OfflineMissionControlAPIError, OSError, ValueError, TypeError):
        raise OfflineRecoveryError('Unsafe or malformed incident JSON.') from None


def _validate_packet(packet, *, source_sha, cutoff, prior=None):
    _exact(packet, INCIDENT_KEYS, 'local_incident_packet')
    _id(packet['packet_id'], 'packet_id')
    if (type(packet['schema_version']) is not int
            or packet['schema_version'] != 1
            or packet['historical_epoch_id'] != EPOCH
            or _time(packet['as_of_utc']) != cutoff
            or packet['source_report_sha256'] != source_sha
            or packet['source_class'] != 'LOCAL_UNATTESTED_CLAIMS'
            or packet['mode'] != INCIDENT_VERSION
            or packet['final_action'] != 'HOLD'):
        raise OfflineRecoveryError('Packet attempts to rebind source or authorize recovery.')
    declared = _time(packet['declared_at_utc'])
    if declared > cutoff:
        raise OfflineRecoveryError('Future packet declaration refused.')
    events = packet['events']
    if type(events) is not list or len(events) > MAX_EVENTS:
        raise OfflineRecoveryError('Bounded event list required.')
    seen, incidents, replay = set(), {}, []
    previous = ZERO_SHA
    last_time = None
    for index, event in enumerate(events):
        _exact(event, EVENT_KEYS, 'local_incident_event')
        eid = _id(event['event_id'], 'event_id')
        incident_id = _id(event['incident_id'], 'incident_id')
        if eid in seen:
            raise OfflineRecoveryError('Duplicate event identity.')
        seen.add(eid)
        if event['kind'] not in KINDS or event['state'] not in STATES:
            raise OfflineRecoveryError('Unknown incident kind or state.')
        if event['component'] not in COMPONENTS:
            raise OfflineRecoveryError('Unsupported local component.')
        occurred = _time(event['event_at_utc'])
        recorded = _time(event['recorded_at_utc'])
        if not (declared <= occurred <= recorded <= cutoff):
            raise OfflineRecoveryError('Noncausal event or recording timestamp.')
        if last_time is not None and occurred < last_time:
            raise OfflineRecoveryError('Events must be chronologically ordered.')
        last_time = occurred
        if event['previous_event_sha256'] != previous:
            raise OfflineRecoveryError('Incident chain ancestry changed.')
        computed = _digest({k: v for k, v in event.items()
                            if k != 'event_sha256'})
        if computed != event['event_sha256']:
            raise OfflineRecoveryError('Local event hash mismatch.')
        prior_incident = incidents.get(incident_id)
        if (event['state'] == 'REPORTED_RESOLVED' and
                (prior_incident is None or not prior_incident['ever_open'])):
            raise OfflineRecoveryError('Resolution claim without earlier local OPEN event.')
        if prior_incident is None:
            incidents[incident_id] = {
                'incident_id': incident_id, 'first_event_at': occurred.isoformat(),
                'last_event_at': occurred.isoformat(), 'latest_kind': event['kind'],
                'latest_state': event['state'], 'event_count': 1,
                'ever_open': event['state'] == 'OPEN',
                'latest_event_sha256': computed,
            }
        else:
            prior_incident['last_event_at'] = occurred.isoformat()
            prior_incident['latest_kind'] = event['kind']
            prior_incident['latest_state'] = event['state']
            prior_incident['event_count'] += 1
            prior_incident['ever_open'] |= event['state'] == 'OPEN'
            prior_incident['latest_event_sha256'] = computed
        replay.append({
            'ordinal': index + 1, 'event_id': eid, 'incident_id': incident_id,
            'kind': event['kind'], 'local_claim_state': event['state'],
            'component': event['component'], 'event_at_utc': occurred.isoformat(),
            'recorded_at_utc': recorded.isoformat(), 'event_sha256': computed,
            'independently_verified': False, 'runtime_action_performed': False,
        })
        previous = computed
    if packet['head_sha256'] != previous:
        raise OfflineRecoveryError('Local event head digest mismatch.')
    if prior is not None:
        # A comparison of two local snapshots is NOT a custody/immutability proof.
        _exact(prior, INCIDENT_KEYS, 'previous_local_incident_packet')
        if (prior['packet_id'] != packet['packet_id']
                or prior['declared_at_utc'] != packet['declared_at_utc']
                or prior['historical_epoch_id'] != packet['historical_epoch_id']
                or prior['source_report_sha256'] != packet['source_report_sha256']
                or len(prior['events']) > len(events)
                or prior['events'] != events[:len(prior['events'])]):
            raise OfflineRecoveryError('Prior local history was rewritten or truncated.')
    grouped = []
    for key in sorted(incidents):
        item = incidents[key]
        grouped.append({
            'incident_id': key,
            'latest_kind': item['latest_kind'],
            'local_state': 'LOCAL_REPORTED_RESOLVED_UNVERIFIED'
                if item['latest_state'] == 'REPORTED_RESOLVED'
                else 'LOCAL_OPEN_OR_UNKNOWN_UNVERIFIED',
            'first_event_at': item['first_event_at'],
            'last_event_at': item['last_event_at'],
            'event_count': item['event_count'],
            'latest_event_sha256': item['latest_event_sha256'],
            'independently_closed': False,
            'restart_safe': False,
        })
    return {
        'status': 'LOCAL_HASH_CHAIN_REPLAY_UNATTESTED',
        'packet_id': packet['packet_id'],
        'head_sha256': previous,
        'event_count': len(events),
        'incident_count': len(grouped),
        'replay_timeline': replay,
        'incident_summary': grouped,
        'prior_local_history_prefix_equal': prior is not None,
        'local_chain_proves_independent_custody': False,
        'events_forwarded_to_engine': 0,
    }


def assess_offline_monitoring_recovery(*, epoch_id, as_of, report=None,
                                       incident_packet=None, prior_incident_packet=None):
    """Pure evaluation of the existing HOLD model and local incident history."""
    if epoch_id != EPOCH:
        raise OfflineRecoveryError('Historical epoch rebinding refused.')
    cutoff = _time(as_of)
    if cutoff > datetime.now(UTC):
        raise OfflineRecoveryError('Future as-of cutoff refused.')
    if incident_packet is not None and report is None:
        raise OfflineRecoveryError('Incident replay requires original Stage 11 read-model report.')
    if prior_incident_packet is not None and incident_packet is None:
        raise OfflineRecoveryError('Prior incident history cannot stand alone.')
    try:
        response = build_offline_api_response(
            epoch_id=epoch_id, as_of=as_of,
            route=API_PREFIX + '/overview', report=report, method='GET', role='VIEWER')
        health = build_offline_api_response(
            epoch_id=epoch_id, as_of=as_of,
            route=API_PREFIX + '/health', report=report, method='GET', role='VIEWER')
    except (OfflineMissionControlAPIError, ValueError, TypeError, KeyError) as exc:
        raise OfflineRecoveryError('Stage 11 original read-model contract rejected.') from exc
    if response['final_action'] != 'HOLD' or health['final_action'] != 'HOLD':
        raise OfflineRecoveryError('Upstream review boundary changed.')
    # The explicit separate checks avoid inferring a safe host state from missing data.
    if (response['body']['verified_external_gate_count'] != 0
            or response['body']['verified_source_family_count'] != 0
            or health['body']['panel']['risk_kill_switch_clear'] is not None):
        raise OfflineRecoveryError('Unexpected independently verified operations claim.')
    source_sha = response['body']['local_source_report_sha256']
    history = None
    if incident_packet is not None:
        if prior_incident_packet is not None:
            # Both packets bind one fixed as-of read-model report. The prior
            # file is a local prefix copy, NOT an earlier independent receipt.
            if _time(prior_incident_packet.get('as_of_utc')) != cutoff:
                raise OfflineRecoveryError('Prior prefix must bind same review cutoff.')
            _validate_packet(prior_incident_packet, source_sha=source_sha,
                             cutoff=cutoff)
        history = _validate_packet(incident_packet, source_sha=source_sha,
                                   cutoff=cutoff, prior=prior_incident_packet)
    alerts = [
        {'code': 'INDEPENDENT_GATES_UNVERIFIED', 'severity': 'CRITICAL',
         'state': 'BLOCKED', 'origin': 'STAGE11_READ_MODEL_CONTRACT'},
        {'code': 'SOURCE_FAMILIES_UNADMITTED', 'severity': 'CRITICAL',
         'state': 'BLOCKED', 'origin': 'STAGE11_READ_MODEL_CONTRACT'},
        {'code': 'HOST_AND_COLLECTOR_UNATTESTED', 'severity': 'HIGH',
         'state': 'UNKNOWN_HOLD', 'origin': 'STAGE11_READ_MODEL_CONTRACT'},
        {'code': 'RISK_VETO_CLEARANCE_UNKNOWN', 'severity': 'HIGH',
         'state': 'UNKNOWN_HOLD', 'origin': 'STAGE11_READ_MODEL_CONTRACT'},
        {'code': 'RECOVERY_NOT_AUTHORIZED', 'severity': 'CRITICAL',
         'state': 'BLOCKED', 'origin': 'STAGE11_READ_MODEL_CONTRACT'},
    ]
    if history is None:
        alerts.append({'code': 'INCIDENT_LOG_NOT_SUPPLIED', 'severity': 'HIGH',
                       'state': 'UNKNOWN_HOLD', 'origin': 'NO_LOCAL_INCIDENT_PACKET'})
    else:
        for incident in history['incident_summary']:
            kind = incident['latest_kind']
            alerts.append({
                'code': 'LOCAL_INCIDENT_' + incident['incident_id'],
                'severity': 'CRITICAL' if kind in CRITICAL else 'HIGH',
                'state': incident['local_state'],
                'origin': 'LOCAL_UNATTESTED_CLAIM',
            })
    restart = {
        'restart_assessment': 'PROHIBITED_NOT_INDEPENDENTLY_AUTHORIZED',
        'required_independent_gates': [
            {'check': code, 'independently_verified': False}
            for code in RECOVERY_GATES
        ],
        'required_checks_verified': 0,
        'required_checks_total': len(RECOVERY_GATES),
        'runtime_restart_permitted': False,
        'collector_restart_permitted': False,
        'paper_trading_permitted': False,
        'runtime_replay_permitted': False,
        'rollback_activated': False,
        'idempotent_recovery_verified': False,
        'dry_run_recovery_dispatched': False,
        'runbook_next_steps': [
            'REVIEW_LOCAL_INCIDENT_TIMELINE_WITHOUT_EXECUTION',
            'OBTAIN_INDEPENDENT_CLOCK_CAPTURE_AND_SOURCE_ATTESTATION',
            'VERIFY_PROCESS_QUIESCENCE_AND_DURABLE_CHECKPOINTS_SEPARATELY',
            'REQUIRE_SIGNED_OPERATOR_RECOVERY_APPROVAL_BEFORE_FUTURE_CHANGE',
        ],
    }
    acceptance = {
        'offline_alert_rule_design_reviewable': True,
        'local_incident_hash_replay_checked': history is not None,
        'local_prior_history_append_only_checked':
            history is not None and history['prior_local_history_prefix_equal'],
        'recovery_safety_denials_enforced': True,
        'independent_incident_custody_verified': False,
        'real_host_fault_drill_completed': False,
        'durable_scheduler_or_alert_delivery_completed': False,
        'independent_recovery_and_restart_approval': False,
        'paper_order_lifecycle_verified': False,
        'stage11_full_operational_exit_met': False,
        'offline_engineering_handover_status': 'LOCAL_DIAGNOSTIC_CONTRACT_REVIEWABLE',
        'remaining_requirements': [
            'INDEPENDENT_EVIDENCE_GATES_AND_SOURCE_ADMISSION',
            'ACTUAL_MONITORING_AND_ALERT_DELIVERY_VERIFICATION',
            'PROCESS_STOP_START_AND_DUPLICATE_CAPTURE_DRILL',
            'INDEPENDENT_RECOVERY_CHECKPOINT_CUSTODY',
            'SEPARATE_PAPER_TRADING_AUTHORIZATION_AND_LIFECYCLE',
        ],
        'final_action': 'HOLD',
    }
    result = {
        'version': VERSION,
        'scope': 'STAGE11_OFFLINE_MONITORING_INCIDENT_REPLAY_RECOVERY_HANDOVER',
        'historical_epoch_id': EPOCH, 'as_of_utc': cutoff.isoformat(),
        'source_report_sha256': source_sha,
        'source_classification': response['body']['source_classification'],
        'incident_status': 'NOT_SUPPLIED' if history is None else history['status'],
        'incident_replay': history,
        'alert_evaluations': alerts,
        'alert_count': len(alerts),
        'alerts_delivered': 0,
        'automated_alert_dispatch_enabled': False,
        'historical_evidence_independently_attested': False,
        'recovery_safety': restart,
        'engineering_acceptance': acceptance,
        'verified_source_families': 0,
        'verified_external_gates': 0,
        'research_decision': 'NOT_ENOUGH_EVIDENCE',
        'read_only': True, 'network_accessed': False,
        'broker_connected': False, 'collector_started': False,
        'council_invoked': False, 'model_loaded': False,
        'training_authorized': False, 'orders_authorized': False,
        'operator_authorization_granted': False, 'runtime_restarted': False,
        'evidence_records_modified': False, 'final_action': 'HOLD',
    }
    result['report_sha256'] = _digest(result)
    return result


def render_monitoring_recovery_html(*, epoch_id, as_of, report=None,
                                    incident_packet=None, prior_incident_packet=None):
    """Inject two read-only sections into the existing verified HTML snapshot."""
    audit = assess_offline_monitoring_recovery(
        epoch_id=epoch_id, as_of=as_of, report=report,
        incident_packet=incident_packet, prior_incident_packet=prior_incident_packet)
    try:
        html = render_offline_dashboard(epoch_id=epoch_id, as_of=as_of,
                                        report=report, role='VIEWER')
    except (OfflineMissionControlAPIError, OfflineDashboardError, ValueError, TypeError) as exc:
        raise OfflineRecoveryError('Static dashboard contract changed.') from exc
    log = audit['incident_replay']
    events = [] if log is None else log['replay_timeline']
    event_rows = [(x['ordinal'], x['incident_id'], x['kind'], x['local_claim_state'],
                   x['event_at_utc']) for x in events]
    if not event_rows:
        event_rows = [('—', 'None supplied', 'Unknown', 'Unknown', 'Not supplied')]
    alert_rows = [(x['code'], x['severity'], x['state'], x['origin'])
                  for x in audit['alert_evaluations']]
    inc = (
        '<section id="incidents" class="section" aria-labelledby="heading-incidents">'
        '<h2 id="heading-incidents">Monitoring &amp; incident replay</h2>'
        '<p class="lead">Diagnostic alerts only. No notification sent; local event claims '
        'are not independent process evidence.</p>'
        + _panel_pairs([
            ('Local incident log', audit['incident_status']),
            ('Events replayed', 'Unknown' if log is None else log['event_count']),
            ('Local hash-chain digest', 'Not supplied' if log is None else log['head_sha256']),
            ('Independent source receipt verified', 'No'),
            ('Alerts delivered', 0),
        ])
        + _table('Safety alert evaluations', ['Code', 'Severity', 'State', 'Origin'], alert_rows)
        + _table('Local incident event timeline',
                 ['No.', 'Incident', 'Kind', 'Local state', 'Claimed time (UTC)'], event_rows)
        + '</section>')
    rec = (
        '<section id="recovery" class="section" aria-labelledby="heading-recovery">'
        '<h2 id="heading-recovery">Recovery &amp; phase handover</h2>'
        '<p class="lead">Runbook-only inspection. Restart and runtime replay remain prohibited.</p>'
        + _panel_pairs([
            ('Restart permission', 'DENIED'),
            ('Independently verified recovery checks', '0 / 10'),
            ('Evidence-backed runtime recovery', 'Not established'),
            ('Automated alert delivery', 'Not implemented'),
            ('Stage 11 full operational exit', 'Not met'),
            ('Final action', 'HOLD'),
        ])
        + _table('Independent recovery prerequisites', ['Requirement', 'Verification'],
                 [(x['check'], 'NOT VERIFIED') for x in
                  audit['recovery_safety']['required_independent_gates']])
        + '<p class="hint">Local digest and prefix checks verify internal consistency only, '
        'not independent custody or operator authorization.</p></section>')
    nav_anchor = '</nav><div class="side-note">'
    footer_anchor = '<footer><strong>EUR/USD ORACLE'
    if html.count(nav_anchor) != 1 or html.count(footer_anchor) != 1:
        raise OfflineRecoveryError('Pinned dashboard HTML anchor changed.')
    html = html.replace(nav_anchor,
                        '<a href="#incidents">Monitoring &amp; incidents</a>'
                        '<a href="#recovery">Recovery &amp; handover</a>' + nav_anchor, 1)
    html = html.replace(footer_anchor, inc + rec + footer_anchor, 1)
    if len(html.encode('utf-8')) > MAX_HTML_BYTES:
        raise OfflineRecoveryError('Extended static HTML exceeds size limit.')
    return html


def run(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--read-only-monitor', required=True, action='store_true')
    p.add_argument('--offline-only', required=True, action='store_true')
    p.add_argument('--epoch-id', required=True)
    p.add_argument('--as-of', required=True)
    p.add_argument('--report-file')
    p.add_argument('--incident-file')
    p.add_argument('--prior-incident-file')
    choice = p.add_mutually_exclusive_group()
    choice.add_argument('--json', action='store_true')
    choice.add_argument('--output-html')
    a = p.parse_args(argv)
    try:
        report = None if a.report_file is None else read_offline_report(a.report_file)
        packet = None if a.incident_file is None else _incident_file(a.incident_file)
        prior = None if a.prior_incident_file is None else _incident_file(a.prior_incident_file)
        result = assess_offline_monitoring_recovery(
            epoch_id=a.epoch_id, as_of=a.as_of, report=report,
            incident_packet=packet, prior_incident_packet=prior)
        html_hash = None
        if a.output_html:
            html = render_monitoring_recovery_html(
                epoch_id=a.epoch_id, as_of=a.as_of, report=report,
                incident_packet=packet, prior_incident_packet=prior)
            html_hash = write_new_html_snapshot(a.output_html, html)
    except (OfflineMissionControlAPIError, OfflineDashboardError,
            OfflineRecoveryError, OSError, ValueError, TypeError, KeyError):
        print('STAGE 11 RECOVERY BLOCKED: Untrusted local timeline or source mismatch.')
        return 1
    if a.json:
        print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    else:
        print('EURUSD ORACLE | Stage 11 offline monitoring and recovery handover')
        print('Source: ' + result['source_classification'])
        print('Incident log: ' + result['incident_status'])
        print('Alert evaluations: %d | Alerts sent: 0' % result['alert_count'])
        print('Verified recovery gates: 0/10 | Restart: DENIED')
        print('Operational Stage 11 exit: NOT MET')
        if html_hash:
            print('New local HTML snapshot: ' + str(a.output_html))
            print('HTML SHA-256: ' + html_hash)
        print('Report SHA-256: ' + result['report_sha256'])
        print('NO COLLECTOR RESTART | NO BROKER | NO ORDERS | FINAL=HOLD')
    return 0


def main():
    raise SystemExit(run())


if __name__ == '__main__':
    main()

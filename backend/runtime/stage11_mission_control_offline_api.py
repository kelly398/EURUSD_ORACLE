
"""EURUSD ORACLE Stage 11 P11-05..08: offline Mission Control API views.

This is a deterministic, in-process GET/HEAD response adapter, NOT an HTTP server.
It accepts the JSON read-model report produced by Stage 11 P11-01..04, checks
its local hash and scope, then emits an allowlisted, redacted panel projection.

It never loads raw market evidence, creates an authentication/session claim,
connects to a network, starts an agent, dispatches a Council, promotes a model,
or permits an order. No report means unknown values, not manufactured zeros.

The input is locally hash-consistent ONLY. That is not external provenance,
independent verification, a real-market feed, a completed dashboard, or a
permission to trade. FINAL=HOLD on every route and method.
"""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import stat

EPOCH = '75311e239cd5c295d3e08e03c358488fb123a46bfc6d342dd31493d95710aa33'
VERSION = 'EURUSD_STAGE11_OFFLINE_MISSION_CONTROL_API_P11_05_08_V1'
SOURCE_SCOPE = 'P11_01_TO_P11_04_MISSION_CONTROL_OFFLINE_READ_MODELS'
API_PREFIX = '/api/v1/mission-control'
ROUTES = (
    'overview', 'sources', 'gates', 'council', 'research', 'health',
    'models', 'audit',
)
MAX_REPORT_BYTES = 1_048_576
MAX_REPORTED_RECORDS = 4096
SOURCE_SLOTS = 13
GATE_SLOTS = 11
DTO_TYPES = 12
HEX64 = re.compile(r'^[0-9a-f]{64}$')
PUBLIC_ID = re.compile(r'^[A-Za-z][A-Za-z0-9_\-]{0,63}$')
UTC = timezone.utc
SAFE_ROLES = ('VIEWER', 'AUDITOR', 'OPERATOR')
SAFE_HEADERS = {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store, max-age=0',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'",
}


class OfflineMissionControlAPIError(ValueError):
    """An untrusted source report cannot be used as a Mission Control view."""


def _timestamp(value):
    if not isinstance(value, str) or not (value.endswith('Z') or value.endswith('+00:00')):
        raise OfflineMissionControlAPIError('Canonical UTC timestamp required.')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, OverflowError):
        raise OfflineMissionControlAPIError('Invalid UTC timestamp.') from None
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise OfflineMissionControlAPIError('Non-UTC report timestamp refused.')
    return parsed.astimezone(UTC)


def _digest(value):
    """Use Stage 8's original report canon if available, not a new hash law."""
    try:
        from backend.runtime.stage8_m5_offline_heartbeat import _digest as original
    except ImportError:
        # This fallback enables isolated contract tests without the whole repo.
        # It follows ORACLE's canonical sorted ASCII JSON representation.
        return sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                 separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    return original(value)


def _positive_int(value, label, upper):
    if type(value) is not int or not 0 <= value <= upper:
        raise OfflineMissionControlAPIError('Invalid nonnegative integer: ' + label)
    return value


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OfflineMissionControlAPIError('Duplicate JSON field refused.')
        result[key] = value
    return result


def _no_nonfinite(value):
    raise OfflineMissionControlAPIError('Nonfinite JSON value refused: ' + value)


def read_offline_report(path):
    """Read only an ordinary bounded JSON file; never resolve symlinks."""
    p = Path(path)
    if p.suffix.lower() != '.json' or any(q.is_symlink() for q in (p, *p.parents)):
        raise OfflineMissionControlAPIError('Regular .json file required.')
    try:
        mode = p.stat().st_mode
        size = p.stat().st_size
        if not stat.S_ISREG(mode) or not 0 < size <= MAX_REPORT_BYTES:
            raise OfflineMissionControlAPIError('Report file is unsafe or oversized.')
        raw = p.read_bytes()
    except (OSError, TypeError, ValueError):
        raise OfflineMissionControlAPIError('Report file unavailable.') from None
    if len(raw) > MAX_REPORT_BYTES:
        raise OfflineMissionControlAPIError('Report grew beyond the byte limit.')
    try:
        parsed = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_no_duplicates,
                            parse_constant=_no_nonfinite)
    except OfflineMissionControlAPIError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        raise OfflineMissionControlAPIError('Invalid or noncanonical source JSON.') from None
    if not isinstance(parsed, dict):
        raise OfflineMissionControlAPIError('Stage 11 source report must be an object.')
    return parsed


def _check_source(report, *, expected_epoch, cutoff):
    if report is None:
        return {
            'origin': 'NO_STAGE11_REPORT_SUPPLIED', 'local_report_sha256': None,
            'snapshot_status': 'NOT_SUPPLIED', 'local_record_count': None,
            'model_count': DTO_TYPES, 'source_count': SOURCE_SLOTS, 'gate_count': GATE_SLOTS,
            'source_board': None, 'gate_board': None, 'read_model_inventory': None,
        }
    if not isinstance(report, dict):
        raise OfflineMissionControlAPIError('Invalid source report type.')
    required = {
        'scope', 'historical_epoch_id', 'as_of_utc', 'report_sha256', 'final_action',
        'snapshot_status', 'record_count', 'read_model_inventory',
        'source_family_board', 'evidence_gate_board',
        'independently_verified_source_families',
        'independently_verified_external_gates',
        'latest_verified_market_price', 'verified_performance_metrics',
        'role_is_authenticated', 'stage11_ui_delivered',
        'research_operational_readiness',
    }
    if not required.issubset(report):
        raise OfflineMissionControlAPIError('Missing required original Stage 11 fields.')
    report_time = _timestamp(report['as_of_utc'])
    if (report['scope'] != SOURCE_SCOPE
            or report['historical_epoch_id'] != expected_epoch
            or report_time != cutoff
            or report['final_action'] != 'HOLD'
            or report['research_operational_readiness'] != 'BLOCKED_EXTERNAL_EVIDENCE'):
        raise OfflineMissionControlAPIError('Source scope, epoch, cutoff or HOLD mismatch.')
    sha = report['report_sha256']
    if not isinstance(sha, str) or not HEX64.fullmatch(sha):
        raise OfflineMissionControlAPIError('Missing lowercase report SHA-256.')
    try:
        computed = _digest({key: value for key, value in report.items()
                            if key != 'report_sha256'})
    except (ValueError, TypeError, OverflowError):
        raise OfflineMissionControlAPIError('Source report cannot be canonically hashed.') from None
    if computed != sha:
        raise OfflineMissionControlAPIError('Stage 11 report changed after local hash.')
    if (report['independently_verified_source_families'] != 0
            or type(report['independently_verified_source_families']) is not int
            or report['independently_verified_external_gates'] != 0
            or type(report['independently_verified_external_gates']) is not int
            or report['latest_verified_market_price'] is not None
            or report['verified_performance_metrics'] is not None
            or report['role_is_authenticated'] is not False
            or report['stage11_ui_delivered'] is not False):
        raise OfflineMissionControlAPIError('False external assurance or authentication denied.')
    for forbidden in (
        'network_accessed', 'broker_connected', 'collector_started',
        'council_invoked', 'model_loaded', 'training_authorized', 'training_executed',
        'paper_orders_authorized', 'live_orders_authorized',
        'operator_approval_granted', 'stage10_adaptive_model_selection_authorized',
    ):
        if report.get(forbidden) is not None and report.get(forbidden) is not False:
            raise OfflineMissionControlAPIError('Source report declares forbidden action: ' + forbidden)
    if report.get('read_only') is not None and report.get('read_only') is not True:
        raise OfflineMissionControlAPIError('Source report not read-only.')
    if report.get('files_written') is not None and report.get('files_written') is not False:
        raise OfflineMissionControlAPIError('Source report claims file writes.')
    for name, count in (('read_model_inventory', DTO_TYPES),
                        ('source_family_board', SOURCE_SLOTS),
                        ('evidence_gate_board', GATE_SLOTS)):
        if type(report[name]) is not list or len(report[name]) != count:
            raise OfflineMissionControlAPIError('Incomplete Stage 11 inventory: ' + name)
    _positive_int(report['record_count'], 'record_count', MAX_REPORTED_RECORDS)
    if not isinstance(report['snapshot_status'], str) or not PUBLIC_ID.fullmatch(report['snapshot_status']):
        raise OfflineMissionControlAPIError('Unsafe snapshot status.')
    return {
        'origin': 'LOCAL_STAGE11_REPORT_HASH_MATCH_UNATTESTED',
        'local_report_sha256': sha,
        'snapshot_status': report['snapshot_status'],
        'local_record_count': report['record_count'],
        'model_count': DTO_TYPES,
        'source_count': SOURCE_SLOTS,
        'gate_count': GATE_SLOTS,
        'source_board': report['source_family_board'],
        'gate_board': report['evidence_gate_board'],
        'read_model_inventory': report['read_model_inventory'],
    }


def _label(item, preferred, fallback):
    if not isinstance(item, dict):
        return fallback
    for field in preferred:
        candidate = item.get(field)
        if isinstance(candidate, str) and PUBLIC_ID.fullmatch(candidate):
            return candidate
    return fallback


def _slots(board, count, kind, role):
    """Never pass through raw provider refs, record paths, text or identifiers."""
    result = []
    for index in range(count):
        item = None if board is None else board[index]
        label = _label(item,
                       ('identifier', 'source_family', 'family', 'family_id') if kind == 'SOURCE' else
                       ('identifier', 'gate_id', 'gate', 'gate_code'),
                       ('SOURCE_SLOT_%02d' % (index + 1)) if kind == 'SOURCE'
                       else ('G%02d' % (index + 1)))
        # Even AUDITOR and OPERATOR are merely read-only previews: no privileges.
        result.append({
            'slot': index + 1,
            'label': label,
            'status': 'NOT_INDEPENDENTLY_VERIFIED',
            'local_claims_are_receipts': False,
            'independent_attestation_present': False,
            'raw_artifact_exposed': False,
        })
    if len({entry['label'] for entry in result}) != len(result):
        raise OfflineMissionControlAPIError('Ambiguous duplicated display identifiers.')
    return result


def _read_model_slots(inventory):
    slots = []
    for i in range(DTO_TYPES):
        item = None if inventory is None else inventory[i]
        label = _label(item, ('dto_type', 'read_model_id', 'record_type', 'name'),
                       'DTO_SLOT_%02d' % (i + 1))
        slots.append({
            'slot': i + 1, 'dto_type': label,
            'classification': 'READ_ONLY_DESIGN_NOT_INDEPENDENTLY_VERIFIED',
            'source_record_contents_exported': False,
            'read_model_schema_available': inventory is not None,
        })
    if len({x['dto_type'] for x in slots}) != len(slots):
        raise OfflineMissionControlAPIError('Duplicate display DTO labels.')
    return slots


def _view(name, source, role):
    missing = source['origin'] == 'NO_STAGE11_REPORT_SUPPLIED'
    if name == 'overview':
        return {
            'status': 'NO_SOURCE_REPORT' if missing else 'LOCAL_REPORT_NOT_INDEPENDENTLY_VERIFIED',
            'snapshot_status': source['snapshot_status'],
            'local_record_count': source['local_record_count'],
            'latest_verified_market_price': None,
            'independently_verified_performance': None,
            'real_forecasts_registered': None,
            'independent_external_gates_verified': 0,
            'independent_source_families_verified': 0,
            'research_decision': 'NOT_ENOUGH_EVIDENCE',
        }
    if name == 'sources':
        return {
            'total_contract_slots': SOURCE_SLOTS,
            'independently_admitted_families': 0,
            'source_families': _slots(source['source_board'], SOURCE_SLOTS, 'SOURCE', role),
            'true_last_receipt_time': None,
        }
    if name == 'gates':
        return {
            'total_external_gate_slots': GATE_SLOTS,
            'independently_verified_gates': 0,
            'gates': _slots(source['gate_board'], GATE_SLOTS, 'GATE', role),
            'signed_operator_approval_present': False,
        }
    if name == 'council':
        return {
            'design_seats': 31, 'primary_seats': 28, 'governance_seats': 2,
            'synthesis_seats': 1, 'real_cycles_dispatched': 0,
            'most_recent_verified_vote': None,
            'most_recent_verified_risk_veto': None,
            'most_recent_verified_consensus': None,
            'risk_veto_state': 'NOT_OBSERVED_DO_NOT_ASSUME_CLEAR',
            'model_weights_promoted': 0,
        }
    if name == 'research':
        return {
            'research_decision': 'NOT_ENOUGH_EVIDENCE',
            'stage9_independent_exit_met': False,
            'stage10_independent_exit_met': False,
            'independent_forecast_outcome_pairs': None,
            'verified_costed_pnl': None,
            'verified_calibration': None,
            'verified_robustness': None,
            'local_record_count': source['local_record_count'],
            'simulation_is_market_probability': False,
        }
    if name == 'health':
        return {
            'status': 'UNKNOWN_NOT_INSPECTED',
            'collector_process_running': None,
            'broker_connected': False,
            'verified_latest_quote_time': None,
            'verified_m5_close': None,
            'real_council_latency_ms': None,
            'clock_attestation_verified': False,
            'risk_kill_switch_clear': None,
            'safety_policy': 'HOLD_UNTIL_INDEPENDENTLY_VERIFIED',
        }
    if name == 'models':
        return {
            'declared_dto_contracts': DTO_TYPES,
            'types': _read_model_slots(source['read_model_inventory']),
            'model_training_executed': False,
            'model_selection_or_promotion_allowed': False,
            'independent_model_performance': None,
        }
    if name == 'audit':
        return {
            'local_source_report_sha256': source['local_report_sha256'],
            'original_report_bytes_independently_attested': False,
            'original_source_records_exposed': False,
            'verified_audit_events': [],
            'historical_epoch_rebound': False,
            'read_only_replay': True,
            'diagnostic_caveat': 'HASH_CONSISTENCY_IS_NOT_EXTERNAL_CUSTODY',
        }
    raise OfflineMissionControlAPIError('Unknown API panel.')


def build_offline_api_response(*, epoch_id, as_of, route,
                               method='GET', role='VIEWER', report=None):
    """Build a safe HTTP-shaped response; it does not open a listening socket."""
    if epoch_id != EPOCH:
        raise OfflineMissionControlAPIError('Historical epoch rebinding refused.')
    cutoff = _timestamp(as_of)
    if cutoff > datetime.now(UTC):
        raise OfflineMissionControlAPIError('Future dashboard cutoff refused.')
    if role not in SAFE_ROLES or type(role) is not str:
        raise OfflineMissionControlAPIError('Unknown or unsafe role preview.')
    if type(method) is not str or type(route) is not str:
        raise OfflineMissionControlAPIError('Invalid request method/path.')
    headers = dict(SAFE_HEADERS)
    allowed = (method in ('GET', 'HEAD'))
    names = {API_PREFIX + '/' + name: name for name in ROUTES}
    target = names.get(route)
    if not allowed or target is None:
        # Invalid methods and routes cannot trigger reading or projections.
        code = 405 if not allowed else 404
        body = {
            'status': 'METHOD_NOT_ALLOWED' if not allowed else 'ROUTE_NOT_FOUND',
            'api_version': VERSION, 'final_action': 'HOLD',
            'network_accessed': False, 'requested_action_performed': False,
        }
    else:
        source = _check_source(report, expected_epoch=epoch_id, cutoff=cutoff)
        view = _view(target, source, role)
        body = {
            'api_version': VERSION,
            'route': route,
            'role_preview': role,
            'role_authenticated': False,
            'can_execute_actions': False,
            'as_of': cutoff.isoformat(),
            'historical_epoch_id': EPOCH,
            'source_classification': source['origin'],
            'local_source_report_sha256': source['local_report_sha256'],
            'panel': view,
            'verified_external_gate_count': 0,
            'verified_source_family_count': 0,
            'research_operational_readiness': 'BLOCKED_EXTERNAL_EVIDENCE',
            'final_action': 'HOLD',
            'network_accessed': False,
            'files_written': False,
            'server_started': False,
        }
        code = 200
    digest = _digest(body)
    headers['ETag'] = '"' + digest + '"'
    result = {
        'http_status_preview': code,
        'headers_preview': headers,
        'body': body if method != 'HEAD' else None,
        'body_sha256': digest,
        'body_emitted': method != 'HEAD',
        'http_listener_created': False,
        'network_accessed': False,
        'read_only': True,
        'role_authenticated': False,
        'final_action': 'HOLD',
    }
    result['response_sha256'] = _digest(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--read-only-api', action='store_true', required=True)
    parser.add_argument('--offline-only', action='store_true', required=True)
    parser.add_argument('--epoch-id', required=True)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--route', default=API_PREFIX + '/overview')
    parser.add_argument('--method', default='GET')
    parser.add_argument('--role', default='VIEWER')
    parser.add_argument('--report-file')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        report = None if args.report_file is None else read_offline_report(args.report_file)
        result = build_offline_api_response(
            epoch_id=args.epoch_id, as_of=args.as_of,
            route=args.route, method=args.method, role=args.role, report=report,
        )
    except (OfflineMissionControlAPIError, OSError, TypeError, ValueError):
        print('STAGE 11 API BLOCKED: Invalid, unauthorized or unverified local view source.')
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=True,
                         allow_nan=False))
    else:
        print('EURUSD ORACLE | Stage 11 P11-05..08 offline API view adapter')
        print('HTTP preview: %d | Route: %s' %
              (result['http_status_preview'], args.route))
        print('Read-only response: YES | Listener opened: NO | Network used: NO')
        print('Source: ' + ('NOT_SUPPLIED' if report is None else
                            'LOCAL_HASH_CONSISTENT_UNATTESTED'))
        print('SHA-256: ' + result['response_sha256'])
        print('READ-ONLY | NO API SERVER | NO BROKER | FINAL=HOLD')
    return 0


def main():
    raise SystemExit(run())


if __name__ == '__main__':
    main()


"""P11-01..04: standard-library-only, read-only ORACLE Mission Control DTOs.

Consumes optional *local, untrusted* Stage 10 report exports and bounded,
self-checksummed offline snapshot fixtures. No snapshot self-certifies external
source custody, authorization or a trading decision. All report evidence is
classified as LOCAL. A matching content digest is not independent attestation.

No provider/MT5/database/agent imports, network, model loading, write paths,
file exports, broker or order actions. The caller's role is a visual projection
preview, NOT authentication; privileged secrets are never accepted or returned.
FINAL=HOLD unconditionally, including when a local seat votes BUY or SELL.
"""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re

VERSION = 'EURUSD_STAGE11_MISSION_CONTROL_READ_MODELS_P11_01_04_V1'
SCHEMA_VERSION = 'MISSION_CONTROL_DTO_V1'
PACKET_VERSION = 'MISSION_CONTROL_LOCAL_OFFLINE_EXPORT_V1'
EPOCH = '75311e239cd5c295d3e08e03c358488fb123a46bfc6d342dd31493d95710aa33'
POLICY_PATH = 'docs/clock_profiles/hfm_forward_20261027.json'
POLICY_SHA256 = 'e34b7a29f78f5865f4b0fe7c4ad778e4e8b650d9943792eee019a48bdf2fe088'
UTC = timezone.utc
MAX_PACKET_BYTES = 262144
MAX_REPORT_BYTES = 131072
MAX_RECORDS = 128
IDENT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$')
HEX = re.compile(r'^[0-9a-f]{64}$')
STAMP = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
NUMBER = re.compile(r'^(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,8})?$')
PIPS = re.compile(r'^-?(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,4})?$')
SECRET = re.compile(r'(?i)(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|'
                    r'account[_-]?(?:id|number)|bearer|authorization|login|credential)')
FAMILIES = (
    'market_state', 'candles', 'ticks', 'cot_data', 'ecb_data', 'fed_data',
    'economic_calendar', 'cross_asset_data', 'calendar_coverage', 'risk_state',
    'risk_limits', 'reliability', 'synthesis_policy',
)
GATES = tuple('G%02d' % n for n in range(1, 12))
ROLES = ('VIEWER', 'RESEARCHER', 'AUDITOR', 'OPERATOR')
EVIDENCE = ('LOCAL_CANDIDATE', 'HASH_MATCHED', 'QUARANTINED', 'REVOKED')
RECORD_TYPES = (
    'EPOCH', 'DECISION', 'SOURCE_FAMILY', 'MARKET_SNAPSHOT', 'SEAT_VOTE',
    'GOVERNANCE_VETO', 'FORECAST', 'OUTCOME', 'MODEL', 'EVALUATION_PROTOCOL',
    'GATE', 'INCIDENT',
)
PACKET_KEYS = {
    'schema_version', 'contract_version', 'historical_epoch_id', 'packet_id',
    'mode', 'exported_at_utc', 'policy_sha256', 'parent_stage10_report_sha256',
    'independent_attestation_verified', 'operational_permissions', 'records',
    'packet_sha256',
}
RECORD_KEYS = {
    'record_type', 'record_id', 'as_of_utc', 'observed_at_utc',
    'origin_report_sha256', 'evidence_status', 'payload', 'record_sha256',
}
PAYLOAD_KEYS = {
    'EPOCH': {'epoch_state', 'operating_mode'},
    'DECISION': {'cycle_status', 'research_direction', 'risk_veto', 'm5_close_at_utc'},
    'SOURCE_FAMILY': {'family_id', 'provider_reference', 'first_available_at_utc',
                      'revision_status', 'clock_uncertainty_ms'},
    'MARKET_SNAPSHOT': {'symbol', 'timeframe', 'bar_close_at_utc', 'mid_price',
                        'source_family_id'},
    'SEAT_VOTE': {'seat_id', 'direction', 'input_complete', 'confidence',
                  'source_family_ids'},
    'GOVERNANCE_VETO': {'seat_id', 'veto_state', 'reason_code'},
    'FORECAST': {'prediction_id', 'forecast_status', 'horizon_seconds', 'probability_up'},
    'OUTCOME': {'prediction_id', 'maturity_status', 'hypothetical_costed_pips'},
    'MODEL': {'candidate_id', 'candidate_state', 'active_approved'},
    'EVALUATION_PROTOCOL': {'experiment_id', 'candidate_sha256', 'protocol_state'},
    'GATE': {'gate_id', 'gate_status', 'receipt_digest_claim'},
    'INCIDENT': {'severity', 'reason_code', 'incident_state'},
}
ALLOWED = {
    'epoch_state': ('HISTORICAL',),
    'operating_mode': ('OFFLINE_REPLAY',),
    'cycle_status': ('NOT_STARTED', 'RECEIVED', 'HELD', 'FAILED', 'EXPIRED'),
    'research_direction': ('HOLD', 'BUY', 'SELL', 'ABSTAIN', 'UNKNOWN'),
    'revision_status': ('NONE', 'REVISED', 'REVOKED', 'UNKNOWN'),
    'symbol': ('EURUSD',), 'timeframe': ('M5',),
    'direction': ('BUY', 'SELL', 'HOLD', 'ABSTAIN', 'UNKNOWN'),
    'veto_state': ('NO_ASSESSMENT', 'VETOED', 'HOLD', 'CHALLENGED'),
    'forecast_status': ('NOT_REGISTERED', 'LOCAL_UNREGISTERED'),
    'maturity_status': ('NOT_REGISTERED', 'PENDING_HORIZON', 'OUTCOME_MISSING',
                        'MATURED_UNVERIFIED'),
    'candidate_state': ('NOT_TRAINED', 'LOCAL_METADATA_ONLY'),
    'protocol_state': ('NOT_REGISTERED', 'LOCAL_UNVERIFIED'),
    'gate_status': ('NOT_VERIFIED', 'QUARANTINED', 'REVOKED'),
    'severity': ('INFO', 'WARNING', 'CRITICAL'),
    'incident_state': ('OPEN', 'ACKNOWLEDGED'),
}
NULLABLE_IDENT = {'provider_reference', 'reason_code'}
IDENT_FIELDS = {'family_id', 'source_family_id', 'seat_id', 'gate_id', 'prediction_id',
                'candidate_id', 'experiment_id'}
NULLABLE_SHA = {'candidate_sha256', 'receipt_digest_claim'}
NULLABLE_STAMP = {'m5_close_at_utc', 'first_available_at_utc', 'bar_close_at_utc'}
NULLABLE_BOOL = {'risk_veto', 'input_complete'}
NULLABLE_NUMERIC = {'mid_price', 'confidence', 'clock_uncertainty_ms',
                    'probability_up', 'hypothetical_costed_pips'}
TTL_SECONDS = {'MARKET_SNAPSHOT': 300, 'SOURCE_FAMILY': 3600, 'SEAT_VOTE': 3600}


class MissionControlReadModelError(ValueError):
    """Invalid offline report or attempt to promote local claims into authority."""


def _strict(value, keys, label):
    if not isinstance(value, dict) or set(value) != keys:
        raise MissionControlReadModelError('Strict schema mismatch: ' + label)


def _ident(value, label):
    if not isinstance(value, str) or not IDENT.fullmatch(value) or SECRET.search(value):
        raise MissionControlReadModelError('Unsafe identifier: ' + label)
    return value


def _hex(value, label):
    if not isinstance(value, str) or HEX.fullmatch(value) is None:
        raise MissionControlReadModelError('Lowercase SHA-256 required: ' + label)
    return value


def _clock(value):
    if not isinstance(value, str) or STAMP.fullmatch(value) is None:
        raise MissionControlReadModelError('Canonical UTC Z timestamp required.')
    try:
        result = datetime.fromisoformat(value[:-1] + '+00:00')
    except (ValueError, OverflowError):
        raise MissionControlReadModelError('Invalid canonical UTC timestamp.') from None
    if result.utcoffset() != timedelta(0):
        raise MissionControlReadModelError('Zero-offset timestamp required.')
    return result


def _report_clock(value):
    if not isinstance(value, str):
        raise MissionControlReadModelError('Stage 10 report UTC timestamp required.')
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, OverflowError):
        raise MissionControlReadModelError('Invalid upstream UTC timestamp.') from None
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise MissionControlReadModelError('Report must use explicit UTC.')
    return dt.astimezone(UTC)


def _digest(obj):
    return sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=True, allow_nan=False).encode('utf-8')).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MissionControlReadModelError('Duplicate JSON key refused.')
        result[key] = value
    return result


def _read(path, maximum):
    p = Path(path).absolute()
    for link in (p, *p.parents):
        if link.is_symlink() or (hasattr(link, 'is_junction') and link.is_junction()):
            raise MissionControlReadModelError('Symlink or junction refused.')
    if not p.is_file():
        raise MissionControlReadModelError('Ordinary local report file required.')
    try:
        with p.open('rb') as handle:
            raw = handle.read(maximum + 1)
    except OSError:
        raise MissionControlReadModelError('Local report cannot be read.') from None
    if len(raw) > maximum:
        raise MissionControlReadModelError('Local report too large.')
    return raw


def _file(path, maximum):
    raw = _read(path, maximum)
    try:
        doc = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique,
                         parse_constant=lambda _: (_ for _ in ()).throw(
                             MissionControlReadModelError('Nonfinite JSON refused.')))
    except MissionControlReadModelError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise MissionControlReadModelError('Malformed UTF-8 JSON refused.') from None
    if not isinstance(doc, dict):
        raise MissionControlReadModelError('JSON root object required.')
    return doc, sha256(raw).hexdigest()


def _quantity(value, field):
    if value is None:
        return
    pattern = PIPS if field == 'hypothetical_costed_pips' else NUMBER
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise MissionControlReadModelError('Bounded decimal string required: ' + field)
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise MissionControlReadModelError('Invalid decimal: ' + field) from None
    if not number.is_finite():
        raise MissionControlReadModelError('Nonfinite decimal: ' + field)
    if field in ('confidence', 'probability_up') and number > 1:
        raise MissionControlReadModelError('Probability must be in [0,1].')
    if field == 'clock_uncertainty_ms' and number > 5000:
        raise MissionControlReadModelError('Clock uncertainty too high for bounded fixture.')
    if field == 'mid_price' and number <= 0:
        raise MissionControlReadModelError('Positive price required.')


def _payload(kind, value):
    _strict(value, PAYLOAD_KEYS[kind], 'payload_' + kind)
    for field, item in value.items():
        if field in ALLOWED:
            if item not in ALLOWED[field]:
                raise MissionControlReadModelError('Disallowed local state: ' + field)
        elif field in IDENT_FIELDS:
            _ident(item, field)
        elif field in NULLABLE_IDENT:
            if item is not None:
                _ident(item, field)
        elif field in NULLABLE_SHA:
            if item is not None:
                _hex(item, field)
        elif field in NULLABLE_STAMP:
            if item is not None:
                _clock(item)
        elif field in NULLABLE_BOOL:
            if item is not None and type(item) is not bool:
                raise MissionControlReadModelError('Nullable bool required: ' + field)
        elif field == 'active_approved':
            if item is not False:
                raise MissionControlReadModelError('Local registry cannot approve a model.')
        elif field == 'horizon_seconds':
            if type(item) is not int or item not in (900, 3600, 86400):
                raise MissionControlReadModelError('Bounded forecast horizon required.')
        elif field == 'source_family_ids':
            if (not isinstance(item, list) or len(item) > 13
                    or any(not isinstance(x, str) or x not in FAMILIES for x in item)
                    or len(set(item)) != len(item)):
                raise MissionControlReadModelError('Invalid source ancestry list.')
        elif field in NULLABLE_NUMERIC:
            _quantity(item, field)
        else:
            raise MissionControlReadModelError('Unexpected payload field: ' + field)
    if kind == 'SOURCE_FAMILY':
        if value['family_id'] not in FAMILIES:
            raise MissionControlReadModelError('Unknown canonical source family.')
    elif kind == 'MARKET_SNAPSHOT':
        if value['source_family_id'] not in FAMILIES:
            raise MissionControlReadModelError('Unknown market snapshot source family.')
    elif kind == 'GATE':
        if value['gate_id'] not in GATES:
            raise MissionControlReadModelError('Unknown canonical evidence gate.')
    elif kind == 'GOVERNANCE_VETO':
        if value['seat_id'] != 'risk_officer':
            raise MissionControlReadModelError('Governance veto can only name Risk Officer.')
    elif kind == 'OUTCOME':
        if (value['hypothetical_costed_pips'] is not None
                and value['maturity_status'] != 'MATURED_UNVERIFIED'):
            raise MissionControlReadModelError('Outcomes cannot invent realized P&L.')
    return value


def _stage10(path, cutoff):
    if path is None:
        return None
    doc, file_sha = _file(path, MAX_REPORT_BYTES)
    required = {
        'version', 'scope', 'as_of', 'report_sha256', 'historical_epoch_id',
        'historical_policy_sha256', 'final_action', 'read_only', 'files_written',
        'broker_connected', 'independent_external_gates_verified',
        'independent_source_families_verified', 'research_handover',
        'network_accessed', 'training_executed', 'model_loaded',
        'council_invoked', 'paper_orders_authorized',
        'live_orders_authorized', 'independent_research_model_selection_granted',
        'research_operational_readiness',
    }
    if not required.issubset(doc):
        raise MissionControlReadModelError('Incomplete Stage 10 report export.')
    if (doc['version'] != 'EURUSD_STAGE10_COMPLEXITY_GOVERNANCE_HANDOVER_P10_21_24_V1'
            or doc['scope'] != 'P10_21_TO_P10_24_COMPLEXITY_GOVERNANCE_AND_HANDOVER'
            or doc['historical_epoch_id'] != EPOCH
            or doc['historical_policy_sha256'] != POLICY_SHA256
            or doc['final_action'] != 'HOLD'
            or doc['read_only'] is not True or doc['files_written'] is not False
            or doc['broker_connected'] is not False
            or doc['network_accessed'] is not False
            or doc['training_executed'] is not False
            or doc['model_loaded'] is not False
            or doc['council_invoked'] is not False
            or doc['paper_orders_authorized'] is not False
            or doc['live_orders_authorized'] is not False
            or doc['independent_research_model_selection_granted'] is not False
            or doc['research_operational_readiness'] != 'BLOCKED_EXTERNAL_EVIDENCE'
            or type(doc['independent_external_gates_verified']) is not int
            or doc['independent_external_gates_verified'] != 0
            or type(doc['independent_source_families_verified']) is not int
            or doc['independent_source_families_verified'] != 0
            or not isinstance(doc['research_handover'], dict)
            or doc['research_handover'].get('research_decision') != 'NOT_ENOUGH_EVIDENCE'
            or doc['research_handover'].get('stage10_independent_research_exit_met') is not False
            or _report_clock(doc['as_of']) > cutoff):
        raise MissionControlReadModelError('Stage 10 report claims inconsistent authority or future time.')
    digest = _digest({k: v for k, v in doc.items() if k != 'report_sha256'})
    if digest != doc['report_sha256']:
        raise MissionControlReadModelError('Stage 10 local report content hash mismatch.')
    return {'source_kind': 'STAGE10_COMPLEXITY_HANDOVER_LOCAL',
            'local_file_sha256': file_sha, 'claimed_report_sha256': digest,
            'as_of_utc': _report_clock(doc['as_of']).isoformat(),
            'local_checksum_matches': True, 'independently_authenticated': False}


def _record(raw, cutoff, packet_time, parent_sha):
    _strict(raw, RECORD_KEYS, 'offline_dto')
    kind = raw['record_type']
    if kind not in RECORD_TYPES:
        raise MissionControlReadModelError('Unrecognized read-model type.')
    identity = _ident(raw['record_id'], 'record_id')
    record_time = _clock(raw['as_of_utc'])
    observed = None if raw['observed_at_utc'] is None else _clock(raw['observed_at_utc'])
    if record_time > packet_time or packet_time > cutoff:
        raise MissionControlReadModelError('Record or packet created after display cutoff.')
    if raw['evidence_status'] not in EVIDENCE:
        raise MissionControlReadModelError('Offline evidence cannot self-attest provider origin.')
    origin = raw['origin_report_sha256']
    if origin is not None:
        _hex(origin, 'origin_report_sha256')
        if origin != parent_sha:
            raise MissionControlReadModelError('Record references absent or changed parent report.')
    payload = _payload(kind, raw['payload'])
    digest = _digest({k: v for k, v in raw.items() if k != 'record_sha256'})
    if digest != raw['record_sha256']:
        raise MissionControlReadModelError('DTO content hash mismatch.')
    problems = []
    if observed is not None and observed > record_time:
        problems.append('NOT_AVAILABLE_AT_DECLARED_AS_OF')
    if observed is not None and observed > cutoff:
        problems.append('FUTURE_DATED_OBSERVATION')
    if kind == 'SOURCE_FAMILY':
        first = payload['first_available_at_utc']
        if first is not None:
            available = _clock(first)
            if available > record_time:
                problems.append('FUTURE_RELEASE_VINTAGE')
            if available > cutoff:
                problems.append('FUTURE_DATED_VINTAGE')
            if observed is not None and available < observed:
                problems.append('CONFLICTING_SOURCE_CLOCKS')
        if payload['revision_status'] == 'REVOKED' and raw['evidence_status'] != 'REVOKED':
            problems.append('REVOKED_SOURCE_INCONSISTENT_CLAIM')
        if raw['evidence_status'] == 'REVOKED' and payload['revision_status'] != 'REVOKED':
            problems.append('REVOCATION_STATE_CONFLICT')
    if kind == 'MARKET_SNAPSHOT':
        if _clock(payload['bar_close_at_utc']) > record_time:
            problems.append('FUTURE_MARKET_CANDLE')
    if kind == 'DECISION' and payload['m5_close_at_utc'] is not None:
        if _clock(payload['m5_close_at_utc']) > record_time:
            problems.append('FUTURE_DECISION_BAR')
    if kind == 'GOVERNANCE_VETO' and payload['veto_state'] in ('VETOED', 'HOLD'):
        problems.append('RISK_OFFICER_VETO_OR_HOLD')
    quarantine = bool(problems and any(x != 'RISK_OFFICER_VETO_OR_HOLD' for x in problems))
    state = 'QUARANTINED' if quarantine else raw['evidence_status']
    if state == 'REVOKED':
        freshness = 'UNKNOWN'
    elif quarantine:
        freshness = ('FUTURE_DATED' if any('FUTURE' in p or 'NOT_AVAILABLE' in p
                                         for p in problems) else 'GAP_DETECTED')
    elif observed is None or (kind == 'SOURCE_FAMILY'
                              and payload['first_available_at_utc'] is None):
        freshness = 'UNKNOWN'
    else:
        age = (cutoff - observed).total_seconds()
        freshness = ('CURRENT_FOR_DECLARED_SCOPE' if age <= TTL_SECONDS.get(kind, 86400)
                     else 'STALE')
    # A causally impossible observation is entirely hidden from a historical
    # pane. Its record ID and safety reason survive for diagnosis, not its value.
    public_payload = None if quarantine else dict(payload)
    board_reference = (payload['family_id'] if kind == 'SOURCE_FAMILY' else
                       payload['gate_id'] if kind == 'GATE' else None)
    return {
        'schema_version': SCHEMA_VERSION,
        'record_type': kind, 'record_id': identity,
        'as_of_utc': raw['as_of_utc'], 'observed_at_utc': raw['observed_at_utc'],
        'evidence_status': state, 'freshness_status': freshness,
        'payload': public_payload, 'board_reference': board_reference,
        'blocked_reasons': sorted(set(problems)),
        'source_digest_sha256': digest,
        'origin_report_sha256': origin,
        'local_checksum_matches': True,
        'external_attestation_verified': False,
        'permission_to_trade': False,
        'display_watermark': 'LOCAL UNVERIFIED' if not quarantine else 'QUARANTINED',
    }


def _snapshot(path, cutoff, parent, prior_path):
    if path is None:
        if prior_path is not None:
            raise MissionControlReadModelError('Previous snapshot cannot replace missing snapshot.')
        return [], None, None
    doc, byte_sha = _file(path, MAX_PACKET_BYTES)
    _strict(doc, PACKET_KEYS, 'offline_export_packet')
    if (type(doc['schema_version']) is not int or doc['schema_version'] != 1
            or doc['contract_version'] != PACKET_VERSION
            or doc['historical_epoch_id'] != EPOCH
            or doc['mode'] != 'LOCAL_UNATTESTED_OFFLINE_EXPORT_ONLY'
            or doc['policy_sha256'] != POLICY_SHA256
            or doc['independent_attestation_verified'] is not False
            or doc['operational_permissions'] != []):
        raise MissionControlReadModelError('Offline packet attempts to grant unverified authority.')
    _ident(doc['packet_id'], 'packet_id')
    packet_time = _clock(doc['exported_at_utc'])
    if packet_time > cutoff:
        raise MissionControlReadModelError('Future exported packet refused.')
    parent_sha = None if parent is None else parent['claimed_report_sha256']
    if doc['parent_stage10_report_sha256'] != parent_sha:
        raise MissionControlReadModelError('Snapshot is not bound to selected Stage 10 report.')
    if doc['packet_sha256'] != _digest({k: v for k, v in doc.items()
                                        if k != 'packet_sha256'}):
        raise MissionControlReadModelError('Snapshot local content hash mismatch.')
    source = doc['records']
    if not isinstance(source, list) or len(source) > MAX_RECORDS:
        raise MissionControlReadModelError('Bounded read-only DTO list required.')
    records, identifiers, families, gates, seats = [], set(), set(), set(), set()
    for raw in source:
        record = _record(raw, cutoff, packet_time, parent_sha)
        key = (record['record_type'], record['record_id'])
        if key in identifiers:
            raise MissionControlReadModelError('Duplicate read-model identity.')
        identifiers.add(key)
        payload = record['payload']
        if record['record_type'] == 'SOURCE_FAMILY':
            family_id = record['board_reference']
            if family_id in families:
                raise MissionControlReadModelError('Duplicate canonical source family state.')
            families.add(family_id)
        if record['record_type'] == 'GATE':
            gate_id = record['board_reference']
            if gate_id in gates:
                raise MissionControlReadModelError('Duplicate gate state.')
            gates.add(gate_id)
        if record['record_type'] == 'SEAT_VOTE' and payload is not None:
            if payload['seat_id'] in seats:
                raise MissionControlReadModelError('Duplicate Council seat opinion.')
            seats.add(payload['seat_id'])
            if len(seats) > 31:
                raise MissionControlReadModelError('Council cannot have more than 31 seats.')
        records.append(record)
    prior_sha = None
    if prior_path is not None:
        if Path(path).absolute() == Path(prior_path).absolute():
            raise MissionControlReadModelError('Distinct local prior snapshot required.')
        _, prior_sha = _file(prior_path, MAX_PACKET_BYTES)
        if prior_sha != byte_sha:
            raise MissionControlReadModelError('Local immutable snapshot bytes changed.')
    return records, byte_sha, prior_sha


def _board(records, kind, names, field):
    present = {}
    for record in records:
        if record['record_type'] == kind:
            name = record['board_reference']
            if name is not None:
                present[name] = record
    result = []
    for name in names:
        row = present.get(name)
        result.append({
            'identifier': name,
            'evidence_status': 'NOT_SUPPLIED' if row is None else row['evidence_status'],
            'freshness_status': 'UNKNOWN' if row is None else row['freshness_status'],
            'as_of_utc': None if row is None else row['as_of_utc'],
            'read_model_record_sha256': None if row is None else row['source_digest_sha256'],
            'independently_verified': False,
        })
    return result


def assess_stage11_read_models(*, epoch_id, as_of, policy_file=POLICY_PATH,
                                 snapshot_file=None, prior_snapshot=None,
                                 stage10_report_file=None, role_preview='VIEWER'):
    if epoch_id != EPOCH:
        raise MissionControlReadModelError('Historical epoch rebinding refused.')
    cutoff = _clock(as_of)
    if cutoff > datetime.now(UTC):
        raise MissionControlReadModelError('Future dashboard cutoff refused.')
    if role_preview not in ROLES:
        raise MissionControlReadModelError('Unsupported read-model role preview.')
    pin = sha256(_read(policy_file, 16384)).hexdigest()
    if pin != POLICY_SHA256:
        raise MissionControlReadModelError('Pinned historical policy bytes changed.')
    parent = _stage10(stage10_report_file, cutoff)
    records, snapshot_sha, prior_sha = _snapshot(
        snapshot_file, cutoff, parent, prior_snapshot)
    types = [{
        'record_type': kind, 'schema_version': SCHEMA_VERSION,
        'payload_fields': sorted(PAYLOAD_KEYS[kind]),
        'local_records_present': sum(r['record_type'] == kind for r in records),
        'evidence_level': 'LOCAL_ONLY_OR_NOT_SUPPLIED',
    } for kind in RECORD_TYPES]
    latest = [r for r in records if r['record_type'] == 'DECISION'
              and r['payload'] is not None]
    latest.sort(key=lambda x: (x['as_of_utc'], x['record_id']))
    decision = latest[-1] if latest else None
    vetoed = any(r['record_type'] == 'GOVERNANCE_VETO'
                 and r['payload'] is not None
                 and r['payload']['veto_state'] in ('VETOED', 'HOLD')
                 for r in records)
    revoked = sum(r['evidence_status'] == 'REVOKED' for r in records)
    quarantine = sum(r['evidence_status'] == 'QUARANTINED' for r in records)
    stale = sum(r['freshness_status'] == 'STALE' for r in records)
    blockers = [
        'STAGE7_EXTERNAL_GATES_0_OF_11',
        'STAGE7_INDEPENDENT_SOURCE_FAMILIES_0_OF_13',
        'NO_INDEPENDENTLY_REGISTERED_FORECASTS_OR_MATURED_OUTCOMES',
        'STAGE9_AND_STAGE10_INDEPENDENT_RESEARCH_EXIT_NOT_MET',
        'NO_TRADE_OR_COUNCIL_DISPATCH_PERMISSIONS',
    ]
    if snapshot_sha is None:
        blockers.append('NO_LOCAL_DASHBOARD_SNAPSHOT_SUPPLIED')
    if parent is None:
        blockers.append('NO_STAGE10_HANDOVER_EXPORT_SUPPLIED')
    if revoked:
        blockers.append('REVOKED_LOCAL_SOURCE_RECORD_PRESENT')
    if quarantine:
        blockers.append('QUARANTINED_NONCAUSAL_OR_INCONSISTENT_RECORDS')
    if stale:
        blockers.append('STALE_LOCAL_REPORT_RECORDS')
    if vetoed:
        blockers.append('RISK_OFFICER_VETO_OR_HOLD')
    # No privileged data is ever available via role_preview: it is a visual
    # contract preview, not an authentication or authorization boundary.
    projection = []
    for record in records:
        item = dict(record)
        if item['payload'] is not None:
            item['payload'] = dict(item['payload'])
            if item['record_type'] == 'SOURCE_FAMILY':
                # Provider references are not required for a public projection;
                # a future authenticated Auditor export needs separate design.
                item['payload']['provider_reference'] = None
        projection.append(item)
    report = {
        'version': VERSION, 'schema_version': SCHEMA_VERSION,
        'scope': 'P11_01_TO_P11_04_MISSION_CONTROL_OFFLINE_READ_MODELS',
        'historical_epoch_id': EPOCH, 'as_of_utc': as_of,
        'historical_policy_sha256': pin, 'operating_mode': 'OFFLINE_REPLAY',
        'role_preview': role_preview, 'role_is_authenticated': False,
        'role_can_grant_privileges': False,
        'role_contracts': [{
            'role': role, 'permitted_actions': ['VIEW_REDACTED_OFFLINE_REPORTS'],
            'can_submit_orders': False, 'can_start_collectors': False,
            'can_train_or_promote': False,
        } for role in ROLES],
        'read_model_inventory': types,
        'snapshot_status': 'NOT_SUPPLIED' if snapshot_sha is None
                           else 'LOCAL_SELF_CHECKSUMMED_UNATTESTED',
        'snapshot_file_sha256': snapshot_sha,
        'prior_local_snapshot_file_sha256': prior_sha,
        'stage10_report_provenance': parent,
        'records': projection, 'record_count': len(records),
        'source_family_board': _board(records, 'SOURCE_FAMILY', FAMILIES, 'family_id'),
        'evidence_gate_board': _board(records, 'GATE', GATES, 'gate_id'),
        'independently_verified_source_families': 0,
        'independently_verified_external_gates': 0,
        'source_families_denominator': 13, 'external_gates_denominator': 11,
        'latest_research_direction_untrusted': None if decision is None
            else decision['payload']['research_direction'],
        'latest_research_decision_digest': None if decision is None
            else decision['source_digest_sha256'],
        'risk_veto_declared_locally': vetoed,
        'veto_independently_assessed': False,
        'revoked_records': revoked, 'quarantined_records': quarantine,
        'stale_records': stale,
        'latest_verified_market_price': None,
        'latest_verified_m5_cycle': None,
        'verified_performance_metrics': None,
        'prospectively_scorable_pairs': 0,
        'seats_independently_dispatched': 0,
        'source_origin_independently_authenticated': False,
        'local_hash_is_not_proof_of_independence': True,
        'missing_numeric_values_are_null_not_zero': True,
        'dataset_watermark': 'OFFLINE LOCAL UNATTESTED / NOT LIVE',
        'why_hold': sorted(set(blockers)),
        'dashboard_content_is_not_trading_advice': True,
        'stage11_ui_delivered': False,
        'read_only': True, 'files_written': False, 'network_accessed': False,
        'broker_connected': False, 'collector_started': False,
        'council_invoked': False, 'model_loaded': False,
        'training_executed': False, 'weights_promoted': False,
        'settlement_invoked': False, 'paper_orders_authorized': False,
        'live_orders_authorized': False, 'operator_approval_granted': False,
        'research_operational_readiness': 'BLOCKED_EXTERNAL_EVIDENCE',
        'final_action': 'HOLD',
    }
    report['report_sha256'] = _digest(report)
    return report


def run(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--read-only-mission-control', required=True, action='store_true')
    p.add_argument('--offline-only', required=True, action='store_true')
    p.add_argument('--epoch-id', required=True)
    p.add_argument('--as-of', required=True)
    p.add_argument('--policy-file', default=POLICY_PATH)
    p.add_argument('--snapshot-file')
    p.add_argument('--prior-snapshot')
    p.add_argument('--stage10-report-file')
    p.add_argument('--role-preview', choices=ROLES, default='VIEWER')
    p.add_argument('--json', action='store_true')
    args = p.parse_args(argv)
    try:
        result = assess_stage11_read_models(
            epoch_id=args.epoch_id, as_of=args.as_of,
            policy_file=args.policy_file, snapshot_file=args.snapshot_file,
            prior_snapshot=args.prior_snapshot,
            stage10_report_file=args.stage10_report_file,
            role_preview=args.role_preview,
        )
    except (MissionControlReadModelError, ValueError, TypeError, KeyError,
            OSError, RecursionError, OverflowError):
        print('STAGE 11 READ MODEL BLOCKED: Unsafe or self-authorizing local input.')
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    else:
        print('EURUSD ORACLE | Stage 11 P11-01..04 offline Mission Control DTOs')
        print('Historical clock policy: ORIGINAL_BYTES_MATCH_PIN')
        print('Snapshot: ' + result['snapshot_status'])
        print('Role preview: %s | Authenticated: NO | Actions: VIEW ONLY' % result['role_preview'])
        print('DTO types: 12 | Local records: %d' % result['record_count'])
        print('Independent gates: 0/11 | Source families: 0/13')
        print('Verified price: NOT AVAILABLE | Verified performance: NOT AVAILABLE')
        print('Research readiness: BLOCKED_EXTERNAL_EVIDENCE')
        for code in result['why_hold']:
            print('  BLOCKER ' + code)
        print('SHA-256: ' + result['report_sha256'])
        print('READ-ONLY | OFFLINE | NO AGENTS | NO BROKER | FINAL=HOLD')
    return 0


def main():
    raise SystemExit(run())


if __name__ == '__main__':
    main()

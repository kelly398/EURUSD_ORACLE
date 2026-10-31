
"""Offline, broker-neutral MT5 market-data normalization contract for ORACLE.

No MetaTrader5 import, initialize, login, network, database, model, Council, order,
file write, or legacy-clock conversion. Only locally exported JSON is inspected.
Normalized values are candidates, NEVER approved operational market data.
FINAL=HOLD for every successful evaluation.
"""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import stat

VERSION = 'EURUSD_STAGE7_UNIVERSAL_MT5_OFFLINE_ADAPTER_V1'
EPOCH = '75311e239cd5c295d3e08e03c358488fb123a46bfc6d342dd31493d95710aa33'
MAX_JSON_BYTES = 256 * 1024
MAX_TICKS = 256
MAX_BARS = 128
UTC = timezone.utc
IDENTIFIER = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()/+&,'-]{0,95}$")
SYMBOL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._+#-]{0,31}$')
SHA = re.compile(r'^[0-9a-f]{64}$')
DECIMAL = re.compile(r'^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$')
PRICE_FIELDS = ('bid', 'ask')
TRADE_MODES = {'DISABLED', 'LONG_ONLY', 'SHORT_ONLY', 'CLOSE_ONLY', 'FULL', 'UNKNOWN'}
SPREAD_MODES = {'FLOATING', 'FIXED', 'UNKNOWN'}
ACCOUNT_MODES = {'HEDGING', 'NETTING', 'EXCHANGE', 'UNKNOWN'}
CALC_MODES = {'FOREX_NO_LEVERAGE', 'FOREX', 'OTHER', 'UNKNOWN'}
PROFILE_FIELDS = {
    'schema_version', 'profile_id', 'platform', 'broker_name', 'server_name',
    'environment', 'account_mode', 'account_ref', 'instrument',
    'timestamp_contract', 'source_admitted', 'trading_authorized', 'final_action',
}
INSTRUMENT_FIELDS = {
    'canonical_symbol', 'broker_symbol', 'digits', 'point', 'contract_size',
    'margin_currency', 'profit_currency', 'spread_mode', 'chart_price',
    'trade_mode', 'calculation_mode',
}
SNAPSHOT_FIELDS = {
    'schema_version', 'origin', 'snapshot_id', 'observed_at_utc', 'terminal',
    'instrument', 'ticks', 'm5_bars', 'final_action',
}
TERMINAL_FIELDS = {
    'platform', 'broker_name', 'server_name', 'environment', 'account_mode',
    'account_ref',
}
SNAPSHOT_SYMBOL_FIELDS = {
    'broker_symbol', 'digits', 'point', 'contract_size', 'trade_mode',
    'spread_mode', 'chart_price',
}
TICK_FIELDS = {'time', 'time_msc', 'bid', 'ask', 'last', 'volume', 'flags'}
BAR_FIELDS = {
    'time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread', 'real_volume',
}


class MT5OfflineAdapterError(ValueError):
    """Unsafe, inconsistent or incomplete offline MT5 candidate."""


def _exact(obj, keys, label):
    if type(obj) is not dict or set(obj) != keys:
        raise MT5OfflineAdapterError('Strict fields mismatch: ' + label)


def _text(value, label, pattern):
    if type(value) is not str or not pattern.fullmatch(value):
        raise MT5OfflineAdapterError('Unsafe value: ' + label)
    return value


def _enum(value, permitted, label):
    if type(value) is not str or value not in permitted:
        raise MT5OfflineAdapterError('Unsupported value: ' + label)
    return value


def _integer(value, label, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise MT5OfflineAdapterError('Invalid integer: ' + label)
    return value


def _decimal(value, label, *, positive=False, allow_none=False):
    if value is None and allow_none:
        return None
    if type(value) is bool or type(value) not in (int, float, str):
        raise MT5OfflineAdapterError('Invalid decimal type: ' + label)
    if type(value) is str and not DECIMAL.fullmatch(value):
        raise MT5OfflineAdapterError('Invalid decimal format: ' + label)
    if type(value) is float and not math.isfinite(value):
        raise MT5OfflineAdapterError('Nonfinite decimal: ' + label)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, OverflowError):
        raise MT5OfflineAdapterError('Invalid decimal: ' + label) from None
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise MT5OfflineAdapterError('Out-of-range decimal: ' + label)
    if number.adjusted() > 17 or number.as_tuple().exponent < -16:
        raise MT5OfflineAdapterError('Unbounded decimal precision: ' + label)
    return number


def _utc(value):
    if type(value) is not str or not re.fullmatch(
            r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)', value):
        raise MT5OfflineAdapterError('Canonical UTC time required.')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise MT5OfflineAdapterError('Invalid UTC time.') from None
    if result.utcoffset() != timedelta(0):
        raise MT5OfflineAdapterError('Non-UTC time refused.')
    return result.astimezone(UTC)


def _digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                     ensure_ascii=True, allow_nan=False).encode('utf-8')
    return sha256(raw).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MT5OfflineAdapterError('Duplicate JSON key.')
        result[key] = value
    return result


def _nonfinite(_):
    raise MT5OfflineAdapterError('Nonfinite JSON refused.')


def read_local_json(path):
    """Bounded, read-only JSON input; denies symlinks and nonordinary files."""
    p = Path(path)
    if p.suffix.lower() != '.json' or p.is_symlink():
        raise MT5OfflineAdapterError('Only ordinary .json files allowed.')
    try:
        info = p.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_JSON_BYTES:
            raise MT5OfflineAdapterError('JSON file is empty, too large or unsafe.')
        with p.open('rb') as handle:
            opened = handle.fileno()
            before = os.fstat(opened)
            raw = handle.read(MAX_JSON_BYTES + 1)
            after = os.fstat(opened)
        final = p.stat(follow_symlinks=False)
    except OSError:
        raise MT5OfflineAdapterError('Cannot safely read JSON file.') from None
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if (len(raw) > MAX_JSON_BYTES or len(raw) != info.st_size
            or not identity(info) == identity(before) == identity(after) == identity(final)
            or p.is_symlink()):
        raise MT5OfflineAdapterError('JSON file changed during inspection.')
    try:
        data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique,
                          parse_constant=_nonfinite)
    except MT5OfflineAdapterError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise MT5OfflineAdapterError('Malformed local JSON.') from None
    if type(data) is not dict:
        raise MT5OfflineAdapterError('Expected JSON object.')
    return data


def example_hfm_profile():
    """Screenshot-derived local draft; no account ID, credential or inferred point."""
    return {
        'schema_version': 1, 'profile_id': 'HFM_MT5_DEMO_RESEARCH',
        'platform': 'MT5', 'broker_name': 'HFM Investments Ltd',
        'server_name': 'HFMarketsKE-Demo2', 'environment': 'DEMO',
        'account_mode': 'HEDGING', 'account_ref': 'LOCAL_DEMO_ALIAS',
        'instrument': {
            'canonical_symbol': 'EURUSD', 'broker_symbol': 'EURUSD',
            'digits': 5, 'point': None, 'contract_size': '100000',
            'margin_currency': 'EUR', 'profit_currency': 'USD',
            'spread_mode': 'FLOATING', 'chart_price': 'BID',
            'trade_mode': 'DISABLED', 'calculation_mode': 'FOREX_NO_LEVERAGE',
        },
        'timestamp_contract': 'MT5_PY_UNIX_FIELDS_UNVERIFIED',
        'source_admitted': False, 'trading_authorized': False, 'final_action': 'HOLD',
    }


def validate_profile(profile):
    _exact(profile, PROFILE_FIELDS, 'broker_profile')
    if type(profile['schema_version']) is not int or profile['schema_version'] != 1:
        raise MT5OfflineAdapterError('Unsupported profile schema.')
    _text(profile['profile_id'], 'profile_id', IDENTIFIER)
    if profile['platform'] != 'MT5':
        raise MT5OfflineAdapterError('Only MT5 supported.')
    for k in ('broker_name', 'server_name'):
        _text(profile[k], k, SAFE_LABEL)
    _enum(profile['environment'], {'DEMO', 'LIVE'}, 'environment')
    _enum(profile['account_mode'], ACCOUNT_MODES, 'account_mode')
    _text(profile['account_ref'], 'account_ref', IDENTIFIER)
    _exact(profile['instrument'], INSTRUMENT_FIELDS, 'instrument')
    instrument = profile['instrument']
    if instrument['canonical_symbol'] != 'EURUSD':
        raise MT5OfflineAdapterError('EURUSD ORACLE only normalizes EUR/USD.')
    _text(instrument['broker_symbol'], 'broker_symbol', SYMBOL)
    digits = _integer(instrument['digits'], 'digits', 1, 8)
    point = _decimal(instrument['point'], 'point', positive=True, allow_none=True)
    if point is not None and point != Decimal(1).scaleb(-digits):
        raise MT5OfflineAdapterError('Point does not match stated digits.')
    _decimal(instrument['contract_size'], 'contract_size', positive=True)
    for k in ('margin_currency', 'profit_currency'):
        if type(instrument[k]) is not str or not re.fullmatch('[A-Z]{3}', instrument[k]):
            raise MT5OfflineAdapterError('Invalid currency: ' + k)
    _enum(instrument['spread_mode'], SPREAD_MODES, 'spread_mode')
    _enum(instrument['chart_price'], {'BID', 'LAST', 'UNKNOWN'}, 'chart_price')
    _enum(instrument['trade_mode'], TRADE_MODES, 'trade_mode')
    _enum(instrument['calculation_mode'], CALC_MODES, 'calculation_mode')
    if (profile['timestamp_contract'] != 'MT5_PY_UNIX_FIELDS_UNVERIFIED'
            or profile['source_admitted'] is not False
            or profile['trading_authorized'] is not False
            or profile['final_action'] != 'HOLD'):
        raise MT5OfflineAdapterError('Profile attempts evidence or trading authorization.')
    return profile


def _check_snapshot(snapshot, profile, cutoff):
    _exact(snapshot, SNAPSHOT_FIELDS, 'offline_snapshot')
    if (type(snapshot['schema_version']) is not int or snapshot['schema_version'] != 1
            or snapshot['origin'] != 'OFFLINE_MT5_EXPORT_UNATTESTED'
            or snapshot['final_action'] != 'HOLD'):
        raise MT5OfflineAdapterError('Only offline HOLD snapshots permitted.')
    _text(snapshot['snapshot_id'], 'snapshot_id', IDENTIFIER)
    observed_at = _utc(snapshot['observed_at_utc'])
    if observed_at > cutoff:
        raise MT5OfflineAdapterError('Snapshot is later than review cutoff.')
    _exact(snapshot['terminal'], TERMINAL_FIELDS, 'terminal')
    for dest, src in (
            ('platform', 'platform'), ('broker_name', 'broker_name'),
            ('server_name', 'server_name'), ('environment', 'environment'),
            ('account_mode', 'account_mode'), ('account_ref', 'account_ref')):
        if snapshot['terminal'][dest] != profile[src]:
            raise MT5OfflineAdapterError('Account/server profile mismatch: ' + dest)
    _exact(snapshot['instrument'], SNAPSHOT_SYMBOL_FIELDS, 'snapshot_symbol')
    for name in SNAPSHOT_SYMBOL_FIELDS:
        if name in ('point', 'contract_size'):
            observed = _decimal(snapshot['instrument'][name], name,
                                positive=True, allow_none=(name == 'point'))
            expected = _decimal(profile['instrument'][name], name,
                                positive=True, allow_none=(name == 'point'))
            matching = observed == expected
        else:
            matching = snapshot['instrument'][name] == profile['instrument'][name]
        if not matching:
            raise MT5OfflineAdapterError('Symbol specification changed: ' + name)
    for name, maximum in (('ticks', MAX_TICKS), ('m5_bars', MAX_BARS)):
        value = snapshot[name]
        if type(value) is not list or len(value) > maximum:
            raise MT5OfflineAdapterError('Bounded ' + name + ' list required.')
    return observed_at


def _price(value, digits, label):
    number = _decimal(value, label, positive=True)
    unit = Decimal(1).scaleb(-digits)
    if number != number.quantize(unit):
        raise MT5OfflineAdapterError('Price precision exceeds broker digits: ' + label)
    return format(number, '.' + str(digits) + 'f')


def _normalize_ticks(rows, digits, observed_at):
    result = []
    previous = None
    for row in rows:
        _exact(row, TICK_FIELDS, 'MT5_tick')
        seconds = _integer(row['time'], 'tick.time', 1, 4102444800)
        msc = _integer(row['time_msc'], 'tick.time_msc', 1000, 4102444800999)
        if msc // 1000 != seconds or (previous is not None and msc <= previous):
            raise MT5OfflineAdapterError('Tick time mismatch, duplicate or nonmonotonic order.')
        candidate_utc = datetime.fromtimestamp(seconds, UTC) + timedelta(
            milliseconds=msc - seconds * 1000)
        if candidate_utc > observed_at:
            raise MT5OfflineAdapterError('Tick is future relative to local snapshot claim.')
        bid = _price(row['bid'], digits, 'tick.bid')
        ask = _price(row['ask'], digits, 'tick.ask')
        if Decimal(ask) < Decimal(bid):
            raise MT5OfflineAdapterError('Crossed bid/ask quote.')
        # MT5 FX feeds commonly report last=0 when no Last trade price exists.
        # Do not invent a Last price or reject valid Bid/Ask-only ticks.
        last_value = _decimal(row['last'], 'tick.last', allow_none=True)
        last = None if last_value is None or last_value == 0 else _price(row['last'], digits, 'tick.last')
        volume = _integer(row['volume'], 'tick.volume')
        flags = _integer(row['flags'], 'tick.flags', 0, 65535)
        result.append({
            'time_raw': seconds, 'time_msc_raw': msc,
            'utc_candidate': candidate_utc.isoformat(),
            'bid': bid, 'ask': ask, 'last': last, 'volume': volume, 'flags': flags,
            'spread_price': str(Decimal(ask) - Decimal(bid)),
            'time_domain_independently_verified': False,
            'admitted_for_council': False,
        })
        previous = msc
    return result


def _normalize_bars(rows, digits, observed_at):
    result = []
    previous = None
    open_ignored = 0
    gaps = 0
    for row in rows:
        _exact(row, BAR_FIELDS, 'MT5_M5_bar')
        seconds = _integer(row['time'], 'M5.time', 1, 4102444800)
        if seconds % 300 or (previous is not None and seconds <= previous):
            raise MT5OfflineAdapterError('Misaligned, duplicate or unordered M5 bar.')
        if previous is not None and seconds - previous > 300:
            gaps += (seconds - previous) // 300 - 1
        previous = seconds
        start = datetime.fromtimestamp(seconds, UTC)
        if start > observed_at:
            raise MT5OfflineAdapterError('M5 start lies after snapshot claim.')
        o, h, l, c = (_price(row[k], digits, 'M5.' + k)
                      for k in ('open', 'high', 'low', 'close'))
        if not Decimal(l) <= min(Decimal(o), Decimal(c)) <= max(Decimal(o), Decimal(c)) <= Decimal(h):
            raise MT5OfflineAdapterError('Invalid M5 OHLC ordering.')
        vol = _integer(row['tick_volume'], 'M5.tick_volume')
        spread = _integer(row['spread'], 'M5.spread', 0, 100000)
        real_vol = _decimal(row['real_volume'], 'M5.real_volume')
        if start + timedelta(minutes=5) > observed_at:
            open_ignored += 1
            continue
        result.append({
            'open_time_raw': seconds, 'open_time_utc_candidate': start.isoformat(),
            'timeframe': 'M5', 'open': o, 'high': h, 'low': l, 'close': c,
            'tick_volume': vol, 'spread_points_reported': spread,
            'real_volume': str(real_vol),
            'time_domain_independently_verified': False,
            'admitted_for_council': False,
        })
    return result, open_ignored, gaps


def inspect_mt5_offline(*, profile, as_of, snapshot=None):
    """Pure offline candidate inspection. No MT5 imports, IO or trading pathways."""
    validate_profile(profile)
    cutoff = _utc(as_of)
    if cutoff > datetime.now(UTC):
        raise MT5OfflineAdapterError('Future review cutoff refused.')
    tick_rows, bar_rows = [], []
    excluded_open, gaps = 0, 0
    state = 'PROFILE_ONLY_NO_MARKET_DATA'
    snapshot_id = None
    if snapshot is not None:
        observed = _check_snapshot(snapshot, profile, cutoff)
        tick_rows = _normalize_ticks(snapshot['ticks'], profile['instrument']['digits'], observed)
        bar_rows, excluded_open, gaps = _normalize_bars(
            snapshot['m5_bars'], profile['instrument']['digits'], observed)
        snapshot_id = snapshot['snapshot_id']
        state = 'OFFLINE_ROWS_QUARANTINED_UNVERIFIED_SOURCE'
    output = {
        'version': VERSION, 'historical_epoch_unchanged': EPOCH,
        'profile_id': profile['profile_id'], 'platform': 'MT5',
        'broker_label_local_claim': profile['broker_name'],
        'server_label_local_claim': profile['server_name'],
        'environment_local_claim': profile['environment'],
        'account_alias_local_claim': profile['account_ref'],
        'account_identity_independently_verified': False,
        'symbol': profile['instrument']['canonical_symbol'],
        'broker_symbol': profile['instrument']['broker_symbol'],
        'digits': profile['instrument']['digits'],
        'contract_size_local_claim': str(_decimal(profile['instrument']['contract_size'], 'contract_size')),
        'point_verified': False,
        'trade_mode_local_claim': profile['instrument']['trade_mode'],
        'read_only_snapshot_id': snapshot_id, 'as_of_utc': cutoff.isoformat(),
        'raw_timestamp_convention': 'MT5_PY_UNIX_FIELDS_UNVERIFIED',
        'broker_utc_offset_applied': False,
        'source_admission_status': 'NOT_ADMITTED',
        'market_data_status': state,
        'candidate_ticks': tick_rows, 'candidate_complete_m5_bars': bar_rows,
        'excluded_incomplete_m5_bars': excluded_open,
        'missing_m5_intervals_unverified': gaps,
        'independent_external_gates_verified': 0,
        'independent_source_families_admitted': 0,
        'data_available_to_council': False,
        'independent_clock_verified': False,
        'mt5_initialized': False, 'network_accessed': False,
        'collector_started': False, 'council_invoked': False,
        'broker_connected': False, 'paper_orders_authorized': False,
        'live_orders_authorized': False, 'orders_sent': 0,
        'historical_archive_modified': False,
        'research_operational_readiness': 'BLOCKED_EXTERNAL_EVIDENCE',
        'final_action': 'HOLD',
    }
    output['report_sha256'] = _digest(output)
    return output


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline-only', action='store_true', required=True)
    parser.add_argument('--read-only-mt5', action='store_true', required=True)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--example-hfm-profile', action='store_true')
    choice.add_argument('--profile-file')
    parser.add_argument('--snapshot-file')
    parser.add_argument('--as-of')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.example_hfm_profile:
            if args.snapshot_file or args.as_of:
                raise MT5OfflineAdapterError('Example is profile-only and cannot load data.')
            print(json.dumps(example_hfm_profile(), indent=2, sort_keys=True))
            return 0
        if not args.as_of:
            raise MT5OfflineAdapterError('Explicit review cutoff required.')
        profile = read_local_json(args.profile_file)
        snapshot = read_local_json(args.snapshot_file) if args.snapshot_file else None
        report = inspect_mt5_offline(profile=profile, as_of=args.as_of, snapshot=snapshot)
    except (MT5OfflineAdapterError, OSError, ValueError, TypeError, OverflowError):
        print('UNIVERSAL MT5 OFFLINE ADAPTER BLOCKED: invalid local profile or export.')
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        print('EURUSD ORACLE | Universal offline MT5 broker adapter')
        print('Profile: ' + report['profile_id'])
        print('Server label (unattested): ' + report['server_label_local_claim'])
        print('Trade mode (unattested): ' + report['trade_mode_local_claim'])
        print('Market data: ' + report['market_data_status'])
        print('Candidate ticks: %d | completed M5: %d | excluded open M5: %d' % (
            len(report['candidate_ticks']), len(report['candidate_complete_m5_bars']),
            report['excluded_incomplete_m5_bars']))
        print('Missing M5 intervals (local, unverified): ' + str(report['missing_m5_intervals_unverified']))
        print('Source gates 0/11 | Families 0/13 | Council data: BLOCKED')
        print('Report SHA-256: ' + report['report_sha256'])
        print('NO MT5 INITIALIZE | NO BROKER CONNECTION | NO ORDERS | FINAL=HOLD')
    return 0


def main():
    raise SystemExit(run())


if __name__ == '__main__':
    main()

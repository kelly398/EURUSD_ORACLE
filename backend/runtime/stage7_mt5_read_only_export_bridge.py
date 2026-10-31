
"""EURUSD ORACLE: offline conversion of allowlisted MT5-shaped exports.

This module does not load the MT5 terminal library or obtain quotes,
open network connections, run agents, modify historical sources, or place orders.
The input is an untrusted LOCAL JSON file; account and broker labels are claims.
All exported rows remain quarantined through the existing Stage 7 MT5 adapter.
A derived snapshot is written only by explicit --output-snapshot and never
replaces an existing file. No evidence gate, Council or trading authorization.
FINAL=HOLD, unconditionally.
"""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from backend.runtime.stage7_universal_mt5_adapter import (
    EPOCH, MAX_JSON_BYTES, MT5OfflineAdapterError,
    _decimal, _digest, _integer, _utc,
    inspect_mt5_offline, read_local_json, validate_profile,
)

VERSION = 'EURUSD_STAGE7_MT5_OFFLINE_EXPORT_BRIDGE_V1'
ORIGIN = 'LOCAL_MT5_ALLOWLIST_EXPORT_UNATTESTED'
MAX_TICKS = 256
MAX_BARS = 128
MAX_QUERY_SECONDS = 86400

RAW_FIELDS = {
    'schema_version', 'origin', 'export_id', 'observed_at_utc',
    'account', 'terminal', 'symbol_info', 'query', 'ticks', 'rates_m5',
    'final_action',
}
ACCOUNT_FIELDS = {
    'company', 'server', 'trade_mode', 'margin_mode', 'local_alias',
}
TERMINAL_FIELDS = {
    'connected', 'build', 'trade_allowed', 'tradeapi_disabled',
}
SYMBOL_FIELDS = {
    'name', 'digits', 'point', 'trade_contract_size', 'currency_margin',
    'currency_profit', 'spread_float', 'chart_mode', 'trade_mode',
    'trade_calc_mode',
}
QUERY_FIELDS = {
    'symbol', 'timeframe', 'utc_from', 'utc_to', 'ticks_mode',
}
RAW_TICK_FIELDS = {
    'time', 'time_msc', 'bid', 'ask', 'last', 'volume', 'flags',
    'volume_real',
}
RAW_BAR_FIELDS = {
    'time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread',
    'real_volume',
}

ACCOUNT_TRADE = {0: 'DEMO', 2: 'LIVE'}
MARGIN_MODE = {0: 'NETTING', 1: 'EXCHANGE', 2: 'HEDGING'}
SYMBOL_TRADE = {
    0: 'DISABLED',
    1: 'LONG_ONLY',
    2: 'SHORT_ONLY',
    3: 'CLOSE_ONLY',
    4: 'FULL',
}
CALC_MODE = {0: 'FOREX', 1: 'FOREX_NO_LEVERAGE'}
CHART_MODE = {0: 'BID', 1: 'LAST'}

SAFE_ID = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
SAFE_OUTPUT = re.compile(
    r'^[A-Za-z0-9_][A-Za-z0-9_.-]{0,119}\.json$'
)


class MT5ExportBridgeError(ValueError):
    """A local export cannot be safely normalized or written."""


def _exact(value, names, label):
    if type(value) is not dict or set(value) != names:
        raise MT5ExportBridgeError(
            'Strict allowlist mismatch: ' + label
        )


def _bool(value, label):
    if type(value) is not bool:
        raise MT5ExportBridgeError(
            'Expected boolean: ' + label
        )
    return value


def _number(value, label, *, positive=False):
    return _decimal(value, label, positive=positive)


def _account_contract(raw, profile):
    _exact(raw, ACCOUNT_FIELDS, 'account')

    if (
        raw['company'] != profile['broker_name']
        or raw['server'] != profile['server_name']
        or raw['local_alias'] != profile['account_ref']
    ):
        raise MT5ExportBridgeError(
            'Account/server labels do not match selected profile.'
        )

    trade = _integer(
        raw['trade_mode'], 'account.trade_mode', 0, 2
    )
    margin = _integer(
        raw['margin_mode'], 'account.margin_mode', 0, 2
    )

    if (
        trade not in ACCOUNT_TRADE
        or ACCOUNT_TRADE[trade] != profile['environment']
        or MARGIN_MODE[margin] != profile['account_mode']
    ):
        raise MT5ExportBridgeError(
            'Account environment or margin-mode mismatch.'
        )

    return {
        'platform': 'MT5',
        'broker_name': profile['broker_name'],
        'server_name': profile['server_name'],
        'environment': profile['environment'],
        'account_mode': profile['account_mode'],
        'account_ref': profile['account_ref'],
    }


def _terminal_contract(raw, *, has_rows):
    _exact(raw, TERMINAL_FIELDS, 'terminal')

    connected = _bool(
        raw['connected'], 'terminal.connected'
    )

    _bool(
        raw['trade_allowed'], 'terminal.trade_allowed'
    )
    _bool(
        raw['tradeapi_disabled'],
        'terminal.tradeapi_disabled',
    )

    build = _integer(
        raw['build'], 'terminal.build', 0, 10000000
    )

    if has_rows and not connected:
        raise MT5ExportBridgeError(
            'Rows cannot claim a disconnected MT5 terminal.'
        )

    # This is only a local claim, not a live connection observation.
    return connected, build


def _symbol_contract(raw, profile):
    _exact(raw, SYMBOL_FIELDS, 'symbol_info')

    expected = profile['instrument']

    if raw['name'] != expected['broker_symbol']:
        raise MT5ExportBridgeError(
            'Broker-specific symbol mismatch.'
        )

    digits = _integer(
        raw['digits'], 'symbol_info.digits', 1, 8
    )

    if digits != expected['digits']:
        raise MT5ExportBridgeError(
            'Symbol price-precision mismatch.'
        )

    unit = Decimal(1).scaleb(-digits)
    actual_point = _number(
        raw['point'], 'symbol_info.point', positive=True
    )

    if actual_point != unit:
        raise MT5ExportBridgeError(
            'Observed point disagrees with digits.'
        )

    expected_point = expected['point']

    if (
        expected_point is not None
        and actual_point != _number(
            expected_point,
            'profile.instrument.point',
            positive=True,
        )
    ):
        raise MT5ExportBridgeError(
            'Profile point changed.'
        )

    actual_contract = _number(
        raw['trade_contract_size'],
        'symbol_info.trade_contract_size',
        positive=True,
    )

    if actual_contract != _number(
        expected['contract_size'],
        'profile.contract_size',
        positive=True,
    ):
        raise MT5ExportBridgeError(
            'Contract size changed.'
        )

    for source, target in (
        ('currency_margin', 'margin_currency'),
        ('currency_profit', 'profit_currency'),
    ):
        if raw[source] != expected[target]:
            raise MT5ExportBridgeError(
                'Symbol settlement currency mismatch.'
            )

    floating = _bool(
        raw['spread_float'], 'symbol_info.spread_float'
    )
    spread = 'FLOATING' if floating else 'FIXED'

    chart = _integer(
        raw['chart_mode'], 'symbol_info.chart_mode', 0, 1
    )
    trade = _integer(
        raw['trade_mode'], 'symbol_info.trade_mode', 0, 4
    )
    calc = _integer(
        raw['trade_calc_mode'],
        'symbol_info.trade_calc_mode',
        0,
        1000,
    )

    calc_label = CALC_MODE.get(calc, 'OTHER')

    if (
        spread != expected['spread_mode']
        or CHART_MODE[chart] != expected['chart_price']
        or SYMBOL_TRADE[trade] != expected['trade_mode']
        or calc_label != expected['calculation_mode']
    ):
        raise MT5ExportBridgeError(
            'Symbol capabilities changed against selected profile.'
        )

    # Keep an unknown profile point unknown. Do not promote
    # exported values into independently verified profile evidence.
    return ({
        'broker_symbol': expected['broker_symbol'],
        'digits': digits,
        'point': expected_point,
        'contract_size': str(actual_contract),
        'trade_mode': SYMBOL_TRADE[trade],
        'spread_mode': spread,
        'chart_price': CHART_MODE[chart],
    }, str(actual_point))


def _query_contract(raw, profile, observed):
    _exact(raw, QUERY_FIELDS, 'query')

    if (
        raw['symbol'] != profile['instrument']['broker_symbol']
        or raw['timeframe'] != 'M5'
        or raw['ticks_mode'] != 'COPY_TICKS_ALL'
    ):
        raise MT5ExportBridgeError(
            'Unsupported MT5 symbol/timeframe/tick selection.'
        )

    first = _utc(raw['utc_from'])
    last = _utc(raw['utc_to'])

    if (
        first.microsecond
        or last.microsecond
        or not first < last <= observed
        or (last - first).total_seconds() > MAX_QUERY_SECONDS
    ):
        raise MT5ExportBridgeError(
            'Query must be second-aligned, causal and <=24h.'
        )

    return int(first.timestamp()), int(last.timestamp())


def _rows_contract(
    raw, *, field_names, count_limit, first, last, label
):
    if type(raw) is not list or len(raw) > count_limit:
        raise MT5ExportBridgeError(
            'Bounded ' + label + ' array required.'
        )

    output = []

    for row in raw:
        _exact(row, field_names, label)

        seconds = _integer(
            row['time'], label + '.time', 1, 4102444800
        )

        if not first <= seconds < last:
            raise MT5ExportBridgeError(
                'Row timestamp outside declared UTC query window.'
            )

        if label == 'tick':
            msc = _integer(
                row['time_msc'],
                'tick.time_msc',
                1000,
                4102444800999,
            )

            if (
                msc // 1000 != seconds
                or not first * 1000 <= msc < last * 1000
            ):
                raise MT5ExportBridgeError(
                    'Tick millisecond timestamp violates query range.'
                )

            _number(row['volume_real'], 'tick.volume_real')

            output.append({
                key: row[key]
                for key in (
                    'time',
                    'time_msc',
                    'bid',
                    'ask',
                    'last',
                    'volume',
                    'flags',
                )
            })

        else:
            output.append({
                key: row[key]
                for key in (
                    'time',
                    'open',
                    'high',
                    'low',
                    'close',
                    'tick_volume',
                    'spread',
                    'real_volume',
                )
            })

    return output


def bridge_offline_export(
    *, profile, as_of, export=None
):
    """Convert a local MT5 export to an upstream HOLD snapshot."""

    validate_profile(profile)

    cutoff = _utc(as_of)

    if cutoff > datetime.now(timezone.utc):
        raise MT5ExportBridgeError(
            'Future review cutoff refused.'
        )

    if export is None:
        adapter = inspect_mt5_offline(
            profile=profile,
            as_of=as_of,
        )

        snapshot = None
        connection_claim = None
        build = None
        point = None
        export_hash = None

        state = 'PROFILE_ONLY_NO_LOCAL_EXPORT'

    else:
        _exact(export, RAW_FIELDS, 'raw_export')

        if (
            type(export['schema_version']) is not int
            or export['schema_version'] != 1
            or export['origin'] != ORIGIN
            or export['final_action'] != 'HOLD'
            or type(export['export_id']) is not str
            or SAFE_ID.fullmatch(export['export_id']) is None
        ):
            raise MT5ExportBridgeError(
                'Unsafe local export header.'
            )

        observed = _utc(export['observed_at_utc'])

        if observed > cutoff:
            raise MT5ExportBridgeError(
                'Export declared after review cutoff.'
            )

        terminal_binding = _account_contract(
            export['account'], profile
        )

        has_rows = (
            bool(export['ticks'])
            or bool(export['rates_m5'])
        )

        connection_claim, build = _terminal_contract(
            export['terminal'],
            has_rows=has_rows,
        )

        instrument, point = _symbol_contract(
            export['symbol_info'],
            profile,
        )

        first, last = _query_contract(
            export['query'],
            profile,
            observed,
        )

        ticks = _rows_contract(
            export['ticks'],
            field_names=RAW_TICK_FIELDS,
            count_limit=MAX_TICKS,
            first=first,
            last=last,
            label='tick',
        )

        rates = _rows_contract(
            export['rates_m5'],
            field_names=RAW_BAR_FIELDS,
            count_limit=MAX_BARS,
            first=first,
            last=last,
            label='M5_bar',
        )

        snapshot = {
            'schema_version': 1,
            'origin': 'OFFLINE_MT5_EXPORT_UNATTESTED',
            'snapshot_id': export['export_id'],
            'observed_at_utc': export['observed_at_utc'],
            'terminal': terminal_binding,
            'instrument': instrument,
            'ticks': ticks,
            'm5_bars': rates,
            'final_action': 'HOLD',
        }

        adapter = inspect_mt5_offline(
            profile=profile,
            as_of=as_of,
            snapshot=snapshot,
        )

        # Canonical JSON hash only. Not a raw-byte custody receipt.
        export_hash = _digest(export)

        state = (
            'LOCAL_UNATTESTED_ROWS_QUARANTINED'
            if has_rows
            else 'EMPTY_LOCAL_EXPORT_NO_ROWS'
        )

    if (
        adapter['final_action'] != 'HOLD'
        or adapter['data_available_to_council'] is not False
        or adapter['broker_connected'] is not False
        or adapter['orders_sent'] != 0
    ):
        raise MT5ExportBridgeError(
            'Pinned offline adapter safety boundary changed.'
        )

    result = {
        'version': VERSION,
        'historical_epoch_unchanged': EPOCH,
        'as_of_utc': cutoff.isoformat(),
        'profile_id': profile['profile_id'],
        'selected_broker_label_local_claim':
            profile['broker_name'],
        'selected_server_label_local_claim':
            profile['server_name'],
        'terminal_connected_local_claim_unverified':
            connection_claim,
        'terminal_build_local_claim_unverified': build,
        'observed_symbol_point_unverified': point,
        'raw_export_canonical_json_sha256_unattested':
            export_hash,
        'derived_snapshot_canonical_json_sha256':
            None if snapshot is None else _digest(snapshot),
        'adapter_review_sha256': adapter['report_sha256'],
        'candidate_tick_count':
            len(adapter['candidate_ticks']),
        'candidate_complete_m5_count':
            len(adapter['candidate_complete_m5_bars']),
        'excluded_incomplete_m5_count':
            adapter['excluded_incomplete_m5_bars'],
        'data_status': state,
        'independent_clock_verified': False,
        'broker_account_identity_independently_verified':
            False,
        'external_source_admitted': False,
        'source_evidence_gate_count': 0,
        'source_family_admission_count': 0,
        'council_data_available': False,
        'mt5_initialized': False,
        'mt5_terminal_contacted': False,
        'network_accessed': False,
        'broker_connected': False,
        'collector_started': False,
        'orders_authorized': False,
        'orders_sent': 0,
        'historical_records_modified': False,
        'final_action': 'HOLD',
    }

    result['report_sha256'] = _digest(result)

    return result, snapshot


def write_new_snapshot(
    destination, snapshot, *, forbidden_paths=()
):
    """Create a sanitized local snapshot without overwriting inputs."""

    if snapshot is None:
        raise MT5ExportBridgeError(
            'No local export supplied for snapshot output.'
        )

    p = Path(destination)

    if (
        not p.is_absolute()
        or SAFE_OUTPUT.fullmatch(p.name) is None
        or '..' in p.parts
        or not p.parent.is_dir()
        or any(a.is_symlink() for a in (p, *p.parents))
    ):
        raise MT5ExportBridgeError(
            'Unsafe output path; existing parent directory required.'
        )

    for source in forbidden_paths:
        if os.path.normcase(os.path.abspath(source)) == (
            os.path.normcase(os.path.abspath(p))
        ):
            raise MT5ExportBridgeError(
                'Refusing to overwrite original source path.'
            )

    raw = (
        json.dumps(
            snapshot,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ) + '\n'
    ).encode('utf-8')

    if len(raw) > MAX_JSON_BYTES:
        raise MT5ExportBridgeError(
            'Derived snapshot exceeds bounded JSON size.'
        )

    try:
        with p.open('xb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise MT5ExportBridgeError(
            'Derived snapshot could not be created without overwrite.'
        ) from exc

    return sha256(raw).hexdigest()


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        '--offline-only',
        required=True,
        action='store_true',
    )
    parser.add_argument(
        '--read-only-bridge',
        required=True,
        action='store_true',
    )
    parser.add_argument(
        '--profile-file',
        required=True,
    )
    parser.add_argument(
        '--as-of',
        required=True,
    )
    parser.add_argument('--raw-export-file')

    result_group = parser.add_mutually_exclusive_group()
    result_group.add_argument('--json', action='store_true')
    result_group.add_argument('--output-snapshot')

    args = parser.parse_args(argv)

    try:
        profile = read_local_json(args.profile_file)

        raw = (
            None
            if args.raw_export_file is None
            else read_local_json(args.raw_export_file)
        )

        report, snapshot = bridge_offline_export(
            profile=profile,
            as_of=args.as_of,
            export=raw,
        )

        written_hash = None

        if args.output_snapshot:
            written_hash = write_new_snapshot(
                args.output_snapshot,
                snapshot,
                forbidden_paths=tuple(
                    x for x in (
                        args.profile_file,
                        args.raw_export_file,
                    ) if x
                ),
            )

    except (
        MT5ExportBridgeError,
        MT5OfflineAdapterError,
        OSError,
        TypeError,
        ValueError,
        KeyError,
        OverflowError,
    ):
        print(
            'MT5 READ-ONLY EXPORT BRIDGE BLOCKED: '
            'unsafe local export or profile mismatch.'
        )
        return 1

    if args.json:
        print(json.dumps(
            report,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ))
    else:
        print(
            'EURUSD ORACLE | Universal MT5 offline export bridge'
        )
        print('Profile: ' + report['profile_id'])
        print('Data: ' + report['data_status'])
        print(
            'Candidate ticks: %d | complete M5: %d | '
            'open M5 excluded: %d' % (
                report['candidate_tick_count'],
                report['candidate_complete_m5_count'],
                report['excluded_incomplete_m5_count'],
            )
        )
        print(
            'Local terminal claim independently verified: NO'
        )

        if written_hash:
            print(
                'Derived local HOLD snapshot: '
                + str(args.output_snapshot)
            )
            print(
                'New local file SHA-256: '
                + written_hash
            )

        print('Report SHA-256: ' + report['report_sha256'])
        print(
            'NO MT5 INITIALIZE | NO BROKER | '
            'NO ORDERS | FINAL=HOLD'
        )

    return 0


def main():
    raise SystemExit(run())


if __name__ == '__main__':
    main()

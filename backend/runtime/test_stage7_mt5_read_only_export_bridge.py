
"""Adversarial offline tests for broker-neutral MT5 export bridge."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import inspect
import json
from pathlib import Path

import pytest

from backend.runtime import stage7_mt5_read_only_export_bridge as m
from backend.runtime.stage7_universal_mt5_adapter import (
    example_hfm_profile,
    inspect_mt5_offline,
    read_local_json,
)

UTC = timezone.utc


def clock():
    return (
        datetime.now(UTC) - timedelta(seconds=30)
    ).replace(microsecond=0)


def z(value):
    return value.astimezone(UTC).strftime(
        '%Y-%m-%dT%H:%M:%SZ'
    )


def profile_other():
    p = deepcopy(example_hfm_profile())

    p.update(
        profile_id='OTHER_MT5_DEMO',
        broker_name='Another Broker Ltd',
        server_name='AnotherBroker-MT5-Demo',
        account_mode='NETTING',
        account_ref='SECOND_LOCAL_ALIAS',
    )

    p['instrument'].update(
        broker_symbol='EURUSD.r',
        trade_mode='FULL',
        spread_mode='FIXED',
        calculation_mode='FOREX',
        point='0.00001',
    )

    return p


def raw_export(profile=None, *, t=None):
    p = profile or example_hfm_profile()
    t = t or clock()

    floor = int(t.timestamp() // 300 * 300)
    second = int(
        (t - timedelta(seconds=15)).timestamp()
    )

    return {
        'schema_version': 1,
        'origin': m.ORIGIN,
        'export_id': 'LOCAL_EXPORT_EXAMPLE_01',
        'observed_at_utc': z(t),
        'account': {
            'company': p['broker_name'],
            'server': p['server_name'],
            'trade_mode':
                0 if p['environment'] == 'DEMO' else 2,
            'margin_mode': {
                'NETTING': 0,
                'EXCHANGE': 1,
                'HEDGING': 2,
            }[p['account_mode']],
            'local_alias': p['account_ref'],
        },
        'terminal': {
            'connected': True,
            'build': 5500,
            'trade_allowed': False,
            'tradeapi_disabled': True,
        },
        'symbol_info': {
            'name': p['instrument']['broker_symbol'],
            'digits': p['instrument']['digits'],
            'point': 0.00001,
            'trade_contract_size': 100000.0,
            'currency_margin': 'EUR',
            'currency_profit': 'USD',
            'spread_float':
                p['instrument']['spread_mode'] == 'FLOATING',
            'chart_mode': 0,
            'trade_mode': {
                'DISABLED': 0,
                'FULL': 4,
            }[p['instrument']['trade_mode']],
            'trade_calc_mode':
                1 if p['instrument']['calculation_mode']
                == 'FOREX_NO_LEVERAGE'
                else 0,
        },
        'query': {
            'symbol': p['instrument']['broker_symbol'],
            'timeframe': 'M5',
            'utc_from': z(
                datetime.fromtimestamp(floor - 1200, UTC)
            ),
            'utc_to': z(t),
            'ticks_mode': 'COPY_TICKS_ALL',
        },
        'ticks': [{
            'time': second,
            'time_msc': second * 1000 + 122,
            'bid': 1.08912,
            'ask': 1.08925,
            'last': 0.0,
            'volume': 1,
            'flags': 6,
            'volume_real': 0.0,
        }],
        'rates_m5': [{
            'time': floor - 600,
            'open': 1.08800,
            'high': 1.08930,
            'low': 1.08780,
            'close': 1.08912,
            'tick_volume': 123,
            'spread': 13,
            'real_volume': 0,
        }],
        'final_action': 'HOLD',
    }


def bridge(p=None, e=None, t=None):
    t = t or clock()
    p = p or example_hfm_profile()

    e = raw_export(p, t=t) if e is None else e

    return m.bridge_offline_export(
        profile=p,
        as_of=z(t),
        export=e,
    )


def reject(mutate, p=None):
    t = clock()
    p = p or example_hfm_profile()
    e = raw_export(p, t=t)

    mutate(e)

    with pytest.raises((
        m.MT5ExportBridgeError,
        m.MT5OfflineAdapterError,
    )):
        m.bridge_offline_export(
            profile=p,
            as_of=z(t),
            export=e,
        )


def write_json(path, content):
    path.write_text(
        json.dumps(content, sort_keys=True),
        encoding='utf-8',
    )


def test_profile_only_has_no_export_no_rows():
    p = example_hfm_profile()
    t = clock()

    report, snapshot = m.bridge_offline_export(
        profile=p,
        as_of=z(t),
        export=None,
    )

    assert snapshot is None
    assert report['data_status'] == 'PROFILE_ONLY_NO_LOCAL_EXPORT'
    assert report['candidate_tick_count'] == 0
    assert report['candidate_complete_m5_count'] == 0
    assert report['mt5_terminal_contacted'] is False
    assert report['final_action'] == 'HOLD'


def test_hfm_raw_mt5_allowlisted_fields_normalize():
    p = example_hfm_profile()
    t = clock()

    report, snapshot = bridge(p=p, t=t)

    assert report['data_status'] == (
        'LOCAL_UNATTESTED_ROWS_QUARANTINED'
    )
    assert report['candidate_tick_count'] == 1
    assert report['candidate_complete_m5_count'] == 1
    assert report['observed_symbol_point_unverified'] == '0.00001'
    assert report['terminal_connected_local_claim_unverified'] is True
    assert snapshot['terminal']['server_name'] == 'HFMarketsKE-Demo2'
    assert snapshot['instrument']['point'] is None
    assert snapshot['instrument']['trade_mode'] == 'DISABLED'
    assert snapshot['ticks'][0]['bid'] == 1.08912
    assert 'volume_real' not in snapshot['ticks'][0]
    assert report['orders_sent'] == 0
    assert report['council_data_available'] is False
    assert report['final_action'] == 'HOLD'


def test_actual_upstream_inspector_receives_valid_snapshot():
    p = example_hfm_profile()
    t = clock()

    _, snapshot = bridge(p=p, t=t)

    r = inspect_mt5_offline(
        profile=p,
        as_of=z(t),
        snapshot=snapshot,
    )

    assert r['market_data_status'] == (
        'OFFLINE_ROWS_QUARANTINED_UNVERIFIED_SOURCE'
    )
    assert r['candidate_ticks'][0]['bid'] == '1.08912'
    assert r['candidate_ticks'][0]['last'] is None
    assert r['candidate_complete_m5_bars'][0]['timeframe'] == 'M5'
    assert r['source_admission_status'] == 'NOT_ADMITTED'
    assert r['orders_sent'] == 0


def test_one_adapter_supports_different_mt5_broker_profile():
    p = profile_other()
    r, snapshot = bridge(p=p)

    assert snapshot['terminal']['server_name'] == (
        'AnotherBroker-MT5-Demo'
    )
    assert snapshot['instrument']['broker_symbol'] == 'EURUSD.r'
    assert snapshot['instrument']['trade_mode'] == 'FULL'
    assert r['candidate_tick_count'] == 1
    assert r['orders_authorized'] is False
    assert r['external_source_admitted'] is False


def test_live_account_profile_still_never_authorizes_order():
    p = profile_other()
    p['environment'] = 'LIVE'

    r, snapshot = bridge(p=p)

    assert snapshot['terminal']['environment'] == 'LIVE'
    assert r['orders_sent'] == 0
    assert r['mt5_initialized'] is False
    assert r['final_action'] == 'HOLD'


def test_different_mt5_accounting_mode_exchange_supported():
    p = profile_other()
    p['account_mode'] = 'EXCHANGE'

    r, snapshot = bridge(p=p)

    assert snapshot['terminal']['account_mode'] == 'EXCHANGE'
    assert r['broker_connected'] is False


def test_profile_unknown_point_never_promoted():
    p = example_hfm_profile()

    r, s = bridge(p=p)

    assert s['instrument']['point'] is None
    assert r['observed_symbol_point_unverified'] == '0.00001'
    assert r['independent_clock_verified'] is False


def test_raw_export_digest_is_canonical_not_external_receipt():
    t = clock()
    p = example_hfm_profile()
    e = raw_export(p, t=t)

    r, s = bridge(p, e, t)

    assert r['raw_export_canonical_json_sha256_unattested'] == (
        m._digest(e)
    )
    assert r['derived_snapshot_canonical_json_sha256'] == (
        m._digest(s)
    )
    assert r['adapter_review_sha256'] is not None
    assert r['external_source_admitted'] is False


def test_report_deterministic_for_same_inputs():
    t = clock()
    p = example_hfm_profile()
    e = raw_export(t=t)

    a, snap1 = bridge(p, e, t)
    b, snap2 = bridge(p, e, t)

    assert a == b
    assert snap1 == snap2

    assert a['report_sha256'] == m._digest({
        k: v for k, v in a.items()
        if k != 'report_sha256'
    })


def test_empty_export_makes_no_market_data_claims():
    t = clock()
    e = raw_export(t=t)

    e['ticks'] = []
    e['rates_m5'] = []
    e['terminal']['connected'] = False

    r, snap = bridge(e=e, t=t)

    assert snap['ticks'] == []
    assert r['candidate_tick_count'] == 0
    assert r['terminal_connected_local_claim_unverified'] is False
    assert r['data_status'] == 'EMPTY_LOCAL_EXPORT_NO_ROWS'
    assert r['council_data_available'] is False


def test_incomplete_latest_m5_bar_excluded_by_upstream():
    t = clock()
    e = raw_export(t=t)
    floored = int(t.timestamp() // 300 * 300)

    unfinished = dict(
        e['rates_m5'][0],
        time=floored,
    )

    e['rates_m5'].append(unfinished)

    r, snap = bridge(e=e, t=t)

    assert r['candidate_complete_m5_count'] == 1
    assert r['excluded_incomplete_m5_count'] == 1


def test_out_of_order_ticks_rejected_by_upstream():
    t = clock()
    e = raw_export(t=t)

    row = deepcopy(e['ticks'][0])
    row['time'] -= 5
    row['time_msc'] -= 5000

    e['ticks'].append(row)

    with pytest.raises(m.MT5OfflineAdapterError):
        bridge(e=e, t=t)


def test_duplicate_mt5_tick_millis_rejected():
    reject(
        lambda e: e['ticks'].append(
            deepcopy(e['ticks'][0])
        )
    )


def test_missing_volume_real_cannot_enter_allowlist():
    reject(
        lambda e: e['ticks'][0].pop('volume_real')
    )


def test_extra_volume_real_validated_and_discarded_from_upstream_snapshot():
    reject(
        lambda e: e['ticks'][0].__setitem__(
            'volume_real', -1
        )
    )


def test_tick_time_ms_tamper_refused():
    reject(
        lambda e: e['ticks'][0].__setitem__(
            'time_msc',
            e['ticks'][0]['time_msc'] + 1000,
        )
    )


def test_infinite_tick_price_refused_by_upstream():
    reject(
        lambda e: e['ticks'][0].__setitem__(
            'ask',
            float('inf'),
        )
    )


def test_outside_query_window_tick_rejected():
    reject(
        lambda e: e['ticks'][0].__setitem__(
            'time',
            int(
                datetime(
                    2020, 1, 1, tzinfo=UTC
                ).timestamp()
            ),
        )
    )


def test_outside_query_window_m5_rejected():
    reject(
        lambda e: e['rates_m5'][0].__setitem__(
            'time', 1600000000
        )
    )


def test_bad_ohlc_rejected_by_upstream():
    reject(
        lambda e: e['rates_m5'][0].__setitem__(
            'high', 1.00000
        )
    )


@pytest.mark.parametrize('key,value', [
    ('schema_version', True),
    ('schema_version', 2),
    ('origin', 'DIRECT_MT5_LIVE_CONNECTED'),
    ('export_id', '../bad'),
    ('observed_at_utc', '2026-10-31T14:00:00+03:00'),
    ('final_action', 'BUY'),
])
def test_unsafe_raw_export_header_rejected(key, value):
    reject(
        lambda e: e.__setitem__(key, value)
    )


@pytest.mark.parametrize('key,value', [
    ('company', 'Another Broker Ltd'),
    ('server', 'AnotherServer-Demo'),
    ('local_alias', 'OTHER_ALIAS'),
    ('trade_mode', 1),
    ('trade_mode', True),
    ('margin_mode', 0),
    ('margin_mode', 3),
])
def test_unsafe_account_identity_or_environment_rejected(
    key, value
):
    reject(
        lambda e: e['account'].__setitem__(
            key, value
        )
    )


@pytest.mark.parametrize('key,value', [
    ('connected', 'true'),
    ('build', -1),
    ('trade_allowed', 1),
    ('tradeapi_disabled', 'False'),
])
def test_bad_terminal_shapes_refused(key, value):
    reject(
        lambda e: e['terminal'].__setitem__(
            key, value
        )
    )


def test_disconnected_terminal_with_rows_refused():
    reject(
        lambda e: e['terminal'].__setitem__(
            'connected', False
        )
    )


def test_connected_claim_does_not_overrule_disabled_symbol():
    t = clock()
    e = raw_export(t=t)

    e['terminal']['trade_allowed'] = True
    e['terminal']['tradeapi_disabled'] = False

    r, snap = bridge(e=e, t=t)

    assert snap['instrument']['trade_mode'] == 'DISABLED'
    assert r['orders_sent'] == 0


@pytest.mark.parametrize('key,value', [
    ('name', 'EURUSD.x'),
    ('digits', 4),
    ('point', 0.0001),
    ('point', 0),
    ('trade_contract_size', 200000),
    ('currency_margin', 'USD'),
    ('currency_profit', 'EUR'),
    ('spread_float', 0),
    ('chart_mode', 1),
    ('trade_mode', 4),
    ('trade_calc_mode', 0),
])
def test_symbol_mismatch_against_selected_profile_rejected(
    key, value
):
    reject(
        lambda e: e['symbol_info'].__setitem__(
            key, value
        )
    )


def test_known_profile_point_matches_numeric_mt5_point():
    p = profile_other()

    r, _ = bridge(p=p)

    assert r['observed_symbol_point_unverified'] == '0.00001'


def test_future_export_time_rejected():
    reject(
        lambda e: e.__setitem__(
            'observed_at_utc',
            z(clock() + timedelta(days=1)),
        )
    )


@pytest.mark.parametrize('key,value', [
    ('symbol', 'EURUSDm'),
    ('timeframe', 'M1'),
    ('ticks_mode', 'COPY_TICKS_INFO'),
    ('utc_from', '2026-10-31T14:00:00+03:00'),
])
def test_query_mismatch_refused(key, value):
    reject(
        lambda e: e['query'].__setitem__(
            key, value
        )
    )


def test_noncausal_query_range_rejected():
    reject(
        lambda e: e['query'].__setitem__(
            'utc_from',
            e['query']['utc_to'],
        )
    )


def test_future_query_window_rejected():
    reject(
        lambda e: e['query'].__setitem__(
            'utc_to',
            z(clock() + timedelta(seconds=90)),
        )
    )


def test_extra_account_secret_key_refused():
    reject(
        lambda e: e['account'].update(
            login=12345678
        )
    )

    reject(
        lambda e: e['account'].update(
            password='NEVER_STORE'
        )
    )


def test_extra_terminal_data_secret_refused():
    reject(
        lambda e: e['terminal'].update(
            path=r'C:\Users\secret'
        )
    )


def test_raw_tick_token_field_refused():
    reject(
        lambda e: e['ticks'][0].update(
            api_key='NEVER_STORE'
        )
    )


def test_raw_symbol_info_extra_field_refused():
    reject(
        lambda e: e['symbol_info'].update(
            account_id=1234
        )
    )


def test_raw_export_external_authorization_refused():
    reject(
        lambda e: e.update(
            external_gates_verified=11
        )
    )


def test_raw_tick_over_limit_refused():
    reject(
        lambda e: e.__setitem__(
            'ticks',
            [deepcopy(e['ticks'][0])] * 257,
        )
    )


def test_raw_m5_over_limit_refused():
    reject(
        lambda e: e.__setitem__(
            'rates_m5',
            [deepcopy(e['rates_m5'][0])] * 129,
        )
    )


def test_no_source_file_is_edited_by_snapshot_writer(tmp_path):
    p = tmp_path / 'source.json'
    s = tmp_path / 'output.json'

    p.write_text('source')

    _, snap = bridge()

    before = sha256(p.read_bytes()).hexdigest()

    digest = m.write_new_snapshot(
        s,
        snap,
        forbidden_paths=(p,),
    )

    assert sha256(s.read_bytes()).hexdigest() == digest
    assert sha256(p.read_bytes()).hexdigest() == before
    assert read_local_json(s)['final_action'] == 'HOLD'


def test_snapshot_writer_never_overwrites_existing(tmp_path):
    p = tmp_path / 'existing.json'
    p.write_text('DO_NOT_OVERWRITE')

    _, snap = bridge()

    with pytest.raises(m.MT5ExportBridgeError):
        m.write_new_snapshot(p, snap)

    assert p.read_text() == 'DO_NOT_OVERWRITE'


def test_snapshot_writer_rejects_source_path_even_when_not_existing(
    tmp_path
):
    p = tmp_path / 'source.json'
    _, snap = bridge()

    with pytest.raises(m.MT5ExportBridgeError):
        m.write_new_snapshot(
            p,
            snap,
            forbidden_paths=(p,),
        )

    assert not p.exists()


def test_snapshot_writer_rejects_relative_path():
    _, snap = bridge()

    with pytest.raises(m.MT5ExportBridgeError):
        m.write_new_snapshot('local.json', snap)


def test_snapshot_writer_rejects_parent_traversal(tmp_path):
    _, snap = bridge()

    with pytest.raises(m.MT5ExportBridgeError):
        m.write_new_snapshot(
            tmp_path / '..' / 'escaped.json',
            snap,
        )


def test_snapshot_writer_rejects_parent_symlink_if_supported(
    tmp_path
):
    p = tmp_path / 'actual'
    p.mkdir()

    link = tmp_path / 'alias'

    try:
        link.symlink_to(
            p,
            target_is_directory=True,
        )
    except (OSError, NotImplementedError):
        pytest.skip(
            'Symlink privilege unavailable on Windows'
        )

    _, snap = bridge()

    with pytest.raises(m.MT5ExportBridgeError):
        m.write_new_snapshot(link / 'output.json', snap)


def test_snapshot_writer_blocks_missing_export(tmp_path):
    with pytest.raises(m.MT5ExportBridgeError):
        m.write_new_snapshot(
            tmp_path / 'nothing.json',
            None,
        )


def test_cli_profile_only_smoke(tmp_path, capsys):
    p = tmp_path / 'profile.json'
    write_json(p, example_hfm_profile())

    t = clock()

    rc = m.run([
        '--offline-only',
        '--read-only-bridge',
        '--profile-file',
        str(p),
        '--as-of',
        z(t),
    ])

    output = capsys.readouterr().out

    assert rc == 0
    assert 'PROFILE_ONLY_NO_LOCAL_EXPORT' in output
    assert 'NO MT5 INITIALIZE' in output
    assert 'FINAL=HOLD' in output


def test_cli_raw_export_json_report(tmp_path, capsys):
    p = tmp_path / 'profile.json'
    s = tmp_path / 'raw.json'

    t = clock()

    write_json(p, example_hfm_profile())
    write_json(s, raw_export(t=t))

    before = [
        sha256(v.read_bytes()).hexdigest()
        for v in (p, s)
    ]

    rc = m.run([
        '--offline-only',
        '--read-only-bridge',
        '--profile-file',
        str(p),
        '--raw-export-file',
        str(s),
        '--as-of',
        z(t),
        '--json',
    ])

    doc = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert doc['candidate_tick_count'] == 1
    assert doc['source_evidence_gate_count'] == 0
    assert doc['orders_sent'] == 0

    assert before == [
        sha256(v.read_bytes()).hexdigest()
        for v in (p, s)
    ]


def test_cli_valid_export_to_new_snapshot_file(
    tmp_path, capsys
):
    p = tmp_path / 'profile.json'
    r = tmp_path / 'raw.json'
    o = tmp_path / 'derived.json'

    t = clock()

    write_json(p, example_hfm_profile())
    write_json(r, raw_export(t=t))

    rc = m.run([
        '--offline-only',
        '--read-only-bridge',
        '--profile-file',
        str(p),
        '--raw-export-file',
        str(r),
        '--as-of',
        z(t),
        '--output-snapshot',
        str(o),
    ])

    assert rc == 0
    assert 'FINAL=HOLD' in capsys.readouterr().out

    snap = read_local_json(o)

    assert snap['final_action'] == 'HOLD'
    assert len(snap['ticks']) == 1

    assert inspect_mt5_offline(
        profile=example_hfm_profile(),
        as_of=z(t),
        snapshot=snap,
    )['orders_sent'] == 0


def test_cli_fails_if_export_claims_other_account(
    tmp_path, capsys
):
    p = tmp_path / 'profile.json'
    r = tmp_path / 'raw.json'
    o = tmp_path / 'derived.json'

    t = clock()
    e = raw_export(t=t)
    e['account']['server'] = 'WRONG_SERVER'

    write_json(p, example_hfm_profile())
    write_json(r, e)

    assert m.run([
        '--offline-only',
        '--read-only-bridge',
        '--profile-file',
        str(p),
        '--raw-export-file',
        str(r),
        '--as-of',
        z(t),
        '--output-snapshot',
        str(o),
    ]) == 1

    assert not o.exists()
    assert 'BLOCKED' in capsys.readouterr().out


def test_cli_cannot_export_no_data(tmp_path, capsys):
    p = tmp_path / 'profile.json'
    write_json(p, example_hfm_profile())

    assert m.run([
        '--offline-only',
        '--read-only-bridge',
        '--profile-file',
        str(p),
        '--as-of',
        z(clock()),
        '--output-snapshot',
        str(tmp_path / 'x.json'),
    ]) == 1

    assert 'BLOCKED' in capsys.readouterr().out


def test_cli_required_flags():
    with pytest.raises(SystemExit) as ex:
        m.run([
            '--profile-file',
            'x.json',
        ])

    assert ex.value.code == 2


def test_cli_json_and_export_are_mutually_exclusive(
    tmp_path
):
    with pytest.raises(SystemExit) as ex:
        m.run([
            '--offline-only',
            '--read-only-bridge',
            '--profile-file',
            'x.json',
            '--as-of',
            z(clock()),
            '--json',
            '--output-snapshot',
            str(tmp_path / 'x.json'),
        ])

    assert ex.value.code == 2


def test_no_meta_trader_module_imports_or_transport_calls():
    source = inspect.getsource(m)

    for forbidden in (
        'import MetaTrader5',
        'mt5.initialize(',
        'mt5.login(',
        'copy_ticks_range(',
        'copy_rates_range(',
        'order_send(',
        'requests.',
        'socket.',
        'subprocess.',
        'run_collector(',
    ):
        assert forbidden not in source


def test_fractional_query_bound_refused():
    reject(
        lambda e: e['query'].__setitem__(
            'utc_from',
            e['query']['utc_from'].replace(
                'Z', '.5Z'
            ),
        )
    )


def test_more_than_24_hour_query_refused():
    reject(
        lambda e: e['query'].__setitem__(
            'utc_from',
            z(clock() - timedelta(days=2)),
        )
    )

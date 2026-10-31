
"""Stage 7 universal MT5 offline broker contract and adversarial fixture tests."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import inspect as pyinspect
import json
from pathlib import Path

import pytest

from backend.runtime import stage7_universal_mt5_adapter as m

UTC = timezone.utc


def clock():
    return (datetime.now(UTC) - timedelta(seconds=40)).replace(microsecond=0)


def utc(t):
    return t.strftime('%Y-%m-%dT%H:%M:%SZ')


def candidate_profile():
    return m.example_hfm_profile()


def second_broker_profile():
    x = deepcopy(candidate_profile())
    x.update(profile_id='OTHER_DEMO', broker_name='Other Broker Ltd',
             server_name='OtherBroker-MT5-Demo', environment='DEMO',
             account_mode='NETTING', account_ref='SECOND_LOCAL_ALIAS')
    x['instrument'].update(broker_symbol='EURUSD.r', trade_mode='FULL',
                           point='0.00001', calculation_mode='FOREX',
                           spread_mode='FIXED')
    return x


def tick(t, *, offset=-8, millis=211):
    second = int((t + timedelta(seconds=offset)).timestamp())
    return {'time': second, 'time_msc': second * 1000 + millis,
            'bid': 1.08912, 'ask': 1.08925, 'last': 0, 'volume': 1, 'flags': 6}


def bar(t, *, offset=-600):
    floor = int(t.timestamp() // 300 * 300)
    return {'time': floor + offset, 'open': 1.08800, 'high': 1.08930,
            'low': 1.08780, 'close': 1.08912, 'tick_volume': 123,
            'spread': 13, 'real_volume': 0}


def snapshot(profile=None, *, t=None):
    profile = profile or candidate_profile()
    t = t or clock()
    return {
        'schema_version': 1, 'origin': 'OFFLINE_MT5_EXPORT_UNATTESTED',
        'snapshot_id': 'SAMPLE_OFFLINE_001', 'observed_at_utc': utc(t),
        'terminal': {name: profile[name] for name in m.TERMINAL_FIELDS},
        'instrument': {name: profile['instrument'][name] for name in m.SNAPSHOT_SYMBOL_FIELDS},
        'ticks': [tick(t)], 'm5_bars': [bar(t)], 'final_action': 'HOLD',
    }


def inspect(profile=None, snap=None, t=None):
    t = t or clock()
    return m.inspect_mt5_offline(profile=profile or candidate_profile(),
                                  as_of=utc(t), snapshot=snap)


def block_profile(change):
    p = candidate_profile()
    change(p)
    with pytest.raises(m.MT5OfflineAdapterError):
        inspect(profile=p)


def block_snapshot(change):
    t = clock()
    p = candidate_profile()
    s = snapshot(p, t=t)
    change(s, t)
    with pytest.raises(m.MT5OfflineAdapterError):
        inspect(profile=p, snap=s, t=t)


def test_hfm_example_comes_from_visible_settings_with_unverified_fields():
    p = candidate_profile()
    assert p['server_name'] == 'HFMarketsKE-Demo2'
    assert p['broker_name'] == 'HFM Investments Ltd'
    assert p['environment'] == 'DEMO'
    assert p['account_mode'] == 'HEDGING'
    assert p['instrument']['trade_mode'] == 'DISABLED'
    assert p['instrument']['digits'] == 5
    assert p['instrument']['contract_size'] == '100000'
    assert p['instrument']['point'] is None
    assert p['source_admitted'] is False
    assert p['final_action'] == 'HOLD'


def test_profile_only_never_invents_prices():
    result = inspect()
    assert result['market_data_status'] == 'PROFILE_ONLY_NO_MARKET_DATA'
    assert result['candidate_ticks'] == []
    assert result['candidate_complete_m5_bars'] == []
    assert result['broker_connected'] is False
    assert result['data_available_to_council'] is False
    assert result['final_action'] == 'HOLD'


def test_normalize_one_tick_and_one_complete_m5_bar():
    t = clock()
    p = candidate_profile()
    s = snapshot(p, t=t)
    result = inspect(profile=p, snap=s, t=t)
    assert result['market_data_status'] == 'OFFLINE_ROWS_QUARANTINED_UNVERIFIED_SOURCE'
    assert len(result['candidate_ticks']) == 1
    assert len(result['candidate_complete_m5_bars']) == 1
    first = result['candidate_ticks'][0]
    assert first['time_raw'] == s['ticks'][0]['time']
    assert first['time_msc_raw'] == s['ticks'][0]['time_msc']
    assert first['bid'] == '1.08912'
    assert first['ask'] == '1.08925'
    assert first['spread_price'] == '0.00013'
    assert first['time_domain_independently_verified'] is False
    assert first['admitted_for_council'] is False
    assert result['candidate_complete_m5_bars'][0]['timeframe'] == 'M5'
    assert result['independent_external_gates_verified'] == 0
    assert result['final_action'] == 'HOLD'


def test_second_mt5_broker_same_contract_with_new_server_symbol_and_mode():
    t = clock()
    for p in (candidate_profile(), second_broker_profile()):
        r = inspect(profile=p, snap=snapshot(p, t=t), t=t)
        assert r['symbol'] == 'EURUSD'
        assert len(r['candidate_ticks']) == 1
        assert r['candidate_ticks'][0]['bid'] == '1.08912'
        assert r['source_admission_status'] == 'NOT_ADMITTED'
        assert r['live_orders_authorized'] is False
        assert r['mt5_initialized'] is False
    assert second_broker_profile()['instrument']['broker_symbol'] == 'EURUSD.r'


def test_mt5_numeric_snapshot_specs_match_equivalent_string_profile_values():
    t = clock()
    p = second_broker_profile()
    s = snapshot(p, t=t)
    s['instrument']['point'] = 0.00001
    s['instrument']['contract_size'] = 100000.0
    r = inspect(profile=p, snap=s, t=t)
    assert len(r['candidate_ticks']) == 1
    assert r['source_admission_status'] == 'NOT_ADMITTED'


def test_time_msc_to_utc_candidate_is_millisecond_exact():
    t = clock()
    r = inspect(snap=snapshot(t=t), t=t)
    assert r['candidate_ticks'][0]['utc_candidate'].endswith('.211000+00:00')


def test_full_trade_mode_cannot_authorize_trading():
    p = second_broker_profile()
    r = inspect(profile=p)
    assert r['trade_mode_local_claim'] == 'FULL'
    assert r['orders_sent'] == 0
    assert r['paper_orders_authorized'] is False
    assert r['live_orders_authorized'] is False
    assert r['data_available_to_council'] is False


def test_profile_timestamp_and_local_digest_determinism():
    t = clock()
    p, s = candidate_profile(), snapshot(t=t)
    a = inspect(profile=p, snap=s, t=t)
    b = inspect(profile=p, snap=s, t=t)
    assert a == b
    assert a['report_sha256'] == m._digest({k: v for k, v in a.items()
                                              if k != 'report_sha256'})
    assert a['broker_utc_offset_applied'] is False
    assert a['historical_epoch_unchanged'] == m.EPOCH


def test_incomplete_candle_excluded_without_inventing_completion():
    t = clock()
    s = snapshot(t=t)
    floor = int(t.timestamp() // 300 * 300)
    unfinished = bar(t)
    unfinished['time'] = floor
    s['m5_bars'].append(unfinished)
    r = inspect(snap=s, t=t)
    assert r['excluded_incomplete_m5_bars'] == 1
    assert len(r['candidate_complete_m5_bars']) == 1


def test_gaps_preserved_as_missing_not_interpolated():
    t = clock()
    s = snapshot(t=t)
    older = bar(t, offset=-1500)
    s['m5_bars'].insert(0, older)
    r = inspect(snap=s, t=t)
    assert r['missing_m5_intervals_unverified'] == 2
    assert len(r['candidate_complete_m5_bars']) == 2


def test_multiple_sorted_ticks_accepted():
    t = clock()
    s = snapshot(t=t)
    s['ticks'] = [tick(t, offset=-20), tick(t, offset=-10), tick(t, offset=-8)]
    r = inspect(snap=s, t=t)
    assert len(r['candidate_ticks']) == 3


def test_no_tick_no_bar_is_still_unverified():
    t = clock()
    s = snapshot(t=t)
    s['ticks'] = []
    s['m5_bars'] = []
    r = inspect(snap=s, t=t)
    assert r['candidate_ticks'] == []
    assert r['source_admission_status'] == 'NOT_ADMITTED'


@pytest.mark.parametrize('key,value', [
    ('platform', 'MT4'), ('environment', 'SIMULATED'),
    ('account_mode', 'AGGREGATED'), ('broker_name', '../secrets'),
    ('server_name', '<script>'), ('profile_id', '../../file'),
    ('timestamp_contract', 'BROKER_UTC_VERIFIED'),
    ('source_admitted', True), ('trading_authorized', True),
    ('final_action', 'BUY'), ('schema_version', True),
])
def test_invalid_profile_fields_refused(key, value):
    block_profile(lambda p: p.__setitem__(key, value))


@pytest.mark.parametrize('key,value', [
    ('canonical_symbol', 'GBPUSD'), ('broker_symbol', 'EUR/USD'),
    ('digits', 0), ('digits', 10), ('digits', True),
    ('point', '0.0001'), ('point', -1), ('contract_size', '0'),
    ('contract_size', '-1'), ('margin_currency', 'eur'),
    ('profit_currency', 'USD>'), ('spread_mode', 'CUSTOM'),
    ('trade_mode', 'BUY_NOW'), ('calculation_mode', 'UNKNOWN_UNSAFE'),
    ('chart_price', 'OFFERS'),
])
def test_invalid_instrument_spec_refused(key, value):
    block_profile(lambda p: p['instrument'].__setitem__(key, value))


def test_unknown_instrument_point_accepted_but_not_verified():
    p = candidate_profile()
    assert inspect(profile=p)['point_verified'] is False


def test_unexpected_account_id_or_password_cannot_be_added_to_profile():
    block_profile(lambda p: p.update(password='do-not-collect'))
    block_profile(lambda p: p.update(login=123456))


def test_extra_trade_authorization_claim_in_snapshot_refused():
    block_snapshot(lambda s, t: s.update(order_send_enabled=True))


@pytest.mark.parametrize('key,value', [
    ('platform', 'MT4'), ('broker_name', 'Wrong Broker'),
    ('server_name', 'OtherBroker-MT5-Demo'), ('environment', 'LIVE'),
    ('account_mode', 'NETTING'), ('account_ref', 'NOT_THE_SELECTED_ALIAS'),
])
def test_terminal_profile_binding_mismatch_refused(key, value):
    block_snapshot(lambda s, t: s['terminal'].__setitem__(key, value))


@pytest.mark.parametrize('key,value', [
    ('broker_symbol', 'EURUSD.r'), ('digits', 4),
    ('point', '0.00001'), ('contract_size', '200000'),
    ('trade_mode', 'FULL'), ('chart_price', 'LAST'), ('spread_mode', 'FIXED'),
])
def test_symbol_spec_drift_refused(key, value):
    block_snapshot(lambda s, t: s['instrument'].__setitem__(key, value))


def test_snapshot_claim_cannot_approve_source():
    block_snapshot(lambda s, t: s.update(source_admitted=True))


def test_snapshot_claim_cannot_authorize_execution():
    block_snapshot(lambda s, t: s.__setitem__('final_action', 'BUY'))


def test_snapshot_after_cutoff_refused():
    block_snapshot(lambda s, t: s.__setitem__(
        'observed_at_utc', utc(t + timedelta(seconds=1))))


def test_utc_timestamp_without_timezone_refused():
    block_snapshot(lambda s, t: s.__setitem__(
        'observed_at_utc', t.strftime('%Y-%m-%dT%H:%M:%S')))


def test_utc_timestamp_with_nairobi_offset_refused():
    block_snapshot(lambda s, t: s.__setitem__(
        'observed_at_utc', '2026-10-31T14:38:00+03:00'))


def test_future_review_cutoff_refused():
    with pytest.raises(m.MT5OfflineAdapterError):
        inspect(t=datetime.now(UTC) + timedelta(days=1))


def test_tick_time_second_and_millisecond_disagree():
    block_snapshot(lambda s, t: s['ticks'][0].__setitem__(
        'time_msc', s['ticks'][0]['time_msc'] + 1000))


def test_tick_future_relative_to_local_snapshot_refused():
    def mutate(s, t):
        second = int((t + timedelta(seconds=1)).timestamp())
        s['ticks'][0]['time'] = second
        s['ticks'][0]['time_msc'] = second * 1000
    block_snapshot(mutate)


def test_duplicate_tick_timestamp_refused():
    block_snapshot(lambda s, t: s['ticks'].append(deepcopy(s['ticks'][0])))


def test_tick_out_of_order_refused():
    block_snapshot(lambda s, t: s['ticks'].insert(0, tick(t, offset=-1)))


@pytest.mark.parametrize('key,value', [
    ('bid', -1), ('bid', True), ('bid', float('nan')),
    ('ask', float('inf')), ('ask', 1.000001),
    ('volume', -1), ('volume', True), ('flags', -1), ('time', 1.5),
])
def test_invalid_tick_measurements_refused(key, value):
    block_snapshot(lambda s, t: s['ticks'][0].__setitem__(key, value))


def test_crossed_bid_ask_refused():
    block_snapshot(lambda s, t: s['ticks'][0].__setitem__('ask', 1.00000))


def test_tick_extra_raw_account_token_refused():
    block_snapshot(lambda s, t: s['ticks'][0].update(api_key='redacted'))


def test_tick_limit_refused():
    block_snapshot(lambda s, t: s.__setitem__('ticks', [{}] * (m.MAX_TICKS + 1)))


def test_bar_limit_refused():
    block_snapshot(lambda s, t: s.__setitem__('m5_bars', [{}] * (m.MAX_BARS + 1)))


def test_misaligned_m5_candle_refused():
    block_snapshot(lambda s, t: s['m5_bars'][0].__setitem__(
        'time', s['m5_bars'][0]['time'] + 60))


def test_duplicate_m5_candle_refused():
    block_snapshot(lambda s, t: s['m5_bars'].append(deepcopy(s['m5_bars'][0])))


def test_future_m5_candle_refused():
    block_snapshot(lambda s, t: s['m5_bars'][0].__setitem__(
        'time', int(t.timestamp()//300*300) + 300))


def test_broken_ohlc_refused():
    block_snapshot(lambda s, t: s['m5_bars'][0].__setitem__('low', 1.09999))


def test_negative_m5_spread_refused():
    block_snapshot(lambda s, t: s['m5_bars'][0].__setitem__('spread', -2))


def test_source_rows_do_not_include_account_secrets():
    t = clock()
    r = inspect(snap=snapshot(t=t), t=t)
    raw = json.dumps(r)
    assert 'password' not in raw.lower()
    assert 'account_number' not in raw.lower()
    assert 'margin' not in r['candidate_ticks'][0]


def test_normalized_candle_can_never_carry_execution_authority():
    t = clock()
    r = inspect(snap=snapshot(t=t), t=t)
    assert all(x['admitted_for_council'] is False for x in r['candidate_complete_m5_bars'])
    assert all(x['admitted_for_council'] is False for x in r['candidate_ticks'])
    assert r['final_action'] == 'HOLD'


def test_json_reader_rejects_duplicate_fields(tmp_path):
    p = tmp_path / 'x.json'
    p.write_text('{"final_action":"HOLD","final_action":"BUY"}')
    with pytest.raises(m.MT5OfflineAdapterError, match='Duplicate'):
        m.read_local_json(p)


def test_json_reader_rejects_nonfinite_numbers(tmp_path):
    p = tmp_path / 'x.json'
    p.write_text('{"value": NaN}')
    with pytest.raises(m.MT5OfflineAdapterError, match='Nonfinite'):
        m.read_local_json(p)


def test_json_reader_rejects_oversized_file(tmp_path):
    p = tmp_path / 'x.json'
    p.write_bytes(b' ' * (m.MAX_JSON_BYTES+1))
    with pytest.raises(m.MT5OfflineAdapterError):
        m.read_local_json(p)


def test_json_reader_rejects_non_json_extensions(tmp_path):
    p = tmp_path / '.env'
    p.write_text('DO_NOT_READ')
    with pytest.raises(m.MT5OfflineAdapterError):
        m.read_local_json(p)


def test_json_reader_rejects_symlinks_if_supported(tmp_path):
    p = tmp_path / 'regular.json'
    p.write_text('{}')
    link = tmp_path / 'alias.json'
    try:
        link.symlink_to(p)
    except (OSError, NotImplementedError):
        pytest.skip('Windows symlink creation privilege unavailable')
    with pytest.raises(m.MT5OfflineAdapterError):
        m.read_local_json(link)


def test_cli_print_hfm_profile_without_connecting(capsys):
    rc = m.run(['--offline-only', '--read-only-mt5', '--example-hfm-profile'])
    assert rc == 0
    p = json.loads(capsys.readouterr().out)
    assert p['server_name'] == 'HFMarketsKE-Demo2'
    assert p['trading_authorized'] is False


def test_cli_profile_only_returns_hold(tmp_path, capsys):
    p = tmp_path / 'profile.json'
    p.write_text(json.dumps(candidate_profile()))
    assert m.run(['--offline-only', '--read-only-mt5', '--profile-file', str(p),
                  '--as-of', utc(clock())]) == 0
    msg = capsys.readouterr().out
    assert 'NO MT5 INITIALIZE' in msg
    assert 'FINAL=HOLD' in msg


def test_cli_with_offline_snapshot_and_json(tmp_path, capsys):
    t = clock()
    p = tmp_path / 'profile.json'
    s = tmp_path / 'snapshot.json'
    p.write_text(json.dumps(candidate_profile()))
    s.write_text(json.dumps(snapshot(t=t)))
    old = (sha256(p.read_bytes()).hexdigest(), sha256(s.read_bytes()).hexdigest())
    assert m.run(['--offline-only', '--read-only-mt5', '--profile-file', str(p),
                  '--snapshot-file', str(s), '--as-of', utc(t), '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report['candidate_ticks']) == 1
    assert report['market_data_status'] == 'OFFLINE_ROWS_QUARANTINED_UNVERIFIED_SOURCE'
    assert report['broker_connected'] is False
    assert report['final_action'] == 'HOLD'
    assert old == (sha256(p.read_bytes()).hexdigest(), sha256(s.read_bytes()).hexdigest())


def test_cli_rejects_bad_snapshot_before_reporting(tmp_path, capsys):
    t = clock()
    p = tmp_path / 'profile.json'
    s = tmp_path / 'snapshot.json'
    p.write_text(json.dumps(candidate_profile()))
    bad = snapshot(t=t)
    bad['terminal']['server_name'] = 'UNEXPECTED'
    s.write_text(json.dumps(bad))
    assert m.run(['--offline-only', '--read-only-mt5', '--profile-file', str(p),
                  '--snapshot-file', str(s), '--as-of', utc(t)]) == 1
    assert 'BLOCKED' in capsys.readouterr().out


def test_cli_disallows_snapshot_with_example_profile(capsys):
    assert m.run(['--offline-only', '--read-only-mt5', '--example-hfm-profile',
                  '--snapshot-file', 'secret.json']) == 1
    assert 'BLOCKED' in capsys.readouterr().out


def test_cli_requires_offline_read_only_flags():
    with pytest.raises(SystemExit) as error:
        m.run(['--example-hfm-profile'])
    assert error.value.code == 2


def test_no_live_mt5_import_or_unsafe_execution_interface():
    source = pyinspect.getsource(m)
    for forbidden in ('import MetaTrader5', 'mt5.initialize(', 'mt5.login(',
                      'order_send(', 'copy_ticks_from(', 'copy_rates_from(',
                      'import requests', 'import socket', 'import subprocess',
                      'from backend.data_engine.mt5_provider import'):
        assert forbidden not in source

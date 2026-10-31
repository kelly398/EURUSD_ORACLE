
"""Offline DP-M2 feed identity tests; no MT5/database connection or mutation."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import inspect
import re

import pytest

from backend.data_engine import feed_identity as feeds
from backend.data_engine import tick_identity as ticks
from backend.runtime.stage7_universal_mt5_adapter import example_hfm_profile

UTC = timezone.utc
EPOCH = 'FUTURE_RESEARCH_20261101_A'


def hfm():
    return example_hfm_profile()


def other():
    p = deepcopy(hfm())
    p.update(
        profile_id='ANOTHER_MT5_DEMO',
        broker_name='Another Broker Ltd',
        server_name='AnotherBroker-MT5-Demo',
        account_mode='NETTING',
        account_ref='OTHER_LOCAL_ALIAS',
    )
    p['instrument'].update(
        broker_symbol='EURUSD.r',
        point='0.00001',
        trade_mode='FULL',
        spread_mode='FIXED',
        calculation_mode='FOREX',
    )
    return p


def identity(profile=None, epoch=EPOCH):
    return feeds.build_local_feed_identity(
        profile or hfm(),
        capture_epoch=epoch,
    )


def tick_input():
    return dict(
        symbol='EURUSD',
        time_msc=1793520000112,
        bid=1.08912,
        ask=1.08925,
        last=0,
        volume=1,
        flags=6,
        volume_real=0,
    )


def v2(profile=None, **changes):
    inputs = tick_input()
    inputs.update(changes)
    return ticks.create_tick_fingerprint_v2(
        feed_id=identity(profile)['feed_id'],
        **inputs,
    )


def test_same_profile_same_epoch_is_deterministic():
    a, b = identity(), identity()
    assert a == b
    assert feeds.require_feed_id(a['feed_id']) == a['feed_id']
    assert a['feed_id'] == 'mt5_eurusd_v1_' + a['identity_sha256']
    assert len(a['identity_sha256']) == 64


def test_hfm_and_second_broker_get_distinct_feed_ids():
    assert identity()['feed_id'] != identity(other())['feed_id']
    assert identity()['identity_claims']['broker_symbol'] == 'EURUSD'
    assert identity(other())['identity_claims']['broker_symbol'] == 'EURUSD.r'


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(profile_id='ANOTHER_PROFILE_ID'),
    lambda p: p.update(broker_name='Another Broker Ltd'),
    lambda p: p.update(server_name='OtherBroker-MT5-Demo'),
    lambda p: p.update(environment='LIVE'),
    lambda p: p.update(account_mode='NETTING'),
    lambda p: p.update(account_ref='SECOND_LOCAL_ALIAS'),
    lambda p: p['instrument'].update(broker_symbol='EURUSD.r'),
])
def test_identity_binding_changes_when_source_routing_claim_changes(mutate):
    p = hfm()
    mutate(p)
    assert identity(p)['feed_id'] != identity()['feed_id']


def test_new_capture_epoch_has_new_feed_id():
    assert identity(epoch='FUTURE_RESEARCH_20261101_B')['feed_id'] != identity()['feed_id']


def test_transient_trade_mode_does_not_split_source_identity():
    p = hfm()
    p['instrument']['trade_mode'] = 'FULL'
    assert identity(p)['feed_id'] == identity()['feed_id']


def test_canonical_identity_is_independent_of_dictionary_order():
    p = hfm()
    reversed_p = dict(reversed(list(p.items())))
    reversed_p['instrument'] = dict(reversed(list(p['instrument'].items())))
    assert identity(reversed_p)['feed_id'] == identity(p)['feed_id']


def test_feed_identity_never_claims_independent_verification():
    r = identity()
    assert r['independently_authenticated'] is False
    assert r['source_admitted'] is False
    assert r['market_data_writable'] is False
    assert r['trading_authorized'] is False
    assert r['final_action'] == 'HOLD'
    assert r['identity_claims']['timestamp_contract_unverified'] == (
        'MT5_PY_UNIX_FIELDS_UNVERIFIED'
    )


@pytest.mark.parametrize('epoch', [
    '', None, 123, True, 'a', '../invalid', ' SPACE',
    'LEGACY_FEED_UNKNOWN', 'UNKNOWN', 'HISTORICAL',
    'UNVERIFIED', 'A' * 70,
])
def test_invalid_capture_epoch_rejected(epoch):
    with pytest.raises(feeds.FeedIdentityError):
        identity(epoch=epoch)


def test_invalid_profile_cannot_generate_identity():
    p = hfm()
    p['source_admitted'] = True
    with pytest.raises(ValueError):
        identity(p)


@pytest.mark.parametrize('value', [
    'MT5', 'EURUSD', '', None, 42, True,
    'mt5_eurusd_v1_' + 'A' * 64,
    'mt5_eurusd_v1_' + '0' * 63,
    'mt5_eurusd_v1_' + '0' * 64 + 'x',
])
def test_invalid_feed_id_rejected(value):
    with pytest.raises(feeds.FeedIdentityError):
        feeds.require_feed_id(value)


def test_original_tick_fingerprint_untouched_and_still_deterministic():
    a = ticks.create_tick_fingerprint(**tick_input())
    b = ticks.create_tick_fingerprint(**tick_input())

    assert a == b
    assert len(a) == 64
    assert re.fullmatch('[0-9a-f]{64}', a)

    original = '|'.join([
        'EURUSD',
        str(1793520000112),
        '1.0891200000',
        '1.0892500000',
        '0.0000000000',
        '1.0000000000',
        '6',
        '0.0000000000',
    ])

    assert a == sha256(original.encode('utf-8')).hexdigest()


def test_version_two_is_different_to_legacy_for_same_tick():
    assert v2() != ticks.create_tick_fingerprint(**tick_input())


def test_version_two_is_stable_and_feed_separated():
    assert v2() == v2()
    assert v2() != v2(other())


def test_version_two_changes_when_actual_tick_values_change():
    assert v2() != v2(bid=1.08913)
    assert v2() != v2(time_msc=1793520000113)
    assert v2() != v2(flags=4)


def test_v2_does_not_need_mt5_library():
    assert 'import MetaTrader5' not in inspect.getsource(feeds)
    assert 'import MetaTrader5' not in inspect.getsource(ticks)


@pytest.mark.parametrize('value', [
    0, -1, True, 1.5, '123', None, 4102444801000,
])
def test_bad_tick_millisecond_timestamp_refused(value):
    with pytest.raises(feeds.FeedIdentityError):
        v2(time_msc=value)


@pytest.mark.parametrize('value', [
    None, -1, True, 1.2, '6', 65536,
])
def test_bad_tick_flags_refused(value):
    with pytest.raises(feeds.FeedIdentityError):
        v2(flags=value)


@pytest.mark.parametrize('field,value', [
    ('bid', 0),
    ('bid', -1),
    ('bid', float('inf')),
    ('bid', float('nan')),
    ('bid', True),
    ('ask', 0),
    ('ask', -1),
    ('last', -1),
    ('volume', -1),
    ('volume_real', -1),
    ('ask', 'not-a-number'),
    ('ask', None),
    ('bid', '1.08912000001'),
    ('bid', '1e30'),
])
def test_invalid_tick_numeric_payload_refused(field, value):
    with pytest.raises(feeds.FeedIdentityError):
        v2(**{field: value})


def test_crossed_bid_ask_refused():
    with pytest.raises(feeds.FeedIdentityError):
        v2(ask=1.00000)


def test_v2_invalid_symbol_and_feed_id_refused():
    for symbol in ('../EURUSD', '', 'EUR/USD'):
        with pytest.raises(feeds.FeedIdentityError):
            v2(symbol=symbol)

    with pytest.raises(feeds.FeedIdentityError):
        ticks.create_tick_fingerprint_v2(
            feed_id='FAKE',
            **tick_input(),
        )


def test_per_feed_m5_keys_are_stable_and_broker_specific():
    t = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)

    a = feeds.create_m5_candle_key_v2(
        feed_id=identity()['feed_id'],
        symbol='EURUSD',
        open_time_utc=t,
    )
    b = feeds.create_m5_candle_key_v2(
        feed_id=identity()['feed_id'],
        symbol='EURUSD',
        open_time_utc=t,
    )
    c = feeds.create_m5_candle_key_v2(
        feed_id=identity(other())['feed_id'],
        symbol='EURUSD.r',
        open_time_utc=t,
    )

    assert a == b
    assert a != c
    assert re.fullmatch('[0-9a-f]{64}', a)


@pytest.mark.parametrize('bad_time', [
    None,
    '2026-11-02T14:00:00Z',
    datetime(2026, 11, 2, 14, 0),
    datetime(
        2026, 11, 2, 17, 0,
        tzinfo=timezone(timedelta(hours=3)),
    ),
    datetime(2026, 11, 2, 14, 1, tzinfo=UTC),
    datetime(2026, 11, 2, 14, 0, 0, 1, tzinfo=UTC),
])
def test_bad_m5_start_rejected(bad_time):
    with pytest.raises(feeds.FeedIdentityError):
        feeds.create_m5_candle_key_v2(
            feed_id=identity()['feed_id'],
            symbol='EURUSD',
            open_time_utc=bad_time,
        )


def test_candle_key_refuses_bad_symbol():
    with pytest.raises(feeds.FeedIdentityError):
        feeds.create_m5_candle_key_v2(
            feed_id=identity()['feed_id'],
            symbol='EUR/USD',
            open_time_utc=datetime(
                2026, 11, 2, 14, 0, tzinfo=UTC
            ),
        )

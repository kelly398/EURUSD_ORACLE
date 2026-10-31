
"""Offline broker-profile tests for the existing MT5Provider (no terminal contact).

The MetaTrader5 module is faked on platforms where the Windows package is not
installed. Every interface call is patched to synthetic local values.
"""
import copy
import sys
import types
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch, Mock

import pytest

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = sys.modules.setdefault('MetaTrader5', types.ModuleType('MetaTrader5'))
    for number, name in enumerate(('TIMEFRAME_M1', 'TIMEFRAME_M5', 'TIMEFRAME_M15',
                                   'TIMEFRAME_M30', 'TIMEFRAME_H1', 'TIMEFRAME_H4',
                                   'TIMEFRAME_D1', 'COPY_TICKS_ALL'), start=1):
        setattr(mt5, name, number)

from backend.data_engine.mt5_provider import (
    MT5Provider, MT5BrokerProfileMismatch, MT5_INITIALIZE_TIMEOUT_MS,
)
from backend.runtime.stage7_universal_mt5_adapter import example_hfm_profile

UTC = timezone.utc
NOW = datetime(2026, 10, 31, 14, 0, tzinfo=UTC)


def other_profile():
    profile = copy.deepcopy(example_hfm_profile())
    profile.update(profile_id='OTHER_MT5_DEMO', broker_name='Another Broker Ltd',
                   server_name='AnotherBroker-MT5-Demo', account_mode='NETTING',
                   account_ref='OTHER_LOCAL_ALIAS')
    profile['instrument'].update(broker_symbol='EURUSD.r', point='0.00001',
                                 spread_mode='FIXED', trade_mode='FULL',
                                 calculation_mode='FOREX')
    return profile


def account(profile=None):
    profile = profile or example_hfm_profile()
    return SimpleNamespace(company=profile['broker_name'], server=profile['server_name'],
        trade_mode=0 if profile['environment'] == 'DEMO' else 2,
        margin_mode={'NETTING': 0, 'EXCHANGE': 1, 'HEDGING': 2}[profile['account_mode']],
        login=987654321)  # Never emitted by the provider.


def symbol_info(profile=None):
    profile = profile or example_hfm_profile()
    p = profile['instrument']
    return SimpleNamespace(digits=p['digits'], point=10 ** -p['digits'],
        trade_contract_size=float(p['contract_size']),
        currency_margin=p['margin_currency'], currency_profit=p['profit_currency'],
        spread_float=p['spread_mode'] == 'FLOATING', chart_mode=0,
        trade_mode={'DISABLED': 0, 'LONG_ONLY': 1, 'SHORT_ONLY': 2,
                    'CLOSE_ONLY': 3, 'FULL': 4}[p['trade_mode']],
        trade_calc_mode={'FOREX': 0, 'FOREX_NO_LEVERAGE': 1}[p['calculation_mode']])


def tick():
    sec = int(NOW.timestamp())
    return SimpleNamespace(time=sec, time_msc=sec * 1000, bid=1.08912, ask=1.08925)


def mock_mt5(profile=None, *, connected=True):
    profile = profile or example_hfm_profile()
    return (
        patch.object(mt5, 'terminal_info', return_value=SimpleNamespace(connected=connected), create=True),
        patch.object(mt5, 'account_info', return_value=account(profile), create=True),
        patch.object(mt5, 'symbol_info', return_value=symbol_info(profile), create=True),
        patch.object(mt5, 'symbol_info_tick', return_value=tick(), create=True),
    )


@pytest.fixture
def fake_hfm():
    patches = mock_mt5()
    started = [x.start() for x in patches]
    yield started
    for item in reversed(patches):
        item.stop()


@pytest.fixture
def fake_other():
    patches = mock_mt5(other_profile())
    started = [x.start() for x in patches]
    yield started
    for item in reversed(patches):
        item.stop()


def test_hfm_profile_is_bound_to_an_existing_provider(fake_hfm):
    provider = MT5Provider(broker_profile=example_hfm_profile())
    provider.connected = True
    with patch('backend.data_engine.mt5_provider._utc_now', return_value=NOW):
        price = provider.get_price('EURUSD')
    assert price['bid'] == 1.08912
    assert price['time_msc'] == int(NOW.timestamp()) * 1000
    assert provider.is_healthy() is True


def test_other_mt5_broker_uses_exact_same_provider_class(fake_other):
    profile = other_profile()
    provider = MT5Provider(broker_profile=profile)
    provider.connected = True
    with patch('backend.data_engine.mt5_provider._utc_now', return_value=NOW):
        quote = provider.get_price('EURUSD.r')
    assert quote['symbol'] == 'EURUSD.r'
    assert quote['ask'] == 1.08925


def test_bound_provider_does_not_expose_account_id(fake_hfm):
    worker = MT5Provider(broker_profile=example_hfm_profile())
    worker.connected = True
    with patch('backend.data_engine.mt5_provider._utc_now', return_value=NOW):
        response = worker.get_price('EURUSD')
    assert '987654321' not in str(response)
    assert 'account_ref' not in response


def test_broker_profile_is_copied_on_construction(fake_hfm):
    profile = example_hfm_profile()
    worker = MT5Provider(broker_profile=profile)
    profile['server_name'] = 'CHANGED_AFTER_CONSTRUCTION'
    worker.connected = True
    assert worker.is_healthy() is True


def test_broker_specific_symbol_selection_refuses_other_pair(fake_hfm):
    worker = MT5Provider(broker_profile=example_hfm_profile())
    worker.connected = True
    with patch.object(mt5, 'symbol_info_tick', create=True) as read:
        with pytest.raises(MT5BrokerProfileMismatch, match='symbol'):
            worker.get_price('GBPUSD')
        read.assert_not_called()


def test_profile_and_legacy_hfm_clock_policy_must_not_mix():
    from backend.data_engine.test_clock_policy import policy
    with pytest.raises(ValueError, match='must not be combined'):
        MT5Provider(clock_policy=policy(), broker_profile=example_hfm_profile())


@pytest.mark.parametrize('change', [
    lambda p: p.update(trading_authorized=True),
    lambda p: p.update(source_admitted=True),
    lambda p: p.update(final_action='BUY'),
    lambda p: p.update(platform='MT4'),
    lambda p: p['instrument'].update(canonical_symbol='GBPUSD'),
])
def test_invalid_broker_profile_blocked_before_mt5_call(change):
    p = example_hfm_profile()
    change(p)
    with patch.object(mt5, 'initialize', create=True) as initialize:
        with pytest.raises(ValueError):
            MT5Provider(broker_profile=p)
        initialize.assert_not_called()


@pytest.mark.parametrize('attribute,value', [
    ('company', 'Other Company'), ('server', 'UNEXPECTED_SERVER'),
    ('trade_mode', 2), ('trade_mode', 1), ('trade_mode', True),
    ('margin_mode', 0), ('margin_mode', None),
])
def test_account_identity_or_mode_drift_blocks_quote(fake_hfm, attribute, value):
    current = account()
    setattr(current, attribute, value)
    fake_hfm[1].return_value = current
    worker = MT5Provider(broker_profile=example_hfm_profile())
    worker.connected = True
    with patch.object(mt5, 'symbol_info_tick', create=True) as read:
        with pytest.raises(MT5BrokerProfileMismatch):
            worker.get_price('EURUSD')
        read.assert_not_called()


@pytest.mark.parametrize('field,value', [
    ('digits', 4), ('point', 0.0001), ('point', 0),
    ('trade_contract_size', 200000), ('currency_margin', 'USD'),
    ('currency_profit', 'EUR'), ('spread_float', False),
    ('spread_float', 1), ('chart_mode', 1), ('trade_mode', 4),
    ('trade_calc_mode', 0), ('trade_calc_mode', 99),
])
def test_symbol_capability_drift_blocks_quote(fake_hfm, field, value):
    current = symbol_info()
    setattr(current, field, value)
    fake_hfm[2].return_value = current
    worker = MT5Provider(broker_profile=example_hfm_profile())
    worker.connected = True
    with patch.object(mt5, 'symbol_info_tick', create=True) as read:
        with pytest.raises(MT5BrokerProfileMismatch):
            worker.get_price('EURUSD')
        read.assert_not_called()


def test_unavailable_account_or_symbol_metadata_fails_closed(fake_hfm):
    worker = MT5Provider(broker_profile=example_hfm_profile())
    worker.connected = True
    for target in (fake_hfm[1], fake_hfm[2]):
        value = target.return_value
        target.return_value = None
        with pytest.raises(MT5BrokerProfileMismatch):
            worker.get_price('EURUSD')
        target.return_value = value


def test_connection_refuses_wrong_server_and_shuts_down():
    profile = example_hfm_profile()
    with (patch.object(mt5, 'shutdown', create=True) as shutdown,
          patch.object(mt5, 'initialize', return_value=True, create=True) as initialize,
          patch.object(mt5, 'terminal_info', return_value=SimpleNamespace(connected=True), create=True),
          patch.object(mt5, 'account_info', return_value=SimpleNamespace(**{**vars(account()), 'server':'WRONG'}), create=True),
          patch.object(mt5, 'symbol_info', return_value=symbol_info(), create=True),
          patch.dict('os.environ', {'MT5_TERMINAL_PATH': ''})):
        worker = MT5Provider(broker_profile=profile)
        with pytest.raises(MT5BrokerProfileMismatch):
            worker.connect()
        initialize.assert_called_once_with(timeout=MT5_INITIALIZE_TIMEOUT_MS)
        assert shutdown.call_count >= 2
        assert worker.connected is False


def test_account_switch_after_initial_read_blocks_next_read(fake_hfm):
    provider = MT5Provider(broker_profile=example_hfm_profile())
    provider.connected = True
    with patch('backend.data_engine.mt5_provider._utc_now', return_value=NOW):
        assert provider.get_price('EURUSD')
    fake_hfm[1].return_value = account(other_profile())
    with patch.object(mt5, 'copy_rates_range', create=True) as read:
        with pytest.raises(MT5BrokerProfileMismatch):
            provider.get_candles('EURUSD', 'M5', NOW-timedelta(minutes=10), NOW)
        read.assert_not_called()


def test_profile_bound_history_refuses_other_symbol_before_clock_query(fake_hfm):
    provider = MT5Provider(broker_profile=example_hfm_profile())
    provider.connected = True
    with patch.object(mt5, 'copy_ticks_range', create=True) as read:
        with pytest.raises(MT5BrokerProfileMismatch):
            provider.get_ticks_range('EURUSD.r', NOW-timedelta(minutes=5), NOW)
        read.assert_not_called()


def test_valid_bound_candle_history_keeps_utc_and_no_clock_shift(fake_hfm):
    provider = MT5Provider(broker_profile=example_hfm_profile())
    provider.connected = True
    fake = [{'time': int((NOW - timedelta(minutes=5)).timestamp())}]
    with (patch('backend.data_engine.mt5_provider._utc_now', return_value=NOW),
          patch.object(mt5, 'copy_rates_range', return_value=fake, create=True) as read):
        result = provider.get_candles('EURUSD', 'M5', NOW-timedelta(minutes=10), NOW)
    assert result == fake
    assert read.call_args.args[2:4] == (NOW - timedelta(minutes=10), NOW)


def test_valid_bound_tick_history_keeps_utc_and_no_clock_shift(fake_hfm):
    provider = MT5Provider(broker_profile=example_hfm_profile())
    provider.connected = True
    fake = [{'time_msc': int((NOW - timedelta(seconds=1)).timestamp()*1000)}]
    with (patch('backend.data_engine.mt5_provider._utc_now', return_value=NOW),
          patch.object(mt5, 'copy_ticks_range', return_value=fake, create=True) as read):
        result = provider.get_ticks_range('EURUSD', NOW-timedelta(minutes=5), NOW)
    assert result == fake
    assert read.call_args.args[1:3] == (NOW-timedelta(minutes=5), NOW)


def test_legacy_provider_signature_and_state_unchanged():
    legacy = MT5Provider()
    assert legacy.clock_policy is None
    assert legacy.broker_profile is None
    assert legacy.connected is False


def test_no_trade_operations_ever_imported_or_called(fake_hfm):
    provider = MT5Provider(broker_profile=example_hfm_profile())
    provider.connected = True
    with (patch('backend.data_engine.mt5_provider._utc_now', return_value=NOW),
          patch.object(mt5, 'order_send', create=True) as order):
        assert provider.get_price('EURUSD')
        order.assert_not_called()

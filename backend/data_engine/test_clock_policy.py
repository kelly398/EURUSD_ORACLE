"""Broker clock correctness, domain isolation and seasonal boundary tests."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError
from backend.data_engine.clock_policy import (ClockPolicy,ClockPolicyError,
    hfm_utc_to_wall,hfm_wall_to_utc_msc,utc_msc,implementation_sha256,decode_json)
from backend.data_engine import mt5_provider as provider_module
from backend.data_engine.mt5_provider import MT5Provider,MT5TimestampIntegrityError

NOW=datetime(2026,10,27,14,0,tzinfo=timezone.utc)
ACCOUNT=SimpleNamespace(company='HFM Investments Ltd',server='SYNTHETIC_TEST_SERVER')


def policy(**changes):
    values=dict(company_sha256=sha256(ACCOUNT.company.encode()).hexdigest(),
        server_sha256=sha256(ACCOUNT.server.encode()).hexdigest(),evidence_sha256='a'*64,
        implementation_sha256=implementation_sha256(),valid_from=NOW-timedelta(hours=1),
        valid_until=NOW+timedelta(days=1))
    return ClockPolicy.model_validate({**values,**changes})


def raw_tick(at=NOW,**changes):
    ms=utc_msc(hfm_utc_to_wall(at))
    return dict(time=ms//1000,time_msc=ms,bid=1.1,ask=1.1001,last=0.,
                volume=0,flags=6,volume_real=0.,**changes)


def raw_bar(at=NOW-timedelta(minutes=5)):
    return dict(time=utc_msc(hfm_utc_to_wall(at))//1000,open=1.1,high=1.101,low=1.099,
                close=1.1005,tick_volume=100,spread=10,real_volume=0)


class ConversionTests(unittest.TestCase):
    def test_winter_summer_and_year_boundaries_round_trip_exact_milliseconds(self):
        for text,hours in [('2026-01-14T12:00:00+00:00',2),('2026-07-14T12:00:00+00:00',3),
            ('2025-10-24T12:00:00+00:00',3),('2025-10-27T12:00:00+00:00',2),
            ('2026-03-27T12:00:00+00:00',2),('2026-03-30T12:00:00+00:00',3),
            ('2026-12-31T23:30:00+00:00',2)]:
            stamp=datetime.fromisoformat(text).replace(microsecond=123000)
            with self.subTest(text=text):
                raw=hfm_utc_to_wall(stamp)
                self.assertEqual(raw-stamp,timedelta(hours=hours))
                self.assertEqual(hfm_wall_to_utc_msc(utc_msc(raw)),utc_msc(stamp))

    def test_ambiguous_nonexistent_and_undocumented_transition_labels_fail(self):
        for day in (datetime(2026,3,29,3,30,tzinfo=timezone.utc),datetime(2026,10,25,3,30,tzinfo=timezone.utc)):
            with self.subTest(day=day),self.assertRaises(ClockPolicyError):
                hfm_wall_to_utc_msc(utc_msc(day))

    def test_conversion_is_independent_of_host_current_timezone(self):
        at=NOW.astimezone(timezone(timedelta(hours=-5)))
        self.assertEqual(hfm_utc_to_wall(at),hfm_utc_to_wall(NOW))

    def test_raw_timestamp_types_are_not_silently_truncated(self):
        for value in (True,1.5,'1790000000000',0,-1,10**30):
            with self.subTest(value=value),self.assertRaises(ClockPolicyError):
                hfm_wall_to_utc_msc(value)

    def test_source_clock_policy_is_bound_to_identity_and_code(self):
        lease=policy()
        lease.check_identity(ACCOUNT)
        lease.check_active(NOW)
        for account in (None,SimpleNamespace(company=ACCOUNT.company,server='OTHER')):
            with self.assertRaises(ClockPolicyError):lease.check_identity(account)
        with self.assertRaises(ClockPolicyError):policy(implementation_sha256='b'*64).check_active(NOW)

    def test_lease_expiry_future_start_and_historical_requests_fail(self):
        lease=policy()
        for stamp in (lease.valid_from-timedelta(microseconds=1),lease.valid_until):
            with self.subTest(stamp=stamp),self.assertRaises(ClockPolicyError):lease.check_active(stamp)
        with self.assertRaises(ClockPolicyError):lease.query_range(lease.valid_from-timedelta(minutes=1),NOW)
        with self.assertRaises(ValidationError):policy(valid_until=NOW+timedelta(days=31))
        with self.assertRaises(ValidationError):policy(historical_admission=True)

    def test_query_clock_is_shifted_for_the_requested_season(self):
        lease=policy()
        start,end=lease.query_range(NOW-timedelta(seconds=10),NOW)
        self.assertEqual((start,end),(NOW-timedelta(seconds=10)+timedelta(hours=2),NOW+timedelta(hours=2)))

    def test_requests_across_seasonal_cutover_are_refused(self):
        lease=policy(valid_from=datetime(2026,3,28,tzinfo=timezone.utc),
                     valid_until=datetime(2026,3,31,tzinfo=timezone.utc))
        with self.assertRaises(ClockPolicyError):
            lease.query_range(datetime(2026,3,28,20,tzinfo=timezone.utc),datetime(2026,3,29,10,tzinfo=timezone.utc))

    def test_tick_raw_source_is_preserved_and_double_conversion_rejected(self):
        lease=policy();raw=raw_tick();normalized=lease.tick(raw)
        self.assertEqual(normalized['time_msc'],utc_msc(NOW))
        self.assertEqual(normalized['source_record'],raw)
        self.assertEqual(raw['time_msc']-normalized['time_msc'],7200000)
        with self.assertRaises(ClockPolicyError):lease.tick(normalized)

    def test_invalid_ticks_and_bars_fail_before_storage(self):
        for changes in ({'ask':1.},{'bid':float('nan')},{'time_msc':utc_msc(hfm_utc_to_wall(NOW))+1000}, {'volume':-1}):
            with self.subTest(changes=changes),self.assertRaises(ClockPolicyError):
                policy().tick({**raw_tick(),**changes})
        for changes in ({'high':1.},{'low':0},{'spread':-1}):
            with self.subTest(changes=changes),self.assertRaises(ClockPolicyError):
                policy().rate({**raw_bar(),**changes})

    def test_duplicate_nonfinite_or_unbounded_json_is_rejected(self):
        for raw in (b'{"a":1,"a":2}',b'{"a":NaN}',b'[]',b' '*16385):
            with self.subTest(raw=raw[:30]),self.assertRaises(ClockPolicyError):decode_json(raw,limit=16384)


class ProviderPolicyTests(unittest.TestCase):
    def setUp(self):
        self.provider=MT5Provider(clock_policy=policy());self.provider.connected=True
        self.patches=[patch.object(provider_module,'_utc_now',return_value=NOW),
            patch.object(provider_module.mt5,'account_info',return_value=ACCOUNT,create=True),
            patch.object(provider_module.mt5,'symbol_info_tick',return_value=SimpleNamespace(**raw_tick()),create=True)]
        for item in self.patches:item.start();self.addCleanup(item.stop)

    def test_current_quote_is_utc_with_original_fields_retained(self):
        value=self.provider.get_price('EURUSD')
        self.assertEqual(value['time_msc'],utc_msc(NOW))
        self.assertEqual(value['source_record']['time_msc'],utc_msc(hfm_utc_to_wall(NOW)))

    def test_identity_mismatch_prevents_any_history_call(self):
        with patch.object(provider_module.mt5,'account_info',return_value=None),patch.object(provider_module.mt5,'copy_ticks_range') as read:
            with self.assertRaises(ClockPolicyError):self.provider.get_ticks_range('EURUSD',NOW-timedelta(seconds=10),NOW)
            read.assert_not_called()

    def test_shifted_parameters_and_normalized_history(self):
        raw=raw_tick(NOW-timedelta(seconds=5))
        with patch.object(provider_module.mt5,'copy_ticks_range',return_value=[raw]) as read:
            result=self.provider.get_ticks_range('EURUSD',NOW-timedelta(seconds=10),NOW)
        self.assertEqual(result[0]['time_msc'],utc_msc(NOW-timedelta(seconds=5)))
        self.assertEqual(read.call_args.args[1:3],(hfm_utc_to_wall(NOW-timedelta(seconds=10)),hfm_utc_to_wall(NOW)))

    def test_inclusive_endpoint_is_filtered_and_wrong_domain_quarantined(self):
        with patch.object(provider_module.mt5,'copy_ticks_range',return_value=[raw_tick()]):
            self.assertEqual(self.provider.get_ticks_range('EURUSD',NOW-timedelta(seconds=10),NOW),[])
        with patch.object(provider_module.mt5,'copy_ticks_range',return_value=[raw_tick(NOW-timedelta(hours=2))]):
            with self.assertRaises(ClockPolicyError):self.provider.get_ticks_range('EURUSD',NOW-timedelta(seconds=10),NOW)

    def test_converted_future_or_stale_quote_still_blocks(self):
        for seconds in (10,-181):
            with self.subTest(seconds=seconds),patch.object(provider_module.mt5,'symbol_info_tick',return_value=SimpleNamespace(**raw_tick(NOW+timedelta(seconds=seconds)))):
                with self.assertRaises(ClockPolicyError):self.provider.get_price('EURUSD')

    def test_api_error_is_distinct_from_empty_history(self):
        with patch.object(provider_module.mt5,'copy_ticks_range',return_value=None):
            with self.assertRaises(ClockPolicyError):self.provider.get_ticks_range('EURUSD',NOW-timedelta(seconds=10),NOW)

    def test_legacy_ingestors_refuse_clock_normalized_provider(self):
        from backend.data_engine.mt5_tick_ingestor import MT5TickIngestor
        from backend.candle_engine.mt5_bar_ingestor import MT5BarIngestor
        for klass in (MT5TickIngestor,MT5BarIngestor):
            worker=klass.__new__(klass);worker.provider=self.provider;worker.storage=Mock()
            with self.subTest(worker=klass),self.assertRaises(RuntimeError):worker.ingest_once()
            worker.storage.save_batch.assert_not_called()


if __name__=='__main__':unittest.main()

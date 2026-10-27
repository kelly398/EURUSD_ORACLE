"""Actual isolated transactions for UTC epochs and preserved raw source data."""
from datetime import timedelta
import json
import unittest
from unittest.mock import Mock
from sqlalchemy import create_engine,select,func,event

from backend.data_engine.clock_policy import ClockPolicyError,instant
from backend.data_engine.forward_store import ForwardFeedStore,epochs,captures,ticks,bars
from backend.data_engine.test_clock_policy import NOW,policy,raw_tick,raw_bar
from backend.runtime.capture_forward_feed import ForwardFeedRecorder


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        self.now=NOW
        self.store=ForwardFeedStore(self.engine,clock=lambda:self.now)
        self.store.initialize();self.policy=policy()
        self.kwargs=dict(tick_rows=[self.policy.tick(raw_tick(NOW-timedelta(seconds=2)))],
            bar_rows=[self.policy.rate(raw_bar())],received_at=NOW,
            tick_start=NOW-timedelta(minutes=1),tick_end=NOW,
            bar_start=NOW-timedelta(minutes=10),bar_end=NOW)

    def count(self,table):
        with self.engine.connect() as connection:return connection.execute(select(func.count()).select_from(table)).scalar_one()

    def test_normalized_and_original_time_are_both_durable(self):
        result=self.store.append(self.policy,**self.kwargs)
        self.assertEqual((result['ticks_inserted'],result['bars_inserted']),(1,1))
        with self.engine.connect() as connection:
            row=connection.execute(select(ticks)).mappings().one()
            self.assertEqual(row['raw_time_msc']-int(row['timestamp'].timestamp()*1000),7200000)
            self.assertEqual(row['ingested_at'],NOW)
            self.assertEqual(json.loads(row['raw_json'])['time_msc'],row['raw_time_msc'])

    def test_duplicate_retry_retains_first_receipt_and_no_duplicate_ticks(self):
        self.store.append(self.policy,**self.kwargs)
        self.now+=timedelta(seconds=10)
        result=self.store.append(self.policy,**self.kwargs)
        self.assertEqual((result['ticks_inserted'],result['bars_inserted']),(0,0))
        with self.engine.connect() as connection:self.assertEqual(connection.execute(select(ticks.c.ingested_at)).scalar_one(),NOW)
        self.assertEqual(self.count(captures),1)

    def test_future_receipt_or_open_bar_cannot_be_persisted(self):
        cases=[dict(received_at=NOW+timedelta(seconds=1)),
               dict(bar_rows=[self.policy.rate(raw_bar(NOW))],bar_end=NOW+timedelta(minutes=5))]
        for changes in cases:
            with self.subTest(changes=changes),self.assertRaises(ClockPolicyError):
                self.store.append(self.policy,**{**self.kwargs,**changes})
        self.assertEqual(self.count(ticks),0)

    def test_altered_normalized_value_and_outside_epoch_fail(self):
        row={**self.kwargs['tick_rows'][0],'bid':1.05}
        with self.assertRaises(ClockPolicyError):self.store.append(self.policy,**{**self.kwargs,'tick_rows':[row]})
        with self.assertRaises(ClockPolicyError):self.store.append(self.policy,**{**self.kwargs,'tick_start':self.policy.valid_from-timedelta(seconds=1)})
        self.assertEqual(self.count(epochs),0)

    def test_closed_bar_revision_is_a_conflict_not_an_overwrite(self):
        self.store.append(self.policy,**self.kwargs)
        changed={**raw_bar(),'close':1.1006}
        with self.assertRaises(ClockPolicyError):
            self.store.append(self.policy,**{**self.kwargs,'bar_rows':[self.policy.rate(changed)]})
        with self.engine.connect() as connection:self.assertEqual(connection.execute(select(bars.c.close)).scalar_one(),1.1005)

    def test_conflicting_duplicate_bars_in_one_batch_fail_atomically(self):
        changed=self.policy.rate({**raw_bar(),'close':1.1006})
        with self.assertRaises(ClockPolicyError):
            self.store.append(self.policy,**{**self.kwargs,'bar_rows':[*self.kwargs['bar_rows'],changed]})
        self.assertEqual([self.count(t) for t in (epochs,captures,ticks,bars)],[0,0,0,0])

    def test_storage_failure_rolls_back_the_whole_capture(self):
        def fail(connection,cursor,statement,parameters,context,executemany):
            if statement.startswith('INSERT INTO oracle_utc_bars'):raise RuntimeError('Injected storage failure')
        event.listen(self.engine,'before_cursor_execute',fail)
        try:
            with self.assertRaises(RuntimeError):self.store.append(self.policy,**self.kwargs)
        finally:event.remove(self.engine,'before_cursor_execute',fail)
        self.assertEqual([self.count(t) for t in (epochs,captures,ticks,bars)],[0,0,0,0])

    def test_recorder_uses_utc_clock_not_fast_broker_clock_for_bar_completion(self):
        provider=Mock();provider.clock_policy=self.policy
        provider.get_price.return_value={'time_msc':int((NOW+timedelta(seconds=4)).timestamp()*1000)}
        provider.get_ticks_range.return_value=self.kwargs['tick_rows']
        provider.get_candles.return_value=self.kwargs['bar_rows']
        result=ForwardFeedRecorder(provider,self.store,clock=lambda:NOW).poll()
        self.assertEqual(result['status'],'captured')
        self.assertEqual(provider.get_candles.call_args.args[-1],NOW)

    def test_a_gap_is_recovered_in_bounded_windows_without_skipping_ahead(self):
        self.store.append(self.policy,**self.kwargs)
        self.now+=timedelta(hours=1)
        provider=Mock();provider.clock_policy=self.policy
        provider.get_ticks_range.return_value=[];provider.get_candles.return_value=[]
        ForwardFeedRecorder(provider,self.store,clock=lambda:self.now).poll()
        start,end=provider.get_ticks_range.call_args.args[1:]
        self.assertEqual(start,NOW-timedelta(seconds=185))
        self.assertEqual(end-start,timedelta(minutes=15))

    def test_empty_recovery_intervals_advance_a_durable_cursor(self):
        self.store.append(self.policy,**{**self.kwargs,'tick_rows':[],'bar_rows':[]})
        self.now+=timedelta(hours=1)
        provider=Mock();provider.clock_policy=self.policy
        provider.get_ticks_range.return_value=[];provider.get_candles.return_value=[]
        recorder=ForwardFeedRecorder(provider,self.store,clock=lambda:self.now)
        recorder.poll()
        previous_end=provider.get_ticks_range.call_args.args[-1]
        reopened=ForwardFeedStore(self.engine,clock=lambda:self.now)
        self.assertEqual(reopened.resume_cursor(self.policy),previous_end)
        ForwardFeedRecorder(provider,reopened,clock=lambda:self.now).poll()
        start,end=provider.get_ticks_range.call_args.args[1:]
        self.assertLessEqual(start,previous_end)
        self.assertGreater(end,previous_end)

    def test_a_cursor_cannot_skip_an_unqueried_gap(self):
        self.store.append(self.policy,**self.kwargs)
        self.now+=timedelta(minutes=10)
        with self.assertRaises(ClockPolicyError):
            self.store.append(self.policy,**{**self.kwargs,'tick_rows':[],'bar_rows':[],
                'tick_start':NOW+timedelta(minutes=1),'tick_end':self.now,'received_at':self.now})

    def test_health_is_causal_and_requires_current_closed_bar_and_tick(self):
        self.store.append(self.policy,**self.kwargs)
        self.assertEqual(self.store.status(self.policy,as_of=NOW)['status'],'pass')
        self.assertEqual(self.store.status(self.policy,as_of=NOW-timedelta(seconds=1))['status'],'blocked')
        self.now+=timedelta(minutes=3)
        self.assertIn('UTC_TICKS_STALE',self.store.status(self.policy,as_of=self.now)['blockers'])
        with self.assertRaises(ClockPolicyError):self.store.status(self.policy,as_of=self.now+timedelta(seconds=1))

    def test_raw_time_column_tampering_is_detected(self):
        self.store.append(self.policy,**self.kwargs)
        with self.engine.begin() as connection:
            connection.execute(ticks.update().values(raw_time_msc=1))
        with self.assertRaises(ClockPolicyError):self.store.status(self.policy,as_of=NOW)


if __name__=='__main__':unittest.main()

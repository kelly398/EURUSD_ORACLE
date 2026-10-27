"""Bounded forward UTC capture into separate tables; no Council or broker orders."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
import signal

from sqlalchemy.exc import SQLAlchemyError

from backend.data_engine.clock_policy import ClockPolicy, ClockPolicyError, aware_utc
from backend.data_engine.forward_store import ForwardFeedStore
from backend.data_engine.mt5_provider import (MT5Provider,MAX_QUOTE_AGE_SECONDS,
    MAX_FUTURE_SECONDS,MT5TimestampIntegrityError)
from backend.runtime.process_lock import SingleInstanceLock


def floor_m5(value):
    return value.replace(minute=value.minute//5*5,second=0,microsecond=0)


class ForwardFeedRecorder:
    def __init__(self, provider, store, *, clock=None):
        if provider.clock_policy is None:
            raise ClockPolicyError('Forward capture requires an explicit clock policy.')
        self.provider, self.store = provider, store
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def poll(self):
        policy = self.provider.clock_policy
        # Check current server identity and converted UTC freshness before history.
        self.provider.get_price('EURUSD')
        now = aware_utc(self.clock())
        policy.check_active(now)
        cursor = self.store.resume_cursor(policy)
        overlap = timedelta(seconds=MAX_QUOTE_AGE_SECONDS+MAX_FUTURE_SECONDS)
        start = max(policy.valid_from, cursor-overlap if cursor else now-timedelta(minutes=5))
        end = min(now, start+timedelta(minutes=15))
        if start >= end:
            return dict(status='no_interval',epoch_id=policy.epoch_id)
        tick_rows = self.provider.get_ticks_range('EURUSD',start,end)
        boundary = floor_m5(end)
        first_bar = floor_m5(policy.valid_from)
        if first_bar < policy.valid_from:
            first_bar += timedelta(minutes=5)
        bar_start = max(first_bar,boundary-timedelta(hours=1))
        bar_end = boundary
        bar_rows=[]
        if bar_start < bar_end:
            bar_rows = self.provider.get_candles('EURUSD','M5',bar_start,bar_end)
        else:
            bar_start = bar_end = policy.valid_from
        received_at=aware_utc(self.clock())
        result=self.store.append(policy,tick_rows=tick_rows,bar_rows=bar_rows,
            received_at=received_at,tick_start=start,tick_end=end,bar_start=bar_start,bar_end=bar_end)
        return dict(status='captured',received_at=received_at.isoformat(),**result)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clock-policy',type=Path,required=True)
    duration=parser.add_mutually_exclusive_group()
    duration.add_argument('--run-seconds',type=int,default=60,choices=range(1,3601))
    duration.add_argument('--continuous',action='store_true',help='Run until the owning supervisor stops this worker.')
    parser.add_argument('--initialize',action='store_true',help='Install only the new forward feed tables.')
    args=parser.parse_args()
    def request_shutdown(signum,frame):
        raise KeyboardInterrupt
    for name in ('SIGBREAK','SIGTERM'):
        if hasattr(signal,name):
            signal.signal(getattr(signal,name),request_shutdown)
    from backend.database.connection import engine
    policy=ClockPolicy.load(args.clock_policy)
    lock=SingleInstanceLock('utc_forward_feed_'+policy.server_sha256[:16])
    if not lock.acquire():
        parser.exit(2,'A forward recorder for this broker is already active.\n')
    provider=MT5Provider(clock_policy=policy)
    try:
        store=ForwardFeedStore(engine)
        if args.initialize:
            store.initialize()
        if not provider.connect():
            parser.exit(2,'MT5 connection unavailable; no forward data captured.\n')
        recorder=ForwardFeedRecorder(provider,store)
        deadline=None if args.continuous else time.monotonic()+args.run_seconds
        while True:
            print(json.dumps(recorder.poll(),allow_nan=False),flush=True)
            if deadline is not None:
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    break
                time.sleep(min(5,remaining))
            else:
                time.sleep(5)
        print(json.dumps(store.status(policy,as_of=datetime.now(timezone.utc))),flush=True)
    except KeyboardInterrupt:
        print('Forward capture stopped.',flush=True)
    except (ClockPolicyError,MT5TimestampIntegrityError) as error:
        print(json.dumps(dict(status='blocked',reason=str(error),execution_authorized=False)),flush=True)
        return 2
    except SQLAlchemyError as error:
        print(json.dumps(dict(status='blocked',error_type=type(error).__name__,execution_authorized=False)),flush=True)
        return 2
    finally:
        provider.disconnect()
        lock.release()


if __name__=='__main__':
    raise SystemExit(main())

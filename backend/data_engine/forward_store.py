"""Separate, forward-only UTC feed epochs. Never writes legacy market tables."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from sqlalchemy import (MetaData, Table, Column, String, Integer, BigInteger,
                        Text, Float, ForeignKey, Index, select, inspect)
from backend.database.utc_type import UTCInstant
from backend.data_engine.clock_policy import ClockPolicy, ClockPolicyError, instant, aware_utc


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def hashed(value):
    return sha256(encoded(value).encode()).hexdigest()


metadata = MetaData()
schema = Table('oracle_utc_feed_schema', metadata, Column('version', Integer, primary_key=True))
epochs = Table('oracle_utc_feed_epochs', metadata,
    Column('epoch_id', String(64), primary_key=True), Column('policy_json', Text, nullable=False),
    Column('valid_from', UTCInstant(), nullable=False), Column('valid_until', UTCInstant(), nullable=False),
    Column('recorded_at', UTCInstant(), nullable=False))
captures = Table('oracle_utc_feed_captures', metadata,
    Column('capture_id', String(64), primary_key=True),
    Column('epoch_id', String(64), ForeignKey(epochs.c.epoch_id), nullable=False),
    Column('received_at', UTCInstant(), nullable=False),
    Column('ingested_at', UTCInstant(), nullable=False),
    Column('tick_start', UTCInstant(), nullable=False), Column('tick_end', UTCInstant(), nullable=False),
    Column('manifest_json', Text, nullable=False))
ticks = Table('oracle_utc_ticks', metadata,
    Column('fingerprint', String(64), primary_key=True),
    Column('epoch_id', String(64), ForeignKey(epochs.c.epoch_id), nullable=False),
    Column('first_capture_id', String(64), ForeignKey(captures.c.capture_id), nullable=False),
    Column('timestamp', UTCInstant(), nullable=False), Column('raw_time_msc', BigInteger, nullable=False),
    Column('received_at', UTCInstant(), nullable=False),
    Column('ingested_at', UTCInstant(), nullable=False),
    Column('bid', Float, nullable=False), Column('ask', Float, nullable=False),
    Column('raw_json', Text, nullable=False))
bars = Table('oracle_utc_bars', metadata,
    Column('bar_id', String(64), primary_key=True),
    Column('epoch_id', String(64), ForeignKey(epochs.c.epoch_id), nullable=False),
    Column('first_capture_id', String(64), ForeignKey(captures.c.capture_id), nullable=False),
    Column('timestamp', UTCInstant(), nullable=False), Column('raw_open_time', BigInteger, nullable=False),
    Column('received_at', UTCInstant(), nullable=False),
    Column('ingested_at', UTCInstant(), nullable=False),
    Column('open', Float, nullable=False), Column('high', Float, nullable=False),
    Column('low', Float, nullable=False), Column('close', Float, nullable=False),
    Column('raw_json', Text, nullable=False))
Index('ix_oracle_utc_ticks_epoch_time', ticks.c.epoch_id, ticks.c.timestamp, ticks.c.raw_time_msc)
Index('ix_oracle_utc_bars_epoch_time', bars.c.epoch_id, bars.c.timestamp)
Index('ix_oracle_utc_captures_cursor', captures.c.epoch_id,captures.c.tick_end)


class ForwardFeedStore:
    def __init__(self, engine, *, clock=None):
        if engine.dialect.name not in ('sqlite','postgresql'):
            raise ValueError('Unsupported forward feed database.')
        self.engine = engine
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def initialize(self):
        with self.engine.begin() as connection:
            existing = set(inspect(connection).get_table_names()) & set(metadata.tables)
            if existing:
                if existing != set(metadata.tables):
                    raise ClockPolicyError('Partial forward feed schema; migration required.')
                if tuple(connection.execute(select(schema.c.version)).scalars()) != (1,):
                    raise ClockPolicyError('Unsupported forward feed schema version.')
                for table in metadata.sorted_tables:
                    actual = {c['name'] for c in inspect(connection).get_columns(table.name)}
                    if actual != set(table.c.keys()):
                        raise ClockPolicyError('Forward feed schema differs from its contract.')
                    if set(inspect(connection).get_pk_constraint(table.name)['constrained_columns']) != {c.name for c in table.primary_key}:
                        raise ClockPolicyError('Forward feed primary key mismatch.')
                return
            metadata.create_all(connection)
            connection.execute(schema.insert().values(version=1))

    def _insert_once(self, connection, table, rows):
        if not rows:
            return 0
        if self.engine.dialect.name == 'postgresql':
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        count = 0
        for start in range(0, len(rows), 1000):
            result = connection.execute(insert(table).values(rows[start:start+1000]).on_conflict_do_nothing())
            count += max(0, result.rowcount or 0)
        return count

    def append(self, policy, *, tick_rows, bar_rows, received_at, tick_start, tick_end,
               bar_start, bar_end):
        policy = ClockPolicy.model_validate(policy.model_dump())
        now = aware_utc(self.clock())
        received_at = aware_utc(received_at)
        tick_start, tick_end = aware_utc(tick_start), aware_utc(tick_end)
        bar_start, bar_end = aware_utc(bar_start), aware_utc(bar_end)
        if received_at > now or tick_end > received_at or bar_end > received_at:
            raise ClockPolicyError('Capture receipt/query boundary is in the future.')
        policy.check_active(received_at)
        policy.check_active(now)
        if not policy.valid_from <= tick_start < tick_end <= policy.valid_until:
            raise ClockPolicyError('Tick capture falls outside its epoch.')
        if not policy.valid_from <= bar_start <= bar_end <= policy.valid_until:
            raise ClockPolicyError('Bar capture falls outside its epoch.')
        normalized_ticks, normalized_bars = [], []
        for row in tick_rows:
            expected = policy.tick(row['source_record'])
            if expected != row:
                raise ClockPolicyError('Normalized tick differs from its raw source fields.')
            stamp = instant(row['time_msc'])
            if not tick_start <= stamp < tick_end:
                raise ClockPolicyError('Tick outside captured UTC interval.')
            normalized_ticks.append(row)
        for row in bar_rows:
            expected = policy.rate(row['source_record'])
            if expected != row:
                raise ClockPolicyError('Normalized bar differs from its raw source fields.')
            stamp = instant(row['time']*1000)
            if stamp.second or stamp.microsecond or stamp.minute % 5:
                raise ClockPolicyError('Bar is not aligned to M5.')
            if not bar_start <= stamp < bar_end or stamp+timedelta(minutes=5) > received_at:
                raise ClockPolicyError('Bar is outside the request or not complete at receipt.')
            normalized_bars.append(row)
        manifest = dict(schema_version=1, epoch_id=policy.epoch_id,
            received_at=received_at.isoformat(), tick_start=tick_start.isoformat(), tick_end=tick_end.isoformat(),
            bar_start=bar_start.isoformat(), bar_end=bar_end.isoformat(),
            ticks_sha256=hashed(normalized_ticks), bars_sha256=hashed(normalized_bars),
            tick_count=len(normalized_ticks), bar_count=len(normalized_bars))
        capture_id = hashed(manifest)
        with self.engine.begin() as connection:
            self._insert_once(connection, epochs, [dict(epoch_id=policy.epoch_id,
                policy_json=encoded(policy.model_dump(mode='json')), valid_from=policy.valid_from,
                valid_until=policy.valid_until, recorded_at=now)])
            saved = connection.execute(select(epochs.c.policy_json).where(epochs.c.epoch_id==policy.epoch_id)).scalar_one()
            if saved != encoded(policy.model_dump(mode='json')):
                raise ClockPolicyError('Stored epoch policy has changed.')
            previous_end = connection.execute(select(captures.c.tick_end).where(captures.c.epoch_id==policy.epoch_id)
                .order_by(captures.c.tick_end.desc()).limit(1)).scalar_one_or_none()
            if previous_end is not None and tick_start > previous_end:
                raise ClockPolicyError('Capture would skip an unqueried tick interval.')
            self._insert_once(connection, captures, [dict(capture_id=capture_id,
                epoch_id=policy.epoch_id, received_at=received_at, ingested_at=now,
                tick_start=tick_start,tick_end=tick_end,
                manifest_json=encoded(manifest))])
            tick_values = [dict(fingerprint=hashed(dict(epoch_id=policy.epoch_id, raw=r['source_record'])),
                epoch_id=policy.epoch_id, first_capture_id=capture_id,
                timestamp=instant(r['time_msc']), raw_time_msc=r['source_record']['time_msc'],
                received_at=received_at, ingested_at=now, bid=r['bid'], ask=r['ask'], raw_json=encoded(r['source_record']))
                for r in normalized_ticks]
            bar_values = [dict(bar_id=hashed(dict(epoch_id=policy.epoch_id,time=r['time'])),
                epoch_id=policy.epoch_id, first_capture_id=capture_id,
                timestamp=instant(r['time']*1000), raw_open_time=r['source_record']['time'],
                received_at=received_at, ingested_at=now, **{k:r[k] for k in ('open','high','low','close')},
                raw_json=encoded(r['source_record'])) for r in normalized_bars]
            # A historical bar revision cannot silently replace an earlier close.
            if bar_values:
                existing_bars = dict(connection.execute(select(bars.c.bar_id,bars.c.raw_json)
                    .where(bars.c.bar_id.in_([r['bar_id'] for r in bar_values]))).all())
                for row in bar_values:
                    if row['bar_id'] in existing_bars and existing_bars[row['bar_id']] != row['raw_json']:
                        raise ClockPolicyError('Closed-bar revision requires an explicit new version.')
                    existing_bars[row['bar_id']] = row['raw_json']
            inserted_ticks = self._insert_once(connection, ticks, tick_values)
            inserted_bars = self._insert_once(connection, bars, bar_values)
        return dict(capture_id=capture_id, epoch_id=policy.epoch_id,
                    ticks_inserted=inserted_ticks, bars_inserted=inserted_bars)

    def latest_tick(self, policy):
        with self.engine.connect() as connection:
            return connection.execute(select(ticks.c.timestamp).where(ticks.c.epoch_id==policy.epoch_id)
                .order_by(ticks.c.timestamp.desc()).limit(1)).scalar_one_or_none()

    def resume_cursor(self,policy):
        """Use completed query intervals, including empty ones, rather than ticks.

        Otherwise a market-closed interval can trap recovery in an endless loop.
        """
        with self.engine.connect() as connection:
            row=connection.execute(select(captures).where(captures.c.epoch_id==policy.epoch_id)
                .order_by(captures.c.tick_end.desc(),captures.c.ingested_at.desc()).limit(1)).mappings().first()
            if row is None:
                return None
            manifest=json.loads(row['manifest_json'])
            if hashed(manifest)!=row['capture_id'] or manifest['epoch_id']!=policy.epoch_id or datetime.fromisoformat(manifest['tick_end'])!=row['tick_end']:
                raise ClockPolicyError('Completed capture cursor has changed.')
            return row['tick_end']

    def status(self,policy,*,as_of):
        as_of=aware_utc(as_of)
        if as_of>aware_utc(self.clock()):
            raise ClockPolicyError('Cannot inspect a future feed state.')
        policy.check_active(as_of)
        blockers=[]
        with self.engine.connect() as connection:
            tick=connection.execute(select(ticks).where(ticks.c.epoch_id==policy.epoch_id,
                ticks.c.ingested_at<=as_of).order_by(ticks.c.timestamp.desc(),ticks.c.fingerprint).limit(1)).mappings().first()
            bar=connection.execute(select(bars).where(bars.c.epoch_id==policy.epoch_id,
                bars.c.ingested_at<=as_of).order_by(bars.c.timestamp.desc()).limit(1)).mappings().first()
        if tick is None:
            blockers.append('UTC_TICKS_MISSING')
        else:
            normalized=policy.tick(json.loads(tick['raw_json']))
            if (instant(normalized['time_msc'])!=tick['timestamp'] or
                    normalized['source_record']['time_msc']!=tick['raw_time_msc'] or
                    hashed(dict(epoch_id=policy.epoch_id,raw=normalized['source_record']))!=tick['fingerprint'] or
                    (normalized['bid'],normalized['ask'])!=(tick['bid'],tick['ask'])):
                raise ClockPolicyError('Stored UTC quote differs from its raw source.')
            if not tick['timestamp']<=tick['received_at']<=tick['ingested_at']<=as_of:
                raise ClockPolicyError('Stored quote receipt chronology is invalid.')
            if (as_of-tick['timestamp']).total_seconds()>120:
                blockers.append('UTC_TICKS_STALE')
        boundary=as_of.replace(minute=as_of.minute//5*5,second=0,microsecond=0)
        expected=boundary-timedelta(minutes=5)
        if bar is None:
            blockers.append('UTC_M5_MISSING')
        else:
            normalized=policy.rate(json.loads(bar['raw_json']))
            if (instant(normalized['time']*1000)!=bar['timestamp'] or
                    normalized['source_record']['time']!=bar['raw_open_time'] or
                    hashed(dict(epoch_id=policy.epoch_id,time=normalized['time']))!=bar['bar_id'] or
                    any(normalized[k]!=bar[k] for k in ('open','high','low','close'))):
                raise ClockPolicyError('Stored UTC bar differs from its raw source.')
            if not bar['timestamp']+timedelta(minutes=5)<=bar['received_at']<=bar['ingested_at']<=as_of:
                raise ClockPolicyError('Stored bar was not complete at receipt.')
            if bar['timestamp']!=expected:
                blockers.append('UTC_M5_NOT_CURRENT')
        return dict(status='pass' if not blockers else 'blocked',epoch_id=policy.epoch_id,
            checked_at=as_of.isoformat(),latest_tick=tick['timestamp'].isoformat() if tick else None,
            latest_bar=bar['timestamp'].isoformat() if bar else None,expected_bar=expected.isoformat(),
            blockers=blockers,execution_authorized=False,legacy_archive_migrated=False)

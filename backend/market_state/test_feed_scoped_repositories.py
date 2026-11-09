"""Feed-aware repository and cursor isolation using synthetic SQLite rows."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.candle_engine import broker_candle_storage
from backend.data_engine import batch_tick_storage
from backend.data_engine.feed_identity import FeedIdentityError
from backend.database.models import (
    Base,
    BrokerCandle,
    MT5FeedRegistry,
    Tick,
)
from backend.market_state import candle_repository, tick_repository


FEED_A = "mt5_eurusd_v1_" + "a" * 64
FEED_B = "mt5_eurusd_v1_" + "b" * 64
START = datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc)
END = START + timedelta(minutes=10)


@pytest.fixture
def feed_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        MT5FeedRegistry.__table__,
        Tick.__table__,
        BrokerCandle.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    session_factory = sessionmaker(bind=engine)

    monkeypatch.setattr(tick_repository, "SessionLocal", session_factory)
    monkeypatch.setattr(candle_repository, "SessionLocal", session_factory)
    monkeypatch.setattr(batch_tick_storage, "SessionLocal", session_factory)
    monkeypatch.setattr(broker_candle_storage, "SessionLocal", session_factory)

    with session_factory() as session:
        session.add_all([
            MT5FeedRegistry(
                feed_id=FEED_A,
                profile_id="SYNTHETIC_A",
                capture_epoch="SYNTHETIC_20261103_A",
                identity_sha256="a" * 64,
            ),
            MT5FeedRegistry(
                feed_id=FEED_B,
                profile_id="SYNTHETIC_B",
                capture_epoch="SYNTHETIC_20261103_B",
                identity_sha256="b" * 64,
            ),
        ])

        tick_rows = [
            (1, None, START),
            (2, FEED_A, START + timedelta(seconds=10)),
            (3, FEED_B, START + timedelta(seconds=20)),
        ]
        for row_id, feed_id, stamp in tick_rows:
            session.add(Tick(
                id=row_id,
                symbol="EURUSD",
                feed_id=feed_id,
                timestamp=stamp,
                source_time_msc=int(stamp.timestamp() * 1000),
                bid=1.1,
                ask=1.1002,
                last=0.0,
                volume=1.0,
                flags=6,
                volume_real=0.0,
                spread=0.0002,
                spread_pips=2.0,
                fingerprint=f"{row_id:064x}",
            ))

        candle_rows = [
            (1, None, START),
            (2, FEED_A, START + timedelta(minutes=1)),
            (3, FEED_B, START + timedelta(minutes=2)),
        ]
        for row_id, feed_id, stamp in candle_rows:
            session.add(BrokerCandle(
                id=row_id,
                symbol="EURUSD",
                timeframe="M5",
                feed_id=feed_id,
                timestamp=stamp,
                open=1.1,
                high=1.101,
                low=1.099,
                close=1.1005,
                tick_volume=10,
                spread_points=2,
                real_volume=0.0,
                is_complete=True,
                source="MT5",
            ))
        session.commit()

    yield session_factory
    engine.dispose()


def test_tick_repository_defaults_to_legacy_and_selects_each_feed(feed_db):
    legacy = tick_repository.TickRepository().get_between(START, END)
    feed_a = tick_repository.TickRepository(FEED_A).get_between(START, END)
    feed_b = tick_repository.TickRepository(FEED_B).get_between(START, END)

    assert [row.feed_id for row in legacy] == [None]
    assert [row.feed_id for row in feed_a] == [FEED_A]
    assert [row.feed_id for row in feed_b] == [FEED_B]


def test_candle_repository_scopes_latest_as_of_and_range_reads(feed_db):
    legacy = candle_repository.CandleRepository().get_latest(limit=10)
    feed_a = candle_repository.CandleRepository(feed_id=FEED_A).get_latest(
        limit=10
    )
    as_of = candle_repository.CandleRepository(feed_id=FEED_B).get_latest_as_of(
        START + timedelta(minutes=3), limit=10
    )
    ranged = candle_repository.CandleRepository(feed_id=FEED_A).get_between(
        START, END
    )

    assert [row.feed_id for row in legacy] == [None]
    assert [row.feed_id for row in feed_a] == [FEED_A]
    assert [row.feed_id for row in as_of] == [FEED_B]
    assert [row.feed_id for row in ranged] == [FEED_A]


def test_latest_tick_and_candle_cursors_are_feed_scoped(feed_db):
    ticks = batch_tick_storage.BatchTickStorage()
    candles = broker_candle_storage.BrokerCandleStorage()

    assert ticks.get_latest_time_msc("EURUSD") == int(START.timestamp() * 1000)
    assert ticks.get_latest_time_msc(
        "EURUSD", feed_id=FEED_A
    ) == int((START + timedelta(seconds=10)).timestamp() * 1000)
    assert ticks.get_latest_time_msc(
        "EURUSD", feed_id=FEED_B
    ) == int((START + timedelta(seconds=20)).timestamp() * 1000)

    legacy_cursor = candles.get_latest_timestamp("EURUSD", "M5")
    feed_a_cursor = candles.get_latest_timestamp(
        "EURUSD", "M5", feed_id=FEED_A
    )
    feed_b_cursor = candles.get_latest_timestamp(
        "EURUSD", "M5", feed_id=FEED_B
    )

    assert legacy_cursor.replace(tzinfo=timezone.utc) == START
    assert feed_a_cursor.replace(tzinfo=timezone.utc) == (
        START + timedelta(minutes=1)
    )
    assert feed_b_cursor.replace(tzinfo=timezone.utc) == (
        START + timedelta(minutes=2)
    )


@pytest.mark.parametrize("repository", [
    tick_repository.TickRepository,
    candle_repository.CandleRepository,
])
def test_repositories_reject_invalid_feed_id_before_query(repository):
    with pytest.raises(FeedIdentityError):
        repository(feed_id="NOT_A_FEED")


def test_storage_cursors_reject_invalid_feed_id_before_opening_session(
    monkeypatch,
):
    def session_must_not_open():
        raise AssertionError("invalid feed must fail before DB access")

    monkeypatch.setattr(batch_tick_storage, "SessionLocal", session_must_not_open)
    monkeypatch.setattr(
        broker_candle_storage, "SessionLocal", session_must_not_open
    )

    with pytest.raises(FeedIdentityError):
        batch_tick_storage.BatchTickStorage().get_latest_time_msc(
            "EURUSD", feed_id="NOT_A_FEED"
        )
    with pytest.raises(FeedIdentityError):
        broker_candle_storage.BrokerCandleStorage().get_latest_timestamp(
            "EURUSD", "M5", feed_id="NOT_A_FEED"
        )

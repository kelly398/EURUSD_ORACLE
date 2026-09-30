from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    Float,
    DateTime,
    Boolean,
    UniqueConstraint,
    func
)

from sqlalchemy.orm import declarative_base


Base = declarative_base()


class Tick(Base):

    __tablename__ = "ticks"

    __table_args__ = (
        UniqueConstraint(
            "fingerprint",
            name="uq_tick_fingerprint"
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True
    )

    symbol = Column(
        String,
        nullable=False,
        index=True
    )

    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True
    )

    source_time_msc = Column(
        BigInteger,
        nullable=False,
        index=True
    )

    bid = Column(
        Float,
        nullable=False
    )

    ask = Column(
        Float,
        nullable=False
    )

    last = Column(
        Float,
        nullable=False,
        default=0.0
    )

    volume = Column(
        Float,
        nullable=False,
        default=0.0
    )

    flags = Column(
        Integer,
        nullable=False,
        default=0
    )

    volume_real = Column(
        Float,
        nullable=False,
        default=0.0
    )

    spread = Column(
        Float,
        nullable=False
    )

    spread_pips = Column(
        Float,
        nullable=False
    )

    fingerprint = Column(
        String(64),
        nullable=False
    )


class Candle(Base):

    __tablename__ = "candles"

    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "timeframe",
            "timestamp",
            name="uq_candle_symbol_timeframe_timestamp"
        ),
    )

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    symbol = Column(
        String,
        nullable=False,
        index=True
    )

    timeframe = Column(
        String,
        nullable=False,
        index=True
    )

    open = Column(
        Float,
        nullable=False
    )

    high = Column(
        Float,
        nullable=False
    )

    low = Column(
        Float,
        nullable=False
    )

    close = Column(
        Float,
        nullable=False
    )

    tick_volume = Column(
        Integer,
        nullable=False
    )

    average_spread_pips = Column(
        Float,
        nullable=False
    )

    max_spread_pips = Column(
        Float,
        nullable=False
    )

    is_complete = Column(
        Boolean,
        nullable=False,
        default=True
    )

    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True
    )

class BrokerCandle(Base):

    __tablename__ = "broker_candles"

    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "timeframe",
            "timestamp",
            "source",
            name="uq_broker_candle"
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True
    )

    symbol = Column(
        String,
        nullable=False,
        index=True
    )

    timeframe = Column(
        String,
        nullable=False,
        index=True
    )

    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True
    )

    open = Column(
        Float,
        nullable=False
    )

    high = Column(
        Float,
        nullable=False
    )

    low = Column(
        Float,
        nullable=False
    )

    close = Column(
        Float,
        nullable=False
    )

    tick_volume = Column(
        BigInteger,
        nullable=False
    )

    spread_points = Column(
        Integer,
        nullable=False
    )

    real_volume = Column(
        Float,
        nullable=False,
        default=0.0
    )

    is_complete = Column(
        Boolean,
        nullable=False,
        default=True
    )

    source = Column(
        String,
        nullable=False,
        default="MT5"
    )

    ingested_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )

class DataQualityEvent(Base):

    __tablename__ = "data_quality_events"

    id = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True
    )

    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True
    )

    symbol = Column(
        String,
        nullable=False,
        index=True
    )

    timeframe = Column(
        String,
        nullable=True
    )

    check_name = Column(
        String,
        nullable=False
    )

    severity = Column(
        String,
        nullable=False
    )

    message = Column(
        String,
        nullable=False
    )
    
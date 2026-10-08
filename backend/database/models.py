from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    Float,
    Date,
    DateTime,
    Boolean,
    UniqueConstraint,
    Index,
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
        Index(
            "ix_ticks_symbol_timestamp_source_msc",
            "symbol",
            "timestamp",
            "source_time_msc"
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
        Index(
            "ix_broker_candles_symbol_timeframe_source_timestamp",
            "symbol",
            "timeframe",
            "source",
            "timestamp"
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

class COTPositioning(Base):

    __tablename__ = "cot_positioning"

    __table_args__ = (

        UniqueConstraint(
            "report_type",
            "cftc_contract_market_code",
            "report_date",
            "content_hash",
            name="uq_cot_positioning_version"
        ),

        Index(
            "ix_cot_positioning_contract_available",
            "cftc_contract_market_code",
            "report_type",
            "available_at"
        ),

        Index(
            "ix_cot_positioning_contract_report_date",
            "cftc_contract_market_code",
            "report_type",
            "report_date"
        ),
    )


    id = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True
    )


    report_type = Column(
        String,
        nullable=False,
        index=True
    )


    cftc_contract_market_code = Column(
        String,
        nullable=False,
        index=True
    )


    market_and_exchange_names = Column(
        String,
        nullable=False
    )


    contract_market_name = Column(
        String,
        nullable=False
    )


    commodity_name = Column(
        String,
        nullable=False
    )


    report_date = Column(
        Date,
        nullable=False,
        index=True
    )


    # Earliest timestamp at which THIS EXACT
    # record version is permitted in analysis.
    #
    # Original reports use the verified official
    # publication timestamp.
    #
    # Later corrected versions receive their own
    # later available_at value and never overwrite
    # the original historical version.
    available_at = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True
    )


    open_interest = Column(
        BigInteger,
        nullable=False
    )


    dealer_long = Column(
        BigInteger,
        nullable=False
    )

    dealer_short = Column(
        BigInteger,
        nullable=False
    )

    dealer_spread = Column(
        BigInteger,
        nullable=False
    )


    asset_manager_long = Column(
        BigInteger,
        nullable=False
    )

    asset_manager_short = Column(
        BigInteger,
        nullable=False
    )

    asset_manager_spread = Column(
        BigInteger,
        nullable=False
    )


    leveraged_money_long = Column(
        BigInteger,
        nullable=False
    )

    leveraged_money_short = Column(
        BigInteger,
        nullable=False
    )

    leveraged_money_spread = Column(
        BigInteger,
        nullable=False
    )


    other_reportables_long = Column(
        BigInteger,
        nullable=False
    )

    other_reportables_short = Column(
        BigInteger,
        nullable=False
    )

    other_reportables_spread = Column(
        BigInteger,
        nullable=False
    )


    nonreportable_long = Column(
        BigInteger,
        nullable=False
    )

    nonreportable_short = Column(
        BigInteger,
        nullable=False
    )


    # Identifier supplied by the upstream CFTC
    # dataset. This is provenance, not our
    # revision identity: an upstream system may
    # reuse an ID when correcting a row.
    source_record_id = Column(
        String,
        nullable=False
    )


    # SHA-256 of the normalized raw positioning
    # fields. It allows ORACLE to retain and
    # distinguish later corrections.
    content_hash = Column(
        String(64),
        nullable=False,
        index=True
    )


    source = Column(
        String,
        nullable=False,
        default="CFTC"
    )


    ingested_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )
class CentralBankRate(Base):

    __tablename__ = "central_bank_rates"

    __table_args__ = (

        UniqueConstraint(
            "source",
            "central_bank",
            "rate_name",
            "source_record_id",
            "content_hash",
            name="uq_central_bank_rate_version"
        ),

        Index(
            "ix_central_bank_rates_bank_rate_available",
            "central_bank",
            "rate_name",
            "available_at"
        ),

        Index(
            "ix_central_bank_rates_bank_rate_reference",
            "central_bank",
            "rate_name",
            "reference_date"
        ),
    )


    id = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True
    )


    central_bank = Column(
        String,
        nullable=False,
        index=True
    )


    rate_name = Column(
        String,
        nullable=False,
        index=True
    )


    reference_date = Column(
        Date,
        nullable=False,
        index=True
    )


    # Earliest timestamp at which THIS EXACT
    # rate-record version may legally enter
    # analysis.
    available_at = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True
    )


    value_percent = Column(
        Float,
        nullable=False
    )


    # Stable logical-record identity supplied
    # or deterministically derived by the
    # acquisition layer.
    source_record_id = Column(
        String,
        nullable=False
    )


    # SHA-256 of normalized economic contents.
    # Changed contents create a new row rather
    # than overwriting a historical version.
    content_hash = Column(
        String(64),
        nullable=False,
        index=True
    )


    source = Column(
        String,
        nullable=False,
        index=True
    )


    ingested_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )


class EconomicCalendarEvent(Base):

    __tablename__ = "economic_calendar_events"

    __table_args__ = (

        UniqueConstraint(
            "source",
            "source_event_id",
            "content_hash",
            name="uq_economic_calendar_event_version"
        ),

        Index(
            "ix_economic_calendar_currency_scheduled_available",
            "currency",
            "scheduled_at",
            "available_at"
        ),

        Index(
            "ix_economic_calendar_event_available",
            "source",
            "source_event_id",
            "available_at"
        ),
    )


    id = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True
    )


    # Stable logical event identity supplied
    # or deterministically derived by the
    # acquisition layer.
    source_event_id = Column(
        String,
        nullable=False
    )


    event_name = Column(
        String,
        nullable=False
    )


    jurisdiction = Column(
        String,
        nullable=False,
        index=True
    )


    currency = Column(
        String(3),
        nullable=False,
        index=True
    )


    # When the economic event is scheduled to
    # occur. This may legitimately be later
    # than the analysis timestamp.
    scheduled_at = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True
    )


    # Earliest timestamp at which THIS EXACT
    # event-record version may legally enter
    # analysis.
    available_at = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True
    )


    actual = Column(
        Float,
        nullable=True
    )


    forecast = Column(
        Float,
        nullable=True
    )


    previous = Column(
        Float,
        nullable=True
    )


    unit = Column(
        String,
        nullable=True
    )


    # SHA-256 of normalized event contents.
    # New forecasts, actual values, corrections,
    # or revisions create new versions.
    content_hash = Column(
        String(64),
        nullable=False,
        index=True
    )


    source = Column(
        String,
        nullable=False,
        index=True
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
    
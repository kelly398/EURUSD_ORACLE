"""Isolated additive tables; never calls the legacy Base.metadata.create_all."""

from sqlalchemy import (MetaData, Table, Column, String, Integer,
                        Boolean, Text, ForeignKey, Index, UniqueConstraint)
from backend.database.utc_type import UTCInstant


metadata = MetaData()
schema_version = Table("oracle_memory_schema", metadata,
    Column("version", Integer, primary_key=True))
cycles = Table("oracle_council_cycles", metadata,
    Column("cycle_id", String(64), primary_key=True),
    Column("scope", String(128), nullable=False),
    Column("origin", String(32), nullable=False),
    Column("symbol", String(16), nullable=False),
    Column("timeframe", String(8), nullable=False),
    Column("as_of", UTCInstant(), nullable=False),
    Column("recorded_at", UTCInstant(), nullable=False),
    Column("mode", String(16), nullable=False),
    Column("decision", String(64), nullable=False),
    Column("is_complete", Boolean, nullable=False),
    Column("context_sha256", String(64), nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("timings_json", Text, nullable=False),
    UniqueConstraint("scope", "symbol", "timeframe", "as_of", "mode", name="uq_oracle_council_cycle"))
agents = Table("oracle_agent_observations", metadata,
    Column("cycle_id", String(64), ForeignKey(cycles.c.cycle_id), primary_key=True),
    Column("agent_id", String(128), primary_key=True),
    Column("seat_number", Integer, nullable=False),
    Column("stage", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("observation_json", Text, nullable=False))
forecasts = Table("oracle_forecasts", metadata,
    Column("prediction_id", String(64), primary_key=True),
    Column("cycle_id", String(64), ForeignKey(cycles.c.cycle_id), nullable=False),
    Column("agent_id", String(128), nullable=False),
    Column("target_at", UTCInstant(), nullable=False),
    Column("recorded_at", UTCInstant(), nullable=False),
    Column("prospective", Boolean, nullable=False),
    Column("direction", String(16), nullable=False),
    Column("forecast_json", Text, nullable=False),
    UniqueConstraint("cycle_id", "agent_id", name="uq_oracle_forecast_agent"))
outcomes = Table("oracle_forecast_outcomes", metadata,
    Column("prediction_id", String(64), ForeignKey(forecasts.c.prediction_id), primary_key=True),
    Column("recorded_at", UTCInstant(), nullable=False),
    Column("quote_available_at", UTCInstant(), nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("outcome_json", Text, nullable=False))
Index("ix_oracle_forecasts_due", forecasts.c.target_at, forecasts.c.prediction_id)
Index("ix_oracle_cycles_scope_time", cycles.c.scope, cycles.c.origin, cycles.c.as_of)

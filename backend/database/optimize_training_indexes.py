from sqlalchemy import text

from backend.database.connection import engine


INDEX_STATEMENTS = (
    (
        "ix_ticks_symbol_timestamp_source_msc",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS
        ix_ticks_symbol_timestamp_source_msc
        ON ticks (symbol, timestamp, source_time_msc)
        """,
    ),
    (
        "ix_broker_candles_symbol_timeframe_source_timestamp",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS
        ix_broker_candles_symbol_timeframe_source_timestamp
        ON broker_candles (symbol, timeframe, source, timestamp)
        """,
    ),
    (
        "ix_ml_training_samples_training_slice",
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS
        ix_ml_training_samples_training_slice
        ON ml_training_samples
        (feature_version, label_version, split, symbol, timeframe, feature_timestamp)
        """,
    ),
)


def optimize_training_indexes():
    # PostgreSQL requires CREATE INDEX CONCURRENTLY to run outside a transaction.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        for name, statement in INDEX_STATEMENTS:
            try:
                print(f"Ensuring index: {name}")
                print("This may take several minutes for large tables...")
                connection.execute(text(statement))
                print(f"Completed: {name}")
            except Exception as exc:
                print(f"FAILED: {name}")
                raise exc


if __name__ == "__main__":
    optimize_training_indexes()
    print("TRAINING INDEX OPTIMIZATION: COMPLETE")

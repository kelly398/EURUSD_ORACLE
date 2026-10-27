"""Explicit journal destination selection, without connection-string output."""
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.engine import URL


def memory_engine(*, journal=None, postgres=False):
    if (journal is not None) == bool(postgres):
        raise ValueError("Choose exactly one memory database destination.")
    if postgres:
        from backend.database.connection import engine
        return engine
    return create_engine(URL.create("sqlite", database=str(Path(journal).resolve())),
                         connect_args={"timeout": 15})

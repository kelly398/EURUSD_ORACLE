"""Shared aware-UTC SQL type with an explicit SQLite round-trip convention."""
from datetime import datetime, timezone
from sqlalchemy.types import TypeDecorator, DateTime


def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Database timestamp must be timezone-aware.")
    return value.astimezone(timezone.utc)


class UTCInstant(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return utc(value) if value is not None else None

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None and dialect.name == 'sqlite':
            value = value.replace(tzinfo=timezone.utc)
        return utc(value)

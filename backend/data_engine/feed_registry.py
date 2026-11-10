"""Local MT5 feed-registry preparation and explicitly confirmed registration.

Registry rows are routing labels only. Registering one does not admit a source,
enable market-data writes, authenticate a broker account, or authorize trading.
The registration API refuses every database except the dedicated local dev DB.
"""

from dataclasses import asdict, dataclass
import json

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.config.credentials import DATABASE_URL
from backend.data_engine.feed_identity import build_local_feed_identity
from backend.database.connection import SessionLocal
from backend.database.feed_schema_migration import (
    LOCAL_DATABASE_NAME,
    FeedSchemaMigrationError,
    validate_local_database_url,
)
from backend.database.models import MT5FeedRegistry


class FeedRegistryRegistrationError(RuntimeError):
    """A local registry insert was not safe or did not match its identity."""


@dataclass(frozen=True)
class PreparedFeedRegistration:
    feed_id: str
    profile_id: str
    capture_epoch: str
    identity_sha256: str

    def database_values(self):
        return asdict(self)

    def report(self, status):
        return {
            "status": status,
            "feed_id": self.feed_id,
            "profile_id": self.profile_id,
            "capture_epoch": self.capture_epoch,
            "identity_sha256": self.identity_sha256,
            "independently_authenticated": False,
            "source_admitted": False,
            "market_data_writable": False,
            "trading_authorized": False,
            "final_action": "HOLD",
        }


def prepare_feed_registration(profile, *, capture_epoch):
    """Build the minimal immutable registry row without a DB or terminal call."""
    identity = build_local_feed_identity(
        profile,
        capture_epoch=capture_epoch,
    )
    return PreparedFeedRegistration(
        feed_id=identity["feed_id"],
        profile_id=profile["profile_id"],
        capture_epoch=capture_epoch,
        identity_sha256=identity["identity_sha256"],
    )


def _same_record(row, registration):
    return all(
        getattr(row, name) == value
        for name, value in registration.database_values().items()
    )


def _insert_or_verify(session, registration):
    existing = session.query(MT5FeedRegistry).filter_by(
        feed_id=registration.feed_id
    ).one_or_none()
    if existing is not None:
        if not _same_record(existing, registration):
            raise FeedRegistryRegistrationError(
                "Existing registry row conflicts with the derived identity."
            )
        return registration.report("ALREADY_REGISTERED_ROUTING_LABEL")

    session.add(MT5FeedRegistry(**registration.database_values()))
    try:
        session.commit()
    except IntegrityError:
        # A concurrent first registration may have inserted the same key.
        session.rollback()
        existing = session.query(MT5FeedRegistry).filter_by(
            feed_id=registration.feed_id
        ).one_or_none()
        if existing is None or not _same_record(existing, registration):
            raise FeedRegistryRegistrationError(
                "Concurrent registry insert did not match the derived identity."
            ) from None
        return registration.report("ALREADY_REGISTERED_ROUTING_LABEL")

    return registration.report("REGISTERED_ROUTING_LABEL_ONLY")


def register_local_feed(
    profile,
    *,
    capture_epoch,
    database_url=None,
    confirm_database_name="",
    session_factory=None,
):
    """Register only a deterministic routing row in local dev PostgreSQL.

    The caller must repeat the exact DB name. The URL is validated before a
    session is opened; the connected database and role are checked again.
    No profile content, account alias, credential, tick, candle, or event is
    persisted by this operation.
    """
    if confirm_database_name != LOCAL_DATABASE_NAME:
        raise FeedRegistryRegistrationError(
            "Exact local database-name confirmation is required."
        )
    raw_url = DATABASE_URL if database_url is None else database_url
    try:
        validate_local_database_url(raw_url)
    except FeedSchemaMigrationError as exc:
        raise FeedRegistryRegistrationError(
            "Refusing registry registration outside the local dev database."
        ) from exc

    registration = prepare_feed_registration(
        profile,
        capture_epoch=capture_epoch,
    )
    factory = SessionLocal if session_factory is None else session_factory
    session = factory()
    try:
        session.execute(text("SET LOCAL lock_timeout = '2s'"))
        session.execute(text("SET LOCAL statement_timeout = '5s'"))
        actual_database = session.execute(
            text("SELECT current_database()")
        ).scalar_one()
        if actual_database != LOCAL_DATABASE_NAME:
            raise FeedRegistryRegistrationError(
                "Connected database did not match the local development target."
            )
        is_superuser = session.execute(
            text(
                "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
            )
        ).scalar_one()
        if is_superuser:
            raise FeedRegistryRegistrationError(
                "Registry registration requires the restricted local role."
            )
        return _insert_or_verify(session, registration)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def format_registration_report(report):
    """Serialize a bounded, credential-free registration report."""
    return json.dumps(report, indent=2, sort_keys=True)

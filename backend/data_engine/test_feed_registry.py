"""Synthetic registry-entry tests; registration never reaches PostgreSQL."""

from copy import deepcopy

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.data_engine import feed_registry
from backend.database.models import MT5FeedRegistry
from backend.runtime.stage7_universal_mt5_adapter import example_hfm_profile


EPOCH = "SYNTHETIC_REGISTRY_20261109"


@pytest.fixture
def registry_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    MT5FeedRegistry.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


def test_prepare_registry_row_is_minimal_and_non_authorizing():
    profile = example_hfm_profile()
    original = deepcopy(profile)

    prepared = feed_registry.prepare_feed_registration(
        profile,
        capture_epoch=EPOCH,
    )
    report = prepared.report("PREVIEW_ONLY")

    assert profile == original
    assert set(prepared.database_values()) == {
        "feed_id", "profile_id", "capture_epoch", "identity_sha256"
    }
    assert "account_ref" not in report
    assert report["independently_authenticated"] is False
    assert report["source_admitted"] is False
    assert report["market_data_writable"] is False
    assert report["trading_authorized"] is False
    assert report["final_action"] == "HOLD"


def test_registry_insert_is_idempotent_and_routing_only(registry_session):
    registration = feed_registry.prepare_feed_registration(
        example_hfm_profile(),
        capture_epoch=EPOCH,
    )

    with registry_session() as session:
        first = feed_registry._insert_or_verify(session, registration)
    with registry_session() as session:
        second = feed_registry._insert_or_verify(session, registration)

    assert first["status"] == "REGISTERED_ROUTING_LABEL_ONLY"
    assert second["status"] == "ALREADY_REGISTERED_ROUTING_LABEL"
    assert first["feed_id"] == second["feed_id"] == registration.feed_id
    assert first["market_data_writable"] is False
    with registry_session() as session:
        assert session.query(MT5FeedRegistry).count() == 1


def test_existing_conflicting_registry_row_is_never_overwritten(registry_session):
    registration = feed_registry.prepare_feed_registration(
        example_hfm_profile(),
        capture_epoch=EPOCH,
    )
    with registry_session() as session:
        session.add(MT5FeedRegistry(
            **{
                **registration.database_values(),
                "profile_id": "DIFFERENT_PROFILE",
            }
        ))
        session.commit()

    with registry_session() as session:
        with pytest.raises(
            feed_registry.FeedRegistryRegistrationError,
            match="conflicts with the derived identity",
        ):
            feed_registry._insert_or_verify(session, registration)
        session.rollback()
        stored = session.query(MT5FeedRegistry).one()
        assert stored.profile_id == "DIFFERENT_PROFILE"


@pytest.mark.parametrize("database_url", [
    "postgresql+psycopg2:///eurusd_oracle_archive",
    "postgresql+psycopg2://db.example/eurusd_oracle_local",
    "sqlite:///eurusd_oracle_local.db",
])
def test_registration_refuses_unsafe_database_before_opening_session(database_url):
    def session_must_not_open():
        raise AssertionError("unsafe target must be rejected before session")

    with pytest.raises(
        feed_registry.FeedRegistryRegistrationError,
        match="Refusing registry registration",
    ):
        feed_registry.register_local_feed(
            example_hfm_profile(),
            capture_epoch=EPOCH,
            database_url=database_url,
            confirm_database_name="eurusd_oracle_local",
            session_factory=session_must_not_open,
        )


def test_registration_requires_exact_confirmation_before_session():
    def session_must_not_open():
        raise AssertionError("confirmation must be checked before session")

    with pytest.raises(
        feed_registry.FeedRegistryRegistrationError,
        match="Exact local database-name confirmation",
    ):
        feed_registry.register_local_feed(
            example_hfm_profile(),
            capture_epoch=EPOCH,
            database_url="postgresql+psycopg2:///eurusd_oracle_local",
            confirm_database_name="",
            session_factory=session_must_not_open,
        )

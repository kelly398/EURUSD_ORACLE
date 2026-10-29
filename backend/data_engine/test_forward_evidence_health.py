
"""Phase 2 forward evidence inspections; synthetic fixtures only."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest
from sqlalchemy import create_engine, event

from backend.data_engine.clock_policy import (
    ClockPolicy,
    hfm_utc_to_wall,
    implementation_sha256,
    utc_msc,
)
from backend.data_engine.forward_store import (
    ForwardFeedStore,
    bars,
    captures,
    ticks,
)
from backend.data_engine.forward_evidence_health import (
    ForwardEvidenceError,
    _parse_as_of,
    inspect_forward_evidence,
    list_forward_epochs,
    run,
)

NOW = datetime(2026, 10, 27, 14, 0, tzinfo=timezone.utc)


def make_fixture(*, count=2):
    engine = create_engine("sqlite://")
    clock = [NOW]

    store = ForwardFeedStore(
        engine, clock=lambda: clock[0]
    )

    # Creation applies to the isolated test fixture only.
    # The production inspector never initializes tables.
    store.initialize()

    policy = ClockPolicy(
        company_sha256=sha256(b"FAKE-COMPANY").hexdigest(),
        server_sha256=sha256(b"FAKE-SERVER").hexdigest(),
        evidence_sha256=sha256(b"FAKE-RECEIPT").hexdigest(),
        implementation_sha256=implementation_sha256(),
        valid_from=NOW - timedelta(hours=1),
        valid_until=NOW + timedelta(days=1),
    )

    for index in range(count):
        current = NOW + timedelta(minutes=5 * index)
        clock[0] = current

        moment = current - timedelta(seconds=2)
        raw_ms = utc_msc(hfm_utc_to_wall(moment))

        tick = dict(
            time=raw_ms // 1000,
            time_msc=raw_ms,
            bid=1.1,
            ask=1.1001,
            last=0.0,
            volume=0,
            flags=6,
            volume_real=0.0,
        )

        bar_at = current - timedelta(minutes=5)
        raw_bar_time = (
            utc_msc(hfm_utc_to_wall(bar_at)) // 1000
        )

        bar = dict(
            time=raw_bar_time,
            open=1.1,
            high=1.101,
            low=1.099,
            close=1.1005,
            tick_volume=10,
            spread=10,
            real_volume=0,
        )

        store.append(
            policy,
            tick_rows=[policy.tick(tick)],
            bar_rows=[policy.rate(bar)],
            received_at=current,
            tick_start=(
                NOW - timedelta(minutes=1)
                if index == 0
                else current - timedelta(minutes=5)
            ),
            tick_end=current,
            bar_start=current - timedelta(minutes=10),
            bar_end=current,
        )

    return engine, policy, clock[0]


@pytest.fixture
def ready():
    engine, policy, cutoff = make_fixture()

    try:
        yield engine, policy, cutoff
    finally:
        engine.dispose()


def check(engine, policy, cutoff, **kwargs):
    return inspect_forward_evidence(
        engine,
        epoch_id=policy.epoch_id,
        as_of=cutoff,
        **kwargs,
    )


def test_source_health_holds_even_when_all_local_evidence_valid(ready):
    engine, policy, now = ready
    result = check(engine, policy, now)

    assert (
        result["local_evidence_status"]
        == "LOCAL_STRUCTURAL_CHECKS_PASS_UNATTESTED"
    )
    assert result["captures_examined"] == 2
    assert result["source_rows_examined"] == 4
    assert result["blockers"] == []

    assert result["latest_bar"] == (
        now - timedelta(minutes=5)
    ).isoformat()

    assert result["latest_tick"] == (
        now - timedelta(seconds=2)
    ).isoformat()

    assert result["final_action"] == "HOLD"

    for field in (
        "independent_broker_clock_attestation",
        "independent_source_receipt_attestation",
        "historical_utc_certified",
        "real_data_admission",
        "training_authorized",
        "execution_authorized",
    ):
        assert result[field] is False


def test_report_reproducible_and_sha256_authentic(ready):
    engine, policy, now = ready

    first = check(engine, policy, now)
    second = check(engine, policy, now)

    assert first == second

    payload = {
        key: value
        for key, value in first.items()
        if key != "report_sha256"
    }

    assert first["report_sha256"] == sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def test_latest_receipt_does_not_backfill_earlier_cutoff(ready):
    engine, policy, now = ready

    earlier = check(
        engine,
        policy,
        now - timedelta(minutes=5),
    )

    assert earlier["captures_examined"] == 1
    assert earlier["source_rows_examined"] == 2

    assert (
        earlier["local_evidence_status"]
        == "LOCAL_STRUCTURAL_CHECKS_PASS_UNATTESTED"
    )


def test_as_of_later_than_capture_reports_staleness(ready):
    engine, policy, now = ready

    report = check(
        engine,
        policy,
        now + timedelta(minutes=3),
    )

    assert report["local_evidence_status"] == "BLOCKED"
    assert (
        "LATEST_TICK_STALE_OR_FUTURE"
        in report["blockers"]
    )


def test_observation_missing_and_tampered_source_fail_closed(ready):
    engine, policy, now = ready

    with engine.begin() as connection:
        connection.execute(
            ticks.update().values(bid=2.0)
        )

    report = check(engine, policy, now)

    assert (
        "TICK_RAW_PROVENANCE_INVALID"
        in report["blockers"]
    )
    assert report["execution_authorized"] is False


def test_manifest_tampering_detected(ready):
    engine, policy, now = ready

    with engine.begin() as connection:
        connection.execute(
            captures.update().values(manifest_json="{}")
        )

    report = check(engine, policy, now)

    assert (
        "CAPTURE_MANIFEST_HASH_MISMATCH"
        in report["blockers"]
    )


def test_missing_bar_blocks_with_different_reason(ready):
    engine, policy, now = ready

    with engine.begin() as connection:
        connection.execute(bars.delete())

    report = check(engine, policy, now)

    assert (
        "ORIGINAL_CAPTURE_PAYLOAD_NOT_RECONSTRUCTABLE"
        in report["evidence_limitations"]
    )
    assert (
        "LATEST_CLOSED_M5_MISSING"
        in report["blockers"]
    )


def test_capture_overlap_and_receipt_regression_block(ready):
    engine, policy, now = ready

    with engine.begin() as connection:
        connection.execute(
            captures.update()
            .where(captures.c.tick_end == now)
            .values(
                tick_start=now - timedelta(minutes=6),
                received_at=now - timedelta(minutes=7),
            )
        )

    report = check(engine, policy, now)

    assert (
        "OVERLAPPING_QUERY_WINDOWS_ALLOWED_BY_STORE"
        in report["observations"]
    )
    assert (
        "CAPTURE_RECEIPT_REGRESSION"
        in report["blockers"]
    )


def test_capture_gap_detected(ready):
    engine, policy, now = ready

    with engine.begin() as connection:
        connection.execute(
            captures.update()
            .where(captures.c.tick_end == now)
            .values(
                tick_start=now - timedelta(minutes=4)
            )
        )

    report = check(engine, policy, now)

    assert (
        "CAPTURE_UNQUERIED_TICK_GAP"
        in report["blockers"]
    )


def test_sample_limits_fail_closed(ready):
    engine, policy, now = ready

    limited = check(
        engine,
        policy,
        now,
        max_captures=1,
    )

    assert (
        "CAPTURE_SAMPLE_TRUNCATED"
        in limited["blockers"]
    )
    assert (
        "INSPECTION_INCOMPLETE"
        in limited["blockers"]
    )

    limited_source = check(
        engine,
        policy,
        now,
        max_source_rows=1,
    )

    assert (
        "SOURCE_SCAN_TRUNCATED"
        in limited_source["blockers"]
    )


def test_wrong_epoch_and_as_of_validation(ready):
    engine, policy, now = ready

    with pytest.raises(
        ForwardEvidenceError, match="Epoch absent"
    ):
        inspect_forward_evidence(
            engine,
            epoch_id="f" * 64,
            as_of=now,
        )

    with pytest.raises(
        ForwardEvidenceError, match="epoch ID"
    ):
        inspect_forward_evidence(
            engine,
            epoch_id="not-an-id",
            as_of=now,
        )

    with pytest.raises(
        ForwardEvidenceError, match="Future"
    ):
        check(
            engine,
            policy,
            datetime.now(timezone.utc)
            + timedelta(days=1),
        )

    for value in (
        "2026-10-27T14:00:00+03:00",
        "2026-10-27",
        "invalid",
    ):
        with pytest.raises(ForwardEvidenceError):
            _parse_as_of(value)


@pytest.mark.parametrize(
    "value",
    [0, -1, 33, True, "10", 3.5],
)
def test_invalid_capture_limits_rejected(ready, value):
    engine, policy, now = ready

    with pytest.raises(
        ForwardEvidenceError,
        match="max_captures",
    ):
        check(
            engine,
            policy,
            now,
            max_captures=value,
        )


def test_epoch_listing_read_only_and_bounded(ready):
    engine, policy, now = ready

    result = list_forward_epochs(engine)

    assert result["truncated"] is False
    assert (
        result["epochs"][0]["epoch_id"]
        == policy.epoch_id
    )
    assert result["read_only"] is True


def test_inspection_and_listing_never_issue_write_sql(ready):
    engine, policy, now = ready
    statements = []

    def observe(
        _conn,
        _cursor,
        statement,
        _params,
        _ctx,
        _many,
    ):
        statements.append(
            statement.lstrip().upper()
        )

    event.listen(
        engine,
        "before_cursor_execute",
        observe,
    )

    try:
        check(engine, policy, now)
        list_forward_epochs(engine)
    finally:
        event.remove(
            engine,
            "before_cursor_execute",
            observe,
        )

    assert statements
    assert all(
        statement.startswith("SELECT")
        for statement in statements
    )


def test_cli_requires_database_optin_before_connection():
    with pytest.raises(SystemExit) as exc:
        run(["--list-epochs"])

    assert exc.value.code == 2


def test_cli_requires_epoch_and_asof_before_connection(capsys):
    assert run(["--read-only-db"]) == 1

    assert (
        "INSPECTION BLOCKED"
        in capsys.readouterr().out
    )


def test_store_permitted_overlap_and_dedup_are_not_source_corruption(ready):
    engine, policy, now = ready

    clock = [now + timedelta(minutes=1)]

    store = ForwardFeedStore(
        engine,
        clock=lambda: clock[0],
    )

    last_ms = utc_msc(
        hfm_utc_to_wall(
            now - timedelta(seconds=2)
        )
    )

    quote = dict(
        time=last_ms // 1000,
        time_msc=last_ms,
        bid=1.1,
        ask=1.1001,
        last=0.0,
        volume=0,
        flags=6,
        volume_real=0.0,
    )

    bar_at = now - timedelta(minutes=5)

    bar = dict(
        time=utc_msc(
            hfm_utc_to_wall(bar_at)
        ) // 1000,
        open=1.1,
        high=1.101,
        low=1.099,
        close=1.1005,
        tick_volume=10,
        spread=10,
        real_volume=0,
    )

    saved = store.append(
        policy,
        tick_rows=[policy.tick(quote)],
        bar_rows=[policy.rate(bar)],
        received_at=clock[0],
        tick_start=now - timedelta(minutes=4),
        tick_end=clock[0],
        bar_start=now - timedelta(minutes=10),
        bar_end=clock[0],
    )

    assert saved["ticks_inserted"] == 0
    assert saved["bars_inserted"] == 0

    report = check(
        engine,
        policy,
        clock[0],
    )

    assert (
        report["local_evidence_status"]
        == "LOCAL_STRUCTURAL_CHECKS_PASS_UNATTESTED"
    )
    assert report["overlap_query_count"] >= 1
    assert report["unqueried_gap_count"] == 0

    assert (
        "OVERLAPPING_QUERY_WINDOWS_ALLOWED_BY_STORE"
        in report["observations"]
    )
    assert (
        "ORIGINAL_CAPTURE_PAYLOAD_NOT_RECONSTRUCTABLE"
        in report["evidence_limitations"]
    )

    assert not any(
        "MANIFEST_MISMATCH" in reason
        for reason in report["blockers"]
    )

    assert report["final_action"] == "HOLD"


def test_bound_clock_version_mismatch_is_explicit_blocker_without_fake_raw_failures(
    ready,
    monkeypatch,
):
    engine, policy, now = ready

    monkeypatch.setattr(
        "backend.data_engine.forward_evidence_health.implementation_sha256",
        lambda: "0" * 64,
    )

    report = check(engine, policy, now)

    assert (
        "LOCAL_CLOCK_IMPLEMENTATION_UNMATCHED"
        in report["blockers"]
    )
    assert (
        "RAW_UTC_REPLAY_SKIPPED_CLOCK_VERSION_MISMATCH"
        in report["evidence_limitations"]
    )
    assert (
        "TICK_RAW_PROVENANCE_INVALID"
        not in report["blockers"]
    )

    assert (
        report["bound_clock_implementation_sha256"]
        == policy.implementation_sha256
    )
    assert (
        report["local_clock_implementation_sha256"]
        == "0" * 64
    )


def test_late_ingested_source_cannot_appear_in_past_inspection(ready):
    engine, policy, now = ready

    with engine.begin() as connection:
        connection.execute(
            ticks.update()
            .where(
                ticks.c.timestamp
                == now - timedelta(seconds=2)
            )
            .values(
                ingested_at=now + timedelta(seconds=30)
            )
        )

    report = check(engine, policy, now)

    assert report["latest_tick"] == (
        now - timedelta(
            minutes=5,
            seconds=2,
        )
    ).isoformat()

    assert (
        "LATEST_TICK_STALE_OR_FUTURE"
        in report["blockers"]
    )


def test_source_membership_loss_is_evidence_limit_not_forged_digest_claim(
    ready,
):
    engine, policy, now = ready

    with engine.begin() as connection:
        connection.execute(
            ticks.delete()
            .where(
                ticks.c.timestamp
                == now - timedelta(seconds=2)
            )
        )

    report = check(engine, policy, now)

    assert (
        "ORIGINAL_CAPTURE_PAYLOAD_NOT_RECONSTRUCTABLE"
        in report["evidence_limitations"]
    )
    assert (
        "LATEST_TICK_STALE_OR_FUTURE"
        in report["blockers"]
    )
    assert (
        "TICK_CAPTURE_MANIFEST_MISMATCH"
        not in report["blockers"]
    )


def test_sampled_earlier_capture_not_sufficient_to_claim_whole_epoch(
    ready,
):
    engine, policy, now = ready

    report = check(
        engine,
        policy,
        now,
        max_captures=1,
    )

    assert report["sample_truncated"] is True

    assert (
        "INSPECTION_INCOMPLETE"
        in report["blockers"]
    )
    assert (
        "EARLIER_CAPTURE_HISTORY_NOT_EXAMINED"
        in report["evidence_limitations"]
    )

    assert report["latest_tick"] == (
        now - timedelta(seconds=2)
    ).isoformat()

    assert report["latest_bar"] == (
        now - timedelta(minutes=5)
    ).isoformat()


def test_bars_and_ticks_always_have_independent_attestation_false(
    ready,
):
    engine, policy, now = ready

    report = check(engine, policy, now)

    assert (
        "ORDERED_ORIGINAL_API_BATCH_BYTES_NOT_RETAINED"
        in report["evidence_limitations"]
    )
    assert report["real_data_admission"] is False
    assert report["training_authorized"] is False
    assert report["execution_authorized"] is False


def test_clock_implementation_hash_drift_changes_report(
    ready,
    monkeypatch,
):
    engine, policy, now = ready

    before = check(engine, policy, now)

    monkeypatch.setattr(
        "backend.data_engine.forward_evidence_health.implementation_sha256",
        lambda: "1" * 64,
    )

    after = check(engine, policy, now)

    assert (
        before["report_sha256"]
        != after["report_sha256"]
    )
    assert after["local_evidence_status"] == "BLOCKED"

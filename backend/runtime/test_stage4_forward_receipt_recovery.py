
"""S4-09..12 synthetic forward receipt inspection; no external broker access."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest
from sqlalchemy import create_engine, event

from backend.data_engine.clock_policy import (
    ClockPolicy, implementation_sha256, hfm_utc_to_wall, utc_msc,
)
from backend.data_engine.forward_store import (
    ForwardFeedStore, captures, ticks, bars, encoded, hashed,
)
from backend.runtime.stage4_forward_receipt_recovery import (
    ForwardReceiptReviewError, inspect_stage4_forward_receipts, run,
)

UTC = timezone.utc
NOW = datetime(2026, 10, 27, 14, 0, 0, tzinfo=UTC)


def raw_tick(at):
    millisecond = utc_msc(hfm_utc_to_wall(at))
    return {
        "time": millisecond // 1000,
        "time_msc": millisecond,
        "bid": 1.1, "ask": 1.1002,
        "last": 0., "volume": 0, "flags": 6, "volume_real": 0.,
    }


def raw_bar(at):
    return {
        "time": utc_msc(hfm_utc_to_wall(at)) // 1000,
        "open": 1.1, "high": 1.101, "low": 1.099, "close": 1.1005,
        "tick_volume": 10, "spread": 10, "real_volume": 0,
    }


@pytest.fixture
def sample(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    current = [NOW]
    policy = ClockPolicy(
        company_sha256=sha256(b"SYNTHETIC COMPANY").hexdigest(),
        server_sha256=sha256(b"SYNTHETIC SERVER").hexdigest(),
        evidence_sha256="a" * 64,
        implementation_sha256=implementation_sha256(),
        valid_from=NOW-timedelta(hours=1),
        valid_until=NOW+timedelta(days=1),
    )
    store = ForwardFeedStore(engine, clock=lambda: current[0])
    store.initialize()
    bar = policy.rate(raw_bar(NOW-timedelta(minutes=5)))
    c1 = store.append(
        policy, tick_rows=[policy.tick(raw_tick(NOW-timedelta(seconds=2)))],
        bar_rows=[bar], received_at=NOW,
        tick_start=NOW-timedelta(minutes=1), tick_end=NOW,
        bar_start=NOW-timedelta(minutes=10), bar_end=NOW,
    )
    current[0] = NOW+timedelta(seconds=10)
    c2 = store.append(
        policy, tick_rows=[policy.tick(raw_tick(NOW+timedelta(seconds=7)))],
        bar_rows=[bar], received_at=current[0],
        tick_start=NOW-timedelta(seconds=3), tick_end=current[0],
        bar_start=NOW-timedelta(minutes=10), bar_end=NOW,
    )
    path = tmp_path / "original-policy.json"
    path.write_text(policy.model_dump_json(), encoding="utf-8")
    try:
        yield engine, policy, path, sha256(path.read_bytes()).hexdigest(), c1, c2
    finally:
        engine.dispose()


def inspect(sample, **changes):
    engine, policy, path, digest, *_ = sample
    return inspect_stage4_forward_receipts(
        engine,
        **{
            "epoch_id": policy.epoch_id,
            "as_of": NOW + timedelta(seconds=20),
            "policy_file": path,
            "expected_policy_sha256": digest,
            **changes,
        },
    )


def test_s409_capture_manifest_hashes_and_counts(sample):
    r = inspect(sample)
    assert r["inspected_scope"]["captures"] == 2
    assert r["inspected_scope"]["first_owned_ticks"] == 2
    assert r["inspected_scope"]["first_owned_bars"] == 1
    assert r["manifest_self_hashes_valid_for_inspected_captures"] is True


def test_s409_no_original_payload_hash_reconstruction(sample):
    r = inspect(sample)
    assert r["original_per_capture_source_lists_available"] is False
    assert r["original_capture_tick_bar_sha256_recomputed"] is False
    assert r["deduplicated_rows_cannot_reconstruct_original_capture_payloads"] is True


def test_s410_first_owned_source_links_consistent(sample):
    r = inspect(sample)
    assert r["first_owned_rows_outside_capture_scope"] == {"ticks": 0, "bars": 0}
    assert r["first_owned_source_rows_locally_consistent_when_owner_in_scope"] is True
    assert r["local_tick_query_interval_gaps"] == []


def test_s410_no_gap_claim_when_capture_sample_truncated(sample):
    r = inspect(sample, max_captures=1)
    assert r["inspected_scope"]["truncated"]["captures"] is True
    assert "BOUNDED_INSPECTION_TRUNCATED" in r["blockers"]
    assert "FIRST_OWNING_CAPTURE_NOT_IN_INSPECTED_SCOPE" in r["blockers"]


def test_s410_no_full_row_coverage_when_source_limit_hit(sample):
    r = inspect(sample, max_source_rows=1)
    assert r["inspected_scope"]["truncated"]["ticks"] is True
    assert "BOUNDED_INSPECTION_TRUNCATED" in r["blockers"]


def test_s409_capture_self_hash_tamper_detected(sample):
    with sample[0].begin() as connection:
        connection.execute(captures.update().where(captures.c.capture_id == sample[4]["capture_id"]).values(manifest_json="{}"))
    with pytest.raises(ForwardReceiptReviewError, match="manifest"):
        inspect(sample)


def test_s409_capture_ingested_at_tamper_detected(sample):
    with sample[0].begin() as connection:
        connection.execute(captures.update().where(captures.c.capture_id == sample[4]["capture_id"]).values(ingested_at=NOW-timedelta(seconds=1)))
    with pytest.raises(ForwardReceiptReviewError, match="chronology"):
        inspect(sample)


def test_s410_tick_raw_source_tamper_detected(sample):
    with sample[0].begin() as connection:
        connection.execute(ticks.update().values(raw_time_msc=123))
    with pytest.raises(ForwardReceiptReviewError, match="Tick"):
        inspect(sample)


def test_s410_tick_first_receipt_tamper_detected(sample):
    with sample[0].begin() as connection:
        connection.execute(ticks.update().values(received_at=NOW+timedelta(hours=1)))
    with pytest.raises(ForwardReceiptReviewError, match="receipt"):
        inspect(sample)


def test_s410_bar_source_tamper_detected(sample):
    with sample[0].begin() as connection:
        connection.execute(bars.update().values(close=2.0))
    with pytest.raises(ForwardReceiptReviewError, match="Bar"):
        inspect(sample)


def test_s410_unknown_first_owner_reported_not_repaired(sample):
    with sample[0].begin() as connection:
        connection.execute(ticks.update().values(first_capture_id="f" * 64))
    # Integrity checks operate in read-only mode; a disabled FK can occur in SQLite fixtures.
    r = inspect(sample)
    assert r["first_owned_rows_outside_capture_scope"]["ticks"] == 2
    assert "FIRST_OWNING_CAPTURE_NOT_IN_INSPECTED_SCOPE" in r["blockers"]


def test_s410_observed_query_gap_is_a_blocker(sample):
    end = NOW + timedelta(seconds=30)
    manifest = {
        "schema_version": 1, "epoch_id": sample[1].epoch_id,
        "received_at": end.isoformat(),
        "tick_start": (NOW+timedelta(seconds=15)).isoformat(),
        "tick_end": (NOW+timedelta(seconds=25)).isoformat(),
        "bar_start": NOW.isoformat(), "bar_end": NOW.isoformat(),
        "ticks_sha256": hashed([]), "bars_sha256": hashed([]),
        "tick_count": 0, "bar_count": 0,
    }
    with sample[0].begin() as connection:
        connection.execute(captures.insert().values(
            capture_id=hashed(manifest), epoch_id=sample[1].epoch_id,
            received_at=end, ingested_at=end,
            tick_start=NOW+timedelta(seconds=15), tick_end=NOW+timedelta(seconds=25),
            manifest_json=encoded(manifest),
        ))
    r = inspect(sample, as_of=NOW+timedelta(seconds=31))
    assert len(r["local_tick_query_interval_gaps"]) == 1
    assert "UNQUERIED_TICK_INTERVALS_IN_INSPECTED_SCOPE" in r["blockers"]


def test_s411_self_hash_does_not_attest_broker_receipt(sample):
    r = inspect(sample)
    assert r["broker_receipt_time_independently_attested"] is False
    assert r["broker_source_identity_independently_attested"] is False
    assert r["broker_query_completeness_independently_attested"] is False
    assert r["local_query_coverage_is_not_broker_completeness"] is True


def test_s411_policy_byte_change_is_rejected(sample):
    sample[2].write_bytes(sample[2].read_bytes() + b"\n")
    with pytest.raises(ForwardReceiptReviewError, match="SHA-256"):
        inspect(sample)


def test_s411_altered_stored_epoch_rejected(sample):
    from backend.data_engine.forward_store import epochs
    with sample[0].begin() as connection:
        connection.execute(epochs.update().values(policy_json="{}"))
    with pytest.raises(ForwardReceiptReviewError, match="Stored policy"):
        inspect(sample)


def test_s411_future_source_not_considered_at_cutoff(sample):
    r = inspect(sample, as_of=NOW+timedelta(seconds=1))
    assert r["inspected_scope"]["captures"] == 1
    assert r["inspected_scope"]["first_owned_ticks"] == 1


def test_s411_local_report_has_deterministic_hash(sample):
    a, b = inspect(sample), inspect(sample)
    assert a == b
    assert a["report_sha256"] == hashed({k: v for k, v in a.items() if k != "report_sha256"})


def test_s412_persisted_state_unchanged_after_inspection(sample):
    with sample[0].connect() as connection:
        before = tuple(connection.execute(
            __import__("sqlalchemy").select(table)
        ).all() for table in (captures, ticks, bars))
    inspect(sample)
    with sample[0].connect() as connection:
        after = tuple(connection.execute(
            __import__("sqlalchemy").select(table)
        ).all() for table in (captures, ticks, bars))
    assert before == after


def test_s412_all_queries_select_only(sample):
    statements = []
    def watch(_conn, _cursor, sql, _parameters, _context, _many):
        statements.append(sql.strip().upper())
    event.listen(sample[0], "before_cursor_execute", watch)
    try:
        inspect(sample)
    finally:
        event.remove(sample[0], "before_cursor_execute", watch)
    assert statements and all(x.startswith("SELECT") for x in statements)


def test_s412_recovery_never_authorized(sample):
    r = inspect(sample)
    for key in (
        "collector_restart_authorized", "existing_epoch_rebound",
        "new_epoch_created", "database_modified", "broker_connected",
        "council_invoked", "training_authorized", "execution_authorized",
    ):
        assert r[key] is False
    assert r["final_action"] == "HOLD"
    assert len(r["recovery_prerequisites"]) == 6


def test_s412_old_capture_is_stale(sample):
    r = inspect(sample, as_of=NOW+timedelta(hours=1))
    assert "NO_CURRENT_RECEIPT_IN_INSPECTED_SCOPE" in r["blockers"]


@pytest.mark.parametrize("cap", (0, 1001, True, "3"))
def test_s412_bad_capture_limit_rejected(sample, cap):
    with pytest.raises(ForwardReceiptReviewError, match="Capture bound"):
        inspect(sample, max_captures=cap)


@pytest.mark.parametrize("cap", (0, 10001, True, "3"))
def test_s412_bad_source_limit_rejected(sample, cap):
    with pytest.raises(ForwardReceiptReviewError, match="Source-row bound"):
        inspect(sample, max_source_rows=cap)


def test_s412_cli_requires_readonly_flags():
    with pytest.raises(SystemExit) as exc:
        run(["--epoch-id", "a" * 64])
    assert exc.value.code == 2


def test_s412_cli_invalid_epoch_blocks_before_db(capsys):
    assert run([
        "--read-only-db", "--read-only-evidence", "--epoch-id", "bad",
        "--as-of", NOW.isoformat(), "--policy-file", "absent",
        "--expected-policy-sha256", "b" * 64,
    ]) == 1
    assert "BLOCKED" in capsys.readouterr().out

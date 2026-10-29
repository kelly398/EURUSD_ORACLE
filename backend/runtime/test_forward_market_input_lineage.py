
"""P3-13..16 read-only candidate market inputs; only synthetic store fixtures."""

from datetime import datetime, timedelta, timezone
import math

import pytest
from sqlalchemy import event

from backend.data_engine.forward_store import bars, captures, ticks, hashed
from backend.runtime.forward_market_input_lineage import (
    MarketInputLineageError, inspect_market_input_lineage, run,
)
from backend.runtime.test_forward_source_provenance import _fixture

NOW = datetime(2026, 10, 27, 14, 5, tzinfo=timezone.utc)


@pytest.fixture
def demo(tmp_path):
    engine, policy, latest = _fixture(count=2)
    path = tmp_path / "policy.json"
    path.write_text(policy.model_dump_json(), encoding="utf-8")
    try:
        yield engine, policy, latest, path
    finally:
        engine.dispose()


def inspect(demo, **kwargs):
    engine, policy, latest, path = demo
    return inspect_market_input_lineage(
        engine,
        epoch_id=policy.epoch_id,
        as_of=latest + timedelta(seconds=3),
        policy_file=path,
        **kwargs,
    )


def test_p313_owner_links_are_exposed_as_ids_and_availability(demo):
    report = inspect(demo)
    refs = report["first_owned_receipts"]
    assert refs["captures_inspected"] == 2
    assert refs["ticks_inspected"] == 2
    assert refs["bars_inspected"] == 2
    assert refs["latest_tick"]["first_capture_id"]
    assert len(refs["latest_tick"]["source_id"]) == 64
    assert refs["latest_closed_m5"]["first_capture_id"]
    assert refs["original_api_order_recoverable"] is False


def test_p314_a_past_decision_never_sees_later_receipts(demo):
    report = inspect(
        demo,
        decision_at=datetime(
            2026, 10, 27, 14, 0, 2, tzinfo=timezone.utc
        ),
    )
    refs = report["first_owned_receipts"]
    assert refs["captures_inspected"] == 1
    assert refs["ticks_inspected"] == 1
    assert refs["bars_inspected"] == 1
    assert (
        refs["latest_tick"]["first_local_availability"]
        < report["decision_at"]
    )
    assert (
        report["decision_time_mode"]
        == "RETROSPECTIVE_RECONSTRUCTION_NOT_REGISTERED_SIGNAL"
    )


def test_p315_safe_prices_are_local_stored_values(demo):
    report = inspect(demo)
    market = report["locally_derived_market_values"]

    assert market["bid"] == 1.1
    assert market["ask"] == 1.1001
    assert market["last_closed_m5_close"] == 1.1005
    assert math.isclose(market["spread_price_units"], .0001)
    assert math.isclose(
        market["last_closed_m5_range_price_units"], .002
    )
    assert (
        report["derived_values_status"]
        == "LOCAL_STORED_VALUES_UNATTESTED"
    )


def test_p315_does_not_replay_old_clock_conversion(demo):
    report = inspect(demo)
    assert (
        "council" not in
        report["derived_values_status"].lower()
    )
    assert report["independent_first_availability_attested"] is False
    assert report["historical_utc_certified"] is False


def test_p316_even_complete_local_inputs_remain_hold(demo):
    report = inspect(demo)

    assert report["final_action"] == "HOLD"
    assert report["council_invoked"] is False
    assert report["council_seats_run"] == 0
    assert report["real_forward_council_admission"] is False
    assert report["training_authorized"] is False
    assert report["execution_authorized"] is False
    assert report["collector_started"] is False
    assert report["prospective_prediction_registered"] is False
    assert (
        report["council_input_families_independently_attested"]
        == 0
    )


def test_p314_later_ingestion_of_old_tick_never_leaks(demo):
    with demo[0].begin() as connection:
        connection.execute(
            ticks.update()
            .where(ticks.c.timestamp < NOW)
            .values(
                ingested_at=NOW + timedelta(minutes=1)
            )
        )

    report = inspect(
        demo,
        decision_at=NOW + timedelta(seconds=2),
    )

    assert (
        report["first_owned_receipts"]["ticks_inspected"]
        == 0
    )
    assert report["locally_derived_market_values"] is None
    assert (
        "NO_TICK_FIRST_AVAILABLE_BEFORE_DECISION"
        in report["local_blockers"]
    )


def test_p314_rejects_decision_later_than_snapshot(demo):
    with pytest.raises(
        MarketInputLineageError, match="future"
    ):
        inspect(
            demo,
            decision_at=NOW + timedelta(minutes=1),
        )


def test_p314_rejects_naive_decision(demo):
    with pytest.raises(
        MarketInputLineageError, match="Invalid UTC"
    ):
        inspect(
            demo,
            decision_at=datetime(2026, 10, 27, 14, 5),
        )


def test_p314_rejects_non_utc_decision(demo):
    with pytest.raises(
        MarketInputLineageError, match="Invalid UTC"
    ):
        inspect(
            demo,
            decision_at=NOW.astimezone(
                timezone(timedelta(hours=3))
            ),
        )


def test_p313_tampered_tick_is_withheld(demo):
    with demo[0].begin() as connection:
        connection.execute(
            ticks.update().values(ask=1.5)
        )

    report = inspect(demo)

    assert (
        "TICK_RAW_PROVENANCE_MISMATCH"
        in report["local_blockers"]
    )
    assert report["locally_derived_market_values"] is None


def test_p313_tampered_bar_id_is_withheld(demo):
    with demo[0].begin() as connection:
        connection.execute(
            bars.update()
            .where(
                bars.c.timestamp
                == NOW - timedelta(minutes=5)
            )
            .values(bar_id="e" * 64)
        )

    report = inspect(demo)

    assert (
        "BAR_ID_NOT_DERIVED_FROM_STORED_UTC_OPEN"
        in report["local_blockers"]
    )
    assert report["locally_derived_market_values"] is None


def test_p313_tampered_capture_manifest_withheld(demo):
    with demo[0].begin() as connection:
        connection.execute(
            captures.update().values(manifest_json="{}")
        )

    report = inspect(demo)

    assert (
        "CAPTURE_MANIFEST_DIGEST_INVALID"
        in report["local_blockers"]
    )
    assert report["locally_derived_market_values"] is None


def test_p313_owner_receipt_changed_is_withheld(demo):
    with demo[0].begin() as connection:
        connection.execute(
            ticks.update().values(
                received_at=NOW - timedelta(seconds=1)
            )
        )

    report = inspect(demo)

    assert (
        "SOURCE_FIRST_CAPTURE_RECEIPT_MISMATCH"
        in report["local_blockers"]
    )
    assert report["locally_derived_market_values"] is None


def test_p313_missing_policy_is_not_recreated(demo, tmp_path):
    other = tmp_path / "missing.json"

    report = inspect_market_input_lineage(
        demo[0],
        epoch_id=demo[1].epoch_id,
        as_of=demo[2] + timedelta(seconds=2),
        policy_file=other,
    )

    assert (
        "MATCHING_EPOCH_POLICY_COPY_NOT_ESTABLISHED"
        in report["local_blockers"]
    )
    assert not other.exists()
    assert report["final_action"] == "HOLD"


def test_p313_changed_policy_expected_digest_is_flagged(demo):
    report = inspect(
        demo,
        expected_policy_sha256="a" * 64,
    )

    assert (
        "POLICY_COPY_CHANGED_FROM_OBSERVED_SHA256"
        in report["local_blockers"]
    )
    assert report["final_action"] == "HOLD"


def test_p314_timing_stale_at_late_decision(demo):
    report = inspect_market_input_lineage(
        demo[0],
        epoch_id=demo[1].epoch_id,
        as_of=NOW + timedelta(minutes=30),
        policy_file=demo[3],
    )

    assert (
        "TICK_NOT_CURRENT_AT_DECISION"
        in report["local_blockers"]
    )
    assert (
        "M5_NOT_LAST_CLOSED_AT_DECISION"
        in report["local_blockers"]
    )
    assert (
        "CAPTURE_NOT_CURRENT_AT_DECISION"
        in report["local_blockers"]
    )
    assert report["final_action"] == "HOLD"


def test_p313_source_sample_truncation_holds_features(demo):
    report = inspect(
        demo,
        max_rows=1,
        max_captures=1,
    )

    assert all(
        report["first_owned_receipts"][
            "sample_truncated"
        ].values()
    )
    assert (
        "BOUNDED_SAMPLE_INCOMPLETE"
        in report["local_blockers"]
    )
    assert report["locally_derived_market_values"] is None


def test_p316_fixed_cutoff_is_deterministic(demo):
    first, second = inspect(demo), inspect(demo)

    assert first == second
    assert first["report_sha256"] == hashed({
        key: value
        for key, value in first.items()
        if key != "report_sha256"
    })


def test_p316_database_sql_is_select_only(demo):
    commands = []

    def watch(
        _conn, _cursor, statement,
        _params, _ctx, _many
    ):
        commands.append(
            statement.strip().upper()
        )

    event.listen(
        demo[0], "before_cursor_execute", watch
    )

    try:
        inspect(demo)
    finally:
        event.remove(
            demo[0], "before_cursor_execute", watch
        )

    assert commands
    assert all(
        value.startswith("SELECT")
        for value in commands
    )


@pytest.mark.parametrize(
    "bad",
    [0, 5001, True, "100", 0.5],
)
def test_p316_rejects_unsafe_source_limit(demo, bad):
    with pytest.raises(
        MarketInputLineageError, match="bounds"
    ):
        inspect(demo, max_rows=bad)


def test_p314_rejects_other_epoch(demo):
    with pytest.raises(
        MarketInputLineageError, match="not available"
    ):
        inspect_market_input_lineage(
            demo[0],
            epoch_id="f" * 64,
            as_of=demo[2] + timedelta(seconds=3),
        )


def test_p316_cli_requires_read_only():
    with pytest.raises(SystemExit) as error:
        run([
            "--epoch-id",
            "a" * 64,
            "--as-of",
            NOW.isoformat(),
        ])
    assert error.value.code == 2


def test_p316_bad_cli_epoch_fails_before_db(capsys):
    result = run([
        "--read-only-db",
        "--epoch-id",
        "BAD",
        "--as-of",
        NOW.isoformat(),
    ])

    assert result == 1
    assert (
        "LINEAGE BLOCKED"
        in capsys.readouterr().out
    )


def test_p314_epoch_not_recorded_before_decision(demo):
    with pytest.raises(
        MarketInputLineageError, match="not available"
    ):
        inspect(
            demo,
            decision_at=datetime(
                2026, 10, 27, 13, 59,
                tzinfo=timezone.utc,
            ),
        )


def test_p314_bar_first_ingested_after_decision_hidden(demo):
    with demo[0].begin() as connection:
        connection.execute(
            bars.update()
            .where(
                bars.c.timestamp
                == NOW - timedelta(minutes=5)
            )
            .values(
                ingested_at=NOW + timedelta(minutes=1)
            )
        )

    result = inspect(
        demo,
        decision_at=NOW + timedelta(seconds=2),
    )

    assert (
        result["first_owned_receipts"]["bars_inspected"]
        == 1
    )
    assert (
        result["first_owned_receipts"][
            "latest_closed_m5"
        ]["event_time"]
        == (NOW - timedelta(minutes=10)).isoformat()
    )
    assert (
        "M5_NOT_LAST_CLOSED_AT_DECISION"
        in result["local_blockers"]
    )


def test_p316_cli_invalid_digest_rejected_without_db(capsys):
    result = run([
        "--read-only-db",
        "--epoch-id",
        "a" * 64,
        "--as-of",
        NOW.isoformat(),
        "--expected-policy-sha256",
        "NOT_A_DIGEST",
    ])

    assert result == 1
    assert (
        "LINEAGE BLOCKED"
        in capsys.readouterr().out
    )

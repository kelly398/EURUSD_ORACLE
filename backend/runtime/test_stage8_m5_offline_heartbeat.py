
"""P8-01..04 synthetic M5 identity, offline queue, idempotency and lifecycle.

All fixture creation is isolated to tmp_path. These tests do not connect to
providers, reconstruct Oct27 history, run agents or register predictions.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime.stage8_m5_offline_heartbeat import (
    EPOCH, POLICY_SHA256, STATES, TRANSITIONS, OfflineHeartbeatError,
    _digest, _utc, assess_stage8_heartbeat, run, transition_state,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "docs/clock_profiles/hfm_forward_20261027.json"
ASOF = "2026-10-30T12:00:00Z"
SOURCE_SHA = sha256(b"UNATTESTED_SOURCE_BYTES").hexdigest()
SESSION_SHA = sha256(b"SYNTHETIC_SESSION_CALENDAR").hexdigest()


def stamp(minutes=0, seconds=0):
    return (
        datetime(2026, 10, 30, 10, tzinfo=timezone.utc)
        + timedelta(minutes=minutes, seconds=seconds)
    ).isoformat()


def save(path, obj):
    path.write_text(
        json.dumps(
            obj,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def envelope(event_id="m5_01", minute=0, *, source_sha=SOURCE_SHA):
    return {
        "event_id": event_id,
        "epoch_id": EPOCH,
        "symbol": "EURUSD",
        "timeframe": "M5",
        "bar_open_at": stamp(minute - 5),
        "bar_close_at": stamp(minute),
        "version_id": "V1",
        "source_digest": source_sha,
        "source_admission_claim": "SYNTHETIC_FIXTURE_ONLY",
        "first_available_at": stamp(minute, 1),
        "received_at": stamp(minute, 3),
        "watermark_at": stamp(minute, 2),
        "clock_uncertainty_ms": "50.000",
        "session_context": {
            "session_id": "SYNTHETIC_SESSION_01",
            "session_state": "OPEN",
            "holiday_state": "CLEAR",
            "published_at": stamp(-2),
            "first_available_at": stamp(-1),
            "source_digest": SESSION_SHA,
        },
        "candle_relative_path": "candles/" + event_id + ".json",
        "candle_sha256": "0" * 64,
    }


def candle(envelope_, *, final=True, ticks=100):
    return {
        "symbol": "EURUSD",
        "timeframe": "M5",
        "bar_open_at": envelope_["bar_open_at"],
        "bar_close_at": envelope_["bar_close_at"],
        "is_final": final,
        "open": "1.0800",
        "high": "1.0850",
        "low": "1.0790",
        "close": "1.0830",
        "tick_count": ticks,
        "source_digest": envelope_["source_digest"],
    }


def fixture(tmp_path, *items, initial=None, mode="SYNTHETIC_OFFLINE"):
    root = tmp_path / "SYNTHETIC_UNATTESTED_M5"
    (root / "candles").mkdir(parents=True)

    packet = {
        "schema_version": 1,
        "historical_epoch_id": EPOCH,
        "packet_id": "M5_OFFLINE_P8_B1",
        "as_of": ASOF,
        "mode": mode,
        "initial_watermark_at": initial,
        "envelopes": list(items),
    }

    for entry in packet["envelopes"]:
        payload = json.dumps(
            candle(entry),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        target = root / entry["candle_relative_path"]
        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        target.write_bytes(payload)
        entry["candle_sha256"] = sha256(
            payload
        ).hexdigest()

    index = tmp_path / "m5_offline_index.json"
    save(index, packet)

    return root, index, packet


def update(index, packet, row, **changes):
    packet["envelopes"][row].update(changes)
    save(index, packet)


def change_candle(root, index, packet, row, **changes):
    entry = packet["envelopes"][row]
    target = root / entry["candle_relative_path"]

    obj = json.loads(
        target.read_text("utf-8")
    )
    obj.update(changes)

    data = json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    target.write_bytes(data)
    entry["candle_sha256"] = sha256(data).hexdigest()
    save(index, packet)


def assess(index=None, root=None, *, prior=None, asof=ASOF, policy=POLICY):
    return assess_stage8_heartbeat(
        epoch_id=EPOCH,
        as_of=asof,
        policy_file=policy,
        fixture_index=index,
        fixture_root=root,
        prior_receipt=prior,
    )


def first(result):
    return result["cycle_records"][0]


def prior_file(tmp_path, result):
    payload = {
        "schema_version": 1,
        "historical_epoch_id": EPOCH,
        "packet_id": "SYNTHETIC_PRIOR_RECEIPT",
        "as_of": ASOF,
        "cycle_receipts": [
            {
                "decision_key": item["decision_key"],
                "envelope_fingerprint": item["envelope_fingerprint"],
                "cycle_receipt_sha256": item["cycle_receipt_sha256"],
            }
            for item in result["cycle_records"]
        ],
    }

    path = tmp_path / "prior_receipts.json"
    save(path, payload)

    return path, payload


# P8-01: completed M5 identity, original bytes, watermark.

def test_p801_no_packet_never_claims_admitted_bar():
    result = assess()
    assert result["source_mode"] == "NOT_SUPPLIED"
    assert result["input_envelopes_count"] == 0
    assert result["unique_decision_keys"] == 0
    assert result["independently_admitted_research_cycles"] == 0
    assert result["final_action"] == "HOLD"


def test_p801_synthetic_completed_m5_has_deterministic_ids(tmp_path):
    root, index, _ = fixture(tmp_path, envelope())
    a, b = assess(index, root), assess(index, root)

    assert a == b
    assert a["unique_decision_keys"] == 1
    assert len(first(a)["decision_key"]) == 64
    assert len(first(a)["bar_identity"]) == 64
    assert len(first(a)["envelope_fingerprint"]) == 64
    assert a["historical_policy_sha256"] == POLICY_SHA256


def test_p801_valid_offline_bar_still_held_not_dispatched(tmp_path):
    root, index, _ = fixture(tmp_path, envelope())
    report = assess(index, root)

    assert first(report)["state_history"] == [
        "RECEIVED", "VALIDATED", "HELD"
    ]
    assert first(report)["locally_validated_synthetic_candidate"] is True
    assert first(report)["independently_admitted"] is False
    assert first(report)["prediction_registered"] is False
    assert report["locally_validated_synthetic_candidates"] == 1
    assert report["independently_admitted_research_cycles"] == 0


def test_p801_source_version_changes_identity_not_decision_key(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())
    before = first(assess(index, root))

    update(index, packet, 0, version_id="V2")
    after = first(assess(index, root))

    assert before["decision_key"] == after["decision_key"]
    assert before["bar_identity"] != after["bar_identity"]


def test_p801_payload_bytes_change_bar_identity_even_if_rehashed(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())
    baseline = first(assess(index, root))

    change_candle(
        root, index, packet, 0,
        close="1.0840",
    )
    current = first(assess(index, root))

    assert current["decision_key"] == baseline["decision_key"]
    assert current["bar_identity"] != baseline["bar_identity"]


def test_p801_partial_m5_bar_quarantined(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())
    change_candle(
        root, index, packet, 0,
        is_final=False,
    )

    result = assess(index, root)

    assert "PARTIAL_M5_CANDLE" in first(result)["terminal_reasons"]
    assert first(result)["state_history"] == ["RECEIVED", "HELD"]
    assert len(result["dead_letter_candidates"]) == 1


def test_p801_zero_tick_bar_quarantined(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())
    change_candle(
        root, index, packet, 0,
        tick_count=0,
    )

    assert "ZERO_TICK_M5_CANDLE" in first(
        assess(index, root)
    )["terminal_reasons"]


@pytest.mark.parametrize("bad", [
    "2026-10-30T09:59:00Z",
    "2026-10-30T10:01:00Z",
])
def test_p801_unaligned_or_wrong_duration_rejected(tmp_path, bad):
    root, index, packet = fixture(tmp_path, envelope())
    update(
        index, packet, 0,
        bar_close_at=bad,
    )

    with pytest.raises(
        OfflineHeartbeatError,
        match="M5 close/open alignment",
    ):
        assess(index, root)


def test_p801_mismatched_candle_envelope_rejected(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())

    change_candle(
        root, index, packet, 0,
        symbol="GBPUSD",
    )

    with pytest.raises(
        OfflineHeartbeatError,
        match="identity or lineage",
    ):
        assess(index, root)


def test_p801_incoherent_ohlc_rejected(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())

    change_candle(
        root, index, packet, 0,
        low="1.0900",
    )

    with pytest.raises(
        OfflineHeartbeatError,
        match="OHLC",
    ):
        assess(index, root)


def test_p801_candle_sha_tamper_rejected(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())

    (
        root / packet["envelopes"][0]["candle_relative_path"]
    ).write_bytes(b"TAMPERED")

    with pytest.raises(
        OfflineHeartbeatError,
        match="SHA-256 mismatch",
    ):
        assess(index, root)


def test_p801_initial_watermark_contiguous_admits_local_candidate(tmp_path):
    root, index, _ = fixture(
        tmp_path,
        envelope(),
        initial=stamp(-5),
    )

    assert first(
        assess(index, root)
    )["locally_validated_synthetic_candidate"]


def test_p801_initial_watermark_same_close_quarantined(tmp_path):
    root, index, _ = fixture(
        tmp_path,
        envelope(),
        initial=stamp(0),
    )

    assert "AT_OR_BEHIND_M5_WATERMARK" in first(
        assess(index, root)
    )["terminal_reasons"]


# P8-02: read-only synthetic queue with freshness, time, holiday/session.

def test_p802_no_real_source_admission_even_for_valid_synthetic(tmp_path):
    root, index, _ = fixture(tmp_path, envelope())
    result = assess(index, root)

    assert result["independent_external_gates_verified"] == 0
    assert result["independent_source_families_verified"] == 0
    assert result["cycle_dispatch_enabled"] is False
    assert result["forecast_registration_enabled"] is False
    assert result["final_action"] == "HOLD"


def test_p802_archived_local_candidate_stays_quarantined(tmp_path):
    root, index, _ = fixture(
        tmp_path,
        envelope(),
        mode="ARCHIVED_OFFLINE_UNATTESTED",
    )

    result = assess(index, root)

    assert "ARCHIVED_PROVENANCE_NOT_INDEPENDENTLY_ADMITTED" in first(
        result
    )["terminal_reasons"]

    assert result["locally_validated_synthetic_candidates"] == 0


def test_p802_stale_delivery_expires(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())

    update(
        index, packet, 0,
        received_at=stamp(2),
    )

    result = assess(index, root)

    assert "STALE_M5_DELIVERY" in first(result)["terminal_reasons"]
    assert first(result)["state_history"] == ["RECEIVED", "EXPIRED"]


@pytest.mark.parametrize("uncertainty", [
    "500.001",
    "99999",
])
def test_p802_clock_uncertainty_breach_quarantined(tmp_path, uncertainty):
    root, index, packet = fixture(tmp_path, envelope())
    update(
        index, packet, 0,
        clock_uncertainty_ms=uncertainty,
    )

    assert "CLOCK_UNCERTAINTY_ABOVE_DESIGN_CEILING" in first(
        assess(index, root)
    )["terminal_reasons"]


def test_p802_clock_uncertainty_boundary_is_only_design_candidate(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())
    update(
        index, packet, 0,
        clock_uncertainty_ms="500.000",
    )

    assert first(
        assess(index, root)
    )["locally_validated_synthetic_candidate"]


@pytest.mark.parametrize("session", [
    "CLOSED",
    "UNKNOWN",
])
def test_p802_closed_or_unknown_session_quarantined(tmp_path, session):
    root, index, packet = fixture(tmp_path, envelope())

    packet["envelopes"][0]["session_context"]["session_state"] = session
    save(index, packet)

    assert "SESSION_NOT_VERIFIABLY_OPEN" in first(
        assess(index, root)
    )["terminal_reasons"]


@pytest.mark.parametrize("holiday", [
    "HOLIDAY",
    "UNKNOWN",
])
def test_p802_holiday_or_unknown_calendar_quarantined(tmp_path, holiday):
    root, index, packet = fixture(tmp_path, envelope())

    packet["envelopes"][0]["session_context"]["holiday_state"] = holiday
    save(index, packet)

    assert "HOLIDAY_CALENDAR_NOT_CLEAR" in first(
        assess(index, root)
    )["terminal_reasons"]


def test_p802_future_session_information_rejected(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())

    packet["envelopes"][0]["session_context"][
        "first_available_at"
    ] = stamp(0, 10)
    save(index, packet)

    with pytest.raises(
        OfflineHeartbeatError,
        match="metadata not available",
    ):
        assess(index, root)


def test_p802_watermark_before_bar_close_rejected(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())

    update(
        index, packet, 0,
        watermark_at=stamp(-1),
    )

    with pytest.raises(
        OfflineHeartbeatError,
        match="chronology",
    ):
        assess(index, root)


def test_p802_pre_close_receipt_rejected(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())

    update(
        index, packet, 0,
        received_at=stamp(-1),
    )

    with pytest.raises(
        OfflineHeartbeatError,
        match="chronology",
    ):
        assess(index, root)


def test_p802_source_self_admission_claim_refused(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())

    update(
        index, packet, 0,
        source_admission_claim="INDEPENDENTLY_APPROVED",
    )

    with pytest.raises(
        OfflineHeartbeatError,
        match="admission refused",
    ):
        assess(index, root)


def test_p802_unsupported_source_mode_rejected(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())

    packet["mode"] = "LIVE_COLLECTOR"
    save(index, packet)

    with pytest.raises(
        OfflineHeartbeatError,
        match="offline fixture modes",
    ):
        assess(index, root)


def test_p802_empty_local_fixture_not_source_admission(tmp_path):
    root, index, _ = fixture(tmp_path)

    result = assess(index, root)

    assert result["source_mode"] == "SYNTHETIC_OFFLINE"
    assert result["unique_decision_keys"] == 0
    assert result["locally_validated_synthetic_candidates"] == 0


# P8-03: idempotency, retry, conflicting revisions and dead letters.

def test_p803_identical_redelivery_yields_one_logical_cycle(tmp_path):
    a = envelope("m5_01")
    root, index, packet = fixture(tmp_path, a)

    packet["envelopes"].append(
        deepcopy(packet["envelopes"][0])
    )
    save(index, packet)

    result = assess(index, root)

    assert result["input_envelopes_count"] == 2
    assert result["unique_decision_keys"] == 1
    assert result["duplicate_deliveries_suppressed"] == 1
    assert len(result["cycle_records"]) == 1
    assert first(result)["delivery_count"] == 2


def test_p803_redelivery_with_new_event_id_same_content_suppressed(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())

    duplicate = deepcopy(
        packet["envelopes"][0]
    )
    duplicate["event_id"] = "delivery_retry_01"

    packet["envelopes"].append(duplicate)
    save(index, packet)

    result = assess(index, root)

    assert result["duplicate_deliveries_suppressed"] == 1
    assert result["unique_decision_keys"] == 1


def test_p803_changed_receipt_timestamp_on_retry_is_conflict(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())

    duplicate = deepcopy(
        packet["envelopes"][0]
    )
    duplicate["event_id"] = "retry_with_changed_receipt"
    duplicate["received_at"] = stamp(0, 4)

    packet["envelopes"].append(duplicate)
    save(index, packet)

    result = assess(index, root)

    assert "CONFLICTING_M5_REVISION_OR_RETRY" in first(
        result
    )["terminal_reasons"]

    assert result["locally_validated_synthetic_candidates"] == 0


def test_p803_same_close_changed_version_quarantines_all(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())

    second = deepcopy(
        packet["envelopes"][0]
    )
    second["event_id"] = "revised_01"
    second["version_id"] = "V2"

    packet["envelopes"].append(second)
    save(index, packet)

    result = assess(index, root)

    assert result["unique_decision_keys"] == 1
    assert "CONFLICTING_M5_REVISION_OR_RETRY" in first(
        result
    )["terminal_reasons"]
    assert len(result["dead_letter_candidates"]) == 1


def test_p803_same_close_changed_source_digest_quarantined(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())

    second = deepcopy(
        packet["envelopes"][0]
    )
    second["event_id"] = "provider_correction"
    second["source_digest"] = "f" * 64
    second["candle_relative_path"] = "candles/revised.json"

    payload = candle(second)
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    (
        root / second["candle_relative_path"]
    ).write_bytes(raw)

    second["candle_sha256"] = sha256(raw).hexdigest()
    packet["envelopes"].append(second)
    save(index, packet)

    result = assess(index, root)

    assert "CONFLICTING_M5_REVISION_OR_RETRY" in first(
        result
    )["terminal_reasons"]


def test_p803_missed_bar_is_dead_letter_not_repaired(tmp_path):
    root, index, _ = fixture(
        tmp_path,
        envelope("bar_1000", 0),
        envelope("bar_1010", 10),
        initial=stamp(-5),
    )

    report = assess(index, root)

    assert report["unique_decision_keys"] == 2
    assert report["locally_validated_synthetic_candidates"] == 1
    assert any(
        "MISSING_M5_BAR_BEFORE_DELIVERY" in d["reason_codes"]
        for d in report["dead_letter_candidates"]
    )
    assert report["watermark"]["last_locally_validated_at"] == stamp(0)


def test_p803_missing_first_bar_from_seed_watermark_is_quarantined(tmp_path):
    root, index, _ = fixture(
        tmp_path,
        envelope(),
        initial=stamp(-10),
    )

    assert "MISSING_M5_BAR_BEFORE_DELIVERY" in first(
        assess(index, root)
    )["terminal_reasons"]


def test_p803_contiguous_5min_offline_batch_has_two_candidates(tmp_path):
    root, index, _ = fixture(
        tmp_path,
        envelope("bar_1000", 0),
        envelope("bar_1005", 5),
        initial=stamp(-5),
    )

    report = assess(index, root)

    assert report["locally_validated_synthetic_candidates"] == 2
    assert report["watermark"]["last_locally_validated_at"] == stamp(5)
    assert all(
        c["terminal_state"] == "HELD"
        for c in report["cycle_records"]
    )


def test_p803_prior_local_receipt_exact_replay_never_registers(tmp_path):
    root, index, _ = fixture(tmp_path, envelope())

    original = assess(index, root)
    prior, _ = prior_file(tmp_path, original)

    replay = assess(index, root, prior=prior)

    assert replay["prior_local_receipt_replays"] == 1
    assert first(replay)["prior_local_receipt_status"] == (
        "LOCAL_REPLAY_MATCH_UNATTESTED"
    )
    assert not first(replay)["prediction_registered"]


def test_p803_prior_receipt_different_hash_quarantines(tmp_path):
    root, index, _ = fixture(tmp_path, envelope())

    prior, doc = prior_file(
        tmp_path,
        assess(index, root),
    )
    doc["cycle_receipts"][0]["cycle_receipt_sha256"] = "f" * 64
    save(prior, doc)

    report = assess(index, root, prior=prior)

    assert "PRIOR_LOCAL_RECEIPT_MISMATCH" in first(
        report
    )["terminal_reasons"]

    assert report["locally_validated_synthetic_candidates"] == 0
    assert report["dead_letter_candidates"]


def test_p803_prior_receipt_different_fingerprint_quarantines(tmp_path):
    root, index, _ = fixture(tmp_path, envelope())

    prior, doc = prior_file(
        tmp_path,
        assess(index, root),
    )
    doc["cycle_receipts"][0]["envelope_fingerprint"] = "f" * 64
    save(prior, doc)

    assert first(
        assess(index, root, prior=prior)
    )["terminal_state"] == "HELD"

    assert "PRIOR_LOCAL_RECEIPT_MISMATCH" in first(
        assess(index, root, prior=prior)
    )["terminal_reasons"]


def test_p803_duplicate_prior_decision_key_refused(tmp_path):
    root, index, _ = fixture(tmp_path, envelope())

    prior, doc = prior_file(
        tmp_path,
        assess(index, root),
    )
    doc["cycle_receipts"].append(
        deepcopy(doc["cycle_receipts"][0])
    )
    save(prior, doc)

    with pytest.raises(
        OfflineHeartbeatError,
        match="Duplicate prior",
    ):
        assess(index, root, prior=prior)


def test_p803_unrecognized_prior_receipt_not_an_external_permission(tmp_path):
    root, index, _ = fixture(tmp_path, envelope())

    other = assess(index, root)
    prior, doc = prior_file(tmp_path, other)

    doc["cycle_receipts"][0]["decision_key"] = "f" * 64
    save(prior, doc)

    assert first(
        assess(index, root, prior=prior)
    )["prior_local_receipt_status"] == "NOT_SUPPLIED"


def test_p803_out_of_order_behind_watermark_quarantined(tmp_path):
    root, index, packet = fixture(
        tmp_path,
        envelope("older", 0),
        envelope("newer", 5),
    )
    packet["envelopes"][0]["received_at"] = stamp(5, 8)
    save(index, packet)

    report = assess(index, root)

    older = next(
        c
        for c in report["cycle_records"]
        if c["bar_close_at"] == stamp(0)
    )

    assert "AT_OR_BEHIND_M5_WATERMARK" in older["terminal_reasons"]


def test_p803_shuffled_index_order_does_not_change_cycles(tmp_path):
    root, index, packet = fixture(
        tmp_path,
        envelope("one", 0),
        envelope("two", 5),
        initial=stamp(-5),
    )

    baseline = assess(index, root)
    packet["envelopes"].reverse()
    save(index, packet)

    current = assess(index, root)

    assert current["cycle_records"] == baseline["cycle_records"]
    assert current["watermark"] == baseline["watermark"]


# P8-04: complete legal state machine without operational execution.

def test_p804_nine_canonical_states_and_terminals():
    assert len(STATES) == 9
    assert set(STATES) == set(TRANSITIONS)
    assert all(
        TRANSITIONS[terminal] == ()
        for terminal in (
            "REGISTERED", "HELD", "EXPIRED", "FAILED"
        )
    )


@pytest.mark.parametrize("from_state,to_state", [
    ("RECEIVED", "VALIDATED"),
    ("VALIDATED", "CONTEXT_READY"),
    ("CONTEXT_READY", "ANALYSIS_RUNNING"),
    ("ANALYSIS_RUNNING", "GOVERNANCE"),
    ("GOVERNANCE", "REGISTERED"),
    ("GOVERNANCE", "HELD"),
    ("RECEIVED", "FAILED"),
    ("RECEIVED", "EXPIRED"),
    ("ANALYSIS_RUNNING", "FAILED"),
])
def test_p804_legal_transition_contract_only(from_state, to_state):
    assert transition_state(from_state, to_state) == to_state


@pytest.mark.parametrize("from_state,to_state", [
    ("RECEIVED", "REGISTERED"),
    ("VALIDATED", "REGISTERED"),
    ("HELD", "VALIDATED"),
    ("FAILED", "RECEIVED"),
    ("REGISTERED", "HELD"),
    ("EXPIRED", "RECEIVED"),
    ("GOVERNANCE", "CONTEXT_READY"),
    ("UNKNOWN", "HELD"),
])
def test_p804_unsupported_or_terminal_transition_refused(
    from_state, to_state
):
    with pytest.raises(
        OfflineHeartbeatError,
        match="Illegal or terminal",
    ):
        transition_state(from_state, to_state)


def test_p804_cycle_never_runs_beyond_validated_in_batch1(tmp_path):
    root, index, _ = fixture(tmp_path, envelope())
    report = assess(index, root)

    for cycle in report["cycle_records"]:
        assert "ANALYSIS_RUNNING" not in cycle["state_history"]
        assert "REGISTERED" not in cycle["state_history"]
        assert cycle["terminal_state"] in ("HELD", "EXPIRED")


def test_p804_no_file_network_broker_model_order_side_effects(tmp_path):
    root, index, _ = fixture(tmp_path, envelope())
    report = assess(index, root)

    for key in (
        "files_written",
        "network_accessed",
        "broker_connected",
        "collector_started",
        "council_invoked",
        "model_loaded",
        "training_authorized",
        "settlement_invoked",
        "paper_orders_authorized",
        "live_orders_authorized",
        "future_epoch_created",
        "operator_approval_granted",
        "historical_epoch_rebound",
    ):
        assert report[key] is False

    assert report["read_only"] is True


def test_p804_report_reproducible_and_content_addressed(tmp_path):
    root, index, _ = fixture(tmp_path, envelope())
    result = assess(index, root)

    assert result == assess(index, root)

    assert result["report_sha256"] == _digest({
        k: v
        for k, v in result.items()
        if k != "report_sha256"
    })


def test_p804_no_raw_candles_or_prices_exported(tmp_path):
    root, index, _ = fixture(tmp_path, envelope())
    data = json.dumps(assess(index, root))

    assert '"open": "1.0800"' not in data
    assert "candles/m5_01.json" not in data
    assert '"close": "1.0830"' not in data


def test_p804_cli_no_fixture_reports_hold(capsys):
    code = run([
        "--read-only-fixtures",
        "--read-only-heartbeat",
        "--epoch-id", EPOCH,
        "--as-of", ASOF,
        "--policy-file", str(POLICY),
    ])

    assert code == 0

    output = capsys.readouterr().out
    assert "FINAL=HOLD" in output
    assert "Input envelopes: 0" in output
    assert "Council cycles dispatched: 0" in output


def test_p804_cli_json_with_fixture(capsys, tmp_path):
    root, index, _ = fixture(tmp_path, envelope())

    assert run([
        "--read-only-fixtures",
        "--read-only-heartbeat",
        "--epoch-id", EPOCH,
        "--as-of", ASOF,
        "--policy-file", str(POLICY),
        "--fixture-index", str(index),
        "--fixture-root", str(root),
        "--json",
    ]) == 0

    report = json.loads(
        capsys.readouterr().out
    )
    assert report["unique_decision_keys"] == 1
    assert report["final_action"] == "HOLD"


def test_p804_cli_readonly_flags_are_mandatory():
    with pytest.raises(SystemExit) as err:
        run([
            "--epoch-id", EPOCH,
            "--as-of", ASOF,
        ])

    assert err.value.code == 2


def test_p804_cli_future_cutoff_blocked(capsys):
    future = (
        datetime.now(timezone.utc) + timedelta(days=1)
    ).isoformat()

    assert run([
        "--read-only-fixtures",
        "--read-only-heartbeat",
        "--epoch-id", EPOCH,
        "--as-of", future,
        "--policy-file", str(POLICY),
    ]) == 1

    assert "BLOCKED" in capsys.readouterr().out


def test_p804_policy_tamper_refused(tmp_path):
    bad = tmp_path / "changed_policy.json"
    bad.write_bytes(POLICY.read_bytes() + b" ")

    with pytest.raises(
        OfflineHeartbeatError,
        match="policy SHA-256 mismatch",
    ):
        assess(policy=bad)


def test_p804_wrong_historical_epoch_refused():
    with pytest.raises(
        OfflineHeartbeatError,
        match="rebinding refused",
    ):
        assess_stage8_heartbeat(
            epoch_id="f" * 64,
            as_of=ASOF,
            policy_file=POLICY,
        )


def test_p804_wrong_index_historical_epoch_refused(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())

    packet["historical_epoch_id"] = "f" * 64
    save(index, packet)

    with pytest.raises(
        OfflineHeartbeatError,
        match="historical epoch",
    ):
        assess(index, root)


def test_p804_duplicate_json_key_refused(tmp_path):
    root, index, _ = fixture(tmp_path, envelope())

    index.write_text(
        '{"schema_version":1,"schema_version":1}',
        encoding="utf-8",
    )

    with pytest.raises(
        OfflineHeartbeatError,
        match="Duplicate JSON",
    ):
        assess(index, root)


@pytest.mark.parametrize("unsafe", [
    "../outside.json",
    "/etc/shadow",
    "C:/temp/bar.json",
    "x//y.json",
    "x/../y.json",
    "./bar.json",
    "x\\bar.json",
    "account_number_012.json",
    "user@example.com",
])
def test_p804_unsafe_fixture_paths_refused(tmp_path, unsafe):
    root, index, packet = fixture(tmp_path, envelope())

    update(
        index, packet, 0,
        candle_relative_path=unsafe,
    )

    with pytest.raises(
        OfflineHeartbeatError,
        match="Unsafe or sensitive",
    ):
        assess(index, root)


def test_p804_symlink_candle_refused_if_supported(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())

    link = root / "candles" / "alias.json"

    try:
        link.symlink_to(
            root / "candles" / "m5_01.json"
        )
    except (OSError, NotImplementedError):
        pytest.skip(
            "Symlink unavailable on this Windows host"
        )

    update(
        index, packet, 0,
        candle_relative_path="candles/alias.json",
    )

    with pytest.raises(
        OfflineHeartbeatError,
        match="Symlink",
    ):
        assess(index, root)


def test_p804_evidence_root_without_index_refused(tmp_path):
    with pytest.raises(
        OfflineHeartbeatError,
        match="root without index",
    ):
        assess(root=tmp_path)


def test_p804_index_without_evidence_root_refused(tmp_path):
    root, index, _ = fixture(tmp_path, envelope())

    with pytest.raises(
        OfflineHeartbeatError,
        match="requires root",
    ):
        assess(index=index)


@pytest.mark.parametrize("bad", [
    "2026-10-30T12:00:00",
    "2026-10-30T15:00:00+03:00",
    "2026-10-30T12:00:60Z",
    "2026-11-31T12:00:00Z",
    "bad",
])
def test_p804_strict_zero_offset_utc(bad):
    with pytest.raises(OfflineHeartbeatError):
        _utc(bad)


def test_p804_extra_approval_field_refused(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())

    update(
        index, packet, 0,
        independently_admitted=True,
    )

    with pytest.raises(
        OfflineHeartbeatError,
        match="Strict schema",
    ):
        assess(index, root)


def test_p804_json_prevents_price_as_number_not_string(tmp_path):
    root, index, packet = fixture(tmp_path, envelope())

    change_candle(
        root, index, packet, 0,
        close=1.0830,
    )

    with pytest.raises(
        OfflineHeartbeatError,
        match="Bounded nonnegative decimal",
    ):
        assess(index, root)


# Additional fail-closed queue and replay integrity checks.

def test_p803_reused_event_id_across_two_closes_quarantines_both(tmp_path):
    root, index, packet = fixture(
        tmp_path,
        envelope("bar_1000", 0),
        envelope("bar_1005", 5),
        initial=stamp(-5),
    )

    packet["envelopes"][1]["event_id"] = "bar_1000"
    save(index, packet)

    report = assess(index, root)

    assert report["locally_validated_synthetic_candidates"] == 0
    assert all(
        "REUSED_EVENT_ID_WITH_DIFFERENT_CLAIM" in c["terminal_reasons"]
        for c in report["cycle_records"]
    )


def test_p803_failed_first_bar_blocks_following_offline_bars(tmp_path):
    root, index, packet = fixture(
        tmp_path,
        envelope("bar_1000", 0),
        envelope("bar_1005", 5),
        initial=stamp(-5),
    )

    packet["envelopes"][0]["session_context"][
        "holiday_state"
    ] = "UNKNOWN"
    save(index, packet)

    report = assess(index, root)

    assert report["locally_validated_synthetic_candidates"] == 0
    assert "PREVIOUS_M5_DELIVERY_QUARANTINED" in (
        report["cycle_records"][1]["terminal_reasons"]
    )


def test_p803_prior_receipt_mismatch_blocks_next_contiguous_bar(tmp_path):
    root, index, _ = fixture(
        tmp_path,
        envelope("bar_1000", 0),
        envelope("bar_1005", 5),
        initial=stamp(-5),
    )

    prior, doc = prior_file(
        tmp_path,
        assess(index, root),
    )
    doc["cycle_receipts"][0]["envelope_fingerprint"] = "f" * 64
    save(prior, doc)

    result = assess(index, root, prior=prior)

    assert result["locally_validated_synthetic_candidates"] == 0
    assert any(
        "PREVIOUS_M5_DELIVERY_QUARANTINED" in c["terminal_reasons"]
        for c in result["cycle_records"][1:]
    )

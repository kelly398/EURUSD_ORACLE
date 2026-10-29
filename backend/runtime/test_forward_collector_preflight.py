
"""Phase 3 P3-01..04 preflight tests: pure synthetic receipts; no broker."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest

from backend.data_engine.clock_policy import ClockPolicy, implementation_sha256
from backend.data_engine.forward_store import hashed
from backend.runtime.forward_collector_preflight import (
    CollectorPreflightError, evaluate_collector_preflight, run,
)

T = datetime(2026, 10, 27, 15, 5, tzinfo=timezone.utc)


def receipt(*, age_seconds=5, bound_hash=None, count=2, errors=None):
    ingested = T - timedelta(seconds=age_seconds)
    captures = [dict(ingested_at=ingested.isoformat(), received_at=ingested.isoformat())] * count
    issues = errors or []
    data = {
        "assessment_version": "EURUSD_FORWARD_EVIDENCE_HEALTH_V2",
        "epoch_id": "a" * 64, "as_of": T.isoformat(),
        "captures": captures, "captures_examined": count,
        "bound_clock_implementation_sha256": bound_hash or implementation_sha256(),
        "blockers": issues, "sample_truncated": False,
        "local_evidence_status": "BLOCKED" if issues else "LOCAL_STRUCTURAL_CHECKS_PASS_UNATTESTED",
        "final_action": "HOLD", "execution_authorized": False,
        "training_authorized": False, "real_data_admission": False,
    }
    data["report_sha256"] = hashed(data)
    return data


def policy(tmp_path, *, hash_value=None):
    p = ClockPolicy(
        company_sha256=sha256(b"fake company").hexdigest(),
        server_sha256=sha256(b"fake server").hexdigest(),
        evidence_sha256=sha256(b"fake evidence").hexdigest(),
        implementation_sha256=hash_value or implementation_sha256(),
        valid_from=T-timedelta(days=1), valid_until=T+timedelta(days=15),
    )
    path = tmp_path / "clock.json"
    path.write_text(p.model_dump_json(), encoding="utf-8")
    return path, p


def test_recent_local_receipt_never_admits_capture_or_trade():
    r = evaluate_collector_preflight(receipt())
    assert "CLOCK_POLICY_PATH_NOT_PROVIDED" in r["operator_review_blockers"]
    assert r["collector_started"] is False
    assert r["recovery_authorized"] is False
    assert r["execution_authorized"] is False
    assert r["final_action"] == "HOLD"


def test_recorder_is_not_supervisors_default_mode():
    r = evaluate_collector_preflight(receipt())
    assert "utc_forward_feed" not in r["supervisor_default_workers_without_policy"]
    assert r["forward_mode_requires_explicit_clock_policy"] is True
    assert r["required_forward_worker_module"] == "backend.runtime.capture_forward_feed"
    assert r["forward_worker_process_running"] == "NOT_OBSERVED"


def test_capture_gap_detected_without_claiming_crash():
    r = evaluate_collector_preflight(receipt(age_seconds=3600))
    assert "NO_RECENT_CAPTURE_INGESTION" in r["operator_review_blockers"]
    assert r["collection_stoppage_cause"] == "UNKNOWN"
    assert r["seconds_since_last_capture_at_cutoff"] == 3600


def test_no_captures_is_blocked():
    r = evaluate_collector_preflight(receipt(count=0))
    assert "NO_CAPTURE_AT_INSPECTION_CUTOFF" in r["operator_review_blockers"]


def test_existing_health_blockers_propagate():
    r = evaluate_collector_preflight(receipt(errors=["LATEST_TICK_STALE_OR_FUTURE"]))
    assert "HEALTH:LATEST_TICK_STALE_OR_FUTURE" in r["operator_review_blockers"]


def test_unmatched_current_clock_is_a_blocker():
    r = evaluate_collector_preflight(receipt(bound_hash="0" * 64))
    assert "BOUND_CLOCK_CODE_DIFFERS_FROM_CURRENT" in r["operator_review_blockers"]


def test_same_current_clock_and_matching_policy_other_epoch_is_blocked(tmp_path):
    path, p = policy(tmp_path)
    r = evaluate_collector_preflight(receipt(), policy_path=path)
    assert r["clock_policy"]["epoch_matches_database"] is False
    assert "POLICY_EPOCH_NOT_DATABASE_EPOCH" in r["operator_review_blockers"]
    assert r["clock_policy"]["clock_hash_matches_current"] is True


def test_matching_policy_for_receipt_is_read_only_and_still_hold(tmp_path):
    path, p = policy(tmp_path)
    data = receipt()
    data["epoch_id"] = p.epoch_id
    data["report_sha256"] = hashed({k: v for k, v in data.items() if k != "report_sha256"})
    before = path.read_bytes()
    r = evaluate_collector_preflight(data, policy_path=path)
    assert path.read_bytes() == before
    assert r["clock_policy"]["epoch_matches_database"] is True
    assert r["clock_policy"]["clock_hash_matches_current"] is True
    assert r["clock_policy"]["active_at_cutoff"] is True
    assert r["local_preflight_status"] == "LOCAL_CONTRACT_CHECKS_PASS_UNATTESTED"
    assert r["final_action"] == "HOLD"


def test_policy_with_wrong_clock_hash_is_flagged(tmp_path):
    path, _ = policy(tmp_path, hash_value="f" * 64)
    r = evaluate_collector_preflight(receipt(), policy_path=path)
    assert "POLICY_CLOCK_NOT_CURRENT_CODE" in r["operator_review_blockers"]


def test_missing_policy_path_does_not_crash(tmp_path):
    r = evaluate_collector_preflight(receipt(), policy_path=tmp_path/"missing.json")
    assert "CLOCK_POLICY_UNREADABLE_OR_INVALID" in r["operator_review_blockers"]


def test_invalid_policy_json_does_not_leak_payload(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text("not-a-valid-policy")
    r = evaluate_collector_preflight(receipt(), policy_path=path)
    assert r["clock_policy"]["inspection"] == "UNREADABLE_OR_INVALID"


def test_large_policy_path_is_blocked_before_read(tmp_path):
    path = tmp_path / "big.json"
    path.write_bytes(b"x" * 16385)
    r = evaluate_collector_preflight(receipt(), policy_path=path)
    assert "CLOCK_POLICY_UNREADABLE_OR_INVALID" in r["operator_review_blockers"]


def test_source_receipt_tampering_rejected():
    data = receipt()
    data["captures_examined"] = 9
    with pytest.raises(CollectorPreflightError, match="digest mismatch"):
        evaluate_collector_preflight(data)


def test_forged_execution_authorization_rejected():
    data = receipt()
    data["execution_authorized"] = True
    data["report_sha256"] = hashed({k: v for k, v in data.items() if k != "report_sha256"})
    with pytest.raises(CollectorPreflightError, match="HOLD"):
        evaluate_collector_preflight(data)


def test_invalid_capture_summary_rejected():
    data = receipt()
    data["captures"] = []
    data["report_sha256"] = hashed({k: v for k, v in data.items() if k != "report_sha256"})
    with pytest.raises(CollectorPreflightError, match="summaries inconsistent"):
        evaluate_collector_preflight(data)


def test_future_ingestion_rejected():
    data = receipt(age_seconds=-10)
    with pytest.raises(CollectorPreflightError, match="after inspection cutoff"):
        evaluate_collector_preflight(data)


@pytest.mark.parametrize("value", [0, 59, 86401, True, 3.5, "900"])
def test_invalid_age_threshold_rejected(value):
    with pytest.raises(CollectorPreflightError, match="threshold"):
        evaluate_collector_preflight(receipt(), max_capture_age_seconds=value)


def test_deterministic_report():
    assert evaluate_collector_preflight(receipt()) == evaluate_collector_preflight(receipt())


def test_cli_requires_explicit_read_only_database_flag():
    with pytest.raises(SystemExit) as error:
        run(["--epoch-id", "a" * 64, "--as-of", T.isoformat()])
    assert error.value.code == 2


"""Phase 3 P3-01..04: read-only collector and clock-policy preflight.

No process start, MT5 connection, DB write, policy repair, or trading. Evidence
from local receipts and code is not independent broker attestation.
"""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

from backend.data_engine.clock_policy import ClockPolicy, implementation_sha256
from backend.data_engine.forward_store import encoded
from backend.runtime.market_data_supervisor import configured_workers

VERSION = "EURUSD_P3_COLLECTOR_PREFLIGHT_V1"
HEALTH_VERSION = "EURUSD_FORWARD_EVIDENCE_HEALTH_V2"


class CollectorPreflightError(RuntimeError):
    """Invalid evidence or unsupported preflight request."""


def _digest(value):
    return sha256(encoded(value).encode("utf-8")).hexdigest()


def _stamp(value):
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or result.utcoffset() != timedelta(0):
            raise ValueError
        return result
    except (ValueError, TypeError, AttributeError) as exc:
        raise CollectorPreflightError("Expected explicit UTC ISO timestamp.") from exc


def evaluate_collector_preflight(health, *, policy_path=None,
                                 max_capture_age_seconds=900):
    """Evaluate a content-hashed V2 health report and optional local policy.

    A passing *local* contract never authorizes capture restart or Council use.
    A missing policy is a diagnostic gap, not a reason to create a new epoch.
    """
    if type(max_capture_age_seconds) is not int or not 60 <= max_capture_age_seconds <= 86400:
        raise CollectorPreflightError("Capture age threshold must be 60..86400 seconds.")
    if not isinstance(health, dict) or health.get("assessment_version") != HEALTH_VERSION:
        raise CollectorPreflightError("V2 forward health report required.")
    digest = health.get("report_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or digest != _digest(
        {key: value for key, value in health.items() if key != "report_sha256"}
    ):
        raise CollectorPreflightError("Forward health digest mismatch.")
    if (health.get("final_action") != "HOLD" or
            health.get("execution_authorized") is not False or
            health.get("training_authorized") is not False or
            health.get("real_data_admission") is not False):
        raise CollectorPreflightError("Health HOLD boundary violated.")
    epoch_id = health["epoch_id"]
    if not isinstance(epoch_id, str) or len(epoch_id) != 64 or any(
        ch not in "0123456789abcdef" for ch in epoch_id
    ):
        raise CollectorPreflightError("Malformed epoch ID.")
    cutoff = _stamp(health["as_of"])
    captures = health.get("captures")
    if not isinstance(captures, list) or len(captures) != health.get("captures_examined"):
        raise CollectorPreflightError("Capture summaries inconsistent.")
    for record in captures:
        if not isinstance(record, dict):
            raise CollectorPreflightError("Capture summary malformed.")
    last_ingested = max((_stamp(row["ingested_at"]) for row in captures), default=None)
    last_received = max((_stamp(row["received_at"]) for row in captures), default=None)
    if last_ingested is not None and last_ingested > cutoff:
        raise CollectorPreflightError("Capture ingested after inspection cutoff.")
    age = (cutoff - last_ingested).total_seconds() if last_ingested else None
    blockers = set()
    if not captures:
        blockers.add("NO_CAPTURE_AT_INSPECTION_CUTOFF")
    if age is None or age > max_capture_age_seconds:
        blockers.add("NO_RECENT_CAPTURE_INGESTION")
    if health.get("sample_truncated"):
        blockers.add("CAPTURE_SAMPLE_TRUNCATED")
    if health.get("blockers"):
        if not isinstance(health["blockers"], list):
            raise CollectorPreflightError("Health blockers malformed.")
        blockers.update("HEALTH:" + str(item) for item in health["blockers"])
    if health.get("local_evidence_status") != "LOCAL_STRUCTURAL_CHECKS_PASS_UNATTESTED":
        blockers.add("FORWARD_HEALTH_NOT_PASSING")
    current_hash = implementation_sha256()
    bound_hash = health.get("bound_clock_implementation_sha256")
    if bound_hash != current_hash:
        blockers.add("BOUND_CLOCK_CODE_DIFFERS_FROM_CURRENT")

    policy = {
        "inspection": "NOT_PROVIDED" if policy_path is None else "INSPECTED",
        "epoch_matches_database": None,
        "clock_hash_matches_bound": None,
        "clock_hash_matches_current": None,
        "active_at_cutoff": None,
        "active_now": None,
        "file_sha256": None,
    }
    if policy_path is None:
        blockers.add("CLOCK_POLICY_PATH_NOT_PROVIDED")
    else:
        path = Path(policy_path)
        try:
            if not path.is_file() or path.stat().st_size > 16384:
                raise ValueError("Missing or oversized policy file.")
            payload = path.read_bytes()
            candidate = ClockPolicy.load(path)
        except (OSError, ValueError, RuntimeError):
            policy["inspection"] = "UNREADABLE_OR_INVALID"
            blockers.add("CLOCK_POLICY_UNREADABLE_OR_INVALID")
        else:
            policy.update(
                file_sha256=sha256(payload).hexdigest(),
                epoch_matches_database=(candidate.epoch_id == epoch_id),
                clock_hash_matches_bound=(candidate.implementation_sha256 == bound_hash),
                clock_hash_matches_current=(candidate.implementation_sha256 == current_hash),
                active_at_cutoff=(candidate.valid_from <= cutoff < candidate.valid_until),
                active_now=(candidate.valid_from <= datetime.now(timezone.utc) < candidate.valid_until),
            )
            for field, code in (
                ("epoch_matches_database", "POLICY_EPOCH_NOT_DATABASE_EPOCH"),
                ("clock_hash_matches_bound", "POLICY_CLOCK_NOT_STORED_CLOCK"),
                ("clock_hash_matches_current", "POLICY_CLOCK_NOT_CURRENT_CODE"),
                ("active_at_cutoff", "POLICY_NOT_ACTIVE_AT_CUTOFF"),
                ("active_now", "POLICY_NOT_ACTIVE_NOW"),
            ):
                if policy[field] is False:
                    blockers.add(code)

    # Reading configured_workers() without a policy is harmless and reveals
    # the legacy default. Do not call configured_workers(policy_path): it may
    # reject a stale policy; the preflight deliberately never starts workers.
    legacy = [item.name for item in configured_workers()]
    if "utc_forward_feed" in legacy:
        raise CollectorPreflightError("Supervisor default-mode contract changed.")
    result = {
        "assessment_version": VERSION,
        "epoch_id": epoch_id,
        "as_of": cutoff.isoformat(),
        "source_health_sha256": digest,
        "captures_inspected": len(captures),
        "last_capture_received_at": last_received.isoformat() if last_received else None,
        "last_capture_ingested_at": last_ingested.isoformat() if last_ingested else None,
        "seconds_since_last_capture_at_cutoff": age,
        "local_capture_age_threshold_seconds": max_capture_age_seconds,
        "clock_code_hash_current": current_hash,
        "clock_code_hash_bound": bound_hash,
        "clock_policy": policy,
        "supervisor_default_workers_without_policy": legacy,
        "required_forward_worker_module": "backend.runtime.capture_forward_feed",
        "forward_mode_requires_explicit_clock_policy": True,
        "forward_worker_process_running": "NOT_OBSERVED",
        "mt5_terminal_connected": "NOT_OBSERVED",
        "collection_stoppage_cause": "UNKNOWN",
        "operator_review_blockers": sorted(blockers),
        "local_preflight_status": (
            "LOCAL_CONTRACT_CHECKS_PASS_UNATTESTED" if not blockers else "BLOCKED"
        ),
        "next_evidence": [
            "INSPECT_SUPERVISOR_PROCESS_COMMAND_LINE_READ_ONLY",
            "IDENTIFY_CONFIGURED_CLOCK_POLICY_PATH",
            "INSPECT_SUPERVISOR_AND_RECORDER_EXIT_LOGS",
            "VERIFY_BROKER_CLOCK_AND_FORWARD_SOURCE_INDEPENDENTLY",
        ],
        "policy_file_modified": False,
        "database_modified": False,
        "collector_started": False,
        "recovery_authorized": False,
        "real_data_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
        "final_action": "HOLD",
    }
    result["report_sha256"] = _digest(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-db", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--clock-policy", type=Path)
    parser.add_argument("--captures", type=int, default=32)
    parser.add_argument("--source-limit", type=int, default=50000)
    parser.add_argument("--capture-age-seconds", type=int, default=900)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        from backend.data_engine.forward_evidence_health import (
            _parse_as_of, _require_epoch_id, inspect_forward_evidence,
        )
        from backend.database.connection import engine
        try:
            health = inspect_forward_evidence(
                engine, epoch_id=_require_epoch_id(args.epoch_id),
                as_of=_parse_as_of(args.as_of),
                max_captures=args.captures, max_source_rows=args.source_limit,
            )
        finally:
            engine.dispose()
        report = evaluate_collector_preflight(
            health, policy_path=args.clock_policy,
            max_capture_age_seconds=args.capture_age_seconds,
        )
    except Exception as exc:
        print("FORWARD COLLECTOR PREFLIGHT BLOCKED: " + type(exc).__name__)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Phase 3 forward collector preflight")
        print("Last capture: " + str(report["last_capture_ingested_at"]))
        print("Policy inspection: " + report["clock_policy"]["inspection"])
        print("Supervisor default workers: " + ", ".join(report["supervisor_default_workers_without_policy"]))
        print("Process and broker state: NOT OBSERVED")
        for reason in report["operator_review_blockers"]:
            print("  BLOCKER " + reason)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | COLLECTOR NOT STARTED | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()


"""Stage 4 S4-01..04: observe runtime/launchers/logs against an existing epoch.

Read-only. No account credentials, process command lines or raw logs are returned.
This does not launch or repair the recorder and cannot establish a historic cause
without trustworthy contemporaneous evidence.
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess

from backend.data_engine.clock_policy import ClockPolicy, implementation_sha256
from backend.data_engine.forward_store import hashed
from backend.runtime.forward_recovery_evidence import (
    read_stored_epoch, inspect_startup_contract,
)

VERSION = "EURUSD_STAGE4_COLLECTOR_FORENSICS_S4_01_04_V1"
HEX64 = re.compile(r"^[a-f0-9]{64}$")
UTC_STAMP = re.compile(
    r"(?<!\d)(\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d"
    r"(?:\.\d+)?(?:Z|[+-]\d\d:\d\d))(?!\d)"
)

EVENTS = {
    "supervisor_start": "SUPERVISOR START",
    "supervisor_exit": "SUPERVISOR EXIT",
    "supervisor_restart_scheduled": "SUPERVISOR RESTART SCHEDULED",
    "recorder_blocked": '"status": "blocked"',
    "clock_code_mismatch": "Clock implementation differs from the bound epoch",
    "mt5_connection_unavailable": "MT5 connection unavailable",
    "duplicate_instance": "A forward recorder for this broker is already active",
    "recorder_stopped": "Forward capture stopped",
    "recorder_captured": '"status": "captured"',
}

# Fixed read-only Windows script. User arguments are never inserted into it.
# Raw command lines, credentials and service executable paths are not returned.
WINDOWS_SURVEY = r'''
$ErrorActionPreference = 'Stop'
$pattern = 'backend[.\\/]runtime[.\\/](market_data_supervisor|capture_forward_feed)(?:[.]py)?'
$modulePattern = 'backend[.]runtime[.](market_data_supervisor|capture_forward_feed)'

function Relevant([string] $s) {
    return ($s -match $pattern -or $s -match $modulePattern)
}

$procStatus = 'OBSERVED'
$processes = @()

try {
    $processes = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^python(?:w)?(?:[.]exe)?$' -and
        (Relevant ([string] $_.CommandLine))
    } | ForEach-Object {
        $cmd = [string] $_.CommandLine

        [PSCustomObject]@{
            pid = [int] $_.ProcessId
            role = $(if ($cmd -match 'capture_forward_feed') {
                'forward_recorder'
            } else {
                'supervisor'
            })
            clock_policy_flag = $cmd -match '(?i)--clock-policy(?:\s|=)'
            continuous_flag = $cmd -match '(?i)--continuous(?:\s|$)'
            initialize_flag = $cmd -match '(?i)--initialize(?:\s|$)'
            started_utc = $(if ($_.CreationDate) {
                $_.CreationDate.ToUniversalTime().ToString('o')
            } else {
                $null
            })
        }
    })
} catch {
    $procStatus = 'UNAVAILABLE'
    $processes = @()
}

$taskStatus = 'OBSERVED'
$tasks = @()

try {
    $tasks = @(Get-ScheduledTask | ForEach-Object {
        $task = $_

        foreach ($action in @($task.Actions)) {
            $command = ([string] $action.Execute) +
                ' ' + ([string] $action.Arguments)

            if (Relevant $command) {
                [PSCustomObject]@{
                    launcher = 'scheduled_task'
                    name = [string] $task.TaskName
                    state = [string] $task.State
                    clock_policy_flag = $command -match '(?i)--clock-policy(?:\s|=)'
                }
            }
        }
    })
} catch {
    $taskStatus = 'UNAVAILABLE'
    $tasks = @()
}

$serviceStatus = 'OBSERVED'
$services = @()

try {
    $services = @(Get-CimInstance Win32_Service | Where-Object {
        Relevant ([string] $_.PathName)
    } | ForEach-Object {
        $command = [string] $_.PathName

        [PSCustomObject]@{
            launcher = 'windows_service'
            name = [string] $_.Name
            state = [string] $_.State
            clock_policy_flag = $command -match '(?i)--clock-policy(?:\s|=)'
        }
    })
} catch {
    $serviceStatus = 'UNAVAILABLE'
    $services = @()
}

[PSCustomObject]@{
    process_status = $procStatus
    processes = @($processes)
    tasks_status = $taskStatus
    tasks = @($tasks)
    services_status = $serviceStatus
    services = @($services)
} | ConvertTo-Json -Depth 6 -Compress
'''


class Stage4ForensicsError(RuntimeError):
    """Malformed input or unsupported observational evidence."""


def _utc(value):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise Stage4ForensicsError(
            "An explicit UTC datetime is required."
        )
    return value.astimezone(timezone.utc)


def _read_bounded(path, maximum):
    file = Path(path)

    if file.is_symlink():
        raise Stage4ForensicsError(
            "Symlinked evidence files are not inspected."
        )

    with file.open("rb") as stream:
        raw = stream.read(maximum + 1)

    if len(raw) > maximum:
        raise Stage4ForensicsError(
            "Evidence file exceeds size limit."
        )

    return raw


def _list(value, limit, key):
    if (
        not isinstance(value, list)
        or len(value) > limit
        or any(not isinstance(item, dict) for item in value)
    ):
        raise Stage4ForensicsError(
            f"Invalid Windows {key} observations."
        )
    return value


def normalize_windows_survey(payload):
    """Validate Windows findings and reject unrequested sensitive fields."""
    required = {
        "process_status", "processes",
        "tasks_status", "tasks",
        "services_status", "services",
    }

    if not isinstance(payload, dict) or set(payload) != required:
        raise Stage4ForensicsError(
            "Unrecognized Windows survey format."
        )

    for key in (
        "process_status", "tasks_status", "services_status"
    ):
        if payload[key] not in ("OBSERVED", "UNAVAILABLE"):
            raise Stage4ForensicsError(
                "Invalid survey visibility status."
            )

    processes = []

    for p in _list(payload["processes"], 50, "process"):
        fields = {
            "pid", "role", "clock_policy_flag",
            "continuous_flag", "initialize_flag", "started_utc",
        }

        if set(p) != fields:
            raise Stage4ForensicsError(
                "Process observation contains unexpected fields."
            )

        if (
            type(p["pid"]) is not int
            or p["pid"] <= 0
            or p["role"] not in (
                "supervisor", "forward_recorder"
            )
            or any(
                type(p[k]) is not bool
                for k in (
                    "clock_policy_flag",
                    "continuous_flag",
                    "initialize_flag",
                )
            )
        ):
            raise Stage4ForensicsError(
                "Invalid process observation."
            )

        if p["started_utc"] is not None:
            _parse_stamp(p["started_utc"])

        processes.append(dict(p))

    launchers = []

    for field, role in (
        ("tasks", "scheduled_task"),
        ("services", "windows_service"),
    ):
        for p in _list(payload[field], 50, field):
            if set(p) != {
                "launcher", "name", "state", "clock_policy_flag"
            }:
                raise Stage4ForensicsError(
                    "Launcher observation contains unexpected fields."
                )

            if (
                p["launcher"] != role
                or not isinstance(p["name"], str)
                or not 1 <= len(p["name"]) <= 256
                or not isinstance(p["state"], str)
                or len(p["state"]) > 40
                or type(p["clock_policy_flag"]) is not bool
            ):
                raise Stage4ForensicsError(
                    "Invalid launcher observation."
                )

            launchers.append(dict(p))

    return {
        "process_visibility": payload["process_status"],
        "matching_process_count": len(processes),
        "processes": sorted(
            processes, key=lambda p: (p["role"], p["pid"])
        ),
        "task_visibility": payload["tasks_status"],
        "service_visibility": payload["services_status"],
        "matching_launchers": sorted(
            launchers,
            key=lambda p: (p["launcher"], p["name"]),
        ),
        "matching_launcher_count": len(launchers),
        "command_lines_exposed": False,
        "historical_process_state_proven": False,
    }


def survey_windows_runtime(*, enabled=False, runner=None):
    """Fixed PowerShell survey, with no process changes."""
    if not enabled:
        return {
            "process_visibility": "NOT_INSPECTED",
            "matching_process_count": None,
            "processes": [],
            "task_visibility": "NOT_INSPECTED",
            "service_visibility": "NOT_INSPECTED",
            "matching_launchers": [],
            "matching_launcher_count": None,
            "command_lines_exposed": False,
            "historical_process_state_proven": False,
        }

    if os.name != "nt" and runner is None:
        raise Stage4ForensicsError(
            "Windows runtime inspection requires Windows."
        )

    runner = runner or subprocess.run

    try:
        response = runner(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                WINDOWS_SURVEY,
            ],
            capture_output=True,
            check=False,
            timeout=25,
        )

        if (
            response.returncode != 0
            or len(response.stdout) > 131072
        ):
            raise ValueError(
                "Windows survey failed or exceeded output bounds."
            )

        payload = json.loads(
            response.stdout.decode("utf-8-sig")
        )

        return normalize_windows_survey(payload)

    except (
        OSError,
        subprocess.TimeoutExpired,
        ValueError,
        UnicodeError,
        Stage4ForensicsError,
    ):
        return {
            "process_visibility": "UNAVAILABLE",
            "matching_process_count": None,
            "processes": [],
            "task_visibility": "UNAVAILABLE",
            "service_visibility": "UNAVAILABLE",
            "matching_launchers": [],
            "matching_launcher_count": None,
            "command_lines_exposed": False,
            "historical_process_state_proven": False,
        }


def _parse_stamp(value):
    try:
        stamp = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
        return _utc(stamp)
    except (
        ValueError,
        AttributeError,
        Stage4ForensicsError,
        TypeError,
    ):
        raise Stage4ForensicsError(
            "Invalid explicit UTC event timestamp."
        ) from None


def inspect_existing_logs(paths=(), *, max_bytes=262144):
    """Count recognized events without returning any raw log lines."""
    if (
        type(max_bytes) is not int
        or not 1024 <= max_bytes <= 1048576
    ):
        raise Stage4ForensicsError(
            "Log inspection bound must be 1024..1048576 bytes."
        )

    if len(paths) > 10:
        raise Stage4ForensicsError(
            "At most ten log files may be inspected."
        )

    event_counts = Counter({
        name: 0 for name in EVENTS
    })
    latest_by_event = {}
    files = []

    for path in paths:
        try:
            raw = _read_bounded(path, max_bytes)
        except (OSError, Stage4ForensicsError):
            files.append({
                "status": "UNAVAILABLE_OR_TOO_LARGE",
                "sha256": None,
            })
            continue

        files.append({
            "status": "READ_UNATTESTED",
            "sha256": sha256(raw).hexdigest(),
        })

        for line in raw.decode(
            "utf-8", "replace"
        ).splitlines():
            found = [
                name
                for name, marker in EVENTS.items()
                if marker.casefold() in line.casefold()
            ]

            if not found:
                continue

            stamp = UTC_STAMP.search(line)
            parsed = None

            if stamp:
                try:
                    parsed = _parse_stamp(
                        stamp.group(1).replace(" ", "T")
                    )
                except Stage4ForensicsError:
                    pass

            for name in found:
                event_counts[name] += 1

                if (
                    parsed is not None
                    and (
                        name not in latest_by_event
                        or parsed > latest_by_event[name]
                    )
                ):
                    latest_by_event[name] = parsed

    return {
        "requested_files": len(paths),
        "readable_files": sum(
            f["status"] == "READ_UNATTESTED"
            for f in files
        ),
        "files": files,
        "event_counts": dict(
            sorted(event_counts.items())
        ),
        "last_explicit_utc_per_event": {
            k: v.isoformat()
            for k, v in sorted(
                latest_by_event.items()
            )
        },
        "raw_log_text_exposed": False,
        "log_source_independently_attested": False,
        "historic_stoppage_cause_proven": False,
    }


def inspect_policy_copy(
    policy_file,
    stored,
    *,
    expected_file_sha256,
):
    if not HEX64.fullmatch(
        expected_file_sha256
    ):
        raise Stage4ForensicsError(
            "Expected policy file hash must be SHA-256."
        )

    result = {
        "status": "UNAVAILABLE",
        "file_sha256": None,
        "hash_matches_previous_observation": False,
        "policy_matches_epoch": False,
        "policy_clock_matches_current_code": False,
        "original_file_location_attested": False,
    }

    try:
        raw = _read_bounded(
            policy_file, 16384
        )
        policy = ClockPolicy.load(
            policy_file
        )
    except (
        OSError,
        ValueError,
        RuntimeError,
        Stage4ForensicsError,
    ):
        return result

    result["file_sha256"] = sha256(
        raw
    ).hexdigest()
    result["hash_matches_previous_observation"] = (
        result["file_sha256"]
        == expected_file_sha256
    )
    result["policy_matches_epoch"] = (
        policy.epoch_id == stored.epoch_id
    )
    result["policy_clock_matches_current_code"] = (
        policy.implementation_sha256
        == implementation_sha256()
    )

    result["status"] = (
        "LOCAL_COPY_MATCH_UNATTESTED"
        if (
            result["hash_matches_previous_observation"]
            and result["policy_matches_epoch"]
        )
        else "COPY_MISMATCH"
    )

    return result


def evaluate_stage4_forensics(
    epoch,
    host,
    logs,
    policy_copy,
    startup,
    *,
    observed_at,
):
    """Cross-check observations without asserting a historic failure cause."""
    at = _utc(observed_at)
    cutoff = _parse_stamp(
        epoch["as_of"]
    )

    if (
        at != cutoff
        or epoch.get("read_only") is not True
    ):
        raise Stage4ForensicsError(
            "Epoch snapshot and inspection time inconsistent."
        )

    if not all(
        isinstance(x, dict)
        for x in (host, logs, policy_copy)
    ):
        raise Stage4ForensicsError(
            "Observation reports missing."
        )

    blockers = set()

    if (
        epoch["stored_code_sha256"]
        != implementation_sha256()
    ):
        blockers.add(
            "EPOCH_CLOCK_CODE_NOT_CURRENT"
        )

    if (
        not policy_copy["policy_matches_epoch"]
        or not policy_copy[
            "hash_matches_previous_observation"
        ]
    ):
        blockers.add(
            "POLICY_COPY_NOT_VERIFIED"
        )

    valid_from = _parse_stamp(
        epoch["policy_valid_from"]
    )
    valid_until = _parse_stamp(
        epoch["policy_valid_until"]
    )

    if not valid_from <= at < valid_until:
        blockers.add(
            "EPOCH_LEASE_NOT_ACTIVE_NOW"
        )

    last = epoch[
        "last_capture_ingested_at"
    ]

    age = (
        (at - _parse_stamp(last)).total_seconds()
        if last else None
    )

    if age is None or age > 900:
        blockers.add(
            "NO_RECENT_CAPTURE"
        )

    if host["process_visibility"] != "OBSERVED":
        blockers.add(
            "CURRENT_PROCESS_SURVEY_NOT_AVAILABLE"
        )
    elif host["matching_process_count"] == 0:
        blockers.add(
            "NO_MATCHING_RUNNING_COLLECTOR_OR_SUPERVISOR"
        )

    if (
        host["task_visibility"] != "OBSERVED"
        or host["service_visibility"] != "OBSERVED"
    ):
        blockers.add(
            "LAUNCHER_INVENTORY_PARTIALLY_UNAVAILABLE"
        )

    if logs["readable_files"] == 0:
        blockers.add(
            "NO_READABLE_SUPERVISOR_OR_RECORDER_LOGS"
        )

    if startup.get("status") != (
        "STATIC_LAUNCH_CONTRACT_RECOGNIZED"
    ):
        blockers.add(
            "STATIC_RECORDER_LAUNCH_CONTRACT_NOT_VERIFIED"
        )

    blockers.add(
        "INDEPENDENT_BROKER_CLOCK_AND_RECEIPT_EVIDENCE_MISSING"
    )

    result = {
        "assessment_version": VERSION,
        "epoch_id": epoch["epoch_id"],
        "observed_at": at.isoformat(),
        "last_capture_ingested_at": last,
        "seconds_since_capture": age,
        "stored_epoch_code_sha256": (
            epoch["stored_code_sha256"]
        ),
        "current_code_sha256": implementation_sha256(),
        "epoch_lease_valid_until": (
            epoch["policy_valid_until"]
        ),
        "policy_copy": policy_copy,
        "windows_runtime": host,
        "logs": logs,
        "startup_contract": startup.get("status"),
        "evidence_blockers": sorted(blockers),
        "historic_stoppage_cause": "NOT_ESTABLISHED",
        "read_only_recovery_steps": [
            "REVIEW_WINDOWS_SURVEY_PROCESS_AND_LAUNCHER_RESULTS",
            "LOCATE_ORIGINAL_SUPERVISOR_AND_RECORDER_LOG_FILES",
            "COMPARE_ANY_EXISTING_LOG_EVENTS_WITH_LAST_INGESTION_RECEIPT",
            "VERIFY_ORIGINAL_CLOCK_SOURCE_AND_INDEPENDENT_BROKER_TIME",
            "DESIGN_SEPARATE_OPERATOR_APPROVED_RECOVERY_ONLY_AFTER_ATTESTATION",
        ],
        "recovery_authorized": False,
        "collector_started": False,
        "database_modified": False,
        "policy_modified": False,
        "council_invoked": False,
        "training_authorized": False,
        "execution_authorized": False,
        "final_action": "HOLD",
    }

    result["report_sha256"] = hashed(
        result
    )
    return result


def inspect_stage4_forensics(
    engine,
    *,
    epoch_id,
    as_of,
    policy_file,
    expected_policy_sha256,
    repo=".",
    inspect_windows=False,
    log_files=(),
):
    cutoff = _utc(as_of)

    stored, epoch = read_stored_epoch(
        engine,
        epoch_id=epoch_id,
        as_of=cutoff,
    )

    policy = inspect_policy_copy(
        policy_file,
        stored,
        expected_file_sha256=expected_policy_sha256,
    )

    host = survey_windows_runtime(
        enabled=inspect_windows
    )
    logs = inspect_existing_logs(
        log_files
    )
    startup = inspect_startup_contract(
        repo
    )

    return evaluate_stage4_forensics(
        epoch,
        host,
        logs,
        policy,
        startup,
        observed_at=cutoff,
    )


def run(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__
    )
    p.add_argument(
        "--read-only-db",
        action="store_true",
        required=True,
    )
    p.add_argument(
        "--read-only-host",
        action="store_true",
        required=True,
    )
    p.add_argument(
        "--epoch-id",
        required=True,
    )
    p.add_argument(
        "--as-of",
        required=True,
    )
    p.add_argument(
        "--policy-file",
        required=True,
    )
    p.add_argument(
        "--expected-policy-sha256",
        required=True,
    )
    p.add_argument(
        "--inspect-windows",
        action="store_true",
    )
    p.add_argument(
        "--log-file",
        action="append",
        default=[],
    )
    p.add_argument(
        "--json",
        action="store_true",
    )

    args = p.parse_args(argv)

    try:
        if (
            not HEX64.fullmatch(args.epoch_id)
            or not HEX64.fullmatch(
                args.expected_policy_sha256
            )
        ):
            raise Stage4ForensicsError(
                "Invalid epoch or policy SHA-256."
            )

        cutoff = _parse_stamp(
            args.as_of
        )

        if cutoff > datetime.now(
            timezone.utc
        ):
            raise Stage4ForensicsError(
                "Future inspection cutoff not allowed."
            )

        if len(args.log_file) > 10:
            raise Stage4ForensicsError(
                "Too many log files."
            )

        from backend.database.connection import engine

        try:
            report = inspect_stage4_forensics(
                engine,
                epoch_id=args.epoch_id,
                as_of=cutoff,
                policy_file=args.policy_file,
                expected_policy_sha256=(
                    args.expected_policy_sha256
                ),
                repo=Path.cwd(),
                inspect_windows=args.inspect_windows,
                log_files=args.log_file,
            )
        finally:
            engine.dispose()

    except Exception as exc:
        print(
            "STAGE 4 COLLECTOR FORENSICS BLOCKED: "
            + type(exc).__name__
        )
        return 1

    if args.json:
        print(
            json.dumps(
                report,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
        )
    else:
        print(
            "EURUSD ORACLE | Stage 4 S4-01..04 collector forensics"
        )
        print(
            "Epoch: " + report["epoch_id"]
            + " | as_of=" + report["observed_at"]
        )
        print(
            "Last capture: "
            + str(report["last_capture_ingested_at"])
        )
        print(
            "Policy: "
            + report["policy_copy"]["status"]
        )
        print(
            "Processes: "
            + report["windows_runtime"]["process_visibility"]
            + " | matching="
            + str(
                report["windows_runtime"]["matching_process_count"]
            )
        )
        print(
            "Launchers: tasks="
            + report["windows_runtime"]["task_visibility"]
            + ", services="
            + report["windows_runtime"]["service_visibility"]
            + ", matches="
            + str(
                report["windows_runtime"]["matching_launcher_count"]
            )
        )
        print(
            "Readable log files: "
            + str(report["logs"]["readable_files"])
        )

        for key, count in report[
            "logs"
        ]["event_counts"].items():
            if count:
                print(
                    "  LOG EVENT "
                    + key + "=" + str(count)
                )

        for item in report["evidence_blockers"]:
            print("  BLOCKER " + item)

        print(
            "Historical stoppage cause: NOT ESTABLISHED"
        )
        print(
            "SHA-256: " + report["report_sha256"]
        )
        print(
            "READ-ONLY | NO RESTART | NO COUNCIL "
            "| NO TRAINING | FINAL=HOLD"
        )

    return 0


def main():
    raise SystemExit(
        run()
    )


if __name__ == "__main__":
    main()

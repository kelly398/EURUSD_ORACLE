
"""P3-05..08: read-only clock artifact, Git source, startup and log preflight.

Reads one stored epoch, bounded operator-selected local paths, and Git history.
Never recreates a policy file, connects to MT5, starts a worker, or authorizes
recovery, historical admission, Council dispatch, training or execution.
"""

import argparse
import ast
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess

from sqlalchemy import select, text

from backend.data_engine.clock_policy import ClockPolicy, aware_utc, implementation_sha256
from backend.data_engine.forward_store import encoded, epochs, captures, schema
from backend.runtime.market_data_supervisor import configured_workers

VERSION = "EURUSD_P3_RECOVERY_EVIDENCE_V1"
HEX64 = re.compile(r"^[a-f0-9]{64}$")
REVISION = re.compile(r"^[a-f0-9]{40,64}$")
CLOCK_PATH = "backend/data_engine/clock_policy.py"
SKIP_DIRS = {".git", ".venv", "venv", "oracle_env", "node_modules", "__pycache__",
             ".pytest_cache", "dist", "build", ".mypy_cache"}
LOG_TOKENS = {
    "supervisor_start": "SUPERVISOR START",
    "supervisor_exit": "SUPERVISOR EXIT",
    "restart_scheduled": "SUPERVISOR RESTART SCHEDULED",
    "clock_policy_error": "Clock implementation differs",
    "mt5_unavailable": "MT5 connection unavailable",
    "recorder_blocked": '"status": "blocked"',
}


class RecoveryEvidenceError(RuntimeError):
    """A bounded read-only recovery assessment could not be trusted."""


def _digest(data):
    return sha256(encoded(data).encode("utf-8")).hexdigest()


def _limit(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise RecoveryEvidenceError(f"{name} must be an integer in [{low}, {high}].")
    return value


def _utc(value):
    try:
        stamp = aware_utc(value)
        if value.utcoffset() != timedelta(0):
            raise ValueError
        return stamp
    except (ValueError, TypeError, AttributeError):
        raise RecoveryEvidenceError("An explicit UTC cutoff is required.") from None


def read_stored_epoch(engine, *, epoch_id, as_of):
    """One PostgreSQL read-only transaction; no table initialization."""
    if not isinstance(epoch_id, str) or not HEX64.fullmatch(epoch_id):
        raise RecoveryEvidenceError("A lowercase 64-character epoch ID is required.")
    cutoff = _utc(as_of)
    if cutoff > datetime.now(timezone.utc):
        raise RecoveryEvidenceError("Cannot inspect evidence in the future.")
    if engine.dialect.name not in ("postgresql", "sqlite"):
        raise RecoveryEvidenceError("Unsupported database dialect.")
    with engine.connect() as connection, connection.begin():
        if engine.dialect.name == "postgresql":
            connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            connection.execute(text("SET LOCAL statement_timeout = '15s'"))
        if connection.execute(select(schema.c.version)).scalars().all() != [1]:
            raise RecoveryEvidenceError("Forward schema version not recognized.")
        row = connection.execute(select(epochs).where(
            epochs.c.epoch_id == epoch_id, epochs.c.recorded_at <= cutoff
        )).mappings().first()
        if row is None:
            raise RecoveryEvidenceError("Epoch not recorded by the cutoff.")
        last = connection.execute(select(
            captures.c.tick_end, captures.c.received_at, captures.c.ingested_at
        ).where(captures.c.epoch_id == epoch_id,
                captures.c.ingested_at <= cutoff,
                captures.c.received_at <= cutoff,
                captures.c.tick_end <= cutoff
        ).order_by(captures.c.ingested_at.desc(), captures.c.capture_id).limit(1)
        ).mappings().first()
    try:
        stored = ClockPolicy.model_validate_json(row["policy_json"])
    except (ValueError, TypeError) as exc:
        raise RecoveryEvidenceError("Stored policy JSON cannot be validated.") from exc
    if (stored.epoch_id != epoch_id or row["valid_from"] != stored.valid_from
            or row["valid_until"] != stored.valid_until
            or row["policy_json"] != encoded(stored.model_dump(mode="json"))):
        raise RecoveryEvidenceError("Stored epoch metadata and policy disagree.")
    return stored, {
        "epoch_id": epoch_id, "as_of": cutoff.isoformat(),
        "stored_policy_sha256": sha256(row["policy_json"].encode("utf-8")).hexdigest(),
        "policy_recorded_at": row["recorded_at"].isoformat(),
        "policy_valid_from": stored.valid_from.isoformat(),
        "policy_valid_until": stored.valid_until.isoformat(),
        "stored_code_sha256": stored.implementation_sha256,
        "last_capture_ingested_at": last["ingested_at"].isoformat() if last else None,
        "last_capture_received_at": last["received_at"].isoformat() if last else None,
        "last_capture_query_end": last["tick_end"].isoformat() if last else None,
        "stored_policy_is_recoverable_as_data": True,
        "original_policy_file_location_proven": False,
        "read_only": True,
    }


def search_local_policy(stored, *, roots=(), max_json_files=500, max_directories=2000):
    """Bounded metadata-only scan of EXPLICIT roots. Never writes policy JSON."""
    _limit(max_json_files, 1, 5000, "max_json_files")
    _limit(max_directories, 1, 10000, "max_directories")
    inspected = 0
    visited_directories = 0
    matches = []
    inaccessible = 0
    cap_hit = False
    for root_arg in roots:
        root = Path(root_arg).resolve()
        if not root.is_dir():
            inaccessible += 1
            continue
        for directory, directories, names in os.walk(root, followlinks=False):
            if visited_directories >= max_directories:
                cap_hit = True
                break
            visited_directories += 1
            directories[:] = sorted(d for d in directories if d not in SKIP_DIRS
                                    and not (Path(directory) / d).is_symlink())
            for name in sorted(names):
                if not name.lower().endswith(".json"):
                    continue
                if inspected >= max_json_files:
                    cap_hit = True
                    break
                path = Path(directory) / name
                if path.is_symlink():
                    continue
                try:
                    if not path.resolve().is_relative_to(root):
                        continue
                    if path.stat().st_size > 16384:
                        continue
                    inspected += 1
                    candidate = ClockPolicy.load(path)
                    if (candidate.epoch_id == stored.epoch_id
                            and encoded(candidate.model_dump(mode="json"))
                            == encoded(stored.model_dump(mode="json"))):
                        matches.append({
                            "path": str(path.resolve()),
                            "file_sha256": sha256(path.read_bytes()).hexdigest(),
                            "same_policy_as_stored_epoch": True,
                            "clock_matches_current_code": (
                                candidate.implementation_sha256 == implementation_sha256()
                            ),
                        })
                except (OSError, ValueError, RuntimeError):
                    inaccessible += 1
            if cap_hit:
                break
        if cap_hit:
            break
    return {
        "roots_requested": len(roots), "json_files_examined": inspected,
        "directories_visited": visited_directories, "search_truncated": cap_hit,
        "unreadable_or_invalid_count": inaccessible,
        "matching_policy_files": matches,
        "matching_file_count": len(matches),
        "original_file_location_attested": False,
        "status": "BOUNDED_SEARCH_INCOMPLETE" if cap_hit else (
            "MATCHING_LOCAL_COPY_FOUND_UNATTESTED" if matches else "NO_MATCHING_LOCAL_COPY_FOUND"
        ),
    }


def _git_blob(repo, revision):
    if REVISION.fullmatch(revision) is None:
        raise RecoveryEvidenceError("Invalid Git revision identifier.")
    try:
        result = subprocess.run(
            ["git", "cat-file", "blob", f"{revision}:{CLOCK_PATH}"],
            cwd=repo, capture_output=True, timeout=8, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecoveryEvidenceError("Cannot inspect local Git clock blob.") from exc
    if result.returncode or len(result.stdout) > 131072:
        raise RecoveryEvidenceError("Local Git clock blob unavailable or oversized.")
    return result.stdout


def _functions(source):
    try:
        tree = ast.parse(source.decode("utf-8-sig"))
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise RecoveryEvidenceError("Clock source cannot be parsed safely.") from exc
    result = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result[node.name] = ast.dump(node, include_attributes=False)
    return result


def compare_clock_code(*, repo, bound_hash, candidate_revision, current_path=None):
    """Summarize function changes. Never execute original or current code."""
    if not isinstance(bound_hash, str) or not HEX64.fullmatch(bound_hash):
        raise RecoveryEvidenceError("Invalid epoch-bound clock hash.")
    root = Path(repo).resolve()
    existing = Path(current_path) if current_path is not None else root / CLOCK_PATH
    original = _git_blob(root, candidate_revision)
    if sha256(original).hexdigest() != bound_hash:
        return {"status": "CANDIDATE_NOT_BYTE_EXACT", "changed_functions": [],
                "source_bytes_match": False, "history_certified": False}
    try:
        current = existing.read_bytes()
    except OSError as exc:
        raise RecoveryEvidenceError("Current local clock source missing.") from exc
    earlier, later = _functions(original), _functions(current)
    changed = sorted(name for name in earlier.keys() | later.keys()
                     if earlier.get(name) != later.get(name))
    return {
        "status": "EXACT_LOCAL_GIT_BLOB_LOCATED_UNATTESTED",
        "revision": candidate_revision,
        "bound_sha256": bound_hash,
        "current_sha256": sha256(current).hexdigest(),
        "source_bytes_match": current == original,
        "changed_functions": changed,
        "changed_function_count": len(changed),
        "history_certified": False,
        "broker_utc_certified": False,
    }


def _options(filepath):
    try:
        tree = ast.parse(Path(filepath).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, SyntaxError):
        return None
    result = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add_argument":
            continue
        names = [arg.value for arg in node.args
                 if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
        required = any(key.arg == "required" and isinstance(key.value, ast.Constant)
                       and key.value.value is True for key in node.keywords)
        for name in names:
            result[name] = {"required": required}
    return result

def inspect_startup_contract(repo):
    root = Path(repo).resolve()
    supervisor = _options(root / "backend/runtime/market_data_supervisor.py")
    recorder = _options(root / "backend/runtime/capture_forward_feed.py")
    legacy = [w.name for w in configured_workers()]
    valid = (supervisor is not None and recorder is not None
             and "--clock-policy" in supervisor
             and recorder.get("--clock-policy", {}).get("required") is True
             and "--initialize" in recorder
             and "utc_forward_feed" not in legacy)
    return {
        "status": "STATIC_LAUNCH_CONTRACT_RECOGNIZED" if valid else "LAUNCH_CONTRACT_UNVERIFIED",
        "supervisor_accepts_clock_policy": supervisor is not None and "--clock-policy" in supervisor,
        "recorder_requires_clock_policy": recorder is not None and recorder.get("--clock-policy", {}).get("required") is True,
        "recorder_has_initialize_write_flag": recorder is not None and "--initialize" in recorder,
        "default_legacy_workers": legacy,
        "process_command_lines_inspected": False,
        "mt5_connection_inspected": False,
        "recorder_or_supervisor_started": False,
    }


def inspect_logs(paths=(), *, max_bytes=262144):
    """Only bounded counts of whitelisted event patterns; never echo log lines."""
    _limit(max_bytes, 1024, 1048576, "max_bytes")
    counts = {key: 0 for key in LOG_TOKENS}
    inspected = 0
    unavailable = 0
    truncated = 0
    for value in paths:
        file = Path(value)
        try:
            with file.open("rb") as stream:
                raw = stream.read(max_bytes + 1)
        except OSError:
            unavailable += 1
            continue
        if len(raw) > max_bytes:
            truncated += 1
            raw = raw[:max_bytes]
        inspected += 1
        for line in raw.decode("utf-8", errors="replace").splitlines():
            for key, token in LOG_TOKENS.items():
                if token.casefold() in line.casefold():
                    counts[key] += 1
    return {
        "requested_log_files": len(paths), "log_files_examined": inspected,
        "unreadable_log_files": unavailable, "truncated_log_files": truncated,
        "event_pattern_counts": counts,
        "log_authenticity_verified": False,
        "historic_stoppage_cause_proven": False,
        "status": "NO_LOG_FILES_SUPPLIED" if not paths else (
            "BOUNDED_LOG_OBSERVATIONS_ONLY" if inspected else "LOGS_UNAVAILABLE"
        ),
    }


def build_recovery_plan(db, policy_scan, code, startup, logs, *, at_time=None):
    """Pure, fail-closed review, including an explicit proposed next action."""
    cutoff = datetime.fromisoformat(db["as_of"])
    now = _utc(at_time) if at_time is not None else cutoff
    if now < cutoff:
        raise RecoveryEvidenceError("Plan time predates inspection cutoff.")
    if db.get("read_only") is not True or db.get("original_policy_file_location_proven") is not False:
        raise RecoveryEvidenceError("Stored epoch evidence contract is invalid.")
    blockers = set()
    local_hash = implementation_sha256()
    if db["stored_code_sha256"] != local_hash:
        blockers.add("BOUND_CLOCK_CODE_DIFFERS_FROM_CURRENT")
    if now >= datetime.fromisoformat(db["policy_valid_until"]):
        blockers.add("ORIGINAL_EPOCH_LEASE_EXPIRED")
    if now < datetime.fromisoformat(db["policy_valid_from"]):
        blockers.add("ORIGINAL_EPOCH_NOT_YET_ACTIVE")
    if not policy_scan["matching_file_count"]:
        blockers.add("NO_VERIFIED_LOCAL_POLICY_COPY")
    if policy_scan["search_truncated"]:
        blockers.add("POLICY_FILE_DISCOVERY_INCOMPLETE")
    if startup["status"] != "STATIC_LAUNCH_CONTRACT_RECOGNIZED":
        blockers.add("SUPERVISOR_STARTUP_CONTRACT_UNVERIFIED")
    if code["status"] != "EXACT_LOCAL_GIT_BLOB_LOCATED_UNATTESTED":
        blockers.add("BOUND_CLOCK_GIT_SOURCE_NOT_BYTE_EXACT")
    if logs["status"] != "BOUNDED_LOG_OBSERVATIONS_ONLY":
        blockers.add("COLLECTOR_EXIT_LOG_EVIDENCE_NOT_REVIEWED")
    if logs["truncated_log_files"] or logs["unreadable_log_files"]:
        blockers.add("COLLECTOR_LOG_COVERAGE_INCOMPLETE")
    if db["last_capture_ingested_at"] is None:
        blockers.add("NO_STORED_FORWARD_CAPTURE")
    elif now - datetime.fromisoformat(db["last_capture_ingested_at"]) > timedelta(minutes=15):
        blockers.add("NO_RECENT_FORWARD_CAPTURE")
    blockers.update({"BROKER_CLOCK_IDENTITY_ATTESTATION_NOT_ESTABLISHED",
                     "FORWARD_RECORDER_PROCESS_STATE_NOT_ATTESTED"})
    result = {
        "assessment_version": VERSION, "epoch_id": db["epoch_id"],
        "as_of": cutoff.isoformat(),
        "stored_policy": db, "local_policy_search": policy_scan,
        "clock_history": code, "startup_contract": startup, "log_observations": logs,
        "operator_blockers": sorted(blockers),
        "recovery_recommendation": "REVIEW_EVIDENCE_AND_CONFIG_ONLY_NO_AUTOMATIC_RESTART",
        "next_manual_evidence": [
            "LOCATE_OR_OPERATOR_VERIFY_ORIGINAL_POLICY_ARTIFACT_WITHOUT_WRITING_DB",
            "REVIEW_GIT_CLOCK_FUNCTION_DIFF_WITH_NO_CHECKOUT_OR_EPOCH_EDIT",
            "INSPECT_EXISTING_PROCESS_AND_SUPERVISOR_LOGS_WITHOUT_RESTART",
            "ESTABLISH_BROKER_CLOCK_AND_FORWARD_RECEIPT_PROVENANCE_INDEPENDENTLY",
        ],
        "historic_collector_failure_cause": "UNDETERMINED",
        "broker_identity_attested": False,
        "new_epoch_created": False, "database_modified": False,
        "clock_policy_file_created": False, "collector_started": False,
        "recovery_authorized": False, "council_invoked": False,
        "real_data_admission": False, "training_authorized": False,
        "execution_authorized": False, "final_action": "HOLD",
    }
    result["report_sha256"] = _digest(result)
    return result


def inspect_recovery_evidence(engine, *, epoch_id, as_of, repo=".", search_roots=(),
                              log_paths=(), max_json_files=500, max_log_bytes=262144,
                              clock_revision=None):
    stored, db = read_stored_epoch(engine, epoch_id=epoch_id, as_of=as_of)
    scan = search_local_policy(stored, roots=search_roots, max_json_files=max_json_files)
    from backend.data_engine.forward_cycle_readiness import audit_bound_clock_git_history
    history = audit_bound_clock_git_history(stored.implementation_sha256,
                                            implementation_sha256(), repo=repo)
    candidate = history["matches"][0] if history["matches"] else None
    if clock_revision is not None:
        revision = clock_revision
    elif candidate is not None:
        revision = candidate["revision"]
    else:
        revision = None
    if revision is None:
        code = {"status": "NO_GIT_CLOCK_SOURCE_CANDIDATE", "changed_functions": [],
                "source_bytes_match": False, "history_certified": False}
    else:
        code = compare_clock_code(repo=repo, bound_hash=stored.implementation_sha256,
                                  candidate_revision=revision)
    startup = inspect_startup_contract(repo)
    logs = inspect_logs(log_paths, max_bytes=max_log_bytes)
    return build_recovery_plan(db, scan, code, startup, logs)


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-db", action="store_true", required=True)
    parser.add_argument("--git-read-only", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--search-root", action="append", default=[])
    parser.add_argument("--log-file", action="append", default=[])
    parser.add_argument("--max-json-files", type=int, default=500)
    parser.add_argument("--max-log-bytes", type=int, default=262144)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not HEX64.fullmatch(args.epoch_id):
            raise RecoveryEvidenceError("A lowercase 64-character epoch ID is required.")
        cutoff = _utc(datetime.fromisoformat(args.as_of.replace("Z", "+00:00")))
        _limit(args.max_json_files, 1, 5000, "max_json_files")
        _limit(args.max_log_bytes, 1024, 1048576, "max_bytes")
        from backend.database.connection import engine
        try:
            result = inspect_recovery_evidence(
                engine, epoch_id=args.epoch_id, as_of=cutoff, repo=Path.cwd(),
                search_roots=args.search_root, log_paths=args.log_file,
                max_json_files=args.max_json_files, max_log_bytes=args.max_log_bytes,
            )
        finally:
            engine.dispose()
    except Exception as exc:
        print("RECOVERY EVIDENCE INSPECTION BLOCKED: " + type(exc).__name__)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2))
    else:
        print("EURUSD ORACLE | Phase 3 recovery evidence P3-05..08")
        print("Epoch: " + result["epoch_id"] + " | cutoff=" + result["as_of"])
        print("Stored policy canonical: YES; original file location: NOT PROVEN")
        print("Local matching policy copies: " + str(result["local_policy_search"]["matching_file_count"]))
        print("Clock history: " + result["clock_history"]["status"])
        print("Changed clock functions: " + ", ".join(result["clock_history"]["changed_functions"]))
        print("Supervisor startup: " + result["startup_contract"]["status"])
        print("Log observations: " + result["log_observations"]["status"])
        for reason in result["operator_blockers"]:
            print("  BLOCKER " + reason)
        print("SHA-256: " + result["report_sha256"])
        print("READ-ONLY | NO POLICY REBUILD | NO RESTART | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()

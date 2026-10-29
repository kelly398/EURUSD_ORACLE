
"""S5-09..12: original-capture custody and proposed recovery prerequisites.

Read-only SELECTs, existing files and self-declared plan checks only. A stored
capture manifest hashes the original *ordered* normalized source lists; later
first-owned/deduplicated rows cannot recreate those lists. An optional genuine
original list can be checked, but never treated as independently broker-attested.
No recovery, new epoch, broker connection, Council, training or trading.
"""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re

from sqlalchemy import select

from backend.data_engine.clock_policy import (
    ClockPolicy, ClockPolicyError, decode_json, instant,
)
from backend.data_engine.forward_store import captures, hashed
from backend.runtime.stage4_forward_receipt_recovery import (
    inspect_stage4_forward_receipts, ForwardReceiptReviewError,
)

VERSION = "EURUSD_STAGE5_CAPTURE_RECOVERY_S5_09_12_V1"
SHA = re.compile(r"^[0-9a-f]{64}$")
LABEL = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
UTC = timezone.utc

MAX_INDEX = 131072
MAX_PAYLOAD = 2097152
MAX_PLAN = 16384
MAX_DOCUMENTS = 100
MAX_TOTAL_PAYLOAD = 16777216

INDEX_KEYS = {
    "schema_version",
    "epoch_id",
    "as_of",
    "entries",
}

ENTRY_KEYS = {
    "capture_id",
    "relative_path",
    "file_sha256",
    "claimed_origin",
    "claimed_acquired_at",
}

PAYLOAD_KEYS = {
    "schema_version",
    "epoch_id",
    "capture_id",
    "tick_rows",
    "bar_rows",
}

PLAN_KEYS = {
    "schema_version",
    "plan_id",
    "prepared_at",
    "historical_epoch_id",
    "proposed_valid_from",
    "proposed_valid_until",
    "execution_mode",
    "candidate_clock_policy_sha256",
    "broker_identity_evidence_sha256",
    "independent_utc_reference_sha256",
    "first_receipt_procedure_sha256",
    "gap_reconciliation_procedure_sha256",
    "risk_and_council_controls_sha256",
    "operator_approval_document_sha256",
    "historical_epoch_rebind_approved",
    "collector_restart_approved",
    "council_dispatch_approved",
    "live_trading_approved",
}

PLAN_DIGEST_FIELDS = tuple(
    sorted(k for k in PLAN_KEYS if k.endswith("_sha256"))
)


class Stage5CaptureError(ValueError):
    """Invalid or misleading local evidence supplied to the audit."""


def _utc(value):
    if not isinstance(value, str):
        raise Stage5CaptureError(
            "Explicit UTC timestamp text required."
        )

    try:
        result = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except (ValueError, OverflowError):
        raise Stage5CaptureError(
            "Invalid UTC timestamp."
        ) from None

    if (
        result.tzinfo is None
        or result.utcoffset() != timedelta(0)
    ):
        raise Stage5CaptureError(
            "Explicit UTC offset zero required."
        )

    return result.astimezone(UTC)


def _document(path, limit):
    file = Path(path)

    if file.is_symlink() or not file.is_file():
        raise Stage5CaptureError(
            "Existing regular evidence file required."
        )

    try:
        with file.open("rb") as stream:
            value = stream.read(limit + 1)
    except OSError:
        raise Stage5CaptureError(
            "Existing evidence file cannot be read."
        ) from None

    if len(value) > limit:
        raise Stage5CaptureError(
            "Evidence exceeds bounded size."
        )

    return value


def _json(raw):
    try:
        return decode_json(
            raw,
            limit=len(raw),
        )
    except (
        ClockPolicyError,
        UnicodeError,
        ValueError,
        RecursionError,
        TypeError,
    ):
        raise Stage5CaptureError(
            "Malformed or duplicate-key JSON evidence."
        ) from None


def _contained(root, relative):
    if (
        not isinstance(relative, str)
        or not 1 <= len(relative) <= 240
    ):
        raise Stage5CaptureError(
            "Invalid original-capture path."
        )

    if (
        "\\" in relative
        or ":" in relative
        or "\x00" in relative
    ):
        raise Stage5CaptureError(
            "Unsafe original-capture path."
        )

    if (
        PurePosixPath(relative).is_absolute()
        or any(
            part in ("", ".", "..")
            for part in relative.split("/")
        )
    ):
        raise Stage5CaptureError(
            "Original-capture path traversal refused."
        )

    base = Path(root)

    if not base.is_dir() or base.is_symlink():
        raise Stage5CaptureError(
            "Existing non-symlink evidence root required."
        )

    for part in relative.split("/"):
        base = base / part

        if base.is_symlink():
            raise Stage5CaptureError(
                "Symlinked original-capture file refused."
            )

    return base


def _verify_original_list(
    payload,
    manifest,
    policy,
    capture_id,
):
    if (
        not isinstance(payload, dict)
        or set(payload) != PAYLOAD_KEYS
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
        or payload["epoch_id"] != policy.epoch_id
        or payload["capture_id"] != capture_id
    ):
        raise Stage5CaptureError(
            "Original list contract or capture identity differs."
        )

    tick_rows = payload["tick_rows"]
    bar_rows = payload["bar_rows"]

    if (
        not isinstance(tick_rows, list)
        or not isinstance(bar_rows, list)
    ):
        raise Stage5CaptureError(
            "Original capture must contain ordered source lists."
        )

    if (
        len(tick_rows) != manifest["tick_count"]
        or len(bar_rows) != manifest["bar_count"]
    ):
        raise Stage5CaptureError(
            "Original source-list counts differ from manifest."
        )

    if (
        hashed(tick_rows) != manifest["ticks_sha256"]
        or hashed(bar_rows) != manifest["bars_sha256"]
    ):
        raise Stage5CaptureError(
            "Original ordered source-list hashes differ from manifest."
        )

    start = _utc(manifest["tick_start"])
    end = _utc(manifest["tick_end"])
    bar_start = _utc(manifest["bar_start"])
    bar_end = _utc(manifest["bar_end"])
    received = _utc(manifest["received_at"])

    try:
        for record in tick_rows:
            if (
                not isinstance(record, dict)
                or policy.tick(
                    record["source_record"]
                ) != record
            ):
                raise Stage5CaptureError(
                    "Original tick normalization differs."
                )

            timestamp = instant(
                record["time_msc"]
            )

            if (
                not start <= timestamp < end
                or timestamp > received
            ):
                raise Stage5CaptureError(
                    "Original tick outside capture receipt/interval."
                )

        for record in bar_rows:
            if (
                not isinstance(record, dict)
                or policy.rate(
                    record["source_record"]
                ) != record
            ):
                raise Stage5CaptureError(
                    "Original bar normalization differs."
                )

            timestamp = instant(
                record["time"] * 1000
            )

            if (
                not bar_start <= timestamp < bar_end
                or timestamp + timedelta(minutes=5) > received
                or timestamp.second
                or timestamp.microsecond
                or timestamp.minute % 5
            ):
                raise Stage5CaptureError(
                    "Original bar outside completed M5 capture."
                )

    except (
        ClockPolicyError,
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
    ):
        raise Stage5CaptureError(
            "Original source-list normalization invalid."
        ) from None

    return len(tick_rows), len(bar_rows)


def inspect_original_capture_candidates(
    engine,
    *,
    index_file,
    evidence_root,
    policy,
    epoch_id,
    as_of,
    capture_limit=100,
):
    """Recompute list hashes only for expressly supplied originals."""
    empty = {
        "status": "NOT_SUPPLIED",
        "index_sha256": None,
        "original_capture_files_hash_matched": 0,
        "capture_list_hashes_recomputed": 0,
        "original_tick_rows_replayed": 0,
        "original_bar_rows_replayed": 0,
        "all_inspected_capture_lists_supplied": False,
        "independent_broker_receipts_verified": False,
        "independent_payload_origin_verified": False,
    }

    if index_file is None:
        if evidence_root is not None:
            raise Stage5CaptureError(
                "Evidence root without index refused."
            )

        return empty

    if evidence_root is None:
        raise Stage5CaptureError(
            "Evidence root required with index."
        )

    raw_index = _document(
        index_file,
        MAX_INDEX,
    )

    data = _json(raw_index)

    if (
        set(data) != INDEX_KEYS
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or data["epoch_id"] != epoch_id
        or _utc(data["as_of"]) != as_of
    ):
        raise Stage5CaptureError(
            "Original capture index schema/scope mismatch."
        )

    entries = data["entries"]

    if (
        not isinstance(entries, list)
        or len(entries) > MAX_DOCUMENTS
    ):
        raise Stage5CaptureError(
            "Original capture index count exceeds bound."
        )

    with engine.connect() as connection:
        rows = connection.execute(
            select(
                captures.c.capture_id,
                captures.c.manifest_json,
            )
            .where(
                captures.c.epoch_id == epoch_id,
                captures.c.ingested_at <= as_of,
            )
            .order_by(
                captures.c.ingested_at,
                captures.c.capture_id,
            )
            .limit(capture_limit + 1)
        ).mappings().all()

    if len(rows) > capture_limit:
        raise Stage5CaptureError(
            "Capture scope truncated for original-list reconciliation."
        )

    stored = {
        row["capture_id"]: row["manifest_json"]
        for row in rows
    }

    if policy.epoch_id != epoch_id:
        raise Stage5CaptureError(
            "Stored policy differs from candidate epoch."
        )

    seen = set()
    total_bytes = 0
    ticks = 0
    bars = 0

    for entry in entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != ENTRY_KEYS
        ):
            raise Stage5CaptureError(
                "Original capture entry contract invalid."
            )

        cid = entry["capture_id"]
        digest = entry["file_sha256"]

        if (
            not isinstance(cid, str)
            or not SHA.fullmatch(cid)
            or cid in seen
        ):
            raise Stage5CaptureError(
                "Invalid or duplicate capture identifier."
            )

        if cid not in stored:
            raise Stage5CaptureError(
                "Original capture not found in inspected database scope."
            )

        if (
            not isinstance(digest, str)
            or not SHA.fullmatch(digest)
        ):
            raise Stage5CaptureError(
                "Invalid candidate file SHA-256."
            )

        if (
            not isinstance(entry["claimed_origin"], str)
            or not LABEL.fullmatch(
                entry["claimed_origin"]
            )
            or _utc(
                entry["claimed_acquired_at"]
            ) > as_of
        ):
            raise Stage5CaptureError(
                "Claimed original-list acquisition metadata invalid."
            )

        contents = _document(
            _contained(
                evidence_root,
                entry["relative_path"],
            ),
            MAX_PAYLOAD,
        )

        total_bytes += len(contents)

        if total_bytes > MAX_TOTAL_PAYLOAD:
            raise Stage5CaptureError(
                "Total original capture payload bytes exceed bound."
            )

        if sha256(contents).hexdigest() != digest:
            raise Stage5CaptureError(
                "Original capture document file SHA-256 mismatch."
            )

        manifest = _json(
            stored[cid].encode("utf-8")
        )

        payload = _json(contents)

        counted_ticks, counted_bars = _verify_original_list(
            payload,
            manifest,
            policy,
            cid,
        )

        ticks += counted_ticks
        bars += counted_bars
        seen.add(cid)

    return {
        "status": "LOCAL_ORIGINAL_LISTS_HASH_MATCHED_UNATTESTED",
        "index_sha256": sha256(raw_index).hexdigest(),
        "original_capture_files_hash_matched": len(entries),
        "capture_list_hashes_recomputed": len(entries),
        "original_tick_rows_replayed": ticks,
        "original_bar_rows_replayed": bars,
        "all_inspected_capture_lists_supplied": (
            bool(stored)
            and len(seen) == len(stored)
        ),
        "independent_broker_receipts_verified": False,
        "independent_payload_origin_verified": False,
    }


def inspect_new_epoch_plan(
    path,
    *,
    epoch_id,
    policy_valid_until,
    as_of,
):
    """Review an existing proposal, never authorize an epoch."""
    if path is None:
        return {
            "status": "NOT_SUPPLIED",
            "file_sha256": None,
            "proposed_valid_from": None,
            "document_references_present": 0,
            "operator_approval_independently_verified": False,
            "new_epoch_approved": False,
        }

    raw = _document(
        path,
        MAX_PLAN,
    )

    data = _json(raw)

    if (
        set(data) != PLAN_KEYS
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
    ):
        raise Stage5CaptureError(
            "New-epoch proposal schema invalid."
        )

    if (
        not isinstance(data["plan_id"], str)
        or not LABEL.fullmatch(
            data["plan_id"]
        )
    ):
        raise Stage5CaptureError(
            "New-epoch proposal identifier invalid."
        )

    prepared = _utc(data["prepared_at"])
    start = _utc(data["proposed_valid_from"])
    end = _utc(data["proposed_valid_until"])

    if (
        data["historical_epoch_id"] != epoch_id
        or not prepared <= as_of < start
        or start < policy_valid_until
        or not timedelta(0) < end - start <= timedelta(days=30)
    ):
        raise Stage5CaptureError(
            "New-epoch proposal timing or historical binding invalid."
        )

    if (
        data["execution_mode"] != "research_only"
        or any(
            data[key] is not False
            for key in (
                "historical_epoch_rebind_approved",
                "collector_restart_approved",
                "council_dispatch_approved",
                "live_trading_approved",
            )
        )
    ):
        raise Stage5CaptureError(
            "New-epoch proposal attempts an unauthorized action."
        )

    if any(
        not isinstance(data[k], str)
        or not SHA.fullmatch(data[k])
        for k in PLAN_DIGEST_FIELDS
    ):
        raise Stage5CaptureError(
            "New-epoch plan references invalid SHA-256 documents."
        )

    return {
        "status": "LOCAL_PROPOSAL_CONTRACT_VALID_UNAPPROVED",
        "file_sha256": sha256(raw).hexdigest(),
        "proposed_valid_from": start.isoformat(),
        "document_references_present": len(
            PLAN_DIGEST_FIELDS
        ),
        "operator_approval_independently_verified": False,
        "new_epoch_approved": False,
    }


def assess_stage5_capture_recovery(
    engine,
    *,
    epoch_id,
    as_of,
    policy_file,
    expected_policy_sha256,
    original_index=None,
    evidence_root=None,
    new_epoch_plan=None,
    max_captures=100,
    max_source_rows=5000,
):
    cutoff = _utc(as_of)

    if (
        not isinstance(epoch_id, str)
        or not SHA.fullmatch(epoch_id)
    ):
        raise Stage5CaptureError(
            "Invalid historical epoch identifier."
        )

    prior = inspect_stage4_forward_receipts(
        engine,
        epoch_id=epoch_id,
        as_of=cutoff,
        policy_file=policy_file,
        expected_policy_sha256=expected_policy_sha256,
        max_captures=max_captures,
        max_source_rows=max_source_rows,
    )

    # Stage 4 verified the policy, including its stored counterpart
    # and its exact expected raw-file hash.
    # Do not call check_active() or change the historical epoch.
    policy = ClockPolicy.load(
        policy_file
    )

    originals = inspect_original_capture_candidates(
        engine,
        index_file=original_index,
        evidence_root=evidence_root,
        policy=policy,
        epoch_id=epoch_id,
        as_of=cutoff,
        capture_limit=max_captures,
    )

    plan = inspect_new_epoch_plan(
        new_epoch_plan,
        epoch_id=epoch_id,
        policy_valid_until=policy.valid_until,
        as_of=cutoff,
    )

    blockers = set(
        prior["blockers"]
    )

    # Stage 4 could not reconstruct lists from deduplicated rows.
    # Optional originals do not change that database limitation.
    blockers.discard(
        "ORIGINAL_PER_CAPTURE_SOURCE_LISTS_UNAVAILABLE"
    )
    blockers.add(
        "ORIGINAL_CAPTURE_LISTS_NOT_RECONSTRUCTIBLE_FROM_STORED_ROWS"
    )

    if originals["status"] == "NOT_SUPPLIED":
        blockers.add(
            "NO_GENUINE_ORIGINAL_PER_CAPTURE_SOURCE_LISTS_SUPPLIED"
        )
    elif not originals["all_inspected_capture_lists_supplied"]:
        blockers.add(
            "ORIGINAL_CAPTURE_LIST_COVERAGE_PARTIAL"
        )

    if plan["status"] == "NOT_SUPPLIED":
        blockers.add(
            "NO_SEPARATELY_REVIEWED_NEW_EPOCH_PLAN_SUPPLIED"
        )

    blockers.update({
        "BROKER_ORIGIN_AND_RECEIPT_TIME_NOT_INDEPENDENTLY_ATTESTED",
        "OPERATOR_RECOVERY_APPROVAL_NOT_INDEPENDENTLY_ATTESTED",
        "NEW_EPOCH_CREATION_AND_COLLECTOR_RESTART_NOT_AUTHORIZED",
    })

    result = {
        "assessment_version": VERSION,
        "epoch_id": epoch_id,
        "as_of": cutoff.isoformat(),
        "stage4_receipt_reinspection_sha256": prior[
            "report_sha256"
        ],
        "stage4_local_receipt_scope": prior[
            "inspected_scope"
        ],
        "stage4_source_lists_cannot_be_reconstructed_from_first_owned_rows": True,
        "source_capture_candidates": originals,
        "new_epoch_candidate_plan": plan,
        "locally_recomputed_original_list_hashes": originals[
            "capture_list_hashes_recomputed"
        ],
        "independently_verified_broker_receipts": False,
        "independently_verified_capture_payload_origin": False,
        "independently_verified_recovery_approval": False,
        "new_epoch_approved": False,
        "research_acceptance": (
            "LOCAL_CAPTURE_SCOPE_INCOMPLETE_EXTERNAL_CUSTODY_PENDING"
            if (
                any(
                    prior["inspected_scope"]["truncated"].values()
                )
                or not prior["inspected_scope"]["captures"]
                or any(
                    prior[
                        "first_owned_rows_outside_capture_scope"
                    ].values()
                )
                or prior["local_tick_query_interval_gaps"]
            )
            else "LOCAL_CAPTURE_STRUCTURE_REVIEWED_EXTERNAL_CUSTODY_PENDING"
        ),
        "required_next_evidence": [
            "LOCATE_TRUE_ORIGINAL_ORDERED_CAPTURE_SOURCE_LISTS_IF_RETAINED",
            "OBTAIN_BROKER_OR_PROVIDER_ATTESTED_CAPTURE_AND_UTC_RECEIPTS",
            "RECONCILE_ORIGINAL_PAYLOAD_HASHES_WITH_INDEPENDENT_CUSTODY",
            "DOCUMENT_FRESH_SESSION_GAP_CHECKS_AND_FIRST_RECEIPT_MONITORING",
            "REQUIRE_SEPARATELY_AUTHENTICATED_OPERATOR_NEW_EPOCH_APPROVAL",
            "REVIEW_CLOCK_CODE_DIVERGENCE_WITHOUT_REBINDING_HISTORICAL_EPOCH",
        ],
        "blockers": sorted(blockers),
        "database_access_mode": "SELECT_ONLY",
        "database_modified": False,
        "files_written": False,
        "broker_connected": False,
        "collector_started": False,
        "historical_epoch_rebound": False,
        "new_epoch_created": False,
        "council_invoked": False,
        "training_authorized": False,
        "execution_authorized": False,
        "read_only": True,
        "final_action": "HOLD",
    }

    result["report_sha256"] = hashed(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--read-only-db",
        action="store_true",
        required=True,
    )
    parser.add_argument(
        "--read-only-evidence",
        action="store_true",
        required=True,
    )
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--policy-file", required=True)
    parser.add_argument("--expected-policy-sha256", required=True)
    parser.add_argument("--original-index")
    parser.add_argument("--evidence-root")
    parser.add_argument("--new-epoch-plan")
    parser.add_argument("--captures", type=int, default=100)
    parser.add_argument("--source-rows", type=int, default=5000)
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    try:
        at = _utc(
            args.as_of
        )

        if at > datetime.now(UTC):
            raise Stage5CaptureError(
                "Future review cutoff refused."
            )

        if (
            not isinstance(args.epoch_id, str)
            or not SHA.fullmatch(args.epoch_id)
        ):
            raise Stage5CaptureError(
                "Invalid historical epoch identifier."
            )

        if (
            not isinstance(args.expected_policy_sha256, str)
            or not SHA.fullmatch(
                args.expected_policy_sha256
            )
        ):
            raise Stage5CaptureError(
                "Expected policy SHA-256 invalid."
            )

        from backend.database.connection import engine

        try:
            result = assess_stage5_capture_recovery(
                engine,
                epoch_id=args.epoch_id,
                as_of=at.isoformat(),
                policy_file=args.policy_file,
                expected_policy_sha256=args.expected_policy_sha256,
                original_index=args.original_index,
                evidence_root=args.evidence_root,
                new_epoch_plan=args.new_epoch_plan,
                max_captures=args.captures,
                max_source_rows=args.source_rows,
            )
        finally:
            engine.dispose()

    except (
        Stage5CaptureError,
        ForwardReceiptReviewError,
        ClockPolicyError,
        ValueError,
        OSError,
    ) as exc:
        print(
            "STAGE 5 CAPTURE RECOVERY REVIEW BLOCKED: "
            + type(exc).__name__
        )
        return 1

    if args.json:
        print(
            json.dumps(
                result,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
        )

    else:
        scope = result[
            "stage4_local_receipt_scope"
        ]

        print(
            "EURUSD ORACLE | Stage 5 S5-09..12 "
            "original capture/recovery review"
        )

        print(
            "Original capture manifests: "
            + str(scope["captures"])
        )

        print(
            "First-owned ticks/bars: %s/%s"
            % (
                scope["first_owned_ticks"],
                scope["first_owned_bars"],
            )
        )

        print(
            "Original source-list documents: "
            + result[
                "source_capture_candidates"
            ]["status"]
        )

        print(
            "Locally recomputed original list hashes: "
            + str(
                result[
                    "locally_recomputed_original_list_hashes"
                ]
            )
        )

        print(
            "Proposed new epoch: "
            + result[
                "new_epoch_candidate_plan"
            ]["status"]
        )

        print(
            "Broker receipts: NOT INDEPENDENTLY ATTESTED"
        )

        for blocker in result["blockers"]:
            print("  BLOCKER " + blocker)

        print(
            "SHA-256: "
            + result["report_sha256"]
        )

        print(
            "SELECT-ONLY | NO EPOCH CHANGE | "
            "NO RESTART | NO COUNCIL | FINAL=HOLD"
        )

    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()

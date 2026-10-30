
"""P8-01..04: deterministic read-only EURUSD M5 OFFLINE research heartbeat.

SYNTHETIC_OFFLINE events are never independent broker/source admissions. Archived
claims may be inspected but never admitted. No producer, collector, Council,
model, clock synchronization, network, ledger write or trading side effect.
No actual research prediction is registered; FINAL=HOLD is unconditional.
"""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re

from backend.runtime.stage7_evidence_board_handover import (
    EPOCH, POLICY_PATH, POLICY_SHA256,
)

VERSION = "EURUSD_STAGE8_M5_OFFLINE_HEARTBEAT_P8_01_04_V1"
UTC = timezone.utc
TIMEFRAME = "M5"
SYMBOL = "EURUSD"
MAX_ENVELOPES = 128
MAX_INDEX_BYTES = 262144
MAX_CANDLE_BYTES = 8192
MAX_PRIOR_BYTES = 65536
MAX_TOTAL_BYTES = 1048576
MAX_LATENESS_SECONDS = 90
MAX_CLOCK_UNCERTAINTY_MS = Decimal("500")
MAX_BAR_TICKS = 10000000
HEX64 = re.compile(r"^[0-9a-f]{64}$")
IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
PRICE = re.compile(r"^(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,8})?$")
UNCERTAINTY = re.compile(r"^(?:0|[1-9][0-9]{0,4})(?:\.[0-9]{1,3})?$")
SENSITIVE = re.compile(
    r"(?i)(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|"
    r"account[_-]?(?:id|number)|bearer|authorization|login)"
)
INDEX_KEYS = {
    "schema_version", "historical_epoch_id", "packet_id", "as_of", "mode",
    "initial_watermark_at", "envelopes",
}
ENVELOPE_KEYS = {
    "event_id", "epoch_id", "symbol", "timeframe", "bar_open_at", "bar_close_at",
    "version_id", "source_digest", "source_admission_claim",
    "first_available_at", "received_at", "watermark_at", "clock_uncertainty_ms",
    "session_context", "candle_relative_path", "candle_sha256",
}
SESSION_KEYS = {
    "session_id", "session_state", "holiday_state", "published_at",
    "first_available_at", "source_digest",
}
CANDLE_KEYS = {
    "symbol", "timeframe", "bar_open_at", "bar_close_at", "is_final",
    "open", "high", "low", "close", "tick_count", "source_digest",
}
PRIOR_KEYS = {
    "schema_version", "historical_epoch_id", "packet_id", "as_of",
    "cycle_receipts",
}
RECEIPT_KEYS = {
    "decision_key", "envelope_fingerprint", "cycle_receipt_sha256",
}
STATES = (
    "RECEIVED", "VALIDATED", "CONTEXT_READY", "ANALYSIS_RUNNING",
    "GOVERNANCE", "REGISTERED", "HELD", "EXPIRED", "FAILED",
)
TRANSITIONS = {
    "RECEIVED": ("VALIDATED", "HELD", "EXPIRED", "FAILED"),
    "VALIDATED": ("CONTEXT_READY", "HELD", "EXPIRED", "FAILED"),
    "CONTEXT_READY": ("ANALYSIS_RUNNING", "HELD", "EXPIRED", "FAILED"),
    "ANALYSIS_RUNNING": ("GOVERNANCE", "HELD", "EXPIRED", "FAILED"),
    "GOVERNANCE": ("REGISTERED", "HELD", "EXPIRED", "FAILED"),
    "REGISTERED": (),
    "HELD": (),
    "EXPIRED": (),
    "FAILED": (),
}


class OfflineHeartbeatError(ValueError):
    """Unsafe or structurally invalid input; cannot infer operational admission."""


def _utc(value):
    if not isinstance(value, str):
        raise OfflineHeartbeatError("Explicit UTC timestamp string required.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (OverflowError, ValueError):
        raise OfflineHeartbeatError("Invalid UTC timestamp.") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise OfflineHeartbeatError("Explicit zero-offset UTC required.")
    return parsed.astimezone(UTC)


def _iso(value):
    return value.isoformat()


def _hex(value, label):
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise OfflineHeartbeatError("SHA-256 required: " + label)
    return value


def _ident(value, label):
    if (
        not isinstance(value, str)
        or not IDENT.fullmatch(value)
        or SENSITIVE.search(value)
    ):
        raise OfflineHeartbeatError(
            "Invalid or sensitive identifier: " + label
        )
    return value


def _strict(row, keys, label):
    if not isinstance(row, dict) or set(row) != keys:
        raise OfflineHeartbeatError("Strict schema mismatch: " + label)


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise OfflineHeartbeatError("Duplicate JSON key refused.")
        obj[key] = value
    return obj


def _decode(raw):
    try:
        obj = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(
                OfflineHeartbeatError("Nonfinite JSON refused.")
            ),
        )
    except OfflineHeartbeatError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise OfflineHeartbeatError("Invalid UTF-8 JSON evidence.") from None
    if not isinstance(obj, dict):
        raise OfflineHeartbeatError("JSON evidence object required.")
    return obj


def _digest(obj):
    return sha256(
        json.dumps(
            obj,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _no_links(path):
    candidate = Path(path).absolute()
    for part in (candidate, *candidate.parents):
        if part.is_symlink() or (
            hasattr(part, "is_junction") and part.is_junction()
        ):
            raise OfflineHeartbeatError("Symlink or junction refused.")
    return candidate


def _read(path, bound):
    candidate = _no_links(path)
    if not candidate.is_file():
        raise OfflineHeartbeatError(
            "Existing ordinary evidence file required."
        )
    try:
        with candidate.open("rb") as handle:
            raw = handle.read(bound + 1)
    except OSError:
        raise OfflineHeartbeatError(
            "Local evidence file unreadable."
        ) from None
    if len(raw) > bound:
        raise OfflineHeartbeatError(
            "Local evidence file exceeds size bound."
        )
    return raw


def _child(root, relative):
    if (
        not isinstance(relative, str)
        or not 1 <= len(relative) <= 180
        or ":" in relative
        or "\\" in relative
        or "@" in relative
        or "\x00" in relative
        or SENSITIVE.search(relative)
        or PurePosixPath(relative).is_absolute()
        or any(
            part in ("", ".", "..")
            for part in relative.split("/")
        )
    ):
        raise OfflineHeartbeatError(
            "Unsafe or sensitive relative evidence path."
        )
    base = _no_links(root)
    if not base.is_dir():
        raise OfflineHeartbeatError(
            "Existing local evidence root required."
        )
    path = _no_links(base.joinpath(*relative.split("/")))
    if not path.resolve().is_relative_to(base.resolve()):
        raise OfflineHeartbeatError("Evidence path escaped root.")
    return path


def _decimal(value, pattern, label, upper):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise OfflineHeartbeatError(
            "Bounded nonnegative decimal required: " + label
        )
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise OfflineHeartbeatError(
            "Invalid decimal: " + label
        ) from None
    if not parsed.is_finite() or parsed > upper:
        raise OfflineHeartbeatError(
            "Decimal exceeds hard limit: " + label
        )
    return parsed


def _m5_bounds(open_at, close_at):
    if (
        open_at.second
        or open_at.microsecond
        or open_at.minute % 5
        or close_at.second
        or close_at.microsecond
        or close_at.minute % 5
        or close_at - open_at != timedelta(minutes=5)
    ):
        raise OfflineHeartbeatError(
            "M5 close/open alignment or duration invalid."
        )


def transition_state(current, target):
    """Pure state-contract check; does not execute or dispatch a Council seat."""
    if current not in TRANSITIONS or target not in TRANSITIONS[current]:
        raise OfflineHeartbeatError(
            "Illegal or terminal cycle state transition."
        )
    return target


def _lineage_key(bar_close):
    return _digest({
        "epoch_id": EPOCH,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "bar_close_at": _iso(bar_close),
    })


def _bar_identity(envelope, artifact_sha):
    return _digest({
        "decision_key": envelope["decision_key"],
        "version_id": envelope["version_id"],
        "source_digest": envelope["source_digest"],
        "candle_sha256": artifact_sha,
    })


def _parse_envelope(item, root, cutoff, mode):
    _strict(item, ENVELOPE_KEYS, "m5_envelope")
    _ident(item["event_id"], "event_id")
    _ident(item["version_id"], "version_id")

    if (
        item["epoch_id"] != EPOCH
        or item["symbol"] != SYMBOL
        or item["timeframe"] != TIMEFRAME
    ):
        raise OfflineHeartbeatError(
            "Historical epoch or EURUSD/M5 binding mismatch."
        )

    if item["source_admission_claim"] != "SYNTHETIC_FIXTURE_ONLY":
        raise OfflineHeartbeatError(
            "Self-asserted live source admission refused."
        )

    source_sha = _hex(item["source_digest"], "source_digest")
    start = _utc(item["bar_open_at"])
    close = _utc(item["bar_close_at"])
    _m5_bounds(start, close)

    available = _utc(item["first_available_at"])
    received = _utc(item["received_at"])
    watermark = _utc(item["watermark_at"])

    if not close <= available <= watermark <= received <= cutoff:
        raise OfflineHeartbeatError(
            "Bar availability, watermark or receipt chronology invalid."
        )

    uncertainty = _decimal(
        item["clock_uncertainty_ms"],
        UNCERTAINTY,
        "clock_uncertainty_ms",
        Decimal("99999"),
    )

    context = item["session_context"]
    _strict(context, SESSION_KEYS, "session_context")
    _ident(context["session_id"], "session_id")
    _hex(context["source_digest"], "session_source_digest")

    if context["session_state"] not in ("OPEN", "CLOSED", "UNKNOWN"):
        raise OfflineHeartbeatError("Invalid session state.")
    if context["holiday_state"] not in ("CLEAR", "HOLIDAY", "UNKNOWN"):
        raise OfflineHeartbeatError("Invalid holiday state.")

    published_context = _utc(context["published_at"])
    available_context = _utc(context["first_available_at"])

    if not published_context <= available_context <= received:
        raise OfflineHeartbeatError(
            "Session/holiday metadata not available by arrival."
        )

    raw = _read(
        _child(root, item["candle_relative_path"]),
        MAX_CANDLE_BYTES,
    )
    artifact_sha = _hex(item["candle_sha256"], "candle_sha256")

    if sha256(raw).hexdigest() != artifact_sha:
        raise OfflineHeartbeatError(
            "M5 artifact byte SHA-256 mismatch."
        )

    candle = _decode(raw)
    _strict(candle, CANDLE_KEYS, "m5_candle")

    if (
        candle["symbol"] != SYMBOL
        or candle["timeframe"] != TIMEFRAME
        or _utc(candle["bar_open_at"]) != start
        or _utc(candle["bar_close_at"]) != close
        or candle["source_digest"] != source_sha
    ):
        raise OfflineHeartbeatError(
            "M5 payload identity or lineage mismatch."
        )

    if type(candle["is_final"]) is not bool:
        raise OfflineHeartbeatError(
            "M5 is_final must be Boolean."
        )

    count = candle["tick_count"]
    if type(count) is not int or not 0 <= count <= MAX_BAR_TICKS:
        raise OfflineHeartbeatError("Invalid M5 tick count.")

    prices = {
        name: _decimal(
            candle[name], PRICE, name, Decimal("999")
        )
        for name in ("open", "high", "low", "close")
    }

    if not all(value > 0 for value in prices.values()):
        raise OfflineHeartbeatError(
            "M5 prices must be strictly positive."
        )

    if not (
        prices["low"] <= prices["open"] <= prices["high"]
        and prices["low"] <= prices["close"] <= prices["high"]
    ):
        raise OfflineHeartbeatError(
            "Inconsistent M5 OHLC range."
        )

    issues = []

    if candle["is_final"] is False:
        issues.append("PARTIAL_M5_CANDLE")
    if count == 0:
        issues.append("ZERO_TICK_M5_CANDLE")
    if received - close > timedelta(seconds=MAX_LATENESS_SECONDS):
        issues.append("STALE_M5_DELIVERY")
    if uncertainty > MAX_CLOCK_UNCERTAINTY_MS:
        issues.append(
            "CLOCK_UNCERTAINTY_ABOVE_DESIGN_CEILING"
        )
    if context["session_state"] != "OPEN":
        issues.append("SESSION_NOT_VERIFIABLY_OPEN")
    if context["holiday_state"] != "CLEAR":
        issues.append("HOLIDAY_CALENDAR_NOT_CLEAR")
    if mode != "SYNTHETIC_OFFLINE":
        issues.append(
            "ARCHIVED_PROVENANCE_NOT_INDEPENDENTLY_ADMITTED"
        )

    decision_key = _lineage_key(close)

    claim = {
        "decision_key": decision_key,
        "event_id": item["event_id"],
        "bar_close": close,
        "received": received,
        "version_id": item["version_id"],
        "source_digest": source_sha,
        "candle_sha256": artifact_sha,
        "session_claim_digest": _digest(context),
        "clock_uncertainty_ms": str(uncertainty),
        "first_available_at": _iso(available),
        "watermark_at": _iso(watermark),
        "issues": sorted(set(issues)),
    }

    claim["bar_identity"] = _bar_identity(
        {**claim}, artifact_sha
    )

    claim["envelope_fingerprint"] = _digest({
        "decision_key": decision_key,
        "bar_identity": claim["bar_identity"],
        "source_digest": source_sha,
        "version_id": item["version_id"],
        "first_available_at": claim["first_available_at"],
        "watermark_at": claim["watermark_at"],
        "received_at": _iso(received),
        "clock_uncertainty_ms": claim["clock_uncertainty_ms"],
        "session_context_sha256": claim["session_claim_digest"],
        "is_final": candle["is_final"],
        "tick_count": count,
    })

    return claim, len(raw)


def _parse_prior(path, cutoff):
    if path is None:
        return {}, None

    raw = _read(path, MAX_PRIOR_BYTES)
    doc = _decode(raw)
    _strict(doc, PRIOR_KEYS, "prior_cycle_receipts")

    if (
        type(doc["schema_version"]) is not int
        or doc["schema_version"] != 1
        or doc["historical_epoch_id"] != EPOCH
    ):
        raise OfflineHeartbeatError(
            "Prior receipt schema or historical epoch mismatch."
        )

    _ident(doc["packet_id"], "packet_id")
    if _utc(doc["as_of"]) > cutoff:
        raise OfflineHeartbeatError(
            "Prior receipt file claims future availability."
        )

    rows = doc["cycle_receipts"]
    if not isinstance(rows, list) or len(rows) > MAX_ENVELOPES:
        raise OfflineHeartbeatError(
            "Prior cycle receipt count invalid."
        )

    result = {}

    for row in rows:
        _strict(row, RECEIPT_KEYS, "prior_receipt")
        key = _hex(row["decision_key"], "decision_key")
        fp = _hex(
            row["envelope_fingerprint"],
            "envelope_fingerprint",
        )
        digest = _hex(
            row["cycle_receipt_sha256"],
            "cycle_receipt_sha256",
        )

        if key in result:
            raise OfflineHeartbeatError(
                "Duplicate prior cycle decision key."
            )

        result[key] = (fp, digest)

    return result, sha256(raw).hexdigest()


def _cycle_receipt(cycle):
    return _digest({
        "decision_key": cycle["decision_key"],
        "bar_identity": cycle["bar_identity"],
        "envelope_fingerprint": cycle["envelope_fingerprint"],
        "state_history": cycle["state_history"],
        "terminal_reasons": cycle["terminal_reasons"],
        "mode": "OFFLINE_RESEARCH_NO_PREDICTION",
    })


def _cycles(claims, initial, prior):
    groups = {}

    for item in claims:
        groups.setdefault(
            item["decision_key"], []
        ).append(item)

    ordered = sorted(
        groups.values(),
        key=lambda entries: (
            min(x["received"] for x in entries),
            min(x["bar_close"] for x in entries),
            min(x["decision_key"] for x in entries),
        ),
    )

    cycles, dead, duplicates, replays, last = (
        [], [], 0, 0, initial
    )

    by_event_id = {}

    for item in claims:
        by_event_id.setdefault(
            item["event_id"], set()
        ).add(
            (
                item["decision_key"],
                item["envelope_fingerprint"],
            )
        )

    colliding_event_ids = {
        event_id
        for event_id, fingerprints in by_event_id.items()
        if len(fingerprints) > 1
    }

    halted = False

    for entries in ordered:
        entries.sort(
            key=lambda r: (
                r["received"],
                r["event_id"],
                r["envelope_fingerprint"],
            )
        )

        base = entries[0]
        key = base["decision_key"]

        fingerprints = {
            r["envelope_fingerprint"]
            for r in entries
        }

        issues = {
            x
            for r in entries
            for x in r["issues"]
        }

        if len(fingerprints) != 1:
            issues.add(
                "CONFLICTING_M5_REVISION_OR_RETRY"
            )
        else:
            duplicates += len(entries) - 1

        if any(
            item["event_id"] in colliding_event_ids
            for item in entries
        ):
            issues.add(
                "REUSED_EVENT_ID_WITH_DIFFERENT_CLAIM"
            )

        if halted:
            issues.add(
                "PREVIOUS_M5_DELIVERY_QUARANTINED"
            )

        if last is not None:
            if base["bar_close"] <= last:
                issues.add(
                    "AT_OR_BEHIND_M5_WATERMARK"
                )
            elif base["bar_close"] - last != timedelta(minutes=5):
                issues.add(
                    "MISSING_M5_BAR_BEFORE_DELIVERY"
                )

        status = "HELD"

        if "STALE_M5_DELIVERY" in issues:
            status = "EXPIRED"

        history = ["RECEIVED"]

        if not issues:
            history.append(
                transition_state("RECEIVED", "VALIDATED")
            )
            history.append(
                transition_state("VALIDATED", "HELD")
            )
            reasons = [
                "OFFLINE_ONLY_NO_INDEPENDENT_SOURCE_OR_OPERATOR_AUTHORIZATION"
            ]
            last = base["bar_close"]
        else:
            history.append(
                transition_state("RECEIVED", status)
            )
            reasons = sorted(issues)

        cycle = {
            "decision_key": key,
            "bar_close_at": _iso(base["bar_close"]),
            "bar_identity": (
                base["bar_identity"]
                if len(fingerprints) == 1
                else None
            ),
            "envelope_fingerprint": (
                base["envelope_fingerprint"]
                if len(fingerprints) == 1
                else None
            ),
            "delivery_count": len(entries),
            "duplicate_deliveries_suppressed": (
                len(entries) - 1
                if len(fingerprints) == 1
                else 0
            ),
            "state_history": history,
            "terminal_state": status,
            "terminal_reasons": reasons,
            "locally_validated_synthetic_candidate": not bool(issues),
            "independently_admitted": False,
            "research_cycle_started": False,
            "prediction_registered": False,
        }

        cycle["cycle_receipt_sha256"] = _cycle_receipt(
            cycle
        )

        if key in prior:
            old_fp, old_receipt = prior[key]

            if (
                old_fp == cycle["envelope_fingerprint"]
                and old_receipt == cycle["cycle_receipt_sha256"]
            ):
                cycle["prior_local_receipt_status"] = (
                    "LOCAL_REPLAY_MATCH_UNATTESTED"
                )
                replays += 1
            else:
                cycle["prior_local_receipt_status"] = (
                    "PRIOR_RECEIPT_CONFLICT_QUARANTINED"
                )
                cycle["locally_validated_synthetic_candidate"] = False
                cycle["state_history"] = [
                    "RECEIVED", "HELD"
                ]
                cycle["terminal_state"] = "HELD"
                cycle["terminal_reasons"] = sorted(
                    set(
                        cycle["terminal_reasons"]
                        + ["PRIOR_LOCAL_RECEIPT_MISMATCH"]
                    )
                )
                cycle["cycle_receipt_sha256"] = _cycle_receipt(
                    cycle
                )

                if last == base["bar_close"]:
                    # Never reuse a suspect watermark as an acceptance receipt.
                    last = None
        else:
            cycle["prior_local_receipt_status"] = (
                "NOT_SUPPLIED"
            )

        if not cycle["locally_validated_synthetic_candidate"]:
            # Later bars cannot escape a broken queue prefix.
            halted = True
            dead.append({
                "decision_key": key,
                "event_ids": sorted({
                    r["event_id"] for r in entries
                }),
                "reason_codes": cycle["terminal_reasons"],
                "quarantine_only_not_written": True,
            })

        cycles.append(cycle)

    return cycles, dead, duplicates, replays, last


def assess_stage8_heartbeat(
    *,
    epoch_id,
    as_of,
    policy_file=POLICY_PATH,
    fixture_index=None,
    fixture_root=None,
    prior_receipt=None,
):
    if epoch_id != EPOCH:
        raise OfflineHeartbeatError(
            "Historical epoch mismatch; rebinding refused."
        )

    cutoff = _utc(as_of)

    if cutoff > datetime.now(UTC):
        raise OfflineHeartbeatError(
            "Future assessment cutoff refused."
        )

    policy_sha = sha256(
        _read(policy_file, 16384)
    ).hexdigest()

    if policy_sha != POLICY_SHA256:
        raise OfflineHeartbeatError(
            "Immutable original policy SHA-256 mismatch."
        )

    if fixture_index is None and fixture_root is not None:
        raise OfflineHeartbeatError(
            "Fixture root without index refused."
        )

    prior, prior_sha = _parse_prior(
        prior_receipt, cutoff
    )

    packet_sha = None
    mode = "NOT_SUPPLIED"
    claims = []
    original_bytes = 0
    initial = None

    if fixture_index is not None:
        if fixture_root is None:
            raise OfflineHeartbeatError(
                "Fixture index requires root directory."
            )

        raw = _read(
            fixture_index, MAX_INDEX_BYTES
        )
        packet = _decode(raw)

        _strict(
            packet, INDEX_KEYS, "m5_offline_index"
        )

        if (
            type(packet["schema_version"]) is not int
            or packet["schema_version"] != 1
            or packet["historical_epoch_id"] != EPOCH
        ):
            raise OfflineHeartbeatError(
                "Index historical epoch/schema mismatch."
            )

        _ident(
            packet["packet_id"], "packet_id"
        )

        if _utc(packet["as_of"]) > cutoff:
            raise OfflineHeartbeatError(
                "Index not available at assessment cutoff."
            )

        mode = packet["mode"]

        if mode not in (
            "SYNTHETIC_OFFLINE",
            "ARCHIVED_OFFLINE_UNATTESTED",
        ):
            raise OfflineHeartbeatError(
                "Only offline fixture modes are supported."
            )

        initial = (
            None
            if packet["initial_watermark_at"] is None
            else _utc(packet["initial_watermark_at"])
        )

        if initial is not None:
            _m5_bounds(
                initial - timedelta(minutes=5),
                initial,
            )
            if initial > cutoff:
                raise OfflineHeartbeatError(
                    "Initial watermark after cutoff."
                )

        rows = packet["envelopes"]

        if (
            not isinstance(rows, list)
            or len(rows) > MAX_ENVELOPES
        ):
            raise OfflineHeartbeatError(
                "Envelope count invalid."
            )

        paths = {}

        for row in rows:
            claim, size = _parse_envelope(
                row,
                fixture_root,
                _utc(packet["as_of"]),
                mode,
            )

            path = row["candle_relative_path"]

            if (
                path in paths
                and paths[path] != claim["candle_sha256"]
            ):
                raise OfflineHeartbeatError(
                    "Same artifact path has conflicting hashes."
                )

            if path not in paths:
                original_bytes += size

            if original_bytes > MAX_TOTAL_BYTES:
                raise OfflineHeartbeatError(
                    "Total M5 artifact size bound exceeded."
                )

            paths[path] = claim["candle_sha256"]
            claims.append(claim)

        packet_sha = sha256(raw).hexdigest()

    cycles, dead, duplicates, replays, final_watermark = _cycles(
        claims, initial, prior
    )

    candidate_count = sum(
        x["locally_validated_synthetic_candidate"]
        for x in cycles
    )

    blockers = [
        "STAGE7_INDEPENDENT_GATES_G01_G07_NOT_ACCEPTED",
        "THIRTEEN_COUNCIL_SOURCE_FAMILIES_NOT_INDEPENDENTLY_ADMITTED",
        "NO_SEPARATE_STAGE8_SHADOW_AUTHORIZATION",
        "NO_COLLECTOR_OR_COUNCIL_DISPATCH_AUTHORITY",
        "NO_INDEPENDENT_PREDICTION_RECEIPT_OR_REGISTRATION",
        "RESEARCH_CYCLE_STATE_MACHINE_DESIGN_ONLY",
    ]

    if packet_sha is None:
        blockers.append(
            "NO_OFFLINE_M5_FIXTURE_INDEX_SUPPLIED"
        )

    if dead:
        blockers.append(
            "M5_DEAD_LETTER_QUARANTINE_OR_EXPIRATION"
        )

    report = {
        "version": VERSION,
        "scope": "P8_01_TO_P8_04_OFFLINE_M5_HEARTBEAT",
        "historical_epoch_id": EPOCH,
        "historical_policy_sha256": policy_sha,
        "as_of": _iso(cutoff),
        "source_mode": mode,
        "fixture_index_sha256": packet_sha,
        "prior_local_receipts_sha256": prior_sha,
        "original_m5_bytes_locally_hashed": original_bytes,
        "input_envelopes_count": len(claims),
        "unique_decision_keys": len(cycles),
        "duplicate_deliveries_suppressed": duplicates,
        "prior_local_receipt_replays": replays,
        "locally_validated_synthetic_candidates": candidate_count,
        "independently_admitted_research_cycles": 0,
        "cycle_records": cycles,
        "dead_letter_candidates": dead,
        "watermark": {
            "initial_at": (
                None
                if initial is None
                else _iso(initial)
            ),
            "last_locally_validated_at": (
                None
                if final_watermark is None
                else _iso(final_watermark)
            ),
            "persisted": False,
            "independently_attested": False,
        },
        "cycle_states": list(STATES),
        "legal_state_transitions": {
            k: list(v)
            for k, v in TRANSITIONS.items()
        },
        "cycle_dispatch_enabled": False,
        "forecast_registration_enabled": False,
        "independent_external_gates_verified": 0,
        "independent_source_families_verified": 0,
        "historical_epoch_rebound": False,
        "read_only": True,
        "files_written": False,
        "network_accessed": False,
        "broker_connected": False,
        "collector_started": False,
        "council_invoked": False,
        "model_loaded": False,
        "training_authorized": False,
        "settlement_invoked": False,
        "paper_orders_authorized": False,
        "live_orders_authorized": False,
        "future_epoch_created": False,
        "operator_approval_granted": False,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(blockers),
        "final_action": "HOLD",
    }

    report["report_sha256"] = _digest(report)
    return report


def run(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__
    )
    parser.add_argument(
        "--read-only-fixtures",
        action="store_true",
        required=True,
    )
    parser.add_argument(
        "--read-only-heartbeat",
        action="store_true",
        required=True,
    )
    parser.add_argument(
        "--epoch-id", required=True
    )
    parser.add_argument(
        "--as-of", required=True
    )
    parser.add_argument(
        "--policy-file", default=POLICY_PATH
    )
    parser.add_argument("--fixture-index")
    parser.add_argument("--fixture-root")
    parser.add_argument("--prior-receipt")
    parser.add_argument(
        "--json", action="store_true"
    )

    opts = parser.parse_args(argv)

    try:
        report = assess_stage8_heartbeat(
            epoch_id=opts.epoch_id,
            as_of=opts.as_of,
            policy_file=opts.policy_file,
            fixture_index=opts.fixture_index,
            fixture_root=opts.fixture_root,
            prior_receipt=opts.prior_receipt,
        )
    except (
        OfflineHeartbeatError,
        OSError,
        ValueError,
        TypeError,
    ):
        print(
            "STAGE 8 OFFLINE HEARTBEAT BLOCKED: "
            "Unsafe or invalid local evidence."
        )
        return 1

    if opts.json:
        print(
            json.dumps(
                report,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
        )
    else:
        print(
            "EURUSD ORACLE | Stage 8 P8-01..04 "
            "offline M5 research heartbeat"
        )
        print(
            "Historical clock policy: ORIGINAL_BYTES_MATCH_PIN"
        )
        print(
            "Source mode: " + report["source_mode"]
        )
        print(
            "Input envelopes: %d | Unique M5 decision keys: %d | "
            "Duplicate deliveries suppressed: %d" % (
                report["input_envelopes_count"],
                report["unique_decision_keys"],
                report["duplicate_deliveries_suppressed"],
            )
        )
        print(
            "Local synthetic candidates: %d | "
            "Dead-letter candidates: %d" % (
                report["locally_validated_synthetic_candidates"],
                len(report["dead_letter_candidates"]),
            )
        )
        print(
            "Independent Stage7 gates/source families: 0/7 | 0/13"
        )
        print(
            "Council cycles dispatched: 0 | Forecasts registered: 0"
        )
        for blocker in report["blockers"]:
            print("  BLOCKER " + blocker)
        print(
            "SHA-256: " + report["report_sha256"]
        )
        print(
            "READ-ONLY | SYNTHETIC/OFFLINE | NO COLLECTOR | "
            "NO COUNCIL | FINAL=HOLD"
        )

    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()

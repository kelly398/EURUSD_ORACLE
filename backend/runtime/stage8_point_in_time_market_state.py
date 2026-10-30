
"""P8-05..08: synthetic OFFLINE point-in-time EURUSD M5 market-state candidates.

All indices and locally hashed bytes are UNATTESTED. A synthetic M5 heartbeat
is a prerequisite, never an operational permission. Council manifest and code
are only read; model/agent code is not executed. The caller cannot dispatch a
seat, register a prediction, connect to a broker, or turn FINAL=HOLD into GO.
"""

import argparse
import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re

from backend.runtime.stage6_council_context_readiness import (
    FAMILIES, DERIVED_INPUTS, CouncilContextError, inspect_static_council,
)
from backend.runtime.stage7_family_vintage_admission import family_contracts, KINDS
from backend.runtime.stage8_m5_offline_heartbeat import (
    EPOCH, POLICY_PATH, POLICY_SHA256, _lineage_key, assess_stage8_heartbeat,
)

VERSION = "EURUSD_STAGE8_MARKET_STATE_P8_05_08_V1"
RULESET_VERSION = "SYNTHETIC_M5_TREND_VOL_SESSION_EVENT_LIQUIDITY_V1"
FEATURE_SCHEMA = "ORACLE_STAGE8_OFFLINE_CONTEXT_V1"
UTC = timezone.utc
SOURCE_KINDS = set(FAMILIES)
DERIVED_LABELS = set(DERIVED_INPUTS)
MAX_INDEX_BYTES = 262144
MAX_PAYLOAD_BYTES = 131072
MAX_TOTAL_BYTES = 12582912
MAX_RECORDS = 128
MAX_PARENTS = 12
MAX_BARS = 48
HEX64 = re.compile(r"^[0-9a-f]{64}$")
IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
NUM = re.compile(r"^-?(?:0|[1-9][0-9]{5,})(?:\.[0-9]{1,8})?$")
SHORT_NUM = re.compile(r"^-?(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,8})?$")
SENSITIVE = re.compile(
    r"(?i)(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|"
    r"account[_-]?(?:id|number)|bearer|authorization|login)"
)
INDEX_KEYS = {
    "schema_version", "historical_epoch_id", "packet_id", "as_of",
    "decision_at", "decision_key", "mode", "records",
}
RECORD_KEYS = {
    "record_id", "record_type", "label", "event_id", "vintage_id",
    "publication_kind", "provider_claim", "event_at", "observed_at",
    "published_at", "first_available_at", "received_at", "supersedes_id",
    "parent_ids", "transform_relative_path", "transform_sha256",
    "feature_schema_version", "source_channel", "value_state",
    "relative_path", "artifact_sha256", "revocation_claim",
}
PAYLOAD_KEYS = {"schema_version", "label", "value_state", "facts"}
BAR_KEYS = {"close_at", "open", "high", "low", "close"}
REGIME_SPEC = {
    "ruleset": RULESET_VERSION,
    "trend_threshold": "0.00050",
    "high_mean_range_threshold": "0.00150",
    "wide_spread_threshold": "0.00020",
    "event_risk_pre_release_seconds": 5400,
    "event_risk_post_release_seconds": 900,
    "session_source": "STAGE8_B1_VERIFIED_SYNTHETIC_M5_ENVELOPE",
    "missing": "NOT_SUPPLIED_NEVER_ZERO",
}
FACT_KEYS = {
    "market_state": {"trend", "volatility", "liquidity", "session"},
    "candles": {"bars"}, "ticks": {"bid", "ask", "tick_at"},
    "cot_data": {"net_position"}, "ecb_data": {"policy_rate_pct"},
    "fed_data": {"policy_rate_pct"},
    "economic_calendar": {"event_risk", "release_at", "phase"},
    "cross_asset_data": {"risk_mode", "quote_at"},
    "calendar_coverage": {"event_risk", "coverage"},
    "risk_state": {"risk_mode"}, "risk_limits": {"risk_budget_pct"},
    "reliability": {"score"}, "synthesis_policy": {"mode"},
}
ENUMS = {
    "trend": {"UP", "DOWN", "FLAT", "UNKNOWN"},
    "volatility": {"HIGH", "LOW", "UNKNOWN"},
    "liquidity": {"TIGHT", "WIDE", "UNKNOWN"},
    "session": {"OPEN", "CLOSED", "UNKNOWN"},
    "event_risk": {"LOW", "MEDIUM", "HIGH", "UNKNOWN"},
    "phase": {"SCHEDULE", "ACTUAL"},
    "risk_mode": {"CLEAR", "HOLD", "RISK_ON", "RISK_OFF", "NEUTRAL"},
    "coverage": {"COMPLETE", "PARTIAL", "UNKNOWN"},
    "mode": {"RESEARCH_ONLY"},
}
STALE_CODES = {
    "ticks": "STALE_TICK_INPUT", "fed_data": "STALE_FED_INPUT",
    "cross_asset_data": "STALE_CROSS_ASSET_INPUT",
    "economic_calendar": "STALE_CALENDAR_INPUT",
    "risk_limits": "STALE_POLICY_INPUT",
    "synthesis_policy": "STALE_POLICY_INPUT",
}


class MarketStateError(ValueError):
    """Malformed, unsafe or noncausal local evidence (never independent proof)."""


def _utc(value):
    if not isinstance(value, str):
        raise MarketStateError("Explicit UTC timestamp string required.")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise MarketStateError("Invalid UTC timestamp.") from None
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise MarketStateError("Explicit zero-offset UTC required.")
    return stamp.astimezone(UTC)


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")).hexdigest()


def _strict(value, keys, name):
    if not isinstance(value, dict) or set(value) != keys:
        raise MarketStateError("Strict schema mismatch: " + name)


def _unique(pairs):
    result = {}
    for key, val in pairs:
        if key in result:
            raise MarketStateError("Duplicate JSON key refused.")
        result[key] = val
    return result


def _json(raw):
    try:
        val = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                         parse_constant=lambda _: (_ for _ in ()).throw(
                             MarketStateError("Nonfinite JSON refused.")))
    except MarketStateError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise MarketStateError("Invalid UTF-8 JSON evidence.") from None
    if not isinstance(val, dict):
        raise MarketStateError("JSON object required.")
    return val


def _id(value, label):
    if not isinstance(value, str) or not IDENT.fullmatch(value) or SENSITIVE.search(value):
        raise MarketStateError("Invalid or sensitive identifier: " + label)
    return value


def _sha(value, label):
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise MarketStateError("SHA-256 required: " + label)
    return value


def _no_links(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise MarketStateError("Symlink or junction refused.")
    return path


def _read(path, maximum):
    path = _no_links(path)
    if not path.is_file():
        raise MarketStateError("Existing ordinary local evidence file required.")
    try:
        with path.open("rb") as stream:
            raw = stream.read(maximum + 1)
    except OSError:
        raise MarketStateError("Local evidence unreadable.") from None
    if len(raw) > maximum:
        raise MarketStateError("Evidence size exceeds configured hard bound.")
    return raw


def _child(root, relative):
    if (not isinstance(relative, str) or not 1 <= len(relative) <= 180
            or ":" in relative or "\\" in relative or "@" in relative
            or "\x00" in relative or SENSITIVE.search(relative)
            or PurePosixPath(relative).is_absolute()
            or any(x in ("", ".", "..") for x in relative.split("/"))):
        raise MarketStateError("Unsafe or sensitive relative evidence path.")
    base = _no_links(root)
    if not base.is_dir():
        raise MarketStateError("Existing evidence root directory required.")
    target = _no_links(base.joinpath(*relative.split("/")))
    if not target.resolve().is_relative_to(base.resolve()):
        raise MarketStateError("Artifact path escapes evidence root.")
    return target


def _number(value, field, *, positive=False):
    if not isinstance(value, str) or not SHORT_NUM.fullmatch(value):
        raise MarketStateError("Bounded decimal string required: " + field)
    try:
        x = Decimal(value)
    except InvalidOperation:
        raise MarketStateError("Invalid numeric value.") from None
    if not x.is_finite() or (positive and x <= 0):
        raise MarketStateError("Nonpositive or nonfinite decimal: " + field)
    return x


def _m5_bar(bar, earlier):
    _strict(bar, BAR_KEYS, "m5_history_bar")
    t = _utc(bar["close_at"])
    if t.second or t.microsecond or t.minute % 5:
        raise MarketStateError("Unaligned M5 source candle.")
    if earlier is not None and t - earlier != timedelta(minutes=5):
        raise MarketStateError("Noncontiguous M5 source history.")
    p = {key: _number(bar[key], key, positive=True)
         for key in ("open", "high", "low", "close")}
    if p["high"] < p["low"] or not p["low"] <= p["open"] <= p["high"] or not p["low"] <= p["close"] <= p["high"]:
        raise MarketStateError("Inconsistent M5 source OHLC.")
    return t, p


def _facts(label, state, payload, event_at, published):
    _strict(payload, PAYLOAD_KEYS, "family_or_feature_payload")
    if payload["schema_version"] != 1 or payload["label"] != label or payload["value_state"] != state:
        raise MarketStateError("Payload schema, label or missing-value mismatch.")
    facts = payload["facts"]
    if not isinstance(facts, dict):
        raise MarketStateError("Payload facts object required.")
    if state == "MISSING":
        if facts:
            raise MarketStateError("Missing source cannot carry values or implicit zero.")
        return {}
    if label in DERIVED_LABELS:
        _strict(facts, {"feature_version_sha256"}, "derived_feature_facts")
        _sha(facts["feature_version_sha256"], "feature_version_sha256")
        return facts
    _strict(facts, FACT_KEYS[label], "canonical_family_facts")
    for k, val in facts.items():
        if k in ENUMS:
            if not isinstance(val, str) or val not in ENUMS[k]:
                raise MarketStateError("Invalid enumerated family claim: " + k)
        elif k in ("net_position", "policy_rate_pct", "risk_budget_pct", "score", "bid", "ask"):
            _number(val, k, positive=k in ("bid", "ask"))
        elif k in ("tick_at", "quote_at", "release_at"):
            _utc(val)
    if label == "candles":
        bars = facts["bars"]
        if not isinstance(bars, list) or not 3 <= len(bars) <= MAX_BARS:
            raise MarketStateError("Synthetic M5 history requires 3-48 candles.")
        prev, last = None, None
        for bar in bars:
            prev, last = _m5_bar(bar, prev)
        if prev != event_at or prev > published:
            raise MarketStateError("M5 history closes after its event/publication.")
    if label == "ticks":
        if _number(facts["ask"], "ask") < _number(facts["bid"], "bid"):
            raise MarketStateError("Tick ask smaller than bid.")
        if _utc(facts["tick_at"]) != event_at:
            raise MarketStateError("Tick claim timestamp disagrees with source event.")
    if label == "cross_asset_data" and _utc(facts["quote_at"]) != event_at:
        raise MarketStateError("Cross-asset observation timestamp mismatch.")
    if label == "economic_calendar":
        release = _utc(facts["release_at"])
        phase = facts["phase"]
        if phase == "ACTUAL" and (release > published or release > event_at):
            raise MarketStateError("Calendar actual before its scheduled release.")
        if phase == "SCHEDULE" and event_at != release:
            raise MarketStateError("Scheduled calendar event mismatch.")
    return facts


def _load(index_path, root, cutoff, decision, key):
    if root is None:
        raise MarketStateError("Context index requires evidence root.")
    raw = _read(index_path, MAX_INDEX_BYTES)
    doc = _json(raw)
    _strict(doc, INDEX_KEYS, "m5_market_state_index")
    if (type(doc["schema_version"]) is not int or doc["schema_version"] != 1
            or doc["historical_epoch_id"] != EPOCH
            or _utc(doc["as_of"]) > cutoff
            or _utc(doc["decision_at"]) != decision
            or _sha(doc["decision_key"], "decision_key") != key
            or doc["mode"] != "SYNTHETIC_OFFLINE"):
        raise MarketStateError("Context epoch, cutoff, decision key or offline mode mismatch.")
    _id(doc["packet_id"], "packet_id")
    rows = doc["records"]
    if not isinstance(rows, list) or len(rows) > MAX_RECORDS:
        raise MarketStateError("Context record count out of bounds.")
    seen_ids, paths, code_cache, total_bytes, records = set(), set(), {}, 0, []
    for item in rows:
        _strict(item, RECORD_KEYS, "market_state_record")
        rid = _id(item["record_id"], "record_id")
        if rid in seen_ids:
            raise MarketStateError("Duplicate point-in-time record ID.")
        seen_ids.add(rid)
        label = item["label"]
        kind = item["record_type"]
        if (kind == "SOURCE" and label not in SOURCE_KINDS) or (kind == "FEATURE" and label not in DERIVED_LABELS) or kind not in ("SOURCE", "FEATURE"):
            raise MarketStateError("Unknown canonical family or derived label.")
        _id(item["event_id"], "event_id")
        _id(item["vintage_id"], "vintage_id")
        _id(item["provider_claim"], "provider_claim")
        if type(item["revocation_claim"]) is not bool:
            raise MarketStateError("Local revocation claim must be Boolean.")
        if item["value_state"] not in ("PRESENT", "MISSING"):
            raise MarketStateError("Missing value must be declared explicitly.")
        publication_kind = item["publication_kind"]
        if (kind == "FEATURE" and publication_kind != "DERIVED_FEATURE") or (
            kind == "SOURCE" and publication_kind not in KINDS[label]
        ):
            raise MarketStateError("Incompatible family publication kind.")
        if kind == "SOURCE":
            allowed = family_contracts()[label]["permitted_source_channels"]
            if item["source_channel"] not in allowed or item["feature_schema_version"] is not None:
                raise MarketStateError("Source channel/schema contradict family.")
        elif item["source_channel"] != "DERIVED_TRANSFORM" or item["feature_schema_version"] != FEATURE_SCHEMA:
            raise MarketStateError("Feature schema/channel mismatch.")
        times = {name: _utc(item[name]) for name in (
            "event_at", "observed_at", "published_at", "first_available_at", "received_at"
        )}
        if not (times["observed_at"] <= times["published_at"]
                <= times["first_available_at"] <= times["received_at"]
                <= _utc(doc["as_of"])):
            raise MarketStateError("Source publication/receipt chronology invalid.")
        if publication_kind not in ("SCHEDULE", "FUTURE_EFFECTIVE_POLICY") and times["event_at"] > times["published_at"]:
            raise MarketStateError("Actual/derived event precedes alleged publication.")
        parents = item["parent_ids"]
        if (not isinstance(parents, list) or len(parents) > MAX_PARENTS
                or any(not isinstance(p, str) for p in parents)):
            raise MarketStateError("Invalid parent list shape.")
        for p in parents:
            _id(p, "parent_id")
        if len(set(parents)) != len(parents) or rid in parents:
            raise MarketStateError("Duplicate/self ancestry refused.")
        previous = item["supersedes_id"]
        if previous is not None:
            _id(previous, "supersedes_id")
        if publication_kind == "REVISION" and previous is None:
            raise MarketStateError("Revision must point to earlier vintage.")
        if previous is not None and publication_kind not in ("REVISION", "DERIVED_FEATURE"):
            raise MarketStateError("Undeclared supersession refused.")
        required_code = kind == "FEATURE" or item["source_channel"] == "DERIVED_TRANSFORM"
        relative_code, code_sha = item["transform_relative_path"], item["transform_sha256"]
        if (relative_code is None) != (code_sha is None) or (required_code and relative_code is None):
            raise MarketStateError("Derived transformation path/hash required and paired.")
        if relative_code is not None:
            if not relative_code.endswith(".py"):
                raise MarketStateError("Only AST-inspectable Python transformation accepted.")
            raw_code = _read(_child(root, relative_code), MAX_PAYLOAD_BYTES)
            if sha256(raw_code).hexdigest() != _sha(code_sha, "transform_sha256"):
                raise MarketStateError("Transformation bytes SHA-256 mismatch.")
            if relative_code not in code_cache:
                try:
                    ast.parse(raw_code.decode("utf-8-sig"))
                except (UnicodeError, SyntaxError, RecursionError):
                    raise MarketStateError("Invalid transformation AST.") from None
                code_cache[relative_code] = code_sha
                total_bytes += len(raw_code)
            elif code_cache[relative_code] != code_sha:
                raise MarketStateError("Conflicting transformation-code digest.")
        relative = item["relative_path"]
        if relative in paths:
            raise MarketStateError("Repeated source payload path.")
        paths.add(relative)
        content = _read(_child(root, relative), MAX_PAYLOAD_BYTES)
        value_sha = _sha(item["artifact_sha256"], "artifact_sha256")
        if sha256(content).hexdigest() != value_sha:
            raise MarketStateError("Source payload SHA-256 mismatch.")
        total_bytes += len(content)
        if total_bytes > MAX_TOTAL_BYTES:
            raise MarketStateError("Cumulative context artifact size exceeded.")
        facts = _facts(label, item["value_state"], _json(content),
                       times["event_at"], times["published_at"])
        records.append({
            "id": rid, "label": label, "record_type": kind,
            "event_id": item["event_id"], "vintage_id": item["vintage_id"],
            "publication_kind": publication_kind, "times": times,
            "value_state": item["value_state"], "facts": facts,
            "provider": item["provider_claim"], "source_channel": item["source_channel"],
            "artifact_sha256": value_sha, "transform_sha256": code_sha,
            "parent_ids": parents, "supersedes_id": previous,
            "revoked": item["revocation_claim"],
        })
    return {"records": records, "index_sha256": sha256(raw).hexdigest(),
            "artifacts_locally_hashed": len(rows), "bytes_locally_hashed": total_bytes}


def _is_visible(row, decision):
    if any(row["times"][field] > decision for field in (
        "observed_at", "published_at", "first_available_at", "received_at"
    )):
        return False
    return (row["publication_kind"] in ("SCHEDULE", "FUTURE_EFFECTIVE_POLICY")
            or row["times"]["event_at"] <= decision)


def _select(packet, decision):
    rows = [] if packet is None else packet["records"]
    visible = [r for r in rows if _is_visible(r, decision)]
    lookup = {r["id"]: r for r in visible}
    groups, selected, findings = {}, {}, {}
    for row in visible:
        groups.setdefault((row["record_type"], row["label"], row["event_id"]), []).append(row)
    for key, versions in groups.items():
        versions.sort(key=lambda r: (r["times"]["first_available_at"], r["times"]["received_at"], r["id"]))
        for old, new in zip(versions, versions[1:]):
            if new["supersedes_id"] != old["id"] and not (
                old["publication_kind"] == "SCHEDULE" and new["publication_kind"] in ("SCHEDULE", "ACTUAL")
            ):
                findings.setdefault(new["label"], set()).add("UNDECLARED_PARALLEL_VINTAGE")
        selected[key] = versions[-1]
    for label in list(FAMILIES) + list(DERIVED_INPUTS):
        versions = [r for k, r in selected.items() if k[1] == label]
        if len(versions) > 1:
            findings.setdefault(label, set()).add("MULTIPLE_VISIBLE_EVENTS_UNRESOLVED")
        if versions:
            selected[label] = max(versions, key=lambda r: (r["times"]["received_at"], r["id"]))
    current = {r["id"] for k, r in selected.items() if isinstance(k, tuple)}
    cache, visiting = {}, set()

    def lineage(row):
        rid = row["id"]
        if rid in cache:
            return cache[rid]
        if rid in visiting:
            return {"issues": {"CYCLIC_CONTEXT_ANCESTRY"}, "families": set(), "sources": set()}
        visiting.add(rid)
        issues = set()
        families = {row["label"]} if row["record_type"] == "SOURCE" else set()
        sources = {rid} if row["record_type"] == "SOURCE" else set()
        if row["revoked"]:
            issues.add("LOCAL_REVOCATION_CLAIM")
        if row["record_type"] == "FEATURE" and not row["parent_ids"]:
            issues.add("DERIVED_FEATURE_ANCESTRY_MISSING")
        if row["record_type"] == "SOURCE" and row["source_channel"] == "DERIVED_TRANSFORM" and not row["parent_ids"]:
            issues.add("DERIVED_SOURCE_ANCESTRY_MISSING")
        for parent_id in row["parent_ids"]:
            parent = lookup.get(parent_id)
            if parent is None:
                issues.add("PARENT_NOT_CAUSALLY_VISIBLE")
                continue
            if parent["times"]["first_available_at"] > row["times"]["first_available_at"]:
                issues.add("NONCAUSAL_DERIVATION_TIMELINE")
            if parent_id not in current:
                issues.add("SUPERSEDED_PARENT_VINTAGE_PIN")
            sub = lineage(parent)
            issues.update(sub["issues"])
            sources.update(sub["sources"])
            families.update(sub["families"])
        if row["record_type"] == "FEATURE" and not set(DERIVED_INPUTS[row["label"]]).issubset(families):
            issues.add("REQUIRED_FEATURE_SOURCE_ANCESTRY_MISSING")
        if row["record_type"] == "SOURCE" and row["source_channel"] == "DERIVED_TRANSFORM":
            parents_expected = set(family_contracts()[row["label"]]["declared_parent_families"])
            if not parents_expected.issubset(families):
                issues.add("DERIVED_SOURCE_PARENT_FAMILY_INCOMPLETE")
        if row["supersedes_id"] is not None:
            prior = lookup.get(row["supersedes_id"])
            if prior is None or prior["label"] != row["label"] or prior["event_id"] != row["event_id"] or prior["times"]["first_available_at"] >= row["times"]["first_available_at"]:
                issues.add("REVISION_ANCESTRY_INCOMPATIBLE")
        visiting.remove(rid)
        result = {"issues": issues, "families": families, "sources": sources}
        cache[rid] = result
        return result

    descriptions = {}
    for label in list(FAMILIES) + list(DERIVED_INPUTS):
        row = selected.get(label)
        if row is None:
            # Later evidence must not change even the missing-field marker in an
            # earlier immutable context. Count withheld rows only in audit data.
            descriptions[label] = {
                "label": label, "category": "SOURCE" if label in FAMILIES else "FEATURE",
                "status": "NOT_SUPPLIED",
                "selected_record_id": None, "selected_vintage_id": None,
                "observed_at": None, "first_available_at": None,
                "artifact_sha256": None, "transform_sha256": None,
                "provenance_digest": None, "received_at": None,
                "publication_kind": None, "event_id": None,
                "fact_digest": None, "facts": None, "source_ancestry": [],
                "issues": [], "freshness_seconds": None,
                "independently_attested": False,
            }
            continue
        sub = lineage(row)
        issues = set(sub["issues"]) | findings.get(label, set())
        if row["value_state"] == "MISSING":
            issues.add("EXPLICIT_MISSING_VALUE_NOT_ZERO")
        if label in FAMILIES:
            limit = family_contracts()[label]["proposed_max_age_seconds"]
        else:
            limit = 900 if label != "tick_features" else 120
        age = int((decision - row["times"]["first_available_at"]).total_seconds())
        if age > limit and row["publication_kind"] != "FUTURE_EFFECTIVE_POLICY":
            issues.add(STALE_CODES.get(label, "STALE_SOURCE_OR_FEATURE"))
        if label == "cross_asset_data" and "ticks" in selected:
            tick = selected["ticks"]
            if abs((row["times"]["event_at"] - tick["times"]["event_at"]).total_seconds()) > 120:
                issues.add("CROSS_ASSET_TICK_TIMESTAMP_CONTRADICTION")
        if label == "economic_calendar" and row["value_state"] == "PRESENT":
            if (row["publication_kind"] == "SCHEDULE") != (row["facts"]["phase"] == "SCHEDULE"):
                issues.add("CALENDAR_SCHEDULE_ACTUAL_PHASE_CONTRADICTION")
        state = ("QUARANTINED" if issues else "LOCAL_CANDIDATE_UNATTESTED")
        descriptions[label] = {
            "label": label, "category": row["record_type"], "status": state,
            "selected_record_id": row["id"], "selected_vintage_id": row["vintage_id"],
            "observed_at": row["times"]["observed_at"].isoformat(),
            "first_available_at": row["times"]["first_available_at"].isoformat(),
            "artifact_sha256": row["artifact_sha256"], "transform_sha256": row["transform_sha256"],
            "received_at": row["times"]["received_at"].isoformat(),
            "publication_kind": row["publication_kind"], "event_id": row["event_id"],
            "provenance_digest": _digest({
                "provider_claim": row["provider"], "source_channel": row["source_channel"],
                "publication_kind": row["publication_kind"], "event_id": row["event_id"],
                "parent_ids": row["parent_ids"], "supersedes_id": row["supersedes_id"],
                "revocation_claim": row["revoked"],
                "received_at": row["times"]["received_at"].isoformat(),
            }),
            "fact_digest": _digest(row["facts"]), "facts": row["facts"],
            "source_ancestry": sorted(sub["sources"]), "issues": sorted(issues),
            "freshness_seconds": age, "independently_attested": False,
        }
    return descriptions, len(rows) - len(visible)


def _regime(fields, heartbeat, decision):
    def eligible(name):
        entry = fields[name]
        return entry["facts"] if entry["status"] == "LOCAL_CANDIDATE_UNATTESTED" else None

    observations = {}
    candles, ticks = eligible("candles"), eligible("ticks")
    if candles is not None and heartbeat.get("_m5") is not None:
        last = candles["bars"][-1]
        expected = heartbeat["_m5"]
        if (last["close_at"] != expected["bar_close_at"] or any(
            _number(last[k], k) != _number(expected[k], k)
            for k in ("open", "high", "low", "close")
        )):
            fields["candles"]["issues"].append("CANDLE_HEARTBEAT_OHLC_MISMATCH")
            candles = None
    if candles is None:
        trend, volatility = "UNKNOWN", "UNKNOWN"
    else:
        bars = candles["bars"]
        first_close, last_close = _number(bars[0]["close"], "close"), _number(bars[-1]["close"], "close")
        delta = last_close - first_close
        trend = "UP" if delta >= Decimal(REGIME_SPEC["trend_threshold"]) else (
            "DOWN" if delta <= -Decimal(REGIME_SPEC["trend_threshold"]) else "FLAT"
        )
        ranges = [_number(x["high"], "high") - _number(x["low"], "low") for x in bars]
        mean_range = sum(ranges, Decimal(0)) / len(ranges)
        volatility = "HIGH" if mean_range >= Decimal(REGIME_SPEC["high_mean_range_threshold"]) else "LOW"
    if ticks is None:
        liquidity = "UNKNOWN"
    else:
        spread = _number(ticks["ask"], "ask") - _number(ticks["bid"], "bid")
        liquidity = "WIDE" if spread >= Decimal(REGIME_SPEC["wide_spread_threshold"]) else "TIGHT"
    calendar = eligible("economic_calendar")
    event_risk = "UNKNOWN"
    if calendar is not None:
        release_lag = (_utc(calendar["release_at"]) - decision).total_seconds()
        if (-REGIME_SPEC["event_risk_post_release_seconds"] <= release_lag
                <= REGIME_SPEC["event_risk_pre_release_seconds"]):
            event_risk = calendar["event_risk"]
    session = "OPEN" if heartbeat["eligible"] else "UNKNOWN"
    computed = {
        "trend": trend, "volatility": volatility, "session": session,
        "event_risk": event_risk, "liquidity": liquidity,
    }
    source_map = {
        "trend": ("candles",), "volatility": ("candles",),
        "session": ("HEARTBEAT_B1",), "event_risk": ("economic_calendar",),
        "liquidity": ("ticks",),
    }
    for name, value in computed.items():
        sources = source_map[name]
        observations[name] = {
            "value": value,
            "status": "LOCAL_RULE_RESULT_UNATTESTED" if value != "UNKNOWN" else "NOT_SUPPLIED",
            "calculation_rule_version": RULESET_VERSION,
            "source_labels": list(sources),
            "source_record_ids": [fields[x]["selected_record_id"] for x in sources if x != "HEARTBEAT_B1"],
            "reason": ("SYNTHETIC_FIXED_RULE_NOT_APPROVED" if value != "UNKNOWN" else "CAUSAL_INPUT_NOT_SUPPLIED"),
        }
    state = eligible("market_state")
    if state is not None:
        for metric in ("trend", "volatility", "liquidity", "session"):
            if computed[metric] != "UNKNOWN" and state[metric] != "UNKNOWN" and state[metric] != computed[metric]:
                fields["market_state"]["issues"].append("MARKET_STATE_" + metric.upper() + "_CONTRADICTION")
    coverage = eligible("calendar_coverage")
    if coverage is not None and event_risk != "UNKNOWN" and coverage["event_risk"] != "UNKNOWN" and coverage["event_risk"] != event_risk:
        fields["calendar_coverage"]["issues"].append("CALENDAR_COVERAGE_EVENT_RISK_CONTRADICTION")
    if "ticks" in fields and ticks is not None and candles is not None:
        last = candles["bars"][-1]
        bid = _number(ticks["bid"], "bid")
        if not _number(last["low"], "low") <= bid <= _number(last["high"], "high"):
            fields["ticks"]["issues"].append("TICK_BID_OUTSIDE_M5_CANDLE")
    for row in fields.values():
        if row["issues"]:
            row["issues"] = sorted(set(row["issues"]))
            row["status"] = "QUARANTINED"
    return observations


def _propagate_quarantines(fields):
    """A downstream version cannot remain eligible after a selected ancestor fails."""
    indexed = {d["selected_record_id"]: d for d in fields.values()
               if d["selected_record_id"] is not None}
    changed = True
    while changed:
        changed = False
        for d in fields.values():
            if d["status"] != "LOCAL_CANDIDATE_UNATTESTED":
                continue
            bad = any(indexed[parent]["status"] == "QUARANTINED"
                      for parent in d["source_ancestry"]
                      if parent in indexed and parent != d["selected_record_id"])
            if bad:
                d["issues"] = sorted(set(d["issues"] + ["QUARANTINED_ANCESTOR_SOURCE"]))
                d["status"] = "QUARANTINED"
                changed = True


def _seats(manifest, fields):
    results = []
    for seat in manifest["seats"]:
        required = list(seat["required_source_families"])
        derived = list(seat["derived_labels"])
        reasons = []
        for label in required + derived:
            status = fields[label]["status"]
            if status != "LOCAL_CANDIDATE_UNATTESTED":
                reason = next((x for x in fields[label]["issues"] if x in STALE_CODES.values()), None)
                reasons.append((reason or "REQUIRED_INPUT_" + status) + ":" + label)
        if seat["needs_model_artifact_lineage"]:
            reasons.append("MODEL_ARTIFACT_NOT_INDEPENDENTLY_ADMITTED")
        if seat["needs_internal_stage_opinions"]:
            reasons.append("INTERNAL_PRIOR_STAGE_OPINIONS_NOT_SUPPLIED")
        local_compatible = not reasons
        reasons.append("G05_G06_G07_AND_STAGE8_OPERATOR_AUTHORIZATION_ABSENT")
        optional_missing = [x for x in FAMILIES if x not in required
                            and fields[x]["status"] != "LOCAL_CANDIDATE_UNATTESTED"]
        results.append({
            "seat_number": seat["seat"], "seat_id": seat["agent_id"],
            "stage": seat["stage"], "required_family_labels": required,
            "required_derived_labels": derived, "optional_family_absences": optional_missing,
            "local_contract_compatible": local_compatible,
            "reasons": sorted(reasons), "decision": "ABSTAIN",
            "seat_dispatched": False,
        })
    return results


def _heartbeat(index, root, decision_key, decision_at, cutoff, policy_file):
    if index is None or root is None:
        raise MarketStateError("Point-in-time packet requires synthetic heartbeat evidence.")
    try:
        result = assess_stage8_heartbeat(
            epoch_id=EPOCH, as_of=cutoff.isoformat(), policy_file=policy_file,
            fixture_index=index, fixture_root=root,
        )
    except (OSError, ValueError, TypeError):
        raise MarketStateError("Synthetic heartbeat rejected by Stage 8 Batch 1.") from None
    matches = [r for r in result["cycle_records"] if r["decision_key"] == decision_key]
    if len(matches) != 1 or not matches[0]["locally_validated_synthetic_candidate"]:
        raise MarketStateError("Heartbeat decision key missing, expired or quarantined.")
    if _utc(matches[0]["bar_close_at"]) > decision_at:
        raise MarketStateError("Heartbeat M5 bar later than context decision.")
    packet = _json(_read(index, MAX_INDEX_BYTES))
    matching_envelopes = [e for e in packet["envelopes"]
                          if _lineage_key(_utc(e["bar_close_at"])) == decision_key]
    if not matching_envelopes:
        raise MarketStateError("Verified heartbeat envelope cannot be located.")
    candle = _json(_read(_child(root, matching_envelopes[0]["candle_relative_path"]), 8192))
    m5 = {k: candle[k] for k in ("open", "high", "low", "close")}
    m5["bar_close_at"] = candle["bar_close_at"]
    return {
        "eligible": True, "bar_close_at": matches[0]["bar_close_at"],
        "_m5": m5,
        "bar_identity": matches[0]["bar_identity"],
        "cycle_receipt_sha256": matches[0]["cycle_receipt_sha256"],
        "heartbeat_report_sha256": result["report_sha256"],
        "independently_admitted": False,
    }


def _one(packet, decision, heartbeat, manifest, code_sha):
    fields, withheld = _select(packet, decision)
    regime = _regime(fields, heartbeat, decision)
    _propagate_quarantines(fields)
    seats = _seats(manifest, fields)
    ordered = [fields[label] for label in list(FAMILIES) + list(DERIVED_INPUTS)]
    immutable_material = {
        "historical_epoch_id": EPOCH, "decision_at": decision.isoformat(),
        "decision_key": _lineage_key(_utc(heartbeat["bar_close_at"])),
        "bar_identity": heartbeat["bar_identity"],
        "heartbeat_cycle_receipt_sha256": heartbeat["cycle_receipt_sha256"],
        "market_state_ruleset": RULESET_VERSION,
        "regime_rule_digest": _digest(REGIME_SPEC),
        "market_state_code_sha256": code_sha,
        "feature_schema": FEATURE_SCHEMA,
        "manifest_sha256": manifest["manifest_sha256"],
        "contracts_sha256": manifest["contracts_sha256"],
        "fields": ordered, "regime_attribution": regime,
        "seat_input_contracts": seats,
    }
    return {
        "status": ("LOCAL_CONTEXT_QUARANTINED" if any(x["status"] == "QUARANTINED" for x in ordered)
                   else "LOCAL_OFFLINE_CONTEXT_CANDIDATE_UNATTESTED"),
        "decision_at": decision.isoformat(), "decision_key": immutable_material["decision_key"],
        "context_sha256": _digest(immutable_material),
        "serialization": "SORTED_KEYS_COMPACT_JSON_SHA256_V1",
        "context_fields": ordered, "regime_attribution": regime,
        "seat_input_preflight": seats, "seat_count": len(seats),
        "local_candidate_family_count": sum(fields[f]["status"] == "LOCAL_CANDIDATE_UNATTESTED" for f in FAMILIES),
        "quarantined_family_count": sum(fields[f]["status"] == "QUARANTINED" for f in FAMILIES),
        "locally_compatible_seat_count": sum(s["local_contract_compatible"] for s in seats),
        "research_cycle_dispatched": False, "council_context_admitted": False,
        "rule_spec_sha256": immutable_material["regime_rule_digest"],
        "market_state_code_sha256": code_sha,
        "manifest_sha256": manifest["manifest_sha256"],
        "contracts_sha256": manifest["contracts_sha256"],
    }, withheld


def assess_stage8_market_state(*, epoch_id, as_of, decision_at=None,
                               bar_close_at=None, policy_file=POLICY_PATH,
                               context_index=None, evidence_root=None,
                               heartbeat_index=None, heartbeat_root=None,
                               replay_index=None, replay_root=None,
                               contracts_file="backend/agents/contracts.py",
                               manifest_file="backend/agents/council_manifest.py"):
    if epoch_id != EPOCH:
        raise MarketStateError("Historical epoch mismatch; rebinding refused.")
    cutoff = _utc(as_of)
    decision = cutoff if decision_at is None else _utc(decision_at)
    if cutoff > datetime.now(UTC) or decision > cutoff:
        raise MarketStateError("Future cutoff or decision after assessment cutoff.")
    if sha256(_read(policy_file, 16384)).hexdigest() != POLICY_SHA256:
        raise MarketStateError("Immutable October 27 policy SHA-256 mismatch.")
    if (context_index is None) != (evidence_root is None):
        raise MarketStateError("Context index and evidence root must be paired.")
    if (replay_index is None) != (replay_root is None):
        raise MarketStateError("Replay index and replay root must be paired.")
    if replay_index is not None and context_index is None:
        raise MarketStateError("Replay requires primary context index.")
    try:
        manifest = inspect_static_council(contracts_file=contracts_file,
                                          manifest_file=manifest_file)
    except (CouncilContextError, OSError):
        raise MarketStateError("Static canonical Council manifest unavailable or unsafe.") from None
    if tuple(manifest["families"]) != tuple(FAMILIES) or manifest["static_seats"] != 31:
        raise MarketStateError("Canonical seat/source manifest changed.")
    code_sha = sha256(_read(__file__, 1048576)).hexdigest()
    no_packet = context_index is None
    if no_packet:
        if bar_close_at is not None or heartbeat_index is not None or heartbeat_root is not None:
            raise MarketStateError("Heartbeat parameters require a context index.")
        # Do not invent a bar or a decision key when no packet is supplied.
        heartbeat = {"eligible": False, "bar_close_at": None, "bar_identity": None,
                     "cycle_receipt_sha256": None, "heartbeat_report_sha256": None,
                     "independently_admitted": False}
        rows = []
        fields = [_empty(label) for label in list(FAMILIES) + list(DERIVED_INPUTS)]
        regime = _regime({r["label"]: r for r in fields}, heartbeat, decision)
        seats = _seats(manifest, {r["label"]: r for r in fields})
        snapshot = {
            "status": "NOT_SUPPLIED", "decision_at": decision.isoformat(),
            "decision_key": None, "context_sha256": None,
            "context_fields": fields, "regime_attribution": regime,
            "seat_input_preflight": seats, "seat_count": len(seats),
            "local_candidate_family_count": 0, "quarantined_family_count": 0,
            "locally_compatible_seat_count": 0,
            "research_cycle_dispatched": False, "council_context_admitted": False,
            "rule_spec_sha256": _digest(REGIME_SPEC),
            "market_state_code_sha256": code_sha,
            "manifest_sha256": manifest["manifest_sha256"],
            "contracts_sha256": manifest["contracts_sha256"],
            "serialization": "SORTED_KEYS_COMPACT_JSON_SHA256_V1",
        }
        packet = None
        withheld = 0
    else:
        if bar_close_at is None:
            raise MarketStateError("Context packet requires completed M5 bar close.")
        bar_close = _utc(bar_close_at)
        if bar_close > decision or bar_close.second or bar_close.microsecond or bar_close.minute % 5:
            raise MarketStateError("Completed M5 close not aligned or after decision.")
        key = _lineage_key(bar_close)
        heartbeat = _heartbeat(heartbeat_index, heartbeat_root, key, decision, cutoff, policy_file)
        packet = _load(context_index, evidence_root, cutoff, decision, key)
        snapshot, withheld = _one(packet, decision, heartbeat, manifest, code_sha)
    comparison = {"status": "NOT_REQUESTED", "same_decision_at": True,
                  "causal_context_unchanged": None, "independent_replay_attested": False}
    if replay_index is not None:
        later = _load(replay_index, replay_root, cutoff, decision, key)
        other, replay_withheld = _one(later, decision, heartbeat, manifest, code_sha)
        same = snapshot["context_sha256"] == other["context_sha256"]
        before_fields = {x["label"]: x for x in snapshot["context_fields"]}
        after_fields = {x["label"]: x for x in other["context_fields"]}
        changed_labels = sorted(k for k in before_fields if before_fields[k] != after_fields[k])
        regime_changes = []
        for dimension, earlier in snapshot["regime_attribution"].items():
            newer = other["regime_attribution"][dimension]
            if earlier["value"] == newer["value"]:
                continue
            deps = [x for x in earlier["source_labels"] if x != "HEARTBEAT_B1"]
            reasons = []
            if any(before_fields[x]["selected_vintage_id"] != after_fields[x]["selected_vintage_id"] for x in deps):
                reasons.append("INPUT_VINTAGE_CHANGED")
            if any(before_fields[x]["fact_digest"] != after_fields[x]["fact_digest"] for x in deps):
                reasons.append("INPUT_CONTENT_CHANGED")
            regime_changes.append({
                "dimension": dimension, "before": earlier["value"],
                "after": newer["value"], "input_labels": deps,
                "reason_codes": reasons or ["UNEXPLAINED_CLASSIFICATION_CHANGE_QUARANTINE"],
            })
        comparison = {
            "status": "CAUSAL_CONTEXT_STABLE" if same else "VISIBLE_CONTEXT_CHANGED_QUARANTINED",
            "same_decision_at": True, "causal_context_unchanged": same,
            "independent_replay_attested": False,
            "changed_visible_family_or_feature_labels": changed_labels,
            "regime_classification_changes": regime_changes,
            "comparison_index_sha256": later["index_sha256"],
            "comparison_postdecision_withheld": replay_withheld,
        }
    blockers = [
        "G05_FAMILY_SOURCE_ADMISSION_NOT_INDEPENDENTLY_VERIFIED",
        "G06_FEATURE_AND_MODEL_ANCESTRY_NOT_INDEPENDENTLY_VERIFIED",
        "G07_RISK_AND_OPERATOR_APPROVAL_NOT_GRANTED",
        "NO_STAGE8_SHADOW_OR_COUNCIL_DISPATCH_AUTHORIZATION",
        "FRESHNESS_AND_MARKET_REGIME_RULES_RESEARCH_PROPOSAL_ONLY",
    ]
    if no_packet:
        blockers.append("NO_POINT_IN_TIME_MARKET_STATE_PACKET_SUPPLIED")
    if snapshot["quarantined_family_count"]:
        blockers.append("CONTRADICTORY_OR_QUARANTINED_LOCAL_CONTEXT")
    if comparison["status"] == "VISIBLE_CONTEXT_CHANGED_QUARANTINED":
        blockers.append("CAUSAL_REPLAY_CHANGED_PREVIOUS_DECISION_CONTEXT")
    report = {
        "version": VERSION, "scope": "P8_05_TO_P8_08_POINT_IN_TIME_MARKET_STATE",
        "historical_epoch_id": EPOCH, "as_of": cutoff.isoformat(),
        "historical_policy_sha256": POLICY_SHA256,
        "evidence": {
            "status": "NOT_SUPPLIED" if packet is None else "LOCAL_HASH_MATCHED_UNATTESTED",
            "index_sha256": None if packet is None else packet["index_sha256"],
            "locally_hashed_artifacts": 0 if packet is None else packet["artifacts_locally_hashed"],
            "locally_hashed_bytes": 0 if packet is None else packet["bytes_locally_hashed"],
            "postdecision_records_withheld": withheld,
            "raw_artifact_bytes_exported": False,
        },
        "heartbeat": {k: v for k, v in heartbeat.items() if not k.startswith("_")},
        "context": snapshot,
        "replay_comparison": comparison,
        "independent_external_gates_verified": 0,
        "independent_source_families_verified": 0,
        "independent_council_contexts_admitted": 0,
        "read_only": True, "files_written": False,
        "historical_epoch_rebound": False, "network_accessed": False,
        "broker_connected": False, "collector_started": False,
        "council_invoked": False, "model_loaded": False,
        "training_authorized": False, "settlement_invoked": False,
        "paper_orders_authorized": False, "live_orders_authorized": False,
        "future_epoch_created": False, "operator_approval_granted": False,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(blockers), "final_action": "HOLD",
    }
    report["report_sha256"] = _digest(report)
    return report


def _empty(label):
    return {
        "label": label, "category": "SOURCE" if label in FAMILIES else "FEATURE",
        "status": "NOT_SUPPLIED", "selected_record_id": None,
        "selected_vintage_id": None, "observed_at": None,
        "first_available_at": None, "artifact_sha256": None,
        "transform_sha256": None, "provenance_digest": None,
        "received_at": None, "publication_kind": None, "event_id": None,
        "fact_digest": None, "facts": None,
        "source_ancestry": [], "issues": [], "freshness_seconds": None,
        "independently_attested": False,
    }


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-market-state", action="store_true", required=True)
    parser.add_argument("--read-only-sources", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--decision-at")
    parser.add_argument("--bar-close-at")
    parser.add_argument("--policy-file", default=POLICY_PATH)
    parser.add_argument("--context-index")
    parser.add_argument("--evidence-root")
    parser.add_argument("--heartbeat-index")
    parser.add_argument("--heartbeat-root")
    parser.add_argument("--replay-index")
    parser.add_argument("--replay-root")
    parser.add_argument("--contracts-file", default="backend/agents/contracts.py")
    parser.add_argument("--manifest-file", default="backend/agents/council_manifest.py")
    parser.add_argument("--json", action="store_true")
    opts = parser.parse_args(argv)
    try:
        report = assess_stage8_market_state(
            epoch_id=opts.epoch_id, as_of=opts.as_of,
            decision_at=opts.decision_at, bar_close_at=opts.bar_close_at,
            policy_file=opts.policy_file, context_index=opts.context_index,
            evidence_root=opts.evidence_root, heartbeat_index=opts.heartbeat_index,
            heartbeat_root=opts.heartbeat_root, replay_index=opts.replay_index,
            replay_root=opts.replay_root, contracts_file=opts.contracts_file,
            manifest_file=opts.manifest_file,
        )
    except (MarketStateError, OSError, ValueError, TypeError):
        print("STAGE 8 MARKET STATE BLOCKED: Invalid, unsafe or noncausal offline evidence.")
        return 1
    if opts.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    else:
        snap = report["context"]
        print("EURUSD ORACLE | Stage 8 P8-05..08 offline point-in-time Market State Engine")
        print("Historical clock policy: ORIGINAL_BYTES_MATCH_PIN")
        print("Context: " + snap["status"])
        print("Canonical families/derived labels: 13/6 | Static seats: " + str(snap["seat_count"]))
        print("Local candidate families: %d | Quarantined: %d" % (
            snap["local_candidate_family_count"], snap["quarantined_family_count"]))
        print("Context SHA-256: " + str(snap["context_sha256"]))
        print("Replay: " + report["replay_comparison"]["status"])
        print("Independent gates/families: 0/11 | 0/13 | Council seats dispatched: 0")
        for item in report["blockers"]:
            print("  BLOCKER " + item)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | SYNTHETIC ONLY | NO MODEL | NO COUNCIL | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()

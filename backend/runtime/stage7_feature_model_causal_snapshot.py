
"""Stage 7 P7-17..20: OFFLINE feature/model ancestry and causal snapshot proposals.

The index and every local artifact are untrusted CANDIDATE evidence. Matching
hashes and replay consistency do not independently authenticate source origin,
clock, model validity, G06 or Council admission. No files are written; no model
is deserialized or executed. No broker, network, collector, Council, training,
settlement, orders or historical-epoch rebinding are permitted.
"""

import argparse
import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re

from backend.runtime.stage6_council_context_readiness import FAMILIES, DERIVED_INPUTS
from backend.runtime.stage7_family_vintage_admission import family_contracts, KINDS

VERSION = "EURUSD_STAGE7_FEATURE_MODEL_CAUSAL_P7_17_20_V1"
EPOCH = "75311e239cd5c295d3e08e03c358488fb123a46bfc6d342dd31493d95710aa33"
POLICY_PATH = "docs/clock_profiles/hfm_forward_20261027.json"
POLICY_SHA256 = "e34b7a29f78f5865f4b0fe7c4ad778e4e8b650d9943792eee019a48bdf2fe088"
FORECAST_SPEC = "RESEARCH_M5_PREDECISION_V1"
FEATURE_SCHEMA = "ORACLE_STAGE7_FEATURE_SCHEMA_V1"
EVAL_PLAN_SPEC = "RESEARCH_FROZEN_EVALUATION_V1"
REQUIRED_REGIMES = ("TREND", "RANGE", "HIGH_VOLATILITY")
FEATURE_MAX_AGE_S = {
    "market_structure": 900, "feature_vector": 900, "tick_features": 120,
    "liquidity_state": 300, "session_state": 900, "rates_data": 2592000,
}
UTC = timezone.utc
HEX = re.compile(r"^[0-9a-f]{64}$")
IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
NUMBER = re.compile(r"^(?:0|[1-9][0-9]{0,4})(?:\.[0-9]{1,3})?$")
SENSITIVE = re.compile(
    r"(?i)(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|"
    r"account[_-]?(?:id|number)|bearer|authorization|login)"
)
INDEX_KEYS = {"schema_version", "historical_epoch_id", "packet_id", "as_of", "sources", "features", "model", "seats"}
SOURCE_KEYS = {
    "record_id", "family", "event_id", "vintage_id", "kind", "provider_claim",
    "observed_at", "event_at", "published_at", "first_available_at", "received_at",
    "uncertainty_ms", "artifact_path", "artifact_sha256", "supersedes_id",
    "parent_ids", "transform_path", "transform_sha256", "revocation_claim", "value_state",
}
FEATURE_KEYS = {
    "record_id", "label", "event_id", "vintage_id", "feature_schema_version",
    "observed_at", "event_at", "published_at", "first_available_at", "received_at",
    "uncertainty_ms", "artifact_path", "artifact_sha256", "supersedes_id",
    "parent_ids", "transform_path", "transform_sha256",
}
MODEL_KEYS = {
    "model_id", "forecast_spec_version", "feature_schema_version", "required_features",
    "feature_version_pins",
    "model_path", "model_sha256", "training_dataset_path", "training_dataset_sha256",
    "training_cutoff_at", "last_training_label_matured_at", "training_data_available_at",
    "built_at", "first_available_at", "regimes_trained", "risk_limits_record_id",
    "synthesis_policy_record_id", "evaluation_plan_spec_version", "evaluation_plan_path",
    "evaluation_plan_sha256", "evaluation_plan_available_at",
}
SEAT_KEYS = {"seat_id", "required_labels", "requires_model", "expected_regime"}
MAX_INDEX_BYTES = 262144
MAX_ARTIFACT_BYTES = 1048576
MAX_TOTAL_BYTES = 12582912
MAX_RECORDS = 128
MAX_SEATS = 31
MAX_PARENTS = 12
MAX_UNCERTAINTY = Decimal("10000")


class FeatureModelError(ValueError):
    """Invalid local candidate; cannot authorize any operational activity."""


def _utc(value):
    if not isinstance(value, str):
        raise FeatureModelError("Explicit UTC timestamp string required.")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise FeatureModelError("Invalid UTC timestamp.") from None
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise FeatureModelError("Explicit zero-offset UTC required.")
    return stamp.astimezone(UTC)


def _iso(dt):
    return dt.isoformat()


def _ident(value, name):
    if not isinstance(value, str) or not IDENT.fullmatch(value) or SENSITIVE.search(value):
        raise FeatureModelError("Invalid or sensitive identifier: " + name)
    return value


def _hex(value, name):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise FeatureModelError("Expected SHA-256: " + name)
    return value


def _number(value, name):
    if not isinstance(value, str) or not NUMBER.fullmatch(value):
        raise FeatureModelError("Bounded nonnegative decimal string required: " + name)
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise FeatureModelError("Invalid uncertainty decimal.") from None
    if not number.is_finite() or number > MAX_UNCERTAINTY:
        raise FeatureModelError("Uncertainty exceeds bound.")
    return number


def _strict(value, fields, label):
    if not isinstance(value, dict) or set(value) != fields:
        raise FeatureModelError("Strict schema mismatch: " + label)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FeatureModelError("Duplicate JSON key refused.")
        result[key] = value
    return result


def _decode(raw):
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(
                               FeatureModelError("Nonfinite JSON refused.")))
    except FeatureModelError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise FeatureModelError("Invalid UTF-8 JSON evidence.") from None
    if not isinstance(value, dict):
        raise FeatureModelError("JSON object required.")
    return value


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")).hexdigest()


def _no_links(path):
    target = Path(path).absolute()
    for part in (target, *target.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise FeatureModelError("Symlink or junction refused.")
    return target


def _read(path, maximum):
    target = _no_links(path)
    if not target.is_file():
        raise FeatureModelError("Existing ordinary evidence file required.")
    try:
        with target.open("rb") as stream:
            data = stream.read(maximum + 1)
    except OSError:
        raise FeatureModelError("Evidence file cannot be read.") from None
    if len(data) > maximum:
        raise FeatureModelError("Evidence file size limit exceeded.")
    return data


def _child(root, name):
    if (not isinstance(name, str) or not 1 <= len(name) <= 180
            or "\\" in name or ":" in name or "@" in name or "\x00" in name
            or SENSITIVE.search(name) or PurePosixPath(name).is_absolute()
            or any(part in ("", ".", "..") for part in name.split("/"))):
        raise FeatureModelError("Unsafe or sensitive relative artifact path.")
    base = _no_links(root)
    if not base.is_dir():
        raise FeatureModelError("Existing evidence root directory required.")
    target = _no_links(base.joinpath(*name.split("/")))
    if not target.resolve().is_relative_to(base.resolve()):
        raise FeatureModelError("Artifact path escapes evidence root.")
    return target


def _parse_times(row, as_of, *, feature):
    fields = ("observed_at", "event_at", "published_at", "first_available_at", "received_at")
    times = {key: _utc(row[key]) for key in fields}
    if not (times["observed_at"] <= times["published_at"] <=
            times["first_available_at"] <= times["received_at"] <= as_of):
        raise FeatureModelError("Evidence publication/receipt chronology invalid.")
    if feature and times["event_at"] > times["published_at"]:
        raise FeatureModelError("Derived feature observes an unpublished future event.")
    return times


def _parents(value, rid):
    if (not isinstance(value, list) or len(value) > MAX_PARENTS
            or any(not isinstance(x, str) for x in value)):
        raise FeatureModelError("Invalid dependency list shape.")
    for parent in value:
        _ident(parent, "parent_id")
    if len(set(value)) != len(value) or rid in value:
        raise FeatureModelError("Duplicate/self dependency refused.")
    return value


def _load(index_path, root, review_cutoff):
    if root is None:
        raise FeatureModelError("Evidence root required with index.")
    raw = _read(index_path, MAX_INDEX_BYTES)
    doc = _decode(raw)
    _strict(doc, INDEX_KEYS, "feature_model_index")
    if (type(doc["schema_version"]) is not int or doc["schema_version"] != 1
            or doc["historical_epoch_id"] != EPOCH):
        raise FeatureModelError("Immutable historical epoch or schema mismatch.")
    _ident(doc["packet_id"], "packet_id")
    as_of = _utc(doc["as_of"])
    if as_of > review_cutoff:
        raise FeatureModelError("Evidence index as-of later than review cutoff.")
    sources, features, seats = doc["sources"], doc["features"], doc["seats"]
    if (not isinstance(sources, list) or not isinstance(features, list)
            or len(sources) + len(features) > MAX_RECORDS
            or not isinstance(seats, list) or len(seats) > MAX_SEATS):
        raise FeatureModelError("Index record or seat count invalid.")
    if tuple(FAMILIES) != tuple(family_contracts()):
        raise FeatureModelError("Stage 6/7 family contracts disagree.")
    if set(DERIVED_INPUTS) != set(FEATURE_MAX_AGE_S):
        raise FeatureModelError("Stage 6 derived-label contract changed.")

    file_cache, artifact_paths, seen_ids, entries, total_bytes = {}, set(), set(), [], 0
    validated_code = set()

    def verified(path, digest, *, code=False):
        nonlocal total_bytes
        expected = _hex(digest, "artifact/transform hash")
        if path not in file_cache:
            raw_file = _read(_child(root, path), MAX_ARTIFACT_BYTES)
            file_cache[path] = sha256(raw_file).hexdigest()
            total_bytes += len(raw_file)
            if total_bytes > MAX_TOTAL_BYTES:
                raise FeatureModelError("Total artifact bytes exceeded.")
        if file_cache[path] != expected:
            raise FeatureModelError("Local artifact/transform SHA-256 mismatch.")
        if code and path not in validated_code:
            try:
                ast.parse(_read(_child(root, path), MAX_ARTIFACT_BYTES).decode("utf-8-sig"))
            except (UnicodeError, SyntaxError, RecursionError):
                raise FeatureModelError("Transformation code AST invalid.") from None
            validated_code.add(path)
        return expected

    for category, records, required_fields in (
        ("source", sources, SOURCE_KEYS), ("feature", features, FEATURE_KEYS)
    ):
        for item in records:
            _strict(item, required_fields, category)
            rid = _ident(item["record_id"], "record_id")
            if rid in seen_ids:
                raise FeatureModelError("Duplicate source/feature record ID.")
            seen_ids.add(rid)
            if category == "source":
                label = item["family"]
                if not isinstance(label, str) or label not in FAMILIES:
                    raise FeatureModelError("Unknown canonical family.")
                if item["kind"] not in KINDS[label]:
                    raise FeatureModelError("Family publication kind incompatible.")
                _ident(item["provider_claim"], "provider_claim")
                if type(item["revocation_claim"]) is not bool:
                    raise FeatureModelError("Revocation flag must be boolean.")
                if item["value_state"] not in ("PRESENT", "MISSING"):
                    raise FeatureModelError("Explicit PRESENT or MISSING required.")
            else:
                label = item["label"]
                if not isinstance(label, str) or label not in DERIVED_INPUTS:
                    raise FeatureModelError("Unknown canonical derived label.")
                _ident(item["feature_schema_version"], "feature_schema_version")
            _ident(item["event_id"], "event_id")
            _ident(item["vintage_id"], "vintage_id")
            times = _parse_times(item, as_of, feature=category == "feature")
            _parents(item["parent_ids"], rid)
            if item["supersedes_id"] is not None:
                _ident(item["supersedes_id"], "supersedes_id")
            if (category == "source" and item["kind"] == "REVISION"
                    and item["supersedes_id"] is None):
                raise FeatureModelError("Source revision must name previous vintage.")
            if (category == "source" and item["kind"] != "REVISION"
                    and item["supersedes_id"] is not None):
                raise FeatureModelError("Only source REVISION may supersede a source.")
            if (item["transform_path"] is None) != (item["transform_sha256"] is None):
                raise FeatureModelError("Transform code path/hash must be paired.")
            needs_code = category == "feature" or label in (
                "market_state", "calendar_coverage", "risk_state"
            )
            if needs_code and item["transform_path"] is None:
                raise FeatureModelError("Derived node requires pinned transformation code.")
            transform = None
            if item["transform_path"] is not None:
                if not item["transform_path"].endswith(".py"):
                    raise FeatureModelError("Only inspectable Python transform code supported.")
                transform = verified(item["transform_path"], item["transform_sha256"], code=True)
            path = item["artifact_path"]
            if path in artifact_paths:
                raise FeatureModelError("Duplicate raw evidence artifact path.")
            artifact_paths.add(path)
            digest = verified(path, item["artifact_sha256"])
            entries.append({
                "id": rid, "category": category, "label": label, "event": item["event_id"],
                "vintage": item["vintage_id"], "times": times,
                "uncertainty": _number(item["uncertainty_ms"], "uncertainty_ms"),
                "artifact_sha256": digest, "transform_sha256": transform,
                "parents": item["parent_ids"], "supersedes": item["supersedes_id"],
                "kind": item["kind"] if category == "source" else "DERIVED_FEATURE",
                "revocation": item["revocation_claim"] if category == "source" else False,
                "value_state": item["value_state"] if category == "source" else "PRESENT",
                "feature_schema": item["feature_schema_version"] if category == "feature" else None,
            })

    model = None
    if doc["model"] is not None:
        m = doc["model"]
        _strict(m, MODEL_KEYS, "model_record")
        _ident(m["model_id"], "model_id")
        for field in ("forecast_spec_version", "feature_schema_version", "evaluation_plan_spec_version",
                      "risk_limits_record_id", "synthesis_policy_record_id"):
            _ident(m[field], field)
        needed = m["required_features"]
        if (not isinstance(needed, list) or not needed or len(needed) > len(DERIVED_INPUTS)
                or any(not isinstance(x, str) or x not in DERIVED_INPUTS for x in needed)
                or len(set(needed)) != len(needed)):
            raise FeatureModelError("Invalid model required feature list.")
        pins = m["feature_version_pins"]
        if (not isinstance(pins, dict) or set(pins) != set(needed)):
            raise FeatureModelError("Model feature-version pins must cover exactly required features.")
        for label, pinned_sha in pins.items():
            _hex(pinned_sha, "feature_version_pins." + label)
        regimes = m["regimes_trained"]
        if (not isinstance(regimes, list) or len(regimes) > 10
                or any(not isinstance(x, str) for x in regimes)
                or any(_ident(x, "regime") != x for x in regimes)
                or len(set(regimes)) != len(regimes)):
            raise FeatureModelError("Invalid trained-regime list.")
        for path_field, hash_field in (
            ("model_path", "model_sha256"),
            ("training_dataset_path", "training_dataset_sha256"),
            ("evaluation_plan_path", "evaluation_plan_sha256"),
        ):
            verified(m[path_field], m[hash_field])
        model = {**m, "times": {
            field: _utc(m[field]) for field in (
                "training_cutoff_at", "last_training_label_matured_at",
                "training_data_available_at", "built_at", "first_available_at",
                "evaluation_plan_available_at",
            )
        }}
        if any(model["times"][field] > as_of for field in (
            "first_available_at", "evaluation_plan_available_at", "training_data_available_at"
        )):
            raise FeatureModelError("Model claim availability after packet as-of.")

    seat_rows, seat_ids = [], set()
    allowed = set(FAMILIES) | set(DERIVED_INPUTS)
    for seat in seats:
        _strict(seat, SEAT_KEYS, "seat_preflight")
        sid = _ident(seat["seat_id"], "seat_id")
        if sid in seat_ids:
            raise FeatureModelError("Duplicate seat ID.")
        seat_ids.add(sid)
        if type(seat["requires_model"]) is not bool:
            raise FeatureModelError("Seat requires_model must be boolean.")
        inputs = seat["required_labels"]
        if (not isinstance(inputs, list) or len(inputs) > len(allowed)
                or any(not isinstance(x, str) or x not in allowed for x in inputs)
                or len(set(inputs)) != len(inputs)):
            raise FeatureModelError("Seat references noncanonical input labels.")
        regime = seat["expected_regime"]
        if regime is not None:
            _ident(regime, "expected_regime")
        seat_rows.append({**seat})

    return {
        "index_sha256": sha256(raw).hexdigest(), "entries": entries,
        "model": model, "seats": seat_rows,
        "locally_hashed_files": len(file_cache), "locally_hashed_bytes": total_bytes,
    }


def _visible(row, decision):
    times = row["times"]
    if any(times[key] > decision for key in (
        "observed_at", "published_at", "first_available_at", "received_at"
    )):
        return False
    if row["category"] == "source" and row["kind"] in ("SCHEDULE", "FUTURE_EFFECTIVE_POLICY"):
        return True
    return times["event_at"] <= decision


def _snapshot(packet, decision):
    records = [] if packet is None else packet["entries"]
    visible = [row for row in records if _visible(row, decision)]
    withheld = len(records) - len(visible)
    by_id = {row["id"]: row for row in visible}
    groups = {}
    for row in visible:
        groups.setdefault((row["category"], row["label"], row["event"]), []).append(row)
    selected = {}
    group_issues = {}
    for key, versions in groups.items():
        versions.sort(key=lambda x: (
            x["times"]["first_available_at"], x["times"]["received_at"], x["id"]
        ))
        for previous, current in zip(versions, versions[1:]):
            if current["category"] == "feature":
                valid = current["supersedes"] == previous["id"]
            else:
                valid = current["supersedes"] == previous["id"] or (
                    previous["kind"] == "SCHEDULE" and current["kind"] in ("SCHEDULE", "ACTUAL")
                )
            if not valid:
                group_issues.setdefault(key, set()).add("UNDECLARED_VINTAGE_REPLACEMENT")
        selected[key] = versions[-1]
    current_ids = {row["id"] for row in selected.values()}
    cache, visiting = {}, set()

    def inspect(row):
        rid = row["id"]
        if rid in cache:
            return cache[rid]
        if rid in visiting:
            return {"issues": {"DEPENDENCY_CYCLE"}, "ancestors": set(),
                    "families": set(), "uncertainty": row["uncertainty"],
                    "graph_digest": "DEPENDENCY_CYCLE_NOT_A_VALID_GRAPH"}
        visiting.add(rid)
        key = (row["category"], row["label"], row["event"])
        issues = set(group_issues.get(key, set()))
        ancestors, families, uncertainties, parent_hashes = set(), set(), [], []
        if row["category"] == "source":
            ancestors.add(rid)
            families.add(row["label"])
        if row["revocation"]:
            issues.add("LOCAL_REVOCATION_CLAIM")
        if row["category"] == "feature" and row["feature_schema"] != FEATURE_SCHEMA:
            issues.add("FEATURE_SCHEMA_VERSION_MISMATCH")
        if row["category"] == "source" and row["kind"] == "SCHEDULE":
            if row["value_state"] == "PRESENT":
                issues.add("SCHEDULE_VALUE_MUST_NOT_POSE_AS_ACTUAL")
        if row["category"] == "source" and row["value_state"] == "MISSING" and row["label"] in (
            "ticks", "candles", "risk_limits", "synthesis_policy"
        ):
            issues.add("REQUIRED_VALUE_MISSING")
        if (row["category"] == "source" and row["kind"] not in ("SCHEDULE", "FUTURE_EFFECTIVE_POLICY")
                and row["times"]["event_at"] > row["times"]["published_at"]):
            issues.add("ACTUAL_OBSERVATION_PREDATES_PUBLICATION_CLAIM")
        if not row["parents"] and (
            row["category"] == "feature" or row["label"] in ("market_state", "calendar_coverage", "risk_state")
        ):
            issues.add("DERIVED_ANCESTRY_NOT_SUPPLIED")
        for parent_id in row["parents"]:
            parent = by_id.get(parent_id)
            if parent is None:
                issues.add("PARENT_NOT_VISIBLE_AT_DECISION")
                parent_hashes.append([parent_id, "NOT_VISIBLE_AT_DECISION"])
                continue
            if parent_id not in current_ids:
                issues.add("PINNED_PARENT_VINTAGE_SUPERSEDED")
            if (parent["times"]["first_available_at"] > row["times"]["first_available_at"]
                    or parent["times"]["received_at"] > row["times"]["first_available_at"]):
                issues.add("NONCAUSAL_PARENT_AVAILABILITY")
            sub = inspect(parent)
            issues.update(sub["issues"])
            ancestors.update(sub["ancestors"])
            families.update(sub["families"])
            uncertainties.append(sub["uncertainty"])
            parent_hashes.append([parent_id, sub["graph_digest"]])
        if row["category"] == "feature":
            if not set(DERIVED_INPUTS[row["label"]]).issubset(families):
                issues.add("DERIVED_FAMILY_ANCESTRY_INCOMPLETE")
        if row["category"] == "source" and row["label"] in (
            "market_state", "calendar_coverage", "risk_state"
        ):
            required = {
                "market_state": {"ticks", "candles"},
                "calendar_coverage": {"economic_calendar"},
                "risk_state": {"risk_limits"},
            }[row["label"]]
            if not required.issubset(families):
                issues.add("DERIVED_SOURCE_PARENT_FAMILY_INCOMPLETE")
        if row["supersedes"] is not None:
            prior = by_id.get(row["supersedes"])
            if (prior is None or prior["label"] != row["label"]
                    or prior["event"] != row["event"]
                    or prior["times"]["first_available_at"] >= row["times"]["first_available_at"]):
                issues.add("REVISION_PARENT_INCOMPATIBLE_OR_UNAVAILABLE")
        age = Decimal((decision - row["times"]["first_available_at"]).total_seconds())
        bound = (family_contracts()[row["label"]]["proposed_max_age_seconds"]
                 if row["category"] == "source" else FEATURE_MAX_AGE_S[row["label"]])
        if age > bound and row["kind"] != "FUTURE_EFFECTIVE_POLICY":
            issues.add("UNAPPROVED_FRESHNESS_LIMIT_EXCEEDED")
        uncertainty = row["uncertainty"] + (max(uncertainties) if uncertainties else Decimal(0))
        graph_digest = _digest({
            "record_id": rid, "label": row["label"], "event": row["event"],
            "vintage": row["vintage"], "kind": row["kind"],
            "artifact_sha256": row["artifact_sha256"],
            "transform_sha256": row["transform_sha256"],
            "feature_schema": row["feature_schema"],
            "observed_at": _iso(row["times"]["observed_at"]),
            "event_at": _iso(row["times"]["event_at"]),
            "published_at": _iso(row["times"]["published_at"]),
            "first_available_at": _iso(row["times"]["first_available_at"]),
            "received_at": _iso(row["times"]["received_at"]),
            "uncertainty_ms": str(row["uncertainty"]),
            "revocation": row["revocation"], "value_state": row["value_state"],
            "parent_graphs": sorted(parent_hashes),
            "issues": sorted(issues),
        })
        result = {"issues": issues, "ancestors": ancestors, "families": families,
                  "uncertainty": uncertainty, "graph_digest": graph_digest}
        visiting.remove(rid)
        cache[rid] = result
        return result

    for row in visible:
        inspect(row)
    descriptors, selected_by_label = [], {}
    for category, labels in (("source", FAMILIES), ("feature", tuple(DERIVED_INPUTS))):
        for label in labels:
            candidates = [r for k, r in selected.items() if k[0] == category and k[1] == label]
            current = max(candidates, key=lambda r: (
                r["times"]["received_at"], r["times"]["first_available_at"], r["id"]
            )) if candidates else None
            if current is None:
                descriptors.append({
                    "label": label, "category": category.upper(), "status": "NOT_SUPPLIED",
                    "observed_at": None, "first_available_at": None,
                    "selected_vintage": None, "source_ancestry": [], "freshness_seconds": None,
                    "uncertainty_ms": None, "artifact_sha256": None,
                    "transform_sha256": None, "parent_record_ids": [],
                    "dependency_graph_sha256": None, "issues": [],
                })
                continue
            inspected = inspect(current)
            issues = sorted(inspected["issues"])
            state = "QUARANTINED" if issues else (
                "NOT_SUPPLIED" if current["value_state"] == "MISSING" else "LOCAL_CANDIDATE_UNATTESTED"
            )
            selected_by_label[label] = current
            descriptors.append({
                "label": label, "category": category.upper(), "status": state,
                "observed_at": _iso(current["times"]["observed_at"]),
                "first_available_at": _iso(current["times"]["first_available_at"]),
                "selected_vintage": current["vintage"],
                "source_ancestry": sorted(inspected["ancestors"]),
                "freshness_seconds": str(Decimal((decision - current["times"]["first_available_at"]).total_seconds())),
                "uncertainty_ms": str(inspected["uncertainty"]),
                "artifact_sha256": current["artifact_sha256"],
                "transform_sha256": current["transform_sha256"],
                "parent_record_ids": list(current["parents"]),
                "dependency_graph_sha256": inspected["graph_digest"],
                "issues": issues,
            })
    return descriptors, selected_by_label, withheld


def _model_review(model, fields, selected, decision):
    empty = {
        "status": "NOT_SUPPLIED", "local_compatibility": False,
        "issues": ["MODEL_RECORD_NOT_SUPPLIED"], "artifact_hash_matched_locally": False,
        "forecast_spec_version_proposed": FORECAST_SPEC,
        "feature_schema_version_proposed": FEATURE_SCHEMA,
        "evaluation_plan_version_proposed": EVAL_PLAN_SPEC,
        "independently_accepted_model": False, "model_used_for_inference": False,
        "eligible_model_digest": None,
    }
    if model is None:
        return empty
    times = model["times"]
    if times["built_at"] > decision or times["first_available_at"] > decision:
        return {**empty, "status": "POST_DECISION_MODEL_WITHHELD",
                "issues": ["MODEL_UNAVAILABLE_AT_DECISION"],
                "artifact_hash_matched_locally": True}
    issues = set()
    if model["forecast_spec_version"] != FORECAST_SPEC:
        issues.add("FORECAST_SPEC_VERSION_MISMATCH")
    if model["feature_schema_version"] != FEATURE_SCHEMA:
        issues.add("FEATURE_SCHEMA_VERSION_MISMATCH")
    if model["evaluation_plan_spec_version"] != EVAL_PLAN_SPEC:
        issues.add("EVALUATION_PLAN_SPEC_VERSION_MISMATCH")
    if not set(REQUIRED_REGIMES).issubset(model["regimes_trained"]):
        issues.add("MODEL_REGIME_COVERAGE_INCOMPLETE")
    if not (times["training_cutoff_at"] <= times["last_training_label_matured_at"]
            <= times["built_at"] <= times["first_available_at"] <= decision):
        issues.add("MODEL_TRAINING_OR_BUILD_CHRONOLOGY_INVALID")
    if times["training_data_available_at"] > times["built_at"]:
        issues.add("TRAINING_DATA_NOT_AVAILABLE_AT_BUILD")
    if times["evaluation_plan_available_at"] > decision:
        issues.add("EVALUATION_PLAN_NOT_AVAILABLE_AT_DECISION")
    for label in model["required_features"]:
        descriptor = next(x for x in fields if x["label"] == label)
        if descriptor["status"] != "LOCAL_CANDIDATE_UNATTESTED":
            issues.add("MODEL_REQUIRED_FEATURE_UNAVAILABLE_OR_QUARANTINED")
        if descriptor["artifact_sha256"] != model["feature_version_pins"][label]:
            issues.add("MODEL_PINNED_FEATURE_VERSION_MISMATCH")
    for policy_label, claim_key in (
        ("risk_limits", "risk_limits_record_id"),
        ("synthesis_policy", "synthesis_policy_record_id"),
    ):
        desc = next(x for x in fields if x["label"] == policy_label)
        picked = selected.get(policy_label)
        if (picked is None or picked["id"] != model[claim_key]
                or desc["status"] != "LOCAL_CANDIDATE_UNATTESTED"):
            issues.add("MODEL_POLICY_VERSION_MISMATCH")
    output = {
        **empty,
        "status": ("LOCAL_MODEL_INCOMPATIBLE_QUARANTINED" if issues
                   else "LOCAL_MODEL_COMPATIBLE_UNATTESTED"),
        "local_compatibility": not issues,
        "issues": sorted(issues), "artifact_hash_matched_locally": True,
        "eligible_model_digest": _digest({
            "model_sha256": model["model_sha256"],
            "training_dataset_sha256": model["training_dataset_sha256"],
            "evaluation_plan_sha256": model["evaluation_plan_sha256"],
            "forecast_spec_version": model["forecast_spec_version"],
            "feature_schema_version": model["feature_schema_version"],
            "required_features": model["required_features"],
            "feature_version_pins": model["feature_version_pins"],
            "regimes_trained": model["regimes_trained"],
            "training_cutoff_at": _iso(times["training_cutoff_at"]),
            "last_training_label_matured_at": _iso(times["last_training_label_matured_at"]),
            "training_data_available_at": _iso(times["training_data_available_at"]),
            "model_built_at": _iso(times["built_at"]),
            "model_first_available_at": _iso(times["first_available_at"]),
            "evaluation_plan_first_available_at": _iso(times["evaluation_plan_available_at"]),
            "risk_policy_id": model["risk_limits_record_id"],
            "synthesis_policy_id": model["synthesis_policy_record_id"],
        }),
    }
    return output


def _seats(seat_requirements, fields, model):
    indexed = {f["label"]: f for f in fields}
    output = []
    for seat in seat_requirements:
        reasons = []
        for label in seat["required_labels"]:
            if indexed[label]["status"] != "LOCAL_CANDIDATE_UNATTESTED":
                reasons.append("MISSING_OR_INCOMPATIBLE_INPUT:" + label)
        if seat["requires_model"] and not model["local_compatibility"]:
            reasons.append("MODEL_UNAVAILABLE_OR_MISMATCHED")
        if seat["expected_regime"] is not None:
            reasons.append("REGIME_CLASSIFICATION_NOT_INDEPENDENTLY_ADMITTED")
        reasons.extend(("INDEPENDENT_SOURCE_AND_G06_REVIEW_REQUIRED",
                        "OFFLINE_PREFLIGHT_CANNOT_INVOKE_COUNCIL_SEAT"))
        output.append({
            "seat_id": seat["seat_id"], "status": "ABSTAIN",
            "reasons": sorted(set(reasons)), "seat_dispatched": False,
            "local_input_contract_compatible": not any(
                r.startswith("MISSING_OR_INCOMPATIBLE_INPUT:") or r == "MODEL_UNAVAILABLE_OR_MISMATCHED"
                for r in reasons
            ),
        })
    return output


def _one(packet, decision):
    fields, selected, withheld = _snapshot(packet, decision)
    model = _model_review(None if packet is None else packet["model"], fields, selected, decision)
    seats = _seats([] if packet is None else packet["seats"], fields, model)
    material = {"epoch": EPOCH, "decision_at": _iso(decision), "context_fields": fields,
                "model_digest": model["eligible_model_digest"]}
    snapshot_id = _digest(material)
    candidates = sum(x["status"] == "LOCAL_CANDIDATE_UNATTESTED" for x in fields)
    quarantined = sum(x["status"] == "QUARANTINED" for x in fields)
    return {
        "status": "NO_LOCAL_CAUSAL_CONTEXT" if candidates == 0 else "LOCAL_SNAPSHOT_CANDIDATE_UNATTESTED",
        "snapshot_id": snapshot_id, "decision_at": _iso(decision),
        "context_fields": fields, "local_candidate_fields": candidates,
        "quarantined_fields": quarantined, "postdecision_records_withheld": withheld,
        "model_compatibility": model, "seat_preflight": seats,
        "seat_requirements_supplied": len(seats), "independent_source_admission": False,
        "snapshot_immutable_bytes_written": False, "council_context_admitted": False,
    }


def assess_stage7_feature_model(*, epoch_id, as_of, decision_at=None,
                                policy_file=POLICY_PATH, index_file=None, evidence_root=None,
                                replay_index=None, replay_root=None):
    if epoch_id != EPOCH:
        raise FeatureModelError("Immutable historical epoch mismatch; rebinding refused.")
    cutoff = _utc(as_of)
    decision = cutoff if decision_at is None else _utc(decision_at)
    if cutoff > datetime.now(UTC) or decision > cutoff:
        raise FeatureModelError("Future review cutoff or decision after review.")
    policy = sha256(_read(policy_file, 16384)).hexdigest()
    if policy != POLICY_SHA256:
        raise FeatureModelError("Immutable original clock policy SHA-256 mismatch.")
    if index_file is None and evidence_root is not None:
        raise FeatureModelError("Evidence root without index refused.")
    if replay_index is None and replay_root is not None:
        raise FeatureModelError("Replay root without replay index refused.")
    if replay_index is not None and index_file is None:
        raise FeatureModelError("Replay comparison requires primary evidence index.")
    packet = None if index_file is None else _load(index_file, evidence_root, cutoff)
    snapshot = _one(packet, decision)
    comparison = {
        "status": "NOT_REQUESTED", "same_decision_cutoff": True,
        "causal_snapshot_unchanged": None,
        "different_visible_inputs_requires_investigation": False,
    }
    if replay_index is not None:
        later = _load(replay_index, replay_root, cutoff)
        second = _one(later, decision)
        same = snapshot["snapshot_id"] == second["snapshot_id"]
        comparison = {
            "status": ("CAUSAL_SNAPSHOT_STABLE" if same else "VISIBLE_CAUSAL_INPUT_CHANGED_QUARANTINED"),
            "same_decision_cutoff": True,
            "causal_snapshot_unchanged": same,
            "different_visible_inputs_requires_investigation": not same,
            "comparison_index_sha256": later["index_sha256"],
            "comparison_postdecision_records_withheld": second["postdecision_records_withheld"],
        }
    blockers = [
        "G06_DERIVED_FEATURE_AND_MODEL_PROVENANCE_NOT_INDEPENDENTLY_ATTESTED",
        "G05_SOURCE_FAMILIES_NOT_INDEPENDENTLY_ADMITTED",
        "NO_OPERATOR_APPROVED_FORECAST_MODEL_RISK_OR_EVALUATION_POLICY",
        "NO_AUTHORIZED_POINT_IN_TIME_COUNCIL_CONTEXT",
        "NO_BROKER_COLLECTOR_OR_COUNCIL_EXECUTION_AUTHORITY",
    ]
    if packet is None:
        blockers.append("NO_LOCAL_FEATURE_MODEL_PACKET_SUPPLIED")
    if snapshot["quarantined_fields"] or snapshot["model_compatibility"]["status"] == "LOCAL_MODEL_INCOMPATIBLE_QUARANTINED":
        blockers.append("LOCAL_FEATURE_OR_MODEL_FINDINGS_REQUIRE_QUARANTINE")
    if comparison["different_visible_inputs_requires_investigation"]:
        blockers.append("CAUSAL_REPLAY_CHANGED_PRIOR_DECISION_CONTEXT")
    report = {
        "version": VERSION, "scope": "P7_17_TO_P7_20_OFFLINE_FEATURE_MODEL_REPLAY",
        "historical_epoch_id": EPOCH, "as_of": _iso(cutoff),
        "historical_clock_policy_sha256": policy, "historical_epoch_rebound": False,
        "canonical_family_labels": list(FAMILIES),
        "canonical_derived_labels": list(DERIVED_INPUTS),
        "derived_family_dependencies": {k: list(v) for k, v in DERIVED_INPUTS.items()},
        "local_evidence": {
            "status": "NOT_SUPPLIED" if packet is None else "LOCAL_HASH_MATCHED_UNATTESTED",
            "index_sha256": None if packet is None else packet["index_sha256"],
            "artifact_files_hash_matched": 0 if packet is None else packet["locally_hashed_files"],
            "artifact_bytes_hashed": 0 if packet is None else packet["locally_hashed_bytes"],
            "raw_artifact_payloads_exported": False,
        },
        "point_in_time_snapshot": snapshot,
        "replay_comparison": comparison,
        "G06_independently_verified": False,
        "external_gates_independently_verified": 0,
        "source_families_independently_verified": 0,
        "Council_seats_invoked": 0,
        "read_only": True,
        "files_written": False, "historical_source_modified": False,
        "network_accessed": False, "broker_connected": False,
        "collector_started": False, "council_invoked": False,
        "training_authorized": False, "settlement_invoked": False,
        "paper_orders_authorized": False, "live_orders_authorized": False,
        "forward_epoch_created": False, "operator_approval_granted": False,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(blockers), "final_action": "HOLD",
    }
    report["report_sha256"] = _digest(report)
    return report


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-features", action="store_true", required=True)
    parser.add_argument("--read-only-models", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--decision-at")
    parser.add_argument("--policy-file", default=POLICY_PATH)
    parser.add_argument("--evidence-index")
    parser.add_argument("--evidence-root")
    parser.add_argument("--replay-index")
    parser.add_argument("--replay-root")
    parser.add_argument("--json", action="store_true")
    options = parser.parse_args(argv)
    try:
        report = assess_stage7_feature_model(
            epoch_id=options.epoch_id, as_of=options.as_of,
            decision_at=options.decision_at, policy_file=options.policy_file,
            index_file=options.evidence_index, evidence_root=options.evidence_root,
            replay_index=options.replay_index, replay_root=options.replay_root,
        )
    except (FeatureModelError, OSError, ValueError, TypeError):
        print("STAGE 7 FEATURE/MODEL REVIEW BLOCKED: Unsafe or invalid evidence.")
        return 1
    if options.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    else:
        snap = report["point_in_time_snapshot"]
        print("EURUSD ORACLE | Stage 7 P7-17..20 feature, model and causal replay")
        print("Historical clock policy: ORIGINAL_BYTES_MATCH_PIN")
        print("Local evidence: " + report["local_evidence"]["status"])
        print("Canonical families/derived labels: 13/6 | Offline candidate fields: %d" % snap["local_candidate_fields"])
        print("Model compatibility: " + snap["model_compatibility"]["status"])
        print("Replayed decision snapshot: " + snap["snapshot_id"])
        print("Replay comparison: " + report["replay_comparison"]["status"])
        print("G06 independent provenance: NO | Gates/families: 0/11 | 0/13")
        for item in report["blockers"]:
            print("  BLOCKER " + item)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | NO MODEL EXECUTION | NO COLLECTOR | NO COUNCIL | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()

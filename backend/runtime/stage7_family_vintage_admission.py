
"""P7-13..16: strict, OFFLINE Council family coverage and as-of vintage review.

Source names, local hashes and timestamps are untrusted CANDIDATE claims. This
module has no authority to certify third parties, approve gate G05, launch
collectors or admit Council inputs. It performs NO network/database/broker I/O,
agent execution, training, settlement, orders or local writes. Immutable
October 27 history is never rebased, normalized or altered.
"""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re

from backend.runtime.stage6_council_context_readiness import FAMILIES, KIND_BY_FAMILY

VERSION = "EURUSD_STAGE7_FAMILY_VINTAGE_ADMISSION_P7_13_16_V1"
EPOCH = "75311e239cd5c295d3e08e03c358488fb123a46bfc6d342dd31493d95710aa33"
POLICY_PATH = "docs/clock_profiles/hfm_forward_20261027.json"
POLICY_SHA256 = "e34b7a29f78f5865f4b0fe7c4ad778e4e8b650d9943792eee019a48bdf2fe088"
EXPECTED_FAMILIES = (
    "market_state", "candles", "ticks", "cot_data", "ecb_data", "fed_data",
    "economic_calendar", "cross_asset_data", "calendar_coverage", "risk_state",
    "risk_limits", "reliability", "synthesis_policy",
)
UTC = timezone.utc
IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
NUM = re.compile(r"^-?(?:0|[1-9]\d{0,9})(?:\.\d{1,8})?$")
SENSITIVE = re.compile(
    r"(?i)(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|"
    r"account[_-]?(?:id|number)|bearer|authorization|login)"
)
INDEX_KEYS = {"schema_version", "epoch_id", "packet_id", "as_of", "records"}
RECORD_KEYS = {
    "record_id", "family", "provider_claim", "owner_claim", "source_channel",
    "publication_kind", "event_id", "event_at", "scheduled_release_at",
    "published_at", "first_available_at", "received_at", "observed_at",
    "effective_at", "value_state", "numeric_claim", "market_claim",
    "upstream_ids", "supersedes_id", "transform_code_sha256",
    "artifact_relative_path", "artifact_sha256", "revocation_notice_claim",
}
TICK_KEYS = {"symbol", "bid", "ask", "tick_at"}
CANDLE_KEYS = {"symbol", "open", "high", "low", "close", "bar_open_at", "bar_close_at"}
MAX_INDEX_BYTES = 262144
MAX_ARTIFACT_BYTES = 1048576
MAX_TOTAL_BYTES = 12582912
MAX_RECORDS = 128
MAX_CROSS_ASSET_LAG_SECONDS = 120
STATUSES = (
    "NOT_SUPPLIED", "LOCAL_CANDIDATE", "QUARANTINED",
    "INDEPENDENTLY_ATTESTED", "REVOKED",
)


class FamilyVintageError(ValueError):
    """Structurally invalid or unsafe local evidence; never promote proof."""


def _utc(value):
    if not isinstance(value, str):
        raise FamilyVintageError("Explicit UTC string required.")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise FamilyVintageError("Invalid UTC timestamp.") from None
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise FamilyVintageError("Explicit zero-offset UTC required.")
    return stamp.astimezone(UTC)


def _maybe_utc(value):
    return None if value is None else _utc(value)


def _id(value, field):
    if not isinstance(value, str) or not IDENT.fullmatch(value) or SENSITIVE.search(value):
        raise FamilyVintageError("Invalid or sensitive identifier: " + field)
    return value


def _hex(value, field):
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise FamilyVintageError("Expected SHA-256: " + field)
    return value


def _strict(value, keys, name):
    if not isinstance(value, dict) or set(value) != keys:
        raise FamilyVintageError("Strict schema mismatch: " + name)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FamilyVintageError("Duplicate JSON field refused.")
        result[key] = value
    return result


def _json(raw):
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(
                FamilyVintageError("Nonfinite JSON refused.")),
        )
    except FamilyVintageError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise FamilyVintageError("Malformed UTF-8 JSON object.") from None
    if not isinstance(value, dict):
        raise FamilyVintageError("JSON object required.")
    return value


def _digest(value):
    return sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _no_links(path):
    candidate = Path(path).absolute()
    for parent in (candidate, *candidate.parents):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise FamilyVintageError("Symlink or junction not permitted.")
    return candidate


def _read(path, maximum):
    candidate = _no_links(path)
    if not candidate.is_file():
        raise FamilyVintageError("Existing ordinary evidence file required.")
    try:
        with candidate.open("rb") as handle:
            content = handle.read(maximum + 1)
    except OSError:
        raise FamilyVintageError("Local evidence not readable.") from None
    if len(content) > maximum:
        raise FamilyVintageError("File exceeds evidence size bound.")
    return content


def _child(root, relative):
    if (
        not isinstance(relative, str) or not 1 <= len(relative) <= 180
        or ":" in relative or "\\" in relative or "\x00" in relative
        or "@" in relative or SENSITIVE.search(relative)
        or PurePosixPath(relative).is_absolute()
        or any(x in ("", ".", "..") for x in relative.split("/"))
    ):
        raise FamilyVintageError("Unsafe or sensitive relative evidence path.")
    base = _no_links(root)
    if not base.is_dir():
        raise FamilyVintageError("Existing evidence root required.")
    selected = _no_links(base.joinpath(*relative.split("/")))
    if not selected.resolve().is_relative_to(base.resolve()):
        raise FamilyVintageError("Evidence path escapes its root.")
    return selected


def _number(value, field, *, allow_negative=True):
    if not isinstance(value, str) or not NUM.fullmatch(value):
        raise FamilyVintageError("Bounded decimal string required: " + field)
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise FamilyVintageError("Invalid decimal: " + field) from None
    if not number.is_finite() or (not allow_negative and number < 0):
        raise FamilyVintageError("Nonfinite or negative decimal: " + field)
    return number


def _contract(family, kind, channel, age, owner, evidence, exceptions=(), parents=()):
    return {
        "family": family,
        "source_class": kind,
        "permitted_source_channels": list(channel),
        "proposed_max_age_seconds": age,
        "proposed_owner_role": owner,
        "independent_evidence_requirements": list(evidence),
        "source_specific_exceptions": list(exceptions),
        "declared_parent_families": list(parents),
        "evidence_kind_labels": list(KIND_BY_FAMILY[family]),
        "policy_status": "RESEARCH_DESIGN_NOT_OPERATOR_APPROVED",
        "independent_proof_required": True,
        "may_be_absent_at_decision": family not in (
            "ticks", "candles", "risk_limits", "synthesis_policy",
        ),
    }


def family_contracts():
    """Exactly 13 Stage 6 Council context labels, not 13 certified feeds."""
    if tuple(FAMILIES) != EXPECTED_FAMILIES or set(KIND_BY_FAMILY) != set(EXPECTED_FAMILIES):
        raise FamilyVintageError("Canonical Stage 6 Council input contract changed.")

    common = (
        "Original provider bytes and provider-authenticated identity",
        "Verifiable UTC receipt and independent availability provenance",
    )
    derived = (
        "Causal upstream Council-family evidence",
        "Pinned transformation-code SHA-256",
        "Versioned first-availability receipts and independent reviewer",
    )
    policy = (
        "Qualified policy owner and effective-version receipt",
        "Separate operator approval and independent source reviewer",
    )

    rows = (
        _contract(
            "market_state", "DERIVED_CONTEXT", ("DERIVED_TRANSFORM",), 900,
            "MARKET_RESEARCH_OWNER", derived, parents=("candles", "ticks"),
        ),
        _contract(
            "candles", "DIRECT_OR_DERIVED",
            ("DIRECT_PROVIDER", "DERIVED_TRANSFORM"), 900,
            "MARKET_DATA_OWNER",
            common + ("Exact M5 interval and OHLC source lineage",),
            exceptions=(
                "Provider-native M5 and tick-aggregated M5 have different lineage",
            ),
            parents=("ticks",),
        ),
        _contract(
            "ticks", "DIRECT_FEED", ("DIRECT_PROVIDER",), 120,
            "MARKET_DATA_OWNER",
            common + ("Broker UTC offset separately attested",),
        ),
        _contract(
            "cot_data", "PUBLISHED_VINTAGE", ("DIRECT_PROVIDER",), 864000,
            "MACRO_DATA_OWNER",
            common + ("COT position date and original publication/revision vintage",),
            exceptions=(
                "Weekly publication lag must not be mistaken for tick freshness",
            ),
        ),
        _contract(
            "ecb_data", "PUBLISHED_OR_EFFECTIVE_POLICY",
            ("DIRECT_PROVIDER",), 2592000,
            "MACRO_DATA_OWNER",
            common + ("ECB publication, effective date, and revision chain",),
            exceptions=(
                "Future-effective announced policy is distinct from future actual data",
            ),
        ),
        _contract(
            "fed_data", "PUBLISHED_OR_EFFECTIVE_POLICY",
            ("DIRECT_PROVIDER",), 2592000,
            "MACRO_DATA_OWNER",
            common + ("Federal Reserve release, effective date, and vintage",),
            exceptions=(
                "Future-effective announced policy is distinct from future actual data",
            ),
        ),
        _contract(
            "economic_calendar", "SCHEDULE_OR_ACTUAL",
            ("DIRECT_PROVIDER",), 604800,
            "MACRO_DATA_OWNER",
            common + ("Publication-time split between schedule and actual",),
            exceptions=(
                "Future scheduled event may be known; future result may not",
            ),
        ),
        _contract(
            "cross_asset_data", "DIRECT_FEED", ("DIRECT_PROVIDER",), 300,
            "CROSS_ASSET_OWNER",
            common + ("Cross-market timestamp and instrument scope",),
        ),
        _contract(
            "calendar_coverage", "DERIVED_CONTEXT",
            ("DERIVED_TRANSFORM",), 3600,
            "CALENDAR_RESEARCH_OWNER", derived,
            parents=("economic_calendar",),
        ),
        _contract(
            "risk_state", "DERIVED_CONTEXT", ("DERIVED_TRANSFORM",), 300,
            "RISK_RESEARCH_OWNER", derived, parents=("risk_limits",),
        ),
        _contract(
            "risk_limits", "OPERATOR_POLICY", ("OPERATOR_POLICY",), 2592000,
            "RISK_OWNER", policy,
            exceptions=("Policy cannot be created by market feed",),
        ),
        _contract(
            "reliability", "EVALUATION_CONTEXT", ("EVALUATION_LOG",), 2592000,
            "RESEARCH_EVALUATION_OWNER", derived,
            exceptions=("No outcome may be used before its maturity",),
        ),
        _contract(
            "synthesis_policy", "OPERATOR_POLICY",
            ("OPERATOR_POLICY",), 2592000,
            "COUNCIL_GOVERNANCE_OWNER", policy,
            exceptions=("Consensus weights are not market prices",),
        ),
    )
    return {row["family"]: row for row in rows}


KINDS = {
    "market_state": {"DERIVED"},
    "candles": {"OBSERVATION", "DERIVED"},
    "ticks": {"OBSERVATION"},
    "cot_data": {"ACTUAL", "REVISION"},
    "ecb_data": {"ACTUAL", "REVISION", "SCHEDULE", "FUTURE_EFFECTIVE_POLICY"},
    "fed_data": {"ACTUAL", "REVISION", "SCHEDULE", "FUTURE_EFFECTIVE_POLICY"},
    "economic_calendar": {"SCHEDULE", "ACTUAL", "REVISION"},
    "cross_asset_data": {"OBSERVATION"},
    "calendar_coverage": {"DERIVED"},
    "risk_state": {"DERIVED"},
    "risk_limits": {"OPERATOR_POLICY"},
    "reliability": {"EVALUATION"},
    "synthesis_policy": {"OPERATOR_POLICY"},
}


def _market(value, family):
    if value is None:
        return None

    keys = TICK_KEYS if family == "ticks" else CANDLE_KEYS if family == "candles" else None
    if keys is None:
        raise FamilyVintageError("Market quote schema not permitted for family.")
    _strict(value, keys, "market_claim")
    symbol = _id(value["symbol"], "symbol")

    if family == "ticks":
        bid = _number(value["bid"], "bid", allow_negative=False)
        ask = _number(value["ask"], "ask", allow_negative=False)
        if bid <= 0 or ask < bid:
            raise FamilyVintageError("Tick bid/ask incoherent.")
        return {
            "family": family, "symbol": symbol, "bid": bid,
            "ask": ask, "tick_at": _utc(value["tick_at"]),
        }

    open_ = _number(value["open"], "open", allow_negative=False)
    high = _number(value["high"], "high", allow_negative=False)
    low = _number(value["low"], "low", allow_negative=False)
    close = _number(value["close"], "close", allow_negative=False)
    begin = _utc(value["bar_open_at"])
    end = _utc(value["bar_close_at"])

    if (
        low <= 0 or not low <= open_ <= high or not low <= close <= high
        or high < low or end - begin != timedelta(minutes=5)
        or begin.second or begin.microsecond or begin.minute % 5
    ):
        raise FamilyVintageError("M5 OHLC or five-minute boundary invalid.")

    return {
        "family": family, "symbol": symbol, "open": open_, "high": high,
        "low": low, "close": close, "begin": begin, "end": end,
    }


def _load(index, root, as_of):
    if root is None:
        raise FamilyVintageError("Evidence root required with index.")
    raw = _read(index, MAX_INDEX_BYTES)
    doc = _json(raw)
    _strict(doc, INDEX_KEYS, "family_vintage_index")

    if (
        type(doc["schema_version"]) is not int
        or doc["schema_version"] != 1 or doc["epoch_id"] != EPOCH
    ):
        raise FamilyVintageError("Index schema or immutable historical epoch mismatch.")

    _id(doc["packet_id"], "packet_id")
    index_asof = _utc(doc["as_of"])
    if index_asof > as_of:
        raise FamilyVintageError("Index as-of exceeds review cutoff.")

    originals = doc["records"]
    if not isinstance(originals, list) or len(originals) > MAX_RECORDS:
        raise FamilyVintageError("Index record count exceeds bound.")

    ids, paths, total, results = set(), set(), 0, []
    contracts = family_contracts()

    for item in originals:
        _strict(item, RECORD_KEYS, "family_vintage_record")
        rid = _id(item["record_id"], "record_id")

        if rid in ids:
            raise FamilyVintageError("Duplicate vintage record ID.")
        ids.add(rid)

        family = item["family"]
        if not isinstance(family, str) or family not in contracts:
            raise FamilyVintageError("Unknown canonical family.")

        _id(item["provider_claim"], "provider_claim")
        _id(item["owner_claim"], "owner_claim")

        if item["source_channel"] not in contracts[family]["permitted_source_channels"]:
            raise FamilyVintageError("Source channel incompatible with canonical family.")

        kind = item["publication_kind"]
        if not isinstance(kind, str) or kind not in KINDS[family]:
            raise FamilyVintageError("Publication kind incompatible with family.")

        event = _id(item["event_id"], "event_id")
        if type(item["revocation_notice_claim"]) is not bool:
            raise FamilyVintageError("Revocation claim must be boolean.")

        state = item["value_state"]
        if state not in ("PRESENT", "MISSING"):
            raise FamilyVintageError("Missing/PRESENT state required.")

        numeric = item["numeric_claim"]
        if numeric is not None:
            _number(numeric, "numeric_claim")

        quote = _market(item["market_claim"], family)

        if state == "MISSING" and (numeric is not None or quote is not None):
            raise FamilyVintageError(
                "Missing value cannot be represented as zero or market data."
            )
        if state == "PRESENT" and family in ("ticks", "candles") and quote is None:
            raise FamilyVintageError(
                "Present tick/candle requires explicit market claim."
            )
        if family in ("ticks", "candles") and numeric is not None:
            raise FamilyVintageError(
                "Market claim cannot mix scalar and OHLC/tick prices."
            )

        when = {
            key: _maybe_utc(item[key])
            for key in ("event_at", "scheduled_release_at", "effective_at")
        }
        when.update({
            key: _utc(item[key])
            for key in (
                "published_at", "first_available_at",
                "received_at", "observed_at",
            )
        })

        if not (
            when["observed_at"] <= when["published_at"]
            <= when["first_available_at"] <= when["received_at"] <= index_asof
        ):
            raise FamilyVintageError(
                "Local publication and receipt chronology invalid."
            )

        if kind in (
            "OBSERVATION", "ACTUAL", "REVISION", "DERIVED", "EVALUATION"
        ) and when["event_at"] is None:
            raise FamilyVintageError(
                "Observed/released event requires event timestamp."
            )

        if kind in ("SCHEDULE", "FUTURE_EFFECTIVE_POLICY") and when["event_at"] is None:
            raise FamilyVintageError(
                "Announced schedule/effective policy requires event time."
            )

        if kind == "FUTURE_EFFECTIVE_POLICY" and (
            when["effective_at"] is None
            or when["effective_at"] <= when["published_at"]
        ):
            raise FamilyVintageError(
                "Future-effective policy needs future-effective timestamp."
            )

        if kind not in ("SCHEDULE", "FUTURE_EFFECTIVE_POLICY") and when["effective_at"] is not None:
            raise FamilyVintageError("Unrelated effective-date claim refused.")

        if quote is not None and family == "ticks" and quote["tick_at"] != when["event_at"]:
            raise FamilyVintageError(
                "Tick event time and market observation mismatch."
            )

        if quote is not None and family == "candles" and quote["end"] > when["published_at"]:
            raise FamilyVintageError(
                "M5 candle claimed published before bar close."
            )

        parents = item["upstream_ids"]
        if (
            not isinstance(parents, list) or len(parents) > 12
            or any(not isinstance(x, str) for x in parents)
        ):
            raise FamilyVintageError("Invalid upstream list shape.")

        for parent in parents:
            _id(parent, "upstream_id")
        if len(set(parents)) != len(parents) or rid in parents:
            raise FamilyVintageError("Repeated or self-referential upstream ID.")

        supersedes = item["supersedes_id"]
        if supersedes is not None:
            _id(supersedes, "supersedes_id")
        if kind == "REVISION" and supersedes is None:
            raise FamilyVintageError(
                "Revision must declare previous record ID."
            )
        if kind != "REVISION" and supersedes is not None:
            raise FamilyVintageError(
                "Only revision may supersede a vintage."
            )

        transform = item["transform_code_sha256"]
        if transform is not None:
            _hex(transform, "transform_code_sha256")
        if item["source_channel"] == "DERIVED_TRANSFORM" and transform is None:
            raise FamilyVintageError(
                "Derived evidence requires transform version SHA-256."
            )

        relative = item["artifact_relative_path"]
        if relative in paths:
            raise FamilyVintageError("Repeated evidence artifact path.")
        paths.add(relative)

        artifact = _read(_child(root, relative), MAX_ARTIFACT_BYTES)
        expected = _hex(item["artifact_sha256"], "artifact_sha256")
        if sha256(artifact).hexdigest() != expected:
            raise FamilyVintageError("Artifact SHA-256 mismatch.")

        total += len(artifact)
        if total > MAX_TOTAL_BYTES:
            raise FamilyVintageError("Cumulative artifact bytes exceeded.")

        results.append({
            "id": rid,
            "family": family,
            "provider": item["provider_claim"],
            "owner": item["owner_claim"],
            "channel": item["source_channel"],
            "kind": kind,
            "event": event,
            "when": when,
            "quote": quote,
            "value_state": state,
            "numeric": numeric,
            "parents": parents,
            "supersedes": supersedes,
            "transform": transform,
            "revocation": item["revocation_notice_claim"],
        })

    return {
        "index_sha256": sha256(raw).hexdigest(),
        "entries": results,
        "locally_hashed_artifacts": len(results),
        "locally_hashed_bytes": total,
    }


def _decision_review(entries, decision):
    """Never emit raw payloads, later-revised actuals, or post-cutoff values."""
    lookup = {entry["id"]: entry for entry in entries}
    withheld, findings, previous_by_group = [], [], {}

    for entry in entries:
        rid, kind, when = entry["id"], entry["kind"], entry["when"]

        if (
            when["published_at"] > decision
            or when["first_available_at"] > decision
            or when["received_at"] > decision
            or when["observed_at"] > decision
        ):
            withheld.append({
                "family": entry["family"],
                "reason": "POST_DECISION_PUBLICATION_OR_RECEIPT",
            })
            continue

        if (
            kind not in ("SCHEDULE", "FUTURE_EFFECTIVE_POLICY")
            and when["event_at"] is not None
            and when["event_at"] > decision
        ):
            withheld.append({
                "family": entry["family"],
                "reason": "FUTURE_OBSERVATION_NOT_KNOWN_AT_DECISION",
            })
            continue

        issues = []
        if entry["revocation"]:
            issues.append(
                "LOCAL_REVOCATION_NOTICE_REQUIRES_INDEPENDENT_REVIEW"
            )

        if kind == "SCHEDULE" and entry["value_state"] == "PRESENT" and entry["numeric"] is not None:
            issues.append(
                "SCHEDULE_CANNOT_CONTAIN_ACTUAL_NUMERIC_RESULT"
            )

        if kind == "SCHEDULE" and when["event_at"] < when["published_at"]:
            issues.append(
                "SCHEDULE_CLAIM_ANNOUNCED_AFTER_EVENT"
            )

        if kind in ("ACTUAL", "REVISION"):
            when_release = when["scheduled_release_at"]
            if when_release is not None and when["published_at"] < when_release:
                issues.append("ACTUAL_BEFORE_DECLARED_RELEASE_TIME")

            if (
                entry["family"] == "cot_data"
                and when["event_at"] is not None
                and when["published_at"] < when["event_at"]
            ):
                issues.append("COT_PUBLISHED_BEFORE_POSITION_DATE")

        if (
            kind == "FUTURE_EFFECTIVE_POLICY"
            and entry["family"] not in ("ecb_data", "fed_data")
        ):
            issues.append(
                "FUTURE_EFFECTIVE_POLICY_FAMILY_MISMATCH"
            )

        if kind in ("DERIVED", "EVALUATION") and not entry["parents"]:
            issues.append(
                "DERIVED_OR_EVALUATION_ANCESTRY_MISSING"
            )

        required_parents = family_contracts()[entry["family"]]["declared_parent_families"]

        if entry["kind"] == "DERIVED" and required_parents and entry["parents"]:
            if not any(
                p in lookup and lookup[p]["family"] in required_parents
                for p in entry["parents"]
            ):
                issues.append("DERIVED_PARENT_FAMILY_NOT_DECLARED")

        for parent_id in entry["parents"]:
            parent = lookup.get(parent_id)
            if parent is None:
                issues.append("UPSTREAM_SOURCE_NOT_IN_INDEX")
            elif (
                parent["when"]["first_available_at"] > when["first_available_at"]
                or parent["when"]["received_at"] > decision
                or parent["when"]["published_at"] > decision
            ):
                issues.append("UPSTREAM_SOURCE_NOT_CAUSALLY_AVAILABLE")

        if entry["supersedes"] is not None:
            parent = lookup.get(entry["supersedes"])
            if parent is None or (
                parent["family"], parent["event"]
            ) != (
                entry["family"], entry["event"]
            ):
                issues.append(
                    "REVISION_TARGET_MISSING_OR_INCOMPATIBLE"
                )
            elif (
                parent["when"]["first_available_at"] >= when["first_available_at"]
                or parent["when"]["received_at"] > decision
                or parent["when"]["received_at"] > when["received_at"]
            ):
                issues.append(
                    "REVISION_PREDECESSOR_NOT_AVAILABLE"
                )
            elif parent["provider"] != entry["provider"]:
                issues.append(
                    "REVISION_PROVIDER_IDENTITY_CHANGED"
                )

        quote = entry["quote"]

        if quote and quote["family"] == "ticks":
            if quote["tick_at"] > decision:
                issues.append("TICK_AFTER_DECISION_CUTOFF")

        if quote and quote["family"] == "candles":
            if quote["end"] > decision:
                issues.append("INCOMPLETE_M5_BAR_AT_DECISION")

            if entry["channel"] == "DERIVED_TRANSFORM":
                linked_ticks = [
                    lookup[p] for p in entry["parents"]
                    if p in lookup and lookup[p]["family"] == "ticks"
                ]
                if not linked_ticks:
                    issues.append(
                        "DERIVED_M5_TICK_ANCESTRY_MISSING"
                    )

                for tick in linked_ticks:
                    t = tick["quote"]
                    if t is None or t["symbol"] != quote["symbol"]:
                        issues.append("TICK_M5_SYMBOL_MISMATCH")
                    elif not quote["begin"] <= t["tick_at"] < quote["end"]:
                        issues.append("TICK_OUTSIDE_M5_BAR_WINDOW")
                    elif not quote["low"] <= t["bid"] <= quote["high"]:
                        issues.append("TICK_BID_OUTSIDE_CANDLE_OHLC")

        if (
            entry["value_state"] == "MISSING"
            and not family_contracts()[entry["family"]]["may_be_absent_at_decision"]
        ):
            issues.append("REQUIRED_VALUE_MISSING")

        age = decision - when["first_available_at"]
        limit = family_contracts()[entry["family"]]["proposed_max_age_seconds"]
        if age > timedelta(seconds=limit) and kind != "FUTURE_EFFECTIVE_POLICY":
            issues.append("SOURCE_FRESHNESS_EXCEEDS_PROPOSED_LIMIT")

        tagged = {
            "family": entry["family"],
            "id": rid,
            "event": entry["event"],
            "kind": kind,
            "state": entry["value_state"],
            "issues": sorted(set(issues)),
            "published": when["published_at"],
            "received": when["received_at"],
            "first_available": when["first_available_at"],
            "quote": quote,
            "provider": entry["provider"],
            "channel": entry["channel"],
        }

        for issue in tagged["issues"]:
            findings.append({
                "family": entry["family"],
                "record_id": rid,
                "finding": issue,
            })

        previous_by_group.setdefault(
            (entry["family"], entry["event"]), []
        ).append(tagged)

    selected = []

    for _, versions in sorted(previous_by_group.items()):
        versions.sort(
            key=lambda x: (
                x["first_available"], x["received"], x["id"],
            )
        )

        for prior, latter in zip(versions, versions[1:]):
            valid_change = (
                latter["kind"] == "REVISION"
                or (
                    prior["kind"] == "SCHEDULE"
                    and latter["kind"] in ("SCHEDULE", "ACTUAL")
                )
            )
            if not valid_change:
                latter["issues"].append("UNDECLARED_PARALLEL_VINTAGES")
                findings.append({
                    "family": latter["family"],
                    "record_id": latter["id"],
                    "finding": "UNDECLARED_PARALLEL_VINTAGES",
                })

        selected.append(versions[-1])

    tick_times = [
        e["quote"]["tick_at"]
        for e in selected
        if e["family"] == "ticks"
        and e["quote"] is not None and not e["issues"]
    ]
    latest_tick = max(tick_times) if tick_times else None

    if latest_tick is not None:
        for entry in selected:
            if entry["family"] == "cross_asset_data":
                if abs(
                    entry["first_available"] - latest_tick
                ) > timedelta(seconds=MAX_CROSS_ASSET_LAG_SECONDS):
                    issue = "CROSS_ASSET_NOT_SYNCHRONOUS_TO_EURUSD"
                    entry["issues"].append(issue)
                    findings.append({
                        "family": entry["family"],
                        "record_id": entry["id"],
                        "finding": issue,
                    })

    for candle in selected:
        q = candle["quote"]
        if candle["family"] != "candles" or q is None:
            continue

        for tick in selected:
            t = tick["quote"]
            if (
                tick["family"] != "ticks"
                or t is None
                or tick["provider"] != candle["provider"]
                or t["symbol"] != q["symbol"]
                or not q["begin"] <= t["tick_at"] < q["end"]
            ):
                continue

            if not q["low"] <= t["bid"] <= q["high"]:
                issue = "EURUSD_TICK_OUTSIDE_OBSERVED_M5_OHLC"
                candle["issues"].append(issue)
                findings.append({
                    "family": "candles",
                    "record_id": candle["id"],
                    "finding": issue,
                })

    return selected, withheld, findings


def assess_stage7_family_vintages(
    *, epoch_id, as_of, decision_at=None,
    policy_file=POLICY_PATH, index_file=None, evidence_root=None,
):
    """Run deterministic local review; never output independent acceptance."""
    if epoch_id != EPOCH:
        raise FamilyVintageError(
            "Immutable historical epoch mismatch; rebinding denied."
        )

    cutoff = _utc(as_of)
    decision = cutoff if decision_at is None else _utc(decision_at)

    if cutoff > datetime.now(UTC) or decision > cutoff:
        raise FamilyVintageError(
            "Review cutoff in future or decision after review cutoff."
        )

    original = sha256(_read(policy_file, 16384)).hexdigest()
    if original != POLICY_SHA256:
        raise FamilyVintageError(
            "Immutable historical policy SHA-256 mismatch."
        )

    contracts = family_contracts()

    if index_file is None:
        if evidence_root is not None:
            raise FamilyVintageError(
                "Evidence root without index refused."
            )
        evidence, selected, withheld, findings = None, [], [], []
    else:
        evidence = _load(index_file, evidence_root, cutoff)
        selected, withheld, findings = _decision_review(
            evidence["entries"], decision,
        )

    grouped = {family: [] for family in FAMILIES}
    for item in selected:
        grouped[item["family"]].append(item)

    reviews = {}
    for family in FAMILIES:
        rows = grouped[family]

        problems = sorted({
            x["finding"]
            for x in findings
            if x["family"] == family
            and (
                x["finding"] != "SOURCE_FRESHNESS_EXCEEDS_PROPOSED_LIMIT"
                or any(
                    x["finding"] in row["issues"]
                    for row in rows
                )
            )
        })

        has_value = any(row["state"] == "PRESENT" for row in rows)

        if problems:
            status = "QUARANTINED"
        elif has_value:
            status = "LOCAL_CANDIDATE"
        else:
            status = "NOT_SUPPLIED"

        reviews[family] = {
            "status": status,
            "local_selected_vintages": len(rows),
            "present_value_vintages": sum(
                x["state"] == "PRESENT" for x in rows
            ),
            "missing_value_vintages": sum(
                x["state"] == "MISSING" for x in rows
            ),
            "claimed_contributing_providers": sorted({
                x["provider"] for x in rows
            }),
            "selected_event_ids": sorted({
                x["event"] for x in rows
            }),
            "selected_publication_kinds": sorted({
                x["kind"] for x in rows
            }),
            "findings": problems,
            "local_custody_only": True,
            "independent_attestation": "NOT_PROVIDED",
            "external_reviewer_decision": "NOT_PROVIDED",
            "approved_for_council": False,
        }

    matrix = [
        {**contracts[family], **reviews[family]}
        for family in FAMILIES
    ]

    blocked = [
        family for family in FAMILIES
        if reviews[family]["status"] != "LOCAL_CANDIDATE"
    ]

    if evidence is None:
        blocked = list(FAMILIES)

    blockers = [
        "G05_13_FAMILY_PROVENANCE_NOT_INDEPENDENTLY_ATTESTED",
        "NO_QUALIFIED_SEPARATE_SOURCE_ADMISSION_REVIEW",
        "NO_BROKER_UTC_OR_PROSPECTIVE_FORWARD_EPOCH_AUTHORIZATION",
        "NO_AUTHORIZED_COUNCIL_CONTEXT_OR_EXECUTION",
        "UNAPPROVED_PROPOSED_FRESHNESS_AND_COHERENCE_THRESHOLDS",
    ]

    if evidence is None:
        blockers.append("NO_LOCAL_FAMILY_VINTAGE_INDEX_SUPPLIED")
    if findings:
        blockers.append("LOCAL_FAMILY_COHERENCE_OR_VINTAGE_FINDINGS")
    if withheld:
        blockers.append("POST_DECISION_OR_FUTURE_VINTAGES_WITHHELD")

    result = {
        "version": VERSION,
        "scope": "P7_13_TO_P7_16_OFFLINE_FAMILY_VINTAGE_REVIEW",
        "epoch_id": EPOCH,
        "as_of": cutoff.isoformat(),
        "decision_at": decision.isoformat(),
        "historical_policy_sha256": original,
        "historical_epoch_rebound": False,
        "canonical_family_labels": list(FAMILIES),
        "family_count": len(FAMILIES),
        "family_evidence_matrix": matrix,
        "status_semantics": {
            "NOT_SUPPLIED": (
                "No eligible local content; absent or missing is never zero"
            ),
            "LOCAL_CANDIDATE": (
                "Locally compatible, not independently authenticated"
            ),
            "QUARANTINED": (
                "Observed local defect, conflict, or revocation claim; never admitted"
            ),
            "INDEPENDENTLY_ATTESTED": (
                "Reserved for separate independent reviewer workflow; never issued here"
            ),
            "REVOKED": (
                "Reserved for separately authenticated revocation; never issued here"
            ),
        },
        "source_admission": {
            "status": "BLOCKED_EXTERNAL_REVIEW",
            "G05_independently_verified": False,
            "independently_attested_families": 0,
            "families_with_local_candidates": sum(
                x["status"] == "LOCAL_CANDIDATE"
                for x in reviews.values()
            ),
            "families_quarantined": sum(
                x["status"] == "QUARANTINED"
                for x in reviews.values()
            ),
            "families_without_eligible_local_content": sum(
                x["status"] == "NOT_SUPPLIED"
                for x in reviews.values()
            ),
            "families_not_locally_eligible": blocked,
            "review_owner": (
                "SEPARATE_QUALIFIED_INDEPENDENT_REVIEWER_REQUIRED"
            ),
            "reviewer_signature": None,
            "operator_approval": False,
            "admitted_council_contexts": 0,
        },
        "local_evidence": {
            "status": (
                "NOT_SUPPLIED"
                if evidence is None
                else "LOCAL_ARTIFACT_HASHES_MATCH_UNATTESTED"
            ),
            "index_sha256": (
                None if evidence is None else evidence["index_sha256"]
            ),
            "artifacts_hash_matched": (
                0 if evidence is None else evidence["locally_hashed_artifacts"]
            ),
            "artifact_bytes_hashed": (
                0 if evidence is None else evidence["locally_hashed_bytes"]
            ),
            "post_decision_records_withheld": len(withheld),
            "withheld_family_counts": {
                f: sum(x["family"] == f for x in withheld)
                for f in FAMILIES
                if any(x["family"] == f for x in withheld)
            },
            "untrusted_record_payloads_exported": False,
        },
        "coherence_findings": findings,
        "external_gates_independently_verified": 0,
        "council_families_independently_verified": 0,
        "read_only": True,
        "historical_source_modified": False,
        "files_written": False,
        "network_accessed": False,
        "broker_connected": False,
        "collector_started": False,
        "council_invoked": False,
        "training_authorized": False,
        "settlement_invoked": False,
        "paper_orders_authorized": False,
        "live_orders_authorized": False,
        "forward_epoch_created": False,
        "operator_approval_granted": False,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(blockers),
        "final_action": "HOLD",
    }

    result["report_sha256"] = _digest(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-sources", action="store_true", required=True)
    parser.add_argument("--read-only-vintages", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--decision-at")
    parser.add_argument("--policy-file", default=POLICY_PATH)
    parser.add_argument("--evidence-index")
    parser.add_argument("--evidence-root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = assess_stage7_family_vintages(
            epoch_id=args.epoch_id,
            as_of=args.as_of,
            decision_at=args.decision_at,
            policy_file=args.policy_file,
            index_file=args.evidence_index,
            evidence_root=args.evidence_root,
        )
    except (FamilyVintageError, OSError, ValueError, TypeError):
        print(
            "STAGE 7 FAMILY/VINTAGE REVIEW BLOCKED: Local evidence invalid or unsafe."
        )
        return 1

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    else:
        print(
            "EURUSD ORACLE | Stage 7 P7-13..16 family and publication-vintage review"
        )
        print("Canonical Council input families: 13/13 CONTRACTS DEFINED")
        print("Historical clock policy: ORIGINAL_BYTES_MATCH_PIN")
        print("Evidence packet: " + result["local_evidence"]["status"])
        print(
            "Local family candidates: %d | Quarantined: %d | Not supplied: %d" % (
                result["source_admission"]["families_with_local_candidates"],
                result["source_admission"]["families_quarantined"],
                result["source_admission"]["families_without_eligible_local_content"],
            )
        )
        print(
            "Postdecision vintages withheld: " +
            str(result["local_evidence"]["post_decision_records_withheld"])
        )
        print("G05 independent family admission: 0/13 | External gates: 0/11")
        for blocker in result["blockers"]:
            print("  BLOCKER " + blocker)
        print("SHA-256: " + result["report_sha256"])
        print(
            "READ-ONLY | NO NETWORK | NO COLLECTOR | NO COUNCIL | FINAL=HOLD"
        )

    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()

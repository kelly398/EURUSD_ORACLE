
"""P6-13..16: static, offline, read-only prospective ledger custody review.

This module NEVER registers a prediction, retrieves a price, settles memory,
verifies a third-party signature, or computes trading performance. Local
file hashes and *claimed* receipt timestamps cannot establish external truth.
"""

import argparse
import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re

VERSION = "EURUSD_STAGE6_PROSPECTIVE_LEDGER_P6_13_16_V1"
HISTORICAL_EPOCH = "75311e239cd5c295d3e08e03c358488fb123a46bfc6d342dd31493d95710aa33"
KINDS = (
    "holdout_freeze", "prediction_registration", "outcome_quote",
    "cost_receipt", "no_trade_scope",
)
HEX64 = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
DECIMAL = re.compile(r"^(?:0|[1-9][0-9]{0,7})(?:\.[0-9]{1,8})?$")
INDEX_KEYS = {"schema_version", "epoch_id", "decision_at", "as_of", "entries"}
INDEX_ENTRY_KEYS = {"record_id", "kind", "relative_path", "file_sha256"}
COMMON_KEYS = {
    "record_id", "kind", "epoch_id", "claimed_issuer",
    "claimed_receipt_id", "claimed_receipt_at",
}
KIND_KEYS = {
    "holdout_freeze": {
        "scope_id", "freeze_at", "partition_sha256", "evaluation_plan_sha256",
        "model_selection_sha256",
    },
    "prediction_registration": {
        "prediction_id", "scope_id", "symbol", "created_at", "registered_at",
        "decision_at", "horizon_seconds", "horizon_end_at", "max_quote_age_seconds",
        "forecast_specification_sha256", "model_artifact_sha256",
        "context_snapshot_sha256", "feature_snapshot_sha256",
    },
    "outcome_quote": {
        "prediction_id", "symbol", "quote_at", "available_at", "bid", "ask",
        "source_record_id",
    },
    "cost_receipt": {
        "prediction_id", "symbol", "filled_at", "broker_fill_record_id",
        "spread_pips", "commission_pips", "slippage_pips", "financing_pips",
    },
    "no_trade_scope": {
        "prediction_id", "scope_account_sha256", "period_start_at",
        "period_end_at", "trade_count",
    },
}
MAX_SOURCE_BYTES = 1048576
MAX_INDEX_BYTES = 262144
MAX_EVIDENCE_BYTES = 1048576
MAX_TOTAL_EVIDENCE_BYTES = 16777216
MAX_ENTRIES = 128
UTC = timezone.utc


class ProspectiveLedgerError(ValueError):
    """Contradictory or invalid local prospective evidence contract."""


def _utc(value):
    if not isinstance(value, str):
        raise ProspectiveLedgerError("Explicit UTC timestamp text required.")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise ProspectiveLedgerError("Invalid UTC timestamp.") from None
    if instant.tzinfo is None or instant.utcoffset() != timedelta(0):
        raise ProspectiveLedgerError("Explicit zero-offset UTC required.")
    return instant.astimezone(UTC)


def _digest(obj):
    return sha256(json.dumps(
        obj, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ProspectiveLedgerError("Duplicate JSON field refused.")
        obj[key] = value
    return obj


def _json(raw):
    try:
        obj = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ProspectiveLedgerError("Nonfinite JSON refused.")
            ),
        )
    except ProspectiveLedgerError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise ProspectiveLedgerError("Invalid evidence JSON.") from None
    if not isinstance(obj, dict):
        raise ProspectiveLedgerError("Evidence must be a JSON object.")
    return obj


def _read(path, limit):
    file = Path(path)
    if file.is_symlink() or not file.is_file():
        raise ProspectiveLedgerError("Existing regular source/evidence file required.")
    try:
        with file.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError:
        raise ProspectiveLedgerError("Source/evidence file unreadable.") from None
    if len(raw) > limit:
        raise ProspectiveLedgerError("Source/evidence file exceeds size limit.")
    return raw


def _safe_child(root, relative):
    if (
        not isinstance(relative, str) or not 1 <= len(relative) <= 240
        or "\\" in relative or ":" in relative or "\x00" in relative
        or PurePosixPath(relative).is_absolute()
        or any(p in ("", ".", "..") for p in relative.split("/"))
    ):
        raise ProspectiveLedgerError("Unsafe relative evidence path.")
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise ProspectiveLedgerError("Existing non-symlink evidence root required.")
    path = root
    for part in relative.split("/"):
        path = path / part
        if path.is_symlink():
            raise ProspectiveLedgerError("Symlinked evidence file/path refused.")
    return path


def _id(value, field):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ProspectiveLedgerError("Invalid identifier: " + field)
    return value


def _hex(value, field):
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise ProspectiveLedgerError("Invalid SHA-256: " + field)
    return value


def _positive_decimal(value, field, allow_zero=False):
    if not isinstance(value, str) or not DECIMAL.fullmatch(value):
        raise ProspectiveLedgerError("Invalid nonnegative decimal text: " + field)
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise ProspectiveLedgerError("Invalid decimal: " + field) from None
    if not parsed.is_finite() or (parsed < 0 if allow_zero else parsed <= 0):
        raise ProspectiveLedgerError("Nonpositive/negative decimal: " + field)
    return parsed


def inspect_learning_source_contracts(*, contracts_file, settlement_file):
    """AST-only inventory; importing settle_outcomes can open DB dependencies."""
    expected = {
        "contracts": {
            "RunProvenance": ("scope", "origin", "policy_version"),
            "ForecastSpecification": (
                "horizon_seconds", "max_quote_age_seconds", "round_trip_commission_pips",
            ),
            "OutcomeQuote": ("timestamp", "available_at", "bid", "ask"),
        },
        "settlement": {"SettlementRequest": ("prediction_id", "quote")},
    }
    result = {}
    for label, file in (("contracts", contracts_file), ("settlement", settlement_file)):
        raw = _read(file, MAX_SOURCE_BYTES)
        try:
            tree = ast.parse(raw.decode("utf-8"))
        except (SyntaxError, UnicodeError, ValueError, RecursionError):
            raise ProspectiveLedgerError("Learning source AST invalid.") from None
        for klass, fields in expected[label].items():
            matches = [node for node in tree.body if isinstance(node, ast.ClassDef)
                       and node.name == klass]
            if len(matches) != 1:
                raise ProspectiveLedgerError("Missing or duplicate learning contract: " + klass)
            names = [node.target.id for node in matches[0].body
                     if isinstance(node, ast.AnnAssign)
                     and isinstance(node.target, ast.Name)]
            if not set(fields).issubset(names):
                raise ProspectiveLedgerError("Required learning contract fields absent: " + klass)
        result[label + "_sha256"] = sha256(raw).hexdigest()
    return {
        "status": "LOCAL_STATIC_LEARNING_CONTRACTS_PRESENT",
        "forecast_specification_present": True,
        "outcome_quote_contract_present": True,
        "settlement_request_contract_present": True,
        "source_files_executed_or_imported": False,
        "settlement_invoked": False,
        **result,
    }


def _load_index(index_file, root, *, decision_at, epoch_id, current_cutoff):
    """Parse and hash-validate existing local candidate payloads only."""
    if root is None:
        raise ProspectiveLedgerError("Evidence root required with index.")
    raw = _read(index_file, MAX_INDEX_BYTES)
    data = _json(raw)
    if (set(data) != INDEX_KEYS or type(data.get("schema_version")) is not int
            or data["schema_version"] != 1 or data["epoch_id"] != epoch_id
            or _utc(data["decision_at"]) != decision_at):
        raise ProspectiveLedgerError("Prospective index version, epoch or decision mismatch.")
    cutoff = _utc(data["as_of"])
    if cutoff > current_cutoff:
        raise ProspectiveLedgerError("Index review cutoff is in the future.")
    entries = data["entries"]
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ProspectiveLedgerError("Prospective index entry count invalid.")
    records = []
    seen_records = set()
    seen_receipts = set()
    last_receipt = None
    total = 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != INDEX_ENTRY_KEYS:
            raise ProspectiveLedgerError("Prospective index entry contract invalid.")
        rid = _id(entry["record_id"], "record_id")
        kind = entry["kind"]
        if not isinstance(kind, str) or kind not in KINDS or rid in seen_records:
            raise ProspectiveLedgerError("Unknown kind or duplicate prospective record.")
        _hex(entry["file_sha256"], "file_sha256")
        contents = _read(_safe_child(root, entry["relative_path"]), MAX_EVIDENCE_BYTES)
        total += len(contents)
        if total > MAX_TOTAL_EVIDENCE_BYTES:
            raise ProspectiveLedgerError("Cumulative evidence size limit exceeded.")
        if sha256(contents).hexdigest() != entry["file_sha256"]:
            raise ProspectiveLedgerError("Local evidence payload SHA-256 mismatch.")
        record = _json(contents)
        if (set(record) != COMMON_KEYS | KIND_KEYS[kind]
                or record["record_id"] != rid or record["kind"] != kind
                or record["epoch_id"] != epoch_id):
            raise ProspectiveLedgerError("Prospective payload schema, kind or epoch mismatch.")
        issuer = _id(record["claimed_issuer"], "claimed_issuer")
        receipt = _id(record["claimed_receipt_id"], "claimed_receipt_id")
        if (issuer, receipt) in seen_receipts:
            raise ProspectiveLedgerError("Claimed external receipt identifier reused.")
        seen_receipts.add((issuer, receipt))
        received = _utc(record["claimed_receipt_at"])
        if received > cutoff or (last_receipt is not None and received < last_receipt):
            raise ProspectiveLedgerError("Receipt claim after cutoff or out of ledger order.")
        last_receipt = received
        records.append({**record, "_received": received})
        seen_records.add(rid)
    return {
        "index_sha256": sha256(raw).hexdigest(),
        "index_as_of": cutoff.isoformat(),
        "entries": entries,
        "records": records,
        "locally_hash_matched_documents": len(records),
    }


def _validate_relations(records, *, decision_at):
    """Check candidate chronology and linkage; never confer authenticity."""
    by_kind = {kind: [] for kind in KINDS}
    for record in records:
        by_kind[record["kind"]].append(record)
    holdouts = {}
    predictions = {}
    outcomes = {}
    costs = {}
    no_trades = {}
    for r in by_kind["holdout_freeze"]:
        scope = _id(r["scope_id"], "scope_id")
        if scope in holdouts:
            raise ProspectiveLedgerError("Duplicate frozen holdout scope.")
        for field in ("partition_sha256", "evaluation_plan_sha256", "model_selection_sha256"):
            _hex(r[field], field)
        freeze = _utc(r["freeze_at"])
        if freeze > r["_received"] or r["_received"] >= decision_at:
            raise ProspectiveLedgerError("Holdout freeze claim not registered before decision.")
        holdouts[scope] = r
    for r in by_kind["prediction_registration"]:
        pid = _hex(r["prediction_id"], "prediction_id")
        if pid in predictions:
            raise ProspectiveLedgerError("Duplicate prospective prediction ID.")
        if r["symbol"] != "EURUSD":
            raise ProspectiveLedgerError("Prospective symbol must be EURUSD.")
        scope = _id(r["scope_id"], "scope_id")
        for field in (
            "forecast_specification_sha256", "model_artifact_sha256",
            "context_snapshot_sha256", "feature_snapshot_sha256",
        ):
            _hex(r[field], field)
        horizon = r["horizon_seconds"]
        max_age = r["max_quote_age_seconds"]
        if (type(horizon) is not int or not 1 <= horizon <= 86400
                or type(max_age) is not int or not 0 <= max_age <= 300):
            raise ProspectiveLedgerError("Invalid forecast horizon or quote age limit.")
        created = _utc(r["created_at"])
        registered = _utc(r["registered_at"])
        if (_utc(r["decision_at"]) != decision_at
                or not (created <= registered <= r["_received"] < decision_at)
                or _utc(r["horizon_end_at"]) != decision_at + timedelta(seconds=horizon)):
            raise ProspectiveLedgerError("Prediction predecision registration/horizon invalid.")
        if scope not in holdouts or holdouts[scope]["_received"] >= registered:
            raise ProspectiveLedgerError("Heldout evaluation must be frozen before registration.")
        predictions[pid] = r
    for r in by_kind["outcome_quote"]:
        pid = _hex(r["prediction_id"], "prediction_id")
        if pid not in predictions or pid in outcomes:
            raise ProspectiveLedgerError("Orphan or duplicate post-horizon outcome.")
        if r["symbol"] != "EURUSD":
            raise ProspectiveLedgerError("Outcome symbol mismatch.")
        _id(r["source_record_id"], "source_record_id")
        bid = _positive_decimal(r["bid"], "bid")
        ask = _positive_decimal(r["ask"], "ask")
        if ask < bid:
            raise ProspectiveLedgerError("Crossed bid/ask outcome quote.")
        quote_time = _utc(r["quote_at"])
        available = _utc(r["available_at"])
        pred = predictions[pid]
        horizon_end = _utc(pred["horizon_end_at"])
        if (not horizon_end <= quote_time <= horizon_end + timedelta(
                seconds=pred["max_quote_age_seconds"])
                or not quote_time <= available <= r["_received"]):
            raise ProspectiveLedgerError("Outcome unavailable or outside declared horizon/age.")
        outcomes[pid] = r
    for r in by_kind["cost_receipt"]:
        pid = _hex(r["prediction_id"], "prediction_id")
        if pid not in predictions or pid in costs:
            raise ProspectiveLedgerError("Orphan or duplicate cost receipt.")
        if r["symbol"] != "EURUSD":
            raise ProspectiveLedgerError("Cost symbol mismatch.")
        _id(r["broker_fill_record_id"], "broker_fill_record_id")
        if not decision_at <= _utc(r["filled_at"]) <= r["_received"]:
            raise ProspectiveLedgerError("Broker cost claim has inconsistent fill time.")
        for field in ("spread_pips", "commission_pips", "slippage_pips", "financing_pips"):
            _positive_decimal(r[field], field, allow_zero=True)
        costs[pid] = r
    for r in by_kind["no_trade_scope"]:
        pid = _hex(r["prediction_id"], "prediction_id")
        if pid not in predictions or pid in no_trades:
            raise ProspectiveLedgerError("Orphan or duplicate no-trade scope.")
        _hex(r["scope_account_sha256"], "scope_account_sha256")
        if type(r["trade_count"]) is not int or r["trade_count"] != 0:
            raise ProspectiveLedgerError("No-trade claim must explicitly have zero trades.")
        if not (_utc(r["period_start_at"]) <= decision_at
                and _utc(r["period_end_at"]) >= _utc(predictions[pid]["horizon_end_at"])
                and _utc(r["period_end_at"]) <= r["_received"]):
            raise ProspectiveLedgerError("No-trade period does not cover prediction horizon.")
        no_trades[pid] = r
    if set(costs) & set(no_trades):
        raise ProspectiveLedgerError("Contradictory trade-cost and no-trade claims.")
    completed = sum(pid in outcomes and (pid in costs or pid in no_trades)
                    for pid in predictions)
    return {
        "candidate_counts": {kind: len(by_kind[kind]) for kind in KINDS},
        "prediction_ids_locally_reconciled": len(predictions),
        "complete_local_claimed_chains": completed,
        "predictions_without_post_horizon_outcome": sum(pid not in outcomes for pid in predictions),
        "predictions_without_cost_or_no_trade_scope": sum(
            pid not in costs and pid not in no_trades for pid in predictions),
        "locally_matched_chain_is_independent_proof": False,
        "independently_authenticated_prediction_registrations": 0,
        "independently_authenticated_outcomes": 0,
        "independently_authenticated_cost_or_no_trade_scopes": 0,
        "independently_authenticated_holdout_freezes": 0,
    }


def inspect_candidate_ledger(*, index_file, evidence_root, epoch_id, decision_at, as_of,
                             previous_index=None, previous_root=None):
    """Validate existing local candidates and optional append-only snapshot prefix."""
    empty_counts = {kind: 0 for kind in KINDS}
    if index_file is None:
        if any(x is not None for x in (evidence_root, previous_index, previous_root)):
            raise ProspectiveLedgerError("Evidence/snapshot root without current index.")
        return {
            "status": "NOT_SUPPLIED", "index_sha256": None,
            "locally_hash_matched_documents": 0, "candidate_counts": empty_counts,
            "prediction_ids_locally_reconciled": 0, "complete_local_claimed_chains": 0,
            "predictions_without_post_horizon_outcome": 0,
            "predictions_without_cost_or_no_trade_scope": 0,
            "previous_snapshot": "NOT_SUPPLIED", "local_append_only_prefix_consistent": None,
            "locally_matched_chain_is_independent_proof": False,
            "independently_authenticated_prediction_registrations": 0,
            "independently_authenticated_outcomes": 0,
            "independently_authenticated_cost_or_no_trade_scopes": 0,
            "independently_authenticated_holdout_freezes": 0,
        }
    decision = _utc(decision_at)
    cutoff = _utc(as_of)
    if cutoff < decision:
        raise ProspectiveLedgerError("Current assessment cutoff precedes decision.")
    present = _load_index(index_file, evidence_root, decision_at=decision,
                          epoch_id=epoch_id, current_cutoff=cutoff)
    if present["index_as_of"] != cutoff.isoformat():
        raise ProspectiveLedgerError("Current index as-of must match assessment cutoff.")
    data = _validate_relations(present["records"], decision_at=decision)
    previous_status = "NOT_SUPPLIED"
    consistent = None
    if previous_index is None:
        if previous_root is not None:
            raise ProspectiveLedgerError("Previous root without previous index.")
    else:
        if previous_root is None:
            raise ProspectiveLedgerError("Previous index requires previous root.")
        old = _load_index(previous_index, previous_root, decision_at=decision,
                          epoch_id=epoch_id, current_cutoff=cutoff)
        _validate_relations(old["records"], decision_at=decision)
        if old["entries"] != present["entries"][:len(old["entries"])]:
            raise ProspectiveLedgerError("Previous ledger entries modified, reordered or deleted.")
        if _utc(old["index_as_of"]) > _utc(present["index_as_of"]):
            raise ProspectiveLedgerError("Previous snapshot cutoff after current cutoff.")
        previous_status = "LOCAL_PREFIX_CHECKED_UNATTESTED"
        consistent = True
    return {
        "status": "LOCAL_CANDIDATE_LEDGER_CONSISTENT_UNATTESTED",
        "index_sha256": present["index_sha256"],
        "locally_hash_matched_documents": present["locally_hash_matched_documents"],
        "previous_snapshot": previous_status,
        "local_append_only_prefix_consistent": consistent,
        **data,
    }


def assess_stage6_prospective_ledger(*, contracts_file, settlement_file,
                                     epoch_id, decision_at, as_of, index_file=None,
                                     evidence_root=None, previous_index=None,
                                     previous_root=None):
    if epoch_id != HISTORICAL_EPOCH:
        raise ProspectiveLedgerError("Historical epoch mismatch; no rebind allowed.")
    if _utc(decision_at) > _utc(as_of):
        raise ProspectiveLedgerError("Decision after assessment cutoff.")
    static = inspect_learning_source_contracts(
        contracts_file=contracts_file, settlement_file=settlement_file,
    )
    local = inspect_candidate_ledger(
        index_file=index_file, evidence_root=evidence_root, epoch_id=epoch_id,
        decision_at=decision_at, as_of=as_of,
        previous_index=previous_index, previous_root=previous_root,
    )
    blockers = [
        "EXTERNAL_PREDECISION_PREDICTION_REGISTRATION_NOT_ATTESTED",
        "INDEPENDENT_POST_HORIZON_OUTCOME_RECEIPTS_NOT_ATTESTED",
        "BROKER_COST_OR_NO_TRADE_SCOPE_NOT_INDEPENDENTLY_CONFIRMED",
        "INDEPENDENT_PRE_EVALUATION_HOLDOUT_FREEZE_NOT_ATTESTED",
        "PROSPECTIVE_NET_OF_COSTS_AND_HELDOUT_PERFORMANCE_NOT_VERIFIED",
    ]
    if local["status"] == "NOT_SUPPLIED":
        blockers.append("NO_EXISTING_PROSPECTIVE_LEDGER_INDEX_SUPPLIED")
    elif local["previous_snapshot"] == "NOT_SUPPLIED":
        blockers.append("NO_PREVIOUS_LOCAL_SNAPSHOT_TO_COMPARE_APPEND_ONLY_PREFIX")
    report = {
        "assessment_version": VERSION,
        "scope": "P6_13_TO_P6_16_OFFLINE_PROSPECTIVE_CUSTODY",
        "epoch_id": epoch_id, "decision_at": _utc(decision_at).isoformat(),
        "as_of": _utc(as_of).isoformat(),
        "learning_contracts": static,
        "candidate_ledger": local,
        "external_prospective_gates_verified": 0,
        "independent_external_timestamp_service_verified": False,
        "independent_outcome_source_verified": False,
        "broker_trade_costs_or_no_trade_scope_verified": False,
        "independent_holdout_freeze_verified": False,
        "prospective_profitability_established": False,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(blockers),
        "read_only": True, "files_written": False,
        "database_accessed": False, "network_accessed": False,
        "broker_connected": False, "collector_started": False,
        "historical_epoch_rebound": False, "council_invoked": False,
        "settlement_invoked": False, "training_authorized": False,
        "paper_execution_authorized": False,
        "live_execution_authorized": False,
        "final_action": "HOLD",
    }
    report["report_sha256"] = _digest(report)
    return report


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-source", action="store_true", required=True)
    parser.add_argument("--read-only-evidence", action="store_true", required=True)
    parser.add_argument("--contracts-file", default="backend/learning/contracts.py")
    parser.add_argument("--settlement-file", default="backend/learning/settle_outcomes.py")
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--decision-at", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--index-file")
    parser.add_argument("--evidence-root")
    parser.add_argument("--previous-index")
    parser.add_argument("--previous-root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if _utc(args.as_of) > datetime.now(UTC):
            raise ProspectiveLedgerError("Future inspection cutoff refused.")
        report = assess_stage6_prospective_ledger(
            contracts_file=args.contracts_file,
            settlement_file=args.settlement_file,
            epoch_id=args.epoch_id, decision_at=args.decision_at,
            as_of=args.as_of, index_file=args.index_file,
            evidence_root=args.evidence_root,
            previous_index=args.previous_index,
            previous_root=args.previous_root,
        )
    except (ProspectiveLedgerError, OSError, ValueError, TypeError) as exc:
        print("STAGE 6 PROSPECTIVE LEDGER BLOCKED: " + type(exc).__name__)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Stage 6 P6-13..16 prospective ledger inspection")
        print("Learning contracts: " + report["learning_contracts"]["status"])
        print("Optional ledger: " + report["candidate_ledger"]["status"])
        print("Local candidate chains: " + str(report["candidate_ledger"]["complete_local_claimed_chains"]))
        print("Previous local snapshot: " + report["candidate_ledger"]["previous_snapshot"])
        print("Independent prospective gates: 0/4")
        print("Net-of-costs and held-out performance: NOT VERIFIED")
        for blocker in report["blockers"]:
            print("  BLOCKER " + blocker)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | NO SETTLEMENT | NO COUNCIL | NO ORDERS | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()

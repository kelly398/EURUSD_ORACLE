
"""S5-17..20: read-only prospective evaluation and holdout evidence review.

Inspects existing Python contracts and optionally supplied existing JSON files.
The source index, document hashes, issuer labels and timestamps are self-claims;
none establish external custody, provider trust or profitability. No Council,
settlement, database, collector, network, training or order execution.
"""

import argparse
import ast
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path, PurePosixPath
import re

VERSION = "EURUSD_STAGE5_PROSPECTIVE_EVIDENCE_S5_17_20_V1"
KINDS = ("prediction", "outcome", "cost", "holdout")
SHA = re.compile(r"^[0-9a-f]{64}$")
LABEL = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
UTC = timezone.utc
MAX_SOURCE = 1048576
MAX_INDEX = 131072
MAX_ARTIFACT = 4194304
MAX_TOTAL = 16777216
MAX_ENTRIES = 64
INDEX_KEYS = {"schema_version", "epoch_id", "as_of", "entries"}
ENTRY_KEYS = {
    "kind", "record_id", "relative_path", "file_sha256",
    "claimed_issuer", "claimed_issued_at", "claimed_received_at",
}
ARTIFACT_KEYS = {
    "prediction": {
        "schema_version", "epoch_id", "prediction_id", "symbol", "direction",
        "created_at", "decision_at", "horizon_seconds", "partition_id",
        "model_sha256", "inputs_sha256", "execution_scope",
    },
    "outcome": {
        "schema_version", "epoch_id", "prediction_id", "observed_at",
        "available_at", "bid", "ask", "source_record_id",
    },
    "cost": {
        "schema_version", "epoch_id", "prediction_id", "executed_at",
        "fill_receipt_id", "spread_pips", "commission_pips",
        "slippage_pips", "financing_pips",
    },
    "holdout": {
        "schema_version", "epoch_id", "partition_id", "frozen_at",
        "partition_sha256", "evaluation_plan_sha256",
    },
}


class Stage5ProspectiveError(ValueError):
    """Supplied local prospective candidate is malformed or causally invalid."""


def _utc(value):
    if not isinstance(value, str):
        raise Stage5ProspectiveError("Explicit UTC timestamp text required.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise Stage5ProspectiveError("Invalid UTC timestamp.") from None
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise Stage5ProspectiveError("Explicit zero-offset UTC timestamp required.")
    return result.astimezone(UTC)


def _digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(raw.encode("utf-8")).hexdigest()


def _unique(pairs):
    result = {}
    for key, val in pairs:
        if key in result:
            raise Stage5ProspectiveError("Duplicate JSON field.")
        result[key] = val
    return result


def _read(file, limit):
    p = Path(file)
    if p.is_symlink() or not p.is_file():
        raise Stage5ProspectiveError("Existing regular evidence file required.")
    try:
        with p.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError:
        raise Stage5ProspectiveError("Existing evidence unreadable.") from None
    if len(raw) > limit:
        raise Stage5ProspectiveError("Evidence size bound exceeded.")
    return raw


def _json(raw):
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(
                Stage5ProspectiveError("Nonfinite JSON value refused.")
            ),
        )
    except Stage5ProspectiveError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise Stage5ProspectiveError("Invalid evidence JSON.") from None
    if not isinstance(value, dict):
        raise Stage5ProspectiveError("Evidence JSON object required.")
    return value


def _inside(root, relative):
    if not isinstance(relative, str) or not 1 <= len(relative) <= 240:
        raise Stage5ProspectiveError("Evidence relative path invalid.")
    if "\\" in relative or ":" in relative or "\x00" in relative:
        raise Stage5ProspectiveError("Unsafe evidence path.")
    if PurePosixPath(relative).is_absolute() or any(
        part in ("", ".", "..") for part in relative.split("/")
    ):
        raise Stage5ProspectiveError("Evidence path traversal refused.")
    base = Path(root)
    if not base.is_dir() or base.is_symlink():
        raise Stage5ProspectiveError("Existing non-symlink evidence root required.")
    for part in relative.split("/"):
        base = base / part
        if base.is_symlink():
            raise Stage5ProspectiveError("Symlinked evidence refused.")
    return base


def _is_sha(value):
    return isinstance(value, str) and bool(SHA.fullmatch(value))


def _is_label(value):
    return isinstance(value, str) and bool(LABEL.fullmatch(value))


def _schema(item, kind, epoch_id):
    if (set(item) != ARTIFACT_KEYS[kind]
            or type(item.get("schema_version")) is not int
            or item["schema_version"] != 1
            or item["epoch_id"] != epoch_id):
        raise Stage5ProspectiveError("Evidence artifact schema/epoch mismatch.")


def _positive_price(value):
    return type(value) in (int, float) and isfinite(value) and value > 0


def _nonnegative_cost(value):
    return type(value) in (int, float) and isfinite(value) and value >= 0


def inspect_memory_contracts(contracts_file, settlement_file):
    """AST inspection only: never imports, runs or initializes Council memory."""
    files = []
    for path in (contracts_file, settlement_file):
        raw = _read(path, MAX_SOURCE)
        try:
            tree = ast.parse(raw.decode("utf-8"))
        except (UnicodeError, SyntaxError, RecursionError, ValueError):
            raise Stage5ProspectiveError("Memory source is not parseable Python.") from None
        files.append((tree, sha256(raw).hexdigest()))
    names = {node.name for node in files[0][0].body if isinstance(node, ast.ClassDef)}
    if not {"RunProvenance", "ForecastSpecification", "OutcomeQuote"} <= names:
        raise Stage5ProspectiveError("Required prospective memory contracts missing.")
    declared = {node.name for node in files[1][0].body if isinstance(node, ast.ClassDef)}
    if "SettlementRequest" not in declared:
        raise Stage5ProspectiveError("SettlementRequest contract missing.")
    return {
        "status": "LOCAL_STATIC_MEMORY_CONTRACTS_PRESENT",
        "memory_contracts_sha256": files[0][1],
        "settlement_contracts_sha256": files[1][1],
        "forecast_specification_present": True,
        "outcome_quote_contract_present": True,
        "settlement_request_contract_present": True,
        "source_files_executed": False,
        "external_prediction_registration_verified": False,
    }


def _validate_artifact(kind, entry, artifact, epoch_id, cutoff):
    _schema(artifact, kind, epoch_id)
    issued = _utc(entry["claimed_issued_at"])
    received = _utc(entry["claimed_received_at"])
    if issued > received or received > cutoff:
        raise Stage5ProspectiveError("Claimed evidence issue/receipt chronology invalid.")
    if kind == "prediction":
        if (not _is_sha(artifact["prediction_id"])
                or not _is_sha(artifact["model_sha256"])
                or not _is_sha(artifact["inputs_sha256"])
                or not _is_label(artifact["partition_id"])
                or artifact["symbol"] != "EURUSD"
                or artifact["direction"] not in ("BUY", "SELL", "HOLD")
                or artifact["execution_scope"] not in ("NO_TRADE_DECLARED", "TRADE_CLAIMED")
                or type(artifact["horizon_seconds"]) is not int
                or not 1 <= artifact["horizon_seconds"] <= 86400):
            raise Stage5ProspectiveError("Prediction contract invalid.")
        created = _utc(artifact["created_at"])
        decision = _utc(artifact["decision_at"])
        if not created <= issued <= received <= decision <= cutoff:
            raise Stage5ProspectiveError("Prediction not claimed registered before decision.")
        return ("prediction", artifact["prediction_id"])
    if kind == "outcome":
        if (not _is_sha(artifact["prediction_id"])
                or not _is_label(artifact["source_record_id"])
                or not _positive_price(artifact["bid"])
                or not _positive_price(artifact["ask"])
                or artifact["ask"] < artifact["bid"]):
            raise Stage5ProspectiveError("Outcome quote or identity invalid.")
        observed = _utc(artifact["observed_at"])
        available = _utc(artifact["available_at"])
        if not observed <= available <= issued <= received <= cutoff:
            raise Stage5ProspectiveError("Outcome availability/receipt chronology invalid.")
        return ("outcome", artifact["prediction_id"])
    if kind == "cost":
        if (not _is_sha(artifact["prediction_id"])
                or not _is_label(artifact["fill_receipt_id"])
                or not all(_nonnegative_cost(artifact[x]) for x in (
                    "spread_pips", "commission_pips", "slippage_pips", "financing_pips"
                ))):
            raise Stage5ProspectiveError("Claimed fill/cost contract invalid.")
        executed = _utc(artifact["executed_at"])
        if not executed <= issued <= received <= cutoff:
            raise Stage5ProspectiveError("Claimed cost timing invalid.")
        return ("cost", artifact["prediction_id"])
    if (not _is_label(artifact["partition_id"])
            or not _is_sha(artifact["partition_sha256"])
            or not _is_sha(artifact["evaluation_plan_sha256"])):
        raise Stage5ProspectiveError("Held-out freeze contract invalid.")
    frozen = _utc(artifact["frozen_at"])
    if not frozen <= issued <= received <= cutoff:
        raise Stage5ProspectiveError("Held-out freeze chronology invalid.")
    return ("holdout", artifact["partition_id"])


def inspect_candidate_index(index_file, *, root, epoch_id, as_of):
    """Reconcile existing candidate file content and causal cross-record links."""
    counts = {k: 0 for k in KINDS}
    empty = {
        "status": "NOT_SUPPLIED", "index_sha256": None,
        "locally_hash_matched_files": 0, "candidate_counts": counts,
        "complete_local_prediction_outcome_pairs": 0,
        "complete_local_evaluation_chains": 0,
        "declared_no_trade_predictions": 0,
        "claimed_trade_predictions": 0,
        "post_horizon_quotes_locally_reconciled": 0,
        "holdout_freezes_locally_precommitted": 0,
        "externally_authenticated_registrations": 0,
        "independently_verified_outcomes": 0,
        "broker_confirmed_costs": 0,
        "independently_verified_holdout_freezes": 0,
        "performance_calculated": False,
    }
    if index_file is None:
        if root is not None:
            raise Stage5ProspectiveError("Evidence root without index refused.")
        return empty
    if root is None:
        raise Stage5ProspectiveError("Evidence root required with index.")
    if not _is_sha(epoch_id):
        raise Stage5ProspectiveError("Invalid historical epoch SHA-256.")
    cutoff = _utc(as_of)
    raw = _read(index_file, MAX_INDEX)
    index = _json(raw)
    if (set(index) != INDEX_KEYS or type(index["schema_version"]) is not int
            or index["schema_version"] != 1 or index["epoch_id"] != epoch_id
            or _utc(index["as_of"]) != cutoff):
        raise Stage5ProspectiveError("Evaluation index epoch/review scope mismatch.")
    entries = index["entries"]
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise Stage5ProspectiveError("Evaluation evidence entry count exceeded.")
    records = {}
    ids = set()
    total = 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            raise Stage5ProspectiveError("Evaluation evidence entry fields invalid.")
        kind = entry["kind"]
        rid = entry["record_id"]
        if kind not in KINDS or not _is_label(rid) or rid in ids:
            raise Stage5ProspectiveError("Invalid/duplicate evaluation evidence identifier.")
        if not _is_label(entry["claimed_issuer"]) or not _is_sha(entry["file_sha256"]):
            raise Stage5ProspectiveError("Claimed issuer or evidence digest invalid.")
        file = _inside(root, entry["relative_path"])
        contents = _read(file, MAX_ARTIFACT)
        total += len(contents)
        if total > MAX_TOTAL:
            raise Stage5ProspectiveError("Combined evidence size bound exceeded.")
        if sha256(contents).hexdigest() != entry["file_sha256"]:
            raise Stage5ProspectiveError("Candidate file SHA-256 mismatch.")
        artifact = _json(contents)
        key = _validate_artifact(kind, entry, artifact, epoch_id, cutoff)
        if key in records:
            raise Stage5ProspectiveError("Duplicate prediction/partition evidence kind.")
        records[key] = (artifact, entry)
        ids.add(rid)
        counts[kind] += 1
    pairs = 0
    complete = 0
    freeze_seen = set()
    no_trade = 0
    claimed_trade = 0
    outcomes_checked = 0
    for (kind, pid), (outcome, _) in records.items():
        if kind not in ("outcome", "cost"):
            continue
        pred_pair = records.get(("prediction", pid))
        if pred_pair is None:
            raise Stage5ProspectiveError("Outcome/cost lacks registered prediction.")
        pred, pred_entry = pred_pair
        registered = _utc(pred_entry["claimed_received_at"])
        decision = _utc(pred["decision_at"])
        target = decision + timedelta(seconds=pred["horizon_seconds"])
        if kind == "outcome":
            observed = _utc(outcome["observed_at"])
            received = _utc(records[(kind, pid)][1]["claimed_received_at"])
            if not target <= observed <= target + timedelta(seconds=300):
                raise Stage5ProspectiveError("Outcome quote not within post-horizon window.")
            if received <= registered:
                raise Stage5ProspectiveError("Outcome receipt does not follow registration.")
            outcomes_checked += 1
        else:
            if pred["execution_scope"] != "TRADE_CLAIMED":
                raise Stage5ProspectiveError("Cost document supplied for no-trade prediction.")
            if _utc(outcome["executed_at"]) < decision:
                raise Stage5ProspectiveError("Claimed fill predates prediction decision.")
    for (kind, pid), (pred, pred_entry) in records.items():
        if kind != "prediction":
            continue
        freeze_pair = records.get(("holdout", pred["partition_id"]))
        if freeze_pair is not None:
            freeze, freeze_entry = freeze_pair
            if _utc(freeze_entry["claimed_received_at"]) > _utc(pred["created_at"]):
                raise Stage5ProspectiveError("Held-out freeze received after prediction creation.")
            freeze_seen.add(pred["partition_id"])
        outcome = records.get(("outcome", pid))
        cost = records.get(("cost", pid))
        if pred["execution_scope"] == "TRADE_CLAIMED":
            claimed_trade += 1
        else:
            no_trade += 1
        if outcome:
            pairs += 1
        if outcome and freeze_pair and (cost or pred["execution_scope"] == "NO_TRADE_DECLARED"):
            complete += 1
        # Complete locally is still never externally attested.
    return {
        "status": "LOCAL_EVALUATION_CANDIDATES_UNATTESTED",
        "index_sha256": sha256(raw).hexdigest(),
        "locally_hash_matched_files": len(entries),
        "candidate_counts": counts,
        "complete_local_prediction_outcome_pairs": pairs,
        "complete_local_evaluation_chains": complete,
        "declared_no_trade_predictions": no_trade,
        "claimed_trade_predictions": claimed_trade,
        "post_horizon_quotes_locally_reconciled": outcomes_checked,
        "holdout_freezes_locally_precommitted": len(freeze_seen),
        "externally_authenticated_registrations": 0,
        "independently_verified_outcomes": 0,
        "broker_confirmed_costs": 0,
        "independently_verified_holdout_freezes": 0,
        "performance_calculated": False,
    }


def assess_stage5_prospective(*, memory_contracts_file, settlement_file,
                              epoch_id, as_of, index_file=None, evidence_root=None):
    if not _is_sha(epoch_id):
        raise Stage5ProspectiveError("Invalid epoch SHA-256.")
    cutoff = _utc(as_of)
    static = inspect_memory_contracts(memory_contracts_file, settlement_file)
    candidate = inspect_candidate_index(
        index_file, root=evidence_root, epoch_id=epoch_id, as_of=cutoff.isoformat()
    )
    blockers = [
        "EXTERNAL_PREDECISION_PREDICTION_REGISTRATION_NOT_ATTESTED",
        "INDEPENDENT_POST_HORIZON_OUTCOME_RECEIPTS_NOT_ATTESTED",
        "BROKER_COSTS_OR_NO_TRADE_SCOPE_NOT_INDEPENDENTLY_CONFIRMED",
        "INDEPENDENT_PRE_EVALUATION_HOLDOUT_FREEZE_NOT_ATTESTED",
        "PROSPECTIVE_PERFORMANCE_AFTER_COSTS_NOT_ESTABLISHED",
    ]
    if candidate["status"] == "NOT_SUPPLIED":
        blockers.append("NO_EXISTING_PROSPECTIVE_EVIDENCE_INDEX_SUPPLIED")
    for kind in KINDS:
        if not candidate["candidate_counts"][kind]:
            blockers.append("LOCAL_EVIDENCE_KIND_NOT_SUPPLIED:" + kind)
    result = {
        "assessment_version": VERSION, "epoch_id": epoch_id,
        "as_of": cutoff.isoformat(), "static_memory_contracts": static,
        "candidate_evidence": candidate,
        "external_prospective_gates_independently_verified": 0,
        "out_of_sample_performance_verified": False,
        "net_of_costs_performance_verified": False,
        "verified_no_trade_scope": False,
        "research_ready": False,
        "required_next_evidence": [
            "OBTAIN_TRUSTED_APPEND_ONLY_PREDICTION_REGISTRATION_BEFORE_DECISION",
            "ACQUIRE_INDEPENDENT_POST_HORIZON_QUOTE_WITH_ORIGINAL_RECEIPT_TIME",
            "OBTAIN_BROKER_FILL_SPREAD_COMMISSION_SLIPPAGE_FINANCING_IF_TRADES_EXIST",
            "ATTEST_NO_TRADE_SCOPE_IF_NO_FILLS_WERE_CREATED",
            "OBTAIN_EXTERNALLY_TIMESTAMPED_HOLDOUT_SPLIT_FREEZE_BEFORE_EVALUATION",
            "VERIFY_SOURCE_AUTHENTICITY_AND_EVALUATE_WITHOUT_LOOKAHEAD_BIAS",
        ],
        "blockers": sorted(blockers),
        "files_written": False, "database_accessed": False,
        "network_accessed": False, "broker_connected": False,
        "collector_started": False, "council_invoked": False,
        "training_authorized": False, "execution_authorized": False,
        "research_admission_authorized": False,
        "read_only": True, "final_action": "HOLD",
    }
    result["report_sha256"] = _digest(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-source", action="store_true", required=True)
    parser.add_argument("--read-only-evidence", action="store_true", required=True)
    parser.add_argument("--memory-contracts-file", default="backend/learning/contracts.py")
    parser.add_argument("--settlement-file", default="backend/learning/settle_outcomes.py")
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--index-file")
    parser.add_argument("--evidence-root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if _utc(args.as_of) > datetime.now(UTC):
            raise Stage5ProspectiveError("Future review cutoff refused.")
        report = assess_stage5_prospective(
            memory_contracts_file=args.memory_contracts_file,
            settlement_file=args.settlement_file,
            epoch_id=args.epoch_id, as_of=args.as_of,
            index_file=args.index_file, evidence_root=args.evidence_root,
        )
    except (Stage5ProspectiveError, ValueError, OSError) as exc:
        print("STAGE 5 PROSPECTIVE EVIDENCE BLOCKED: " + type(exc).__name__)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Stage 5 S5-17..20 prospective evidence review")
        print("Memory contract inventory: " + report["static_memory_contracts"]["status"])
        print("Existing evidence index: " + report["candidate_evidence"]["status"])
        print("Local candidate counts: " + str(report["candidate_evidence"]["candidate_counts"]))
        print("Independent prospective gates verified: 0/4")
        print("Net-of-costs / held-out performance: NOT VERIFIED")
        for blocker in report["blockers"]:
            print("  BLOCKER " + blocker)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | NO COUNCIL | NO SETTLEMENT | NO ORDERS | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()

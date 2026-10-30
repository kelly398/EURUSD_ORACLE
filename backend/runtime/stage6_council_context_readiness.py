
"""Stage 6 P6-09..12: offline point-in-time Council input readiness inspection.

Static AST-only contract inspection. Optional existing local evidence files are
read, never imported or executed; hashes/timestamps do not prove external origin.
Does not dispatch any agent, open databases, connect to MT5 or approve trades.
"""

import argparse
import ast
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re

VERSION = "EURUSD_STAGE6_COUNCIL_CONTEXT_P6_09_12_V1"
EPOCH = "75311e239cd5c295d3e08e03c358488fb123a46bfc6d342dd31493d95710aa33"
FAMILIES = (
    "market_state", "candles", "ticks", "cot_data", "ecb_data",
    "fed_data", "economic_calendar", "cross_asset_data", "calendar_coverage",
    "risk_state", "risk_limits", "reliability", "synthesis_policy",
)
# These are contextual/derived inputs, not fourteen more certified source feeds.
DERIVED_INPUTS = {
    "market_structure": ("market_state", "candles"),
    "feature_vector": ("candles", "market_state", "ticks"),
    "tick_features": ("ticks",),
    "liquidity_state": ("market_state", "ticks"),
    "session_state": ("market_state", "candles"),
    "rates_data": ("ecb_data", "fed_data"),
}
INTERNAL_STAGE_INPUTS = ("primary_agent_opinions", "governance_opinions")
MODEL_REQUIREMENT = "model_artifact"
# Additional runtime requirements beyond manifest labels (from execution policy).
RUNTIME_EXTRA = {
    "economic_calendar": ("calendar_coverage", "risk_limits"),
    "risk_officer": ("risk_limits",),
    "chief_strategist": ("synthesis_policy", "reliability"),
}
KIND_BY_FAMILY = {
    "market_state": ("DERIVED",), "candles": ("OBSERVED",),
    "ticks": ("OBSERVED",), "cot_data": ("OBSERVED",),
    "ecb_data": ("OBSERVED", "EFFECTIVE"),
    "fed_data": ("OBSERVED", "EFFECTIVE"),
    "economic_calendar": ("SCHEDULED", "OBSERVED"),
    "cross_asset_data": ("OBSERVED",),
    "calendar_coverage": ("DERIVED",),
    "risk_state": ("DERIVED",), "risk_limits": ("OPERATOR_POLICY",),
    "reliability": ("EVALUATION",),
    "synthesis_policy": ("OPERATOR_POLICY",),
}
HEX = re.compile(r"^[0-9a-f]{64}$")
LABEL = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
MAX_SOURCE_BYTES = 1048576
MAX_INDEX_BYTES = 131072
MAX_ARTIFACT_BYTES = 1048576
MAX_ARTIFACT_TOTAL = 16777216
MAX_RECORDS = 128
INDEX_KEYS = {"schema_version", "epoch_id", "decision_at", "entries"}
RECORD_KEYS = {
    "record_id", "family", "evidence_kind", "relative_path", "file_sha256",
    "observed_at", "first_available_at", "captured_at", "supersedes", "dependencies",
}


class CouncilContextError(ValueError):
    """Source shape, chronology, lineage or historical-scope conflict."""


def _utc(value):
    if not isinstance(value, str):
        raise CouncilContextError("UTC timestamp string required.")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise CouncilContextError("Invalid UTC timestamp.") from None
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise CouncilContextError("Explicit zero-offset UTC required.")
    return stamp.astimezone(timezone.utc)


def _digest(obj):
    return sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")).hexdigest()


def _unique(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise CouncilContextError("Duplicate JSON field refused.")
        out[k] = v
    return out


def _decode(raw):
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(
                                CouncilContextError("Nonfinite JSON refused.")))
    except CouncilContextError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise CouncilContextError("Invalid JSON data.") from None
    if not isinstance(result, dict):
        raise CouncilContextError("JSON object required.")
    return result


def _read(path, maximum):
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise CouncilContextError("Existing ordinary source file required.")
    try:
        with p.open("rb") as handle:
            data = handle.read(maximum + 1)
    except OSError:
        raise CouncilContextError("Source unreadable.") from None
    if len(data) > maximum:
        raise CouncilContextError("Source file size limit exceeded.")
    return data


def _contained(root, relative):
    if (not isinstance(relative, str) or not 1 <= len(relative) <= 240
            or "\\" in relative or ":" in relative or "\x00" in relative
            or PurePosixPath(relative).is_absolute()
            or any(x in ("", ".", "..") for x in relative.split("/"))):
        raise CouncilContextError("Unsafe relative evidence path.")
    path = Path(root)
    if not path.is_dir() or any(part.is_symlink() for part in (path, *path.parents)):
        raise CouncilContextError("Existing non-symlink evidence root required.")
    for part in relative.split("/"):
        path = path / part
        if path.is_symlink():
            raise CouncilContextError("Symlinked evidence path refused.")
    return path


def _literal(node):
    """ast.literal_eval only, never evaluate imports, functions or properties."""
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, RecursionError):
        raise CouncilContextError("Nonliteral Council manifest value refused.") from None


def inspect_static_council(*, contracts_file, manifest_file):
    """AST-read canonical 31-seat manifest and 13 family fields, no imports."""
    files = {}
    for name, path in (("contracts", contracts_file), ("manifest", manifest_file)):
        raw = _read(path, MAX_SOURCE_BYTES)
        try:
            files[name] = (ast.parse(raw.decode("utf-8")), sha256(raw).hexdigest())
        except (SyntaxError, UnicodeError, ValueError, RecursionError):
            raise CouncilContextError("Council source AST parsing failed.") from None
    contracts = files["contracts"][0]
    contexts = [n for n in contracts.body if isinstance(n, ast.ClassDef)
                and n.name == "AgentContext"]
    if len(contexts) != 1:
        raise CouncilContextError("Exactly one static AgentContext required.")
    context_fields = [n.target.id for n in contexts[0].body
                      if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)]
    if len(context_fields) != len(set(context_fields)) or not set(FAMILIES).issubset(context_fields):
        raise CouncilContextError("Required 13 Council families absent from AgentContext.")
    manifest = files["manifest"][0]
    assignments = [n.value for n in manifest.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "COUNCIL_MANIFEST"
                           for t in n.targets)]
    if len(assignments) != 1 or not isinstance(assignments[0], (ast.Tuple, ast.List)):
        raise CouncilContextError("Single literal 31-seat Council manifest required.")
    seats = []
    seen = set()
    supported = set(FAMILIES) | set(DERIVED_INPUTS) | set(INTERNAL_STAGE_INPUTS) | {MODEL_REQUIREMENT}
    for call in assignments[0].elts:
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "AgentSpec":
            raise CouncilContextError("Unexpected Council manifest entry.")
        if call.args or any(k.arg is None for k in call.keywords):
            raise CouncilContextError("Dynamic/positional manifest entries refused.")
        fields = {k.arg: k.value for k in call.keywords}
        if len(fields) != len(call.keywords):
            raise CouncilContextError("Duplicate Council manifest keyword.")
        if not {"seat_number", "agent_id", "stage", "required_inputs"}.issubset(fields):
            raise CouncilContextError("Incomplete Council seat definition.")
        number = _literal(fields["seat_number"])
        seat_id = _literal(fields["agent_id"])
        required = _literal(fields["required_inputs"])
        stage_node = fields["stage"]
        if (type(number) is not int or not isinstance(seat_id, str)
                or not LABEL.fullmatch(seat_id) or seat_id in seen
                or not isinstance(required, tuple) or not all(isinstance(v, str) for v in required)
                or len(required) != len(set(required))
                or not set(required).issubset(supported)
                or not isinstance(stage_node, ast.Attribute)
                or not isinstance(stage_node.value, ast.Name)
                or stage_node.value.id != "AgentStage"
                or stage_node.attr not in ("PRIMARY", "GOVERNANCE", "SYNTHESIS")):
            raise CouncilContextError("Council seat fields or inputs invalid.")
        seen.add(seat_id)
        requirements = tuple(dict.fromkeys((*required, *RUNTIME_EXTRA.get(seat_id, ()))))
        families = sorted(set(x for r in requirements for x in
                              (DERIVED_INPUTS.get(r, (r,))) if x in FAMILIES))
        seats.append({
            "seat": number, "agent_id": seat_id,
            "stage": stage_node.attr,
            "required_manifest_labels": list(required),
            "required_runtime_extra_labels": list(RUNTIME_EXTRA.get(seat_id, ())),
            "required_source_families": families,
            "derived_labels": sorted(set(requirements) & set(DERIVED_INPUTS)),
            "needs_model_artifact_lineage": MODEL_REQUIREMENT in requirements,
            "needs_internal_stage_opinions": sorted(set(requirements) & set(INTERNAL_STAGE_INPUTS)),
        })
    if (len(seats) != 31 or [s["seat"] for s in seats] != list(range(1, 32))
            or [s["stage"] for s in seats] !=
            ["PRIMARY"] * 28 + ["GOVERNANCE"] * 2 + ["SYNTHESIS"]):
        raise CouncilContextError("Council ordering/stage manifest mismatch.")
    return {
        "status": "LOCAL_STATIC_AST_INVENTORY_ONLY",
        "contracts_sha256": files["contracts"][1],
        "manifest_sha256": files["manifest"][1],
        "source_executed_or_imported": False,
        "static_seats": 31, "static_source_families": len(FAMILIES),
        "families": list(FAMILIES),
        "derived_labels_are_not_independent_feeds": True,
        "seats": seats,
    }


def inspect_point_in_time_index(index_file, *, evidence_root, epoch_id, decision_at):
    """Bounded existing local candidates; reject leaks, cycles and retroactive updates."""
    counts = {name: 0 for name in FAMILIES}
    empty = {
        "status": "NOT_SUPPLIED", "index_sha256": None,
        "records_locally_hash_matched": 0,
        "family_candidate_counts": counts,
        "family_latest_visible_record_ids": {name: None for name in FAMILIES},
        "local_families_with_visible_candidate": 0,
        "dependency_edges_checked": 0,
        "independently_verified_source_families": 0,
        "independent_origin_or_receipt_attested": False,
    }
    if index_file is None:
        if evidence_root is not None:
            raise CouncilContextError("Evidence root without index refused.")
        return empty
    if evidence_root is None:
        raise CouncilContextError("Evidence root required with index.")
    cutoff = _utc(decision_at)
    raw = _read(index_file, MAX_INDEX_BYTES)
    doc = _decode(raw)
    if (set(doc) != INDEX_KEYS or type(doc.get("schema_version")) is not int
            or doc["schema_version"] != 1 or doc.get("epoch_id") != epoch_id
            or _utc(doc.get("decision_at")) != cutoff):
        raise CouncilContextError("Context index schema/epoch/decision cutoff mismatch.")
    entries = doc["entries"]
    if not isinstance(entries, list) or len(entries) > MAX_RECORDS:
        raise CouncilContextError("Context index entry count exceeds contract.")
    records = {}
    total = 0
    for record in entries:
        if not isinstance(record, dict) or set(record) != RECORD_KEYS:
            raise CouncilContextError("Context record has unexpected/missing fields.")
        rid, family, kind = record["record_id"], record["family"], record["evidence_kind"]
        if (not isinstance(rid, str) or not LABEL.fullmatch(rid) or rid in records
                or not isinstance(family, str) or family not in counts
                or not isinstance(kind, str) or kind not in KIND_BY_FAMILY[family]):
            raise CouncilContextError("Duplicate record, unrecognized family or incompatible kind.")
        raw_hash = record["file_sha256"]
        if not isinstance(raw_hash, str) or not HEX.fullmatch(raw_hash):
            raise CouncilContextError("Malformed artifact SHA-256.")
        available = _utc(record["first_available_at"])
        captured = _utc(record["captured_at"])
        observed = _utc(record["observed_at"]) if record["observed_at"] is not None else None
        if available > captured or captured > cutoff:
            raise CouncilContextError("Future or retroactive availability/capture evidence.")
        if observed is None and kind not in ("DERIVED", "OPERATOR_POLICY", "EVALUATION"):
            raise CouncilContextError("Observed/effective/scheduled event timestamp missing.")
        if observed is not None and observed > available and kind not in ("SCHEDULED", "EFFECTIVE"):
            raise CouncilContextError("Future observed event not yet available at decision.")
        parents = record["dependencies"]
        predecessor = record["supersedes"]
        if (not isinstance(parents, list) or len(parents) > 24
                or any(not isinstance(x, str) or not LABEL.fullmatch(x) for x in parents)
                or len(parents) != len(set(parents)) or rid in parents
                or (predecessor is not None and
                    (not isinstance(predecessor, str) or not LABEL.fullmatch(predecessor)
                     or predecessor == rid))):
            raise CouncilContextError("Invalid lineage/dependency identifiers.")
        if kind in ("DERIVED", "EVALUATION") and not parents:
            raise CouncilContextError("Derived/evaluation candidate must declare dependencies.")
        payload = _read(_contained(evidence_root, record["relative_path"]), MAX_ARTIFACT_BYTES)
        total += len(payload)
        if total > MAX_ARTIFACT_TOTAL:
            raise CouncilContextError("Context evidence total size exceeded.")
        if sha256(payload).hexdigest() != raw_hash:
            raise CouncilContextError("Context artifact SHA-256 mismatch.")
        records[rid] = {**record, "available": available, "captured": captured}
        counts[family] += 1
    deps = {}
    for rid, row in records.items():
        predecessor = row["supersedes"]
        if predecessor is not None:
            if predecessor not in records or records[predecessor]["family"] != row["family"]:
                raise CouncilContextError("Revision predecessor absent or from another family.")
            old = records[predecessor]
            if not (old["available"] < row["available"] and old["captured"] <= row["captured"]):
                raise CouncilContextError("Revised version availability must increase.")
        for parent in row["dependencies"]:
            if parent not in records:
                raise CouncilContextError("Unresolved dependency in local context DAG.")
            if records[parent]["available"] > row["available"]:
                raise CouncilContextError("Derived value precedes its dependency availability.")
        deps[rid] = list(row["dependencies"]) + ([predecessor] if predecessor else [])
    state = {}
    def visit(rid):
        if state.get(rid) == 1:
            raise CouncilContextError("Dependency/revision cycle refused.")
        if state.get(rid) == 2:
            return
        state[rid] = 1
        for parent in deps[rid]:
            visit(parent)
        state[rid] = 2
    for rid in sorted(records):
        visit(rid)
    # Every family may have only one root revision chain, preventing silent forks.
    latest = {name: None for name in FAMILIES}
    for family in FAMILIES:
        versions = [r for r in records.values() if r["family"] == family]
        if not versions:
            continue
        roots = [r for r in versions if r["supersedes"] is None]
        if len(roots) != 1:
            raise CouncilContextError("Ambiguous revision roots for source family.")
        successors = {r["supersedes"]: r["record_id"] for r in versions if r["supersedes"]}
        if len(successors) != len(versions) - 1:
            raise CouncilContextError("Forked revision lineage refused.")
        cursor = roots[0]["record_id"]
        while cursor in successors:
            cursor = successors[cursor]
        if sum(r["record_id"] == cursor for r in versions) != 1:
            raise CouncilContextError("Invalid source revision terminal.")
        latest[family] = cursor
    return {
        "status": "LOCAL_POINT_IN_TIME_CANDIDATES_UNATTESTED",
        "index_sha256": sha256(raw).hexdigest(),
        "records_locally_hash_matched": len(records),
        "family_candidate_counts": counts,
        "family_latest_visible_record_ids": latest,
        "local_families_with_visible_candidate": sum(v is not None for v in latest.values()),
        "dependency_edges_checked": sum(len(row["dependencies"]) for row in records.values()),
        "independently_verified_source_families": 0,
        "independent_origin_or_receipt_attested": False,
    }


def inspect_saved_envelope(report_file, *, epoch_id, decision_at, as_of):
    """Optional earlier Stage 6 report: local digest only, no attestation."""
    if report_file is None:
        return {"status": "NOT_SUPPLIED", "report_sha256": None,
                "independently_attested": False}
    data = _decode(_read(report_file, MAX_SOURCE_BYTES))
    digest = data.get("report_sha256")
    if (not isinstance(digest, str) or not HEX.fullmatch(digest)
            or _digest({k: v for k, v in data.items() if k != "report_sha256"}) != digest
            or data.get("epoch_id") != epoch_id
            or _utc(data.get("decision_at")) != _utc(decision_at)
            or _utc(data.get("as_of")) > _utc(as_of)
            or data.get("final_action") != "HOLD"
            or data.get("independent_source_families_verified") != 0
            or data.get("external_provenance_gates_verified") != 0
            or data.get("read_only") is not True):
        raise CouncilContextError("Saved source-envelope report cannot satisfy HOLD/local contract.")
    return {"status": "LOCAL_SAVED_REPORT_DIGEST_MATCHES_UNATTESTED",
            "report_sha256": digest, "independently_attested": False}


def assess_stage6_council_context(*, contracts_file, manifest_file, epoch_id,
                                  decision_at, as_of, context_index=None,
                                  evidence_root=None, envelope_report=None):
    """Construct deterministic *inspection report*, never AgentContext or votes."""
    if epoch_id != EPOCH:
        raise CouncilContextError("Historical epoch mismatch; no rebind allowed.")
    if _utc(decision_at) > _utc(as_of):
        raise CouncilContextError("Council decision occurs after review cutoff.")
    static = inspect_static_council(contracts_file=contracts_file, manifest_file=manifest_file)
    local = inspect_point_in_time_index(
        context_index, evidence_root=evidence_root, epoch_id=epoch_id,
        decision_at=decision_at,
    )
    saved = inspect_saved_envelope(
        envelope_report, epoch_id=epoch_id, decision_at=decision_at, as_of=as_of,
    )
    visible = {name for name, rid in local["family_latest_visible_record_ids"].items() if rid}
    seats = []
    for seat in static["seats"]:
        missing = sorted(set(seat["required_source_families"]) - visible)
        blockers = ["LOCAL_SOURCE_FAMILY_MISSING:" + x for x in missing]
        if seat["needs_model_artifact_lineage"]:
            blockers.append("MODEL_ARTIFACT_LINEAGE_NOT_INDEPENDENTLY_VERIFIED")
        if seat["agent_id"] in ("fed", "rate_differential"):
            blockers.append("FED_RELEASE_VINTAGE_NOT_INDEPENDENTLY_VERIFIED")
        if seat["agent_id"] == "risk_sentiment":
            blockers.append("CROSS_ASSET_PAIR_INTERVAL_NOT_SEMANTICALLY_RECONCILED")
        blockers.append("EXTERNAL_SOURCE_AND_DERIVED_CUSTODY_NOT_VERIFIED")
        seats.append({
            "seat": seat["seat"], "agent_id": seat["agent_id"],
            "stage": seat["stage"],
            "required_source_families": seat["required_source_families"],
            "missing_local_source_families": missing,
            "local_family_coverage_complete": not missing,
            "model_artifact_lineage_required": seat["needs_model_artifact_lineage"],
            "internal_stage_opinions_are_not_external_feeds": seat["needs_internal_stage_opinions"],
            "external_admission_approved": False,
            "dispatch_allowed": False,
            "blockers": sorted(blockers),
        })
    blockers = ["NO_INDEPENDENT_SOURCE_ATTESTATION_FOR_13_FAMILIES",
                "NO_AUTHORIZED_PROSPECTIVE_COUNCIL_CONTEXT_OR_DISPATCH",
                "FED_AND_MODEL_VERSION_PROVENANCE_UNATTESTED",
                "INTERNAL_GOVERNANCE_AND_RISK_VETOES_NOT_EXERCISED"]
    if local["status"] == "NOT_SUPPLIED":
        blockers.append("NO_OPTIONAL_POINT_IN_TIME_CONTEXT_INDEX_SUPPLIED")
    if saved["status"] == "NOT_SUPPLIED":
        blockers.append("NO_OPTIONAL_SAVED_STAGE6_SOURCE_ENVELOPE_REPORT_SUPPLIED")
    result = {
        "assessment_version": VERSION, "epoch_id": epoch_id,
        "decision_at": _utc(decision_at).isoformat(),
        "as_of": _utc(as_of).isoformat(),
        "scope": "P6_09_TO_P6_12_OFFLINE_COUNCIL_CONTEXT",
        "static_council_inventory": static,
        "point_in_time_candidate_context": local,
        "previous_stage6_source_envelope": saved,
        "seat_readiness": seats,
        "static_seat_count": 31,
        "static_input_family_count": 13,
        "locally_complete_source_family_count": local["local_families_with_visible_candidate"],
        "locally_complete_seat_source_coverage": sum(
            x["local_family_coverage_complete"] for x in seats),
        "source_families_independently_verified": 0,
        "council_seats_operationally_admitted": 0,
        "fed_production_source_admitted": False,
        "risk_veto_executed": False,
        "council_replay_executed": False,
        "model_artifacts_loaded": False,
        "point_in_time_snapshot_created": False,
        "performance_verified": False,
        "blockers": sorted(blockers),
        "research_ready": False, "read_only": True,
        "files_written": False, "network_accessed": False,
        "database_accessed": False, "broker_connected": False,
        "collector_started": False, "historical_epoch_rebound": False,
        "council_invoked": False, "training_authorized": False,
        "paper_execution_authorized": False, "live_execution_authorized": False,
        "final_action": "HOLD",
    }
    result["report_sha256"] = _digest(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-source", action="store_true", required=True)
    parser.add_argument("--read-only-evidence", action="store_true", required=True)
    parser.add_argument("--contracts-file", default="backend/agents/contracts.py")
    parser.add_argument("--manifest-file", default="backend/agents/council_manifest.py")
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--decision-at", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--context-index")
    parser.add_argument("--evidence-root")
    parser.add_argument("--envelope-report")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if _utc(args.as_of) > datetime.now(timezone.utc):
            raise CouncilContextError("Future review cutoff refused.")
        result = assess_stage6_council_context(
            contracts_file=args.contracts_file, manifest_file=args.manifest_file,
            epoch_id=args.epoch_id, decision_at=args.decision_at, as_of=args.as_of,
            context_index=args.context_index, evidence_root=args.evidence_root,
            envelope_report=args.envelope_report,
        )
    except (CouncilContextError, OSError, ValueError, TypeError) as exc:
        print("STAGE 6 COUNCIL CONTEXT BLOCKED: " + type(exc).__name__)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    else:
        print("EURUSD ORACLE | Stage 6 P6-09..12 Council context inspection")
        print("Static seats/families: %s/%s" % (
            result["static_seat_count"], result["static_input_family_count"]))
        print("Local point-in-time context: " + result["point_in_time_candidate_context"]["status"])
        print("Locally visible source families: %s/13" % result["locally_complete_source_family_count"])
        print("Source families independently verified: 0/13")
        print("Council seats admitted: 0/31")
        print("Fed admission: BLOCKED | Model lineage: UNVERIFIED")
        for reason in result["blockers"]:
            print("  BLOCKER " + reason)
        print("SHA-256: " + result["report_sha256"])
        print("READ-ONLY | NO IMPORTS | NO COUNCIL | NO BROKER | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()

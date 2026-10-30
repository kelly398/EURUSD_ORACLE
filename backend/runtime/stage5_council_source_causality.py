
"""S5-13..16: read-only Council source and point-in-time evidence review.

AST-inspects existing Council contracts (no Council imports/execution). Optional
existing file-backed records are checked for local integrity, causality,
revisions and dependency closure. Metadata and hashes are self-declared; the
report does not certify external source authenticity or authorize operation.
"""

import argparse
import ast
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re

VERSION = "EURUSD_STAGE5_COUNCIL_SOURCE_CAUSALITY_S5_13_16_V1"
FAMILIES = (
    "market_state", "candles", "ticks", "cot_data", "ecb_data",
    "fed_data", "economic_calendar", "cross_asset_data",
    "calendar_coverage", "risk_state", "risk_limits", "reliability",
    "synthesis_policy",
)
KINDS = {
    "market_state": {"market", "derived_feature"},
    "candles": {"market"},
    "ticks": {"market"},
    "cot_data": {"cot"},
    "ecb_data": {"policy_rate"},
    "fed_data": {"policy_rate"},
    "economic_calendar": {"calendar_schedule", "calendar_actual"},
    "cross_asset_data": {"cross_asset"},
    "calendar_coverage": {"coverage"},
    "risk_state": {"risk"},
    "risk_limits": {"risk"},
    "reliability": {"reliability", "model_artifact"},
    "synthesis_policy": {"governance"},
}
SOURCE_FIELDS = {
    "schema_version", "epoch_id", "decision_at", "as_of", "records",
}
RECORD_FIELDS = {
    "family", "record_id", "source_kind", "source_record_id", "version",
    "revision_of", "claimed_issuer", "claimed_published_at",
    "claimed_available_at", "claimed_ingested_at", "economic_event_at",
    "dependencies", "relative_path", "file_sha256",
}
SHA = re.compile(r"^[0-9a-f]{64}$")
LABEL = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
MAX_SOURCE = 1024 * 1024
MAX_INDEX = 131072
MAX_RECORD = 4 * 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024
MAX_ENTRIES = 64
UTC = timezone.utc


class Stage5CouncilEvidenceError(ValueError):
    """Local Council evidence fails strict intake or causal checks."""


def _utc(value):
    if not isinstance(value, str):
        raise Stage5CouncilEvidenceError("Explicit UTC timestamp text required.")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise Stage5CouncilEvidenceError("Invalid UTC timestamp.") from None
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise Stage5CouncilEvidenceError("Explicit zero-offset UTC required.")
    return stamp.astimezone(UTC)


def _hash(obj):
    return sha256(json.dumps(
        obj, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _unique(pairs):
    data = {}
    for key, val in pairs:
        if key in data:
            raise Stage5CouncilEvidenceError("Duplicate JSON key.")
        data[key] = val
    return data


def _read(path, limit):
    file = Path(path)
    if file.is_symlink() or not file.is_file():
        raise Stage5CouncilEvidenceError(
            "Existing regular source/evidence file required."
        )
    try:
        with file.open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError:
        raise Stage5CouncilEvidenceError(
            "Source/evidence file unreadable."
        ) from None
    if len(raw) > limit:
        raise Stage5CouncilEvidenceError(
            "Source/evidence file exceeds size limit."
        )
    return raw


def _json(path, limit):
    raw = _read(path, limit)
    try:
        obj = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(
                Stage5CouncilEvidenceError("Nonfinite JSON refused.")
            ),
        )
    except Stage5CouncilEvidenceError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise Stage5CouncilEvidenceError(
            "Evidence index JSON invalid."
        ) from None
    if not isinstance(obj, dict):
        raise Stage5CouncilEvidenceError(
            "Evidence index must be JSON object."
        )
    return obj, sha256(raw).hexdigest()


def _tree(path):
    raw = _read(path, MAX_SOURCE)
    try:
        parsed = ast.parse(raw.decode("utf-8"))
    except (UnicodeError, SyntaxError, ValueError, RecursionError):
        raise Stage5CouncilEvidenceError(
            "Council source cannot be statically parsed."
        ) from None
    return parsed, sha256(raw).hexdigest()


def inspect_council_contracts(contracts_file, manifest_file):
    """Check static contracts, not input availability or Council execution."""
    contracts, contract_sha = _tree(contracts_file)
    classes = [
        n for n in contracts.body
        if isinstance(n, ast.ClassDef) and n.name == "AgentContext"
    ]
    if len(classes) != 1:
        raise Stage5CouncilEvidenceError(
            "Exactly one AgentContext required."
        )
    fields = [
        n.target.id for n in classes[0].body
        if isinstance(n, ast.AnnAssign)
        and isinstance(n.target, ast.Name)
    ]
    if len(fields) != len(set(fields)) or not set(FAMILIES) <= set(fields):
        raise Stage5CouncilEvidenceError(
            "13 source-facing Council families missing/duplicated."
        )

    tree, manifest_sha = _tree(manifest_file)
    bindings = [
        n for n in tree.body
        if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "COUNCIL_MANIFEST"
            for t in n.targets
        )
    ]
    if len(bindings) != 1 or not isinstance(
        bindings[0].value, (ast.Tuple, ast.List)
    ):
        raise Stage5CouncilEvidenceError(
            "Static COUNCIL_MANIFEST tuple required."
        )

    seats = []
    ids = []
    for item in bindings[0].value.elts:
        if (
            not isinstance(item, ast.Call)
            or not isinstance(item.func, ast.Name)
            or item.func.id != "AgentSpec"
        ):
            raise Stage5CouncilEvidenceError(
                "Council manifest must contain AgentSpec calls."
            )
        kw = {k.arg: k.value for k in item.keywords}
        if not all(
            k in kw and isinstance(kw[k], ast.Constant)
            for k in ("seat_number", "agent_id")
        ):
            raise Stage5CouncilEvidenceError(
                "Council seat identity must be literal."
            )
        seats.append(kw["seat_number"].value)
        ids.append(kw["agent_id"].value)

    if (
        seats != list(range(1, 32))
        or len(ids) != 31
        or any(
            not isinstance(x, str) or not LABEL.fullmatch(x)
            for x in ids
        )
        or len(set(ids)) != 31
    ):
        raise Stage5CouncilEvidenceError(
            "31 Council seat identities invalid."
        )

    return {
        "status": "LOCAL_STATIC_CONTRACTS_MATCH_UNATTESTED",
        "contracts_file_sha256": contract_sha,
        "manifest_file_sha256": manifest_sha,
        "expected_source_family_count": len(FAMILIES),
        "static_source_families_present": list(FAMILIES),
        "static_council_seat_count": len(seats),
        "source_files_executed": False,
        "data_families_independently_attested": 0,
    }


def _within(root, relative):
    if not isinstance(relative, str) or not 1 <= len(relative) <= 240:
        raise Stage5CouncilEvidenceError(
            "Invalid relative evidence path."
        )
    if "\\" in relative or ":" in relative or "\x00" in relative:
        raise Stage5CouncilEvidenceError(
            "Unsafe evidence path."
        )
    if PurePosixPath(relative).is_absolute() or any(
        segment in ("", ".", "..")
        for segment in relative.split("/")
    ):
        raise Stage5CouncilEvidenceError(
            "Evidence path traversal refused."
        )
    base = Path(root)
    if not base.is_dir() or base.is_symlink():
        raise Stage5CouncilEvidenceError(
            "Existing non-symlink evidence root required."
        )
    for segment in relative.split("/"):
        base = base / segment
        if base.is_symlink():
            raise Stage5CouncilEvidenceError(
                "Symlinked evidence path refused."
            )
    return base


def inspect_point_in_time_index(
    index_file, *, evidence_root, epoch_id, decision_at, as_of
):
    """Verify supplied local files, claimed releases, revisions and lineage."""
    counts = {family: 0 for family in FAMILIES}
    empty = {
        "status": "NOT_SUPPLIED",
        "index_sha256": None,
        "records_locally_hash_matched": 0,
        "families_with_candidates": 0,
        "family_candidate_counts": counts,
        "release_revisions_linked": 0,
        "derived_lineage_edges_checked": 0,
        "calendar_actual_records": 0,
        "calendar_schedule_records": 0,
        "independently_attested_source_families": 0,
        "historical_publication_times_independently_verified": False,
        "model_and_feature_origin_independently_verified": False,
    }
    if index_file is None:
        if evidence_root is not None:
            raise Stage5CouncilEvidenceError(
                "Evidence root without index refused."
            )
        return empty
    if evidence_root is None:
        raise Stage5CouncilEvidenceError(
            "Evidence root required with index."
        )
    if not isinstance(epoch_id, str) or not SHA.fullmatch(epoch_id):
        raise Stage5CouncilEvidenceError(
            "Invalid epoch SHA-256."
        )

    decision, cutoff = _utc(decision_at), _utc(as_of)
    if decision > cutoff:
        raise Stage5CouncilEvidenceError(
            "Decision after cutoff."
        )

    index, index_sha = _json(index_file, MAX_INDEX)
    if (
        set(index) != SOURCE_FIELDS
        or type(index["schema_version"]) is not int
        or index["schema_version"] != 1
        or index["epoch_id"] != epoch_id
        or _utc(index["decision_at"]) != decision
        or _utc(index["as_of"]) != cutoff
    ):
        raise Stage5CouncilEvidenceError(
            "Evidence index schema/epoch/decision scope mismatch."
        )

    rows = index["records"]
    if not isinstance(rows, list) or len(rows) > MAX_ENTRIES:
        raise Stage5CouncilEvidenceError(
            "Evidence records exceed intake bound."
        )

    records = {}
    total_bytes = 0
    actual = schedule = 0
    versions = {}

    for r in rows:
        if not isinstance(r, dict) or set(r) != RECORD_FIELDS:
            raise Stage5CouncilEvidenceError(
                "Evidence record fields invalid."
            )
        family = r["family"]
        kind = r["source_kind"]
        if (
            not isinstance(family, str)
            or family not in counts
            or not isinstance(kind, str)
            or kind not in KINDS[family]
        ):
            raise Stage5CouncilEvidenceError(
                "Source kind does not match Council family."
            )

        rid = r["record_id"]
        sid = r["source_record_id"]
        issuer = r["claimed_issuer"]
        if any(
            not isinstance(s, str) or not LABEL.fullmatch(s)
            for s in (rid, sid, issuer)
        ):
            raise Stage5CouncilEvidenceError(
                "Evidence record identity invalid."
            )
        if rid in records:
            raise Stage5CouncilEvidenceError(
                "Duplicate evidence record ID."
            )
        if type(r["version"]) is not int or r["version"] < 1:
            raise Stage5CouncilEvidenceError(
                "Source revision version invalid."
            )
        if (
            not isinstance(r["file_sha256"], str)
            or not SHA.fullmatch(r["file_sha256"])
        ):
            raise Stage5CouncilEvidenceError(
                "Evidence SHA-256 invalid."
            )

        prior = r["revision_of"]
        if prior is not None and (
            not isinstance(prior, str)
            or not LABEL.fullmatch(prior)
        ):
            raise Stage5CouncilEvidenceError(
                "Revision pointer invalid."
            )
        deps = r["dependencies"]
        if (
            not isinstance(deps, list)
            or len(deps) > 32
            or any(
                not isinstance(x, str) or not LABEL.fullmatch(x)
                for x in deps
            )
            or len(set(deps)) != len(deps)
            or rid in deps
        ):
            raise Stage5CouncilEvidenceError(
                "Derived lineage dependencies invalid."
            )
        if kind in {"derived_feature", "model_artifact"} and not deps:
            raise Stage5CouncilEvidenceError(
                "Derived/model record requires evidence dependencies."
            )

        published = _utc(r["claimed_published_at"])
        available = _utc(r["claimed_available_at"])
        ingested = _utc(r["claimed_ingested_at"])
        if not published <= available <= ingested <= decision:
            raise Stage5CouncilEvidenceError(
                "Evidence unavailable at decision time."
            )

        economic = r["economic_event_at"]
        if economic is not None:
            economic = _utc(economic)
        if (
            kind in {
                "calendar_actual", "calendar_schedule",
                "policy_rate", "cross_asset", "cot"
            }
            and economic is None
        ):
            raise Stage5CouncilEvidenceError(
                "Economic source needs event/effective timestamp."
            )

        if kind == "calendar_actual":
            if economic > published or economic > decision:
                raise Stage5CouncilEvidenceError(
                    "Future economic actual leaked into decision."
                )
            actual += 1
        elif kind == "calendar_schedule":
            schedule += 1
        elif kind in {"cross_asset", "cot", "market"}:
            if economic is not None and economic > published:
                raise Stage5CouncilEvidenceError(
                    "Observation timestamp later than claimed release."
                )

        raw = _read(
            _within(evidence_root, r["relative_path"]),
            MAX_RECORD,
        )
        total_bytes += len(raw)
        if total_bytes > MAX_TOTAL:
            raise Stage5CouncilEvidenceError(
                "Total evidence bytes exceed intake bound."
            )
        if sha256(raw).hexdigest() != r["file_sha256"]:
            raise Stage5CouncilEvidenceError(
                "Local evidence file SHA-256 mismatch."
            )

        key = (family, sid, r["version"])
        if key in versions:
            raise Stage5CouncilEvidenceError(
                "Duplicate source version in point-in-time index."
            )
        versions[key] = rid
        records[rid] = {
            "family": family,
            "kind": kind,
            "source_id": sid,
            "version": r["version"],
            "previous": prior,
            "dependencies": deps,
            "published": published,
            "available": available,
            "ingested": ingested,
        }
        counts[family] += 1

    revisions = edges = 0
    for rid, r in records.items():
        key = (r["family"], r["source_id"], r["version"] - 1)
        predecessor = versions.get(key)
        if r["version"] > 1 and r["previous"] is None:
            raise Stage5CouncilEvidenceError(
                "Source revision omitted required predecessor link."
            )
        if r["previous"] is not None:
            if predecessor != r["previous"]:
                raise Stage5CouncilEvidenceError(
                    "Revision chain lacks exact preceding source version."
                )
            older = records[predecessor]
            if (
                not older["published"] < r["published"]
                or not older["available"] < r["available"]
            ):
                raise Stage5CouncilEvidenceError(
                    "Later revision lacks later publication and availability."
                )
            revisions += 1
        elif predecessor is not None:
            raise Stage5CouncilEvidenceError(
                "Source revision omitted required predecessor link."
            )

        for dep in r["dependencies"]:
            prior = records.get(dep)
            if prior is None:
                raise Stage5CouncilEvidenceError(
                    "Missing derived/model lineage dependency."
                )
            if (
                not prior["available"] <= r["available"]
                or not prior["ingested"] <= r["ingested"]
            ):
                raise Stage5CouncilEvidenceError(
                    "Derived record depends on later evidence."
                )
            edges += 1

    # Equal timestamp labels cannot rule out a circular derivation.
    visiting, finished = set(), set()

    def visit(rid):
        if rid in visiting:
            raise Stage5CouncilEvidenceError(
                "Cyclic feature/model evidence lineage refused."
            )
        if rid in finished:
            return
        visiting.add(rid)
        for dep in records[rid]["dependencies"]:
            visit(dep)
        visiting.remove(rid)
        finished.add(rid)

    for rid in records:
        visit(rid)

    return {
        "status": "LOCAL_POINT_IN_TIME_CANDIDATES_UNATTESTED",
        "index_sha256": index_sha,
        "records_locally_hash_matched": len(records),
        "families_with_candidates": sum(
            bool(x) for x in counts.values()
        ),
        "family_candidate_counts": counts,
        "release_revisions_linked": revisions,
        "derived_lineage_edges_checked": edges,
        "calendar_actual_records": actual,
        "calendar_schedule_records": schedule,
        "independently_attested_source_families": 0,
        "historical_publication_times_independently_verified": False,
        "model_and_feature_origin_independently_verified": False,
    }


def assess_stage5_council_sources(
    *, contracts_file, manifest_file, epoch_id,
    decision_at, as_of, index_file=None, evidence_root=None
):
    decision, cutoff = _utc(decision_at), _utc(as_of)
    if (
        not isinstance(epoch_id, str)
        or not SHA.fullmatch(epoch_id)
        or decision > cutoff
    ):
        raise Stage5CouncilEvidenceError(
            "Invalid epoch/decision/cutoff."
        )

    static = inspect_council_contracts(
        contracts_file, manifest_file
    )
    local = inspect_point_in_time_index(
        index_file,
        evidence_root=evidence_root,
        epoch_id=epoch_id,
        decision_at=decision.isoformat(),
        as_of=cutoff.isoformat(),
    )

    blockers = [
        "NO_INDEPENDENT_MARKET_MACRO_PUBLICATION_AND_RECEIPT_ATTESTATION",
        "NO_INDEPENDENT_FED_RELEASE_AND_VINTAGE_CERTIFICATION",
        "NO_INDEPENDENT_MODEL_FEATURE_AND_AGENT_INPUT_LINEAGE",
        "NO_RISK_SOURCE_AND_COUNCIL_ADMISSION_APPROVAL",
    ]

    if local["status"] == "NOT_SUPPLIED":
        blockers.append(
            "NO_EXISTING_COUNCIL_EVIDENCE_INDEX_SUPPLIED"
        )

    for family in FAMILIES:
        blockers.append(
            "FAMILY_EXTERNAL_ATTESTATION_MISSING:" + family
        )
        if local["family_candidate_counts"][family] == 0:
            blockers.append(
                "FAMILY_LOCAL_CANDIDATE_NOT_SUPPLIED:" + family
            )

    result = {
        "assessment_version": VERSION,
        "epoch_id": epoch_id,
        "decision_at": decision.isoformat(),
        "as_of": cutoff.isoformat(),
        "static_council_inventory": static,
        "point_in_time_candidate_evidence": local,
        "source_families_independently_verified": 0,
        "calendar_release_times_independently_verified": False,
        "fed_source_admitted_to_live_context": False,
        "derived_model_lineage_independently_verified": False,
        "real_council_research_admission": False,
        "external_evidence_blockers": sorted(blockers),
        "operator_next_actions": [
            "OBTAIN_ORIGINAL_VERSIONED_MARKET_MACRO_AND_COT_SOURCE_RESPONSES",
            "OBTAIN_INDEPENDENT_PUBLICATION_AND_FIRST_RECEIPT_TIMESTAMPS",
            "VERIFY_FED_CANDIDATES_WITH_OFFICIAL_RELEASE_AND_VINTAGE_CUSTODY",
            "FREEZE_FEATURE_MODEL_AND_AGENT_INPUT_DEPENDENCIES_BEFORE_DECISION",
            "REVIEW_RISK_INPUT_SOURCE_AND_PREDECISION_APPROVAL_SEPARATELY",
            "NEVER_BACKDATE_REVISIONS_OR_POPULATE_FUTURE_ACTUAL_VALUES",
        ],
        "source_files_executed": False,
        "files_written": False,
        "database_accessed": False,
        "network_accessed": False,
        "broker_connected": False,
        "collector_started": False,
        "council_invoked": False,
        "training_authorized": False,
        "execution_authorized": False,
        "read_only": True,
        "final_action": "HOLD",
    }
    result["report_sha256"] = _hash(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--read-only-source", action="store_true", required=True
    )
    parser.add_argument(
        "--read-only-evidence", action="store_true", required=True
    )
    parser.add_argument(
        "--contracts-file", default="backend/agents/contracts.py"
    )
    parser.add_argument(
        "--manifest-file", default="backend/agents/council_manifest.py"
    )
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--decision-at", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--index-file")
    parser.add_argument("--evidence-root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if _utc(args.as_of) > datetime.now(UTC):
            raise Stage5CouncilEvidenceError(
                "Future inspection cutoff refused."
            )
        report = assess_stage5_council_sources(
            contracts_file=args.contracts_file,
            manifest_file=args.manifest_file,
            epoch_id=args.epoch_id,
            decision_at=args.decision_at,
            as_of=args.as_of,
            index_file=args.index_file,
            evidence_root=args.evidence_root,
        )
    except (Stage5CouncilEvidenceError, OSError, ValueError) as exc:
        print(
            "STAGE 5 COUNCIL SOURCE REVIEW BLOCKED: "
            + type(exc).__name__
        )
        return 1

    if args.json:
        print(
            json.dumps(
                report, indent=2, sort_keys=True, allow_nan=False
            )
        )
    else:
        print(
            "EURUSD ORACLE | Stage 5 S5-13..16 Council source causality"
        )
        print(
            "Static Council seats: "
            + str(
                report["static_council_inventory"][
                    "static_council_seat_count"
                ]
            )
        )
        print(
            "Static Council source families: "
            + str(
                report["static_council_inventory"][
                    "expected_source_family_count"
                ]
            )
        )
        print(
            "Point-in-time evidence index: "
            + report["point_in_time_candidate_evidence"]["status"]
        )
        print(
            "Locally hash-matched candidates: "
            + str(
                report["point_in_time_candidate_evidence"][
                    "records_locally_hash_matched"
                ]
            )
        )
        print("Independent source families verified: 0/13")
        print("Fed source admission: BLOCKED")
        for item in report["external_evidence_blockers"]:
            print("  BLOCKER " + item)
        print("SHA-256: " + report["report_sha256"])
        print(
            "READ-ONLY | NO COUNCIL | NO TRAINING "
            "| NO BROKER | FINAL=HOLD"
        )
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()

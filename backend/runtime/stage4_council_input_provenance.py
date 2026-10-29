
"""S4-13..16: read-only inventory of the 13 Council input provenance families.

Parses Python sources as *text*, never imports or calls the Council/factory.
An optional existing local evidence index supplies self-declared source metadata,
not independently verified publication time, source origin, or Council admission.
This report always remains research-only HOLD.
"""

import argparse
import ast
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re

VERSION = "EURUSD_STAGE4_COUNCIL_INPUT_PROVENANCE_S4_13_16_V1"
HEX64 = re.compile(r"^[a-f0-9]{64}$")
SOURCE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
SOURCE_LIMIT = 262144
INDEX_LIMIT = 262144

# Explicit requirements, not a claim that these independent proofs exist.
# One concrete source-code path is catalogued for every route. Some routes
# have supplemental artifacts, services, or policies outside these files.
FAMILY_SPECS = (
    ("market_state", "market", ("backend/market_state/market_state_engine.py", "backend/market_state/schemas.py"), "BROKER_SOURCE_AND_CAUSAL_SNAPSHOT_RECEIPT"),
    ("candles", "market", ("backend/market_state/candle_repository.py", "backend/market_state/schemas.py"), "BROKER_BAR_ORIGIN_AND_FIRST_AVAILABILITY"),
    ("ticks", "market", ("backend/market_state/tick_repository.py", "backend/market_state/schemas.py"), "BROKER_TICK_ORIGIN_AND_FIRST_AVAILABILITY"),
    ("cot_data", "macro", ("backend/institutional/cot_repository.py", "backend/institutional/cot_schemas.py"), "CFTC_RELEASE_VERSION_AND_PUBLICATION_TIME"),
    ("ecb_data", "macro", ("backend/macro/macro_repository.py", "backend/macro/ecb_rate_protocol.py"), "ECB_RATE_SOURCE_VERSION_AND_PUBLICATION_TIME"),
    ("fed_data", "macro", ("backend/agents/fed_input_readiness.py", "backend/agents/fed_live_source_admission.py"), "FED_RELEASE_VERSION_AND_ADMITTED_CONTEXT_DELIVERY"),
    ("economic_calendar", "macro", ("backend/macro/macro_repository.py", "backend/macro/fed_calendar_provenance_gaps.py"), "EVENT_VERSION_RELEASE_TIME_AND_AS_OF_VISIBILITY"),
    ("cross_asset_data", "risk", ("backend/macro/cross_asset_client.py", "backend/agents/council_inputs.py"), "FRED_SOURCE_VINTAGE_AND_FIRST_RECEIPT"),
    ("calendar_coverage", "macro", ("backend/agents/council_inputs.py", "backend/macro/fed_calendar_provenance_gaps.py"), "INDEPENDENT_FEED_COMPLETENESS_FOR_COVERED_WINDOW"),
    ("risk_state", "risk", ("backend/agents/council_inputs.py", "backend/agents/implementations/risk_officer_agent.py"), "BROKER_ACCOUNT_RECONCILIATION_AT_DECISION"),
    ("risk_limits", "risk", ("backend/agents/council_inputs.py", "backend/agents/execution_policy.py"), "OPERATOR_SIGNED_VERSIONED_RISK_LIMITS"),
    ("reliability", "governance", ("backend/agents/council_inputs.py", "backend/agents/research_council.py"), "HELDOUT_AGENT_EVALUATIONS_REGISTERED_BEFORE_DECISION"),
    ("synthesis_policy", "governance", ("backend/agents/council_inputs.py", "backend/agents/council_runner.py"), "OPERATOR_APPROVED_VERSIONED_SYNTHESIS_POLICY"),
)

CONTRACT_PATHS = {
    "AgentContext": "backend/agents/contracts.py",
    "CouncilSupplementalInputs": "backend/agents/council_inputs.py",
}

MANIFEST_PATH = "backend/agents/council_manifest.py"
FACTORY_PATH = "backend/agents/context_factory.py"
FED_GUARD_PATH = "backend/agents/fed_input_readiness.py"

CORE_PATHS = tuple(sorted({
    MANIFEST_PATH,
    FACTORY_PATH,
    FED_GUARD_PATH,
    *CONTRACT_PATHS.values(),
}))


class CouncilProvenanceError(ValueError):
    """Reject malformed local inventory or self-declared evidence."""


def _utc(value):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise CouncilProvenanceError(
            "Explicit UTC timestamp required."
        )
    return value.astimezone(timezone.utc)


def _parse_utc(value):
    if not isinstance(value, str):
        raise CouncilProvenanceError(
            "Timestamp must be UTC text."
        )
    try:
        return _utc(
            datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        )
    except (ValueError, TypeError, OverflowError):
        raise CouncilProvenanceError(
            "Invalid UTC timestamp."
        ) from None


def _hashed(value):
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _existing_bytes(file, maximum):
    if file.is_symlink() or not file.is_file():
        raise CouncilProvenanceError(
            "Source is not a regular file."
        )
    try:
        with file.open("rb") as stream:
            content = stream.read(maximum + 1)
    except OSError:
        raise CouncilProvenanceError(
            "Unable to read bounded source."
        ) from None
    if len(content) > maximum:
        raise CouncilProvenanceError(
            "Source exceeds inspection limit."
        )
    return content


def _read_code(repo, name):
    """Fixed relative paths only; never executes imported application code."""
    try:
        data = _existing_bytes(
            Path(repo) / name,
            SOURCE_LIMIT,
        )
        return (
            ast.parse(data, filename=name),
            sha256(data).hexdigest(),
        )
    except (
        OSError,
        UnicodeError,
        SyntaxError,
        CouncilProvenanceError,
    ):
        return None, None


def _class_fields(tree, name):
    if tree is None:
        return set()

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return {
                item.target.id
                for item in node.body
                if (
                    isinstance(item, ast.AnnAssign)
                    and isinstance(item.target, ast.Name)
                )
            }
    return set()


def _manifest_seats(tree):
    if tree is None:
        return [], ["MANIFEST_NOT_PARSEABLE"]

    seats = []
    try:
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Name)
                or node.func.id != "AgentSpec"
            ):
                continue

            fields = {
                k.arg: k.value
                for k in node.keywords
            }
            number = ast.literal_eval(fields["seat_number"])
            agent_id = ast.literal_eval(fields["agent_id"])
            required = ast.literal_eval(
                fields.get(
                    "required_inputs",
                    ast.Tuple(elts=[], ctx=ast.Load()),
                )
            )

            if (
                type(number) is not int
                or not 1 <= number <= 31
                or not isinstance(agent_id, str)
                or not agent_id
                or not isinstance(required, tuple)
                or any(
                    not isinstance(x, str) or not x
                    for x in required
                )
            ):
                raise ValueError("Malformed AgentSpec")

            seats.append({
                "seat": number,
                "agent_id": agent_id,
                "required_inputs": list(required),
            })

    except (ValueError, TypeError, KeyError, SyntaxError):
        return [], ["MANIFEST_NOT_STATICALLY_DECODABLE"]

    seats.sort(key=lambda item: item["seat"])

    if (
        len(seats) != 31
        or [row["seat"] for row in seats] != list(range(1, 32))
        or len({row["agent_id"] for row in seats}) != 31
    ):
        return seats, ["MANIFEST_31_SEATS_NOT_VERIFIED"]

    return seats, []


def _fed_missing_gate_in_code(tree):
    if tree is None:
        return False

    literals = {
        node.value
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
        )
    }

    return {
        "FED_SOURCE_VERSION_ATTESTATION_MISSING",
        "FED_CALENDAR_SOURCE_NOT_VERIFIED",
        "FED_CONTEXT_ACQUISITION_NOT_ACTIVATED",
    }.issubset(literals)


def _factory_guard_in_code(tree):
    if tree is None:
        return False

    return any(
        isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and isinstance(node.exc.func, ast.Name)
        and node.exc.func.id == "FedInputNotReady"
        for node in ast.walk(tree)
    )


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CouncilProvenanceError(
                "Duplicate JSON key."
            )
        result[key] = value
    return result


def inspect_local_index(path, *, as_of, max_records=130):
    """Optional EXISTING, self-declared metadata. Not attestation."""
    at = _utc(as_of)

    if (
        type(max_records) is not int
        or not 1 <= max_records <= 1000
    ):
        raise CouncilProvenanceError(
            "Invalid index record limit."
        )

    if path is None:
        return {
            "status": "NOT_SUPPLIED",
            "sha256": None,
            "claimed_records": {
                name: 0
                for name, *_ in FAMILY_SPECS
            },
            "records_total": 0,
            "independent_records_verified": 0,
        }

    try:
        raw = _existing_bytes(
            Path(path),
            INDEX_LIMIT,
        )
        data = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=lambda _value: (
                _ for _ in ()
            ).throw(
                CouncilProvenanceError(
                    "Non-finite JSON value."
                )
            ),
        )
    except CouncilProvenanceError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise CouncilProvenanceError(
            "Evidence index unreadable or invalid."
        ) from exc

    if (
        not isinstance(data, dict)
        or set(data) != {"version", "as_of", "records"}
        or type(data["version"]) is not int
        or data["version"] != 1
    ):
        raise CouncilProvenanceError(
            "Evidence index header invalid."
        )

    if _parse_utc(data["as_of"]) != at:
        raise CouncilProvenanceError(
            "Evidence index cutoff differs from review."
        )

    records = data["records"]

    if (
        not isinstance(records, list)
        or len(records) > max_records
    ):
        raise CouncilProvenanceError(
            "Evidence index record count invalid."
        )

    families = {
        name
        for name, *_ in FAMILY_SPECS
    }
    counts = {
        name: 0
        for name, *_ in FAMILY_SPECS
    }
    seen = set()

    for row in records:
        if (
            not isinstance(row, dict)
            or set(row) != {
                "family",
                "source_id",
                "content_sha256",
                "claimed_published_at",
                "claimed_received_at",
                "record_sha256",
            }
        ):
            raise CouncilProvenanceError(
                "Evidence record fields invalid."
            )

        family = row["family"]
        source_id = row["source_id"]

        if (
            not isinstance(family, str)
            or family not in families
            or not isinstance(source_id, str)
            or not SOURCE_ID.fullmatch(source_id)
        ):
            raise CouncilProvenanceError(
                "Invalid family/source identifier."
            )

        if (
            not isinstance(row["content_sha256"], str)
            or not HEX64.fullmatch(row["content_sha256"])
        ):
            raise CouncilProvenanceError(
                "Invalid source-content digest."
            )

        if row["record_sha256"] != _hashed({
            k: v
            for k, v in row.items()
            if k != "record_sha256"
        }):
            raise CouncilProvenanceError(
                "Self-declared evidence record hash mismatch."
            )

        if not (
            _parse_utc(row["claimed_published_at"])
            <= _parse_utc(row["claimed_received_at"])
            <= at
        ):
            raise CouncilProvenanceError(
                "Claimed publication/receipt ordering invalid."
            )

        if (family, source_id) in seen:
            raise CouncilProvenanceError(
                "Duplicate family/source evidence."
            )

        seen.add((family, source_id))
        counts[family] += 1

    return {
        "status": "LOCAL_SELF_DECLARATIONS_UNATTESTED",
        "sha256": sha256(raw).hexdigest(),
        "claimed_records": counts,
        "records_total": len(records),
        "independent_records_verified": 0,
    }


def inspect_stage4_council_inputs(
    *,
    repo=".",
    as_of,
    evidence_index=None,
):
    """Pure static source inventory + optional read-only index. No broker/DB/Council."""
    at = _utc(as_of)
    root = Path(repo)

    if not root.is_dir():
        raise CouncilProvenanceError(
            "Repository path unavailable."
        )

    required_paths = sorted(
        {
            path
            for _, _, paths, _ in FAMILY_SPECS
            for path in paths
        } | set(CORE_PATHS)
    )

    source = {
        name: _read_code(root, name)
        for name in required_paths
    }

    context = _class_fields(
        source[CONTRACT_PATHS["AgentContext"]][0],
        "AgentContext",
    )
    supplemental = _class_fields(
        source[CONTRACT_PATHS["CouncilSupplementalInputs"]][0],
        "CouncilSupplementalInputs",
    )

    seats, manifest_issues = _manifest_seats(
        source[MANIFEST_PATH][0]
    )

    seat_inputs = {
        x
        for s in seats
        for x in s["required_inputs"]
    }
    input_fields = context | supplemental

    index = inspect_local_index(
        evidence_index,
        as_of=at,
    )

    fam = []

    for name, group, paths, proof in FAMILY_SPECS:
        files = [
            {
                "path": p,
                "local_source_sha256": source[p][1],
            }
            for p in paths
        ]

        contract = name in context
        source_ok = all(
            source[p][0] is not None
            for p in paths
        )
        count = index["claimed_records"][name]

        fam.append({
            "family": name,
            "group": group,
            "typed_context_field_present": contract,
            "seat_numbers_with_direct_requirement": [
                s["seat"]
                for s in seats
                if name in s["required_inputs"]
            ],
            "source_files": files,
            "local_code_paths_parseable": source_ok,
            "claimed_local_evidence_records": count,
            "local_evidence_status": (
                "CODE_CONTRACT_INCOMPLETE"
                if not contract or not source_ok
                else "LOCAL_DECLARATION_UNATTESTED"
                if count
                else "NO_SOURCE_RECORD_SUPPLIED"
            ),
            "independent_source_origin_verified": False,
            "independent_publication_or_receipt_time_verified": False,
            "admitted_to_real_council": False,
            "independent_evidence_needed": proof,
        })

    static_controls = {
        "fed_readiness_missing_gates_found_in_source":
            _fed_missing_gate_in_code(
                source[FED_GUARD_PATH][0]
            ),
        "production_fed_injection_guard_found_in_factory":
            _factory_guard_in_code(
                source[FACTORY_PATH][0]
            ),
    }

    missing = sorted(
        p
        for p in required_paths
        if source[p][0] is None
    )

    issues = list(manifest_issues)

    if missing:
        issues.append(
            "SOURCE_FILES_MISSING_OR_UNPARSEABLE"
        )

    if any(
        not row["typed_context_field_present"]
        for row in fam
    ):
        issues.append(
            "COUNCIL_CONTEXT_FIELDS_INCOMPLETE"
        )

    if not all(static_controls.values()):
        issues.append(
            "STATIC_FED_GUARD_NOT_FOUND"
        )

    result = {
        "assessment_version": VERSION,
        "as_of": at.isoformat(),
        "scope": "LOCAL_SOURCE_CODE_AND_OPTIONAL_SELF_DECLARED_INDEX_ONLY",
        "manifest_seats_found": len(seats),
        "manifest_31_seats_statically_verified": not manifest_issues,
        "manifest_dependencies_outside_13_families": sorted(
            seat_inputs - {
                n
                for n, *_ in FAMILY_SPECS
            }
        ),
        "typed_context_family_fields_found": sum(
            x["typed_context_field_present"]
            for x in fam
        ),
        "families_total": len(FAMILY_SPECS),
        "families": fam,
        "additional_supplemental_contract_fields": sorted(
            supplemental - {
                n
                for n, *_ in FAMILY_SPECS
            }
        ),
        "static_fed_safeguards": static_controls,
        "local_source_files_missing_or_unparseable": missing,
        "optional_evidence_index": index,
        "local_code_inventory_status": (
            "COMPLETE" if not issues else "INCOMPLETE"
        ),
        "local_code_inventory_issues": sorted(issues),
        "independently_verified_family_count": 0,
        "unverified_families": [
            r["family"]
            for r in fam
        ],
        "required_independent_evidence_actions": [
            {
                "family": r["family"],
                "evidence": r["independent_evidence_needed"],
            }
            for r in fam
        ],
        "source_origin_independently_attested": False,
        "raw_inputs_read_or_council_context_built": False,
        "network_accessed": False,
        "database_accessed": False,
        "files_modified": False,
        "council_invoked": False,
        "council_seats_run": 0,
        "real_forward_council_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
        "read_only": True,
        "final_action": "HOLD",
    }

    result["report_sha256"] = _hashed(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--read-only-sources",
        action="store_true",
        required=True,
    )
    parser.add_argument(
        "--read-only-evidence",
        action="store_true",
        required=True,
    )
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--evidence-index")
    parser.add_argument(
        "--json",
        action="store_true",
    )

    args = parser.parse_args(argv)

    try:
        at = _parse_utc(args.as_of)

        if at > datetime.now(timezone.utc):
            raise CouncilProvenanceError(
                "Future inspection time refused."
            )

        report = inspect_stage4_council_inputs(
            repo=args.repo,
            as_of=at,
            evidence_index=args.evidence_index,
        )

    except (CouncilProvenanceError, OSError, ValueError) as exc:
        print(
            "STAGE 4 COUNCIL INPUT REVIEW BLOCKED: "
            + type(exc).__name__
        )
        return 1

    if args.json:
        print(
            json.dumps(
                report,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
        )
    else:
        print(
            "EURUSD ORACLE | Stage 4 S4-13..16 Council input provenance"
        )
        print(
            "31-seat manifest statically verified:",
            report["manifest_31_seats_statically_verified"],
        )
        print(
            "Typed family fields:",
            f'{report["typed_context_family_fields_found"]}/13',
        )
        print(
            "Local code inventory:",
            report["local_code_inventory_status"],
        )
        print(
            "Self-declared records:",
            report["optional_evidence_index"]["records_total"],
        )
        print("Independent families verified: 0/13")

        for row in report["families"]:
            print(
                "  FAMILY",
                row["family"],
                "|",
                row["local_evidence_status"],
                "| independent=NO",
            )

        for issue in report["local_code_inventory_issues"]:
            print("  LOCAL BLOCKER", issue)

        print(
            "Additional manifest inputs:",
            ", ".join(
                report["manifest_dependencies_outside_13_families"]
            ),
        )
        print(
            "SHA-256:",
            report["report_sha256"],
        )
        print(
            "READ-ONLY | NO CONTEXT BUILD | NO COUNCIL "
            "| NO TRAINING | FINAL=HOLD"
        )

    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()

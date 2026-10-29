
"""P3-17..20: read-only prospective evaluation evidence contract.

This inspects optional EXISTING records. Self-hashes and self-declared times do
not independently prove prospective registration, broker outcomes, execution
costs, or a frozen held-out split. No trading or model evaluation is run.
"""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re

from backend.data_engine.forward_store import hashed
from backend.runtime.forward_market_input_lineage import inspect_market_input_lineage

VERSION = "EURUSD_P3_PROSPECTIVE_EVALUATION_V1"
HEX64 = re.compile(r"^[a-f0-9]{64}$")
ZERO_HASH = "0" * 64
MAX_BYTES = 1048576


class ProspectiveEvidenceError(RuntimeError):
    """Reject malformed, inconsistent or misrepresented research evidence."""


def _timestamp(value):
    try:
        if not isinstance(value, str):
            raise ValueError()
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
            raise ValueError()
        return stamp.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise ProspectiveEvidenceError("Explicit UTC timestamp required.") from None


def _hash_value(value):
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _hashed_record(record):
    if not isinstance(record, dict) or not _hash_value(record.get("record_sha256")):
        raise ProspectiveEvidenceError("Missing record SHA-256.")
    if record["record_sha256"] != hashed({
        k: v for k, v in record.items() if k != "record_sha256"
    }):
        raise ProspectiveEvidenceError("Self-hashed record integrity mismatch.")


def _keys(record, required):
    if not isinstance(record, dict) or set(record) != set(required):
        raise ProspectiveEvidenceError("Evidence record fields differ from contract.")
    _hashed_record(record)


def _positive(value):
    return type(value) in (float, int) and math.isfinite(value) and value > 0


def _nonnegative(value):
    return type(value) in (float, int) and math.isfinite(value) and value >= 0


def _safe_json(path):
    if path is None:
        return None, None
    file = Path(path)
    try:
        if file.is_symlink():
            raise ValueError("Symlinked evidence is not accepted.")
        with file.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Evidence file too large.")

        def distinct(pairs):
            record = {}
            for k, v in pairs:
                if k in record:
                    raise ValueError("Duplicate JSON keys.")
                record[k] = v
            return record

        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=distinct,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ValueError("Non-finite JSON.")
            ),
        )
    except (OSError, ValueError, UnicodeError) as exc:
        raise ProspectiveEvidenceError(
            "Evidence file unreadable or invalid."
        ) from exc
    return decoded, sha256(raw).hexdigest()


def inspect_evaluation_contract(lineage, *, bundle=None, file_sha256=None,
                                max_records=2000):
    """Pure validator. Does not create or insert prediction/outcome records."""
    if type(max_records) is not int or not 1 <= max_records <= 5000:
        raise ProspectiveEvidenceError("max_records must be 1..5000.")

    if (
        not isinstance(lineage, dict)
        or lineage.get("assessment_version")
        != "EURUSD_P3_MARKET_INPUT_LINEAGE_V1"
    ):
        raise ProspectiveEvidenceError("Expected market-input lineage V1.")

    digest = lineage.get("report_sha256")
    if not _hash_value(digest) or digest != hashed({
        k: v for k, v in lineage.items() if k != "report_sha256"
    }):
        raise ProspectiveEvidenceError(
            "Source lineage report integrity mismatch."
        )

    for name in (
        "real_forward_council_admission",
        "training_authorized",
        "execution_authorized",
        "prospective_prediction_registered",
        "historical_utc_certified",
        "council_invoked",
    ):
        if lineage.get(name) is not False:
            raise ProspectiveEvidenceError(
                "Source lineage safety contract violated."
            )

    if (
        lineage.get("read_only") is not True
        or lineage.get("final_action") != "HOLD"
        or lineage.get("council_seats_run") != 0
    ):
        raise ProspectiveEvidenceError(
            "Source lineage is not HOLD/read-only."
        )

    decision = _timestamp(lineage["decision_at"])
    cutoff = _timestamp(lineage["as_of"])
    if decision > cutoff or not _hash_value(lineage.get("epoch_id")):
        raise ProspectiveEvidenceError(
            "Source decision/epoch is invalid."
        )

    if bundle is None:
        regs, outcomes, costs, split = [], [], [], None
        if file_sha256 is not None:
            raise ProspectiveEvidenceError(
                "File hash without evidence bundle."
            )
    else:
        if not _hash_value(file_sha256):
            raise ProspectiveEvidenceError(
                "File digest must accompany evidence bundle."
            )
        if (
            not isinstance(bundle, dict)
            or set(bundle) != {
                "version", "epoch_id", "registrations",
                "outcomes", "costs", "holdout",
            }
            or type(bundle["version"]) is not int
            or bundle["version"] != 1
            or bundle["epoch_id"] != lineage["epoch_id"]
        ):
            raise ProspectiveEvidenceError(
                "Evidence bundle header mismatch."
            )
        regs, outcomes, costs, split = (
            bundle[x] for x in (
                "registrations", "outcomes", "costs", "holdout"
            )
        )
        if any(
            not isinstance(x, list) or len(x) > max_records
            for x in (regs, outcomes, costs)
        ):
            raise ProspectiveEvidenceError(
                "Evidence arrays invalid or oversized."
            )

    sources = lineage.get("first_owned_receipts")
    if not isinstance(sources, dict):
        raise ProspectiveEvidenceError(
            "Source receipt references missing."
        )

    tick = sources.get("latest_tick")
    bar = sources.get("latest_closed_m5")

    prior = ZERO_HASH
    last_registration_time = None
    registration_map = {}

    for row in regs:
        _keys(row, (
            "epoch_id", "decision_at", "registered_at",
            "source_report_sha256", "tick_source_id",
            "bar_source_id", "direction", "horizon_seconds",
            "previous_sha256", "record_sha256",
        ))

        registered_at = _timestamp(row["registered_at"])
        if (
            row["epoch_id"] != lineage["epoch_id"]
            or _timestamp(row["decision_at"]) != decision
            or row["source_report_sha256"] != digest
            or not isinstance(tick, dict)
            or not isinstance(bar, dict)
            or row["tick_source_id"] != tick.get("source_id")
            or row["bar_source_id"] != bar.get("source_id")
            or row["direction"] not in ("UP", "DOWN", "FLAT")
            or type(row["horizon_seconds"]) is not int
            or not 60 <= row["horizon_seconds"] <= 86400
            or row["previous_sha256"] != prior
            or registered_at > decision
            or registered_at <= _timestamp(
                tick["first_local_availability"]
            )
            or registered_at <= _timestamp(
                bar["first_local_availability"]
            )
            or (
                last_registration_time is not None
                and registered_at < last_registration_time
            )
        ):
            raise ProspectiveEvidenceError(
                "Registration causal contract invalid."
            )

        if row["record_sha256"] in registration_map:
            raise ProspectiveEvidenceError(
                "Duplicate registration digest."
            )

        registration_map[row["record_sha256"]] = row
        prior = row["record_sha256"]
        last_registration_time = registered_at

    outcome_map = {}

    for row in outcomes:
        _keys(row, (
            "registration_sha256", "horizon_end",
            "observed_at", "source_first_available_at",
            "bid", "ask", "source_capture_id", "record_sha256",
        ))

        rid = row["registration_sha256"]
        if rid not in registration_map or rid in outcome_map:
            raise ProspectiveEvidenceError(
                "Outcome missing unique registration."
            )

        prediction = registration_map[rid]
        horizon = decision + timedelta(
            seconds=prediction["horizon_seconds"]
        )
        availability = _timestamp(
            row["source_first_available_at"]
        )

        if (
            _timestamp(row["horizon_end"]) != horizon
            or not horizon <= availability <= _timestamp(
                row["observed_at"]
            ) <= cutoff
            or not _positive(row["bid"])
            or not _positive(row["ask"])
            or row["bid"] > row["ask"]
            or not _hash_value(row["source_capture_id"])
        ):
            raise ProspectiveEvidenceError(
                "Outcome horizon or source contract invalid."
            )

        outcome_map[rid] = row

    cost_map = {}

    for row in costs:
        _keys(row, (
            "registration_sha256", "observed_at",
            "entry_bid", "entry_ask", "exit_bid", "exit_ask",
            "commission_round_trip_pips", "slippage_pips",
            "financing_pips", "broker_fill_reference",
            "record_sha256",
        ))

        rid = row["registration_sha256"]
        if rid not in outcome_map or rid in cost_map:
            raise ProspectiveEvidenceError(
                "Costs require uniquely matched outcome."
            )

        horizon = decision + timedelta(
            seconds=registration_map[rid]["horizon_seconds"]
        )

        if (
            _timestamp(row["observed_at"]) < horizon
            or _timestamp(row["observed_at"]) > cutoff
            or any(
                not _positive(row[x])
                for x in (
                    "entry_bid", "entry_ask",
                    "exit_bid", "exit_ask",
                )
            )
            or row["entry_bid"] > row["entry_ask"]
            or row["exit_bid"] > row["exit_ask"]
            or any(
                not _nonnegative(row[x])
                for x in (
                    "commission_round_trip_pips",
                    "slippage_pips", "financing_pips",
                )
            )
            or not isinstance(
                row["broker_fill_reference"], str
            )
            or not 1 <= len(
                row["broker_fill_reference"]
            ) <= 128
        ):
            raise ProspectiveEvidenceError(
                "Cost evidence contract invalid."
            )

        cost_map[rid] = row

    split_status = "NOT_SUPPLIED"

    if split is not None:
        if (
            not isinstance(split, dict)
            or set(split) != {
                "frozen_at", "evaluation_start",
                "evaluation_end", "split_sha256",
            }
            or not _hash_value(split.get("split_sha256"))
        ):
            raise ProspectiveEvidenceError(
                "Held-out split contract invalid."
            )

        freeze = _timestamp(split["frozen_at"])
        start = _timestamp(split["evaluation_start"])
        end = _timestamp(split["evaluation_end"])

        if not freeze < start <= decision < end <= cutoff:
            raise ProspectiveEvidenceError(
                "Held-out freeze/decision ordering invalid."
            )

        if split["split_sha256"] != hashed({
            k: v for k, v in split.items()
            if k != "split_sha256"
        }):
            raise ProspectiveEvidenceError(
                "Held-out split content hash mismatch."
            )

        split_status = "LOCAL_SPLIT_DECLARATION_UNATTESTED"

    local_issues = set(lineage.get("local_blockers", ()))

    if lineage.get("locally_derived_market_values") is None:
        local_issues.add("LOCAL_MARKET_VALUES_WITHHELD")
    if not regs:
        local_issues.add("NO_REGISTRATIONS_IN_SUPPLIED_SCOPE")
    if not outcomes:
        local_issues.add(
            "NO_POST_HORIZON_OUTCOMES_IN_SUPPLIED_SCOPE"
        )
    if not costs:
        local_issues.add(
            "NO_MATCHED_ALL_IN_COST_RECORDS_IN_SUPPLIED_SCOPE"
        )
    if split is None:
        local_issues.add(
            "NO_HELDOUT_SPLIT_IN_SUPPLIED_SCOPE"
        )

    result = {
        "assessment_version": VERSION,
        "epoch_id": lineage["epoch_id"],
        "decision_at": decision.isoformat(),
        "as_of": cutoff.isoformat(),
        "market_lineage_sha256": digest,
        "evidence_file_sha256": file_sha256,
        "evidence_scope": (
            "NONE_SUPPLIED" if bundle is None
            else "LOCAL_SELF_HASHED_BUNDLE_UNATTESTED"
        ),
        "registration_contract":
            "HASH_CHAIN_AND_LOCAL_TIMESTAMP_ORDER_ONLY",
        "registration_count_in_scope": len(regs),
        "post_horizon_outcomes_in_scope": len(outcome_map),
        "cost_records_in_scope": len(cost_map),
        "fully_linked_triplets_in_scope": len(cost_map),
        "heldout_local_declaration": split_status,
        "external_timestamp_anchor_verified": False,
        "independent_prediction_registration_attested": False,
        "independent_outcome_source_attested": False,
        "broker_fill_and_costs_attested": False,
        "heldout_split_independently_frozen": False,
        "performance_measured": False,
        "transaction_costs_measured": False,
        "out_of_sample_trades_measured": None,
        "independent_evidence_gaps": [
            "NO_VERIFIED_EXTERNAL_PREDICTION_TIMESTAMP_ANCHOR",
            "NO_ATTESTED_POST_HORIZON_OUTCOME_SOURCE",
            "NO_BROKER_CONFIRMED_BID_ASK_COMMISSION_SLIPPAGE_FILLS",
            "NO_INDEPENDENTLY_FROZEN_HELDOUT_SPLIT",
        ],
        "local_evidence_blockers": sorted(local_issues),
        "read_only": True,
        "files_created": False,
        "database_modified": False,
        "prospective_prediction_registered_by_inspector": False,
        "real_forward_council_admission": False,
        "council_invoked": False,
        "council_seats_run": 0,
        "training_authorized": False,
        "execution_authorized": False,
        "final_action": "HOLD",
    }

    result["report_sha256"] = hashed(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--read-only-db", action="store_true", required=True
    )
    parser.add_argument(
        "--read-only-evidence", action="store_true", required=True
    )
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--decision-at", required=True)
    parser.add_argument("--policy-file")
    parser.add_argument("--expected-policy-sha256")
    parser.add_argument("--evidence-file")
    parser.add_argument("--captures", type=int, default=32)
    parser.add_argument("--source-rows", type=int, default=5000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if not _hash_value(args.epoch_id):
            raise ProspectiveEvidenceError("Invalid epoch ID.")

        if (
            args.expected_policy_sha256 is not None
            and not _hash_value(args.expected_policy_sha256)
        ):
            raise ProspectiveEvidenceError(
                "Invalid expected policy SHA-256."
            )

        cutoff = _timestamp(args.as_of)
        decision = _timestamp(args.decision_at)

        if (
            decision > cutoff
            or cutoff > datetime.now(timezone.utc)
        ):
            raise ProspectiveEvidenceError(
                "Decision/cutoff is in future."
            )

        if (
            type(args.captures) is not int
            or not 1 <= args.captures <= 100
        ):
            raise ProspectiveEvidenceError(
                "Invalid capture limit."
            )

        if (
            type(args.source_rows) is not int
            or not 1 <= args.source_rows <= 5000
        ):
            raise ProspectiveEvidenceError(
                "Invalid source limit."
            )

        bundle, file_digest = _safe_json(
            args.evidence_file
        )

        from backend.database.connection import engine

        try:
            lineage = inspect_market_input_lineage(
                engine,
                epoch_id=args.epoch_id,
                as_of=cutoff,
                decision_at=decision,
                policy_file=args.policy_file,
                expected_policy_sha256=args.expected_policy_sha256,
                max_captures=args.captures,
                max_rows=args.source_rows,
            )
        finally:
            engine.dispose()

        report = inspect_evaluation_contract(
            lineage,
            bundle=bundle,
            file_sha256=file_digest,
        )

    except Exception as exc:
        print(
            "PROSPECTIVE EVALUATION BLOCKED: "
            + type(exc).__name__
        )
        return 1

    if args.json:
        print(json.dumps(
            report, indent=2, sort_keys=True,
            allow_nan=False,
        ))
    else:
        print(
            "EURUSD ORACLE | P3-17..20 prospective evaluation contract"
        )
        print(
            "Decision: " + report["decision_at"]
            + " | cutoff=" + report["as_of"]
        )
        print(
            "Evidence: " + report["evidence_scope"]
        )
        print(
            "Registration/outcome/cost records: %s/%s/%s" % (
                report["registration_count_in_scope"],
                report["post_horizon_outcomes_in_scope"],
                report["cost_records_in_scope"],
            )
        )
        print(
            "Independent prospective attestation: NOT ESTABLISHED"
        )
        print(
            "Measured predictive performance: NOT MEASURED"
        )
        print(
            "Measured transaction costs: NOT MEASURED"
        )

        for reason in report["local_evidence_blockers"]:
            print("  BLOCKER " + reason)

        print("SHA-256: " + report["report_sha256"])
        print(
            "READ-ONLY | NO SIGNAL REGISTRATION | NO COUNCIL | FINAL=HOLD"
        )

    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()

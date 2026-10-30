
"""P6-17..20: deterministic, offline-only ORACLE research evaluation design.

Reads Council contract source statically and, optionally, pre-existing locally
claimed study/observation files. Calculates *exploratory hypothetical* statistics
only when a predeclared candidate protocol and complete local sample are present.
A hash or self-declared receipt NEVER proves independent registration, custody,
execution costs, held-out performance, profitability, or broker provenance.
No imports of agents, MT5, SQL, training, settlement, or trading services.
"""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from hashlib import sha256
import json
from math import sqrt
from pathlib import Path, PurePosixPath
import re

from backend.runtime.stage6_council_context_readiness import (
    CouncilContextError, inspect_static_council,
)

VERSION = "EURUSD_STAGE6_RESEARCH_EVALUATION_P6_17_20_V1"
EPOCH = "75311e239cd5c295d3e08e03c358488fb123a46bfc6d342dd31493d95710aa33"
BENCHMARKS = ("NO_TRADE", "ALWAYS_LONG", "ALWAYS_SHORT", "MOMENTUM_SIGN")
REGIMES = ("ASIA", "LONDON", "NEW_YORK", "OTHER")
DIRECTIONS = ("LONG", "SHORT", "HOLD")
VOTE_DIRECTIONS = ("LONG", "SHORT", "ABSTAIN")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
NUM = re.compile(r"^-?(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,8})?$")
PROTOCOL_FIELDS = {
    "schema_version", "epoch_id", "experiment_id", "council_manifest_sha256",
    "claimed_freeze_at", "claimed_freeze_receipt_at", "evaluation_start_at",
    "evaluation_end_at", "holdout_partition_sha256", "evaluation_plan_sha256",
    "horizon_seconds", "min_samples", "neutral_band_pips",
    "assumed_roundtrip_cost_pips", "stress_extra_cost_pips", "benchmarks", "regimes",
}
INDEX_FIELDS = {"schema_version", "epoch_id", "experiment_id", "as_of", "protocol_sha256", "entries"}
INDEX_ENTRY_FIELDS = {"record_id", "relative_path", "file_sha256"}
SAMPLE_FIELDS = {
    "record_id", "epoch_id", "experiment_id", "prediction_id", "decision_at",
    "claimed_registered_at", "claimed_registration_receipt_at", "horizon_end_at",
    "entry_quote_at", "entry_quote_available_at", "entry_mid", "outcome_quote_at",
    "outcome_quote_available_at", "claimed_outcome_receipt_at", "outcome_mid",
    "prior_signal_available_at", "prior_move_pips", "regime", "consensus_direction",
    "consensus_probability_up", "primary_votes", "devils_advocate_challenge",
    "risk_veto", "final_research_action", "claimed_registration_receipt_sha256",
    "claimed_outcome_receipt_sha256", "claimed_cost_basis_sha256",
    "research_only_no_orders",
}
VOTE_FIELDS = {"seat", "agent_id", "direction", "confidence"}
MAX_SOURCE = 1048576
MAX_PROTOCOL = 65536
MAX_INDEX = 131072
MAX_PAYLOAD = 131072
MAX_ENTRIES = 256
MAX_TOTAL = 8388608
MONEY_UNIT = Decimal("0.0001")
PIP = Decimal("10000")
METRIC_UNIT = Decimal("0.000001")
UTC = timezone.utc


class ResearchEvaluationError(ValueError):
    """Invalid local study, unsafe source or impossible evaluation chronology."""


def _utc(value):
    if not isinstance(value, str):
        raise ResearchEvaluationError("Explicit UTC timestamp required.")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise ResearchEvaluationError("Invalid UTC timestamp.") from None
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise ResearchEvaluationError("Zero-offset UTC timestamp required.")
    return dt.astimezone(UTC)


def _digest(obj):
    return sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")).hexdigest()


def _unique(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ResearchEvaluationError("Duplicate JSON field.")
        result[k] = v
    return result


def _json(raw):
    try:
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                         parse_constant=lambda _: (_ for _ in ()).throw(
                             ResearchEvaluationError("Nonfinite JSON value.")))
    except ResearchEvaluationError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise ResearchEvaluationError("Invalid UTF-8 JSON object.") from None
    if not isinstance(obj, dict):
        raise ResearchEvaluationError("JSON must be an object.")
    return obj


def _read(path, maximum):
    file = Path(path)
    if file.is_symlink() or not file.is_file():
        raise ResearchEvaluationError("Existing regular file required.")
    try:
        with file.open("rb") as handle:
            data = handle.read(maximum + 1)
    except OSError:
        raise ResearchEvaluationError("File cannot be read.") from None
    if len(data) > maximum:
        raise ResearchEvaluationError("File size exceeds bounded limit.")
    return data


def _safe_child(root, relative):
    if (not isinstance(relative, str) or not 1 <= len(relative) <= 240
            or "\\" in relative or ":" in relative or "\x00" in relative
            or PurePosixPath(relative).is_absolute()
            or any(part in ("", ".", "..") for part in relative.split("/"))):
        raise ResearchEvaluationError("Unsafe relative path.")
    path = Path(root)
    if not path.is_dir() or path.is_symlink():
        raise ResearchEvaluationError("Existing nonsymlinked evidence root required.")
    for part in relative.split("/"):
        path = path / part
        if path.is_symlink():
            raise ResearchEvaluationError("Symlinked evidence path refused.")
    return path


def _id(value, label):
    if not isinstance(value, str) or not LABEL.fullmatch(value):
        raise ResearchEvaluationError("Invalid identifier: " + label)
    return value


def _sha(value, label):
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise ResearchEvaluationError("Invalid SHA-256: " + label)
    return value


def _decimal(value, label, *, positive=False, signed=False):
    if not isinstance(value, str) or not NUM.fullmatch(value):
        raise ResearchEvaluationError("Invalid decimal string: " + label)
    try:
        d = Decimal(value)
    except InvalidOperation:
        raise ResearchEvaluationError("Invalid decimal string: " + label) from None
    if not d.is_finite() or (positive and d <= 0) or (not signed and d < 0):
        raise ResearchEvaluationError("Out-of-range decimal: " + label)
    return d


def _metric(value):
    """Deterministic output precision (local, not performance certification)."""
    if value is None:
        return None
    return str(Decimal(str(value)).quantize(METRIC_UNIT, rounding=ROUND_HALF_EVEN))


def _protocol(path, static, cutoff):
    raw = _read(path, MAX_PROTOCOL)
    doc = _json(raw)
    if (set(doc) != PROTOCOL_FIELDS or type(doc.get("schema_version")) is not int
            or doc["schema_version"] != 1 or doc["epoch_id"] != EPOCH):
        raise ResearchEvaluationError("Study protocol schema or epoch mismatch.")
    _id(doc["experiment_id"], "experiment_id")
    if _sha(doc["council_manifest_sha256"], "council_manifest_sha256") != static["manifest_sha256"]:
        raise ResearchEvaluationError("Council manifest differs from predeclared study.")
    _sha(doc["holdout_partition_sha256"], "holdout_partition_sha256")
    _sha(doc["evaluation_plan_sha256"], "evaluation_plan_sha256")
    freeze = _utc(doc["claimed_freeze_at"])
    receipt = _utc(doc["claimed_freeze_receipt_at"])
    start = _utc(doc["evaluation_start_at"])
    end = _utc(doc["evaluation_end_at"])
    if not freeze <= receipt < start < end:
        raise ResearchEvaluationError("Study freeze/receipt/window chronology invalid.")
    if freeze > cutoff or receipt > cutoff:
        raise ResearchEvaluationError("Study protocol claims future freeze receipt.")
    if (type(doc["horizon_seconds"]) is not int
            or not 60 <= doc["horizon_seconds"] <= 86400
            or type(doc["min_samples"]) is not int
            or not 3 <= doc["min_samples"] <= MAX_ENTRIES):
        raise ResearchEvaluationError("Study horizon or minimum sample count invalid.")
    _decimal(doc["neutral_band_pips"], "neutral_band_pips")
    _decimal(doc["assumed_roundtrip_cost_pips"], "assumed_roundtrip_cost_pips")
    if _decimal(doc["neutral_band_pips"], "neutral_band_pips") > 20:
        raise ResearchEvaluationError("Neutral band exceeds bounded limit.")
    if _decimal(doc["assumed_roundtrip_cost_pips"], "assumed_roundtrip_cost_pips") > 100:
        raise ResearchEvaluationError("Cost assumption exceeds bounded limit.")
    stress = doc["stress_extra_cost_pips"]
    if (not isinstance(stress, list) or not 1 <= len(stress) <= 6
            or len(set(map(str, stress))) != len(stress)):
        raise ResearchEvaluationError("Study cost-stress grid invalid.")
    if any(_decimal(x, "stress_extra_cost_pips") > 100 for x in stress):
        raise ResearchEvaluationError("Study cost stress exceeds bounded limit.")
    if (doc["benchmarks"] != list(BENCHMARKS)
            or doc["regimes"] != list(REGIMES)):
        raise ResearchEvaluationError("Study benchmarks/regimes must be predeclared exactly.")
    return {"doc": doc, "sha256": sha256(raw).hexdigest(),
            "start": start, "end": end, "freeze_claim_received_at": receipt}


def _candidate_index(index_file, root, protocol, static, *, cutoff):
    raw = _read(index_file, MAX_INDEX)
    obj = _json(raw)
    if (set(obj) != INDEX_FIELDS or type(obj.get("schema_version")) is not int
            or obj["schema_version"] != 1 or obj["epoch_id"] != EPOCH
            or obj["experiment_id"] != protocol["doc"]["experiment_id"]
            or obj["protocol_sha256"] != protocol["sha256"]
            or _utc(obj["as_of"]) != cutoff):
        raise ResearchEvaluationError("Evaluation index/protocol/cutoff mismatch.")
    entries = obj["entries"]
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ResearchEvaluationError("Evaluation index size invalid.")
    seen_id = set()
    seen_predictions = set()
    samples = []
    total = 0
    seats = [(s["seat"], s["agent_id"]) for s in static["seats"] if s["stage"] == "PRIMARY"]
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != INDEX_ENTRY_FIELDS:
            raise ResearchEvaluationError("Evaluation index entry schema invalid.")
        rid = _id(entry["record_id"], "record_id")
        if rid in seen_id:
            raise ResearchEvaluationError("Duplicate evaluation record ID.")
        digest = _sha(entry["file_sha256"], "file_sha256")
        payload = _read(_safe_child(root, entry["relative_path"]), MAX_PAYLOAD)
        total += len(payload)
        if total > MAX_TOTAL:
            raise ResearchEvaluationError("Evaluation total evidence size limit exceeded.")
        if sha256(payload).hexdigest() != digest:
            raise ResearchEvaluationError("Evaluation sample SHA-256 mismatch.")
        sample = _json(payload)
        if (set(sample) != SAMPLE_FIELDS or sample["record_id"] != rid
                or sample["epoch_id"] != EPOCH
                or sample["experiment_id"] != protocol["doc"]["experiment_id"]):
            raise ResearchEvaluationError("Evaluation sample schema or scope mismatch.")
        pid = _sha(sample["prediction_id"], "prediction_id")
        if pid in seen_predictions:
            raise ResearchEvaluationError("Duplicate prediction in evaluation sample.")
        _check_sample(sample, protocol, seats, cutoff)
        samples.append(sample)
        seen_id.add(rid)
        seen_predictions.add(pid)
    decisions = [_utc(s["decision_at"]) for s in samples]
    if decisions != sorted(decisions) or len(set(decisions)) != len(decisions):
        raise ResearchEvaluationError("Sample decisions out of order or duplicated.")
    return {"index_sha256": sha256(raw).hexdigest(), "records": samples}


def _check_sample(row, protocol, seats, cutoff):
    decision = _utc(row["decision_at"])
    frozen = protocol["doc"]
    if not protocol["start"] <= decision < protocol["end"]:
        raise ResearchEvaluationError("Decision outside predeclared evaluation window.")
    receipt = _utc(row["claimed_registration_receipt_at"])
    registered = _utc(row["claimed_registered_at"])
    if not protocol["freeze_claim_received_at"] < registered <= receipt < decision:
        raise ResearchEvaluationError("Prediction not claimed registered before decision/freeze.")
    horizon = decision + timedelta(seconds=frozen["horizon_seconds"])
    if _utc(row["horizon_end_at"]) != horizon:
        raise ResearchEvaluationError("Prediction horizon differs from frozen protocol.")
    entry_quote = _utc(row["entry_quote_at"])
    entry_available = _utc(row["entry_quote_available_at"])
    prior_available = _utc(row["prior_signal_available_at"])
    if not entry_quote <= entry_available <= decision or not prior_available <= decision:
        raise ResearchEvaluationError("Entry quote or momentum baseline used future information.")
    outcome_quote = _utc(row["outcome_quote_at"])
    outcome_available = _utc(row["outcome_quote_available_at"])
    outcome_receipt = _utc(row["claimed_outcome_receipt_at"])
    if not horizon <= outcome_quote <= outcome_available <= outcome_receipt <= cutoff:
        raise ResearchEvaluationError("Outcome horizon/availability/receipt chronology invalid.")
    for name in ("claimed_registration_receipt_sha256", "claimed_outcome_receipt_sha256",
                 "claimed_cost_basis_sha256"):
        _sha(row[name], name)
    entry = _decimal(row["entry_mid"], "entry_mid", positive=True)
    exit_price = _decimal(row["outcome_mid"], "outcome_mid", positive=True)
    if not Decimal("0.2") <= entry <= 10 or not Decimal("0.2") <= exit_price <= 10:
        raise ResearchEvaluationError("EURUSD midprice outside conservative sanity range.")
    _decimal(row["prior_move_pips"], "prior_move_pips", signed=True)
    prob = _decimal(row["consensus_probability_up"], "consensus_probability_up")
    if prob > 1:
        raise ResearchEvaluationError("Consensus probability outside zero-one range.")
    if row["regime"] not in REGIMES or row["consensus_direction"] not in DIRECTIONS:
        raise ResearchEvaluationError("Unrecognized regime or direction.")
    if (type(row["risk_veto"]) is not bool
            or type(row["devils_advocate_challenge"]) is not bool
            or type(row["research_only_no_orders"]) is not bool
            or row["research_only_no_orders"] is not True):
        raise ResearchEvaluationError("Research-only governance flags invalid.")
    if row["final_research_action"] not in DIRECTIONS:
        raise ResearchEvaluationError("Invalid final research action.")
    if row["risk_veto"] and row["final_research_action"] != "HOLD":
        raise ResearchEvaluationError("Risk veto must force HOLD.")
    if (row["final_research_action"] != "HOLD"
            and row["final_research_action"] != row["consensus_direction"]):
        raise ResearchEvaluationError("Strategist direction conflicts with consensus.")
    votes = row["primary_votes"]
    if not isinstance(votes, list) or len(votes) != len(seats):
        raise ResearchEvaluationError("Exactly 28 declared primary votes required.")
    for vote, (number, agent) in zip(votes, seats):
        if (not isinstance(vote, dict) or set(vote) != VOTE_FIELDS
                or type(vote["seat"]) is not int or vote["seat"] != number
                or vote["agent_id"] != agent or vote["direction"] not in VOTE_DIRECTIONS):
            raise ResearchEvaluationError("Primary seat vote identity/order/fields mismatch.")
        if _decimal(vote["confidence"], "vote confidence") > 1:
            raise ResearchEvaluationError("Primary vote confidence outside zero-one range.")


def _wilson_95(wins, total):
    """Exploratory binomial interval, not a holdout-valid uncertainty claim."""
    if not total:
        return None
    z = 1.96
    fraction = wins / total
    denom = 1 + z * z / total
    center = (fraction + z * z / (2 * total)) / denom
    radius = (z / denom) * sqrt(fraction * (1 - fraction) / total
                                + z * z / (4 * total * total))
    return {"lower": _metric(max(0, center - radius)),
            "upper": _metric(min(1, center + radius)),
            "interval_assumption": "iid_binomial_EXPLORATORY_ONLY"}


def _directions(row):
    prev = _decimal(row["prior_move_pips"], "prior_move_pips", signed=True)
    momentum = "LONG" if prev > 0 else "SHORT" if prev < 0 else "HOLD"
    return {
        "COUNCIL_FINAL": row["final_research_action"],
        "NO_TRADE": "HOLD", "ALWAYS_LONG": "LONG",
        "ALWAYS_SHORT": "SHORT", "MOMENTUM_SIGN": momentum,
    }


def _score(samples, protocol):
    """Descriptive local simulations only, never certified returns or P&L."""
    p = protocol["doc"]
    threshold = _decimal(p["neutral_band_pips"], "neutral_band_pips")
    cost = _decimal(p["assumed_roundtrip_cost_pips"], "assumed_roundtrip_cost_pips")
    stress = [_decimal(x, "stress_extra_cost_pips") for x in p["stress_extra_cost_pips"]]
    by_strategy = {name: [] for name in ("COUNCIL_FINAL", *BENCHMARKS)}
    disagreement = []
    votes_by_seat = [[] for _ in range(28)]
    brier = []
    calibration = [{"count": 0, "sum_pred": Decimal(0), "sum_actual": Decimal(0)} for _ in range(5)]
    n_flat = 0
    n_risk_veto = 0
    n_challenged = 0
    for row in samples:
        move = (_decimal(row["outcome_mid"], "outcome_mid", positive=True)
                - _decimal(row["entry_mid"], "entry_mid", positive=True)) * PIP
        actual = "LONG" if move > threshold else "SHORT" if move < -threshold else "FLAT"
        n_flat += actual == "FLAT"
        n_risk_veto += row["risk_veto"]
        n_challenged += row["devils_advocate_challenge"]
        directions = _directions(row)
        for strategy, direction in directions.items():
            sign = 1 if direction == "LONG" else -1 if direction == "SHORT" else 0
            by_strategy[strategy].append({
                "regime": row["regime"], "direction": direction,
                "actual": actual, "gross": move * sign,
                "base_net": move * sign - cost * abs(sign),
            })
        primary = [vote["direction"] for vote in row["primary_votes"]]
        for i, direction in enumerate(primary):
            votes_by_seat[i].append(1 if direction == "LONG" else -1 if direction == "SHORT" else 0)
        total_directional = primary.count("LONG") + primary.count("SHORT")
        if total_directional:
            disagreement.append(Decimal(min(primary.count("LONG"), primary.count("SHORT")))
                                / Decimal(total_directional))
        if actual != "FLAT":
            prob = _decimal(row["consensus_probability_up"], "consensus_probability_up")
            y = Decimal(1 if actual == "LONG" else 0)
            brier.append((prob - y) ** 2)
            bin_id = min(4, int(prob * 5))
            calibration[bin_id]["count"] += 1
            calibration[bin_id]["sum_pred"] += prob
            calibration[bin_id]["sum_actual"] += y
    # Pairwise Pearson on the *same* non-abstained decisions; skip constant pairs.
    correlations = []
    for a in range(28):
        for b in range(a + 1, 28):
            paired = [(x, y) for x, y in zip(votes_by_seat[a], votes_by_seat[b]) if x and y]
            if len(paired) < 3:
                continue
            xs, ys = zip(*paired)
            n = len(xs)
            vx = n * sum(x * x for x in xs) - sum(xs) ** 2
            vy = n * sum(y * y for y in ys) - sum(ys) ** 2
            if vx <= 0 or vy <= 0:
                continue
            corr = (n * sum(x * y for x, y in paired) - sum(xs) * sum(ys)) / sqrt(vx * vy)
            correlations.append(corr)
    result = {}
    for strategy, records in by_strategy.items():
        actionable = [r for r in records if r["direction"] != "HOLD"]
        eval_directional = [r for r in actionable if r["actual"] != "FLAT"]
        correct = sum(r["direction"] == r["actual"] for r in eval_directional)
        stress_values = []
        for extra in stress:
            net = sum(r["base_net"] - extra * (r["direction"] != "HOLD") for r in records)
            stress_values.append({"extra_assumed_cost_pips": _metric(extra),
                                  "average_hypothetical_net_pips": _metric(net / len(records))})
        result[strategy] = {
            "records": len(records), "hypothetical_actions": len(actionable),
            "hold_actions": len(records) - len(actionable),
            "directional_labels_evaluated": len(eval_directional),
            "directional_accuracy": _metric(Decimal(correct) / len(eval_directional)) if eval_directional else None,
            "directional_accuracy_wilson_95_local": _wilson_95(correct, len(eval_directional)),
            "mean_hypothetical_gross_pips": _metric(sum(r["gross"] for r in records) / len(records)),
            "mean_hypothetical_net_assumed_cost_pips": _metric(sum(r["base_net"] for r in records) / len(records)),
            "net_sensitivity": stress_values,
            "per_regime": [
                {"regime": name, "records": len(rows),
                 "mean_hypothetical_net_assumed_cost_pips":
                 _metric(sum(r["base_net"] for r in rows) / len(rows)) if rows else None}
                for name in REGIMES for rows in ([r for r in records if r["regime"] == name],)
            ],
        }
    return {
        "score_status": "LOCAL_HYPOTHETICAL_EXPLORATORY_ONLY",
        "sample_size": len(samples), "neutral_label_count": n_flat,
        "risk_veto_count": n_risk_veto, "devils_advocate_challenge_count": n_challenged,
        "mean_primary_directional_disagreement": _metric(sum(disagreement) / len(disagreement)) if disagreement else None,
        "directional_disagreement_sample_count": len(disagreement),
        "pairwise_correlations_with_variation": len(correlations),
        "mean_absolute_primary_vote_correlation": _metric(sum(map(abs, correlations)) / len(correlations)) if correlations else None,
        "high_absolute_correlation_pair_count": sum(abs(x) >= 0.85 for x in correlations),
        "brier_probability_up": _metric(sum(brier) / len(brier)) if brier else None,
        "temporal_calibration_drift_exploratory": (
            {"earlier_half_brier": _metric(sum(brier[:len(brier)//2]) / (len(brier)//2)),
             "later_half_brier": _metric(sum(brier[len(brier)//2:]) / (len(brier) - len(brier)//2)),
             "later_minus_earlier_brier": _metric(
                 sum(brier[len(brier)//2:]) / (len(brier) - len(brier)//2)
                 - sum(brier[:len(brier)//2]) / (len(brier)//2)),
             "drift_statistically_established": False}
            if len(brier) >= 4 else None
        ),
        "calibration_bins": [
            {"bin": str(i), "sample_count": c["count"],
             "mean_predicted_up": _metric(c["sum_pred"] / c["count"]) if c["count"] else None,
             "observed_up_frequency": _metric(c["sum_actual"] / c["count"]) if c["count"] else None}
            for i, c in enumerate(calibration)
        ],
        "strategies": result,
        "compared_to_baselines_externally_validated": False,
        "net_of_actual_broker_costs_verified": False,
        "heldout_externally_verified": False,
        "profitable_or_statistically_superior_strategy_established": False,
    }


def assess_stage6_research_evaluation(*, contracts_file, manifest_file, epoch_id, as_of,
                                        protocol_file=None, sample_index=None, evidence_root=None):
    """No evidence => descriptive protocol requirements; candidate data => local metrics."""
    if epoch_id != EPOCH:
        raise ResearchEvaluationError("Historical epoch mismatch; rebind forbidden.")
    cutoff = _utc(as_of)
    try:
        static = inspect_static_council(
            contracts_file=contracts_file, manifest_file=manifest_file)
    except (CouncilContextError, OSError, ValueError, TypeError):
        raise ResearchEvaluationError("Static Council contract inspection failed.") from None
    if protocol_file is None:
        if sample_index is not None or evidence_root is not None:
            raise ResearchEvaluationError("Sample/index root cannot exist without study protocol.")
        proto_result = {"status": "NOT_SUPPLIED", "sha256": None,
                        "claimed_predeclared_holdout_custody": False}
        sample_result = {"status": "NOT_SUPPLIED", "index_sha256": None,
                         "locally_hash_matched_samples": 0, "locally_scoreable_samples": 0,
                         "study_minimum_samples_met": False}
        metrics = None
    else:
        p = _protocol(protocol_file, static, cutoff)
        proto_result = {"status": "LOCAL_PREDECLARED_PROTOCOL_CLAIM_UNATTESTED",
                        "sha256": p["sha256"],
                        "experiment_id": p["doc"]["experiment_id"],
                        "window_started": p["start"] <= cutoff,
                        "window_completed": p["end"] <= cutoff,
                        "claimed_predeclared_holdout_custody": False,
                        "requested_minimum_sample_count": p["doc"]["min_samples"]}
        if sample_index is None:
            if evidence_root is not None:
                raise ResearchEvaluationError("Sample evidence root without index.")
            sample_result = {"status": "NOT_SUPPLIED", "index_sha256": None,
                             "locally_hash_matched_samples": 0, "locally_scoreable_samples": 0,
                             "study_minimum_samples_met": False}
            metrics = None
        else:
            if evidence_root is None:
                raise ResearchEvaluationError("Study sample index requires evidence root.")
            index = _candidate_index(sample_index, evidence_root, p, static, cutoff=cutoff)
            enough = len(index["records"]) >= p["doc"]["min_samples"]
            completed = p["end"] <= cutoff
            sample_result = {"status": "LOCAL_CANDIDATE_SAMPLE_UNATTESTED",
                             "index_sha256": index["index_sha256"],
                             "locally_hash_matched_samples": len(index["records"]),
                             "locally_scoreable_samples": len(index["records"]) if enough and completed else 0,
                             "study_minimum_samples_met": enough}
            metrics = _score(index["records"], p) if enough and completed else None
    blockers = [
        "NO_INDEPENDENT_PRE_EVALUATION_HOLDOUT_FREEZE",
        "NO_INDEPENDENT_PREDECISION_FORECAST_CUSTODY",
        "NO_INDEPENDENT_POST_HORIZON_OUTCOME_OR_CLAIMED_COST_CUSTODY",
        "COUNCIL_BENCHMARK_SUPERIORITY_NOT_INDEPENDENTLY_DEMONSTRATED",
        "COST_AWARE_FORWARD_PROFITABILITY_NOT_VERIFIED",
        "NO_OPERATOR_APPROVED_RISK_OR_EXECUTION",
    ]
    if proto_result["status"] == "NOT_SUPPLIED":
        blockers.append("NO_PREDECLARED_EVALUATION_PROTOCOL_SUPPLIED")
    if sample_result["status"] == "NOT_SUPPLIED":
        blockers.append("NO_EXISTING_POINT_IN_TIME_EVALUATION_SAMPLE_INDEX_SUPPLIED")
    elif not sample_result["study_minimum_samples_met"]:
        blockers.append("LOCAL_SAMPLE_BELOW_PREDECLARED_MINIMUM")
    if proto_result["status"] != "NOT_SUPPLIED" and not proto_result["window_completed"]:
        blockers.append("EVALUATION_WINDOW_NOT_COMPLETED")
    report = {
        "assessment_version": VERSION,
        "scope": "P6_17_TO_P6_20_OFFLINE_EVALUATION_DESIGN",
        "epoch_id": epoch_id, "as_of": cutoff.isoformat(),
        "static_council": {
            "status": static["status"], "seats": static["static_seats"],
            "source_families": static["static_source_families"],
            "manifest_sha256": static["manifest_sha256"],
            "contracts_sha256": static["contracts_sha256"],
            "primary_seats_evaluated_by_contract": 28,
            "governance_stages_executed": False,
        },
        "protocol_candidate": proto_result, "candidate_sample": sample_result,
        "exploratory_local_metrics": metrics,
        "metric_scope": "HYPOTHETICAL_LOCAL_NEVER_CERTIFIED_TRADING_PERFORMANCE",
        "benchmark_definitions": [
            {"name": "NO_TRADE", "action": "HOLD; zero gross and assumed cost"},
            {"name": "ALWAYS_LONG", "action": "One hypothetical long per eligible decision"},
            {"name": "ALWAYS_SHORT", "action": "One hypothetical short per eligible decision"},
            {"name": "MOMENTUM_SIGN", "action": "Sign of claimed predecision prior return; zero means HOLD"},
        ],
        "external_source_families_independently_verified": 0,
        "external_evaluation_gates_verified": 0,
        "independent_holdout_certified": False,
        "independent_outcomes_certified": False,
        "actual_broker_costs_certified": False,
        "statistical_superiority_or_net_profit_established": False,
        "read_only": True, "files_written": False,
        "network_accessed": False, "database_accessed": False,
        "broker_connected": False, "collector_started": False,
        "council_invoked": False, "model_training_authorized": False,
        "settlement_invoked": False, "historical_epoch_rebound": False,
        "paper_orders_authorized": False, "live_orders_authorized": False,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(blockers), "final_action": "HOLD",
    }
    report["report_sha256"] = _digest(report)
    return report


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-source", action="store_true", required=True)
    parser.add_argument("--read-only-evidence", action="store_true", required=True)
    parser.add_argument("--contracts-file", default="backend/agents/contracts.py")
    parser.add_argument("--manifest-file", default="backend/agents/council_manifest.py")
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--protocol-file")
    parser.add_argument("--sample-index")
    parser.add_argument("--evidence-root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if _utc(args.as_of) > datetime.now(UTC):
            raise ResearchEvaluationError("Future review cutoff refused.")
        report = assess_stage6_research_evaluation(
            contracts_file=args.contracts_file, manifest_file=args.manifest_file,
            epoch_id=args.epoch_id, as_of=args.as_of,
            protocol_file=args.protocol_file,
            sample_index=args.sample_index, evidence_root=args.evidence_root,
        )
    except (ResearchEvaluationError, OSError, ValueError, TypeError) as exc:
        print("STAGE 6 EVALUATION ASSESSMENT BLOCKED: " + type(exc).__name__)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Stage 6 P6-17..20 research evaluation")
        print("Council seats/families: 31/13 | PRIMARY votes: 28")
        print("Frozen protocol claim: " + report["protocol_candidate"]["status"])
        print("Local evaluation sample: " + report["candidate_sample"]["status"])
        print("Local exploratory score: " + ("AVAILABLE_UNATTESTED" if report["exploratory_local_metrics"] else "NOT_AVAILABLE"))
        print("Independent holdout, outcome, broker costs: NOT VERIFIED")
        print("Council superiority/net trading profitability: NOT VERIFIED")
        for blocker in report["blockers"]:
            print("  BLOCKER " + blocker)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | NO COUNCIL | NO TRADING | NO SETTLEMENT | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()

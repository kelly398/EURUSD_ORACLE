
"""Milestones 25-28: verified, inert historical research consensus preview.

M25: lineage-bound evidence contracts; M26: three independent numeric lenses;
M27: visible agreement/disagreement and conservative spread review;
M28: integrated audit over each immutable source export.

NOT a second canonical 31-seat Council. No AgentContext, live data, trained
models, database calls, prediction probabilities, persistence or orders.
Historical first-availability and event UTC remain uncertified. HOLD always.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re

from backend.models.consolidate_historical_utc_research import _canonical
from backend.models.historical_utc_registry_features import (
    RegistryFeatureError, assess_registry_features,
)
from backend.models.historical_utc_research_features import (
    ResearchFeatureError, ResearchFeatureRow,
    build_clock_aware_research_features,
)
from backend.models.register_historical_utc_seasons import (
    SeasonRegistryError, read_index,
)

VERSION = "EURUSD_RESEARCH_CONSENSUS_PREVIEW_V1"
PROBES = ("trend_vs_sma", "one_bar_momentum", "candle_body")
PIP = 0.0001
MAX_ROWS = 500_000
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class ResearchPreviewError(RuntimeError):
    """An input, source, or non-executing research invariant failed."""


@dataclass(frozen=True, slots=True)
class ResearchSnapshot:
    """Minimal verified-feature representation; not an admitted Council input."""

    source_lineage_sha256: str
    source_rows_sha256: str
    feature_rows_sha256: str
    source_row_id: int
    broker_label_time: str
    candidate_utc_open: str
    candidate_offset_hours: int
    segment_id: int
    close: float
    sma_close_12: float | None
    log_return_1: float | None
    body_pips: float
    atr_14_pips: float | None
    spread_points: int
    first_available_at: None = None
    historical_utc_certified: bool = False
    training_authorized: bool = False
    execution_authorized: bool = False


def _snapshot(row, parent):
    if not isinstance(row, ResearchFeatureRow):
        raise ResearchPreviewError("Expected a Milestone 07 feature row.")

    for key in (
        "source_lineage_sha256",
        "source_rows_sha256",
        "feature_rows_sha256",
    ):
        value = parent.get(key)
        if not isinstance(value, str) or HEX64.fullmatch(value) is None:
            raise ResearchPreviewError("Invalid feature lineage digest.")

    safety = row.record()
    if (
        safety.get("first_available_at") is not None
        or safety.get("research_only") is not True
        or safety.get("training_authorized") is not False
        or safety.get("execution_authorized") is not False
    ):
        raise ResearchPreviewError("Unadmitted research feature row.")

    return ResearchSnapshot(
        source_lineage_sha256=parent["source_lineage_sha256"],
        source_rows_sha256=parent["source_rows_sha256"],
        feature_rows_sha256=parent["feature_rows_sha256"],
        source_row_id=row.source_row_id,
        broker_label_time=row.broker_label_time,
        candidate_utc_open=row.candidate_utc_open,
        candidate_offset_hours=row.candidate_offset_hours,
        segment_id=row.segment_id,
        close=row.close,
        sma_close_12=row.sma_close_12,
        log_return_1=row.log_return_1,
        body_pips=row.body_pips,
        atr_14_pips=row.atr_14_pips,
        spread_points=row.spread_points,
    )


def preview_snapshot(snapshot, *, spread_review_points=50):
    """Deterministic three-lens description; always return HOLD.

    Agreement is not forecast accuracy, probability, a direction to trade,
    first-availability proof, or permission to call the canonical Council.
    """
    if not isinstance(snapshot, ResearchSnapshot):
        raise ResearchPreviewError("ResearchSnapshot required.")

    if (
        type(spread_review_points) is not int
        or not 1 <= spread_review_points <= 100_000
    ):
        raise ResearchPreviewError(
            "spread_review_points must be 1..100000."
        )

    if (
        snapshot.first_available_at is not None
        or snapshot.historical_utc_certified is not False
        or snapshot.training_authorized is not False
        or snapshot.execution_authorized is not False
    ):
        raise ResearchPreviewError(
            "Snapshot would bypass admission restrictions."
        )

    if (
        any(
            HEX64.fullmatch(value) is None
            for value in (
                snapshot.source_lineage_sha256,
                snapshot.source_rows_sha256,
                snapshot.feature_rows_sha256,
            )
        )
        or type(snapshot.source_row_id) is not int
        or snapshot.source_row_id <= 0
        or type(snapshot.segment_id) is not int
        or snapshot.segment_id <= 0
        or type(snapshot.spread_points) is not int
        or snapshot.spread_points < 0
        or type(snapshot.candidate_offset_hours) is not int
        or snapshot.candidate_offset_hours not in (2, 3)
    ):
        raise ResearchPreviewError(
            "Invalid evidence identity or magnitude."
        )

    try:
        broker = datetime.fromisoformat(snapshot.broker_label_time)
        candidate = datetime.fromisoformat(snapshot.candidate_utc_open)
    except (ValueError, TypeError) as exc:
        raise ResearchPreviewError(
            "Unparseable candidate clock label."
        ) from exc

    if (
        broker.utcoffset() != timedelta(0)
        or candidate.utcoffset() != timedelta(0)
        or broker - candidate
        != timedelta(hours=snapshot.candidate_offset_hours)
    ):
        raise ResearchPreviewError(
            "Candidate clock and broker label disagree."
        )

    for value in (snapshot.close, snapshot.body_pips):
        if (
            type(value) not in (int, float)
            or not isfinite(value)
        ):
            raise ResearchPreviewError(
                "Nonfinite numeric research evidence."
            )

    if snapshot.close <= 0:
        raise ResearchPreviewError("Nonpositive close.")

    for value in (
        snapshot.sma_close_12,
        snapshot.log_return_1,
        snapshot.atr_14_pips,
    ):
        if (
            value is not None
            and (
                type(value) not in (int, float)
                or not isfinite(value)
            )
        ):
            raise ResearchPreviewError(
                "Nonfinite rolling indicator."
            )

    missing = [
        name
        for name, value in (
            ("sma_close_12", snapshot.sma_close_12),
            ("log_return_1", snapshot.log_return_1),
            ("atr_14_pips", snapshot.atr_14_pips),
        )
        if value is None
    ]

    if not missing and (
        snapshot.sma_close_12 <= 0
        or snapshot.atr_14_pips <= 0
    ):
        missing.append("invalid_indicator_scale")

    probes = []

    if not missing:
        scale = snapshot.atr_14_pips

        raw_scores = (
            (
                snapshot.close - snapshot.sma_close_12
            ) / PIP / scale,
            (
                snapshot.log_return_1
                * snapshot.close / PIP / scale
            ),
            snapshot.body_pips / scale,
        )

        for name, raw in zip(PROBES, raw_scores):
            if not isfinite(raw):
                raise ResearchPreviewError(
                    "Nonfinite computed diagnostic."
                )

            score = round(
                max(-1.0, min(1.0, raw)),
                6,
            )

            direction = (
                "UP" if score >= 0.15
                else "DOWN" if score <= -0.15
                else "NEUTRAL"
            )

            probes.append({
                "probe": name,
                "direction": direction,
                "score": score,
            })

    flagged = (
        snapshot.spread_points >= spread_review_points
    )

    directions = [
        p["direction"] for p in probes
    ]

    if missing:
        status = "INSUFFICIENT_RESEARCH_FEATURES"

    elif flagged:
        status = "SPREAD_REVIEW_REQUIRED"

    elif (
        len(directions) == 3
        and len(set(directions)) == 1
        and directions[0] != "NEUTRAL"
    ):
        status = "DIRECTIONAL_ALIGNMENT_RESEARCH_ONLY"

    elif len(set(directions) - {"NEUTRAL"}) > 1:
        status = "DIRECTIONAL_DISAGREEMENT_RESEARCH_ONLY"

    else:
        status = "INSUFFICIENT_DIRECTIONAL_AGREEMENT"

    return {
        "source_row_id": snapshot.source_row_id,
        "broker_label_time": snapshot.broker_label_time,
        "candidate_utc_open": snapshot.candidate_utc_open,
        "source_lineage_sha256": snapshot.source_lineage_sha256,
        "source_rows_sha256": snapshot.source_rows_sha256,
        "feature_rows_sha256": snapshot.feature_rows_sha256,
        "segment_id": snapshot.segment_id,
        "spread_points": snapshot.spread_points,
        "spread_review_threshold_points": spread_review_points,
        "spread_review_flag": flagged,
        "missing_indicators": missing,
        "diagnostic_probes": probes,
        "research_consensus_status": status,
        "research_only": True,
        "action": "HOLD",
        "prediction_probability": None,
        "first_available_at": None,
        "historical_utc_certified": False,
        "training_authorized": False,
        "execution_authorized": False,
    }


def assess_historical_agent_previews(
    registry,
    indexes,
    dataset_root,
    *,
    spread_review_points=50,
):
    """Re-audit all registered exports, preview LAST row of each separately."""

    if (
        type(spread_review_points) is not int
        or not 1 <= spread_review_points <= 100_000
    ):
        raise ResearchPreviewError(
            "spread_review_points must be 1..100000."
        )

    indexes = tuple(indexes)
    root = Path(dataset_root)

    try:
        coverage = assess_registry_features(
            registry,
            indexes,
            root,
        )

        manifests = [
            read_index(index)
            for index in indexes
        ]

    except (
        RegistryFeatureError,
        SeasonRegistryError,
        OSError,
    ) as exc:
        raise ResearchPreviewError(
            "Source-backed feature verification failed."
        ) from exc

    expected = coverage["parent_features"]

    actual = [
        (index["index_sha256"], parent)
        for index in manifests
        for parent in index["source_windows"]
    ]

    if len(actual) != len(expected):
        raise ResearchPreviewError(
            "Feature source membership mismatch."
        )

    previews = []

    try:
        for (index_sha, parent), proof in zip(
            actual,
            expected,
        ):
            lineage = parent["source_lineage_sha256"]

            if (
                index_sha != proof["index_sha256"]
                or lineage != proof["source_lineage_sha256"]
                or (
                    parent["source_rows_sha256"]
                    != proof["source_rows_sha256"]
                )
            ):
                raise ResearchPreviewError(
                    "Feature source lineage/order mismatch."
                )

            folder = root / ("dataset_" + lineage)

            if folder.is_symlink() or not folder.is_dir():
                raise ResearchPreviewError(
                    "Unsafe source export folder."
                )

            features = build_clock_aware_research_features(
                folder,
                max_rows=MAX_ROWS,
            )

            if (
                not features.rows
                or features.source_lineage_sha256 != lineage
                or (
                    features.source_rows_sha256
                    != proof["source_rows_sha256"]
                )
                or (
                    features.feature_rows_sha256
                    != proof["feature_rows_sha256"]
                )
                or len(features.rows) != proof["row_count"]
                or (
                    features.segment_count
                    != proof["feature_segment_count"]
                )
                or features.gap_count != proof["internal_gap_count"]
            ):
                raise ResearchPreviewError(
                    "Recomputed feature identity mismatch."
                )

            # Never select a warm row before a gap in place of
            # the latest row of this particular export.
            snap = _snapshot(
                features.rows[-1],
                proof,
            )

            item = preview_snapshot(
                snap,
                spread_review_points=spread_review_points,
            )

            item["index_sha256"] = index_sha
            item["source_export_rows"] = len(features.rows)

            previews.append(item)

    except ResearchPreviewError:
        raise

    except (
        ResearchFeatureError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
        UnicodeError,
    ) as exc:
        raise ResearchPreviewError(
            "Re-audited preview could not be completed."
        ) from exc

    if (
        len(previews) != coverage["source_export_count"]
        or sum(
            p["source_export_rows"]
            for p in previews
        ) != coverage["row_count"]
    ):
        raise ResearchPreviewError(
            "Preview coverage totals disagree."
        )

    payload = {
        "assessment_version": VERSION,
        "registry_sha256": coverage["registry_sha256"],
        "feature_inventory_sha256": (
            coverage["feature_inventory_sha256"]
        ),
        "source_export_count": len(previews),
        "underlying_audited_rows": coverage["row_count"],
        "preview_selection": (
            "LAST_OBSERVED_ROW_PER_EXPORT_NO_CROSS_EXPORT_STATE"
        ),
        "previews": previews,
        "real_council_invoked": False,
        "canonical_31_seat_council_replaced": False,
        "research_only": True,
        "historical_utc_certified": False,
        "historical_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
        "action": "HOLD",
    }

    payload["preview_sha256"] = sha256(
        _canonical(payload)
    ).hexdigest()

    return payload


def run(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--registry",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--index",
        action="append",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path("data/research/historical_utc_v2"),
    )

    parser.add_argument(
        "--spread-review-points",
        type=int,
        default=50,
    )

    parser.add_argument(
        "--research-only",
        required=True,
        action="store_true",
    )

    parser.add_argument(
        "--json",
        action="store_true",
    )

    args = parser.parse_args(argv)

    try:
        report = assess_historical_agent_previews(
            args.registry,
            args.index,
            args.dataset_root,
            spread_review_points=args.spread_review_points,
        )

    except ResearchPreviewError as exc:
        print(
            "RESEARCH PREVIEW BLOCKED: " + str(exc)
        )
        return 1

    if args.json:
        print(
            json.dumps(
                report,
                indent=2,
                sort_keys=True,
            )
        )

    else:
        print(
            "EURUSD ORACLE | Research-only analytical preview M25-28"
        )

        print(
            "REGISTRY: " + report["registry_sha256"]
        )

        print(
            f"AUDITED ROWS: {report['underlying_audited_rows']} | "
            f"SEPARATE EXPORTS: {report['source_export_count']}"
        )

        for p in report["previews"]:
            probes = ", ".join(
                f"{q['probe']}={q['direction']}"
                for q in p["diagnostic_probes"]
            ) or "no warmed probes"

            print(
                f"SOURCE {p['source_lineage_sha256'][:12]} "
                f"ROW {p['source_row_id']} | "
                f"{p['research_consensus_status']} "
                f"| {probes} | ACTION=HOLD"
            )

        print(
            "PREVIEW SHA-256: " + report["preview_sha256"]
        )

        print(
            "CANONICAL COUNCIL NOT INVOKED | NO FILES WRITTEN | "
            "UTC UNCERTIFIED | TRAINING BLOCKED | EXECUTION BLOCKED"
        )

    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()

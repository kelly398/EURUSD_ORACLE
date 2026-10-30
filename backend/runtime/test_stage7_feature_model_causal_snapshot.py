
"""P7-17..20: synthetic, offline feature DAG, model compatibility and causal replay.

Only tmp_path files are created by tests. No historical originals, broker,
collector, Council, network, model loading, fitting, settlement or orders.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime.stage7_feature_model_causal_snapshot import (
    EPOCH, POLICY_SHA256, DERIVED_INPUTS, FAMILIES, FORECAST_SPEC, FEATURE_SCHEMA,
    EVAL_PLAN_SPEC, REQUIRED_REGIMES, FeatureModelError,
    _digest, _utc, assess_stage7_feature_model, run,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "docs/clock_profiles/hfm_forward_20261027.json"
ASOF = "2026-10-30T11:00:00Z"
DECISION = "2026-10-30T10:00:20Z"


def stamp(seconds):
    return (
        datetime(2026, 10, 30, 10, tzinfo=timezone.utc)
        + timedelta(seconds=seconds)
    ).isoformat()


def save(path, data):
    path.write_text(
        json.dumps(data, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def source(rid, family="ticks", *, parents=(), kind=None, event="evt_01",
           published=1, available=2, received=3):
    if kind is None:
        kind = (
            "DERIVED" if family in ("market_state", "calendar_coverage", "risk_state")
            else "OPERATOR_POLICY" if family in ("risk_limits", "synthesis_policy")
            else "EVALUATION" if family == "reliability"
            else "ACTUAL" if family in ("cot_data", "ecb_data", "fed_data", "economic_calendar")
            else "OBSERVATION"
        )
    transform = family in ("market_state", "calendar_coverage", "risk_state")
    return {
        "record_id": rid,
        "family": family,
        "event_id": event,
        "vintage_id": "v_" + rid,
        "kind": kind,
        "provider_claim": "SYNTHETIC_SOURCE_UNATTESTED",
        "observed_at": stamp(0),
        "event_at": stamp(0),
        "published_at": stamp(published),
        "first_available_at": stamp(available),
        "received_at": stamp(received),
        "uncertainty_ms": "12.500",
        "artifact_path": "bytes/" + rid + ".dat",
        "artifact_sha256": "0" * 64,
        "supersedes_id": None,
        "parent_ids": list(parents),
        "transform_path": "code/derived_transform.py" if transform else None,
        "transform_sha256": "0" * 64 if transform else None,
        "revocation_claim": False,
        "value_state": "PRESENT",
    }


def feature(rid, label="tick_features", *, parents=("tick_01",),
            event="evt_01", published=11, available=12, received=13):
    return {
        "record_id": rid,
        "label": label,
        "event_id": event,
        "vintage_id": "v_" + rid,
        "feature_schema_version": FEATURE_SCHEMA,
        "observed_at": stamp(0),
        "event_at": stamp(0),
        "published_at": stamp(published),
        "first_available_at": stamp(available),
        "received_at": stamp(received),
        "uncertainty_ms": "5",
        "artifact_path": "bytes/" + rid + ".dat",
        "artifact_sha256": "0" * 64,
        "supersedes_id": None,
        "parent_ids": list(parents),
        "transform_path": "code/derived_transform.py",
        "transform_sha256": "0" * 64,
    }


def model():
    return {
        "model_id": "SYNTHETIC_MODEL_ONLY",
        "forecast_spec_version": FORECAST_SPEC,
        "feature_schema_version": FEATURE_SCHEMA,
        "required_features": ["feature_vector", "tick_features"],
        "feature_version_pins": {
            "feature_vector": "0" * 64,
            "tick_features": "0" * 64,
        },
        "model_path": "assets/model.bin",
        "model_sha256": "0" * 64,
        "training_dataset_path": "assets/training_index.bin",
        "training_dataset_sha256": "0" * 64,
        "training_cutoff_at": "2026-10-29T08:00:00Z",
        "last_training_label_matured_at": "2026-10-30T09:30:00Z",
        "training_data_available_at": "2026-10-30T09:31:00Z",
        "built_at": stamp(16),
        "first_available_at": stamp(18),
        "regimes_trained": list(REQUIRED_REGIMES),
        "risk_limits_record_id": "risk_limits_01",
        "synthesis_policy_record_id": "synthesis_policy_01",
        "evaluation_plan_spec_version": EVAL_PLAN_SPEC,
        "evaluation_plan_path": "assets/evaluation_plan.bin",
        "evaluation_plan_sha256": "0" * 64,
        "evaluation_plan_available_at": "2026-10-30T09:32:00Z",
    }


def seat():
    return {
        "seat_id": "SYNTHETIC_SEAT_01",
        "required_labels": ["ticks", "feature_vector", "risk_limits"],
        "requires_model": True,
        "expected_regime": None,
    }


def fixture(tmp_path, *, with_model=False, full=False):
    root = tmp_path / "UNTRUSTED_LOCAL_SNAPSHOT"
    (root / "bytes").mkdir(parents=True)
    (root / "code").mkdir()
    (root / "assets").mkdir()
    sources = [
        source("tick_01"),
        source("candles_01", "candles"),
    ]
    features = [feature("tick_features_01")]
    seats = [seat()]

    if full:
        sources.extend([
            source(
                "market_state_01", "market_state",
                parents=("tick_01", "candles_01"),
                published=5, available=6, received=7,
            ),
            source("ecb_01", "ecb_data"),
            source("fed_01", "fed_data"),
            source("risk_limits_01", "risk_limits"),
            source("synthesis_policy_01", "synthesis_policy"),
        ])
        features.extend([
            feature(
                "feature_vector_01", "feature_vector",
                parents=("tick_01", "candles_01", "market_state_01"),
            ),
            feature(
                "market_structure_01", "market_structure",
                parents=("market_state_01", "candles_01"),
            ),
            feature(
                "liquidity_state_01", "liquidity_state",
                parents=("market_state_01", "tick_01"),
            ),
            feature(
                "session_state_01", "session_state",
                parents=("market_state_01", "candles_01"),
            ),
            feature(
                "rates_data_01", "rates_data",
                parents=("ecb_01", "fed_01"),
            ),
        ])

    doc = {
        "schema_version": 1,
        "historical_epoch_id": EPOCH,
        "packet_id": "SYNTHETIC_P7_B5_UNTRUSTED",
        "as_of": ASOF,
        "sources": sources,
        "features": features,
        "model": model() if with_model else None,
        "seats": seats,
    }
    code = b"def transform(source):\n    return source\n"
    (root / "code/derived_transform.py").write_bytes(code)

    for row in doc["sources"] + doc["features"]:
        content = b"FAKE_LOCAL_UNTRUSTED_" + row["record_id"].encode("ascii")
        (root / row["artifact_path"]).write_bytes(content)
        row["artifact_sha256"] = sha256(content).hexdigest()
        if row["transform_path"] is not None:
            row["transform_sha256"] = sha256(code).hexdigest()

    if with_model:
        doc["model"]["feature_version_pins"] = {
            row["label"]: row["artifact_sha256"]
            for row in doc["features"]
            if row["label"] in doc["model"]["required_features"]
        }
        for path_field, hash_field in (
            ("model_path", "model_sha256"),
            ("training_dataset_path", "training_dataset_sha256"),
            ("evaluation_plan_path", "evaluation_plan_sha256"),
        ):
            m = doc["model"]
            content = b"OPAQUE_NOT_FOR_EXECUTION_" + path_field.encode("ascii")
            (root / m[path_field]).write_bytes(content)
            m[hash_field] = sha256(content).hexdigest()

    index = tmp_path / "feature_model_index.json"
    save(index, doc)
    return root, index, doc


def assess(index=None, root=None, *, decision=DECISION, asof=ASOF,
           replay_index=None, replay_root=None, policy=POLICY):
    return assess_stage7_feature_model(
        epoch_id=EPOCH,
        as_of=asof,
        decision_at=decision,
        policy_file=policy,
        index_file=index,
        evidence_root=root,
        replay_index=replay_index,
        replay_root=replay_root,
    )


def field(result, name):
    return next(
        x for x in result["point_in_time_snapshot"]["context_fields"]
        if x["label"] == name
    )


def overwrite(index, doc, category, n, **changes):
    doc[category][n].update(changes)
    save(index, doc)


def append_valid(root, index, doc, row, kind):
    new = deepcopy(row)
    content = b"FAKE_LOCAL_UNTRUSTED_" + new["record_id"].encode("ascii")
    target = root / new["artifact_path"]
    target.write_bytes(content)
    new["artifact_sha256"] = sha256(content).hexdigest()
    if new["transform_path"]:
        new["transform_sha256"] = sha256(
            (root / new["transform_path"]).read_bytes()
        ).hexdigest()
    doc[kind].append(new)
    save(index, doc)


# P7-17: pinned derived input source graph and immutable code ancestry.

def test_p717_canonical_family_and_derived_inventory():
    report = assess()
    assert len(FAMILIES) == 13
    assert len(DERIVED_INPUTS) == 6
    assert report["canonical_derived_labels"] == list(DERIVED_INPUTS)
    assert report["derived_family_dependencies"]["feature_vector"] == [
        "candles", "market_state", "ticks"
    ]
    assert report["derived_family_dependencies"]["rates_data"] == [
        "ecb_data", "fed_data"
    ]


def test_p717_no_index_does_not_invent_provenance():
    report = assess()
    assert report["local_evidence"]["status"] == "NOT_SUPPLIED"
    assert report["point_in_time_snapshot"]["local_candidate_fields"] == 0
    assert not report["G06_independently_verified"]
    assert report["final_action"] == "HOLD"


def test_p717_pinned_transform_and_tick_feature_candidate(tmp_path):
    root, index, _ = fixture(tmp_path)
    report = assess(index, root)
    tick_features = field(report, "tick_features")
    assert tick_features["status"] == "LOCAL_CANDIDATE_UNATTESTED"
    assert tick_features["source_ancestry"] == ["tick_01"]
    assert len(tick_features["transform_sha256"]) == 64
    assert tick_features["uncertainty_ms"] == "17.500"


def test_p717_complete_six_features_local_only(tmp_path):
    root, index, _ = fixture(tmp_path, full=True)
    report = assess(index, root)
    for label in DERIVED_INPUTS:
        assert field(report, label)["status"] == "LOCAL_CANDIDATE_UNATTESTED"
    assert "ecb_01" in field(report, "rates_data")["source_ancestry"]
    assert "fed_01" in field(report, "rates_data")["source_ancestry"]
    assert not report["G06_independently_verified"]


def test_p717_market_state_is_derived_from_candles_ticks(tmp_path):
    root, index, _ = fixture(tmp_path, full=True)
    state = field(assess(index, root), "market_state")
    assert set(state["source_ancestry"]) == {
        "market_state_01", "tick_01", "candles_01"
    }
    assert state["transform_sha256"] is not None


def test_p717_missing_required_derived_family_quarantined(tmp_path):
    root, index, doc = fixture(tmp_path, full=True)
    overwrite(
        index, doc, "features", 1,
        parent_ids=["tick_01", "candles_01"],
    )
    assert "DERIVED_FAMILY_ANCESTRY_INCOMPLETE" in field(
        assess(index, root), "feature_vector"
    )["issues"]


def test_p717_missing_market_state_parent_quarantined(tmp_path):
    root, index, doc = fixture(tmp_path, full=True)
    overwrite(index, doc, "sources", 2, parent_ids=["tick_01"])
    assert "DERIVED_SOURCE_PARENT_FAMILY_INCOMPLETE" in field(
        assess(index, root), "market_state"
    )["issues"]


def test_p717_missing_upstream_record_quarantined(tmp_path):
    root, index, doc = fixture(tmp_path)
    overwrite(index, doc, "features", 0, parent_ids=["unknown_tick"])
    assert "PARENT_NOT_VISIBLE_AT_DECISION" in field(
        assess(index, root), "tick_features"
    )["issues"]


def test_p717_future_parent_of_earlier_feature_quarantined(tmp_path):
    root, index, doc = fixture(tmp_path)
    overwrite(
        index, doc, "sources", 0,
        published_at=stamp(14),
        first_available_at=stamp(15),
        received_at=stamp(16),
    )
    assert "NONCAUSAL_PARENT_AVAILABILITY" in field(
        assess(index, root), "tick_features"
    )["issues"]


def test_p717_dependency_cycle_quarantined(tmp_path):
    root, index, doc = fixture(tmp_path, full=True)
    overwrite(
        index, doc, "features", 0,
        parent_ids=["tick_features_01", "feature_vector_01"],
    )
    with pytest.raises(FeatureModelError, match="Duplicate/self dependency"):
        assess(index, root)


def test_p717_mutual_feature_cycle_quarantined(tmp_path):
    root, index, doc = fixture(tmp_path, full=True)
    overwrite(
        index, doc, "features", 0,
        parent_ids=["feature_vector_01"],
    )
    overwrite(
        index, doc, "features", 1,
        parent_ids=["tick_features_01"],
    )
    report = assess(index, root)
    assert "DEPENDENCY_CYCLE" in field(
        report, "feature_vector"
    )["issues"]


def test_p717_transform_byte_tamper_rejected(tmp_path):
    root, index, _ = fixture(tmp_path)
    (root / "code/derived_transform.py").write_bytes(
        b"def transform():\n    return 7\n"
    )
    with pytest.raises(FeatureModelError, match="SHA-256 mismatch"):
        assess(index, root)


def test_p717_transform_invalid_ast_rejected(tmp_path):
    root, index, doc = fixture(tmp_path)
    bad = b"def syntax(: no\n"
    (root / "code/derived_transform.py").write_bytes(bad)
    doc["features"][0]["transform_sha256"] = sha256(bad).hexdigest()
    save(index, doc)
    with pytest.raises(FeatureModelError, match="AST invalid"):
        assess(index, root)


def test_p717_missing_feature_transform_pair_rejected(tmp_path):
    root, index, doc = fixture(tmp_path)
    overwrite(
        index, doc, "features", 0,
        transform_path=None,
        transform_sha256=None,
    )
    with pytest.raises(FeatureModelError, match="requires pinned"):
        assess(index, root)


def test_p717_feature_schema_mismatch_quarantined(tmp_path):
    root, index, doc = fixture(tmp_path)
    overwrite(
        index, doc, "features", 0,
        feature_schema_version="OTHER_SCHEMA",
    )
    assert "FEATURE_SCHEMA_VERSION_MISMATCH" in field(
        assess(index, root), "tick_features"
    )["issues"]


# P7-18: content-addressed point-in-time metadata.

def test_p718_snapshot_has_nineteen_explicit_fields():
    snapshot = assess()["point_in_time_snapshot"]
    assert len(snapshot["context_fields"]) == 19
    for entry in snapshot["context_fields"]:
        assert set((
            "observed_at", "first_available_at",
            "selected_vintage", "source_ancestry",
            "freshness_seconds", "uncertainty_ms",
        )).issubset(entry)
        assert entry["status"] == "NOT_SUPPLIED"
        assert entry["selected_vintage"] is None


def test_p718_snapshot_is_stable_for_same_packet_and_cutoff(tmp_path):
    root, index, _ = fixture(tmp_path, full=True)
    a, b = assess(index, root), assess(index, root)
    assert a == b
    assert len(a["point_in_time_snapshot"]["snapshot_id"]) == 64
    assert a["report_sha256"] == _digest({
        k: v for k, v in a.items()
        if k != "report_sha256"
    })


def test_p718_untrusted_future_tick_not_selected(tmp_path):
    root, index, doc = fixture(tmp_path)
    row = source(
        "tick_future", event="evt_future",
        published=30, available=31, received=32,
    )
    row["observed_at"] = stamp(30)
    row["event_at"] = stamp(30)
    append_valid(root, index, doc, row, "sources")
    snap = assess(index, root)["point_in_time_snapshot"]
    assert snap["postdecision_records_withheld"] == 1
    assert field(
        {"point_in_time_snapshot": snap}, "ticks"
    )["selected_vintage"] == "v_tick_01"


def test_p718_future_calendar_schedule_visible_as_claim_not_actual(tmp_path):
    root, index, doc = fixture(tmp_path)
    row = source(
        "calendar_announced", "economic_calendar",
        kind="SCHEDULE", event="FUTURE_EVENT",
    )
    row["event_at"] = "2026-11-03T12:00:00Z"
    row["value_state"] = "MISSING"
    append_valid(root, index, doc, row, "sources")
    entry = field(assess(index, root), "economic_calendar")
    assert entry["selected_vintage"] == "v_calendar_announced"
    assert entry["status"] == "NOT_SUPPLIED"


def test_p718_schedule_cannot_smuggle_present_actual(tmp_path):
    root, index, doc = fixture(tmp_path)
    row = source(
        "calendar_announced", "economic_calendar",
        kind="SCHEDULE", event="FUTURE_EVENT",
    )
    row["event_at"] = "2026-11-03T12:00:00Z"
    append_valid(root, index, doc, row, "sources")
    assert "SCHEDULE_VALUE_MUST_NOT_POSE_AS_ACTUAL" in field(
        assess(index, root), "economic_calendar"
    )["issues"]


def test_p718_stale_tick_is_quarantined(tmp_path):
    root, index, _ = fixture(tmp_path)
    result = assess(index, root, decision=stamp(240))
    assert "UNAPPROVED_FRESHNESS_LIMIT_EXCEEDED" in field(
        result, "ticks"
    )["issues"]


def test_p718_missing_tick_not_silently_zero(tmp_path):
    root, index, doc = fixture(tmp_path)
    overwrite(index, doc, "sources", 0, value_state="MISSING")
    result = assess(index, root)
    assert "REQUIRED_VALUE_MISSING" in field(
        result, "ticks"
    )["issues"]
    assert field(result, "ticks")["status"] == "QUARANTINED"


def test_p718_revoked_local_source_quarantines_dependent_feature(tmp_path):
    root, index, doc = fixture(tmp_path)
    overwrite(index, doc, "sources", 0, revocation_claim=True)
    result = assess(index, root)
    assert "LOCAL_REVOCATION_CLAIM" in field(
        result, "tick_features"
    )["issues"]
    assert result["source_families_independently_verified"] == 0


def test_p718_snapshot_no_raw_bytes_or_provider_claims(tmp_path):
    root, index, _ = fixture(tmp_path)
    body = json.dumps(assess(index, root))
    for forbidden in (
        "FAKE_LOCAL_UNTRUSTED",
        "SYNTHETIC_SOURCE_UNATTESTED",
        "bytes/tick_01.dat",
    ):
        assert forbidden not in body


# P7-19: model and policy ancestry and explicit seat abstention.

def test_p719_missing_model_reports_absent_and_seat_abstains(tmp_path):
    root, index, _ = fixture(tmp_path)
    snap = assess(index, root)["point_in_time_snapshot"]
    assert snap["model_compatibility"]["status"] == "NOT_SUPPLIED"
    assert snap["seat_preflight"][0]["status"] == "ABSTAIN"
    assert "MODEL_UNAVAILABLE_OR_MISMATCHED" in snap["seat_preflight"][0]["reasons"]


def test_p719_locally_compatible_model_never_approved(tmp_path):
    root, index, _ = fixture(tmp_path, with_model=True, full=True)
    result = assess(index, root)
    model_review = result["point_in_time_snapshot"]["model_compatibility"]
    assert model_review["status"] == "LOCAL_MODEL_COMPATIBLE_UNATTESTED"
    assert model_review["local_compatibility"] is True
    assert model_review["artifact_hash_matched_locally"] is True
    assert model_review["model_used_for_inference"] is False
    assert result["point_in_time_snapshot"]["seat_preflight"][0]["status"] == "ABSTAIN"
    assert not result["G06_independently_verified"]


@pytest.mark.parametrize("field_name,bad,reason", [
    ("forecast_spec_version", "OTHER_SPEC", "FORECAST_SPEC_VERSION_MISMATCH"),
    ("feature_schema_version", "OTHER_SCHEMA", "FEATURE_SCHEMA_VERSION_MISMATCH"),
    ("evaluation_plan_spec_version", "OTHER_EVAL", "EVALUATION_PLAN_SPEC_VERSION_MISMATCH"),
    ("regimes_trained", ["TREND"], "MODEL_REGIME_COVERAGE_INCOMPLETE"),
    ("training_cutoff_at", "2026-10-30T11:30:00Z", "MODEL_TRAINING_OR_BUILD_CHRONOLOGY_INVALID"),
    ("last_training_label_matured_at", "2026-10-30T10:00:19Z", "MODEL_TRAINING_OR_BUILD_CHRONOLOGY_INVALID"),
    ("training_data_available_at", "2026-10-30T10:00:17Z", "TRAINING_DATA_NOT_AVAILABLE_AT_BUILD"),
    ("evaluation_plan_available_at", "2026-10-30T10:00:21Z", "EVALUATION_PLAN_NOT_AVAILABLE_AT_DECISION"),
    ("risk_limits_record_id", "wrong_policy", "MODEL_POLICY_VERSION_MISMATCH"),
    ("synthesis_policy_record_id", "other_synthesis", "MODEL_POLICY_VERSION_MISMATCH"),
    (
        "required_features",
        ["feature_vector", "rates_data", "tick_features",
         "market_structure", "session_state", "liquidity_state"],
        "MODEL_REQUIRED_FEATURE_UNAVAILABLE_OR_QUARANTINED",
    ),
])
def test_p719_model_mismatch_reasons(tmp_path, field_name, bad, reason):
    root, index, doc = fixture(tmp_path, with_model=True, full=True)
    doc["model"][field_name] = bad
    if field_name == "required_features":
        doc["features"][-1]["feature_schema_version"] = "INCOMPATIBLE"
        doc["model"]["feature_version_pins"] = {
            row["label"]: row["artifact_sha256"]
            for row in doc["features"]
            if row["label"] in bad
        }
    save(index, doc)
    issues = assess(index, root)[
        "point_in_time_snapshot"
    ]["model_compatibility"]["issues"]
    assert reason in issues


def test_p719_tampered_opaque_model_bytes_rejected(tmp_path):
    root, index, _ = fixture(tmp_path, with_model=True, full=True)
    (root / "assets/model.bin").write_bytes(b"MODIFIED_OPAQUE_MODEL")
    with pytest.raises(FeatureModelError, match="SHA-256 mismatch"):
        assess(index, root)


def test_p719_model_in_future_is_withheld(tmp_path):
    root, index, doc = fixture(tmp_path, with_model=True, full=True)
    doc["model"]["built_at"] = stamp(40)
    doc["model"]["first_available_at"] = stamp(41)
    save(index, doc)
    review = assess(index, root)[
        "point_in_time_snapshot"
    ]["model_compatibility"]
    assert review["status"] == "POST_DECISION_MODEL_WITHHELD"
    assert review["eligible_model_digest"] is None


def test_p719_explicit_abstention_for_absent_feature(tmp_path):
    root, index, _ = fixture(tmp_path, with_model=False)
    seat_review = assess(index, root)[
        "point_in_time_snapshot"
    ]["seat_preflight"][0]
    assert "MISSING_OR_INCOMPATIBLE_INPUT:feature_vector" in seat_review["reasons"]
    assert "INDEPENDENT_SOURCE_AND_G06_REVIEW_REQUIRED" in seat_review["reasons"]


def test_p719_regime_not_independently_accepted(tmp_path):
    root, index, doc = fixture(tmp_path, with_model=True, full=True)
    doc["seats"][0]["expected_regime"] = "TREND"
    save(index, doc)
    assert "REGIME_CLASSIFICATION_NOT_INDEPENDENTLY_ADMITTED" in assess(
        index, root
    )["point_in_time_snapshot"]["seat_preflight"][0]["reasons"]


def test_p719_no_seat_spec_fabrication(tmp_path):
    root, index, doc = fixture(tmp_path)
    doc["seats"] = []
    save(index, doc)
    snap = assess(index, root)["point_in_time_snapshot"]
    assert snap["seat_requirements_supplied"] == 0
    assert snap["seat_preflight"] == []


def test_p719_model_signature_claim_cannot_approve(tmp_path):
    root, index, doc = fixture(tmp_path, with_model=True, full=True)
    doc["model"]["external_approval"] = True
    save(index, doc)
    with pytest.raises(FeatureModelError, match="Strict schema"):
        assess(index, root)


# P7-20: deterministic causal replay and fail-closed safety.

def test_p720_later_new_event_does_not_change_past_snapshot(tmp_path):
    root, index, doc = fixture(tmp_path)
    baseline = tmp_path / "baseline.json"
    save(baseline, doc)
    future = source(
        "later_tick", event="later_event",
        published=30, available=31, received=32,
    )
    future["observed_at"] = stamp(30)
    future["event_at"] = stamp(30)
    append_valid(root, index, doc, future, "sources")
    compare = assess(
        baseline, root,
        replay_index=index,
        replay_root=root,
    )
    assert compare["replay_comparison"]["status"] == "CAUSAL_SNAPSHOT_STABLE"
    assert compare["replay_comparison"]["causal_snapshot_unchanged"] is True


def test_p720_later_revision_cannot_rewrite_earlier_tick(tmp_path):
    root, index, doc = fixture(tmp_path)
    baseline = tmp_path / "baseline.json"
    save(baseline, doc)
    revision = source(
        "tick_revision", event="evt_01",
        kind="OBSERVATION", published=30, available=31, received=32,
    )
    revision["observed_at"] = stamp(30)
    revision["event_at"] = stamp(30)
    append_valid(root, index, doc, revision, "sources")
    report = assess(
        baseline, root,
        replay_index=index,
        replay_root=root,
    )
    assert report["replay_comparison"]["status"] == "CAUSAL_SNAPSHOT_STABLE"


def test_p720_changed_past_visible_artifact_is_detected(tmp_path):
    root, index, doc = fixture(tmp_path)
    baseline = tmp_path / "baseline.json"
    save(baseline, doc)
    content = b"REWRITTEN_HISTORICAL_TICK"
    (root / doc["sources"][0]["artifact_path"]).write_bytes(content)
    doc["sources"][0]["artifact_sha256"] = sha256(content).hexdigest()
    save(index, doc)
    with pytest.raises(FeatureModelError, match="SHA-256 mismatch"):
        assess(
            baseline, root,
            replay_index=index,
            replay_root=root,
        )


def test_p720_changed_visible_vintage_flags_replay_difference(tmp_path):
    root, index, doc = fixture(tmp_path)
    baseline = tmp_path / "baseline.json"
    save(baseline, doc)
    doc["sources"][0]["vintage_id"] = "REWRITTEN_VINTAGE"
    save(index, doc)
    report = assess(
        baseline, root,
        replay_index=index,
        replay_root=root,
    )
    assert report["replay_comparison"]["status"] == (
        "VISIBLE_CAUSAL_INPUT_CHANGED_QUARANTINED"
    )
    assert "CAUSAL_REPLAY_CHANGED_PRIOR_DECISION_CONTEXT" in report["blockers"]


def test_p720_replay_cutoff_unchanged_when_later_model_added(tmp_path):
    root, index, doc = fixture(tmp_path, with_model=True, full=True)
    baseline = tmp_path / "baseline.json"
    doc["model"]["built_at"] = stamp(30)
    doc["model"]["first_available_at"] = stamp(31)
    save(baseline, doc)
    doc["model"]["built_at"] = stamp(40)
    doc["model"]["first_available_at"] = stamp(41)
    save(index, doc)
    report = assess(
        baseline, root,
        replay_index=index,
        replay_root=root,
    )
    assert report["replay_comparison"]["causal_snapshot_unchanged"]


def test_p720_index_wrong_epoch_refused(tmp_path):
    root, index, doc = fixture(tmp_path)
    doc["historical_epoch_id"] = "f" * 64
    save(index, doc)
    with pytest.raises(FeatureModelError, match="Immutable historical epoch"):
        assess(index, root)


def test_p720_policy_tamper_rejected(tmp_path):
    bad = tmp_path / "changed_policy.json"
    bad.write_bytes(POLICY.read_bytes() + b" ")
    with pytest.raises(FeatureModelError, match="clock policy SHA-256 mismatch"):
        assess(policy=bad)


def test_p720_record_duplicate_id_rejected(tmp_path):
    root, index, doc = fixture(tmp_path)
    doc["features"][0]["record_id"] = "tick_01"
    save(index, doc)
    with pytest.raises(FeatureModelError, match="Duplicate source/feature"):
        assess(index, root)


def test_p720_duplicate_json_key_rejected(tmp_path):
    root, index, _ = fixture(tmp_path)
    index.write_text(
        '{"schema_version":1,"schema_version":1}',
        encoding="utf-8",
    )
    with pytest.raises(FeatureModelError, match="Duplicate JSON"):
        assess(index, root)


@pytest.mark.parametrize("path", [
    "../private.bin",
    "/tmp/secret.bin",
    "C:/accounts.bin",
    "a//b",
    "a/../b",
    "./a",
    "folder\\file.dat",
    "account_number_123.dat",
    "user@example.com",
])
def test_p720_unsafe_artifact_path_rejected(tmp_path, path):
    root, index, doc = fixture(tmp_path)
    overwrite(index, doc, "sources", 0, artifact_path=path)
    with pytest.raises(FeatureModelError, match="Unsafe or sensitive"):
        assess(index, root)


def test_p720_symlink_artifact_rejected_if_supported(tmp_path):
    root, index, doc = fixture(tmp_path)
    link = root / "bytes" / "pointer.bin"
    try:
        link.symlink_to(root / "bytes" / "tick_01.dat")
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation unavailable on Windows")
    overwrite(
        index, doc, "sources", 0,
        artifact_path="bytes/pointer.bin",
    )
    with pytest.raises(FeatureModelError, match="Symlink"):
        assess(index, root)


def test_p720_bad_src_field_rejected(tmp_path):
    root, index, doc = fixture(tmp_path)
    overwrite(
        index, doc, "sources", 0,
        approved_for_council=True,
    )
    with pytest.raises(FeatureModelError, match="Strict schema"):
        assess(index, root)


def test_p720_only_readonly_no_execution_side_effects(tmp_path):
    root, index, _ = fixture(tmp_path, full=True, with_model=True)
    report = assess(index, root)
    for name in (
        "files_written",
        "historical_source_modified",
        "network_accessed",
        "broker_connected",
        "collector_started",
        "council_invoked",
        "training_authorized",
        "settlement_invoked",
        "paper_orders_authorized",
        "live_orders_authorized",
        "forward_epoch_created",
        "operator_approval_granted",
        "G06_independently_verified",
    ):
        assert report[name] is False
    assert report["read_only"] is True
    assert report["final_action"] == "HOLD"
    assert report["point_in_time_snapshot"][
        "seat_preflight"
    ][0]["seat_dispatched"] is False


def test_p720_cli_no_packet_hold(capsys):
    code = run([
        "--read-only-features",
        "--read-only-models",
        "--epoch-id", EPOCH,
        "--as-of", ASOF,
        "--policy-file", str(POLICY),
    ])
    assert code == 0
    printed = capsys.readouterr().out
    assert "13/6" in printed
    assert "G06" in printed and "FINAL=HOLD" in printed


def test_p720_cli_json(capsys):
    code = run([
        "--read-only-features",
        "--read-only-models",
        "--epoch-id", EPOCH,
        "--as-of", ASOF,
        "--policy-file", str(POLICY),
        "--json",
    ])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["point_in_time_snapshot"]["context_fields"]) == 19
    assert report["final_action"] == "HOLD"


def test_p720_cli_mandatory_readonly_flags():
    with pytest.raises(SystemExit) as exc:
        run(["--epoch-id", EPOCH, "--as-of", ASOF])
    assert exc.value.code == 2


def test_p720_cli_future_cutoff_blocked(capsys):
    future = (
        datetime.now(timezone.utc) + timedelta(days=1)
    ).isoformat()
    assert run([
        "--read-only-features",
        "--read-only-models",
        "--epoch-id", EPOCH,
        "--as-of", future,
        "--policy-file", str(POLICY),
    ]) == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_p720_root_without_index_refused(tmp_path):
    with pytest.raises(FeatureModelError, match="root without index"):
        assess(root=tmp_path)


def test_p720_replay_without_primary_index_refused(tmp_path):
    root, index, _ = fixture(tmp_path)
    with pytest.raises(FeatureModelError, match="requires primary"):
        assess(replay_index=index, replay_root=root)


def test_p720_bad_historical_epoch_refused():
    with pytest.raises(FeatureModelError, match="rebinding refused"):
        assess_stage7_feature_model(
            epoch_id="f" * 64,
            as_of=ASOF,
            policy_file=POLICY,
        )


@pytest.mark.parametrize("bad", [
    "2026-10-30T11:00:00",
    "2026-10-30T14:00:00+03:00",
    "2026-10-30T10:00:60Z",
    "2026-11-31T10:00:00Z",
    "notutc",
])
def test_p720_utc_strict_zero_offset(bad):
    with pytest.raises(FeatureModelError):
        _utc(bad)


def test_p720_original_policy_pin_in_report():
    assert assess()["historical_clock_policy_sha256"] == POLICY_SHA256


# Additional model pins and transitive content-addressing tests.

def test_p719_model_feature_version_pins_mismatch_quarantined(tmp_path):
    root, index, doc = fixture(tmp_path, with_model=True, full=True)
    doc["model"]["feature_version_pins"]["feature_vector"] = "f" * 64
    save(index, doc)
    review = assess(index, root)[
        "point_in_time_snapshot"
    ]["model_compatibility"]
    assert "MODEL_PINNED_FEATURE_VERSION_MISMATCH" in review["issues"]
    assert not review["local_compatibility"]


def test_p719_model_missing_required_pin_rejected(tmp_path):
    root, index, doc = fixture(tmp_path, with_model=True, full=True)
    doc["model"]["feature_version_pins"].pop("tick_features")
    save(index, doc)
    with pytest.raises(FeatureModelError, match="pins must cover"):
        assess(index, root)


def test_p720_changed_direct_feature_ancestry_detected_even_when_roots_match(tmp_path):
    root, index, doc = fixture(tmp_path, full=True)
    baseline = tmp_path / "baseline.json"
    save(baseline, doc)
    doc["features"][1]["parent_ids"] = ["market_state_01"]
    save(index, doc)
    result = assess(
        baseline, root,
        replay_index=index,
        replay_root=root,
    )
    assert result["replay_comparison"]["status"] == (
        "VISIBLE_CAUSAL_INPUT_CHANGED_QUARANTINED"
    )


def test_p720_changed_model_build_timestamp_detected(tmp_path):
    root, index, doc = fixture(tmp_path, full=True, with_model=True)
    baseline = tmp_path / "baseline.json"
    save(baseline, doc)
    doc["model"]["built_at"] = stamp(17)
    save(index, doc)
    result = assess(
        baseline, root,
        replay_index=index,
        replay_root=root,
    )
    assert result["replay_comparison"]["status"] == (
        "VISIBLE_CAUSAL_INPUT_CHANGED_QUARANTINED"
    )


def test_p717_transformation_code_must_be_parsed_when_seen_first_as_artifact(tmp_path):
    root, index, doc = fixture(tmp_path)
    broken = b"def not_python(: \n"
    (root / "code/derived_transform.py").write_bytes(broken)
    doc["sources"][0]["artifact_path"] = "code/derived_transform.py"
    doc["sources"][0]["artifact_sha256"] = sha256(broken).hexdigest()
    doc["features"][0]["transform_sha256"] = sha256(broken).hexdigest()
    save(index, doc)
    with pytest.raises(FeatureModelError, match="AST invalid"):
        assess(index, root)


def test_p720_visible_supersession_does_not_silently_repoint_feature(tmp_path):
    root, index, doc = fixture(tmp_path)
    new = source(
        "tick_02", event="evt_01",
        published=5, available=6, received=7,
    )
    new["kind"] = "OBSERVATION"
    append_valid(root, index, doc, new, "sources")
    issues = field(
        assess(index, root), "tick_features"
    )["issues"]
    assert "PINNED_PARENT_VINTAGE_SUPERSEDED" in issues


def test_p720_revision_visible_after_cutoff_does_not_repoint(tmp_path):
    root, index, doc = fixture(tmp_path)
    baseline = tmp_path / "baseline.json"
    save(baseline, doc)
    new = source(
        "tick_02", event="evt_01",
        published=40, available=41, received=42,
    )
    new["observed_at"] = stamp(40)
    new["event_at"] = stamp(40)
    append_valid(root, index, doc, new, "sources")
    result = assess(
        baseline, root,
        replay_index=index,
        replay_root=root,
    )
    assert result["replay_comparison"]["causal_snapshot_unchanged"] is True

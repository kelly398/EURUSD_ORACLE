
"""P8-05..08: synthetic-only M5 market context, vintage and 31-seat abstention.

All evidence and transforms are temporary local files. No network, broker,
collector, model, Council invocation, prediction registration or order path.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

from backend.runtime.stage8_m5_offline_heartbeat import _lineage_key
from backend.runtime.stage8_point_in_time_market_state import (
    EPOCH, POLICY_SHA256, FAMILIES, DERIVED_INPUTS, FEATURE_SCHEMA,
    VERSION, REGIME_SPEC, STALE_CODES, MarketStateError, _digest, _utc,
    assess_stage8_market_state, run,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "docs/clock_profiles/hfm_forward_20261027.json"
CONTRACTS = ROOT / "backend/agents/contracts.py"
MANIFEST = ROOT / "backend/agents/council_manifest.py"
ASOF = "2026-10-30T11:00:00Z"
DECISION = "2026-10-30T10:00:20Z"
BAR_CLOSE = "2026-10-30T10:00:00Z"
SOURCE = sha256(b"UNVERIFIED_PROVIDER").hexdigest()
SESSION = sha256(b"UNVERIFIED_SESSION").hexdigest()
CODE = b"def transform(values):\n    return values\n"


def stamp(seconds):
    return (datetime(2026, 10, 30, 10, tzinfo=timezone.utc)
            + timedelta(seconds=seconds)).isoformat()


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), sort_keys=True), encoding="utf-8")


def heartbeat(tmp_path):
    root = tmp_path / "SYNTHETIC_HEARTBEAT"
    (root / "candles").mkdir(parents=True)
    payload = {
        "symbol": "EURUSD", "timeframe": "M5", "bar_open_at": "2026-10-30T09:55:00Z",
        "bar_close_at": BAR_CLOSE, "is_final": True, "open": "1.0830",
        "high": "1.0850", "low": "1.0820", "close": "1.0840", "tick_count": 100,
        "source_digest": SOURCE,
    }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    (root / "candles/m5.json").write_bytes(data)
    envelope = {
        "event_id": "SYNTHETIC_BAR_A", "epoch_id": EPOCH, "symbol": "EURUSD", "timeframe": "M5",
        "bar_open_at": "2026-10-30T09:55:00Z", "bar_close_at": BAR_CLOSE,
        "version_id": "V1", "source_digest": SOURCE,
        "source_admission_claim": "SYNTHETIC_FIXTURE_ONLY",
        "first_available_at": stamp(1), "received_at": stamp(3), "watermark_at": stamp(2),
        "clock_uncertainty_ms": "50.000",
        "session_context": {
            "session_id": "SYNTHETIC_SESSION", "session_state": "OPEN", "holiday_state": "CLEAR",
            "published_at": stamp(-120), "first_available_at": stamp(-60),
            "source_digest": SESSION,
        },
        "candle_relative_path": "candles/m5.json", "candle_sha256": sha256(data).hexdigest(),
    }
    doc = {
        "schema_version": 1, "historical_epoch_id": EPOCH, "packet_id": "SYNTHETIC_HEARTBEAT_PACKET",
        "as_of": ASOF, "mode": "SYNTHETIC_OFFLINE", "initial_watermark_at": None,
        "envelopes": [envelope],
    }
    index = tmp_path / "heartbeat_index.json"
    save(index, doc)
    return root, index, doc


def bars():
    return [
        {"close_at": "2026-10-30T09:50:00Z", "open": "1.0810", "high": "1.0820", "low": "1.0810", "close": "1.0815"},
        {"close_at": "2026-10-30T09:55:00Z", "open": "1.0815", "high": "1.0830", "low": "1.0810", "close": "1.0825"},
        {"close_at": "2026-10-30T10:00:00Z", "open": "1.0830", "high": "1.0850", "low": "1.0820", "close": "1.0840"},
    ]


def facts(label, state="PRESENT"):
    if state == "MISSING":
        return {}
    lookup = {
        "market_state": {"trend": "UP", "volatility": "HIGH", "session": "OPEN", "liquidity": "TIGHT"},
        "candles": {"bars": bars()},
        "ticks": {"bid": "1.0830", "ask": "1.0831", "tick_at": BAR_CLOSE},
        "cot_data": {"net_position": "123"},
        "ecb_data": {"policy_rate_pct": "2.5"},
        "fed_data": {"policy_rate_pct": "4.5"},
        "economic_calendar": {"event_risk": "HIGH", "phase": "SCHEDULE", "release_at": "2026-10-30T11:00:00Z"},
        "cross_asset_data": {"risk_mode": "RISK_ON", "quote_at": BAR_CLOSE},
        "calendar_coverage": {"event_risk": "HIGH", "coverage": "COMPLETE"},
        "risk_state": {"risk_mode": "CLEAR"},
        "risk_limits": {"risk_budget_pct": "1.0"},
        "reliability": {"score": "0.9"},
        "synthesis_policy": {"mode": "RESEARCH_ONLY"},
    }
    if label in DERIVED_INPUTS:
        return {"feature_version_sha256": sha256(label.encode()).hexdigest()}
    return lookup[label]


def row(label, *, rid=None, state="PRESENT", parent_ids=(), available=5,
        event_at=None, published=None, kind=None, event_id="event_one"):
    rid = rid or label + "_01"
    derived = label in DERIVED_INPUTS or label in ("market_state", "calendar_coverage", "risk_state")
    channel = "DERIVED_TRANSFORM" if derived else "OPERATOR_POLICY" if label in ("risk_limits", "synthesis_policy") else "EVALUATION_LOG" if label == "reliability" else "DIRECT_PROVIDER"
    record_type = "FEATURE" if label in DERIVED_INPUTS else "SOURCE"
    if kind is None:
        kind = "DERIVED_FEATURE" if record_type == "FEATURE" else "DERIVED" if derived else "OPERATOR_POLICY" if channel == "OPERATOR_POLICY" else "EVALUATION" if channel == "EVALUATION_LOG" else "SCHEDULE" if label == "economic_calendar" else "ACTUAL" if label in ("cot_data", "ecb_data", "fed_data") else "OBSERVATION"
    if event_at is None:
        event_at = "2026-10-30T11:00:00Z" if label == "economic_calendar" and kind == "SCHEDULE" else BAR_CLOSE
    published = stamp(available - 2) if published is None else published
    return {
        "record_id": rid, "record_type": record_type, "label": label,
        "event_id": event_id, "vintage_id": "v_" + rid,
        "publication_kind": kind, "provider_claim": "SYNTHETIC_UNVERIFIED",
        "event_at": event_at, "observed_at": published, "published_at": published,
        "first_available_at": stamp(available - 1), "received_at": stamp(available),
        "supersedes_id": None, "parent_ids": list(parent_ids),
        "transform_relative_path": "code/transform.py" if derived else None,
        "transform_sha256": "0" * 64 if derived else None,
        "feature_schema_version": FEATURE_SCHEMA if record_type == "FEATURE" else None,
        "source_channel": channel, "value_state": state,
        "relative_path": "payload/" + rid + ".json",
        "artifact_sha256": "0" * 64,
        "revocation_claim": False,
    }


def context(tmp_path, records=()):
    root = tmp_path / "UNVERIFIED_MARKET_CONTEXT"
    (root / "payload").mkdir(parents=True)
    (root / "code").mkdir()
    (root / "code/transform.py").write_bytes(CODE)
    doc = {
        "schema_version": 1, "historical_epoch_id": EPOCH,
        "packet_id": "SYNTHETIC_CONTEXT_PACKET", "as_of": ASOF,
        "decision_at": DECISION, "decision_key": _lineage_key(_utc(BAR_CLOSE)),
        "mode": "SYNTHETIC_OFFLINE", "records": list(records),
    }
    for item in doc["records"]:
        if item["transform_relative_path"]:
            item["transform_sha256"] = sha256(CODE).hexdigest()
        payload = {"schema_version": 1, "label": item["label"],
                   "value_state": item["value_state"], "facts": facts(item["label"], item["value_state"])}
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        (root / item["relative_path"]).write_bytes(data)
        item["artifact_sha256"] = sha256(data).hexdigest()
    index = tmp_path / "context_index.json"
    save(index, doc)
    return root, index, doc


def edit(index, doc, n, **changes):
    doc["records"][n].update(changes)
    save(index, doc)


def change_payload(root, index, doc, n, **updates):
    r = doc["records"][n]
    target = root / r["relative_path"]
    payload = json.loads(target.read_text("utf-8"))
    payload["facts"].update(updates)
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    target.write_bytes(data)
    r["artifact_sha256"] = sha256(data).hexdigest()
    save(index, doc)


def append(root, index, doc, entry):
    if entry["transform_relative_path"]:
        entry["transform_sha256"] = sha256(CODE).hexdigest()
    content = json.dumps({"schema_version": 1, "label": entry["label"],
                          "value_state": entry["value_state"],
                          "facts": facts(entry["label"], entry["value_state"])},
                         sort_keys=True, separators=(",", ":")).encode()
    (root / entry["relative_path"]).write_bytes(content)
    entry["artifact_sha256"] = sha256(content).hexdigest()
    doc["records"].append(entry)
    save(index, doc)


def assess(index=None, root=None, *, hb_index=None, hb_root=None,
           bar_close=BAR_CLOSE, decision=DECISION, replay_index=None, replay_root=None,
           policy=POLICY, contracts=CONTRACTS, manifest=MANIFEST, asof=ASOF):
    return assess_stage8_market_state(
        epoch_id=EPOCH, as_of=asof, decision_at=decision,
        bar_close_at=bar_close if index is not None else None,
        context_index=index, evidence_root=root,
        heartbeat_index=hb_index, heartbeat_root=hb_root,
        replay_index=replay_index, replay_root=replay_root,
        policy_file=policy, contracts_file=contracts, manifest_file=manifest,
    )


def field(report, label):
    return next(r for r in report["context"]["context_fields"] if r["label"] == label)


def seat(report, label):
    return next(r for r in report["context"]["seat_input_preflight"] if r["seat_id"] == label)


def setup(tmp_path, records=None):
    if records is None:
        records = [
            row("candles"), row("ticks"),
            row("market_state", parent_ids=("candles_01", "ticks_01"), available=13),
            row("economic_calendar", available=-70),
            row("calendar_coverage", parent_ids=("economic_calendar_01",), available=16),
        ]
    cr, ci, doc = context(tmp_path, records)
    hr, hi, hb = heartbeat(tmp_path)
    return cr, ci, doc, hr, hi, hb


def checked(tmp_path, records=None):
    cr, ci, doc, hr, hi, hb = setup(tmp_path, records)
    return assess(ci, cr, hb_index=hi, hb_root=hr)


# P8-05: thirteen explicit families + six derived labels at a decision cutoff.

def test_p805_empty_packet_thirteen_families_and_six_derived():
    report = assess()
    fields = report["context"]["context_fields"]
    assert len(fields) == 19
    assert len([x for x in fields if x["category"] == "SOURCE"]) == 13
    assert all(x["status"] == "NOT_SUPPLIED" for x in fields)
    assert report["context"]["context_sha256"] is None
    assert report["final_action"] == "HOLD"


def test_p805_real_31_seat_manifest_is_static_not_executed():
    report = assess()
    assert report["context"]["seat_count"] == 31
    assert [r["seat_number"] for r in report["context"]["seat_input_preflight"]] == list(range(1, 32))
    assert all(s["decision"] == "ABSTAIN" and not s["seat_dispatched"] for s in report["context"]["seat_input_preflight"])


def test_p805_synthetic_causal_families_only_local_candidates(tmp_path):
    report = checked(tmp_path)
    assert report["context"]["local_candidate_family_count"] == 5
    assert field(report, "candles")["status"] == "LOCAL_CANDIDATE_UNATTESTED"
    assert field(report, "fed_data")["status"] == "NOT_SUPPLIED"
    assert field(report, "synthesis_policy")["status"] == "NOT_SUPPLIED"
    assert report["independent_source_families_verified"] == 0
    assert report["independent_council_contexts_admitted"] == 0


def test_p805_same_decision_key_as_verified_synthetic_heartbeat(tmp_path):
    report = checked(tmp_path)
    assert report["context"]["decision_key"] == _lineage_key(_utc(BAR_CLOSE))
    assert report["heartbeat"]["eligible"] is True
    assert report["heartbeat"]["independently_admitted"] is False


def test_p805_packet_requires_prior_synthetic_heartbeat(tmp_path):
    cr, ci, doc = context(tmp_path, [row("ticks")])
    with pytest.raises(MarketStateError, match="requires synthetic heartbeat"):
        assess(ci, cr)


def test_p805_mismatched_decision_key_refused(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    doc["decision_key"] = "f" * 64
    save(ci, doc)
    with pytest.raises(MarketStateError, match="decision key"):
        assess(ci, cr, hb_index=hi, hb_root=hr)


def test_p805_missing_bar_close_refused(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    with pytest.raises(MarketStateError, match="requires completed"):
        assess_stage8_market_state(epoch_id=EPOCH, as_of=ASOF, decision_at=DECISION,
                                   context_index=ci, evidence_root=cr,
                                   heartbeat_index=hi, heartbeat_root=hr,
                                   policy_file=POLICY, contracts_file=CONTRACTS, manifest_file=MANIFEST)


def test_p805_unsupported_live_mode_refused(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    doc["mode"] = "LIVE_FEED"
    save(ci, doc)
    with pytest.raises(MarketStateError, match="offline mode"):
        assess(ci, cr, hb_index=hi, hb_root=hr)


def test_p805_missing_macro_never_imputed_zero(tmp_path):
    report = checked(tmp_path)
    assert field(report, "cot_data")["facts"] is None
    assert field(report, "cot_data")["fact_digest"] is None
    assert field(report, "reliability")["selected_vintage_id"] is None


def test_p805_explicit_missing_value_not_zero(tmp_path):
    report = checked(tmp_path, [row("fed_data", state="MISSING")])
    assert field(report, "fed_data")["status"] == "QUARANTINED"
    assert "EXPLICIT_MISSING_VALUE_NOT_ZERO" in field(report, "fed_data")["issues"]


def test_p805_future_published_macro_record_withheld(tmp_path):
    late = row("fed_data", available=90, published=stamp(88))
    cr, ci, _, hr, hi, _ = setup(tmp_path, [late])
    result = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert field(result, "fed_data")["status"] == "NOT_SUPPLIED"
    assert result["evidence"]["postdecision_records_withheld"] == 1


def test_p805_preannounced_calendar_schedule_visible(tmp_path):
    result = checked(tmp_path, [row("economic_calendar", available=-70)])
    assert field(result, "economic_calendar")["status"] == "LOCAL_CANDIDATE_UNATTESTED"
    assert result["context"]["regime_attribution"]["event_risk"]["value"] == "HIGH"


def test_p805_future_actual_not_counted_as_schedule(tmp_path):
    actual = row("economic_calendar", kind="ACTUAL", available=100,
                 event_at=stamp(90), published=stamp(98))
    cr, ci, doc, hr, hi, _ = setup(tmp_path, [actual])
    change_payload(cr, ci, doc, 0, phase="ACTUAL", release_at=stamp(90))
    result = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert field(result, "economic_calendar")["status"] == "NOT_SUPPLIED"
    assert result["context"]["regime_attribution"]["event_risk"]["value"] == "UNKNOWN"


def test_p805_artifact_sha_mismatch_rejected(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    (cr / doc["records"][0]["relative_path"]).write_bytes(b"MODIFIED")
    with pytest.raises(MarketStateError, match="SHA-256 mismatch"):
        assess(ci, cr, hb_index=hi, hb_root=hr)


def test_p805_heartbeat_partial_bar_blocks_context(tmp_path):
    cr, ci, doc, hr, hi, hb = setup(tmp_path)
    candle = hr / "candles/m5.json"
    payload = json.loads(candle.read_text("utf-8"))
    payload["is_final"] = False
    save(candle, payload)
    hb["envelopes"][0]["candle_sha256"] = sha256(candle.read_bytes()).hexdigest()
    save(hi, hb)
    with pytest.raises(MarketStateError, match="Heartbeat decision key"):
        assess(ci, cr, hb_index=hi, hb_root=hr)


# P8-06: fixed rule calculation, provenance, contradictions and revisions.

def test_p806_fixed_calculation_provides_five_attributions(tmp_path):
    report = checked(tmp_path)
    observed = {k: v["value"] for k, v in report["context"]["regime_attribution"].items()}
    assert observed == {"trend": "UP", "volatility": "HIGH", "liquidity": "TIGHT",
                        "session": "OPEN", "event_risk": "HIGH"}
    assert len(report["context"]["rule_spec_sha256"]) == 64
    assert report["context"]["rule_spec_sha256"] == _digest(REGIME_SPEC)


def test_p806_regime_has_source_ids_and_versioned_rules(tmp_path):
    report = checked(tmp_path)
    trend = report["context"]["regime_attribution"]["trend"]
    assert trend["source_record_ids"] == ["candles_01"]
    assert trend["status"] == "LOCAL_RULE_RESULT_UNATTESTED"
    assert trend["calculation_rule_version"].startswith("SYNTHETIC_M5")


def test_p806_changed_m5_window_recalculates_trend(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    baseline = assess(ci, cr, hb_index=hi, hb_root=hr)
    altered = bars()
    altered[0]["close"] = "1.0840"
    altered[0]["high"] = "1.0850"
    change_payload(cr, ci, doc, 0, bars=altered)
    # Opening window shifts but last close stays fixed, avoiding a retrospective price change.
    modified = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert baseline["context"]["regime_attribution"]["trend"]["value"] == "UP"
    assert modified["context"]["regime_attribution"]["trend"]["value"] == "FLAT"
    assert modified["context"]["context_sha256"] != baseline["context"]["context_sha256"]
    assert "MARKET_STATE_TREND_CONTRADICTION" in field(modified, "market_state")["issues"]


def test_p806_market_state_classification_mismatch_quarantined(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    change_payload(cr, ci, doc, 2, liquidity="WIDE")
    report = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert "MARKET_STATE_LIQUIDITY_CONTRADICTION" in field(report, "market_state")["issues"]
    assert field(report, "market_state")["status"] == "QUARANTINED"


def test_p806_calendar_coverage_conflict_quarantined(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    change_payload(cr, ci, doc, 4, event_risk="LOW")
    result = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert "CALENDAR_COVERAGE_EVENT_RISK_CONTRADICTION" in field(result, "calendar_coverage")["issues"]


def test_p806_tick_bid_outside_last_m5_range_quarantined(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    change_payload(cr, ci, doc, 1, bid="1.1000", ask="1.1002")
    result = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert "TICK_BID_OUTSIDE_M5_CANDLE" in field(result, "ticks")["issues"]


def test_p806_no_candle_or_tick_does_not_fabricate_trend_liquidity(tmp_path):
    report = checked(tmp_path, [row("fed_data")])
    regime = report["context"]["regime_attribution"]
    assert regime["trend"]["value"] == "UNKNOWN"
    assert regime["liquidity"]["value"] == "UNKNOWN"
    assert regime["session"]["value"] == "OPEN"


def test_p806_revision_available_at_decision_selected(tmp_path):
    original = row("fed_data", rid="fed_old", available=-120, event_at=stamp(-125))
    newer = row("fed_data", rid="fed_new", available=9, kind="REVISION")
    newer["supersedes_id"] = "fed_old"
    cr, ci, doc, hr, hi, _ = setup(tmp_path, [original, newer])
    report = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert field(report, "fed_data")["selected_vintage_id"] == "v_fed_new"
    assert field(report, "fed_data")["status"] == "LOCAL_CANDIDATE_UNATTESTED"


def test_p806_parallel_revision_without_supersession_quarantined(tmp_path):
    original = row("fed_data", rid="fed_old", available=-120, event_at=stamp(-125))
    newer = row("fed_data", rid="fed_new", available=9)
    cr, ci, doc, hr, hi, _ = setup(tmp_path, [original, newer])
    report = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert "UNDECLARED_PARALLEL_VINTAGE" in field(report, "fed_data")["issues"]


def test_p806_unseen_revision_does_not_change_earlier_version(tmp_path):
    original = row("fed_data", rid="fed_old")
    cr, ci, doc, hr, hi, _ = setup(tmp_path, [original])
    baseline = assess(ci, cr, hb_index=hi, hb_root=hr)
    newer = row("fed_data", rid="fed_new", kind="REVISION", available=40)
    newer["supersedes_id"] = "fed_old"
    append(cr, ci, doc, newer)
    result = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert field(result, "fed_data")["selected_vintage_id"] == "v_fed_old"
    assert baseline["context"]["context_sha256"] == result["context"]["context_sha256"]


def test_p806_derived_feature_missing_required_family_quarantined(tmp_path):
    report = checked(tmp_path, [row("tick_features", parent_ids=())])
    assert "REQUIRED_FEATURE_SOURCE_ANCESTRY_MISSING" in field(report, "tick_features")["issues"]


def test_p806_valid_derived_tick_feature_has_source_ancestry(tmp_path):
    records = [row("ticks"), row("tick_features", parent_ids=("ticks_01",), available=11)]
    report = checked(tmp_path, records)
    assert field(report, "tick_features")["status"] == "LOCAL_CANDIDATE_UNATTESTED"
    assert field(report, "tick_features")["source_ancestry"] == ["ticks_01"]


def test_p806_noncausal_derived_parent_quarantined(tmp_path):
    records = [row("ticks", available=17), row("tick_features", parent_ids=("ticks_01",), available=11)]
    report = checked(tmp_path, records)
    assert "NONCAUSAL_DERIVATION_TIMELINE" in field(report, "tick_features")["issues"]


def test_p806_revoked_source_propagates_to_feature(tmp_path):
    records = [row("ticks"), row("tick_features", parent_ids=("ticks_01",), available=11)]
    cr, ci, doc, hr, hi, _ = setup(tmp_path, records)
    edit(ci, doc, 0, revocation_claim=True)
    report = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert "LOCAL_REVOCATION_CLAIM" in field(report, "ticks")["issues"]
    assert "LOCAL_REVOCATION_CLAIM" in field(report, "tick_features")["issues"]


# P8-07: exact AST seat requirements, family-specific stale/abstention codes.

def test_p807_manifest_real_primary_seat_requires_actual_market_state_and_candles():
    report = assess()
    first = seat(report, "trend_master")
    assert first["stage"] == "PRIMARY"
    assert first["required_family_labels"] == ["candles", "market_state"]
    assert first["decision"] == "ABSTAIN"


def test_p807_all_seats_include_operator_hold_reason():
    assert all("G05_G06_G07_AND_STAGE8_OPERATOR_AUTHORIZATION_ABSENT" in x["reasons"]
               for x in assess()["context"]["seat_input_preflight"])


def test_p807_optional_families_not_treated_as_required_for_price_action():
    row_ = seat(assess(), "price_action")
    assert "candles" in row_["required_family_labels"]
    assert "fed_data" in row_["optional_family_absences"]
    assert "fed_data" not in row_["required_family_labels"]


def test_p807_missing_candles_prevents_price_action_local_compatibility():
    row_ = seat(assess(), "price_action")
    assert not row_["local_contract_compatible"]
    assert any("REQUIRED_INPUT_NOT_SUPPLIED:candles" == reason for reason in row_["reasons"])


@pytest.mark.parametrize("label,code", [
    ("ticks", "STALE_TICK_INPUT"), ("fed_data", "STALE_FED_INPUT"),
    ("cross_asset_data", "STALE_CROSS_ASSET_INPUT"),
    ("economic_calendar", "STALE_CALENDAR_INPUT"),
    ("risk_limits", "STALE_POLICY_INPUT"),
    ("synthesis_policy", "STALE_POLICY_INPUT"),
])
def test_p807_stale_family_reason_codes(tmp_path, label, code):
    item = row(label, available=-3000000)
    if label != "economic_calendar":
        item["event_at"] = stamp(-3000003)
    cr, ci, doc, hr, hi, _ = setup(tmp_path, [item])
    if label == "ticks":
        change_payload(cr, ci, doc, 0, tick_at=item["event_at"])
    if label == "cross_asset_data":
        change_payload(cr, ci, doc, 0, quote_at=item["event_at"])
    result = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert code in field(result, label)["issues"]


def test_p807_cross_asset_lag_contradiction_quarantined(tmp_path):
    tick = row("ticks")
    cross = row("cross_asset_data", event_at=stamp(-400), published=stamp(-399), available=5)
    cr, ci, doc, hr, hi, _ = setup(tmp_path, [tick, cross])
    change_payload(cr, ci, doc, 1, quote_at=stamp(-400))
    result = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert "CROSS_ASSET_TICK_TIMESTAMP_CONTRADICTION" in field(result, "cross_asset_data")["issues"]


def test_p807_missing_risk_policy_never_implicitly_approved(tmp_path):
    report = checked(tmp_path)
    risk = seat(report, "risk_officer")
    assert any("risk_limits" in reason for reason in risk["reasons"])
    assert risk["decision"] == "ABSTAIN"


def test_p807_synthesis_governance_missing_policy_explicit():
    strategist = seat(assess(), "chief_strategist")
    assert "synthesis_policy" in strategist["required_family_labels"]
    assert any("synthesis_policy" in x for x in strategist["reasons"])


def test_p807_complete_local_family_cannot_admit_or_dispatch_seat(tmp_path):
    records = [row("candles")]
    report = checked(tmp_path, records)
    result = seat(report, "price_action")
    assert result["local_contract_compatible"]
    assert result["decision"] == "ABSTAIN"
    assert not result["seat_dispatched"]


def test_p807_untrusted_model_signature_cannot_approve_packet(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    doc["independently_approved"] = True
    save(ci, doc)
    with pytest.raises(MarketStateError, match="Strict schema"):
        assess(ci, cr, hb_index=hi, hb_root=hr)


# P8-08: digest, replay, immutable source and malicious file controls.

def test_p808_same_context_packet_replays_identically(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    result = assess(ci, cr, hb_index=hi, hb_root=hr)
    repeat = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert result == repeat
    assert len(result["context"]["context_sha256"]) == 64
    assert result["report_sha256"] == _digest({k: v for k, v in result.items() if k != "report_sha256"})


def test_p808_explicit_replay_same_cutoff_identical(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    result = assess(ci, cr, hb_index=hi, hb_root=hr,
                    replay_index=ci, replay_root=cr)
    assert result["replay_comparison"]["status"] == "CAUSAL_CONTEXT_STABLE"
    assert result["replay_comparison"]["causal_context_unchanged"]


def test_p808_late_revision_does_not_rewrite_earlier_snapshot(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    baseline = tmp_path / "baseline.json"
    save(baseline, deepcopy(doc))
    future = row("fed_data", rid="fed_future", available=35)
    append(cr, ci, doc, future)
    result = assess(baseline, cr, hb_index=hi, hb_root=hr,
                    replay_index=ci, replay_root=cr)
    assert result["replay_comparison"]["status"] == "CAUSAL_CONTEXT_STABLE"
    assert result["replay_comparison"]["comparison_postdecision_withheld"] == 1


def test_p808_rewritten_visible_vintage_triggers_quarantine(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    baseline = tmp_path / "baseline.json"
    save(baseline, deepcopy(doc))
    edit(ci, doc, 0, vintage_id="REWRITTEN_V1")
    result = assess(baseline, cr, hb_index=hi, hb_root=hr,
                    replay_index=ci, replay_root=cr)
    assert result["replay_comparison"]["status"] == "VISIBLE_CONTEXT_CHANGED_QUARANTINED"
    assert "CAUSAL_REPLAY_CHANGED_PREVIOUS_DECISION_CONTEXT" in result["blockers"]


def test_p808_rehashed_visible_payload_change_quarantines_replay(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    baseline = tmp_path / "baseline.json"
    save(baseline, deepcopy(doc))
    replay_root = tmp_path / "REVISED_CONTEXT"
    shutil.copytree(cr, replay_root)
    # Update a new rooted copy so the historical original remains unchanged.
    replay_doc = deepcopy(doc)
    replay_index = tmp_path / "replay.json"
    change_payload(replay_root, replay_index, replay_doc, 0,
                   bars=[{**b, "close": "1.0835"} if i == 2 else b for i, b in enumerate(bars())])
    result = assess(baseline, cr, hb_index=hi, hb_root=hr,
                    replay_index=replay_index, replay_root=replay_root)
    assert result["replay_comparison"]["status"] == "VISIBLE_CONTEXT_CHANGED_QUARANTINED"


def test_p808_invalid_model_does_not_execute_anything(tmp_path):
    result = checked(tmp_path)
    for key in ("files_written", "network_accessed", "broker_connected", "collector_started",
                "council_invoked", "model_loaded", "training_authorized", "settlement_invoked",
                "paper_orders_authorized", "live_orders_authorized", "future_epoch_created",
                "operator_approval_granted", "historical_epoch_rebound"):
        assert result[key] is False
    assert result["read_only"] and result["final_action"] == "HOLD"


def test_p808_no_secret_data_or_source_paths_in_report(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    text = json.dumps(assess(ci, cr, hb_index=hi, hb_root=hr))
    assert "payload/candles_01.json" not in text
    assert "SYNTHETIC_UNVERIFIED" not in text
    assert "UNVERIFIED_MARKET_CONTEXT" not in text


def test_p808_code_transform_bytes_tamper_rejected(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    (cr / "code/transform.py").write_bytes(b"def changed():\n return None\n")
    with pytest.raises(MarketStateError, match="Transformation bytes SHA-256 mismatch"):
        assess(ci, cr, hb_index=hi, hb_root=hr)


def test_p808_invalid_transformation_ast_rejected(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    bad = b"def invalid(: pass"
    (cr / "code/transform.py").write_bytes(bad)
    for item in doc["records"]:
        if item["transform_relative_path"]:
            item["transform_sha256"] = sha256(bad).hexdigest()
    save(ci, doc)
    with pytest.raises(MarketStateError, match="Invalid transformation AST"):
        assess(ci, cr, hb_index=hi, hb_root=hr)


def test_p808_same_event_revision_cannot_self_supersede(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    edit(ci, doc, 0, supersedes_id="candles_01")
    with pytest.raises(MarketStateError, match="Undeclared supersession"):
        assess(ci, cr, hb_index=hi, hb_root=hr)


def test_p808_duplicate_id_rejected(tmp_path):
    a = row("ticks", rid="same")
    b = row("candles", rid="other")
    cr, ci, doc, hr, hi, _ = setup(tmp_path, [a, b])
    edit(ci, doc, 1, record_id="same")
    with pytest.raises(MarketStateError, match="Duplicate point-in-time"):
        assess(ci, cr, hb_index=hi, hb_root=hr)


def test_p808_duplicate_json_key_rejected(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    ci.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(MarketStateError, match="Duplicate JSON"):
        assess(ci, cr, hb_index=hi, hb_root=hr)


@pytest.mark.parametrize("unsafe", [
    "../leak.json", "/tmp/account.json", "C:/private.json", "a//b.json",
    "a/../b.json", "./candles.json", "folder\\file.json",
    "account_number_77.json", "user@example.com",
])
def test_p808_unsafe_relative_path_rejected(tmp_path, unsafe):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    edit(ci, doc, 0, relative_path=unsafe)
    with pytest.raises(MarketStateError, match="Unsafe or sensitive"):
        assess(ci, cr, hb_index=hi, hb_root=hr)


def test_p808_symlink_artifact_rejected_if_supported(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    link = cr / "payload/alias.json"
    try:
        link.symlink_to(cr / "payload/candles_01.json")
    except (OSError, NotImplementedError):
        pytest.skip("Windows symlink unavailable")
    edit(ci, doc, 0, relative_path="payload/alias.json")
    with pytest.raises(MarketStateError, match="Symlink"):
        assess(ci, cr, hb_index=hi, hb_root=hr)


def test_p808_policy_byte_tamper_rejected(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_bytes(POLICY.read_bytes() + b" ")
    with pytest.raises(MarketStateError, match="policy SHA-256 mismatch"):
        assess(policy=policy)


def test_p808_wrong_historical_epoch_refused():
    with pytest.raises(MarketStateError, match="rebinding refused"):
        assess_stage8_market_state(epoch_id="f" * 64, as_of=ASOF,
                                   policy_file=POLICY, contracts_file=CONTRACTS, manifest_file=MANIFEST)


def test_p808_wrong_index_epoch_refused(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    doc["historical_epoch_id"] = "f" * 64
    save(ci, doc)
    with pytest.raises(MarketStateError, match="Context epoch"):
        assess(ci, cr, hb_index=hi, hb_root=hr)


def test_p808_context_index_without_evidence_root_refused(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    with pytest.raises(MarketStateError, match="must be paired"):
        assess(index=ci)


def test_p808_replay_without_primary_index_refused(tmp_path):
    with pytest.raises(MarketStateError, match="Replay requires primary"):
        assess(replay_index=tmp_path / "any.json", replay_root=tmp_path)


@pytest.mark.parametrize("bad", [
    "2026-10-30T10:00:00", "2026-10-30T13:00:00+03:00",
    "2026-10-30T10:00:60Z", "2026-11-31T10:00:00Z", "invalid",
])
def test_p808_strict_utc(bad):
    with pytest.raises(MarketStateError):
        _utc(bad)


def test_p808_cli_no_packet_text_stays_hold(capsys):
    code = run(["--read-only-market-state", "--read-only-sources",
                "--epoch-id", EPOCH, "--as-of", ASOF,
                "--policy-file", str(POLICY), "--contracts-file", str(CONTRACTS),
                "--manifest-file", str(MANIFEST)])
    assert code == 0
    out = capsys.readouterr().out
    assert "13/6" in out and "Static seats: 31" in out
    assert "FINAL=HOLD" in out


def test_p808_cli_json_no_packet(capsys):
    assert run(["--read-only-market-state", "--read-only-sources", "--epoch-id", EPOCH,
                "--as-of", ASOF, "--policy-file", str(POLICY),
                "--contracts-file", str(CONTRACTS), "--manifest-file", str(MANIFEST),
                "--json"]) == 0
    obj = json.loads(capsys.readouterr().out)
    assert obj["context"]["seat_count"] == 31
    assert obj["final_action"] == "HOLD"


def test_p808_cli_flags_required():
    with pytest.raises(SystemExit) as exc:
        run(["--epoch-id", EPOCH, "--as-of", ASOF])
    assert exc.value.code == 2


def test_p808_cli_future_review_cutoff_blocked(capsys):
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    code = run(["--read-only-market-state", "--read-only-sources", "--epoch-id", EPOCH,
                "--as-of", tomorrow, "--policy-file", str(POLICY),
                "--contracts-file", str(CONTRACTS), "--manifest-file", str(MANIFEST)])
    assert code == 1
    assert "BLOCKED" in capsys.readouterr().out


# Additional high-risk boundaries: classification attribution and stale ancestry.

def test_p806_replay_explains_classification_change_and_changed_content(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    baseline = tmp_path / "explain_baseline.json"
    save(baseline, deepcopy(doc))
    newroot = tmp_path / "DIFFERENT_UNTRUSTED_SNAPSHOT"
    shutil.copytree(cr, newroot)
    later = deepcopy(doc)
    revised = bars()
    revised[0]["close"] = "1.0840"
    revised[0]["high"] = "1.0850"
    latest = tmp_path / "explain_later.json"
    change_payload(newroot, latest, later, 0, bars=revised)
    result = assess(baseline, cr, hb_index=hi, hb_root=hr,
                    replay_index=latest, replay_root=newroot)
    replay = result["replay_comparison"]
    assert replay["status"] == "VISIBLE_CONTEXT_CHANGED_QUARANTINED"
    assert "candles" in replay["changed_visible_family_or_feature_labels"]
    trend = next(x for x in replay["regime_classification_changes"]
                 if x["dimension"] == "trend")
    assert (trend["before"], trend["after"]) == ("UP", "FLAT")
    assert "INPUT_CONTENT_CHANGED" in trend["reason_codes"]


def test_p806_replay_late_unseen_revision_does_not_claim_regime_change(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path, [row("fed_data")])
    baseline = tmp_path / "unseen_baseline.json"
    save(baseline, deepcopy(doc))
    newer = row("fed_data", rid="fed_revision", event_id="event_one", available=40,
                kind="REVISION")
    newer["supersedes_id"] = "fed_data_01"
    append(cr, ci, doc, newer)
    report = assess(baseline, cr, hb_index=hi, hb_root=hr,
                    replay_index=ci, replay_root=cr)
    assert report["replay_comparison"]["status"] == "CAUSAL_CONTEXT_STABLE"
    assert report["replay_comparison"]["changed_visible_family_or_feature_labels"] == []
    assert report["replay_comparison"]["regime_classification_changes"] == []


def test_p806_derived_market_state_must_have_correct_parent_families(tmp_path):
    rows = [row("fed_data"), row("market_state", parent_ids=("fed_data_01",), available=13)]
    report = checked(tmp_path, rows)
    assert "DERIVED_SOURCE_PARENT_FAMILY_INCOMPLETE" in field(report, "market_state")["issues"]


def test_p807_quarantined_stale_tick_propagates_to_tick_feature(tmp_path):
    old = row("ticks", available=-500, event_at=stamp(-503))
    feat = row("tick_features", parent_ids=("ticks_01",), available=11)
    cr, ci, doc, hr, hi, _ = setup(tmp_path, [old, feat])
    change_payload(cr, ci, doc, 0, tick_at=stamp(-503))
    report = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert field(report, "ticks")["status"] == "QUARANTINED"
    assert "QUARANTINED_ANCESTOR_SOURCE" in field(report, "tick_features")["issues"]


def test_p805_calendar_actual_before_release_refused(tmp_path):
    r = row("economic_calendar", kind="ACTUAL", event_at=stamp(-20))
    cr, ci, doc, hr, hi, _ = setup(tmp_path, [r])
    change_payload(cr, ci, doc, 0, phase="ACTUAL", release_at=stamp(120))
    with pytest.raises(MarketStateError, match="actual before its scheduled release"):
        assess(ci, cr, hb_index=hi, hb_root=hr)


def test_p805_source_mutation_does_not_update_original_historical_policy(tmp_path):
    policy_before = sha256(POLICY.read_bytes()).hexdigest()
    _ = checked(tmp_path)
    assert sha256(POLICY.read_bytes()).hexdigest() == policy_before == POLICY_SHA256


def test_p808_context_hash_includes_manifest_version(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    original = assess(ci, cr, hb_index=hi, hb_root=hr)
    modified = tmp_path / "manifest_comment.py"
    modified.write_bytes(MANIFEST.read_bytes() + b"\n# synthetic whitespace change\n")
    candidate = assess(ci, cr, hb_index=hi, hb_root=hr, manifest=modified)
    assert candidate["context"]["manifest_sha256"] != original["context"]["manifest_sha256"]
    assert candidate["context"]["context_sha256"] != original["context"]["context_sha256"]
    assert candidate["final_action"] == "HOLD"


def test_p808_context_hash_includes_feature_version(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path, [
        row("ticks"), row("tick_features", parent_ids=("ticks_01",), available=11),
    ])
    original = assess(ci, cr, hb_index=hi, hb_root=hr)
    feature_before = field(original, "tick_features")["fact_digest"]
    change_payload(cr, ci, doc, 1, feature_version_sha256="f" * 64)
    new = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert field(new, "tick_features")["fact_digest"] != feature_before
    assert new["context"]["context_sha256"] != original["context"]["context_sha256"]


def test_p808_multiple_visible_calendar_events_conservatively_quarantined(tmp_path):
    a = row("economic_calendar", rid="event_a", event_id="a", available=-70)
    b = row("economic_calendar", rid="event_b", event_id="b", available=-55)
    report = checked(tmp_path, [a, b])
    assert "MULTIPLE_VISIBLE_EVENTS_UNRESOLVED" in field(report, "economic_calendar")["issues"]
    assert report["context"]["regime_attribution"]["event_risk"]["value"] == "UNKNOWN"


def test_p808_replay_earlier_field_not_supplied_even_with_later_evidence(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path, [row("ticks")])
    baseline = tmp_path / "old_index.json"
    save(baseline, deepcopy(doc))
    later = row("fed_data", available=40)
    append(cr, ci, doc, later)
    report = assess(baseline, cr, hb_index=hi, hb_root=hr,
                    replay_index=ci, replay_root=cr)
    assert report["replay_comparison"]["causal_context_unchanged"] is True
    assert field(report, "fed_data")["status"] == "NOT_SUPPLIED"


def test_p808_provider_claim_metadata_is_bound_without_exporting_provider(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    first = assess(ci, cr, hb_index=hi, hb_root=hr)
    edit(ci, doc, 0, provider_claim="ANOTHER_UNVERIFIED_PROVIDER")
    second = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert first["context"]["context_sha256"] != second["context"]["context_sha256"]
    assert field(first, "candles")["provenance_digest"] != field(second, "candles")["provenance_digest"]
    assert "ANOTHER_UNVERIFIED_PROVIDER" not in json.dumps(second)


def test_p808_received_timestamp_change_is_bound_to_context(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    first = assess(ci, cr, hb_index=hi, hb_root=hr)
    edit(ci, doc, 1, received_at=stamp(8))
    second = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert field(first, "ticks")["received_at"] != field(second, "ticks")["received_at"]
    assert first["context"]["context_sha256"] != second["context"]["context_sha256"]


def test_p808_reordered_index_does_not_change_causal_context(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    initial = assess(ci, cr, hb_index=hi, hb_root=hr)
    doc["records"].reverse()
    save(ci, doc)
    reordered = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert initial["context"] == reordered["context"]
    assert initial["context"]["context_sha256"] == reordered["context"]["context_sha256"]
    assert initial["evidence"]["index_sha256"] != reordered["evidence"]["index_sha256"]


def test_p808_future_record_does_not_change_any_serialized_context_fields(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    initial = assess(ci, cr, hb_index=hi, hb_root=hr)
    append(cr, ci, doc, row("fed_data", rid="late_fed", available=40))
    later = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert initial["context"] == later["context"]
    assert later["evidence"]["postdecision_records_withheld"] == 1
    assert initial["evidence"]["postdecision_records_withheld"] == 0


def test_p805_m5_context_candle_must_match_validated_heartbeat(tmp_path):
    cr, ci, doc, hr, hi, _ = setup(tmp_path)
    modified = bars()
    modified[-1]["close"] = "1.0835"
    change_payload(cr, ci, doc, 0, bars=modified)
    report = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert "CANDLE_HEARTBEAT_OHLC_MISMATCH" in field(report, "candles")["issues"]
    assert field(report, "candles")["status"] == "QUARANTINED"
    assert report["context"]["regime_attribution"]["trend"]["value"] == "UNKNOWN"
    assert report["context"]["status"] == "LOCAL_CONTEXT_QUARANTINED"
    assert "_m5" not in json.dumps(report)


def test_p806_event_risk_is_unknown_outside_proposed_time_window(tmp_path):
    calendar = row("economic_calendar", available=-70,
                   event_at="2026-10-30T14:00:00Z")
    cr, ci, doc, hr, hi, _ = setup(tmp_path, [calendar])
    change_payload(cr, ci, doc, 0, release_at="2026-10-30T14:00:00Z")
    result = assess(ci, cr, hb_index=hi, hb_root=hr)
    assert field(result, "economic_calendar")["status"] == "LOCAL_CANDIDATE_UNATTESTED"
    assert result["context"]["regime_attribution"]["event_risk"]["value"] == "UNKNOWN"
    assert result["context"]["regime_attribution"]["event_risk"]["status"] == "NOT_SUPPLIED"

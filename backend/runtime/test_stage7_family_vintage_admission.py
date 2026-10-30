
"""P7-13..16: offline synthetic 13-family/vintage/source-admission tests.

No existing data is modified. pytest tmp_path contains all untrusted records.
Provider strings and hashes are local claims, never external attestations.
"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime.stage7_family_vintage_admission import (
    EPOCH, POLICY_SHA256, EXPECTED_FAMILIES, STATUSES,
    FamilyVintageError, _digest, _utc, assess_stage7_family_vintages,
    family_contracts, run,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "docs/clock_profiles/hfm_forward_20261027.json"
ASOF = "2026-10-30T12:00:00Z"
INDEX_ASOF = "2026-10-30T11:00:00Z"
DECISION = "2026-10-30T10:00:20Z"


def save(path, value):
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def record(
    family="ticks", kind="OBSERVATION", *, record_id="item_01",
    event_id="event_01", published="2026-10-30T10:00:02Z",
    available="2026-10-30T10:00:03Z",
    received="2026-10-30T10:00:04Z",
    event_at="2026-10-30T10:00:00Z",
    parents=(), supersedes=None,
):
    contracts = family_contracts()
    channel = contracts[family]["permitted_source_channels"][0]
    market = None

    if family == "ticks":
        market = {
            "symbol": "EURUSD",
            "bid": "1.08400",
            "ask": "1.08410",
            "tick_at": event_at,
        }

    if family == "candles":
        market = {
            "symbol": "EURUSD",
            "open": "1.08400",
            "high": "1.08500",
            "low": "1.08300",
            "close": "1.08450",
            "bar_open_at": "2026-10-30T09:55:00Z",
            "bar_close_at": "2026-10-30T10:00:00Z",
        }

    return {
        "record_id": record_id,
        "family": family,
        "provider_claim": "UNVERIFIED_PROVIDER_A",
        "owner_claim": "UNVERIFIED_RESEARCH_OWNER",
        "source_channel": channel,
        "publication_kind": kind,
        "event_id": event_id,
        "event_at": event_at,
        "scheduled_release_at": None,
        "published_at": published,
        "first_available_at": available,
        "received_at": received,
        "observed_at": published,
        "effective_at": None,
        "value_state": "PRESENT",
        "numeric_claim": None,
        "market_claim": market,
        "upstream_ids": list(parents),
        "supersedes_id": supersedes,
        "transform_code_sha256": (
            "a" * 64 if channel == "DERIVED_TRANSFORM" else None
        ),
        "artifact_relative_path": "payload/" + record_id + ".dat",
        "artifact_sha256": "0" * 64,
        "revocation_notice_claim": False,
    }


def fixture(tmp_path, *records):
    root = tmp_path / "LOCAL_UNVERIFIED_SOURCE_PACKET"
    (root / "payload").mkdir(parents=True)

    doc = {
        "schema_version": 1,
        "epoch_id": EPOCH,
        "packet_id": "SYNTHETIC_FAMILY_PACKET",
        "as_of": INDEX_ASOF,
        "records": list(records),
    }

    for rec in records:
        body = (
            "SYNTHETIC_UNATTESTED_BYTES_" + rec["record_id"]
        ).encode("utf-8")

        target = root / rec["artifact_relative_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        rec["artifact_sha256"] = sha256(body).hexdigest()

    index = tmp_path / "family_vintage_index.json"
    save(index, doc)
    return root, index, doc


def assess(index=None, root=None, *, decision=DECISION, asof=ASOF, policy=POLICY):
    return assess_stage7_family_vintages(
        epoch_id=EPOCH,
        as_of=asof,
        decision_at=decision,
        policy_file=policy,
        index_file=index,
        evidence_root=root,
    )


def family(report, name):
    return next(
        x for x in report["family_evidence_matrix"]
        if x["family"] == name
    )


def update(index, document, n, **values):
    document["records"][n].update(values)
    save(index, document)


# P7-13: exact canonical 13-family matrix and evidence ownership.

def test_p713_exact_stage6_family_set():
    contracts = family_contracts()
    assert tuple(contracts) == EXPECTED_FAMILIES
    assert len(contracts) == 13
    assert "reliability" in contracts
    assert "synthesis_policy" in contracts


def test_p713_no_index_does_not_claim_any_source():
    result = assess()
    assert result["family_count"] == 13
    assert all(
        x["status"] == "NOT_SUPPLIED"
        for x in result["family_evidence_matrix"]
    )
    assert (
        result["source_admission"]["families_without_eligible_local_content"]
        == 13
    )
    assert result["final_action"] == "HOLD"


def test_p713_all_source_classes_are_distinguished():
    rows = family_contracts()
    assert rows["ticks"]["source_class"] == "DIRECT_FEED"
    assert rows["market_state"]["source_class"] == "DERIVED_CONTEXT"
    assert rows["risk_limits"]["source_class"] == "OPERATOR_POLICY"
    assert rows["reliability"]["source_class"] == "EVALUATION_CONTEXT"
    assert rows["economic_calendar"]["source_class"] == "SCHEDULE_OR_ACTUAL"


def test_p713_every_family_has_owner_freshness_exception_and_proof():
    for name, row in family_contracts().items():
        assert name == row["family"]
        assert row["proposed_max_age_seconds"] > 0
        assert row["proposed_owner_role"]
        assert row["independent_evidence_requirements"]
        assert isinstance(row["source_specific_exceptions"], list)
        assert row["policy_status"] == "RESEARCH_DESIGN_NOT_OPERATOR_APPROVED"
        assert row["independent_proof_required"] is True


def test_p713_13_labels_are_not_13_independent_data_feeds():
    rows = family_contracts().values()
    direct = [
        r for r in rows
        if "DIRECT_PROVIDER" in r["permitted_source_channels"]
    ]
    assert len(direct) < 13
    assert len([
        r for r in rows if r["source_class"] == "OPERATOR_POLICY"
    ]) == 2


def test_p713_canonical_family_mismatch_rejected(tmp_path):
    root, index, doc = fixture(tmp_path, record())
    update(index, doc, 0, family="invented_macro")
    with pytest.raises(FamilyVintageError, match="canonical family"):
        assess(index, root)


def test_p713_wrong_source_channel_rejected(tmp_path):
    root, index, doc = fixture(tmp_path, record())
    update(index, doc, 0, source_channel="OPERATOR_POLICY")
    with pytest.raises(FamilyVintageError, match="Source channel"):
        assess(index, root)


def test_p713_policy_context_cannot_pose_as_broker_tick(tmp_path):
    row = record("risk_limits", "OPERATOR_POLICY")
    root, index, doc = fixture(tmp_path, row)
    update(index, doc, 0, publication_kind="OBSERVATION")
    with pytest.raises(FamilyVintageError, match="Publication kind"):
        assess(index, root)


def test_p713_derived_context_requires_transform_hash(tmp_path):
    row = record("market_state", "DERIVED")
    root, index, doc = fixture(tmp_path, row)
    update(index, doc, 0, transform_code_sha256=None)
    with pytest.raises(FamilyVintageError, match="transform version"):
        assess(index, root)


def test_p713_local_single_tick_is_only_candidate(tmp_path):
    root, index, doc = fixture(tmp_path, record())
    result = assess(index, root)
    entry = family(result, "ticks")
    assert entry["status"] == "LOCAL_CANDIDATE"
    assert entry["independent_attestation"] == "NOT_PROVIDED"
    assert entry["approved_for_council"] is False
    assert result["source_admission"]["families_with_local_candidates"] == 1


# P7-14: first-publication, postdecision revision, scheduled events.

def test_p714_announced_future_event_schedule_visible_without_actual(tmp_path):
    future = "2026-11-03T12:00:00Z"
    row = record("economic_calendar", "SCHEDULE", event_at=future)
    root, index, _ = fixture(tmp_path, row)

    result = assess(index, root)
    entry = family(result, "economic_calendar")
    assert entry["status"] == "LOCAL_CANDIDATE"
    assert entry["selected_publication_kinds"] == ["SCHEDULE"]
    assert result["local_evidence"]["post_decision_records_withheld"] == 0


def test_p714_actual_later_than_cutoff_is_withheld(tmp_path):
    row = record(
        "economic_calendar", "ACTUAL",
        published="2026-10-30T10:00:30Z",
        available="2026-10-30T10:00:31Z",
        received="2026-10-30T10:00:32Z",
        event_at="2026-10-30T10:00:25Z",
    )
    row["observed_at"] = row["published_at"]

    root, index, doc = fixture(tmp_path, row)
    report = assess(index, root)

    assert family(report, "economic_calendar")["status"] == "NOT_SUPPLIED"
    assert report["local_evidence"]["post_decision_records_withheld"] == 1
    assert report["source_admission"]["admitted_council_contexts"] == 0


def test_p714_announced_schedule_survives_later_actual(tmp_path):
    event = "2026-10-30T10:00:25Z"

    schedule = record(
        "economic_calendar", "SCHEDULE",
        record_id="schedule_01", event_at=event,
    )
    actual = record(
        "economic_calendar", "ACTUAL",
        record_id="actual_01", event_at=event,
        published="2026-10-30T10:00:30Z",
        available="2026-10-30T10:00:31Z",
        received="2026-10-30T10:00:32Z",
    )
    actual["observed_at"] = actual["published_at"]

    root, index, _ = fixture(tmp_path, schedule, actual)
    result = assess(index, root)
    entry = family(result, "economic_calendar")

    assert entry["status"] == "LOCAL_CANDIDATE"
    assert entry["selected_publication_kinds"] == ["SCHEDULE"]
    assert result["local_evidence"]["post_decision_records_withheld"] == 1


def test_p714_earlier_actual_survives_later_revision(tmp_path):
    a = record("cot_data", "ACTUAL", record_id="cot_original")
    b = record(
        "cot_data", "REVISION",
        record_id="cot_revision", supersedes="cot_original",
        published="2026-10-30T10:00:30Z",
        available="2026-10-30T10:00:31Z",
        received="2026-10-30T10:00:32Z",
    )
    b["observed_at"] = b["published_at"]

    root, index, doc = fixture(tmp_path, a, b)
    result = assess(index, root)

    assert family(result, "cot_data")["selected_publication_kinds"] == ["ACTUAL"]
    assert result["local_evidence"]["post_decision_records_withheld"] == 1


def test_p714_earlier_revision_selected_when_available(tmp_path):
    a = record("cot_data", "ACTUAL", record_id="cot_original")
    b = record(
        "cot_data", "REVISION",
        record_id="cot_revision", supersedes="cot_original",
        published="2026-10-30T10:00:10Z",
        available="2026-10-30T10:00:11Z",
        received="2026-10-30T10:00:12Z",
    )
    b["observed_at"] = b["published_at"]

    root, index, doc = fixture(tmp_path, a, b)
    result = assess(index, root)
    entry = family(result, "cot_data")

    assert entry["local_selected_vintages"] == 1
    assert entry["selected_publication_kinds"] == ["REVISION"]


def test_p714_revision_parent_not_in_index_quarantined(tmp_path):
    rev = record(
        "cot_data", "REVISION", supersedes="unknown_parent",
    )
    root, index, doc = fixture(tmp_path, rev)
    report = assess(index, root)
    entry = family(report, "cot_data")

    assert entry["status"] == "QUARANTINED"
    assert "REVISION_TARGET_MISSING_OR_INCOMPATIBLE" in entry["findings"]


def test_p714_revision_without_declared_parent_rejected(tmp_path):
    root, index, doc = fixture(
        tmp_path, record("cot_data", "REVISION"),
    )
    with pytest.raises(FamilyVintageError, match="Revision must declare"):
        assess(index, root)


def test_p714_future_effective_ecb_policy_visible(tmp_path):
    row = record(
        "ecb_data", "FUTURE_EFFECTIVE_POLICY",
        event_at="2026-11-03T12:00:00Z",
    )
    row["effective_at"] = "2026-11-03T12:00:00Z"
    root, index, _ = fixture(tmp_path, row)

    report = assess(index, root)
    entry = family(report, "ecb_data")
    assert entry["status"] == "LOCAL_CANDIDATE"
    assert entry["approved_for_council"] is False


def test_p714_future_effective_fed_policy_visible(tmp_path):
    row = record(
        "fed_data", "FUTURE_EFFECTIVE_POLICY",
        event_at="2026-11-03T12:00:00Z",
    )
    row["effective_at"] = "2026-11-03T12:00:00Z"
    root, index, _ = fixture(tmp_path, row)

    assert family(
        assess(index, root), "fed_data",
    )["status"] == "LOCAL_CANDIDATE"


def test_p714_future_effective_not_allowed_for_economic_calendar(tmp_path):
    root, index, doc = fixture(
        tmp_path, record("economic_calendar", "SCHEDULE"),
    )
    update(
        index, doc, 0,
        publication_kind="FUTURE_EFFECTIVE_POLICY",
    )
    with pytest.raises(FamilyVintageError, match="Publication kind"):
        assess(index, root)


def test_p714_revision_published_before_declared_release_quarantined(tmp_path):
    row = record("economic_calendar", "ACTUAL")
    row["scheduled_release_at"] = "2026-10-30T10:00:10Z"
    root, index, _ = fixture(tmp_path, row)

    assert "ACTUAL_BEFORE_DECLARED_RELEASE_TIME" in family(
        assess(index, root), "economic_calendar",
    )["findings"]


def test_p714_cot_position_after_publication_quarantined(tmp_path):
    row = record(
        "cot_data", "ACTUAL",
        event_at="2026-10-30T10:00:10Z",
    )
    root, index, _ = fixture(tmp_path, row)

    assert "COT_PUBLISHED_BEFORE_POSITION_DATE" in family(
        assess(index, root), "cot_data",
    )["findings"]


def test_p714_schedule_numeric_actual_smuggling_quarantined(tmp_path):
    row = record(
        "economic_calendar", "SCHEDULE",
        event_at="2026-11-03T12:00:00Z",
    )
    row["numeric_claim"] = "250.000"
    root, index, _ = fixture(tmp_path, row)

    assert "SCHEDULE_CANNOT_CONTAIN_ACTUAL_NUMERIC_RESULT" in family(
        assess(index, root), "economic_calendar",
    )["findings"]


def test_p714_first_available_after_cutoff_withheld(tmp_path):
    row = record(
        "economic_calendar", "ACTUAL",
        available="2026-10-30T10:00:30Z",
        received="2026-10-30T10:00:35Z",
    )
    root, index, _ = fixture(tmp_path, row)

    assert (
        assess(index, root)["local_evidence"]["post_decision_records_withheld"]
        == 1
    )


def test_p714_decision_may_not_exceed_review_cutoff():
    with pytest.raises(FamilyVintageError, match="decision after review"):
        assess(decision="2026-11-03T12:00:00Z")


# P7-15: cross-family coherence, age and missing-is-not-zero.

def test_p715_tick_bid_ask_invalid_rejected(tmp_path):
    root, index, doc = fixture(tmp_path, record())
    update(
        index, doc, 0,
        market_claim={
            **doc["records"][0]["market_claim"],
            "ask": "1.0700",
        },
    )
    with pytest.raises(FamilyVintageError, match="bid/ask"):
        assess(index, root)


def test_p715_bad_candle_five_minute_boundary_rejected(tmp_path):
    root, index, doc = fixture(
        tmp_path, record("candles", "OBSERVATION"),
    )
    update(
        index, doc, 0,
        market_claim={
            **doc["records"][0]["market_claim"],
            "bar_close_at": "2026-10-30T10:01:00Z",
        },
    )
    with pytest.raises(FamilyVintageError, match="M5 OHLC"):
        assess(index, root)


def test_p715_bad_candle_ohlc_rejected(tmp_path):
    root, index, doc = fixture(
        tmp_path, record("candles", "OBSERVATION"),
    )
    update(
        index, doc, 0,
        market_claim={
            **doc["records"][0]["market_claim"],
            "high": "1.00000",
        },
    )
    with pytest.raises(FamilyVintageError, match="M5 OHLC"):
        assess(index, root)


def test_p715_candle_published_before_bar_closed_rejected(tmp_path):
    root, index, doc = fixture(
        tmp_path, record("candles", "OBSERVATION"),
    )
    update(
        index, doc, 0,
        market_claim={
            **doc["records"][0]["market_claim"],
            "bar_open_at": "2026-10-30T10:00:00Z",
            "bar_close_at": "2026-10-30T10:05:00Z",
        },
    )
    with pytest.raises(
        FamilyVintageError, match="published before bar close",
    ):
        assess(index, root)


def test_p715_tick_market_time_mismatch_rejected(tmp_path):
    root, index, doc = fixture(tmp_path, record())
    update(
        index, doc, 0,
        market_claim={
            **doc["records"][0]["market_claim"],
            "tick_at": "2026-10-30T09:59:00Z",
        },
    )
    with pytest.raises(
        FamilyVintageError, match="market observation mismatch",
    ):
        assess(index, root)


def test_p715_direct_native_m5_is_not_derived_tick_claim(tmp_path):
    row = record("candles", "OBSERVATION")
    root, index, doc = fixture(tmp_path, row)
    report = assess(index, root)
    entry = family(report, "candles")

    assert entry["status"] == "LOCAL_CANDIDATE"
    assert entry["approved_for_council"] is False


def test_p715_derived_m5_parent_tick_not_in_bar_quarantined(tmp_path):
    tick = record(record_id="tick_01")
    candle = record(
        "candles", "DERIVED",
        record_id="m5_01", parents=["tick_01"],
    )
    candle["source_channel"] = "DERIVED_TRANSFORM"
    candle["transform_code_sha256"] = "a" * 64

    root, index, _ = fixture(tmp_path, tick, candle)
    assert "TICK_OUTSIDE_M5_BAR_WINDOW" in family(
        assess(index, root), "candles",
    )["findings"]


def test_p715_derived_m5_without_tick_parent_quarantined(tmp_path):
    candle = record(
        "candles", "DERIVED", record_id="m5_01",
    )
    candle["source_channel"] = "DERIVED_TRANSFORM"
    candle["transform_code_sha256"] = "a" * 64

    root, index, _ = fixture(tmp_path, candle)
    assert "DERIVED_M5_TICK_ANCESTRY_MISSING" in family(
        assess(index, root), "candles",
    )["findings"]


def test_p715_stale_tick_research_threshold_quarantined(tmp_path):
    root, index, _ = fixture(tmp_path, record())
    result = assess(
        index, root, decision="2026-10-30T10:04:00Z",
    )
    assert "SOURCE_FRESHNESS_EXCEEDS_PROPOSED_LIMIT" in family(
        result, "ticks",
    )["findings"]


def test_p715_missing_value_remains_missing_not_zero(tmp_path):
    row = record("cot_data", "ACTUAL")
    row["value_state"] = "MISSING"

    root, index, _ = fixture(tmp_path, row)
    result = family(assess(index, root), "cot_data")

    assert result["status"] == "NOT_SUPPLIED"
    assert result["missing_value_vintages"] == 1
    assert result["present_value_vintages"] == 0
    assert result["findings"] == []


def test_p715_missing_value_with_zero_numeric_refused(tmp_path):
    row = record("cot_data", "ACTUAL")
    row["value_state"] = "MISSING"
    row["numeric_claim"] = "0"

    root, index, _ = fixture(tmp_path, row)
    with pytest.raises(FamilyVintageError, match="Missing value"):
        assess(index, root)


def test_p715_actual_zero_value_is_present_not_missing(tmp_path):
    row = record("cot_data", "ACTUAL")
    row["numeric_claim"] = "0"

    root, index, _ = fixture(tmp_path, row)
    assert family(
        assess(index, root), "cot_data",
    )["status"] == "LOCAL_CANDIDATE"


def test_p715_unlinked_derived_context_quarantined(tmp_path):
    row = record("calendar_coverage", "DERIVED")
    root, index, _ = fixture(tmp_path, row)

    assert "DERIVED_OR_EVALUATION_ANCESTRY_MISSING" in family(
        assess(index, root), "calendar_coverage",
    )["findings"]


def test_p715_unavailable_derived_parent_quarantined(tmp_path):
    parent = record(
        "economic_calendar", "ACTUAL",
        record_id="actual_01",
        published="2026-10-30T10:00:30Z",
        available="2026-10-30T10:00:31Z",
        received="2026-10-30T10:00:32Z",
    )
    parent["observed_at"] = parent["published_at"]

    child = record(
        "calendar_coverage", "DERIVED",
        record_id="coverage_01",
        parents=["actual_01"],
    )

    root, index, _ = fixture(tmp_path, parent, child)
    assert "UPSTREAM_SOURCE_NOT_CAUSALLY_AVAILABLE" in family(
        assess(index, root), "calendar_coverage",
    )["findings"]


def test_p715_cross_asset_async_to_tick_quarantined(tmp_path):
    tick = record(record_id="tick_01")
    cross = record(
        "cross_asset_data", "OBSERVATION",
        record_id="cross_01",
        published="2026-10-30T09:57:50Z",
        available="2026-10-30T09:57:51Z",
        received="2026-10-30T09:57:52Z",
        event_at="2026-10-30T09:57:50Z",
    )
    cross["observed_at"] = cross["published_at"]

    root, index, _ = fixture(tmp_path, tick, cross)
    result = assess(index, root)

    assert "CROSS_ASSET_NOT_SYNCHRONOUS_TO_EURUSD" in family(
        result, "cross_asset_data",
    )["findings"]


def test_p715_future_broker_observation_withheld(tmp_path):
    tick = record(
        event_at="2026-10-30T10:00:30Z",
        published="2026-10-30T10:00:30Z",
        available="2026-10-30T10:00:31Z",
        received="2026-10-30T10:00:32Z",
    )
    tick["observed_at"] = tick["published_at"]

    root, index, _ = fixture(tmp_path, tick)
    result = assess(index, root)

    assert family(result, "ticks")["status"] == "NOT_SUPPLIED"
    assert result["local_evidence"]["post_decision_records_withheld"] == 1


# P7-16: local review packet, revocation, strict custody and HOLD.

def test_p716_all_five_status_semantics_defined():
    result = assess()
    assert tuple(result["status_semantics"]) == STATUSES
    assert result["source_admission"]["reviewer_signature"] is None


def test_p716_local_parser_never_issues_independent_attestation(tmp_path):
    root, index, _ = fixture(tmp_path, record())
    result = assess(index, root)

    assert result["source_admission"]["G05_independently_verified"] is False
    assert result["council_families_independently_verified"] == 0
    assert result["external_gates_independently_verified"] == 0
    assert all(
        x["status"] not in ("REVOKED", "INDEPENDENTLY_ATTESTED")
        for x in result["family_evidence_matrix"]
    )


def test_p716_unverified_revocation_claim_conservatively_quarantines(tmp_path):
    root, index, doc = fixture(tmp_path, record())
    update(index, doc, 0, revocation_notice_claim=True)

    entry = family(assess(index, root), "ticks")
    assert entry["status"] == "QUARANTINED"
    assert (
        "LOCAL_REVOCATION_NOTICE_REQUIRES_INDEPENDENT_REVIEW"
        in entry["findings"]
    )


def test_p716_extra_self_approval_field_rejected(tmp_path):
    root, index, doc = fixture(tmp_path, record())
    update(index, doc, 0, externally_approved=True)

    with pytest.raises(FamilyVintageError, match="Strict schema"):
        assess(index, root)


def test_p716_tampered_local_payload_rejected(tmp_path):
    root, index, doc = fixture(tmp_path, record())

    target = root / doc["records"][0]["artifact_relative_path"]
    target.write_bytes(b"TAMPERED")

    with pytest.raises(FamilyVintageError, match="SHA-256 mismatch"):
        assess(index, root)


def test_p716_rehashed_payload_still_not_external_proof(tmp_path):
    root, index, doc = fixture(tmp_path, record())

    target = root / doc["records"][0]["artifact_relative_path"]
    target.write_bytes(b"REPLACED")

    update(
        index, doc, 0,
        artifact_sha256=sha256(b"REPLACED").hexdigest(),
    )

    assert family(
        assess(index, root), "ticks",
    )["status"] == "LOCAL_CANDIDATE"

    assert assess(
        index, root,
    )["council_families_independently_verified"] == 0


def test_p716_duplicate_id_refused(tmp_path):
    a = record(record_id="same")
    b = record(record_id="same")

    root, index, _ = fixture(tmp_path, a, b)

    with pytest.raises(FamilyVintageError, match="Duplicate vintage"):
        assess(index, root)


def test_p716_duplicate_path_refused(tmp_path):
    a = record(record_id="one")
    b = record(record_id="two")

    root, index, doc = fixture(tmp_path, a, b)
    update(
        index, doc, 1, artifact_relative_path="payload/one.dat",
    )

    with pytest.raises(FamilyVintageError, match="Repeated evidence"):
        assess(index, root)


@pytest.mark.parametrize("unsafe", [
    "../outside.bin",
    "/etc/shadow",
    "C:/account.bin",
    "a//b",
    "a/../b",
    "./x",
    "folder\\file",
    "account_number_12.dat",
    "user@example.com",
])
def test_p716_unsafe_artifact_path_refused(tmp_path, unsafe):
    root, index, doc = fixture(tmp_path, record())
    update(index, doc, 0, artifact_relative_path=unsafe)

    with pytest.raises(
        FamilyVintageError, match="Unsafe or sensitive",
    ):
        assess(index, root)


def test_p716_symlink_artifact_refused_if_supported(tmp_path):
    root, index, doc = fixture(tmp_path, record())
    link = root / "payload" / "alias.dat"

    try:
        link.symlink_to(root / "payload" / "item_01.dat")
    except (OSError, NotImplementedError):
        pytest.skip("Windows symlink unavailable")

    update(
        index, doc, 0,
        artifact_relative_path="payload/alias.dat",
    )

    with pytest.raises(FamilyVintageError, match="Symlink"):
        assess(index, root)


def test_p716_broken_historical_policy_rejected(tmp_path):
    bad = tmp_path / "mutated_policy.json"
    bad.write_bytes(POLICY.read_bytes() + b" ")

    with pytest.raises(
        FamilyVintageError,
        match="historical policy SHA-256 mismatch",
    ):
        assess(policy=bad)


def test_p716_wrong_epoch_cannot_rebind_history():
    with pytest.raises(
        FamilyVintageError, match="Immutable historical epoch",
    ):
        assess_stage7_family_vintages(
            epoch_id="f" * 64,
            as_of=ASOF,
            policy_file=POLICY,
        )


def test_p716_index_wrong_epoch_refused(tmp_path):
    root, index, doc = fixture(tmp_path, record())
    doc["epoch_id"] = "f" * 64
    save(index, doc)

    with pytest.raises(FamilyVintageError, match="historical epoch"):
        assess(index, root)


def test_p716_index_future_asof_rejected(tmp_path):
    root, index, doc = fixture(tmp_path, record())
    doc["as_of"] = "2026-11-30T12:00:00Z"
    save(index, doc)

    with pytest.raises(FamilyVintageError, match="review cutoff"):
        assess(index, root)


def test_p716_duplicate_json_field_rejected(tmp_path):
    root, index, doc = fixture(tmp_path, record())
    index.write_text(
        '{"schema_version":1,"schema_version":1}',
        encoding="utf-8",
    )

    with pytest.raises(FamilyVintageError, match="Duplicate JSON"):
        assess(index, root)


def test_p716_evidence_index_requires_root(tmp_path):
    root, index, doc = fixture(tmp_path, record())

    with pytest.raises(FamilyVintageError, match="root required"):
        assess(index=index)


def test_p716_root_without_index_refused(tmp_path):
    with pytest.raises(FamilyVintageError, match="root without index"):
        assess(root=tmp_path)


def test_p716_no_raw_payload_or_numeric_value_exported(tmp_path):
    row = record("cot_data", "ACTUAL")
    row["numeric_claim"] = "123456.789"

    root, index, _ = fixture(tmp_path, row)
    output = json.dumps(assess(index, root))

    assert "SYNTHETIC_UNATTESTED_BYTES" not in output
    assert "123456.789" not in output
    assert "payload/item_01.dat" not in output


def test_p716_repeated_report_has_same_digest(tmp_path):
    root, index, doc = fixture(tmp_path, record())

    a = assess(index, root)
    b = assess(index, root)

    assert a == b
    assert a["report_sha256"] == _digest({
        k: v for k, v in a.items()
        if k != "report_sha256"
    })


def test_p716_no_broker_collector_agent_or_order_side_effects():
    result = assess()

    for key in (
        "historical_source_modified", "files_written", "network_accessed",
        "broker_connected", "collector_started", "council_invoked",
        "training_authorized", "settlement_invoked", "paper_orders_authorized",
        "live_orders_authorized", "forward_epoch_created",
        "operator_approval_granted",
    ):
        assert result[key] is False

    assert result["read_only"] is True
    assert result["final_action"] == "HOLD"


def test_p716_cli_without_optional_packet(capsys):
    code = run([
        "--read-only-sources",
        "--read-only-vintages",
        "--epoch-id", EPOCH,
        "--as-of", ASOF,
        "--policy-file", str(POLICY),
    ])

    assert code == 0
    out = capsys.readouterr().out
    assert "13/13" in out
    assert "0/11" in out
    assert "FINAL=HOLD" in out


def test_p716_cli_json_empty_packet(capsys):
    code = run([
        "--read-only-sources",
        "--read-only-vintages",
        "--epoch-id", EPOCH,
        "--as-of", ASOF,
        "--decision-at", DECISION,
        "--policy-file", str(POLICY),
        "--json",
    ])

    assert code == 0
    obj = json.loads(capsys.readouterr().out)
    assert len(obj["family_evidence_matrix"]) == 13
    assert obj["source_admission"]["status"] == "BLOCKED_EXTERNAL_REVIEW"


def test_p716_cli_flags_required():
    with pytest.raises(SystemExit) as info:
        run(["--epoch-id", EPOCH, "--as-of", ASOF])
    assert info.value.code == 2


def test_p716_cli_future_cutoff_refused(capsys):
    tomorrow = (
        datetime.now(timezone.utc) + timedelta(days=1)
    ).isoformat()

    assert run([
        "--read-only-sources",
        "--read-only-vintages",
        "--epoch-id", EPOCH,
        "--as-of", tomorrow,
        "--policy-file", str(POLICY),
    ]) == 1

    assert "BLOCKED" in capsys.readouterr().out


@pytest.mark.parametrize("value", [
    "2026-10-30T12:00:00",
    "2026-10-30T15:00:00+03:00",
    "2026-10-30T12:00:60Z",
    "2026-11-31T12:00:00Z",
    "bad",
])
def test_p716_requires_strict_utc(value):
    with pytest.raises(FamilyVintageError):
        _utc(value)


def test_p715_required_tick_missing_quarantined(tmp_path):
    row = record()
    row["value_state"] = "MISSING"
    row["market_claim"] = None

    root, index, _ = fixture(tmp_path, row)

    assert "REQUIRED_VALUE_MISSING" in family(
        assess(index, root), "ticks",
    )["findings"]


def test_p715_derived_parent_family_must_match_contract(tmp_path):
    parent = record(record_id="tick_01")
    child = record(
        "calendar_coverage", "DERIVED",
        record_id="coverage_01",
        parents=["tick_01"],
    )

    root, index, _ = fixture(tmp_path, parent, child)

    assert "DERIVED_PARENT_FAMILY_NOT_DECLARED" in family(
        assess(index, root), "calendar_coverage",
    )["findings"]


def test_p714_schedule_announced_after_event_quarantined(tmp_path):
    row = record(
        "economic_calendar", "SCHEDULE",
        event_at="2026-10-30T09:00:00Z",
    )

    root, index, _ = fixture(tmp_path, row)

    assert "SCHEDULE_CLAIM_ANNOUNCED_AFTER_EVENT" in family(
        assess(index, root), "economic_calendar",
    )["findings"]


def test_p714_two_parallel_actuals_without_declared_revision_quarantined(tmp_path):
    a = record("cot_data", "ACTUAL", record_id="cot_a")
    b = record(
        "cot_data", "ACTUAL",
        record_id="cot_b",
        published="2026-10-30T10:00:10Z",
        available="2026-10-30T10:00:11Z",
        received="2026-10-30T10:00:12Z",
    )
    b["observed_at"] = b["published_at"]

    root, index, _ = fixture(tmp_path, a, b)

    assert "UNDECLARED_PARALLEL_VINTAGES" in family(
        assess(index, root), "cot_data",
    )["findings"]


def test_p713_optional_missing_proposed_for_cot_not_ticks():
    rows = family_contracts()
    assert rows["cot_data"]["may_be_absent_at_decision"] is True
    assert rows["ticks"]["may_be_absent_at_decision"] is False


def test_p714_missing_actual_timestamp_rejected(tmp_path):
    root, index, doc = fixture(
        tmp_path, record("economic_calendar", "ACTUAL"),
    )
    update(index, doc, 0, event_at=None)

    with pytest.raises(
        FamilyVintageError, match="requires event timestamp",
    ):
        assess(index, root)


def test_p715_direct_tick_and_m5_conflict_quarantined(tmp_path):
    tick = record(
        record_id="tick_01",
        event_id="tick_event",
        event_at="2026-10-30T09:59:59Z",
    )

    tick["observed_at"] = "2026-10-30T09:59:59Z"
    tick["published_at"] = "2026-10-30T09:59:59Z"
    tick["first_available_at"] = "2026-10-30T10:00:00Z"
    tick["received_at"] = "2026-10-30T10:00:01Z"
    tick["market_claim"]["bid"] = "1.09990"
    tick["market_claim"]["ask"] = "1.10000"

    candle = record(
        "candles", "OBSERVATION",
        record_id="m5_01",
        event_id="m5_event",
    )

    root, index, _ = fixture(tmp_path, tick, candle)
    result = assess(index, root)

    assert "EURUSD_TICK_OUTSIDE_OBSERVED_M5_OHLC" in family(
        result, "candles",
    )["findings"]


def test_p714_revision_different_provider_requires_review(tmp_path):
    a = record("cot_data", "ACTUAL", record_id="cot_a")
    b = record(
        "cot_data", "REVISION",
        record_id="cot_b",
        supersedes="cot_a",
        published="2026-10-30T10:00:10Z",
        available="2026-10-30T10:00:11Z",
        received="2026-10-30T10:00:12Z",
    )

    b["observed_at"] = b["published_at"]
    b["provider_claim"] = "DIFFERENT_UNVERIFIED_PROVIDER"

    root, index, _ = fixture(tmp_path, a, b)

    assert "REVISION_PROVIDER_IDENTITY_CHANGED" in family(
        assess(index, root), "cot_data",
    )["findings"]


def test_p714_earlier_anomaly_not_cleared_by_later_vintage(tmp_path):
    a = record(
        "cot_data", "ACTUAL",
        record_id="cot_a",
        event_at="2026-10-30T10:00:10Z",
    )
    b = record(
        "cot_data", "REVISION",
        record_id="cot_b",
        supersedes="cot_a",
        event_at="2026-10-30T10:00:10Z",
        published="2026-10-30T10:00:11Z",
        available="2026-10-30T10:00:12Z",
        received="2026-10-30T10:00:13Z",
    )
    b["observed_at"] = b["published_at"]

    root, index, _ = fixture(tmp_path, a, b)

    assert "COT_PUBLISHED_BEFORE_POSITION_DATE" in family(
        assess(index, root), "cot_data",
    )["findings"]

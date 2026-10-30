
"""P9-09..12: synthetic horizon/quote maturation and denominator reconciliation.

Only temporary pytest fixture files are written. Neither a signed provider
receipt nor an independently registered forecast can be created by this code.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest

from backend.runtime.stage8_m5_offline_heartbeat import _lineage_key
from backend.runtime.stage9_predeclared_experiment_protocol import _digest
from backend.runtime.test_stage9_predeclared_experiment_protocol import save, cutoff, z, POLICY
from backend.runtime.stage9_forecast_custody_reconciliation import assess_stage9_forecast_custody
from backend.runtime.test_stage9_forecast_custody_reconciliation import (
    setup as custody_fixture, first_bar, make_forecast,
)
from backend.runtime.stage9_outcome_maturation_reconciliation import (
    EPOCH, POLICY_SHA256, VERSION, QUOTE_CONTRACT, DATASET_CONTRACT,
    OutcomeMaturationError, _clock, assess_stage9_outcome_maturation, run,
)

UTC = timezone.utc


def q(time, *, target=False, provider="SYNTHETIC_PROVIDER_A", spread="0.0002"):
    base = time + timedelta(seconds=1 if target else 5)
    quote = {
        "quote_id": "Q_TARGET" if target else "Q_ENTRY",
        "provider_id": provider,
        "vintage_id": "Q_VINTAGE_B" if target else "Q_VINTAGE_A",
        "observed_at": z(base), "published_at": z(base + timedelta(seconds=1)),
        "first_available_at": z(base + timedelta(seconds=2)),
        "received_at": z(base + timedelta(seconds=3)),
        "bid": "1.0804" if target else "1.0800",
        "ask": "1.0806" if target else "1.0802",
        "quote_side": "BID_ASK_PAIR", "clock_uncertainty_ms": "10.000",
        "source_class": "LOCAL_SYNTHETIC_UNVERIFIED",
    }
    quote["source_sha256"] = _digest(quote)
    return quote


def requote(quote):
    quote["source_sha256"] = _digest({k: v for k, v in quote.items() if k != "source_sha256"})
    return quote


def econ(bar):
    doc = {
        "provider_id": "LOCAL_MACRO", "vintage_id": "ORIGINAL_ECON_VINTAGE",
        "published_at": z(bar - timedelta(hours=1)),
        "first_available_at": z(bar - timedelta(minutes=59)),
        "received_at": z(bar - timedelta(minutes=58)),
        "source_class": "LOCAL_SYNTHETIC_UNVERIFIED",
    }
    doc["source_sha256"] = _digest(doc)
    return doc


def reecon(item):
    item["source_sha256"] = _digest({k: v for k, v in item.items() if k != "source_sha256"})
    return item


def outcome_fixture(tmp_path, *, include=True, witness=False):
    pp, proto, lp, ledger, rp, receipts = custody_fixture(tmp_path, receipts=witness)
    at = cutoff()
    custody = assess_stage9_forecast_custody(
        epoch_id=EPOCH, as_of=at, policy_file=POLICY,
        protocol_file=pp, ledger_file=lp, receipt_candidates=rp,
    )
    bar = first_bar()
    record = ledger["decisions"][0]
    f = record["forecast"]
    decision = datetime.fromisoformat(record["decision_at"].replace("Z", "+00:00"))
    target = decision + timedelta(seconds=f["horizon_seconds"])
    original = {
        "observation_id": "LOCAL_OBS_001", "decision_key": record["decision_key"],
        "synthetic_prediction_id": f["prediction_id"],
        "hypothesis_id": f["hypothesis_id"],
        "horizon_seconds": f["horizon_seconds"], "claimed_target_at": z(target),
        "decision_quote": q(bar), "target_quote": q(target, target=True),
        "economic_vintage": econ(bar),
        "provenance_claim": "LOCAL_UNVERIFIED_QUOTE_AND_VINTAGE",
    }
    doc = {
        "schema_version": 1, "historical_epoch_id": EPOCH,
        "packet_id": "LOCAL_UNVERIFIED_OUTCOME_CANDIDATES",
        "mode": "LOCAL_SYNTHETIC_OUTCOMES_UNATTESTED",
        "experiment_id": proto["experiment_id"],
        "protocol_candidate_sha256": _digest(proto),
        "ledger_file_sha256": sha256(lp.read_bytes()).hexdigest(),
        "custody_report_sha256": custody["report_sha256"],
        "mock_clock_at": at, "observations": [original] if include else [],
        "censor_claims": [],
    }
    op = save(tmp_path / "unverified_outcomes.json", doc)
    return pp, proto, lp, ledger, rp, op, doc, at


def assess(pp=None, lp=None, *, rp=None, op=None, prior=None, at=None,
           policy=POLICY, epoch=EPOCH):
    return assess_stage9_outcome_maturation(
        epoch_id=epoch, as_of=cutoff() if at is None else at,
        policy_file=policy, protocol_file=pp, ledger_file=lp,
        receipt_candidates=rp, outcome_file=op, prior_outcome=prior,
    )


def ready(tmp_path, *, include=True, witness=False):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(
        tmp_path, include=include, witness=witness)
    return assess(pp, lp, rp=rp, op=op, at=at)


def mutate(op, doc):
    save(op, doc)


def first_row(report):
    return report["evaluation_matrix"][0]


# P9-09: horizon fixed from the original forecast and not mutable afterward.

def test_p909_no_inputs_do_not_invent_outcomes_or_predictions():
    r = assess()
    assert r["outcome_status"] == "NOT_SUPPLIED"
    assert r["coverage"]["m5_decision_denominator"] == 0
    assert r["coverage"]["prospectively_scorable_pairs"] == 0
    assert r["final_action"] == "HOLD"


def test_p909_maturation_uses_original_frozen_prediction_target(tmp_path):
    r = ready(tmp_path)
    assert r["coverage"]["locally_matured_unattested_pairs"] == 1
    assert first_row(r)["maturation_state"] == "LOCALLY_MATURED_UNATTESTED_NOT_SCORABLE"
    assert r["real_forecasts_registered"] == 0
    assert r["hypotheses_evaluated"] == 0


def test_p909_maturing_one_horizon_does_not_imply_all_horizons(tmp_path):
    r = ready(tmp_path)
    assert r["coverage"]["hypotheses_per_decision"] == 2
    assert r["coverage"]["all_hypothesis_decision_pairs"] == 10
    assert r["coverage"]["missing_hypothesis_forecasts"] == 9


def test_p909_forecast_horizon_mismatch_refused(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    doc["observations"][0]["horizon_seconds"] = 900
    mutate(op, doc)
    with pytest.raises(OutcomeMaturationError, match="target/horizon"):
        assess(pp, lp, op=op, at=at)


def test_p909_claimed_target_can_never_move_after_prediction(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    doc["observations"][0]["claimed_target_at"] = z(first_bar() + timedelta(hours=2))
    mutate(op, doc)
    with pytest.raises(OutcomeMaturationError, match="target/horizon"):
        assess(pp, lp, op=op, at=at)


def test_p909_future_one_day_horizon_is_pending_without_opening_outcomes(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path, include=False)
    instant = first_bar()
    item = ledger["decisions"][0]
    item["forecast"] = make_forecast(
        proto, instant, item["decision_key"], hypothesis=proto["hypotheses"][1],
        direction="BUY", prob=None,
    )
    save(lp, ledger)
    review = z(instant + timedelta(hours=3))
    r = assess(pp, lp, at=review)
    assert first_row(r)["maturation_state"] == "MISSING_HYPOTHESIS_FORECAST_COUNTED"
    assert r["evaluation_matrix"][1]["maturation_state"] == "PENDING_PREDECLARED_HORIZON"
    assert r["coverage"]["pending_horizon_pairs"] == 1


def test_p909_reject_outcome_claim_for_future_unmatured_target(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    key = ledger["decisions"][0]["decision_key"]
    ledger["decisions"][0]["forecast"] = make_forecast(
        proto, first_bar(), key, hypothesis=proto["hypotheses"][1],
        direction="BUY", prob=None,
    )
    save(lp, ledger)
    review_at = z(first_bar() + timedelta(hours=3))
    later = assess_stage9_forecast_custody(
        epoch_id=EPOCH, as_of=review_at, policy_file=POLICY,
        protocol_file=pp, ledger_file=lp,
    )
    doc["mock_clock_at"] = review_at
    doc["custody_report_sha256"] = later["report_sha256"]
    doc["ledger_file_sha256"] = sha256(lp.read_bytes()).hexdigest()
    obs = doc["observations"][0]
    obs["hypothesis_id"] = proto["hypotheses"][1]["hypothesis_id"]
    obs["horizon_seconds"] = 86400
    obs["synthetic_prediction_id"] = ledger["decisions"][0]["forecast"]["prediction_id"]
    obs["claimed_target_at"] = z(first_bar() + timedelta(seconds=20 + 86400))
    mutate(op, doc)
    # Future quotes are never accepted, regardless of locally edited content.
    with pytest.raises(OutcomeMaturationError):
        assess(pp, lp, op=op, at=review_at)


def test_p909_original_ledger_mutation_invalidates_bound_outcome_packet(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    ledger["decisions"][1]["attempt_status"] = "FAILED"
    save(lp, ledger)
    with pytest.raises(OutcomeMaturationError, match="original custody/clock"):
        assess(pp, lp, op=op, at=at)


# P9-10: bid/ask and economic vintage are untrusted but causally constrained.

def test_p910_quote_hashes_are_retained_without_raw_prices(tmp_path):
    r = ready(tmp_path)
    first = first_row(r)
    evidence = first["local_quote_hashes_only"]
    assert len(evidence["decision_quote_sha256"]) == 64
    assert len(evidence["target_quote_sha256"]) == 64
    assert evidence["economic_vintage_sha256"]
    assert "1.0804" not in json.dumps(r)
    assert r["provider_quote_receipts_independently_verified"] == 0


def test_p910_causal_economic_vintage_does_not_create_external_origin(tmp_path):
    r = ready(tmp_path)
    assert first_row(r)["independent_outcome_provider_verified"] is False
    assert r["economic_receipts_independently_verified"] == 0


def test_p910_late_economic_revision_quarantined_not_backdated(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    vintage = doc["observations"][0]["economic_vintage"]
    vintage["published_at"] = z(first_bar() + timedelta(seconds=25))
    vintage["first_available_at"] = z(first_bar() + timedelta(seconds=26))
    vintage["received_at"] = z(first_bar() + timedelta(seconds=27))
    reecon(vintage)
    mutate(op, doc)
    r = assess(pp, lp, op=op, at=at)
    assert first_row(r)["maturation_state"] == "QUARANTINED_SOURCE_OR_VINTAGE_CONFLICT"
    assert "POSTDECISION_ECONOMIC_REVISION_CANNOT_BECOME_FEATURE" in first_row(r)["source_issues"]


def test_p910_entry_quote_not_received_by_decision_quarantined(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    entry = doc["observations"][0]["decision_quote"]
    entry["received_at"] = z(first_bar() + timedelta(seconds=30))
    requote(entry)
    mutate(op, doc)
    r = assess(pp, lp, op=op, at=at)
    assert "ENTRY_QUOTE_UNAVAILABLE_AT_DECISION" in first_row(r)["source_issues"]


def test_p910_quote_provider_switch_quarantined(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    doc["observations"][0]["target_quote"]["provider_id"] = "SYNTHETIC_PROVIDER_B"
    requote(doc["observations"][0]["target_quote"])
    mutate(op, doc)
    r = assess(pp, lp, op=op, at=at)
    assert "QUOTE_PROVIDER_SWITCH" in first_row(r)["source_issues"]


def test_p910_local_hash_mismatch_refused_even_without_witness(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    doc["observations"][0]["target_quote"]["ask"] = "1.0900"
    mutate(op, doc)
    with pytest.raises(OutcomeMaturationError, match="content changed"):
        assess(pp, lp, op=op, at=at)


@pytest.mark.parametrize("field,value", [
    ("source_class", "BROKER_VERIFIED"),
    ("quote_side", "EXECUTABLE_MID"),
    ("broker_receipt_id", "FAKE_EXTERNAL_RECEIPT"),
])
def test_p910_quote_cannot_self_assert_external_authority(tmp_path, field, value):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    doc["observations"][0]["decision_quote"][field] = value
    mutate(op, doc)
    with pytest.raises(OutcomeMaturationError):
        assess(pp, lp, op=op, at=at)


def test_p910_bid_ask_inversion_refused(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    doc["observations"][0]["target_quote"]["bid"] = "1.2000"
    requote(doc["observations"][0]["target_quote"])
    mutate(op, doc)
    with pytest.raises(OutcomeMaturationError, match="inversion"):
        assess(pp, lp, op=op, at=at)


def test_p910_quote_observed_after_publication_refused(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    entry = doc["observations"][0]["decision_quote"]
    entry["published_at"] = z(first_bar())
    requote(entry)
    mutate(op, doc)
    with pytest.raises(OutcomeMaturationError, match="order invalid"):
        assess(pp, lp, op=op, at=at)


def test_p910_future_outcome_quote_cannot_be_peeked(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    target = doc["observations"][0]["target_quote"]
    target["received_at"] = z(datetime.now(UTC) + timedelta(days=1))
    requote(target)
    mutate(op, doc)
    with pytest.raises(OutcomeMaturationError, match="Post-cutoff"):
        assess(pp, lp, op=op, at=at)


def test_p910_clock_uncertainty_large_quarantined(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    target = doc["observations"][0]["target_quote"]
    target["clock_uncertainty_ms"] = "750.000"
    requote(target)
    mutate(op, doc)
    assert "CLOCK_UNCERTAINTY_EXCEEDS_RESEARCH_CEILING" in first_row(
        assess(pp, lp, op=op, at=at))["source_issues"]


def test_p910_huge_spread_quarantined(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    target = doc["observations"][0]["target_quote"]
    target["ask"] = "1.0950"
    requote(target)
    mutate(op, doc)
    assert "ILLQUID_QUOTE_SPREAD" in first_row(assess(pp, lp, op=op, at=at))["source_issues"]


# P9-11: original target window, missingness and censoring never select winners.

def test_p911_no_observation_after_full_release_lag_is_missing_not_zero(tmp_path):
    r = ready(tmp_path, include=False)
    assert first_row(r)["maturation_state"] == "CENSORED_MISSING_ORIGINAL_OUTCOME"
    assert r["coverage"]["censored_missing_pairs"] == 1
    assert not r["realized_return_estimated"]


def test_p911_future_release_window_pending_not_zero(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path, include=False)
    # Change only the review time; outcome file is not used to avoid a future mock-clock claim.
    bar = first_bar()
    review = z(bar + timedelta(hours=1, minutes=5))
    r = assess(pp, lp, at=review)
    assert r["coverage"]["pending_release_pairs"] == 1


def test_p911_late_target_quote_cannot_replace_original(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    target = doc["observations"][0]["target_quote"]
    orig = _clock(doc["observations"][0]["claimed_target_at"])
    target["observed_at"] = z(orig + timedelta(minutes=2))
    target["published_at"] = z(orig + timedelta(minutes=2, seconds=1))
    target["first_available_at"] = z(orig + timedelta(minutes=2, seconds=2))
    target["received_at"] = z(orig + timedelta(minutes=2, seconds=3))
    requote(target)
    mutate(op, doc)
    r = assess(pp, lp, op=op, at=at)
    assert "NONCANONICAL_TARGET_QUOTE_WINDOW" in first_row(r)["source_issues"]
    assert first_row(r)["maturation_state"] == "CENSORED_STALE_TARGET_QUOTE"
    assert r["coverage"]["censored_stale_pairs"] == 1
    assert first_row(r)["local_quote_hashes_only"] is None


def test_p911_target_release_after_fixed_lag_is_censored(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    target = doc["observations"][0]["target_quote"]
    target_at = _clock(doc["observations"][0]["claimed_target_at"])
    target["first_available_at"] = z(target_at + timedelta(hours=1, seconds=1))
    target["received_at"] = z(target_at + timedelta(hours=1, seconds=2))
    requote(target)
    mutate(op, doc)
    r = assess(pp, lp, op=op, at=at)
    assert "TARGET_RELEASE_AFTER_FIXED_LAG" in first_row(r)["source_issues"]
    assert first_row(r)["maturation_state"] == "CENSORED_LATE_TARGET_RELEASE"
    assert r["coverage"]["censored_late_release_pairs"] == 1


def test_p911_censor_claim_does_not_delete_m5_denominator(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    f = ledger["decisions"][0]["forecast"]
    doc["censor_claims"].append({
        "decision_key": ledger["decisions"][0]["decision_key"],
        "synthetic_prediction_id": f["prediction_id"],
        "reason_code": "ORIGINAL_TARGET_QUOTE_MISSING", "claimed_at": at,
    })
    mutate(op, doc)
    r = assess(pp, lp, op=op, at=at)
    assert first_row(r)["maturation_state"] == "CENSORED_SOURCE_UNAVAILABLE_CLAIM"
    assert r["coverage"]["m5_decision_denominator"] == 5
    assert r["coverage"]["local_censor_claims_not_independently_verified"] == 1


def test_p911_favorable_result_cannot_be_claimed_as_censor(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    f = ledger["decisions"][0]["forecast"]
    doc["censor_claims"] = [{
        "decision_key": ledger["decisions"][0]["decision_key"],
        "synthetic_prediction_id": f["prediction_id"],
        "reason_code": "BAD_LOSS_DROP_FROM_SCORE", "claimed_at": at,
    }]
    mutate(op, doc)
    with pytest.raises(OutcomeMaturationError, match="Nonpredeclared"):
        assess(pp, lp, op=op, at=at)


def test_p911_censor_future_time_refused(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    f = ledger["decisions"][0]["forecast"]
    doc["censor_claims"] = [{
        "decision_key": ledger["decisions"][0]["decision_key"],
        "synthetic_prediction_id": f["prediction_id"],
        "reason_code": "ORIGINAL_TARGET_QUOTE_MISSING",
        "claimed_at": z(datetime.now(UTC) + timedelta(days=1)),
    }]
    mutate(op, doc)
    with pytest.raises(OutcomeMaturationError, match="Future censor"):
        assess(pp, lp, op=op, at=at)


# P9-12: deterministic matrix, same baseline universe, no favorable selection.

def test_p912_complete_denominator_for_all_hypotheses_and_five_baselines(tmp_path):
    r = ready(tmp_path)
    c = r["coverage"]
    assert c["m5_decision_denominator"] == 5
    assert c["all_hypothesis_decision_pairs"] == 10
    assert c["baseline_comparison_pairs"] == 50
    assert all(x["baseline_policy_ids"] == [
        "NO_TRADE", "ALWAYS_LONG", "ALWAYS_SHORT", "MOMENTUM_SIGN", "LAST_CANDLE_SIGN"
    ] for x in r["evaluation_matrix"])


def test_p912_every_row_no_independent_scoring_even_with_local_receipt_match(tmp_path):
    r = ready(tmp_path, witness=True)
    assert all(x["eligible_for_prospective_scoring"] is False for x in r["evaluation_matrix"])
    assert r["coverage"]["independently_verified_forecasts"] == 0
    assert r["coverage"]["independently_verified_outcome_records"] == 0
    assert r["statistical_edge_demonstrated"] is False


def test_p912_duplicate_matching_outcomes_quarantined(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    second = deepcopy(doc["observations"][0])
    second["observation_id"] = "LOCAL_OBS_002"
    doc["observations"].append(second)
    mutate(op, doc)
    r = assess(pp, lp, op=op, at=at)
    assert first_row(r)["maturation_state"] == "QUARANTINED_DUPLICATE_OR_CONFLICTING_OUTCOME"
    assert r["coverage"]["locally_matured_unattested_pairs"] == 0


def test_p912_duplicate_observation_id_refused(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    doc["observations"].append(deepcopy(doc["observations"][0]))
    mutate(op, doc)
    with pytest.raises(OutcomeMaturationError, match="Duplicate observation ID"):
        assess(pp, lp, op=op, at=at)


def test_p912_orphan_outcome_is_quarantined_not_scored(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    orphan = deepcopy(doc["observations"][0])
    orphan["observation_id"] = "LOCAL_OBS_UNKNOWN"
    orphan["decision_key"] = "f" * 64
    doc["observations"].append(orphan)
    mutate(op, doc)
    r = assess(pp, lp, op=op, at=at)
    assert r["coverage"]["orphan_observations_quarantined"] == 1
    assert "ORPHAN_OUTCOME_RECORDS_QUARANTINED" in r["blockers"]


def test_p912_outcome_snapshot_no_edit_across_two_local_files(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    prior = tmp_path / "outcome_prior_copy.json"
    prior.write_bytes(op.read_bytes())
    r = assess(pp, lp, op=op, prior=prior, at=at)
    assert r["prior_local_outcome_file_sha256"] == sha256(op.read_bytes()).hexdigest()
    doc["observations"][0]["observation_id"] = "ALTERED_OBS_ID"
    mutate(op, doc)
    with pytest.raises(OutcomeMaturationError, match="modified after freeze"):
        assess(pp, lp, op=op, prior=prior, at=at)


def test_p912_protocol_reformatted_after_outcome_snapshot_refused(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    pp.write_bytes(json.dumps(proto, separators=(",", ":")).encode("utf-8"))
    # Replay identity includes the original protocol report, so even formatting
    # changes make an already-bound local outcome packet stale.
    with pytest.raises(OutcomeMaturationError, match="original custody/clock"):
        assess(pp, lp, op=op, at=at)


def test_p912_quote_status_wins_no_prospective_label_even_with_structural_witness(tmp_path):
    r = ready(tmp_path, witness=True)
    assert r["receipt_reconciliation_status"] == "LOCAL_UNTRUSTED_RECEIPT_CANDIDATES"
    assert first_row(r)["maturation_state"] == "LOCALLY_MATURED_UNATTESTED_NOT_SCORABLE"
    assert not first_row(r)["independent_forecast_receipt_verified"]


def test_p912_report_reproducible_for_fixed_review_clock(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    a, b = assess(pp, lp, op=op, at=at), assess(pp, lp, op=op, at=at)
    assert a == b
    assert a["report_sha256"] == _digest({k: v for k, v in a.items() if k != "report_sha256"})


def test_p912_historical_epoch_and_policy_are_pinned(tmp_path):
    r = ready(tmp_path)
    assert r["historical_epoch_id"] == EPOCH
    assert sha256(POLICY.read_bytes()).hexdigest() == POLICY_SHA256
    assert r["historical_policy_sha256"] == POLICY_SHA256


def test_p912_wrong_epoch_rebinding_refused():
    with pytest.raises(OutcomeMaturationError, match="rebinding refused"):
        assess(epoch="f" * 64)


def test_p912_changed_policy_bytes_refused(tmp_path):
    changed = tmp_path / "bad_policy.json"
    changed.write_bytes(POLICY.read_bytes() + b" ")
    with pytest.raises(OutcomeMaturationError, match="SHA-256"):
        assess(policy=changed)


def test_p912_outcomes_without_ledger_protocol_refused(tmp_path):
    bogus = tmp_path / "outcomes.json"
    save(bogus, {})
    with pytest.raises(OutcomeMaturationError, match="require original"):
        assess(op=bogus)


def test_p912_production_module_does_not_mutate_files(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    files = [pp, lp, op]
    before = [sha256(x.read_bytes()).hexdigest() for x in files]
    r = assess(pp, lp, op=op, at=at)
    assert [sha256(x.read_bytes()).hexdigest() for x in files] == before
    assert r["files_written"] is False
    assert r["network_accessed"] is False
    assert r["broker_connected"] is False
    assert r["final_action"] == "HOLD"


def test_p912_cli_no_inputs_holds(capsys):
    assert run([
        "--read-only-outcomes", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", cutoff(), "--policy-file", str(POLICY),
    ]) == 0
    output = capsys.readouterr().out
    assert "FINAL=HOLD" in output
    assert "Eligible M5 denominator: 0" in output
    assert "Outcomes scored: 0" in output


def test_p912_cli_with_synthetic_evidence_holds(tmp_path, capsys):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    assert run([
        "--read-only-outcomes", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", at, "--policy-file", str(POLICY),
        "--protocol-file", str(pp), "--ledger-file", str(lp),
        "--outcome-file", str(op), "--json",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["coverage"]["locally_matured_unattested_pairs"] == 1
    assert result["coverage"]["prospectively_scorable_pairs"] == 0
    assert result["final_action"] == "HOLD"


def test_p912_cli_safety_flags_required():
    with pytest.raises(SystemExit) as exc:
        run(["--epoch-id", EPOCH, "--as-of", cutoff()])
    assert exc.value.code == 2


def test_p912_cli_future_review_cutoff_rejected(capsys):
    future = z(datetime.now(UTC) + timedelta(days=1))
    assert run([
        "--read-only-outcomes", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", future, "--policy-file", str(POLICY),
    ]) == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_p912_duplicate_json_key_rejected(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    op.write_text('{"mode":"LOCAL_SYNTHETIC_OUTCOMES_UNATTESTED","mode":"BROKER_VERIFIED"}')
    with pytest.raises(OutcomeMaturationError, match="Unsafe"):
        assess(pp, lp, op=op, at=at)


def test_p912_outcome_symlink_refused_if_supported(tmp_path):
    pp, proto, lp, ledger, rp, op, doc, at = outcome_fixture(tmp_path)
    alias = tmp_path / "linked_outcome.json"
    try:
        alias.symlink_to(op)
    except (OSError, NotImplementedError):
        pytest.skip("Host cannot create symlinks")
    with pytest.raises(OutcomeMaturationError, match="Unsafe"):
        assess(pp, lp, op=alias, at=at)

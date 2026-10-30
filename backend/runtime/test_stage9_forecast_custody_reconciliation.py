
"""P9-05..08: purely synthetic custody, M5 denominator and receipt rejection.

Only pytest fixtures write temporary files. No forecast/experiment registered;
matching local witness claims cannot become independently verified receipts.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime.stage8_m5_offline_heartbeat import _lineage_key
from backend.runtime.stage9_predeclared_experiment_protocol import _digest
from backend.runtime.test_stage9_predeclared_experiment_protocol import (
    fixture as original_protocol, save, cutoff, z, POLICY,
)
from backend.runtime.stage9_forecast_custody_reconciliation import (
    EPOCH, POLICY_SHA256, VERSION, FORECAST_VERSION,
    WITNESS_VERSION, COVERAGE_VERSION, RECONCILIATION_VERSION,
    MAX_DECISIONS, MAX_RECEIPTS, ForecastCustodyError,
    assess_stage9_forecast_custody, run,
)

UTC = timezone.utc


def first_bar():
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=4)


def make_forecast(protocol, bar, key, *, hypothesis=None, direction="BUY", prob="0.700"):
    chosen = protocol["hypotheses"][0] if hypothesis is None else hypothesis
    f = {
        "contract_version": FORECAST_VERSION,
        "hypothesis_id": chosen["hypothesis_id"],
        "horizon_seconds": chosen["horizon_seconds"],
        "direction": direction, "probability_up": prob,
        "model_version_sha256": "a" * 64,
        "feature_schema_sha256": "b" * 64,
        "feature_lineage_sha256": "c" * 64,
        "forecast_created_at": z(bar + timedelta(seconds=2)),
        "witness_request_claimed_at": z(bar + timedelta(seconds=10)),
        "signature_claim_status": "LOCAL_UNVERIFIED_SIGNATURE_DIGEST",
        "signature_claim_sha256": "d" * 64,
    }
    f["forecast_sha256"] = _digest(f)
    f["prediction_id"] = _digest({
        "namespace": "SYNTHETIC_UNREGISTERED_ONLY",
        "historical_epoch_id": EPOCH,
        "decision_key": key,
        "experiment_id": protocol["experiment_id"],
        "frozen_forecast_sha256": f["forecast_sha256"],
    })
    return f


def reset_forecast_hashes(f, key, experiment):
    f["forecast_sha256"] = _digest({
        k: v for k, v in f.items() if k not in ("forecast_sha256", "prediction_id")
    })
    f["prediction_id"] = _digest({
        "namespace": "SYNTHETIC_UNREGISTERED_ONLY", "historical_epoch_id": EPOCH,
        "decision_key": key, "experiment_id": experiment,
        "frozen_forecast_sha256": f["forecast_sha256"],
    })


def setup(tmp_path, *, attempts=None, missing=(), receipts=False):
    pp, proto = original_protocol(tmp_path)
    bar = first_bar()
    attempts = attempts or ("FORECAST", "HOLD", "ABSTAIN", "FAILED", "NO_FORECAST")
    records = []
    for i, attempt in enumerate(attempts):
        instant = bar + timedelta(minutes=5 * i)
        if i in missing:
            continue
        key = _lineage_key(instant)
        row = {
            "decision_key": key,
            "bar_close_at": z(instant),
            "decision_at": z(instant + timedelta(seconds=20)),
            "source_status": "READY",
            "attempt_status": attempt,
            "exclusion_claim_code": None,
            "exclusion_claim_digest": None,
            "forecast": make_forecast(proto, instant, key) if attempt == "FORECAST" else None,
        }
        records.append(row)
    ledger = {
        "schema_version": 1,
        "historical_epoch_id": EPOCH,
        "packet_id": "SYNTHETIC_LOCAL_CUSTODY_001",
        "mode": "LOCAL_SYNTHETIC_CUSTODY_ONLY",
        "experiment_id": proto["experiment_id"],
        "protocol_sha256": _digest(proto),
        "mock_clock_at": z(bar + timedelta(hours=1)),
        "window_first_bar_at": z(bar),
        "window_last_bar_at": z(bar + timedelta(minutes=5 * (len(attempts) - 1))),
        "cadence_seconds": 300,
        "decisions": records,
    }
    path = save(tmp_path / "local_synthetic_forecast_ledger.json", ledger)
    rp, receipt_doc = (make_receipts(tmp_path, path, ledger, proto) if receipts
                       else (None, None))
    return pp, proto, path, ledger, rp, receipt_doc


def make_receipts(tmp_path, ledger_path, ledger, protocol):
    claim = []
    for row in ledger["decisions"]:
        if row["forecast"] is None:
            continue
        f = row["forecast"]
        claim.append({
            "receipt_id": "LOCAL_RECEIPT_001",
            "service_id": "LOCAL_UNVERIFIED_WITNESS_CLAIM",
            "decision_key": row["decision_key"],
            "prediction_id": f["prediction_id"],
            "forecast_sha256": f["forecast_sha256"],
            "claimed_received_at": z(datetime.fromisoformat(
                row["bar_close_at"].replace("Z", "+00:00")) + timedelta(seconds=15)),
            "signature_claim_sha256": "e" * 64,
        })
    receipt_doc = {
        "schema_version": 1, "historical_epoch_id": EPOCH,
        "packet_id": "LOCAL_UNVERIFIED_RECEIPT_LIST",
        "mode": "LOCAL_UNVERIFIED_RECEIPT_CANDIDATES",
        "experiment_id": protocol["experiment_id"],
        "ledger_file_sha256": sha256(ledger_path.read_bytes()).hexdigest(),
        "receipts": claim,
    }
    return save(tmp_path / "local_unverified_witness_claims.json", receipt_doc), receipt_doc


def assess(pp=None, lp=None, *, prior_protocol=None, prior_ledger=None,
           receipt_file=None, as_of=None, policy=POLICY, epoch=EPOCH):
    return assess_stage9_forecast_custody(
        epoch_id=epoch, as_of=cutoff() if as_of is None else as_of,
        policy_file=policy, protocol_file=pp, prior_protocol=prior_protocol,
        ledger_file=lp, prior_ledger=prior_ledger, receipt_candidates=receipt_file,
    )


def checked(tmp_path, *, receipts=False):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=receipts)
    return assess(pp, lp, receipt_file=rp)


def row(result, n=0):
    return result["decisions"][n]


# P9-05: read-only, unsent witness request contract and trust separation.

def test_p905_empty_cli_and_report_have_no_request_or_external_receipt():
    r = assess()
    assert r["ledger_status"] == "NOT_SUPPLIED"
    assert r["witness_interface"]["requests_prepared_not_sent"] == 0
    assert r["witness_interface"]["external_receipts_returned"] == 0
    assert r["real_registered_forecasts"] == 0
    assert r["final_action"] == "HOLD"


def test_p905_local_forecast_has_unsent_request_not_receipt(tmp_path):
    r = checked(tmp_path)
    req = row(r)["witness_request"]
    assert len(req["request_sha256"]) == 64
    assert req["status"] == "UNSENT_NO_WITNESS"
    assert req["service_contacted"] is False
    assert r["witness_interface"]["request_transmissions"] == 0
    assert r["witness_interface"]["trusted_signer_keys_loaded"] == 0
    assert r["witness_interface"]["contract_version"] == WITNESS_VERSION


def test_p905_request_is_deterministic_and_cannot_be_real_receipt(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    a = assess(pp, lp)
    b = assess(pp, lp)
    assert a == b
    assert a["witness_interface"]["independent_receipt_verified"] is False
    assert row(a)["witness_request"] == row(b)["witness_request"]


def test_p905_valid_untrusted_receipt_is_still_quarantined(tmp_path):
    r = checked(tmp_path, receipts=True)
    assert row(r)["structural_receipt_match"] is True
    assert row(r)["custody_state"] == "QUARANTINED_UNATTESTED_RECEIPT_CLAIM"
    assert r["independent_receipts_verified"] == 0
    assert r["reconciliation"]["cryptographic_signature_verifications"] == 0


def test_p905_fake_signature_status_refused(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["decisions"][0]["forecast"]["signature_claim_status"] = "INDEPENDENTLY_SIGNED"
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="signature cannot self-assert"):
        assess(pp, lp)


def test_p905_forecast_signature_digest_is_only_a_claim(tmp_path):
    r = checked(tmp_path, receipts=True)
    assert r["witness_interface"]["request_transmissions"] == 0
    assert r["reconciliation"]["trusted_witness_public_keys_loaded"] == 0
    assert r["reconciliation"]["externally_verified_receipts"] == 0


def test_p905_inject_service_activation_into_local_receipt_rejected(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=True)
    receipt["service_approved"] = True
    save(rp, receipt)
    with pytest.raises(ForecastCustodyError, match="Strict schema"):
        assess(pp, lp, receipt_file=rp)


# P9-06: immutable identities, causal timing, versioned ancestry and no edit.

def test_p906_local_candidate_prediction_id_and_model_lineage_bound(tmp_path):
    r = checked(tmp_path)
    f = row(r)["synthetic_forecast"]
    assert len(f["synthetic_prediction_id"]) == 64
    assert f["model_version_sha256"] == "a" * 64
    assert f["feature_schema_sha256"] == "b" * 64
    assert f["feature_lineage_sha256"] == "c" * 64
    assert f["horizon_seconds"] == 3600
    assert r["real_registered_forecasts"] == 0


def test_p906_original_local_ledger_bytes_match_distinct_prior_snapshot(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    prior = tmp_path / "prior_readonly_copy.json"
    prior.write_bytes(lp.read_bytes())
    r = assess(pp, lp, prior_ledger=prior)
    assert r["local_frozen_snapshot_bytes_match"]
    assert r["local_snapshot_proves_external_timestamp"] is False


def test_p906_modified_forecast_with_old_payload_sha_refused(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["decisions"][0]["forecast"]["probability_up"] = "0.800"
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="edited after local freeze"):
        assess(pp, lp)


def test_p906_rehashed_forecast_requires_different_identity_and_prior_detects_edit(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    prior = tmp_path / "prior.json"
    prior.write_bytes(lp.read_bytes())
    forecast = ledger["decisions"][0]["forecast"]
    forecast["probability_up"] = "0.800"
    reset_forecast_hashes(forecast, ledger["decisions"][0]["decision_key"], proto["experiment_id"])
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="edited or reformatted"):
        assess(pp, lp, prior_ledger=prior)


def test_p906_same_local_ledger_cannot_be_prior_snapshot(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    with pytest.raises(ForecastCustodyError, match="Distinct local prior"):
        assess(pp, lp, prior_ledger=lp)


@pytest.mark.parametrize("field,invalid", [
    ("model_version_sha256", "BAD"),
    ("feature_schema_sha256", "A" * 64),
    ("feature_lineage_sha256", None),
    ("signature_claim_sha256", "a" * 63),
])
def test_p906_invalid_version_or_signature_digest_refused(tmp_path, field, invalid):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    f = ledger["decisions"][0]["forecast"]
    f[field] = invalid
    reset_forecast_hashes(f, ledger["decisions"][0]["decision_key"], proto["experiment_id"])
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="SHA-256"):
        assess(pp, lp)


@pytest.mark.parametrize("field,new_value", [
    ("horizon_seconds", 900),
    ("hypothesis_id", "NOT_PREDECLARED"),
    ("horizon_seconds", True),
])
def test_p906_forecast_not_in_predeclared_hypothesis_refused(tmp_path, field, new_value):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    f = ledger["decisions"][0]["forecast"]
    f[field] = new_value
    reset_forecast_hashes(f, ledger["decisions"][0]["decision_key"], proto["experiment_id"])
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="not predeclared"):
        assess(pp, lp)


@pytest.mark.parametrize("field,offset", [
    ("forecast_created_at", -1),
    ("forecast_created_at", 30),
    ("witness_request_claimed_at", 21),
])
def test_p906_backdated_or_late_forecast_claim_refused(tmp_path, field, offset):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    f = ledger["decisions"][0]["forecast"]
    bar = first_bar()
    f[field] = z(bar + timedelta(seconds=offset))
    reset_forecast_hashes(f, ledger["decisions"][0]["decision_key"], proto["experiment_id"])
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="Late or backdated"):
        assess(pp, lp)


def test_p906_forecast_duplicate_prediction_field_refused(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["decisions"][0]["forecast"]["registered_id"] = "FAKE"
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="Strict schema"):
        assess(pp, lp)


def test_p906_changed_decision_key_refused(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["decisions"][0]["decision_key"] = "f" * 64
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="forged M5 decision"):
        assess(pp, lp)


def test_p906_duplicate_m5_key_refused(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["decisions"].append(deepcopy(ledger["decisions"][0]))
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="Duplicate/forged"):
        assess(pp, lp)


def test_p906_probability_not_admissible_for_direction_only_hypothesis(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    f = ledger["decisions"][0]["forecast"]
    h = proto["hypotheses"][1]
    f["hypothesis_id"] = h["hypothesis_id"]
    f["horizon_seconds"] = h["horizon_seconds"]
    reset_forecast_hashes(f, ledger["decisions"][0]["decision_key"], proto["experiment_id"])
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="Directional-only"):
        assess(pp, lp)


@pytest.mark.parametrize("bad", ["1.1", "nan", "0.12345", 0.7, "-0.1"])
def test_p906_bad_forecast_probability_refused(tmp_path, bad):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    f = ledger["decisions"][0]["forecast"]
    f["probability_up"] = bad
    reset_forecast_hashes(f, ledger["decisions"][0]["decision_key"], proto["experiment_id"])
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="probability"):
        assess(pp, lp)


def test_p906_buy_probability_direction_contradiction_refused(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    f = ledger["decisions"][0]["forecast"]
    f["probability_up"] = "0.200"
    reset_forecast_hashes(f, ledger["decisions"][0]["decision_key"], proto["experiment_id"])
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="contradicts"):
        assess(pp, lp)


def test_p906_wrong_local_protocol_digest_refused(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["protocol_sha256"] = "f" * 64
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="bind to frozen protocol"):
        assess(pp, lp)


def test_p906_wrong_original_epoch_refused(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["historical_epoch_id"] = "f" * 64
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="bind to frozen protocol"):
        assess(pp, lp)


def test_p906_review_cannot_create_new_epoch():
    with pytest.raises(ForecastCustodyError, match="rebinding refused"):
        assess(epoch="f" * 64)


def test_p906_ledger_mock_time_after_review_refused(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["mock_clock_at"] = z(datetime.now(UTC) + timedelta(hours=1))
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="chronology"):
        assess(pp, lp)


def test_p906_wrong_m5_alignment_rejected(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["window_first_bar_at"] = z(first_bar() + timedelta(seconds=30))
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="chronology"):
        assess(pp, lp)


# P9-07: denominator includes HOLD, abstention, failure, no-forecast, and absent slots.

def test_p907_complete_five_m5_slots_are_in_denominator(tmp_path):
    r = checked(tmp_path)
    c = r["coverage"]
    assert c["expected_m5_decisions"] == 5
    assert c["research_denominator_including_missing_and_holds"] == 5
    assert c["forecast_candidates"] == 1
    assert c["held_decisions"] == 1
    assert c["abstained_decisions"] == 1
    assert c["failed_decisions"] == 1
    assert c["no_forecast_decisions"] == 1
    assert c["contract_version"] == COVERAGE_VERSION


def test_p907_missing_m5_row_is_retained_as_quarantined_denominator(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, missing=(1,))
    r = assess(pp, lp)
    assert r["coverage"]["missing_decision_records"] == 1
    assert r["coverage"]["research_denominator_including_missing_and_holds"] == 5
    assert row(r, 1)["custody_state"] == "QUARANTINED_MISSING_DECISION_RECORD"
    assert "MISSING_M5_DECISION_RECORDS_QUARANTINED_IN_DENOMINATOR" in r["blockers"]


def test_p907_all_missing_rows_do_not_create_registered_forecasts(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, missing=tuple(range(5)))
    r = assess(pp, lp)
    assert r["coverage"]["research_denominator_including_missing_and_holds"] == 5
    assert r["coverage"]["missing_decision_records"] == 5
    assert r["witness_interface"]["requests_prepared_not_sent"] == 0


def test_p907_hold_and_abstain_rows_are_not_receipt_candidates(tmp_path):
    r = checked(tmp_path)
    assert row(r, 1)["custody_state"] == "HELD_NO_FORECAST"
    assert row(r, 2)["custody_state"] == "ABSTAINED_NO_FORECAST"
    assert row(r, 3)["custody_state"] == "FAILED_NO_FORECAST"
    assert row(r, 4)["custody_state"] == "MISSING_FORECAST_COUNTED"
    assert all(x["witness_request"] is None for x in r["decisions"][1:])


def test_p907_m5_source_failure_is_still_counted(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["decisions"][1]["source_status"] = "MISSING"
    save(lp, ledger)
    r = assess(pp, lp)
    assert r["coverage"]["research_denominator_including_missing_and_holds"] == 5
    assert "SOURCE_NOT_INDEPENDENTLY_ADMITTED_OR_UNAVAILABLE" in row(r, 1)["reconciliation_issues"]


def test_p907_unverified_predeclared_exclusion_does_not_shrink_denominator(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["decisions"][1]["exclusion_claim_code"] = "PREDECLARED_MARKET_CLOSURE"
    ledger["decisions"][1]["exclusion_claim_digest"] = "f" * 64
    save(lp, ledger)
    r = assess(pp, lp)
    assert r["coverage"]["locally_claimed_predeclared_exclusions"] == 1
    assert r["coverage"]["independently_verified_exclusions"] == 0
    assert r["coverage"]["research_denominator_including_missing_and_holds"] == 5


def test_p907_nonpredeclared_or_favorable_exclusion_refused(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["decisions"][1]["exclusion_claim_code"] = "EXCLUDE_LOSING_DECISIONS"
    ledger["decisions"][1]["exclusion_claim_digest"] = "f" * 64
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="Exclusion not predeclared"):
        assess(pp, lp)


def test_p907_unpaired_exclusion_claim_digest_refused(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["decisions"][1]["exclusion_claim_digest"] = "f" * 64
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="Unpaired"):
        assess(pp, lp)


def test_p907_hold_cannot_carry_forecast(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["decisions"][1]["forecast"] = deepcopy(ledger["decisions"][0]["forecast"])
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="cannot carry a prediction"):
        assess(pp, lp)


def test_p907_forecast_cannot_override_missing_or_quarantined_data(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["decisions"][0]["source_status"] = "QUARANTINED"
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="cannot bypass"):
        assess(pp, lp)


def test_p907_forecast_cannot_bypass_claimed_exclusion(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["decisions"][0]["exclusion_claim_code"] = "INVALID_M5_CANDLE"
    ledger["decisions"][0]["exclusion_claim_digest"] = "f" * 64
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="cannot bypass"):
        assess(pp, lp)


def test_p907_cannot_inject_profitable_only_filter_field(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["only_winning_forecasts"] = True
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="Strict schema"):
        assess(pp, lp)


def test_p907_cannot_inject_outcome_in_decision_record(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["decisions"][0]["realized_profit"] = "999.00"
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="Strict schema"):
        assess(pp, lp)


def test_p907_bar_interval_outside_window_refused(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["decisions"][2]["bar_close_at"] = z(first_bar() + timedelta(hours=2))
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="outside declared M5"):
        assess(pp, lp)


def test_p907_bounded_universe_refuses_more_than_96_slots(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    ledger["window_last_bar_at"] = z(first_bar() + timedelta(minutes=5 * (MAX_DECISIONS + 1)))
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="bound"):
        assess(pp, lp)


# P9-08: untrusted receipt matches, duplicates, orphans and noncausal claims.

def test_p908_no_receipt_list_counts_forecast_without_approval(tmp_path):
    r = checked(tmp_path)
    assert r["reconciliation"]["missing_witness_claims"] == 1
    assert row(r)["custody_state"] == "QUARANTINED_NO_TRUSTED_WITNESS"
    assert r["independent_receipts_verified"] == 0


def test_p908_matching_unverified_receipt_is_only_structural(tmp_path):
    r = checked(tmp_path, receipts=True)
    assert r["reconciliation"]["contract_version"] == RECONCILIATION_VERSION
    assert r["reconciliation"]["structurally_matching_unverified_receipts"] == 1
    assert not r["reconciliation"]["all_receipt_claims_independently_verified"]
    assert "LOCAL_MATCH_IS_NOT_INDEPENDENT_SIGNATURE" in row(r)["reconciliation_issues"]


def test_p908_modified_prediction_id_in_receipt_quarantined(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=True)
    receipt["receipts"][0]["prediction_id"] = "f" * 64
    save(rp, receipt)
    r = assess(pp, lp, receipt_file=rp)
    assert row(r)["custody_state"] == "QUARANTINED_CONFLICTING_RECEIPT_CLAIM"


def test_p908_modified_forecast_sha_in_receipt_quarantined(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=True)
    receipt["receipts"][0]["forecast_sha256"] = "f" * 64
    save(rp, receipt)
    assert row(assess(pp, lp, receipt_file=rp))["custody_state"] == "QUARANTINED_CONFLICTING_RECEIPT_CLAIM"


def test_p908_late_claim_after_decision_quarantined(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=True)
    receipt["receipts"][0]["claimed_received_at"] = z(first_bar() + timedelta(seconds=21))
    save(rp, receipt)
    r = assess(pp, lp, receipt_file=rp)
    assert row(r)["custody_state"] == "QUARANTINED_NONCAUSAL_RECEIPT_CLAIM"


def test_p908_receipt_claim_predating_submission_quarantined(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=True)
    receipt["receipts"][0]["claimed_received_at"] = z(first_bar() + timedelta(seconds=5))
    save(rp, receipt)
    assert row(assess(pp, lp, receipt_file=rp))["custody_state"] == "QUARANTINED_NONCAUSAL_RECEIPT_CLAIM"


def test_p908_duplicate_same_receipt_id_quarantined(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=True)
    receipt["receipts"].append(deepcopy(receipt["receipts"][0]))
    save(rp, receipt)
    r = assess(pp, lp, receipt_file=rp)
    assert r["reconciliation"]["duplicate_receipt_ids"] == ["LOCAL_RECEIPT_001"]
    assert row(r)["custody_state"] == "QUARANTINED_DUPLICATE_RECEIPT_CLAIM"


def test_p908_two_receipts_for_one_prediction_quarantined_even_with_distinct_ids(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=True)
    other = deepcopy(receipt["receipts"][0])
    other["receipt_id"] = "LOCAL_RECEIPT_002"
    receipt["receipts"].append(other)
    save(rp, receipt)
    r = assess(pp, lp, receipt_file=rp)
    assert row(r)["custody_state"] == "QUARANTINED_DUPLICATE_RECEIPT_CLAIM"
    assert r["reconciliation"]["receipt_candidate_count"] == 2


def test_p908_orphan_receipt_for_unknown_decision_tracked(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=True)
    receipt["receipts"][0]["decision_key"] = "f" * 64
    save(rp, receipt)
    r = assess(pp, lp, receipt_file=rp)
    assert r["reconciliation"]["orphan_receipt_candidate_count"] == 1
    assert r["reconciliation"]["missing_witness_claims"] == 1


def test_p908_orphan_receipt_targeting_hold_decision_tracked(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=True)
    receipt["receipts"][0]["decision_key"] = ledger["decisions"][1]["decision_key"]
    save(rp, receipt)
    r = assess(pp, lp, receipt_file=rp)
    assert r["reconciliation"]["orphan_receipt_candidate_count"] == 1
    assert row(r, 1)["custody_state"] == "HELD_NO_FORECAST"


def test_p908_receipt_claim_future_of_review_rejected(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=True)
    receipt["receipts"][0]["claimed_received_at"] = z(datetime.now(UTC) + timedelta(hours=1))
    save(rp, receipt)
    with pytest.raises(ForecastCustodyError, match="after review cutoff"):
        assess(pp, lp, receipt_file=rp)


def test_p908_receipt_binds_original_ledger_exact_bytes(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=True)
    ledger["packet_id"] = "SYNTHETIC_LOCAL_CUSTODY_002"
    save(lp, ledger)
    with pytest.raises(ForecastCustodyError, match="original ledger"):
        assess(pp, lp, receipt_file=rp)


@pytest.mark.parametrize("field, value", [
    ("mode", "INDEPENDENTLY_REGISTERED"),
    ("historical_epoch_id", "f" * 64),
    ("experiment_id", "OTHER_EXPERIMENT"),
])
def test_p908_receipt_packet_authority_and_epoch_mutations_refused(tmp_path, field, value):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=True)
    receipt[field] = value
    save(rp, receipt)
    with pytest.raises(ForecastCustodyError, match="original ledger"):
        assess(pp, lp, receipt_file=rp)


def test_p908_receipt_unsupported_signature_format_refused(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=True)
    receipt["receipts"][0]["signature_claim_sha256"] = "not_a_signature"
    save(rp, receipt)
    with pytest.raises(ForecastCustodyError, match="SHA-256"):
        assess(pp, lp, receipt_file=rp)


def test_p908_late_receipt_can_never_be_marked_registered(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=True)
    receipt["receipts"][0]["claimed_received_at"] = z(first_bar() + timedelta(seconds=30))
    save(rp, receipt)
    r = assess(pp, lp, receipt_file=rp)
    assert r["independent_receipts_verified"] == 0
    assert r["real_registered_forecasts"] == 0
    assert r["final_action"] == "HOLD"


def test_p908_receipt_duplicate_json_key_rejected(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=True)
    rp.write_text('{"mode":"LOCAL_UNVERIFIED_RECEIPT_CANDIDATES","mode":"SIGNED"}')
    with pytest.raises(ForecastCustodyError, match="Unsafe"):
        assess(pp, lp, receipt_file=rp)


def test_p908_more_than_safe_receipts_rejected(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=True)
    receipt["receipts"] = [deepcopy(receipt["receipts"][0])] * (MAX_RECEIPTS + 1)
    save(rp, receipt)
    with pytest.raises(ForecastCustodyError, match="safe bound"):
        assess(pp, lp, receipt_file=rp)


# Safety, CLI and deterministic result identity.

def test_p908_receipts_without_ledger_refused(tmp_path):
    path = tmp_path / "receipt.json"
    save(path, {})
    with pytest.raises(ForecastCustodyError, match="require original frozen ledger"):
        assess(receipt_file=path)


def test_p908_ledger_without_protocol_refused(tmp_path):
    path = tmp_path / "ledger.json"
    save(path, {})
    with pytest.raises(ForecastCustodyError, match="without a frozen candidate protocol"):
        assess(lp=path)


def test_p908_prior_ledger_without_current_refused(tmp_path):
    path = tmp_path / "prior.json"
    save(path, {})
    with pytest.raises(ForecastCustodyError, match="requires the current ledger"):
        assess(prior_ledger=path)


def test_p908_historical_clock_policy_bytes_unchanged(tmp_path):
    before = sha256(POLICY.read_bytes()).hexdigest()
    _ = checked(tmp_path)
    assert before == sha256(POLICY.read_bytes()).hexdigest() == POLICY_SHA256


def test_p908_changed_policy_refused(tmp_path):
    bad = tmp_path / "bad_policy.json"
    bad.write_bytes(POLICY.read_bytes() + b" ")
    with pytest.raises(ForecastCustodyError, match="SHA-256 mismatch"):
        assess(policy=bad)


def test_p908_future_review_cutoff_refused():
    with pytest.raises(ForecastCustodyError, match="Future review"):
        assess(as_of=z(datetime.now(UTC) + timedelta(days=1)))


def test_p908_readonly_cli_no_fixture_prints_hold(capsys):
    assert run([
        "--read-only-custody", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", cutoff(), "--policy-file", str(POLICY),
    ]) == 0
    out = capsys.readouterr().out
    assert "FINAL=HOLD" in out
    assert "Unsent requests: 0" in out
    assert "Real registered forecasts: 0" in out


def test_p908_cli_json_with_synthetic_fixture_still_hold(tmp_path, capsys):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=True)
    assert run([
        "--read-only-custody", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", cutoff(), "--policy-file", str(POLICY),
        "--protocol-file", str(pp), "--ledger-file", str(lp),
        "--receipt-candidates", str(rp), "--json",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["coverage"]["research_denominator_including_missing_and_holds"] == 5
    assert result["reconciliation"]["structurally_matching_unverified_receipts"] == 1
    assert result["independent_receipts_verified"] == 0
    assert result["final_action"] == "HOLD"


def test_p908_cli_requires_explicit_readonly_flag():
    with pytest.raises(SystemExit) as e:
        run(["--epoch-id", EPOCH, "--as-of", cutoff()])
    assert e.value.code == 2


def test_p908_cli_invalid_future_fails_closed(capsys):
    assert run([
        "--read-only-custody", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", z(datetime.now(UTC) + timedelta(days=1)),
        "--policy-file", str(POLICY),
    ]) == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_p908_deterministic_replay_digest(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=True)
    at = cutoff()
    a = assess(pp, lp, receipt_file=rp, as_of=at)
    b = assess(pp, lp, receipt_file=rp, as_of=at)
    assert a == b
    assert a["report_sha256"] == _digest({k: v for k, v in a.items() if k != "report_sha256"})


def test_p908_protocol_and_ledger_bytes_not_written_by_reviewer(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path, receipts=True)
    before = [sha256(p.read_bytes()).hexdigest() for p in (pp, lp, rp)]
    result = assess(pp, lp, receipt_file=rp)
    after = [sha256(p.read_bytes()).hexdigest() for p in (pp, lp, rp)]
    assert before == after
    assert result["files_written"] is False


def test_p908_no_execution_or_approval_side_effects(tmp_path):
    r = checked(tmp_path, receipts=True)
    for field in (
        "files_written", "network_accessed", "broker_connected", "collector_started",
        "council_invoked", "model_loaded", "training_authorized",
        "settlement_invoked", "paper_orders_authorized", "live_orders_authorized",
        "operator_approval_granted", "historical_epoch_rebound", "future_epoch_created",
        "forecast_outcomes_loaded", "test_holdout_opened", "statistical_edge_demonstrated",
    ):
        assert r[field] is False
    assert r["independent_external_gates_verified"] == 0
    assert r["independent_source_families_verified"] == 0
    assert r["final_action"] == "HOLD"


def test_p908_report_contains_no_raw_prices_or_fixture_paths(tmp_path):
    r = checked(tmp_path)
    body = json.dumps(r)
    assert '"1.0840"' not in body
    assert "local_synthetic_forecast_ledger.json" not in body
    assert '"private_key"' not in body


def test_p908_symlink_ledger_refused_if_available(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    alias = tmp_path / "linked_ledger.json"
    try:
        alias.symlink_to(lp)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation unavailable on Windows")
    with pytest.raises(ForecastCustodyError, match="Unsafe"):
        assess(pp, alias)


def test_p908_oversized_ledger_file_refused(tmp_path):
    pp, proto, lp, ledger, rp, receipt = setup(tmp_path)
    lp.write_bytes(b"X" * 262145)
    with pytest.raises(ForecastCustodyError, match="oversized"):
        assess(pp, lp)

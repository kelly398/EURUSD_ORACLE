
"""S5-05..08: bounded evidence tests. Synthetic references are NOT trusted time."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.data_engine.clock_policy import (
    ClockPolicy,
    hfm_utc_to_wall,
    utc_msc,
)
from backend.runtime.stage5_broker_clock_attestation import (
    Stage5ClockError,
    _hash,
    _utc,
    assess_stage5_broker_clock,
    inspect_candidate_bundle,
    inspect_original_broker_probe,
    run,
)

ROOT = Path(__file__).parents[2]

POLICY_FILE = (
    ROOT / "docs/clock_profiles/hfm_forward_20261027.json"
)
PROBE_FILE = (
    ROOT / "docs/clock_profiles/hfm_probe_evidence_20261027.json"
)

POLICY_SHA = "e34b7a29f78f5865f4b0fe7c4ad778e4e8b650d9943792eee019a48bdf2fe088"
EPOCH = "75311e239cd5c295d3e08e03c358488fb123a46bfc6d342dd31493d95710aa33"

ASOF = datetime(
    2026, 10, 29, 19, 10, 0,
    tzinfo=timezone.utc,
)


def _args(**overrides):
    return {
        **dict(
            policy_file=POLICY_FILE,
            probe_file=PROBE_FILE,
            epoch_id=EPOCH,
            expected_policy_sha256=POLICY_SHA,
            as_of=ASOF.isoformat(),
        ),
        **overrides,
    }


def _fixture(tmp_path):
    folder = tmp_path / "existing"
    folder.mkdir()

    original = json.loads(
        POLICY_FILE.read_text()
    )
    policy = ClockPolicy.model_validate(
        original
    )

    def document(name):
        path = folder / (name + ".txt")
        path.write_bytes(
            ("SYNTHETIC-ONLY " + name).encode()
        )

        return {
            "relative_path": path.name,
            "file_sha256": sha256(
                path.read_bytes()
            ).hexdigest(),
            "claimed_issuer": "SYNTHETIC-NOT-TRUSTED",
            "claimed_issued_at": (
                ASOF - timedelta(hours=3)
            ).isoformat(),
            "claimed_received_at": (
                ASOF - timedelta(hours=2)
            ).isoformat(),
        }

    start = ASOF - timedelta(minutes=6)
    samples = []

    for i in range(3):
        current = start + timedelta(seconds=2 * i)

        samples.append({
            "broker_raw_time_msc": utc_msc(
                hfm_utc_to_wall(
                    current - timedelta(seconds=1)
                )
            ),
            "received_before": (
                current - timedelta(milliseconds=100)
            ).isoformat(),
            "reference_utc": current.isoformat(),
            "received_after": (
                current + timedelta(milliseconds=100)
            ).isoformat(),
        })

    data = {
        "schema_version": 1,
        "epoch_id": EPOCH,
        "as_of": ASOF.isoformat(),
        "broker_identity": {
            "claimed_company": "",
            "claimed_server": "",
            "provider_document": document("provider"),
            "terminal_document": document("terminal"),
        },
        "utc_reference": {
            "reference_document": document("utc"),
            "reference_kind": "ntp",
            "claimed_uncertainty_ms": 100,
            "samples": samples,
        },
    }

    # The real raw broker names are not embedded in tests.
    # Use a synthetic policy for optional candidate checks.
    company = "SYNTHETIC BROKER"
    server = "SYNTHETIC SERVER"

    data["broker_identity"]["claimed_company"] = company
    data["broker_identity"]["claimed_server"] = server

    mock_policy = policy.model_copy(update={
        "company_sha256": sha256(
            company.encode()
        ).hexdigest(),
        "server_sha256": sha256(
            server.encode()
        ).hexdigest(),
    })

    file = tmp_path / "candidate.json"
    file.write_text(
        json.dumps(data),
        encoding="utf-8",
    )

    return mock_policy, file, folder, data


def _candidate(policy, file, folder):
    return inspect_candidate_bundle(
        file,
        root=folder,
        policy=policy,
        epoch_id=EPOCH,
        as_of=ASOF,
    )


def _rewrite(file, data):
    file.write_text(
        json.dumps(data),
        encoding="utf-8",
    )


def test_s505_original_policy_and_probe_digest_bound():
    policy, result = inspect_original_broker_probe(
        policy_file=POLICY_FILE,
        probe_file=PROBE_FILE,
        epoch_id=EPOCH,
        expected_policy_sha256=POLICY_SHA,
    )

    assert policy.epoch_id == EPOCH
    assert result["policy_and_original_probe_locally_bound"] is True
    assert result["historical_observation_count"] == 3
    assert result["historical_query_kind_count"] == 2
    assert result[
        "original_broker_or_utc_evidence_independently_attested"
    ] is False
    assert len(result["bound_clock_code_sha256"]) == 64
    assert len(result["current_clock_code_sha256"]) == 64


def test_s505_original_wall_label_offset_is_not_independent():
    report = assess_stage5_broker_clock(
        **_args()
    )
    original = report["original_baseline"]

    assert original["bound_broker_wall_offset_seconds"] == 7200
    assert original[
        "offset_is_policy_mapping_not_independent_clock_measurement"
    ] is True
    assert report["independently_verified_broker_identity"] is False

    if not original["current_code_byte_exact_to_epoch"]:
        assert (
            "CURRENT_CLOCK_IMPLEMENTATION_DIFFERS_FROM_BOUND_EPOCH"
            in report["blockers"]
        )


def test_s505_original_policy_byte_tamper_rejected(tmp_path):
    p = tmp_path / "policy.json"
    p.write_bytes(
        POLICY_FILE.read_bytes() + b"\n"
    )
    args = _args(policy_file=p)

    with pytest.raises(
        Stage5ClockError,
        match="policy bytes",
    ):
        assess_stage5_broker_clock(**args)


def test_s505_original_probe_byte_tamper_rejected(tmp_path):
    p = tmp_path / "probe.json"
    p.write_bytes(
        PROBE_FILE.read_bytes() + b"\n"
    )
    args = _args(probe_file=p)

    with pytest.raises(
        Stage5ClockError,
        match="probe does not match",
    ):
        assess_stage5_broker_clock(**args)


def test_s505_wrong_epoch_rejected():
    args = _args(epoch_id="a" * 64)

    with pytest.raises(
        Stage5ClockError,
        match="epoch identity",
    ):
        assess_stage5_broker_clock(**args)


def test_s505_missing_probe_does_not_generate_one(tmp_path):
    absent = tmp_path / "not-created.json"
    args = _args(probe_file=absent)

    with pytest.raises(
        Stage5ClockError,
        match="regular",
    ):
        assess_stage5_broker_clock(**args)

    assert not absent.exists()


def test_s506_no_candidate_is_not_independent():
    report = assess_stage5_broker_clock(
        **_args()
    )
    candidate = report["candidate_triangulation"]

    assert candidate["status"] == "NOT_SUPPLIED"
    assert candidate["documents_locally_hash_matched"] == 0
    assert candidate["broker_clock_offset_independently_verified"] is False
    assert (
        "NO_BROKER_IDENTITY_AND_CLOCK_CANDIDATE_BUNDLE"
        in report["blockers"]
    )


def test_s506_hash_matched_docs_still_untrusted(tmp_path):
    policy, file, folder, _ = _fixture(tmp_path)
    candidate = _candidate(
        policy, file, folder
    )

    assert candidate["status"] == "LOCAL_CANDIDATE_TRIANGULATION_UNATTESTED"
    assert candidate["documents_locally_hash_matched"] == 3
    assert candidate["identity_hashes_match_bound_policy"] is True
    assert candidate["provider_origin_independently_verified"] is False
    assert candidate["utc_reference_independently_verified"] is False


def test_s506_identity_mismatch_rejected(tmp_path):
    policy, file, folder, data = _fixture(tmp_path)
    data["broker_identity"]["claimed_server"] = "OTHER SERVER"
    _rewrite(file, data)

    with pytest.raises(
        Stage5ClockError,
        match="differs from bound epoch",
    ):
        _candidate(policy, file, folder)


def test_s506_broker_names_not_in_candidate_result(tmp_path):
    policy, file, folder, data = _fixture(tmp_path)
    out = json.dumps(
        _candidate(policy, file, folder)
    )

    assert data["broker_identity"]["claimed_company"] not in out
    assert data["broker_identity"]["claimed_server"] not in out


def test_s506_provider_document_tampered(tmp_path):
    policy, file, folder, _ = _fixture(tmp_path)
    (folder / "provider.txt").write_text("CHANGED")

    with pytest.raises(
        Stage5ClockError,
        match="SHA-256",
    ):
        _candidate(policy, file, folder)


def test_s506_terminal_document_missing(tmp_path):
    policy, file, folder, _ = _fixture(tmp_path)
    (folder / "terminal.txt").unlink()

    with pytest.raises(
        Stage5ClockError,
        match="regular",
    ):
        _candidate(policy, file, folder)


def test_s506_reference_document_after_cutoff(tmp_path):
    policy, file, folder, data = _fixture(tmp_path)

    data["utc_reference"]["reference_document"][
        "claimed_received_at"
    ] = (ASOF + timedelta(seconds=1)).isoformat()

    _rewrite(file, data)

    with pytest.raises(
        Stage5ClockError,
        match="order",
    ):
        _candidate(policy, file, folder)


def test_s506_duplicate_document_json_key_rejected(tmp_path):
    policy, file, folder, data = _fixture(tmp_path)
    raw = json.dumps(data)

    raw = raw.replace(
        '"schema_version": 1,',
        '"schema_version": 1, "schema_version": 1,',
        1,
    )
    file.write_text(raw)

    with pytest.raises(
        Stage5ClockError,
        match="Duplicate",
    ):
        _candidate(policy, file, folder)


def test_s506_unsupported_reference_kind_rejected(tmp_path):
    policy, file, folder, data = _fixture(tmp_path)

    data["utc_reference"]["reference_kind"] = (
        "claimed_broker_certified"
    )

    _rewrite(file, data)

    with pytest.raises(
        Stage5ClockError,
        match="kind",
    ):
        _candidate(policy, file, folder)


@pytest.mark.parametrize(
    "bad",
    [
        "../secret",
        "C:/secret",
        "folder/../secret",
        "sub\\file",
        "/absolute",
        "a//b",
        "./x",
    ],
)
def test_s506_document_path_traversal_rejected(tmp_path, bad):
    policy, file, folder, data = _fixture(tmp_path)

    data["broker_identity"]["provider_document"][
        "relative_path"
    ] = bad

    _rewrite(file, data)

    with pytest.raises(
        Stage5ClockError,
        match="path|traversal",
    ):
        _candidate(policy, file, folder)


def test_s506_symlinked_document_rejected(tmp_path):
    policy, file, folder, data = _fixture(tmp_path)
    link = folder / "link.txt"

    try:
        link.symlink_to(
            folder / "provider.txt"
        )
    except (OSError, NotImplementedError):
        pytest.skip(
            "Symlinks unavailable on host"
        )

    data["broker_identity"]["provider_document"][
        "relative_path"
    ] = link.name

    _rewrite(file, data)

    with pytest.raises(
        Stage5ClockError,
        match="Symlink",
    ):
        _candidate(policy, file, folder)


def test_s507_three_repeated_samples_are_causally_ordered(tmp_path):
    policy, file, folder, _ = _fixture(tmp_path)
    cand = _candidate(policy, file, folder)

    assert cand["time_samples_consistent"] is True
    assert cand["time_sample_count"] == 3
    assert cand["sample_quote_age_residual_ms_min"] == -1000
    assert cand["sample_quote_age_residual_ms_max"] == -1000
    assert cand["broker_clock_offset_independently_verified"] is False


def test_s507_claimed_reference_time_outside_receipt_rejected(
    tmp_path,
):
    policy, file, folder, data = _fixture(tmp_path)

    data["utc_reference"]["samples"][0][
        "reference_utc"
    ] = (ASOF + timedelta(seconds=1)).isoformat()

    _rewrite(file, data)

    with pytest.raises(
        Stage5ClockError,
        match="ordering",
    ):
        _candidate(policy, file, folder)


def test_s507_future_receipt_rejected(tmp_path):
    policy, file, folder, data = _fixture(tmp_path)

    data["utc_reference"]["samples"][1][
        "received_after"
    ] = (ASOF + timedelta(seconds=1)).isoformat()

    _rewrite(file, data)

    with pytest.raises(
        Stage5ClockError,
        match="ordering",
    ):
        _candidate(policy, file, folder)


def test_s507_raw_broker_value_bool_rejected(tmp_path):
    policy, file, folder, data = _fixture(tmp_path)

    data["utc_reference"]["samples"][0][
        "broker_raw_time_msc"
    ] = True

    _rewrite(file, data)

    with pytest.raises(
        Stage5ClockError,
        match="raw clock",
    ):
        _candidate(policy, file, folder)


def test_s507_uncertainty_bool_rejected(tmp_path):
    policy, file, folder, data = _fixture(tmp_path)

    data["utc_reference"][
        "claimed_uncertainty_ms"
    ] = True

    _rewrite(file, data)

    with pytest.raises(
        Stage5ClockError,
        match="uncertainty",
    ):
        _candidate(policy, file, folder)


def test_s507_uncertainty_above_bound_rejected(tmp_path):
    policy, file, folder, data = _fixture(tmp_path)

    data["utc_reference"][
        "claimed_uncertainty_ms"
    ] = 2001

    _rewrite(file, data)

    with pytest.raises(
        Stage5ClockError,
        match="uncertainty",
    ):
        _candidate(policy, file, folder)


def test_s507_unsorted_repeated_samples_rejected(tmp_path):
    policy, file, folder, data = _fixture(tmp_path)

    data["utc_reference"]["samples"].reverse()
    _rewrite(file, data)

    with pytest.raises(
        Stage5ClockError,
        match="causally ordered",
    ):
        _candidate(policy, file, folder)


def test_s507_stale_quoted_tick_rejected(tmp_path):
    policy, file, folder, data = _fixture(tmp_path)

    sample = data["utc_reference"]["samples"][0]
    ref = _utc(sample["reference_utc"])

    sample["broker_raw_time_msc"] = utc_msc(
        hfm_utc_to_wall(
            ref - timedelta(seconds=181)
        )
    )

    _rewrite(file, data)

    with pytest.raises(
        Stage5ClockError,
        match="residual",
    ):
        _candidate(policy, file, folder)


def test_s507_future_quote_over_five_seconds_rejected(tmp_path):
    policy, file, folder, data = _fixture(tmp_path)

    sample = data["utc_reference"]["samples"][0]
    ref = _utc(sample["reference_utc"])

    sample["broker_raw_time_msc"] = utc_msc(
        hfm_utc_to_wall(
            ref + timedelta(seconds=6)
        )
    )

    _rewrite(file, data)

    with pytest.raises(
        Stage5ClockError,
        match="residual",
    ):
        _candidate(policy, file, folder)


def test_s507_too_few_samples_rejected(tmp_path):
    policy, file, folder, data = _fixture(tmp_path)

    data["utc_reference"]["samples"] = (
        data["utc_reference"]["samples"][:2]
    )

    _rewrite(file, data)

    with pytest.raises(
        Stage5ClockError,
        match="count",
    ):
        _candidate(policy, file, folder)


def test_s508_deterministic_report_with_fixed_inputs():
    first = assess_stage5_broker_clock(
        **_args()
    )
    second = assess_stage5_broker_clock(
        **_args()
    )

    assert first == second

    assert first["report_sha256"] == _hash({
        k: v
        for k, v in first.items()
        if k != "report_sha256"
    })


def test_s508_never_creates_or_changes_original_documents():
    original_a = POLICY_FILE.read_bytes()
    original_b = PROBE_FILE.read_bytes()

    assess_stage5_broker_clock(
        **_args()
    )

    assert (
        POLICY_FILE.read_bytes(),
        PROBE_FILE.read_bytes(),
    ) == (original_a, original_b)


def test_s508_all_execution_capabilities_denied():
    report = assess_stage5_broker_clock(
        **_args()
    )

    assert report["final_action"] == "HOLD"

    for flag in (
        "historical_epoch_mutated",
        "files_written",
        "database_accessed",
        "network_accessed",
        "broker_connected",
        "collector_started",
        "council_invoked",
        "training_authorized",
        "execution_authorized",
        "real_forward_admission",
        "new_epoch_approved",
    ):
        assert report[flag] is False


def test_s508_expired_epoch_is_blocker_not_automatic_restart():
    args = _args(
        as_of="2026-12-01T00:00:00Z"
    )

    result = assess_stage5_broker_clock(
        **args
    )

    assert (
        "BOUND_EPOCH_NOT_ACTIVE_AT_REVIEW_CUTOFF"
        in result["blockers"]
    )
    assert result["collector_started"] is False


def test_s508_cli_requires_both_readonly_flags():
    with pytest.raises(SystemExit) as exc:
        run([
            "--epoch-id",
            EPOCH,
        ])

    assert exc.value.code == 2


def test_s508_cli_bad_epoch_fails_closed(capsys):
    args = [
        "--read-only-evidence",
        "--read-only-clock",
        "--epoch-id",
        "bad",
        "--as-of",
        ASOF.isoformat(),
        "--policy-file",
        str(POLICY_FILE),
        "--probe-file",
        str(PROBE_FILE),
        "--expected-policy-sha256",
        POLICY_SHA,
    ]

    assert run(args) == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_s508_cli_json_output_stays_hold(capsys):
    args = [
        "--read-only-evidence",
        "--read-only-clock",
        "--epoch-id",
        EPOCH,
        "--as-of",
        ASOF.isoformat(),
        "--policy-file",
        str(POLICY_FILE),
        "--probe-file",
        str(PROBE_FILE),
        "--expected-policy-sha256",
        POLICY_SHA,
        "--json",
    ]

    assert run(args) == 0

    report = json.loads(
        capsys.readouterr().out
    )

    assert report["final_action"] == "HOLD"
    assert report["candidate_triangulation"]["status"] == "NOT_SUPPLIED"


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-29T19:10:00",
        "bad",
        "2026-10-29T22:10:00+03:00",
    ],
)
def test_s508_non_utc_rejected(value):
    with pytest.raises(Stage5ClockError):
        _utc(value)

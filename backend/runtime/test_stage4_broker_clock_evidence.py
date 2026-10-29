
"""Synthetic and original-file S4-05..08 contract tests; no MT5 connection."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import pytest
from sqlalchemy import create_engine, event

from backend.data_engine.clock_policy import (
    ClockPolicy, implementation_sha256,
)
from backend.data_engine.forward_store import epochs, encoded, hashed
from backend.runtime.stage4_broker_clock_evidence import (
    ClockEvidenceReviewError,
    _parse_utc,
    inspect_epoch_policy,
    inspect_historical_clock_blob,
    inspect_original_probe,
    inspect_stage4_broker_clock,
    run,
)

UTC = timezone.utc
ASOF = datetime(
    2026, 10, 29, 19, 58, 48,
    tzinfo=UTC,
)

BASE = Path(__file__).parents[2]
POLICY = BASE / "docs/clock_profiles/hfm_forward_20261027.json"
PROBE = BASE / "docs/clock_profiles/hfm_probe_evidence_20261027.json"


@pytest.fixture
def local(tmp_path):
    policy_raw = POLICY.read_bytes()
    probe_raw = PROBE.read_bytes()
    policy = ClockPolicy.load(POLICY)

    assert sha256(probe_raw).hexdigest() == policy.evidence_sha256

    working_policy = tmp_path / "policy.json"
    working_policy.write_bytes(policy_raw)

    working_probe = tmp_path / "probe.json"
    working_probe.write_bytes(probe_raw)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    epochs.create(engine)

    with engine.begin() as connection:
        connection.execute(epochs.insert().values(
            epoch_id=policy.epoch_id,
            policy_json=encoded(policy.model_dump(mode="json")),
            valid_from=policy.valid_from,
            valid_until=policy.valid_until,
            recorded_at=policy.valid_from + timedelta(seconds=5),
        ))

    try:
        yield (
            engine,
            policy,
            working_policy,
            working_probe,
            sha256(policy_raw).hexdigest(),
        )
    finally:
        engine.dispose()


def review_local(local, *, as_of=ASOF):
    engine, policy, file, _probe, digest = local

    return inspect_epoch_policy(
        engine,
        epoch_id=policy.epoch_id,
        policy_file=file,
        expected_policy_sha256=digest,
        as_of=as_of,
    )


def test_s405_exact_stored_epoch_canonical_identity(local):
    policy, r = review_local(local)

    assert r["epoch_id"] == policy.epoch_id
    assert r["stored_policy_matches_local_copy"] is True
    assert r["local_policy_file_matches_expected_digest"] is True
    assert (
        r["original_policy_file_location_independently_attested"]
        is False
    )


def test_s405_company_server_hashes_not_raw_identity(local):
    _, r = review_local(local)

    assert r["company_sha256_matches_stored_policy"] is True
    assert r["server_sha256_matches_stored_policy"] is True
    assert "company" not in r or "company_sha256" not in r
    assert "server" not in r or "server_sha256" not in r


def test_s405_modified_policy_bytes_rejected(local):
    _, _, file, _, _ = local
    file.write_bytes(file.read_bytes() + b"\n")

    with pytest.raises(
        ClockEvidenceReviewError,
        match="digest mismatch",
    ):
        review_local(local)


def test_s405_modified_policy_semantics_rejected(local):
    _, _, file, _, _ = local

    data = json.loads(file.read_text())
    data["server_sha256"] = "f" * 64
    file.write_text(json.dumps(data))

    with pytest.raises(
        ClockEvidenceReviewError,
        match="stored epoch",
    ):
        review_local(local)


def test_s405_nonexistent_epoch_rejected(local):
    with pytest.raises(
        ClockEvidenceReviewError,
        match="not found",
    ):
        inspect_epoch_policy(
            local[0],
            epoch_id="a" * 64,
            policy_file=local[2],
            expected_policy_sha256=local[4],
            as_of=ASOF,
        )


def test_s405_readonly_db_select_only(local):
    events = []

    def watch(_conn, _cursor, statement, _params, _ctx, _many):
        events.append(statement.strip().upper())

    event.listen(
        local[0], "before_cursor_execute", watch
    )

    try:
        review_local(local)
    finally:
        event.remove(
            local[0], "before_cursor_execute", watch
        )

    assert events
    assert all(x.startswith("SELECT") for x in events)


def test_s405_stored_epoch_future_recording_refused(local):
    with pytest.raises(
        ClockEvidenceReviewError,
        match="boundaries mismatch",
    ):
        review_local(
            local,
            as_of=local[1].valid_from,
        )


def test_s406_original_probe_digest_identity_and_query_checks(local):
    r = inspect_original_probe(
        local[1], local[3]
    )

    assert r["status"] == "LOCAL_PROBE_LINKED_UNATTESTED"
    assert r["observation_count"] == 3
    assert r["query_kind_count"] == 2
    assert r["repeated_probe_receipts_locally_consistent"]
    assert r["history_query_convention_locally_consistent"]
    assert r["independent_broker_probe_attested"] is False


def test_s406_probe_not_supplied_does_not_create_evidence(local):
    r = inspect_original_probe(
        local[1]
    )

    assert r["status"] == "NOT_SUPPLIED"
    assert r["probe_sha256"] is None


def test_s406_probe_tampered_digest_detected(local):
    local[3].write_bytes(
        local[3].read_bytes() + b"\n"
    )

    r = inspect_original_probe(
        local[1], local[3]
    )

    assert r["status"] == "PROBE_DIGEST_MISMATCH"
    assert r["probe_hash_matches_bound_policy"] is False


def test_s406_probe_bad_identity_not_promoted(local):
    data = json.loads(
        local[3].read_text()
    )
    data["server_sha256"] = "1" * 64

    raw = json.dumps(
        data, separators=(",", ":")
    ).encode()

    policy = local[1].model_copy(update={
        "evidence_sha256": sha256(raw).hexdigest()
    })

    local[3].write_bytes(raw)
    r = inspect_original_probe(
        policy, local[3]
    )

    assert r["status"] == "LOCAL_PROBE_CHECKS_INCOMPLETE"
    assert r["independent_broker_clock_attested"] is False


def test_s406_probe_no_true_historical_certification_allowed(local):
    data = json.loads(
        local[3].read_text()
    )
    data["historical_utc_certified"] = True

    raw = json.dumps(data).encode()

    policy = local[1].model_copy(update={
        "evidence_sha256": sha256(raw).hexdigest()
    })

    local[3].write_bytes(raw)

    assert (
        inspect_original_probe(policy, local[3])["status"]
        == "PROBE_CONTRACT_INVALID"
    )


def test_s406_missing_probe_stays_unavailable(local):
    r = inspect_original_probe(
        local[1],
        local[3].parent / "missing.json",
    )

    assert r["status"] == "PROBE_UNREADABLE_OR_INCONSISTENT"


def test_s406_probe_not_modified(local):
    before = local[3].read_bytes()

    inspect_original_probe(
        local[1], local[3]
    )

    assert local[3].read_bytes() == before


def test_s407_candidate_not_supplied_no_assumption(local):
    r = inspect_historical_clock_blob(
        ".",
        bound_sha256=local[1].implementation_sha256,
    )

    assert r["status"] == "NOT_SUPPLIED"
    assert r["old_code_restored_or_executed"] is False



def _repo(tmp_path, blob):
    repo = tmp_path / "repo"
    repo.mkdir()

    subprocess.run(
        ["git", "init", "-q"],
        cwd=repo,
        check=True,
    )

    # Preserve exact source bytes on Windows.
    # This changes only the temporary test repository,
    # never the user's real Git configuration.
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=repo,
        check=True,
    )

    dest = repo / "backend/data_engine/clock_policy.py"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(blob)

    subprocess.run(
        [
            "git",
            "add",
            "--",
            "backend/data_engine/clock_policy.py",
        ],
        cwd=repo,
        check=True,
    )

    subprocess.run(
        [
            "git",
            "-c", "user.name=Fixture",
            "-c", "user.email=fixture@example.org",
            "commit", "-qm", "fixture",
        ],
        cwd=repo,
        check=True,
    )

    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
    ).decode().strip()

    # Verify Git did not normalize or modify the
    # bytes before the SHA-256 comparison.
    stored_blob = subprocess.check_output(
        [
            "git",
            "show",
            f"{commit}:backend/data_engine/clock_policy.py",
        ],
        cwd=repo,
    )

    assert stored_blob == blob, (
        "Temporary Git repository changed source bytes."
    )

    return repo, commit


def test_s407_exact_historical_git_blob_found_without_checkout(
    tmp_path, local
):
    source = BASE / "backend/data_engine/clock_policy.py"
    old = source.read_bytes()

    repo, commit = _repo(tmp_path, old)

    before = subprocess.check_output(
        ["git", "status", "--porcelain"],
        cwd=repo,
    )

    result = inspect_historical_clock_blob(
        repo,
        ref=commit,
        bound_sha256=sha256(old).hexdigest(),
        current_file=source,
    )

    assert result["status"] == "LOCAL_BOUND_BLOB_FOUND_UNATTESTED"
    assert result["candidate_ast_matches_current"] is True
    assert result["git_blob_independently_attested"] is False

    assert subprocess.check_output(
        ["git", "status", "--porcelain"],
        cwd=repo,
    ) == before


def test_s407_changed_local_blob_not_bound_even_if_ast_same(
    tmp_path, local
):
    source = BASE / "backend/data_engine/clock_policy.py"
    raw = source.read_bytes()

    repo, commit = _repo(
        tmp_path,
        raw + b"\n# added comment\n",
    )

    result = inspect_historical_clock_blob(
        repo,
        ref=commit,
        bound_sha256=sha256(raw).hexdigest(),
        current_file=source,
    )

    assert (
        result["status"]
        == "GIT_CANDIDATE_NOT_BOUND_IMPLEMENTATION"
    )
    assert result["candidate_ast_matches_current"] is True


def test_s407_different_ast_is_not_equivalence_proof(
    tmp_path, local
):
    source = BASE / "backend/data_engine/clock_policy.py"

    repo, commit = _repo(
        tmp_path,
        b"def wrong():\n    return None\n",
    )

    result = inspect_historical_clock_blob(
        repo,
        ref=commit,
        bound_sha256="0" * 64,
        current_file=source,
    )

    assert result["candidate_ast_matches_current"] is False
    assert result["candidate_matches_bound_code"] is False


def test_s407_missing_candidate_no_failure_or_claim(tmp_path):
    repo = tmp_path / "not_git"
    repo.mkdir()

    r = inspect_historical_clock_blob(
        repo,
        ref="a" * 40,
        bound_sha256="f" * 64,
    )

    assert r["status"] == "GIT_CANDIDATE_UNAVAILABLE"
    assert r["candidate_matches_bound_code"] is False


def test_s407_malicious_ref_rejected(tmp_path):
    with pytest.raises(
        ClockEvidenceReviewError,
        match="Git candidate",
    ):
        inspect_historical_clock_blob(
            tmp_path,
            ref="HEAD:../../secret",
            bound_sha256="f" * 64,
        )


def test_s408_all_independent_gates_still_false(local):
    report = inspect_stage4_broker_clock(
        local[0],
        epoch_id=local[1].epoch_id,
        as_of=ASOF,
        policy_file=local[2],
        expected_policy_sha256=local[4],
        probe_file=local[3],
    )

    assert (
        report["original_probe"]["status"]
        == "LOCAL_PROBE_LINKED_UNATTESTED"
    )
    assert report["external_broker_identity_attested"] is False
    assert report["external_broker_clock_offset_attested"] is False
    assert (
        report["original_broker_probe_independently_attested"]
        is False
    )
    assert report["historical_utc_certified"] is False
    assert report["final_action"] == "HOLD"


def test_s408_no_rebinding_or_collector_actions(local):
    r = inspect_stage4_broker_clock(
        local[0],
        epoch_id=local[1].epoch_id,
        as_of=ASOF,
        policy_file=local[2],
        expected_policy_sha256=local[4],
    )

    for field in (
        "clock_policy_rebound",
        "historical_code_restored",
        "collector_started",
        "database_modified",
        "files_created_by_inspector",
        "real_forward_council_admission",
        "training_authorized",
        "execution_authorized",
    ):
        assert r[field] is False


def test_s408_fixed_inputs_deterministic(local):
    args = dict(
        epoch_id=local[1].epoch_id,
        as_of=ASOF,
        policy_file=local[2],
        expected_policy_sha256=local[4],
        probe_file=local[3],
    )

    first = inspect_stage4_broker_clock(
        local[0], **args
    )
    second = inspect_stage4_broker_clock(
        local[0], **args
    )

    assert first == second

    assert first["report_sha256"] == hashed({
        k: v
        for k, v in first.items()
        if k != "report_sha256"
    })


def test_s408_expired_epoch_is_a_blocker(local):
    later = local[1].valid_until + timedelta(seconds=1)

    report = inspect_stage4_broker_clock(
        local[0],
        epoch_id=local[1].epoch_id,
        as_of=later,
        policy_file=local[2],
        expected_policy_sha256=local[4],
        probe_file=local[3],
    )

    assert (
        "EPOCH_NOT_ACTIVE_AT_INSPECTION"
        in report["blockers"]
    )
    assert report["final_action"] == "HOLD"


def test_s408_mismatch_is_blocker(local):
    r = inspect_stage4_broker_clock(
        local[0],
        epoch_id=local[1].epoch_id,
        as_of=ASOF,
        policy_file=local[2],
        expected_policy_sha256=local[4],
        probe_file=local[3],
    )

    if local[1].implementation_sha256 != implementation_sha256():
        assert "BOUND_CLOCK_IMPLEMENTATION_NOT_CURRENT" in r["blockers"]
    else:
        assert (
            "BOUND_CLOCK_IMPLEMENTATION_NOT_CURRENT"
            not in r["blockers"]
        )


def test_s408_cli_requires_three_readonly_flags():
    with pytest.raises(SystemExit) as exc:
        run([
            "--epoch-id",
            "a" * 64,
        ])

    assert exc.value.code == 2


def test_s408_cli_invalid_epoch_fails_before_db(capsys):
    status = run([
        "--read-only-db",
        "--git-read-only",
        "--read-only-evidence",
        "--epoch-id", "bad",
        "--as-of", ASOF.isoformat(),
        "--policy-file", "missing.json",
        "--expected-policy-sha256", "f" * 64,
    ])

    assert status == 1
    assert "BLOCKED" in capsys.readouterr().out


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-10-27T01:00:00",
        "x",
        "2026-10-27T03:00:00+03:00",
    ],
)
def test_s408_bad_utc_rejected(stamp):
    with pytest.raises(ClockEvidenceReviewError):
        _parse_utc(stamp)

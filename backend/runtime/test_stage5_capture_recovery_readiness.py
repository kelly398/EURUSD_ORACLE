
"""S5-09..12 synthetic-only original list and recovery plan validation."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest
from sqlalchemy import event

from backend.data_engine.clock_policy import (
    utc_msc,
    hfm_utc_to_wall,
)
from backend.data_engine.forward_store import hashed
from backend.runtime.test_stage4_forward_receipt_recovery import (
    sample,
    NOW,
    raw_tick,
    raw_bar,
)
from backend.runtime.stage5_capture_recovery_readiness import (
    PLAN_DIGEST_FIELDS,
    Stage5CaptureError,
    _utc,
    assess_stage5_capture_recovery,
    inspect_original_capture_candidates,
    inspect_new_epoch_plan,
    run,
)

UTC = timezone.utc
ASOF = NOW + timedelta(seconds=20)


def options(sample, **kw):
    engine, policy, policy_path, policy_sha, *_ = sample

    return dict(
        engine=engine,
        epoch_id=policy.epoch_id,
        as_of=ASOF.isoformat(),
        policy_file=policy_path,
        expected_policy_sha256=policy_sha,
        **kw,
    )


def assess(sample, **kw):
    return assess_stage5_capture_recovery(
        **options(sample, **kw)
    )


def originals(sample, tmp_path, *, both=True):
    engine, policy, _, _, c1, c2 = sample

    root = tmp_path / "real-only-in-tests"
    root.mkdir()

    normalized_bar = policy.rate(
        raw_bar(
            NOW - timedelta(minutes=5)
        )
    )

    documents = []

    for index, (capture, tick_at) in enumerate((
        (
            c1,
            NOW - timedelta(seconds=2),
        ),
        (
            c2,
            NOW + timedelta(seconds=7),
        ),
    )):
        if index and not both:
            break

        payload = {
            "schema_version": 1,
            "epoch_id": policy.epoch_id,
            "capture_id": capture["capture_id"],
            "tick_rows": [
                policy.tick(
                    raw_tick(tick_at)
                )
            ],
            "bar_rows": [
                normalized_bar
            ],
        }

        filename = (
            f"original-capture-{index}.json"
        )

        artifact = root / filename

        artifact.write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

        documents.append({
            "capture_id": capture["capture_id"],
            "relative_path": filename,
            "file_sha256": sha256(
                artifact.read_bytes()
            ).hexdigest(),
            "claimed_origin": "SYNTHETIC-TEST-ONLY",
            "claimed_acquired_at": ASOF.isoformat(),
        })

    index_path = tmp_path / "existing-index.json"

    index_data = {
        "schema_version": 1,
        "epoch_id": policy.epoch_id,
        "as_of": ASOF.isoformat(),
        "entries": documents,
    }

    index_path.write_text(
        json.dumps(index_data),
        encoding="utf-8",
    )

    return root, index_path, index_data


def original_review(sample, root, file):
    engine, policy, *_ = sample

    return inspect_original_capture_candidates(
        engine,
        index_file=file,
        evidence_root=root,
        policy=policy,
        epoch_id=policy.epoch_id,
        as_of=ASOF,
        capture_limit=100,
    )


def write(path, obj):
    path.write_text(
        json.dumps(obj),
        encoding="utf-8",
    )


def candidate_plan(sample, tmp_path):
    policy = sample[1]

    plan = {
        "schema_version": 1,
        "plan_id": "SYNTHETIC-PLAN",
        "prepared_at": (
            NOW + timedelta(seconds=10)
        ).isoformat(),
        "historical_epoch_id": policy.epoch_id,
        "proposed_valid_from": (
            policy.valid_until + timedelta(hours=1)
        ).isoformat(),
        "proposed_valid_until": (
            policy.valid_until + timedelta(days=7)
        ).isoformat(),
        "execution_mode": "research_only",
        "historical_epoch_rebind_approved": False,
        "collector_restart_approved": False,
        "council_dispatch_approved": False,
        "live_trading_approved": False,
    }

    plan.update({
        k: sha256(
            ("TEST-ONLY" + k).encode()
        ).hexdigest()
        for k in PLAN_DIGEST_FIELDS
    })

    path = tmp_path / "synthetic-plan.json"

    write(path, plan)

    return path, plan


def plan_review(sample, file):
    return inspect_new_epoch_plan(
        file,
        epoch_id=sample[1].epoch_id,
        policy_valid_until=sample[1].valid_until,
        as_of=ASOF,
    )


def test_s509_stage4_reinspection_consistent(sample):
    r = assess(sample)

    assert (
        r["stage4_local_receipt_scope"]["captures"]
        == 2
    )
    assert (
        r["stage4_local_receipt_scope"]["first_owned_ticks"]
        == 2
    )
    assert (
        r["stage4_local_receipt_scope"]["first_owned_bars"]
        == 1
    )

    assert (
        r["stage4_source_lists_cannot_be_reconstructed_from_first_owned_rows"]
        is True
    )


def test_s509_no_originals_cannot_recompute_digests(sample):
    r = assess(sample)

    assert r["locally_recomputed_original_list_hashes"] == 0

    assert (
        r["source_capture_candidates"]["status"]
        == "NOT_SUPPLIED"
    )

    assert (
        "NO_GENUINE_ORIGINAL_PER_CAPTURE_SOURCE_LISTS_SUPPLIED"
        in r["blockers"]
    )


def test_s509_genuine_ordered_list_path_recomputes_only_local_hashes(
    sample,
    tmp_path,
):
    root, index_file, _ = originals(
        sample,
        tmp_path,
    )

    r = assess(
        sample,
        original_index=index_file,
        evidence_root=root,
    )

    d = r["source_capture_candidates"]

    assert d["original_capture_files_hash_matched"] == 2
    assert d["capture_list_hashes_recomputed"] == 2
    assert d["original_tick_rows_replayed"] == 2

    # The database stores one first-owned bar.
    # Both original capture lists contained the same bar.
    assert d["original_bar_rows_replayed"] == 2

    assert d["all_inspected_capture_lists_supplied"] is True
    assert d["independent_payload_origin_verified"] is False


def test_s509_partial_originals_keep_coverage_blocker(
    sample,
    tmp_path,
):
    root, index_file, _ = originals(
        sample,
        tmp_path,
        both=False,
    )

    r = assess(
        sample,
        original_index=index_file,
        evidence_root=root,
    )

    assert (
        r["source_capture_candidates"][
            "all_inspected_capture_lists_supplied"
        ] is False
    )

    assert "ORIGINAL_CAPTURE_LIST_COVERAGE_PARTIAL" in r["blockers"]


def test_s509_unknown_capture_id_rejected(sample, tmp_path):
    root, idx, data = originals(sample, tmp_path)

    data["entries"][0]["capture_id"] = "a" * 64
    write(idx, data)

    with pytest.raises(
        Stage5CaptureError,
        match="not found",
    ):
        original_review(sample, root, idx)


def test_s509_duplicate_capture_id_rejected(sample, tmp_path):
    root, idx, data = originals(sample, tmp_path)

    data["entries"].append(
        data["entries"][0].copy()
    )

    write(idx, data)

    with pytest.raises(
        Stage5CaptureError,
        match="duplicate",
    ):
        original_review(sample, root, idx)


def test_s509_truncated_capture_scope_rejected(sample, tmp_path):
    root, idx, _ = originals(sample, tmp_path)

    with pytest.raises(
        Stage5CaptureError,
        match="truncated",
    ):
        inspect_original_capture_candidates(
            sample[0],
            index_file=idx,
            evidence_root=root,
            policy=sample[1],
            epoch_id=sample[1].epoch_id,
            as_of=ASOF,
            capture_limit=1,
        )


def test_s510_original_file_hash_tamper_rejected(
    sample,
    tmp_path,
):
    root, idx, _ = originals(sample, tmp_path)

    (
        root / "original-capture-0.json"
    ).write_text("tampered")

    with pytest.raises(
        Stage5CaptureError,
        match="file SHA-256",
    ):
        original_review(sample, root, idx)


def test_s510_altered_ordered_source_digest_rejected(
    sample,
    tmp_path,
):
    root, idx, data = originals(sample, tmp_path)

    path = root / "original-capture-0.json"

    payload = json.loads(
        path.read_text()
    )

    payload["tick_rows"][0]["bid"] = 1.05
    write(path, payload)

    data["entries"][0]["file_sha256"] = sha256(
        path.read_bytes()
    ).hexdigest()

    write(idx, data)

    with pytest.raises(
        Stage5CaptureError,
        match="list hashes",
    ):
        original_review(sample, root, idx)


def test_s510_wrong_source_list_count_rejected(
    sample,
    tmp_path,
):
    root, idx, data = originals(sample, tmp_path)

    path = root / "original-capture-0.json"

    payload = json.loads(
        path.read_text()
    )

    payload["bar_rows"] = []
    write(path, payload)

    data["entries"][0]["file_sha256"] = sha256(
        path.read_bytes()
    ).hexdigest()

    write(idx, data)

    with pytest.raises(
        Stage5CaptureError,
        match="counts",
    ):
        original_review(sample, root, idx)


def test_s510_document_is_not_broker_attestation(
    sample,
    tmp_path,
):
    root, idx, _ = originals(sample, tmp_path)

    r = assess(
        sample,
        original_index=idx,
        evidence_root=root,
    )

    assert r["independently_verified_broker_receipts"] is False
    assert r["independently_verified_capture_payload_origin"] is False
    assert r["final_action"] == "HOLD"


def test_s510_duplicate_json_key_rejected(sample, tmp_path):
    root, idx, _ = originals(sample, tmp_path)

    idx.write_text(
        '{"schema_version":1,"schema_version":1}',
        encoding="utf-8",
    )

    with pytest.raises(
        Stage5CaptureError,
        match="duplicate-key",
    ):
        original_review(sample, root, idx)


@pytest.mark.parametrize(
    "relative",
    [
        "../other",
        "C:/other",
        "/absolute",
        "folder/../other",
        "sub\\foo",
        "foo//bar",
        "./file",
    ],
)
def test_s510_path_traversal_rejected(
    sample,
    tmp_path,
    relative,
):
    root, idx, data = originals(sample, tmp_path)

    data["entries"][0]["relative_path"] = relative
    write(idx, data)

    with pytest.raises(
        Stage5CaptureError,
        match="path",
    ):
        original_review(sample, root, idx)


def test_s510_symlinked_original_refused(sample, tmp_path):
    root, idx, data = originals(sample, tmp_path)

    link = root / "linked.json"

    try:
        link.symlink_to(
            root / "original-capture-0.json"
        )
    except (OSError, NotImplementedError):
        pytest.skip(
            "Symlinks unavailable on host"
        )

    data["entries"][0]["relative_path"] = link.name
    write(idx, data)

    with pytest.raises(
        Stage5CaptureError,
        match="Symlinked",
    ):
        original_review(sample, root, idx)


def test_s510_no_unavailable_file_is_created(sample, tmp_path):
    root, idx, _ = originals(sample, tmp_path)

    missing = root / "original-capture-0.json"
    missing.unlink()

    with pytest.raises(
        Stage5CaptureError,
        match="regular",
    ):
        original_review(sample, root, idx)

    assert not missing.exists()


def test_s511_no_new_epoch_plan_is_no_approval(sample):
    result = assess(sample)

    assert (
        result["new_epoch_candidate_plan"]["status"]
        == "NOT_SUPPLIED"
    )
    assert result["new_epoch_approved"] is False

    assert (
        "NO_SEPARATELY_REVIEWED_NEW_EPOCH_PLAN_SUPPLIED"
        in result["blockers"]
    )


def test_s511_candidate_plan_is_never_operator_approval(
    sample,
    tmp_path,
):
    path, _ = candidate_plan(
        sample,
        tmp_path,
    )

    r = assess(
        sample,
        new_epoch_plan=path,
    )

    assert (
        r["new_epoch_candidate_plan"]["status"]
        == "LOCAL_PROPOSAL_CONTRACT_VALID_UNAPPROVED"
    )

    assert (
        r["new_epoch_candidate_plan"][
            "operator_approval_independently_verified"
        ] is False
    )

    assert (
        r["new_epoch_candidate_plan"][
            "document_references_present"
        ] == len(PLAN_DIGEST_FIELDS)
    )

    assert r["new_epoch_approved"] is False


def test_s511_prepared_in_future_rejected(sample, tmp_path):
    path, obj = candidate_plan(sample, tmp_path)

    obj["prepared_at"] = (
        ASOF + timedelta(seconds=1)
    ).isoformat()

    write(path, obj)

    with pytest.raises(
        Stage5CaptureError,
        match="timing",
    ):
        plan_review(sample, path)


def test_s511_overlapping_historical_epoch_rejected(
    sample,
    tmp_path,
):
    path, obj = candidate_plan(sample, tmp_path)

    obj["proposed_valid_from"] = (
        sample[1].valid_until - timedelta(seconds=1)
    ).isoformat()

    write(path, obj)

    with pytest.raises(
        Stage5CaptureError,
        match="timing",
    ):
        plan_review(sample, path)


def test_s511_historical_epoch_id_mismatch_rejected(
    sample,
    tmp_path,
):
    path, obj = candidate_plan(sample, tmp_path)

    obj["historical_epoch_id"] = "f" * 64
    write(path, obj)

    with pytest.raises(
        Stage5CaptureError,
        match="historical binding",
    ):
        plan_review(sample, path)


def test_s511_more_than_30_day_lease_rejected(
    sample,
    tmp_path,
):
    path, obj = candidate_plan(sample, tmp_path)

    start = _utc(
        obj["proposed_valid_from"]
    )

    obj["proposed_valid_until"] = (
        start + timedelta(days=31)
    ).isoformat()

    write(path, obj)

    with pytest.raises(
        Stage5CaptureError,
        match="timing",
    ):
        plan_review(sample, path)


@pytest.mark.parametrize(
    "field",
    [
        "historical_epoch_rebind_approved",
        "collector_restart_approved",
        "council_dispatch_approved",
        "live_trading_approved",
    ],
)
def test_s511_true_action_flag_refused(
    sample,
    tmp_path,
    field,
):
    path, obj = candidate_plan(sample, tmp_path)

    obj[field] = True
    write(path, obj)

    with pytest.raises(
        Stage5CaptureError,
        match="unauthorized",
    ):
        plan_review(sample, path)


def test_s511_invalid_reference_digest_rejected(
    sample,
    tmp_path,
):
    path, obj = candidate_plan(sample, tmp_path)

    obj[
        "broker_identity_evidence_sha256"
    ] = "not-a-sha"

    write(path, obj)

    with pytest.raises(
        Stage5CaptureError,
        match="SHA-256",
    ):
        plan_review(sample, path)


def test_s511_only_research_mode_allowed(
    sample,
    tmp_path,
):
    path, obj = candidate_plan(sample, tmp_path)

    obj["execution_mode"] = "live"
    write(path, obj)

    with pytest.raises(
        Stage5CaptureError,
        match="unauthorized",
    ):
        plan_review(sample, path)


def test_s512_select_only_statements(sample, tmp_path):
    root, idx, _ = originals(
        sample,
        tmp_path,
    )

    seen = []

    def listener(
        _conn,
        _cursor,
        statement,
        _params,
        _context,
        _many,
    ):
        seen.append(
            statement.strip().upper()
        )

    event.listen(
        sample[0],
        "before_cursor_execute",
        listener,
    )

    try:
        assess(
            sample,
            original_index=idx,
            evidence_root=root,
        )
    finally:
        event.remove(
            sample[0],
            "before_cursor_execute",
            listener,
        )

    assert seen
    assert all(
        sql.startswith("SELECT")
        for sql in seen
    )


def test_s512_deterministic_hash(sample):
    a = assess(sample)
    b = assess(sample)

    assert a == b

    assert a["report_sha256"] == hashed({
        k: v
        for k, v in a.items()
        if k != "report_sha256"
    })


def test_s512_no_actions_authorized(sample):
    report = assess(sample)

    for flag in (
        "database_modified",
        "files_written",
        "broker_connected",
        "collector_started",
        "historical_epoch_rebound",
        "new_epoch_created",
        "council_invoked",
        "training_authorized",
        "execution_authorized",
        "independently_verified_recovery_approval",
        "new_epoch_approved",
    ):
        assert report[flag] is False

    assert report["final_action"] == "HOLD"


def test_s512_cli_requires_readonly_optins():
    with pytest.raises(
        SystemExit
    ) as error:
        run([
            "--epoch-id",
            "a" * 64,
        ])

    assert error.value.code == 2


def test_s512_cli_bad_epoch_fails_before_db(capsys):
    code = run([
        "--read-only-db",
        "--read-only-evidence",
        "--epoch-id",
        "BAD",
        "--as-of",
        ASOF.isoformat(),
        "--policy-file",
        "missing",
        "--expected-policy-sha256",
        "a" * 64,
    ])

    assert code == 1

    assert (
        "BLOCKED"
        in capsys.readouterr().out
    )


def test_s512_past_scope_reinspection_untouched(sample):
    result = assess(sample)

    assert result["read_only"] is True

    assert (
        result["stage4_local_receipt_scope"]["captures"]
        == 2
    )

    assert result["stage4_local_receipt_scope"]["truncated"] == {
        "captures": False,
        "ticks": False,
        "bars": False,
    }


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-27T14:00:20",
        "invalid",
        "2026-10-27T17:00:20+03:00",
    ],
)
def test_s512_non_utc_rejected(value):
    with pytest.raises(Stage5CaptureError):
        _utc(value)

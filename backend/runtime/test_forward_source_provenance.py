
"""P3-09..P3-12 synthetic provenance tests; no MT5 or live database."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest
from sqlalchemy import create_engine, event

from backend.data_engine.clock_policy import (
    ClockPolicy, hfm_utc_to_wall, implementation_sha256, utc_msc,
)
from backend.data_engine.forward_store import (
    ForwardFeedStore, bars, captures, epochs, ticks,
)
from backend.runtime.forward_source_provenance import (
    SourceProvenanceError, _identity_candidate, _policy_copy,
    inspect_forward_source_provenance, run,
)

NOW = datetime(2026, 10, 27, 14, 0, tzinfo=timezone.utc)


def _fixture(*, count=1):
    engine = create_engine("sqlite://")
    clock = [NOW]
    store = ForwardFeedStore(engine, clock=lambda: clock[0])
    store.initialize()

    policy = ClockPolicy(
        company_sha256=sha256(b"FAKE-COMPANY").hexdigest(),
        server_sha256=sha256(b"FAKE-SERVER").hexdigest(),
        evidence_sha256=sha256(b"FAKE-EVIDENCE").hexdigest(),
        implementation_sha256=implementation_sha256(),
        valid_from=NOW - timedelta(hours=1),
        valid_until=NOW + timedelta(days=1),
    )

    for i in range(count):
        now = NOW + timedelta(minutes=5 * i)
        clock[0] = now
        moment = now - timedelta(seconds=2)
        stamp = utc_msc(hfm_utc_to_wall(moment))

        raw_tick = dict(
            time=stamp // 1000,
            time_msc=stamp,
            bid=1.1,
            ask=1.1001,
            last=0.0,
            volume=0,
            flags=6,
            volume_real=0.0,
        )

        open_time = (
            utc_msc(
                hfm_utc_to_wall(
                    now - timedelta(minutes=5)
                )
            ) // 1000
        )

        raw_bar = dict(
            time=open_time,
            open=1.1,
            high=1.101,
            low=1.099,
            close=1.1005,
            tick_volume=10,
            spread=10,
            real_volume=0,
        )

        store.append(
            policy,
            tick_rows=[policy.tick(raw_tick)],
            bar_rows=[policy.rate(raw_bar)],
            received_at=now,
            tick_start=now - (
                timedelta(minutes=1)
                if i == 0 else timedelta(minutes=5)
            ),
            tick_end=now,
            bar_start=now - timedelta(minutes=10),
            bar_end=now,
        )

    return engine, policy, clock[0]


@pytest.fixture
def demo(tmp_path):
    engine, policy, last = _fixture()
    path = tmp_path / "original-policy-any-filename.json"
    path.write_text(
        policy.model_dump_json(),
        encoding="utf-8",
    )
    try:
        yield engine, policy, last, path
    finally:
        engine.dispose()


def inspect(demo, *, cutoff=None, **kwargs):
    engine, policy, now, path = demo
    return inspect_forward_source_provenance(
        engine,
        epoch_id=policy.epoch_id,
        as_of=cutoff or now,
        policy_file=path,
        **kwargs,
    )


def test_p309_matching_copy_is_canonical_not_original_attestation(demo):
    result = inspect(demo)

    assert (
        result["policy_file"]["status"]
        == "LOCAL_CANONICAL_COPY_MATCH_UNATTESTED"
    )
    assert result["policy_file"]["canonical_epoch_match"] is True
    assert (
        result["policy_file"]["original_creation_path_attested"]
        is False
    )
    assert len(result["policy_file"]["file_sha256"]) == 64


def test_p309_missing_policy_does_not_regenerate(demo, tmp_path):
    missing = tmp_path / "missing.json"
    result = _policy_copy(demo[1], missing)

    assert result["status"] == "UNAVAILABLE_OR_INVALID"
    assert not missing.exists()


def test_p309_mismatched_policy_fails_closed(demo, tmp_path):
    different = demo[1].model_copy(
        update={"server_sha256": "a" * 64}
    )
    path = tmp_path / "other.json"
    path.write_text(different.model_dump_json())

    result = inspect_forward_source_provenance(
        demo[0],
        epoch_id=demo[1].epoch_id,
        as_of=demo[2],
        policy_file=path,
    )

    assert result["policy_file"]["canonical_epoch_match"] is False
    assert (
        "EXACT_STORED_POLICY_COPY_NOT_ESTABLISHED"
        in result["local_structural_blockers"]
    )


def test_p309_oversize_file_untrusted(demo, tmp_path):
    path = tmp_path / "large.json"
    path.write_bytes(b"x" * 16385)

    assert (
        _policy_copy(demo[1], path)["status"]
        == "UNAVAILABLE_OR_INVALID"
    )


def test_p309_db_policy_tampering_rejected(demo):
    with demo[0].begin() as connection:
        connection.execute(
            epochs.update().values(policy_json="{}")
        )

    with pytest.raises(
        SourceProvenanceError,
        match="Stored policy JSON invalid",
    ):
        inspect(demo)


def test_p310_identity_candidate_not_provided_is_not_certification(demo):
    result = inspect(demo)

    assert (
        result["optional_identity_candidate"]["status"]
        == "NOT_PROVIDED"
    )
    assert (
        result["broker_identity_fields"][
            "independent_broker_identity_attested"
        ]
        is False
    )
    assert (
        result["broker_identity_fields"][
            "independent_clock_offset_attested"
        ]
        is False
    )


def test_p310_matching_identity_hash_not_broker_attestation(demo, tmp_path):
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps({
        "company": "FAKE-COMPANY",
        "server": "FAKE-SERVER",
    }))

    result = inspect(demo, identity_file=path)

    assert (
        result["optional_identity_candidate"]["status"]
        == "LOCAL_IDENTITY_HASH_MATCH_UNATTESTED"
    )
    assert (
        result["optional_identity_candidate"][
            "independently_signed_or_attested"
        ]
        is False
    )
    assert "FAKE-COMPANY" not in json.dumps(result)
    assert "FAKE-SERVER" not in json.dumps(result)


def test_p310_wrong_identity_produces_blocker(demo, tmp_path):
    path = tmp_path / "wrong.json"
    path.write_text(json.dumps({
        "company": "FAKE-COMPANY",
        "server": "WRONG",
    }))

    result = inspect(demo, identity_file=path)

    assert (
        "IDENTITY_CANDIDATE_DIFFERS_FROM_POLICY"
        in result["local_structural_blockers"]
    )


def test_p310_identity_extra_sensitive_fields_rejected(demo, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({
        "company": "FAKE-COMPANY",
        "server": "FAKE-SERVER",
        "password": "secret",
    }))

    result = _identity_candidate(demo[1], path)

    assert result["status"] == "UNAVAILABLE_OR_INVALID"
    assert "secret" not in json.dumps(result)


def test_p311_visible_capture_tick_and_bar_have_consistent_receipts(demo):
    result = inspect(demo)

    assert result["capture_sample"]["captures_examined"] == 1
    assert result["capture_sample"]["ticks_examined"] == 1
    assert result["capture_sample"]["bars_examined"] == 1
    assert (
        result["capture_sample"][
            "raw_rows_checked_without_clock_reinterpretation"
        ]
        == 2
    )
    assert result["local_structural_blockers"] == []


def test_p311_tick_source_digest_tamper_detected(demo):
    with demo[0].begin() as connection:
        connection.execute(
            ticks.update().values(bid=1.2)
        )

    result = inspect(demo)

    assert (
        "TICK_RAW_PROVENANCE_MISMATCH"
        in result["local_structural_blockers"]
    )


def test_p311_bar_source_price_tamper_detected(demo):
    with demo[0].begin() as connection:
        connection.execute(
            bars.update().values(close=1.2)
        )

    assert (
        "BAR_RAW_PROVENANCE_MISMATCH"
        in inspect(demo)["local_structural_blockers"]
    )


def test_p311_capture_manifest_hash_tamper_detected(demo):
    with demo[0].begin() as connection:
        connection.execute(
            captures.update().values(manifest_json="{}")
        )

    assert (
        "CAPTURE_MANIFEST_DIGEST_INVALID"
        in inspect(demo)["local_structural_blockers"]
    )


def test_p311_first_capture_receipt_mismatch_detected(demo):
    with demo[0].begin() as connection:
        connection.execute(
            ticks.update().values(
                received_at=NOW - timedelta(seconds=1)
            )
        )

    result = inspect(demo)

    assert (
        "SOURCE_FIRST_CAPTURE_RECEIPT_MISMATCH"
        in result["local_structural_blockers"]
    )


def test_p311_no_future_source_rows_in_past_inspection(tmp_path):
    engine, policy, last = _fixture(count=2)
    path = tmp_path / "policy.json"
    path.write_text(policy.model_dump_json())

    try:
        result = inspect_forward_source_provenance(
            engine,
            epoch_id=policy.epoch_id,
            as_of=NOW,
            policy_file=path,
        )

        assert result["capture_sample"]["captures_examined"] == 1
        assert result["capture_sample"]["ticks_examined"] == 1
        assert result["capture_sample"]["bars_examined"] == 1
    finally:
        engine.dispose()


def test_p311_truncation_not_claimed_complete(tmp_path):
    engine, policy, last = _fixture(count=3)
    path = tmp_path / "policy.json"
    path.write_text(policy.model_dump_json())

    try:
        result = inspect_forward_source_provenance(
            engine,
            epoch_id=policy.epoch_id,
            as_of=last,
            policy_file=path,
            max_captures=2,
            max_rows=2,
        )

        assert result["capture_sample"]["sample_truncated"] == {
            "captures": True,
            "ticks": True,
            "bars": True,
        }
        assert (
            "BOUNDED_EVIDENCE_SAMPLE_INCOMPLETE"
            in result["local_structural_blockers"]
        )
    finally:
        engine.dispose()


def test_p311_later_cutoff_marks_stale(demo):
    result = inspect(
        demo,
        cutoff=demo[2] + timedelta(minutes=20),
    )

    assert (
        "CAPTURE_NOT_CURRENT_AT_CUTOFF"
        in result["local_structural_blockers"]
    )


def test_p312_output_deterministic_at_fixed_cutoff(demo):
    first = inspect(demo)
    second = inspect(demo)

    assert first == second

    from backend.data_engine.forward_store import hashed

    assert first["report_sha256"] == hashed({
        key: value
        for key, value in first.items()
        if key != "report_sha256"
    })


def test_p312_no_source_attestation_or_authorized_activity(demo):
    result = inspect(demo)

    assert len(result["external_certification_gaps"]) == 5
    assert (
        result["capture_sample"][
            "independent_first_receipt_attested"
        ]
        is False
    )
    assert result["real_data_admission"] is False
    assert result["training_authorized"] is False
    assert result["execution_authorized"] is False
    assert result["collector_started"] is False
    assert result["final_action"] == "HOLD"


def test_p312_select_only_sql_for_real_inspector(demo):
    statements = []

    def track(_cn, _cur, sql, _params, _ctx, _many):
        statements.append(sql.strip().upper())

    event.listen(
        demo[0],
        "before_cursor_execute",
        track,
    )
    try:
        inspect(demo)
    finally:
        event.remove(
            demo[0],
            "before_cursor_execute",
            track,
        )

    assert statements
    assert all(
        statement.startswith("SELECT")
        for statement in statements
    )


@pytest.mark.parametrize(
    "value",
    [0, 101, True, "32", 4.5],
)
def test_p312_invalid_capture_limits_rejected(demo, value):
    with pytest.raises(
        SourceProvenanceError,
        match="max_captures",
    ):
        inspect(demo, max_captures=value)


def test_p312_wrong_epoch_blocked(demo):
    with pytest.raises(
        SourceProvenanceError,
        match="Epoch not visible",
    ):
        inspect_forward_source_provenance(
            demo[0],
            epoch_id="f" * 64,
            as_of=demo[2],
        )


def test_p312_cli_requires_read_only_optin():
    with pytest.raises(SystemExit) as error:
        run([
            "--epoch-id",
            "f" * 64,
            "--as-of",
            NOW.isoformat(),
        ])
    assert error.value.code == 2


def test_p312_invalid_epoch_cli_does_not_connect(capsys):
    code = run([
        "--read-only-db",
        "--epoch-id",
        "bad",
        "--as-of",
        NOW.isoformat(),
    ])
    assert code == 1
    assert (
        "PROVENANCE BLOCKED"
        in capsys.readouterr().out
    )


def test_p309_observed_policy_sha_stays_stable(demo):
    correct = sha256(demo[3].read_bytes()).hexdigest()

    result = inspect(
        demo,
        expected_policy_sha256=correct,
    )
    assert (
        result["policy_file"]["observed_digest_matches_expected"]
        is True
    )

    changed = inspect(
        demo,
        expected_policy_sha256="f" * 64,
    )
    assert (
        changed["policy_file"]["observed_digest_matches_expected"]
        is False
    )
    assert (
        "POLICY_FILE_CHANGED_FROM_PREVIOUSLY_OBSERVED_SHA256"
        in changed["local_structural_blockers"]
    )


def test_p309_invalid_expected_digest_refused(demo):
    with pytest.raises(
        SourceProvenanceError,
        match="Expected policy",
    ):
        inspect(
            demo,
            expected_policy_sha256="bad",
        )


"""S4-17..20: synthetic-only tests of local evidence custody."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest

from backend.runtime.stage4_prospective_evidence_custody import (
    EvidenceCustodyError,
    GATES,
    _digest,
    assess_stage4_prospective_custody,
    inspect_evidence_manifest,
    run,
)


DECISION = datetime(
    2026, 10, 27, 15, 1, 43,
    tzinfo=timezone.utc,
)

ASOF = datetime(
    2026, 10, 29, 17, 26, 42,
    tzinfo=timezone.utc,
)

EPOCH = "7" * 64

SUBJECT = sha256(
    b"SYNTHETIC-ONLY-PREDICTION-ID"
).hexdigest()


def ts(value):
    return value.isoformat()


def args(**kwargs):
    return dict(
        epoch_id=EPOCH,
        decision_at=ts(DECISION),
        as_of=ts(ASOF),
        **kwargs,
    )


def create_manifest(tmp_path, gates=None):
    root = tmp_path / "evidence"
    root.mkdir()

    items = []

    gates = tuple(
        gates if gates is not None
        else (g for g, _ in GATES)
    )

    for gate in gates:
        artifact = root / (gate + ".txt")

        artifact.write_text(
            "SYNTHETIC UNIT TEST " + gate,
            encoding="utf-8",
        )

        when = {
            "prediction_registration":
                DECISION - timedelta(minutes=1),
            "post_horizon_outcome":
                DECISION + timedelta(minutes=6),
            "broker_cost_fills":
                DECISION + timedelta(minutes=7),
            "holdout_freeze":
                DECISION - timedelta(days=1),
        }[gate]

        items.append({
            "gate": gate,
            "artifact_id": "SYNTH-" + gate,
            "relative_path": artifact.name,
            "file_sha256": sha256(
                artifact.read_bytes()
            ).hexdigest(),
            "claimed_issuer": "SYNTHETIC-TEST-NOT-BROKER",
            "claimed_event_at": ts(when),
            "subject_sha256": (
                SUBJECT
                if gate != "holdout_freeze"
                else "f" * 64
            ),
        })

    manifest = {
        "schema_version": 1,
        "epoch_id": EPOCH,
        "decision_at": ts(DECISION),
        "as_of": ts(ASOF),
        "horizon_seconds": 300,
        "heldout_start": ts(
            DECISION - timedelta(hours=1)
        ),
        "heldout_end": ts(
            DECISION + timedelta(days=1)
        ),
        "artifacts": items,
    }

    file = tmp_path / "manifest.json"

    file.write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    return file, root, manifest


def save(file, data):
    file.write_text(
        json.dumps(data),
        encoding="utf-8",
    )


def p3_file(tmp_path, **changes):
    obj = {
        "assessment_version":
            "EURUSD_P3_PROSPECTIVE_EVALUATION_V1",
        "epoch_id": EPOCH,
        "decision_at": ts(DECISION),
        "as_of": ts(ASOF),
        "market_lineage_sha256": "c" * 64,
        "registration_count_in_scope": 0,
        "post_horizon_outcomes_in_scope": 0,
        "cost_records_in_scope": 0,
        "independent_prediction_registration_attested": False,
        "independent_outcome_source_attested": False,
        "broker_fill_and_costs_attested": False,
        "heldout_split_independently_frozen": False,
        "external_timestamp_anchor_verified": False,
        "performance_measured": False,
        "transaction_costs_measured": False,
        "training_authorized": False,
        "execution_authorized": False,
        "council_invoked": False,
        "out_of_sample_trades_measured": None,
        "read_only": True,
        "final_action": "HOLD",
    }

    obj.update(changes)
    obj["report_sha256"] = _digest(obj)

    file = tmp_path / "existing-p3-report.json"

    save(file, obj)

    return file


def check_doc(file, root):
    return inspect_evidence_manifest(
        file,
        root=root,
        epoch_id=EPOCH,
        decision_at=DECISION,
        as_of=ASOF,
    )


def test_s417_empty_scope_never_fabricates_registrations():
    r = assess_stage4_prospective_custody(
        **args()
    )

    assert (
        r["existing_artifact_custody"]["status"]
        == "NOT_SUPPLIED"
    )
    assert (
        r["phase3_evaluation"][
            "registration_count_in_scope"
        ] is None
    )
    assert (
        r["prediction_records_independently_registered"]
        is False
    )
    assert r["final_action"] == "HOLD"


def test_s417_existing_synthetic_document_hash_only(tmp_path):
    file, root, _ = create_manifest(tmp_path)

    r = assess_stage4_prospective_custody(
        **args(
            evidence_manifest=file,
            evidence_root=root,
        )
    )

    assert (
        r["existing_artifact_custody"][
            "files_locally_hash_matched"
        ] == 4
    )
    assert (
        r["existing_artifact_custody"][
            "independently_attested_gates"
        ] == 0
    )
    assert r["external_gates_independently_verified"] == 0


def test_s417_predecision_claim_not_external_timestamp(tmp_path):
    file, root, _ = create_manifest(
        tmp_path,
        ["prediction_registration"],
    )

    r = check_doc(file, root)

    assert r["gate_names_present"] == [
        "prediction_registration"
    ]
    assert (
        r["status"]
        == "LOCAL_ARTIFACT_BYTES_LINKED_UNATTESTED"
    )


def test_s417_late_prediction_registration_rejected(tmp_path):
    file, root, doc = create_manifest(
        tmp_path,
        ["prediction_registration"],
    )

    doc["artifacts"][0]["claimed_event_at"] = ts(
        DECISION + timedelta(seconds=1)
    )

    save(file, doc)

    with pytest.raises(
        EvidenceCustodyError,
        match="after decision",
    ):
        check_doc(file, root)


def test_s417_subject_digest_not_a_registration(tmp_path):
    file, root, _ = create_manifest(tmp_path)

    r = assess_stage4_prospective_custody(
        **args(
            evidence_manifest=file,
            evidence_root=root,
        )
    )

    assert (
        r["prediction_records_independently_registered"]
        is False
    )
    assert r["performance_measured"] is False
    assert r["out_of_sample_trades_measured"] is None


def test_s418_outcome_at_pre_horizon_rejected(tmp_path):
    file, root, doc = create_manifest(
        tmp_path,
        ["post_horizon_outcome"],
    )

    doc["artifacts"][0]["claimed_event_at"] = ts(
        DECISION + timedelta(minutes=4)
    )

    save(file, doc)

    with pytest.raises(
        EvidenceCustodyError,
        match="before horizon",
    ):
        check_doc(file, root)


def test_s418_posthorizon_claim_not_independent_outcome(tmp_path):
    file, root, _ = create_manifest(
        tmp_path,
        ["post_horizon_outcome"],
    )

    r = assess_stage4_prospective_custody(
        **args(
            evidence_manifest=file,
            evidence_root=root,
        )
    )

    assert (
        r["post_horizon_outcomes_independently_attested"]
        is False
    )
    assert (
        "holdout_freeze"
        in r["existing_artifact_custody"]["gate_names_missing"]
    )


def test_s418_future_claim_rejected(tmp_path):
    file, root, doc = create_manifest(
        tmp_path,
        ["post_horizon_outcome"],
    )

    doc["artifacts"][0]["claimed_event_at"] = ts(
        ASOF + timedelta(seconds=1)
    )

    save(file, doc)

    with pytest.raises(
        EvidenceCustodyError,
        match="after review cutoff",
    ):
        check_doc(file, root)


def test_s418_outcome_registration_subject_mismatch_rejected(
    tmp_path,
):
    file, root, doc = create_manifest(
        tmp_path,
        [
            "prediction_registration",
            "post_horizon_outcome",
        ],
    )

    doc["artifacts"][1]["subject_sha256"] = "d" * 64

    save(file, doc)

    with pytest.raises(
        EvidenceCustodyError,
        match="digest mismatch",
    ):
        check_doc(file, root)


def test_s419_cost_document_without_fill_authentication(tmp_path):
    file, root, _ = create_manifest(
        tmp_path,
        ["broker_cost_fills"],
    )

    r = assess_stage4_prospective_custody(
        **args(
            evidence_manifest=file,
            evidence_root=root,
        )
    )

    assert (
        r["broker_executions_or_costs_independently_attested"]
        is False
    )
    assert r["transaction_costs_measured"] is False


def test_s419_claimed_cost_before_horizon_rejected(tmp_path):
    file, root, doc = create_manifest(
        tmp_path,
        ["broker_cost_fills"],
    )

    doc["artifacts"][0]["claimed_event_at"] = ts(
        DECISION
    )

    save(file, doc)

    with pytest.raises(
        EvidenceCustodyError,
        match="before horizon",
    ):
        check_doc(file, root)


def test_s419_tampered_cost_file_rejected(tmp_path):
    file, root, _ = create_manifest(
        tmp_path,
        ["broker_cost_fills"],
    )

    (
        root / "broker_cost_fills.txt"
    ).write_text("TAMPERED")

    with pytest.raises(
        EvidenceCustodyError,
        match="file hash mismatch",
    ):
        check_doc(file, root)


def test_s420_holdout_freeze_after_start_rejected(tmp_path):
    file, root, doc = create_manifest(
        tmp_path,
        ["holdout_freeze"],
    )

    doc["artifacts"][0]["claimed_event_at"] = (
        doc["heldout_start"]
    )

    save(file, doc)

    with pytest.raises(
        EvidenceCustodyError,
        match="freeze not before",
    ):
        check_doc(file, root)


def test_s420_claimed_frozen_split_not_independent(tmp_path):
    file, root, _ = create_manifest(
        tmp_path,
        ["holdout_freeze"],
    )

    r = assess_stage4_prospective_custody(
        **args(
            evidence_manifest=file,
            evidence_root=root,
        )
    )

    assert r["holdout_independently_frozen"] is False
    assert r["external_gates_independently_verified"] == 0


def test_s420_holdout_interval_must_include_decision(tmp_path):
    file, root, doc = create_manifest(tmp_path)

    doc["heldout_start"] = ts(
        DECISION + timedelta(minutes=1)
    )

    save(file, doc)

    with pytest.raises(
        EvidenceCustodyError,
        match="interval/horizon",
    ):
        check_doc(file, root)


def test_s420_horizon_cannot_extend_beyond_asof(tmp_path):
    file, root, doc = create_manifest(tmp_path)

    cutoff = DECISION + timedelta(seconds=180)

    doc["as_of"] = ts(cutoff)
    doc["heldout_end"] = ts(
        DECISION + timedelta(seconds=120)
    )

    save(file, doc)

    with pytest.raises(
        EvidenceCustodyError,
        match="interval/horizon",
    ):
        inspect_evidence_manifest(
            file,
            root=root,
            epoch_id=EPOCH,
            decision_at=DECISION,
            as_of=cutoff,
        )


def test_s420_boolean_horizon_rejected(tmp_path):
    file, root, doc = create_manifest(tmp_path)

    doc["horizon_seconds"] = True

    save(file, doc)

    with pytest.raises(
        EvidenceCustodyError,
        match="horizon",
    ):
        check_doc(file, root)


def test_s420_manifest_invalid_version_rejected(tmp_path):
    file, root, doc = create_manifest(tmp_path)

    doc["schema_version"] = 2

    save(file, doc)

    with pytest.raises(
        EvidenceCustodyError,
        match="version/scope",
    ):
        check_doc(file, root)


def test_s420_manifest_duplicate_gate_rejected(tmp_path):
    file, root, doc = create_manifest(tmp_path)

    doc["artifacts"][1]["gate"] = (
        "prediction_registration"
    )

    save(file, doc)

    with pytest.raises(
        EvidenceCustodyError,
        match="duplicate evidence gate",
    ):
        check_doc(file, root)


def test_s420_manifest_bad_artifact_sha_rejected(tmp_path):
    file, root, doc = create_manifest(tmp_path)

    doc["artifacts"][0]["file_sha256"] = (
        "not-a-hash"
    )

    save(file, doc)

    with pytest.raises(
        EvidenceCustodyError,
        match="SHA-256",
    ):
        check_doc(file, root)


@pytest.mark.parametrize(
    "relative",
    (
        "../other",
        "/absolute",
        "sub/../other",
        "C:/Windows/file",
        "sub\\file",
        "sub//file",
        ".hidden/./file",
    ),
)
def test_s420_path_escapes_rejected(tmp_path, relative):
    file, root, doc = create_manifest(tmp_path)

    doc["artifacts"][0]["relative_path"] = relative

    save(file, doc)

    with pytest.raises(
        EvidenceCustodyError,
        match="path",
    ):
        check_doc(file, root)


def test_s420_absent_root_when_manifest_given_rejected(tmp_path):
    file, root, _ = create_manifest(tmp_path)

    with pytest.raises(
        EvidenceCustodyError,
        match="root required",
    ):
        inspect_evidence_manifest(
            file,
            epoch_id=EPOCH,
            decision_at=DECISION,
            as_of=ASOF,
        )


def test_s420_symlinked_artifact_rejected(tmp_path):
    file, root, doc = create_manifest(tmp_path)

    target = root / doc["artifacts"][0]["relative_path"]
    link = root / "artifact-link.txt"

    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip(
            "Symlink creation unavailable"
        )

    doc["artifacts"][0]["relative_path"] = link.name

    save(file, doc)

    with pytest.raises(
        EvidenceCustodyError,
        match="Symlinked",
    ):
        check_doc(file, root)


def test_s420_missing_artifact_is_not_created(tmp_path):
    file, root, doc = create_manifest(tmp_path)

    filename = root / doc["artifacts"][0]["relative_path"]
    filename.unlink()

    with pytest.raises(
        EvidenceCustodyError,
        match="missing",
    ):
        check_doc(file, root)

    assert not filename.exists()


def test_s420_duplicate_json_keys_rejected(tmp_path):
    file = tmp_path / "dup.json"

    file.write_text(
        '{"schema_version":1,"schema_version":2}'
    )

    with pytest.raises(
        EvidenceCustodyError,
        match="Duplicate JSON",
    ):
        inspect_evidence_manifest(
            file,
            root=tmp_path,
            epoch_id=EPOCH,
            decision_at=DECISION,
            as_of=ASOF,
        )


def test_s420_oversized_artifact_refused(tmp_path):
    file, root, doc = create_manifest(tmp_path)

    first = root / doc["artifacts"][0]["relative_path"]
    first.write_bytes(
        b"a" * 1048577
    )

    doc["artifacts"][0]["file_sha256"] = sha256(
        first.read_bytes()
    ).hexdigest()

    save(file, doc)

    with pytest.raises(
        EvidenceCustodyError,
        match="size bound",
    ):
        check_doc(file, root)


def test_s420_repeated_inspection_deterministic(tmp_path):
    file, root, _ = create_manifest(tmp_path)

    kw = args(
        evidence_manifest=file,
        evidence_root=root,
    )

    a = assess_stage4_prospective_custody(**kw)
    b = assess_stage4_prospective_custody(**kw)

    assert a == b

    assert a["report_sha256"] == _digest({
        k: v
        for k, v in a.items()
        if k != "report_sha256"
    })


def test_s420_local_phase3_report_not_independent(tmp_path):
    p = p3_file(tmp_path)

    result = assess_stage4_prospective_custody(
        **args(
            phase3_report=p,
        )
    )

    assert (
        result["phase3_evaluation"][
            "registration_count_in_scope"
        ] == 0
    )
    assert (
        result["phase3_evaluation"]["independent_attestation"]
        is False
    )
    assert result["final_action"] == "HOLD"


def test_s420_tampered_p3_report_rejected(tmp_path):
    p = p3_file(tmp_path)
    obj = json.loads(p.read_text())

    obj["registration_count_in_scope"] = 5

    save(p, obj)

    with pytest.raises(
        EvidenceCustodyError,
        match="report hash mismatch",
    ):
        assess_stage4_prospective_custody(
            **args(
                phase3_report=p,
            )
        )


def test_s420_unauthorized_p3_report_even_when_rehashed(tmp_path):
    p = p3_file(
        tmp_path,
        training_authorized=True,
    )

    with pytest.raises(
        EvidenceCustodyError,
        match="unauthorized",
    ):
        assess_stage4_prospective_custody(
            **args(
                phase3_report=p,
            )
        )


def test_s420_mismatched_p3_cutoff_rejected(tmp_path):
    p = p3_file(
        tmp_path,
        as_of=ts(
            ASOF - timedelta(seconds=1)
        ),
    )

    with pytest.raises(
        EvidenceCustodyError,
        match="scope/HOLD",
    ):
        assess_stage4_prospective_custody(
            **args(
                phase3_report=p,
            )
        )


def test_s420_every_gate_is_unverified_with_all_four_files(
    tmp_path,
):
    file, root, _ = create_manifest(tmp_path)

    r = assess_stage4_prospective_custody(
        **args(
            phase3_report=p3_file(tmp_path),
            evidence_manifest=file,
            evidence_root=root,
        )
    )

    assert not any(
        g["independently_verified"]
        for g in r["external_gates"]
    )

    assert r["council_seats_run"] == 0

    for key in (
        "files_modified",
        "database_accessed",
        "network_accessed",
        "collector_started",
        "council_invoked",
        "execution_authorized",
        "training_authorized",
    ):
        assert r[key] is False


def test_s420_cli_requires_readonly_optins():
    with pytest.raises(SystemExit) as err:
        run([
            "--epoch-id",
            EPOCH,
            "--decision-at",
            ts(DECISION),
            "--as-of",
            ts(ASOF),
        ])

    assert err.value.code == 2


def test_s420_cli_incomplete_id_fails_before_files(capsys):
    rc = run([
        "--read-only-evidence",
        "--read-only-phase3",
        "--epoch-id",
        "invalid",
        "--decision-at",
        ts(DECISION),
        "--as-of",
        ts(ASOF),
    ])

    assert rc == 1
    assert (
        "BLOCKED"
        in capsys.readouterr().out
    )


def test_s420_cli_json_has_valid_hash(capsys):
    rc = run([
        "--read-only-evidence",
        "--read-only-phase3",
        "--epoch-id",
        EPOCH,
        "--decision-at",
        ts(DECISION),
        "--as-of",
        ts(ASOF),
        "--json",
    ])

    assert rc == 0

    report = json.loads(
        capsys.readouterr().out
    )

    assert report["report_sha256"] == _digest({
        k: v
        for k, v in report.items()
        if k != "report_sha256"
    })

    assert report["final_action"] == "HOLD"


def test_s420_docs_not_misrepresented_as_phase3_registered(
    tmp_path,
):
    file, root, _ = create_manifest(tmp_path)

    result = assess_stage4_prospective_custody(
        **args(
            evidence_manifest=file,
            evidence_root=root,
            phase3_report=p3_file(tmp_path),
        )
    )

    assert (
        "ARTIFACT_NOT_RECONCILED_TO_PHASE3_SCOPE:prediction_registration"
        in result["blockers"]
    )
    assert (
        "ARTIFACT_NOT_RECONCILED_TO_PHASE3_SCOPE:post_horizon_outcome"
        in result["blockers"]
    )
    assert (
        "ARTIFACT_NOT_RECONCILED_TO_PHASE3_SCOPE:broker_cost_fills"
        in result["blockers"]
    )

    assert result["final_action"] == "HOLD"


"""Stage 7 external evidence candidate intake safety tests."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime import stage7_external_evidence_intake as m


UTC = timezone.utc

POLICY_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs/clock_profiles/hfm_forward_20261027.json"
)


def now():
    return (
        datetime.now(UTC).replace(microsecond=0)
        - timedelta(seconds=30)
    )


def z(value):
    return value.isoformat().replace("+00:00", "Z")


def manifest(t):
    return {
        "schema_version": 1,
        "historical_epoch_id": m.EPOCH,
        "created_at_utc": z(t - timedelta(days=1)),
        "artifacts": [],
    }


def candidate(
    t,
    *,
    name="ecb_reference.csv",
    gate="G05",
    slot=1,
    content=b"USD,EUR\n1.0\n",
):
    return {
        "artifact_id": "ECB_EXR_SAMPLE",
        "gate_id": gate,
        "evidence_kind": m.GATES[gate][1],
        "file_name": name,
        "expected_sha256": sha256(content).hexdigest(),
        "origin_url": (
            "https://data.ecb.europa.eu/help/api/data-examples"
        ),
        "published_at_utc": z(t - timedelta(hours=4)),
        "acquired_at_utc": z(t - timedelta(hours=1)),
        "family_slot": slot,
    }


def save(root, doc):
    path = root / "candidate_manifest.json"

    path.write_text(
        json.dumps(doc, sort_keys=True),
        encoding="utf-8",
    )


def assess(
    root,
    t,
    manifest_name="candidate_manifest.json",
    policy=POLICY_PATH,
):
    return m.assess_evidence_candidates(
        epoch_id=m.EPOCH,
        as_of=z(t),
        evidence_root=str(root),
        manifest=manifest_name,
        policy_file=str(policy),
    )


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "candidates"
    root.mkdir()

    t = now()
    doc = manifest(t)

    content = b"USD,EUR\n1.0\n"

    (root / "ecb_reference.csv").write_bytes(content)

    doc["artifacts"].append(
        candidate(t, content=content)
    )

    save(root, doc)

    return root, t, doc


def test_missing_manifest_is_unknown_not_evidence(tmp_path):
    result = m.assess_evidence_candidates(
        epoch_id=m.EPOCH,
        as_of=z(now()),
        evidence_root=str(tmp_path),
        manifest=None,
        policy_file=str(POLICY_PATH),
    )

    assert result["manifest_status"] == "NOT_SUPPLIED"
    assert result["candidate_count"] == 0
    assert len(result["external_gate_board"]) == 11
    assert len(result["family_planning_board"]) == 13
    assert result["final_action"] == "HOLD"


def test_local_file_bytes_compared_without_admission(setup):
    root, t, doc = setup

    result = assess(root, t)

    assert result["candidate_count"] == 1
    assert result["locally_hash_matching_candidates"] == 1

    artifact = result["candidates"][0]

    assert artifact["status"] == "LOCAL_BYTES_MATCH_UNATTESTED"

    assert artifact["observed_sha256"] == (
        doc["artifacts"][0]["expected_sha256"]
    )

    assert artifact["claimed_origin_host_unverified"] == (
        "data.ecb.europa.eu"
    )

    assert artifact["independently_authenticated"] is False
    assert artifact["independently_admitted"] is False

    assert artifact["local_file_bytes"] == len(
        (root / "ecb_reference.csv").read_bytes()
    )

    assert result["independent_external_gates_verified"] == 0
    assert result["independent_source_families_admitted"] == 0

    assert result["family_planning_board"][0][
        "local_candidate_present"
    ] is True

    assert result["family_planning_board"][0][
        "independently_admitted"
    ] is False


def test_manifest_does_not_leak_local_path_or_url(setup):
    root, t, doc = setup

    result = json.dumps(assess(root, t))

    assert str(root) not in result
    assert "ecb_reference.csv" not in result

    assert (
        "https://data.ecb.europa.eu/help/api/data-examples"
        not in result
    )

    assert "origin_url" not in result


def test_input_files_untouched(setup):
    root, t, doc = setup

    before = {
        p.name: sha256(p.read_bytes()).hexdigest()
        for p in root.iterdir()
    }

    assess(root, t)

    after = {
        p.name: sha256(p.read_bytes()).hexdigest()
        for p in root.iterdir()
    }

    assert before == after


def test_report_deterministic_and_digest(setup):
    root, t, doc = setup

    first = assess(root, t)
    second = assess(root, t)

    assert first == second

    payload = {
        k: v
        for k, v in first.items()
        if k != "report_sha256"
    }

    assert first["report_sha256"] == m._canonical_hash(payload)


def test_tampered_local_bytes_quarantined(setup):
    root, t, doc = setup

    (root / "ecb_reference.csv").write_bytes(
        b"USD,EUR\n8.0\n"
    )

    report = assess(root, t)

    assert report["candidates"][0]["status"] == (
        "HASH_MISMATCH_QUARANTINED"
    )

    assert report["locally_hash_matching_candidates"] == 0

    assert report["external_gate_board"][4][
        "local_candidates_with_time_and_hash"
    ] == 0


def test_missing_candidate_not_gate_approval(setup):
    root, t, doc = setup

    (root / "ecb_reference.csv").unlink()

    report = assess(root, t)

    assert report["candidates"][0]["status"] == (
        "LOCAL_FILE_MISSING"
    )

    assert report["candidates"][0]["observed_sha256"] is None

    assert report["independent_external_gates_verified"] == 0


def test_publication_unknown_marks_incomplete(setup):
    root, t, doc = setup

    doc["artifacts"][0]["published_at_utc"] = None
    save(root, doc)

    report = assess(root, t)

    assert report["candidates"][0]["status"] == (
        "LOCAL_BYTES_MATCH_PUBLICATION_TIME_UNKNOWN"
    )

    assert report["locally_hash_matching_candidates"] == 1

    assert report["external_gate_board"][4][
        "local_candidates_with_time_and_hash"
    ] == 0


def test_empty_manifest_not_admission(tmp_path):
    t = now()
    save(tmp_path, manifest(t))

    report = assess(tmp_path, t)

    assert report["candidate_count"] == 0
    assert report["independent_source_families_admitted"] == 0


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(historical_epoch_id="f" * 64),
    lambda d: d.update(schema_version=True),
    lambda d: d.update(schema_version=2),
    lambda d: d.update(final_action="TRADE"),
    lambda d: d.pop("artifacts"),
    lambda d: d.update(
        created_at_utc="2020-01-01T00:00:00+03:00"
    ),
])
def test_manifest_structural_attack_denied(setup, mutate):
    root, t, doc = setup

    mutate(doc)
    save(root, doc)

    with pytest.raises(m.CandidateIntakeError):
        assess(root, t)


@pytest.mark.parametrize("key,value", [
    ("artifact_id", "../escape"),
    ("artifact_id", "<script>"),
    ("gate_id", "G99"),
    ("evidence_kind", "BROKER_IDENTITY_RECORD"),
    ("file_name", "../secret.pdf"),
    ("file_name", "file\\path.csv"),
    ("file_name", "file..csv"),
    ("file_name", "code.py"),
    ("file_name", "archive.zip"),
    ("expected_sha256", "A" * 64),
    ("expected_sha256", "x"),
    ("origin_url", "http://data.ecb.europa.eu/a"),
    (
        "origin_url",
        "https://user:pass@data.ecb.europa.eu/a",
    ),
    (
        "origin_url",
        "https://data.ecb.europa.eu/a?api_key=SECRET",
    ),
    (
        "origin_url",
        "https://data.ecb.europa.eu/a#fragment",
    ),
    ("origin_url", "https://evil.com:1234/a"),
    ("origin_url", "https://localhost/a"),
    ("family_slot", 14),
    ("family_slot", True),
    ("family_slot", 1.5),
    (
        "acquired_at_utc",
        "2025-10-01T03:00:00+03:00",
    ),
])
def test_unsafe_artifact_fields_fail_closed(
    setup,
    key,
    value,
):
    root, t, doc = setup

    doc["artifacts"][0][key] = value
    save(root, doc)

    with pytest.raises(m.CandidateIntakeError):
        assess(root, t)


def test_family_slot_for_g05_only(setup):
    root, t, doc = setup

    item = doc["artifacts"][0]

    item["gate_id"] = "G01"
    item["evidence_kind"] = m.GATES["G01"][1]

    save(root, doc)

    with pytest.raises(m.CandidateIntakeError):
        assess(root, t)


def test_prepublication_acquisition_rejected(setup):
    root, t, doc = setup

    doc["artifacts"][0]["acquired_at_utc"] = z(
        t - timedelta(days=3)
    )

    save(root, doc)

    with pytest.raises(m.CandidateIntakeError):
        assess(root, t)


def test_future_publication_rejected(setup):
    root, t, doc = setup

    doc["artifacts"][0]["published_at_utc"] = z(
        t + timedelta(days=1)
    )

    save(root, doc)

    with pytest.raises(m.CandidateIntakeError):
        assess(root, t)


def test_future_acquisition_rejected(setup):
    root, t, doc = setup

    doc["artifacts"][0]["acquired_at_utc"] = z(
        t + timedelta(days=1)
    )

    save(root, doc)

    with pytest.raises(m.CandidateIntakeError):
        assess(root, t)


def test_created_after_cutoff_rejected(setup):
    root, t, doc = setup

    doc["created_at_utc"] = z(
        t + timedelta(days=1)
    )

    save(root, doc)

    with pytest.raises(m.CandidateIntakeError):
        assess(root, t)


def test_duplicate_candidate_id_rejected(setup):
    root, t, doc = setup

    doc["artifacts"].append(
        dict(doc["artifacts"][0])
    )

    save(root, doc)

    with pytest.raises(m.CandidateIntakeError):
        assess(root, t)


def test_duplicate_file_other_id_rejected(setup):
    root, t, doc = setup

    copy = dict(doc["artifacts"][0])
    copy["artifact_id"] = "SECOND_ARTIFACT"

    doc["artifacts"].append(copy)
    save(root, doc)

    with pytest.raises(m.CandidateIntakeError):
        assess(root, t)


def test_self_declared_verification_refused(setup):
    root, t, doc = setup

    doc["artifacts"][0]["independently_verified"] = True
    save(root, doc)

    with pytest.raises(m.CandidateIntakeError):
        assess(root, t)


def test_oversized_file_refused(setup, monkeypatch):
    root, t, doc = setup

    monkeypatch.setattr(
        m,
        "MAX_FILE_BYTES",
        2,
    )

    with pytest.raises(m.CandidateIntakeError):
        assess(root, t)


def test_empty_file_refused(setup):
    root, t, doc = setup

    (root / "ecb_reference.csv").write_bytes(b"")

    with pytest.raises(m.CandidateIntakeError):
        assess(root, t)


def test_policy_tamper_refused(setup, tmp_path):
    root, t, doc = setup

    fake = tmp_path / "policy.json"

    fake.write_bytes(
        POLICY_PATH.read_bytes() + b" "
    )

    with pytest.raises(m.CandidateIntakeError):
        assess(root, t, policy=fake)


def test_wrong_epoch_refused(setup):
    root, t, doc = setup

    with pytest.raises(m.CandidateIntakeError):
        m.assess_evidence_candidates(
            epoch_id="f" * 64,
            as_of=z(t),
            evidence_root=str(root),
            manifest="candidate_manifest.json",
            policy_file=str(POLICY_PATH),
        )


def test_future_cutoff_refused(setup):
    root, t, doc = setup

    with pytest.raises(m.CandidateIntakeError):
        assess(
            root,
            datetime.now(UTC) + timedelta(days=1),
        )


def test_unbounded_manifest_refused(
    setup,
    monkeypatch,
):
    root, t, doc = setup

    monkeypatch.setattr(
        m,
        "MAX_MANIFEST_BYTES",
        10,
    )

    with pytest.raises(m.CandidateIntakeError):
        assess(root, t)


def test_duplicate_json_keys_refused(setup):
    root, t, doc = setup

    (root / "candidate_manifest.json").write_text(
        '{"schema_version":1,"schema_version":2}'
    )

    with pytest.raises(
        m.CandidateIntakeError,
        match="Duplicate",
    ):
        assess(root, t)


def test_nonfinite_json_refused(setup):
    root, t, doc = setup

    (root / "candidate_manifest.json").write_text(
        '{"schema_version":NaN}'
    )

    with pytest.raises(
        m.CandidateIntakeError,
        match="Nonfinite",
    ):
        assess(root, t)


def test_symlink_artifact_denied_if_supported(setup):
    root, t, doc = setup

    original = root / "ecb_reference.csv"

    original.rename(
        root / "source_data.csv"
    )

    try:
        original.symlink_to(
            root / "source_data.csv"
        )

    except (OSError, NotImplementedError):
        pytest.skip(
            "Windows symlink creation permission not available"
        )

    with pytest.raises(
        m.CandidateIntakeError,
        match="Symlink",
    ):
        assess(root, t)


def test_symlink_manifest_denied_if_supported(setup):
    root, t, doc = setup

    original = root / "candidate_manifest.json"

    original.rename(
        root / "real_manifest.json"
    )

    try:
        original.symlink_to(
            root / "real_manifest.json"
        )

    except (OSError, NotImplementedError):
        pytest.skip(
            "Windows symlink creation permission not available"
        )

    with pytest.raises(m.CandidateIntakeError):
        assess(root, t)


def test_template_creates_new_only(tmp_path):
    target = tmp_path / "template.json"

    digest = m.make_empty_template(
        epoch_id=m.EPOCH,
        as_of=z(now()),
        root=str(tmp_path),
        name=target.name,
    )

    assert digest == m._canonical_hash(
        json.loads(target.read_text())
    )

    assert json.loads(
        target.read_text()
    )["artifacts"] == []

    initial = sha256(
        target.read_bytes()
    ).hexdigest()

    with pytest.raises(
        m.CandidateIntakeError,
        match="never overwritten",
    ):
        m.make_empty_template(
            epoch_id=m.EPOCH,
            as_of=z(now()),
            root=str(tmp_path),
            name=target.name,
        )

    assert initial == sha256(
        target.read_bytes()
    ).hexdigest()


def test_template_requires_absolute_directory(tmp_path):
    with pytest.raises(m.CandidateIntakeError):
        m.make_empty_template(
            epoch_id=m.EPOCH,
            as_of=z(now()),
            root=".",
            name="manifest.json",
        )


def _cli_args(root, t):
    return [
        "--read-only-evidence",
        "--offline-only",
        "--epoch-id",
        m.EPOCH,
        "--as-of",
        z(t),
        "--policy-file",
        str(POLICY_PATH),
        "--evidence-root",
        str(root),
    ]


def test_cli_without_manifest_prints_hold(
    tmp_path,
    capsys,
):
    rc = m.run(_cli_args(tmp_path, now()))

    assert rc == 0

    output = capsys.readouterr().out

    assert "0/11" in output
    assert "0/13" in output
    assert "FINAL=HOLD" in output


def test_cli_json_from_actual_local_file(
    setup,
    capsys,
):
    root, t, doc = setup

    rc = m.run(
        _cli_args(root, t)
        + [
            "--manifest",
            "candidate_manifest.json",
            "--json",
        ]
    )

    assert rc == 0

    output = json.loads(
        capsys.readouterr().out
    )

    assert output["candidate_count"] == 1

    assert output[
        "independent_external_gates_verified"
    ] == 0

    assert output[
        "upstream_stage7_admission_registry_modified"
    ] is False


def test_cli_safe_template_creation(
    tmp_path,
    capsys,
):
    rc = m.run(
        _cli_args(tmp_path, now())
        + [
            "--create-template",
            "candidate_manifest.json",
        ]
    )

    assert rc == 0

    assert (
        tmp_path / "candidate_manifest.json"
    ).exists()

    assert "FINAL=HOLD" in capsys.readouterr().out


def test_cli_invalid_manifest_blocks(
    tmp_path,
    capsys,
):
    (tmp_path / "candidate_manifest.json").write_text(
        "bad json"
    )

    rc = m.run(
        _cli_args(tmp_path, now())
        + [
            "--manifest",
            "candidate_manifest.json",
        ]
    )

    assert rc == 1

    assert "BLOCKED" in capsys.readouterr().out


def test_required_two_safety_flags(tmp_path):
    with pytest.raises(SystemExit) as exc:
        m.run([
            "--epoch-id",
            m.EPOCH,
            "--as-of",
            z(now()),
            "--policy-file",
            str(POLICY_PATH),
            "--evidence-root",
            str(tmp_path),
        ])

    assert exc.value.code == 2

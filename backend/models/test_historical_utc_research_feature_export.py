
"""Milestone 08 tests using temporary research artifacts only."""

from dataclasses import replace
from pathlib import Path

import pytest

from backend.models.test_audit_historical_utc_research_export import (
    make_export,
)
from backend.models.historical_utc_research_features import (
    build_clock_aware_research_features,
)
from backend.models.historical_utc_research_feature_export import (
    ResearchFeatureExportError,
    export_research_features,
    verify_feature_export,
    main,
)


def sample(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()

    source, _, _ = make_export(
        inputs,
        minutes=tuple(range(0, 120, 5)),
    )

    project = tmp_path / "EURUSD_ORACLE"
    (project / "backend" / "models").mkdir(
        parents=True
    )

    v1 = project / "backend" / "models" / "frozen_v1.json"
    v1.write_text('{"frozen":true}\n')

    features = build_clock_aware_research_features(
        source
    )

    return project, source, features, v1


def test_export_and_source_backed_verify(tmp_path):
    project, source, features, legacy = sample(tmp_path)
    before = legacy.read_bytes()

    target = export_research_features(
        features, source, project
    )

    meta = verify_feature_export(target, source)

    assert meta["feature_count"] == 24
    assert (
        meta["feature_rows_sha256"]
        == features.feature_rows_sha256
    )
    assert (
        meta["source_lineage_sha256"]
        == features.source_lineage_sha256
    )

    assert meta["training_authorized"] is False
    assert meta["historical_utc_certified"] is False
    assert legacy.read_bytes() == before

    assert set(p.name for p in target.iterdir()) == {
        "features.jsonl",
        "manifest.json",
        "checksums.sha256",
    }

    assert target.parent == (
        project
        / "data"
        / "research"
        / "historical_utc_features_v1"
    )


def test_export_never_overwrites(tmp_path):
    project, source, features, _ = sample(tmp_path)

    target = export_research_features(
        features, source, project
    )

    old = (target / "features.jsonl").read_bytes()

    with pytest.raises(
        ResearchFeatureExportError,
        match="already exists",
    ):
        export_research_features(
            features, source, project
        )

    assert (
        target / "features.jsonl"
    ).read_bytes() == old


def test_modified_feature_detected_before_publish(tmp_path):
    project, source, features, _ = sample(tmp_path)

    fake = replace(
        features,
        rows=(
            replace(
                features.rows[0],
                close=1.2312,
            ),
        ) + features.rows[1:],
    )

    with pytest.raises(
        ResearchFeatureExportError,
        match="changed before publication",
    ):
        export_research_features(
            fake, source, project
        )

    folder = (
        project
        / "data"
        / "research"
        / "historical_utc_features_v1"
    )

    assert not list(folder.glob("features_*"))
    assert not list(folder.glob(".stage_*"))
    assert not (folder / ".feature_export.lock").exists()


def test_forged_digest_rejected(tmp_path):
    project, source, features, _ = sample(tmp_path)

    fake = replace(
        features,
        feature_rows_sha256="a" * 64,
    )

    with pytest.raises(
        ResearchFeatureExportError,
        match="audited source",
    ):
        export_research_features(
            fake, source, project
        )


def test_input_tamper_detected(tmp_path):
    project, source, features, _ = sample(tmp_path)

    with (source / "candles.jsonl").open("ab") as out:
        out.write(b"modified\n")

    with pytest.raises(Exception):
        export_research_features(
            features, source, project
        )

    assert not (project / "data").exists()


def test_saved_feature_tamper_detected(tmp_path):
    project, source, features, _ = sample(tmp_path)

    target = export_research_features(
        features, source, project
    )

    with (target / "features.jsonl").open("ab") as out:
        out.write(b"tampered\n")

    with pytest.raises(ResearchFeatureExportError):
        verify_feature_export(target)


def test_saved_manifest_tamper_detected(tmp_path):
    project, source, features, _ = sample(tmp_path)

    target = export_research_features(
        features, source, project
    )

    with (target / "manifest.json").open("ab") as out:
        out.write(b" ")

    with pytest.raises(ResearchFeatureExportError):
        verify_feature_export(target)


def test_lock_blocks_parallel_export(tmp_path):
    project, source, features, _ = sample(tmp_path)

    directory = (
        project
        / "data"
        / "research"
        / "historical_utc_features_v1"
    )

    directory.mkdir(parents=True)

    lock = directory / ".feature_export.lock"
    lock.write_text("busy")

    with pytest.raises(
        ResearchFeatureExportError,
        match="locked",
    ):
        export_research_features(
            features, source, project
        )

    assert lock.read_text() == "busy"


def test_wrong_project_root(tmp_path):
    _, source, features, _ = sample(tmp_path)

    with pytest.raises(
        ResearchFeatureExportError,
        match="root",
    ):
        export_research_features(
            features,
            source,
            tmp_path / "missing",
        )


def test_cli_exports_and_prevents_repeat(
    tmp_path, capsys
):
    project, source, _, _ = sample(tmp_path)

    args = [
        "--source-directory", str(source),
        "--project-root", str(project),
        "--research-only",
        "--export",
    ]

    assert main(args) == 0

    assert "FEATURE EXPORT VERIFIED" in (
        capsys.readouterr().out
    )

    assert main(args) == 1

    assert "FEATURE EXPORT BLOCKED" in (
        capsys.readouterr().out
    )


def test_cli_requires_explicit_flags(tmp_path):
    _, source, _, _ = sample(tmp_path)

    with pytest.raises(SystemExit) as info:
        main([
            "--source-directory",
            str(source),
        ])

    assert info.value.code == 2


def test_post_publication_verification_failure_reports_path(
    tmp_path,
    monkeypatch,
    capsys,
):
    project, source, _, _ = sample(tmp_path)

    from backend.models import (
        historical_utc_research_feature_export as module,
    )

    real_verify = module.verify_feature_export

    def fail_last(directory, source_directory=None):
        if Path(directory).name.startswith("features_"):
            raise ResearchFeatureExportError(
                "Simulated post-publish failure"
            )

        return real_verify(
            directory, source_directory
        )

    monkeypatch.setattr(
        module,
        "verify_feature_export",
        fail_last,
    )

    args = [
        "--source-directory", str(source),
        "--project-root", str(project),
        "--research-only",
        "--export",
    ]

    assert module.main(args) == 1

    assert "EXPORT NEEDS REVIEW" in (
        capsys.readouterr().out
    )

    assert len(
        list(
            (
                project
                / "data"
                / "research"
                / "historical_utc_features_v1"
            ).glob("features_*")
        )
    ) == 1

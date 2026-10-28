
"""Milestone 14 offline tests using genuine Milestone 12 synthetic indexes."""

from datetime import date, datetime, timezone
import json
from hashlib import sha256

import pytest

from backend.models.consolidate_historical_utc_research import (
    _canonical, inspect_windows, publish_index, ResearchWindow,
)
from backend.models.test_consolidate_historical_utc_research import (
    one_export, sample, project,
)
from backend.models.register_historical_utc_seasons import (
    SeasonRegistryError, read_index, build_registry, publish_registry, run,
)


def fixture_indexes(tmp_path):
    root = project(tmp_path)
    winter = publish_index(inspect_windows(sample(tmp_path)), root)
    summer_source = one_export(
        tmp_path, "summer",
        datetime(2026, 6, 5, tzinfo=timezone.utc), (10, 11, 12),
    )
    summer = publish_index(inspect_windows((ResearchWindow(
        date(2026, 6, 1), date(2026, 6, 8), summer_source,
    ),)), root)
    return root, winter, summer


def rewrite_index(path, change):
    """Re-sign synthetic data after deliberate mutation to test semantics."""
    old = json.loads((path / "manifest.json").read_bytes())
    change(old)
    old.pop("index_sha256")
    old["index_sha256"] = sha256(_canonical(old)).hexdigest()
    payload = _canonical(old)
    (path / "manifest.json").write_bytes(payload)
    (path / "checksums.sha256").write_text(
        f"{sha256(payload).hexdigest()}  manifest.json\n", encoding="ascii",
    )
    return path


def test_registry_totals_and_excluded_dates(tmp_path):
    _, winter, summer = fixture_indexes(tmp_path)
    manifest = build_registry((winter, summer))
    assert manifest["row_count"] == 9
    assert manifest["winter_count"] == 6
    assert manifest["summer_count"] == 3
    assert manifest["index_count"] == 2
    assert manifest["inter_index_calendar_days_excluded"] == (
        date(2026, 6, 1) - date(2026, 1, 26)
    ).days
    assert manifest["cross_index_candles_joined"] is False
    assert manifest["training_authorized"] is False
    assert manifest["execution_authorized"] is False
    assert manifest["historical_utc_certified"] is False
    assert manifest == build_registry((winter, summer))


def test_checksums_and_index_identity(tmp_path):
    _, winter, summer = fixture_indexes(tmp_path)
    assert read_index(winter)["row_count"] == 6
    assert read_index(summer)["row_count"] == 3


def test_original_data_unchanged_by_preview(tmp_path):
    _, winter, summer = fixture_indexes(tmp_path)
    watched = [p for root in (winter, summer) for p in root.iterdir()]
    before = {p: p.read_bytes() for p in watched}
    build_registry((winter, summer))
    assert {p: p.read_bytes() for p in watched} == before


def test_missing_checksum_fails_closed(tmp_path):
    _, winter, summer = fixture_indexes(tmp_path)
    (winter / "checksums.sha256").write_text("wrong\n")
    with pytest.raises(SeasonRegistryError, match="checksum"):
        build_registry((winter, summer))


def test_manifest_tamper_fails_closed(tmp_path):
    _, winter, summer = fixture_indexes(tmp_path)
    with (winter / "manifest.json").open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(SeasonRegistryError):
        build_registry((winter, summer))


def test_repeated_index_fails_closed(tmp_path):
    _, winter, _ = fixture_indexes(tmp_path)
    with pytest.raises(SeasonRegistryError, match="Duplicate index"):
        build_registry((winter, winter))


def test_wrong_order_fails_closed(tmp_path):
    _, winter, summer = fixture_indexes(tmp_path)
    with pytest.raises(SeasonRegistryError, match="overlap or are out of order"):
        build_registry((summer, winter))


def test_single_index_rejected(tmp_path):
    _, winter, _ = fixture_indexes(tmp_path)
    with pytest.raises(SeasonRegistryError, match="Requires 2"):
        build_registry((winter,))


def test_missing_season_rejected(tmp_path):
    _, winter, summer = fixture_indexes(tmp_path)
    # Represent a separate winter-only index by re-signing synthetic data.
    second = json.loads((summer / "manifest.json").read_bytes())
    second["winter_count"] = second["row_count"]
    second["summer_count"] = 0
    second.pop("index_sha256")
    second["index_sha256"] = sha256(_canonical(second)).hexdigest()
    new_path = summer.parent / ("index_" + second["index_sha256"])
    new_path.mkdir()
    payload = _canonical(second)
    (new_path / "manifest.json").write_bytes(payload)
    (new_path / "checksums.sha256").write_text(
        f"{sha256(payload).hexdigest()}  manifest.json\n", encoding="ascii",
    )
    with pytest.raises(SeasonRegistryError, match="Both winter and summer"):
        build_registry((winter, new_path))


def test_wrong_safety_flags_even_when_resigned(tmp_path):
    _, winter, _ = fixture_indexes(tmp_path)
    changed = json.loads((winter / "manifest.json").read_bytes())
    changed["training_authorized"] = True
    changed.pop("index_sha256")
    changed["index_sha256"] = sha256(_canonical(changed)).hexdigest()
    changed_path = winter.parent / ("index_" + changed["index_sha256"])
    changed_path.mkdir()
    payload = _canonical(changed)
    (changed_path / "manifest.json").write_bytes(payload)
    (changed_path / "checksums.sha256").write_text(
        f"{sha256(payload).hexdigest()}  manifest.json\n", encoding="ascii",
    )
    with pytest.raises(SeasonRegistryError, match="safety"):
        read_index(changed_path)


def test_parent_row_mismatch_even_when_resigned(tmp_path):
    _, winter, _ = fixture_indexes(tmp_path)
    changed = json.loads((winter / "manifest.json").read_bytes())
    changed["source_windows"][0]["source_row_count"] += 1
    changed.pop("index_sha256")
    changed["index_sha256"] = sha256(_canonical(changed)).hexdigest()
    changed_path = winter.parent / ("index_" + changed["index_sha256"])
    changed_path.mkdir()
    payload = _canonical(changed)
    (changed_path / "manifest.json").write_bytes(payload)
    (changed_path / "checksums.sha256").write_text(
        f"{sha256(payload).hexdigest()}  manifest.json\n", encoding="ascii",
    )
    with pytest.raises(SeasonRegistryError, match="mismatched parent rows"):
        read_index(changed_path)


def test_immutable_publication_and_cli(tmp_path, capsys):
    root, winter, summer = fixture_indexes(tmp_path)
    cli = ["--index", str(winter), "--index", str(summer), "--research-only"]
    assert run(cli) == 0
    assert "PREVIEW ONLY" in capsys.readouterr().out
    assert run(cli + ["--publish-registry"], project_root=root) == 0
    assert "REGISTRY PUBLISHED" in capsys.readouterr().out
    registry = build_registry((winter, summer))
    directory = root / "data" / "research" / "historical_utc_registries_v1" / (
        "registry_" + registry["registry_sha256"]
    )
    assert {p.name for p in directory.iterdir()} == {"manifest.json", "checksums.sha256"}
    assert json.loads((directory / "manifest.json").read_text()) == registry
    assert run(cli + ["--publish-registry"], project_root=root) == 1
    assert "already exists" in capsys.readouterr().out


def test_publish_refuses_modified_flags(tmp_path):
    root, winter, summer = fixture_indexes(tmp_path)
    registry = build_registry((winter, summer))
    registry["execution_authorized"] = True
    with pytest.raises(SeasonRegistryError, match="proof or safety"):
        publish_registry(registry, root)


def test_missing_research_acknowledgement(tmp_path):
    _, winter, summer = fixture_indexes(tmp_path)
    with pytest.raises(SystemExit) as err:
        run(["--index", str(winter), "--index", str(summer)])
    assert err.value.code == 2


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_accepts_both_published_checksum_newlines(tmp_path, newline):
    _, winter, summer = fixture_indexes(tmp_path)
    checksum_path = winter / "checksums.sha256"
    original = (checksum_path.read_bytes()
                .replace(b"\r\n", b"\n"))
    assert original.endswith(b"\n")
    checksum_path.write_bytes(original[:-1] + newline)
    assert build_registry((winter, summer))["row_count"] == 9


@pytest.mark.parametrize("suffix", [b"", b"\r", b"\n\n", b"\r\n\r\n"])
def test_rejects_invalid_checksum_newlines(tmp_path, suffix):
    _, winter, summer = fixture_indexes(tmp_path)
    checksum_path = winter / "checksums.sha256"
    original = checksum_path.read_bytes().replace(b"\r\n", b"\n")
    checksum_path.write_bytes(original[:-1] + suffix)
    with pytest.raises(SeasonRegistryError, match="checksum"):
        build_registry((winter, summer))


def test_registry_publication_uses_lf_on_all_platforms(tmp_path):
    root, winter, summer = fixture_indexes(tmp_path)
    manifest = build_registry((winter, summer))
    registry_dir = publish_registry(manifest, root)
    content = (registry_dir / "manifest.json").read_bytes()
    assert (registry_dir / "checksums.sha256").read_bytes() == (
        f"{sha256(content).hexdigest()}  manifest.json\n".encode("ascii")
    )

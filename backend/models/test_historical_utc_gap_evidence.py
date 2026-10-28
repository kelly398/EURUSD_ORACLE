
"""Milestone 15 offline tests, using real export/index/registry formats."""

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

from backend.models.audit_historical_utc_research_export import audit_research_export
from backend.models.consolidate_historical_utc_research import (
    ResearchWindow, inspect_windows, publish_index,
)
from backend.models.historical_utc_reconstruction import (
    LegacyM5Bar, reconstruct_historical_m5_bars,
)
from backend.models.register_historical_utc_seasons import (
    build_registry, publish_registry,
)
from backend.models.test_audit_historical_utc_research_export import write
from backend.models.test_consolidate_historical_utc_research import (
    one_export, project, sample,
)
from backend.models.historical_utc_gap_evidence import (
    GapEvidenceError, _read_published_registry, inspect_gap_evidence, run,
)

UTC = timezone.utc


def summer_export_with_internal_gap(tmp_path):
    folder = tmp_path / 'summer_source'
    folder.mkdir()
    labels = (
        datetime(2026, 6, 10, 0, 0, tzinfo=UTC),
        datetime(2026, 6, 10, 0, 5, tzinfo=UTC),
        datetime(2026, 6, 10, 0, 15, tzinfo=UTC),
    )
    batch = reconstruct_historical_m5_bars([
        LegacyM5Bar(
            row_id=i + 10, broker_label_time=label,
            open=1.10, high=1.12, low=1.09, close=1.11,
            tick_volume=100, spread_points=8, real_volume=0,
        )
        for i, label in enumerate(labels)
    ])
    write(folder, [r.record() for r in batch.records],
          batch.evidence_sha256, batch.mapping_implementation_sha256)
    return folder


def setup(tmp_path, *, internal_gap=True):
    root = project(tmp_path)
    win = sample(tmp_path)
    winter_index = publish_index(inspect_windows(win), root)
    if internal_gap:
        summer_source = summer_export_with_internal_gap(tmp_path)
        summer_start = date(2026, 6, 8)
        summer_end = date(2026, 6, 15)
    else:
        summer_source = one_export(
            tmp_path, 'summer_source',
            datetime(2026, 6, 5, tzinfo=UTC), (10, 11, 12),
        )
        summer_start = date(2026, 6, 1)
        summer_end = date(2026, 6, 8)
    summer_index = publish_index(inspect_windows((ResearchWindow(
        summer_start, summer_end, summer_source,
    ),)), root)
    registry_dir = publish_registry(
        build_registry((winter_index, summer_index)), root,
    )
    dataset_root = root / 'data/research/historical_utc_v2'
    dataset_root.mkdir()
    for source in [w.directory for w in win] + [summer_source]:
        lineage = audit_research_export(source).lineage_sha256
        shutil.copytree(source, dataset_root / ('dataset_' + lineage))
    return registry_dir, (winter_index, summer_index), dataset_root


def test_classifications_and_exact_missing_slot(tmp_path):
    registry, indexes, data = setup(tmp_path)
    result = inspect_gap_evidence(registry, indexes, data)
    assert result['row_count'] == 9
    assert result['observed_gap_count'] == 2
    assert result['unclassified_missing_m5_slots'] == 577
    assert result['classification_counts'] == {
        'SAME_BROKER_DAY_UNVERIFIED': 1,
        'WEEKEND_SHAPED_UNVERIFIED': 1,
        'CROSS_BROKER_DAY_UNVERIFIED': 0,
    }
    weekend, intraday = result['gaps']
    assert weekend['unobserved_m5_slots'] == 576
    assert weekend['market_closure_certified'] is False
    assert intraday['unobserved_m5_slots'] == 1
    assert intraday['first_unobserved_candidate_utc'] == (
        '2026-06-09T21:10:00+00:00'
    )
    assert intraday['previous_broker_label'] == '2026-06-10T00:05:00+00:00'
    assert intraday['next_broker_label'] == '2026-06-10T00:15:00+00:00'
    assert result['excluded_periods_are_observed_gaps'] is False
    assert result['excluded_calendar_days_between_indexes'] > 100
    assert result['training_authorized'] is False
    assert result['execution_authorized'] is False


def test_no_internal_gap_and_no_crossseason_join(tmp_path):
    registry, indexes, data = setup(tmp_path, internal_gap=False)
    result = inspect_gap_evidence(registry, indexes, data)
    assert result['observed_gap_count'] == 1
    assert result['unclassified_missing_m5_slots'] == 576
    assert result['classification_counts']['WEEKEND_SHAPED_UNVERIFIED'] == 1


def test_cli_no_writes(tmp_path, capsys):
    registry, indexes, data = setup(tmp_path)
    observed = {
        str(p): p.read_bytes()
        for folder in (registry, *indexes)
        for p in folder.iterdir()
    }
    args = ['--registry', str(registry), '--research-only',
            '--dataset-root', str(data)]
    for index in indexes:
        args += ['--index', str(index)]
    assert run(args) == 0
    output = capsys.readouterr().out
    assert 'OBSERVED GAPS: 2' in output
    assert 'NO FILES WRITTEN' in output
    assert {
        str(p): p.read_bytes()
        for folder in (registry, *indexes)
        for p in folder.iterdir()
    } == observed


def test_missing_dataset_refused(tmp_path):
    registry, indexes, data = setup(tmp_path)
    one = next(data.iterdir())
    shutil.rmtree(one)
    with pytest.raises(GapEvidenceError, match='unavailable'):
        inspect_gap_evidence(registry, indexes, data)


def test_modified_candle_refused(tmp_path):
    registry, indexes, data = setup(tmp_path)
    one = next(data.iterdir()) / 'candles.jsonl'
    one.write_bytes(one.read_bytes() + b'broken\n')
    with pytest.raises(GapEvidenceError, match='evidence|export'):
        inspect_gap_evidence(registry, indexes, data)


def test_modified_registry_checksum_refused(tmp_path):
    registry, indexes, data = setup(tmp_path)
    (registry / 'checksums.sha256').write_bytes(b'forged\n')
    with pytest.raises(GapEvidenceError, match='checksum'):
        inspect_gap_evidence(registry, indexes, data)


def test_modified_registry_manifest_refused(tmp_path):
    registry, indexes, data = setup(tmp_path)
    (registry / 'manifest.json').write_bytes(
        (registry / 'manifest.json').read_bytes() + b'\n'
    )
    with pytest.raises(GapEvidenceError):
        inspect_gap_evidence(registry, indexes, data)


def test_wrong_index_order_refused(tmp_path):
    registry, indexes, data = setup(tmp_path)
    with pytest.raises(GapEvidenceError, match='Referenced index'):
        inspect_gap_evidence(registry, indexes[::-1], data)


def test_wrong_index_reuse_refused(tmp_path):
    registry, indexes, data = setup(tmp_path)
    with pytest.raises(GapEvidenceError, match='Referenced index'):
        inspect_gap_evidence(registry, (indexes[0], indexes[0]), data)


def test_checksum_crlf_accepted_without_modifying_registry(tmp_path):
    registry, indexes, data = setup(tmp_path)
    path = registry / 'checksums.sha256'
    path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
    assert inspect_gap_evidence(registry, indexes, data)['row_count'] == 9


@pytest.mark.parametrize('bad', [b'', b'\r', b'\n\n', b'\r\n\r\n'])
def test_invalid_registry_checksum_terminators(tmp_path, bad):
    registry, indexes, data = setup(tmp_path)
    path = registry / 'checksums.sha256'
    path.write_bytes(path.read_bytes()[:-1] + bad)
    with pytest.raises(GapEvidenceError, match='checksum'):
        inspect_gap_evidence(registry, indexes, data)


def test_invalid_dataset_root_refused(tmp_path):
    registry, indexes, _ = setup(tmp_path)
    with pytest.raises(GapEvidenceError, match='Dataset root'):
        inspect_gap_evidence(registry, indexes, tmp_path / 'unknown')


def test_cli_requires_research_only(tmp_path):
    registry, indexes, data = setup(tmp_path)
    args = ['--registry', str(registry), '--dataset-root', str(data)]
    for index in indexes:
        args += ['--index', str(index)]
    with pytest.raises(SystemExit) as exc:
        run(args)
    assert exc.value.code == 2

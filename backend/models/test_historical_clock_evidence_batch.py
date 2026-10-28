
"""Milestones 21-24: source-backed and fake-PostgreSQL read-only contracts."""

from datetime import datetime, timedelta, timezone
import json
import shutil
from types import SimpleNamespace

import pytest

from backend.models.historical_clock_evidence_batch import (
    ClockEvidenceError, assess_historical_clock_evidence, run,
)
from backend.models.test_historical_utc_gap_evidence import setup

UTC = timezone.utc


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def one(self):
        assert len(self.rows) == 1
        return self.rows[0]

    def first(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(self, *, unexpected_label=False, missing_candle=False):
        self.sql = []
        self.unexpected_label = unexpected_label
        self.missing_candle = missing_candle

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def begin(self):
        return self

    def execute(self, clause, values=None):
        statement = str(clause)
        self.sql.append((statement, values))
        if statement.startswith('SET '):
            return FakeResult([])
        if 'FROM ticks' in statement:
            assert set(values) == {'start_ts', 'end_ts'}
            assert values['end_ts'] - values['start_ts'] == timedelta(minutes=5)
            assert values['start_ts'].tzinfo is not None
            return FakeResult([{'n': 463 if values['start_ts'].hour == 21 else 0,
                                'raw_timestamp_mismatch': 0}])
        if 'FROM broker_candles' in statement:
            assert set(values) == {'row_id'}
            if self.missing_candle:
                return FakeResult([])
            row_id = values['row_id']
            labels = {
                10: datetime(2026, 6, 10, 0, 0, tzinfo=UTC),
                11: datetime(2026, 6, 10, 0, 5, tzinfo=UTC),
                12: datetime(2026, 6, 10, 0, 15, tzinfo=UTC),
            }
            label = labels[row_id] + (
                timedelta(hours=1) if self.unexpected_label else timedelta(0)
            )
            return FakeResult([{
                'id': row_id, 'timestamp': label,
                'ingested_at': datetime(2026, 6, 12, tzinfo=UTC),
            }])
        raise AssertionError('Unexpected SQL: ' + statement)


class FakePG:
    dialect = SimpleNamespace(name='postgresql')

    def __init__(self, **kwargs):
        self.connection = FakeConnection(**kwargs)

    def connect(self):
        return self.connection


def args_for(tmp_path):
    registry, indexes, data = setup(tmp_path)
    args = ['--registry', str(registry), '--dataset-root', str(data),
            '--research-only']
    for index in indexes:
        args.extend(['--index', str(index)])
    return args, (registry, indexes, data)


def test_m21_m23_m24_offline_integrated(tmp_path):
    _, (registry, indexes, data) = args_for(tmp_path)
    result = assess_historical_clock_evidence(registry, indexes, data)
    m21 = result['milestone_21_clock_inventory']
    m22 = result['milestone_22_tick_candle_domain_review']
    m23 = result['milestone_23_causal_availability']
    m24 = result['milestone_24_integrated_readiness']
    assert m21['verified_candle_rows'] == 9
    assert m21['source_export_count'] == 3
    assert m21['winter_candidate_rows'] == 6
    assert m21['summer_candidate_rows'] == 3
    assert m21['export_contains_original_receipt_timestamp'] is False
    assert m22['archive_mode'] == 'NOT_QUERIED'
    assert m22['eligible_single_slot_same_day_gaps'] == 1
    assert m22['archive_samples'] == []
    assert m23['export_rows_without_first_available_at'] == 9
    assert m23['first_available_at_verified_rows'] == 0
    assert m24['observed_gap_count'] == 2
    assert m24['unclassified_missing_m5_slots'] == 577
    assert m24['excluded_calendar_days'] > 100
    assert m24['status'] == 'HISTORICAL_CLOCK_UNCERTIFIED_RESEARCH_ONLY'
    assert result['historical_admission'] is False
    assert result['training_authorized'] is False
    assert result['execution_authorized'] is False


def test_m22_archive_distinguishes_unaligned_domains(tmp_path):
    _, (registry, indexes, data) = args_for(tmp_path)
    fake = FakePG()
    report = assess_historical_clock_evidence(registry, indexes, data,
                                               archive_engine=fake)
    m22 = report['milestone_22_tick_candle_domain_review']
    assert m22['archive_mode'] == 'BOUNDED_ARCHIVE_SAMPLES_UNCERTIFIED'
    item, = m22['archive_samples']
    assert item['gap_number'] == 2
    assert item['broker_missing_label'] == '2026-06-10T00:10:00+00:00'
    assert item['missing_utc_candidate'] == '2026-06-09T21:10:00+00:00'
    assert item['tick_counts_by_unaligned_domain'][
        'broker_wall_label_domain']['archived_tick_rows'] == 0
    assert item['tick_counts_by_unaligned_domain'][
        'utc_candidate_domain']['archived_tick_rows'] == 463
    assert all(x['source_record_found'] for x in item['source_candle_anchors'])
    assert item['clock_domains_proven_equivalent'] is False
    assert item['first_available_at_proven'] is False
    sql = [text for text, _ in fake.connection.sql]
    assert sql[0] == 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'
    assert sql[1].startswith('SET LOCAL statement_timeout')
    assert len([x for x in sql if 'FROM ticks' in x]) == 2
    assert len([x for x in sql if 'FROM broker_candles' in x]) == 2
    assert not any('INSERT ' in x or 'UPDATE ' in x or 'DELETE ' in x
                   for x in sql)


def test_absent_candle_is_reported_not_imputed(tmp_path):
    _, (registry, indexes, data) = args_for(tmp_path)
    report = assess_historical_clock_evidence(
        registry, indexes, data, archive_engine=FakePG(missing_candle=True))
    anchors = report['milestone_22_tick_candle_domain_review'][
        'archive_samples'][0]['source_candle_anchors']
    assert len(anchors) == 2
    assert not any(x['source_record_found'] for x in anchors)
    assert report['historical_utc_certified'] is False


def test_inconsistent_archive_identity_fails_closed(tmp_path):
    _, (registry, indexes, data) = args_for(tmp_path)
    with pytest.raises(ClockEvidenceError, match='identity mismatch'):
        assess_historical_clock_evidence(
            registry, indexes, data,
            archive_engine=FakePG(unexpected_label=True),
        )


def test_non_postgresql_archive_fails_closed(tmp_path):
    _, (registry, indexes, data) = args_for(tmp_path)
    fake = FakePG()
    fake.dialect = SimpleNamespace(name='sqlite')
    with pytest.raises(ClockEvidenceError, match='PostgreSQL'):
        assess_historical_clock_evidence(registry, indexes, data,
                                         archive_engine=fake)


def test_registry_tampering_fails_closed(tmp_path):
    _, (registry, indexes, data) = args_for(tmp_path)
    (registry / 'manifest.json').write_bytes(b'forged')
    with pytest.raises(ClockEvidenceError, match='verification failed'):
        assess_historical_clock_evidence(registry, indexes, data)


def test_export_tampering_fails_closed(tmp_path):
    _, (registry, indexes, data) = args_for(tmp_path)
    source = next(data.iterdir()) / 'candles.jsonl'
    source.write_bytes(source.read_bytes() + b'corruption\n')
    with pytest.raises(ClockEvidenceError, match='verification failed'):
        assess_historical_clock_evidence(registry, indexes, data)


def test_wrong_index_order_fails_closed(tmp_path):
    _, (registry, indexes, data) = args_for(tmp_path)
    with pytest.raises(ClockEvidenceError, match='verification failed'):
        assess_historical_clock_evidence(registry, indexes[::-1], data)


def test_cli_preview_and_json(tmp_path, capsys):
    args, _ = args_for(tmp_path)
    assert run(args) == 0
    output = capsys.readouterr().out
    assert 'M21 ROWS: 9 | EXPORTS: 3' in output
    assert 'M22 ARCHIVE: NOT_QUERIED' in output
    assert 'M23 UNKNOWN FIRST AVAILABILITY: 9' in output
    assert 'M24 STATUS: HISTORICAL_CLOCK_UNCERTIFIED_RESEARCH_ONLY' in output
    assert 'NO FILES WRITTEN' in output
    assert run(args + ['--json']) == 0
    data = json.loads(capsys.readouterr().out)
    assert data['research_only'] is True
    assert data['training_authorized'] is False


def test_cli_archive_opt_in_and_no_writes(tmp_path, capsys):
    args, (registry, indexes, data) = args_for(tmp_path)
    paths = [p for folder in (registry, *indexes) for p in folder.iterdir()]
    paths += [p for folder in data.iterdir() for p in folder.iterdir()]
    saved = {p: p.read_bytes() for p in paths}
    fake = FakePG()
    assert run(args + ['--inspect-archive'], engine=fake) == 0
    assert 'candidate-UTC-slot ticks=463' in capsys.readouterr().out
    assert {p: p.read_bytes() for p in paths} == saved


def test_archive_engine_cannot_be_used_without_opt_in(tmp_path, capsys):
    args, _ = args_for(tmp_path)
    assert run(args, engine=FakePG()) == 1
    assert 'BLOCKED' in capsys.readouterr().out


def test_research_flag_is_mandatory(tmp_path):
    args, _ = args_for(tmp_path)
    args.remove('--research-only')
    with pytest.raises(SystemExit) as exc:
        run(args)
    assert exc.value.code == 2
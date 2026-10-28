
"""Milestone 06 offline dataset audit tests."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.models.historical_utc_reconstruction import (
    LegacyM5Bar,
    MAPPING_ID,
    reconstruct_historical_m5_bars,
)
from backend.models.audit_historical_utc_research_export import (
    ResearchAuditError,
    audit_research_export,
    main,
)


def compact(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(obj):
    return sha256(compact(obj).encode()).hexdigest()


def mkbar(row_id, mins):
    return LegacyM5Bar(
        row_id=row_id,
        broker_label_time=(
            datetime(2026, 1, 15, 14, tzinfo=timezone.utc)
            + timedelta(minutes=mins)
        ),
        open=1.1,
        high=1.12,
        low=1.09,
        close=1.11,
        tick_volume=100,
        spread_points=8,
        real_volume=0.0,
    )


def make_export(tmp_path: Path, minutes=(0, 5, 10)):
    batch = reconstruct_historical_m5_bars(
        mkbar(i + 1, minute)
        for i, minute in enumerate(minutes)
    )

    records = [bar.record() for bar in batch.records]

    folder = tmp_path / "dataset_sample"
    folder.mkdir()

    data, meta = write(
        folder,
        records,
        batch.evidence_sha256,
        batch.mapping_implementation_sha256,
    )

    return folder, data, meta


def write(
    folder, records, proof, impl_sha, *,
    modifications=None,
):
    content = b"".join(
        (compact(row) + "\n").encode()
        for row in records
    )

    rows_digest = sha256(content).hexdigest()

    offsets = [
        row["candidate_offset_hours"]
        for row in records
    ]

    utc_times = [
        datetime.fromisoformat(row["candidate_utc_open"])
        for row in records
    ]

    missing = sum(
        int((b - a) // timedelta(minutes=5)) - 1
        for a, b in zip(utc_times, utc_times[1:])
        if b - a > timedelta(minutes=5)
    )

    gaps = sum(
        b - a > timedelta(minutes=5)
        for a, b in zip(utc_times, utc_times[1:])
    )

    base = {
        "schema_version": 2,
        "dataset_version":
            "EURUSD_M5_HISTORICAL_UTC_RESEARCH_V2",
        "symbol": "EURUSD",
        "timeframe": "M5",
        "source": "MT5_legacy_broker_labels",
        "clock_mapping_id": MAPPING_ID,
        "clock_mapping_implementation_sha256": impl_sha,
        "record_count": len(records),
        "batch_count": 1,
        "batch_evidence_sha256": [proof],
        "rows_jsonl_sha256": rows_digest,
        "first_source_row_id":
            records[0]["source_row"]["row_id"],
        "last_source_row_id":
            records[-1]["source_row"]["row_id"],
        "first_broker_label":
            records[0]["source_row"]["broker_label_time"],
        "last_broker_label":
            records[-1]["source_row"]["broker_label_time"],
        "first_candidate_utc":
            records[0]["candidate_utc_open"],
        "last_candidate_utc":
            records[-1]["candidate_utc_open"],
        "winter_count": offsets.count(2),
        "summer_count": offsets.count(3),
        "gap_count": gaps,
        "unobserved_intervals": missing,
        "unobserved_intervals_include_market_closures": True,
        "first_available_at": None,
        "source_receipt_certified": False,
        "research_only": True,
        "historical_admission": False,
        "execution_authorized": False,
        "training_authorized": False,
        "export_format": "jsonl_manifest_v1",
        "data_file": "candles.jsonl",
    }

    base.update(modifications or {})

    base["lineage_sha256"] = digest({
        "schema_version": 2,
        "dataset_version": base["dataset_version"],
        "mapping_id": MAPPING_ID,
        "mapping_implementation_sha256": impl_sha,
        "batch_evidence_sha256": [proof],
        "record_count": len(records),
        "rows_jsonl_sha256": rows_digest,
    })

    manifest_bytes = (compact(base) + "\n").encode()

    (folder / "candles.jsonl").write_bytes(content)
    (folder / "manifest.json").write_bytes(manifest_bytes)

    (folder / "checksums.sha256").write_text(
        rows_digest + "  candles.jsonl\n"
        + sha256(manifest_bytes).hexdigest()
        + "  manifest.json\n",
        encoding="ascii",
    )

    return records, base


def test_audit_passes_and_is_read_only(tmp_path):
    folder, _, meta = make_export(tmp_path)

    before = {
        p.name: p.read_bytes()
        for p in folder.iterdir()
    }

    result = audit_research_export(folder)

    assert result.row_count == 3
    assert result.batch_count == 1
    assert result.gap_count == 0
    assert result.unobserved_intervals == 0
    assert result.winter_count == 3
    assert result.research_only
    assert not result.execution_authorized
    assert result.rows_sha256 == meta["rows_jsonl_sha256"]

    assert before == {
        p.name: p.read_bytes()
        for p in folder.iterdir()
    }


def test_cli(tmp_path, capsys):
    folder, _, _ = make_export(tmp_path)

    assert main([
        "--directory", str(folder)
    ]) == 0

    output = capsys.readouterr().out

    assert "AUDIT PASSED" in output
    assert "TRAINING: BLOCKED" in output


def test_gap_is_counted(tmp_path):
    folder, _, _ = make_export(
        tmp_path,
        minutes=(0, 5, 20),
    )

    result = audit_research_export(folder)

    assert (
        result.gap_count,
        result.unobserved_intervals,
    ) == (1, 2)


def test_modified_candidate_rejected_even_if_checksums_updated(
    tmp_path,
):
    folder, rows, meta = make_export(tmp_path)

    rows[0]["candidate_utc_open"] = (
        "2026-01-15T14:00:00+00:00"
    )

    write(
        folder,
        rows,
        meta["batch_evidence_sha256"][0],
        meta["clock_mapping_implementation_sha256"],
    )

    with pytest.raises(
        ResearchAuditError,
        match="reconstructed source",
    ):
        audit_research_export(folder)


def test_corrupt_file_rejected(tmp_path):
    folder, _, _ = make_export(tmp_path)

    with (folder / "candles.jsonl").open("ab") as out:
        out.write(b"corrupt")

    with pytest.raises(
        ResearchAuditError,
        match="verification",
    ):
        audit_research_export(folder)


def test_wrong_manifest_stat_rejected(tmp_path):
    folder, rows, meta = make_export(tmp_path)

    write(
        folder,
        rows,
        meta["batch_evidence_sha256"][0],
        meta["clock_mapping_implementation_sha256"],
        modifications={"winter_count": 42},
    )

    with pytest.raises(
        ResearchAuditError,
        match="statistic mismatch",
    ):
        audit_research_export(folder)


def test_wrong_lineage_rejected(tmp_path):
    folder, rows, meta = make_export(tmp_path)

    write(
        folder,
        rows,
        meta["batch_evidence_sha256"][0],
        meta["clock_mapping_implementation_sha256"],
        modifications={
            "batch_evidence_sha256": ["a" * 64]
        },
    )

    with pytest.raises(
        ResearchAuditError,
        match="lineage",
    ):
        audit_research_export(folder)


def test_duplicated_source_id_rejected(tmp_path):
    folder, rows, meta = make_export(tmp_path)

    rows[1]["source_row"]["row_id"] = (
        rows[0]["source_row"]["row_id"]
    )

    write(
        folder,
        rows,
        meta["batch_evidence_sha256"][0],
        meta["clock_mapping_implementation_sha256"],
    )

    with pytest.raises(
        ResearchAuditError,
        match="Duplicate",
    ):
        audit_research_export(folder)


def test_dangerous_manifest_rejected(tmp_path):
    folder, rows, meta = make_export(tmp_path)

    write(
        folder,
        rows,
        meta["batch_evidence_sha256"][0],
        meta["clock_mapping_implementation_sha256"],
        modifications={
            "execution_authorized": True
        },
    )

    with pytest.raises(
        ResearchAuditError,
        match="verification",
    ):
        audit_research_export(folder)


def test_unexpected_fields_rejected(tmp_path):
    folder, rows, meta = make_export(tmp_path)

    rows[0]["unapproved"] = True

    write(
        folder,
        rows,
        meta["batch_evidence_sha256"][0],
        meta["clock_mapping_implementation_sha256"],
    )

    with pytest.raises(
        ResearchAuditError,
        match="row schema",
    ):
        audit_research_export(folder)


def test_missing_directory_rejected(tmp_path):
    with pytest.raises(
        ResearchAuditError,
        match="verification",
    ):
        audit_research_export(
            tmp_path / "absent"
        )


def test_full_day_288_candles(tmp_path):
    folder, _, _ = make_export(
        tmp_path,
        minutes=tuple(range(0, 1440, 5)),
    )

    result = audit_research_export(folder)

    assert result.row_count == 288
    assert result.unobserved_intervals == 0

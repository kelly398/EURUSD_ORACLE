
"""Milestone 07 offline feature tests.

No PostgreSQL or trading APIs.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from backend.models.historical_utc_reconstruction import (
    LegacyM5Bar,
    reconstruct_historical_m5_bars,
)

from backend.models.test_audit_historical_utc_research_export import (
    make_export,
    write,
)

from backend.models import (
    historical_utc_research_features as features,
)


def test_audited_288_candle_input_and_protection(tmp_path):
    folder, _, meta = make_export(
        tmp_path,
        minutes=range(0, 1440, 5),
    )

    before = {
        p.name: p.read_bytes()
        for p in folder.iterdir()
    }

    result = features.build_clock_aware_research_features(
        folder
    )

    assert len(result.rows) == 288
    assert result.segment_count == 1
    assert result.gap_count == 0

    assert (
        result.source_lineage_sha256
        == meta["lineage_sha256"]
    )

    assert result.rows[0].log_return_1 is None
    assert result.rows[10].sma_close_12 is None
    assert result.rows[11].sma_close_12 is not None

    assert (
        result.rows[11].volatility_log_return_12
        is None
    )

    assert (
        result.rows[12].volatility_log_return_12
        == 0.0
    )

    assert result.rows[12].atr_14_pips is None
    assert result.rows[13].atr_14_pips is not None

    assert (
        result.rows[-1].record()["first_available_at"]
        is None
    )

    assert (
        result.rows[-1].record()["training_authorized"]
        is False
    )

    assert (
        result.manifest()["historical_utc_certified"]
        is False
    )

    assert before == {
        p.name: p.read_bytes()
        for p in folder.iterdir()
    }


def test_no_future_leakage_when_last_candle_changes(
    tmp_path,
):
    folder, original, meta = make_export(
        tmp_path,
        minutes=range(0, 100, 5),
    )

    before = (
        features.build_clock_aware_research_features(
            folder
        )
    )

    originals = []

    for item in original:
        x = item["source_row"]

        originals.append(
            LegacyM5Bar(
                row_id=x["row_id"],
                broker_label_time=datetime.fromisoformat(
                    x["broker_label_time"]
                ),
                open=x["open"],
                high=x["high"],
                low=x["low"],
                close=x["close"],
                tick_volume=x["tick_volume"],
                spread_points=x["spread_points"],
                real_volume=x["real_volume"],
            )
        )

    originals[-1] = replace(
        originals[-1],
        close=1.115,
    )

    batch = reconstruct_historical_m5_bars(
        originals
    )

    write(
        folder,
        [r.record() for r in batch.records],
        batch.evidence_sha256,
        batch.mapping_implementation_sha256,
    )

    after = (
        features.build_clock_aware_research_features(
            folder
        )
    )

    assert (
        [r.record() for r in before.rows[:-1]]
        == [r.record() for r in after.rows[:-1]]
    )

    assert (
        before.rows[-1].body_pips
        != after.rows[-1].body_pips
    )

    assert (
        before.feature_rows_sha256
        != after.feature_rows_sha256
    )


def test_gap_resets_all_rolling_indicators(tmp_path):
    minutes = (
        list(range(0, 65, 5))
        + list(range(80, 150, 5))
    )

    folder, _, _ = make_export(
        tmp_path,
        minutes=minutes,
    )

    dataset = (
        features.build_clock_aware_research_features(
            folder
        )
    )

    assert dataset.segment_count == 2
    assert dataset.gap_count == 1
    assert dataset.unobserved_intervals == 3

    gap_row = dataset.rows[13]

    assert gap_row.gap_before_m5 == 3
    assert gap_row.segment_id == 2
    assert gap_row.log_return_1 is None
    assert gap_row.sma_close_12 is None

    assert (
        gap_row.volatility_log_return_12
        is None
    )

    assert gap_row.atr_14_pips is None
    assert dataset.rows[-1].atr_14_pips is not None


def test_candle_end_is_not_claimed_as_receipt(tmp_path):
    folder, _, _ = make_export(tmp_path)

    result = (
        features.build_clock_aware_research_features(
            folder
        )
    )

    first = result.rows[0].record()

    assert datetime.fromisoformat(
        first["candidate_bar_end_utc"]
    ) == (
        datetime.fromisoformat(
            first["candidate_utc_open"]
        )
        + timedelta(minutes=5)
    )

    assert first["first_available_at"] is None
    assert first["execution_authorized"] is False


def test_fails_on_modified_saved_export(tmp_path):
    folder, _, _ = make_export(tmp_path)

    with (folder / "candles.jsonl").open("ab") as fp:
        fp.write(b"corrupt\n")

    with pytest.raises(
        features.ResearchFeatureError,
        match="audit",
    ):
        features.build_clock_aware_research_features(
            folder
        )


def test_fails_if_dataset_changes_after_audit(
    tmp_path,
    monkeypatch,
):
    folder, _, _ = make_export(tmp_path)

    actual_audit = features.audit_research_export

    def changed_after_audit(where):
        result = actual_audit(where)

        with (
            Path(where) / "candles.jsonl"
        ).open("ab") as fp:
            fp.write(b"\n")

        return result

    monkeypatch.setattr(
        features,
        "audit_research_export",
        changed_after_audit,
    )

    with pytest.raises(
        features.ResearchFeatureError
    ):
        features.build_clock_aware_research_features(
            folder
        )


@pytest.mark.parametrize(
    "bad",
    [0, -1, True, 500_001, "100", 1.5],
)
def test_max_rows_rejected(bad, tmp_path):
    with pytest.raises(
        features.ResearchFeatureError,
        match="max_rows",
    ):
        features.build_clock_aware_research_features(
            tmp_path,
            max_rows=bad,
        )


def test_too_small_limit_rejected(tmp_path):
    folder, _, _ = make_export(tmp_path)

    with pytest.raises(
        features.ResearchFeatureError,
        match="limit",
    ):
        features.build_clock_aware_research_features(
            folder,
            max_rows=2,
        )


def test_deterministic_features_checksum(tmp_path):
    folder, _, _ = make_export(
        tmp_path,
        minutes=range(0, 100, 5),
    )

    a = (
        features.build_clock_aware_research_features(
            folder
        )
    )

    b = (
        features.build_clock_aware_research_features(
            folder
        )
    )

    assert (
        a.feature_rows_sha256
        == b.feature_rows_sha256
    )

    assert a.manifest() == b.manifest()


def test_cli_prints_research_only_status(
    tmp_path,
    capsys,
):
    folder, _, _ = make_export(tmp_path)

    assert features.main([
        "--directory",
        str(folder),
    ]) == 0

    output = capsys.readouterr().out

    assert "Rows: 3" in output
    assert "NO FILES WRITTEN" in output
    assert "TRAINING: BLOCKED" in output


def test_cli_rejects_missing_dataset(
    tmp_path,
    capsys,
):
    assert features.main([
        "--directory",
        str(tmp_path / "missing"),
    ]) == 1

    assert "FEATURE BUILD BLOCKED" in (
        capsys.readouterr().out
    )

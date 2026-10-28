
"""Milestone 03: audited, research-only EURUSD M5 UTC-candidate dataset.

Combines Milestone 01 ReconstructionBatch objects returned by Milestone 02.
Pure and in-memory: no SQL, filesystem writes, trading, or model admission.
"""

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import json

from backend.models.historical_utc_reconstruction import (
    HistoricalReconstructionError,
    HistoricalUTCBarCandidate,
    MAPPING_ID,
    ReconstructionBatch,
    reconstruct_historical_m5_bars,
)

DATASET_VERSION = "EURUSD_M5_HISTORICAL_UTC_RESEARCH_V2"
FIVE_MINUTES = timedelta(minutes=5)


class ResearchDatasetBuildError(ValueError):
    """An incomplete or unverifiable research dataset must be rejected."""


def _json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha(value: object) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ResearchDatasetV2:
    """Validated candidate candles with explicit source provenance.

    Corrected timestamps are hypotheses, not broker-certified event times.
    This dataset must never enter live/paper trading or v1 models.
    """

    records: tuple[HistoricalUTCBarCandidate, ...]
    mapping_implementation_sha256: str
    batch_evidence_sha256: tuple[str, ...]
    rows_sha256: str
    lineage_sha256: str
    winter_count: int
    summer_count: int
    unobserved_intervals: int
    gap_count: int

    @property
    def research_only(self) -> bool:
        return True

    @property
    def historical_admission(self) -> bool:
        return False

    @property
    def execution_authorized(self) -> bool:
        return False

    def iter_jsonl(self):
        """Yield canonical JSONL rows without writing files."""
        for item in self.records:
            yield _json(item.record())

    def manifest(self) -> dict:
        """Return deterministic, research-only dataset metadata."""
        return {
            "schema_version": 2,
            "dataset_version": DATASET_VERSION,
            "symbol": "EURUSD",
            "timeframe": "M5",
            "source": "MT5_legacy_broker_labels",
            "clock_mapping_id": MAPPING_ID,
            "clock_mapping_implementation_sha256":
                self.mapping_implementation_sha256,
            "record_count": len(self.records),
            "batch_count": len(self.batch_evidence_sha256),
            "batch_evidence_sha256":
                list(self.batch_evidence_sha256),
            "rows_jsonl_sha256": self.rows_sha256,
            "lineage_sha256": self.lineage_sha256,
            "first_source_row_id":
                self.records[0].original.row_id,
            "last_source_row_id":
                self.records[-1].original.row_id,
            "first_broker_label":
                self.records[0].original.broker_label_time.isoformat(),
            "last_broker_label":
                self.records[-1].original.broker_label_time.isoformat(),
            "first_candidate_utc":
                self.records[0].candidate_utc_open.isoformat(),
            "last_candidate_utc":
                self.records[-1].candidate_utc_open.isoformat(),
            "winter_count": self.winter_count,
            "summer_count": self.summer_count,
            "gap_count": self.gap_count,
            "unobserved_intervals": self.unobserved_intervals,
            "unobserved_intervals_include_market_closures": True,
            "first_available_at": None,
            "source_receipt_certified": False,
            "research_only": True,
            "historical_admission": False,
            "execution_authorized": False,
            "training_authorized": False,
        }


def build_historical_utc_research_dataset(
    batches, *, max_rows: int = 100_000
) -> ResearchDatasetV2:
    """Validate all batches before returning a complete research dataset.

    A failed extraction never returns a partial dataset.
    Batch pagination affects lineage, but not the content checksum.
    Unobserved M5 intervals may include normal market closures.
    """

    if (
        isinstance(max_rows, bool)
        or not isinstance(max_rows, int)
        or not 1 <= max_rows <= 500_000
    ):
        raise ResearchDatasetBuildError(
            "max_rows must be an integer from 1 to 500000."
        )

    rows: list[HistoricalUTCBarCandidate] = []
    seen_ids: set[int] = set()
    proofs: list[str] = []

    source_sha = None
    previous_label = None
    previous_utc = None

    winter = 0
    summer = 0
    gaps = 0
    unobserved = 0

    content_hash = sha256()

    for index, batch in enumerate(batches, start=1):

        if (
            not isinstance(batch, ReconstructionBatch)
            or not batch.records
        ):
            raise ResearchDatasetBuildError(
                f"Batch {index} is empty or invalid."
            )

        if (
            batch.research_only is not True
            or batch.historical_admission is not False
        ):
            raise ResearchDatasetBuildError(
                f"Batch {index} is not research-only."
            )

        if len(rows) + len(batch.records) > max_rows:
            raise ResearchDatasetBuildError(
                "Research dataset row limit exceeded."
            )

        # Independently reconstruct source labels and fingerprints.
        # Reject modified candidates or altered batch evidence.

        try:
            expected = reconstruct_historical_m5_bars(
                item.original for item in batch.records
            )
        except (
            HistoricalReconstructionError,
            AttributeError,
            TypeError,
            ValueError,
        ) as exc:
            raise ResearchDatasetBuildError(
                f"Batch {index} failed reconstruction verification."
            ) from exc

        if (
            batch.manifest() != expected.manifest()
            or tuple(x.record() for x in batch.records)
            != tuple(x.record() for x in expected.records)
        ):
            raise ResearchDatasetBuildError(
                f"Batch {index} fingerprint or conversion mismatch."
            )

        if source_sha is None:
            source_sha = expected.mapping_implementation_sha256
        elif source_sha != expected.mapping_implementation_sha256:
            raise ResearchDatasetBuildError(
                "Clock mapping implementation changed mid-dataset."
            )

        for item in expected.records:

            bar = item.original
            candidate_time = item.candidate_utc_open

            if bar.row_id in seen_ids:
                raise ResearchDatasetBuildError(
                    "Duplicate source row ID across batches."
                )

            if (
                previous_label is not None
                and bar.broker_label_time <= previous_label
            ):
                raise ResearchDatasetBuildError(
                    "Broker labels are not strictly increasing."
                )

            if previous_utc is not None:

                elapsed = candidate_time - previous_utc

                if (
                    elapsed < FIVE_MINUTES
                    or elapsed % FIVE_MINUTES
                ):
                    raise ResearchDatasetBuildError(
                        "UTC candidates are not increasing in M5 units."
                    )

                if elapsed > FIVE_MINUTES:
                    gaps += 1
                    unobserved += int(
                        elapsed // FIVE_MINUTES
                    ) - 1

            content_hash.update(
                _json(item.record()).encode("utf-8") + b"\n"
            )

            seen_ids.add(bar.row_id)
            rows.append(item)

            previous_label = bar.broker_label_time
            previous_utc = candidate_time

        winter += expected.winter_count
        summer += expected.summer_count
        proofs.append(expected.evidence_sha256)

    if not rows:
        raise ResearchDatasetBuildError(
            "Empty extraction cannot become a dataset."
        )

    rows_digest = content_hash.hexdigest()

    lineage_digest = _sha({
        "schema_version": 2,
        "dataset_version": DATASET_VERSION,
        "mapping_id": MAPPING_ID,
        "mapping_implementation_sha256": source_sha,
        "batch_evidence_sha256": proofs,
        "record_count": len(rows),
        "rows_jsonl_sha256": rows_digest,
    })

    return ResearchDatasetV2(
        records=tuple(rows),
        mapping_implementation_sha256=source_sha,
        batch_evidence_sha256=tuple(proofs),
        rows_sha256=rows_digest,
        lineage_sha256=lineage_digest,
        winter_count=winter,
        summer_count=summer,
        unobserved_intervals=unobserved,
        gap_count=gaps,
    )

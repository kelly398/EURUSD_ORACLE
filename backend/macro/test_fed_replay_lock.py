"""F4.3.1: Offline tests for conservative Fed replay selection.

Run: python -m backend.macro.test_fed_replay_lock
No DB, HTTP, broker, or HMM execution.
"""

import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from backend.macro.fed_rate_protocol import build_fed_content_hash
from backend.macro.fed_replay_lock import (
    FedReplayLockError,
    lock_fed_data_as_of,
)
from backend.macro.macro_schemas import CentralBankRateSnapshot


UTC = timezone.utc
RELEASE = datetime(2026, 9, 16, 18, tzinfo=UTC)
INGESTED = RELEASE + timedelta(minutes=2)
EFFECTIVE = date(2026, 9, 17)
STATEMENT = "monetary20260916a"


def fed_pair(
    *,
    statement=STATEMENT,
    effective=EFFECTIVE,
    release=RELEASE,
    ingested=INGESTED,
    lower=3.75,
    upper=4.0,
):
    """Synthetic pair with F1-consistent hashes, not source attestation."""

    out = []

    for bound, value in (
        ("lower", lower),
        ("upper", upper),
    ):
        rate_name = f"federal_funds_target_{bound}"

        out.append(
            CentralBankRateSnapshot(
                central_bank="FED",
                source="FED_FOMC_RELEASE",
                source_record_id=f"FOMC:{statement}:{bound}",
                rate_name=rate_name,
                reference_date=effective,
                available_at=release,
                ingested_at=ingested,
                value_percent=value,
                content_hash=build_fed_content_hash(
                    statement,
                    rate_name,
                    effective,
                    format(Decimal(str(lower)), "f"),
                    format(Decimal(str(upper)), "f"),
                ),
            )
        )

    return tuple(out)


class FedReplayLockTests(unittest.TestCase):

    def test_01_empty_is_allowed(self):
        self.assertEqual(
            lock_fed_data_as_of((), INGESTED),
            (),
        )

    def test_02_complete_pair_at_ingestion_boundary(self):
        pair = fed_pair()

        self.assertEqual(
            lock_fed_data_as_of(pair, INGESTED),
            pair,
        )

    def test_03_release_before_effective_date_is_allowed(self):
        pair = fed_pair(ingested=RELEASE)

        selected = lock_fed_data_as_of(
            pair,
            RELEASE,
        )

        self.assertEqual(selected, pair)

        self.assertGreater(
            selected[0].reference_date,
            RELEASE.date(),
        )

    def test_04_future_release_excluded(self):
        pair = fed_pair(ingested=RELEASE)

        self.assertEqual(
            lock_fed_data_as_of(
                pair,
                RELEASE - timedelta(microseconds=1),
            ),
            (),
        )

    def test_05_retrieved_after_replay_timestamp_excluded(self):
        self.assertEqual(
            lock_fed_data_as_of(
                fed_pair(),
                RELEASE + timedelta(minutes=1),
            ),
            (),
        )

    def test_06_partial_pair_rejected(self):
        for item in fed_pair():

            with self.subTest(item=item.rate_name):
                with self.assertRaisesRegex(
                    FedReplayLockError,
                    "complete lower/upper",
                ):
                    lock_fed_data_as_of(
                        (item,),
                        INGESTED,
                    )

    def test_07_source_identity_rejected_even_if_future(self):
        pair = fed_pair()

        for update in (
            {"central_bank": "ECB"},
            {"source": "ECB_SDMX_FM"},
        ):
            with self.subTest(update=update):
                with self.assertRaisesRegex(
                    FedReplayLockError,
                    "source identity",
                ):
                    lock_fed_data_as_of(
                        (
                            pair[0].model_copy(update=update),
                            pair[1],
                        ),
                        RELEASE,
                    )

    def test_08_noncanonical_ids_rejected(self):
        pair = fed_pair()

        for record_id in (
            "WRONG:monetary20260916a:lower",
            "FOMC:monetary:lower",
            "FOMC:monetary20260916a:upper",
        ):
            with self.subTest(id=record_id):
                with self.assertRaisesRegex(
                    FedReplayLockError,
                    "identity",
                ):
                    lock_fed_data_as_of(
                        (
                            pair[0].model_copy(
                                update={
                                    "source_record_id": record_id
                                }
                            ),
                            pair[1],
                        ),
                        INGESTED,
                    )

    def test_09_early_ingestion_rejected(self):
        pair = fed_pair()

        with self.assertRaisesRegex(
            FedReplayLockError,
            "predates",
        ):
            lock_fed_data_as_of(
                (
                    pair[0].model_copy(
                        update={
                            "ingested_at":
                                RELEASE - timedelta(seconds=1)
                        }
                    ),
                    pair[1],
                ),
                INGESTED,
            )

    def test_10_wrong_item_type_rejected(self):
        with self.assertRaises(TypeError):
            lock_fed_data_as_of(
                ("bogus",),
                INGESTED,
            )

    def test_11_naive_as_of_rejected(self):
        with self.assertRaisesRegex(
            FedReplayLockError,
            "timezone-aware",
        ):
            lock_fed_data_as_of(
                fed_pair(),
                INGESTED.replace(tzinfo=None),
            )

    def test_12_hash_tampering_rejected(self):
        pair = fed_pair()

        wrong_hash = pair[0].model_copy(
            update={"content_hash": "a" * 64}
        )

        with self.assertRaisesRegex(
            FedReplayLockError,
            "hash",
        ):
            lock_fed_data_as_of(
                (wrong_hash, pair[1]),
                INGESTED,
            )

    def test_13_value_tampering_rejected_by_hash(self):
        pair = fed_pair()

        modified = pair[0].model_copy(
            update={"value_percent": 3.5}
        )

        with self.assertRaisesRegex(
            FedReplayLockError,
            "hash",
        ):
            lock_fed_data_as_of(
                (modified, pair[1]),
                INGESTED,
            )

    def test_14_conflicting_versions_at_same_available_time_rejected(self):
        original = fed_pair()

        revised = fed_pair(
            lower=3.5,
            ingested=INGESTED + timedelta(minutes=1),
        )

        with self.assertRaisesRegex(
            FedReplayLockError,
            "Ambiguous Fed revisions",
        ):
            lock_fed_data_as_of(
                original + revised,
                INGESTED + timedelta(minutes=1),
            )

    def test_15_exact_duplicate_returns_canonical_pair(self):
        pair = fed_pair()

        later_duplicate = tuple(
            item.model_copy(
                update={
                    "ingested_at":
                        INGESTED + timedelta(hours=1)
                }
            )
            for item in pair
        )

        self.assertEqual(
            lock_fed_data_as_of(
                later_duplicate[::-1] + pair[::-1],
                INGESTED + timedelta(hours=1),
            ),
            pair,
        )

    def test_16_revisions_are_selected_atomically(self):
        original = fed_pair()

        revised = fed_pair(
            lower=3.5,
            upper=3.75,
            release=RELEASE + timedelta(hours=1),
            ingested=RELEASE + timedelta(
                hours=1,
                minutes=2,
            ),
        )

        self.assertEqual(
            lock_fed_data_as_of(
                original + revised,
                revised[0].ingested_at,
            ),
            revised,
        )

    def test_17_before_revised_ingestion_keep_original(self):
        original = fed_pair()

        revised = fed_pair(
            lower=3.5,
            upper=3.75,
            release=RELEASE + timedelta(hours=1),
            ingested=RELEASE + timedelta(
                hours=1,
                minutes=2,
            ),
        )

        self.assertEqual(
            lock_fed_data_as_of(
                original + revised,
                RELEASE + timedelta(
                    hours=1,
                    minutes=1,
                ),
            ),
            original,
        )

    def test_18_half_revised_pair_rejected(self):
        original = fed_pair()

        revised = fed_pair(
            lower=3.5,
            upper=3.75,
            release=RELEASE + timedelta(hours=1),
            ingested=RELEASE + timedelta(
                hours=1,
                minutes=2,
            ),
        )

        with self.assertRaisesRegex(
            FedReplayLockError,
            "inconsistent provenance",
        ):
            lock_fed_data_as_of(
                original + revised[:1],
                revised[0].ingested_at,
            )

    def test_19_mismatched_pair_chronology_rejected(self):
        original = fed_pair()

        for update in (
            {
                "reference_date":
                    date(2026, 9, 18)
            },
            {
                "available_at":
                    RELEASE + timedelta(seconds=1)
            },
            {
                "ingested_at":
                    INGESTED + timedelta(seconds=1)
            },
        ):
            with self.subTest(update=update):
                with self.assertRaisesRegex(
                    FedReplayLockError,
                    "inconsistent provenance",
                ):
                    lock_fed_data_as_of(
                        (
                            original[0],
                            original[1].model_copy(
                                update=update
                            ),
                        ),
                        INGESTED + timedelta(seconds=1),
                    )

    def test_20_invalid_bounds_rejected(self):
        pair = fed_pair()

        with self.assertRaisesRegex(
            FedReplayLockError,
            "lower target",
        ):
            lock_fed_data_as_of(
                (
                    pair[0],
                    pair[1].model_copy(
                        update={"value_percent": 3.0}
                    ),
                ),
                INGESTED,
            )

    def test_21_nonfinite_rate_rejected(self):
        pair = fed_pair()

        for value in (
            float("nan"),
            float("inf"),
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    FedReplayLockError,
                    "finite",
                ):
                    lock_fed_data_as_of(
                        (
                            pair[0].model_copy(
                                update={
                                    "value_percent": value
                                }
                            ),
                            pair[1],
                        ),
                        INGESTED,
                    )

    def test_22_multiple_decisions_sorted(self):
        early = fed_pair()

        later = fed_pair(
            statement="monetary20260930a",
            effective=date(2026, 10, 1),
            release=datetime(
                2026, 9, 30, 18, tzinfo=UTC
            ),
            ingested=datetime(
                2026, 9, 30, 18, 2, tzinfo=UTC
            ),
            lower=3.5,
            upper=3.75,
        )

        self.assertEqual(
            lock_fed_data_as_of(
                later[::-1] + early[::-1],
                later[0].ingested_at,
            ),
            early + later,
        )

    def test_23_pending_second_release_is_excluded(self):
        early = fed_pair()

        later = fed_pair(
            statement="monetary20260930a",
            effective=date(2026, 10, 1),
            release=datetime(
                2026, 9, 30, 18, tzinfo=UTC
            ),
            ingested=datetime(
                2026, 9, 30, 18, 2, tzinfo=UTC
            ),
        )

        self.assertEqual(
            lock_fed_data_as_of(
                early + later,
                INGESTED,
            ),
            early,
        )

    def test_24_sub_micro_percent_rejected(self):
        pair = fed_pair()

        with self.assertRaisesRegex(
            FedReplayLockError,
            "invalid",
        ):
            lock_fed_data_as_of(
                (
                    pair[0].model_copy(
                        update={
                            "value_percent": 3.7500001
                        }
                    ),
                    pair[1],
                ),
                INGESTED,
            )

    def test_25_valid_micro_precision_is_accepted(self):
        pair = fed_pair(
            lower=0.000001,
            upper=0.25,
        )

        self.assertEqual(
            lock_fed_data_as_of(
                pair,
                INGESTED,
            ),
            pair,
        )

    def test_26_older_conflict_cannot_hide_behind_new_revision(self):
        original = fed_pair()

        conflicting_old = fed_pair(
            lower=3.5,
            upper=4.0,
        )

        revised = fed_pair(
            lower=3.5,
            upper=3.75,
            release=RELEASE + timedelta(hours=1),
            ingested=RELEASE + timedelta(
                hours=1,
                minutes=2,
            ),
        )

        with self.assertRaisesRegex(
            FedReplayLockError,
            "Ambiguous Fed revisions",
        ):
            lock_fed_data_as_of(
                revised + original + conflicting_old,
                revised[0].ingested_at,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
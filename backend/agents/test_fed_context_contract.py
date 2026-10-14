
"""Seat 25 F4.2.2: deterministic AgentContext Fed causal-contract tests.

Run from EURUSD_ORACLE project root:
    python -m backend.agents.test_fed_context_contract

Pure Pydantic construction. No HTTP, database, broker, or HMM runs.
Synthetic snapshots test context guards, not historical certification.
"""

from datetime import date, datetime, timedelta, timezone
import unittest

from backend.agents.contracts import AgentContext
from backend.macro.macro_schemas import CentralBankRateSnapshot
from backend.market_state.schemas import (
    CandleBar,
    DataQualityStatus,
    MarketStateSnapshot,
)


UTC = timezone.utc

RELEASE = datetime(
    2026, 9, 16, 18, 0, tzinfo=UTC
)

FETCHED = datetime(
    2026, 10, 13, 22, 30, tzinfo=UTC
)

AS_OF = datetime(
    2026, 10, 14, 12, 0, tzinfo=UTC
)

EFFECTIVE = date(2026, 9, 17)


def fed_pair(
    *,
    statement_id="monetary20260916a",
    effective_date=EFFECTIVE,
    release_at=RELEASE,
    fetched_at=FETCHED,
    lower_value=3.75,
    upper_value=4.0,
    hashes=("a" * 64, "b" * 64),
):
    """Synthetic complete Fed decision, not historically attested."""

    result = []

    for bound, value, digest in zip(
        ("lower", "upper"),
        (lower_value, upper_value),
        hashes,
        strict=True,
    ):
        result.append(
            CentralBankRateSnapshot(
                central_bank="FED",
                source="FED_FOMC_RELEASE",
                rate_name=f"federal_funds_target_{bound}",
                reference_date=effective_date,
                available_at=release_at,
                ingested_at=fetched_at,
                value_percent=value,
                source_record_id=f"FOMC:{statement_id}:{bound}",
                content_hash=digest,
            )
        )

    return tuple(result)


def context(*, fed_data=(), as_of=AS_OF):
    """Use real context construction and all Pydantic validators."""

    candle = CandleBar(
        symbol="EURUSD",
        timeframe="M5",
        timestamp=as_of - timedelta(minutes=5),
        open=1.10,
        high=1.11,
        low=1.09,
        close=1.10,
        tick_volume=100,
        spread_points=10,
        real_volume=0.0,
        source="TEST",
    )

    state = MarketStateSnapshot(
        symbol="EURUSD",
        timeframe="M5",
        as_of=as_of,
        data_quality=DataQualityStatus.PASS,
        candle_count=1,
    )

    return AgentContext(
        symbol="EURUSD",
        timeframe="M5",
        as_of=as_of,
        market_state=state,
        candles=(candle,),
        fed_data=tuple(fed_data),
    )


class FedContextContractTests(unittest.TestCase):

    def test_01_optional_fed_data_defaults_to_empty(self):
        self.assertIn("fed_data", AgentContext.model_fields)
        self.assertEqual(context().fed_data, ())
        self.assertEqual(context().ecb_data, ())

    def test_02_complete_fed_pair_accepted(self):
        pair = fed_pair()
        built = context(fed_data=pair)

        self.assertEqual(built.fed_data, pair)
        self.assertEqual(len(built.fed_data), 2)

    def test_03_announced_before_effective_date_is_allowed(self):
        as_of = RELEASE + timedelta(minutes=30)

        pair = fed_pair(
            fetched_at=RELEASE + timedelta(minutes=1)
        )

        built = context(
            fed_data=pair,
            as_of=as_of,
        )

        self.assertGreater(
            built.fed_data[0].reference_date,
            as_of.date(),
        )

    def test_04_exact_release_boundary_can_be_accepted(self):
        pair = fed_pair(fetched_at=RELEASE)

        self.assertEqual(
            context(
                fed_data=pair,
                as_of=RELEASE,
            ).fed_data,
            pair,
        )

    def test_05_publication_after_as_of_is_blocked(self):
        pair = fed_pair(fetched_at=RELEASE)

        with self.assertRaisesRegex(
            ValueError, "after as_of"
        ):
            context(
                fed_data=pair,
                as_of=RELEASE - timedelta(seconds=1),
            )

    def test_06_ingestion_after_as_of_is_blocked(self):
        pair = fed_pair()

        with self.assertRaisesRegex(
            ValueError, "after as_of"
        ):
            context(
                fed_data=pair,
                as_of=FETCHED - timedelta(seconds=1),
            )

    def test_07_ingestion_before_publication_is_blocked(self):
        pair = fed_pair(
            fetched_at=RELEASE - timedelta(seconds=1)
        )

        with self.assertRaisesRegex(
            ValueError, "predates reported availability"
        ):
            context(fed_data=pair)

    def test_08_missing_either_target_bound_is_blocked(self):
        lower, upper = fed_pair()

        for data in ((lower,), (upper,)):
            with self.subTest(data=data):
                with self.assertRaisesRegex(
                    ValueError,
                    "complete Fed lower/upper pairs",
                ):
                    context(fed_data=data)

    def test_09_duplicate_source_record_is_blocked(self):
        lower, upper = fed_pair()

        with self.assertRaisesRegex(
            ValueError, "duplicate Fed source record"
        ):
            context(
                fed_data=(lower, lower, upper)
            )

    def test_10_reversed_rate_order_is_blocked(self):
        lower, upper = fed_pair()

        with self.assertRaisesRegex(
            ValueError, "strictly ordered"
        ):
            context(
                fed_data=(upper, lower)
            )

    def test_11_wrong_central_bank_or_source_is_blocked(self):
        lower, upper = fed_pair()

        for update in (
            {"central_bank": "ECB"},
            {"source": "ECB_SDMX_FM"},
        ):
            with self.subTest(update=update):
                with self.assertRaisesRegex(
                    ValueError,
                    "official Fed source identity",
                ):
                    context(
                        fed_data=(
                            lower.model_copy(update=update),
                            upper,
                        )
                    )

    def test_12_unknown_rate_name_is_blocked(self):
        lower, upper = fed_pair()

        with self.assertRaisesRegex(
            ValueError, "unsupported rate name"
        ):
            context(
                fed_data=(
                    lower.model_copy(
                        update={"rate_name": "EFFR"}
                    ),
                    upper,
                )
            )

    def test_13_forged_or_swapped_record_identity_is_blocked(self):
        lower, upper = fed_pair()

        for record_id in (
            "FOMC:monetary20260916a:upper",
            "FOMC:monetary:lower",
            "WRONG:monetary20260916a:lower",
        ):
            with self.subTest(record_id=record_id):
                with self.assertRaisesRegex(
                    ValueError, "record identity"
                ):
                    context(
                        fed_data=(
                            lower.model_copy(
                                update={
                                    "source_record_id": record_id
                                }
                            ),
                            upper,
                        )
                    )

    def test_14_inconsistent_pair_chronology_is_blocked(self):
        lower, upper = fed_pair()

        variants = (
            {
                "reference_date":
                    EFFECTIVE + timedelta(days=1)
            },
            {
                "available_at":
                    RELEASE + timedelta(seconds=1)
            },
            {
                "ingested_at":
                    FETCHED + timedelta(seconds=1)
            },
        )

        for update in variants:
            with self.subTest(update=update):
                with self.assertRaisesRegex(
                    ValueError, "inconsistent provenance"
                ):
                    context(
                        fed_data=(
                            lower,
                            upper.model_copy(update=update),
                        )
                    )

    def test_15_reversed_bounds_or_hash_collision_is_blocked(self):
        lower, upper = fed_pair()

        for update in (
            {"value_percent": 3.0},
            {"content_hash": lower.content_hash},
        ):
            with self.subTest(update=update):
                with self.assertRaisesRegex(
                    ValueError, "pair is inconsistent"
                ):
                    context(
                        fed_data=(
                            lower,
                            upper.model_copy(update=update),
                        )
                    )

    def test_16_multiple_ordered_complete_decisions_are_allowed(self):
        earlier = fed_pair()

        later = fed_pair(
            statement_id="monetary20260930a",
            effective_date=date(2026, 10, 1),
            release_at=datetime(
                2026, 9, 30, 18, tzinfo=UTC
            ),
            hashes=("c" * 64, "d" * 64),
        )

        built = context(
            fed_data=earlier + later
        )

        self.assertEqual(
            len(built.fed_data),
            4,
        )

    def test_17_partial_second_decision_is_blocked(self):
        earlier = fed_pair()

        later = fed_pair(
            statement_id="monetary20260930a",
            effective_date=date(2026, 10, 1),
            release_at=datetime(
                2026, 9, 30, 18, tzinfo=UTC
            ),
            hashes=("c" * 64, "d" * 64),
        )

        with self.assertRaisesRegex(
            ValueError, "complete Fed lower/upper pairs"
        ):
            context(
                fed_data=earlier + later[:1]
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

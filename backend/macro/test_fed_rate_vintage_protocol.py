
"""F3.3.2: offline ALFRED-vintage corroboration protocol tests.

Run from project root inside oracle_env:
    python -m backend.macro.test_fed_rate_vintage_protocol

Uses the existing synthetic Fed document fixture. No HTTP, PostgreSQL,
MT5, broker actions, or reserved HMM evaluation.
"""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
import unittest

from backend.macro.fed_rate_backfill import FedRateBackfillPlanner
from backend.macro.fed_rate_vintage_protocol import (
    ALFRED_RATE_SERIES,
    ALFRED_VINTAGE_STATUS,
    FedAlfredVintageObservation,
    FedVintageCorroboration,
    FedVintageProtocolError,
    corroborate_fed_effective_rate_vintage,
)
from backend.macro.test_fed_rate_backfill import FakeClient
from backend.macro.test_fed_rate_extractor import (
    FETCHED_AT,
    RELEASE_AT,
    STATEMENT_URL,
)


UTC = timezone.utc
EFFECTIVE = date(2026, 9, 17)
AFTER_VINTAGE_DAY = datetime(
    2026, 9, 18, 4, 0, tzinfo=UTC
)


def candidate_plan():
    """Real F1/F2/F3.2 path on synthetic evidence, no network."""
    return FedRateBackfillPlanner(
        client=FakeClient()
    ).prepare(STATEMENT_URL)


def vintage(series_id, value_percent, **changes):
    """Synthetic, UNAUTHENTICATED response metadata and values."""
    defaults = dict(
        series_id=series_id,
        observation_date=EFFECTIVE,
        vintage_date=EFFECTIVE,
        realtime_start=EFFECTIVE,
        realtime_end=date(9999, 12, 31),
        value_percent=value_percent,
        response_sha256=(
            "a" if series_id == "DFEDTARL" else "b"
        ) * 64,
        fetched_at=FETCHED_AT,
    )
    defaults.update(changes)
    return FedAlfredVintageObservation(**defaults)


def rate_pair():
    return (
        vintage("DFEDTARL", "3.75"),
        vintage("DFEDTARU", "4.00"),
    )


class FedVintageProtocolTests(unittest.TestCase):

    def setUp(self):
        self.plan = candidate_plan()
        self.lower, self.upper = rate_pair()

    def check(self, plan=None, lower=None, upper=None):
        return corroborate_fed_effective_rate_vintage(
            self.plan if plan is None else plan,
            self.lower if lower is None else lower,
            self.upper if upper is None else upper,
        )

    def test_01_canonical_match_has_expected_identity_and_dates(self):
        result = self.check()

        self.assertIsInstance(result, FedVintageCorroboration)
        self.assertEqual(
            result.statement_id, "monetary20260916a"
        )
        self.assertEqual(result.effective_date, EFFECTIVE)
        self.assertEqual(result.vintage_date, EFFECTIVE)
        self.assertEqual(
            result.conservative_vintage_boundary,
            AFTER_VINTAGE_DAY,
        )
        self.assertEqual(
            result.evidence_observed_at, FETCHED_AT
        )
        self.assertEqual(
            result.observation_hashes,
            ("a" * 64, "b" * 64),
        )

    def test_02_matching_vintage_never_becomes_historical_certification(self):
        result = self.check()

        self.assertEqual(
            result.status, ALFRED_VINTAGE_STATUS
        )
        self.assertFalse(result.historical_html_certified)
        self.assertFalse(result.source_response_authenticated)
        self.assertFalse(result.persistence_permitted)
        self.assertFalse(self.plan.persistence_permitted)

        with self.assertRaises(FrozenInstanceError):
            result.status = "CERTIFIED"

    def test_03_official_series_contract_and_strict_order(self):
        self.assertEqual(
            ALFRED_RATE_SERIES,
            ("DFEDTARL", "DFEDTARU"),
        )

        with self.assertRaisesRegex(
            FedVintageProtocolError, "series identity"
        ):
            self.check(
                lower=self.upper,
                upper=self.lower,
            )

        with self.assertRaisesRegex(
            FedVintageProtocolError, "series identity"
        ):
            self.check(
                lower=replace(
                    self.lower, series_id="EFFR"
                )
            )

    def test_04_rate_token_normalization_preserves_economic_match(self):
        result = self.check(
            lower=replace(
                self.lower, value_percent="3-3/4"
            ),
            upper=replace(
                self.upper, value_percent="4"
            ),
        )

        self.assertEqual(
            result.status, ALFRED_VINTAGE_STATUS
        )

    def test_05_later_daily_vintage_keeps_date_only_boundary(self):
        next_day = EFFECTIVE + timedelta(days=1)

        result = self.check(
            lower=replace(
                self.lower, vintage_date=next_day
            ),
            upper=replace(
                self.upper, vintage_date=next_day
            ),
        )

        self.assertEqual(result.vintage_date, next_day)
        self.assertEqual(
            result.conservative_vintage_boundary,
            datetime(
                2026, 9, 19, 4, 0, tzinfo=UTC
            ),
        )
        self.assertFalse(result.historical_html_certified)

    def test_06_fetched_at_normalizes_timezone_and_observed_at_takes_max(self):
        plus_three = timezone(timedelta(hours=3))
        later = FETCHED_AT + timedelta(minutes=5)

        result = self.check(
            lower=replace(
                self.lower,
                fetched_at=FETCHED_AT.astimezone(plus_three),
            ),
            upper=replace(
                self.upper,
                fetched_at=later.astimezone(plus_three),
            ),
        )

        self.assertEqual(
            result.evidence_observed_at, later
        )
        self.assertIs(
            result.evidence_observed_at.tzinfo, UTC
        )

    def test_07_reject_non_plan_or_non_observations(self):
        actions = (
            lambda: self.check(plan=object()),
            lambda: self.check(lower=object()),
            lambda: self.check(upper=object()),
        )

        for action in actions:
            with self.subTest(action=action):
                with self.assertRaises(
                    FedVintageProtocolError
                ):
                    action()

    def test_08_reject_missing_or_reversed_candidate_bounds(self):
        a, b = self.plan.candidate_snapshots

        for pair in ((a,), (b, a)):
            with self.subTest(pair=pair):
                with self.assertRaises(
                    FedVintageProtocolError
                ):
                    self.check(
                        plan=replace(
                            self.plan,
                            candidate_snapshots=pair,
                        )
                    )

    def test_09_reject_wrong_candidate_central_bank_or_source(self):
        a, b = self.plan.candidate_snapshots

        cases = (
            a.model_copy(
                update={"central_bank": "ECB"}
            ),
            a.model_copy(
                update={"source": "UNKNOWN"}
            ),
        )

        for mutated in cases:
            with self.subTest(mutated=mutated):
                with self.assertRaisesRegex(
                    FedVintageProtocolError,
                    "sources",
                ):
                    self.check(
                        plan=replace(
                            self.plan,
                            candidate_snapshots=(mutated, b),
                        )
                    )

    def test_10_reject_candidate_record_id_or_content_hash_changes(self):
        a, b = self.plan.candidate_snapshots

        cases = (
            b.model_copy(
                update={"source_record_id": "forged"}
            ),
            b.model_copy(
                update={"content_hash": "0" * 64}
            ),
            b.model_copy(
                update={"value_percent": 3.99}
            ),
        )

        for changed in cases:
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(
                    FedVintageProtocolError,
                    "hash mismatch",
                ):
                    self.check(
                        plan=replace(
                            self.plan,
                            candidate_snapshots=(a, changed),
                        )
                    )

    def test_11_reject_candidate_timestamps_and_reference_date(self):
        a, b = self.plan.candidate_snapshots

        cases = (
            a.model_copy(
                update={
                    "reference_date": date(2026, 9, 18)
                }
            ),
            a.model_copy(
                update={
                    "available_at": (
                        RELEASE_AT + timedelta(seconds=1)
                    )
                }
            ),
            a.model_copy(
                update={
                    "ingested_at": (
                        FETCHED_AT + timedelta(seconds=1)
                    )
                }
            ),
        )

        for changed in cases:
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(
                    FedVintageProtocolError,
                    "chronology",
                ):
                    self.check(
                        plan=replace(
                            self.plan,
                            candidate_snapshots=(changed, b),
                        )
                    )

    def test_12_reject_bad_candidate_plan_release_or_ingestion_clock(self):
        cases = (
            replace(
                self.plan,
                announced_at=datetime(2026, 9, 16, 18),
            ),
            replace(
                self.plan,
                ingested_at=(
                    RELEASE_AT - timedelta(seconds=1)
                ),
            ),
        )

        for changed in cases:
            with self.subTest(plan=changed):
                with self.assertRaises(
                    FedVintageProtocolError
                ):
                    self.check(plan=changed)

    def test_13_reject_candidate_decision_identity_change(self):
        with self.assertRaises(FedVintageProtocolError):
            self.check(
                plan=replace(
                    self.plan,
                    statement_id="monetary20260917a",
                )
            )

    def test_14_reject_vintage_dates_that_disagree_between_bounds(self):
        with self.assertRaisesRegex(
            FedVintageProtocolError,
            "same vintage",
        ):
            self.check(
                upper=replace(
                    self.upper,
                    vintage_date=date(2026, 9, 18),
                )
            )

    def test_15_reject_observation_date_not_matching_effective_date(self):
        with self.assertRaisesRegex(
            FedVintageProtocolError,
            "effective date",
        ):
            self.check(
                lower=replace(
                    self.lower,
                    observation_date=date(2026, 9, 16),
                )
            )

    def test_16_reject_vintage_before_effective_date_even_if_window_contains_it(self):
        prior = date(2026, 9, 16)

        low = replace(
            self.lower,
            vintage_date=prior,
            realtime_start=prior,
        )

        high = replace(
            self.upper,
            vintage_date=prior,
            realtime_start=prior,
        )

        with self.assertRaisesRegex(
            FedVintageProtocolError,
            "predates",
        ):
            self.check(lower=low, upper=high)

    def test_17_reject_vintage_outside_realtime_validity_window(self):
        cases = (
            replace(
                self.lower,
                realtime_start=date(2026, 9, 18),
            ),
            replace(
                self.lower,
                realtime_end=date(2026, 9, 16),
            ),
        )

        for low in cases:
            with self.subTest(low=low):
                with self.assertRaisesRegex(
                    FedVintageProtocolError,
                    "not valid",
                ):
                    self.check(lower=low)

    def test_18_reject_non_date_typed_metadata(self):
        cases = (
            replace(
                self.lower,
                observation_date="2026-09-17",
            ),
            replace(
                self.lower,
                vintage_date=datetime(2026, 9, 17),
            ),
            replace(
                self.lower,
                realtime_start="2026-09-17",
            ),
            replace(
                self.lower,
                realtime_end="2026-09-17",
            ),
        )

        for low in cases:
            with self.subTest(low=low):
                with self.assertRaises(
                    FedVintageProtocolError
                ):
                    self.check(
                        lower=low,
                        upper=replace(
                            self.upper,
                            vintage_date=low.vintage_date,
                        ),
                    )

    def test_19_reject_invalid_response_fingerprints(self):
        invalid_hashes = (
            "",
            "a" * 63,
            "A" * 64,
            "z" * 64,
            123,
        )

        for hash_value in invalid_hashes:
            with self.subTest(hash_value=hash_value):
                with self.assertRaisesRegex(
                    FedVintageProtocolError,
                    "SHA-256",
                ):
                    self.check(
                        lower=replace(
                            self.lower,
                            response_sha256=hash_value,
                        )
                    )

    def test_20_reject_naive_fetch_time_and_future_vintage(self):
        with self.assertRaisesRegex(
            FedVintageProtocolError,
            "timezone-aware",
        ):
            self.check(
                lower=replace(
                    self.lower,
                    fetched_at=datetime(2026, 10, 13),
                )
            )

        with self.assertRaisesRegex(
            FedVintageProtocolError,
            "future vintage",
        ):
            self.check(
                lower=replace(
                    self.lower,
                    fetched_at=RELEASE_AT,
                )
            )

    def test_21_reject_unknown_or_conflicting_numeric_values(self):
        invalid = (
            "nan",
            "inf",
            "-1",
            "1e2",
            "3.75%",
            "",
            3.75,
        )

        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    FedVintageProtocolError,
                    "numeric rate",
                ):
                    self.check(
                        lower=replace(
                            self.lower,
                            value_percent=value,
                        )
                    )

        for value in ("3.50", "4.00"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    FedVintageProtocolError,
                    "conflicts",
                ):
                    self.check(
                        lower=replace(
                            self.lower,
                            value_percent=value,
                        )
                    )

    def test_22_reject_reversed_alfred_bounds(self):
        with self.assertRaisesRegex(
            FedVintageProtocolError,
            "lower bound exceeds",
        ):
            self.check(
                lower=replace(
                    self.lower,
                    value_percent="4.25",
                ),
                upper=replace(
                    self.upper,
                    value_percent="4.00",
                ),
            )

    def test_23_structural_hash_does_not_prove_source_authenticity(self):
        # A syntactically valid hash can be caller-invented.
        # The outcome must remain unauthenticated and non-persistable.
        result = self.check(
            lower=replace(
                self.lower,
                response_sha256="f" * 64,
            )
        )

        self.assertEqual(
            result.observation_hashes[0],
            "f" * 64,
        )
        self.assertFalse(
            result.source_response_authenticated
        )
        self.assertFalse(
            result.historical_html_certified
        )
        self.assertFalse(
            result.persistence_permitted
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

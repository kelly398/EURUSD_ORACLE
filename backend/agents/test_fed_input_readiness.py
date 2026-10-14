"""F4.5.2a: Strictly offline Fed input-readiness diagnostics tests.

Run: python -m backend.agents.test_fed_input_readiness

No HTTP, database access or writes, broker actions,
Council registration, or reserved HMM evaluation.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from backend.agents.contracts import AgentContext
from backend.agents.fed_input_readiness import (
    FedInputNotReady,
    FedInputReadinessReport,
    FedRateInputStatus,
    inspect_fed_input_readiness,
)
from backend.agents.test_fed_agent import (
    INGESTED,
    RELEASE,
    fed_pair,
    make_context,
)
from backend.macro.macro_schemas import EconomicCalendarEventSnapshot


def calendar_event(
    *,
    currency="USD",
    available_at=RELEASE,
    ingested_at=RELEASE,
):
    return EconomicCalendarEventSnapshot(
        source_event_id="synthetic:calendar:20260916",
        event_name="FOMC Test Candidate",
        jurisdiction="United States" if currency == "USD" else "EU",
        currency=currency,
        source="UNVERIFIED_TEST_DATA",
        scheduled_at=RELEASE + timedelta(hours=4),
        available_at=available_at,
        ingested_at=ingested_at,
        content_hash="c" * 64,
    )


def context_with_calendar(*, fed_data=(), calendar=()):
    original = make_context(fed_data=fed_data)

    return AgentContext.model_validate({
        **original.model_dump(),
        "economic_calendar": [
            event.model_dump()
            for event in calendar
        ],
    })


class FedInputReadinessTests(unittest.TestCase):

    def test_01_absent_rates_are_reported(self):
        report = inspect_fed_input_readiness(make_context())

        self.assertEqual(
            report.rate_input_status,
            FedRateInputStatus.ABSENT,
        )
        self.assertEqual(report.rate_snapshot_count, 0)
        self.assertIn("FED_RATE_INPUT_ABSENT", report.blockers)

    def test_02_f1_valid_pair_is_diagnostic_only(self):
        report = inspect_fed_input_readiness(
            make_context(fed_data=fed_pair())
        )

        self.assertEqual(
            report.rate_input_status,
            FedRateInputStatus.INTERNALLY_CONSISTENT,
        )
        self.assertEqual(report.validated_decision_count, 1)
        self.assertEqual(report.rate_snapshot_count, 2)
        self.assertFalse(report.council_registration_authorized)

    def test_03_two_decisions_are_counted(self):
        earlier = fed_pair(
            statement="monetary20260729a",
            release=datetime(
                2026, 7, 29, 18, tzinfo=timezone.utc
            ),
            ingested=datetime(
                2026, 7, 29, 18, 1, tzinfo=timezone.utc
            ),
            effective=RELEASE.date(),
        )

        report = inspect_fed_input_readiness(
            make_context(fed_data=earlier + fed_pair())
        )

        self.assertEqual(report.validated_decision_count, 2)

    def test_04_hash_tampering_is_rejected_even_if_context_copied(self):
        lower, upper = fed_pair()

        bypassed = make_context().model_copy(update={
            "fed_data": (
                lower.model_copy(
                    update={"content_hash": "f" * 64}
                ),
                upper,
            )
        })

        report = inspect_fed_input_readiness(bypassed)

        self.assertEqual(
            report.rate_input_status,
            FedRateInputStatus.INVALID,
        )
        self.assertEqual(report.validated_decision_count, 0)

    def test_05_incomplete_pair_is_invalid(self):
        lower, _ = fed_pair()

        bypassed = make_context().model_copy(
            update={"fed_data": (lower,)}
        )

        self.assertEqual(
            inspect_fed_input_readiness(
                bypassed
            ).rate_input_status,
            FedRateInputStatus.INVALID,
        )

    def test_06_reordered_pair_is_invalid(self):
        bypassed = make_context().model_copy(
            update={"fed_data": fed_pair()[::-1]}
        )

        self.assertEqual(
            inspect_fed_input_readiness(
                bypassed
            ).rate_input_status,
            FedRateInputStatus.INVALID,
        )

    def test_07_future_ingestion_is_invalid(self):
        lower, upper = fed_pair()

        later = lower.model_copy(update={
            "ingested_at": INGESTED + timedelta(days=1)
        })

        bypassed = make_context().model_copy(
            update={"fed_data": (later, upper)}
        )

        self.assertEqual(
            inspect_fed_input_readiness(
                bypassed
            ).rate_input_status,
            FedRateInputStatus.INVALID,
        )

    def test_08_usd_calendar_is_only_a_candidate(self):
        context = context_with_calendar(
            fed_data=fed_pair(),
            calendar=(calendar_event(),),
        )

        report = inspect_fed_input_readiness(context)

        self.assertEqual(
            report.usd_calendar_candidate_count,
            1,
        )
        self.assertIn(
            "FED_CALENDAR_SOURCE_NOT_VERIFIED",
            report.blockers,
        )
        self.assertFalse(report.council_registration_authorized)

    def test_09_ecb_calendar_does_not_count_as_usd(self):
        context = context_with_calendar(
            fed_data=fed_pair(),
            calendar=(calendar_event(currency="EUR"),),
        )

        self.assertEqual(
            inspect_fed_input_readiness(
                context
            ).usd_calendar_candidate_count,
            0,
        )

    def test_10_calendar_observed_after_as_of_cannot_count(self):
        context = make_context(fed_data=fed_pair())

        future_event = calendar_event(
            ingested_at=context.as_of + timedelta(seconds=1)
        )

        # Deliberately bypass construction-time validation to
        # test the audit's independent timestamp defense.
        bypassed = context.model_copy(update={
            "economic_calendar": (future_event,)
        })

        report = inspect_fed_input_readiness(bypassed)

        self.assertEqual(report.usd_calendar_candidate_count, 0)
        self.assertFalse(report.council_registration_authorized)

    def test_11_calendar_claiming_future_availability_cannot_count(self):
        context = make_context(fed_data=fed_pair())

        event = calendar_event(
            available_at=context.as_of + timedelta(seconds=1),
            ingested_at=context.as_of + timedelta(seconds=1),
        )

        bypassed = context.model_copy(update={
            "economic_calendar": (event,)
        })

        self.assertEqual(
            inspect_fed_input_readiness(
                bypassed
            ).usd_calendar_candidate_count,
            0,
        )

    def test_12_bad_calendar_observation_order_cannot_count(self):
        event = calendar_event(
            available_at=INGESTED,
            ingested_at=RELEASE,
        )

        context = context_with_calendar(
            fed_data=fed_pair(),
            calendar=(event,),
        )

        self.assertEqual(
            inspect_fed_input_readiness(
                context
            ).usd_calendar_candidate_count,
            0,
        )

    def test_13_invalid_context_type_rejected(self):
        with self.assertRaisesRegex(TypeError, "AgentContext"):
            inspect_fed_input_readiness(object())

    def test_14_invalid_context_clock_rejected(self):
        context = make_context().model_copy(update={
            "as_of": RELEASE.replace(tzinfo=None)
        })

        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            inspect_fed_input_readiness(context)

    def test_15_no_source_can_override_registration_deny(self):
        report = inspect_fed_input_readiness(
            context_with_calendar(
                fed_data=fed_pair(),
                calendar=(calendar_event(),),
            )
        )

        self.assertFalse(report.council_registration_authorized)
        self.assertFalse(report.historical_replay_authorized)
        self.assertFalse(report.fed_persistence_authorized)

        self.assertIn(
            "FED_SOURCE_VERSION_ATTESTATION_MISSING",
            report.blockers,
        )
        self.assertIn(
            "FED_CONTEXT_ACQUISITION_NOT_ACTIVATED",
            report.blockers,
        )

    def test_16_require_registration_always_blocks(self):
        report = inspect_fed_input_readiness(
            make_context(fed_data=fed_pair())
        )

        with self.assertRaisesRegex(
            FedInputNotReady,
            "registration blocked",
        ):
            report.require_council_registration()

    def test_17_forging_empty_blockers_still_cannot_authorize(self):
        report = inspect_fed_input_readiness(
            make_context(fed_data=fed_pair())
        )

        altered = replace(report, blockers=())

        self.assertFalse(altered.council_registration_authorized)

        with self.assertRaises(FedInputNotReady):
            altered.require_council_registration()

    def test_18_report_is_read_only_and_context_unchanged(self):
        context = make_context(fed_data=fed_pair())
        before = context.model_dump()

        report = inspect_fed_input_readiness(context)

        self.assertEqual(context.model_dump(), before)
        self.assertIsInstance(report, FedInputReadinessReport)


if __name__ == "__main__":
    unittest.main(verbosity=2)
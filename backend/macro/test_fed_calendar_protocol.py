
"""F4.5.4a: Offline FOMC regular-schedule candidate tests.

Run: python -m backend.macro.test_fed_calendar_protocol
No networking, database access, Council registration, broker, or HMM.
"""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
import unittest

from backend.macro.fed_calendar_protocol import (
    FED_2026_SCHEDULED_MEETINGS,
    FED_2026_SCHEDULE_RELEASE_LABEL_UTC,
    FED_2026_SCHEDULE_URL,
    FED_FOMC_SCHEDULE_SOURCE,
    FedCalendarProtocolError,
    FedScheduledMeetingSpec,
    build_fed_schedule_event_id,
    build_fed_schedule_content_hash,
    make_fed_schedule_candidate,
    planned_fed_statement_at,
)


OBSERVED = datetime(
    2026, 10, 14, 12, tzinfo=timezone.utc
)


class FedCalendarProtocolTests(unittest.TestCase):

    def setUp(self):
        self.september = FED_2026_SCHEDULED_MEETINGS[5]

    def candidate(self, spec=None, observed=OBSERVED):
        return make_fed_schedule_candidate(
            spec or self.september,
            first_observed_at=observed,
        )

    def test_01_exact_2026_schedule_from_official_release(self):
        self.assertEqual(
            len(FED_2026_SCHEDULED_MEETINGS), 8
        )

        self.assertEqual(
            [
                (
                    s.meeting_start.isoformat(),
                    s.meeting_end.isoformat(),
                )
                for s in FED_2026_SCHEDULED_MEETINGS
            ],
            [
                ("2026-01-27", "2026-01-28"),
                ("2026-03-17", "2026-03-18"),
                ("2026-04-28", "2026-04-29"),
                ("2026-06-16", "2026-06-17"),
                ("2026-07-28", "2026-07-29"),
                ("2026-09-15", "2026-09-16"),
                ("2026-10-27", "2026-10-28"),
                ("2026-12-08", "2026-12-09"),
            ],
        )

    def test_02_official_label_august_2024_1330_edt(self):
        self.assertEqual(
            FED_2026_SCHEDULE_RELEASE_LABEL_UTC,
            datetime(
                2024, 8, 9, 17, 30,
                tzinfo=timezone.utc,
            ),
        )

    def test_03_january_winter_statement_nominal_1900_utc(self):
        self.assertEqual(
            planned_fed_statement_at(
                FED_2026_SCHEDULED_MEETINGS[0]
            ),
            datetime(
                2026, 1, 28, 19,
                tzinfo=timezone.utc,
            ),
        )

    def test_04_september_summer_nominal_1800_utc(self):
        self.assertEqual(
            planned_fed_statement_at(self.september),
            datetime(
                2026, 9, 16, 18,
                tzinfo=timezone.utc,
            ),
        )

    def test_05_october_dst_nominal_1800_utc(self):
        self.assertEqual(
            planned_fed_statement_at(
                FED_2026_SCHEDULED_MEETINGS[6]
            ),
            datetime(
                2026, 10, 28, 18,
                tzinfo=timezone.utc,
            ),
        )

    def test_06_december_standard_nominal_1900_utc(self):
        self.assertEqual(
            planned_fed_statement_at(
                FED_2026_SCHEDULED_MEETINGS[7]
            ),
            datetime(
                2026, 12, 9, 19,
                tzinfo=timezone.utc,
            ),
        )

    def test_07_stable_ids_by_year_ordinal(self):
        self.assertEqual(
            [
                build_fed_schedule_event_id(
                    s.year, s.ordinal
                )
                for s in FED_2026_SCHEDULED_MEETINGS
            ],
            [
                f"FED|FOMC_REGULAR_MEETING|2026|{n:02d}"
                for n in range(1, 9)
            ],
        )

    def test_08_rescheduled_meeting_preserves_logical_id(self):
        modified = replace(
            self.september,
            meeting_start=date(2026, 9, 22),
            meeting_end=date(2026, 9, 23),
        )

        self.assertEqual(
            self.candidate().event_id,
            build_fed_schedule_event_id(
                modified.year,
                modified.ordinal,
            ),
        )

    def test_09_reschedule_changes_content_hash(self):
        modified = replace(
            self.september,
            meeting_start=date(2026, 9, 22),
            meeting_end=date(2026, 9, 23),
        )

        self.assertNotEqual(
            self.candidate().schedule_content_sha256,
            build_fed_schedule_content_hash(modified),
        )

    def test_10_repeat_observation_does_not_change_content_hash(self):
        early = self.candidate(
            observed=FED_2026_SCHEDULE_RELEASE_LABEL_UTC
        )
        late = self.candidate()

        self.assertEqual(
            early.schedule_content_sha256,
            late.schedule_content_sha256,
        )
        self.assertNotEqual(
            early.first_observed_at,
            late.first_observed_at,
        )

    def test_11_content_hash_has_64_hex_characters(self):
        self.assertRegex(
            self.candidate().schedule_content_sha256,
            "^[0-9a-f]{64}$",
        )

    def test_12_no_historical_authenticity_or_admission_claim(self):
        candidate = self.candidate()

        self.assertFalse(candidate.historical_version_attested)
        self.assertFalse(candidate.council_calendar_admission_authorized)
        self.assertFalse(candidate.historical_replay_authorized)
        self.assertFalse(candidate.persistence_authorized)

    def test_13_no_actual_decision_or_policy_result_in_candidate(self):
        candidate = self.candidate()

        for name in (
            "actual",
            "forecast",
            "previous",
            "rate_value",
            "decision",
        ):
            self.assertFalse(hasattr(candidate, name))

    def test_14_offcycle_decisions_not_modeled_by_this_protocol(self):
        with self.assertRaisesRegex(
            FedCalendarProtocolError,
            "audited 2026",
        ):
            FedScheduledMeetingSpec(
                2020,
                1,
                date(2020, 3, 15),
                date(2020, 3, 16),
            )

        self.assertEqual(
            len(FED_2026_SCHEDULED_MEETINGS), 8
        )

    def test_15_claimed_release_prior_to_observation(self):
        with self.assertRaisesRegex(
            FedCalendarProtocolError,
            "precedes",
        ):
            self.candidate(
                observed=(
                    FED_2026_SCHEDULE_RELEASE_LABEL_UTC
                    - timedelta(seconds=1)
                ),
            )

    def test_16_naive_observation_rejected(self):
        with self.assertRaisesRegex(
            FedCalendarProtocolError,
            "timezone-aware",
        ):
            self.candidate(
                observed=OBSERVED.replace(tzinfo=None)
            )

    def test_17_future_observation_not_visible_to_older_replay(self):
        candidate = self.candidate()

        self.assertFalse(
            candidate.was_observed_by(
                datetime(
                    2025, 1, 1,
                    tzinfo=timezone.utc,
                )
            )
        )

        self.assertTrue(
            candidate.was_observed_by(OBSERVED)
        )

    def test_18_unaware_as_of_rejected(self):
        with self.assertRaisesRegex(
            FedCalendarProtocolError,
            "timezone-aware",
        ):
            self.candidate().was_observed_by(
                OBSERVED.replace(tzinfo=None)
            )

    def test_19_source_identity_exact_and_named(self):
        candidate = self.candidate()

        self.assertEqual(
            candidate.source_url,
            FED_2026_SCHEDULE_URL,
        )
        self.assertEqual(
            candidate.source,
            FED_FOMC_SCHEDULE_SOURCE,
        )
        self.assertIn(
            "federalreserve.gov",
            candidate.source_url,
        )

    def test_20_immutable_candidate(self):
        with self.assertRaises(FrozenInstanceError):
            self.candidate().event_id = "altered"

    def test_21_invalid_year_ordinal_and_type_rejected(self):
        for year, ordinal in (
            (2025, 1),
            (2026, 0),
            (2026, 9),
            (True, 1),
            (2026, True),
        ):
            with self.subTest(
                year=year,
                ordinal=ordinal,
            ):
                with self.assertRaises(
                    FedCalendarProtocolError
                ):
                    build_fed_schedule_event_id(
                        year, ordinal
                    )

    def test_22_invalid_meeting_span_rejected(self):
        with self.assertRaisesRegex(
            FedCalendarProtocolError,
            "two days",
        ):
            FedScheduledMeetingSpec(
                2026,
                6,
                date(2026, 9, 15),
                date(2026, 9, 17),
            )

    def test_23_invalid_meeting_spec_type_rejected(self):
        with self.assertRaises(TypeError):
            planned_fed_statement_at("bad")

        with self.assertRaises(TypeError):
            make_fed_schedule_candidate(
                "bad",
                first_observed_at=OBSERVED,
            )

    def test_24_timezone_equivalent_observation_is_normalized(self):
        eastern_offset = timezone(
            timedelta(hours=-4)
        )
        eastern_time = datetime(
            2026, 10, 14, 8,
            tzinfo=eastern_offset,
        )

        self.assertEqual(
            self.candidate(observed=eastern_time),
            self.candidate(),
        )

    def test_25_unreviewed_rescheduled_date_rejected_by_source_scope(self):
        revised = replace(
            self.september,
            meeting_start=date(2026, 9, 22),
            meeting_end=date(2026, 9, 23),
        )

        with self.assertRaisesRegex(
            FedCalendarProtocolError,
            "audited 2024 schedule",
        ):
            self.candidate(revised)


if __name__ == "__main__":
    unittest.main(verbosity=2)

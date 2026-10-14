
"""F4.5.6: Strictly offline FOMC schedule-source reconciliation tests.

Run: python -m backend.macro.test_fed_calendar_extractor
No network, database writes, broker, Council registration or HMM execution.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from html import escape
import unittest

from backend.macro.fed_calendar_client import (
    FedCalendarAcquisitionRecord,
    FedCalendarClient,
)
from backend.macro.fed_calendar_extractor import (
    FedCalendarExtractionError,
    extract_fed_calendar_schedule,
)
from backend.macro.fed_calendar_protocol import (
    FED_2026_SCHEDULED_MEETINGS,
    FED_2026_SCHEDULE_URL,
)
from backend.macro.test_fed_calendar_client import FakeOpener, FakeResponse


START = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
END = START + timedelta(seconds=2)

TITLE = (
    "Federal Open Market Committee announces its tentative "
    "meeting schedule for 2025 and 2026"
)

ROWS = (
    "Tuesday, January 27, and Wednesday, January 28",
    "Tuesday, March 17, and Wednesday, March 18",
    "Tuesday, April 28, and Wednesday, April 29",
    "Tuesday, June 16, and Wednesday, June 17",
    "Tuesday, July 28, and Wednesday, July 29",
    "Tuesday, September 15, and Wednesday, September 16",
    "Tuesday, October 27, and Wednesday, October 28",
    "Tuesday, December 8, and Wednesday, December 9",
)

ROLLOVER = (
    "Tuesday, January 26, and Wednesday, January 27, 2027"
)

POLICY_RULE = (
    "The Committee releases a policy statement at 2 p.m. Eastern Time "
    "on the second day of each regularly scheduled meeting, and the Chair "
    "holds a news conference at 2:30 p.m. Eastern Time the same day."
)


def html_document(
    *,
    rows=ROWS,
    rollover=ROLLOVER,
    title=TITLE,
    pub_date="August 09, 2024",
    release_label="For release at 1:30 p.m. EDT",
    policy_rule=POLICY_RULE,
    header="For 2026:",
    extra_html="",
) -> bytes:
    meeting_items = "\n".join(
        f"<li>{escape(row)}</li>"
        for row in rows
    )

    meeting_items += (
        "\n<li>" + escape(rollover) + "</li>"
    )

    html = f"""<!doctype html><html lang="en"><head>
<title>Fed 2026 Calendar</title></head><body>
<main><article>
<p>{escape(pub_date)}</p>
<h3>{escape(title)}</h3>
<p>{escape(release_label)}</p>
<p>The Federal Open Market Committee announced its tentative meeting schedule.</p>
<p>For 2025:</p>
<ul><li>Tuesday, January 28, and Wednesday, January 29</li></ul>
<p>{escape(header)}</p>
<ul>{meeting_items}</ul>
<p>{escape(policy_rule)}</p>
{extra_html}
<p>Last Update: August 09, 2024</p>
</article></main></body></html>"""

    return html.encode("utf-8")


def make_record(*, body=None, **changes):
    raw = html_document() if body is None else body

    record = FedCalendarAcquisitionRecord(
        source_url=FED_2026_SCHEDULE_URL,
        response_url=FED_2026_SCHEDULE_URL,
        http_status=200,
        request_started_at=START,
        response_completed_at=END,
        content_type="text/html; charset=utf-8",
        content_etag='"untrusted-etag"',
        content_last_modified=(
            "Fri, 09 Aug 2024 17:30:00 GMT"
        ),
        entity_body=raw,
        entity_body_sha256=sha256(raw).hexdigest(),
        transport_mode="INJECTED_TRANSPORT_UNVERIFIED",
    )

    return replace(record, **changes)


class FedCalendarExtractorTests(unittest.TestCase):

    def test_01_exact_eight_candidate_dates(self):
        result = extract_fed_calendar_schedule(
            make_record()
        )

        self.assertEqual(len(result.candidates), 8)

        self.assertEqual(
            [
                (c.meeting_start, c.meeting_end)
                for c in result.candidates
            ],
            [
                (spec.meeting_start, spec.meeting_end)
                for spec in FED_2026_SCHEDULED_MEETINGS
            ],
        )

    def test_02_dst_and_statement_time_from_existing_protocol(self):
        result = extract_fed_calendar_schedule(
            make_record()
        )

        self.assertEqual(
            result.candidates[6].planned_statement_at,
            datetime(
                2026, 10, 28, 18,
                tzinfo=timezone.utc,
            ),
        )

        self.assertEqual(
            result.candidates[7].planned_statement_at,
            datetime(
                2026, 12, 9, 19,
                tzinfo=timezone.utc,
            ),
        )

    def test_03_2027_rollover_is_explicit_not_ninth_2026_event(self):
        result = extract_fed_calendar_schedule(
            make_record()
        )

        self.assertEqual(
            result.rollover_2027_start,
            date(2027, 1, 26),
        )

        self.assertEqual(
            result.rollover_2027_end,
            date(2027, 1, 27),
        )

        self.assertEqual(len(result.candidates), 8)

    def test_04_original_bytes_bound_to_observation_but_not_attested(self):
        record = make_record()
        result = extract_fed_calendar_schedule(record)

        self.assertEqual(
            result.entity_body_sha256,
            sha256(record.entity_body).hexdigest(),
        )

        self.assertEqual(result.observed_at, END)

        self.assertEqual(
            result.transport_mode,
            "INJECTED_TRANSPORT_UNVERIFIED",
        )

        self.assertTrue(
            all(
                c.first_observed_at == END
                for c in result.candidates
            )
        )

        self.assertFalse(
            result.historical_source_version_attested
        )
        self.assertFalse(
            result.council_calendar_admission_authorized
        )
        self.assertFalse(result.historical_replay_authorized)
        self.assertFalse(result.persistence_authorized)

        self.assertTrue(
            all(
                not c.persistence_authorized
                for c in result.candidates
            )
        )

    def test_05_source_observation_time_changes_candidate_only(self):
        record = make_record()

        later = replace(
            record,
            request_started_at=END,
            response_completed_at=(
                END + timedelta(hours=1)
            ),
        )

        a = extract_fed_calendar_schedule(record)
        b = extract_fed_calendar_schedule(later)

        self.assertEqual(
            a.entity_body_sha256,
            b.entity_body_sha256,
        )

        self.assertNotEqual(
            a.candidates[0].first_observed_at,
            b.candidates[0].first_observed_at,
        )

        self.assertEqual(
            a.candidates[0].schedule_content_sha256,
            b.candidates[0].schedule_content_sha256,
        )

    def test_06_nonsemantic_byte_change_changes_source_hash(self):
        altered = html_document(
            extra_html="<p>New footer comment.</p>"
        )

        one = extract_fed_calendar_schedule(
            make_record()
        )

        two = extract_fed_calendar_schedule(
            make_record(body=altered)
        )

        self.assertNotEqual(
            one.entity_body_sha256,
            two.entity_body_sha256,
        )

        self.assertEqual(
            one.candidates[0].schedule_content_sha256,
            two.candidates[0].schedule_content_sha256,
        )

    def test_07_forged_sha256_rejected(self):
        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "SHA-256",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    entity_body_sha256="f" * 64
                )
            )

    def test_08_modified_html_and_matching_hash_cannot_rewrite_approved_meetings(self):
        rows = list(ROWS)
        rows[5] = (
            "Tuesday, September 22, and Wednesday, September 23"
        )

        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "differs",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    body=html_document(rows=rows)
                )
            )

    def test_09_missing_meeting_rejected(self):
        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "eight 2026",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    body=html_document(rows=ROWS[:-1])
                )
            )

    def test_10_duplicate_meeting_rejected(self):
        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "eight 2026",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    body=html_document(
                        rows=ROWS + (ROWS[0],)
                    )
                )
            )

    def test_11_swapped_meeting_order_rejected(self):
        rows = list(ROWS)
        rows[0], rows[1] = rows[1], rows[0]

        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "differs",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    body=html_document(rows=rows)
                )
            )

    def test_12_unexpected_ninth_2026_meeting_rejected(self):
        extra = (
            "Tuesday, December 15, and Wednesday, December 16"
        )

        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "eight 2026",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    body=html_document(
                        rows=ROWS + (extra,)
                    )
                )
            )

    def test_13_incorrect_weekday_rejected(self):
        rows = list(ROWS)

        rows[0] = rows[0].replace(
            "Tuesday",
            "Monday",
            1,
        )

        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "weekday",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    body=html_document(rows=rows)
                )
            )

    def test_14_wrong_rollover_rejected(self):
        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "2027 rollover",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    body=html_document(
                        rollover=(
                            "Tuesday, February 2, and "
                            "Wednesday, February 3, 2027"
                        )
                    )
                )
            )

    def test_15_missing_2027_rollover_rejected(self):
        body = html_document().replace(
            f"<li>{ROLLOVER}</li>".encode(),
            b"",
        )

        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "eight 2026",
        ):
            extract_fed_calendar_schedule(
                make_record(body=body)
            )

    def test_16_forged_year_label_rejected(self):
        rows = list(ROWS)
        rows[0] += ", 2027"

        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "weekday|year|differs",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    body=html_document(rows=rows)
                )
            )

    def test_17_missing_statement_policy_rule_rejected(self):
        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "ambiguous",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    body=html_document(
                        policy_rule=(
                            "Statement will be announced later."
                        )
                    )
                )
            )

    def test_18_wrong_nominal_statement_time_rejected(self):
        altered = POLICY_RULE.replace(
            "at 2 p.m.",
            "at 2:30 p.m.",
            1,
        )

        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "ambiguous",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    body=html_document(
                        policy_rule=altered
                    )
                )
            )

    def test_19_missing_publication_label_rejected(self):
        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "release label",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    body=html_document(
                        release_label=(
                            "For release at 2:30 p.m. EDT"
                        )
                    )
                )
            )

    def test_20_wrong_article_title_rejected(self):
        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "title",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    body=html_document(
                        title="Unrelated Fed update"
                    )
                )
            )

    def test_21_wrong_article_date_rejected(self):
        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "document date",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    body=html_document(
                        pub_date="August 08, 2024"
                    )
                )
            )

    def test_22_for_2026_heading_missing_rejected(self):
        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "ambiguous",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    body=html_document(
                        header="For 2027:"
                    )
                )
            )

    def test_23_script_and_navigation_fakes_are_ignored(self):
        doc = html_document(
            extra_html=(
                f"<script>For 2026: {ROWS[0]}</script>"
                "<nav>For 2026:</nav>"
            )
        )

        result = extract_fed_calendar_schedule(
            make_record(body=doc)
        )

        self.assertEqual(len(result.candidates), 8)

    def test_24_duplicate_visible_year_heading_rejected(self):
        doc = html_document(
            extra_html="<p>For 2026:</p>"
        )

        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "ambiguous",
        ):
            extract_fed_calendar_schedule(
                make_record(body=doc)
            )

    def test_25_nested_formatting_and_html_entities_supported(self):
        doc = html_document()

        doc = doc.replace(
            b"<p>For 2026:</p>",
            b"<p>For <strong>2026:</strong></p>",
        )

        doc = doc.replace(
            b"Tuesday, September 15, and Wednesday, September 16",
            b"Tuesday, September 15, and&nbsp;Wednesday, September 16",
        )

        result = extract_fed_calendar_schedule(
            make_record(body=doc)
        )

        self.assertEqual(len(result.candidates), 8)

    def test_26_wrong_pinned_url_rejected(self):
        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "identity",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    response_url="https://evil.invalid/"
                )
            )

    def test_27_http_302_record_rejected(self):
        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "identity",
        ):
            extract_fed_calendar_schedule(
                make_record(http_status=302)
            )

    def test_28_invalid_entity_body_type_rejected(self):
        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "entity body",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    entity_body="not bytes"
                )
            )

    def test_29_inconsistent_or_naive_observation_clock_rejected(self):
        record = make_record()

        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "chronology",
        ):
            extract_fed_calendar_schedule(
                replace(
                    record,
                    request_started_at=(
                        END + timedelta(seconds=1)
                    ),
                )
            )

        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "timezone-aware",
        ):
            extract_fed_calendar_schedule(
                replace(
                    record,
                    response_completed_at=(
                        END.replace(tzinfo=None)
                    ),
                )
            )

    def test_30_wrong_content_type_or_transport_label_rejected(self):
        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "content-type",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    content_type="application/json"
                )
            )

        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "transport",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    transport_mode="TRUSTED_2024_CERTIFICATE"
                )
            )

    def test_31_invalid_utf8_or_missing_body_element_rejected(self):
        doc = html_document() + b"\xff"

        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "UTF-8",
        ):
            extract_fed_calendar_schedule(
                make_record(body=doc)
            )

        doc = (
            html_document()
            .replace(b"<body>", b"<main>")
            .replace(b"</body>", b"</main>")
        )

        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "body element",
        ):
            extract_fed_calendar_schedule(
                make_record(body=doc)
            )

    def test_32_result_has_no_live_calendar_snapshots_or_admission(self):
        result = extract_fed_calendar_schedule(
            make_record()
        )

        for attribute in (
            "economic_calendar",
            "calendar_snapshots",
            "context",
        ):
            self.assertFalse(
                hasattr(result, attribute)
            )

        self.assertFalse(
            result.candidates[0].council_calendar_admission_authorized
        )

    def test_33_in_memory_client_to_extractor_integration_remains_unverified(self):
        body = html_document()
        response = FakeResponse(body=body)
        opener = FakeOpener(response=response)
        times = iter((START, END))

        client = FedCalendarClient(
            opener=opener,
            clock=lambda: next(times),
        )

        acquisition = client.fetch()

        extraction = extract_fed_calendar_schedule(
            acquisition
        )

        self.assertEqual(
            len(extraction.candidates),
            8,
        )

        self.assertEqual(
            extraction.entity_body_sha256,
            acquisition.entity_body_sha256,
        )

        self.assertFalse(
            extraction.historical_source_version_attested
        )

        self.assertEqual(
            extraction.transport_mode,
            "INJECTED_TRANSPORT_UNVERIFIED",
        )

    def test_34_record_type_required(self):
        with self.assertRaises(TypeError):
            extract_fed_calendar_schedule(
                object()
            )

    def test_35_incomplete_html_envelope_rejected(self):
        body = html_document().replace(
            b"</html>",
            b"",
        )

        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "envelope",
        ):
            extract_fed_calendar_schedule(
                make_record(body=body)
            )

    def test_36_document_metadata_order_is_enforced(self):
        doc = html_document().replace(
            b"<p>August 09, 2024</p>",
            b"<p>For release at 1:30 p.m. EDT</p>",
            1,
        ).replace(
            b"<p>For release at 1:30 p.m. EDT</p>\n<p>The Federal Open Market Committee",
            b"<p>August 09, 2024</p>\n<p>The Federal Open Market Committee",
            1,
        )

        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "ambiguous",
        ):
            extract_fed_calendar_schedule(
                make_record(body=doc)
            )

    def test_37_conference_time_change_is_not_silently_accepted(self):
        with self.assertRaisesRegex(
            FedCalendarExtractionError,
            "ambiguous",
        ):
            extract_fed_calendar_schedule(
                make_record(
                    body=html_document(
                        policy_rule=POLICY_RULE.replace(
                            "at 2:30 p.m.",
                            "at 3 p.m.",
                        )
                    )
                )
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

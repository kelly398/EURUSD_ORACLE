
"""F2.4 deterministic tests for the FOMC evidence extractor.

Run from the project root with oracle_env active:
    python -m backend.macro.test_fed_rate_extractor

All documents are synthetic in-memory fixtures. No HTTP, database, trading,
or reserved HMM evaluation is used.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import unittest

from backend.macro.fed_rate_client import FedSourceBundle, FedSourceDocument
from backend.macro.fed_rate_extractor import (
    FedEvidenceExtractionError,
    extract_fed_original_rate_evidence,
)
from backend.macro.fed_rate_protocol import (
    FedRateProtocolError,
    build_fed_original_rate_snapshots,
    validate_fed_original_evidence,
)


STATEMENT_URL = (
    "https://www.federalreserve.gov/newsevents/pressreleases/"
    "monetary20260916a.htm"
)
IMPLEMENTATION_URL = (
    "https://www.federalreserve.gov/newsevents/pressreleases/"
    "monetary20260916a1.htm"
)
MEETING_URL = (
    "https://www.federalreserve.gov/monetarypolicy/"
    "fomcpresconf20260916.htm"
)
UTC = timezone.utc
FETCHED_AT = datetime(2026, 10, 13, 22, 17, tzinfo=UTC)
RELEASE_AT = datetime(2026, 9, 16, 18, 0, tzinfo=UTC)


def make_document(role, url, html, *, fetched_at=FETCHED_AT):
    raw = html.encode("utf-8")
    return FedSourceDocument(
        role=role,
        url=url,
        html=html,
        raw_bytes=raw,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        fetched_at=fetched_at,
    )


def september_bundle(*, statement=None, implementation=None, meeting=None):
    """Representative synthetic Fed-style markup, not archived releases."""
    if statement is None:
        statement = """<html><head><title>FOMC statement</title></head><body>
            <h1>September 16, 2026</h1>
            <h2>Federal Reserve issues FOMC statement</h2>
            <p>For release at 2:00 p.m. EDT</p>
            <p>The Committee decided to raise the target range for the
            federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent,
            in support of its dual mandate.</p>
            <p>A dissenter preferred a different rate of 3 to 3-1/4 percent.</p>
            <p>Last Update: October 13, 2026</p>
            </body></html>"""

    if implementation is None:
        implementation = """<html><body>
            <h1>Implementation Note issued September 16, 2026</h1>
            <p>The Board voted to raise the interest rate paid on reserve
            balances to 3.90 percent, effective September 17, 2026.</p>
            <p>As part of its policy decision:
            Effective September 17, 2026, the Federal Open Market Committee
            directs the Desk to: Undertake open market operations as necessary
            to maintain the federal funds rate in a target range of
            3-3/4 to 4 percent. Conduct standing overnight repurchase
            agreement operations at a rate of 4.0 percent.</p>
            </body></html>"""

    if meeting is None:
        meeting = """<html><body><section>Related Information
            <a href="/newsevents/pressreleases/monetary20260916a.htm">
            FOMC Meeting Statement: PDF | HTML</a>
            (Released September 16, 2026 at 2:00 p.m.)
            <a href="/newsevents/pressreleases/monetary20260916a1.htm">
            Implementation Note</a>
            (Released September 16, 2026 at 2:00 p.m.)
            </section><p>Minutes: Released October 07, 2026 at 2:00 p.m.</p>
            </body></html>"""

    return FedSourceBundle(
        statement=make_document("statement", STATEMENT_URL, statement),
        implementation=make_document(
            "implementation", IMPLEMENTATION_URL, implementation
        ),
        meeting=make_document("meeting", MEETING_URL, meeting),
    )


def with_html(bundle, role, new_html):
    old = getattr(bundle, role)
    return replace(bundle, **{
        role: make_document(
            role, old.url, new_html, fetched_at=old.fetched_at
        )
    })


def emergency_march3_bundle():
    """Synthetic March 3, 2020-style rate release fixture."""
    statement = """<html><body>
        March 3, 2020 Federal Reserve issues FOMC statement
        For release at 10:00 a.m. EST
        The Committee decided to lower the target range for the federal funds
        rate to 1 to 1-1/4 percent. One member preferred another range.
        </body></html>"""

    implementation = """<html><body>
        Implementation Note issued March 3, 2020
        Effective March 4, 2020, the Federal Open Market Committee directs
        the Desk to: Undertake open market operations as necessary to maintain
        the federal funds rate in a target range of 1 to 1-1/4 percent.
        </body></html>"""

    meeting = """<html><body>
        <a href="/newsevents/pressreleases/monetary20200303a.htm">
        FOMC Meeting Statement: PDF | HTML</a>
        (Released March 3, 2020 at 10:00 a.m.)
        <a href="/newsevents/pressreleases/monetary20200303a1.htm">
        Implementation Note</a>
        (Released March 3, 2020 at 10:00 a.m.)
        </body></html>"""

    return FedSourceBundle(
        statement=make_document(
            "statement",
            "https://www.federalreserve.gov/newsevents/pressreleases/"
            "monetary20200303a.htm",
            statement,
        ),
        implementation=make_document(
            "implementation",
            "https://www.federalreserve.gov/newsevents/pressreleases/"
            "monetary20200303a1.htm",
            implementation,
        ),
        meeting=make_document(
            "meeting",
            "https://www.federalreserve.gov/monetarypolicy/"
            "fomcpresconf20200303.htm",
            meeting,
        ),
    )


class FedRateExtractorTests(unittest.TestCase):

    def test_01_september_three_document_evidence(self):
        evidence = extract_fed_original_rate_evidence(september_bundle())
        self.assertEqual(evidence.statement_lower, "3-3/4")
        self.assertEqual(evidence.statement_upper, "4")
        self.assertEqual(evidence.implementation_lower, "3-3/4")
        self.assertEqual(evidence.implementation_upper, "4")
        self.assertEqual(evidence.effective_date, date(2026, 9, 17))

        statement_id, released, low, high = validate_fed_original_evidence(
            evidence
        )
        self.assertEqual(statement_id, "monetary20260916a")
        self.assertEqual(released, RELEASE_AT)
        self.assertEqual((float(low), float(high)), (3.75, 4.0))

    def test_02_normalized_snapshots_and_causal_boundary(self):
        evidence = extract_fed_original_rate_evidence(september_bundle())
        lower, upper = build_fed_original_rate_snapshots(
            evidence, FETCHED_AT
        )

        self.assertEqual(
            (lower.value_percent, upper.value_percent), (3.75, 4.0)
        )
        self.assertEqual(lower.reference_date, date(2026, 9, 17))
        self.assertEqual(
            (lower.available_at, upper.available_at),
            (RELEASE_AT, RELEASE_AT),
        )
        self.assertFalse(
            lower.available_at <= RELEASE_AT - timedelta(microseconds=1)
        )
        self.assertTrue(lower.available_at <= RELEASE_AT)
        self.assertEqual(lower.central_bank, "FED")
        self.assertNotEqual(lower.content_hash, upper.content_hash)

    def test_03_ignore_dissent_and_non_target_operational_rates(self):
        bundle = september_bundle()
        statement = bundle.statement.html.replace(
            "a different rate of 3 to 3-1/4 percent",
            "an alternative target range of 2 to 2-1/4 percent",
        )
        implementation = bundle.implementation.html.replace(
            "3.90 percent", "3.65 percent"
        )

        revised = with_html(bundle, "statement", statement)
        revised = with_html(revised, "implementation", implementation)
        evidence = extract_fed_original_rate_evidence(revised)

        self.assertEqual(evidence.statement_lower, "3-3/4")
        self.assertEqual(evidence.implementation_upper, "4")

    def test_04_ignore_script_style_and_template_spoofing(self):
        bundle = september_bundle()
        poison = """<script>The Committee decided to raise the target range
            for the federal funds rate to 15 to 16 percent.</script>
            <style>Effective September 17, 2026, the Federal Open Market
            Committee directs the Desk to: Undertake open market operations
            as necessary to maintain the federal funds rate in a target range
            of 20 to 21 percent.</style>
            <template>Fake: FOMC Meeting Statement: PDF | HTML
            (Released September 16, 2026 at 1:00 p.m.)</template>"""

        updated = with_html(
            bundle,
            "statement",
            bundle.statement.html.replace("<body>", "<body>" + poison),
        )
        evidence = extract_fed_original_rate_evidence(updated)
        self.assertEqual(evidence.statement_lower, "3-3/4")

    def test_05_raw_html_change_does_not_change_economic_hash(self):
        initial = september_bundle()
        altered = with_html(
            initial,
            "statement",
            initial.statement.html.replace(
                "Last Update: October 13, 2026",
                "Last Update: October 14, 2026",
            ),
        )
        self.assertNotEqual(
            initial.statement.content_sha256,
            altered.statement.content_sha256,
        )

        snapshots_a = build_fed_original_rate_snapshots(
            extract_fed_original_rate_evidence(initial), FETCHED_AT
        )
        snapshots_b = build_fed_original_rate_snapshots(
            extract_fed_original_rate_evidence(altered),
            FETCHED_AT + timedelta(hours=1),
        )
        self.assertEqual(
            tuple(s.content_hash for s in snapshots_a),
            tuple(s.content_hash for s in snapshots_b),
        )

    def test_06_reject_tampered_source_hash_and_decoded_text(self):
        bundle = september_bundle()

        altered_hash = replace(
            bundle.statement, content_sha256="0" * 64
        )
        with self.assertRaisesRegex(
            FedEvidenceExtractionError, "fingerprint"
        ):
            extract_fed_original_rate_evidence(
                replace(bundle, statement=altered_hash)
            )

        altered_text = replace(
            bundle.statement, html="<html>different</html>"
        )
        with self.assertRaisesRegex(
            FedEvidenceExtractionError, "differs"
        ):
            extract_fed_original_rate_evidence(
                replace(bundle, statement=altered_text)
            )

    def test_07_reject_invalid_utf8_and_naive_fetch_clock(self):
        bundle = september_bundle()
        corrupted_bytes = b"\xff"
        corrupted = replace(
            bundle.statement,
            raw_bytes=corrupted_bytes,
            content_sha256=hashlib.sha256(corrupted_bytes).hexdigest(),
        )
        with self.assertRaisesRegex(
            FedEvidenceExtractionError, "UTF-8"
        ):
            extract_fed_original_rate_evidence(
                replace(bundle, statement=corrupted)
            )

        naive = replace(
            bundle.statement,
            fetched_at=datetime(2026, 10, 13, 22, 0),
        )
        with self.assertRaisesRegex(
            FedEvidenceExtractionError, "aware"
        ):
            extract_fed_original_rate_evidence(
                replace(bundle, statement=naive)
            )

    def test_08_reject_missing_meeting_links(self):
        bundle = september_bundle()
        for token in (
            "monetary20260916a.htm",
            "monetary20260916a1.htm",
        ):
            with self.subTest(missing=token):
                new_meeting = bundle.meeting.html.replace(
                    token, "missing.htm"
                )
                changed = with_html(bundle, "meeting", new_meeting)
                with self.assertRaisesRegex(
                    FedEvidenceExtractionError, "link"
                ):
                    extract_fed_original_rate_evidence(changed)

    def test_09_reject_mismatched_source_identity(self):
        bundle = september_bundle()
        cases = (
            (
                "statement",
                STATEMENT_URL.replace("20260916", "20260917"),
            ),
            (
                "implementation",
                IMPLEMENTATION_URL.replace("a1.htm", "a2.htm"),
            ),
            (
                "meeting",
                MEETING_URL.replace("20260916", "20260917"),
            ),
        )

        for role, new_url in cases:
            with self.subTest(role=role):
                mutated_doc = replace(
                    getattr(bundle, role), url=new_url
                )
                with self.assertRaises(FedRateProtocolError):
                    extract_fed_original_rate_evidence(
                        replace(bundle, **{role: mutated_doc})
                    )

    def test_10_reject_contradictory_policy_rates(self):
        bundle = september_bundle()

        for new_rate in (
            "3-1/2 to 4 percent",
            "4-1/4 to 4 percent",
        ):
            with self.subTest(rate=new_rate):
                mutated = with_html(
                    bundle,
                    "implementation",
                    bundle.implementation.html.replace(
                        "3-3/4 to 4 percent",
                        new_rate,
                    ),
                )
                with self.assertRaises(FedRateProtocolError):
                    extract_fed_original_rate_evidence(mutated)

    def test_11_reject_meeting_time_and_date_conflicts(self):
        bundle = september_bundle()

        for replacement in (
            "Released September 16, 2026 at 2:01 p.m.",
            "Released September 15, 2026 at 2:00 p.m.",
        ):
            with self.subTest(replacement=replacement):
                mutated = with_html(
                    bundle,
                    "meeting",
                    bundle.meeting.html.replace(
                        "Released September 16, 2026 at 2:00 p.m.",
                        replacement,
                        1,
                    ),
                )
                with self.assertRaises(FedRateProtocolError):
                    extract_fed_original_rate_evidence(mutated)

    def test_12_reject_wrong_statement_header_timezone_or_date(self):
        bundle = september_bundle()

        for old, new in (
            ("2:00 p.m. EDT", "2:00 p.m. EST"),
            ("September 16, 2026", "September 15, 2026"),
        ):
            with self.subTest(new=new):
                mutated = with_html(
                    bundle,
                    "statement",
                    bundle.statement.html.replace(old, new, 1),
                )
                with self.assertRaises(FedRateProtocolError):
                    extract_fed_original_rate_evidence(mutated)

    def test_13_reject_missing_and_duplicate_adopted_decision(self):
        bundle = september_bundle()
        sentence = (
            "The Committee decided to raise the target range for the\n"
            "            federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent"
        )
        self.assertIn(sentence, bundle.statement.html)

        cases = (
            bundle.statement.html.replace(
                "The Committee decided to raise",
                "Staff predicted to raise",
            ),
            bundle.statement.html.replace(
                "</body>",
                f"<p>{sentence}.</p></body>",
            ),
        )

        for changed_html in cases:
            with self.subTest(
                count=changed_html.count("Committee decided")
            ):
                with self.assertRaisesRegex(
                    FedEvidenceExtractionError, "adopted"
                ):
                    extract_fed_original_rate_evidence(
                        with_html(
                            bundle, "statement", changed_html
                        )
                    )

    def test_14_reject_missing_or_duplicate_desk_directive(self):
        bundle = september_bundle()
        directive = (
            "Effective September 17, 2026, the Federal Open Market Committee\n"
            "            directs the Desk to: Undertake open market operations as necessary\n"
            "            to maintain the federal funds rate in a target range of\n"
            "            3-3/4 to 4 percent"
        )

        cases = (
            bundle.implementation.html.replace(
                "directs the Desk to:",
                "instructs the Desk to:",
            ),
            bundle.implementation.html.replace(
                "</body>",
                f"<p>{directive}.</p></body>",
            ),
        )

        for mutated in cases:
            with self.subTest(
                count=mutated.count("directs the Desk to:")
            ):
                with self.assertRaisesRegex(
                    FedEvidenceExtractionError, "directive"
                ):
                    extract_fed_original_rate_evidence(
                        with_html(
                            bundle, "implementation", mutated
                        )
                    )

    def test_15_emergency_release_and_contradictory_index_fail_closed(self):
        emergency = emergency_march3_bundle()
        evidence = extract_fed_original_rate_evidence(emergency)

        self.assertEqual(
            evidence.effective_date, date(2020, 3, 4)
        )
        _, released, low, high = validate_fed_original_evidence(
            evidence
        )
        self.assertEqual(
            released,
            datetime(2020, 3, 3, 15, 0, tzinfo=UTC),
        )
        self.assertEqual(
            (float(low), float(high)), (1.0, 1.25)
        )

        contradictory = with_html(
            emergency,
            "meeting",
            emergency.meeting.html.replace(
                "Implementation Note</a>\n        (Released March 3, 2020",
                "Implementation Note</a>\n        (Released March 2, 2020",
            ),
        )
        with self.assertRaises(FedRateProtocolError):
            extract_fed_original_rate_evidence(contradictory)

    def test_16_reject_partial_bundle_and_role_swaps(self):
        bundle = september_bundle()

        with self.assertRaises(FedEvidenceExtractionError):
            extract_fed_original_rate_evidence(None)

        with self.assertRaises(FedEvidenceExtractionError):
            extract_fed_original_rate_evidence(
                replace(bundle, implementation=bundle.statement)
            )


    def test_17_ignore_page_title_but_reject_duplicate_article_heading(self):
        bundle = september_bundle()

        heading = "Implementation Note issued September 16, 2026"

        # The page title repeats the real article heading.
        # HTML head metadata must not count as article evidence.
        html_with_title = bundle.implementation.html.replace(
            "<html>",
            (
                "<html><head><title>"
                "Federal Reserve Board - "
                + heading
                + "</title></head>"
            ),
            1,
        )

        updated = with_html(
            bundle,
            "implementation",
            html_with_title,
        )

        evidence = extract_fed_original_rate_evidence(updated)

        self.assertEqual(
            evidence.effective_date,
            date(2026, 9, 17),
        )

        # A second heading INSIDE the actual article must
        # still fail closed. We must not silently choose one.
        duplicated_article = html_with_title.replace(
            "</body>",
            f"<p>{heading}</p></body>",
            1,
        )

        conflicting = with_html(
            bundle,
            "implementation",
            duplicated_article,
        )

        with self.assertRaisesRegex(
            FedEvidenceExtractionError,
            "implementation note heading",
        ):
            extract_fed_original_rate_evidence(conflicting)

if __name__ == "__main__":
    unittest.main(verbosity=2)

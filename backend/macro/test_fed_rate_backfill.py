
"""F3.2 deterministic, read-only Federal Reserve backfill planner tests.

Run from the project root with oracle_env activated:
    python -m backend.macro.test_fed_rate_backfill

Uses existing synthetic F2 extractor fixtures. No HTTP requests,
PostgreSQL, broker actions, or reserved HMM evaluation.
"""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import unittest
from unittest.mock import patch

from backend.macro.fed_rate_backfill import (
    FedBackfillPreflightError,
    FedRateBackfillPlanner,
    FedRateBackfillPlan,
)
from backend.macro.fed_rate_extractor import (
    extract_fed_original_rate_evidence,
)
from backend.macro.fed_rate_protocol import FedRateProtocolError
from backend.macro.test_fed_rate_extractor import (
    FETCHED_AT,
    RELEASE_AT,
    STATEMENT_URL,
    emergency_march3_bundle,
    september_bundle,
    with_html,
)

UTC = timezone.utc


class FakeClient:
    """An injected document client with no network implementation."""

    def __init__(self, bundle=None, error=None):
        self.bundle = september_bundle() if bundle is None else bundle
        self.error = error
        self.calls = []

    def fetch_original_documents(self, statement_url):
        self.calls.append(statement_url)
        if self.error is not None:
            raise self.error
        return self.bundle


class FedRateBackfillPlannerTests(unittest.TestCase):

    def planner(self, bundle=None, extractor=None, error=None):
        client = FakeClient(bundle=bundle, error=error)
        options = {"client": client}
        if extractor is not None:
            options["extractor"] = extractor
        return FedRateBackfillPlanner(**options), client

    def test_01_complete_pair_and_decision_identity(self):
        planner, client = self.planner()
        plan = planner.prepare(STATEMENT_URL)

        self.assertIsInstance(plan, FedRateBackfillPlan)
        self.assertEqual(client.calls, [STATEMENT_URL])
        self.assertEqual(plan.statement_id, "monetary20260916a")
        self.assertEqual(plan.announced_at, RELEASE_AT)
        self.assertEqual(plan.effective_date, date(2026, 9, 17))
        self.assertEqual(plan.ingested_at, FETCHED_AT)
        self.assertEqual(len(plan.candidate_snapshots), 2)

        lower, upper = plan.candidate_snapshots
        self.assertEqual((lower.value_percent, upper.value_percent), (3.75, 4.0))
        self.assertEqual(
            (lower.rate_name, upper.rate_name),
            ("federal_funds_target_lower", "federal_funds_target_upper"),
        )
        self.assertEqual(lower.source, "FED_FOMC_RELEASE")
        self.assertEqual(upper.central_bank, "FED")
        self.assertEqual(
            (lower.available_at, upper.available_at),
            (RELEASE_AT,) * 2,
        )
        self.assertEqual(
            (lower.source_record_id, upper.source_record_id),
            (
                "FOMC:monetary20260916a:lower",
                "FOMC:monetary20260916a:upper",
            ),
        )

    def test_02_historical_persistence_explicitly_prohibited(self):
        plan = self.planner()[0].prepare(STATEMENT_URL)
        self.assertEqual(
            plan.provenance_status,
            "CURRENT_HTML_NOT_HISTORICALLY_ATTESTED",
        )
        self.assertFalse(plan.persistence_permitted)
        with self.assertRaises(FrozenInstanceError):
            plan.ingested_at = RELEASE_AT

    def test_03_three_exact_verified_source_fingerprints(self):
        bundle = september_bundle()
        plan = self.planner(bundle)[0].prepare(STATEMENT_URL)
        fingerprints = plan.source_fingerprints
        documents = (
            bundle.statement,
            bundle.implementation,
            bundle.meeting,
        )

        self.assertEqual(
            tuple(fp.role for fp in fingerprints),
            ("statement", "implementation", "meeting"),
        )
        for fp, document in zip(fingerprints, documents, strict=True):
            self.assertEqual(fp.url, document.url)
            self.assertEqual(
                fp.sha256,
                hashlib.sha256(document.raw_bytes).hexdigest(),
            )
            self.assertEqual(fp.fetched_at, FETCHED_AT)

    def test_04_reject_invalid_url_before_fetching(self):
        planner, client = self.planner()
        with self.assertRaises(FedRateProtocolError):
            planner.prepare(
                "https://untrusted.example/monetary20260916a.htm"
            )
        self.assertEqual(client.calls, [])

    def test_05_normalize_aware_fetch_and_ingestion_clocks_to_utc(self):
        bundle = september_bundle()
        plus_three = timezone(timedelta(hours=3))
        local_fetch = FETCHED_AT.astimezone(plus_three)

        localized = replace(
            bundle,
            statement=replace(
                bundle.statement, fetched_at=local_fetch
            ),
            implementation=replace(
                bundle.implementation, fetched_at=local_fetch
            ),
            meeting=replace(
                bundle.meeting, fetched_at=local_fetch
            ),
        )
        local_ingestion = (
            FETCHED_AT + timedelta(minutes=5)
        ).astimezone(plus_three)

        plan = self.planner(localized)[0].prepare(
            STATEMENT_URL,
            ingested_at=local_ingestion,
        )

        self.assertEqual(
            plan.ingested_at,
            FETCHED_AT + timedelta(minutes=5),
        )
        self.assertIs(plan.ingested_at.tzinfo, UTC)
        self.assertTrue(
            all(
                fp.fetched_at == FETCHED_AT
                for fp in plan.source_fingerprints
            )
        )
        self.assertTrue(
            all(
                s.ingested_at == plan.ingested_at
                for s in plan.candidate_snapshots
            )
        )

    def test_06_reingestion_does_not_change_economic_identity(self):
        planner, _ = self.planner()
        first = planner.prepare(STATEMENT_URL)
        later = planner.prepare(
            STATEMENT_URL,
            ingested_at=FETCHED_AT + timedelta(hours=1),
        )

        self.assertNotEqual(first.ingested_at, later.ingested_at)
        self.assertEqual(
            tuple(s.content_hash for s in first.candidate_snapshots),
            tuple(s.content_hash for s in later.candidate_snapshots),
        )
        self.assertEqual(
            tuple(s.source_record_id for s in first.candidate_snapshots),
            tuple(s.source_record_id for s in later.candidate_snapshots),
        )

    def test_07_html_fingerprint_change_does_not_invent_rate_revision(self):
        original = september_bundle()
        modified = with_html(
            original,
            "statement",
            original.statement.html.replace(
                "Last Update: October 13, 2026",
                "Last Update: October 14, 2026",
            ),
        )

        first = self.planner(original)[0].prepare(STATEMENT_URL)
        second = self.planner(modified)[0].prepare(STATEMENT_URL)

        self.assertNotEqual(
            first.source_fingerprints[0].sha256,
            second.source_fingerprints[0].sha256,
        )
        self.assertEqual(
            tuple(s.content_hash for s in first.candidate_snapshots),
            tuple(s.content_hash for s in second.candidate_snapshots),
        )
        self.assertFalse(second.persistence_permitted)

    def test_08_reject_tampered_source_fingerprint(self):
        bundle = september_bundle()
        changed = replace(
            bundle,
            implementation=replace(
                bundle.implementation,
                content_sha256="0" * 64,
            ),
        )

        with self.assertRaisesRegex(
            FedBackfillPreflightError, "fingerprint"
        ):
            self.planner(changed)[0].prepare(STATEMENT_URL)

    def test_09_reject_incomplete_or_wrong_role_bundle(self):
        bundle = september_bundle()

        for changed in (
            replace(bundle, implementation=None),
            replace(bundle, meeting=bundle.statement),
        ):
            with self.subTest(bundle=changed):
                with self.assertRaises(FedBackfillPreflightError):
                    self.planner(changed)[0].prepare(STATEMENT_URL)

    def test_10_reject_changed_statement_url(self):
        bundle = september_bundle()
        wrong = replace(
            bundle.statement,
            url=bundle.statement.url.replace("16a", "17a"),
        )

        with self.assertRaisesRegex(
            FedBackfillPreflightError, "URL changed"
        ):
            self.planner(
                replace(bundle, statement=wrong)
            )[0].prepare(STATEMENT_URL)

    def test_11_reject_nonmonotonic_fetch_timestamps(self):
        bundle = september_bundle()
        early_meeting = replace(
            bundle.meeting,
            fetched_at=FETCHED_AT - timedelta(seconds=1),
        )

        with self.assertRaisesRegex(
            FedBackfillPreflightError, "out of order"
        ):
            self.planner(
                replace(bundle, meeting=early_meeting)
            )[0].prepare(STATEMENT_URL)

    def test_12_reject_documents_fetched_before_release(self):
        bundle = september_bundle()
        before = RELEASE_AT - timedelta(days=1)

        changed = replace(
            bundle,
            statement=replace(
                bundle.statement, fetched_at=before
            ),
            implementation=replace(
                bundle.implementation, fetched_at=before
            ),
            meeting=replace(
                bundle.meeting, fetched_at=before
            ),
        )

        with self.assertRaisesRegex(
            FedBackfillPreflightError, "before public release"
        ):
            self.planner(changed)[0].prepare(STATEMENT_URL)

    def test_13_reject_naive_fetch_clock(self):
        bundle = september_bundle()
        naive = replace(
            bundle.statement,
            fetched_at=datetime(2026, 10, 13, 22, 17),
        )

        with self.assertRaisesRegex(
            FedBackfillPreflightError, "aware"
        ):
            self.planner(
                replace(bundle, statement=naive)
            )[0].prepare(STATEMENT_URL)

    def test_14_reject_naive_or_early_ingestion_clock(self):
        planner, _ = self.planner()

        with self.assertRaisesRegex(
            FedBackfillPreflightError, "aware"
        ):
            planner.prepare(
                STATEMENT_URL,
                ingested_at=datetime(2026, 10, 13, 22, 17),
            )

        with self.assertRaisesRegex(
            FedBackfillPreflightError, "predates"
        ):
            planner.prepare(
                STATEMENT_URL,
                ingested_at=FETCHED_AT - timedelta(microseconds=1),
            )

    def test_15_reject_non_bundle_and_non_evidence_outputs(self):
        planner, _ = self.planner(extractor=lambda _: None)

        with self.assertRaisesRegex(
            FedBackfillPreflightError, "invalid evidence"
        ):
            planner.prepare(STATEMENT_URL)

        client = FakeClient()
        client.bundle = object()

        with self.assertRaisesRegex(
            FedBackfillPreflightError, "complete bundle"
        ):
            FedRateBackfillPlanner(
                client=client
            ).prepare(STATEMENT_URL)

    def test_16_reject_cross_decision_extractor_output(self):
        foreign = extract_fed_original_rate_evidence(
            emergency_march3_bundle()
        )

        planner, _ = self.planner(extractor=lambda _: foreign)

        with self.assertRaisesRegex(
            FedBackfillPreflightError, "identity changed"
        ):
            planner.prepare(STATEMENT_URL)

    def test_17_reject_policy_rate_disagreement(self):
        bundle = september_bundle()

        changed = with_html(
            bundle,
            "implementation",
            bundle.implementation.html.replace(
                "3-3/4 to 4 percent",
                "3-1/2 to 4 percent",
            ),
        )

        with self.assertRaises(FedRateProtocolError):
            self.planner(changed)[0].prepare(STATEMENT_URL)

    def test_18_pair_integrity_rejects_missing_swapped_and_forged_rows(self):
        baseline = self.planner()[0].prepare(STATEMENT_URL)
        lower, upper = baseline.candidate_snapshots

        variants = (
            (lower,),
            (upper, lower),
            (
                lower,
                upper.model_copy(
                    update={"content_hash": lower.content_hash}
                ),
            ),
            (
                lower,
                upper.model_copy(
                    update={"source_record_id": "forged"}
                ),
            ),
            (
                lower,
                upper.model_copy(
                    update={"value_percent": 3.0}
                ),
            ),
        )

        for pair in variants:
            with self.subTest(pair=pair):
                with patch(
                    "backend.macro.fed_rate_backfill."
                    "build_fed_original_rate_snapshots",
                    return_value=pair,
                ):
                    with self.assertRaises(
                        FedBackfillPreflightError
                    ):
                        self.planner()[0].prepare(STATEMENT_URL)

    def test_19_constructor_rejects_invalid_collaborators(self):
        with self.assertRaises(TypeError):
            FedRateBackfillPlanner(client=object())

        with self.assertRaises(TypeError):
            FedRateBackfillPlanner(
                client=FakeClient(),
                extractor="not callable",
            )

    def test_20_source_failure_does_not_produce_a_partial_plan(self):
        planner, client = self.planner(
            error=OSError("simulated source failure")
        )

        with self.assertRaisesRegex(
            OSError, "simulated source failure"
        ):
            planner.prepare(STATEMENT_URL)

        self.assertEqual(client.calls, [STATEMENT_URL])


if __name__ == "__main__":
    unittest.main(verbosity=2)

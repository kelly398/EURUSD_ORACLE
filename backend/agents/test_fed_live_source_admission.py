"""F5.2 source-observed, forward-only Fed live preview (all synthetic data).

No live HTTP, PostgreSQL, Council registration or broker calls.
A mocked source bundle proves the integration logic, NOT real provenance.
"""

from dataclasses import replace
from datetime import timedelta
import unittest
from unittest.mock import patch

from backend.agents.fed_live_source_admission import (
    FedLiveSourceAdmission,
    FedLiveSourceAdmissionError,
    MAX_PENDING_CAPTURES,
)
from backend.agents.test_fed_agent import make_context
from backend.agents.implementations.fed_agent import FedAgent
from backend.agents.contracts import AgentDirection
from backend.macro.fed_rate_client import FedRateClient
from backend.macro.test_fed_rate_extractor import (
    FETCHED_AT,
    STATEMENT_URL,
    september_bundle,
)

START = FETCHED_AT - timedelta(seconds=2)
END = FETCHED_AT + timedelta(seconds=2)
AS_OF = END + timedelta(seconds=1)
NOW = END + timedelta(seconds=10)


class FedLiveSourceAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.source = FedLiveSourceAdmission()

    def capture(self, bundle=None, *, start=START, end=END):
        with patch(
            "backend.agents.fed_live_source_admission._now",
            side_effect=(start, end),
        ), patch.object(
            FedRateClient, "fetch_original_documents",
            return_value=september_bundle() if bundle is None else bundle,
        ) as fetch:
            receipt = self.source.capture(STATEMENT_URL)
        fetch.assert_called_once_with(STATEMENT_URL)
        return receipt

    def preview(self, receipt, *, as_of=AS_OF, now=NOW, base=None):
        with patch("backend.agents.fed_live_source_admission._now", return_value=now):
            return self.source.materialize_preview(
                base if base is not None else make_context(as_of=as_of), receipt,
            )

    def test_01_real_client_type_uses_mocked_original_bundle(self):
        receipt = self.capture()
        self.assertEqual(receipt.statement_id, "monetary20260916a")
        self.assertEqual(receipt.observed_at, END)
        self.assertEqual(len(receipt.document_sha256), 3)
        self.assertEqual(len(receipt.snapshots_sha256), 64)
        self.assertEqual(len(receipt.capture_id), 48)

    def test_02_observed_after_capture_live_preview_populates_fed_data(self):
        receipt = self.capture()
        preview = self.preview(receipt)
        self.assertEqual(len(preview.context.fed_data), 2)
        self.assertEqual(preview.context.as_of, AS_OF)
        self.assertEqual(preview.context.ecb_data, ())
        self.assertEqual(preview.context.economic_calendar, ())
        self.assertFalse(preview.council_context_admission_authorized)
        self.assertFalse(preview.council_registration_authorized)
        self.assertFalse(preview.historical_replay_authorized)
        self.assertFalse(preview.fed_persistence_authorized)
        self.assertFalse(preview.readiness.council_registration_authorized)
        self.assertFalse(receipt.historical_replay_authorized)
        self.assertFalse(receipt.council_registration_authorized)
        self.assertFalse(receipt.fed_persistence_authorized)

    def test_03_only_genuinely_paired_data_can_reach_preview(self):
        receipt = self.capture()
        preview = self.preview(receipt)
        self.assertEqual(
            tuple(x.rate_name for x in preview.context.fed_data),
            ("federal_funds_target_lower", "federal_funds_target_upper"),
        )

    def test_04_cannot_reuse_consumed_capture(self):
        receipt = self.capture()
        self.preview(receipt)
        with self.assertRaisesRegex(FedLiveSourceAdmissionError, "consumed"):
            self.preview(receipt)

    def test_05_unknown_or_fabricated_capture_fails_closed(self):
        real = self.capture()
        fake = replace(real, capture_id="0" * 48)
        with self.assertRaisesRegex(FedLiveSourceAdmissionError, "Unknown"):
            self.preview(fake)
        self.assertEqual(len(self.preview(real).context.fed_data), 2)

    def test_06_caller_must_supply_new_market_state_after_fetch(self):
        receipt = self.capture()
        with self.assertRaisesRegex(FedLiveSourceAdmissionError, "post-capture"):
            self.preview(receipt, as_of=START)

    def test_07_future_market_clock_not_accepted(self):
        receipt = self.capture()
        with self.assertRaisesRegex(FedLiveSourceAdmissionError, "post-capture"):
            self.preview(receipt, as_of=NOW + timedelta(seconds=1))

    def test_08_expired_capture_does_not_authorize_preview(self):
        receipt = self.capture()
        with self.assertRaisesRegex(FedLiveSourceAdmissionError, "post-capture"):
            self.preview(receipt, now=END + timedelta(minutes=5))

    def test_09_modified_receipt_is_rejected_even_when_capture_exists(self):
        receipt = self.capture()
        fake = replace(receipt, snapshots_sha256="f" * 64)
        with self.assertRaisesRegex(FedLiveSourceAdmissionError, "altered"):
            self.preview(fake)
        with self.assertRaisesRegex(FedLiveSourceAdmissionError, "consumed"):
            self.preview(receipt)

    def test_10_certification_object_cannot_substitute_for_capture(self):
        from backend.macro.test_fed_certification import certification
        from backend.macro.fed_certification import (
            FedCertificationLevel, FedAvailabilityLevel,
        )
        self.capture()
        claimed = certification(
            FedCertificationLevel.PERSISTENCE_APPROVED,
            availability=FedAvailabilityLevel.INTRADAY_CERTIFIED,
        )
        self.assertTrue(claimed.persistence_authorized)
        with self.assertRaisesRegex(FedLiveSourceAdmissionError, "receipt"):
            self.preview(claimed)

    def test_11_untrusted_historical_fixture_cannot_masquerade_as_fresh_capture(self):
        with self.assertRaisesRegex(FedLiveSourceAdmissionError, "not observed"):
            self.capture(start=FETCHED_AT + timedelta(days=1),
                         end=FETCHED_AT + timedelta(days=1, seconds=2))

    def test_12_mismatched_documents_rejected_by_existing_f2_parser(self):
        bundle = september_bundle()
        corrupt = replace(bundle, statement=replace(bundle.statement, content_sha256="0" * 64))
        with self.assertRaises(ValueError):
            self.capture(bundle=corrupt)
        self.assertEqual(self.source._pending, {})

    def test_13_failed_acquisition_creates_no_capture_ticket(self):
        with patch("backend.agents.fed_live_source_admission._now", return_value=START), patch.object(
            FedRateClient, "fetch_original_documents", side_effect=OSError("offline")
        ):
            with self.assertRaises(OSError):
                self.source.capture(STATEMENT_URL)
        self.assertEqual(self.source._pending, {})

    def test_14_invalid_clock_order_fails_closed(self):
        with self.assertRaisesRegex(FedLiveSourceAdmissionError, "clock/window"):
            self.capture(start=END + timedelta(seconds=1), end=START)

    def test_15_timeout_window_fails_closed(self):
        with self.assertRaisesRegex(FedLiveSourceAdmissionError, "clock/window"):
            self.capture(start=START, end=START + timedelta(minutes=3))

    def test_16_pending_budget_checked_before_fetch(self):
        for n in range(MAX_PENDING_CAPTURES):
            self.source._pending[str(n)] = ((), ())
        with patch.object(FedRateClient, "fetch_original_documents") as fetch:
            with self.assertRaisesRegex(FedLiveSourceAdmissionError, "pending"):
                self.source.capture(STATEMENT_URL)
            fetch.assert_not_called()

    def test_17_invalid_base_context_is_denied(self):
        receipt = self.capture()
        base = make_context(as_of=AS_OF).model_copy(update={"symbol": "GBPUSD"})
        with self.assertRaisesRegex(FedLiveSourceAdmissionError, "base context"):
            self.preview(receipt, base=base)

    def test_18_existing_fed_data_cannot_be_overwritten(self):
        receipt = self.capture()
        bad_base = make_context(as_of=AS_OF, fed_data=self.source._pending[receipt.capture_id][1])
        with self.assertRaisesRegex(FedLiveSourceAdmissionError, "must be empty"):
            self.preview(receipt, base=bad_base)

    def test_19_existing_fed_agent_stays_contextual_and_zero_score(self):
        receipt = self.capture()
        opinion = FedAgent().run(self.preview(receipt).context)
        self.assertEqual(opinion.direction, AgentDirection.NEUTRAL)
        self.assertEqual(opinion.score, 0.0)
        self.assertEqual(opinion.confidence, 0.0)

    def test_20_preview_preserves_all_authorization_denials(self):
        with patch(
            "backend.agents.fed_live_source_admission._now", side_effect=(START, END, NOW)
        ), patch.object(FedRateClient, "fetch_original_documents", return_value=september_bundle()):
            receipt = self.source.capture(STATEMENT_URL)
            preview = self.source.materialize_preview(make_context(as_of=AS_OF), receipt)
        self.assertFalse(preview.fed_persistence_authorized)
        self.assertFalse(preview.historical_replay_authorized)
        self.assertFalse(preview.council_registration_authorized)

    def test_21_bad_official_url_refused_before_source_fetch(self):
        with patch("backend.agents.fed_live_source_admission._now", return_value=START), patch.object(
            FedRateClient, "fetch_original_documents"
        ) as fetch:
            with self.assertRaises(ValueError):
                self.source.capture("https://example.com/wrong.htm")
            fetch.assert_not_called()

    def test_22_injected_arbitrary_source_client_class_is_refused(self):
        with patch("backend.agents.fed_live_source_admission._now", return_value=START), patch(
            "backend.agents.fed_live_source_admission.FedRateClient", return_value=object()
        ):
            with self.assertRaises(FedLiveSourceAdmissionError):
                self.source.capture(STATEMENT_URL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
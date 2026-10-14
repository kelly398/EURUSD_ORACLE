
"""F4.5.3: Fully offline quarantined Fed operational input tests.

Run:
    python -m backend.agents.test_fed_context_adapter

No database, HTTP, Council registration, broker activity, or HMM.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from itertools import repeat
import unittest

from backend.agents.contracts import AgentContext
from backend.agents.fed_context_adapter import (
    MAX_FED_CANDIDATE_SNAPSHOTS,
    FedContextAdapterError,
    FedContextQuarantineReceipt,
    quarantine_fed_context_candidates,
)
from backend.agents.fed_input_readiness import (
    FedInputNotReady,
    FedRateInputStatus,
)
from backend.agents.test_fed_agent import (
    RELEASE,
    fed_pair,
    make_context,
)
from backend.agents.test_fed_input_readiness import (
    calendar_event
)


class FedContextAdapterTests(unittest.TestCase):

    def setUp(self):
        self.base = make_context()
        self.pair = fed_pair()

    def test_01_empty_input_stays_absent_and_blocked(self):
        result = quarantine_fed_context_candidates(
            self.base, ()
        )

        self.assertIsInstance(
            result, FedContextQuarantineReceipt
        )
        self.assertEqual(result.candidate_snapshot_count, 0)
        self.assertEqual(
            result.readiness.rate_input_status,
            FedRateInputStatus.ABSENT,
        )
        self.assertFalse(
            result.council_context_admission_authorized
        )

    def test_02_complete_pair_receipt_is_only_diagnostic(self):
        result = quarantine_fed_context_candidates(
            self.base, self.pair
        )

        self.assertEqual(result.candidate_snapshot_count, 2)
        self.assertEqual(
            result.readiness.validated_decision_count, 1
        )
        self.assertEqual(
            result.readiness.rate_input_status,
            FedRateInputStatus.INTERNALLY_CONSISTENT,
        )
        self.assertFalse(result.council_registration_authorized)
        self.assertFalse(result.historical_replay_authorized)
        self.assertFalse(result.fed_persistence_authorized)

    def test_03_base_context_is_not_modified(self):
        before = self.base.model_dump()

        quarantine_fed_context_candidates(
            self.base, self.pair
        )

        self.assertEqual(self.base.model_dump(), before)
        self.assertEqual(self.base.fed_data, ())

    def test_04_diagnostic_has_no_live_context_or_snapshots(self):
        receipt = quarantine_fed_context_candidates(
            self.base, self.pair
        )

        self.assertFalse(hasattr(receipt, "context"))
        self.assertFalse(hasattr(receipt, "fed_data"))
        self.assertFalse(hasattr(receipt, "snapshots"))
        self.assertRegex(
            receipt.diagnostic_manifest_sha256,
            r"^[0-9a-f]{64}$",
        )

    def test_05_repeated_inputs_have_same_receipt(self):
        a = quarantine_fed_context_candidates(
            self.base, self.pair
        )
        b = quarantine_fed_context_candidates(
            self.base, iter(self.pair)
        )

        self.assertEqual(
            a.diagnostic_manifest_sha256,
            b.diagnostic_manifest_sha256,
        )

    def test_06_different_ingestion_time_changes_receipt(self):
        alternative = fed_pair(
            ingested=RELEASE + timedelta(minutes=2)
        )

        a = quarantine_fed_context_candidates(
            self.base, self.pair
        )
        b = quarantine_fed_context_candidates(
            self.base, alternative
        )

        self.assertNotEqual(
            a.diagnostic_manifest_sha256,
            b.diagnostic_manifest_sha256,
        )

    def test_07_f1_hash_tampering_rejected(self):
        tampered = self.pair[0].model_copy(
            update={"content_hash": "f" * 64}
        )

        with self.assertRaisesRegex(
            FedContextAdapterError, "replay-lock"
        ):
            quarantine_fed_context_candidates(
                self.base,
                (tampered, self.pair[1]),
            )

    def test_08_incomplete_decision_rejected(self):
        with self.assertRaisesRegex(
            FedContextAdapterError, "replay-lock"
        ):
            quarantine_fed_context_candidates(
                self.base,
                self.pair[:1],
            )

    def test_09_future_ingestion_is_not_silently_omitted(self):
        future = fed_pair(
            ingested=self.base.as_of + timedelta(seconds=1)
        )

        with self.assertRaisesRegex(
            FedContextAdapterError, "canonical"
        ):
            quarantine_fed_context_candidates(
                self.base, future
            )

    def test_10_future_publication_is_not_silently_omitted(self):
        future = fed_pair(
            release=self.base.as_of + timedelta(seconds=1),
            ingested=self.base.as_of + timedelta(seconds=2),
        )

        with self.assertRaisesRegex(
            FedContextAdapterError, "canonical"
        ):
            quarantine_fed_context_candidates(
                self.base, future
            )

    def test_11_wrong_order_not_silently_sorted(self):
        with self.assertRaisesRegex(
            FedContextAdapterError, "canonical"
        ):
            quarantine_fed_context_candidates(
                self.base, self.pair[::-1]
            )

    def test_12_duplicate_observations_not_silently_deduplicated(self):
        with self.assertRaisesRegex(
            FedContextAdapterError, "canonical"
        ):
            quarantine_fed_context_candidates(
                self.base,
                self.pair + self.pair,
            )

    def test_13_two_complete_decisions_accepted_for_diagnostics(self):
        older = fed_pair(
            statement="monetary20260729a",
            release=datetime(
                2026, 7, 29, 18, tzinfo=timezone.utc
            ),
            ingested=datetime(
                2026, 7, 29, 18, 1, tzinfo=timezone.utc
            ),
            effective=date(2026, 7, 30),
        )

        receipt = quarantine_fed_context_candidates(
            self.base,
            older + self.pair,
        )

        self.assertEqual(
            receipt.readiness.validated_decision_count,
            2,
        )
        self.assertFalse(
            receipt.council_context_admission_authorized
        )

    def test_14_nonempty_base_context_is_rejected(self):
        preloaded = make_context(fed_data=self.pair)

        with self.assertRaisesRegex(
            FedContextAdapterError,
            "base context must be empty",
        ):
            quarantine_fed_context_candidates(
                preloaded, ()
            )

    def test_15_forged_invalid_base_is_rejected(self):
        forged = self.base.model_copy(
            update={"symbol": "GBPUSD"}
        )

        with self.assertRaisesRegex(
            FedContextAdapterError,
            "base context failed validation",
        ):
            quarantine_fed_context_candidates(
                forged, self.pair
            )

    def test_16_snapshot_limit_is_enforced(self):
        with self.assertRaisesRegex(
            FedContextAdapterError, "limit"
        ):
            quarantine_fed_context_candidates(
                self.base,
                repeat(
                    self.pair[0],
                    MAX_FED_CANDIDATE_SNAPSHOTS + 1,
                ),
            )

    def test_17_type_errors_do_not_emit_receipts(self):
        with self.assertRaises(TypeError):
            quarantine_fed_context_candidates(
                object(), ()
            )

        with self.assertRaises(TypeError):
            quarantine_fed_context_candidates(
                self.base, None
            )

        with self.assertRaisesRegex(
            FedContextAdapterError,
            "invalid snapshot type",
        ):
            quarantine_fed_context_candidates(
                self.base, ("bad",)
            )

    def test_18_delivery_block_cannot_be_overridden(self):
        receipt = quarantine_fed_context_candidates(
            self.base, self.pair
        )

        altered = replace(
            receipt,
            readiness=replace(
                receipt.readiness,
                blockers=(),
            ),
        )

        self.assertFalse(
            altered.council_context_admission_authorized
        )

        with self.assertRaisesRegex(
            FedInputNotReady,
            "delivery remains blocked",
        ):
            altered.require_council_context_delivery()

    def test_19_usd_calendar_remains_unverified_candidate(self):
        candidate = calendar_event()

        baseline = AgentContext.model_validate({
            **self.base.model_dump(),
            "economic_calendar": (candidate,),
        })

        receipt = quarantine_fed_context_candidates(
            baseline, self.pair
        )

        self.assertEqual(
            receipt.readiness.usd_calendar_candidate_count,
            1,
        )
        self.assertIn(
            "FED_CALENDAR_SOURCE_NOT_VERIFIED",
            receipt.readiness.blockers,
        )
        self.assertFalse(
            receipt.council_context_admission_authorized
        )

    def test_20_unaware_clock_rejected(self):
        forged = self.base.model_copy(
            update={
                "as_of":
                    self.base.as_of.replace(tzinfo=None)
            }
        )

        with self.assertRaisesRegex(
            FedContextAdapterError,
            "aware as_of",
        ):
            quarantine_fed_context_candidates(
                forged, self.pair
            )

    def test_21_model_copy_bypass_fails_revalidation(self):
        forged = self.pair[0].model_copy(
            update={
                "available_at": "2026-09-16T18:00:00Z"
            }
        )

        with self.assertRaisesRegex(
            FedContextAdapterError,
            "canonical types",
        ):
            quarantine_fed_context_candidates(
                self.base,
                (forged, self.pair[1]),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

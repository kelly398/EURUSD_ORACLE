"""F5.1: isolated Fed SQL candidate ingress + live factory diagnostics.

Synthetic data, no actual DB/network, no Council activation or Fed persistence.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from backend.agents.context_factory import AgentContextFactory
from backend.agents.fed_input_readiness import FedRateInputStatus
from backend.agents.test_fed_agent import RELEASE, fed_pair, make_context
from backend.macro.fed_rate_candidate_repository import (
    FedCandidateRepositoryError,
    FedRateCandidateRepository,
    MAX_FED_SCAN_ROWS,
)
from backend.macro.fed_rate_protocol import FED_RATE_SOURCE


class FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False
        self.statement = None
        self.calls = 0

    def execute(self, statement):
        self.calls += 1
        self.statement = statement
        return self

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def close(self):
        self.closed = True


def db_row(item, id=1):
    return SimpleNamespace(id=id, **item.model_dump())


class FedRateCandidateRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repo = FedRateCandidateRepository()
        self.as_of = RELEASE + timedelta(minutes=10)
        self.pair = fed_pair()

    def run_db(self, rows, *, as_of=None, limit=256):
        session = FakeSession(rows)
        with patch(
            "backend.macro.fed_rate_candidate_repository.SessionLocal",
            return_value=session,
        ) as factory:
            result = self.repo.get_history_as_of(
                as_of=as_of or self.as_of, limit=limit
            )
        factory.assert_called_once()
        self.assertEqual(session.calls, 1)
        self.assertTrue(session.closed)
        return result, session

    def test_01_no_fed_rows_return_no_candidates(self):
        result, session = self.run_db(())
        self.assertEqual(result, ())
        compiled = str(
            session.statement.compile(compile_kwargs={"literal_binds": True})
        )
        self.assertIn("central_bank_rates.ingested_at <=", compiled)
        self.assertIn("central_bank_rates.available_at <=", compiled)
        self.assertIn(FED_RATE_SOURCE, compiled)
        self.assertIn("'FED'", compiled)
        self.assertIn(f"LIMIT {MAX_FED_SCAN_ROWS + 1}", compiled)

    def test_02_complete_pair_passes_as_diagnostic_candidates(self):
        result, _ = self.run_db(
            tuple(db_row(x, n) for n, x in enumerate(self.pair))
        )
        self.assertEqual(result, self.pair)

    def test_03_future_ingestion_cannot_leak_even_if_query_misbehaves(self):
        bad = fed_pair(ingested=self.as_of + timedelta(seconds=1))
        session = FakeSession(tuple(db_row(x) for x in bad))
        with patch(
            "backend.macro.fed_rate_candidate_repository.SessionLocal",
            return_value=session,
        ):
            with self.assertRaisesRegex(FedCandidateRepositoryError, "future"):
                self.repo.get_history_as_of(as_of=self.as_of)
        self.assertTrue(session.closed)

    def test_04_future_publication_cannot_leak(self):
        bad = fed_pair(
            release=self.as_of + timedelta(seconds=1),
            ingested=self.as_of + timedelta(seconds=2),
        )
        session = FakeSession(tuple(db_row(x) for x in bad))
        with patch(
            "backend.macro.fed_rate_candidate_repository.SessionLocal",
            return_value=session,
        ):
            with self.assertRaises(FedCandidateRepositoryError):
                self.repo.get_history_as_of(as_of=self.as_of)
        self.assertTrue(session.closed)

    def test_05_fake_source_identity_rejected(self):
        fake = self.pair[0].model_copy(update={"central_bank": "ECB"})
        with self.assertRaises(FedCandidateRepositoryError):
            self.run_db((db_row(fake), db_row(self.pair[1])))

    def test_06_bad_f1_hash_is_rejected(self):
        fake = self.pair[0].model_copy(
            update={"content_hash": "f" * 64}
        )
        with self.assertRaises(FedCandidateRepositoryError):
            self.run_db((db_row(fake), db_row(self.pair[1])))

    def test_07_partial_decision_is_rejected(self):
        with self.assertRaises(FedCandidateRepositoryError):
            self.run_db((db_row(self.pair[0]),))

    def test_08_eligible_revision_selected_atomically(self):
        newer = fed_pair(
            release=RELEASE + timedelta(minutes=2),
            ingested=RELEASE + timedelta(minutes=3),
            lower=3.50,
            upper=3.75,
        )
        rows = tuple(
            db_row(x, n) for n, x in enumerate(self.pair + newer)
        )
        result, _ = self.run_db(rows)
        self.assertEqual(result, newer)

    def test_09_scan_cap_fails_closed(self):
        session = FakeSession(
            [db_row(self.pair[0])] * (MAX_FED_SCAN_ROWS + 1)
        )
        with patch(
            "backend.macro.fed_rate_candidate_repository.SessionLocal",
            return_value=session,
        ):
            with self.assertRaisesRegex(FedCandidateRepositoryError, "budget"):
                self.repo.get_history_as_of(as_of=self.as_of)
        self.assertTrue(session.closed)

    def test_10_bad_clock_or_limit_never_reaches_session(self):
        with patch(
            "backend.macro.fed_rate_candidate_repository.SessionLocal"
        ) as factory:
            for as_of, limit in (
                (datetime(2026, 9, 16, 18), 256),
                (self.as_of, 1),
                (self.as_of, 3),
                (self.as_of, 258),
                (self.as_of, True),
            ):
                with self.subTest(as_of=as_of, limit=limit):
                    with self.assertRaises(FedCandidateRepositoryError):
                        self.repo.get_history_as_of(
                            as_of=as_of, limit=limit
                        )
            factory.assert_not_called()

    def test_11_history_limit_preserves_whole_most_recent_decision(self):
        older = fed_pair(
            statement="monetary20260729a",
            release=datetime(2026, 7, 29, 18, tzinfo=timezone.utc),
            ingested=datetime(2026, 7, 29, 18, 1, tzinfo=timezone.utc),
            effective=date(2026, 7, 30),
        )
        rows = tuple(
            db_row(x, n) for n, x in enumerate(older + self.pair)
        )
        result, _ = self.run_db(rows, limit=2)
        self.assertEqual(result, self.pair)

    def test_12_query_failure_still_closes_session(self):
        session = FakeSession(())
        session.execute = Mock(side_effect=RuntimeError("query failed"))
        with patch(
            "backend.macro.fed_rate_candidate_repository.SessionLocal",
            return_value=session,
        ):
            with self.assertRaisesRegex(RuntimeError, "query failed"):
                self.repo.get_history_as_of(as_of=self.as_of)
        self.assertTrue(session.closed)


class FedFactoryCandidateProbeTests(unittest.TestCase):
    def setUp(self):
        self.base = make_context()
        self.repository = Mock()
        self.factory = AgentContextFactory(
            require_ticks=False,
            fed_candidate_repository=self.repository,
        )

    def test_13_explicit_probe_is_diagnostic_only(self):
        self.repository.get_history_as_of.return_value = fed_pair()
        receipt = self.factory.inspect_fed_candidates(self.base)
        self.repository.get_history_as_of.assert_called_once_with(
            as_of=self.base.as_of,
            limit=256,
        )
        self.assertEqual(receipt.candidate_snapshot_count, 2)
        self.assertEqual(
            receipt.readiness.rate_input_status,
            FedRateInputStatus.INTERNALLY_CONSISTENT,
        )
        self.assertFalse(receipt.council_context_admission_authorized)
        self.assertFalse(receipt.council_registration_authorized)
        self.assertEqual(self.base.fed_data, ())
        self.assertFalse(hasattr(receipt, "context"))
        self.assertFalse(hasattr(receipt, "snapshots"))

    def test_14_absent_inputs_still_block_registration(self):
        self.repository.get_history_as_of.return_value = ()
        receipt = self.factory.inspect_fed_candidates(self.base)
        self.assertEqual(receipt.candidate_snapshot_count, 0)
        self.assertFalse(receipt.council_registration_authorized)

    def test_15_base_context_forgery_rejected_before_query(self):
        malformed = self.base.model_copy(update={"symbol": "GBPUSD"})
        with self.assertRaisesRegex(ValueError, "base context"):
            self.factory.inspect_fed_candidates(malformed)
        self.repository.get_history_as_of.assert_not_called()

    def test_16_nonempty_base_fed_context_cannot_be_reused(self):
        context = make_context(fed_data=fed_pair())
        with self.assertRaisesRegex(ValueError, "empty operational"):
            self.factory.inspect_fed_candidates(context)
        self.repository.get_history_as_of.assert_not_called()

    def test_17_actual_live_build_never_calls_fed_probe(self):
        self.factory.candle_repository = Mock()
        self.factory.candle_repository.get_latest_as_of.return_value = (
            self.base.candles
        )
        self.factory.tick_repository = Mock()
        self.factory.tick_repository.get_between.return_value = ()
        self.factory._load_macro_context = Mock(return_value=((), ()))
        result = SimpleNamespace(
            snapshot=self.base.market_state,
            anchor_candle_timestamp=self.base.candles[-1].timestamp,
        )
        context = self.factory.build(result)
        self.assertEqual(context.fed_data, ())
        self.assertEqual(context.ecb_data, ())
        self.repository.get_history_as_of.assert_not_called()

    def test_18_invalid_candidate_does_not_create_delivery_context(self):
        self.repository.get_history_as_of.return_value = fed_pair()[::-1]
        with self.assertRaises(ValueError):
            self.factory.inspect_fed_candidates(self.base)
        self.assertEqual(self.base.fed_data, ())

    def test_19_factory_default_repo_is_fed_only(self):
        fresh = AgentContextFactory(require_ticks=False)
        self.assertEqual(
            fresh.fed_candidate_repository.source,
            FED_RATE_SOURCE,
        )
        self.assertEqual(fresh.fed_candidate_repository.central_bank, "FED")
        self.assertEqual(fresh.ecb_rate_repository.central_bank, "ECB")
        self.assertFalse(hasattr(fresh, "fed_rate_repository"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
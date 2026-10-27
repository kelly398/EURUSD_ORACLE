"""Real transactions on isolated SQLite; synthetic inputs, no broker/network."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select, func, update, event
from pydantic import ValidationError

from backend.agents.council_runner import CouncilRunner
from backend.agents.test_canonical_council import registry_with_core_doubles, ready_context
from backend.agents.test_remaining_council_seats import NOW
from backend.learning.contracts import ForecastSpecification, OutcomeQuote, RunProvenance, digest
from backend.learning.memory import CouncilMemory, MemoryConflict, MemoryIntegrityError
from backend.learning.schema import cycles, agents, forecasts, outcomes, schema_version
from backend.learning.service import CouncilMemoryService


def specification(**changes):
    values = dict(target_version="mid_quote_15m_v1", horizon_seconds=900,
                  neutral_band_pips=1, max_quote_age_seconds=5,
                  round_trip_commission_pips=0.2)
    return ForecastSpecification.model_validate({**values, **changes})


def provenance(**changes):
    values = dict(scope="synthetic_memory_v1", origin="synthetic", policy_version="v1",
                  code_sha256="a"*64, artifact_sha256={"frozen_model": "b"*64},
                  environment_sha256="c"*64)
    return RunProvenance.model_validate({**values, **changes})


def quote(**changes):
    values = dict(timestamp=NOW+timedelta(seconds=900),
                  available_at=NOW+timedelta(seconds=901), bid=1.1003, ask=1.1004,
                  source="SYNTHETIC", source_record_id="synthetic_outcome_1", origin="synthetic")
    return OutcomeQuote.model_validate({**values, **changes})


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.url = "sqlite:///" + str(Path(self.folder.name)/"memory.sqlite3")
        self.engine = create_engine(self.url, connect_args={"timeout": 15})
        self.addCleanup(self.engine.dispose)
        self.now = NOW+timedelta(seconds=1)
        self.memory = CouncilMemory(self.engine, clock=lambda: self.now)
        self.memory.initialize()
        self.context = ready_context()
        self.runner = CouncilRunner(registry_with_core_doubles(), mode="research", max_workers=2)
        self.run = self.runner.run(self.context)
        self.specs = {"trend_master": specification()}

    def record(self, **changes):
        values = dict(context=self.context, result=self.run, provenance=provenance(), forecast_specs=self.specs)
        return self.memory.record_cycle(**{**values, **changes})

    def count(self, table):
        with self.engine.connect() as connection:
            return connection.execute(select(func.count()).select_from(table)).scalar_one()

    def mature(self):
        self.now = NOW+timedelta(seconds=902)

    def test_cycle_stores_all_seats_inputs_models_policy_and_abstention(self):
        receipt = self.record()
        saved = self.memory.load_cycle(receipt.cycle_id)
        self.assertEqual(self.count(cycles), 1)
        self.assertEqual(self.count(agents), 31)
        self.assertEqual(saved["provenance"]["artifact_sha256"], {"frozen_model": "b"*64})
        self.assertEqual(saved["context"]["synthesis_policy"], self.context.synthesis_policy.model_dump())
        self.assertEqual(saved["run"]["decision"], "hold")
        self.assertFalse(saved["run"]["execution_authorized"])

    def test_duplicate_cycle_ignores_durations_and_keeps_original_record_time(self):
        first = self.record()
        altered = replace(self.run, primary=replace(self.run.primary, executions=tuple(
            replace(e, duration_ms=e.duration_ms+100) for e in self.run.primary.executions)))
        self.now += timedelta(hours=1)
        second = self.record(result=altered)
        self.assertEqual(first.cycle_id, second.cycle_id)
        self.assertEqual(first.prediction_ids, second.prediction_ids)
        self.assertFalse(second.inserted)
        self.assertEqual(self.count(agents), 31)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(cycles.c.recorded_at)).scalar_one(), NOW+timedelta(seconds=1))

    def test_changed_policy_or_code_cannot_overwrite_cycle(self):
        self.record()
        for changed in (provenance(policy_version="v2"), provenance(code_sha256="d"*64)):
            with self.subTest(changed=changed), self.assertRaises(MemoryConflict):
                self.record(provenance=changed)
        self.assertEqual(self.count(cycles), 1)

    def test_changed_forecast_specification_conflicts(self):
        self.record()
        with self.assertRaises(MemoryConflict):
            self.record(forecast_specs={"trend_master": specification(horizon_seconds=300)})

    def test_conflicting_context_is_rejected_before_writing(self):
        changed = self.context.model_copy(update={"as_of": self.context.as_of+timedelta(seconds=1)})
        with self.assertRaises(ValueError):
            self.record(context=changed)
        self.assertEqual(self.count(cycles), 0)

    def test_no_implicit_forecast_horizons(self):
        receipt = self.record(forecast_specs={})
        self.assertEqual(receipt.prediction_ids, ())
        self.assertEqual(self.count(forecasts), 0)

    def test_contextual_agent_cannot_be_scored_as_directional(self):
        with self.assertRaises(ValueError):
            self.record(forecast_specs={"fed": specification()})
        self.assertEqual(self.count(cycles), 0)

    def test_failed_forecast_insert_rolls_back_cycle_and_all_seats(self):
        def fail(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO oracle_forecasts"):
                raise RuntimeError("Simulated storage failure")
        event.listen(self.engine, "before_cursor_execute", fail)
        try:
            with self.assertRaises(RuntimeError):
                self.record()
        finally:
            event.remove(self.engine, "before_cursor_execute", fail)
        self.assertEqual([self.count(t) for t in (cycles, agents, forecasts)], [0, 0, 0])

    def test_concurrent_duplicate_writers_commit_one_complete_cycle(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = tuple(executor.map(lambda _: self.record(), range(8)))
        self.assertEqual(sum(r.inserted for r in results), 1)
        self.assertEqual([self.count(t) for t in (cycles, agents, forecasts)], [1, 31, 1])

    def test_reopening_database_preserves_record_identity(self):
        receipt = self.record()
        other = create_engine(self.url)
        try:
            reopened = CouncilMemory(other, clock=lambda: self.now)
            reopened.initialize()
            self.assertEqual(reopened.load_cycle(receipt.cycle_id)["schema_version"], 1)
            self.assertFalse(reopened.record_cycle(self.context, self.run, provenance(), forecast_specs=self.specs).inserted)
        finally:
            other.dispose()

    def test_unknown_schema_version_is_refused(self):
        with self.engine.begin() as connection:
            connection.execute(schema_version.insert().values(version=2))
        with self.assertRaises(MemoryIntegrityError):
            self.memory.initialize()

    def test_partial_schema_is_not_silently_repaired(self):
        from sqlalchemy import inspect
        other = create_engine('sqlite://')
        try:
            schema_version.create(other)
            memory = CouncilMemory(other)
            with self.assertRaises(MemoryIntegrityError):
                memory.initialize()
            self.assertEqual(inspect(other).get_table_names(), ['oracle_memory_schema'])
        finally:
            other.dispose()

    def test_outcome_is_not_available_before_maturity(self):
        receipt = self.record()
        self.assertEqual(self.memory.pending(as_of=self.now, scope=provenance().scope, origin="synthetic"), ())
        with self.assertRaises(ValueError):
            self.memory.resolve(receipt.prediction_ids[0], quote())
        self.assertEqual(self.count(outcomes), 0)

    def test_mature_long_outcome_uses_bid_ask_and_commission(self):
        prediction = self.record().prediction_ids[0]
        self.mature()
        self.assertEqual(self.memory.pending(as_of=self.now, scope=provenance().scope, origin="synthetic"), (prediction,))
        result = self.memory.resolve(prediction, quote())
        self.assertEqual(result["target_label"], "long")
        self.assertAlmostEqual(result["mid_move_pips"], 3)
        self.assertAlmostEqual(result["hypothetical_net_pips"], 1.8)
        self.assertFalse(result["actual_fill"])
        self.assertFalse(result["execution_authorized"])
        self.assertEqual(self.memory.pending(as_of=self.now, scope=provenance().scope, origin="synthetic"), ())

    def test_outcome_receipt_delay_is_respected(self):
        prediction = self.record().prediction_ids[0]
        self.mature()
        with self.assertRaises(ValueError):
            self.memory.resolve(prediction, quote(available_at=self.now+timedelta(seconds=1)))

    def test_historical_reads_cannot_see_later_scoring(self):
        prediction = self.record().prediction_ids[0]
        self.mature()
        self.memory.resolve(prediction, quote())
        self.assertEqual(self.memory.available_outcomes(as_of=NOW+timedelta(seconds=901), scope=provenance().scope, origin="synthetic"), ())
        self.assertEqual(len(self.memory.available_outcomes(as_of=self.now, scope=provenance().scope, origin="synthetic")), 1)

    def test_synthetic_outcomes_do_not_appear_in_live_or_other_scopes(self):
        prediction = self.record().prediction_ids[0]
        self.mature()
        self.memory.resolve(prediction, quote())
        self.assertEqual(self.memory.available_outcomes(as_of=self.now, scope=provenance().scope, origin="live"), ())
        self.assertEqual(self.memory.available_outcomes(as_of=self.now, scope="other", origin="synthetic"), ())

    def test_outcome_quote_origin_must_match_recorded_cycle(self):
        prediction = self.record().prediction_ids[0]
        self.mature()
        with self.assertRaises(ValueError):
            self.memory.resolve(prediction, quote(origin="live"))

    def test_short_and_neutral_predictions_have_distinct_cost_behavior(self):
        from backend.agents.contracts import AgentOpinion
        first = self.run.primary.executions[0]
        for direction, score, expected in (("short", -0.8, -4.2), ("neutral", 0, None)):
            with self.subTest(direction=direction):
                opinion = AgentOpinion.model_validate({**first.opinion.model_dump(),
                    "direction": direction, "score": score})
                run = replace(self.run, primary=replace(self.run.primary, executions=(
                    replace(first, opinion=opinion), *self.run.primary.executions[1:])))
                self.now = NOW+timedelta(seconds=1)
                receipt = self.record(result=run, provenance=provenance(scope=direction))
                self.mature()
                result = self.memory.resolve(receipt.prediction_ids[0], quote())
                self.assertEqual(result["hypothetical_net_pips"], expected)
                self.assertFalse(result["correct"])

    def test_neutral_band_boundary_is_not_rounded_into_directional_label(self):
        prediction = self.record().prediction_ids[0]
        self.mature()
        result = self.memory.resolve(prediction, quote(bid=1.1001, ask=1.1002))
        self.assertEqual(result["mid_move_pips"], 1)
        self.assertEqual(result["target_label"], "neutral")

    def test_blocked_production_seats_are_preserved_without_forecasts(self):
        from backend.agents.contracts import AgentContext
        sample = AgentContext.model_validate({**self.context.model_dump(), "execution_mode": "production"})
        run = CouncilRunner(registry_with_core_doubles(), mode="production").run(sample)
        receipt = self.record(context=sample, result=run, forecast_specs={"fed": specification()})
        saved = self.memory.load_cycle(receipt.cycle_id)
        fed = next(e for e in saved["run"]["primary"]["executions"] if e["agent_id"] == "fed")
        self.assertEqual(fed["status"], "blocked")
        self.assertTrue(fed["blockers"])
        self.assertEqual(receipt.prediction_ids, ())

    def test_live_commit_cannot_be_created_after_maturity_but_can_be_retried(self):
        from backend.agents.contracts import AgentContext
        sample = AgentContext.model_validate({**self.context.model_dump(), "execution_mode": "production"})
        run = CouncilRunner(registry_with_core_doubles(), mode="production").run(sample)
        receipt = self.record(context=sample, result=run, provenance=provenance(origin="live"))
        self.mature()
        again = self.record(context=sample, result=run, provenance=provenance(origin="live"))
        self.assertEqual(receipt.cycle_id, again.cycle_id)
        self.assertFalse(again.inserted)
        with self.assertRaises(ValueError):
            self.record(context=sample, result=run, provenance=provenance(origin="live", scope="late"))

    def test_stale_or_after_horizon_quote_cannot_score_forecast(self):
        prediction = self.record().prediction_ids[0]
        self.mature()
        for seconds in (894, 901):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                self.memory.resolve(prediction, quote(timestamp=NOW+timedelta(seconds=seconds)))

    def test_outcome_retry_is_idempotent_and_correction_cannot_overwrite(self):
        prediction = self.record().prediction_ids[0]
        self.mature()
        original = self.memory.resolve(prediction, quote())
        self.now += timedelta(seconds=1)
        self.assertEqual(self.memory.resolve(prediction, quote()), original)
        with self.assertRaises(MemoryConflict):
            self.memory.resolve(prediction, quote(bid=1.1002))
        self.assertEqual(self.count(outcomes), 1)

    def test_snapshot_tampering_is_detected(self):
        receipt = self.record()
        with self.engine.begin() as connection:
            connection.execute(update(cycles).values(payload_json="{}"))
        with self.assertRaises(MemoryIntegrityError):
            self.memory.load_cycle(receipt.cycle_id)

    def test_forecast_tampering_is_detected(self):
        prediction = self.record().prediction_ids[0]
        with self.engine.begin() as connection:
            connection.execute(update(forecasts).values(direction="short"))
        self.mature()
        with self.assertRaises(MemoryIntegrityError):
            self.memory.resolve(prediction, quote())

    def test_unknown_agent_and_governance_forecasts_are_rejected(self):
        for agent_id in ("unknown", "chief_strategist"):
            with self.subTest(agent_id=agent_id), self.assertRaises(ValueError):
                self.record(forecast_specs={agent_id: specification()})

    def test_research_run_cannot_be_relabelled_live(self):
        with self.assertRaises(ValueError):
            self.record(provenance=provenance(origin="live"))

    def test_old_entry_quote_is_rejected_atomically(self):
        with self.assertRaises(ValueError):
            self.record(forecast_specs={"trend_master": specification(max_quote_age_seconds=0)})
        self.assertEqual(self.count(cycles), 0)

    def test_queries_cannot_request_future_information(self):
        for method in (self.memory.pending, self.memory.available_outcomes):
            with self.assertRaises(ValueError):
                method(as_of=self.now+timedelta(seconds=1), scope="test", origin="synthetic")

    def test_utc_identity_normalizes_equivalent_offsets(self):
        self.assertEqual(digest({"time": NOW}), digest({"time": NOW.astimezone(timezone(timedelta(hours=3)))}))

    def test_journal_service_uses_canonical_runner_and_returns_receipt(self):
        with patch.object(self.runner, "run", wraps=self.runner.run) as run:
            service = CouncilMemoryService(self.runner, self.memory, scope=provenance().scope,
                origin="synthetic", policy_version="v1", forecast_specs=self.specs,
                provenance_provider=lambda **kw: provenance())
            result = service.run(self.context)
        run.assert_called_once()
        self.assertEqual(result.run.decision, self.run.decision)
        self.assertTrue(result.receipt.inserted)

    def test_service_refuses_artifact_changes_during_inference(self):
        versions = iter((provenance(), provenance(code_sha256="d"*64)))
        service = CouncilMemoryService(self.runner, self.memory, scope="test", origin="synthetic",
            policy_version="v1", provenance_provider=lambda **kw: next(versions))
        with self.assertRaises(RuntimeError):
            service.run(self.context)
        self.assertEqual(self.count(cycles), 0)


class ContractTests(unittest.TestCase):
    def test_invalid_quotes_targets_and_provenance(self):
        cases = ((specification, {"horizon_seconds": 0}), (specification, {"horizon_seconds": True}),
                 (specification, {"neutral_band_pips": float("nan")}),
                 (specification, {"round_trip_commission_pips": -1}),
                 (quote, {"ask": 1}), (quote, {"timestamp": NOW.replace(tzinfo=None)}),
                 (quote, {"available_at": NOW}), (provenance, {"artifact_sha256": {"bad": "secret"}}))
        for factory, changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                factory(**changes)


if __name__ == "__main__":
    unittest.main()

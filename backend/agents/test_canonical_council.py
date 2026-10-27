"""Canonical registration, eligibility, stages and factory integration boundaries.

All inputs are synthetic. No broker, DB, network, training or reserved eval.
"""
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from backend.agents.agent_registry import AgentRegistry
from backend.agents.contracts import AgentContext, AgentStage
from backend.agents.council_inputs import ExecutionMode, CouncilSupplementalInputs
from backend.agents.council_manifest import COUNCIL_MANIFEST
from backend.agents.register_core_agents import register_core_agents, CORE_AGENT_FACTORIES
from backend.agents.council_runner import CouncilRunner, AgentRunStatus
from backend.agents.context_factory import AgentContextFactory
from backend.agents.fed_input_readiness import FedInputNotReady
from backend.agents.test_remaining_council_seats import (
    context, update, with_primary, SyntheticCoreAgent, calendar_event,
)
from backend.agents.test_fed_agent import make_context


def registry_with_core_doubles(*, fail=False):
    registry=AgentRegistry()
    registry.register_many(SyntheticCoreAgent(spec,fail=fail and spec.seat_number==1)
                           for spec in COUNCIL_MANIFEST[:24])
    return register_core_agents(registry,agent_ids=tuple(spec.agent_id for spec in COUNCIL_MANIFEST[24:]))


def ready_context():
    return update(context(), reliability=with_primary().reliability,
                  economic_calendar=(calendar_event(90000),))


class CanonicalCatalogueTests(unittest.TestCase):
    def test_one_catalogue_matches_all_manifest_seats_in_order(self):
        self.assertEqual([factory.agent_id for factory in CORE_AGENT_FACTORIES],
                         [spec.agent_id for spec in COUNCIL_MANIFEST])

    def test_registration_is_distinct_from_mode_eligibility(self):
        registry=register_core_agents(AgentRegistry(),agent_ids=('fed',))
        self.assertTrue(registry.is_registered('fed'))
        self.assertEqual(registry.registered_count,1)
        research=ready_context()
        production=update(research,execution_mode=ExecutionMode.PRODUCTION)
        self.assertTrue(registry.eligibility('fed',research,mode='research').allowed)
        report=registry.eligibility('fed',production)
        self.assertFalse(report.allowed)
        self.assertIn('FED_SOURCE_VERSION_ATTESTATION_MISSING',report.blockers)

    def test_default_registration_includes_all_31_real_implementations(self):
        registry=register_core_agents(AgentRegistry())
        self.assertEqual(registry.registered_count,31)
        self.assertEqual(registry.implemented_count,31)
        self.assertEqual(registry.missing_agent_ids,())
        self.assertEqual([len(registry.get_registered_by_stage(stage)) for stage in AgentStage],[28,2,1])

    def test_duplicate_batch_is_atomic(self):
        registry=AgentRegistry()
        candidate=SyntheticCoreAgent(COUNCIL_MANIFEST[0])
        with self.assertRaises(ValueError):
            registry.register_many((candidate,candidate))
        self.assertEqual(registry.registered_count,0)

    def test_invalid_candidate_does_not_partially_register_batch(self):
        registry=AgentRegistry()
        with self.assertRaises(TypeError):
            registry.register_many((SyntheticCoreAgent(COUNCIL_MANIFEST[0]),object()))
        self.assertEqual(registry.registered_count,0)

    def test_subset_request_preflights_existing_or_unknown_ids(self):
        registry=register_core_agents(AgentRegistry(),agent_ids=('fed',))
        for selection in (('rate_differential','fed'),('rate_differential','unknown'),('fed','fed')):
            with self.subTest(selection=selection),self.assertRaises(ValueError):
                register_core_agents(registry,agent_ids=selection)
            self.assertEqual(registry.registered_agent_ids,('fed',))


class CanonicalDispatchTests(unittest.TestCase):
    def test_production_blocks_fed_without_calling_agent(self):
        registry=registry_with_core_doubles()
        sample=update(ready_context(),execution_mode='production')
        with patch.object(registry.get_agent('fed'),'run',side_effect=AssertionError('Must not execute')) as run:
            result=CouncilRunner(registry).run(sample)
        run.assert_not_called()
        fed=next(e for e in result.executions if e.agent_id=='fed')
        self.assertEqual(fed.status,AgentRunStatus.BLOCKED)
        self.assertIsNone(fed.opinion)
        self.assertIsNone(fed.error)
        self.assertIn('FED_SOURCE_VERSION_ATTESTATION_MISSING',fed.blockers)
        self.assertEqual(result.primary.missing_agent_ids,())
        self.assertGreaterEqual(result.primary.blocked_count,3)
        self.assertFalse(result.is_complete)
        self.assertEqual(result.decision,'hold')
        self.assertFalse(result.execution_authorized)

    def test_default_mode_cannot_run_a_research_context(self):
        with self.assertRaises(ValueError):
            CouncilRunner(registry_with_core_doubles()).run(ready_context())

    def test_model_symbol_scope_is_checked_before_dispatch(self):
        original=make_context()
        payload=original.model_dump()
        payload['symbol']='GBPUSD'
        payload['market_state']['symbol']='GBPUSD'
        payload['candles']=tuple({**candle,'symbol':'GBPUSD'} for candle in payload['candles'])
        sample=AgentContext.model_validate(payload)
        with self.assertRaisesRegex(ValueError,'require EURUSD M5'):
            CouncilRunner(registry_with_core_doubles()).run_primary(sample)

    def test_eligibility_revalidates_caller_copies(self):
        sample=ready_context()
        invalid=sample.model_copy(update={'cross_asset_data':tuple(
            item.model_copy(update={'ingested_at':sample.as_of+timedelta(seconds=1)})
            for item in sample.cross_asset_data)})
        with self.assertRaises(ValidationError):
            registry_with_core_doubles().eligibility('risk_sentiment',invalid,mode='research')

    def test_agent_mutation_is_detected_and_original_input_is_preserved(self):
        registry=registry_with_core_doubles()
        original=ready_context()
        def mutate(shared):
            object.__setattr__(shared,'max_cross_asset_age_days',5)
            return registry.get_agent('price_action').run(shared)
        with patch.object(registry.get_agent('trend_master'),'run',side_effect=mutate):
            with self.assertRaisesRegex(RuntimeError,'mutated shared Council input'):
                CouncilRunner(registry,mode='research',max_workers=1).run(original)
        self.assertEqual(original.max_cross_asset_age_days,4)

    def test_wrong_agent_identity_becomes_error_without_an_opinion(self):
        registry=registry_with_core_doubles()
        sample=ready_context()
        wrong=registry.get_agent('price_action').run(sample)
        with patch.object(registry.get_agent('trend_master'),'run',return_value=wrong):
            result=CouncilRunner(registry,mode='research').run(sample)
        execution=result.primary.executions[0]
        self.assertEqual(execution.status,AgentRunStatus.ERROR)
        self.assertIsNone(execution.opinion)
        self.assertIn('does not match dispatch',execution.error)
        self.assertEqual(result.decision,'hold')

    def test_explicit_research_runs_canonical_stages(self):
        result=CouncilRunner(registry_with_core_doubles(),mode='research').run(ready_context())
        self.assertTrue(result.is_complete)
        self.assertEqual([e.seat_number for e in result.executions],list(range(1,32)))
        self.assertEqual(result.primary.succeeded_count,28)
        self.assertEqual(result.mode,ExecutionMode.RESEARCH)
        self.assertEqual(result.decision,'hold')

    def test_missing_measurements_block_before_invocation(self):
        registry=registry_with_core_doubles()
        sample=update(ready_context(),cross_asset_data=(),risk_state=None)
        with (patch.object(registry.get_agent('risk_sentiment'),'run') as sentiment,
              patch.object(registry.get_agent('risk_officer'),'run') as officer):
            result=CouncilRunner(registry,mode='research').run(sample)
        sentiment.assert_not_called()
        officer.assert_not_called()
        blocked={e.agent_id:e for e in result.blocked_executions}
        self.assertIn('risk_sentiment',blocked)
        self.assertIn('risk_officer',blocked)
        self.assertEqual(result.decision,'hold')

    def test_blocked_and_execution_errors_are_distinct(self):
        registry=registry_with_core_doubles(fail=True)
        sample=update(ready_context(),cross_asset_data=())
        result=CouncilRunner(registry,mode='research').run(sample)
        self.assertEqual(result.primary.failed_count,1)
        self.assertEqual(result.primary.blocked_count,1)
        self.assertNotIn('risk_sentiment',{o.agent_id for o in result.primary.opinions})
        self.assertNotIn('trend_master',{o.agent_id for o in result.primary.opinions})
        self.assertEqual(result.decision,'hold')

    def test_stale_cross_asset_is_blocked(self):
        sample=ready_context()
        old=tuple(update(item,observation_at=item.observation_at-timedelta(days=5),
                         previous_observation_at=item.previous_observation_at-timedelta(days=5))
                  for item in sample.cross_asset_data)
        registry=registry_with_core_doubles()
        result=CouncilRunner(registry,mode='research').run(update(sample,cross_asset_data=old))
        blocked=next(e for e in result.executions if e.agent_id=='risk_sentiment')
        self.assertEqual(blocked.status,AgentRunStatus.BLOCKED)
        self.assertIn('CROSS_ASSET_DATA_STALE',blocked.blockers)

    def test_data_quality_gate_and_injected_opinions_fail_closed(self):
        sample=ready_context()
        registry=registry_with_core_doubles()
        bad=sample.model_copy(update={'market_state':update(sample.market_state,data_quality='FAIL')})
        runner=CouncilRunner(registry,mode='research')
        with patch.object(runner,'_dispatch') as dispatch:
            with self.assertRaises(ValidationError):
                runner.run(bad)
        dispatch.assert_not_called()
        with self.assertRaises(ValueError):
            CouncilRunner(registry,mode='research').run(with_primary())


class CanonicalFactoryTests(unittest.TestCase):
    def factory(self,provider=None):
        sample=ready_context()
        factory=AgentContextFactory(ecb_rate_repository=Mock(),economic_calendar_repository=Mock(),
                                    council_input_provider=provider)
        factory.ecb_rate_repository.get_history_as_of.return_value=sample.ecb_data
        factory.economic_calendar_repository.get_window_as_of.return_value=sample.economic_calendar
        factory.candle_repository=Mock()
        factory.candle_repository.get_latest_as_of.return_value=sample.candles
        factory.tick_repository=Mock()
        factory.tick_repository.get_between.return_value=sample.ticks
        unified=SimpleNamespace(snapshot=sample.market_state,anchor_candle_timestamp=sample.candles[-1].timestamp)
        return factory,unified,sample

    def inputs(self,sample,*,fed=False):
        return CouncilSupplementalInputs(fed_data=sample.fed_data if fed else (),
            cross_asset_data=sample.cross_asset_data,calendar_coverage=sample.calendar_coverage,
            risk_state=sample.risk_state,risk_limits=sample.risk_limits,
            reliability=sample.reliability,synthesis_policy=sample.synthesis_policy)

    def test_canonical_factory_defaults_to_production_without_fed(self):
        factory,unified,sample=self.factory()
        result=factory.build(unified)
        self.assertIs(type(result),AgentContext)
        self.assertEqual(result.execution_mode,ExecutionMode.PRODUCTION)
        self.assertEqual(result.fed_data,())
        self.assertEqual(result.cross_asset_data,())

    def test_factory_materializes_typed_research_inputs(self):
        factory,unified,sample=self.factory()
        result=factory.build(unified,mode='research',supplemental=self.inputs(sample,fed=True))
        self.assertIs(type(result),AgentContext)
        self.assertEqual(result.fed_data,sample.fed_data)
        self.assertEqual(result.cross_asset_data,sample.cross_asset_data)
        self.assertEqual(result.risk_state,sample.risk_state)
        self.assertEqual(result.execution_mode,ExecutionMode.RESEARCH)

    def test_production_fed_injection_refused_before_market_queries(self):
        factory,unified,sample=self.factory()
        with self.assertRaises(FedInputNotReady):
            factory.build(unified,supplemental=self.inputs(sample,fed=True))
        factory.candle_repository.get_latest_as_of.assert_not_called()
        factory.tick_repository.get_between.assert_not_called()

    def test_provider_receives_exact_asof_and_cannot_inject_stage_opinions(self):
        provider=Mock()
        factory,unified,sample=self.factory(provider)
        provider.get_inputs_as_of.return_value=self.inputs(sample)
        result=factory.build(unified)
        provider.get_inputs_as_of.assert_called_once_with(as_of=sample.as_of)
        self.assertEqual(result.cross_asset_data,sample.cross_asset_data)
        with self.assertRaises(ValidationError):
            CouncilSupplementalInputs(primary_agent_opinions=with_primary().primary_agent_opinions)

    def test_future_provider_inputs_and_mixed_input_sources_rejected(self):
        provider=Mock()
        factory,unified,sample=self.factory(provider)
        provider.get_inputs_as_of.return_value=update(self.inputs(sample),risk_state=update(sample.risk_state,
            as_of=sample.as_of+timedelta(seconds=1)))
        with self.assertRaises(ValidationError):
            factory.build(unified)
        with self.assertRaises(ValueError):
            factory.build(unified,supplemental=self.inputs(sample))


if __name__=='__main__':
    unittest.main(verbosity=2)

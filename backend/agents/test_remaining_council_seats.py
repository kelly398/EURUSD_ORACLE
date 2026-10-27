"""Deterministic boundary tests; no broker, database, network or reserved eval."""
from datetime import timedelta
import unittest

from pydantic import ValidationError

from backend.agents.agent_registry import AgentRegistry
from backend.agents.base_agent import BaseAgent
from backend.agents.contracts import AgentContext, AgentOpinion, AgentSignalType, AgentStage
from backend.agents.council_inputs import ExecutionMode
from backend.agents.test_cot_positioning import make_snapshot
from backend.agents.council_manifest import COUNCIL_MANIFEST
from backend.agents.council_analysis_context import (
    CouncilAnalysisContext, CrossAssetObservation, CalendarCoverage,
    PortfolioRiskSnapshot, RiskLimits, AgentReliability, SynthesisPolicy,
)
from backend.agents.implementations.rate_differential_agent import RateDifferentialAgent
from backend.agents.implementations.economic_calendar_agent import EconomicCalendarAgent
from backend.agents.implementations.risk_sentiment_agent import RiskSentimentAgent
from backend.agents.implementations.devils_advocate_agent import DevilsAdvocateAgent
from backend.agents.implementations.risk_officer_agent import RiskOfficerAgent
from backend.agents.implementations.chief_strategist_agent import ChiefStrategistAgent
from backend.agents.research_council import register_research_seats, ResearchCouncilRunner
from backend.agents.test_fed_agent import make_context, fed_pair, RELEASE
from backend.macro.macro_schemas import CentralBankRateSnapshot, EconomicCalendarEventSnapshot
from backend.market_state.schemas import LiquidityState, SpreadState, VolatilityState, VolatilityRegime, TickSample


NOW = RELEASE + timedelta(minutes=5)


def limits(**updates):
    return RiskLimits.model_validate(dict(
        max_daily_drawdown_fraction=0.05, max_gross_exposure_multiple=2,
        max_total_risk_fraction=0.03, max_trade_risk_fraction=0.01,
        max_spread_pips=2, max_atr_pips=30, max_snapshot_age_seconds=60,
        max_market_age_seconds=30, event_blackout_before_seconds=900,
        event_blackout_after_seconds=300, **updates))


def update(model, **changes):
    return type(model).model_validate({**model.model_dump(), **changes})


def risk(**updates):
    values = dict(as_of=NOW, account_currency="USD", equity=100000,
                  day_start_equity=100000, gross_exposure=10000, open_risk=100,
                  proposed_risk=100, proposed_gross_exposure=10000,
                  broker_connected=True, reconciliation_ok=True, kill_switch=False)
    return PortfolioRiskSnapshot.model_validate({**values, **updates})


def observations(**changes):
    result = []
    for series, value, previous in (("SP500", 101, 100), ("VIXCLS", 19, 20)):
        values = dict(series_id=series, observation_at=NOW-timedelta(hours=1),
                      previous_observation_at=NOW-timedelta(days=1, hours=1),
                      available_at=NOW-timedelta(minutes=1), ingested_at=NOW,
                      value=value, previous_value=previous, content_hash="a"*64)
        result.append(CrossAssetObservation.model_validate({**values, **changes.get(series, {})}))
    return tuple(result)


def context(**changes):
    base = make_context(fed_data=fed_pair())
    payload = base.model_dump()
    payload['ecb_data'] = (CentralBankRateSnapshot(
        central_bank="ECB", rate_name="deposit_facility_rate", reference_date=NOW.date(),
        available_at=RELEASE, value_percent=2.5, source_record_id="test_ecb_dfr",
        content_hash="b"*64, source="ECB_SDMX_FM", ingested_at=NOW),)
    payload['ticks'] = (TickSample(symbol="EURUSD", timestamp=NOW-timedelta(seconds=1),
                                   source_time_msc=int((NOW-timedelta(seconds=1)).timestamp()*1000),
                                   bid=1.1, ask=1.1001, spread_pips=1),)
    payload['market_state']['liquidity'] = LiquidityState(
        spread_state=SpreadState.NORMAL, average_spread_pips=1)
    payload['market_state']['volatility'] = VolatilityState(
        regime=VolatilityRegime.NORMAL, atr_pips=5)
    base = type(base).model_validate(payload)
    coverage = CalendarCoverage(start=NOW-timedelta(days=1), end=NOW+timedelta(days=1),
                                available_at=RELEASE, currencies=("EUR", "USD"), sources=("TEST",))
    inputs = dict(execution_mode=ExecutionMode.RESEARCH, cot_data=(make_snapshot(0),),
                  cross_asset_data=observations(), calendar_coverage=coverage,
                  risk_limits=limits(), risk_state=risk(),
                  synthesis_policy=SynthesisPolicy(min_directional_agents=3, min_categories=2,
                    min_agreement=0.7, min_strength=0.2, min_confidence=0.5, max_reliability_age_days=30))
    return AgentContext.model_validate({**base.model_dump(), **inputs, **changes})


def primary_opinions(*, certified=False, opposing=(), omit=()):
    opinions = []
    for spec in COUNCIL_MANIFEST:
        if spec.stage != AgentStage.PRIMARY or spec.agent_id in omit:
            continue
        directional = spec.seat_number <= 21
        score = (-0.8 if spec.agent_id in opposing else 0.8) if directional else 0
        evidence = {"input_ready": True}
        if spec.agent_id == "fed":
            evidence['historical_source_version_certified'] = certified
        opinions.append(AgentOpinion(agent_id=spec.agent_id, agent_name=spec.agent_name,
            category=spec.category, signal_type=AgentSignalType.DIRECTIONAL if directional else AgentSignalType.CONTEXTUAL,
            version="test", as_of=NOW, direction="long" if score>0 else "short" if score<0 else "neutral",
            score=score, confidence=0.8, rationale="Synthetic boundary fixture", evidence=evidence))
    return tuple(opinions)


def with_primary(**changes):
    opinions = primary_opinions()
    weights = tuple(AgentReliability(agent_id=o.agent_id, reliability=0.7,
                    regime_compatibility=0.8, available_at=RELEASE, evaluation_id="SYNTHETIC")
                    for o in opinions if o.signal_type == AgentSignalType.DIRECTIONAL)
    return context(primary_agent_opinions=opinions, reliability=weights, **changes)


def calendar_event(delta):
    return EconomicCalendarEventSnapshot(source_event_id="test", event_name="test release",
        jurisdiction="US", currency="USD", scheduled_at=NOW+timedelta(seconds=delta),
        available_at=RELEASE, ingested_at=NOW, content_hash="a"*64, source="TEST")


class AnalysisInputTests(unittest.TestCase):
    def test_base_context_is_preserved(self):
        base = make_context()
        result = CouncilAnalysisContext.from_context(base)
        self.assertEqual(result.candles, base.candles)
        self.assertFalse(result.execution_authorized)

    def test_cannot_replace_base_context_through_supplements(self):
        with self.assertRaises(ValueError):
            CouncilAnalysisContext.from_context(make_context(), fed_data=fed_pair())

    def test_analysis_mode_is_research_only(self):
        with self.assertRaises(ValidationError):
            context(execution_mode="live")

    def test_unknown_fields_rejected(self):
        with self.assertRaises(ValidationError):
            context(allow_execution=True)

    def test_nonfinite_portfolio_values_rejected(self):
        for field in ('equity', 'open_risk', 'proposed_risk', 'gross_exposure'):
            for value in (float('nan'), float('inf')):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    risk(**{field: value})

    def test_future_or_naive_portfolio_rejected(self):
        with self.assertRaises(ValidationError):
            context(risk_state=risk(as_of=NOW+timedelta(microseconds=1)))
        with self.assertRaises(ValidationError):
            risk(as_of=NOW.replace(tzinfo=None))

    def test_cross_asset_future_receipt_rejected(self):
        with self.assertRaises(ValidationError):
            context(cross_asset_data=observations(SP500={'ingested_at': NOW+timedelta(seconds=1)}))

    def test_cross_asset_chronology_and_hash_rejected(self):
        for changes in ({'previous_observation_at':NOW}, {'available_at':RELEASE-timedelta(days=2)},
                        {'value':0}, {'value':float('nan')}, {'content_hash':'x'*64}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                observations(SP500=changes)

    def test_duplicate_cross_asset_series_rejected(self):
        with self.assertRaises(ValidationError):
            context(cross_asset_data=(observations()[0], observations()[0]))

    def test_future_coverage_rejected(self):
        with self.assertRaises(ValidationError):
            context(calendar_coverage=update(context().calendar_coverage, available_at=NOW+timedelta(seconds=1)))

    def test_duplicate_out_of_order_or_wrong_stage_opinions_rejected(self):
        opinions = primary_opinions()
        for supplied in ((opinions[0], opinions[0]), tuple(reversed(opinions)),
                         (update(opinions[0], as_of=RELEASE),)):
            with self.subTest(supplied=len(supplied)), self.assertRaises(ValidationError):
                context(primary_agent_opinions=supplied)
        with self.assertRaises(ValidationError):
            context(governance_opinions=(opinions[0],))

    def test_false_identity_rejected(self):
        with self.assertRaises(ValidationError):
            context(primary_agent_opinions=(update(primary_opinions()[0], agent_name="False identity"),))

    def test_reliability_future_duplicate_or_unknown_identity_rejected(self):
        item = AgentReliability(agent_id="trend_master", reliability=.8, regime_compatibility=.8,
                                 available_at=RELEASE, evaluation_id="test")
        for values in ((item,item), (update(item, agent_id="risk_officer"),),
                       (update(item,available_at=NOW+timedelta(seconds=1)),)):
            with self.subTest(count=len(values)), self.assertRaises(ValidationError):
                context(reliability=values)

    def test_calendar_actual_cannot_arrive_before_scheduled_event(self):
        event = EconomicCalendarEventSnapshot(source_event_id="future",event_name="release",jurisdiction="US",
            currency="USD",scheduled_at=NOW+timedelta(seconds=1),available_at=RELEASE,
            ingested_at=NOW,content_hash="a"*64,source="TEST",actual=2)
        with self.assertRaises(ValidationError):
            context(economic_calendar=(event,))

    def test_research_calendar_preserves_public_availability_not_backfill_receipt(self):
        event=EconomicCalendarEventSnapshot(source_event_id="known",event_name="release",jurisdiction="US",
            currency="USD",scheduled_at=NOW+timedelta(days=1),available_at=RELEASE,
            ingested_at=NOW+timedelta(days=10),content_hash="a"*64,source="TEST")
        self.assertEqual(context(economic_calendar=(event,)).economic_calendar[0],event)

    def test_forged_primary_role_and_nonfinite_evidence_rejected(self):
        original=primary_opinions()[0]
        for forged in (update(original,signal_type=AgentSignalType.GOVERNANCE),
                       update(original,evidence={'bad':float('nan')})):
            with self.subTest(forged=forged.signal_type),self.assertRaises(ValidationError):
                context(primary_agent_opinions=(forged,))

    def test_nested_model_copy_cannot_bypass_revalidation(self):
        ctx=context()
        forged=ctx.model_copy(update={'risk_state':risk().model_copy(update={'equity':float('nan')})})
        with self.assertRaises(ValidationError):
            RiskOfficerAgent().run(forged)

    def test_direction_and_sign_mismatch_rejected(self):
        opinion=update(primary_opinions()[0],direction='short')
        with self.assertRaises(ValidationError):
            context(primary_agent_opinions=(opinion,))


class RemainingMacroSeatsTests(unittest.TestCase):
    def test_rate_differential_announced_values_and_no_vote(self):
        result = RateDifferentialAgent().run(context())
        self.assertEqual(result.evidence['differential_basis_points'], -137.5)
        self.assertEqual(result.score, 0)
        self.assertEqual(result.confidence, 0)
        self.assertEqual(result.evidence['fed_reference_date'], '2026-09-17')
        self.assertFalse(result.evidence['historical_source_version_certified'])

    def test_rate_uses_canonical_context_and_reports_missing_inputs(self):
        with self.assertRaises(RuntimeError):
            RateDifferentialAgent().run(make_context())

    def test_rate_missing_bank_rejected(self):
        for changes in ({'ecb_data':()}, {'fed_data':()}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                RateDifferentialAgent().run(update(context(), **changes))

    def test_calendar_empty_feed_without_coverage_is_unknown(self):
        result = EconomicCalendarAgent().run(context(calendar_coverage=None))
        self.assertFalse(result.evidence['input_ready'])
        self.assertTrue(result.evidence['event_risk_blocked'])

    def test_calendar_empty_covered_window_is_only_scoped_clear(self):
        result = EconomicCalendarAgent().run(context())
        self.assertTrue(result.evidence['coverage_complete_for_window'])
        self.assertFalse(result.evidence['event_risk_blocked'])
        self.assertEqual(result.score, 0)

    def event(self, delta):
        return calendar_event(delta)

    def test_calendar_blackout_edges(self):
        for delta, blocked in ((901,False), (900,True), (0,True), (-299,True), (-300,False)):
            with self.subTest(delta=delta):
                result = EconomicCalendarAgent().run(update(context(), economic_calendar=(self.event(delta),)))
                self.assertEqual(result.evidence['event_risk_blocked'], blocked)

    def test_calendar_forecast_not_a_surprise_model(self):
        event = update(self.event(-30), actual=2, forecast=1, previous=0, unit="percent")
        result = EconomicCalendarAgent().run(update(context(), economic_calendar=(event,)))
        self.assertEqual(result.evidence['latest_actual'], 2)
        self.assertFalse(result.evidence['surprise_model_available'])

    def test_calendar_partial_currency_coverage_unknown(self):
        result = EconomicCalendarAgent().run(context(calendar_coverage=update(context().calendar_coverage, currencies=("EUR",))))
        self.assertFalse(result.evidence['input_ready'])

    def test_risk_sentiment_is_neutral_measurement(self):
        result = RiskSentimentAgent().run(context())
        self.assertEqual(result.evidence['risk_regime_proxy'], 'risk_on_proxy')
        self.assertEqual(result.score, 0)

    def test_risk_off_and_mixed(self):
        for values, label in (({'SP500':{'value':99},'VIXCLS':{'value':21}}, 'risk_off_proxy'),
                              ({'SP500':{'value':99}}, 'mixed_or_unchanged')):
            result = RiskSentimentAgent().run(context(cross_asset_data=observations(**values)))
            self.assertEqual(result.evidence['risk_regime_proxy'], label)

    def test_cross_asset_missing_stale_misaligned_rejected(self):
        old = NOW-timedelta(days=5)
        cases = ((), observations()[:1], observations(VIXCLS={'observation_at': NOW-timedelta(hours=2)}),
                 observations(**{key:{'observation_at':old, 'previous_observation_at':old-timedelta(days=1)}
                                 for key in ('SP500','VIXCLS')}))
        for values in cases:
            with self.subTest(count=len(values)), self.assertRaises(RuntimeError):
                RiskSentimentAgent().run(context(cross_asset_data=values))


class GovernanceTests(unittest.TestCase):
    def test_risk_pass_is_not_execution_permission(self):
        result = RiskOfficerAgent().run(with_primary())
        self.assertTrue(result.evidence['risk_checks_passed'])
        self.assertFalse(result.evidence['execution_authorized'])
        self.assertEqual(result.score, 0)

    def test_all_independent_portfolio_limits(self):
        cases = (({'kill_switch':True},'kill_switch'), ({'broker_connected':False},'broker_disconnected'),
                 ({'reconciliation_ok':False},'portfolio_not_reconciled'),
                 ({'as_of': NOW-timedelta(seconds=61)},'stale_portfolio_state'),
                 ({'equity':94000},'daily_drawdown_limit'),
                 ({'gross_exposure':200000},'gross_exposure_limit'),
                 ({'open_risk':3000},'total_risk_limit'),
                 ({'proposed_risk':1001},'per_trade_risk_limit'))
        for changes, reason in cases:
            with self.subTest(reason=reason):
                result = RiskOfficerAgent().run(with_primary(risk_state=risk(**changes)))
                self.assertFalse(result.evidence['risk_checks_passed'])
                self.assertIn(reason, result.evidence['blockers'])

    def test_missing_inputs_veto(self):
        for changes in ({'risk_state':None}, {'risk_limits':None}, {'calendar_coverage':None}, {'ticks':()}):
            with self.subTest(changes=list(changes)):
                result = RiskOfficerAgent().run(update(with_primary(), **changes))
                self.assertTrue(result.evidence['veto'])

    def test_latest_quote_spread_cannot_hide_in_average(self):
        ctx = with_primary()
        tick = update(ctx.ticks[-1], spread_pips=3)
        result = RiskOfficerAgent().run(update(ctx, ticks=(tick,)))
        self.assertIn('spread_limit',result.evidence['blockers'])

    def test_blackout_veto_is_independent_of_directional_support(self):
        ctx=with_primary()
        event=RemainingMacroSeatsTests().event(30)
        result=RiskOfficerAgent().run(update(ctx,economic_calendar=(event,)))
        self.assertIn('economic_event_blackout',result.evidence['blockers'])

    def test_missing_primary_measurement_cannot_be_a_successful_risk_pass(self):
        opinions=list(primary_opinions())
        opinions[-1]=update(opinions[-1],evidence={'input_ready':False})
        ctx=update(with_primary(),primary_agent_opinions=tuple(opinions))
        result=RiskOfficerAgent().run(ctx)
        self.assertIn('missing_inputs:risk_sentiment',result.evidence['blockers'])

    def test_stale_quote_and_missing_atr_veto(self):
        ctx = with_primary()
        stale_tick = update(ctx.ticks[-1], timestamp=NOW-timedelta(seconds=31))
        result = RiskOfficerAgent().run(update(ctx,ticks=(stale_tick,)))
        self.assertIn('stale_current_quote',result.evidence['blockers'])
        result = RiskOfficerAgent().run(update(ctx, market_state=update(ctx.market_state,volatility=None)))
        self.assertIn('atr_measurement_missing',result.evidence['blockers'])

    def test_incomplete_primary_blocks_both_governors(self):
        ctx = update(with_primary(), primary_agent_opinions=primary_opinions(omit=('fed',)))
        for agent in (RiskOfficerAgent(), DevilsAdvocateAgent()):
            self.assertTrue(agent.run(ctx).evidence['veto'])

    def test_devils_advocate_raises_contradictions(self):
        ctx = update(with_primary(), primary_agent_opinions=primary_opinions(
            opposing=tuple(s.agent_id for s in COUNCIL_MANIFEST[:10])))
        result = DevilsAdvocateAgent().run(ctx)
        self.assertIn('contradictory_directional_evidence',result.evidence['blockers'])

    def test_chief_holds_without_governance_or_weights(self):
        result = ChiefStrategistAgent().run(with_primary())
        self.assertEqual(result.evidence['decision'],'hold')
        self.assertIn('incomplete_governance',result.evidence['blockers'])
        self.assertIn('fed_source_admission_missing',result.evidence['blockers'])

    def synthesis_context(self):
        ctx = update(with_primary(), primary_agent_opinions=primary_opinions(certified=True))
        return update(ctx, governance_opinions=(DevilsAdvocateAgent().run(ctx), RiskOfficerAgent().run(ctx)))

    def test_synthetic_positive_research_candidate_never_executes(self):
        result = ChiefStrategistAgent().run(self.synthesis_context())
        self.assertEqual(result.evidence['decision'],'research_long_candidate')
        self.assertGreater(result.score,0)
        self.assertFalse(result.evidence['execution_authorized'])

    def test_missing_stale_or_zero_reliability_holds(self):
        ctx = self.synthesis_context()
        for changes in ({'reliability':()},
                        {'reliability':tuple(update(w,available_at=NOW-timedelta(days=31)) for w in ctx.reliability)},
                        {'reliability':tuple(update(w,reliability=0) for w in ctx.reliability)}):
            with self.subTest(kind=list(changes)):
                result = ChiefStrategistAgent().run(update(ctx,**changes))
                self.assertEqual(result.evidence['decision'],'hold')

    def test_risk_veto_always_overrides_unanimous_direction(self):
        ctx = self.synthesis_context()
        risk_result = RiskOfficerAgent().run(update(ctx,risk_state=risk(kill_switch=True)))
        result = ChiefStrategistAgent().run(update(ctx,governance_opinions=(ctx.governance_opinions[0],risk_result)))
        self.assertEqual(result.evidence['decision'],'hold')

    def test_contextual_confidence_never_creates_directional_weight(self):
        ctx=self.synthesis_context()
        baseline=ChiefStrategistAgent().run(ctx)
        opinions=tuple(update(o,confidence=1) if o.signal_type==AgentSignalType.CONTEXTUAL else o
                       for o in ctx.primary_agent_opinions)
        changed=ChiefStrategistAgent().run(update(ctx,primary_agent_opinions=opinions))
        self.assertEqual(baseline.evidence['weighted_strength'],changed.evidence['weighted_strength'])
        self.assertEqual(baseline.evidence['weighted_agreement'],changed.evidence['weighted_agreement'])

    def test_category_balancing_prevents_agent_count_dominance(self):
        ctx=self.synthesis_context()
        # Every category contributes one average regardless of seat count.
        opinions=tuple(update(o,score=-.8,direction='short') if o.category.value=='technical' else o
                       for o in ctx.primary_agent_opinions)
        result=ChiefStrategistAgent().run(update(ctx,primary_agent_opinions=opinions))
        self.assertEqual(result.evidence['weighted_categories'],5)
        self.assertAlmostEqual(result.evidence['weighted_strength'],.48)


class SyntheticCoreAgent(BaseAgent):
    def __init__(self, spec, *, fail=False):
        self.agent_id, self.agent_name, self.category = spec.agent_id, spec.agent_name, spec.category
        self.fail = fail
        super().__init__()

    def analyze(self, context):
        if self.fail:
            raise RuntimeError('Intentional primary failure')
        return self.build_opinion(context,.8,.8,'Synthetic stage boundary test')


def synthetic_registry(fail=False):
    registry = AgentRegistry()
    for spec in COUNCIL_MANIFEST[:24]:
        registry.register(SyntheticCoreAgent(spec, fail=fail and spec.seat_number==1))
    return register_research_seats(registry)


class FullCouncilTests(unittest.TestCase):
    def test_stage_remapping_cannot_bypass_governance(self):
        manifest=tuple(spec.model_copy(update={'stage':AgentStage.GOVERNANCE}) if spec.agent_id=='fed'
                       else spec.model_copy(update={'stage':AgentStage.PRIMARY}) if spec.agent_id=='devils_advocate'
                       else spec for spec in COUNCIL_MANIFEST)
        with self.assertRaises(ValueError):
            ResearchCouncilRunner(AgentRegistry(manifest=manifest))

    def test_manifest_order_does_not_change_execution_order(self):
        registry=AgentRegistry(manifest=tuple(reversed(COUNCIL_MANIFEST)))
        for spec in COUNCIL_MANIFEST[:24]:
            registry.register(SyntheticCoreAgent(spec))
        register_research_seats(registry)
        result=ResearchCouncilRunner(registry).run(update(context(),reliability=with_primary().reliability,
            economic_calendar=(RemainingMacroSeatsTests().event(90000),)))
        self.assertTrue(result.is_complete)
        self.assertEqual([e.seat_number for e in result.executions],list(range(1,32)))

    def test_research_extension_does_not_change_production_core(self):
        registry = register_research_seats(AgentRegistry())
        self.assertEqual(registry.implemented_count,7)
        self.assertFalse(AgentRegistry().is_registered('fed'))

    def test_duplicate_registration_preflight_is_atomic(self):
        registry = register_research_seats(AgentRegistry())
        original = registry.registered_agent_ids
        with self.assertRaises(ValueError):
            register_research_seats(registry)
        self.assertEqual(registry.registered_agent_ids,original)

    def test_incomplete_registry_rejected_before_running(self):
        with self.assertRaises(RuntimeError):
            ResearchCouncilRunner(register_research_seats(AgentRegistry())).run(context())

    def test_precomputed_opinions_rejected(self):
        with self.assertRaises(ValueError):
            ResearchCouncilRunner(synthetic_registry()).run(with_primary())

    def test_all_31_staged_seats_deterministic_and_no_activation(self):
        runner = ResearchCouncilRunner(synthetic_registry(),max_workers=4)
        sample=update(context(),reliability=with_primary().reliability,
            economic_calendar=(RemainingMacroSeatsTests().event(90000),))
        first = runner.run(sample)
        second = runner.run(sample)
        self.assertTrue(first.is_complete)
        self.assertEqual([e.seat_number for e in first.executions],list(range(1,32)))
        self.assertEqual(first.primary.succeeded_count,28)
        self.assertEqual(first.final_opinion,second.final_opinion)
        self.assertEqual(first.context_sha256,second.context_sha256)
        self.assertEqual(first.final_opinion.evidence['decision'],'hold')
        self.assertFalse(first.execution_authorized)

    def test_primary_failure_propagates_to_governance_and_hold(self):
        result = ResearchCouncilRunner(synthetic_registry(fail=True)).run(context())
        self.assertFalse(result.is_complete)
        self.assertEqual(result.primary.failed_count,1)
        self.assertTrue(all(e.opinion.evidence['veto'] for e in result.governance))
        self.assertEqual(result.decision,'hold')


if __name__ == '__main__':
    unittest.main(verbosity=2)

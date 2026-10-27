"""31 real implementations on SYNTHETIC inputs and existing frozen artifacts.

No reserved test evaluation, fitting, database, live API, broker or production
admission. HOLD is expected because provenance and reliability remain unadmitted.
"""
from datetime import timedelta
import unittest

from backend.agents.contracts import AgentContext, AgentStage
from backend.agents.council_inputs import AgentReliability, ExecutionMode
from backend.agents.agent_registry import AgentRegistry
from backend.agents.register_core_agents import register_core_agents
from backend.agents.council_runner import CouncilRunner
from backend.agents.council_manifest import COUNCIL_MANIFEST
from backend.agents.test_remaining_council_seats import context, update, NOW
from backend.macro.macro_schemas import CentralBankRateSnapshot, EconomicCalendarEventSnapshot
from backend.models.historical_replay import HistoricalReplayBuilder
from backend.models.test_historical_dataset_builder import create_candles, create_ticks


def synthetic_full_context():
    candles=create_candles()
    delta=NOW-(candles[-1].timestamp+timedelta(minutes=5))
    candles=[update(candle,timestamp=candle.timestamp+delta) for candle in candles]
    ticks=create_ticks(candles)
    replay=HistoricalReplayBuilder().build(candles=candles,ticks=ticks,anchor_timestamp=candles[-1].timestamp)
    sample=context()
    rates=list(sample.ecb_data)
    for name,value in (('main_refinancing_operations_rate',2.65),('marginal_lending_facility_rate',2.9)):
        rates.append(update(rates[0],rate_name=name,value_percent=value,source_record_id=name))
    rates=tuple(sorted(rates,key=lambda r:(r.reference_date,r.rate_name,r.source_record_id)))
    event=EconomicCalendarEventSnapshot(source_event_id='ECB|MONETARY_POLICY_DECISION|2026|07',
        event_name='ECB Monetary Policy Decision',jurisdiction='EURO_AREA',currency='EUR',
        scheduled_at=NOW+timedelta(days=2),available_at=NOW-timedelta(days=10),ingested_at=NOW,
        source='ECB_GOVERNING_COUNCIL_CALENDAR',content_hash='a'*64)
    inputs=sample.model_dump()
    inputs.update(market_state=replay.context.market_state,candles=replay.context.candles,ticks=replay.context.ticks)
    inputs['calendar_coverage']=update(sample.calendar_coverage,sources=(event.source,))
    inputs['reliability']=tuple(AgentReliability(agent_id=spec.agent_id,reliability=.7,
        regime_compatibility=.8,available_at=NOW-timedelta(days=1),evaluation_id='SYNTHETIC_ONLY')
        for spec in COUNCIL_MANIFEST if spec.stage==AgentStage.PRIMARY)
    return AgentContext.model_validate({**inputs,
        'ecb_data':rates,'fed_data':sample.fed_data,'economic_calendar':(event,)})


class FullRealImplementationTests(unittest.TestCase):
    def test_all_real_seats_on_synthetic_causal_inputs(self):
        registry=register_core_agents(AgentRegistry())
        self.assertEqual(registry.implemented_count,31)
        self.assertEqual(registry.missing_agent_ids,())
        runner=CouncilRunner(registry,max_workers=4,mode=ExecutionMode.RESEARCH)
        sample=synthetic_full_context()
        first=runner.run(sample)
        errors=[(e.agent_id,e.error) for e in first.executions if e.error]
        self.assertEqual(errors,[])
        self.assertTrue(first.is_complete)
        self.assertEqual(first.final_opinion.evidence['decision'],'hold')
        self.assertFalse(first.execution_authorized)
        second=runner.run(sample)
        self.assertEqual([e.opinion for e in first.executions],[e.opinion for e in second.executions])
        self.assertEqual(first.context_sha256,second.context_sha256)


if __name__=='__main__':
    unittest.main(verbosity=2)
